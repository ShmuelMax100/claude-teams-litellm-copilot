"""Offline identity, fixed routing and restricted enrollment verification."""
import asyncio
import base64
import copy
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_engine'))
import fusion_gateway as onboarding
from gateway import Error, load_catalog, read


def load_guard():
    fake = types.ModuleType('litellm')
    class ProviderError(Exception):
        def __init__(self, message='', **kwargs):
            super().__init__(message)
    for name in ('PermissionDeniedError', 'BadRequestError'):
        setattr(fake, name, type(name, (ProviderError,), {}))
    logger = types.ModuleType('litellm.integrations.custom_logger')
    class CustomLogger:
        def __init__(self, **kwargs):
            pass
    logger.CustomLogger = CustomLogger
    transport = types.ModuleType('httpx')
    transport.Request = lambda *args, **kwargs: None
    transport.Response = lambda *args, **kwargs: None
    spec = importlib.util.spec_from_file_location('tested_fusion_gateway_guard', ROOT / 'services/litellm-fusion-gateway/fusion_gateway_guard.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {'litellm': fake, 'litellm.integrations.custom_logger': logger, 'httpx': transport}), patch.dict(os.environ, {
            'FUSION_API_BASE': 'https://fusion.example/v1', 'FUSION_SERVICE_TOKEN': 's' * 40, 'FUSION_IDENTITY_KEY': 'i' * 40}):
        spec.loader.exec_module(module)
    return module, fake


guard_module, provider = load_guard()


def auth(subject='alice', tier='standard'):
    return {'user_id': subject, 'models': ['fusion-' + tier],
            'metadata': {'fusion_gateway': onboarding.marker(subject, tier)}}


def claims(assertion):
    version, payload, signature = assertion.split('.')
    expected = base64.urlsafe_b64encode(hmac.new(b'i' * 40, (version + '.' + payload).encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    assert hmac.compare_digest(signature, expected)
    return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guard = guard_module.FusionGatewayGuard('https://fusion.example/v1', 's' * 40, 'i' * 40)

    async def begin(self, subject='alice', tier='standard', **extra):
        return await self.guard.async_pre_call_hook(auth(subject, tier), None, {'model': 'fusion-' + tier, **extra}, 'anthropic_messages')

    async def test_first_request_routes_directly_with_authenticated_identity(self):
        data = await self.begin()
        self.assertTrue(data['disable_fallbacks'])
        self.assertEqual(data['fallbacks'], [])
        self.assertEqual(data['router_settings_override']['max_fallbacks'], 0)
        outer = await self.guard.async_pre_call_deployment_hook({'model': 'custom_openai/fusion-standard'}, 'anthropic_messages')
        inner = await self.guard.async_pre_call_deployment_hook(outer, 'acompletion')
        self.assertEqual(inner['api_base'], 'https://fusion.example/v1')
        self.assertEqual(inner['api_key'], 's' * 40)
        asserted = claims(inner['extra_headers']['X-Fusion-Identity'])
        self.assertEqual(asserted['sub'], 'alice')
        self.assertEqual(asserted['aud'], 'fusion-mcp')
        self.assertEqual(asserted['exp'] - asserted['iat'], 60)
        self.assertNotIn('router_settings_override', inner)
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(outer, 'acompletion')

    async def test_provider_or_tier_switch_and_absent_identity_denied(self):
        for model in ('anthropic/claude-sonnet', 'custom_openai/other', 'custom_openai/fusion-high'):
            await self.begin()
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_deployment_hook({'model': model}, 'acompletion')
        guard_module._request.set(None)
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook({'model': 'custom_openai/fusion-standard'}, 'acompletion')

    async def test_legacy_wildcard_shared_and_forged_identity_denied(self):
        cases = [dict(auth(), user_id='bob'), dict(auth(), user_id=None), dict(auth(), metadata={}),
                 dict(auth(), models=['*']), dict(auth(), models=['fusion-standard', 'anthropic/*'])]
        forged = auth()
        forged['metadata']['fusion_gateway']['version'] = True
        cases.append(forged)
        for identity in cases:
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_hook(identity, None, {'model': 'fusion-standard'}, 'anthropic_messages')
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_hook(auth(), None, {'model': 'fusion-standard'}, 'call_mcp_tool')

    async def test_all_client_routing_and_runtime_overrides_rejected(self):
        for field in ('api_key', 'api_base', 'base_url', 'custom_llm_provider', 'fallbacks', 'context_window_fallbacks',
                      'content_policy_fallbacks', 'default_fallbacks', 'model_list', 'model_id', 'deployment_id',
                      'specific_deployment', 'mcp_servers', 'context_management', 'previous_response_id', 'session_id',
                      'mock_response', 'mock_testing_fallbacks', 'extra_body', 'callbacks', 'disable_cooldowns',
                      'allowed_fails_policy', 'retry_after', 'retry_policy', 'retry_strategy', 'cooldown_time',
                      'max_retries', 'num_retries', 'routing_strategy', 'timeout'):
            with self.subTest(field=field), self.assertRaises(provider.BadRequestError):
                await self.begin(**{field: 'untrusted'})

    async def test_untrusted_credentials_headers_metadata_removed(self):
        await self.begin(extra_headers={'X-Fusion-Identity': 'forged'}, metadata={'user_id': 'bob'},
                         router_settings_override={'fallbacks': [{'fusion-standard': ['other']}]})
        result = await self.guard.async_pre_call_deployment_hook({'model': 'custom_openai/fusion-standard',
            'extra_headers': {'Authorization': 'caller-token'}, 'api_key': 'wrong-key',
            'metadata': {'subject': 'bob'}, 'user': 'bob', 'proxy_server_request': {'headers': {'Authorization': 'secret'}}}, 'acompletion')
        self.assertEqual(claims(result['extra_headers']['X-Fusion-Identity'])['sub'], 'alice')
        self.assertNotIn('caller-token', json.dumps(result))
        self.assertNotIn('metadata', result)
        self.assertNotIn('user', result)

    async def test_concurrent_users_have_separate_context(self):
        async def invoke(subject, tier):
            await self.begin(subject, tier)
            await asyncio.sleep(0)
            result = await self.guard.async_pre_call_deployment_hook({'model': 'custom_openai/fusion-' + tier}, 'acompletion')
            return claims(result['extra_headers']['X-Fusion-Identity'])['sub'], result['model']
        result = await asyncio.gather(invoke('alice', 'standard'), invoke('bob', 'high'))
        self.assertEqual(result, [('alice', 'custom_openai/fusion-standard'), ('bob', 'custom_openai/fusion-high')])

    async def test_model_discovery_contains_only_authorized_alias(self):
        result = await self.guard.async_filter_listed_models(auth(), ['anthropic/*', 'fusion-standard', 'fusion-max'])
        self.assertEqual(result, ['fusion-standard'])

    def test_invalid_base_keys_and_timeout_rejected(self):
        for base in ('http://remote/v1', 'https://user@host/v1', 'https://host/wrong'):
            with self.assertRaises(ValueError):
                guard_module.FusionGatewayGuard(base, 's' * 40, 'i' * 40)
        for key in ('short', 'github_pat_' + 's' * 40, 's' * 40):
            with self.assertRaises(ValueError):
                guard_module.FusionGatewayGuard('https://fusion.example/v1', 's' * 40, key)
        for timeout in ('NaN', 'Infinity', '0', '231'):
            with patch.dict(os.environ, {'FUSION_TIMEOUT_SECONDS': timeout}), self.assertRaises(ValueError):
                guard_module.FusionGatewayGuard('https://fusion.example/v1', 's' * 40, 'i' * 40)


class Gateway:
    url = 'https://gateway.example'
    def __init__(self):
        self.rows = load_catalog()['models']
        self.callback = 'fusion-gateway-guard/v1 api_base=https://fusion.example/v1'
        self.writes = []
        self.key = None
        self.user = {'user_id': 'alice', 'user_role': 'internal_user', 'metadata': {'github_login': 'alice'}}

    def call(self, path, method='GET', body=None):
        if method != 'GET':
            self.writes.append((path, body))
        if path == '/health/readiness/details':
            return {'status': 'healthy', 'db': 'connected', 'litellm_version': '1.108.0'}
        if path == '/active/callbacks':
            return {'litellm.callbacks': [self.callback]}
        if path.startswith('/v2/model/info'):
            return {'data': copy.deepcopy(self.rows), 'total_pages': 1}
        if path.startswith('/user/info'):
            return {'user_info': self.user}
        if path == '/key/generate':
            self.key = body
            return {'key': 'sk-' + 'synthetic-personal-key'}
        if path == '/key/info':
            return {'info': self.key}
        raise AssertionError(path)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.api = Gateway()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'alice.invite.json'

    def tearDown(self):
        self.temp.cleanup()

    def enroll(self, apply=False):
        return onboarding.enroll(self.api, 'https://fusion.example/v1', 'alice', 'alice', self.path, apply=apply)

    def test_static_prepare_and_enroll_preview_never_mutate(self):
        onboarding.register(self.api, 'https://fusion.example/v1', apply=True)
        self.assertFalse(self.enroll()['applied'])
        self.assertEqual(self.api.writes, [])
        self.assertFalse(self.path.exists())

    def test_personal_key_has_no_provider_or_mcp_permissions(self):
        self.enroll(True)
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(self.api.key['models'], ['fusion-standard'])
        self.assertNotIn('router_settings', self.api.key)
        self.assertNotIn('/mcp', self.api.key['allowed_routes'])
        self.assertFalse(any('/credentials' in route for route in self.api.key['allowed_routes']))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with patch.object(onboarding, 'API', return_value=self.api):
            self.assertEqual(self.enroll(True)['action'], 'reused')
        self.assertEqual(len(self.api.writes), 1)

    def test_wrong_guard_route_user_rejected_before_mutation(self):
        for mutation in ('guard', 'catalog', 'user'):
            self.api = Gateway()
            if mutation == 'guard':
                self.api.callback = 'old-guard'
            elif mutation == 'catalog':
                self.api.rows.append({'model_name': 'anthropic/*'})
            else:
                self.api.user['user_role'] = 'proxy_admin'
            with self.assertRaises(Error):
                self.enroll(True)
            self.assertEqual(self.api.writes, [])

    def test_expired_or_tampered_grants_fail_closed(self):
        self.enroll(True)
        invite = read(self.path)
        original = copy.deepcopy(self.api.key)
        for field, value in [('user_id', 'bob'), ('models', ['*']), ('allowed_routes', onboarding.ROUTES + ['/mcp']),
                             ('router_settings', {'fallbacks': [{'fusion-standard': ['alternate']}]}),
                             ('aliases', {'fusion-standard': 'other'}), ('metadata', {})]:
            self.api.key = dict(original, **{field: value})
            with patch.object(onboarding, 'API', return_value=self.api), self.assertRaises(Error):
                onboarding.validate_key(invite)
        with patch.object(onboarding, 'API', side_effect=Error('HTTP 401 expired')), self.assertRaises(Error):
            onboarding.validate_key(invite)

    def test_no_shared_credential_or_legacy_invitation(self):
        self.enroll(True)
        invite = read(self.path)
        for field in ('credential', 'fallback_policy', 'github_token'):
            with self.assertRaises(Error):
                onboarding.validate_invitation(dict(invite, **{field: 'old'}))
