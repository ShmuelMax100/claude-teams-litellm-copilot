#!/usr/bin/env python3
"""Test the real pinned gateway and MCP service with synthetic, network-isolated providers.

Build fusion-mcp:local first. This creates and removes only its unique Compose
project; no Azure login, provider credentials, live prompts or pilot state.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_engine'))
from gateway import API, Error, private_write, read
import fusion
import fusion_fallback


def expect_error(action):
    try:
        action()
    except Error:
        return
    raise AssertionError('An unauthorized operation unexpectedly succeeded')


def wait_ready(action, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = action()
            if result:
                return result
        except (Error, OSError, ValueError):
            pass
        time.sleep(2)
    raise AssertionError('Synthetic test services did not become ready')


def http_status(url, headers=None):
    request = urllib.request.Request(url, data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        headers=dict({'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}, **(headers or {})))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def call(invite, tier, prompt='synthetic-example'):
    client = fusion.MCPClient(invite)
    try:
        client.initialize()
        return client.request('tools/call', {'name': invite['server_alias'] + '-fusion_' + tier,
                                            'arguments': {'prompt': prompt}})
    finally:
        client.close()


def denied(invite):
    """MCP may deny at handshake or return an empty catalog; calls must fail."""
    client = fusion.MCPClient(invite)
    try:
        client.initialize()
        listing = client.request('tools/list', {})
        assert not listing.get('tools'), 'Unauthorized key discovered Fusion tools'
        try:
            response = client.request('tools/call', {'name': 'fusion-fusion_standard', 'arguments': {'prompt': 'forbidden'}})
        except Error:
            return
        assert response.get('isError'), 'Unauthorized key invoked Fusion'
    except Error:
        pass
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='fusion-mcp:local')
    args = parser.parse_args()
    project = 'fusion-qa-' + uuid.uuid4().hex[:12]
    checks = []
    with tempfile.TemporaryDirectory(prefix='fusion-qa-') as temporary:
        state = Path(temporary)
        state.chmod(0o700)
        admin_key = 'sk-' + secrets.token_hex(24)
        service_key = secrets.token_urlsafe(40)
        db_password = secrets.token_hex(24)
        values = {
            'db.env': f'POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD={db_password}\n',
            'gateway.env': f'DATABASE_URL=postgresql://fixture:{db_password}@db:5432/fixture\nLITELLM_MASTER_KEY={admin_key}\nLITELLM_SALT_KEY={secrets.token_hex(32)}\nSTORE_MODEL_IN_DB=True\nLITELLM_LOCAL_MODEL_COST_MAP=True\nPYTHONPATH=/qa\nFUSION_FALLBACK_OWNER_SUBJECT=qa-owner\nFUSION_FALLBACK_API_BASE=http://127.0.0.1:8080/v1\nFUSION_FALLBACK_BEARER_TOKEN={service_key}\nFUSION_FALLBACK_PRIMARY_TIMEOUT_SECONDS=5\nFUSION_FALLBACK_TIMEOUT_SECONDS=15\n',
            'fusion.env': f'FUSION_AUTH_MODE=single_user\nFUSION_SINGLE_USER_SUBJECT=qa-owner\nFUSION_GITHUB_TOKEN=github_pat_fixture\nFUSION_MCP_TOKEN={service_key}\nFUSION_TIMEOUT_SECONDS=2\nFUSION_ENABLE_FALLBACK=true\nCOPILOT_PROVIDER_API_KEY=ambient-must-not-reach-runtime\nGH_TOKEN=ambient-must-not-reach-runtime\n',
        }
        for name, value in values.items():
            path = state / name
            path.write_text(value)
            path.chmod(0o600)
        private_write(state / 'config.json', {
            'model_list': [],
            'general_settings': {'master_key': 'os.environ/LITELLM_MASTER_KEY',
                'database_url': 'os.environ/DATABASE_URL', 'store_model_in_db': True, 'trusted_proxy_ranges': []},
            'litellm_settings': {'turn_off_message_logging': True, 'callbacks': ['fusion_fallback_guard.guard']}})
        environment = dict(os.environ, FUSION_QA_DIR=str(state), FUSION_QA_IMAGE=args.image)
        command = ['docker', 'compose', '-p', project, '-f', str(Path(__file__).with_name('compose.yaml'))]

        def compose(*arguments):
            result = subprocess.run(command + list(arguments), env=environment, capture_output=True, text=True)
            if result.returncode:
                # Container logs and rendered environment can contain ephemeral secrets.
                raise RuntimeError('Synthetic Compose operation failed: ' + ' '.join(arguments[:2]))
            return result.stdout.strip()

        def events():
            data = compose('exec', '-T', 'fusion', 'python', '-c',
                "from pathlib import Path; p=Path('/tmp/qa-events.jsonl'); print(p.read_text() if p.exists() else '')")
            return [json.loads(line) for line in data.splitlines() if line]

        def inference_events():
            return [item for item in events() if item['phase'] == 'send']

        def inference_wire():
            data = compose('exec', '-T', 'fusion', 'python', '-c',
                "from pathlib import Path; p=Path('/tmp/qa-inference-wire.jsonl'); print(p.read_text() if p.exists() else '')")
            return [json.loads(line) for line in data.splitlines() if line]

        try:
            print('Starting fresh network-isolated gateway, database and mock Fusion service.', flush=True)
            compose('up', '-d', '--no-build')
            gateway_port = compose('port', 'ingress', '4000').splitlines()[0].rsplit(':', 1)[1]
            fusion_port = compose('port', 'ingress', '8080').splitlines()[0].rsplit(':', 1)[1]
            gateway_url = 'http://127.0.0.1:' + gateway_port
            fusion_url = 'http://127.0.0.1:' + fusion_port
            print('Checking loopback fixture ports ' + gateway_port + ' and ' + fusion_port + '.', flush=True)
            api = API(gateway_url, admin_key)
            wait_ready(lambda: fusion.check_gateway(api))
            wait_ready(lambda: json.load(urllib.request.urlopen(fusion_url + '/health/ready', timeout=5)))
            checks.append('gateway_database_and_service_readiness')
            for user in ('qa-owner', 'qa-other'):
                api.call('/user/new', 'POST', {'user_id': user, 'user_role': 'internal_user',
                    'auto_create_key': False, 'send_invite_email': False,
                    'models': ['anthropic/*', 'qa-primary', 'qa-alternate'],
                    'metadata': {'github_login': 'synthetic-owner' if user == 'qa-owner' else 'synthetic-other'}})
            preview = fusion.register(api, 'http://127.0.0.1:8080/mcp', 'qa-owner', single_user_attested=True)
            assert preview['applied'] is False
            registered = fusion.register(api, 'http://127.0.0.1:8080/mcp', 'qa-owner', token=service_key,
                apply=True, single_user_attested=True)
            server_id = registered['server_id']
            reused = fusion.register(api, 'http://127.0.0.1:8080/mcp', 'qa-owner', single_user_attested=True)
            assert reused['action'] == 'reuse' and reused['server_id'] == server_id
            checks.append('native_registration_preview_apply_and_reuse')
            expect_error(lambda: fusion.enroll(api, server_id, 'qa-other', state / 'wrong.invite.json', apply=True))
            invitation_path = state / 'owner.invite.json'
            fusion.enroll(api, server_id, 'qa-owner', invitation_path, apply=True)
            invite = read(invitation_path)
            assert fusion.enroll(api, server_id, 'qa-owner', invitation_path, apply=True)['action'] == 'reuse'
            assert invitation_path.stat().st_mode & 0o777 == 0o600
            checks.append('owner_only_enrollment_and_key_reuse')
            tools = fusion.discover(invite)
            assert len(tools['tools']) == 3 and tools['model_requests'] == 0 and inference_events() == []
            assert not [item for item in events() if item['phase'] == 'create']
            checks.append('native_mcp_handshake_discovery_no_inference')
            assert http_status(gateway_url + '/mcp') in (401, 403)
            assert http_status(fusion_url + '/mcp') == 401
            assert http_status(fusion_url + '/mcp', {'Authorization': 'Bearer wrong'}) == 401
            assert http_status(fusion_url + '/mcp', {'Authorization': 'Bearer ' + service_key, 'Origin': 'https://untrusted.example'}) == 403
            for routes, permission in ((['/v1/models', '/key/info'], {'mcp_servers': [server_id]}),
                                       (fusion.ROUTES, None),
                                       (fusion.ROUTES, {'mcp_servers': ['no-mcp-servers']})):
                body = {'user_id': 'qa-other', 'models': ['no-default-models'], 'allowed_routes': routes}
                if permission:
                    body['object_permission'] = permission
                key = api.call('/key/generate', 'POST', body)['key']
                denied(dict(invite, user_id='qa-other', key=key))
            assert inference_events() == [], 'Unauthorized or discovery operations reached SDK inference'
            checks.append('unauthenticated_inference_only_ungranted_and_explicit_deny_rejected')
            for tier in ('standard', 'high', 'max'):
                result = call(invite, tier)
                output = result['structuredContent']
                assert not result.get('isError') and output['ok'] and output['tier'] == tier
                assert output['output'] == 'SYNTHETIC_FUSION_' + tier.upper()
            history = events()
            created = [item for item in history if item['phase'] == 'create']
            assert len(created) == 3 and len({item['working_directory'] for item in created}) == 3
            assert len({item['session_id'] for item in created}) == 3
            assert len({item['client_id'] for item in created}) == 1
            assert len([item for item in history if item['phase'] == 'start']) == 1
            assert {item['session_id'] for item in created} == {
                item['session_id'] for item in history if item['phase'] == 'delete'}
            checks.append('all_three_tiers_reuse_runtime_with_fresh_deleted_sessions')
            result = call(invite, 'standard', 'synthetic-provider-failure')
            assert result.get('isError') and result['structuredContent']['status'] == 'provider_error'
            result = call(invite, 'standard', 'synthetic-redaction')
            assert result['structuredContent']['output'] == '[REDACTED]'
            assert call(invite, 'standard')['structuredContent']['ok']
            checks.append('provider_failure_isolation_and_redaction')
            result = call(invite, 'high', 'synthetic-timeout')
            assert result.get('isError') and result['structuredContent']['status'] == 'timeout'
            assert call(invite, 'standard')['structuredContent']['ok']
            history = events()
            aborted = {item['session_id'] for item in history if item['phase'] == 'abort'}
            deleted = {item['session_id'] for item in history if item['phase'] == 'delete'}
            assert aborted and aborted <= deleted
            checks.append('sdk_timeout_aborts_deletes_and_recovers')
            with contextlib.ExitStack() as stack:
                from unittest.mock import patch
                stack.enter_context(patch.dict(os.environ, {}, clear=False))
                os.environ.pop('CLAUDE_CONFIG_DIR', None)
                home = state / 'client'
                before = {'theme': 'keep', 'mcpServers': {'unrelated': {'type': 'http', 'url': 'https://example.invalid/mcp'}}}
                private_write(home / '.claude.json', before)
                fusion.install(invite, home)
                assert fusion.install(invite, home)['action'] == 'unchanged'
                installed = read(home / '.claude.json')
                installed['later'] = 'preserve'
                private_write(home / '.claude.json', installed)
                fusion.uninstall(home)
                assert read(home / '.claude.json') == dict(before, later='preserve')
            checks.append('authorized_client_install_idempotence_and_targeted_uninstall')
            # Exercise database model management and native fallback without any provider egress.
            for alias, model in (('qa-primary', 'generic'), ('qa-alternate', 'alternate')):
                api.call('/model/new', 'POST', {'model_name': alias, 'litellm_params': {
                    'model': 'openai/' + model, 'api_base': 'http://fixture:8080/v1',
                    'api_key': 'synthetic', 'max_retries': 0}, 'model_info': {'cooldown_time': 0}})
            wait_ready(lambda: {'qa-primary', 'qa-alternate'} <= {item['id'] for item in api.call('/v1/models')['data']})
            for disabled in (False, True):
                key = api.call('/key/generate', 'POST', {'user_id': 'qa-owner', 'models': ['qa-primary', 'qa-alternate'],
                    'allowed_routes': ['/v1/chat/completions'], 'metadata': {'disable_fallbacks': disabled},
                    'router_settings': {'num_retries': 0, 'fallbacks': [{'qa-primary': ['qa-alternate']}]}})['key']
                personal = API(gateway_url, key)
                action = lambda: personal.call('/v1/chat/completions', 'POST', {'model': 'qa-primary',
                    'messages': [{'role': 'user', 'content': 'LOCAL_SYNTHETIC_CHECK'}], 'max_tokens': 8})
                if disabled:
                    expect_error(action)
                else:
                    assert action()['choices'][0]['message']['content'] == 'LOCAL_OK'
            checks.append('database_model_registration_and_existing_fallback_policy')

            # Exercise the actual optional administrator and owner enrollment
            # against PostgreSQL-backed routes, not manually invented key state.
            api.call('/model/new', 'POST', {'model_name': 'anthropic/*', 'litellm_params': {
                'model': 'anthropic/*', 'api_base': 'http://primary:8080',
                'api_key': 'synthetic-primary-api-key', 'max_retries': 0}, 'model_info': {'cooldown_time': 0}})
            upstream = 'http://127.0.0.1:8080/v1'
            wait_ready(lambda: any(row.get('model_name') == 'anthropic/*' for row in
                api.call('/v2/model/info').get('data', [])))
            preview = fusion_fallback.register(api, upstream, 'qa-owner', single_user_attested=True)
            assert len(preview['models']) == 3 and not preview['applied']
            fusion_fallback.register(api, upstream, 'qa-owner', apply=True, single_user_attested=True)
            wait_ready(lambda: len([row for row in api.call('/v2/model/info').get('data', [])
                if row.get('model_name', '').startswith('fusion-fallback-')]) == 3)
            assert fusion_fallback.register(api, upstream, 'qa-owner', single_user_attested=True)['models'] == []
            fallback_invitation = state / 'fallback.invite.json'
            fusion_fallback.enroll(api, upstream, 'qa-owner', 'standard', fallback_invitation,
                apply=True, single_user_attested=True)
            fallback_invite = read(fallback_invitation)
            fusion_fallback.validate_key(fallback_invite)
            assert fusion_fallback.enroll(api, upstream, 'qa-owner', 'standard', fallback_invitation,
                apply=True, single_user_attested=True)['action'] == 'reuse'
            expect_error(lambda: fusion_fallback.enroll(api, upstream, 'qa-other', 'standard', state / 'bad-fallback.json',
                apply=True, single_user_attested=True))
            checks.append('guarded_fallback_registration_enrollment_and_owner_validation')

            def message(key, content='SYNTHETIC_PRIMARY_429', **changes):
                body = dict({'model': 'claude-sonnet-5-5', 'max_tokens': 4096,
                    'messages': [{'role': 'user', 'content': content}]}, **changes)
                request = urllib.request.Request(gateway_url + '/v1/messages', data=json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json', 'x-litellm-api-key': 'Bearer ' + key,
                        'Authorization': 'Bearer synthetic-primary-oauth', 'anthropic-version': '2023-06-01'})
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        raw = response.read().decode()
                        return response.status, raw if body.get('stream') else json.loads(raw)
                except urllib.error.HTTPError as error:
                    return error.code, error.read().decode()

            for code in (200, 429, 500, 502, 503, 504, 400, 401, 403):
                before = len(inference_events())
                status, response = message(fallback_invite['key'], 'SYNTHETIC_PRIMARY_' + str(code))
                expected = code in (429, 500, 502, 503, 504)
                assert len(inference_events()) - before == int(expected), (code, status)
                if expected or code == 200:
                    assert status == 200, (code, status)
                    assert response['content'][0]['text'] == ('SYNTHETIC_FUSION_STANDARD' if expected else 'SYNTHETIC_PRIMARY_OK')
                else:
                    assert status >= 400
            checks.append('primary_success_eligible_failures_and_auth_input_denial')

            for wire in inference_wire():
                encoded = json.dumps(wire)
                for forbidden in ('synthetic-primary-api-key', 'synthetic-primary-oauth',
                                  'github_pat_fixture', fallback_invite['key']):
                    assert forbidden not in encoded, 'Primary or end-user credential reached the SDK service'
                headers = dict(wire['headers'])
                assert headers.get('authorization') == 'Bearer ' + service_key
            checks.append('fallback_wire_uses_only_independent_service_bearer')

            before = len(inference_events())
            # The backup can only be reached after an actual eligible primary
            # failure for this authenticated owner key, never directly or by
            # supplying eligibility in request JSON.
            assert message(fallback_invite['key'], model='fusion-fallback-standard')[0] >= 400
            for override in ({'mock_response': 'fake'}, {'mock_testing_fallbacks': True},
                             {'fallbacks': [{'claude-sonnet-5-5': ['fusion-fallback-standard']}]},
                             {'api_base': 'http://primary:8080'},
                             {'extra_body': {'mock_testing_fallbacks': True, 'mock_response': 'fake'}},
                             {'router_settings_override': {'fallbacks': [{'claude-sonnet-5-5': ['fusion-fallback-standard']}]}}):
                # The proxy may discard unsupported body fields before hooks.
                # Neither rejection nor ignoring them may fabricate eligibility.
                status, response = message(fallback_invite['key'], 'SYNTHETIC_PRIMARY_200', **override)
                assert status >= 400 or response['content'][0]['text'] == 'SYNTHETIC_PRIMARY_OK'
            for user, metadata in (('qa-other', {'fusion_fallback': fusion_fallback.marker('qa-owner', 'standard')}),
                                   ('qa-owner', {})):
                key = api.call('/key/generate', 'POST', {'user_id': user,
                    'models': ['anthropic/*', 'fusion-fallback-standard'], 'allowed_routes': ['/v1/messages'],
                    'metadata': metadata, 'router_settings': fusion_fallback.policy('standard')})['key']
                assert message(key, metadata={'fusion_fallback': fusion_fallback.marker('qa-owner', 'standard')})[0] >= 400
            assert len(inference_events()) == before
            checks.append('direct_alias_fabricated_failure_other_user_and_forged_metadata_denied')

            tool = {'name': 'Read', 'description': 'Client reads a file', 'input_schema': {
                'type': 'object', 'properties': {'path': {'type': 'string'}},
                'required': ['path'], 'additionalProperties': False}}
            initial_messages = [{'role': 'user', 'content': 'SYNTHETIC_PRIMARY_429 SYNTHETIC_REQUEST_CLIENT_TOOL'}]
            before = len(inference_events())
            status, first = message(fallback_invite['key'], messages=initial_messages, tools=[tool],
                system='Synthetic original system instructions')
            assert status == 200 and first['stop_reason'] == 'tool_use', (status, str(first)[:150])
            request = next(block for block in first['content'] if block['type'] == 'tool_use')
            assert request['name'] == 'Read' and request['input'] == {'path': 'src/example.py'}
            following = initial_messages + [{'role': 'assistant', 'content': first['content']},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': request['id'],
                    'content': 'SYNTHETIC_CLIENT_TOOL_RESULT'}]}]
            status, second = message(fallback_invite['key'], messages=following, tools=[tool],
                system='Synthetic original system instructions')
            assert status == 200 and second['content'][0]['text'] == 'SYNTHETIC_TOOL_RESULT_ACCEPTED'
            calls = inference_events()[before:]
            assert len(calls) == 2 and calls[0]['session_id'] != calls[1]['session_id']
            assert 'SYNTHETIC_CLIENT_TOOL_RESULT' in calls[1]['prompt'] and '"role":"tool"' in calls[1]['prompt']
            completed = {item['session_id'] for item in events() if item['phase'] == 'delete'}
            assert all(item['session_id'] in completed for item in calls)
            checks.append('anthropic_client_tool_round_trip_uses_fresh_deleted_sessions')

            before = len(inference_events())
            status, streamed = message(fallback_invite['key'], stream=True)
            frames = [json.loads(line[6:]) for line in streamed.splitlines() if line.startswith('data: {')]
            assert status == 200 and any(frame.get('type') == 'message_stop' for frame in frames)
            assert any(frame.get('delta', {}).get('text') == 'SYNTHETIC_FUSION_STANDARD' for frame in frames)
            status, streamed = message(fallback_invite['key'], messages=initial_messages, tools=[tool], stream=True)
            frames = [json.loads(line[6:]) for line in streamed.splitlines() if line.startswith('data: {')]
            assert status == 200 and any(frame.get('content_block', {}).get('name') == 'Read' for frame in frames)
            assert any(frame.get('delta', {}).get('stop_reason') == 'tool_use' for frame in frames)
            assert len(inference_events()) - before == 2
            checks.append('buffered_fallback_text_and_tools_convert_to_anthropic_streams')

            before = len(inference_events())
            status, streamed = message(fallback_invite['key'], 'SYNTHETIC_PRIMARY_STREAM_ERROR', stream=True)
            assert status == 200 and 'SYNTHETIC_PRIMARY_PARTIAL' in streamed
            assert 'SYNTHETIC_FUSION' not in streamed and len(inference_events()) == before
            checks.append('partial_primary_stream_never_replayed_to_fusion')

            # Restart only the gateway, retaining this fixture's PostgreSQL.
            # Database-managed routes, MCP registration and personal grants
            # must survive an application restart without re-enrollment.
            compose('restart', 'gateway')
            wait_ready(lambda: fusion.check_gateway(api))
            # This fixture shares the gateway's network namespace for loopback
            # HTTP. Reattach its companion after Docker recreates that namespace.
            compose('restart', 'fusion')
            wait_ready(lambda: json.load(urllib.request.urlopen(fusion_url + '/health/ready', timeout=5)))
            wait_ready(lambda: {'qa-primary', 'qa-alternate'} <= {item['id'] for item in api.call('/v1/models')['data']})
            restored = wait_ready(lambda: fusion.discover(invite))
            assert len(restored['tools']) == 3 and restored['model_requests'] == 0
            fusion_fallback.validate_key(fallback_invite)
            status, restored_fallback = message(fallback_invite['key'])
            assert status == 200 and restored_fallback['content'][0]['text'] == 'SYNTHETIC_FUSION_STANDARD'
            checks.append('gateway_restart_preserves_database_models_mcp_and_fallback_grants')
            result = {'checks': checks, 'passed': len(checks), 'real_provider_requests': 0,
                      'azure_deployed': False, 'image': args.image, 'litellm_version': fusion.VERSION}
            print(json.dumps(result, indent=2), flush=True)
        finally:
            # Unique disposable project only: no existing/customer volumes can match.
            compose('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
