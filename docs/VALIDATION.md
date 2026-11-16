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
