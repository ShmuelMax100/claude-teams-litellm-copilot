"""Reproducibly build the official tagged SDK when wheel CDN access is unavailable.

This is a source build, not a claim of equivalence to the published PyPI wheel.
Only release metadata and the runtime sentinel are injected, matching the
release's documented build convention. The source archive is checksum pinned.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

COMMIT = '3ce61458503b6b27ebd1f674a980f4d9596d9b22'
SHA256 = '2b134f8b56dcd162da7c434dfad578f27af927e8405a8cf020be1a898d09f4c4'
SDK_VERSION = '2.0.1'
RUNTIME_VERSION = '1.0.114'
URL = f'https://codeload.github.com/microsoft/copilot-sdk/tar.gz/{COMMIT}'


def main():
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / 'sdk.tar.gz'
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('SDK source checksum mismatch')
        with tarfile.open(archive) as bundle:
            bundle.extractall(root, filter='data')
        source = root / ('copilot-sdk-' + COMMIT)
        metadata = json.loads((source / 'nodejs/package.json').read_text())
        if metadata['copilotCliVersion'] != RUNTIME_VERSION:
            raise RuntimeError('SDK release runtime pin changed')
        project = source / 'python/pyproject.toml'
        text = project.read_text()
        assert text.count('version = "0.0.0.dev0"') == 1
        project.write_text(text.replace('version = "0.0.0.dev0"', f'version = "{SDK_VERSION}"'))
        runtime = source / 'python/copilot/_cli_version.py'
        text = runtime.read_text()
        assert text.count('CLI_VERSION: str | None = None') == 1
        runtime.write_text(text.replace('CLI_VERSION: str | None = None',
                                       f'CLI_VERSION: str | None = "{RUNTIME_VERSION}"'))
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                        '--no-build-isolation', '--wheel-dir', str(destination),
                        str(source / 'python')], check=True,
                       env={**os.environ, 'SOURCE_DATE_EPOCH': '315532800'})
    (destination / 'sdk-provenance.json').write_text(json.dumps({
        'sdk_version': SDK_VERSION, 'runtime_version': RUNTIME_VERSION,
        'source_commit': COMMIT, 'source_archive_sha256': SHA256,
        'distribution': 'built from official release source'}, indent=2) + '\n')


if __name__ == '__main__':
    main()
