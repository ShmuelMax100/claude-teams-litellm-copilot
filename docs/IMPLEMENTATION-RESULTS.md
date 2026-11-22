# Fusion-only implementation results

The implementation replaces the previous optional Fusion design with one required route: Claude Code → ACA, containing LiteLLM and Fusion. It removes the Claude subscription route, provider connections, optional enrollment and automatic failover mechanisms. Old invitations are rejected.

The service uses the pinned Copilot SDK directly, a private Chat Completions model adapter, per-user signed identity delegation, distinct server-held PATs, fresh sessions, incremental text streaming and validated client-tool handoff. MCP remains a distinct private endpoint; no shared-user public registration is shipped.

Terraform always deploys both applications in one environment. It supplies distinct user secrets, generates a separate identity-signing secret, scopes access per secret, disables dynamic provider models and retains warm capacity, health checks and bounded timeouts. Documentation, diagrams, setup commands and the standalone guide describe only this architecture.

Validation completed on 22 November 2026:

| Check | Result |
| --- | --- |
| Complete onboarding, gateway and package unit suite | 38 passed |
| Complete Fusion service suite | 118 passed |
| Native Terraform mocked plans | 21 passed; formatting and validation passed |
| Actual pinned LiteLLM wire probe | 11 cases passed, including deliberately configured obsolete failover policies |
| Actual LiteLLM + PostgreSQL + production service with synthetic SDK | 19 groups passed |
| Production Docker builds | Linux ARM64 and AMD64 passed |
| Actual SDK/runtime offline protocol and tool probes | ARM64 and AMD64 passed |
| Installed Claude Code 2.1.296, temporary profile and loopback fixture | First Fusion request and streamed reply passed with 20 default tool declarations |
| Generated guide, package/public-content checks and workflow lint | Passed |
| Independent security, Azure and functional reviews | Approved after corrections |

The full integration run verifies text and all tiers, real early text deltas, client disconnect cleanup, complete and streamed tool calls, two-user credential isolation, local PAT expiry, actual gateway-key expiry, service/auth/timeout errors, explicit midstream errors and recovery without another provider. Client identity, routing and retry controls cannot change the selected route. LiteLLM safely discards its client-supplied mock controls; tests require genuine fixture output and exactly one SDK invocation rather than assuming that a 200 response means a bypass.

Corrections from integration/review include disabling shared-route cooldowns so one user's expired token cannot interrupt others; matching Terraform's user-ID validation to the service; rejecting prefix-overlapping credentials; making doctor detect incompatible Claude Code settings; and avoiding deletion of an uncreated session when credentials expire during the health probe. Root service tests used the available host Python environment; the production containers and CI use Python 3.11. See VALIDATION.md for commands and verification limits.

Remaining activation requirements: an accepted Azure region, a published immutable Fusion image, separate user PATs with real expiry and Fusion access, a reviewed Terraform plan/apply, matching gateway users and personal invitations. No live Fusion credential was available for this revision. No cloud resources or this computer's Claude Code settings were changed by the code revision.

Compatibility limits: Anthropic does not support non-Claude models behind Claude Code; the stable SDK does not import native Claude history; advanced/multimodal options outside the adapter subset fail explicitly; returned-output caps are not provider billing limits. These limits are documented rather than hidden by another provider route.
