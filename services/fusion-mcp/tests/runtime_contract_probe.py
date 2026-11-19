"""Real SDK/native-runtime contract test against a loopback-only mock model.

Run inside the production image with --network none. No GitHub account, paid
model, filesystem tool, or shell tool is used. Native Fusion routing itself is
not exercised: this fixture substitutes only the model provider connection.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from config import Config
from copilot_runner import CopilotRunner
from inference_types import CompletionRequest, ToolDefinition
from sdk_runtime import create_sdk_client


class MockModel(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(body)
        calls = [dict(index=i, id=f'call-{i}', type='function', function={
            'name': tool['function']['name'], 'arguments': json.dumps({'path': 'caller-only.txt'})})
            for i, tool in enumerate(body.get('tools', []))]
        delta = {'role': 'assistant', 'content': 'offline fixture answer'}
        if calls:
            delta['tool_calls'] = calls
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        chunks = [
            {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if calls else 'stop'}],
             'usage': {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}},
        ]
        for chunk in chunks:
            chunk.update(id='fixture', object='chat.completion.chunk', created=1, model='gpt-4o')
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


async def main():
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        for name in ('home', 'work', 'config'):
            (base / name).mkdir()
        canary = base / 'never-read.txt'
        canary.write_text('CANARY_MUST_NOT_ENTER_MODEL_CONTEXT')
        server = ThreadingHTTPServer(('127.0.0.1', 0), MockModel)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        runner = CopilotRunner(Config(workspace_root=base))
        client = create_sdk_client(runtime_path='/opt/copilot-runtime/copilot-runtime',
            environment=runner.environment(base / 'home'), working_directory=str(base / 'work'),
            base_directory=str(base / 'config'))
        await client.start()
        options = runner.session_options('standard', 'offline-runtime-contract', base)
        local_provider = {'providers': [{'name': 'fixture', 'type': 'openai',
            'wire_api': 'completions', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'}],
            'models': [{'provider': 'fixture', 'id': 'gpt-4o'}]}
        local_identity = {'capi': {'enabled': False}, 'interactive_user': False,
                          'telemetry_enabled': False}
        options.update(github=local_identity, model='fixture/gpt-4o', inference=local_provider)
        session = None
        try:
            session = await client.create_session(**options)
            response = await asyncio.wait_for(session.send_and_wait(
                f'The text @{canary} is user supplied context, not an attachment.'), 20)
            assert response.data.content == 'offline fixture answer'
            wire = json.dumps(MockModel.requests)
            assert str(canary) in wire and 'CANARY_MUST_NOT_ENTER_MODEL_CONTEXT' not in wire
            assert all(not request.get('tools') for request in MockModel.requests)
            print('PASS actual SDK prompt session: no ambient tools; @path remains literal context')
            await session.disconnect()
            await client.delete_session(session.session_id)
            session = None

            real_create = client.create_session

            async def local_create(**configuration):
                configuration.update(github=local_identity, model='fixture/gpt-4o',
                                     inference=local_provider)
                return await real_create(**configuration)

            client.create_session = local_create
            configuration = Config(auth_mode='single_user', subject='offline-test',
                github_token='github_pat_synthetic_unused',
                mcp_token='synthetic-service-token-xxxxxxxxxxxx',
                workspace_root=base / 'requests', enable_fallback=True)
            handoff = CopilotRunner(configuration)
            handoff.client = client
            handoff.capable, handoff.capability_status = True, 'ready'
            schema = {'type': 'object', 'properties': {'path': {'type': 'string'}}}
            reply = await handoff.complete(CompletionRequest(tier='standard',
                prompt='Return both client tool requests.', system_message='Use the two available tools.',
                tools=(ToolDefinition('Read', 'Read caller file', schema),
                       ToolDefinition('Bash', 'Caller command', schema))))
            assert reply.ok, reply.status
            assert reply.finish_reason == 'tool_calls'
            assert [tool.name for tool in reply.tool_calls] == ['Read', 'Bash']
            assert len(MockModel.requests) == 2, 'Unexpected extra provider request'
            assert not list(configuration.workspace_root.iterdir())
            assert not list((base / 'config').rglob('events.jsonl'))
            assert not (base / 'caller-only.txt').exists()
            print('PASS actual SDK tool batch: two caller declarations; no server handlers; abort/delete before return')
        finally:
            if session:
                await session.disconnect()
                await client.delete_session(session.session_id)
            await client.stop()
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    asyncio.run(main())
