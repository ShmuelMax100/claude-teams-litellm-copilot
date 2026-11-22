import asyncio
from dataclasses import replace
from pathlib import Path
import pytest
from config import Config
from copilot_runner import CopilotRunner


@pytest.mark.parametrize('tier', ['standard', 'high', 'max'])
async def test_native_tiers_fresh_sessions_and_warm_runtime(config, factory, tier):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    await runner.probe()
    assert runner.capable
    assert len(factory.clients) == 1
    client = factory.clients[0]
    assert client.starts == 1 and client.sessions == [] and client.prompts == []
    for _ in range(2):
        result = await runner.run(tier, 'hello')
        assert result.ok and result.output == 'mock answer' and result.exit_code is None
    a, b = client.sessions
    assert a.session_id != b.session_id
    assert a.options['working_directory'] != b.options['working_directory']
    for session in client.sessions:
        options = session.options
        assert options['github']['capi'] == {'fusion_tier': tier}
        assert options['model'] == 'fusion' and options['tools']['available_tools'] == []
        assert not options['enable_config_discovery'] and not options['skills']['enabled']
        assert not options['state']['memory']['enabled']
        assert session.disconnected and session.session_id in client.deleted
        assert not Path(options['working_directory']).exists()
    await runner.close()
    assert not list(config.workspace_root.iterdir())


@pytest.mark.parametrize('prompt', ['', ' ', 'bad\x00prompt', 'read @/etc/passwd', 'x' * 32001,
    ' /delegate do work', '& do work', '!id', '--allow-all'])
async def test_invalid_prompt(config, factory, prompt):
    runner = CopilotRunner(config, client_factory=factory)
    assert (await runner.run('standard', prompt)).status == 'invalid_input'
    assert not config.workspace_root.exists()


def test_configuration_fails_closed(config):
    from config import UserCredential
    with pytest.raises(ValueError):
        Config.from_env({})
    for changes in ({'credentials': {}}, {'service_token': 'short'},
                    {'identity_key': config.service_token}, {'allowed_tiers': ('bogus',)},
                    {'timeout_seconds': 211},
                    {'credentials': {'owner-user-id': UserCredential('owner-user-id', 'ghp_classic', 123)}}):
        with pytest.raises(ValueError): replace(config, **changes)
    with pytest.raises(ValueError, match='Obsolete'):
        Config.from_env({'FUSION_COPILOT_BIN': 'copilot'})
    assert config.credentials['owner-user-id'].token not in repr(config)
    assert config.service_token not in repr(config) and config.identity_key not in repr(config)


async def test_limits_redaction_and_tool_denial(config, factory, caplog):
    runner = CopilotRunner(replace(config, timeout_seconds=1, max_output_bytes=1024), client_factory=factory)
    await runner.probe()
    for prompt, status in [('sleep', 'timeout'), ('fail', 'provider_error'),
                           ('overflow', 'output_limit'), ('tools', 'tool_denied')]:
        result = await asyncio.wait_for(runner.run('standard', prompt), 3)
        assert result.status == status and not result.output
        assert factory.clients[0].sessions[-1].aborted
    assert (await runner.run('standard', 'secrets')).output == '[REDACTED]'
    assert config.credentials["owner-user-id"].token not in caplog.text
    await runner.close()


async def test_concurrency_isolation_and_environment(config, factory, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'parent-secret')
    monkeypatch.setenv('COPILOT_PROVIDER_API_KEY', 'parent-bypass')
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    a, b = await asyncio.gather(runner.run('standard', 'slow'), runner.run('max', 'slow'))
    assert a.ok and b.ok
    environment = factory.clients[0].options['environment']
    assert not any(k in environment for k in ('GITHUB_TOKEN', 'COPILOT_PROVIDER_API_KEY', 'FUSION_MCP_TOKEN'))
    assert config.credentials["owner-user-id"].token not in str(environment)
    await runner.close()


async def test_busy_cancel_and_next_request(config, factory):
    runner = CopilotRunner(replace(config, max_concurrency=1), client_factory=factory)
    await runner.probe()
    task = asyncio.create_task(runner.run('standard', 'sleep'))
    while not factory.clients[0].sessions:
        await asyncio.sleep(.01)
    assert (await runner.run('high', 'hello')).status == 'busy'
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert factory.clients[0].sessions[0].aborted
    assert (await runner.run('standard', 'hello')).ok
    await runner.close()


async def test_policy_and_capability_failure(config, factory):
    runner = CopilotRunner(replace(config, allowed_tiers=('standard',)), client_factory=factory)
    assert (await runner.run('max', 'hello')).status == 'access_denied'
    assert (await runner.run('standard', 'hello')).status == 'not_checked'
    def unsupported(**kwargs):
        client = factory(**kwargs)
        client.version = '0.0.1'
        return client
    runner = CopilotRunner(config, client_factory=unsupported)
    await runner.probe()
    assert runner.capability_status == 'unsupported_runtime' and not runner.capable
    assert not list(config.workspace_root.iterdir())


async def test_missing_identity_never_starts(config, factory):
    from auth import current_credential
    reset = current_credential.set(None)
    try:
        runner = CopilotRunner(config, client_factory=factory)
        assert (await runner.run('standard', 'hello')).status == 'unauthorized'
        assert not factory.clients and not config.workspace_root.exists()
    finally:
        current_credential.reset(reset)


async def test_dead_runtime_closes_admission(config, factory):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def broken():
        raise ConnectionError('synthetic dead runtime')
    factory.clients[0].ping = broken
    assert not await runner.check_health()
    assert (await runner.run('standard', 'hello')).status == 'runtime_unavailable'
    assert not factory.clients[0].sessions
    await runner.close()
