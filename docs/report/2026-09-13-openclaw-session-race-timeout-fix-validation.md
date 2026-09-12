# OpenClaw Session Race and Timeout Retry Fix Validation

Date: 2026-09-13

Status: implementation and required acceptance complete

Scope: single-LLM attempt outcome precedence, timeout retry semantics, OpenClaw
session ownership/lifecycle evidence, takeover classification, provider timeout
observability, and Docker evidence propagation.

## Evidence Baseline

The diagnosis baseline remains the formal Qwen3.8 Flash run documented in
[`2026-09-13-openclaw-session-race-timeout-fix-handoff.md`](../handoff/2026-09-13-openclaw-session-race-timeout-fix-handoff.md).
It supplies the real completed-answer-plus-historical-timeout case and three
original `EmbeddedAttemptSessionTakeoverError` stderr records. Tests use reduced
fixtures without prompts, credentials, or unrelated run data.

The post-fix real-model run is:

`state/benchmark-runs/temporary/verifier-grounded-property-calculation-easy/qwen3-8-flash/verifier-grounded-property-calculation-easy-qwen3-8-flash-20260913-013152`

It used one ethanol property-calculation record, Qwen3.8 Flash, skills-on and
skills-off, one concurrent attempt, a 900-second budget, no benchmark retries,
and no detached analysis.

## Implemented Contracts

- `benchmarking.core.attempt_outcome` makes complete native, transcript, or
  rescue answers terminal before current-failure retry policy is considered.
  Historical prompt errors remain diagnostics only.
- Attempt history separates `current_failure_code`,
  `historical_prompt_errors`, `answer_source`, and current retryability.
- The wrapper owns an exclusive per-session mutex, records an append-only event
  journal plus atomic lifecycle summary, uses new session files for follow-up
  turns, and freezes transcripts before releasing ownership.
- `openclaw_session_takeover` is a stable, non-retryable session-layer error that
  preserves its original lines, path, lanes, and process code. A complete
  transcript may still recover the answer without erasing this diagnostic.
- Docker runner metadata links container identity, return code, stdout/stderr,
  session lifecycle, cleanup, and frozen session evidence. Process exit zero is
  not treated as an answer-success signal.
- Provider terminal evidence distinguishes first-token, stream-gap, HTTP, and
  OpenClaw watchdog timeout categories when the available trajectory permits it.

## Verification

- Focused regression and contract suite: 122 passed, 4 subtests passed.
- Existing timeout/workspace regression file: 20 passed after adding native and
  recovered historical-timeout no-retry cases plus terminal takeover coverage.
- Full suite: 927 passed, 9 skipped, 164 subtests passed, with five existing
  SWIG deprecation warnings.
- Static checks: new modules passed complete Ruff checks; changed modules passed
  `ruff check --select F`; `git diff --check` passed.
- Rebuilt `openclaw-benchmark-single-llm:latest` from the implementation
  checkout, pinned to OpenClaw 2026.6.9. The final image digest is
  `sha256:ba72e64e150da19d3081ed7c91e153bff1f8f5c8aa95253f1d640374f31dc12c`.
- Real no-model Docker suite:
  `BENCHMARK_TEST_CONTAINER_IMAGE=openclaw-benchmark-single-llm:latest uv run pytest -q tests/test_container_attempt_integration.py`
  passed 4 cases covering install, outer timeout, cancellation, orphan recovery,
  evidence finalization, and cleanup.
- The real Qwen skills-on attempt completed natively, scored
  `0.9266666666666667`, used one wrapper/OpenClaw PID pair, recorded no takeover,
  passed session/workspace isolation, froze its transcript, did not retry, and
  removed its container.
- The real Qwen skills-off attempt produced no complete answer and ended with
  `idleTimedOut=true` plus `LLM idle timeout (120s): no response from model`.
  Docker return code was zero; the runner nevertheless produced
  `agent_response_timeout`, `answer_source=none`, and a retryable current outcome.
  The configured retry count was zero, so it retained one failed attempt. Its
  session/workspace audit passed and its container was removed.
- Takeover injection verifies terminal classification without retry; a separate
  recovery test verifies that an already-complete transcript wins while the
  typed takeover diagnostic remains attached.

No benchmark containers remained after acceptance. All recorded lifecycle
mutexes were unlocked and their retained files contained no owner token.

## Verification Limits

OpenClaw 2026.6.9 trajectories expose request submission and terminal model
events, but not actual first/last response-chunk timestamps. The implementation
therefore leaves those timestamps empty and does not infer them from
`model.completed`, which is emitted for both success and idle abort. The wrapper
can prevent multiple benchmark wrapper owners and isolate follow-up session
files; OpenClaw's own embedded threads remain governed by its internal session
write lock and fence. Any internal fence failure is preserved as a typed
takeover rather than suppressed.

The post-fix real run naturally covered success, skills-on/off, and a real
provider idle timeout. Historical-timeout-after-success and session takeover are
deterministic regression fixtures backed by the formal baseline evidence; the
acceptance run did not try to force either fault against the live provider.
