# Administrator guide

## Existing gateway: unchanged default

Run `./setup admin`, prepare the customer gateway and invite each developer.
Use the existing [invitation example](INVITATION-EXAMPLE.txt). Deliver invitations
privately; the wizard does not send email. Each invitation contains a personal
virtual key, never the administrator key or provider token.

Retain personal Copilot OAuth, the current model catalog and key-level fallback
policy. Copilot-only mode remains a separate opt-in path described in
[COPILOT-ONLY.md](COPILOT-ONLY.md). No migration is required for existing users.

## New Azure gateway

Follow [AZURE-DEPLOYMENT.md](AZURE-DEPLOYMENT.md) to select the subscription,
resource prefix, region, database and network mode. Review Terraform's plan and
cost implications before applying. The optional command helper is documented by
`./setup azure --help`; Terraform remains independently runnable.

Use the output gateway URL with the same administrator wizard. Retrieve the
master key securely from Key Vault and enter it only at the hidden prompt.
Validate readiness/database connectivity before model registration. Confirm
that routes and personal connections survive restart before broader enrollment.

## Optional Fusion POC

The available CLI 1.0.114 lacks `--fusion-tier`; activation is blocked until a
reviewed supported binary passes capability checks. See [Fusion](FUSION-MCP.md).
Do not enable shared execution for a team. This implementation supports one
explicit owner, with a separate user-owned GitHub PAT and MCP bearer.

After that prerequisite is resolved:

1. Identify one existing LiteLLM user ID and verify the GitHub PAT belongs to
   that person. Configure the same service owner; do not trust request headers.
2. Deploy the authenticated service with internal ingress and approved secrets.
3. Use `./setup fusion --help` to preview native server registration, then apply
   the reviewed single-user record. Never use public/passthrough grants.
4. Enroll only that owner with a new MCP-only key and deliver its private Fusion
   invitation. Leave all inference invitations and permissions intact.
5. Verify discovery and unauthorized-key rejection before any opt-in model call.

On an existing gateway, inspect all keys, teams/access groups and server grants
before attaching a static-bearer service. A dedicated single-user POC gateway is
the simplest boundary. The bearer authenticates the gateway, not an end user.

## Operations

Assign owners for renewal, revocation, backups, restore drills, log retention and
image upgrades. Keep the LiteLLM salt stable with database backups. Review plans
that replace persistent resources as migrations. Disable only Fusion when
removing the optional POC; do not destroy the gateway/database.

Use [validation](VALIDATION.md), [troubleshooting](TROUBLESHOOTING.md) and
[security](SECURITY.md) for the exact boundaries. Local automated tests do not
replace Azure networking, organization policy or provider-entitlement checks.
