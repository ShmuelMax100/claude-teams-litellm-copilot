# Optional Azure deployment

Existing gateway users can continue with `./setup admin`; they do not need Azure
or Terraform. This guide creates a new gateway with the repository's audited
official LiteLLM 1.108.0 image. Fusion is optional and disabled by default.

**Current Fusion blocker:** the verified official Copilot CLI 1.0.114 lacks
`--fusion-tier`. Leave Fusion disabled until an audited compatible binary is
available. The service intentionally fails readiness/execution with that pin;
the optional infrastructure and mocked integration are still testable.

## Before deployment

Use Terraform 1.7 or newer, Azure CLI authentication, an approved subscription,
and an identity allowed to create resources and role assignments. Review the
provider and Terraform constraints in `infra/terraform/versions.tf`. Choose a
region that supports your desired Container Apps and PostgreSQL SKUs.

Decide the resource prefix, region, ownership/cost tags, database backup policy
and network access. Private-only environments require a customer VPN/peering
and DNS path before developers can reach the gateway. Default mode exposes only
the authenticated LiteLLM HTTPS endpoint; PostgreSQL and Fusion stay private.

Configure encrypted remote Terraform state with access controls and locking
using your organization's backend. A backend is deliberately not hardcoded to
a customer account. Local state is acceptable only for a protected POC: state
and plans contain generated secret values. Do not upload them as public CI
artifacts or commit them.

## Deploy LiteLLM

```sh
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit subscription_id, name_prefix, region and ownership tags.
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out=deployment.tfplan
# Review resource additions, replacements and recurring costs first.
terraform apply deployment.tfplan
terraform output -raw gateway_url
```

Alternatively, the optional helper can select an enabled Azure subscription
from `az account list` and save private variables without deploying:

```sh
./setup azure configure --name-prefix litellm-dev --owner platform-team
./setup azure plan
# Review the saved plan with the displayed terraform show command.
./setup azure apply
./setup azure outputs
```

Run these helper commands from the repository root. `apply` requires the exact
confirmation `APPLY` and uses the saved plan. `configure` also accepts explicit
`--subscription-id`, `--location` and `--out`; it does not change Azure CLI's
selected account. The resource group is derived from `name_prefix`.

These commands are operator instructions; this implementation does not run
`plan` against Azure or apply infrastructure automatically. The plan is a
sensitive file. Never use `terraform destroy` as routine onboarding or cleanup.

The root module provisions a resource group, Log Analytics, a VNet-integrated
Container Apps environment, LiteLLM, Key Vault and separate managed identities.
The managed PostgreSQL option creates a private Flexible Server, delegated
subnet and private DNS. LiteLLM receives a mounted configuration with persistent
model storage and secret-backed environment settings. The normal LiteLLM image
startup performs its supported database schema initialization/migrations;
readiness must pass before onboarding.

The native Secret volume mounts **all LiteLLM app secrets** as files alongside
the nonsecret configuration. The same trusted container also receives them as
environment variables; do not add untrusted sidecars or expose this mount.

On a fresh deployment, Key Vault RBAC can take several minutes to propagate
after Terraform creates the role assignment. The pinned provider can return 403
during this interval. Wait up to five minutes, verify the intended role and
identity, create and review a **fresh plan**, then apply again. Terraform retains
completed resources and generated secrets in its state. Do not reuse a stale
saved plan, destroy resources, broaden permissions or disable RBAC to bypass
this timing issue. If 403 persists, diagnose tenant/identity/network policy.

Use the HTTPS `gateway_url` output with `./setup admin`. Retrieve the generated
master key from the reported Key Vault using the Azure portal or your approved
secret manager, then enter it at the setup wizard's hidden prompt. Do not place
the key in a shell command, invitation, ticket or Terraform output.

```sh
cd ../..
./setup admin
```

Prepare the gateway, create a private developer invitation and complete the
existing developer flow. Database persistence is required for model registration,
keys and encrypted personal Copilot connections. For copilot-only mode, follow
[its active configuration audit](COPILOT-ONLY.md); API-only route creation still
cannot replace the explicit per-user marker required by the pinned version.

## Deployment switches

| Variable | Default | Meaning |
|---|---|---|
| `enable_managed_postgresql` | `true` | Create private PostgreSQL Flexible Server |
| `enable_private_networking` | `false` | Make the entire ACA environment internal when enabled |
| `enable_fusion_mcp` | `false` | Add the single-user Fusion app after its prerequisites are supplied |
| `subscription_id` | required | Operator-selected subscription |
| `name_prefix` | required | 3–16 lowercase characters; start with a letter, end with a letter/digit |
| `location` | `westeurope` | Region; verify subscription availability |
| `tags` | development values | Replace owner/environment with accountable customer values |

CPU, memory, replicas, HTTP concurrency, database SKU/storage/retention and log
retention are configurable. Inspect the variable descriptions and the saved
plan; development defaults do not constitute a production availability target.

`litellm_log_level` controls operational logs; prompt logging is disabled while
metadata spend/routing logs remain available. `copilot_only_models` accepts an
optional alias-to-model map for explicit file-managed per-user Copilot routes.
It is empty by default. After selecting entitled models, apply the configuration
and export/audit the active JSON configuration as required by COPILOT-ONLY.md.

```sh
terraform -chdir=infra/terraform output -raw gateway_config_json > /secure/active-config.json
```

This output contains environment references, not credential values. Use the
export from the active deployment, not a separately generated route fragment.

For an existing database, set `enable_managed_postgresql=false`,
`external_database_secret_id` to its Key Vault secret URI, and
`external_database_key_vault_id` to that vault's resource ID. Store a PostgreSQL
URL with TLS required in the secret. The operator must ensure the database
network/DNS path is reachable from ACA and allow schema migration permissions.
No database password belongs in Git or a command-line argument.

Changing the database mode, resource prefix, network topology or private-only
setting can replace resources. Treat a plan showing a database replacement as
a data migration, with backup and recovery steps completed first.

## Add optional Fusion

First read [Fusion setup and current limitations](FUSION-MCP.md) and
[identity boundaries](SECURITY.md). Prepare a reviewed container image and
publish it to your approved registry. Publishing/deployment is a separate
operator action; a local Docker build does not publish anything.

Enable Fusion only after supplying:

- `fusion_image`: immutable image reference ending in `@sha256:<digest>`.
- `fusion_single_user_subject`: the one existing LiteLLM user ID allowed to use
  the POC. It must match gateway registration/enrollment.
- `fusion_github_token_secret_id`: Key Vault URI for that user's fine-grained PAT.
- `fusion_mcp_token_secret_id`: Key Vault URI for a separate random service bearer
  of at least 32 characters.
- `fusion_key_vault_id`: resource ID of the vault containing those secrets.

Set `enable_fusion_mcp=true`, review a new plan and apply it explicitly. The
output `fusion_mcp_url` is internal to the ACA environment. Register it from the
LiteLLM administrator workflow; developer machines connect to LiteLLM `/mcp`,
not directly to the private service. For a private ACR, supply `registry_server`
and `registry_resource_id` for managed-identity pull access.

Readiness checks configuration and local CLI support without inference. A CLI
without verified Fusion flags intentionally returns not-ready and cannot serve
model calls. A successful container build alone does not establish Fusion
availability, account entitlement or enterprise policy.

ACA HTTP ingress has a 240-second request deadline. Keep Fusion's execution
timeout below that deadline with room for gateway/cold-start overhead. The
defaults are 120 seconds in the service and 150 seconds in MCP registration.
The standalone service's configurable 1800-second maximum is **not** supported
end to end through this ACA HTTP path.

## Recovery, upgrades and costs

Managed PostgreSQL backup retention is configurable. Before production, perform
a point-in-time restore drill into a separate server and test reconnecting the
gateway. Retain the matching LiteLLM encryption salt securely with the recovery
procedure. Regional recovery and zone-redundant availability require explicit
customer planning and suitable SKUs; this POC does not promise them.

For upgrades, pin a reviewed image/digest, run the complete local test suite,
back up the database, review migration compatibility and test a staging revision.
Keep the previous image and configuration. Rolling back an image does not undo
database migrations; use the vendor-supported recovery path when schemas change.
Never rotate the salt casually to fix an upgrade problem.

To disable Fusion, revoke its dedicated keys/remove the optional registration,
then set `enable_fusion_mcp=false`, review the plan and apply it. The plan should
remove only Fusion resources and related permissions while retaining LiteLLM
and its database. Local client uninstall is separate from access revocation.

Costs include persistent PostgreSQL compute/storage/backups, ACA usage/minimum
replicas, VNet environment platform-managed load balancer/public IP resources,
Log Analytics ingestion/retention, Key Vault operations and any selected private
registry/networking. GitHub Fusion requests consume applicable Copilot usage.
No NAT Gateway, Azure Firewall, paid dedicated workload profile or new ACR is
required by default. Check current regional prices in the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
and configure budgets before deployment; no fixed bill is promised here.

Key Vault retains its RBAC-protected public service endpoint. Private-only ACA
does not automatically make every supporting Azure service endpoint private.
Private endpoints, restrictive vault networking, zone redundancy, enterprise
identity delegation and regional recovery require a separate production design.

## Safe validation

Run Terraform formatting, validation and mocked plan tests without Azure:

```sh
cd infra/terraform
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

After an operator-approved deployment, confirm gateway liveness/readiness,
database connectivity and model-list access without running provider inference.
Then complete [deployment validation](VALIDATION.md). Model calls and GitHub
consent are explicit later steps, not health probes. No Azure deployment was
performed as part of the repository implementation.

## References

- [ACA ingress](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview)
- [ACA VNet configuration and managed resource costs](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks)
- [ACA Key Vault references](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
- [PostgreSQL private networking](https://learn.microsoft.com/en-us/azure/postgresql/network/concepts-networking-private)
