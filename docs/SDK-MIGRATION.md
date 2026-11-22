# SDK interfaces and compatibility limits

The image pins `microsoft-copilot-sdk==2.0.1` and its native runtime bundle, with source/archive and runtime checksums verified during build. `sdk-provenance.json` records the source build. It does not claim byte identity with the published wheel. The service uses the SDK's documented native transport; it does not invoke the Copilot CLI's prompt command or depend on a `--fusion-tier` CLI flag.

| Boundary | Implemented contract | Limit |
| --- | --- | --- |
| Claude Code → LiteLLM | Anthropic Messages | Non-Claude upstreams are not supported by Anthropic |
| LiteLLM → Fusion service | `custom_openai` Chat Completions | Explicitly supported text/tool subset |
| Fusion service → SDK | `model="fusion"`, `github.capi.fusion_tier` | GitHub controls internal routing and access |
| Identity | Explicit per-session `github.token` in empty mode | User PAT and entitlement required |
| Streaming | SDK message deltas → bounded SSE | A post-start failure terminates the stream |
| Tools | Declaration-only tools; validated complete batch returned to client | No server-side tool execution |
| History | Serialized previous messages and real tool results | Not lossless native Claude conversation import |

Stable SDK session sending accepts user messages, not arbitrary imported Claude assistant/tool history. This implementation serializes history as task context and creates a fresh session each request. Experimental semantic import in a prerelease SDK is not relied upon. This can affect instruction semantics, context use and model quality even when protocol tests pass.

Unsupported multimodal content, forced named tool selection and unsupported generation controls produce clear errors. Advanced thinking, context-management summarization and server-executed tools are not implemented. Supported stop handling and the returned-output byte bound are local adapter behavior; no documented aggregate Fusion generation-token cap was established. Do not interpret `max_tokens` handling or the output cap as a billing limit. Usage is reported only when supplied by the SDK.

The SDK/runtime is warm to avoid per-request process startup. Network, gateway translation, credential filtering, Fusion generation and tool-batch validation still take time; latency is not zero.

Primary references checked for this revision:

- [Stable Fusion configuration](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/docs/features/session-persistence.md#copilot-fusion-names)
- [Per-user SDK server isolation](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/docs/setup/multi-tenancy.md)
- [Streaming and declaration-only tools](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/python/README.md)
- [Generated session message contract](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/python/copilot/_generated/rpc.py)
- [Pinned LiteLLM Messages conversion](https://github.com/BerriAI/litellm/blob/v1.108.0/litellm/llms/anthropic/pass_through/messages/handler.py)
- [Claude Code gateway support boundary](https://code.claude.com/docs/en/llm-gateway)

Mock inference, real native offline probes and a local synthetic provider establish interface behavior only. Live Fusion execution and each user's access require an actual user credential and a separately authorized live request.
