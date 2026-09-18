# Benchmark Observability and Dashboard v2 Specification

- Date: 2026-09-18
- Scope: active single-LLM VGB runtime and local dashboard
- Status: implemented

## Contract

Current per-record and aggregate results use schema version 5. Each record carries `observability.schema_version=1` with:

- `coverage`: `exact`, `partial`, or `unavailable` for timing, tokens, tools, packages, and resources;
- `totals`: all attempts, retries, reminder turns, rescue turns, backoff, and scoring cost attributable to the record;
- `attempts`: bounded attempt projections and run-relative evidence paths;
- `final_attempt`: the terminal attempt projection.

Attempt summaries live under `observability/attempts/<group>/<record>/attempt-<index>-<session-hash>/`. `summary.json` contains redacted commands and bounded result excerpts; `resources.jsonl` contains the 5-second resource windows. A running attempt has an atomic `observability/active/<attempt-id>.json` sentinel. The resource sampler updates its heartbeat and normal finalization removes it; an old surviving heartbeat is exposed as stale evidence.

Legacy v1-v4 data is never rewritten. Readers synthesize a schema-v1 observability projection with explicit coverage. Missing telemetry is null/unavailable, never a measured zero.

## Metric Semantics

Python environment evidence has three snapshots: seeded baseline, post-agent/pre-remediation inventory, and effective post-remediation inventory. Dependency manifest v3 preserves `distributions` as pre-remediation security evidence and adds the other snapshots plus added, removed, version-changed, and policy-removed rows. Successful literal `uv pip install` requests identify direct packages; other added distributions are transitive.

The shared transcript tool parser correlates calls and results by call ID, with ordered tool-name fallback for historical transcripts and terminal `process` correlation for background exec. Canonical status precedence is guard block, structured timeout/cancellation, `isError` or nonzero exit, structured failure, pending/incomplete, then success. Every non-success state contributes to the unified failure count. Commands, cwd, and result excerpts are path/secret redacted; hashes bind them to retained raw transcript evidence.

Token totals prefer the final `trace.artifacts.data.usage` for every lifecycle invocation. Assistant-message usage is the partial fallback. Input, output, cache read, and cache write form the total; reasoning tokens are reported separately and not added again. Provider-reported totals are retained and checked.

Record wall time uses a monotonic clock from record execution start through scoring completion. Attempt wall time, agent-reported duration, retry backoff, scoring wait, scoring duration, and residual overhead remain distinct.

One owned `docker stats` stream runs per Docker attempt. Raw Docker samples are normalized and folded into 5-second windows retaining CPU average/peak, memory average/peak/limit, cumulative network and block I/O, and PID last/peak. The reader is stopped and reaped on success, failure, timeout, cancellation, and forced cleanup. Sampling failure changes only telemetry coverage.

## Dashboard Boundary

The FastAPI service remains read-only for benchmark artifacts. It exposes compact observability in run and record responses, `/monitor` for progress and active heartbeats, and an identity-derived resource endpoint. The resource endpoint rejects symlinks and path escape, caps responses at 2,000 points, and preserves endpoints and global CPU/memory peaks during downsampling.

The browser is a dense monitoring console with run comparison, active attempts, sortable records, group selection, and Overview, Timeline, Exec, Packages, Tokens, Resources, and Evidence tabs. It polls monitor data every five seconds. Annotation writes remain isolated in the dashboard SQLite store.
