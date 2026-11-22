#!/usr/bin/env python3
"""Actual pinned LiteLLM/Postgres + production service with an offline SDK fixture.

No cloud changes, live provider requests, host credentials or developer settings.
Only a uniquely named Compose project is created and removed.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import hmac
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
import developer
import fusion_gateway


def request(url, body=None, headers=None, timeout=30):
    supplied = {'Content-Type': 'application/json', **(headers or {})}
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=supplied)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), dict(response.headers)


def wait_ready(action, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if action():
                return
        except (Error, OSError, ValueError):
            pass
        time.sleep(2)
    raise AssertionError('Synthetic test services did not become ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='fusion-mcp:local')
    args = parser.parse_args()
    project = 'fusion-qa-' + uuid.uuid4().hex[:12]
    checks = []
    policy_failures = []
    with tempfile.TemporaryDirectory(prefix='fusion-qa-') as temporary:
        state = Path(temporary)
        state.chmod(0o700)
        master = 'sk-' + secrets.token_hex(24)
        bearer = secrets.token_urlsafe(40)
        signing = secrets.token_urlsafe(40)
        db_password = secrets.token_hex(24)
        upstream = 'http://127.0.0.1:8080/v1'
        aliases = ['fusion-' + tier for tier in ('standard', 'high', 'max')]
        users = {name: {'token_env': 'FUSION_GITHUB_USER_' + name.upper(),
                        'expires_at': int(time.time()) + (3600 if name != 'expired' else -1)}
                 for name in ('alice', 'bob', 'expired')}
        tokens = {name: 'github_pat_fixture_' + name for name in users}
        user_map = {'qa-' + name: value for name, value in users.items()}
        values = {
            'db.env': f'POSTGRES_USER=fixture\nPOSTGRES_DB=fixture\nPOSTGRES_PASSWORD={db_password}\n',
            'gateway.env': f'DATABASE_URL=postgresql://fixture:{db_password}@db:5432/fixture\nLITELLM_MASTER_KEY={master}\nLITELLM_SALT_KEY={secrets.token_hex(32)}\nSTORE_MODEL_IN_DB=False\nLITELLM_LOCAL_MODEL_COST_MAP=True\nPYTHONPATH=/qa\nFUSION_API_BASE={upstream}\nFUSION_SERVICE_TOKEN={bearer}\nFUSION_IDENTITY_KEY={signing}\nFUSION_TIMEOUT_SECONDS=15\n',
            'fusion.env': f'FUSION_SERVICE_TOKEN={bearer}\nFUSION_IDENTITY_KEY={signing}\nFUSION_USER_CREDENTIALS_JSON={json.dumps(user_map)}\nFUSION_TIMEOUT_SECONDS=4\nCOPILOT_PROVIDER_API_KEY=ambient-must-not-reach-runtime\nGH_TOKEN=ambient-must-not-reach-runtime\n' + ''.join(f'{users[name]["token_env"]}={token}\n' for name, token in tokens.items()),
        }
        for name, value in values.items():
            path = state / name
            path.write_text(value)
            path.chmod(0o600)
        private_write(state / 'config.json', {
            'model_list': [{'model_name': name, 'litellm_params': {
                'model': 'custom_openai/' + name, 'api_base': upstream,
                'api_key': 'os.environ/FUSION_SERVICE_TOKEN', 'max_retries': 0}}
                for name in aliases],
            'general_settings': {'master_key': 'os.environ/LITELLM_MASTER_KEY',
                'database_url': 'os.environ/DATABASE_URL', 'store_model_in_db': False, 'trusted_proxy_ranges': [],
                'forward_client_headers_to_llm_api': False},
            'router_settings': {'disable_cooldowns': True, 'num_retries': 0, 'max_fallbacks': 0, 'fallbacks': [], 'context_window_fallbacks': [], 'content_policy_fallbacks': []},
            'litellm_settings': {'turn_off_message_logging': True, 'callbacks': ['fusion_gateway_guard.guard']}})
        environment = dict(os.environ, FUSION_QA_DIR=str(state), FUSION_QA_IMAGE=args.image)
        command = ['docker', 'compose', '-p', project, '-f', str(Path(__file__).with_name('compose.yaml'))]

        def compose(*arguments):
            result = subprocess.run(command + list(arguments), env=environment, capture_output=True, text=True)
            if result.returncode:
                # No raw output: rendered env/logs may contain ephemeral test secrets.
                raise RuntimeError('Synthetic Compose operation failed: ' + ' '.join(arguments[:2]))
            return result.stdout.strip()

        def records(filename):
            data = compose('exec', '-T', 'fusion', 'python', '-c',
                "from pathlib import Path; p=Path('/tmp/" + filename + "'); print(p.read_text() if p.exists() else '')")
            return [json.loads(line) for line in data.splitlines() if line]

        def events():
            return records('qa-events.jsonl')

        def sends():
            return [event for event in events() if event['phase'] == 'send']

        def identity(subject='qa-alice', *, expired=False, key=None):
            now = int(time.time())
            claims = {'v': 1, 'aud': 'fusion-mcp', 'sub': subject, 'iat': now - (90 if expired else 0),
                      'exp': now - 30 if expired else now + 60, 'jti': uuid.uuid4().hex}
            enc = lambda data: base64.urlsafe_b64encode(data).rstrip(b'=').decode()
            payload = enc(json.dumps(claims, separators=(',', ':')).encode())
            unsigned = 'v1.' + payload
            signature = enc(hmac.new((key or signing).encode(), unsigned.encode(), hashlib.sha256).digest())
            return {'Authorization': 'Bearer ' + bearer, 'X-Fusion-Identity': unsigned + '.' + signature}

        invitations = {}

        def messages(who='alice', prompt='synthetic-example', *, stream=False, extra=None, headers=None):
            invite = invitations[who]
            body = {'model': invite['default_model'], 'max_tokens': 2048,
                    'messages': [{'role': 'user', 'content': prompt}], 'stream': stream, **(extra or {})}
            return request(gateway_url + '/v1/messages', body,
                {'Authorization': 'Bearer ' + invite['key'], 'anthropic-version': '2023-06-01', **(headers or {})})

        def mcp(method, params=None, headers=None):
            return request(fusion_url + '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
                {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-03-26',
                 **(identity() if headers is None else headers)})

        def passed(name):
            checks.append(name)
            print('PASS: ' + name, flush=True)

        try:
            print('Starting isolated pinned LiteLLM, Postgres and synthetic Fusion SDK.', flush=True)
            compose('up', '-d', '--no-build')
            gateway_url = 'http://127.0.0.1:' + compose('port', 'ingress', '4000').splitlines()[0].rsplit(':', 1)[1]
            fusion_url = 'http://127.0.0.1:' + compose('port', 'ingress', '8080').splitlines()[0].rsplit(':', 1)[1]
            api = API(gateway_url, master)
            wait_ready(lambda: api.call('/health/readiness/details').get('db') == 'connected')
            wait_ready(lambda: request(fusion_url + '/health/ready')[0] == 200)
            result = fusion_gateway.register(api, upstream)
            assert result['model_requests'] == 0 and not sends()
            passed('healthy_static_fusion_catalog_and_guard')

            for name in (*users, 'unknown'):
                subject = 'qa-' + name
                api.call('/user/new', 'POST', {'user_id': subject, 'user_role': 'internal_user',
                    'auto_create_key': False, 'send_invite_email': False,
                    'models': aliases, 'metadata': {'github_login': 'synthetic-' + name}})
                destination = state / (name + '.invite.json')
                preview = fusion_gateway.enroll(api, upstream, subject, 'synthetic-' + name, destination)
                assert preview['applied'] is False and not destination.exists()
                fusion_gateway.enroll(api, upstream, subject, 'synthetic-' + name, destination, apply=True)
                invitations[name] = read(destination)
                fusion_gateway.validate_key(invitations[name])
                assert destination.stat().st_mode & 0o777 == 0o600
            assert not sends()
            passed('personal_enrollment_and_validation_without_inference')

            before = len(sends())
            status, body, _ = request(gateway_url + '/v1/messages/count_tokens', {
                'model': 'fusion-standard', 'messages': [{'role': 'user', 'content': 'synthetic-count'}]},
                {'Authorization': 'Bearer ' + invitations['alice']['key']})
            count = json.loads(body)
            assert status == 200 and isinstance(count.get('input_tokens'), int) and count['input_tokens'] >= 0, ('count tokens', status)
            # Anthropic's response may expose only input_tokens. This isolated
            # network and unchanged SDK count prove it did not call a provider.
            assert count.get('count_source') in (None, 'local_estimate')
            assert len(sends()) == before
            status, _, _ = request(gateway_url + '/v1/messages/count_tokens', {
                'model': 'anthropic/claude', 'messages': [{'role': 'user', 'content': 'synthetic-count'}]},
                {'Authorization': 'Bearer ' + invitations['alice']['key']})
            assert status in (400, 401, 403) and len(sends()) == before
            passed('local_count_tokens_without_provider_request_and_authorization_bypass')

            status, body, _ = messages()
            assert status == 200, ('valid request', status)
            assert 'SYNTHETIC_FUSION_STANDARD' in body.decode()
            assert len(sends()) == 1, 'First request must invoke Fusion exactly once'
            passed('first_request_uses_fusion')
            for tier in ('high', 'max'):
                name = 'alice-' + tier
                destination = state / (name + '.invite.json')
                fusion_gateway.enroll(api, upstream, 'qa-alice', 'synthetic-alice', destination, tier=tier, apply=True)
                invitations[name] = read(destination)
                status, body, _ = messages(name)
                assert status == 200 and ('SYNTHETIC_FUSION_' + tier.upper()).encode() in body
                assert sends()[-1]['tier'] == tier
            passed('every_public_tier_selects_matching_sdk_fusion_tier')

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda name: messages(name), ('alice', 'bob')))
            assert all(result[0] == 200 for result in results)
            created = [event for event in events() if event['phase'] == 'create']
            expected_hashes = {hashlib.sha256(tokens[name].encode()).hexdigest() for name in ('alice', 'bob')}
            assert {event['credential_hash'] for event in created} == expected_hashes
            assert len({event['session_id'] for event in created}) == len(created)
            assert len({event['working_directory'] for event in created}) == len(created)
            assert {event['session_id'] for event in created} <= {event['session_id'] for event in events() if event['phase'] == 'delete'}
            passed('concurrent_users_have_distinct_credentials_and_deleted_sessions')

            # Actual wire timing: the fixture delays completion 1.5 seconds after its first delta.
            req = urllib.request.Request(gateway_url + '/v1/messages', data=json.dumps({
                'model': 'fusion-standard', 'max_tokens': 2048, 'stream': True,
                'messages': [{'role': 'user', 'content': 'synthetic-slow-stream'}]}).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + invitations['alice']['key']})
            started = time.monotonic()
            first_text = None
            with urllib.request.urlopen(req, timeout=15) as response:
                lines = []
                for raw in response:
                    line = raw.decode()
                    lines.append(line)
                    if first_text is None and 'SYNTHETIC_STREAM_' in line:
                        first_text = time.monotonic() - started
                duration = time.monotonic() - started
            assert first_text is not None and duration - first_text >= 0.8, 'Stream was buffered until completion'
            assert 'message_stop' in ''.join(lines)
            passed('true_messages_streaming_before_sdk_completion')

            started = time.monotonic()
            compose('exec', '-T', 'fusion', 'python', '/qa/cancel_probe.py')
            cancelled_session = sends()[-1]['session_id']
            wait_ready(lambda: any(event['phase'] == 'delete' and event.get('session_id') == cancelled_session
                                   for event in events()), seconds=3)
            assert time.monotonic() - started < 4, 'Disconnect cleanup waited for the inference timeout'
            assert any(event['phase'] == 'abort' and event.get('session_id') == cancelled_session for event in events())
            assert messages()[0] == 200
            passed('client_disconnect_aborts_and_deletes_session_before_deadline')

            tools = [{'name': 'read_project_file', 'description': 'Read a local project file',
                      'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'], 'additionalProperties': False}}]
            status, body, _ = messages(prompt='SYNTHETIC_REQUEST_CLIENT_TOOL', extra={'tools': tools})
            reply = json.loads(body)
            assert status == 200 and reply['stop_reason'] == 'tool_use', (status, reply.get('stop_reason'))
            call = next(item for item in reply['content'] if item['type'] == 'tool_use')
            assert call['name'] == 'read_project_file' and call['input'] == {'path': 'src/example.py'}
            history = [{'role': 'user', 'content': 'SYNTHETIC_REQUEST_CLIENT_TOOL'},
                       {'role': 'assistant', 'content': reply['content']},
                       {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call['id'], 'content': 'SYNTHETIC_CLIENT_TOOL_RESULT'}]}]
            status, body, _ = messages(extra={'tools': tools, 'messages': history})
            assert status == 200 and b'SYNTHETIC_TOOL_RESULT_ACCEPTED' in body
            status, body, _ = messages(prompt='SYNTHETIC_REQUEST_CLIENT_TOOL', stream=True, extra={'tools': tools})
            assert status == 200 and b'tool_use' in body and b'read_project_file' in body
            passed('client_tool_round_trip_and_streamed_tool_call')

            for who in ('expired', 'unknown'):
                before = len(sends())
                status, _, _ = messages(who)
                assert status in (401, 403), (who, status)
                assert len(sends()) == before, 'Unapproved/expired credential reached SDK'
                assert messages('bob')[0] == 200, 'A user credential failure disabled another user shared route'
            assert messages('bob')[0] == 200
            api.call('/key/block', 'POST', {'key': invitations['alice']['key']})
            before = len(sends())
            assert messages()[0] in (401, 403)
            assert len(sends()) == before
            api.call('/key/unblock', 'POST', {'key': invitations['alice']['key']})
            passed('expired_unknown_and_revoked_identity_denied_without_cross_user_effect')

            for prompt in ('synthetic-provider-failure', 'synthetic-auth-failure', 'synthetic-timeout'):
                before = len(sends())
                status, body, _ = messages(prompt=prompt)
                assert status >= 400, (prompt, status)
                assert len(sends()) == before + 1, 'Failure retried or changed route'
                assert not any(token.encode() in body for token in (*tokens.values(), bearer, signing))
            passed('service_authentication_and_timeout_errors_do_not_retry_or_redirect')

            before = len(sends())
            status, body, _ = messages(prompt='synthetic-stream-failure', stream=True)
            assert status == 200 and b'SYNTHETIC_STREAM_' in body and b'error' in body.lower()
            assert len(sends()) == before + 1
            passed('mid_stream_failure_is_explicit_without_restart')

            for extra in ({'model': 'anthropic/claude'}, {'fallbacks': ['other-provider']},
                          {'api_base': 'https://forbidden.invalid/v1'}, {'context_management': {'edits': []}},
                          {'router_settings': {'num_retries': 99}}, {'num_retries': 99}, {'max_retries': 99},
                          {'max_fallbacks': 99}, {'disable_cooldowns': False}, {'allowed_fails_policy': {}},
                          {'cooldown_time': 999}, {'timeout': 999}, {'stream_timeout': 999}):
                before = len(sends())
                status, response_body, _ = messages(extra=extra)
                delta = len(sends()) - before
                if status < 400 or delta != 0:
                    failure = 'override allowed: ' + ','.join(extra) + ' (HTTP ' + str(status) + ')'
                    policy_failures.append(failure)
                    print('FAIL: ' + failure, flush=True)
            # Pinned LiteLLM intentionally strips test-only request controls before
            # callbacks. Prove they still execute Fusion once and cannot forge output.
            for extra, fabricated in (
                    ({'mock_response': 'FORGED_CLIENT_RESPONSE'}, b'FORGED_CLIENT_RESPONSE'),
                    ({'mock_tool_calls': [{'id': 'FORGED_CLIENT_TOOL', 'type': 'function',
                        'function': {'name': 'forged_tool', 'arguments': '{}'}}]}, b'FORGED_CLIENT_TOOL')):
                before = len(sends())
                status, body, _ = messages(extra=extra)
                assert status == 200 and b'SYNTHETIC_FUSION_STANDARD' in body and fabricated not in body
                assert len(sends()) == before + 1, 'Client test controls bypassed the actual Fusion route'
            passed('client_test_controls_cannot_fabricate_responses_or_tools')

            # Body user/metadata are not trusted identity; discarding them is safe.
            for extra in ({'user': 'qa-bob'}, {'metadata': {'user_id': 'qa-bob'}}):
                before = len(sends())
                status, _, _ = messages(extra=extra)
                assert status in (200, 400, 401, 403)
                if status == 200:
                    assert len(sends()) == before + 1
                    last = [event for event in events() if event['phase'] == 'create'][-1]
                    assert last['credential_hash'] == hashlib.sha256(tokens['alice'].encode()).hexdigest()
                else:
                    assert len(sends()) == before
            status, body, _ = messages(headers={'X-Fusion-Identity': 'spoof', 'X-Fusion-User': 'qa-bob', 'X-API-Key': 'fake'})
            # Incoming identity headers may be stripped or rejected, but never select another user.
            assert status in (200, 400, 401, 403)
            if status == 200:
                last = [event for event in events() if event['phase'] == 'create'][-1]
                assert last['credential_hash'] == hashlib.sha256(tokens['alice'].encode()).hexdigest()
            if not policy_failures:
                passed('provider_routing_and_identity_override_attempts_blocked')

            for headers in ({}, {'Authorization': 'Bearer ' + bearer}, identity(expired=True), identity(key='x' * 40)):
                status, _, _ = mcp('tools/list', headers=headers)
                assert status in (401, 403)
            assert mcp('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'offline-qa', 'version': '1'}})[0] == 200
            before = len(sends())
            status, body, _ = mcp('tools/list')
            names = {tool['name'] for tool in json.loads(body)['result']['tools']}
            assert status == 200 and names == {'fusion_standard', 'fusion_high', 'fusion_max'} and len(sends()) == before
            for tier in ('standard', 'high', 'max'):
                status, body, _ = mcp('tools/call', {'name': 'fusion_' + tier, 'arguments': {'prompt': 'synthetic-example'}})
                result = json.loads(body)['result']
                assert status == 200 and result['structuredContent']['ok']
                assert result['structuredContent']['output'] == 'SYNTHETIC_FUSION_' + tier.upper()
            passed('private_mcp_signed_discovery_and_all_tier_invocations')

            wire = records('qa-inference-wire.jsonl')
            for record in wire:
                headers = {name.lower(): value for name, value in record['headers']}
                assert headers.get('authorization') == 'Bearer ' + bearer
                assert headers.get('x-fusion-identity', '').startswith('v1.')
                assert record['body']['model'] in aliases
                rendered = json.dumps(record)
                assert not any(invite['key'] in rendered for invite in invitations.values())
                assert not any(token in rendered for token in tokens.values())
                assert signing not in rendered
            passed('private_wire_contains_no_client_keys_or_github_credentials')

            # Literal secret split over SDK deltas must never reach the client.
            for stream in (False, True):
                status, body, _ = messages(prompt='synthetic-redaction', stream=stream)
                assert status == 200 and tokens['alice'].encode() not in body
            passed('credential_redaction_in_complete_and_streamed_output')
            # Exercise the pinned gateway's expiry policy independently of PAT expiry.
            api.call('/key/update', 'POST', {'key': invitations['bob']['key'],
                'duration': '1s'})
            time.sleep(2)
            before = len(sends())
            status, body, _ = messages('bob')
            assert status in (401, 403) and b'expir' in body.lower(), ('gateway key expiry', status)
            assert len(sends()) == before
            assert messages()[0] == 200
            passed('expired_gateway_key_denied_without_affecting_other_users')
            compose('stop', '--timeout', '5', 'fusion')
            status, body, _ = messages()
            assert status >= 400 and not any(token.encode() in body for token in (*tokens.values(), bearer, signing))
            compose('start', 'fusion')
            wait_ready(lambda: request(fusion_url + '/health/ready')[0] == 200)
            assert not sends(), 'Gateway retried the failed request after service recovery'
            assert messages()[0] == 200
            passed('unavailable_service_fails_explicitly_without_retry_after_recovery')
            assert not policy_failures, '; '.join(policy_failures)
            print(json.dumps({'status': 'passed', 'groups': len(checks), 'checks': checks}, indent=2))
        except Exception:
            # Preserve only phase counts for diagnostics, not raw request content or credentials.
            with contextlib.suppress(Exception):
                history = events()
                print('Synthetic SDK phases: ' + json.dumps({name: sum(item['phase'] == name for item in history)
                    for name in ('start', 'create', 'send', 'abort', 'delete', 'stop')}), flush=True)
            raise
        finally:
            with contextlib.suppress(Exception):
                compose('down', '--volumes', '--remove-orphans', '--timeout', '10')


if __name__ == '__main__':
    main()
