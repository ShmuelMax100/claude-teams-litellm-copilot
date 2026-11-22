#!/usr/bin/env python3
"""Offline checks for the distributable package; no provider requests or credentials."""
import ast
import json
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIRS = {'.git', '.terraform', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules', 'private', 'dist', '_site'}


def source_files():
    """Scan distributable source, including new files, without walking tool caches."""
    import os
    for directory, names, files in os.walk(ROOT):
        names[:] = [name for name in names if name not in CACHE_DIRS and not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                yield path

class Guide(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.anchors = []
        self.images = []
        self.external = []
        self.journeys = []
        self.buttons = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.append(a['id'])
        if tag == 'a' and a.get('href', '').startswith('#'):
            self.anchors.append(a['href'][1:])
        if tag == 'img' and a.get('src'):
            self.images.append(a['src'])
        if tag in ('script', 'link') and (a.get('src') or a.get('href')):
            self.external.append(a)
        if 'data-journey' in a:
            self.journeys.append(a)
        if 'data-role' in a:
            self.buttons.append(a)

def main():
    files = list(source_files())
    for p in files:
        if p.suffix == '.py':
            # Standalone onboarding remains 3.10; the service explicitly needs 3.11.
            version = (3, 11) if p.is_relative_to(ROOT / 'services') else (3, 10)
            ast.parse(p.read_text(), feature_version=version)
        if p.suffix == '.json':
            json.loads(p.read_text())
    subprocess.run(['sh', '-n', str(ROOT / 'setup')], check=True)
    page = (ROOT / 'START-HERE.html').read_text()
    guide = Guide()
    guide.feed(page)
    assert len(guide.ids) == len(set(guide.ids)), 'Duplicate HTML IDs'
    assert set(guide.anchors) <= set(guide.ids), 'Broken section anchor'
    assert not guide.external, 'Guide must be self-contained'
    assert not guide.images and '<svg ' in page, 'Guide must embed its architecture diagram'
    assert {x['data-journey'] for x in guide.journeys} == {'admin', 'developer'}
    assert all('hidden' in x for x in guide.journeys), 'No journey may be selected initially'
    assert len(guide.buttons) == 2 and all(x.get('aria-pressed') == 'false' for x in guide.buttons)
    with tempfile.TemporaryDirectory() as temp:
        scripts = re.findall(r'<script>(.*?)</script>', page, re.S)
        for i, source in enumerate(scripts):
            p = Path(temp) / f'guide-{i}.js'
            p.write_text(source)
            subprocess.run(['node', '--check', str(p)], check=True)
    for doc in [ROOT / 'README.md', ROOT / 'docs/CONFIGURATION.md']:
        for target in re.findall(r'\]\(([^)]+)\)', doc.read_text()):
            if '://' not in target and not target.startswith('#'):
                assert (doc.parent / target.split('#')[0]).exists(), f'Broken file link: {target}'
    assert not [p for p in files if p.name.endswith('.invite.json')], 'Invitation found in package'
    print('PASS: Python compatibility (onboarding 3.10 / service 3.11), JSON, shell, inline JavaScript, guide assets/roles/anchors and documentation file links.')

if __name__ == '__main__':
    main()
