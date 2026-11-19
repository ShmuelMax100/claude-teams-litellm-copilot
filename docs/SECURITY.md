# Identity and security boundaries

## Existing inference remains personal

Claude subscription authentication and LiteLLM's per-user Copilot OAuth remain
the existing inference path. The Azure option changes hosting, not provider
identity. Existing invitations, model aliases and key-scoped fallback policies
are unchanged. Copilot-only mode still requires its file-managed per-user
authentication marker and native no-fallback key policy.

Fusion has a separate credential lifecycle. A LiteLLM OAuth connection is not a
verified Copilot CLI credential and is never exported or reused for this service.

## Supported Fusion mode

The initial service supports an explicitly configured **single-user POC only**.
`FUSION_AUTH_MODE` defaults to `disabled`; `single_user` requires an owner,
GitHub token and independent MCP bearer. Multi-user mode is rejected.

Use a user-owned fine-grained GitHub PAT with the documented **Copilot Requests**
permission. An organization-owned token or a classic PAT is not a substitute.
The operator must verify that the token belongs to the intended GitHub account,
and that the configured `FUSION_SINGLE_USER_SUBJECT` is the corresponding
LiteLLM user ID. This mapping is administrator-attested. The implementation does
not claim cryptographic delegation or automatic GitHub account verification.

Register the service with that same owner and grant a separate MCP-only key only
to that owner. Never put a shared GitHub token behind a server available to a
team. A dedicated single-user gateway is the simplest POC deployment. Attaching
to another gateway requires an administrator audit of existing grants, access
groups, keys and configuration. The service bearer authenticates the gateway;
it cannot prove which person is behind it. Caller-supplied identity headers are
not trusted.

Future multi-user support requires a verified per-user delegation protocol or
separate credentials and execution boundaries, revocation, ownership checks and
concurrent two-user tests. Enabling a `multi_user` setting is not sufficient.

## Gateway access controls

The MCP registration uses native LiteLLM Streamable HTTP, bearer authentication
and `allow_all_keys: false`. No anonymous passthrough mode is used. The client
sends its dedicated virtual key in `x-litellm-api-key`, preserving the separation
between gateway and upstream credentials. The `x-mcp-servers` selector narrows
access; it does not grant it.

MCP keys explicitly name the server in `object_permission.mcp_servers`, limit
routes to MCP and identity checks, and set `models: [no-default-models]`.
Inference invitations and keys are never widened by Fusion enrollment. A
server's `available_on_public_internet` setting concerns the caller's IP range;
it does not make an internal ACA service public or remove key authentication.

Permission behavior is version-specific. Tests exercise the pinned LiteLLM
image with authorized, inference-only, ungranted and unauthenticated clients.
Repeat these checks before upgrading or changing team/access-group policy.

## Execution isolation

- Each invocation gets a new temporary working directory, home and CLI config.
  No developer repository, host home or Docker socket is mounted.
- The subprocess receives an allowlisted environment with only its configured
  GitHub credential. Ambient `GH_TOKEN`, `GITHUB_TOKEN`, provider keys and BYOK
  routing settings are not inherited.
- Tool input contains a bounded prompt only. Clients cannot choose executables,
  paths, models, CLI flags, shell commands or environment variables.
- All CLI tools and built-in MCP integrations are denied. Custom instructions
  and auto-update are disabled. Missing required controls fail capability checks.
- Time, input, output and concurrency are bounded. Cancellation and timeout
  terminate the process group; temporary state is removed.
- Startup checks CLI help/version only. Discovery and diagnostics do not incur
  model usage. Live invocation requires an explicit operator action.

This service returns coding assistance as text; it is not a remote repository
editor or a general-purpose shell. A container boundary and these controls are
not a claim that arbitrary untrusted native software can safely share secrets.
Run only reviewed, pinned artifacts and restrict administrator access.

## Secrets, storage and logs

Azure uses separate user-assigned identities for LiteLLM and Fusion. Key Vault
references supply runtime secrets over identity-authenticated access. Keep role
assignments scoped to the required secrets. Rotate the service bearer and PAT
separately, coordinating the gateway's stored upstream credential.

The stable LiteLLM salt protects stored provider credentials. Back up and retain
it with the database recovery procedure; changing it arbitrarily can make
existing encrypted connections unusable. Do not expose keys through Terraform
outputs. Terraform state and saved plans still contain generated database/master
secrets even when marked sensitive. Use encrypted remote state, restricted RBAC,
locking, private plan storage and an approved retention policy. `.tfvars`, state
and plan files are ignored by Git.

PostgreSQL is private by default and uses TLS. Fusion ingress is internal and
still requires a bearer. Public LiteLLM ingress is HTTPS with key authentication;
private-only environments additionally require customer network connectivity.

Fusion audit logs include generated request ID, configured subject, tool/tier,
duration, result and CLI exit code. They do not intentionally contain prompt text,
provider output or tokens. Treat upstream and infrastructure logs according to
the customer's code-privacy and retention policy. Do not enable request-body or
debug logging for production troubleshooting without an approved data policy.

## Revocation and incident response

1. Revoke the dedicated MCP key to stop client access without changing inference.
2. Disable the Fusion app or remove its registration if upstream isolation is in
   doubt. Retain evidence without publishing payloads or secrets.
3. Revoke the GitHub PAT and rotate the independent service bearer when exposed.
4. Audit gateway administrators, server grants, team/access-group membership,
   Key Vault access and container-image provenance.
5. Re-run negative authorization and isolation tests before restoring access.

Local MCP uninstall only removes the installed client registration. It does not
revoke a gateway key or a GitHub token.

## References

- [GitHub CLI authentication and token types](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
- [GitHub CLI tool permissions](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools)
- [LiteLLM MCP authentication](https://docs.litellm.ai/docs/mcp_config_reference)
- [ACA Key Vault references](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
