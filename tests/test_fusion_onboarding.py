"""Offline coverage for optional Fusion grants, transport and settings isolation."""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_engine'))
import fusion
from gateway import API, Error, private_write, read


class Gateway:
    url = 'https://gateway.example'
    def __init__(self):
        self.calls = []
        self.servers = []
        self.version = fusion.VERSION
        self.db = 'connected'
    def call(self, path, method='GET', body=None):
        self.calls.append((path, method, copy.deepcopy(body)))
        if path == '/health/readiness/details':
            return {'status': 'healthy', 'db': self.db, 'litellm_version': self.version}
        if path.startswith('/user/info'):
            return {'user_info': {'user_id': 'alice', 'user_role': 'internal_user'}}
        if path == '/v1/mcp/server':
            if method == 'GET':
                return copy.deepcopy(self.servers)
            server = dict(body, server_id='server-1')
            self.servers.append(server)
            return copy.deepcopy(server)
        if path == '/v1/mcp/server/server-1':
            return copy.deepcopy(self.servers[0])
        if path == '/key/generate':
            return {'key': 'sk-fusion-fixture', 'expires': '2099-01-01'}
        raise AssertionError((path, method, body))


def invitation():
    return {'format': fusion.FORMAT, 'gateway': Gateway.url, 'user_id': 'alice',
            'server_id': 'server-1', 'server_alias': 'fusion', 'key': 'sk-fusion-fixture'}


def key_information():
    return {'user_id': 'alice', 'models': ['no-default-models'], 'allowed_routes': list(fusion.ROUTES),
            'object_permission': {'mcp_servers': ['server-1']},
            'metadata': {'onboarding_package': fusion.OWNER, 'single_user_subject': 'alice'}}


class FusionGrantTests(unittest.TestCase):
    def setUp(self):
        self.api = Gateway()
    def register(self, **options):
        return fusion.register(self.api, 'https://fusion.internal.example/mcp', 'alice',
                               token='service-fixture-' + 'a' * 32, single_user_attested=True, **options)
    def test_registration_preview_never_mutates_or_contains_secret(self):
        result = self.register()
        self.assertFalse(result['applied'])
        self.assertFalse(any(method != 'GET' for _, method, _ in self.api.calls))
        self.assertNotIn('service-fixture', json.dumps(result))
    def test_explicit_registration_persists_owner_and_is_private_by_permissions(self):
        result = self.register(apply=True)
        body = self.api.servers[0]
        self.assertEqual(result['server_id'], 'server-1')
        self.assertEqual(body['mcp_info']['single_user_subject'], 'alice')
        self.assertEqual(body['credentials']['auth_value'], 'service-fixture-' + 'a' * 32)
        self.assertFalse(body['allow_all_keys'])
        self.assertTrue(body['available_on_public_internet'])
        self.assertEqual(body['extra_headers'], [])
        self.assertEqual(body['auth_type'], 'bearer_token')
        self.assertEqual(body['timeout'], 150)
        self.assertNotIn('service-fixture', json.dumps(result))
        self.assertEqual(self.register(apply=True)['action'], 'reuse')
        self.assertEqual(len(self.api.servers), 1)
    def test_registration_requires_attestation_and_supported_version(self):
        with self.assertRaises(Error):
            fusion.register(self.api, 'https://fusion.example/mcp', 'alice')
        self.assertEqual(self.api.calls, [])
        self.api.version = 'other'
        with self.assertRaises(Error):
            self.register(apply=True)
        self.assertEqual(self.api.servers, [])
    def test_database_required(self):
        self.api.db = 'Not connected'
        with self.assertRaises(Error):
            self.register(apply=True)
        self.assertEqual(self.api.servers, [])
    def test_registration_timeout_is_bounded_and_conflicts_are_not_overwritten(self):
        for timeout in (0, 1831, float('nan'), True):
            with self.subTest(timeout=timeout), self.assertRaises(Error):
                self.register(timeout_seconds=timeout)
        self.assertEqual(self.api.calls, [])
        self.register(apply=True)
        with self.assertRaises(Error):
            self.register(apply=True, timeout_seconds=200)
        self.assertEqual(self.api.servers[0]['timeout'], 150)
    def test_conflicting_backend_owner_denied_even_with_different_alias(self):
        self.register(apply=True)
        self.api.servers[0]['mcp_info']['single_user_subject'] = 'bob'
        with self.assertRaises(Error):
            fusion.register(self.api, 'https://fusion.internal.example/mcp/', 'alice', alias='another',
                            apply=True, token='a' * 40, single_user_attested=True)
        self.assertEqual(len(self.api.servers), 1)
    def test_unrelated_server_alias_not_overwritten(self):
        self.api.servers = [{'server_id': 'other', 'alias': 'fusion', 'url': 'https://other.example/mcp'}]
        with self.assertRaises(Error):
            self.register(apply=True)
        self.assertEqual(len(self.api.servers), 1)
    def test_enrollment_only_owner_and_separate_key(self):
        self.register(apply=True)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'fusion.invite.json'
            with self.assertRaises(Error):
                fusion.enroll(self.api, 'server-1', 'bob', out, apply=True)
            self.assertFalse(out.exists())
            preview = fusion.enroll(self.api, 'server-1', 'alice', out)
            self.assertFalse(preview['applied'])
            self.assertFalse(out.exists())
            fusion.enroll(self.api, 'server-1', 'alice', out, apply=True)
            payload = next(body for path, _, body in self.api.calls if path == '/key/generate')
            self.assertEqual(payload['user_id'], 'alice')
            self.assertEqual(payload['object_permission'], {'mcp_servers': ['server-1']})
            self.assertEqual(payload['models'], ['no-default-models'])
            self.assertEqual(payload['allowed_routes'], fusion.ROUTES)
            self.assertNotIn('/v1/messages', payload['allowed_routes'])
            self.assertEqual(read(out)['format'], fusion.FORMAT)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
    def test_rejects_inference_key_additive_grants_and_wrong_owner(self):
        for mutation in ({'models': ['anthropic/*']}, {'user_id': 'bob'},
                         {'allowed_routes': ['/mcp', '/key/info']},
                         {'object_permission': {'mcp_servers': ['server-1'], 'mcp_access_groups': ['all']}},
                         {'object_permission': {'mcp_servers': ['server-1'], 'mcp_toolsets': ['extra-tools']}},
                         {'object_permission': {'mcp_servers': ['server-1'], 'mcp_tool_permissions': {'other-server': ['tool']}}},
                         {'access_group_ids': ['additive-grant']},
                         {'object_permission': {'mcp_servers': ['server-1', 'server-2']}},
                         {'metadata': {}}):
            info = dict(key_information(), **mutation)
            with self.subTest(mutation=mutation), patch.object(fusion, 'key_info', return_value=info), self.assertRaises(Error):
                fusion.validate_key(invitation())
    def test_no_live_execution_without_opt_in(self):
        with patch.object(fusion, 'discover') as discover, self.assertRaises(Error):
            fusion.run_tool(invitation(), 'standard', 'hello')
        discover.assert_not_called()
    def test_bad_endpoint_rejected(self):
        for url in ('http://public.example/mcp', 'https://name:secret@example/mcp', 'https://example/mcp?token=x',
                    'https://example/mcp#secret', 'https://example/sse'):
            with self.subTest(url=url), self.assertRaises(Error):
                fusion.endpoint(url)


class FusionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.account = self.home / '.claude.json'
        self.settings = self.home / '.claude' / 'settings.json'
        self.before = {'theme': 'dark', 'projects': {'/repo': {'mcpServers': {'managed': {'url': 'https://managed.example'}}}},
                       'mcpServers': {'other': {'type': 'http', 'url': 'https://other.example/mcp'}}}
        private_write(self.account, self.before)
        private_write(self.settings, {'env': {'ANTHROPIC_BASE_URL': 'https://inference.example'}})
        self.discovery = patch.object(fusion, 'discover', return_value={'tools': list(fusion.TOOLS), 'model_requests': 0})
        self.discovery.start()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
    def tearDown(self):
        self.environment.stop()
        self.discovery.stop()
        self.temp.cleanup()
    def test_preserves_settings_and_unrelated_mcp_entries_with_private_backup(self):
        settings = self.settings.read_bytes()
        result = fusion.install(invitation(), self.home)
        after = read(self.account)
        self.assertEqual(after['projects'], self.before['projects'])
        self.assertEqual(after['mcpServers']['other'], self.before['mcpServers']['other'])
        self.assertEqual(self.settings.read_bytes(), settings)
        self.assertEqual(read(result['backup']), self.before)
        self.assertEqual(self.account.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(result['backup']).stat().st_mode & 0o777, 0o600)
    def test_repeat_is_idempotent_and_uninstall_keeps_later_unrelated_edits(self):
        fusion.install(invitation(), self.home)
        self.assertEqual(fusion.install(invitation(), self.home)['action'], 'unchanged')
        self.assertEqual(len(list((self.home / '.claude' / 'backups').iterdir())), 1)
        after = read(self.account)
        after['theme'] = 'light'
        after['mcpServers']['new-server'] = {'type': 'http', 'url': 'https://new.example/mcp'}
        private_write(self.account, after)
        fusion.uninstall(self.home)
        restored = read(self.account)
        self.assertEqual(restored['theme'], 'light')
        self.assertIn('new-server', restored['mcpServers'])
        self.assertNotIn(fusion.LOCAL_NAME, restored['mcpServers'])
        self.assertEqual(fusion.uninstall(self.home)['action'], 'not-installed')
    def test_conflicting_registration_refused(self):
        changed = dict(self.before)
        changed['mcpServers'] = {fusion.LOCAL_NAME: {'url': 'https://other.example/mcp'}}
        private_write(self.account, changed)
        with self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        self.assertEqual(read(self.account), changed)
    def test_edited_entry_blocks_uninstall(self):
        fusion.install(invitation(), self.home)
        changed = read(self.account)
        changed['mcpServers'][fusion.LOCAL_NAME]['url'] = 'https://changed.example/mcp'
        private_write(self.account, changed)
        with self.assertRaises(Error):
            fusion.uninstall(self.home)
        self.assertEqual(read(self.account), changed)
    def test_denied_discovery_does_not_write(self):
        before = self.account.read_bytes()
        with patch.object(fusion, 'discover', side_effect=Error('Denied')), self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        self.assertEqual(self.account.read_bytes(), before)
        self.assertFalse((self.home / '.claude' / 'fusion-mcp-onboarding.json').exists())
    def test_symlink_and_custom_profile_refused(self):
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/managed'}), self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        self.account.unlink()
        self.account.symlink_to(self.settings)
        with self.assertRaises(Error):
            fusion.install(invitation(), self.home)
    def test_managed_policy_and_backup_symlink_refused(self):
        policy = self.home / '.claude' / 'managed-mcp.json'
        private_write(policy, {'mcpServers': {}})
        with self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        policy.unlink()
        elsewhere = self.home / 'elsewhere'
        elsewhere.mkdir()
        (self.home / '.claude' / 'backups').symlink_to(elsewhere)
        with self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        self.assertEqual(list(elsewhere.iterdir()), [])
    def test_current_project_mcp_collision_refused(self):
        project = self.home / 'project'
        project.mkdir()
        private_write(project / '.mcp.json', {'mcpServers': {fusion.LOCAL_NAME: {'url': 'https://project.example/mcp'}}})
        with patch.object(fusion.Path, 'cwd', return_value=project), self.assertRaises(Error):
            fusion.install(invitation(), self.home)
        self.assertEqual(read(self.account), self.before)
    def test_installed_key_can_be_recovered_for_discovery(self):
        fusion.install(invitation(), self.home)
        self.assertEqual(fusion.installed_invitation(self.home), invitation())


class FakeResponse:
    def __init__(self, body, content_type='application/json', headers=None):
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {'Content-Type': content_type, **(headers or {})}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.raw[:size]


class MCPTransportTests(unittest.TestCase):
    def test_tool_timeout_covers_max_service_duration_but_discovery_stays_bounded(self):
        client = fusion.MCPClient(invitation())
        for index, (method, timeout) in enumerate((('tools/list', 90), ('tools/call', 1830)), 1):
            response = FakeResponse({'jsonrpc': '2.0', 'id': index, 'result': {}})
            with patch.object(client.opener, 'open', return_value=response) as opened:
                client.request(method)
            self.assertEqual(opened.call_args.kwargs['timeout'], timeout)
    def test_malformed_key_rejected_without_exposing_it_or_sending_request(self):
        for key in ('sk-private-fixture\nX: hidden', 'sk-private-fixture\x00', 'sk-private fixture'):
            inv = dict(invitation(), key=key)
            with patch.object(fusion.urllib.request, 'build_opener') as opener:
                for construct in (lambda: fusion.MCPClient(inv), lambda: API(inv['gateway'], key)):
                    with self.assertRaises(Error) as caught:
                        construct()
                    self.assertNotIn(key, str(caught.exception))
                    self.assertNotIn('private-fixture', str(caught.exception))
                opener.assert_not_called()
    def test_handshake_discovery_and_session_headers_without_tool_calls(self):
        responses = iter([
            FakeResponse({'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': '2025-03-26'}}, headers={'Mcp-Session-Id': 'session-one'}),
            FakeResponse(b''),
            FakeResponse({'jsonrpc': '2.0', 'id': 3, 'result': {'tools': [{'name': 'fusion-' + name} for name in fusion.TOOLS]}}),
            FakeResponse(b''),
        ])
        calls = []
        def request(req, timeout):
            calls.append(req)
            return next(responses)
        with patch.object(fusion, 'validate_key'), patch.object(fusion.urllib.request.OpenerDirector, 'open', side_effect=request):
            result = fusion.discover(invitation())
        methods = [json.loads(req.data)['method'] for req in calls if req.data]
        self.assertEqual(methods, ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertEqual(calls[0].get_header('X-litellm-api-key'), 'Bearer sk-fusion-fixture')
        self.assertEqual(calls[2].get_header('Mcp-session-id'), 'session-one')
        self.assertEqual(calls[-1].method, 'DELETE')
        self.assertEqual(result['model_requests'], 0)
    def test_sse_response_and_protocol_error(self):
        client = fusion.MCPClient(invitation())
        data = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
        with patch.object(client.opener, 'open', return_value=FakeResponse(data, 'text/event-stream')):
            self.assertEqual(client.request('tools/list'), {'tools': []})
        with patch.object(client.opener, 'open', return_value=FakeResponse({'jsonrpc': '2.0', 'id': 99, 'result': {}})), self.assertRaises(Error):
            client.request('tools/list')
    def test_mcp_error_does_not_echo_sensitive_upstream_details(self):
        client = fusion.MCPClient(invitation())
        error = {'jsonrpc': '2.0', 'id': 1, 'error': {'message': 'secret provider details'}}
        with patch.object(client.opener, 'open', return_value=FakeResponse(error)), self.assertRaises(Error) as caught:
            client.request('tools/list')
        self.assertNotIn('secret provider', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
