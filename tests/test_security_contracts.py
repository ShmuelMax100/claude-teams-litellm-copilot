"""Independent boundary tests; no provider, Azure or Docker access required."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_engine'))
import admin
import fusion
from gateway import Error, load_catalog, private_write, read

spec = importlib.util.spec_from_file_location('public_scan', ROOT / 'tools/check_public.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class SecurityContracts(unittest.TestCase):
    def test_default_enrollment_does_not_grant_mcp_or_change_fallbacks(self):
        class API:
            url = 'https://gateway.example'

            def __init__(self):
                self.writes = []

            def call(self, path, method='GET', body=None, missing=False):
                if path.startswith('/v2/model/info'):
                    return {'data': load_catalog()['models'], 'total_pages': 1}
                if path.startswith('/user/info'):
                    return None
                self.writes.append((path, body))
                return {'key': 'sk-unit-test-placeholder'} if path == '/key/generate' else {}

        api = API()
        with tempfile.TemporaryDirectory() as directory:
            admin.enroll(api, load_catalog(), 'dev@example.com', 'developer', Path(directory) / 'invitation.json')
        payload = next(body for path, body in api.writes if path == '/key/generate')
        self.assertFalse(any('/mcp' in route for route in payload['allowed_routes']))
        self.assertNotIn('object_permission', payload)
        self.assertEqual(payload['router_settings']['fallbacks'], load_catalog()['fallbacks'])

    def test_single_owner_attestation_precedes_network(self):
        class NoNetwork:
            def call(self, *args, **kwargs):
                raise AssertionError('Network access before attestation')

        with self.assertRaisesRegex(Error, 'Attest'):
            fusion.register(NoNetwork(), 'https://fusion.example/mcp', 'user')

    def test_mcp_installer_denied_before_local_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            private_write(home / '.claude.json', {'mcpServers': {'unrelated': {'url': 'https://example.invalid/mcp'}}})
            before = (home / '.claude.json').read_bytes()
            with patch.object(fusion, 'discover', side_effect=Error('not authorized')):
                with self.assertRaises(Error):
                    fusion.install({}, home)
            self.assertEqual((home / '.claude.json').read_bytes(), before)
            self.assertFalse((home / '.claude/backups').exists())

    def test_public_scan_only_exempts_mock_subscription_namespace(self):
        synthetic = '/subscriptions/' + '00000000-0000-0000-0000-000000000003' + '/'
        actual = '/subscriptions/' + '12345678-1234-1234-1234-123456789abc' + '/'
        tests = Path('infra/terraform/tests/deployment.tftest.hcl')
        self.assertEqual(scan.scan_text(synthetic, tests), [])
        self.assertIn('Azure resource identity', scan.scan_text(actual, tests))
        self.assertIn('Azure resource identity', scan.scan_text(synthetic, Path('README.md')))
        token = 'github_pat_' + 'A' * 30
        self.assertIn('provider credential', scan.scan_text(token, tests))

    def test_runtime_cache_is_excluded_but_tracked_ignored_secret_is_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.terraform').mkdir()
            (root / '.terraform/provider').write_bytes(b'\x00\xff')
            (root / 'private').mkdir()
            secret = root / 'private/accidentally-tracked.env'
            secret.write_text('a secret')
            (root / 'new.py').write_text('pass')
            with patch.object(scan, 'ROOT', root), patch.object(scan.subprocess, 'check_output', return_value=b'private/accidentally-tracked.env\0'):
                files = scan.source_files()
            self.assertIn(secret, files)
            self.assertIn(root / 'new.py', files)
            self.assertNotIn(root / '.terraform/provider', files)


if __name__ == '__main__':
    unittest.main()
