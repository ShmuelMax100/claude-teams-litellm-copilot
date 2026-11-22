#!/usr/bin/env python3
"""Connect Claude Code to the Fusion gateway without executing a model request."""
import argparse
import datetime
import hashlib
import json
import os
import shutil
from pathlib import Path

from gateway import Error, private_write, read
from fusion_gateway import validate_invitation, validate_key

CONFLICTS = ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_MODEL',
             'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS', 'ANTHROPIC_SMALL_FAST_MODEL',
             'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
             'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
             'CLAUDE_CODE_EFFORT_LEVEL', 'CLAUDE_CODE_DISABLE_THINKING', 'CLAUDE_CODE_MAX_RETRIES', 'CLAUDE_CODE_RETRY_WATCHDOG',
             'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS', 'CLAUDE_CODE_DISABLE_STRUCTURED_OUTPUTS']
MODEL_ENV = ('ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
             'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL')
COMPATIBILITY_ENV = {'CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY': '1', 'CLAUDE_CODE_AUTO_MODE_SERVER': '0',
    'CLAUDE_CODE_DISABLE_THINKING': '1', 'CLAUDE_CODE_EFFORT_LEVEL': 'auto', 'CLAUDE_CODE_MAX_RETRIES': '0',
    'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS': '1', 'CLAUDE_CODE_DISABLE_STRUCTURED_OUTPUTS': '1'}
validate_invitation_shape = validate_invitation
validate_invite = validate_key


def configure(invite, directory):
    validate_invitation(invite)
    directory = Path(directory)
    settings = directory / 'settings.json'
    state = directory / 'gateway-onboarding.json'
    if settings.is_symlink() or state.is_symlink():
        raise Error('Claude settings or onboarding state is a symlink. Update the managed source instead.')
    existed = settings.exists()
    before = read(settings) if existed else {}
    if not isinstance(before, dict) or not isinstance(before.get('env', {}), dict):
        raise Error('Claude settings must be an object with an object env field.')
    updated = json.loads(json.dumps(before))
    updated.pop('apiKeyHelper', None)
    env = updated.setdefault('env', {})
    for name in CONFLICTS:
        env.pop(name, None)
    env.update({'ANTHROPIC_BASE_URL': invite['gateway'], 'ANTHROPIC_AUTH_TOKEN': invite['key'], **COMPATIBILITY_ENV})
    for name in MODEL_ENV:
        env[name] = invite['default_model']
    updated['model'] = invite['default_model']
    previous = read(state) if state.exists() else {}
    if (before == updated and previous.get('format') == invite['format']
            and previous.get('user_id') == invite['user_id'] and previous.get('gateway') == invite['gateway']):
        return settings
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = directory / 'backups' / ('gateway-' + stamp)
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    private_write(backup / 'settings.json', before)
    private_write(settings, updated)
    saved = {key: invite[key] for key in ('format', 'gateway', 'user_id', 'github_login', 'tier', 'models', 'default_model')}
    saved.update(backup=str(backup / 'settings.json'), previously_existed=existed,
                 installed_hash=hashlib.sha256(settings.read_bytes()).hexdigest())
    private_write(state, saved)
    return settings


def preflight():
    if not shutil.which('claude'):
        raise Error('Install Claude Code first, then rerun this command.')
    if os.environ.get('CLAUDE_CONFIG_DIR'):
        raise Error('A custom CLAUDE_CONFIG_DIR is active. Use the standard Claude profile for this installer.')
    conflicts = [name for name in CONFLICTS if os.environ.get(name)]
    if conflicts:
        raise Error('Clear conflicting shell overrides before installation: ' + ', '.join(conflicts))


def join(invite, directory=None):
    preflight()
    validate_key(invite)
    settings = configure(invite, directory or Path.home() / '.claude')
    return {'action': 'connected', 'settings': str(settings), 'model': invite['default_model'], 'model_requests': 0,
            'next': 'Restart Claude Code. Fusion is used from the first request; no subscription sign-in is required.'}


def doctor(invite=None, directory=None):
    if invite is not None:
        validate_key(invite)
        return {'action': 'verified-personal-key', 'user_id': invite['user_id'], 'model': invite['default_model'],
                'model_requests': 0, 'note': 'Provider entitlement and live inference were not tested.'}
    directory = Path(directory) if directory else Path.home() / '.claude'
    if not (directory / 'gateway-onboarding.json').is_file():
        raise Error('This machine has not joined Fusion. Run ./setup developer join --invite /path/to/invitation.json.')
    preflight()
    state = read(directory / 'gateway-onboarding.json')
    settings = read(directory / 'settings.json')
    env = settings.get('env', {})
    if (env.get('ANTHROPIC_BASE_URL') != state.get('gateway') or settings.get('model') != state.get('default_model')
            or any(env.get(name) != state.get('default_model') for name in MODEL_ENV)
            or any(env.get(name) != expected for name, expected in COMPATIBILITY_ENV.items())
            or 'apiKeyHelper' in settings or any(env.get(name) for name in (
                'ANTHROPIC_API_KEY', 'ANTHROPIC_CUSTOM_HEADERS', 'CLAUDE_CODE_USE_BEDROCK',
                'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'))):
        raise Error('Claude gateway configuration changed. Review it or reconnect with your Fusion invitation.')
    return doctor(dict(state, key=env.get('ANTHROPIC_AUTH_TOKEN', '')))


def undo(directory=None):
    directory = Path(directory) if directory else Path.home() / '.claude'
    state = read(directory / 'gateway-onboarding.json')
    settings = directory / 'settings.json'
    if settings.is_symlink() or hashlib.sha256(settings.read_bytes()).hexdigest() != state['installed_hash']:
        raise Error('Settings changed since onboarding. Merge the private backup manually: ' + state['backup'])
    if state['previously_existed']:
        private_write(settings, read(state['backup']))
    else:
        settings.unlink()
    (directory / 'gateway-onboarding.json').unlink()
    return {'action': 'restored', 'note': 'The personal gateway key remains valid until revoked or expired.'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='./setup developer', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    join_parser = commands.add_parser('join')
    join_parser.add_argument('--invite', type=Path, required=True)
    doctor_parser = commands.add_parser('doctor')
    doctor_parser.add_argument('--invite', type=Path)
    commands.add_parser('restore')
    args = parser.parse_args(argv)
    if args.command == 'join':
        result = join(read(args.invite))
    elif args.command == 'doctor':
        result = doctor(read(args.invite) if args.invite else None)
    else:
        result = undo()
    print(json.dumps(result, indent=2))
    return result
