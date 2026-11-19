# Developer guide

## Standard onboarding

Get your private invitation from the administrator, then run `./setup developer`
and select **Join with my invitation**. Sign in with the accounts named in the
invitation. The existing flow checks model access, backs up Claude settings and
preserves unrelated settings. It includes the existing explicit live checks.

Restart Claude Code and use it normally. `./setup doctor` checks the established
connection, and `./setup models` shows model/fallback choices. Those default
commands keep their current behavior. If you need direct Copilot without a
Claude subscription, use the separate [copilot-only guide](COPILOT-ONLY.md).

## Optional automatic Fusion fallback

An administrator can issue a separate `fusion-fallback-v1` invitation for the
single configured owner. Keep your Claude subscription sign-in, then run:

```sh
./setup fusion fallback-connect --invite /secure/fusion-fallback.invite.json
./setup fusion fallback-doctor --invite /secure/fusion-fallback.invite.json
```

Restart Claude Code afterward. Connection and this doctor make no provider calls.
Your existing model selection remains; eligible temporary primary failures can
start a fresh Fusion task with conversation history as context. Claude Code
continues to execute tools locally. Fusion uses your Copilot entitlement, not
your Claude Team subscription. Unsupported content fails explicitly, and fallback
output is buffered before display. Read the [compatibility limits](FUSION-FALLBACK.md).

Use `./setup fusion fallback-restore` to restore the backed-up settings, subject
to the existing conflict checks. Local restoration does not revoke the key;
ask the administrator to revoke it when retiring access.

## Optional MCP tools

MCP is separate from automatic model fallback and disabled by default. Your
ordinary inference invitation does not grant MCP access. Use only the separate
MCP invitation issued to you by the administrator. `./setup fusion --help` lists
connect, discovery and uninstall commands.

Connection validates ownership/server access and checks discovery without
sending a model request. It installs one user-scoped MCP entry with a private
backup and preserves unrelated registrations and settings. Project or centrally
managed policies may still require IT configuration.

The client uses LiteLLM `/mcp`; do not connect directly to the private ACA
service or paste GitHub tokens into Claude Code's MCP registration. Invitations,
local virtual keys and backups are sensitive and must not be committed.

Tools accept text and return coding assistance. They do not receive your
repository automatically or offer a remote shell. An actual Standard/High/Max
call can consume Copilot usage and requires the administrator to confirm account
policy. Discovery and local SDK readiness do not prove entitlement.

Use `./setup fusion uninstall` to remove only the MCP entry installed by setup.
If it changed afterward, resolve the conflict using the private backup. Local
removal does not revoke the gateway key or GitHub PAT. For runtime or access
failures, use [troubleshooting](TROUBLESHOOTING.md); repeating prompts cannot fix
missing entitlement or an unhealthy runtime.
