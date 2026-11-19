# Claude primary with optional Fusion fallback

This opt-in mode keeps the existing Claude Team subscription route primary and
uses native GitHub Copilot Fusion only after an eligible failure. It is an
explicitly single-owner proof of concept. Existing invitations and default
personal Copilot fallback are unchanged.

Fusion consumes the owner's Copilot entitlement. Its internal model selection
is controlled by GitHub; it does not spend Claude Team credits, guarantee MAI,
or let this gateway redirect a chosen Claude model to another subscription.

## Request flow

1. Claude Code sends its normal Messages request through LiteLLM using a new
   owner-bound fallback key. The primary uses the existing Claude credentials.
2. A pinned LiteLLM callback records primary failure. Only 429, 500, 502, 503,
   504, connection failure or timeout before any output can qualify. Authentication,
   authorization, invalid input, context-window and policy failures do not.
3. Once per request, the guard sends the supported transcript to the private
   Fusion adapter with its independent service bearer. Claude OAuth, proxy keys,
   incoming headers and provider settings are removed from that hop.
4. The warm Copilot SDK creates a fresh Fusion session, with system instructions
   separate from JSON-serialized conversation context. This is a new task with
   history, not a lossless native continuation of the Claude session.
5. Fusion returns text or a declared tool batch. The service aborts/deletes the
   session before returning. Claude Code executes requested tools locally; its
   next request carries their results into another fresh task if fallback occurs.

A successful primary request never calls or initializes the SDK through this
route. Direct requests to backup aliases, other users, caller-supplied fallback
eligibility and a second fallback attempt are denied. Once primary streaming
has emitted a frame, the guard forbids fallback replay.

## Supported compatibility surface

- Text messages, system instructions, JSON function schemas, prior assistant tool
  calls and their matching real results. Missing results are rejected.
- Automatic or disabled tool choice; optional parallel calls. Named/required
  tool selection, attachments, images, audio and unsupported generation controls
  fail explicitly instead of being silently converted.
- Claude thinking settings are removed at handoff; the enrolled Standard/High/Max
  Fusion tier controls effort. Native Claude reasoning state is not imported.
- Streaming is buffered until the SDK task succeeds, then delivered as SSE.
  It is not token-by-token live generation. This also avoids replay after partial
  fallback output, but increases time to first displayed text on the fallback.
- The SDK has no verified aggregate generation-token budget. `max_tokens` and
  `max_completion_tokens` conservatively bound returned UTF-8 content bytes and
  can truncate earlier than a tokenizer would. Complete tool calls must fit or
  fail; arguments are never truncated. These bounds do **not** limit upstream
  computation or billing. Usage is included only when reported by the SDK.
- Default combined deadline: primary 60 seconds plus fallback 140 seconds
  (120-second task and 20 seconds transport/cleanup). At most 230 seconds may be
  configured for the full path under ACA's 240-second ingress limit.

This is a constrained compatibility adapter. Local mocks and real SDK startup
checks do not prove live entitlement, routing quality or all Claude Code request
shapes. Run the opt-in live acceptance checks before relying on it for work.
Zero latency is not possible: warm capacity avoids routine runtime startup, but
networking, primary failure detection and model generation still take time.

## Administrator setup

Provision the [single-owner SDK service](FUSION-MCP.md). In Terraform, set
`enable_fusion_mcp=true` and `enable_fusion_fallback=true` with the owner and
separate Key Vault secret references. Review and apply a plan only through the
organization's approved deployment process. No deployment is performed by
registering routes.

For an existing pinned LiteLLM 1.108.0 gateway, mount
`services/litellm-fusion-guard/fusion_fallback_guard.py` next to its configuration,
make that directory importable, and add:

```yaml
litellm_settings:
  callbacks: [fusion_fallback_guard.guard]
```

Supply these settings securely; do not place secret values in commands or Git:

| Environment | Meaning |
|---|---|
| `FUSION_FALLBACK_OWNER_SUBJECT` | Same existing LiteLLM user ID as the SDK service owner |
| `FUSION_FALLBACK_API_BASE` | Private HTTPS origin ending in `/v1` |
| `FUSION_FALLBACK_BEARER_TOKEN` | Independent service bearer, never the GitHub PAT |
| `FUSION_FALLBACK_PRIMARY_TIMEOUT_SECONDS` | Primary budget, default `60` |
| `FUSION_FALLBACK_TIMEOUT_SECONDS` | Adapter budget, default `140` |

The service also requires `FUSION_ENABLE_FALLBACK=true`. The guard is necessary
for the owner/eligibility boundary. Merely registering a model is insufficient.
Enrollment checks the running callback descriptor, pinned gateway and matching
owner. LiteLLM uses its supported `custom_openai/fusion-*` provider path to reach
the private Chat Completions adapter; no global Messages conversion flag changes.

Preview both operations, then repeat with `--apply` after review. Administrator
keys are requested at hidden prompts. Replace the example URLs and user ID:

```sh
./setup fusion fallback-register --gateway https://gateway.example \
  --upstream https://fusion.internal.example/v1 \
  --single-user-subject EXISTING_USER_ID --single-user-attested

./setup fusion fallback-enroll --gateway https://gateway.example \
  --upstream https://fusion.internal.example/v1 \
  --single-user-subject EXISTING_USER_ID --single-user-attested \
  --tier standard --out private/fusion-fallback.invite.json
```

Enrollment issues a separate restricted key and `fusion-fallback-v1` invitation
for that owner and one tier. It does not widen any existing key, enable MCP
permissions or change the model catalog. Deliver the invitation privately.

## Developer setup and rollback

Keep the existing Claude subscription sign-in. Use the separate invitation:

```sh
./setup fusion fallback-connect --invite /secure/fusion-fallback.invite.json
./setup fusion fallback-doctor --invite /secure/fusion-fallback.invite.json
# Restart Claude Code after connection.
```

Connection verifies the key and owner, backs up settings using the established
mechanism and preserves model selectors. It makes no provider call or new OAuth
connection. The fallback doctor validates key policy only; it does not prove the
service is reachable or Fusion entitlement. Use the deployment checks separately.

To restore prior local settings:

```sh
./setup fusion fallback-restore
```

Restoration preserves existing backup conflict checks. Local restore does not
revoke the key. Administrators must revoke the dedicated inference key and
remove the three optional aliases to retire this route. Disable fallback before
removing its guard. Keep ordinary onboarding keys and primary routes intact.
To remove the entire optional service, also disable `enable_fusion_fallback`
and `enable_fusion_mcp` in a reviewed plan; retain LiteLLM and PostgreSQL.

See [security](SECURITY.md), [validation](VALIDATION.md) and the
[SDK contract and sources](SDK-MIGRATION.md).
