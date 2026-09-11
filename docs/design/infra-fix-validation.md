# Benchmark Infra Fix Validation

Date: 2026-09-10
Branch: `feat/benchmark-single-llm-containerization`

Scope correction (2026-09-11): the passing checks below establish the tested
paths only, not completion of the entire migration. The follow-up review found
six open issues in multimodal paths, crash recovery, dependency evidence and
policy, attempt scheduling, and cancellation. See
[the open-issues handoff](2026-09-11-benchmark-infra-open-issues-handoff.md).
The earlier run outcomes remain valid evidence within their tested scope.

## Implemented Contracts

- Removed the skill-health implementation, compatibility imports, pass-through
  arguments, and writer fields. Skill routing and usage diagnostics remain;
  new diagnostics use `configured_skills` and `configured_skill_count`.
- All Docker records use a container-created scratch venv, shared installation
  policy, run-start cutoff, and container-generated dependency evidence. Host
  VGB retains its existing environment lifecycle. Interpreter symlinks retain
  their venv identity when constructing the OpenClaw environment.
- Admission is a cancellation-aware FIFO count limit, defaulting to 2.
  Single-LLM records share a queue across groups; each has its own runtime
  config and agent/workspace identity. Retries reacquire admission. ChemQA
  retains group waves. Docker is the internal and CLI default.
- Startup checks Docker and pins an immutable image ID. Orphan recovery verifies
  complete identity, the workspace sentinel, local host, and dead owner PID.
  Unverifiable or live containers are retained. Startup evidence is persisted.
- Container cancellation stops the agent before dependency collection; generated
  plugin-skill links are removed with evidence before normal archive validation.
  Transcript paths are projected in memory for audit, including failure paths.
- Container environment inspection values are redacted. Runtime environment
  values override `.env`; environment SecretRefs do not require host gateway
  resolution. The project now uses `python-dotenv` for this input.

## Verification

Baseline: `833 passed, 5 warnings, 139 subtests passed`.

Final full suite (`uv run pytest -q`):
`841 passed, 4 skipped, 5 warnings, 143 subtests passed`.
The four skips are opt-in Docker integration checks. They were also executed
against the final rebuilt image:

```sh
BENCHMARK_TEST_CONTAINER_IMAGE=openclaw-benchmark-single-llm:infra-fix uv run pytest -q tests/test_container_attempt_integration.py
```

Result: `4 passed`. Coverage includes registry installation and exact freeze
comparison, hashed replay requirements, timeout, cancellation, environment
cleanup, stale-container recovery, and retention of a live owner container.
Additional regression tests cover prohibited installs, read-only skill mounts,
invalid images, daemon failure, identity mismatch, concurrent record configs,
retry lease acquisition, and raw-transcript-preserving path projection.

## Real Model Runs

Artifacts are under `state/benchmark-runs/temporary/infra-fix/gpt-5.6-sol/`.
All runs used `openai/gpt-5.6-sol`; run suffixes below identify individual
verification executions, not changes to formal datasets or scores.

| Run | Outcome |
| --- | --- |
| `infra-fix-gpt-5.6-sol-20260910-02`, skills-off | Completed, scored 1.0, session/audit/archive passed. |
| `infra-fix-gpt-5.6-sol-20260910-04`, skills-on | Completed, scored 1.0, session/audit/archive passed; skill source mounted read-only. |
| `infra-fix-vgb-gpt-5.6-sol-20260910-05`, RDKit skills-on | Installed RDKit, NumPy, Pillow, and selfies in the container venv and ran computation; subsequent provider HTTP 400 prevented completion. Failure and dependency evidence retained. |
| `infra-fix-vgb-gpt-5.6-sol-20260910-07`, property skills-off, thinking off | Completed, evaluable, scored approximately 0.67 by the pinned host verifier; session/audit/archive passed. |

Earlier failed verification runs remain available as diagnostic evidence; they
are not counted as successful model runs. The runtime `.env` contains two
non-assignment statements reported by python-dotenv; valid assignments load
successfully, and the live file was not changed.

Final Docker integration artifacts are under
`state/benchmark-runs/temporary/infra-contract/no-llm/`.
Successful model archives retain dependency manifests and replay evidence but
no venv/cache. All verification containers were removed; no containers bearing
`benchmark.run_id` remained after verification.

The VGB release pin, dataset contents, evaluator formula, ChemQA, judge,
cleanroom, and historical result readers were not replaced. Current behavior
is documented in `GLOBAL_DEV_SPEC.md`.
