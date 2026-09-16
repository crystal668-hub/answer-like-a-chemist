# Invocation-owned verifier worker

Date: 2026-09-16
Status: implemented experimentally; disabled by default
Scope: RT-04 second step, based on `24a998b`.

## Contract and decision

`--verifier-mode isolated|worker` defaults to `isolated`. Only scoring uses the
worker; provisioning, describe and public reference APIs keep their existing
one-shot subprocesses. The CLI owns one lazy worker and its cancellation token
and process registry. An outer ExitStack closes it on every exit; scheduling's
finally closes it before final metadata is written. No global worker registry or
cross-invocation process reuse is introduced.

The child runs pinned Python with `-I`, the pinned runtime cwd and existing
allowlisted environment. It executes the same API script in a fresh namespace
per request, loading a track per request; Python's imported modules persist.
Module/native state is therefore a remaining equivalence risk, requiring repeated
and reordered shadow inputs. The experimental path cannot become default based
on synthetic fixtures alone.

## Protocol and bounds

Single-flight JSONL envelopes contain protocol version, monotonically increasing
request ID, release identity and the unchanged action/track/task/answer payload.
Responses must match the request ID and contain an object result or a typed
error. Dedicated protocol stdout is separated from Python/native library output,
which goes to generation-specific stderr evidence. Request/response frames have
a 16 MiB limit; oversized data fails explicitly and is never truncated into a
score. Nonblocking reads and writes share the track deadline and poll cancellation.

The worker is recycled after 100 requests. Two fault restarts per invocation are
allowed for subsequent requests; the failed request is never automatically
replayed. Runtime fingerprint changes stop the loaded process before revalidation
and a new generation. Changed/invalid runtimes cannot execute through a warm
worker. No automatic fallback conceals a worker fault; selecting `isolated`
provides the compatibility path.

## Failure and evidence

Timeout, EOF/crash, invalid envelope/JSON, oversize, startup failure, exhausted
restart budget and closed-worker use are typed bridge failures. The evaluator
continues to treat these as infrastructure failures rather than zero scores.
Cancellation remains `BenchmarkCancelledError`. Library-returned scoring/error
objects remain unchanged. A library exception is a typed worker failure with its
message and traceback retained, not an invented scientific result.

Every stop targets the owned process group, including descendants when the leader
has exited, and reaps the child with bounded TERM/KILL waits. Close is idempotent.
Cleanup failure is recorded and prevents another process from starting. Full
stderr and durable lifecycle JSONL remain on disk; runtime metadata contains
counters and evidence paths, not accumulated answers. Lifecycle events store
answer/request hashes and identities, not another copy of the answer.

## Acceptance

Tests exercise real child processes using a deterministic installed fixture and
compare complete old/new results including failure payloads and evaluator errors.
They cover repeated/reordered requests, process reuse/recycle, request validation,
timeouts, crashes, malformed JSON, frame bounds, cancellation, close, restart
limits, fingerprint changes and invocation isolation. CLI tests prove the default
and lifetime wiring. Offline shadow tooling compares both modes and reports
latency, process count, output digests/bytes and parent/child peak RSS in separate
measurement processes. Pinned-release acceptance is reported separately from
fixture evidence; no model or Docker run is needed for synthetic validation.
