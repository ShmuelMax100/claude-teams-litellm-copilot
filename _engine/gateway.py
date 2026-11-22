"""Shared, dependency-free helpers. No provider credentials are handled here."""
import http.client, json, os, tempfile, urllib.error, urllib.parse, urllib.request
from pathlib import Path

class Error(Exception):
    pass

def origin(value):
    u=urllib.parse.urlsplit(value)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
        raise Error('Gateway must be an HTTPS origin without a path or credentials.')
    if u.scheme=='http' and u.hostname not in ('localhost','127.0.0.1','::1'):
        raise Error('HTTP is allowed only for a loopback test gateway.')
    return value.rstrip('/')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class API:
    def __init__(self,url,key):
        if not isinstance(key,str) or not key or any(not 33<=ord(c)<=126 for c in key):
            raise Error('Invalid gateway key. Enter a nonempty key without whitespace or control characters.')
        self.url=origin(url); self.key=key
        self.opener=urllib.request.build_opener(NoRedirect)
    def call(self,path,method='GET',body=None,missing=False):
        req=urllib.request.Request(self.url+path,method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=90) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if missing and e.code==404:return None
            # Upstream errors may contain credentials or request bodies; do not echo them.
            raise Error(f'{method} {path.split("?")[0]} returned HTTP {e.code}. Check gateway Logs.') from None
        except (urllib.error.URLError,TimeoutError,ConnectionError,http.client.HTTPException):
            raise Error('Gateway could not be reached. Check its URL, network and TLS certificate.') from None

def read(path): return json.loads(Path(path).read_text())

def private_write(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        os.chmod(f.name,0o600); json.dump(value,f,indent=2); f.write('\n'); tmp=f.name
    os.replace(tmp,path)

def load_catalog():
    return read(Path(__file__).with_name('catalog.json'))


def key_info(api):
    info=api.call('/key/info').get('info',{})
    if not info.get('user_id'): raise Error('Use a personal LiteLLM key with a user_id, not the master key.')
    return info
