"""Independent SDK security boundaries; no real runtime or provider calls."""
import asyncio
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from config import Config
from copilot_runner import CopilotRunner
import sdk_runtime

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('qa_fake_sdk', ROOT / 'tests/integration/fake_sdk.py')
fake_sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake_sdk)


@pytest.fixture
def security_config(tmp_path):
    return Config(auth_mode='single_user', subject='test-owner', github_token='github_pat_security_fixture',
                  mcp_token='service-auth-fixture-' + 'x' * 32, workspace_root=tmp_path / 'runtime',
                  copilot_bin=sys.executable, timeout_seconds=1, max_output_bytes=1024)


async def finish(runner):
    await asyncio.wait_for(runner.close(), timeout=5)


async def test_default_disabled_never_constructs_runtime(security_config):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(replace(security_config, auth_mode='disabled'), client_factory=factory)
    await runner.probe()
    assert (await runner.run('standard', 'hello')).status == 'disabled'
    assert not factory.clients
    assert not security_config.workspace_root.exists()
    await finish(runner)


def test_real_factory_disables_ambient_account_and_coding_agent(security_config, monkeypatch):
    construction = {}

    def spawned(**options):
        construction['spawn'] = options
        return object()

    def client(**options):
        construction['client'] = options
        return object()

    monkeypatch.setattr(sdk_runtime, 'version', lambda distribution: sdk_runtime.SDK_VERSION)
    monkeypatch.setitem(sys.modules, 'copilot', SimpleNamespace(CopilotClient=client,
                       RuntimeConnection=SimpleNamespace(for_spawned_stdio=spawned)))
    sdk_runtime.create_sdk_client(runtime_path=sys.executable, environment={'HOME': '/tmp/isolated'},
                                  working_directory='/tmp/isolated/work', base_directory='/tmp/isolated/config')
    assert construction['client']['mode'] == 'empty'
    assert construction['client']['github']['use_logged_in_user'] is False
    assert construction['client']['observability']['log_level'] == 'none'
    assert construction['spawn']['env'] == {'HOME': '/tmp/isolated'}
    assert construction['spawn']['base_directory'] == '/tmp/isolated/config'


async def test_request_workspace_failure_does_not_leave_runtime_busy(security_config, monkeypatch):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(security_config, client_factory=factory)
    await runner.probe()
    original = runner.workspace

    def failure(prefix='request-'):
        raise PermissionError('synthetic workspace failure')

    monkeypatch.setattr(runner, 'workspace', failure)
    try:
        result = await runner.run('standard', 'synthetic-example')
        assert not result.ok and result.status == 'execution_error'
        assert runner._active == 0 and runner._idle.is_set()
        monkeypatch.setattr(runner, 'workspace', original)
        assert (await runner.run('standard', 'synthetic-example')).ok
    finally:
        # Keep a failing lifecycle regression from leaving its fixture alive.
        runner._idle.set()
        await finish(runner)


async def test_startup_probe_is_local_and_environment_does_not_inherit_auth(security_config, monkeypatch):
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'COPILOT_GITHUB_TOKEN', 'COPILOT_PROVIDER_API_KEY',
                 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY', 'BASH_ENV',
                 'PYTHONPATH', 'NODE_OPTIONS'):
        monkeypatch.setenv(name, 'ambient-secret-must-not-cross-boundary')
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(security_config, client_factory=factory)
    try:
        await runner.probe()
        await runner.probe()
        assert runner.capable
        assert len(factory.clients) == 1 and factory.clients[0].started == 1
        client = factory.clients[0]
        assert not client.sessions
        assert not any(event['phase'] == 'send' for event in client.events)
        environment = client.options['environment']
        assert 'ambient-secret' not in repr(environment)
        assert security_config.github_token not in repr(environment)
        assert security_config.mcp_token not in repr(environment)
        assert environment['HOME'] != os.environ.get('HOME')
        assert Path(client.options['base_directory']).is_dir()
    finally:
        await finish(runner)
    assert not list(security_config.workspace_root.iterdir())


async def test_failed_probe_does_not_publish_credentials_or_keep_runtime(security_config, monkeypatch, caplog):
    factory = fake_sdk.FakeFactory()

    async def failed_status(client):
        raise RuntimeError(security_config.github_token + ' ' + security_config.mcp_token)

    monkeypatch.setattr(fake_sdk.FakeCopilotClient, 'get_status', failed_status)
    runner = CopilotRunner(security_config, client_factory=factory)
    await runner.probe()
    assert not runner.capable and runner.capability_status == 'runtime_check_failed'
    assert factory.clients[0].stopped == 1 and not factory.clients[0].sessions
    assert not list(security_config.workspace_root.iterdir())
    assert security_config.github_token not in caplog.text and security_config.mcp_token not in caplog.text
    await finish(runner)


async def test_requests_share_runtime_but_have_fresh_private_sessions(security_config):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(security_config, client_factory=factory)
    try:
        await runner.probe()
        results = await asyncio.gather(*(runner.run(tier, 'synthetic-example')
                                        for tier in ('standard', 'high')))
        assert all(result.ok for result in results)
        assert len(factory.clients) == 1
        client = factory.clients[0]
        assert len(client.sessions) == 2
        assert len({session.session_id for session in client.sessions}) == 2
        assert len({session.options['working_directory'] for session in client.sessions}) == 2
        for session in client.sessions:
            assert session.options['model'] == 'fusion'
            assert session.options['github']['token'] == security_config.github_token
            assert session.options['tools']['available_tools'] == []
            assert session.options['tools']['definitions'] == []
            assert session.options['enable_config_discovery'] is False
            assert session.options['hooks']['enable_repository_hooks'] is False
            assert session.options['state']['memory']['enabled'] is False
            assert session.options['skills']['enabled'] is False
            assert session.options['mcp']['servers'] == {}
            assert session.options['plugins']['directories'] == []
            assert session.options['git']['enable_host_git_operations'] is False
            assert session.disconnected and session.session_id in client.deleted
            assert not Path(session.options['working_directory']).exists()
        assert {session.tier for session in client.sessions} == {'standard', 'high'}
    finally:
        await finish(runner)


async def test_secret_redaction_and_provider_error_recover_without_payload_logging(security_config, caplog):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(security_config, client_factory=factory)
    try:
        await runner.probe()
        result = await runner.run('standard', 'synthetic-redaction')
        assert result.ok and result.output == '[REDACTED]'
        failed = await runner.run('high', 'synthetic-provider-failure')
        assert not failed.ok and failed.status == 'provider_error' and not failed.output
        assert (await runner.run('standard', 'synthetic-example')).ok
        assert security_config.github_token not in caplog.text
        assert security_config.mcp_token not in caplog.text
        assert 'synthetic-provider-failure' not in caplog.text
    finally:
        await finish(runner)


async def test_cancel_aborts_deletes_and_releases_capacity(security_config):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(replace(security_config, max_concurrency=1), client_factory=factory)
    await runner.probe()
    task = asyncio.create_task(runner.run('standard', 'synthetic-cancel'))
    try:
        for _ in range(100):
            if factory.clients[0].sessions and factory.clients[0].sessions[0].producer:
                break
            await asyncio.sleep(.005)
        session = factory.clients[0].sessions[0]
        assert (await runner.run('high', 'synthetic-example')).status == 'busy'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert session.aborted and session.disconnected
        assert session.session_id in factory.clients[0].deleted
        assert not Path(session.options['working_directory']).exists()
        assert (await runner.run('max', 'synthetic-example')).ok
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await finish(runner)


async def test_repeated_cancellation_cannot_interrupt_session_cleanup(security_config, monkeypatch):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(replace(security_config, max_concurrency=1), client_factory=factory)
    entered, release = asyncio.Event(), asyncio.Event()
    original = fake_sdk.FakeSession.disconnect

    async def delayed_disconnect(session):
        entered.set()
        await release.wait()
        await original(session)

    monkeypatch.setattr(fake_sdk.FakeSession, 'disconnect', delayed_disconnect)
    await runner.probe()
    task = asyncio.create_task(runner.run('standard', 'synthetic-example'))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        session = factory.clients[0].sessions[0]
        assert session.disconnected and session.session_id in factory.clients[0].deleted
        assert not Path(session.options['working_directory']).exists()
        assert (await runner.run('high', 'synthetic-example')).ok
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await finish(runner)


@pytest.mark.parametrize('prompt,status', [('synthetic-timeout', 'timeout'),
                                          ('synthetic-overflow', 'output_limit'),
                                          ('synthetic-tools', 'tool_denied')])
async def test_failed_turn_is_bounded_and_next_turn_can_run(security_config, prompt, status):
    factory = fake_sdk.FakeFactory()
    runner = CopilotRunner(security_config, client_factory=factory)
    try:
        await runner.probe()
        result = await asyncio.wait_for(runner.run('standard', prompt), timeout=3)
        assert not result.ok and result.status == status
        session = factory.clients[0].sessions[0]
        assert session.aborted and session.disconnected and session.session_id in factory.clients[0].deleted
        assert (await runner.run('standard', 'synthetic-example')).ok
    finally:
        await finish(runner)
