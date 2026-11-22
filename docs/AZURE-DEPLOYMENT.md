# Azure deployment

The supported deployment always includes two applications in one Azure Container Apps environment: public LiteLLM and private Fusion. PostgreSQL is persistent and privately networked; Key Vault and managed identities supply the required secrets. There is no deployment option that omits Fusion or enables another model provider.

## Prerequisites

- An approved Azure subscription and region with ACA, Key Vault and PostgreSQL capacity. Terraform 1.11+; CI pins 1.16.5 and the checked-in provider lock.
- Azure CLI authentication and permission to create the reviewed resources and individual role assignments.
- Docker and a container registry for a Linux AMD64 Fusion image. ACA's standard environment uses Linux AMD64; the image also supports ARM64 for local testing.
- An existing Key Vault containing an independent service token and one distinct fine-grained GitHub PAT for each approved user. Each PAT needs Copilot Requests permission, an actual expiry date and that user's Fusion entitlement. A Claude Teams subscription does not supply this entitlement.
- A stable LiteLLM user ID for each credential binding. Do not use one GitHub account or PAT for a multi-user deployment.

Build from the pinned SDK/runtime sources, then publish to your approved registry and record the resulting immutable image digest:

```sh
docker build --platform linux/amd64 -t fusion-mcp:reviewed services/fusion-mcp
```

The SDK and native runtime are inside the Fusion image. No Copilot CLI installation on the developer's machine or a CLI `--fusion-tier` flag is required. Image build verifies the recorded dependency/runtime hashes; no SDK/runtime download occurs during a model request.

## Configure and review

Copy `infra/terraform/terraform.tfvars.example` to a protected ignored path under `private/azure/`, then replace every placeholder. Required Fusion values are `fusion_image`, `fusion_key_vault_id`, `fusion_service_token_secret_id` and `fusion_users`. A user entry holds a Key Vault secret URI and actual PAT expiry in Unix seconds, never the token value. The example expiry `0` intentionally fails validation.

The credential vault must match the referenced PAT and service-token URIs. Distinct users need distinct secret scopes even when secret versions differ. Terraform rejects reuse, expired entries and overlap with the service token. It generates a separate signing secret in the deployment vault. Gateway identity access excludes all user PATs; Fusion gets only the named secret scopes.

```sh
./setup azure plan --var-file private/azure/deployment.tfvars.json
```

The helper expects JSON when using the `.tfvars.json` extension. Use a `.tfvars` path for the HCL example, or use `./setup azure configure --help` to generate JSON with all mandatory Fusion inputs. Review the saved plan locally before applying. The plan and Terraform state contain secrets; keep them private. A shared deployment should configure an encrypted, access-controlled remote state backend before applying.

```sh
./setup azure apply --plan-file private/azure/deployment.tfplan
./setup azure outputs
```

Apply creates billable resources and requires an explicit operator decision. This source revision does not run apply or change existing Azure resources. Follow [administrator setup](ADMINISTRATOR.md) after deployment and issue personal invitations before connecting Claude Code.

## Runtime and network

Only LiteLLM has external HTTPS ingress. Fusion uses internal ingress in the same environment. The gateway holds the service/signing secrets; the Fusion application receives per-user secrets through Key Vault references and a nonsecret binding map. SDK sessions run with explicit user credentials in empty mode.

The generated static catalog contains only `custom_openai/fusion-standard`, `custom_openai/fusion-high` and `custom_openai/fusion-max`. Database-managed model storage is disabled. The mandatory callback derives the user identity after key validation, fixes the private destination and rejects provider overrides. Gateway retries and all failover policies are disabled.

Warm replicas reduce startup delay and incur ongoing charges. Defaults are one warm Fusion replica and at least one gateway replica; PostgreSQL, monitoring and the registry have their own charges. Review current regional pricing and a fresh plan for the chosen subscription. This guide does not estimate exact cost.

[ACA HTTP ingress has a 240-second timeout](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview). Fusion execution is at most 210 seconds; the gateway adds 20 seconds of transport/cleanup margin. Long-running work beyond that limit returns an error. Startup, readiness and liveness probes remain separate. A user's expired token does not restart the shared service; an unrecoverable runtime/cleanup failure does.

[Versionless Key Vault references refresh within 30 minutes and restart affected revisions](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets). Updating an app secret directly also needs a revision restart. Revoke a compromised PAT at GitHub immediately instead of relying on the refresh schedule.

## Migration from the previous deployment

Regenerate variable files and saved plans. The old optional-Fusion, single-owner and provider-failover flags no longer exist. Terraform includes moved blocks for the former counted Fusion application, identity and service-secret role addresses. The old owner PAT grant is replaced by user-specific grants; inspect those IAM changes in the plan.

Revoke obsolete gateway keys/provider connections, review stored database records and redeploy the static catalog and mandatory guard. Existing database routes cannot authorize a non-Fusion request through the guard. Issue fresh `fusion-gateway-v1` invitations; old invitations are rejected. Remove obsolete provider secrets only after confirming no other application owns them.

A prior regional denial is not fixed by changing source. The recorded West Europe deployment attempt did not create a running gateway. Region selection or quota/access approval remains an operator prerequisite; this revision does not select or deploy another region.
