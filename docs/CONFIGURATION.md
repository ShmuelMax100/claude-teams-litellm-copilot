# Configuration reference

The sole public aliases are `fusion-standard`, `fusion-high` and `fusion-max`. The deployment uses LiteLLM 1.108.0 and a checksum-pinned SDK/runtime image. See [Terraform inputs](../infra/terraform/variables.tf) and [example values](../infra/terraform/terraform.tfvars.example).

| Setting | Purpose |
| --- | --- |
| `FUSION_API_BASE` | Private Fusion base URL ending in `/v1`; gateway only |
| `FUSION_SERVICE_TOKEN` | Independent secret authenticating the gateway service |
| `FUSION_IDENTITY_KEY` | Separate HMAC secret for short-lived user assertions |
| `FUSION_USER_CREDENTIALS_JSON` | Fusion-only subject-to-PAT-environment-name and expiry map |
| `FUSION_TIMEOUT_SECONDS` | Same-route request deadline; ACA deployment at most 210 seconds |
| `FUSION_ALLOWED_TIERS` | Service-allowed `standard,high,max` subset |
| `FUSION_MAX_CONCURRENCY` | Admission bound for active SDK sessions |
| `FUSION_MAX_CONTEXT_CHARS` | Input context bound |
| `FUSION_MAX_OUTPUT_BYTES` | Returned output bound; not an upstream token/billing limit |
| `FUSION_RUNTIME_PATH` | Image-bundled native SDK runtime executable |

Credential map shape (nonsecret names; never put token values here):

```json
{"developer-id":{"token_env":"FUSION_GITHUB_USER_EXAMPLE","expires_at":2000000000}}
```

Use the PAT's actual expiry in Unix seconds. The service rejects missing, expired, malformed or reused credentials. Terraform generates environment secret references from approved `fusion_users`; avoid hand-editing the application environment.

The static LiteLLM model catalog uses only `custom_openai/fusion-*`. Database-managed models are disabled. Gateway callbacks enforce authenticated user identity, fixed upstream credentials and deployment, zero retries, and no provider failover. Do not remove the callback: provisioning verifies its active descriptor.

Legacy Claude-primary, single-owner and optional Fusion variables are obsolete. Existing variable files must be replaced using the new examples; saved plans must be regenerated. No compatibility switch restores the old routes.

Claude Code settings use `ANTHROPIC_BASE_URL`, a personal `ANTHROPIC_AUTH_TOKEN`, and Fusion names for the main and secondary model selectors. Those selectors name tiers within one route. They do not configure a second provider.
