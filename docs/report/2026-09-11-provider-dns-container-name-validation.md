# Provider DNS and Container Name Validation

- Date: 2026-09-11 (Asia/Shanghai)
- Scope: the 21:01 Qwen3.8 Flash basic run, DNS diagnosis, and container naming.
- Status: container naming fixed and verified; DNS availability recovered during
  diagnosis; direct-provider DNS startup checking implemented.
- Baseline: source commit `e2143a1`; Docker image
  `sha256:5b968aebe2b3e5cb2678e9b3a0d3fffd6fc0cc0314b204021ba9c48036a4fa96`.

## Incident Evidence

The run
`verifier-grounded-property-calculation-easy-qwen3-8-flash-20260911-210118`
ran from 21:01:19 to 21:09:31. Its evidence is under
`state/benchmark-runs/formal/verifier-grounded-property-calculation-easy/qwen3-8-flash/`
in the canonical workspace. All ten records were unscored and made zero tool
calls. Nine records exhausted three retries with final
`provider_transport_error` and `getaddrinfo ENOTFOUND agent-team-api.myrimate.cn`.
The other record, skills-off basic 003, failed during Docker creation with a
container-name conflict. Across 37 recorded attempts there were 35 provider
transport errors, one initial agent-response timeout, and one creation conflict.

## DNS Findings

At approximately 21:11, macOS `dns.lookup` and the same image with
`--network host` both returned `ENOTFOUND`. Docker's resolver was
`192.168.65.7`. Direct queries to the configured upstream `202.38.64.18`
resolved the provider's Huawei Cloud WAF CNAME to IPv4 addresses, including from
inside Docker. Docker could also resolve `pypi.org` normally.

By approximately 21:16, macOS default resolution, Docker default resolution,
and both configured upstreams (`202.38.64.18`, `202.38.64.56`) resolved the
provider domain. Unauthenticated direct HTTPS requests to `/v1/models` from
both host and container returned HTTP 401, confirming DNS, TCP, and TLS
availability. No resolver setting, hosts entry, or Docker setting was changed.

This establishes a transient failure in the default resolution path, consistent
with negative caching or forwarding state. The observations do not identify
which cache or upstream response originally caused it. Recovery is observed,
not evidence of a permanent DNS repair. The system HTTP proxy was also enabled
during follow-up, so direct connectivity tests do not validate every proxy path.

The new startup check executes a bounded Node `dns.lookup` in the pinned image
and the attempt network mode. For explicit direct provider endpoints, failure
stops scheduling and persists the hostname, error code, and failed startup
status. It sends no authenticated requests. Providers without an explicit
endpoint and proxy-configured requests are explicitly marked skipped because
their target resolution may be owned by the SDK or remote proxy. This check
does not prevent DNS failures that begin after startup.

## Container Naming Fix

The old name concatenated run, record, retry index, and session, then truncated
the result to 180 characters. The skills-on and skills-off basic 003 attempts
produced exactly the same truncated name. Concurrent creation therefore failed.

Names now append 16 hexadecimal SHA-256 characters computed from the complete
structured attempt identity. The suffix includes invocation, group, agent,
record, retry, session, and template differences and survives prefix truncation.
Labels and workspace sentinels remain the ownership authority; old container
names remain recoverable without a migration.

## Verification

- `uv run pytest tests -q`: 692 passed, 9 skipped, 124 subtests passed.
- Regression coverage checks long names across groups, invocations, agents,
  records, sessions, and retries, plus stable names for identical identities.
- Two real Docker containers using the incident's long run and record names
  were created concurrently, started, and resolved the provider domain. Both
  exited successfully and were removed.
- A real probe against `benchmark-dns-check.invalid` returned `ENOTFOUND`.
- A CLI regression verifies DNS failure is persisted and stops execution before
  workspace recovery, scheduling, and per-record output creation.
- Probe timeout tests verify cleanup and surface cleanup failures.
- The failed formal run was not rewritten or restarted; authenticated model
  execution and scoring were not part of these checks.

The fixes execute on the host; rebuilding the attempt image is not required.
