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
            'gateway.env': f'DATABASE_URL=postgresql://fixture:{db_password}@db:5432/fixture\nLITELLM_MASTER_KEY={admin_key}\nLITELLM_SALT_KEY={secrets.token_hex(32)}\nSTORE_MODEL_IN_DB=True\nLITELLM_LOCAL_MODEL_COST_MAP=True\n',
            'fusion.env': f'FUSION_AUTH_MODE=single_user\nFUSION_SINGLE_USER_SUBJECT=qa-owner\nFUSION_GITHUB_TOKEN=github_pat_fixture\nFUSION_MCP_TOKEN={service_key}\nFUSION_COPILOT_BIN=/qa/mock-copilot\nCOPILOT_PROVIDER_API_KEY=ambient-must-not-reach-child\nGH_TOKEN=ambient-must-not-reach-child\n',
        }
        for name, value in values.items():
            path = state / name
            path.write_text(value)
            path.chmod(0o600)
        private_write(state / 'config.json', {
            'model_list': [],
            'general_settings': {'master_key': 'os.environ/LITELLM_MASTER_KEY',
                'database_url': 'os.environ/DATABASE_URL', 'store_model_in_db': True, 'trusted_proxy_ranges': []},
            'litellm_settings': {'turn_off_message_logging': True}})
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
                    'auto_create_key': False, 'send_invite_email': False})
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
            assert len(tools['tools']) == 3 and tools['model_requests'] == 0 and events() == []
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
            assert events() == [], 'Unauthorized or discovery operations reached CLI inference'
            checks.append('unauthenticated_inference_only_ungranted_and_explicit_deny_rejected')
            for tier in ('standard', 'high', 'max'):
                result = call(invite, tier)
                output = result['structuredContent']
                assert not result.get('isError') and output['ok'] and output['tier'] == tier
                assert output['output'] == 'SYNTHETIC_FUSION_' + tier.upper()
            history = events()
            assert len(history) == 3 and len({item['home'] for item in history}) == 3
            assert all(item['isolated'] for item in history)
            checks.append('all_three_tiers_invoked_through_gateway_with_isolated_environments')
            result = call(invite, 'standard', 'synthetic-provider-failure')
            assert result.get('isError') and result['structuredContent']['status'] == 'provider_error'
            result = call(invite, 'standard', 'synthetic-redaction')
            assert result['structuredContent']['output'] == '[REDACTED]'
            assert call(invite, 'standard')['structuredContent']['ok']
            checks.append('provider_failure_isolation_and_redaction')
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
            checks.append('gateway_restart_preserves_database_models_and_mcp_grants')
            result = {'checks': checks, 'passed': len(checks), 'real_provider_requests': 0,
                      'azure_deployed': False, 'image': args.image, 'litellm_version': fusion.VERSION}
            print(json.dumps(result, indent=2), flush=True)
        finally:
            # Unique disposable project only: no existing/customer volumes can match.
            compose('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
