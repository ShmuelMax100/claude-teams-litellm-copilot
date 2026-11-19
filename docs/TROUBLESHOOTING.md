# Troubleshooting optional Azure and Fusion

Existing model/fallback diagnostics remain in [configuration](CONFIGURATION.md)
and the onboarding website. The cases below concern the optional components.

| Symptom | Check and recovery |
|---|---|
| Fusion `unsupported_cli`; readiness 503 | Expected with the verified 1.0.114 pin, which lacks `--fusion-tier`. Audit an actually supported release, update its pin/checksums and rerun capability/tests. Do not bypass checks. |
| Fusion disabled | Confirm the operator explicitly selected single-user mode, owner and independent credentials. Never switch to a shared multi-user token. |
| MCP 401 | Check the dedicated virtual key and `x-litellm-api-key` header. The service bearer and GitHub token are different credentials. |
| MCP 403 or missing tools | Confirm owner/server grants, exact alias, route allowlist, expiry and team/access-group restrictions. An inference-only key is intentionally denied. |
| Public developer cannot reach a registered server | LiteLLM `available_on_public_internet` filters client IPs; it is separate from internal upstream ingress and key authorization. |
| Upstream Fusion unreachable | Confirm internal ACA DNS/HTTPS reachability from LiteLLM, active revision/readiness and matching service bearer. Developer machines do not need direct access. |
| Gateway cannot read Key Vault secret | Confirm user-assigned identity is attached, secret URI is correct, scoped role is assigned and RBAC propagation has completed. Do not print the secret to diagnose access. |
| Gateway readiness/database failure | Confirm private DNS link/subnets, database state, TLS URL and migration permissions. Retain the stable salt and check startup logs without enabling payload logging. |
| Private gateway unreachable | Confirm customer VPN/peering and private DNS. Private-only mode requires a network path beyond a valid key. |
| Model routes disappear after restart | Check persistent `DATABASE_URL` and `store_model_in_db`; do not treat a stateless container as a complete gateway. |
| Copilot-only route audit fails | Follow its file-managed per-user marker requirements. Do not weaken authentication or replace the marker with shared credentials. |
| CLI timeout or output limit | Reduce input or choose permitted operator limits. The request fails and its process group is stopped; it does not fall back to another account/model. |
| Client install/uninstall conflict | Preserve newer settings, inspect the private backup and resolve the named MCP entry manually. Do not replace the whole Claude config. |
| Terraform wants to replace PostgreSQL | Stop and plan a database migration/restore. A changed mode, name or network setting may replace persistent resources. |

Health and discovery checks do not prove model entitlement. Live calls require
explicit consent and can consume usage. Capture nonsecret status, request ID,
tier and exit code for support; avoid posting prompt contents, keys, raw upstream
errors, Terraform state or saved plans.
