from dataclasses import replace
from starlette.testclient import TestClient

from server import create_app
from conftest import identity_headers


def rpc(client, config, method, params=None, ident=1):
    return client.post("/mcp", headers={**identity_headers(config),
        "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": ident,
        "method": method, "params": params or {}})


def test_native_handshake_discovery_invocation(config):
    with TestClient(create_app(config)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        init = rpc(client, config, "initialize", {"protocolVersion": "2025-11-25",
            "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        assert init.status_code == 200, init.text
        assert "serverInfo" in init.json()["result"]
        listing = rpc(client, config, "tools/list").json()["result"]["tools"]
        assert {t["name"] for t in listing} == {"fusion_standard", "fusion_high", "fusion_max"}
        for tool in listing:
            assert set(tool["inputSchema"]["properties"]) == {"prompt"}
            assert tool["inputSchema"]["additionalProperties"] is False
            call = rpc(client, config, "tools/call", {"name": tool["name"], "arguments": {"prompt": "hello"}})
            result = call.json()["result"]["structuredContent"]
            assert result["ok"] is True and result["output"] == "mock answer"


def test_unauthorized_and_origin_denied(config):
    with TestClient(create_app(config)) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"},
                        {"x-user-id": "owner-user-id"}, {"Authorization": "Bearer " + config.credentials["owner-user-id"].token}):
            assert client.post("/mcp", headers=headers).status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer " + config.service_token,
            "Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/mcp", headers=[("Authorization", "Bearer " + config.service_token),
            ("Authorization", "Bearer wrong")]).status_code == 401


def test_discovery_with_unsupported_runtime(config):
    from copilot_runner import CopilotRunner
    def missing(**kwargs):
        raise FileNotFoundError()
    runner = CopilotRunner(config, client_factory=missing)
    with TestClient(create_app(config, runner)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert len(rpc(client, config, "tools/list").json()["result"]["tools"]) == 3
        wire = rpc(client, config, "tools/call", {"name": "fusion_standard",
            "arguments": {"prompt": "hello"}}).json()["result"]
        assert wire["isError"] is True
        result = wire["structuredContent"]
        assert result["ok"] is False and result["status"] == "runtime_missing"


def test_invalid_arguments_and_tier_policy(config):
    config = replace(config, allowed_tiers=("standard",))
    with TestClient(create_app(config)) as client:
        forbidden = rpc(client, config, "tools/call", {"name": "fusion_max",
            "arguments": {"prompt": "hello"}}).json()["result"]["structuredContent"]
        assert forbidden["status"] == "access_denied"
        for args in ({}, {"prompt": ["invalid"]}, {"prompt": "hello", "command": "id"}):
            response = rpc(client, config, "tools/call", {"name": "fusion_standard", "arguments": args}).json()
            assert "error" in response or response.get("result", {}).get("isError"), response


def test_request_size(config):
    config = replace(config, max_input_chars=10)
    with TestClient(create_app(config)) as client:
        response = rpc(client, config, "tools/call", {"name": "fusion_standard", "arguments": {"prompt": "x" * 10000}})
        assert response.status_code == 413


def test_dead_runtime_requests_container_restart(config, factory):
    from copilot_runner import CopilotRunner
    runner = CopilotRunner(config, client_factory=factory)
    with TestClient(create_app(config, runner)) as client:
        async def failed_ping():
            raise ConnectionError("runtime exited")
        runner.client.ping = failed_ping
        assert client.get("/health/ready").status_code == 503
        live = client.get("/health/live")
        assert live.status_code == 503
        assert live.json()["status"] == "restart_required"
