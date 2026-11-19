#!/usr/local/bin/python
"""Synthetic executable for Docker integration. Never contacts GitHub."""
import json
import os
from pathlib import Path
import sys

FLAGS = ('--fusion-tier', '--model', '--agent', '--deny-tool', '--disable-builtin-mcps',
         '--disable-mcp-server', '--no-ask-user', '--no-auto-update', '--no-custom-instructions',
         '--no-bash-env', '--disable-client-telemetry', '--sync-session', '--log-level', '--silent')

if '--help' in sys.argv:
    print(' '.join(FLAGS))
elif '--version' in sys.argv:
    print('GitHub Copilot CLI 0.0.0 (synthetic fixture)')
else:
    assert all(flag in ' '.join(sys.argv) for flag in FLAGS)
    assert sys.argv[sys.argv.index('--model') + 1] == 'fusion'
    assert sys.argv[sys.argv.index('--deny-tool') + 1] == 'read,write,shell,url,memory'
    assert os.environ['COPILOT_GITHUB_TOKEN'] == 'github_pat_fixture'
    assert not any(name in os.environ for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'FUSION_MCP_TOKEN',
               'FUSION_GITHUB_TOKEN', 'COPILOT_PROVIDER_API_KEY', 'ANTHROPIC_API_KEY'))
    tier = sys.argv[sys.argv.index('--fusion-tier') + 1]
    prompt = sys.argv[sys.argv.index('-p') + 1]
    home = Path(os.environ['HOME'])
    assert home.is_dir() and home != Path('/root') and Path.cwd().name == 'work'
    event = {'tier': tier, 'home': str(home), 'cwd': str(Path.cwd()), 'isolated': True}
    with open('/tmp/qa-events.jsonl', 'a') as stream:
        stream.write(json.dumps(event) + '\n')
    if prompt == 'synthetic-provider-failure':
        print(os.environ['COPILOT_GITHUB_TOKEN'], file=sys.stderr)
        sys.exit(7)
    if prompt == 'synthetic-redaction':
        print(os.environ['COPILOT_GITHUB_TOKEN'])
    else:
        print('SYNTHETIC_FUSION_' + tier.upper())
