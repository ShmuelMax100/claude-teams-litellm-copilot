"""Authenticate and pin every model request to the private Fusion adapter."""
import base64
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
import re
import time
from urllib.parse import urlsplit
import uuid

import httpx
import litellm
from litellm.integrations.custom_logger import CustomLogger

TIERS = ('standard', 'high', 'max')
ALIASES = {'fusion-' + tier: tier for tier in TIERS}
MARKER = 'fusion_gateway'
_request = ContextVar('fusion_gateway_identity', default=None)


@dataclass
class RequestState:
    subject: str
    tier: str
    adapting: bool = False
    consumed: bool = False


def descriptor(api_base):
    return 'fusion-gateway-guard/v1 api_base=' + api_base


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def sign_identity(subject, key):
    now = int(time.time())
    claims = {'v': 1, 'aud': 'fusion-mcp', 'sub': subject, 'iat': now, 'exp': now + 60, 'jti': uuid.uuid4().hex}
    payload = _b64(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())
    signed = 'v1.' + payload
    return signed + '.' + _b64(hmac.new(key.encode(), signed.encode('ascii'), hashlib.sha256).digest())


def _secret(value):
    return (isinstance(value, str) and len(value) >= 32 and all(33 <= ord(c) <= 126 for c in value)
            and not value.startswith(('ghp_', 'github_pat_', 'sk-ant-')))


class FusionGatewayGuard(CustomLogger):
    def __init__(self, api_base=None, bearer=None, identity_key=None):
        super().__init__(turn_off_message_logging=True)
        self.api_base = api_base if api_base is not None else os.environ.get('FUSION_API_BASE', '')
        self._bearer = bearer if bearer is not None else os.environ.get('FUSION_SERVICE_TOKEN', '')
        self._identity_key = identity_key if identity_key is not None else os.environ.get('FUSION_IDENTITY_KEY', '')
        self.timeout = float(os.environ.get('FUSION_TIMEOUT_SECONDS', '210'))
        parsed = urlsplit(self.api_base)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != '/v1'
                or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Fusion requires a private HTTPS API base ending in /v1.')
        if not _secret(self._bearer) or not _secret(self._identity_key) or self._bearer == self._identity_key:
            raise ValueError('Fusion requires distinct service and signing secrets, each at least 32 characters.')
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 230:
            raise ValueError('Fusion gateway timeout must be positive and at most 230 seconds.')

    def __str__(self):
        return descriptor(self.api_base)

    __repr__ = __str__

    @staticmethod
    def _deny(message='A restricted personal Fusion key and authorized Fusion model are required.'):
        raise litellm.PermissionDeniedError(message, llm_provider='custom_openai', model='fusion',
            response=httpx.Response(403, request=httpx.Request('POST', 'https://fusion.invalid')))

    @staticmethod
    def _bad(message):
        raise litellm.BadRequestError(message, llm_provider='custom_openai', model='fusion')

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        _request.set(None)
        kind = getattr(call_type, 'value', call_type)
        if kind not in ('anthropic_messages', 'aanthropic_messages', 'completion', 'acompletion'):
            self._deny('Only the Fusion model interface is public; MCP is a private service interface.')
        auth = user_api_key_dict
        subject = _field(auth, 'user_id')
        metadata = _field(auth, 'metadata', {}) or {}
        marker = metadata.get(MARKER) if isinstance(metadata, dict) else None
        model = data.get('model')
        if (not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', subject)
                or not isinstance(model, str) or model not in ALIASES or not isinstance(marker, dict)
                or marker != {'version': 1, 'subject': subject, 'tier': ALIASES[model]}
                or type(marker.get('version')) is not int
                or _field(auth, 'models') != [model]):
            self._deny()
        # These are routing/execution controls, never part of the model request.
        forbidden = {'callbacks', 'success_callback', 'failure_callback', 'input_callback', 'fallbacks',
            'context_window_fallbacks', 'content_policy_fallbacks', 'default_fallbacks', 'router_settings',
            'api_base', 'base_url', 'api_key', 'api_version', 'custom_llm_provider', 'litellm_credential_name',
            'client', 'http_client', 'async_client', 'model_list', 'model_id', 'deployment_id', 'deployment',
            'specific_deployment', 'api_type', 'organization', 'headers', 'default_headers', 'authorization',
            'extra_body', 'extra_query', 'extra_request_body', 'context_management', 'mcp_servers',
            'previous_response_id', 'session_id', 'retry_policy', 'allowed_fails', 'allowed_fails_policy',
            'disable_cooldowns', 'cooldown_time', 'retry_after', 'max_retries', 'num_retries', 'max_fallbacks',
            'enable_pre_call_checks', 'enable_health_check_routing', 'routing_strategy', 'routing_strategy_args',
            'allowed_model_region', 'model_group_alias', 'model_group_aliases', 'model_group_retry_policy',
            'timeout', 'request_timeout', 'stream_timeout', 'mock_response'}
        # LiteLLM adds extra_headers/proxy metadata itself. They are discarded at the
        # deployment boundary, rather than accepted as trusted identity or credentials.
        if any(name in forbidden or name.startswith(('mock_', 'retry_', 'cooldown_')) for name in data):
            self._bad('Provider, session, routing and unsupported execution overrides are unavailable on the Fusion gateway.')
        result = dict(data)
        result.update(num_retries=0, max_retries=0, max_fallbacks=0, disable_fallbacks=True,
                      fallbacks=[], context_window_fallbacks=[], content_policy_fallbacks=[],
                      router_settings_override={'num_retries': 0, 'max_fallbacks': 0, 'fallbacks': [],
                          'context_window_fallbacks': [], 'content_policy_fallbacks': []}, caching=False)
        _request.set(RequestState(subject, ALIASES[model]))
        return result

    async def async_filter_listed_models(self, user_api_key_dict, model_names):
        allowed = _field(user_api_key_dict, 'models', []) or []
        return [name for name in model_names if name in ALIASES and name in allowed]

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        state = _request.get()
        model = kwargs.get('model')
        expected = 'fusion-' + state.tier if state is not None else None
        provider = kwargs.get('custom_llm_provider')
        if (state is None or state.consumed
                or model not in (expected, 'custom_openai/' + expected)
                or provider not in (None, 'custom_openai')):
            self._deny('This gateway permits only the authenticated Fusion route; provider switching is disabled.')
        kind = getattr(call_type, 'value', call_type)
        if kind in ('anthropic_messages', 'aanthropic_messages'):
            if state.adapting:
                self._deny()
            state.adapting = True
        elif kind in ('acompletion', 'completion'):
            state.consumed = True
        else:
            self._deny()
        result = dict(kwargs)
        for name in ('api_key', 'api_base', 'base_url', 'api_version', 'organization', 'headers', 'extra_headers',
                     'default_headers', 'authorization', 'client', 'http_client', 'async_client',
                     'litellm_credential_name', 'extra_body', 'extra_query', 'extra_request_body', 'anthropic_beta',
                     'github_copilot_auth_type', 'forward_client_headers_to_llm_api', 'metadata',
                     'litellm_metadata', 'proxy_server_request', 'litellm_params', 'user', 'router_settings_override',
                     'fallbacks', 'context_window_fallbacks', 'content_policy_fallbacks', 'max_fallbacks',
                     'disable_fallbacks'):
            result.pop(name, None)
        # A new assertion is generated at each conversion boundary, without reusing
        # any user-supplied header or metadata. Only the inner completion emits HTTP.
        result.update(model='custom_openai/' + expected, custom_llm_provider='custom_openai',
            api_base=self.api_base, api_key=self._bearer,
            extra_headers={'X-Fusion-Identity': sign_identity(state.subject, self._identity_key)},
            max_retries=0, num_retries=0, caching=False,
            timeout=self.timeout, stream_timeout=self.timeout)
        return result


guard = FusionGatewayGuard()
