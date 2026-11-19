# Optional Copilot Fusion MCP

Fusion adds MCP tools alongside the existing LiteLLM model gateway. It does not
change Claude subscription routing, personal Copilot fallback or copilot-only
mode. No Fusion configuration or access is added by default.

## Current support boundary

**Live Fusion is blocked by the available CLI.** GitHub documents
`copilot --model fusion --fusion-tier standard|high|max -p ...`, but the official
checksum-verified **Copilot CLI 1.0.114** inspected on 19 November 2026 does not
advertise `--fusion-tier`. The service pins that reviewed release and checks
required capabilities locally. It returns `unsupported_cli` without a model
request when support is absent; readiness returns 503. Liveness still succeeds.

The implementation includes real Streamable HTTP transport, authenticated tool
discovery, isolated execution and the documented command mapping. Automated
tests use a mock CLI. They do not establish live GitHub support, account
entitlement or a working Azure Fusion deployment. Upgrade to a reviewed binary
that passes capability checks before activating live use. Never remove the
check, enable an undocumented feature flag or silently substitute another model.

## Tools and policy

| Tool | Intended use | Documented tier |
|---|---|---|
| `fusion_standard` | Everyday coding advice with cost/latency considerations | `standard` |
| `fusion_high` | More involved reasoning and engineering questions | `high` |
| `fusion_max` | Highest permitted effort for complex tasks | `max` |

Each tool accepts only a `prompt` string. The service returns structured result
metadata and bounded text. It does not mount repositories, execute arbitrary
shell commands or permit the CLI to use coding tools. Supply only text you are
authorized to send to GitHub. File-reference syntax is restricted to prevent
CLI-side prompt expansion.

These tiers express effort, not fixed model assignments. A customer might aim
to use cost-effective supported GPT/MAI Code models for Standard and stronger
permitted models for High/Max, potentially including Claude Opus. Those are
policy goals, not guaranteed routing. The service does not promise a particular
model pair, simultaneous execution, per-model cost breakdown or an unsupported
model allowlist flag.

An enterprise administrator must enable Copilot CLI use, confirm the user's
license/usage entitlement and configure the organization's permitted models in
GitHub's supported policy controls. Available models and behavior may vary by
account, rollout and platform. Do not infer permission from successful discovery.

## Credentials and ownership

Only an explicitly declared single-user POC is supported. Shared multi-user
execution is disabled. Use the intended user's **user-owned fine-grained PAT**
with **Copilot Requests**, supplied securely through Key Vault in Azure.
Existing LiteLLM Copilot OAuth is independent and is never reused by the CLI.

Set `FUSION_SINGLE_USER_SUBJECT` to the one existing LiteLLM user ID. Verify the
GitHub PAT owner separately. The same subject must be recorded in gateway
registration and enrollment. This mapping is administrator-attested, not a
cryptographically delegated GitHub identity. Read [security](SECURITY.md) before
attaching to a gateway that already serves other users.

Keep three credentials separate:

1. The developer's dedicated LiteLLM MCP key authenticates to the gateway.
2. `FUSION_MCP_TOKEN` authenticates the gateway to the private service.
3. `FUSION_GITHUB_TOKEN` is passed only to that user's CLI subprocess as
   `COPILOT_GITHUB_TOKEN`.

Do not share any of them across developers. Never use the LiteLLM master key as
a client MCP key. Revoke MCP access separately from existing inference access.

## Service configuration

| Setting | Default / requirement |
|---|---|
| `FUSION_AUTH_MODE` | `disabled`; only `single_user` enables execution |
| `FUSION_SINGLE_USER_SUBJECT` | Required owner in enabled mode |
| `FUSION_GITHUB_TOKEN` | Required user-owned PAT in enabled mode |
| `FUSION_MCP_TOKEN` | Independent bearer, at least 32 characters |
| `FUSION_ALLOWED_TIERS` | `standard,high,max`; operator can narrow |
| `FUSION_TIMEOUT_SECONDS` | `120` |
| `FUSION_MAX_INPUT_CHARS` | `32000` |
| `FUSION_MAX_OUTPUT_BYTES` | `262144` |
| `FUSION_MAX_CONCURRENCY` | `2` |
| `FUSION_WORKSPACE_ROOT` | `/tmp/fusion` |
| `FUSION_COPILOT_BIN` | `copilot`; administrator-controlled executable |
| `FUSION_PORT` | `8080` |

The HTTP endpoints are `/mcp`, `/health/live` and `/health/ready`. Health checks
do not send inference. Authentication precedes MCP processing. Each invocation
gets a new temporary home/workspace and an allowlisted environment; timeout or
cancellation terminates the process group. Prompt, output and secrets are
excluded from audit logs. Do not enable debug/body logging to diagnose payloads.

Build the service locally:

```sh
docker build --platform linux/amd64 -t fusion-mcp:review services/fusion-mcp
```

The image runs as a non-root user. For Azure, publish an approved image and use
its immutable digest with [Terraform](AZURE-DEPLOYMENT.md). No image push or
cloud deployment is performed by the build. Do not mount a host home, repository,
Docker socket or existing GitHub login state.

The explicit `linux/amd64` target is suitable for ACA, including when building
on Apple Silicon. Local ARM64 builds are also supported for isolated testing.

## Gateway and developer setup

The optional command trees expose their complete arguments:

```sh
./setup azure --help
./setup fusion --help
```

The administrator registers the internal HTTPS `/mcp` upstream using LiteLLM's
native `/v1/mcp/server` management API, with `transport: http`,
`auth_type: bearer_token` and `allow_all_keys: false`. Keys are entered at hidden
prompts. Review the preview before applying. Existing gateway deployments must
use their established network/configuration process; registration does not
create Azure resources or rewrite the customer's configuration.

The administrator grants a new MCP-only virtual key to the configured owner.
It explicitly authorizes the server and identity-check routes, and disables
inference models. Existing invitation keys keep their permissions. The separate
private Fusion invitation must be delivered only to its intended owner.

Example operator commands, once a supported CLI image is available (replace
the example URLs and identifiers; secrets are entered at hidden prompts):

```sh
# Preview first. Repeat with --apply after reviewing the single-owner record.
./setup fusion register --gateway https://gateway.example \
  --upstream https://fusion.internal.example/mcp \
  --single-user-subject EXISTING_USER_ID --single-user-attested

# Use server_id returned by registration. Preview, then repeat with --apply.
./setup fusion enroll --gateway https://gateway.example \
  --server-id SERVER_ID --single-user-subject EXISTING_USER_ID \
  --out private/fusion.invite.json

# On the owner's workstation, using the privately delivered invitation:
./setup fusion connect --invite /secure/fusion.invite.json
./setup doctor --fusion
./setup models --fusion
./setup fusion uninstall
```

`--single-user-attested` records the administrator's responsibility to verify
the gateway/grant boundary and matching service owner. It does not prove the
GitHub account behind a token. Registration/enrollment previews do not change
the gateway, and connect never grants its own permissions.

Registration explicitly sets the upstream timeout to 150 seconds, above the
service's default 120 seconds; LiteLLM 1.108.0 otherwise defaults to 60. Use
`register --timeout-seconds` when deploying a differently bounded service.
Keep the service deadline below the gateway deadline and both below your
infrastructure's limit. ACA HTTP ingress allows 240 seconds, so the standalone
service's 1800-second maximum cannot be used through this Azure HTTP path.
Changing an existing registration's timeout is a conflict requiring explicit
administrator review; setup does not silently overwrite it.

Claude Code connects to **LiteLLM `/mcp`**, with
`x-litellm-api-key: Bearer <dedicated-key>` and `x-mcp-servers: <server-alias>`.
Server selectors narrow access; they never grant it. The service's internal
ACA URL is not a developer endpoint. `available_on_public_internet` on the
LiteLLM registration filters the client's IP, independently of upstream ingress.

Developer setup validates the invitation, owner and server grant, performs
discovery without model calls, then merges one named user-scoped MCP entry in
the Claude configuration. It preserves unrelated registrations, creates private
backups and supports idempotent setup and conflict-aware uninstall. Project and
organization-managed settings are not overwritten. A separate MCP key may be
stored in the private local client configuration; protect it like an invitation.

## Validation and rollback

Start with health/readiness and MCP initialize/list-tools. Tool discovery is
not execution, entitlement or provider-routing proof. The normal doctor/list
checks send no model request. Live Standard/Max invocation is explicitly opt-in
and may consume Copilot usage; only attempt it after CLI support and account
policy are confirmed.

Before enabling any live use, test unauthenticated requests, an inference-only
key and an ungranted MCP key. None should invoke the upstream CLI. Test timeouts,
provider failures and denied tiers independently; failures must not fall back
to the inference gateway or another user's credential.

To roll back, remove the installed local MCP entry, revoke its dedicated key,
remove/disable the server registration, and disable the optional Fusion app.
Existing model routes, developer settings and personal OAuth stay in place.
With Terraform, setting `enable_fusion_mcp=false` requires an explicit reviewed
plan/apply and must retain LiteLLM/PostgreSQL. Never use whole-project destroy to
remove only Fusion.

## Sources

- [GitHub Fusion model selection and tiers](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/choose-a-model)
- [GitHub CLI 1.0.114 release](https://github.com/github/copilot-cli/releases/tag/v1.0.114)
- [Reported CLI capability mismatch](https://github.com/github/copilot-cli/issues/5298)
- [GitHub authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
- [GitHub programmatic CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [GitHub tool controls](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools)
- [LiteLLM MCP endpoint and headers](https://docs.litellm.ai/docs/mcp_config_reference)
