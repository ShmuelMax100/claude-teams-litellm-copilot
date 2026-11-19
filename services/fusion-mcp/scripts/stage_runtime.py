"""Download and checksum the complete official native SDK runtime bundle."""
import hashlib
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request

VERSION = '1.0.114'
ARTIFACTS = {
    'amd64': ('x64', 'e9687515f6db23c05e5403fbe1f7f5283dfa58f77240bb61ac935bed39f31727'),
    'arm64': ('arm64', '6ec2186c7bcd3da347588c66ebeca921b9568b804bb2c9c849644709ec33536c'),
}


def main():
    arch, digest = ARTIFACTS[sys.argv[1]]
    destination = Path(sys.argv[2])
    asset = f'github-copilot-{VERSION}-linux-{arch}.tgz'
    url = f'https://github.com/github/copilot-cli/releases/download/v{VERSION}/{asset}'
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        archive = root / asset
        with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise RuntimeError('Copilot runtime checksum mismatch')
        with tarfile.open(archive) as bundle:
            bundle.extractall(root / 'extracted', filter='data')
        package = root / 'extracted/package'
        candidates = list(package.rglob('copilot-runtime'))
        if len(candidates) != 1 or not (candidates[0].parent / 'runtime.node').is_file():
            raise RuntimeError('Unexpected native runtime bundle layout')
        # Preserve adjacent assets; the standalone CLI archive is insufficient.
        shutil.copytree(candidates[0].parent, destination, dirs_exist_ok=True)
        (destination / 'copilot-runtime').chmod(0o755)


if __name__ == '__main__':
    main()
