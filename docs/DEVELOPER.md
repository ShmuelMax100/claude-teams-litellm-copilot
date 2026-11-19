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

## Optional Fusion

Fusion is separate from model access and disabled by default. It currently
requires a supported future/audited CLI build because the verified 1.0.114
release lacks the documented Fusion flag. The initial service is a single-user
POC, not shared team execution. Your existing inference invitation does not
automatically grant MCP access.

Use only the separate Fusion invitation issued to you by the administrator.
`./setup fusion --help` lists connect, discovery and uninstall commands.
Connection validates ownership/server access and checks tool discovery without
sending a model request. It installs one user-scoped MCP entry with a private
backup and preserves unrelated registrations and Claude settings. Project or
centrally managed policies may still require IT configuration.

The client uses LiteLLM `/mcp`; do not connect directly to the private ACA
service or paste GitHub tokens into Claude Code's MCP registration. The virtual
key in your local configuration is sensitive. Keep invitations and backups
private and do not commit them.

Tools accept text only and return coding assistance. They do not receive your
repository automatically or offer a remote shell. Tool listing is free of model
usage; an actual Standard/High/Max invocation may consume Copilot usage and
requires the administrator to resolve CLI support and policy prerequisites.

## Remove or recover

Use the Fusion uninstall command to remove only the entry installed by setup.
If it changed afterward, resolve the conflict using the private backup rather
than overwriting newer settings. Existing inference setup remains in place.
Local removal does not revoke the gateway key or GitHub PAT; ask the
administrator to revoke access when offboarding.

For an authorization failure, confirm you have the separate owner-bound Fusion
invitation. For `unsupported_cli`, the administrator must provide an audited
compatible CLI image; retrying prompts will not fix it. More cases are in
[troubleshooting](TROUBLESHOOTING.md).
