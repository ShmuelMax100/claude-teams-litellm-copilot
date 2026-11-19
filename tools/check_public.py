#!/usr/bin/env python3
"""Reject private execution artifacts and common secret formats in public source."""
import re
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENIED_NAMES = {'TEST-RESULTS.json', 'COMMAND-AUDIT.json', 'WALKTHROUGH.txt', '.env'}
PLACEHOLDERS = ('sk-unit-test-placeholder', 'sk-test-not-a-real-credential')
CACHE_DIRS = {'.git', '__pycache__', 'dist', '_site', 'private', '.terraform', '.venv', 'venv', '.pytest_cache', 'node_modules'}
PATTERNS = {
    'provider credential': r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})',
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'provider request ID': r'\b(?:msg|req)_[A-Za-z0-9]{12,}',
    'personal home path': r'/Users/[A-Za-z0-9_.-]+/',
    'workstation hostname': r'[A-Za-z0-9-]+\.local\b',
    'Azure resource identity': r'/subscriptions/[0-9a-fA-F-]{36}/',
}

def source_files():
    """Include tracked files even if ignored, plus new source outside known caches."""
    paths = set()
    try:
        listed = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z'])
        paths.update(ROOT / name for name in listed.decode().split('\0') if name)
    except (OSError, subprocess.CalledProcessError):
        pass  # Extracted release packages need no Git installation.
    for directory, names, files in os.walk(ROOT):
        names[:] = [name for name in names if name not in CACHE_DIRS and not (Path(directory) / name).is_symlink()]
        paths.update(Path(directory) / name for name in files)
    return sorted(path for path in paths if path.is_file())


def scan_text(text, relative_path):
    for placeholder in PLACEHOLDERS:
        text = text.replace(placeholder, 'TEST_PLACEHOLDER')
    # Only the synthetic all-zero UUID namespace in the provider-mock test is exempt.
    if relative_path.as_posix() == 'infra/terraform/tests/deployment.tftest.hcl':
        text = re.sub(r'/subscriptions/00000000-0000-0000-0000-00000000000[0-9]/', '/subscriptions/SYNTHETIC/', text)
    return [label for label, pattern in PATTERNS.items() if re.search(pattern, text)]


def main():
    findings = []
    for path in source_files():
        rel = path.relative_to(ROOT)
        if path.is_symlink():
            findings.append((str(rel), 'symlink in public source'))
            continue
        if path.name in DENIED_NAMES or path.name.endswith(('.invite.json', '.pem', '.pfx', '.p12', '.bundle', '.log', '.tfstate', '.tfstate.backup', '.tfplan')):
            findings.append((str(rel), 'private artifact'))
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.pyc'}:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            findings.append((str(rel), 'unexpected binary in public source'))
            continue
        findings.extend((str(rel), label) for label in scan_text(text, rel))
    if findings:
        for path, reason in findings:
            print(f'FAIL: {path}: {reason}')
        raise SystemExit(1)
    print('PASS: no private execution files, common credential formats or personal execution identifiers found.')

if __name__ == '__main__':
    main()
