# Private Fusion MCP tools

The Fusion service exposes Streamable HTTP `/mcp` with `fusion_standard`, `fusion_high` and `fusion_max`. Each tool takes a text prompt and creates an isolated Fusion SDK task using the authenticated user's server-owned credential.

MCP is a tool protocol. Claude Code model requests instead use `/v1/messages` on LiteLLM, translated to the private Fusion `/v1/chat/completions` adapter. An MCP connection alone cannot replace this model endpoint.

Every MCP request needs the independent service bearer and a fresh signed user assertion described in [the contract](FUSION-CONTRACT.md). The public gateway key and a caller-selected user header are not sufficient. Health endpoints reveal no credential values. Initialization and tool discovery send no model requests; tool invocation does.

This release keeps MCP private. The pinned LiteLLM MCP manager has different identity/header hooks for discovery and tool calls, so the previous shared registration and invitation workflow has been removed. Do not expose this endpoint publicly or register it with one shared GitHub identity. The integration suite exercises signed initialization, tool discovery and calls directly on the private test interface.

Tool results report a sanitized status and output. Unauthorized or expired credentials fail before SDK session creation where locally detectable; upstream revocation errors remain explicit. Internal GitHub Fusion orchestration stays under GitHub's control.
