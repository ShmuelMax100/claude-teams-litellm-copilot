# Copilot SDK integration contract

## Accepted direction

Claude Team remains the existing primary route. The requested backup is native
GitHub Copilot Fusion, using the user's Copilot entitlement. Fusion does not
transfer its Claude calls to a Claude Team subscription. Model selection remains
with GitHub; neither a particular Claude model nor MAI is guaranteed.

The Fusion service moves from per-request CLI execution to a persistent Copilot
SDK runtime inside its existing Azure Container App. LiteLLM remains the gateway.
No infrastructure or paid model calls are authorized by this implementation.

## SDK service contract

- Preserve the `/mcp` Streamable HTTP endpoint and `fusion_standard`,
  `fusion_high`, and `fusion_max` tools with a single `prompt` argument.
- Preserve response fields `ok`, `request_id`, `tier`, `status`, `output`,
  `duration_ms`, and the nullable legacy `exit_code` field.
- Pin and inspect the published `microsoft-copilot-sdk` package and its matching
  runtime. Package verified runtime assets during the image build; never download
  executable assets in response to a user request. The image source-builds the
  checksum-pinned official SDK release and records provenance when the published
  wheel CDN is unavailable; byte equivalence to that wheel is not claimed.
- Start one SDK-managed runtime per service process. Use an isolated child
  process over stdio, a minimal environment, empty agent mode, explicit GitHub
  credentials, and no logged-in-user discovery.
- Allocate a fresh session and private workspace for every invocation. Do not
  reuse conversation history, grant server-side filesystem/shell tools, discover
  local instructions, or enable persistent memory.
- Apply input/output/concurrency limits and bounded cancellation. Abort and
  delete session state before releasing its workspace. Failed cleanup must not
  silently leave a reusable, potentially contaminated runtime.
- Schema validation runs in bounded, killable processes with no inherited
  credentials so expensive caller schemas cannot block service health.
- Fatal local runtime loss/cleanup failure signals failed liveness for container
  restart; ordinary provider errors do not.
- Readiness verifies local runtime startup/protocol only. It does not prove
  account entitlement, successful Fusion inference, or a model billing path.
- Preserve disabled-by-default and explicit single-owner execution. A persistent
  runtime is not authorization to share one account across developers.
- For enabled Azure Fusion deployments, keep one warm replica by default, with
  explicit scale-to-zero available. Warm capacity incurs idle cost. Keep the
  synchronous execution deadline below the ACA ingress request deadline.

## Automatic fallback boundary

An MCP tool is not an Anthropic Messages model endpoint. Claude Code's model
requests include role history, tool definitions/results, streaming and other
content. A primary model that cannot answer cannot initiate another MCP call.

The inspected SDK supports external tool requests and session continuation but
does not expose a full Claude transcript import through its public session
message interface. Passing serialized conversation history as prompt context is
a separate compatibility choice, not transparent protocol preservation.

The user explicitly accepted an automatic fallback that transfers the history as
context to a new Fusion task, with tool execution remaining in Claude Code.
The private `/v1/chat/completions` adapter exposes this behavior to LiteLLM's
OpenAI-compatible `custom_openai/fusion-standard`, `custom_openai/fusion-high`
and `custom_openai/fusion-max` routes; it is separate from the MCP endpoint.
This verified provider path uses Chat Completions without changing global
OpenAI/Azure routing behavior. System
instructions remain separate from JSON-serialized conversation data. Historical
tool calls require their real results; the adapter does not invent missing
results, execute old tools, or import a native session transcript.

Tools are declared to the SDK without handlers. The first response's tool calls
are validated, the SDK session is aborted/deleted, and the calls are returned to
Claude Code. The next request carries those actual tool results into a fresh
Fusion task. Unsupported attachments or generation controls fail explicitly.
Streaming responses are buffered until the task succeeds, then encoded as SSE;
this avoids returning a successful partial answer followed by a fallback replay.

The SDK has no verified aggregate generation-token budget control. The adapter's
`max_tokens` handling conservatively bounds returned UTF-8 content bytes and may
truncate earlier than a model-specific tokenizer would. It does not limit the
upstream computation or Copilot billing. Whole tool calls are rejected if they
exceed that returned-content budget. Provider usage is included only when the
SDK reports it; absent usage is not represented as a measured zero.

The opt-in LiteLLM guard initializes state from the authenticated key, records
eligible primary failures, and checks that state before a Fusion deployment is
called. Only specified transient failures before output qualify. Direct calls
to backup aliases, other users, authentication/input/policy failures, second
fallback attempts, and replay after output are rejected. Incoming Claude OAuth
and caller-controlled provider headers must never reach the SDK endpoint.
New owner-bound enrollment selects this policy; existing keys and default
onboarding remain unchanged.

## Ownership and dependencies

| Owner | Files | Dependencies |
|---|---|---|
| Fusion agent | SDK runtime, configuration, server assembly, Docker and SDK tests under `services/fusion-mcp/**` | Published SDK/runtime contract |
| Azure agent | `infra/terraform/**` | Service ports, readiness and timeout contract |
| Security/QA agent | `tests/integration/**`, independent SDK/inference security tests, `.github/workflows/checks.yml` | Injectable SDK client boundary; no production mock switch |
| LiteLLM agent | `services/litellm-fusion-guard/**`, `_engine/fusion_fallback.py`, optional dispatch and gateway tests | Owner-bound inference handoff and fallback eligibility |
| Lead | `services/fusion-mcp/src/inference.py`, its functional tests, architecture, public documentation, integration and PR | Review all contributions |

Files have one active editor. Independent reviews follow implementation. Tests
use synthetic providers with network egress blocked; actual runtime startup is
verified separately from mocked inference. Docker AMD64 is the Azure target;
ARM64 verifies the local development image. Existing baseline: 84 repository
tests and 26 CLI-era service tests passed before this migration.

## Sources

- [Released SDK architecture](https://github.com/microsoft/copilot-sdk/tree/v2.0.1)
- [Fusion tier configuration](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/docs/features/session-persistence.md#copilot-fusion-names)
- [SDK provider configuration](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/docs/auth/byok.md)
- [SDK session API](https://github.com/microsoft/copilot-sdk/blob/v2.0.1/python/copilot/_generated/rpc.py)
- [Fusion selection and billing](https://docs.github.com/en/copilot/concepts/models/copilot-fusion)
- [Azure ingress limits](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview)
