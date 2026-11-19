"""Bounded, prompt-only subprocess execution with no shared CLI state."""
import asyncio
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
import uuid

from config import Config
from schemas import FusionResult, validate_prompt

LOG = logging.getLogger("fusion.audit")
REQUIRED_FLAGS = ("--fusion-tier", "--model", "--agent", "--deny-tool",
                  "--disable-builtin-mcps", "--disable-mcp-server", "--no-ask-user",
                  "--no-auto-update", "--no-custom-instructions", "--no-bash-env",
                  "--disable-client-telemetry", "--sync-session", "--log-level", "--silent")
AGENT = """---
name: fusion-prompt
description: Answer the supplied prompt without accessing files or tools.
tools: []
include-custom-instructions: false
---
Use only the content supplied in the prompt. Return advice or code as text.
"""


class OutputLimit(Exception):
    pass


class CopilotRunner:
    def __init__(self, config: Config):
        self.config = config
        self.binary = shutil.which(config.copilot_bin)
        self.capable = False
        self.capability_status = "disabled" if config.auth_mode == "disabled" else "not_checked"
        self.cli_version = None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    def redact(self, value: str) -> str:
        for secret in (self.config.github_token, self.config.mcp_token):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[REDACTED]", value)

    @contextmanager
    def workspace(self):
        root = self.config.workspace_root
        if root.is_symlink():
            raise ValueError("Workspace root cannot be a symbolic link.")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="request-", dir=root) as name:
            base = Path(name)
            home, work = base / "home", base / "work"
            home.mkdir(mode=0o700)
            work.mkdir(mode=0o700)
            agents = home / ".copilot" / "agents"
            agents.mkdir(mode=0o700, parents=True)
            (agents / "fusion-prompt.agent.md").write_text(AGENT)
            yield home, work

    def environment(self, home: Path, credential: bool):
        # Do not inherit GitHub, BYOK, proxy, logging, preload or shell variables.
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
                "COPILOT_HOME": str(home / ".copilot"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "TMPDIR": str(home), "LANG": "C.UTF-8", "NO_COLOR": "1",
                "COPILOT_AUTO_UPDATE": "false", "COPILOT_MCP_TOOL_CACHE": "false",
                "COPILOT_GITHUB_TOKEN": self.config.github_token if credential else ""}

    def command(self, tier: str, prompt: str):
        if tier not in ("standard", "high", "max"):
            raise ValueError("Invalid tier")
        return [self.binary or self.config.copilot_bin,
                "--no-auto-update", "--no-custom-instructions", "--no-bash-env",
                "--disable-builtin-mcps", "--disable-mcp-server", "computer-use",
                "--disable-client-telemetry", "--sync-session=off", "--no-ask-user",
                "--log-level", "none", "--agent", "fusion-prompt",
                "--deny-tool", "read,write,shell,url,memory", "--silent",
                "--model", "fusion", "--fusion-tier", tier, "-p", prompt]

    async def _terminate(self, process, readers):
        # Stop the bounded collectors before consuming the pipes elsewhere.
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # The group can outlive its leader. Kill the whole session even if the
        # leader already exited, then reap it before removing its workspace.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        async def discard(stream):
            while await stream.read(8192):
                pass

        try:
            async with asyncio.timeout(5):
                # On Linux, wait() can wait for pipe disconnection even after
                # SIGKILL. A collector that exceeded its cap leaves a paused
                # reader, so drain and discard pending bytes before waiting.
                await asyncio.gather(discard(process.stdout), discard(process.stderr))
                await process.wait()
        except TimeoutError:
            # A detached descendant could retain a pipe descriptor. asyncio's
            # Process has no public close API; close its transport as a bounded
            # last resort so one failed call cannot retain pipes indefinitely.
            process._transport.close()
            await asyncio.wait_for(process.wait(), timeout=1)

    async def _execute(self, args, home, work, timeout, credential, limit):
        process = await asyncio.create_subprocess_exec(*args, cwd=work,
            env=self.environment(home, credential), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        total = 0

        async def read(stream):
            nonlocal total
            chunks = []
            while chunk := await stream.read(8192):
                total += len(chunk)
                if total > limit:
                    raise OutputLimit()
                chunks.append(chunk)
            return b"".join(chunks)

        readers = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr))]
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await asyncio.gather(*readers)
                code = await process.wait()
                return code, stdout, stderr
        finally:
            # Also reap tool descendants left behind by a normally exiting CLI.
            await asyncio.shield(self._terminate(process, readers))

    async def probe(self):
        self.capable = False
        if self.config.auth_mode != "single_user":
            self.capability_status = "disabled"
            return
        if not self.binary:
            self.capability_status = "cli_missing"
            return
        try:
            with self.workspace() as (home, work):
                code, out, _ = await self._execute([self.binary, "--help"], home, work, 15, False, 262144)
                help_text = out.decode("utf-8", errors="replace")
                if code or any(flag not in help_text for flag in REQUIRED_FLAGS):
                    self.capability_status = "unsupported_cli"
                    return
                code, out, _ = await self._execute([self.binary, "--version"], home, work, 15, False, 4096)
                match = re.search(r"GitHub Copilot CLI (\d+\.\d+\.\d+)", out.decode(errors="replace"))
                self.cli_version = match.group(1) if match else None
                if code:
                    self.capability_status = "unsupported_cli"
                    return
            self.capable = True
            self.capability_status = "ready"
        except (OSError, ValueError, TimeoutError, OutputLimit):
            self.capability_status = "cli_check_failed"

    async def run(self, tier: str, prompt: str) -> FusionResult:
        started = time.monotonic()
        result = FusionResult(ok=False, request_id=str(uuid.uuid4()), tier=tier, status="internal_error")
        acquired = False
        try:
            if self.config.auth_mode != "single_user":
                result.status = "disabled"
                return result
            if tier not in self.config.allowed_tiers:
                result.status = "access_denied"
                return result
            try:
                validate_prompt(prompt, self.config.max_input_chars)
            except ValueError:
                result.status = "invalid_input"
                return result
            if not self.capable:
                result.status = self.capability_status
                return result
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=0.05)
                acquired = True
            except TimeoutError:
                result.status = "busy"
                return result
            with self.workspace() as (home, work):
                code, out, _ = await self._execute(self.command(tier, prompt), home, work,
                    self.config.timeout_seconds, True, self.config.max_output_bytes)
            result.exit_code = code
            if code:
                # stderr can contain code, credentials or account details.
                result.status = "provider_error"
            else:
                result.status = "succeeded"
                result.ok = True
                result.output = self.redact(out.decode("utf-8", errors="replace").strip())
            return result
        except TimeoutError:
            result.status = "timeout"
            return result
        except OutputLimit:
            result.status = "output_limit"
            return result
        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except (OSError, ValueError):
            result.status = "execution_error"
            return result
        finally:
            if acquired:
                self._semaphore.release()
            result.duration_ms = round((time.monotonic() - started) * 1000)
            LOG.info(self.redact(json.dumps({"request_id": result.request_id,
                "subject": self.config.subject, "tool": "fusion_" + tier,
                "tier": tier, "duration_ms": result.duration_ms,
                "status": result.status, "exit_code": result.exit_code})))
