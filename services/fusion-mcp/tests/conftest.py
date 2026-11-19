from dataclasses import replace
from pathlib import Path
import sys
import pytest

from config import Config
from copilot_runner import REQUIRED_FLAGS


@pytest.fixture
def config(tmp_path):
    executable = tmp_path / "fake-copilot"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, subprocess, sys, time
if '--help' in sys.argv:
    print(HELP)
elif '--version' in sys.argv:
    print('GitHub Copilot CLI 1.0.114.')
else:
    prompt = sys.argv[sys.argv.index('-p') + 1]
    if prompt == 'sleep': time.sleep(30)
    elif prompt == 'overflow': print('x' * 1000000)
    elif prompt == 'fail':
        print(os.environ['COPILOT_GITHUB_TOKEN'], file=sys.stderr)
        sys.exit(7)
    elif prompt == 'secrets': print(os.environ['COPILOT_GITHUB_TOKEN'])
    elif prompt == 'env': print(json.dumps(dict(os.environ, CWD=os.getcwd(), ARGS=sys.argv)))
    elif prompt == 'slow':
        time.sleep(.15)
        print(json.dumps({'home': os.environ['HOME'], 'cwd': os.getcwd()}))
    elif prompt == 'child':
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        open(os.path.join(os.environ['HOME'], 'pid'), 'w').write(str(child.pid))
        time.sleep(60)
    else: print('mock answer')
'''.replace("print(HELP)", "print(" + repr(" ".join(REQUIRED_FLAGS)) + ")"))
    executable.chmod(0o700)
    return Config(auth_mode="single_user", subject="owner-user-id",
        github_token="github_pat_testonly_0123456789", mcp_token="mcp-test-only-" + "x" * 32,
        workspace_root=tmp_path / "workspaces", copilot_bin=str(executable), timeout_seconds=2)
