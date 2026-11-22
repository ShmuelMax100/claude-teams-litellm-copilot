"""Warm empty SDK runtime; explicitly authenticated isolated sessions per request."""
import asyncio
import json
import logging
import re
from pathlib import Path
import tempfile
import time
import uuid

from config import Config
from auth import current_credential
from stream_filter import DeltaRedactor
from inference_types import CompletionRequest, CompletionResult, ToolCall
from schemas import FusionResult, validate_prompt
from sdk_runtime import create_sdk_client, deny_permission, SDK_VERSION, RUNTIME_VERSION, PROTOCOL_VERSION

LOG = logging.getLogger("fusion.audit")
CLEANUP_TIMEOUT = 5


class OutputLimit(Exception):
    pass


class ProviderError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class ProviderAccessDenied(Exception):
    pass


class CredentialExpired(Exception):
    pass


class UnexpectedTool(Exception):
    pass


class CopilotRunner:
    def __init__(self, config: Config, *, client_factory=None):
        self.config = config
        self._client_factory = client_factory or create_sdk_client
        self.client = None
        self.capable = False
        self.capability_status = "not_checked"
        self.cli_version = None  # Deprecated compatibility name for runtime version.
        self.sdk_version = SDK_VERSION
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._runtime_workspace = None
        self._retained_workspaces = []
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def secrets(self):
        return (self.config.service_token, self.config.identity_key,
                *(credential.token for credential in self.config.credentials.values()))

    def redact(self, value: str) -> str:
        for secret in self.secrets():
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[REDACTED]", value)

    def workspace(self, prefix="request-"):
        root = self.config.workspace_root
        if root.is_symlink():
            raise ValueError("Workspace root cannot be a symbolic link.")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace = tempfile.TemporaryDirectory(prefix=prefix, dir=root)
        for name in ("home", "work", "config"):
            (Path(workspace.name) / name).mkdir(mode=0o700)
        return workspace

    def environment(self, home: Path):
        # Explicit replacement, never inherited: no GitHub/BYOK credentials,
        # proxy configuration, shell startup, native preload or SDK downloads.
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"), "TMPDIR": str(home),
                "LANG": "C.UTF-8", "NO_COLOR": "1", "COPILOT_SKIP_CLI_DOWNLOAD": "1",
                "COPILOT_AUTO_UPDATE": "false", "COPILOT_MCP_TOOL_CACHE": "false"}

    async def probe(self):
        async with self._lifecycle_lock:
            if self.client is not None:
                return
            self.capable = False
            try:
                self._runtime_workspace = self.workspace("runtime-")
                base = Path(self._runtime_workspace.name)
                self.client = self._client_factory(runtime_path=self.config.copilot_bin,
                    environment=self.environment(base / "home"),
                    working_directory=str(base / "work"), base_directory=str(base / "config"))
                async with asyncio.timeout(20):
                    await self.client.start()
                    await self.client.ping()
                    status = await self.client.get_status()
                if status.version != RUNTIME_VERSION or status.protocol_version != PROTOCOL_VERSION:
                    self.capability_status = "unsupported_runtime"
                    await self._retire()
                    return
                self.cli_version = status.version
                self.capability_status = "ready"
                self.capable = True
            except FileNotFoundError:
                self.capability_status = "runtime_missing"
                await self._retire()
            except Exception:
                self.capability_status = "runtime_check_failed"
                await self._retire()

    def session_options(self, tier, request_id, base, credential):
        return dict(session_id=request_id, model="fusion", working_directory=str(base / "work"),
            enable_config_discovery=False,
            github={"token": credential.token, "capi": {"fusion_tier": tier},
                    "telemetry_enabled": False, "interactive_user": False},
            permissions={"on_request": deny_permission},
            tools={"available_tools": [], "definitions": [], "manage_schedule_enabled": False},
            instructions={"skip_custom_instructions": True, "on_demand_discovery": False,
                          "directories": []},
            hooks={"enable_repository_hooks": False, "directories": []},
            skills={"enabled": False, "directories": [], "included_builtin_skills": []},
            plugins={"directories": []},
            agents={"definitions": [], "included_builtin_agents": [], "directories": []},
            mcp={"servers": {}, "disabled_servers": ["github", "computer-use"],
                 "oauth_token_storage": "in-memory", "enable_apps": False},
            state={"memory": {"enabled": False}, "enable_cross_session_store": False,
                   "skip_embedding_retrieval": True, "embedding_cache_storage": "in-memory",
                   "infinite_sessions": {"enabled": False}},
            execution={"autopilot": {"continuation_enabled": False, "max_continues": 0}},
            git={"enable_host_git_operations": False, "coauthor_enabled": False},
            events={"streaming": True, "include_sub_agent_streaming_events": False},
            experiments={"enable_experimental_mode": False},
            system_message={"mode": "replace", "content":
                "Answer only from the supplied prompt. Return advice or code as text. No tools are available."})

    async def check_health(self):
        """Local RPC only: readiness never probes account/model entitlement."""
        if not self.capable or self.client is None:
            return False
        try:
            await asyncio.wait_for(self.client.ping(), 2)
        except Exception:
            self.capable = False
            self.capability_status = "runtime_unavailable"
        return self.capable

    async def _response(self, session, prompt, tool_map=None, on_delta=None):
        tool_map = tool_map or {}
        loop = asyncio.get_running_loop()
        completed = loop.create_future()
        chunks, messages = [], []
        delta_bytes = final_bytes = 0
        tool_calls = []
        usage = {}
        finish_reason = "stop"
        redactor = DeltaRedactor(self.secrets())
        message_deltas = []

        def stream(text, final=False):
            safe = redactor.feed(text, final=final)
            if safe and on_delta is not None:
                on_delta(safe)

        def answer():
            return {"output": "".join(messages) if messages else "".join(chunks),
                    "tool_calls": tool_calls, "usage": usage or None,
                    "finish_reason": "tool_calls" if tool_calls else finish_reason}

        def declared_name(name):
            return name.removeprefix("custom:") if isinstance(name, str) else ""

        def observe(event):
            nonlocal delta_bytes, final_bytes, finish_reason
            if completed.done():
                return
            kind = getattr(event.type, "value", event.type)
            # No agent/tool execution is authorized, including unexpected runtime changes.
            if kind == "subagent.started" or (kind in ("external_tool.requested", "tool.execution_start")
                    and declared_name(getattr(event.data, "tool_name", None)) not in tool_map):
                completed.set_exception(UnexpectedTool())
                return
            if getattr(event, "agent_id", None):
                return
            if kind == "assistant.usage":
                for key in ("input_tokens", "output_tokens"):
                    value = getattr(event.data, key, None)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[key] = usage.get(key, 0) + value
                if getattr(event.data, "finish_reason", None) in ("length", "max_tokens"):
                    finish_reason = "length"
            elif kind == "session.error":
                # Structured auth codes are safe to classify; never relay upstream text.
                codes = {getattr(event.data, key, None) for key in
                         ("error_type", "error_code", "provider_error_type", "provider_error_code", "status_code")}
                error = (CredentialExpired() if "token_expired" in codes else
                         AuthenticationError() if codes & {"authentication_error", "unauthorized", "invalid_token", "401", 401} else
                         ProviderAccessDenied() if codes & {"access_denied", "forbidden", "403", 403} else ProviderError())
                completed.set_exception(error)
            elif kind == "assistant.message_delta":
                text = getattr(event.data, "delta_content", "") or ""
                delta_bytes += len(text.encode("utf-8"))
                if delta_bytes > self.config.max_output_bytes:
                    completed.set_exception(OutputLimit())
                else:
                    chunks.append(text)
                    message_deltas.append(text)
                    try:
                        stream(text)
                    except Exception:
                        completed.set_exception(OutputLimit())
            elif kind == "assistant.message":
                text = getattr(event.data, "content", "") or ""
                final_bytes += len(text.encode("utf-8"))
                if final_bytes > self.config.max_output_bytes:
                    completed.set_exception(OutputLimit())
                else:
                    # Some providers emit only complete messages. Reconcile each final
                    # message with its deltas before forwarding any missing suffix.
                    previous = "".join(message_deltas)
                    if not text.startswith(previous):
                        completed.set_exception(ProviderError())
                        return
                    try:
                        stream(text[len(previous):])
                    except Exception:
                        completed.set_exception(OutputLimit())
                        return
                    message_deltas.clear()
                    messages.append(text)
                    requests = getattr(event.data, "tool_requests", None) or []
                    for request in requests:
                        name = declared_name(getattr(request, "name", None))
                        arguments = getattr(request, "arguments", None)
                        identifier = getattr(request, "tool_call_id", None)
                        if (name not in tool_map or not isinstance(arguments, dict)
                                or not isinstance(identifier, str) or not identifier
                                or identifier in {tool.id for tool in tool_calls}):
                            completed.set_exception(UnexpectedTool())
                            return
                        try:
                            serialized = json.dumps({"id": identifier, "name": tool_map[name].name,
                                                     "arguments": arguments},
                                                    ensure_ascii=False, allow_nan=False)
                        except (TypeError, ValueError):
                            completed.set_exception(UnexpectedTool())
                            return
                        if self.redact(serialized) != serialized:
                            completed.set_exception(ProviderError())
                            return
                        final_bytes += len(serialized.encode("utf-8")) + 1
                        if final_bytes > self.config.max_output_bytes:
                            completed.set_exception(OutputLimit())
                            return
                        tool_calls.append(ToolCall(id=identifier, name=tool_map[name].name,
                                                  arguments=arguments))
                    if requests:
                        # This event contains the whole model tool batch. Never
                        # return just the first external_tool.requested event.
                        completed.set_result(answer())
            elif kind == "session.idle":
                completed.set_result(answer())

        unsubscribe = session.on(observe)
        try:
            await session.send(prompt)
            result = await completed
            stream("", final=True)
            return result
        finally:
            unsubscribe()
            # Retrieve any event failure that raced a failed/cancelled send.
            if completed.done() and not completed.cancelled():
                completed.exception()
            elif not completed.done():
                completed.cancel()

    async def _cleanup_session(self, session, session_id, abort):
        ok = True
        # Attempt deletion even if abort/disconnect fails. Every operation is bounded.
        operations = ([session.abort] if session is not None and abort else [])
        if session is not None:
            operations.append(session.disconnect)
        operations.append(lambda: self.client.delete_session(session_id))
        for operation in operations:
            try:
                await asyncio.wait_for(operation(), CLEANUP_TIMEOUT)
            except Exception:
                ok = False
        if not ok:
            self.capable = False
            self.capability_status = "cleanup_failed"
        return ok

    async def _retire(self):
        # Caller holds lifecycle lock or is the last in-flight request. Mark
        # unavailable first; no new invocation can acquire this runtime.
        self.capable = False
        stopped = True
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.stop(), 15)
            except Exception:
                try:
                    await asyncio.wait_for(self.client.force_stop(), 5)
                except Exception:
                    stopped = False
            if stopped:
                self.client = None
        if stopped:
            for workspace in self._retained_workspaces:
                workspace.cleanup()
            self._retained_workspaces.clear()
            if self._runtime_workspace is not None:
                self._runtime_workspace.cleanup()
                self._runtime_workspace = None

    async def close(self):
        self.capable = False
        self.capability_status = "stopping"
        try:
            await asyncio.wait_for(self._idle.wait(), self.config.timeout_seconds + 20)
        finally:
            async with self._lifecycle_lock:
                await self._retire()

    async def run(self, tier: str, prompt: str, *, credential=None) -> FusionResult:
        return await self._run(tier, prompt, credential=credential)

    async def complete(self, request: CompletionRequest, *, credential=None, on_delta=None) -> CompletionResult:
        return await self._run(request.tier, request.prompt, request, credential=credential, on_delta=on_delta)

    async def _run(self, tier, prompt, completion=None, *, credential=None, on_delta=None):
        started = time.monotonic()
        result_type = CompletionResult if completion is not None else FusionResult
        result = result_type(ok=False, request_id=str(uuid.uuid4()), tier=tier, status="internal_error")
        acquired = active = False
        session = workspace = None
        session_attempted = False
        try:
            credential = credential or current_credential.get()
            if credential is None or self.config.credentials.get(credential.subject) != credential:
                result.status = "unauthorized"
                return result
            if credential.expires_at <= time.time():
                result.status = "credential_expired"
                return result
            if tier not in self.config.allowed_tiers:
                result.status = "access_denied"
                return result
            try:
                if completion is None:
                    validate_prompt(prompt, self.config.max_input_chars)
                else:
                    if (not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt
                            or not isinstance(completion.system_message, str)
                            or len(prompt) + len(completion.system_message) > self.config.max_context_chars
                            or len(completion.tools) > 128):
                        raise ValueError("Invalid context")
                    names = set()
                    for tool in completion.tools:
                        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tool.name)
                                or tool.name in names or not isinstance(tool.parameters, dict)
                                or not isinstance(tool.description, str)):
                            raise ValueError("Invalid tools")
                        names.add(tool.name)
                    tool_size = len(json.dumps([dict(name=t.name, description=t.description,
                        parameters=t.parameters) for t in completion.tools], allow_nan=False))
                    if len(prompt) + len(completion.system_message) + tool_size > self.config.max_context_chars:
                        raise ValueError("Context too large")
            except ValueError:
                result.status = "invalid_input"
                return result
            if not self.capable:
                result.status = self.capability_status
                return result
            try:
                await asyncio.wait_for(self._semaphore.acquire(), .05)
                acquired = True
            except TimeoutError:
                result.status = "busy"
                return result
            # A concurrent cleanup failure may have closed admission while waiting.
            if not self.capable:
                result.status = self.capability_status
                return result
            self._active += 1
            active = True
            self._idle.clear()
            workspace = self.workspace()
            async with asyncio.timeout(self.config.timeout_seconds):
                if not await self.check_health():
                    result.status = self.capability_status
                    return result
                if credential.expires_at <= time.time():
                    raise CredentialExpired()
                options = self.session_options(tier, result.request_id, Path(workspace.name), credential)
                tool_map = {}
                if completion is not None:
                    # Internal names prevent caller names colliding with builtins.
                    tool_map = {f"fusion_caller_{i}": tool for i, tool in enumerate(completion.tools)}
                    from copilot import Tool
                    options["tools"]["definitions"] = [Tool(name=name,
                        description=f"Caller tool {tool.name}: {tool.description}",
                        parameters=tool.parameters, handler=None) for name, tool in tool_map.items()]
                    options["tools"]["available_tools"] = ["custom:" + name for name in tool_map]
                    options["system_message"] = {"mode": "replace", "content": completion.system_message}
                session_attempted = True
                session = await self.client.create_session(**options)
                answer = await self._response(session, prompt, tool_map, on_delta)
            result.output = self.redact(answer["output"])
            if completion is not None:
                result.tool_calls = answer["tool_calls"]
                result.usage = answer["usage"]
                result.finish_reason = answer["finish_reason"]
            result.ok, result.status = True, "succeeded"
        except TimeoutError:
            result.status = "timeout"
        except OutputLimit:
            result.status = "output_limit"
        except UnexpectedTool:
            result.status = "tool_denied"
        except AuthenticationError:
            result.status = "authentication_error"
        except ProviderAccessDenied:
            result.status = "access_denied"
        except CredentialExpired:
            result.status = "credential_expired"
        except ProviderError:
            result.status = "provider_error"
        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except Exception:
            result.status = "execution_error"
        finally:
            # A separate task owns *all* cleanup. Repeated HTTP cancellation
            # cannot interrupt workspace removal, semaphore release or audit.
            cleanup = asyncio.create_task(self._finish_request(result, started, session,
                workspace, session_attempted, active, acquired, getattr(credential, "subject", None)))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            await cleanup
            if cancelled:
                raise asyncio.CancelledError
        return result

    async def _finish_request(self, result, started, session, workspace,
                              session_attempted, active, acquired, subject):
        try:
            clean = True
            if session_attempted:
                clean = await self._cleanup_session(session, result.request_id, not result.ok or bool(getattr(result, "tool_calls", [])))
            if workspace is not None:
                if clean:
                    try:
                        workspace.cleanup()
                    except OSError:
                        clean = False
                if not clean:
                    self._retained_workspaces.append(workspace)
            if not clean:
                self.capable = False
                self.capability_status = "cleanup_failed"
                result.ok, result.status, result.output = False, "cleanup_failed", ""
                if hasattr(result, "tool_calls"):
                    result.tool_calls = []
        finally:
            if active:
                self._active -= 1
                if self._active == 0:
                    self._idle.set()
            try:
                if not self.capable and self._active == 0 and self.client is not None:
                    async with self._lifecycle_lock:
                        await self._retire()
            finally:
                if acquired:
                    self._semaphore.release()
                result.duration_ms = round((time.monotonic() - started) * 1000)
                LOG.info(self.redact(json.dumps({"request_id": result.request_id,
                    "subject": subject, "tool": "fusion_" + result.tier,
                    "tier": result.tier, "duration_ms": result.duration_ms,
                    "status": result.status, "exit_code": result.exit_code})))
