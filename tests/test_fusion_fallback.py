"""Offline tests for optional fallback enrollment and trusted routing state."""
import asyncio
import copy
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_engine'))
import developer
import fusion_fallback as onboarding
from gateway import Error, read


def load_guard():
    fake = types.ModuleType('litellm')
    class ProviderError(Exception):
        def __init__(self, message='', status_code=None, **kwargs):
            super().__init__(message)
            self.status_code = status_code
    for name in ('AuthenticationError', 'BadRequestError', 'ContentPolicyViolationError',
                 'ContextWindowExceededError', 'PermissionDeniedError', 'Timeout', 'APIConnectionError'):
        setattr(fake, name, type(name, (ProviderError,), {}))
    fake.APIError = ProviderError
    logger = types.ModuleType('litellm.integrations.custom_logger')
    class CustomLogger:
        def __init__(self, **kwargs): pass
    logger.CustomLogger = CustomLogger
    transport = types.ModuleType('httpx')
    transport.Request = lambda *args, **kwargs: None
    transport.Response = lambda *args, **kwargs: None
    spec = importlib.util.spec_from_file_location('tested_fusion_fallback_guard',
        ROOT / 'services/litellm-fusion-guard/fusion_fallback_guard.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {'litellm': fake, 'litellm.integrations.custom_logger': logger, 'httpx': transport}), patch.dict(os.environ, {
            'FUSION_FALLBACK_OWNER_SUBJECT': 'alice',
            'FUSION_FALLBACK_API_BASE': 'https://fusion.example/v1',
            'FUSION_FALLBACK_BEARER_TOKEN': 'fixture-service-bearer-' + 'x' * 32}):
        spec.loader.exec_module(module)
    return module, fake


guard_module, provider = load_guard()


def auth(owner='alice', tier='standard'):
    return {'user_id': owner, 'models': ['anthropic/*', 'fusion-fallback-' + tier],
            'metadata': {'fusion_fallback': onboarding.marker(owner, tier)}}


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.guard = guard_module.FusionFallbackGuard('alice', 'https://fusion.example/v1', 's' * 40)
        self.primary = {'model': 'anthropic/claude-sonnet-5-5'}
        self.target = {'model': 'custom_openai/fusion-standard'}

    async def begin(self, identity=None):
        await self.guard.async_pre_call_hook(auth() if identity is None else identity, None,
                                            {'model': 'claude-sonnet-5-5'}, 'anthropic_messages')
        await self.guard.async_pre_call_deployment_hook(self.primary, None)

    async def failure(self, exception):
        await self.guard.async_post_call_failure_deployment_hook(self.primary, exception, None)

    async def test_only_eligible_failures_allow_one_attempt(self):
        for status in (429, 500, 502, 503, 504):
            await self.begin()
            await self.failure(provider.APIError(status_code=status))
            result = await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
            self.assertEqual(result['model'], 'custom_openai/fusion-standard')
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_network_and_timeout_allowed(self):
        for error in (provider.Timeout, provider.APIConnectionError):
            await self.begin()
            await self.failure(error())
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_auth_input_policy_context_and_other_statuses_never_fallback(self):
        errors = [provider.APIError(status_code=code) for code in (400, 401, 403, 404, 409, 422, 501, 505)]
        errors += [kind(status_code=500) for kind in (provider.AuthenticationError, provider.BadRequestError,
                   provider.ContentPolicyViolationError, provider.ContextWindowExceededError, provider.PermissionDeniedError)]
        for error in errors:
            await self.begin()
            await self.failure(error)
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_no_fallback_without_primary_failure_or_after_output(self):
        await self.begin()
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
        await self.guard.async_post_call_streaming_deployment_hook(self.primary, {'delta': {}}, None)
        await self.failure(provider.APIError(status_code=429))
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_other_owner_legacy_key_direct_alias_and_forged_metadata_rejected(self):
        for identity in (auth('bob'), {'user_id': 'alice', 'models': ['anthropic/*'], 'metadata': {}}, None):
            await self.guard.async_pre_call_hook(identity, None, {'model': 'claude-sonnet-5-5',
                'metadata': {'fusion_fallback': onboarding.marker('alice', 'standard'), 'previous_models': [{}]}}, None)
            await self.guard.async_pre_call_deployment_hook(self.primary, None)
            await self.failure(provider.APIError(status_code=429))
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
        for name in ('fusion-fallback-standard', 'custom_openai/fusion-standard'):
            with self.assertRaises(provider.PermissionDeniedError):
                await self.guard.async_pre_call_hook(auth(), None, {'model': name}, None)

    async def test_credentials_headers_and_transport_overrides_removed(self):
        await self.begin()
        await self.failure(provider.APIError(status_code=429))
        incoming = dict(self.target, api_key='primary-secret', api_base='https://attacker.example',
                        extra_headers={'Authorization': 'Bearer primary-secret'}, headers={'x-api-key': 'primary-secret'},
                        extra_body={'api_key': 'primary-secret'}, litellm_credential_name='stolen',
                        thinking={'type': 'enabled'}, reasoning_effort='high', client=object(),
                        metadata={'key': 'primary-secret'}, litellm_metadata={'auth': 'primary-secret'},
                        proxy_server_request={'headers': {'Authorization': 'primary-secret'}},
                        litellm_params={'headers': {'Authorization': 'primary-secret'}},
                        messages=[{'role': 'user', 'content': 'history'}], tools=[{'type': 'function'}])
        result = await self.guard.async_pre_call_deployment_hook(incoming, 'acompletion')
        self.assertEqual(result['api_key'], 's' * 40)
        self.assertEqual(result['api_base'], 'https://fusion.example/v1')
        self.assertEqual(result['extra_headers'], {})
        for field in ('headers', 'extra_body', 'litellm_credential_name', 'thinking', 'reasoning_effort', 'client'):
            self.assertNotIn(field, result)
        self.assertEqual(result['messages'], incoming['messages'])
        self.assertEqual(result['tools'], incoming['tools'])
        self.assertNotIn('primary-secret', json.dumps(result))

    async def test_messages_conversion_allows_adapter_then_exactly_one_provider(self):
        await self.begin()
        await self.failure(provider.APIError(status_code=429))
        await self.guard.async_pre_call_deployment_hook(self.target, 'anthropic_messages')
        await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_opt_in_timeouts_and_mock_or_routing_overrides(self):
        await self.begin()
        primary = await self.guard.async_pre_call_deployment_hook(dict(self.primary, timeout=999, max_retries=9), None)
        self.assertEqual(primary['timeout'], 60)
        self.assertEqual(primary['max_retries'], 0)
        await self.failure(provider.APIError(status_code=429))
        fallback = await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
        self.assertEqual(fallback['timeout'], 140)
        for field in ('mock_response', 'mock_testing_fallbacks', 'mock_timeout', 'fallbacks', 'router_settings',
                      'api_base', 'custom_llm_provider', 'callbacks'):
            with self.assertRaises(provider.BadRequestError):
                await self.guard.async_pre_call_hook(auth(), None, {'model': 'primary', field: 'forged'}, None)
        data = {'model': 'legacy', 'mock_response': 'legacy unchanged'}
        self.assertIs(await self.guard.async_pre_call_hook({'metadata': {}}, None, data, None), data)

    async def test_request_router_override_replaced_by_authenticated_policy(self):
        identity = dict(auth(), router_settings={'num_retries': 4, 'fallbacks': [{'primary': ['fusion-fallback-standard']}]})
        data = await self.guard.async_pre_call_hook(identity, None,
            {'model': 'primary', 'router_settings_override': {'fallbacks': [{'primary': ['attacker']}]}}, None)
        self.assertEqual(data['router_settings_override']['fallbacks'], identity['router_settings']['fallbacks'])
        self.assertEqual(data['router_settings_override']['num_retries'], 0)
        self.assertEqual(data['router_settings_override']['max_fallbacks'], 1)
        self.assertEqual(data['router_settings_override']['content_policy_fallbacks'], [])
        self.assertEqual(data['router_settings_override']['context_window_fallbacks'], [])
        with self.assertRaises(provider.BadRequestError):
            await self.guard.async_pre_call_hook(identity, None, {'model': {}}, None)

    async def test_child_task_inherits_mutable_state_but_requests_are_isolated(self):
        async def one(owner):
            await self.begin(auth(owner))
            async def attempt():
                await asyncio.sleep(0)
                await self.failure(provider.APIError(status_code=429))
                try:
                    await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
                    return True
                except provider.PermissionDeniedError:
                    return False
            return await asyncio.create_task(attempt())
        self.assertEqual(await asyncio.gather(one('alice'), one('bob'), one('alice')), [True, False, True])

    async def test_second_failure_cannot_upgrade_auth_denial(self):
        await self.begin()
        await self.failure(provider.AuthenticationError())
        await self.failure(provider.APIError(status_code=429))
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')

    async def test_wrong_tier_and_wrong_failure_model_rejected(self):
        await self.begin()
        await self.guard.async_post_call_failure_deployment_hook({'model': 'openai/other'}, provider.APIError(status_code=429), None)
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook(self.target, 'acompletion')
        await self.failure(provider.APIError(status_code=429))
        with self.assertRaises(provider.PermissionDeniedError):
            await self.guard.async_pre_call_deployment_hook({'model': 'openai/fusion-max'}, None)

    def test_configuration_requires_private_service_secret_and_safe_base(self):
        for base in ('http://remote.example/v1', 'https://user:password@host/v1', 'https://host/v1?key=secret'):
            with self.assertRaises(ValueError):
                guard_module.FusionFallbackGuard('alice', base, 's' * 40)
        with self.assertRaises(ValueError):
            guard_module.FusionFallbackGuard('alice', 'https://host/v1', 'github_pat_' + 's' * 40)
        self.assertNotIn('s' * 40, repr(self.guard))
        for primary, fallback in [('NaN', '140'), ('60', 'Infinity'), ('0', '140'), ('100', '140')]:
            with patch.dict(os.environ, {'FUSION_FALLBACK_PRIMARY_TIMEOUT_SECONDS': primary,
                                        'FUSION_FALLBACK_TIMEOUT_SECONDS': fallback}), self.assertRaises(ValueError):
                guard_module.FusionFallbackGuard('alice', 'https://host/v1', 's' * 40)


class Gateway:
    url = 'https://gateway.example'
    def __init__(self):
        self.calls = []
        self.rows = [{'model_name': 'anthropic/*'}]
        self.callback = 'fusion-fallback-guard/v1 owner=alice api_base=https://fusion.example/v1'
        self.user = {'user_id': 'alice', 'user_role': 'internal_user', 'metadata': {'github_login': 'alice'}}
        self.key = None
    def call(self, path, method='GET', body=None):
        self.calls.append((path, method, copy.deepcopy(body)))
        if path == '/health/readiness/details':
            return {'status': 'healthy', 'db': 'connected', 'litellm_version': '1.108.0'}
        if path == '/active/callbacks': return {'litellm.callbacks': [self.callback]}
        if path.startswith('/user/info'): return {'user_info': copy.deepcopy(self.user)}
        if path.startswith('/v2/model/info'): return {'data': copy.deepcopy(self.rows), 'total_pages': 1}
        if path == '/model/new':
            self.rows.append(copy.deepcopy(body))
            return {'status': 'success'}
        if path == '/key/generate':
            self.key = copy.deepcopy(body)
            return {'key': 'sk-unit-test-placeholder', 'expires': '2099-01-01'}
        if path == '/key/info': return {'info': copy.deepcopy(self.key)}
        raise AssertionError((path, method, body))


class EnrollmentTests(unittest.TestCase):
    def setUp(self): self.api = Gateway()
    def test_pinned_gateway_policy_normalization_is_narrow(self):
        expected = onboarding.policy('standard')
        stored = {key: value for key, value in expected.items()
                  if key not in ('max_fallbacks', 'content_policy_fallbacks')}
        self.assertTrue(onboarding.stored_policy_matches(stored, expected))
        for changed in (dict(stored, max_fallbacks=2), dict(stored, content_policy_fallbacks=[{'x': ['y']}]),
                        dict(stored, unknown=True), dict(stored, fallbacks=[])):
            self.assertFalse(onboarding.stored_policy_matches(changed, expected))
    def register(self, **options):
        return onboarding.register(self.api, 'https://fusion.example/v1', 'alice', single_user_attested=True, **options)
    def enroll(self, path, **options):
        return onboarding.enroll(self.api, 'https://fusion.example/v1', 'alice', 'standard', path,
                                  single_user_attested=True, **options)
    def test_preview_no_mutation_or_secret_then_idempotent_registration(self):
        self.register()
        self.assertTrue(all(method == 'GET' for _, method, _ in self.api.calls))
        self.register(apply=True)
        self.assertEqual(len(self.api.rows), 4)
        self.assertEqual(self.register(apply=True)['models'], [])
        for row in self.api.rows[1:]:
            self.assertEqual(row['litellm_params']['api_key'], 'os.environ/FUSION_FALLBACK_BEARER_TOKEN')
            self.assertFalse(row['model_name'].startswith('anthropic/'))
    def test_guard_owner_and_existing_conflicts_block_before_write(self):
        for marker in ('', 'fusion-fallback-guard/v1 owner=bob api_base=https://fusion.example/v1'):
            self.api.callback = marker
            with self.assertRaises(Error): self.register(apply=True)
        self.assertTrue(all(method == 'GET' for _, method, _ in self.api.calls))
    def test_restricted_new_key_invitation_reuse_and_no_existing_changes(self):
        self.register(apply=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owner.invite.json'
            self.enroll(path, apply=True)
            invite = read(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.api.key['models'], ['anthropic/*', 'fusion-fallback-standard'])
            self.assertEqual(self.api.key['router_settings'], onboarding.policy('standard'))
            self.assertFalse(any(path == '/key/update' or path == '/user/update' for path, _, _ in self.api.calls))
            with patch.object(onboarding, 'API', return_value=self.api):
                onboarding.validate_key(invite)
                self.assertEqual(self.enroll(path, apply=True)['action'], 'reuse')
            self.assertNotIn('FUSION_FALLBACK_BEARER_TOKEN', path.read_text())
    def test_policy_has_no_direct_copilot_targets_and_no_context_fallback(self):
        policy = onboarding.policy('high')
        self.assertEqual(policy['num_retries'], 0)
        self.assertEqual(policy['context_window_fallbacks'], [])
        self.assertEqual(policy['content_policy_fallbacks'], [])
        for entry in policy['fallbacks'][:-1]:
            self.assertEqual(next(iter(entry.values())), ['fusion-fallback-high'])
    def test_invalid_key_policy_or_additive_grants_rejected(self):
        self.register(apply=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invite.json'
            self.enroll(path, apply=True)
            invite = read(path)
            original = copy.deepcopy(self.api.key)
            for field, value in (('user_id', 'bob'), ('models', ['*']), ('object_permission', {'mcp_servers': ['all']}),
                                 ('router_settings', {'fallbacks': []})):
                self.api.key = dict(original, **{field: value})
                with patch.object(onboarding, 'API', return_value=self.api), self.assertRaises(Error):
                    onboarding.validate_key(invite)
    def test_configure_preserves_subscription_auth_and_restore(self):
        self.register(apply=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invite.json'
            self.enroll(path, apply=True)
            target = Path(directory) / '.claude'
            target.mkdir()
            original = {'permissions': {'allow': ['Read']}, 'env': {'UNRELATED': 'keep'}}
            (target / 'settings.json').write_text(json.dumps(original))
            auth_file = target / '.credentials.json'
            auth_file.write_text('{"synthetic_subscription_auth":"unchanged"}')
            with patch.object(onboarding, 'API', return_value=self.api), patch.object(developer, 'preflight'):
                onboarding.connect(read(path), target)
            self.assertEqual(auth_file.read_text(), '{"synthetic_subscription_auth":"unchanged"}')
            configured = read(target / 'settings.json')
            self.assertNotIn('ANTHROPIC_AUTH_TOKEN', configured['env'])
            self.assertEqual(configured['env']['UNRELATED'], 'keep')
            developer.undo(target)
            self.assertEqual(read(target / 'settings.json'), original)


if __name__ == '__main__': unittest.main()
