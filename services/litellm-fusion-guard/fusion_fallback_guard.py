"""Opt-in single-owner Fusion fallback gate for the pinned LiteLLM proxy.

Load ``fusion_fallback_guard.guard`` as a LiteLLM callback. Mutable request state
lives only in a ContextVar; caller metadata cannot manufacture a primary failure.
The failure hook observes attempts; the pre-deployment hook enforces the gate.
"""
from contextvars import ContextVar
from dataclasses import dataclass
import math
import os
import re
from urllib.parse import urlsplit

import litellm
import httpx
from litellm.integrations.custom_logger import CustomLogger


TIERS = ("standard", "high", "max")
ALIASES = {"fusion-fallback-" + tier: tier for tier in TIERS}
BACKENDS = {provider + "/fusion-" + tier: tier for provider in ("custom_openai", "openai") for tier in TIERS}
MARKER = "fusion_fallback"
ELIGIBLE_STATUS = frozenset((429, 500, 502, 503, 504))
_request = ContextVar("fusion_fallback_request", default=None)


@dataclass
class RequestState:
    tier: str
    primary: str | None = None
    failed: bool = False
    eligible: bool = False
    emitted: bool = False
    consumed: bool = False
    adapting: bool = False


def descriptor(owner, api_base):
    return f"fusion-fallback-guard/v1 owner={owner} api_base={api_base}"


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _tier(kwargs):
    model = kwargs.get("model")
    if not isinstance(model, str):
        return None
    if model in BACKENDS:
        return BACKENDS[model]
    if model in ALIASES:
        return ALIASES[model]
    if kwargs.get("custom_llm_provider") in ("custom_openai", "openai") and model in {"fusion-" + t for t in TIERS}:
        return model.removeprefix("fusion-")
    return None


def eligible_failure(exception):
    # Exception type takes precedence over a misleading status on policy/context
    # errors. Never inspect provider error text, which can contain sensitive data.
    denied = (litellm.AuthenticationError, litellm.BadRequestError,
              litellm.ContentPolicyViolationError, litellm.ContextWindowExceededError,
              litellm.PermissionDeniedError)
    if isinstance(exception, denied):
        return False
    if isinstance(exception, (litellm.Timeout, litellm.APIConnectionError)):
        return True
    return getattr(exception, "status_code", None) in ELIGIBLE_STATUS


class FusionFallbackGuard(CustomLogger):
    def __init__(self, owner=None, api_base=None, bearer=None):
        super().__init__(turn_off_message_logging=True)
        self.owner = owner if owner is not None else os.environ.get("FUSION_FALLBACK_OWNER_SUBJECT", "")
        self.api_base = api_base if api_base is not None else os.environ.get("FUSION_FALLBACK_API_BASE", "")
        self._bearer = bearer if bearer is not None else os.environ.get("FUSION_FALLBACK_BEARER_TOKEN", "")
        self.primary_timeout = float(os.environ.get("FUSION_FALLBACK_PRIMARY_TIMEOUT_SECONDS", "60"))
        self.fallback_timeout = float(os.environ.get("FUSION_FALLBACK_TIMEOUT_SECONDS", "140"))
        if (not all(math.isfinite(value) and value > 0 for value in (self.primary_timeout, self.fallback_timeout))
                or self.primary_timeout + self.fallback_timeout > 230):
            raise ValueError("Fusion fallback timeouts must be positive, finite and total at most 230 seconds.")
        parsed = urlsplit(self.api_base)
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", self.owner):
            raise ValueError("Fusion fallback requires a configured single owner.")
        if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != "/v1"
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("Fusion fallback requires a private HTTPS API base ending in /v1.")
        if (len(self._bearer) < 32 or any(not 33 <= ord(c) <= 126 for c in self._bearer)
                or self._bearer.startswith(("ghp_", "github_pat_", "sk-ant-"))):
            raise ValueError("Fusion fallback requires the independent service bearer, not a provider credential.")

    def __str__(self):
        return descriptor(self.owner, self.api_base)

    def __repr__(self):
        return self.__str__()

    @staticmethod
    def _deny():
        raise litellm.PermissionDeniedError(
            "Fusion is available only as an authorized fallback before primary output.",
            llm_provider="openai", model="fusion",
            response=httpx.Response(403, request=httpx.Request("POST", "https://fusion.invalid")))

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        # Every request resets context, including requests from legacy keys. The
        # proxy awaits this hook before creating the provider task; child tasks
        # inherit this object while concurrent incoming requests remain isolated.
        _request.set(None)
        if "model" in data and not isinstance(data["model"], str):
            raise litellm.BadRequestError("Model must be a string.", llm_provider="openai", model="fusion")
        if _tier(data):
            self._deny()
        auth = user_api_key_dict
        metadata = _field(auth, "metadata", {}) or {}
        opt_in = metadata.get(MARKER) if isinstance(metadata, dict) else None
        if (isinstance(opt_in, dict) and type(opt_in.get("version")) is int and opt_in["version"] == 1
                and _field(auth, "user_id") == self.owner and opt_in.get("owner") == self.owner
                and opt_in.get("tier") in TIERS
                and "fusion-fallback-" + opt_in["tier"] in (_field(auth, "models", []) or [])):
            forbidden = {"callbacks", "success_callback", "failure_callback", "input_callback", "fallbacks",
                         "context_window_fallbacks", "content_policy_fallbacks", "default_fallbacks", "router_settings",
                         "api_base", "base_url", "custom_llm_provider", "litellm_credential_name", "client", "http_client"}
            if any(name in forbidden or name.startswith("mock_") for name in data):
                raise litellm.BadRequestError("Request-level routing and mock overrides are unavailable for Fusion fallback.",
                                             llm_provider="openai", model="fusion")
            # The proxy merges this field before pre-call hooks. Replace it with
            # the authenticated key's policy so a request cannot forge it.
            data = dict(data)
            trusted_policy = _field(auth, "router_settings")
            if isinstance(trusted_policy, dict):
                data["router_settings_override"] = dict(trusted_policy, num_retries=0, max_fallbacks=1,
                                                        content_policy_fallbacks=[], context_window_fallbacks=[])
            else:
                data.pop("router_settings_override", None)
            data.update(num_retries=0, max_fallbacks=1)
            _request.set(RequestState(tier=opt_in["tier"]))
        return data

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        state = _request.get()
        tier = _tier(kwargs)
        if tier is None:
            if state is not None and state.primary is None:
                model = kwargs.get("model", "")
                if isinstance(model, str) and (model.startswith("anthropic/") or kwargs.get("custom_llm_provider") == "anthropic"):
                    state.primary = model
            if state is not None and kwargs.get("model") == state.primary and not state.consumed:
                kwargs = dict(kwargs, timeout=self.primary_timeout, stream_timeout=self.primary_timeout,
                              max_retries=0, num_retries=0)
            return kwargs
        if (state is None or state.tier != tier or not state.primary or not state.eligible
                or state.emitted or state.consumed):
            self._deny()
        # Anthropic Messages conversion enters this hook once for its adapter,
        # then once for the actual OpenAI completion. Only the latter consumes
        # the attempt; a repeated adapter entry is not another permitted retry.
        kind = getattr(call_type, "value", call_type)
        if kind in ("anthropic_messages", "aanthropic_messages"):
            if state.adapting:
                self._deny()
            state.adapting = True
        elif kind in ("acompletion", "completion"):
            state.consumed = True
            state.eligible = False
        else:
            self._deny()
        result = dict(kwargs)
        # Provider credentials, HTTP clients and request extensions are not part
        # of the context handoff. A copied primary OAuth header must never reach
        # the independent SDK service. Credential loading happens after this hook.
        for name in ("api_key", "api_base", "base_url", "api_version", "organization",
                     "headers", "extra_headers", "default_headers", "authorization",
                     "client", "http_client", "async_client", "litellm_credential_name",
                     "extra_body", "extra_query", "extra_request_body", "anthropic_beta",
                     "github_copilot_auth_type", "forward_client_headers_to_llm_api",
                     "thinking", "reasoning_effort", "metadata", "litellm_metadata",
                     "proxy_server_request", "litellm_params"):
            result.pop(name, None)
        result.update(model="custom_openai/fusion-" + tier, custom_llm_provider="custom_openai",
                      api_base=self.api_base, api_key=self._bearer, extra_headers={},
                      max_retries=0, num_retries=0, caching=False,
                      timeout=self.fallback_timeout, stream_timeout=self.fallback_timeout)
        return result

    async def async_post_call_failure_deployment_hook(self, request_data, exception, call_type, fallback_depth=None):
        state = _request.get()
        if (state is not None and state.primary is not None and not state.failed and not state.consumed
                and request_data.get("model") == state.primary and fallback_depth in (None, 0)):
            state.failed = True
            state.eligible = not state.emitted and eligible_failure(exception)

    async def async_post_call_streaming_deployment_hook(self, request_data, response_chunk, call_type):
        state = _request.get()
        if state is not None and request_data.get("model") == state.primary:
            # Conservative: even a role/message-start frame means a response has
            # started. Never replay it as a new Fusion response after this point.
            state.emitted = True
            state.eligible = False
        return response_chunk


guard = FusionFallbackGuard()
