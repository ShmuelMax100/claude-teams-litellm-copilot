"""Test-only app assembly. Production has no environment switch for this fixture."""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/app')

from config import Config
from copilot_runner import CopilotRunner
from fake_sdk import FakeFactory
from server import create_app
import uvicorn


class CaptureInferenceWire:
    """Test-only capture of synthetic credentials, deleted with the container."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/v1/chat/completions':
            return await self.app(scope, receive, send)
        raw = bytearray()
        async def capture():
            message = await receive()
            if message['type'] == 'http.request':
                raw.extend(message.get('body', b''))
                if not message.get('more_body'):
                    with Path('/tmp/qa-inference-wire.jsonl').open('a') as output:
                        output.write(json.dumps({'headers': [(k.decode(), v.decode()) for k, v in scope['headers']],
                            'body': json.loads(raw)}) + '\n')
            return message
        await self.app(scope, capture, send)


if __name__ == '__main__':
    config = Config.from_env()
    factory = FakeFactory('/tmp/qa-events.jsonl')
    runner = CopilotRunner(config, client_factory=factory)
    uvicorn.run(CaptureInferenceWire(create_app(config, runner)), host='0.0.0.0', port=config.port,
                access_log=False, log_level='warning')
