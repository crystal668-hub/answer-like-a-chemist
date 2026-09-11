# Benchmark Infra Follow-up Validation

Status: implementation complete; required acceptance complete.
Historical provider failures remain retained as failed evidence and are not used
as passing substitutes.

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
- Final full suite: `uv run pytest -q` completed with 890 passed, 9 skipped,
  5 pre-existing SWIG warnings, and 164 subtests passed. The nine opt-in Docker
  tests are run separately. Docker timeout-redaction/runtime regressions: 25 passed.
- Rebuilt Docker image from the current service-directory migration checkout:
  `openclaw-benchmark-single-llm:latest` (manifest digest
  `sha256:5b968aebe2b3e5cb2678e9b3a0d3fffd6fc0cc0314b204021ba9c48036a4fa96`).
  The image contains `benchmarking/service/single/openclaw_wrapper.py` and
  `uv 0.8.17`; this is the image used for all acceptance below.
  The final host-only cancellation-before-retry change is covered by the full
  suite and `test_cancellation_after_attempt_returns_its_original_error_before_retry`.
- Combined real Docker suites: 9 passed. The first multimodal test
  execution had a JavaScript syntax error in the test fixture; fixed and rerun.
  Failed artifacts remain retained.
- The current-checkout `latest` image rerun of
  `tests/test_container_attempt_integration.py` and
  `tests/test_infra_docker_acceptance.py` passed all nine cases in 27.03 seconds.
  An earlier image iteration produced a timeout-case partial dependency manifest
  because its replay lock was temporarily unavailable; that failed artifact is
  retained under `infra-contract-no-llm-20260911T122638248365` and its exact
  retry passed in 9.72 seconds.
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
- SuperChem real-model acceptance:
  `state/benchmark-runs/temporary/infra-superchem/gpt-6-astra/infra-superchem-gpt-6-astra-20260911-01`.
  Both skills-off and skills-on Docker records completed with actual question/image
  reads, clean archives, and pinned verifier results (score `0.0` for each).
  Earlier gpt-5.6-sol connection/timeout runs remain retained as failed evidence.
- VGB RDKit installation/scoring acceptance:
  `state/benchmark-runs/temporary/infra-vgb-rdkit/gpt-5.6-sol/infra-vgb-rdkit-gpt-5.6-sol-20260911-1250`.
  The model executed `uv pip install rdkit` from `$BENCHMARK_SKILL_SCRATCH_DIR`,
  then used `/benchmark/workspace/scratch/venv/bin/python` to import RDKit and
  solve the task. The archived schema-2 dependency manifest reports
  `dependency_evidence.status=complete`, with freeze, inventory, RECORD hashes,
  replay lock, and dependency audit all complete. The host pinned verifier scored
  `0.9484085646807822`; the attempt was archived and its container removed.
  Freeze and inventory contain `rdkit==2026.3.6`, `numpy==2.5.3`, and
  `pillow==12.3.0`, each with RECORD evidence; the model also installed
  `selfies==2.2.0`. Earlier `...-1139` and `...-1142`
  runs exposed, respectively, a provider overload and a login-shell PATH defect;
  both are retained as failed evidence and neither is counted as acceptance.
- INFRA-05 real scheduler evidence:
  `state/benchmark-runs/temporary/infra-throughput/no-llm/infra-throughput-no-llm-20260911-1150`.
  The throughput scenario ran six records across two groups with two attempt
  workers, round-robin starts, a slow score, and two retry backoffs; attempt
  execution continued while scoring was active and attempt artifacts were
  persisted before the run ended. The cancellation scenario triggered a
  synthetic SIGINT during active attempts and recorded zero post-cancel starts.
  `summary.json` and `events.jsonl` contain the timing and persistence evidence.
- No benchmark-labeled containers remained after these runs. Provider failures
  preserved their diagnostic evidence and archived workspaces.
- The minimal attempt-environment fix materializes `.runtime-bin/uv` as a
  fixed-config wrapper. It restores the attempt venv, PyPI index, cutoff, and
  cache after OpenClaw exec environment filtering, while preserving command
  arguments. Container configs use OpenClaw's `tools.exec.pathPrepend` so login
  shells resolve that wrapper after resetting PATH. Dependency install audit also
  follows background exec session IDs to their final process results instead of
  treating `Command still running` as success. Regression coverage includes
  filtered variables, paths with spaces, and asynchronous failure attribution.
- Static checks: `ruff check --select F` on changed runtime/workflow modules and
  `git diff --check` passed. Docker timeout diagnostics omit command arguments
  so injected provider environment values are not persisted in errors.

All Docker contract artifacts live under `state/benchmark-runs/temporary/` in
the `infra-contract/no-llm` and `infra-acceptance/no-llm` trees. No formal datasets,
historical scores, release pin, live provider settings, or credentials were changed.

## Closure

The required real-model, latest-image Docker contract, and INFRA-05 scheduler
acceptance are complete. No benchmark-labeled containers or active workspace
locks from these acceptance runs remain. Historical runtime directories and
their retained evidence are unchanged. Provider, PyPI, and image-registry failures that
occurred during earlier attempts remain preserved in their original run trees;
they are explicitly not reclassified as passes.
