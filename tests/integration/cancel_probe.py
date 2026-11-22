"""Disconnect from the actual local ASGI stream; uses only fixture credentials."""
import base64
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.request
import uuid


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


now = int(time.time())
claims = {'v': 1, 'aud': 'fusion-mcp', 'sub': 'qa-alice', 'iat': now,
          'exp': now + 60, 'jti': uuid.uuid4().hex}
unsigned = 'v1.' + encode(json.dumps(claims, separators=(',', ':')).encode())
assertion = unsigned + '.' + encode(hmac.new(os.environ['FUSION_IDENTITY_KEY'].encode(),
                                           unsigned.encode(), hashlib.sha256).digest())
body = {'model': 'fusion-standard', 'max_tokens': 2048, 'stream': True,
        'messages': [{'role': 'user', 'content': 'synthetic-disconnect'}]}
request = urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions',
    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + os.environ['FUSION_SERVICE_TOKEN'],
        'X-Fusion-Identity': assertion})
with urllib.request.urlopen(request, timeout=8) as response:
    for line in response:
        if b'SYNTHETIC_DISCONNECT_' in line:
            response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            break
    else:
        raise AssertionError('No stream delta arrived before disconnection')
print('Disconnected after the first real stream delta')
