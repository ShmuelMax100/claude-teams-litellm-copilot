"""Personal Fusion gateway enrollment; no provider credential or model execution."""
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from gateway import API, Error, key_info, origin, private_write, read

FORMAT = 'fusion-gateway-v1'
OWNER = 'fusion-gateway-v1'
VERSION = '1.108.0'
TIERS = ('standard', 'high', 'max')
ROUTES = ['/v1/messages', '/v1/messages/count_tokens', '/v1/models', '/v1/model/info', '/model/info', '/key/info', '/user/info']


def alias(tier):
    if tier not in TIERS:
        raise Error('Choose Fusion standard, high or max.')
    return 'fusion-' + tier


def marker(subject, tier):
    alias(tier)
    return {'version': 1, 'subject': subject, 'tier': tier}


def endpoint(value):
    parsed = urlsplit(value)
    origin(parsed.scheme + '://' + parsed.netloc)
    if parsed.path != '/v1' or parsed.query or parsed.fragment:
        raise Error('Fusion upstream must be a private HTTPS URL ending in /v1.')
    return value


def model_rows(api):
    rows = []
    page = 1
    while True:
        result = api.call('/v2/model/info?page=' + str(page) + '&size=100')
        rows.extend(result.get('data', []))
        if page >= result.get('total_pages', 1):
            return rows
        page += 1


def register(api, upstream, apply=False):
    """Verify the deployment-owned catalog; dynamic provider registration is absent."""
    upstream = endpoint(upstream)
    health = api.call('/health/readiness/details')
    if health.get('litellm_version') != VERSION or health.get('status') != 'healthy' or health.get('db') != 'connected':
        raise Error('A healthy LiteLLM ' + VERSION + ' gateway with persistent database is required.')
    callbacks = api.call('/active/callbacks')
    names = callbacks.get('litellm.callbacks', []) if isinstance(callbacks, dict) else callbacks
    expected = 'fusion-gateway-guard/v1 api_base=' + upstream
    if not isinstance(names, list) or names.count(expected) != 1:
        raise Error('The deployed gateway must load exactly one matching Fusion gateway guard.')
    rows = model_rows(api)
    if len(rows) != 3 or {row.get('model_name') for row in rows} != {alias(t) for t in TIERS}:
        raise Error('The gateway must expose exactly the three Fusion aliases; remove obsolete deployments in deployment configuration.')
    for row in rows:
        name = row['model_name']
        params = row.get('litellm_params', {})
        if (params.get('model') != 'custom_openai/' + name
                or params.get('api_base') not in (upstream, 'os.environ/FUSION_API_BASE')
                or params.get('max_retries') != 0
                or params.get('litellm_credential_name')):
            raise Error('A Fusion alias has an unexpected provider configuration.')
    return {'action': 'verified-fusion-gateway', 'gateway': api.url, 'models': [alias(t) for t in TIERS],
            'model_requests': 0, 'changed': False}


def validate_invitation(invite):
    if not isinstance(invite, dict) or invite.get('format') != FORMAT:
        raise Error('Use a new Fusion gateway invitation; previous subscription and provider invitations are unsupported.')
    for field in ('gateway', 'key', 'user_id', 'github_login', 'tier'):
        if not isinstance(invite.get(field), str) or not invite[field]:
            raise Error('Fusion invitation is missing ' + field + '.')
    origin(invite['gateway'])
    if not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', invite['user_id']):
        raise Error('Invalid Fusion user ID.')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', invite['github_login']):
        raise Error('Invalid GitHub login.')
    if (not invite['key'].startswith('sk-') or any(not 33 <= ord(c) <= 126 for c in invite['key'])
            or invite.get('models') != [alias(invite['tier'])]
            or invite.get('default_model') != alias(invite['tier'])):
        raise Error('Invalid personal key or Fusion model selection.')
    if any(field in invite for field in ('fallback_policy', 'credential', 'provider_token', 'github_token')):
        raise Error('Fusion invitations cannot contain provider credentials or routing policies.')


def validate_key(invite):
    validate_invitation(invite)
    api = API(invite['gateway'], invite['key'])
    info = key_info(api)
    md = info.get('metadata') or {}
    if (info.get('user_id') != invite['user_id'] or info.get('models') != invite['models']
            or set(info.get('allowed_routes') or []) != set(ROUTES)
            or md.get('fusion_gateway') != marker(invite['user_id'], invite['tier'])
            or md.get('onboarding_package') != OWNER
            or info.get('aliases') or info.get('object_permission') or info.get('access_groups')
            or info.get('router_settings')):
        raise Error('The personal key does not match the restricted Fusion gateway grant.')
    user = api.call('/user/info').get('user_info', {})
    if (user.get('user_id') != invite['user_id'] or user.get('user_role') not in ('internal_user', 'internal_user_viewer')
            or (user.get('metadata') or {}).get('github_login', '').lower() != invite['github_login'].lower()):
        raise Error('The invitation does not match the personal gateway user.')
    return api


def enroll(api, upstream, subject, login, destination, tier='standard', apply=False):
    register(api, upstream)
    name = alias(tier)
    if not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', subject):
        raise Error('Select a valid personal LiteLLM user ID.')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', login):
        raise Error('Invalid GitHub login.')
    login = login.lower()
    user = api.call('/user/info?user_id=' + quote(subject, safe='')).get('user_info', {})
    if (user.get('user_id') != subject or user.get('user_role') not in ('internal_user', 'internal_user_viewer')
            or (user.get('metadata') or {}).get('github_login', '').lower() != login):
        raise Error('Select an existing personal developer user with matching administrator-managed github_login metadata.')
    path = Path(destination).expanduser()
    if path.is_symlink():
        raise Error('Invitation path must not be a symlink.')
    if path.exists():
        previous = read(path)
        validate_invitation(previous)
        if (previous['gateway'], previous['user_id'], previous['github_login'], previous['tier']) != (api.url, subject, login, tier):
            raise Error('The invitation file belongs to another user or tier.')
        validate_key(previous)
        path.chmod(0o600)
        return {'action': 'reused', 'invitation': str(path), 'model_requests': 0}
    if not apply:
        return {'action': 'issue-personal-fusion-key', 'user_id': subject, 'model': name, 'applied': False}
    result = api.call('/key/generate', 'POST', {'user_id': subject, 'key_alias': 'fusion-' + uuid.uuid4().hex[:12],
        'duration': '90d', 'models': [name], 'allowed_routes': ROUTES, 'send_invite_email': False,
        'metadata': {'onboarding_package': OWNER, 'fusion_gateway': marker(subject, tier)}})
    invite = {'format': FORMAT, 'gateway': api.url, 'user_id': subject, 'github_login': login,
              'tier': tier, 'models': [name], 'default_model': name, 'key': result.get('key'), 'expires': result.get('expires')}
    validate_invitation(invite)
    private_write(path, invite)
    return {'action': 'issued', 'invitation': str(path), 'model_requests': 0,
            'prerequisite': 'This user must have an unexpired dedicated GitHub credential in the Fusion service.'}
