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

The implementation began with **44 passing existing unit tests** plus successful
guide/package/public-content checks. This is separate from the historical live
provider results above. Azure was not deployed and no live Fusion request was
sent. Official CLI 1.0.114 was verified locally and lacks `--fusion-tier`; the
service correctly refuses that unsupported capability.

Final local results: **84 repository tests**, **26 service tests**, **8 mocked
Terraform plans** and **10 real-gateway integration groups** passed. Both ARM64
and AMD64 Docker builds passed. Independent security, Azure and functional
reviews approved after fixes. See [the implementation report](IMPLEMENTATION-RESULTS.md)
for file inventory, limitations and remaining manual steps.

Run the new service tests in an isolated Python 3.11+ environment:

```sh
python3 -m venv /tmp/fusion-checks
/tmp/fusion-checks/bin/python -m pip install --require-hashes \
  -r services/fusion-mcp/requirements-dev.lock
/tmp/fusion-checks/bin/python -m pytest services/fusion-mcp/tests -q
python3 -m unittest discover -s tests
docker build -t fusion-mcp:review services/fusion-mcp
python3 tests/integration/run.py --image fusion-mcp:review
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

These tests mock provider execution. Do not provide Azure credentials or real
GitHub tokens to ordinary unit/CI checks. The integration fixture uses a fresh
local PostgreSQL instance and the pinned official LiteLLM image, with a mock CLI
behind the actual MCP service. It must never reuse `private/pilot/`, a live
OAuth connection or a developer's normal Claude configuration.

### Operator acceptance after an approved deployment

| Check | Expected evidence | Model usage |
|---|---|---|
| Gateway health and DB | Readiness reports healthy and database connected | None |
| Dynamic model storage | Administrator preparation is repeatable; routes survive restart | None |
| Existing developer onboarding | Same owner/key/connection/settings behavior as before | Existing explicit live checks only |
| MCP initialization/discovery | Dedicated owner's key lists exactly the authorized Fusion tools | None |
| Negative authorization | Anonymous, inference-only and ungranted clients cannot invoke Fusion | None |
| CLI capability | Required Fusion and restrictive flags verified locally | None |
| Standard execution | Opt-in synthetic prompt succeeds; owner/tier/status recorded | Yes, only after approval |
| Max execution | Same, only when configured and permitted by policy | Yes, only after approval |
| Failure isolation | Timeout/provider failure yields bounded error, no fallback or credential reuse | Mock first |
| Disable/uninstall | Optional resources/entry removed; inference and database retained | None |

Use `./setup azure validate --gateway https://gateway.example` for read-only
readiness/database/model-registry checks. Use `./setup fusion doctor` or
`./setup doctor --fusion` for MCP discovery without model usage. Default legacy
doctor behavior remains unchanged and includes its existing live checks.

Only after resolving CLI support and enterprise entitlement, an operator may
explicitly opt into a synthetic live call:

```sh
./setup fusion run --invite /secure/fusion.invite.json --tier standard \
  --prompt-file /secure/approved-synthetic-prompt.txt --allow-live-usage
```

Use `--tier max` only after separate policy/usage approval. Do not use customer
source code for initial validation. A discovery result or model label does not
prove specific internal model selection or multi-user identity isolation.
