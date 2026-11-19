#!/usr/bin/env python3
"""Optional Terraform deployment workflow. Nothing deploys without explicit apply."""
import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from gateway import API, Error, origin, private_write

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / 'infra' / 'terraform'
DEFAULT_VARS = ROOT / 'private' / 'azure' / 'deployment.tfvars.json'
DEFAULT_PLAN = ROOT / 'private' / 'azure' / 'deployment.tfplan'
SAFE_OUTPUTS = ('gateway_url', 'fusion_mcp_url', 'key_vault_name', 'resource_group_name', 'key_vault_id', 'litellm_identity_id')


def _run(command, capture=False):
    """Do not echo stderr: provider diagnostics can contain secret configuration."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError:
        raise Error('Install ' + command[0] + ' before using the optional Azure workflow.') from None
    except subprocess.TimeoutExpired:
        raise Error('Deployment command timed out. Inspect Azure and Terraform state before retrying.') from None
    if result.returncode:
        raise Error(command[0] + ' ' + command[1].split('=')[0] + ' failed. Review it locally; diagnostics were withheld to protect secrets.')
    if capture:
        return result.stdout
    return None


def _terraform(directory, *arguments, capture=False):
    directory = Path(directory).expanduser().resolve()
    if not (directory / 'main.tf').is_file():
        raise Error('Select the repository Terraform directory containing main.tf.')
    return _run(['terraform', '-chdir=' + str(directory), *arguments], capture=capture)


def configure(subscription_id, name_prefix, location='westeurope', owner=None, destination=DEFAULT_VARS):
    try:
        uuid.UUID(subscription_id)
    except (ValueError, AttributeError):
        raise Error('Enter a valid Azure subscription ID.') from None
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,14}[a-z0-9]', name_prefix):
        raise Error('Environment name must be 3–16 lowercase letters, numbers or hyphens, starting with a letter and ending with a letter or digit.')
    if not re.fullmatch(r'[a-z][a-z0-9]{1,47}', location):
        raise Error('Use an Azure region identifier such as westeurope.')
    destination = Path(destination).expanduser()
    if destination.exists() or destination.is_symlink():
        raise Error('Configuration already exists. Review it manually rather than overwriting it.')
    value = {'subscription_id': subscription_id, 'name_prefix': name_prefix, 'location': location,
             'enable_fusion_mcp': False, 'enable_managed_postgresql': True,
             'enable_private_networking': False,
             'tags': {'environment': 'dev', 'owner': owner or getpass.getuser()}}
    private_write(destination, value)
    return {'configuration': str(destination), 'deployed': False,
            'next': './setup azure plan --var-file ' + str(destination)}


def choose_subscription(input_fn=input):
    """Read Azure CLI's local account list; do not change its selected account."""
    accounts = json.loads(_run(['az', 'account', 'list', '--output', 'json'], capture=True))
    accounts = [row for row in accounts if row.get('state') == 'Enabled']
    if not accounts:
        raise Error('No enabled Azure subscription found. Run az login, then retry.')
    for index, row in enumerate(accounts, 1):
        print(str(index) + ') ' + str(row.get('name', '')) + ' (' + row['id'] + ')')
    answer = input_fn('Choose subscription: ').strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(accounts):
        raise Error('Choose one listed subscription number.')
    return accounts[int(answer) - 1]['id']


def plan(directory=DEFAULT_DIRECTORY, var_file=DEFAULT_VARS, plan_file=DEFAULT_PLAN):
    var_file = Path(var_file).expanduser()
    plan_file = Path(plan_file).expanduser()
    if not var_file.is_file() or var_file.is_symlink():
        raise Error('Create a private variables file with ./setup azure configure first.')
    if plan_file.is_symlink():
        raise Error('Plan file must not be a symlink.')
    var_file = var_file.resolve()
    plan_file = plan_file.resolve()
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.chmod(0o700)
    # A saved Terraform plan includes secret values; restrict creation permissions.
    previous_umask = os.umask(0o077)
    try:
        _terraform(directory, 'init', '-input=false', '-no-color')
        _terraform(directory, 'fmt', '-check', '-recursive')
        _terraform(directory, 'validate', '-no-color')
        _terraform(directory, 'plan', '-input=false', '-no-color', '-var-file=' + str(var_file), '-out=' + str(plan_file))
    finally:
        os.umask(previous_umask)
    if not plan_file.is_file():
        raise Error('Terraform did not produce the saved plan; no apply is available.')
    plan_file.chmod(0o600)
    return {'plan': str(plan_file), 'deployed': False,
            'review': 'Review locally with terraform -chdir=' + str(Path(directory).resolve()) + ' show ' + str(plan_file),
            'next': './setup azure apply --plan-file ' + str(plan_file)}


def apply(directory=DEFAULT_DIRECTORY, plan_file=DEFAULT_PLAN, confirmation=None, input_fn=input):
    plan_file = Path(plan_file).expanduser()
    if plan_file.is_symlink() or not plan_file.is_file():
        raise Error('Create and review a saved plan first. No infrastructure was changed.')
    print('Applying this saved plan creates or changes billable Azure resources.')
    print('Review the exact saved plan before continuing: ' + str(plan_file))
    answer = confirmation if confirmation is not None else input_fn('Type APPLY to deploy this reviewed plan: ')
    if answer != 'APPLY':
        raise Error('Deployment cancelled. No infrastructure was changed.')
    _terraform(directory, 'apply', '-input=false', '-no-color', str(plan_file.resolve()))
    return outputs(directory)


def outputs(directory=DEFAULT_DIRECTORY):
    result = json.loads(_terraform(directory, 'output', '-json', capture=True))
    safe = {name: result[name]['value'] for name in SAFE_OUTPUTS
            if name in result and not result[name].get('sensitive', True)}
    if safe.get('gateway_url'):
        safe['gateway_url'] = origin(safe['gateway_url'])
    safe['next'] = './setup admin → Prepare customer gateway, using gateway_url and the master key from Key Vault.'
    return safe


def validate(gateway, key):
    api = API(gateway, key)
    details = api.call('/health/readiness/details')
    if details.get('status') != 'healthy' or details.get('db') != 'connected':
        raise Error('Gateway readiness or persistent database connectivity failed.')
    models = api.call('/v1/models').get('data', [])
    return {'gateway': api.url, 'database': 'connected', 'litellm_version': details.get('litellm_version'),
            'registered_models': [item.get('id') for item in models], 'model_requests': 0,
            'next': './setup admin to prepare routes; existing developer onboarding stays unchanged.'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='./setup azure', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('configure', help='Write private nonsecret variables; no deployment')
    config.add_argument('--subscription-id')
    config.add_argument('--name-prefix')
    config.add_argument('--location', default='westeurope')
    config.add_argument('--owner')
    config.add_argument('--out', type=Path, default=DEFAULT_VARS)
    for name in ('plan', 'apply', 'outputs'):
        command = commands.add_parser(name)
        command.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
        if name == 'plan':
            command.add_argument('--var-file', type=Path, default=DEFAULT_VARS)
        if name in ('plan', 'apply'):
            command.add_argument('--plan-file', type=Path, default=DEFAULT_PLAN)
    health = commands.add_parser('validate', help='Read readiness/database/model registry without model execution')
    health.add_argument('--gateway', required=True)
    args = parser.parse_args(argv)
    if args.command == 'configure':
        result = configure(args.subscription_id or choose_subscription(), args.name_prefix or input('Environment name: ').strip(),
                           args.location, args.owner, args.out)
    elif args.command == 'plan':
        result = plan(args.directory, args.var_file, args.plan_file)
    elif args.command == 'apply':
        result = apply(args.directory, args.plan_file)
    elif args.command == 'outputs':
        result = outputs(args.directory)
    else:
        key = getpass.getpass('LiteLLM administrator key (hidden): ').strip()
        if not key:
            raise Error('No administrator key supplied.')
        result = validate(args.gateway, key)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    try:
        main()
    except (Error, ValueError, KeyError, OSError) as exc:
        print('Azure setup needs attention: ' + str(exc), file=sys.stderr)
        sys.exit(1)
