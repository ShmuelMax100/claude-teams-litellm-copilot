"""Offline deployment workflow tests; never contact Azure or run Terraform."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_engine'))
import azure
import cli
from gateway import Error, read


class AzureWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_configuration_requires_fusion_and_never_deploys(self):
        destination = self.root / 'vars.json'
        with patch.object(azure, '_run') as run:
            result = azure.configure('00000000-0000-4000-8000-000000000001', 'team-dev', destination=destination,
                fusion_image='example.azurecr.io/fusion:v1', fusion_key_vault_id='/subscriptions/example/vaults/test',
                fusion_service_token_secret_id='https://test.vault.azure.net/secrets/service',
                fusion_users={'alice': {'github_token_secret_id': 'https://test.vault.azure.net/secrets/alice', 'expires_at': 2000000000}})
        run.assert_not_called()
        self.assertFalse(result['deployed'])
        value = read(destination)
        self.assertNotIn('enable_fusion_mcp', value)
        self.assertEqual(set(value['fusion_users']), {'alice'})
        self.assertTrue(value['enable_managed_postgresql'])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(Error):
            azure.configure('00000000-0000-4000-8000-000000000001', 'other-dev', destination=destination)
    def test_configuration_matches_terraform_name_bounds_before_writing(self):
        destination = self.root / 'vars.json'
        for name in ('ab', 'ending-', 'A-name', 'a' * 17):
            with self.subTest(name=name), self.assertRaises(Error):
                azure.configure('00000000-0000-4000-8000-000000000001', name, destination=destination)
            self.assertFalse(destination.exists())
    def test_apply_requires_saved_plan_and_exact_confirmation(self):
        saved = self.root / 'deployment.tfplan'
        saved.write_text('fixture')
        with patch.object(azure, '_terraform') as terraform:
            for answer in ('yes', '', 'apply'):
                with self.subTest(answer=answer), self.assertRaises(Error):
                    azure.apply(plan_file=saved, confirmation=answer)
            terraform.assert_not_called()
        with patch.object(azure, '_terraform') as terraform, patch.object(azure, 'outputs', return_value={'gateway_url': 'https://gateway.example'}):
            result = azure.apply(plan_file=saved, confirmation='APPLY')
        self.assertEqual(terraform.call_args.args[1], 'apply')
        self.assertNotIn('-auto-approve', terraform.call_args.args)
        self.assertNotIn('destroy', terraform.call_args.args)
        self.assertEqual(result['gateway_url'], 'https://gateway.example')
    def test_plan_checks_then_saves_private_plan(self):
        variables = self.root / 'vars.json'
        variables.write_text('{}')
        saved = self.root / 'plan' / 'deployment.tfplan'
        calls = []
        def terraform(directory, *args, **kwargs):
            calls.append(args)
            if args[0] == 'plan':
                saved.write_text('private-plan')
        with patch.object(azure, '_terraform', side_effect=terraform):
            result = azure.plan(var_file=variables, plan_file=saved)
        self.assertEqual([args[0] for args in calls], ['init', 'fmt', 'validate', 'plan'])
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertFalse(result['deployed'])
        self.assertTrue(any(item == '-out=' + str(saved.resolve()) for item in calls[-1]))
    def test_plan_rejects_symlink_before_running_tools(self):
        target = self.root / 'target'
        target.write_text('{}')
        variables = self.root / 'vars.json'
        variables.symlink_to(target)
        with patch.object(azure, '_terraform') as terraform, self.assertRaises(Error):
            azure.plan(var_file=variables, plan_file=self.root / 'plan')
        terraform.assert_not_called()
    def test_only_allowlisted_nonsensitive_outputs_returned(self):
        payload = {'gateway_url': {'value': 'https://gateway.example', 'sensitive': False},
                   'master_key': {'value': 'private-secret', 'sensitive': True},
                   'database_url': {'value': 'also-private', 'sensitive': False},
                   'key_vault_id': {'value': 'secret-marked', 'sensitive': True}}
        with patch.object(azure, '_terraform', return_value=json.dumps(payload)):
            result = azure.outputs()
        self.assertEqual(result['gateway_url'], 'https://gateway.example')
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('key_vault_id', result)
    def test_readiness_and_registry_validation_does_not_infer(self):
        class API:
            url = 'https://gateway.example'
            calls = []
            def call(self, path):
                self.calls.append(path)
                if path == '/health/readiness/details':
                    return {'status': 'healthy', 'db': 'connected', 'litellm_version': '1.108.0'}
                return {'data': [{'id': 'fusion-standard'}]}
        api = API()
        with patch.object(azure, 'API', return_value=api):
            result = azure.validate(api.url, 'sk-fixture')
        self.assertEqual(api.calls, ['/health/readiness/details', '/v1/models'])
        self.assertEqual(result['model_requests'], 0)
    def test_subscription_selection_does_not_switch_account(self):
        account = {'id': '00000000-0000-4000-8000-000000000001', 'name': 'Test account', 'state': 'Enabled'}
        with patch.object(azure, '_run', return_value=json.dumps([account])) as run:
            self.assertEqual(azure.choose_subscription(lambda _: '1'), account['id'])
        self.assertEqual(run.call_args.args[0], ['az', 'account', 'list', '--output', 'json'])
    def test_configuration_without_required_fusion_never_writes(self):
        destination = self.root / 'vars.json'
        with self.assertRaises(Error):
            azure.configure('00000000-0000-4000-8000-000000000001', 'team-dev', destination=destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
