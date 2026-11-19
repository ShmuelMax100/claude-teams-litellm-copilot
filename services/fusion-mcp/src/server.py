"""Native MCP Streamable HTTP service. No provider request occurs at startup."""
from contextlib import asynccontextmanager
import logging

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from auth import BearerGate
from config import Config
from copilot_runner import CopilotRunner
from schemas import FusionResult


def create_app(config: Config | None = None, runner: CopilotRunner | None = None):
    config = config or Config.from_env()
    runner = runner or CopilotRunner(config)
    async def strict_tools(ctx, call_next):
        if ctx.method == "tools/call":
            arguments = (ctx.params or {}).get("arguments")
            if (not isinstance(arguments, dict) or set(arguments) != {"prompt"}
                    or not isinstance(arguments["prompt"], str)):
                return CallToolResult(isError=True, content=[TextContent(type="text",
                    text="Invalid arguments: provide only a string prompt.")])
        result = await call_next(ctx)
        if hasattr(result, "model_dump"):
            result = result.model_dump(by_alias=True, exclude_none=True)
        if ctx.method == "tools/list" and isinstance(result, dict):
            for tool in result.get("tools", []):
                tool["inputSchema"]["additionalProperties"] = False
                tool["inputSchema"]["properties"]["prompt"].update(
                    minLength=1, maxLength=config.max_input_chars, pattern=r"^[^@\u0000]+$")
        if ctx.method == "tools/call" and isinstance(result, dict):
            if (result.get("structuredContent") or {}).get("ok") is False:
                result["isError"] = True
        return result

    mcp = MCPServer("GitHub Copilot Fusion", version="0.1.0", log_level="CRITICAL",
                    middleware=[strict_tools])

    @mcp.tool(description="Everyday coding advice balancing cost, quality and latency. Single-user Fusion Standard; prompt text only, no file access.")
    async def fusion_standard(prompt: str) -> FusionResult:
        return await runner.run("standard", prompt)

    @mcp.tool(description="Complex, well-scoped development advice using Fusion High. May consume more usage; prompt text only, no file access.")
    async def fusion_high(prompt: str) -> FusionResult:
        return await runner.run("high", prompt)

    @mcp.tool(description="Demanding engineering analysis using Fusion Max. Highest permitted capability and potentially more usage; subject to plan and model policy.")
    async def fusion_max(prompt: str) -> FusionResult:
        return await runner.run("max", prompt)

    app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
        json_response=True, host="0.0.0.0", max_request_body_size=config.max_input_chars * 4 + 8192)
    sdk_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        await runner.probe()
        async with sdk_lifespan(app):
            yield

    async def live(request):
        return JSONResponse({"status": "alive"})

    async def ready(request):
        return JSONResponse({"status": runner.capability_status,
            "mode": config.auth_mode, "cli_capable": runner.capable},
            status_code=200 if runner.capable else 503)

    app.router.lifespan_context = lifespan
    app.routes.extend([Route("/health/live", live), Route("/health/ready", ready)])
    app.add_middleware(BearerGate, config=config)
    # SDK/protocol logs can include invalid tool arguments. Retain only our
    # deliberately content-free audit logger in this service.
    logging.getLogger("mcp").setLevel(logging.CRITICAL)
    app.state.runner = runner
    return app


def main():
    config = Config.from_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(create_app(config), host="0.0.0.0", port=config.port,
                access_log=False, log_level="warning", limit_concurrency=128)


if __name__ == "__main__":
    main()
