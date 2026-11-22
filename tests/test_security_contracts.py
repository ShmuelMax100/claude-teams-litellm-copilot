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
from gateway import Error, load_catalog, private_write, read

spec = importlib.util.spec_from_file_location('public_scan', ROOT / 'tools/check_public.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class SecurityContracts(unittest.TestCase):
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

    def test_local_terraform_state_is_not_distribution_but_tracked_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'terraform.tfstate'
            state.write_text('{}')
            with patch.object(scan, 'ROOT', root), patch.object(scan.subprocess, 'check_output', return_value=b''):
                self.assertNotIn(state, scan.source_files())
            with patch.object(scan, 'ROOT', root), patch.object(scan.subprocess, 'check_output', return_value=b'terraform.tfstate\0'):
                self.assertIn(state, scan.source_files())


if __name__ == '__main__':
    unittest.main()
