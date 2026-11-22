"""Test-only bounded relay into the egress-isolated application network."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def forward(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 1048576:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {name: value for name, value in self.headers.items()
                   if name.lower() not in ('host', 'connection', 'content-length', 'transfer-encoding')}
        connection = http.client.HTTPConnection('gateway', self.server.server_port, timeout=120)
        try:
            connection.request(self.command, self.path, body, headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('content-length', 'connection', 'transfer-encoding'):
                    self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            remaining = 1048576
            while remaining:
                data = response.read1(min(16384, remaining))
                if not data:
                    break
                self.wfile.write(data)
                self.wfile.flush()
                remaining -= len(data)
            if remaining == 0:
                raise OSError('fixture response limit exceeded')
        except (OSError, http.client.HTTPException):
            self.send_error(502)
        finally:
            connection.close()

    do_GET = do_POST = do_DELETE = do_PUT = forward


for port in (4000, 8080):
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=False).start()
