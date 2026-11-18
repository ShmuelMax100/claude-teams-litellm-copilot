# Direct Copilot mode (pilot)

Use `./setup --mode copilot-only --help`. Omitting the mode preserves the existing
Claude subscription and fallback workflow. Copilot connection does not require
Claude Code, a Claude subscription, or changes to the normal Claude profile.

## Compatibility boundary

This implementation targets the official, unmodified LiteLLM **1.108.0** image.
The runtime checks are recorded separately in `COPILOT-VALIDATION.json`.
Do not interpret historical `VALIDATION.json` results as evidence for this mode.

**API-only preparation is blocked on this version.** The `/model/new` API rejects
`github_copilot_auth_type`, while omitting that parameter from the deployment
allows missing credentials to select the shared device-login path. The supported
pilot prerequisite is a file-managed route with an explicit
`github_copilot_auth_type: per_user_oauth` marker. It must remain on the route even
when the named credential is deleted. Do not remove this guard to make API writes
succeed.

Generate a fragment using a model ID verified for the developer's account:

```sh
./setup --mode copilot-only fragment --model claude-direct=YOUR_COPILOT_MODEL
```

The output is JSON (also valid YAML). Merge its `model_list` entries into the
customer's authoritative gateway configuration through their deployment process;
keep unrelated models, router policy, database settings and credentials. Restart
using that configuration. This installer never replaces the server's config.
Do not assume checked-in model IDs are still offered or entitled.

Export the **active, complete deployment configuration** as JSON for the audit.
Keep it private: it may contain customer secrets. `--server-config` must refer to
that active configuration, not merely the generated fragment. The CLI compares
its route fields against live model discovery and checks the gateway version.
LiteLLM masks the auth marker in discovery; the active config is therefore part
of the trust boundary. An export from a different server is not valid evidence.

```sh
./setup --mode copilot-only prepare --gateway https://gateway.example \
  --server-config /secure/active-config.json --model claude-direct=YOUR_COPILOT_MODEL
# Repeat with --apply after reviewing the preview.
```

Preparation creates or reuses `github-copilot-user` as a native per-user OAuth
credential. It rejects duplicate aliases, conflicting provider overrides and
mismatched credentials before writing. It adds no retry/cooldown overrides.
Database persistence and native per-user OAuth must be enabled on the gateway.

## Existing users and keys

Supply the exact existing LiteLLM user ID and that user's virtual key. No new
UUID, user, team or replacement key is required. The supplied key must explicitly
authorize the selected aliases and allow the required connection operations.
Missing permissions stop onboarding without modifying them. The protocol-specific
allowlist is defined by `copilot.required_routes()`; administrators should review
any additions using their existing permission-management process.

```sh
./setup --mode copilot-only enroll --gateway https://gateway.example \
  --server-config /secure/active-config.json --model claude-direct=YOUR_COPILOT_MODEL \
  --user-id EXISTING_USER_ID --github-login EXPECTED_LOGIN \
  --protocol chat --out /secure/developer.invite.json
# Repeat with --apply to apply the displayed plan and save the private invitation.
```

Keys are entered at hidden prompts, never as command-line arguments. Preview does
not write an invitation or mutate the gateway. Apply merges
`metadata.disable_fallbacks=true` into the existing key metadata, preserving other
metadata, budgets, expiry, teams and router settings. It does not change model or
route permissions. Repeating enrollment reuses the same key and invitation.

## No-fallback policy and limitations

LiteLLM's native key metadata switch overrides a client's `disable_fallbacks:false`
and stops generic/default, context-window and content-policy fallback paths.
Retries are separate and remain governed by the existing gateway policy.

The guarantee applies to calls authenticated with the enrolled key. Other,
unmodified gateway keys (including broad-access keys) are outside this guarantee.
Do not advertise this as a gateway-wide route policy.

The switch applies to the **whole key**, not a particular model. An existing key
that also serves unrelated routes is rejected unless it already disables
fallbacks. Do not disable fallback globally or silently change a mixed-purpose
key. A separately approved, user-bound Copilot key is another option. Unrelated
keys, models, teams and fallback policies remain unchanged.

In the pilot, `default_fallbacks` alone remained visible in the reported router
settings but did not execute for an unmatched model with the combined database
configuration. The control check initially failed. The pilot uses an explicit
`{"*": ["local-alternate"]}` entry in `fallbacks` for its default-rule check;
this is not evidence that the separate `default_fallbacks` option works. Do not
silently rewrite a customer's policy to hide this limitation.

File configuration can drift after an audit. Repeat preparation after gateway
changes; restrict configuration administration through normal customer controls.
This is not a custom proxy, and it does not enforce policy against administrators
who deliberately change the underlying routes or key metadata.

## Connection and optional client setup

```sh
./setup --mode copilot-only connect --invite /secure/developer.invite.json
./setup --mode copilot-only doctor
```

Complete the GitHub device consent yourself, using the expected account. The CLI
rejects mismatches, denied consent and expired flows. Rerun connect after expiry
or a disconnect. Existing correct connections are reused. Connection state lives
under `$XDG_CONFIG_HOME/claude-gateway/copilot-only.json` (default `~/.config`).
It contains the virtual key and is saved with owner-only permissions.

Doctor verifies key ownership, native no-fallback metadata, GitHub identity and
model discovery. It explicitly reports **inference not run** and requires an
administrator route audit. Model discovery is not proof of provider entitlement.
No live inference is implicit in connect or doctor.

For Claude Code, enroll with `--protocol messages`, use a `claude-`/`anth-` alias,
and explicitly select a client directory:

```sh
./setup --mode copilot-only configure --invite /secure/developer.invite.json \
  --client-dir /secure/isolated-claude
CLAUDE_CONFIG_DIR=/secure/isolated-claude claude
./setup --mode copilot-only restore --client-dir /secure/isolated-claude
```

Configuration uses the virtual key as `ANTHROPIC_AUTH_TOKEN` and direct model
aliases. It preserves unrelated settings, creates private backups and refuses
restore if settings changed later. Detected managed policy requires IT handling;
project, shell and centrally managed policy layers still need customer review.
Configure performs no Claude sign-in. Do not use the normal Claude directory for
the pilot.

Format-1 invitations remain legacy. Format 2 explicitly records copilot-only,
protocol and key policy. Inconsistent format/mode combinations are rejected.

## Local pilot and rollback

`pilot/compose.yaml` uses a pinned official LiteLLM image, dedicated PostgreSQL
volume and a synthetic provider that counts calls without logging prompts or
headers. Only the gateway port is published, on loopback. Private runtime state
belongs under ignored `private/pilot/` with directory mode 700 and files 600.
See `pilot/bootstrap.py` for reproducible initialization, then:

```sh
python3 pilot/bootstrap.py
docker compose -f pilot/compose.yaml up -d
python3 pilot/check_local.py
docker compose -f pilot/compose.yaml restart gateway
python3 pilot/check_local.py
docker compose -f pilot/compose.yaml stop
```

The controlled tests make synthetic local calls only; they do not run real
Copilot inference. Live consent and prompts require separate user action and
approval. Retain the secret files and database volume for restart. `stop` leaves
both intact. Do not run `down -v` or revoke connections as routine cleanup.

Rollback of a customer integration must follow the customer's configuration
process: remove only the aliases introduced for this mode after checking current
usage; restore a key's metadata only if it still matches the installed state.
Do not delete shared users, keys or credential definitions. Local client restore
is separate and does not revoke GitHub or LiteLLM access.

## Sources

- [Native Copilot setup](https://docs.litellm.ai/docs/providers/github_copilot)
- [Native fallback controls](https://docs.litellm.ai/docs/proxy/reliability)
- [1.108.0 authentication implementation](https://github.com/BerriAI/litellm/blob/v1.108.0/litellm/llms/github_copilot/per_user_auth.py)
- [1.108.0 key policy injection](https://github.com/BerriAI/litellm/blob/v1.108.0/litellm/proxy/litellm_pre_call_utils.py)

Preview the live synthetic batch without sending requests:

```sh
python3 pilot/check_live.py --invite /secure/pilot-invitation.json
```

Only after the user confirms model entitlement and approves the displayed exact
prompts may the batch be run with `--approved`. It plans four requests and keeps
a persistent ten-request cap, counting failures. No customer or repository
content is included. It does not certify provider routing from a response label;
inspect gateway evidence as well.

## Verified pilot results · 18 November 2026

The pinned official LiteLLM 1.108.0 image passed direct Chat Completions,
streaming and one synthetic `echo` tool/result round trip against
`github_copilot/claude-sonnet-5.5` with one consenting GitHub account.
Five live requests were sent within the approved ten-request cap: four succeeded;
one forced-tool-choice request failed. The provider rejects forced `tool`/`any`
selection for this model; leaving selection automatic passed with the same
synthetic prompt. This does not establish full tool parity or certify Claude Code.

Gateway spend records identified the expected user, alias, provider, upstream
model and a GitHub Copilot API host for all five requests. No retries or cache
hits were logged. The instrumented alternate target received zero requests during
that live interval, including the failed request. Private request IDs and logs
remain in ignored protected storage; the public report contains aggregates only.
Run `python3 pilot/check_evidence.py` immediately after a live batch to reproduce
that evidence check, before running control tests that intentionally increment
the alternate-target counter.

The connection survived restart. An unconnected user's request returned 401 while
the real account was connected. This is not a test of two connected accounts.
The 16 controlled integration checks separately cover generic/wildcard-default,
context-window, content-policy and pre-first-chunk stream failures, key/team
policy overrides, a deleted test credential and model authorization. Each blocked
scenario recorded zero alternate calls, while its applicable control invoked the
alternate once. See the explicit `default_fallbacks` limitation above.

All 44 unit tests, guide generation, package checks and public-content checks
passed. Services are stopped after acceptance; persistent database and protected
secrets are retained. Restart does not require re-consent while the connection
remains valid. No normal Claude profile or Claude sign-in was modified.

After an interrupted approved batch, `--approved --resume` reuses saved completed
stages and preserves the accumulated request count. A completed batch sends no
new inference when resumed. Approval is still required for any new live batch.
