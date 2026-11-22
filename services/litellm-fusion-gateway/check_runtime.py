"""Verify actual pinned LiteLLM translation against an offline loopback fixture."""
import asyncio
import base64
import contextlib
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import os
from pathlib import Path
import sys
import threading

logging.disable(logging.CRITICAL)
wire = []
status = 200
mode = 'text'


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        wire.append({'path': self.path, 'headers': dict(self.headers), 'body': request})
        code = status if self.path == '/v1/chat/completions' else 500
        if code != 200:
            payload = {'error': {'type': 'authentication_error' if code == 401 else 'api_error', 'message': 'synthetic service error'}}
        else:
            message = {'role': 'assistant', 'content': 'Fusion first request'}
            if mode == 'tools':
                message.update(content=None, tool_calls=[{'id': 'call_fixture', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"README.md"}'}}])
            payload = {'id': 'chatcmpl_fixture', 'object': 'chat.completion', 'created': 1, 'model': request['model'],
                       'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if mode == 'tools' else 'stop'}],
                       'usage': {'prompt_tokens': 1, 'completion_tokens': 3, 'total_tokens': 4}}
        if code == 200 and request.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [{'role': 'assistant'}, {'content': 'Fusion '}, {'content': 'first request'}, {}]
            for index, delta in enumerate(chunks):
                chunk = {'id': 'chatcmpl_fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': request['model'],
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': 'stop' if index == 3 else None}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
            return
        encoded = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
base = 'http://127.0.0.1:' + str(server.server_port)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update(FUSION_API_BASE=base + '/v1', FUSION_SERVICE_TOKEN='synthetic-service-' + 's' * 40,
                  FUSION_IDENTITY_KEY='synthetic-signing-' + 'i' * 40, LITELLM_LOCAL_MODEL_COST_MAP='True')
sys.path.insert(0, str(Path(__file__).resolve().parent))


async def run():
    global status, mode
    import litellm
    from litellm import Router
    from litellm.proxy._types import UserAPIKeyAuth
    from fusion_gateway_guard import guard
    litellm.suppress_debug_info = True
    litellm.callbacks = [guard]
    # Production also disables shared deployment cooldowns: an individual user's
    # revoked credential must never poison the shared alias for other users.
    router = Router(model_list=[
        {'model_name': 'fusion-standard', 'litellm_params': {'model': 'custom_openai/fusion-standard',
            'api_base': base + '/v1', 'api_key': 'synthetic-wrong-provider-key', 'max_retries': 0}},
        {'model_name': 'obsolete-provider', 'litellm_params': {'model': 'custom_openai/obsolete',
            'api_base': base + '/alternate', 'api_key': 'synthetic-obsolete-key', 'max_retries': 0}}],
        fallbacks=[{'fusion-standard': ['obsolete-provider']}], num_retries=3, max_fallbacks=3, disable_cooldowns=True)
    outcomes = []
    for code, stream, tools, subject in [(200, False, False, 'alice'), (200, True, False, 'bob'),
            (200, False, True, 'alice')] + [(code, False, False, 'alice') for code in (429, 500, 502, 503, 504, 400, 401, 403)]:
        status = code
        mode = 'tools' if tools else 'text'
        wire.clear()
        identity = UserAPIKeyAuth(user_id=subject, models=['fusion-standard'],
            metadata={'fusion_gateway': {'version': 1, 'subject': subject, 'tier': 'standard'}})
        data = {'model': 'fusion-standard', 'messages': [{'role': 'user', 'content': 'synthetic history'}], 'max_tokens': 64, 'stream': stream}
        if tools:
            data['tools'] = [{'name': 'read_file', 'description': 'Read local file', 'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}]
        data = await guard.async_pre_call_hook(identity, None, data, 'anthropic_messages')
        try:
            result = await asyncio.create_task(router.aanthropic_messages(**data,
                extra_headers={'Authorization': 'Bearer synthetic-client-oauth', 'X-Fusion-Identity': 'forged'},
                metadata={'client_secret': 'synthetic-client-secret'},
                proxy_server_request={'headers': {'Authorization': 'synthetic-proxy-key'}}))
            if stream:
                frames = [chunk async for chunk in result]
                assert len(frames) >= 4, frames
            elif tools:
                blocks = result['content']
                assert any(block.get('type') == 'tool_use' and block.get('name') == 'read_file' for block in blocks), result
            else:
                assert result['content'][0]['text'] == 'Fusion first request', result
            outcome = 'success'
        except Exception as exc:
            outcome = type(exc).__name__
            if code == 200:
                raise
        assert len(wire) == 1 and wire[0]['path'] == '/v1/chat/completions', (code, outcome, wire)
        request = wire[0]
        serialized = json.dumps(request)
        for forbidden in ('synthetic-wrong-provider-key', 'synthetic-client-oauth', 'synthetic-client-secret', 'synthetic-proxy-key', 'forged'):
            assert forbidden not in serialized, forbidden
        headers = {name.lower(): value for name, value in request['headers'].items()}
        assert headers['authorization'] == 'Bearer ' + os.environ['FUSION_SERVICE_TOKEN']
        assertion = headers['x-fusion-identity']
        version, payload, signature = assertion.split('.')
        raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
        claims = json.loads(raw)
        expected = base64.urlsafe_b64encode(hmac.new(os.environ['FUSION_IDENTITY_KEY'].encode(), (version + '.' + payload).encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        assert hmac.compare_digest(expected, signature) and claims['sub'] == subject
        assert request['body']['model'] == 'fusion-standard'
        assert request['body']['messages'][0]['content'] == 'synthetic history'
        outcomes.append({'service_status': code, 'stream': stream, 'tools': tools, 'calls': len(wire), 'outcome': outcome})
    return outcomes


try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        outcomes = asyncio.run(run())
    print(json.dumps({'passed': True, 'cases': outcomes}))
finally:
    server.shutdown()
