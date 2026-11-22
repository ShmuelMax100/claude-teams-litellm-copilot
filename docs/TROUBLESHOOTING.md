# Troubleshooting

| Symptom | Check |
| --- | --- |
| Gateway rejects model | Use the exact tier on the personal invitation; no Claude/provider alias is accepted |
| 401/403 before model work | Check the personal key, matching subject binding, PAT expiry and signed service identity; do not share another user's token |
| Credential revoked upstream | Replace that user's PAT through Key Vault and update its recorded expiry |
| 429 busy | The Fusion service reached its session limit; retry later on the same route |
| 502/503/504 | Inspect sanitized service health and logs; no alternate provider will be invoked |
| Stream ends with an error | The request failed after output began; review local tool effects before restarting |
| Advanced Claude feature rejected | Check the supported text/tool subset in SDK-MIGRATION.md |
| Doctor sees another endpoint | Remove inherited shell/provider overrides and start a new Claude Code process |
| Fusion health fails | Check bundled runtime, startup allowance, secrets and session cleanup; health is not an entitlement check |
| Azure region creation denied | Request access or explicitly select another allowed region; do not reuse the failed saved plan |

Run `./setup doctor` for local authorization/configuration checks. It sends no inference. The old Claude Teams login window, provider connection wizard and fallback settings are no longer part of setup.

Do not paste raw server logs or secrets into an issue. Report sanitized status, test name, version and whether output had begun. Source changes do not migrate existing cloud resources; review a fresh Terraform plan before deployment.
