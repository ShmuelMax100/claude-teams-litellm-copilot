#!/usr/bin/env python3
"""Verify the Fusion gateway and issue personal, restricted developer keys."""
import argparse
import getpass
import json
from pathlib import Path
from gateway import API, Error
from fusion_gateway import enroll, model_rows, register


def main(argv=None):
    parser = argparse.ArgumentParser(prog='./setup admin', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'enroll'):
        command = commands.add_parser(name)
        command.add_argument('--gateway', required=True)
        command.add_argument('--upstream', required=True)
        command.add_argument('--apply', action='store_true')
        if name == 'enroll':
            command.add_argument('--user-id', required=True)
            command.add_argument('--github-login', required=True)
            command.add_argument('--tier', choices=['standard', 'high', 'max'], default='standard')
            command.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    key = getpass.getpass('LiteLLM administrator key (hidden): ').strip()
    if not key:
        raise Error('No administrator key supplied.')
    api = API(args.gateway, key)
    result = register(api, args.upstream, args.apply) if args.command == 'prepare' else enroll(
        api, args.upstream, args.user_id, args.github_login, args.out, args.tier, args.apply)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    main()
