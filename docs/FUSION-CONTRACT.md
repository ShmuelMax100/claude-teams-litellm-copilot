# Fusion-only integration contract

This is the implementation contract for the sole supported architecture: Claude Code → Azure Container Apps, with LiteLLM and Fusion in the same ACA environment. It supersedes the former optional Fusion and Claude-primary designs.

## Routing and protocols

- LiteLLM is the only public endpoint. It accepts Anthropic Messages from Claude Code and translates them through its documented `custom_openai` Chat Completions adapter.
- Public model names are exactly `fusion-standard`, `fusion-high`, and `fusion-max`. Every deployment uses `custom_openai/fusion-{tier}` and the private Fusion `/v1/chat/completions` endpoint. Fusion creates SDK sessions with `model="fusion"` and the corresponding `github.capi.fusion_tier`.
- The gateway rejects provider, URL, credential, identity, deployment and routing overrides. Retries are zero; alternate-provider routing is absent. Existing database deployments cannot bypass the deployment guard.
- `/mcp` remains a separate private tool interface. It is not the Claude Code model endpoint and is not registered using shared-user credentials. Both interfaces require the same delegated user identity.

## Identity contract

- Only validated LiteLLM `UserAPIKeyAuth.user_id` determines the subject. Each personal key is restricted to the Fusion aliases. Client bodies, metadata and headers cannot select another identity.
- Gateway and Fusion share `FUSION_SERVICE_TOKEN` (independent of GitHub credentials) and a separate `FUSION_IDENTITY_KEY`. Both are distinct secret values of at least 32 non-whitespace characters; configured credentials must not prefix-overlap. The gateway sends `Authorization: Bearer <service token>` and `X-Fusion-Identity: <assertion>`.
- Assertion format is `v1.<payload>.<signature>`. Payload is unpadded base64url UTF-8 JSON with `v:1`, `aud:"fusion-mcp"`, exact `sub`, integer `iat` and `exp`, and nonempty random `jti`. Signature is unpadded base64url HMAC-SHA256 over the ASCII bytes `v1.<payload>`, using the UTF-8 identity key. Lifetime is at most 60 seconds; allow at most five seconds of future clock skew. Expired assertions fail closed. Reject malformed/duplicate claims and duplicate security headers. Assertions are not logged. Their nonce does not claim distributed replay protection.
- Fusion uses `FUSION_USER_CREDENTIALS_JSON`: an administrator-owned object mapping each subject to `{ "token_env": "FUSION_GITHUB_USER_<id>", "expires_at": <Unix seconds> }`. Each named environment secret contains that user's fine-grained GitHub PAT. Expiry is required; expired credentials fail before SDK session creation. Early GitHub revocation still depends on the upstream response.
- Distinct users must have distinct PAT values and distinct Key Vault secret scopes. Neither the caller nor the gateway receives a GitHub PAT. The gateway identity cannot read those secrets. No ambient GitHub login is allowed.

## Sessions, output and tools

- Each request creates a fresh UUID SDK session and private workspace, with explicit request-scoped `github.token` in an empty runtime. No caller-selected session resumption, ambient tools, local instructions or server tool execution.
- SDK text deltas are streamed through a bounded channel. Redaction must handle secrets spanning chunks. Tool batches are emitted only after complete validation and session cleanup; Claude Code executes the returned tools locally.
- Disconnect, timeout and error abort and delete session state. A stream that has begun returns a sanitized stream error on failure; it is never restarted or redirected.
- Stable SDK history import is limited to user messages. Prior assistant/tool history is serialized as context, not represented as native Claude continuation. Unsupported inputs/options fail explicitly. A returned-output byte limit is not an upstream generation or billing limit.

## Deployment and ownership

- Terraform always creates the private Fusion application and public LiteLLM application in one ACA environment. Only LiteLLM has external ingress. Warm replicas are the default; execution timeout is at most 210 seconds within ACA's 240-second HTTP window.
- Terraform inputs include required `fusion_image`, `fusion_key_vault_id`, `fusion_service_token_secret_id`, and `fusion_users` with per-user `github_token_secret_id` and `expires_at`. The deployment creates a separate signing secret and scopes identity access to individual secrets.
- Fusion agent owns `services/fusion-mcp/**`. Gateway agent owns `_engine/**`, `setup`, `services/litellm-fusion-gateway/**` (replacing the old guard directory), and onboarding/unit tests assigned by the lead. Azure agent owns `infra/terraform/**`. The lead owns documentation, diagrams, package tooling, integration tests, CI and final integration. No concurrent ownership overlap.
- This revision changes source and the feature pull request only. It does not apply Terraform, change cloud resources, or overwrite the developer's existing Claude Code settings.

## Required verification

Test first-request routing, two-user credential/session isolation, real streamed deltas, complete tool calls and results, service errors, credential/assertion expiry, invalid identity, cancellation, and the absence of any alternate route. Use the actual pinned LiteLLM translation in integration tests and the actual native SDK runtime in offline protocol probes. Live Fusion entitlement/inference requires the user's own GitHub credential and is not established by mocks.
