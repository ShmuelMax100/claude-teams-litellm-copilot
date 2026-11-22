import asyncio
from types import SimpleNamespace
import pytest
from config import Config, UserCredential
from auth import current_credential
import base64
import hashlib
import hmac
import json
import time
import uuid


class FakeSession:
    def __init__(self, client, options):
        self.client, self.options = client, options
        self.session_id = options['session_id']
        self.callbacks = []
        self.aborted = self.disconnected = False
        self.producer = None

    def on(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def emit(self, kind, **data):
        for callback in self.callbacks[:]:
            callback(SimpleNamespace(type=SimpleNamespace(value=kind), data=SimpleNamespace(**data)))

    async def send(self, prompt):
        self.client.prompts.append(prompt)
        async def produce():
            if prompt == 'sleep':
                await asyncio.Event().wait()
            elif prompt == 'fail':
                self.emit('session.error', message=self.options['github']['token'])
            elif prompt == 'tools':
                self.emit('external_tool.requested', tool_name='shell')
            else:
                if prompt == 'slow':
                    await asyncio.sleep(.1)
                output = ('x' * 300000 if prompt == 'overflow' else
                          self.options['github']['token'] if prompt == 'secrets' else 'mock answer')
                self.emit('assistant.message_delta', delta_content=output[:5])
                self.emit('assistant.message_delta', delta_content=output[5:])
                self.emit('assistant.message', content=output)
                self.emit('session.idle')
        self.producer = asyncio.create_task(produce())
        return 'message-id'

    async def abort(self):
        self.aborted = True
        if self.producer:
            self.producer.cancel()
            await asyncio.gather(self.producer, return_exceptions=True)

    async def disconnect(self):
        self.disconnected = True


class FakeClient:
    def __init__(self, **options):
        self.options = options
        self.sessions, self.deleted, self.prompts = [], [], []
        self.starts = self.stops = 0
        self.version = '1.0.114'

    async def start(self): self.starts += 1
    async def ping(self): return SimpleNamespace(message='pong')
    async def get_status(self): return SimpleNamespace(version=self.version, protocol_version=4)
    async def stop(self): self.stops += 1
    async def force_stop(self): self.stops += 1
    async def delete_session(self, session_id): self.deleted.append(session_id)
    async def create_session(self, **options):
        session = FakeSession(self, options)
        self.sessions.append(session)
        return session


@pytest.fixture
def config(tmp_path):
    return Config(service_token='service-test-only-' + 'x' * 32, identity_key='identity-test-only-' + 'y' * 32,
        credentials={'owner-user-id': UserCredential('owner-user-id', 'github_pat_testonly_0123456789', int(time.time()) + 3600)},
        workspace_root=tmp_path / 'workspaces', timeout_seconds=2)


@pytest.fixture
def factory():
    clients = []
    def create(**options):
        client = FakeClient(**options)
        clients.append(client)
        return client
    create.clients = clients
    return create


@pytest.fixture(autouse=True)
def mock_server_boundary(monkeypatch, factory):
    from copilot_runner import CopilotRunner
    monkeypatch.setattr('server.CopilotRunner', lambda config: CopilotRunner(config, client_factory=factory))


def identity_headers(config, subject="owner-user-id", **claims):
    data = dict(v=1, aud="fusion-mcp", sub=subject, iat=int(time.time()), exp=int(time.time()) + 60, jti=uuid.uuid4().hex)
    data.update(claims)
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    payload = encode(json.dumps(data, separators=(",", ":")).encode())
    signature = encode(hmac.new(config.identity_key.encode(), ("v1." + payload).encode(), hashlib.sha256).digest())
    return {"Authorization": "Bearer " + config.service_token, "X-Fusion-Identity": f"v1.{payload}.{signature}"}


@pytest.fixture(autouse=True)
def direct_runner_identity(config):
    reset = current_credential.set(config.credentials["owner-user-id"])
    yield
    current_credential.reset(reset)
