"""Exercise the production SDK/runtime bundle with Docker network disabled."""
import asyncio
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, '/app')

from config import Config, UserCredential
from copilot_runner import CopilotRunner


async def main():
    with tempfile.TemporaryDirectory(prefix='sdk-probe-') as temporary:
        credential = UserCredential('offline-fixture', 'github_pat_offline_fixture', int(time.time()) + 3600)
        config = Config(service_token='offline-service-fixture-' + 'x' * 32,
                        identity_key='offline-identity-fixture-' + 'y' * 32,
                        credentials={'offline-fixture': credential},
                        workspace_root=Path(temporary) / 'runtime')
        runner = CopilotRunner(config)
        try:
            await asyncio.wait_for(runner.probe(), timeout=45)
            assert runner.capable, 'Bundled SDK/runtime metadata probe failed'
            # Discovery/probe must create no inference session or use a token.
            assert not list(config.workspace_root.rglob('events.jsonl'))
        finally:
            await asyncio.wait_for(runner.close(), timeout=15)
        assert not list(config.workspace_root.iterdir())
    print('PASS: real bundled SDK/runtime local probe; Docker network disabled; no model requests.')


if __name__ == '__main__':
    asyncio.run(main())
