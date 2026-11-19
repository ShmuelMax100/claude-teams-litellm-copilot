# Azure and optional Fusion implementation results

19 November 2026 · feature branch `codex/azure-fusion-mcp`

## Delivered architecture

The existing LiteLLM inference gateway, personal provider authentication and
fallback policies remain the default. Optional Terraform provisions the pinned
official LiteLLM 1.108.0 image on Azure Container Apps with private PostgreSQL,
Key Vault, managed identities, HTTPS, monitoring and configurable scaling.
Existing PostgreSQL and private-only gateway modes are supported.

Fusion is a separate authenticated Streamable HTTP service with three
prompt-only tools. It is disabled by default and supports only an explicitly
declared single-owner POC. Dedicated MCP-only keys preserve inference-key
permissions. The client installer preserves other settings, backs up private
state, supports repeat installation and removes only its own entry.

**External blocker:** checksum-verified Copilot CLI 1.0.114 lacks the documented
`--fusion-tier` flag on both tested Linux architectures. Production execution
therefore fails closed as `unsupported_cli`, with readiness 503. The service
does not substitute a model or use undocumented flags. Mock-backed tests verify
the command mapping and transport; live Fusion is not claimed to work.

## Verification evidence

| Check | Result |
|---|---|
| Existing baseline before changes | 44 unit tests passed |
| Final repository unit suite | 84 passed, including existing tests |
| Fusion service suite | 23 passed |
| Real gateway with synthetic CLI/providers | 10 integration groups passed, including persistence across gateway restart |
| Terraform | Format, initialization, validation and 8 mocked plan scenarios passed |
| Docker images | Linux ARM64 and AMD64 builds passed; non-root runtime |
| Actual pinned CLI capability check | `unsupported_cli` verified with network disabled |
| Guide and source distribution | Rebuild, Python/JSON/shell/JavaScript checks and public-content scan passed |
| CI definition | Actionlint passed; offline, service, Terraform and Docker integration jobs added |
| Independent reviews | Security/identity, Azure/cost and functional/regression reviews approved after fixes |

The mock-backed integration uses the actual LiteLLM image and PostgreSQL with
the actual MCP service, replacing only the CLI executable with a synthetic
fixture. Provider egress is blocked. It checks native registration/enrollment,
MCP handshake/discovery, unauthorized keys, three tiers, child environment
isolation, error/redaction/recovery, settings preservation, database model APIs
and existing fallback policy. Temporary containers and credentials are removed.
Restart testing retains PostgreSQL and verifies model routes, MCP registration
and dedicated-key grants without re-enrollment. The test-only Fusion companion
reattaches to the gateway's recreated Docker network namespace after restart.
No existing live pilot state or normal Claude profile is used.

Security review fixes include rejecting additive MCP access-group/toolset/tool
grants on dedicated keys, rejecting malformed keys before HTTP diagnostics,
strict tool argument/error contracts and aligned client/gateway execution
timeouts. Legacy `admin.py`, `developer.py`, `copilot.py`, model catalog and the
`setup` shell entry point remain byte-for-byte unchanged. The shared API helper
only adds safe rejection of malformed credential headers.

## Remaining operator steps

1. Review subscription/region, budgets, ownership tags and encrypted remote
   Terraform state. Follow [Azure deployment](AZURE-DEPLOYMENT.md); review the
   saved plan before any apply. No Azure deployment was performed here.
2. Verify gateway readiness and database networking/migrations, retrieve the
   master key privately from Key Vault, then use the existing `./setup admin`
   and developer flow. Confirm provider entitlement and personal OAuth yourself.
3. Leave Fusion disabled until a reviewed CLI build actually supports the
   documented tier flag and all restrictive controls. Update its pin/checksums,
   rerun tests, publish an approved AMD64 image and use its immutable digest.
4. For the single-user POC, verify the user's fine-grained PAT ownership and
   Copilot Requests permission. Enable CLI/model access through GitHub Enterprise
   policies; do not assume LiteLLM OAuth can be reused.
5. Store the PAT and independent MCP bearer securely, configure the same single
   LiteLLM owner everywhere, preview/apply registration and issue a separate
   MCP invitation. See [Fusion setup](FUSION-MCP.md).
6. Verify negative access tests and discovery before separately approved live
   Standard/Max prompts. Those calls can consume usage and were not run here.

## Production limitations and hardening

Multi-user Fusion delegation is not implemented. A static service bearer is not
end-user identity; account mapping is administrator-attested. Audit all grants
before attaching to a shared gateway. Future multi-user support needs verified
per-user delegation and concurrent two-account isolation tests.

Live Azure regional availability, role propagation, private DNS, image pulls,
backup recovery and migrations remain operator validation. The guide documents
safe re-plan/retry for initial Key Vault RBAC propagation. Terraform state holds
generated secrets. The ACA Secret volume exposes app secrets to the same trusted
LiteLLM container. Key Vault retains its RBAC-protected public endpoint.

Configure production backup/restore drills, availability targets, private
endpoints/network restrictions as required, log retention, cost alerts, image
vulnerability review and controlled upgrades. ACA HTTP ingress has a 240-second
deadline; keep service/gateway timeouts below it. No fixed cloud-cost estimate
or exact Fusion per-model execution/cost claim is made.

Detailed contracts, directory ownership and sources are in
[architecture](ARCHITECTURE.md), [security](SECURITY.md) and
[validation](VALIDATION.md). Deployment, administrator, developer and
troubleshooting guides are linked from the README and updated standalone site.

## Files created and modified

| Change | File |
|---|---|
| Modified | [`.github/workflows/checks.yml`](../.github/workflows/checks.yml) |
| Modified | [`.gitignore`](../.gitignore) |
| Modified | [`README.md`](../README.md) |
| Modified | [`START-HERE.html`](../START-HERE.html) |
| Created | [`_engine/azure.py`](../_engine/azure.py) |
| Modified | [`_engine/cli.py`](../_engine/cli.py) |
| Created | [`_engine/fusion.py`](../_engine/fusion.py) |
| Modified | [`_engine/gateway.py`](../_engine/gateway.py) |
| Created | [`docs/ADMINISTRATOR.md`](../docs/ADMINISTRATOR.md) |
| Created | [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) |
| Created | [`docs/AZURE-DEPLOYMENT.md`](../docs/AZURE-DEPLOYMENT.md) |
| Modified | [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) |
| Created | [`docs/DEVELOPER.md`](../docs/DEVELOPER.md) |
| Created | [`docs/FUSION-MCP.md`](../docs/FUSION-MCP.md) |
| Created | [`docs/IMPLEMENTATION-RESULTS.md`](../docs/IMPLEMENTATION-RESULTS.md) |
| Created | [`docs/SECURITY.md`](../docs/SECURITY.md) |
| Created | [`docs/TROUBLESHOOTING.md`](../docs/TROUBLESHOOTING.md) |
| Modified | [`docs/VALIDATION.md`](../docs/VALIDATION.md) |
| Created | [`infra/terraform/.terraform.lock.hcl`](../infra/terraform/.terraform.lock.hcl) |
| Created | [`infra/terraform/environments/dev/terraform.tfvars.example`](../infra/terraform/environments/dev/terraform.tfvars.example) |
| Created | [`infra/terraform/main.tf`](../infra/terraform/main.tf) |
| Created | [`infra/terraform/modules/container-apps/main.tf`](../infra/terraform/modules/container-apps/main.tf) |
| Created | [`infra/terraform/modules/container-apps/variables.tf`](../infra/terraform/modules/container-apps/variables.tf) |
| Created | [`infra/terraform/modules/container-apps/versions.tf`](../infra/terraform/modules/container-apps/versions.tf) |
| Created | [`infra/terraform/modules/key-vault/main.tf`](../infra/terraform/modules/key-vault/main.tf) |
| Created | [`infra/terraform/modules/key-vault/variables.tf`](../infra/terraform/modules/key-vault/variables.tf) |
| Created | [`infra/terraform/modules/key-vault/versions.tf`](../infra/terraform/modules/key-vault/versions.tf) |
| Created | [`infra/terraform/modules/monitoring/main.tf`](../infra/terraform/modules/monitoring/main.tf) |
| Created | [`infra/terraform/modules/monitoring/variables.tf`](../infra/terraform/modules/monitoring/variables.tf) |
| Created | [`infra/terraform/modules/monitoring/versions.tf`](../infra/terraform/modules/monitoring/versions.tf) |
| Created | [`infra/terraform/modules/postgresql/main.tf`](../infra/terraform/modules/postgresql/main.tf) |
| Created | [`infra/terraform/modules/postgresql/variables.tf`](../infra/terraform/modules/postgresql/variables.tf) |
| Created | [`infra/terraform/modules/postgresql/versions.tf`](../infra/terraform/modules/postgresql/versions.tf) |
| Created | [`infra/terraform/outputs.tf`](../infra/terraform/outputs.tf) |
| Created | [`infra/terraform/providers.tf`](../infra/terraform/providers.tf) |
| Created | [`infra/terraform/terraform.tfvars.example`](../infra/terraform/terraform.tfvars.example) |
| Created | [`infra/terraform/tests/deployment.tftest.hcl`](../infra/terraform/tests/deployment.tftest.hcl) |
| Created | [`infra/terraform/variables.tf`](../infra/terraform/variables.tf) |
| Created | [`infra/terraform/versions.tf`](../infra/terraform/versions.tf) |
| Created | [`services/fusion-mcp/.dockerignore`](../services/fusion-mcp/.dockerignore) |
| Created | [`services/fusion-mcp/Dockerfile`](../services/fusion-mcp/Dockerfile) |
| Created | [`services/fusion-mcp/pyproject.toml`](../services/fusion-mcp/pyproject.toml) |
| Created | [`services/fusion-mcp/requirements-dev.lock`](../services/fusion-mcp/requirements-dev.lock) |
| Created | [`services/fusion-mcp/requirements.lock`](../services/fusion-mcp/requirements.lock) |
| Created | [`services/fusion-mcp/src/auth.py`](../services/fusion-mcp/src/auth.py) |
| Created | [`services/fusion-mcp/src/config.py`](../services/fusion-mcp/src/config.py) |
| Created | [`services/fusion-mcp/src/copilot_runner.py`](../services/fusion-mcp/src/copilot_runner.py) |
| Created | [`services/fusion-mcp/src/schemas.py`](../services/fusion-mcp/src/schemas.py) |
| Created | [`services/fusion-mcp/src/server.py`](../services/fusion-mcp/src/server.py) |
| Created | [`services/fusion-mcp/tests/conftest.py`](../services/fusion-mcp/tests/conftest.py) |
| Created | [`services/fusion-mcp/tests/test_runner.py`](../services/fusion-mcp/tests/test_runner.py) |
| Created | [`services/fusion-mcp/tests/test_server.py`](../services/fusion-mcp/tests/test_server.py) |
| Created | [`tests/integration/compose.yaml`](../tests/integration/compose.yaml) |
| Created | [`tests/integration/ingress.py`](../tests/integration/ingress.py) |
| Created | [`tests/integration/mock_copilot.py`](../tests/integration/mock_copilot.py) |
| Created | [`tests/integration/run.py`](../tests/integration/run.py) |
| Created | [`tests/test_azure_onboarding.py`](../tests/test_azure_onboarding.py) |
| Created | [`tests/test_fusion_onboarding.py`](../tests/test_fusion_onboarding.py) |
| Created | [`tests/test_security_contracts.py`](../tests/test_security_contracts.py) |
| Modified | [`tools/build_guide.py`](../tools/build_guide.py) |
| Modified | [`tools/check_package.py`](../tools/check_package.py) |
| Modified | [`tools/check_public.py`](../tools/check_public.py) |
