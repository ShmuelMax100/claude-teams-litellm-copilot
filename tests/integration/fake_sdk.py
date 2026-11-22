"""Offline SDK boundary fixture; never imported by the production service.

The real runner owns authorization, session policy, events, limits and cleanup.
Only the external Copilot SDK client is substituted in the test entrypoint.
"""
import asyncio
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import uuid


class FakeCopilotClient:
    def __init__(self, *, runtime_path, environment, working_directory, base_directory,
                 event_path=None):
        self.client_id = uuid.uuid4().hex
        self.options = dict(runtime_path=runtime_path, environment=environment,
                            working_directory=working_directory, base_directory=base_directory)
        self.event_path = Path(event_path) if event_path else None
        self.events = []
        self.sessions = []
        self.started = 0
        self.stopped = 0
        self.deleted = []

    def record(self, phase, **data):
        item = dict(phase=phase, client_id=self.client_id, **data)
        self.events.append(item)
        if self.event_path:
            with self.event_path.open('a') as stream:
                stream.write(json.dumps(item) + '\n')

    async def start(self):
        self.started += 1
        self.record('start')

    async def ping(self, *args, **kwargs):
        self.record('ping')
        return SimpleNamespace(message='offline', protocol_version=4)

    async def get_status(self):
        self.record('status')
        return SimpleNamespace(version='1.0.114', protocol_version=4)

    async def create_session(self, **options):
        session = FakeSession(self, options)
        self.sessions.append(session)
        return session

    async def delete_session(self, session_id):
        self.deleted.append(session_id)
        self.record('delete', session_id=session_id)

    async def stop(self):
        self.stopped += 1
        self.record('stop')

    async def force_stop(self):
        self.stopped += 1
        self.record('force_stop')


class FakeSession:
    def __init__(self, client, options):
        self.client = client
        self.options = options
        self.session_id = options.get('session_id', uuid.uuid4().hex)
        self.callbacks = []
        self.producer = None
        self.aborted = False
        self.disconnected = False
        self.tier = options.get('github', {}).get('capi', {}).get('fusion_tier')
        self.client.record('create', session_id=self.session_id, tier=self.tier,
                           credential_hash=hashlib.sha256(options['github']['token'].encode()).hexdigest(),
                           working_directory=options.get('working_directory'))

    def on(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback) if callback in self.callbacks else None

    def emit(self, kind, **data):
        event = SimpleNamespace(type=SimpleNamespace(value=kind), data=SimpleNamespace(**data))
        for callback in self.callbacks[:]:
            callback(event)

    async def send(self, prompt=None, **kwargs):
        if isinstance(prompt, dict):
            prompt = prompt['prompt']
        prompt = prompt if prompt is not None else kwargs['prompt']
        self.client.record('send', session_id=self.session_id, tier=self.tier, prompt=prompt,
                           system_message=self.options.get('system_message'))

        async def produce():
            if 'SYNTHETIC_REQUEST_CLIENT_TOOL' in prompt and 'SYNTHETIC_CLIENT_TOOL_RESULT' not in prompt:
                definitions = self.options['tools']['definitions']
                assert len(definitions) == 1 and definitions[0].handler is None
                self.emit('assistant.message', content='', tool_requests=[SimpleNamespace(
                    name=definitions[0].name, tool_call_id='call_fixture_read', arguments={'path': 'src/example.py'})])
                self.emit('external_tool.requested', request_id='pending', tool_call_id='call_fixture_read',
                          session_id=self.session_id, tool_name=definitions[0].name, arguments={'path': 'src/example.py'})
                return
            if 'synthetic-disconnect' in prompt:
                self.emit('assistant.message_delta', delta_content='SYNTHETIC_DISCONNECT_' + 'a' * 160)
                self.client.record('first_delta', session_id=self.session_id)
                await asyncio.Event().wait()
            if 'synthetic-timeout' in prompt or 'synthetic-cancel' in prompt:
                await asyncio.Event().wait()
            elif 'synthetic-provider-failure' in prompt:
                self.emit('session.error', message=self.options['github']['token'], error_type='provider')
            elif 'synthetic-auth-failure' in prompt:
                self.emit('session.error', message='authentication expired', error_type='authentication')
            elif 'synthetic-slow-stream' in prompt or 'synthetic-stream-failure' in prompt:
                output = 'SYNTHETIC_STREAM_' + 'a' * 160
                self.emit('assistant.message_delta', delta_content=output)
                self.client.record('first_delta', session_id=self.session_id)
                await asyncio.sleep(1.5)
                if 'synthetic-stream-failure' in prompt:
                    self.emit('session.error', message='synthetic error', error_type='provider')
                else:
                    self.emit('assistant.message', content=output)
                    self.emit('session.idle')
                    self.client.record('completed', session_id=self.session_id)
            else:
                output = 'SYNTHETIC_FUSION_' + str(self.tier).upper()
                if 'SYNTHETIC_CLIENT_TOOL_RESULT' in prompt:
                    output = 'SYNTHETIC_TOOL_RESULT_ACCEPTED'
                if 'synthetic-redaction' in prompt:
                    output = self.options['github']['token']
                elif 'synthetic-overflow' in prompt:
                    output = 'x' * 300000
                elif 'synthetic-tools' in prompt:
                    self.emit('external_tool.requested', request_id='forbidden', tool_call_id='forbidden',
                              session_id=self.session_id, tool_name='shell', arguments={'command': 'id'})
                    return
                # A split secret proves redaction covers the assembled response.
                midpoint = len(output) // 2
                self.emit('assistant.message_delta', delta_content=output[:midpoint])
                self.emit('assistant.message_delta', delta_content=output[midpoint:])
                self.emit('assistant.message', content=output)
                self.emit('session.idle')

        self.producer = asyncio.create_task(produce())
        return uuid.uuid4().hex

    async def abort(self):
        self.aborted = True
        self.client.record('abort', session_id=self.session_id)
        if self.producer:
            self.producer.cancel()
            await asyncio.gather(self.producer, return_exceptions=True)

    async def disconnect(self):
        self.disconnected = True
        self.client.record('disconnect', session_id=self.session_id)


class FakeFactory:
    def __init__(self, event_path=None):
        self.event_path = event_path
        self.clients = []

    def __call__(self, **options):
        client = FakeCopilotClient(**options, event_path=self.event_path)
        self.clients.append(client)
        return client
