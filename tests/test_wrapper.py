import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_engine'))
import cli
import developer
from gateway import Error


class WrapperTests(unittest.TestCase):
    def test_dragged_invitation_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'my invitation.json'
            path.touch()
            self.assertEqual(cli.input_path("'" + str(path) + "'"), path)
            self.assertEqual(cli.input_path(str(path).replace(' ', '\\ ')), path)

    def test_doctor_before_join_explains_next_step(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(Error, './setup developer'):
            developer.doctor(directory=directory)

    def test_entrypoint_and_model_list_only_expose_fusion(self):
        result = subprocess.run([str(ROOT / 'setup'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Fusion only', result.stdout)
        result = subprocess.run([str(ROOT / 'setup'), 'models'], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['models'], ['fusion-standard', 'fusion-high', 'fusion-max'])
        for args in (['--mode', 'legacy'], ['--mode', 'copilot-only'], ['fusion', 'fallback-connect']):
            result = subprocess.run([str(ROOT / 'setup')] + args, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_doctor_dispatch(self):
        with patch.object(developer, 'main') as main:
            cli.main(['doctor', '--invite', '/private/invite.json'])
        main.assert_called_once_with(['doctor', '--invite', '/private/invite.json'])
