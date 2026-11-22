import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_engine'))
import developer
from gateway import Error, origin, private_write, read, load_catalog


def invitation(subject='alice', tier='standard'):
    return {'format': 'fusion-gateway-v1', 'gateway': 'https://gateway.example', 'key': 'sk-' + 'synthetic-personal-key',
            'user_id': subject, 'github_login': subject, 'tier': tier, 'models': ['fusion-' + tier], 'default_model': 'fusion-' + tier}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / '.claude'
        self.invite = invitation()

    def tearDown(self):
        self.temp.cleanup()

    def test_sole_route_configuration_private_backup_and_restore(self):
        before = {'theme': 'dark', 'permissions': {'deny': ['Bash(rm *)']}, 'apiKeyHelper': 'old-helper',
                  'env': {'CUSTOM_VALUE': 'keep', 'ANTHROPIC_API_KEY': 'old-provider-key', 'ANTHROPIC_AUTH_TOKEN': 'old',
                          'ANTHROPIC_CUSTOM_HEADERS': 'x-litellm-api-key: old', 'ANTHROPIC_SMALL_FAST_MODEL': 'old-provider',
                          'CLAUDE_CODE_USE_BEDROCK': '1'}}
        private_write(self.directory / 'settings.json', before)
        developer.configure(self.invite, self.directory)
        after = read(self.directory / 'settings.json')
        self.assertEqual(after['permissions'], before['permissions'])
        self.assertEqual(after['env']['CUSTOM_VALUE'], 'keep')
        self.assertEqual(after['env']['ANTHROPIC_AUTH_TOKEN'], self.invite['key'])
        self.assertNotIn('apiKeyHelper', after)
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_CUSTOM_HEADERS', 'CLAUDE_CODE_USE_BEDROCK'):
            self.assertNotIn(name, after['env'])
        for name in developer.MODEL_ENV:
            self.assertEqual(after['env'][name], 'fusion-standard')
        self.assertEqual((self.directory / 'settings.json').stat().st_mode & 0o777, 0o600)
        state = read(self.directory / 'gateway-onboarding.json')
        self.assertNotIn('key', state)
        self.assertEqual(read(state['backup']), before)
        developer.undo(self.directory)
        self.assertEqual(read(self.directory / 'settings.json'), before)

    def test_repeated_install_creates_one_backup(self):
        developer.configure(self.invite, self.directory)
        developer.configure(self.invite, self.directory)
        self.assertEqual(len(list((self.directory / 'backups').iterdir())), 1)

    def test_restore_removes_new_settings_but_refuses_later_changes(self):
        developer.configure(self.invite, self.directory)
        developer.undo(self.directory)
        self.assertFalse((self.directory / 'settings.json').exists())
        developer.configure(self.invite, self.directory)
        settings = self.directory / 'settings.json'
        private_write(settings, dict(read(settings), theme='changed'))
        with self.assertRaises(Error):
            developer.undo(self.directory)

    def test_symlink_not_overwritten(self):
        target = Path(self.temp.name) / 'managed.json'
        private_write(target, {'theme': 'dark'})
        self.directory.mkdir()
        (self.directory / 'settings.json').symlink_to(target)
        with self.assertRaises(Error):
            developer.configure(self.invite, self.directory)
        self.assertEqual(read(target), {'theme': 'dark'})

    def test_join_auth_failure_precedes_settings_and_paid_requests_absent(self):
        with patch.object(developer, 'preflight'), patch.object(developer, 'validate_key', side_effect=Error('expired')):
            with self.assertRaises(Error):
                developer.join(self.invite, self.directory)
        self.assertFalse((self.directory / 'settings.json').exists())
        with patch.object(developer, 'preflight'), patch.object(developer, 'validate_key') as validate:
            result = developer.join(self.invite, self.directory)
        self.assertEqual(result['model_requests'], 0)
        validate.assert_called_once_with(self.invite)

    def test_doctor_checks_installed_configuration_without_inference(self):
        developer.configure(self.invite, self.directory)
        with patch.object(developer, 'preflight'), patch.object(developer, 'validate_key') as validate:
            self.assertEqual(developer.doctor(directory=self.directory)['model_requests'], 0)
            validate.assert_called_once()
        settings = self.directory / 'settings.json'
        changed = read(settings)
        changed['env']['ANTHROPIC_SMALL_FAST_MODEL'] = 'other'
        private_write(settings, changed)
        with patch.object(developer, 'preflight'), self.assertRaises(Error):
            developer.doctor(directory=self.directory)

    def test_shell_override_and_old_invitation_rejected(self):
        with patch.object(developer.shutil, 'which', return_value='/bin/claude'), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'old'}):
            with self.assertRaises(Error):
                developer.preflight()
        for old in ({'format': 1}, {'format': 2, 'mode': 'copilot-only'}, {'format': 'fusion-fallback-v1'}):
            with self.assertRaises(Error):
                developer.configure(old, self.directory)

    def test_doctor_rejects_client_options_that_break_fusion_requests(self):
        developer.configure(self.invite, self.directory)
        settings = self.directory / 'settings.json'
        installed = read(settings)
        for name, value in (('CLAUDE_CODE_EFFORT_LEVEL', 'high'),
                            ('CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS', '0'),
                            ('CLAUDE_CODE_DISABLE_STRUCTURED_OUTPUTS', '0')):
            changed = json.loads(json.dumps(installed))
            changed['env'][name] = value
            private_write(settings, changed)
            with self.subTest(option=name), patch.object(developer, 'preflight'), \
                    patch.object(developer, 'validate_key') as validate, self.assertRaises(Error):
                developer.doctor(directory=self.directory)
            validate.assert_not_called()

    def test_catalog_only_fusion_and_secure_origins(self):
        catalog = load_catalog()
        self.assertEqual([row['model_name'] for row in catalog['models']], ['fusion-standard', 'fusion-high', 'fusion-max'])
        self.assertNotIn('fallbacks', catalog)
        for value in ('http://remote.example', 'https://key@gateway.example', 'https://gateway.example/v1', 'https://gateway.example?key=x'):
            with self.assertRaises(Error):
                origin(value)
        self.assertEqual(origin('http://127.0.0.1:4198/'), 'http://127.0.0.1:4198')
