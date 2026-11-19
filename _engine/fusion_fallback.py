"""Separate opt-in enrollment for Claude primary with native Fusion fallback."""
import argparse
import getpass
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from gateway import API, Error, key_info, load_catalog, origin, private_write, read

FORMAT = 'fusion-fallback-v1'
OWNER = 'claude-gateway-fusion-fallback-v1'
TIERS = ('standard', 'high', 'max')
ROUTES = ['/v1/messages', '/v1/messages/count_tokens', '/v1/models',
          '/v1/model/info', '/model/info', '/key/info', '/user/info']


def api_base(value):
    parsed = urlsplit(value)
    root = origin(urlunsplit((parsed.scheme, parsed.netloc, '', '', '')))
    if parsed.path.rstrip('/') != '/v1' or parsed.query or parsed.fragment:
        raise Error('Fusion inference URL must end in /v1 without query parameters.')
    return root + '/v1'


def alias(tier):
    if tier not in TIERS:
        raise Error('Select the standard, high or max Fusion tier.')
    return 'fusion-fallback-' + tier


def marker(subject, tier):
    return {'version': 1, 'owner': subject, 'tier': tier}


def policy(tier):
    destination = alias(tier)
    # Reuse the established primary spellings, without changing the catalog or
    # inheriting its direct Copilot model fallback routes.
    primary = [next(iter(row)) for row in load_catalog()['fallbacks']
               if not next(iter(row)).startswith('claude-copilot-')]
    return {'num_retries': 0, 'max_fallbacks': 1,
            'fallbacks': [{name: [destination]} for name in primary] + [{destination: []}],
            'context_window_fallbacks': [], 'content_policy_fallbacks': []}


def stored_policy_matches(value, expected):
    # LiteLLM 1.108.0's key schema omits these two fields on persistence. The
    # loaded guard enforces them on each authenticated request. Do not accept
    # changed values, unknown fields or missing persisted fallback routes.
    if not isinstance(value, dict):
        return False
    normalized = dict(value)
    for field in ('max_fallbacks', 'content_policy_fallbacks'):
        normalized.setdefault(field, expected[field])
    return normalized == expected


def check_guard(api, upstream, subject):
    from fusion import check_gateway, _user
    if not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', subject):
        raise Error('A valid dedicated single-user subject is required.')
    upstream = api_base(upstream)
    check_gateway(api)
    user = _user(api, subject)
    expected = f'fusion-fallback-guard/v1 owner={subject} api_base={upstream}'
    callbacks = api.call('/active/callbacks').get('litellm.callbacks', [])
    if not isinstance(callbacks, list) or callbacks.count(expected) != 1:
        raise Error('The running gateway must load exactly one matching Fusion fallback guard before enrollment.')
    return upstream, user


def model(tier, upstream, subject):
    return {'model_name': alias(tier),
            'litellm_params': {'model': 'custom_openai/fusion-' + tier, 'api_base': upstream,
                               'api_key': 'os.environ/FUSION_FALLBACK_BEARER_TOKEN',
                               'max_retries': 0},
            'model_info': {'cooldown_time': 0, 'onboarding_package': OWNER,
                           'fusion_fallback': marker(subject, tier)}}


def check_model(row, tier, upstream, subject):
    wanted = model(tier, upstream, subject)
    params = row.get('litellm_params') or {}
    info = row.get('model_info') or {}
    # Read APIs may mask the bearer reference. The running guard replaces any
    # provider credential with its configured service bearer on every attempt.
    return (row.get('model_name') == wanted['model_name']
            and all(params.get(k) == v for k, v in wanted['litellm_params'].items() if k != 'api_key')
            and all(info.get(k) == v for k, v in wanted['model_info'].items()))


def register(api, upstream, subject, apply=False, single_user_attested=False):
    from admin import model_rows
    if not single_user_attested:
        raise Error('Attest that this is a dedicated single-owner Fusion deployment.')
    upstream, _ = check_guard(api, upstream, subject)
    existing = model_rows(api)
    if not any(row.get('model_name') == 'anthropic/*' for row in existing):
        raise Error('Configure the existing Claude subscription primary route first.')
    pending = []
    for tier in TIERS:
        rows = [row for row in existing if row.get('model_name') == alias(tier)]
        if len(rows) > 1 or (rows and not check_model(rows[0], tier, upstream, subject)):
            raise Error('A conflicting Fusion inference route exists; nothing was changed.')
        if not rows:
            pending.append(model(tier, upstream, subject))
    if apply:
        for row in pending:
            api.call('/model/new', 'POST', row)
    return {'action': 'register-fallback-models', 'models': [row['model_name'] for row in pending],
            'user_id': subject, 'applied': apply, 'billing': 'github-copilot',
            'handoff': 'history-as-context; tools execute in Claude Code'}


def validate_invitation(invite):
    if not isinstance(invite, dict) or invite.get('format') != FORMAT:
        raise Error('Use the separate Fusion fallback invitation.')
    for field in ('gateway', 'key', 'user_id', 'github_login', 'api_base'):
        if not isinstance(invite.get(field), str) or not invite[field]:
            raise Error('Fusion fallback invitation is missing ' + field + '.')
    origin(invite['gateway'])
    api_base(invite['api_base'])
    destination = alias(invite.get('tier'))
    if (not invite['key'].startswith('sk-') or any(not 33 <= ord(c) <= 126 for c in invite['key'])
            or invite.get('models') != ['anthropic/*', destination]
            or invite.get('fallback_policy') != policy(invite['tier'])):
        raise Error('Fusion fallback invitation models, key or policy are invalid.')


def validate_key(invite):
    validate_invitation(invite)
    api = API(invite['gateway'], invite['key'])
    info = key_info(api)
    metadata = info.get('metadata') or {}
    permissions = info.get('object_permission') or {}
    if (info.get('user_id') != invite['user_id'] or set(info.get('models') or []) != set(invite['models'])
            or set(info.get('allowed_routes') or []) != set(ROUTES)
            or not stored_policy_matches(info.get('router_settings'), invite['fallback_policy'])
            or metadata.get('onboarding_package') != OWNER
            or metadata.get('fusion_fallback') != marker(invite['user_id'], invite['tier'])
            or any(permissions.values()) or info.get('access_group_ids')):
        raise Error('The personal key does not match the restricted Fusion fallback policy.')
    user = api.call('/user/info').get('user_info') or {}
    if (user.get('user_id') != invite['user_id'] or user.get('user_role') not in ('internal_user', 'internal_user_viewer')
            or (user.get('metadata') or {}).get('github_login') != invite['github_login']):
        raise Error('Fusion fallback invitation owner does not match the developer account.')
    return api


def enroll(api, upstream, subject, tier, destination, apply=False, single_user_attested=False):
    from admin import model_rows
    if not single_user_attested:
        raise Error('Attest that this is a dedicated single-owner Fusion deployment.')
    destination_alias = alias(tier)
    upstream, user = check_guard(api, upstream, subject)
    login = (user.get('metadata') or {}).get('github_login')
    if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', login):
        raise Error('Enroll an existing developer account with verified GitHub identity metadata.')
    rows = [row for row in model_rows(api) if row.get('model_name') == destination_alias]
    if len(rows) != 1 or not check_model(rows[0], tier, upstream, subject):
        raise Error('Register the matching guarded Fusion fallback route first.')
    path = Path(destination).expanduser()
    if path.is_symlink():
        raise Error('Invitation path must be a private regular file, not a symlink.')
    if path.exists():
        invite = read(path)
        validate_invitation(invite)
        if (invite['gateway'], invite['api_base'], invite['user_id'], invite['tier']) != (api.url, upstream, subject, tier):
            raise Error('This invitation belongs to another owner, tier or deployment.')
        validate_key(invite)
        path.chmod(0o600)
        return {'action': 'reuse', 'invitation': str(path)}
    if not apply:
        return {'action': 'issue-owner-fallback-key', 'user_id': subject, 'tier': tier, 'applied': False}
    models = ['anthropic/*', destination_alias]
    routing = policy(tier)
    issued = api.call('/key/generate', 'POST', {
        'user_id': subject, 'key_alias': 'fusion-fallback-' + uuid.uuid4().hex[:12],
        'duration': '90d', 'models': models, 'allowed_routes': ROUTES, 'send_invite_email': False,
        'metadata': {'onboarding_package': OWNER, 'fusion_fallback': marker(subject, tier)},
        'router_settings': routing})
    invite = {'format': FORMAT, 'gateway': api.url, 'api_base': upstream, 'user_id': subject,
              'github_login': login, 'tier': tier, 'models': models, 'fallback_policy': routing,
              'credential': 'fusion-sdk-single-owner', 'key': issued.get('key'), 'expires': issued.get('expires')}
    validate_invitation(invite)
    private_write(path, invite)
    return {'action': 'issued', 'invitation': str(path), 'billing': 'github-copilot'}


def connect(invite, directory=None):
    import developer
    validate_key(invite)
    developer.preflight()
    settings = developer.configure(invite, directory or Path.home() / '.claude')
    return {'action': 'connected', 'settings': str(settings), 'primary': 'existing Claude subscription',
            'fallback': alias(invite['tier']), 'billing': 'github-copilot',
            'note': 'Keep Claude subscription sign-in. Use fusion fallback-doctor for this invitation; no provider calls were made.'}


def main(argv):
    parser = argparse.ArgumentParser(prog='./setup fusion')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('fallback-register', 'fallback-enroll'):
        item = commands.add_parser(name)
        item.add_argument('--gateway', required=True)
        item.add_argument('--upstream', required=True, help='Private inference origin ending in /v1')
        item.add_argument('--single-user-subject', required=True)
        item.add_argument('--single-user-attested', action='store_true', required=True)
        item.add_argument('--apply', action='store_true')
        if name == 'fallback-enroll':
            item.add_argument('--tier', choices=TIERS, required=True)
            item.add_argument('--out', type=Path, required=True)
    for name in ('fallback-connect', 'fallback-doctor'):
        commands.add_parser(name).add_argument('--invite', type=Path, required=True)
    commands.add_parser('fallback-restore')
    args = parser.parse_args(argv)
    if args.command in ('fallback-register', 'fallback-enroll'):
        secret = getpass.getpass('LiteLLM administrator key (hidden): ').strip()
        api = API(args.gateway, secret)
        if args.command == 'fallback-register':
            result = register(api, args.upstream, args.single_user_subject, args.apply, args.single_user_attested)
        else:
            result = enroll(api, args.upstream, args.single_user_subject, args.tier, args.out,
                            args.apply, args.single_user_attested)
    elif args.command == 'fallback-restore':
        import developer
        developer.undo()
        result = {'action': 'previous-settings-restored'}
    else:
        invite = read(args.invite.expanduser())
        if args.command == 'fallback-connect':
            result = connect(invite)
        else:
            validate_key(invite)
            result = {'action': 'validated-owner-key-policy', 'tier': invite['tier'], 'provider_calls': 0}
    print(json.dumps(result, indent=2))
    return result
