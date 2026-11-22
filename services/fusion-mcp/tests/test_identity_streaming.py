"""Independent request identities and incremental output contracts."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from auth import BearerGate, current_credential, verify_identity
from config import Config, UserCredential
from conftest import FakeSession, identity_headers
from copilot_runner import CopilotRunner
from inference import create_inference_routes
from inference_types import CompletionRequest, CompletionResult
from server import create_app
from stream_filter import DeltaRedactor, OutputFilter


def two_users(config):
    return replace(config, credentials={**config.credentials,
        'second-user': UserCredential('second-user', 'github_pat_' + 'second_user_only', int(time.time()) + 3600),
        'expired-user': UserCredential('expired-user', 'github_pat_' + 'expired_user_only', int(time.time()) - 1)})


def test_environment_maps_only_admin_named_individual_credentials(config):
    env = {'FUSION_SERVICE_TOKEN': config.service_token, 'FUSION_IDENTITY_KEY': config.identity_key,
        'FUSION_USER_CREDENTIALS_JSON': json.dumps({'owner-user-id': {'token_env': 'FUSION_GITHUB_USER_OWNER', 'expires_at': 123}}),
        'FUSION_GITHUB_USER_OWNER': config.credentials['owner-user-id'].token}
    resolved = Config.from_env(env)
    assert resolved.credentials['owner-user-id'].expires_at == 123  # Expiry is request-specific.
    for value in ('{}', '{"owner": {"token_env": "GITHUB_TOKEN", "expires_at": 123}}',
                  '{"owner": {"token_env": "FUSION_GITHUB_USER_OWNER"}}',
                  '{"owner":{},"owner":{}}'):
        with pytest.raises(ValueError):
            Config.from_env({**env, 'FUSION_USER_CREDENTIALS_JSON': value})
    with pytest.raises(ValueError, match='distinct'):
        replace(config, credentials={**config.credentials, 'second-user': UserCredential(
            'second-user', config.credentials['owner-user-id'].token, 123)})


@pytest.mark.parametrize('claims', [
    {'aud': 'other'}, {'sub': 'not-enrolled'}, {'exp': 1}, {'iat': lambda now: now + 30},
    {'iat': False}, {'exp': True}, {'v': True}, {'exp': lambda now: now + 61},
    {'jti': ''}, {'jti': '\n'}, {'unexpected': 'value'},
])
def test_invalid_or_unknown_assertions_never_start_sdk(config, factory, claims, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr('auth.time.time', lambda: now)
    claims = {name: value(now) if callable(value) else value for name, value in claims.items()}
    with TestClient(create_app(config)) as client:
        response = client.post('/v1/chat/completions', headers=identity_headers(config, **claims),
            json={'model': 'fusion-standard', 'messages': [{'role': 'user', 'content': 'hello'}]})
    assert response.status_code in (401, 403)
    assert factory.clients[0].sessions == []
    assert config.identity_key not in response.text


def test_assertion_signature_duplicate_claims_and_security_headers(config, factory):
    headers = identity_headers(config)
    value = headers['X-Fusion-Identity']
    payload = base64.urlsafe_b64encode(b'{"v":1,"v":1}').rstrip(b'=').decode()
    signature = base64.urlsafe_b64encode(hmac.new(config.identity_key.encode(),
        ('v1.' + payload).encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    invalid = [value[:-2] + 'AA', value + '=', 'v2.' + value[3:], 'v1.' + payload + '.' + signature]
    with TestClient(create_app(config)) as client:
        for assertion in invalid:
            response = client.post('/mcp', headers={**headers, 'X-Fusion-Identity': assertion})
            assert response.status_code == 401
        for duplicate in ('Authorization', 'X-Fusion-Identity'):
            response = client.post('/mcp', headers=[*headers.items(), (duplicate, headers[duplicate])])
            assert response.status_code == 401
    assert factory.clients[0].sessions == []


def test_auth_expiry_is_per_user_and_mcp_requires_delegation(config, factory):
    config = two_users(config)
    with TestClient(create_app(config)) as client:
        for subject in ('owner-user-id', 'second-user', 'expired-user'):
            headers = {**identity_headers(config, subject), 'Accept': 'application/json, text/event-stream'}
            response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1,
                'method': 'tools/call', 'params': {'name': 'fusion_standard', 'arguments': {'prompt': 'hello'}}})
            if subject == 'expired-user':
                assert response.status_code == 401 and response.json()['error']['code'] == 'credential_expired'
            else:
                assert response.status_code == 200
                assert response.json()['result']['structuredContent']['ok']
        no_delegation = client.post('/mcp', headers={'Authorization': 'Bearer ' + config.service_token})
        assert no_delegation.status_code == 401
    sessions = factory.clients[0].sessions
    assert len(sessions) == 2
    assert [session.options['github']['token'] for session in sessions] == [
        config.credentials[user].token for user in ('owner-user-id', 'second-user')]
    assert sessions[0].session_id != sessions[1].session_id


async def test_parallel_users_have_explicit_tokens_and_isolated_sessions(config, factory):
    config = two_users(config)
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    a, b = await asyncio.gather(*(runner.run('standard', 'slow', credential=config.credentials[user])
        for user in ('owner-user-id', 'second-user')))
    assert a.ok and b.ok
    sessions = factory.clients[0].sessions
    assert len({session.options['github']['token'] for session in sessions}) == 2
    assert len({session.options['working_directory'] for session in sessions}) == 2
    assert set(factory.clients[0].deleted) == {a.request_id, b.request_id}
    expired = await runner.run('high', 'hello', credential=config.credentials['expired-user'])
    assert expired.status == 'credential_expired' and len(sessions) == 2
    await runner.close()


@pytest.mark.parametrize('text', ['github_pat_' + 'unregistered_long_value', 'gho_oauth_shape', 'ghu_app_shape'])
def test_redaction_catches_arbitrary_token_chunk_boundaries(config, text):
    for offset in range(1, len(text)):
        redactor = DeltaRedactor([config.service_token, config.identity_key, config.credentials['owner-user-id'].token])
        result = redactor.feed('prefix ' + text[:offset])
        result += redactor.feed(text[offset:] + ' suffix')
        result += redactor.feed('', final=True)
        assert result == 'prefix [REDACTED] suffix'


def test_known_secrets_cannot_leak_at_any_split(config):
    for secret in (config.service_token, config.identity_key, config.credentials['owner-user-id'].token):
        for offset in range(1, len(secret)):
            redactor = DeltaRedactor([secret])
            result = redactor.feed('before ' + secret[:offset])
            result += redactor.feed(secret[offset:] + ' after') + redactor.feed('', final=True)
            assert result == 'before [REDACTED] after'


def test_stream_stop_and_utf8_output_bounds():
    output = OutputFilter(100, ['STOP'])
    assert output.feed('hello ST') == 'hello '
    assert output.feed('OP ignored') == '' and output.stopped
    output = OutputFilter(5, [])
    assert output.feed('ééé') == 'éé' and output.limited


async def asgi_call(app, config, body, on_send):
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
        await asyncio.Event().wait()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
        'http_version': '1.1', 'method': 'POST', 'scheme': 'http', 'path': '/v1/chat/completions',
        'raw_path': b'/v1/chat/completions', 'query_string': b'', 'root_path': '',
        'headers': [(key.lower().encode(), value.encode()) for key, value in identity_headers(config).items()],
        'server': ('fixture', 80), 'client': ('fixture', 123)}
    await app(scope, receive, on_send)


async def test_http_text_arrives_before_sdk_finishes_and_post_start_failure_is_explicit(config, factory, monkeypatch):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    release, arrived = asyncio.Event(), asyncio.Event()
    async def send(session, prompt):
        async def produce():
            session.emit('assistant.message_delta', delta_content='first incremental text ')
            await release.wait()
            session.emit('session.error', error_type='token_expired', message='do not relay me')
        session.producer = asyncio.create_task(produce())
    monkeypatch.setattr(FakeSession, 'send', send)
    app = Starlette(routes=create_inference_routes(config, runner))
    app.add_middleware(BearerGate, config=config)
    wire = []
    async def consume(message):
        wire.append(message)
        if b'first incremental text' in message.get('body', b''):
            assert not release.is_set()
            arrived.set()
    task = asyncio.create_task(asgi_call(app, config, {'model': 'fusion-standard', 'stream': True,
        'messages': [{'role': 'user', 'content': 'hello'}]}, consume))
    await asyncio.wait_for(arrived.wait(), 1)
    assert not task.done() and not factory.clients[0].deleted
    release.set()
    await asyncio.wait_for(task, 2)
    response = b''.join(message.get('body', b'') for message in wire).decode()
    assert 'event: error' in response and 'credential_expired' in response
    assert '[DONE]' not in response and 'do not relay me' not in response
    assert factory.clients[0].sessions[0].aborted and factory.clients[0].deleted
    await runner.close()


async def test_slow_stream_consumer_is_bounded_and_cleans_session(config, factory, monkeypatch):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        for _ in range(100):
            session.emit('assistant.message_delta', delta_content='safe text ')
        session.emit('assistant.message', content='safe text ' * 100)
        session.emit('session.idle')
    monkeypatch.setattr(FakeSession, 'send', send)
    app = Starlette(routes=create_inference_routes(config, runner))
    app.add_middleware(BearerGate, config=config)
    wire = []
    async def consume(message):
        wire.append(message)
    await asgi_call(app, config, {'model': 'fusion-standard', 'stream': True,
        'messages': [{'role': 'user', 'content': 'hello'}]}, consume)
    content = b''.join(message.get('body', b'') for message in wire)
    assert b'event: error' in content and b'output_limit' in content
    assert b'[DONE]' not in content and len(wire) < 40
    assert factory.clients[0].sessions[0].aborted and factory.clients[0].deleted
    await runner.close()


@pytest.mark.parametrize('disconnect_at', ['headers', 'body'])
async def test_http_disconnect_always_cancels_and_deletes_sdk_session(config, factory, monkeypatch, disconnect_at):
    from starlette.requests import ClientDisconnect
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        async def produce():
            session.emit('assistant.message_delta', delta_content='initial text before client disconnect')
            await asyncio.Event().wait()
        session.producer = asyncio.create_task(produce())
    monkeypatch.setattr(FakeSession, 'send', send)
    app = Starlette(routes=create_inference_routes(config, runner))
    app.add_middleware(BearerGate, config=config)
    async def disconnected(message):
        if message['type'] == ('http.response.start' if disconnect_at == 'headers' else 'http.response.body'):
            raise OSError('Client connection closed')
    with pytest.raises((OSError, ClientDisconnect)):
        await asgi_call(app, config, {'model': 'fusion-standard', 'stream': True,
            'messages': [{'role': 'user', 'content': 'hello'}]}, disconnected)
    session = factory.clients[0].sessions[0]
    assert session.aborted and session.disconnected and session.session_id in factory.clients[0].deleted
    assert runner._active == 0
    await runner.close()


@pytest.mark.parametrize('status,expected', [(401, 'authentication_error'), (403, 'access_denied')])
async def test_structured_upstream_auth_status_is_sanitized(config, factory, monkeypatch, status, expected):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        session.emit('session.error', error_type='provider_error', status_code=status,
                     message=config.credentials['owner-user-id'].token)
    monkeypatch.setattr(FakeSession, 'send', send)
    result = await runner.complete(CompletionRequest('standard', 'hello', ''))
    assert result.status == expected and not result.ok
    assert config.credentials['owner-user-id'].token not in result.model_dump_json()
    assert len(factory.clients[0].sessions) == 1
    await runner.close()


def test_prefix_overlapping_service_secrets_fail_before_runtime(config):
    with pytest.raises(ValueError, match="prefix-overlap"):
        replace(config, service_token="A" * 32, identity_key="A" * 32 + "BBBB")


async def test_expiry_during_health_check_does_not_delete_an_uncreated_session(config, factory, monkeypatch):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    credential = config.credentials['owner-user-id']
    clock = [credential.expires_at - 1]
    monkeypatch.setattr('copilot_runner.time.time', lambda: clock[0])

    async def healthy_but_now_expired():
        clock[0] = credential.expires_at
        return True

    monkeypatch.setattr(runner, 'check_health', healthy_but_now_expired)
    result = await runner.complete(CompletionRequest('standard', 'hello', ''), credential=credential)
    client = factory.clients[0]
    assert result.status == 'credential_expired'
    assert client.sessions == [] and client.deleted == []
    assert runner.capable and runner._active == 0 and client.stops == 0
    await runner.close()
