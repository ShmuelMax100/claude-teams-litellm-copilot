# Troubleshooting optional Azure and Fusion

Existing model/fallback diagnostics remain in [configuration](CONFIGURATION.md)
and the onboarding website. The cases below concern the optional components.

| Symptom | Check and recovery |
|---|---|
| `runtime_missing`, `unsupported_runtime` or `runtime_check_failed` | Use the reviewed SDK image and matching native bundle; check local readiness and nonsecret startup status. `FUSION_COPILOT_BIN` is obsolete; use `FUSION_RUNTIME_PATH`. Never bypass protocol/version checks. |
| `runtime_unavailable` or `cleanup_failed` | Admission closes and liveness requests a container restart. Inspect runtime health/resource pressure and nonsecret status; never reuse uncertain session state. |
| Fallback registration says guard is missing | Mount the reviewed callback, set matching owner/private `/v1` base/service bearer and verify the active gateway revision. A model alone is insufficient. |
| Fallback 403 or no switch after primary error | Check the separate owner-bound invitation. Auth/input/policy failures and errors after any output intentionally do not qualify. Direct backup aliases are denied. |
| Fallback 400 or 502 | Check supported text/tool shape, complete historical results, local-only schema references and output bounds. Expensive schema validation is terminated in an isolated worker; invalid tool arguments are never returned for execution. |
| Fallback 429 | The service or bounded schema-validation workers are busy; reduce concurrency. No second backup or shared credential is used. |
| Fallback appears slow | It first observes primary failure and then buffers the Fusion result. One warm replica reduces cold starts; it cannot remove network/generation latency. |
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
| SDK timeout or output limit | Reduce input or choose permitted operator limits. The session is aborted/deleted; no other account or unconfigured model is substituted. |
| Client install/uninstall conflict | Preserve newer settings, inspect the private backup and resolve the named MCP entry manually. Do not replace the whole Claude config. |
| Terraform wants to replace PostgreSQL | Stop and plan a database migration/restore. A changed mode, name or network setting may replace persistent resources. |

Health and discovery checks do not prove model entitlement. Live calls require
explicit consent and can consume usage. Capture nonsecret status, request ID,
tier and runtime status for support; avoid posting prompt contents, keys, raw upstream
errors, Terraform state or saved plans.
