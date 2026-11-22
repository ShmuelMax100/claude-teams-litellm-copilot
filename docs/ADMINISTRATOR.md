# Administrator setup

Deploy [the required ACA architecture](AZURE-DEPLOYMENT.md) first. Keep the LiteLLM master key private; setup asks for it without echoing it. Static model configuration and the direct-routing guard must already be active. Preparation verifies them and does not add arbitrary providers or enable database-managed model routes.

```sh
./setup admin prepare --gateway https://gateway.example --upstream https://fusion.internal.example/v1
```

Create a LiteLLM user with an exact stable `user_id` matching the administrator-approved `fusion_users` binding in Terraform. Each user must have their own unexpired GitHub fine-grained PAT with Copilot Requests permission and suitable Copilot/Fusion access. Store the PAT only in that user's Key Vault secret; record its real expiry. Do not reuse a PAT or secret across users.

Use LiteLLM's administrator-authenticated `POST /user/new` management API with the following body (replace the example identity). Keep the administrator bearer credential outside the request-body file and shell history. An existing user must have `user_role` equal to `internal_user` or `internal_user_viewer`, and administrator-managed `metadata.github_login` matching `--github-login`; enrollment checks both. Disable automatic key generation so only the restricted key created by this package is issued.

```json
{
  "user_id": "developer-id",
  "user_role": "internal_user",
  "auto_create_key": false,
  "send_invite_email": false,
  "models": ["fusion-standard", "fusion-high", "fusion-max"],
  "metadata": {"github_login": "developer-login"}
}
```

Issue a personal invitation after reviewing the preview:

```sh
./setup admin enroll --gateway https://gateway.example --upstream https://fusion.internal.example/v1 --user-id developer-id --github-login developer-login --tier standard --out private/developer.invite.json
./setup admin enroll --gateway https://gateway.example --upstream https://fusion.internal.example/v1 --user-id developer-id --github-login developer-login --tier standard --out private/developer.invite.json --apply
```

The key is limited to the chosen Fusion tier and necessary model/key inspection routes. It grants no provider credential-management or public MCP permissions. Invitations contain a gateway credential and must be transferred privately. The GitHub login label is descriptive; the authenticated gateway user ID and approved server binding control credential selection.

Preparation, invitation validation and doctor send no model requests. A live invocation consumes the user's Copilot allowance and is a separate acceptance step.

To offboard a user, revoke their personal gateway keys and GitHub PAT, remove their server binding, then deploy the revised configuration. For rotation, update the user's Key Vault secret and declared expiry. Versionless ACA Key Vault references refresh within approximately 30 minutes and restart affected revisions; use a controlled revision rollout when immediate replacement is required. Revocation at GitHub takes effect independently of secret refresh.
