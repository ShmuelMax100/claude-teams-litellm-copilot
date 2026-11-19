# Azure and optional SDK Fusion implementation results

19 November 2026 · feature branch `codex/azure-fusion-mcp`

## Delivered behavior

The established Claude Team subscription path remains primary. Ordinary
onboarding, existing invitations, the model catalog and personal Copilot fallback
retain their defaults. A separate owner-bound invitation opts into automatic
native Copilot Fusion fallback after eligible transient failures before output.
Fusion uses Copilot entitlement and cannot redirect its internal Claude calls to
Claude Team credits or guarantee a particular MAI/Claude model.

Optional Terraform hosts the pinned official LiteLLM 1.108.0 image on Azure
Container Apps with private PostgreSQL, Key Vault, separate managed identities,
HTTPS and monitoring. Existing-database and private-only gateway modes remain
available. Fusion and its inference adapter are disabled by default.

The private service runs Microsoft Copilot SDK 2.0.1 and matching native runtime
1.0.114. One warm runtime creates isolated sessions. MCP exposes three existing
prompt-only tools. The separate inference adapter gives a new Fusion task the
conversation history as context. Tools have no server handlers; complete validated
calls return to Claude Code for local execution after SDK session cleanup.

The LiteLLM guard allows one eligible fallback, binds it to the authenticated
owner key, denies direct backup access and replay after output, and removes
primary/provider credentials from the private hop. No global Messages conversion
setting is changed. The pinned gateway omits two policy fields on persistence;
enrollment accepts only that verified normalization and the loaded guard enforces
those limits on each request.

## Verification evidence

All checks below use synthetic credentials and isolated fixtures.

| Check | Evidence |
|---|---|
| Repository unit suite | 104 tests passed, including existing onboarding |
| Service suite | 90 passed on macOS, Linux ARM64 and Linux AMD64; includes 29 independent inference security cases |
| Terraform | Formatting, validation and 16 mocked plans passed |
| Docker | Latest Linux ARM64 and AMD64 production builds passed |
| Actual SDK/runtime | Both architectures passed offline startup and mock-provider tool contract |
| Pinned LiteLLM wire | 9 status scenarios passed; correct bearer, no synthetic caller/provider credential in headers or body |
| Full gateway/PostgreSQL | 18 groups passed, including streaming/tool handoff and fallback after restart |
| Independent reviews | Security, Azure and functional reviews found no remaining blockers after fixes |
| Distribution | Guide rebuild, package/public scans, workflow lint and whitespace checks passed |

Two independent reviews reproduced a costly JSON Schema pattern blocking the
service loop. Schema checks now run in credential-free, killable workers with
wall-time, CPU, additional-allocation and concurrency limits. The Linux allocation
budget is 256 MiB above the trusted interpreter baseline, supporting native and
emulated builds without removing the memory bound. The reproduction terminates in
about two seconds while the loop stays responsive and a subsequent validation
succeeds. Another review found that a dead SDK process left the only replica
unready indefinitely; fatal local runtime health/cleanup now fails liveness so
ACA can restart it. Ordinary provider errors do not trigger this restart signal.

The SDK wheel is built from the official v2.0.1 release commit with verified
source archive and matching runtime metadata because the wheel CDN was
inaccessible locally. The image records source-build provenance; equivalence to
the published wheel's bytes is not claimed. Native bundles and other dependencies
are checksum-pinned. No executable asset is fetched during requests.

The legacy `admin.py`, `developer.py`, `copilot.py`, model catalog and `setup`
entry point are unchanged relative to this PR's base.

## Limits and remaining operator work

- No Azure deployment, live Fusion inference, entitlement check, billing test or
  production OAuth change was performed. Publish an approved AMD64 image, review
  subscription/region/network/cost choices and an explicit Terraform plan before
  any deployment. Verify identity roles, private DNS, image pulls and readiness.
- Fusion is single-owner only. PAT ownership is administrator-attested; a static
  service bearer is not delegated end-user identity. No shared-token team mode.
- History is context for a fresh task, not native Claude session continuation.
  Attachments and unsupported controls fail explicitly. Reasoning uses the
  selected Fusion tier. Streaming is buffered before delivery.
- Returned token limits conservatively bound UTF-8 bytes, not upstream compute or
  billing. Complete tool calls must fit; usage is reported only when supplied by
  the SDK. Schema validation can add bounded latency on tool-enabled handoffs.
- A warm replica reduces cold starts and incurs idle cost. It cannot provide zero
  latency. Default primary+fallback budget is 200 seconds, with a maximum 230
  under ACA's 240-second ingress deadline.
- Single replica and default database SKU are POC availability choices. Protect
  Terraform state/plans, keep the encryption salt with database backups, rehearse
  recovery and configure budgets/retention. Key Vault retains its RBAC-protected
  public endpoint; private ACA does not make every Azure endpoint private.

Use [deployment](AZURE-DEPLOYMENT.md), [fallback setup](FUSION-FALLBACK.md),
[MCP setup](FUSION-MCP.md), [security](SECURITY.md) and [validation](VALIDATION.md)
for operator instructions. Existing live-provider evidence elsewhere in this
repository does not certify these new SDK/fallback paths.
