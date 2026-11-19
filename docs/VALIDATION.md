# Verify your deployment

Run setup commands from the folder containing `setup`. Administrator and developer steps use different accounts and should be followed by their respective owners. Never put an invitation or live credential in this repository.

## Administrator

1. Run `python3 --version`. Expect Python 3.10 or newer.
2. Run `./setup admin` and select **Prepare customer gateway**. Enter the gateway HTTPS origin and administrator key when prompted. Review the changes before applying them. Expect **Gateway ready**.
3. Repeat preparation with the same model options. Expect matching resources to be reused. A conflicting route must stop setup for review.
4. Select **Invite a new developer**. Provide their work email and exact GitHub username, choose the model policy, and save a private `.invite.json`. Expect **Invitation ready** and **No email was sent**.
   Follow the [invitation example](INVITATION-EXAMPLE.txt) using your own gateway and developer identities. Verify file permissions are 600, the key belongs to that developer, and its fallback policy matches the reviewed selection. Reusing the file must preserve the key and expiry.
5. Share the package and that invitation through your approved private channel. The email labels a user record; it does not verify a mailbox or send a message.

## Developer

1. Run `python3 --version` and `claude --version`. Expect both tools to be installed. Compatibility was checked with Claude Code 2.1.317.
2. Run `./setup developer` and select **Join with my invitation**. Provide the private file, review the gateway and expected account, and complete the sign-ins. Expect model checks to pass and **READY** to appear.
3. Restart existing Claude sessions and run `claude` normally. Ask it to reply with `MY_GATEWAY_TEST`. Ask the administrator to confirm the request reached the intended gateway.
4. Run `./setup doctor`. Expect connection checks to pass. Run `./setup models` to see your key's live policy; before joining, this command explicitly shows starter defaults.
5. In Claude Code, use `/model` to choose each entitled primary family. Direct backup aliases include `/model claude-copilot-sonnet`, `/model claude-copilot-haiku`, `/model claude-copilot-sonnet-5`, and `/model claude-copilot-haiku-4-5`.

## Verify real fallback and tools

A direct Copilot response proves access to that provider, not fallback. When a real eligible primary failure occurs, inspect LiteLLM Logs: the request should succeed using the configured Copilot model and record a positive `attempted_fallbacks` value. A 429 can indicate a short-term rate limit; it is not by itself proof of subscription quota exhaustion.

Use a harmless sample file to test Claude Code's Read tool on both routes. Then test the other tools your developers need. Native WebSearch previously failed on Copilot; do not assume that model-name parity implies tool parity. Optional search configuration is separate.

Do not deliberately exhaust a user's subscription as a routine onboarding check. The aggregate validation includes prior genuine quota-triggered fallback across Sonnet, Opus and Haiku. Raw request IDs, prompts, account records and detailed execution traces are intentionally excluded from this public package.

## Restore, renewal and offboarding

Test **Developer → Restore previous settings** in a disposable profile. It should restore the saved configuration and refuse to overwrite later edits. Restoring settings does not revoke the key. The administrator owns key renewal, revocation and provider-access review.

## Invitation walkthrough recheck · 16 November 2026

The documented `./setup admin` menu flow was rerun against a disposable LiteLLM 1.108.0 gateway: prepare routes, create an invitation using the recommended policy, and validate it through the developer code. File mode 600, 90-day expiry, key ownership and saved fallback mappings passed. Repeating preparation and invitation creation preserved the routes, key and expiry. Developer settings configuration and restore passed in an isolated profile. All 17 automated tests and package checks passed.

This documentation recheck made no provider inference requests and did not repeat browser consent or the earlier real-quota fallback test.

## Offline checks for maintainers

With Python 3.10+ and Node.js installed:

```sh
python3 -m unittest discover -s tests
python3 tools/build_guide.py
python3 tools/check_package.py
python3 tools/check_public.py
```

These checks use temporary profiles and placeholder credentials. They do not consume provider quota. Browser consent, customer networking, organization policies, model entitlements and live provider behavior require the deployment checks above.

## Copilot-only pilot · 18 November 2026

See [the direct-mode guide](COPILOT-ONLY.md) and [the structured results](../COPILOT-VALIDATION.json). The latter distinguishes unit coverage, controlled gateway tests and verified live Copilot checks with documented limitations. The original validation above describes the legacy workflow.

## Optional Azure / Fusion verification · 19 November 2026

The Azure/Fusion implementation began with 44 existing unit tests. Before the
SDK migration, 84 repository tests and 26 CLI-era service tests passed. These
local results are separate from the historical live provider evidence above.
No Azure infrastructure was deployed and no live Fusion request was sent.

Current evidence and final counts are maintained in
[IMPLEMENTATION-RESULTS.md](IMPLEMENTATION-RESULTS.md). Tests cover the real pinned
LiteLLM image and fresh PostgreSQL, with only provider/SDK execution replaced by
synthetic fixtures. Provider egress is blocked. Separate offline checks start the
actual Copilot SDK/native runtime on Linux ARM64 and AMD64 and exercise its
contract against a local mock provider. They prove local SDK behavior, not live
Fusion entitlement, model selection, billing or routing quality.

### Reproduce local checks

```sh
python3 -m unittest discover -s tests
python3 -m venv /tmp/fusion-checks
/tmp/fusion-checks/bin/python -m pip install --require-hashes \
  -r services/fusion-mcp/requirements-dev.lock
/tmp/fusion-checks/bin/python -m pytest services/fusion-mcp/tests -q
docker build --platform linux/amd64 -t fusion-mcp:review services/fusion-mcp
python3 tests/integration/run.py --image fusion-mcp:review
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
python3 tools/build_guide.py
python3 tools/check_package.py
python3 tools/check_public.py
```

The container builds the SDK from checksum-pinned official release source when
the wheel CDN is unavailable; provenance is retained in the image. Test dependency
installation above uses the published wheel pin. A TLS/download failure must be
fixed through an approved package source, never by disabling verification.

CI also runs the real SDK offline startup and the pinned LiteLLM's nine-case
fallback wire test with Docker networking disabled. Integration checks authorized
and denied keys, persistence across gateway restart, SDK cancellation/deletion,
credential non-forwarding, and the context/tool handoff. Independent hostile
schema tests verify bounded rejection without blocking health or later requests.
Do not supply real Azure/GitHub credentials to ordinary tests, reuse pilot
storage, or point fixtures at a normal Claude profile.

### Operator acceptance after an approved deployment

| Check | Expected evidence | Model usage |
|---|---|---|
| Gateway health and DB | Healthy readiness and connected persistent database | None |
| Dynamic storage | Preparation is repeatable; routes/grants survive restart | None |
| Existing onboarding | Same identity, connection and settings behavior as before | Existing explicit live checks only |
| SDK readiness | Correct local SDK/runtime versions and healthy protocol | None |
| MCP discovery | Owner's dedicated key lists exactly the authorized tools | None |
| Negative authorization | Anonymous, inference-only and ungranted clients cannot invoke MCP | None |
| Fallback enrollment | Matching active guard, owner and restricted key policy | None |
| Primary success | Ordinary Claude result, no Fusion session | Approved synthetic call |
| Eligible fallback | Temporary primary failure starts one new Fusion task; context is preserved as data | Mock first; live separately approved |
| Client tool round trip | Tool request returned to Claude Code, actual result carried in next context, no server side effects | Mock first; live separately approved |
| Ineligible failure | Authentication/policy/input/partial-stream failure never switches to Fusion | Mock |
| Standard/High/Max | Synthetic request succeeds only for configured/entitled tiers | Yes, only after approval |
| Runtime failure | Readiness/liveness fail after local runtime loss and ACA restarts it | No model usage needed |
| Rollback | Optional keys/routes/config removed; ordinary inference and database retained | None |

Use `./setup azure validate --gateway https://gateway.example` for read-only
readiness/database/model-registry checks, and `./setup doctor --fusion` for MCP
discovery. `./setup fusion fallback-doctor --invite PATH` checks the owner key
policy, not live service access. Default legacy doctor behavior retains its
existing live checks.

After account policy and deployment readiness are confirmed, an operator may
explicitly opt into a synthetic live MCP call:

```sh
./setup fusion run --invite /secure/fusion.invite.json --tier standard \
  --prompt-file /secure/approved-synthetic-prompt.txt --allow-live-usage
```

Higher tiers need applicable policy/usage approval. Use synthetic data for first
acceptance. Discovery, a model label, or local runtime readiness does not prove
particular internal model selection or multi-user identity isolation.
