# Benchmark Infra Follow-up Validation

Status: implementation complete; external-provider acceptance partially blocked.
The six-item handoff remains open for the outstanding real-model scenarios below.

## Approved Contract Changes

- Requirements/constraints installation inputs are rejected in this iteration.
- Docker and host VGB may score with diagnostic degradation when replay locks or
  native-tool fingerprints are unavailable. Invalid inventory, identity,
  interpreter, RECORD evidence, generated locks, or forbidden dependencies reject
  scoring. Evidence completeness and scoring eligibility are separate.

## Implementation Mapping

| Issue | Implementation | Focused coverage |
| --- | --- | --- |
| INFRA-01 | RuntimePathProjection, localized bundle prompts, exact input policies and mounts, image/images argument audit, persisted replay mapping | test_infra_input_projection, test_benchmarking_runtime_bundles, multimodal Docker acceptance |
| INFRA-02 | Environment ownership record, fixed-path cleanup, orphan evidence capture, inactive workspace quarantine | test_agent_workspace, initialized-environment Docker crash recovery |
| INFRA-03 | Schema-2 evidence collector and independent validator, shared Docker/host VGB scoring gate | test_dependency_evidence, test_single_llm_timeout_retry, real dependency installation |
| INFRA-04 | Literal command parsing and dependency classification, explicit indirect-input refusal | test_benchmark_workdir_guard |
| INFRA-05 | Staged runner/orchestration, round-robin attempt executor, deadline retries, persisted scoring input | test_attempt_queue, test_benchmarking_cli, test_benchmarking_orchestration |
| INFRA-06 | Outer Docker timeouts, interruptible grace, cleanup error propagation and scheduling cancellation | test_container_runtime, test_benchmark_cancellation, real timeout/cancel contracts |

## Validation

- Initial targeted baseline: 51 passed, 14 subtests passed.
- Final full suite: `uv run pytest -q` completed with 875 passed, 9 skipped,
  5 pre-existing SWIG warnings, and 160 subtests passed. The nine opt-in Docker
  tests are run separately. Docker timeout-redaction/runtime regressions: 25 passed.
- Rebuilt Docker image: `openclaw-benchmark-single-llm:infra-fix`.
  Verified image ID: `sha256:324d38d06dad05575367213cf8b22bedfa29d7802cd1ad847998dedecb397853`.
  This verified agent image is also tagged `openclaw-benchmark-single-llm:latest`.
  The final host-only cancellation-before-retry change is covered by the full
  suite and `test_cancellation_after_attempt_returns_its_original_error_before_retry`.
- Combined real Docker suites: 9 passed. The first multimodal test
  execution had a JavaScript syntax error in the test fixture; fixed and rerun.
  Failed artifacts remain retained.
- The final-image rerun had 8 passed and one pip-seeding failure caused by a
  PyPI TLS handshake EOF, with cleanup complete. Rerunning that exact installation
  case passed in 3.30 seconds. Thus all nine scenarios have passing evidence for
  the verified image; the transient network failure remains recorded under
  `infra-contract/no-llm/infra-contract-no-llm-20260911T034455439914`.
- Real provider multimodal run:
  `state/benchmark-runs/temporary/infra-multimodal/gpt-5.6-sol/infra-multimodal-gpt-5.6-sol-20260911-01`.
  HLE skills-on/off scored with complete dependency evidence and successful
  archives. Skills-off audit was clean; skills-on had a guard-blocked path event
  and remained scoreable with degradation, without confirmed contamination.
  Both SuperChem attempts read question Markdown and actual image files, then
  timed out at the configured 240-second answer budget. Their archives and
  dependency evidence remain available. A complete SuperChem answer was not obtained.
- SuperChem retry with a 600-second answer budget:
  `state/benchmark-runs/temporary/infra-superchem/gpt-5.6-sol/infra-superchem-gpt-5.6-sol-20260911-02`.
  Both groups failed with `Connection error`. No complete-model acceptance claimed.
- VGB property run:
  `state/benchmark-runs/temporary/infra-vgb/gpt-5.6-sol/infra-vgb-gpt-5.6-sol-20260911-01`.
  Scored 0.9733333333 using pinned 0.9.2 host verifier, complete dependency evidence,
  clean audit, and successful archive. No additional dependency installation occurred.
- VGB RDKit installation/scoring attempt:
  `state/benchmark-runs/temporary/infra-vgb-rdkit/gpt-5.6-sol/infra-vgb-rdkit-gpt-5.6-sol-20260911-01`.
  Provider connection failed before any tools ran. Model-directed installation
  followed by scoring remains unverified; the separate real Docker package
  installation test does not substitute for this scenario.
- No benchmark-labeled containers remained after these runs. Provider failures
  preserved their diagnostic evidence and archived workspaces.
- The minimal attempt-environment fix materializes `.runtime-bin/uv` as a
  fixed-config wrapper. It restores the attempt venv, PyPI index, cutoff, and
  cache after OpenClaw exec environment filtering, while preserving command
  arguments. Regression coverage includes filtered variables and paths with
  spaces.
- Static checks: `ruff check --select F` on changed runtime/workflow modules and
  `git diff --check` passed. Docker timeout diagnostics omit command arguments
  so injected provider environment values are not persisted in errors.

All Docker contract artifacts live under `state/benchmark-runs/temporary/` in
the `infra-contract/no-llm` and `infra-acceptance/no-llm` trees. No formal datasets,
historical scores, release pin, live provider settings, or credentials were changed.

## Remaining Acceptance

- Repeat SuperChem skills-on/off with a working provider and obtain final scored
  answers, retaining actual image-read, audit, dependency, and cleanup evidence.
- Complete a VGB attempt that actually installs a permitted package and then
  scores through the isolated host verifier.
- Do not close the original handoff based only on the automated suites or the
  successful HLE/property examples.
