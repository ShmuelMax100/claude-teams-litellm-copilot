# Validation

Run the complete offline suite and generated-package checks:

```sh
python3 -m unittest discover -s tests
python3 tools/build_guide.py
python3 tools/check_package.py
python3 tools/check_public.py
```

Install the service's hash-locked development dependencies in a Python 3.11 virtual environment, then run:

```sh
cd services/fusion-mcp
python -m pytest -q -o faulthandler_timeout=30
```

Terraform checks use mocked providers and make no Azure calls:

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

Build `fusion-mcp:local` and run `python3 tests/integration/run.py`. The integration harness starts a unique Docker Compose project with the pinned LiteLLM image, PostgreSQL and the production Fusion service with only the external SDK client replaced by a fixture. Provider egress is blocked; credentials are synthetic; the fixture project is removed in a finally block. It never reads real user credentials or changes local Claude Code settings.

The harness verifies the first request, concurrent user identity separation, actual Messages streaming before SDK completion, tool round trips, service and authentication errors, expired credentials, spoofed identity/routing attempts, signed private MCP discovery/invocation, cleanup and credential filtering. Wire assertions verify one Fusion invocation per failed request and no client key/PAT leakage. A separate pinned-LiteLLM probe deliberately configures obsolete failover policy and proves the guard still sends only one request to Fusion.

The optional `python3 services/litellm-fusion-gateway/check_client.py` probe runs an installed Claude Code against a loopback mock with a temporary profile and its default tool declarations; it never edits the real profile.

CI additionally runs the real native SDK runtime with network disabled and a local synthetic provider to verify session, delta and declaration-only tool contracts. The production image has no test-mode switch. The local provider used by the runtime probe is a test facility, not a production route.

These tests establish protocol and isolation behavior. They do not establish live Fusion entitlement, output quality, real billing/token limits, cloud deployment success or full Claude Code feature compatibility. Live acceptance requires each user's approved PAT and an explicitly authorized request, followed by a review of local tool effects.

Current execution evidence is recorded in [implementation results](IMPLEMENTATION-RESULTS.md) and `VALIDATION.json`. Old Claude-primary/direct-Copilot pilot scripts and results were removed because they tested an architecture no longer shipped.
