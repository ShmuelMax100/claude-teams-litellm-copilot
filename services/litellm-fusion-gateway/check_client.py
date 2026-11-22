"""Inspect an installed Claude Code against a loopback fixture and temporary profile.

No real client settings, provider credentials or live model requests are used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_engine'))
import developer

seen = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        seen.append(data)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        events = [
            ('message_start', {'type': 'message_start', 'message': {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'fusion-standard', 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 0}}}),
            ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'OFFLINE_CLIENT_OK'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
            ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 1}}),
            ('message_stop', {'type': 'message_stop'})]
        for name, value in events:
            self.wfile.write(('event: ' + name + '\ndata: ' + json.dumps(value) + '\n\n').encode())
            self.wfile.flush()


def main():
    executable = shutil.which('claude')
    if not executable:
        raise SystemExit('Install Claude Code to run the optional offline client compatibility probe.')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='fusion-claude-probe-') as folder:
            profile = Path(folder) / 'profile'
            invite = {'format': 'fusion-gateway-v1', 'gateway': 'http://127.0.0.1:' + str(server.server_port),
                'key': 'sk-' + 'offline-fixture', 'user_id': 'alice', 'github_login': 'alice', 'tier': 'standard',
                'models': ['fusion-standard'], 'default_model': 'fusion-standard'}
            settings = developer.configure(invite, profile)
            env = {'PATH': os.environ['PATH'], 'CLAUDE_CONFIG_DIR': str(profile),
                'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1', 'TERM': 'dumb'}
            result = subprocess.run([executable, '-p', 'Reply OFFLINE_CLIENT_OK', '--settings', str(settings),
                '--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--max-turns', '1', '--output-format', 'json', '--no-session-persistence'],
                cwd=folder, env=env, capture_output=True, text=True, timeout=45)
            assert result.returncode == 0 and 'OFFLINE_CLIENT_OK' in result.stdout, 'Offline Claude Code reply failed.'
            assert seen and all(request.get('model') == 'fusion-standard' for request in seen)
            for request in seen:
                assert not request.get('thinking') and not request.get('context_management'), list(request)
                assert not request.get('output_config'), request.get('output_config')
                assert all(tool.get('type') in (None, 'custom') and 'input_schema' in tool for tool in request.get('tools', [])), 'Unsupported server-side tool declaration.'
            print(json.dumps({'passed': True, 'requests': len(seen), 'model': 'fusion-standard',
                'thinking_omitted': True, 'tool_declarations': len(seen[0].get('tools', [])), 'provider_requests': 0}))
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
