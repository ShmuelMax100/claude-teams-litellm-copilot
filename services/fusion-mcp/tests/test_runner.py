import asyncio
from dataclasses import replace
import json
import os
import signal

import pytest

from config import Config
from copilot_runner import CopilotRunner


@pytest.mark.parametrize("tier", ["standard", "high", "max"])
async def test_documented_tiers_and_safe_command(config, tier):
    runner = CopilotRunner(config)
    await runner.probe()
    assert runner.capable
    result = await runner.run(tier, "env")
    assert result.ok
    data = json.loads(result.output)
    args = data["ARGS"]
    assert args[args.index("--fusion-tier") + 1] == tier
    assert args[args.index("--model") + 1] == "fusion"
    assert "--allow-all" not in args
    assert "--no-custom-instructions" in args
    assert "--disable-builtin-mcps" in args
    assert "--deny-tool" in args
    assert data["HOME"] != data["CWD"]
    assert data["COPILOT_GITHUB_TOKEN"] == "[REDACTED]"
    assert not list(config.workspace_root.iterdir())


@pytest.mark.parametrize("prompt", ["", " ", "bad\x00prompt", "read @/etc/passwd", "x" * 32001,
    " /delegate do work", "& do work", "!id", "--allow-all"])
async def test_invalid_prompt(config, prompt):
    runner = CopilotRunner(config)
    runner.capable = True
    assert (await runner.run("standard", prompt)).status == "invalid_input"
    assert not config.workspace_root.exists()


def test_configuration_fails_closed(config):
    assert Config.from_env({}).auth_mode == "disabled"
    for changes in ({"auth_mode": "multi_user"}, {"github_token": ""}, {"mcp_token": "short"},
                    {"github_token": "ghp_classic"}, {"subject": "bad\nowner"},
                    {"mcp_token": config.github_token}, {"allowed_tiers": ("bogus",)}):
        with pytest.raises(ValueError):
            replace(config, **changes)
    assert config.github_token not in repr(config)
    assert config.mcp_token not in repr(config)


async def test_timeout_failure_output_cap_and_redaction(config, caplog):
    runner = CopilotRunner(replace(config, timeout_seconds=1, max_output_bytes=1024))
    await runner.probe()
    assert (await runner.run("standard", "sleep")).status == "timeout"
    failed = await runner.run("high", "fail")
    assert failed.status == "provider_error" and failed.exit_code == 7 and not failed.output
    assert (await runner.run("standard", "overflow")).status == "output_limit"
    assert (await runner.run("standard", "secrets")).output == "[REDACTED]"
    assert config.github_token not in caplog.text
    assert not list(config.workspace_root.iterdir())


async def test_concurrency_isolation_and_environment(config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "parent-secret")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "parent-bypass")
    monkeypatch.setenv("BASH_ENV", "/unsafe")
    runner = CopilotRunner(config)
    await runner.probe()
    first, second = await asyncio.gather(runner.run("standard", "slow"), runner.run("max", "slow"))
    a, b = json.loads(first.output), json.loads(second.output)
    assert a["home"] != b["home"] and a["cwd"] != b["cwd"]
    data = json.loads((await runner.run("high", "env")).output)
    assert all(name not in data for name in ("GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY", "BASH_ENV", "FUSION_MCP_TOKEN"))


async def test_busy_and_cancellation_reap_process_group(config):
    runner = CopilotRunner(replace(config, max_concurrency=1))
    await runner.probe()
    task = asyncio.create_task(runner.run("standard", "child"))
    child_pid = None
    for _ in range(100):
        pids = list(config.workspace_root.glob("*/home/pid"))
        if pids:
            child_pid = int(pids[0].read_text())
            break
        await asyncio.sleep(.02)
    assert child_pid
    assert (await runner.run("high", "hello")).status == "busy"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(config.workspace_root.iterdir())
    # A killed child can briefly be an init-owned zombie; either absent or
    # zombie is evidence that it cannot keep running or retain its workspace.
    import subprocess
    check = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True)
    assert not check.stdout.strip() or check.stdout.strip().startswith("Z")
    assert (await runner.run("standard", "hello")).ok


async def test_policy_and_missing_capabilities(config):
    runner = CopilotRunner(replace(config, allowed_tiers=("standard",)))
    assert (await runner.run("max", "hello")).status == "access_denied"
    assert (await runner.run("standard", "hello")).status == "not_checked"
    missing = CopilotRunner(replace(config, copilot_bin="/not-present/copilot"))
    await missing.probe()
    assert missing.capability_status == "cli_missing"
    binary = config.copilot_bin
    from pathlib import Path
    Path(binary).write_text("#!/bin/sh\necho '--model'\n")
    await runner.probe()
    assert runner.capability_status == "unsupported_cli"
    assert (await runner.run("standard", "hello")).status == "unsupported_cli"


async def test_disabled_never_executes(config):
    runner = CopilotRunner(replace(config, auth_mode="disabled"))
    await runner.probe()
    assert (await runner.run("standard", "hello")).status == "disabled"
    assert not config.workspace_root.exists()
