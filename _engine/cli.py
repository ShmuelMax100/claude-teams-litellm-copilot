#!/usr/bin/env python3
"""Claude Code → ACA: personal LiteLLM gateway access to Fusion only."""
import argparse
import json
import shlex
import sys
from pathlib import Path
from gateway import Error, load_catalog


def input_path(value):
    raw = Path(value).expanduser()
    if raw.exists():
        return raw
    try:
        parts = shlex.split(value)
    except ValueError:
        return raw
    return Path(parts[0]).expanduser() if len(parts) == 1 else raw


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('admin', 'developer', 'azure'):
        import importlib
        return importlib.import_module(argv[0]).main(argv[1:])
    if argv and argv[0] == 'doctor':
        import developer
        return developer.main(['doctor'] + argv[1:])
    parser = argparse.ArgumentParser(prog='./setup', description=__doc__,
        epilog='Use ./setup admin --help, ./setup developer --help or ./setup azure --help. No model calls are made during onboarding.')
    parser.add_argument('command', nargs='?', choices=['models'])
    args = parser.parse_args(argv)
    if args.command == 'models':
        print(json.dumps({'models': [row['model_name'] for row in load_catalog()['models']],
                          'routing': 'Every request uses Fusion. Internal model selection belongs to GitHub.'}, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nSetup cancelled. You can rerun ./setup.')
        sys.exit(130)
    except (Error, ValueError, KeyError, OSError) as exc:
        print('Setup needs attention: ' + str(exc), file=sys.stderr)
        sys.exit(1)
