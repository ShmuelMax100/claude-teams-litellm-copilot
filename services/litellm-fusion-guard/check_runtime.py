"""Offline pinned-LiteLLM compatibility test. Run in its image with --network none.

Only a loopback synthetic provider is started; no credentials or paid APIs are
used. Checks actual Anthropic-to-OpenAI fallback hook ordering and wire isolation.
"""
import asyncio
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import os
from pathlib import Path
import sys
import threading

logging.disable(logging.CRITICAL)
capture = io.StringIO()
wire = []
status = 429


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        request = json.loads(raw)
        wire.append({'path': self.path, 'headers': dict(self.headers), 'body': request})
        if self.path.startswith('/v1/messages'):
            code = status
            payload = {'type': 'error', 'error': {'type': 'rate_limit_error' if code == 429 else 'api_error', 'message': 'synthetic'}}
            if code == 200:
                payload = {'id': 'msg_primary', 'type': 'message', 'role': 'assistant', 'model': request['model'],
                           'content': [{'type': 'text', 'text': 'primary'}], 'stop_reason': 'end_turn',
                           'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        elif self.path == '/v1/chat/completions':
            code = 200
            payload = {'id': 'chatcmpl_fusion', 'object': 'chat.completion', 'created': 1, 'model': request['model'],
                       'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'fallback'}, 'finish_reason': 'stop'}],
                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        else:
            code, payload = 404, {'error': 'unknown fixture path'}
        encoded = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
base = 'http://127.0.0.1:' + str(server.server_port)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
os.environ.update(FUSION_FALLBACK_OWNER_SUBJECT='alice', FUSION_FALLBACK_API_BASE=base + '/v1',
                  FUSION_FALLBACK_BEARER_TOKEN='synthetic-service-bearer-' + 'x' * 40)
sys.path.insert(0, str(Path(__file__).resolve().parent))


async def run():
    global status
    import litellm
    from litellm import Router
    from litellm.proxy._types import UserAPIKeyAuth
    from fusion_fallback_guard import guard, _request

    events = []
    original_pre = guard.async_pre_call_deployment_hook
    original_failure = guard.async_post_call_failure_deployment_hook
    async def pre(kwargs, call_type):
        events.append(('pre', kwargs.get('model'), str(call_type), kwargs.get('_is_litellm_internal_call'), str(_request.get())))
        return await original_pre(kwargs, call_type)
    async def failure(request_data, exception, call_type, fallback_depth=None):
        await original_failure(request_data, exception, call_type, fallback_depth)
        events.append(('failure', request_data.get('model'), type(exception).__name__, str(_request.get())))
    guard.async_pre_call_deployment_hook = pre
    guard.async_post_call_failure_deployment_hook = failure

    litellm.suppress_debug_info = True
    litellm.callbacks = [guard]
    router = Router(model_list=[
        {'model_name': 'primary', 'litellm_params': {'model': 'anthropic/claude-sonnet-4-5', 'api_base': base,
            'api_key': 'synthetic-primary-key', 'max_retries': 0}},
        {'model_name': 'fusion-fallback-standard', 'litellm_params': {'model': 'custom_openai/fusion-standard',
            'api_base': base + '/v1', 'api_key': 'synthetic-wrong-bearer', 'max_retries': 0}}],
        fallbacks=[{'primary': ['fusion-fallback-standard']}], num_retries=0, max_fallbacks=1, disable_cooldowns=True)
    identity = UserAPIKeyAuth(user_id='alice', models=['primary', 'fusion-fallback-standard'],
        metadata={'fusion_fallback': {'version': 1, 'owner': 'alice', 'tier': 'standard'}})
    outcomes = []
    for code in (200, 429, 500, 502, 503, 504, 400, 401, 403):
        status = code
        wire.clear()
        events.clear()
        data = {'model': 'primary', 'messages': [{'role': 'user', 'content': 'synthetic history'}], 'max_tokens': 64}
        await guard.async_pre_call_hook(identity, None, data, 'anthropic_messages')
        try:
            # A child task models proxy dispatch after its awaited pre-call hook.
            result = await asyncio.create_task(router.aanthropic_messages(**data,
                extra_headers={'Authorization': 'Bearer synthetic-primary-oauth'},
                metadata={'client_secret': 'synthetic-client-key'},
                proxy_server_request={'headers': {'Authorization': 'synthetic-proxy-oauth'}}))
            outcome = 'success'
        except Exception as exc:
            outcome = type(exc).__name__
        fallback = [request for request in wire if request['path'] == '/v1/chat/completions']
        assert len(fallback) == int(code in (429, 500, 502, 503, 504)), (code, outcome, [r['path'] for r in wire], events)
        for request in fallback:
            serialized = json.dumps(request)
            for forbidden in ('synthetic-primary-key', 'synthetic-primary-oauth', 'synthetic-client-key', 'synthetic-proxy-oauth', 'synthetic-wrong-bearer'):
                assert forbidden not in serialized, (code, forbidden)
            headers = {name.lower(): value for name, value in request['headers'].items()}
            assert headers.get('authorization') == 'Bearer ' + os.environ['FUSION_FALLBACK_BEARER_TOKEN']
            assert request['body']['model'] == 'fusion-standard'
            assert request['body']['messages'][0]['content'] == 'synthetic history'
        outcomes.append({'primary_status': code, 'fallback_calls': len(fallback), 'outcome': outcome})
    return outcomes


try:
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        outcomes = asyncio.run(run())
    print(json.dumps({'passed': True, 'cases': outcomes}))
finally:
    server.shutdown()
