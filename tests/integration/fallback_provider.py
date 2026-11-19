"""Synthetic Anthropic provider for owner-fallback tests; never calls a model."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading


events = []
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        with lock:
            self.reply(200, list(events))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        with lock:
            events.append({'path': self.path, 'body': body})
        marker = re.search(r'SYNTHETIC_PRIMARY_(\d{3}|STREAM_ERROR)', json.dumps(body))
        status = int(marker.group(1)) if marker and marker.group(1).isdigit() else 200
        if marker and marker.group(1) == 'STREAM_ERROR':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            frames = [
                ('message_start', {'type': 'message_start', 'message': {'id': 'msg_fixture', 'type': 'message',
                    'role': 'assistant', 'model': body['model'], 'content': [], 'stop_reason': None,
                    'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 0}}}),
                ('content_block_start', {'type': 'content_block_start', 'index': 0,
                    'content_block': {'type': 'text', 'text': ''}}),
                ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                    'delta': {'type': 'text_delta', 'text': 'SYNTHETIC_PRIMARY_PARTIAL'}}),
                ('error', {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'synthetic stream failure'}}),
            ]
            for event, data in frames:
                self.wfile.write(('event: ' + event + '\ndata: ' + json.dumps(data) + '\n\n').encode())
                self.wfile.flush()
            return
        if status != 200:
            kind = {400: 'invalid_request_error', 401: 'authentication_error', 403: 'permission_error',
                    429: 'rate_limit_error'}.get(status, 'api_error')
            return self.reply(status, {'type': 'error', 'error': {'type': kind, 'message': 'synthetic primary failure'}})
        self.reply(200, {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': body['model'],
            'content': [{'type': 'text', 'text': 'SYNTHETIC_PRIMARY_OK'}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
