#!/usr/bin/env python3
"""Opt-in, single-owner Fusion MCP administration and Claude Code registration."""
import argparse
import datetime
import getpass
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from gateway import API, Error, NoRedirect, key_info, origin, private_write, read

OWNER = 'claude-gateway-fusion-v1'
VERSION = '1.108.0'
FORMAT = 'fusion-mcp-v1'
LOCAL_NAME = 'litellm-fusion'
TOOLS = ('fusion_standard', 'fusion_high', 'fusion_max')
ROUTES = ['/mcp', '/mcp/', '/key/info', '/user/info']


def endpoint(value):
    parsed = urllib.parse.urlsplit(value)
    root = origin(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '', '', '')))
    if parsed.path.rstrip('/') != '/mcp' or parsed.query or parsed.fragment:
        raise Error('Fusion URL must be an HTTPS /mcp endpoint without query parameters.')
    return root + '/mcp'


def check_gateway(api):
    health = api.call('/health/readiness/details')
    if health.get('litellm_version') != VERSION:
        raise Error('Fusion integration requires the verified LiteLLM ' + VERSION + ' release.')
    if health.get('status') != 'healthy' or health.get('db') != 'connected':
        raise Error('Fusion registration requires a connected persistent LiteLLM database.')
    return health


def _rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for name in ('servers', 'data', 'mcp_servers'):
            if isinstance(value.get(name), list):
                return value[name]
    raise Error('Unexpected MCP server listing. Check the pinned gateway version.')


def _owner(server):
    info = server.get('mcp_info') or {}
    if (info.get('onboarding_package') != OWNER or info.get('auth_mode') != 'single_user'
            or not isinstance(info.get('single_user_subject'), str) or not info['single_user_subject']):
        raise Error('Server is not an owned single-user Fusion registration.')
    if server.get('allow_all_keys') is not False or server.get('auth_type') != 'bearer_token':
        raise Error('Fusion server permissions or authentication changed; administrator review required.')
    if server.get('transport') != 'http' or server.get('delegate_auth_to_upstream') or server.get('oauth_passthrough'):
        raise Error('Fusion server transport or authentication delegation changed.')
    if server.get('extra_headers'):
        raise Error('Fusion must not forward caller-supplied identity or authentication headers.')
    return info['single_user_subject']


def _user(api, subject):
    user = api.call('/user/info?user_id=' + urllib.parse.quote(subject, safe='')).get('user_info', {})
    if user.get('user_id') != subject or user.get('user_role') not in ('internal_user', 'internal_user_viewer'):
        raise Error('Select an existing personal LiteLLM developer user, not an administrator or shared account.')
    return user


def register(api, url, subject, alias='fusion', token=None, apply=False, single_user_attested=False, timeout_seconds=150):
    """Register only after explicit single-owner attestation; never alter another server."""
    if not single_user_attested:
        raise Error('Attest that this is a dedicated single-user gateway or an audited single-owner deployment.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', alias):
        raise Error('Use a short MCP alias containing letters, numbers, underscores or hyphens.')
    if not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', subject):
        raise Error('A valid --single-user-subject is required.')
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 1830:
        raise Error('MCP timeout must be between 1 and 1830 seconds.')
    url = endpoint(url)
    check_gateway(api)
    _user(api, subject)
    existing = _rows(api.call('/v1/mcp/server'))
    wanted = {'server_name': alias, 'alias': alias, 'url': url, 'transport': 'http',
              'auth_type': 'bearer_token', 'allow_all_keys': False, 'timeout': timeout_seconds,
              # This is client-IP visibility, NOT anonymous access or ACA upstream ingress.
              'available_on_public_internet': True, 'extra_headers': [],
              'allowed_tools': list(TOOLS),
              'mcp_info': {'onboarding_package': OWNER, 'auth_mode': 'single_user',
                           'single_user_subject': subject}}
    match = None
    for server in existing:
        same_name = alias in (server.get('alias'), server.get('server_name'))
        same_url = str(server.get('url', '')).rstrip('/') == url
        if not same_name and not same_url:
            continue
        if _owner(server) != subject:
            raise Error('This alias or backend already belongs to a different single-user subject.')
        if not same_name or not same_url or any(server.get(k) != wanted[k] for k in ('transport', 'auth_type', 'allow_all_keys', 'timeout')):
            raise Error('Existing Fusion registration conflicts; review it without overwriting it.')
        if match is not None:
            raise Error('Duplicate Fusion registrations require administrator review.')
        match = server
    if match:
        return {'action': 'reuse', 'server_id': match['server_id'], 'alias': alias, 'user_id': subject}
    if not apply:
        return {'action': 'create', 'alias': alias, 'url': url, 'user_id': subject, 'applied': False}
    if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
        raise Error('Provide the service bearer secret (at least 32 non-whitespace characters), not a GitHub token.')
    body = dict(wanted, credentials={'auth_value': token})
    result = api.call('/v1/mcp/server', 'POST', body)
    server_id = result.get('server_id') if isinstance(result, dict) else None
    if not server_id:
        # A creation might have succeeded; do not blindly issue a second one.
        raise Error('Registration response lacked a server ID. Inspect the gateway before retrying.')
    return {'action': 'created', 'server_id': server_id, 'alias': alias, 'user_id': subject}


def server_info(api, server_id):
    result = api.call('/v1/mcp/server/' + urllib.parse.quote(server_id, safe=''))
    if not isinstance(result, dict) or result.get('server_id') != server_id:
        raise Error('Unexpected Fusion registration response.')
    _owner(result)
    return result


def enroll(api, server_id, subject, destination, apply=False):
    check_gateway(api)
    server = server_info(api, server_id)
    if _owner(server) != subject:
        raise Error('Fusion can be granted only to the persisted single-user subject.')
    _user(api, subject)
    path = Path(destination).expanduser()
    if path.is_symlink():
        raise Error('Invitation path is a symlink; select a private regular file.')
    if path.exists():
        invite = read(path)
        validate_invitation(invite)
        if (invite['gateway'], invite['server_id'], invite['user_id']) != (api.url, server_id, subject):
            raise Error('Invitation belongs to another Fusion registration or owner.')
        validate_key(invite)
        path.chmod(0o600)
        return {'action': 'reuse', 'invitation': str(path)}
    if not apply:
        return {'action': 'issue-mcp-only-key', 'user_id': subject, 'server_id': server_id, 'applied': False}
    issued = api.call('/key/generate', 'POST', {
        'user_id': subject, 'key_alias': 'fusion-' + uuid.uuid4().hex[:12], 'duration': '90d',
        'models': ['no-default-models'], 'allowed_routes': ROUTES,
        'send_invite_email': False, 'object_permission': {'mcp_servers': [server_id]},
        'metadata': {'onboarding_package': OWNER, 'single_user_subject': subject}})
    invite = {'format': FORMAT, 'gateway': api.url, 'user_id': subject, 'server_id': server_id,
              'server_alias': server.get('alias') or server['server_name'],
              'key': issued.get('key'), 'expires': issued.get('expires')}
    validate_invitation(invite)
    private_write(path, invite)
    return {'action': 'issued', 'invitation': str(path)}


def validate_invitation(invite):
    if not isinstance(invite, dict) or invite.get('format') != FORMAT:
        raise Error('Use a separate private Fusion invitation; inference invitations are unchanged.')
    for field in ('gateway', 'user_id', 'server_id', 'server_alias', 'key'):
        if not isinstance(invite.get(field), str) or not invite[field]:
            raise Error('Fusion invitation is missing a valid ' + field + '.')
    origin(invite['gateway'])
    if (not invite['key'].startswith('sk-') or any(not 33 <= ord(c) <= 126 for c in invite['key'])
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', invite['server_alias'])):
        raise Error('Invalid Fusion key or server alias.')


def validate_key(invite):
    validate_invitation(invite)
    api = API(invite['gateway'], invite['key'])
    info = key_info(api)
    if info.get('user_id') != invite['user_id']:
        raise Error('Fusion invitation and key owner do not match.')
    permission = info.get('object_permission') or {}
    if (permission.get('mcp_servers') != [invite['server_id']] or permission.get('mcp_access_groups')
            or permission.get('mcp_toolsets') or permission.get('mcp_tool_permissions')
            or info.get('access_group_ids')):
        raise Error('Fusion key must explicitly grant only this server, without additive access groups.')
    if info.get('models') != ['no-default-models'] or set(info.get('allowed_routes') or []) != set(ROUTES):
        raise Error('Fusion requires a separate MCP-only key; existing inference keys are not accepted.')
    metadata = info.get('metadata') or {}
    if metadata.get('onboarding_package') != OWNER or metadata.get('single_user_subject') != invite['user_id']:
        raise Error('Fusion key ownership metadata is missing or mismatched.')
    return api


class MCPClient:
    """Small Streamable HTTP diagnostic client; never sends model requests implicitly."""
    def __init__(self, invite):
        validate_invitation(invite)
        self.url = origin(invite['gateway']) + '/mcp'
        self.headers = {'x-litellm-api-key': 'Bearer ' + invite['key'],
                        'x-mcp-servers': invite['server_alias'], 'Content-Type': 'application/json',
                        'Accept': 'application/json, text/event-stream'}
        self.opener = urllib.request.build_opener(NoRedirect)
        self.request_id = 0
        # Service execution is bounded at 1800 seconds. Discovery stays short;
        # valid long tool calls must not become a misleading transport timeout.
        self.tool_timeout = 1830

    def request(self, method, params=None, notification=False):
        self.request_id += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            body['params'] = params
        if not notification:
            body['id'] = self.request_id
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method='POST')
        try:
            with self.opener.open(request, timeout=self.tool_timeout if method == 'tools/call' else 90) as response:
                session = response.headers.get('Mcp-Session-Id')
                if session:
                    if len(session) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in session):
                        raise Error('Invalid MCP session identifier.')
                    self.headers['Mcp-Session-Id'] = session
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise Error('MCP response exceeds the diagnostic size limit.')
                if notification and not raw:
                    return None
                content_type = response.headers.get('Content-Type', '')
        except urllib.error.HTTPError as exc:
            raise Error('MCP request returned HTTP ' + str(exc.code) + '. Check key permissions and gateway health.') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            raise Error('MCP gateway could not be reached securely.') from None
        try:
            if 'text/event-stream' in content_type:
                candidates = []
                for event in raw.decode().replace('\r\n', '\n').split('\n\n'):
                    data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
                    if data:
                        candidates.append(json.loads(data))
                result = next((item for item in candidates if item.get('id') == body.get('id')), None)
            else:
                result = json.loads(raw)
        except (ValueError, UnicodeError, AttributeError):
            raise Error('Invalid MCP response; check gateway protocol compatibility.') from None
        if not isinstance(result, dict) or result.get('id') != body.get('id') or result.get('jsonrpc') != '2.0':
            raise Error('MCP response did not match the request.')
        if 'error' in result:
            raise Error('MCP request was rejected. Check gateway permissions and service readiness.')
        return result.get('result')

    def initialize(self):
        result = self.request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                            'clientInfo': {'name': 'claude-gateway-setup', 'version': '1'}})
        if not isinstance(result, dict) or result.get('protocolVersion') not in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'):
            raise Error('Unsupported MCP protocol negotiation.')
        self.headers['MCP-Protocol-Version'] = result['protocolVersion']
        self.request('notifications/initialized', notification=True)
        return self

    def close(self):
        if 'Mcp-Session-Id' not in self.headers:
            return
        request = urllib.request.Request(self.url, headers=self.headers, method='DELETE')
        try:
            with self.opener.open(request, timeout=10):
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            pass  # Best-effort termination; unsupported DELETE must not hide diagnostics.


def discover(invite):
    validate_key(invite)
    client = MCPClient(invite)
    try:
        client.initialize()
        tools = []
        cursor = None
        for _ in range(20):
            result = client.request('tools/list', {'cursor': cursor} if cursor else {})
            if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
                raise Error('MCP tool discovery returned an invalid catalog.')
            tools.extend(result['tools'])
            cursor = result.get('nextCursor')
            if not cursor:
                break
        else:
            raise Error('MCP tool catalog exceeded the diagnostic page limit.')
        names = {item.get('name') for item in tools if isinstance(item, dict)}
        expected = {invite['server_alias'] + '-' + name for name in TOOLS}
        if not expected <= names:
            raise Error('Fusion tools are missing or unauthorized; local settings were not changed.')
        return {'gateway': invite['gateway'], 'server_id': invite['server_id'], 'tools': sorted(expected), 'model_requests': 0}
    finally:
        client.close()


def _paths(home):
    home = Path(home) if home else Path.home()
    directory = home / '.claude'
    account = home / '.claude.json'
    state = directory / 'fusion-mcp-onboarding.json'
    if os.environ.get('CLAUDE_CONFIG_DIR'):
        raise Error('A custom CLAUDE_CONFIG_DIR is active; review that profile separately.')
    if any(path.is_symlink() for path in (directory, directory / 'backups', account, state)):
        raise Error('Claude configuration is a symlink; ask its owner to update the managed source.')
    return directory, account, state


def managed_policy_paths(directory):
    roots = (directory, Path('/Library/Application Support/ClaudeCode'), Path('/etc/claude-code'),
             Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'ClaudeCode')
    return [root / name for root in roots for name in ('managed-mcp.json', 'managed-settings.json')]


def install(invite, home=None):
    directory, account, state = _paths(home)
    if any(path.exists() or path.is_symlink() for path in managed_policy_paths(directory)):
        raise Error('Managed Claude policy is present. Ask the administrator to register Fusion through that policy.')
    project_mcp = Path.cwd() / '.mcp.json'
    if project_mcp.exists() and LOCAL_NAME in read(project_mcp).get('mcpServers', {}):
        raise Error('This project defines litellm-fusion. Preserve its registration and review the scope with its owner.')
    discovery = discover(invite)  # Authorization and actual tool discovery precede every local write.
    before = read(account) if account.exists() else {}
    if not isinstance(before, dict) or not isinstance(before.get('mcpServers', {}), dict):
        raise Error('Claude user MCP configuration must be a JSON object.')
    local_project = before.get('projects', {}).get(str(Path.cwd()), {})
    if LOCAL_NAME in local_project.get('mcpServers', {}):
        raise Error('This project already defines litellm-fusion in local state. Review that entry first.')
    registration = {'type': 'http', 'url': origin(invite['gateway']) + '/mcp',
                    'headers': {'x-litellm-api-key': 'Bearer ' + invite['key'],
                                'x-mcp-servers': invite['server_alias']}}
    existing = before.get('mcpServers', {}).get(LOCAL_NAME)
    previous = read(state) if state.exists() else None
    if previous and (previous.get('gateway'), previous.get('user_id'), previous.get('server_id')) != (invite['gateway'], invite['user_id'], invite['server_id']):
        raise Error('A different Fusion owner is installed. Uninstall that registration first.')
    if existing == registration and previous and previous.get('installed') == registration:
        return dict(discovery, action='unchanged')
    if existing is not None and (not previous or existing != previous.get('installed')):
        raise Error('A different or edited litellm-fusion entry exists. Preserve it and review manually.')
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / 'backups' / ('fusion-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    private_write(backup / 'claude.json', before)
    updated = json.loads(json.dumps(before))
    updated.setdefault('mcpServers', {})[LOCAL_NAME] = registration
    if (read(account) if account.exists() else {}) != before:
        raise Error('Claude configuration changed during installation. Retry without overwriting concurrent changes.')
    private_write(account, updated)
    private_write(state, {'format': FORMAT, 'gateway': invite['gateway'], 'user_id': invite['user_id'],
                          'server_id': invite['server_id'], 'server_alias': invite['server_alias'],
                          'installed': registration, 'backup': str(backup / 'claude.json')})
    return dict(discovery, action='installed', backup=str(backup / 'claude.json'))


def installed_invitation(home=None):
    _, account, state = _paths(home)
    if not state.exists():
        raise Error('Fusion is not installed. Use ./setup fusion connect --invite <private-file>.')
    saved = read(state)
    if read(account).get('mcpServers', {}).get(LOCAL_NAME) != saved.get('installed'):
        raise Error('Installed Fusion entry changed; review it before running diagnostics.')
    header = saved['installed']['headers']['x-litellm-api-key']
    return {'format': FORMAT, 'gateway': saved['gateway'], 'user_id': saved['user_id'],
            'server_id': saved['server_id'], 'server_alias': saved['server_alias'],
            'key': header.removeprefix('Bearer ')}


def uninstall(home=None):
    _, account, state = _paths(home)
    if not state.exists():
        return {'action': 'not-installed'}
    saved = read(state)
    current = read(account)
    if current.get('mcpServers', {}).get(LOCAL_NAME) != saved.get('installed'):
        raise Error('Fusion entry changed after installation. Preserve it and merge the private backup manually.')
    del current['mcpServers'][LOCAL_NAME]
    private_write(account, current)
    state.unlink()
    return {'action': 'uninstalled', 'gateway_key_revoked': False}


def run_tool(invite, tier, prompt, allow_live_usage=False):
    if not allow_live_usage:
        raise Error('Live Fusion execution consumes Copilot usage, independently of Claude Team; explicitly set --allow-live-usage.')
    if tier not in ('standard', 'high', 'max') or not isinstance(prompt, str) or not prompt.strip():
        raise Error('Choose a supported tier and a non-empty prompt.')
    discover(invite)
    client = MCPClient(invite)
    try:
        client.initialize()
        return client.request('tools/call', {'name': invite['server_alias'] + '-fusion_' + tier,
                                             'arguments': {'prompt': prompt}})
    finally:
        client.close()


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected and selected[0].startswith('fallback-'):
        import fusion_fallback
        return fusion_fallback.main(selected)
    parser = argparse.ArgumentParser(prog='./setup fusion', description=__doc__,
        epilog='Automatic Claude-primary fallback: fallback-register, fallback-enroll, '
               'fallback-connect, fallback-doctor, fallback-restore. Use COMMAND --help '
               'or see docs/FUSION-FALLBACK.md.')
    commands = parser.add_subparsers(dest='command', required=True)
    register_parser = commands.add_parser('register', help='Preview or apply a single-user MCP registration')
    register_parser.add_argument('--gateway', required=True)
    register_parser.add_argument('--upstream', required=True)
    register_parser.add_argument('--single-user-subject', required=True)
    register_parser.add_argument('--single-user-attested', action='store_true', required=True)
    register_parser.add_argument('--alias', default='fusion')
    register_parser.add_argument('--timeout-seconds', type=int, default=150,
                                 help='Upstream MCP timeout; keep above the service limit and below infrastructure deadlines')
    register_parser.add_argument('--apply', action='store_true')
    grant = commands.add_parser('enroll', help='Issue a separate MCP-only invitation to the registered owner')
    grant.add_argument('--gateway', required=True)
    grant.add_argument('--server-id', required=True)
    grant.add_argument('--single-user-subject', required=True)
    grant.add_argument('--out', type=Path, required=True)
    grant.add_argument('--apply', action='store_true')
    connect = commands.add_parser('connect', help='Validate permission and install one user MCP entry')
    connect.add_argument('--invite', type=Path, required=True)
    for name in ('doctor', 'tools'):
        command = commands.add_parser(name, help='Discover authorized tools without model usage')
        command.add_argument('--invite', type=Path)
    commands.add_parser('uninstall', help='Remove only this entry while preserving unrelated or later settings')
    run = commands.add_parser('run', help='Explicit live Fusion tool call using Copilot entitlement')
    run.add_argument('--invite', type=Path)
    run.add_argument('--tier', required=True, choices=['standard', 'high', 'max'])
    run.add_argument('--prompt-file', required=True, type=Path)
    run.add_argument('--allow-live-usage', action='store_true', required=True)
    args = parser.parse_args(argv)
    if args.command in ('register', 'enroll'):
        key = getpass.getpass('LiteLLM administrator key (hidden): ').strip()
        if not key:
            raise Error('No administrator key supplied.')
        api = API(args.gateway, key)
        if args.command == 'register':
            token = getpass.getpass('Fusion service bearer (hidden; NOT a GitHub token): ').strip() if args.apply else None
            result = register(api, args.upstream, args.single_user_subject, args.alias, token, args.apply,
                              args.single_user_attested, args.timeout_seconds)
        else:
            result = enroll(api, args.server_id, args.single_user_subject, args.out, args.apply)
    elif args.command == 'uninstall':
        result = uninstall()
    else:
        invite = read(args.invite.expanduser()) if args.invite else installed_invitation()
        if args.command == 'connect':
            result = install(invite)
        elif args.command == 'run':
            result = run_tool(invite, args.tier, args.prompt_file.read_text(), args.allow_live_usage)
        else:
            result = discover(invite)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    try:
        main()
    except (Error, ValueError, KeyError, OSError) as exc:
        print('Fusion setup needs attention: ' + str(exc), file=sys.stderr)
        sys.exit(1)
