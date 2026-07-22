# GLOBAL DEV SPEC

This document describes only the current implemented system. Source code and
runtime contracts are the source of truth when they differ from this document.

Maintain this file by updating the relevant existing section in place. Do not
append release history, migration narratives, individual benchmark results,
planned work, or speculative features. Keep volatile details in code, generated
manifests, run artifacts, skill documentation, or the linked specifications and
runbooks.

## 1. Project and Repository Boundaries

### Canonical project root

- `/Users/xutao/.openclaw/workspace` is the Git repository and canonical source
  root.
- The project is a Python 3.12+ workspace managed by `uv`; project commands and
  tests run from this directory with `uv run ...` or its `.venv`.
- Persistent source changes belong under this root. The primary source surfaces
  are `benchmarking/`, `skills/`, `scripts/`, `docs/`, `tests/`,
  `pyproject.toml`, and `uv.lock`.

### OpenClaw runtime home

- `/Users/xutao/.openclaw` is the local OpenClaw runtime home, not the Git
  repository.
- `agents/`, `benchmark/`, `debateclaw/`, `flows/`, `tasks/`, `logs/`,
  `devices/`, `identity/`, and the runtime-home `memory/` contain live runtime
  state, generated workspaces, sessions, databases, or logs. They are not source
  modules.
- `openclaw.json` and `.env` are live local configuration inputs. Benchmark
  launchers read them to produce run-scoped configuration; they are not copied
  into this repository as canonical source configuration.

### Data and generated project state

- `benchmarking.runtime.paths` owns default path resolution.
  `OPENCLAW_PROJECT_ROOT`, `OPENCLAW_DATA_ROOT`, `OPENCLAW_SKILLS_ROOT`, and
  `OPENCLAW_BENCHMARKS_ROOT` provide supported overrides.
- Formal benchmark datasets default to
  `/Users/xutao/.openclaw/data/formal-benchmarks`; temporary datasets default to
  `/Users/xutao/.openclaw/data/temp-benchmarks`.
- Benchmark run records are generated under
  `workspace/state/benchmark-runs/<formal|temporary>/<benchmark>/<model>/<run-id>`.
  Formal and temporary inputs determine the top-level category; benchmark and
  single-LLM model slugs provide the next two levels. Verifier-grounded isolated
  runtimes and dashboard metadata also live under `workspace/state/`.
- Active attempt workspaces default to `.openclaw/benchmark/workspaces`; live
  DebateClaw workspaces default to `.openclaw/debateclaw/workspaces`.

## 2. Module Ownership

### Benchmark package

| Module | Ownership |
| --- | --- |
| `benchmarking/core/` | Dataset normalization, runner/result dataclasses, convergence and answer recovery, result status axes, reporting, and stdout result validation. |
| `benchmarking/scoring/` | Evaluator registry and implementations for ChemBench, FrontierScience, SuperChem, HLE, verifier-grounded tracks, and generic semantic fallback. |
| `benchmarking/runtime/` | Shared path resolution, run-scoped OpenClaw configuration, attempt workspaces and access policy, session isolation, visual input bundles, subprocess environment, cleanroom integration, web-search preflight, and historical adjudication replay. |
| `benchmarking/skills/` | Benchmark skill inventory projection, health checks, fixed skill-script runtime, and post-run tool/skill diagnostics. |
| `benchmarking/workflow/` | CLI, prompts, wave/group orchestration, single-LLM runner, and the ChemQA runner with dedicated artifact and workspace support modules. |
| `benchmarking/analysis/` | Detached post-run evidence bundling and automated analysis reports. |
| `benchmarking/dashboard/` | Local FastAPI dashboard, progress reconciliation, immutable run inspection, asset containment, and dashboard-only annotations. |

`benchmarking.runtime.paths` is the shared path authority used by the package
and scripts. The benchmark CLI is owned directly by `benchmarking.workflow.cli`;
there is no root-level compatibility facade.

### Skill bundles

- `skills/debateclaw-v1/` owns the DebateClaw state machine, preset/run-plan
  compilation, prompt and command materialization, slot provisioning, launch
  wrappers, and one-turn OpenClaw wrapper.
- `skills/chemqa-review/` owns the fixed-lane ChemQA protocol, role driver,
  shared spawn-registry policy, liveness and recovery tools, typed Artifact
  Flow, and terminal artifact reconstruction.
- `skills/benchmark-cleanroom/` owns cleanup manifests, runtime leases, and
  benchmark-owned process termination.
- Chemistry provider skills live as independent bundles under `skills/`.
  `skills/chemistry-routing-matrix.json` is the machine-readable capability and
  exposure inventory; it is not a deterministic router.
- `skills/paper-retrieval/`, `paper-access/`, `paper-parse/`, and
  `paper-rerank/` are independent paper-processing stages.

### Project scripts and resources

- `scripts/run_skill.py` is the fixed entrypoint for benchmark-agent execution
  of local skill scripts through the workspace `uv` environment.
- `scripts/sync_verifier_grounded_datasets.py` validates a pinned release and
  synchronizes public prompt datasets and isolated scoring runtime metadata.
- `scripts/replay_workspace_adjudication.py` replays stored transcript evidence
  without a model call and can apply explicitly approved record-selective
  recovery.
- `scripts/sync_openclaw_qwen_provider.py` updates the live runtime-home Qwen
  provider configuration and removes applicable stale agent provider caches.
- `scripts/docker_services.sh` and `scripts/mineru_service.sh` manage the local
  GROBID and MinerU services used by the paper pipeline.
- `benchmarking/resources/agent-workspace-templates/` contains the canonical
  benchmark workspace base contract and role overlays.
- `benchmarking/resources/verifier_grounded/` contains the pinned release
  identity and sanitized public dataset snapshots.

## 3. Core Execution Flows

### Benchmark CLI

The canonical entrypoint is:

```bash
uv run python -m benchmarking.workflow.cli
```

The implemented default experiment groups are:

- `single_llm_skills_on`: one OpenClaw agent with the health-filtered benchmark
  skill allowlist.
- `single_llm_skills_off`: one OpenClaw agent with an explicit empty skill list.
- `chemqa_skills_on`: the fixed-lane ChemQA workflow with the health-filtered
  benchmark skill allowlist.

All three current group definitions disable generic web search and web fetch.
For each invocation, the CLI:

1. Discovers or accepts JSONL datasets, normalizes them to `BenchmarkRecord`,
   applies record selection, and materializes run-local visual bundles when
   required.
2. Runs skill health checks, filters skills-on allowlists, prepares a unique
   invocation identity, recovers sentinel-proven stale active workspaces, and
   writes run-scoped OpenClaw configs.
3. Dispatches groups in waves. Each record runs through either the single-LLM
   runner or the ChemQA runner, then through the registered evaluator when the
   runner result is scoreable.
4. Persists each record immediately, updates progress artifacts, aggregates only
   `scored=true` records, and writes final results and the runtime manifest.
5. Starts detached automated analysis unless `--no-analysis` is selected.

### Single-LLM runner

- Every primary or timeout-retry attempt receives a fresh sentinel-managed
  workspace and run-scoped session id.
- The runner materializes the role contract, attaches current scratch paths,
  invokes `benchmarking.runtime.single_llm_openclaw_wrapper`, validates OpenClaw
  JSON stdout, and enforces the eval-aware candidate-answer contract.
- Timeout-family failures may create a fresh attempt. Transcript recovery and a
  same-session finalization repair can preserve a complete answer; incomplete or
  unreliable output remains non-scoreable.
- The transcript is audited under the attempt access policy before the complete
  workspace is archived. A `non_evaluable` adjudication or archive failure
  rejects an otherwise complete answer; `scoreable_degraded` preserves it with
  degraded-execution metadata.

### ChemQA runner

- Each attempt prepares one coordinator workspace and five role workspaces as an
  all-or-fail lease set.
- The runner compiles and materializes a `chemqa-review@1` launch, then the role
  drivers advance the DebateClaw SQLite state machine through candidate, review,
  rebuttal, and finalization phases.
- The fixed semantic topology is one candidate owner (`proposer-1`) and four
  reviewer lanes (`proposer-2` through `proposer-5`).
- Stalled status can invoke `recover_run.py`; a new recovery attempt uses a new
  workspace lease set.
- Artifact Flow validates typed protocol artifacts and publishes benchmark
  terminal status only after canonical terminal artifacts are readable.
- Default scoring consumes `final_answer_artifact.json`. Preview text and failure
  projections remain diagnostic. Cleanup, transcript audit, and archive complete
  before the final runner result is accepted.

### Evaluation, reporting, and review

- `benchmarking.scoring.evaluation` dispatches by `eval_kind` with
  `generic_semantic` fallback. LLM-judge calls use a fresh isolated judge session
  and attempt workspace.
- Verifier-grounded tasks use the pinned package through a hash-addressed,
  non-agent virtual environment and `python -I`; agent-visible datasets contain
  public prompts and answer schemas, not hidden verifier material.
- Completed aggregation writes run-local evidence and may launch
  `benchmarking.analysis.automated`. Analysis failure is diagnostic and does not
  change benchmark scoring or the CLI exit outcome.
- The dashboard recursively discovers classified run directories, stops scanning
  below each detected run, and writes only its own annotation SQLite database.
  It does not mutate benchmark results or launch benchmark processes.

### Paper pipeline

Paper processing is an explicit sequence of independent scripts:

```text
retrieval -> access -> parse -> rerank
```

Parsing can use MinerU or PyMuPDF. Reranking consumes local documents, builds
GROBID profiles, and calls an OpenAI-compatible chat-completions endpoint.

## 4. Stable Data and Isolation Contracts

### Runner and result contracts

- Runners return `RunnerResult` with `RunStatus`, `AnswerPayload`, `runner_meta`,
  raw provider data, and optional `FailureInfo` or `RecoveryInfo`.
- `RunnerResult.should_score()` is the gate into evaluator execution. Completed
  results score; recovered results score only when their recovery metadata marks
  them both evaluable and scoreable.
- Current per-record and top-level result writers use schema version `3`.
- Stable result axes are `run_lifecycle_status`,
  `protocol_completion_status`, `answer_availability`, `answer_reliability`,
  `evaluable`, `scored`, `recovery_mode`, `degraded_execution`, and
  `execution_error_kind`.
- `passed` is an evaluator quality outcome, not a runtime-health field.
  Verifier-grounded continuous scores use `passed = null`.
- Aggregate score denominators contain only records with `scored=true`.

The final run artifact set includes:

- `results.json` and `runtime-manifest.json`;
- `per-record/<group>/<record>.json`;
- `progress/events.jsonl` and `progress/state.json`;
- `runtime-config/*.json`, `input-bundles/`, and archived attempt workspaces;
- `skill-health.json` and `web-search-preflight.json`;
- `analysis/` status, evidence, and reports when automated analysis is enabled.

### Attempt workspace contract

- Attempt workspaces use scratch contract version `2` with stable
  `scratch/requests`, `scratch/outputs`, `scratch/notes`, and `scratch/tmp`.
- Structured file tools use workspace-relative `scratch/...` paths. Shell
  commands enter scratch through runner-provided environment variables.
- A canonical base `AGENTS.md` plus a minimal role overlay defines the same
  isolation behavior for single-LLM, judge, and ChemQA roles.
- Immutable `WorkspaceAccessPolicy` objects define normalized read, write, and
  exec-workdir scopes, exact-file scopes, protected roots, and a deterministic
  digest. Skills-off and judge policies do not grant access to the skill source
  tree or `scripts/run_skill.py`.
- The `benchmark-workdir-guard` plugin preflights structured path arguments and
  explicit exec working directories. Transcript audit independently correlates
  tool calls and results and records access mode, outcome, resolved path, policy,
  and matched protected root.

Workspace audit has four independent axes:

- `audit_execution_status`: `complete` or `unavailable`;
- `boundary_status`: `clean`, `warning`, `violated`, or `unknown`;
- `contamination_status`: `clear`, `confirmed`, or `indeterminate`;
- `adjudication`: `scoreable`, `scoreable_degraded`, or `non_evaluable`.

Confirmed or indeterminate external information exposure is `non_evaluable`.
Write-only, blocked, failed, or allowed-fallback boundary events do not by
themselves prove information contamination. A write-only boundary violation can
be `scoreable_degraded`; an allowed fallback is a warning and remains
`scoreable`. Audit evidence recovery is attempted before an unavailable audit is
finalized. Archive failure remains fail closed.

This lifecycle, guard, and transcript audit is not an operating-system security
boundary. Processes still run as the same local user.

### Session, skill, artifact, and cleanup contracts

- Single-LLM and judge calls clear only stale main-session pointers, use explicit
  run-scoped session ids, and verify the requested session and transcript after
  the turn. Historical transcripts remain available for audit.
- Skills-on exposure is the intersection of the inventory allowlist and startup
  health results. Skills-off runner configs contain `skills: []`. Skill choice is
  left to the model; tool and skill diagnostics do not change answer scores.
- Agent-invoked local skill scripts run through `scripts/run_skill.py`, which uses
  the canonical workspace for dependency resolution and the attempt scratch
  directory for relative artifacts.
- ChemQA terminal output is `final_answer_artifact.json` or
  `failure_artifact.json`, accompanied by `artifact_manifest.json`,
  `candidate_view.json`, validation diagnostics, and the compatibility projection
  `qa_result.json`.
- Benchmark cleanroom cleanup terminates benchmark-owned processes from manifests
  and leases. It intentionally retains session stores, transcripts, run artifacts,
  manifests, and archived workspaces for audit.

## 5. Current Risks and Non-goals

### Current risks

- Attempt isolation detects and adjudicates filesystem evidence but cannot prevent
  every same-user filesystem access performed inside arbitrary subprocesses.
- The benchmark CLI still owns argument parsing, wave scheduling, final
  aggregation, runtime-manifest writing, and compatibility facade hooks.
- OpenClaw and ClawTeam integration is subprocess- and file-contract-based;
  correctness depends on session identifiers, manifests, status files, and
  process metadata remaining consistent.
- ChemQA recovery and artifact reconstruction retain compatibility with specific
  protocol filenames and directory layouts.
- The live runtime-home `openclaw.json` is the mutable base for run-scoped configs
  and may contain provider and gateway configuration. It must be treated as local
  operational state.
- Many chemistry skills require optional Python packages, external executables,
  API credentials, network providers, or local GROBID/MinerU services. Startup
  health filtering is the runtime authority for benchmark exposure.

### Non-goals of the current system

- Attempt workspaces are not containers, separate OS users, or syscall sandboxes.
- The benchmark dashboard is a localhost review surface, not a benchmark launcher,
  multi-user service, or authority that rewrites immutable result artifacts.
- Automated post-run analysis is not part of benchmark scoring.
- The chemistry inventory does not prescribe deterministic skill routing.
- The paper stages are not exposed as one transactional orchestration service.
- Benchmark cleanup does not prune retained sessions or historical run artifacts.

## 6. Specification and Runbook Index

### Normative project and benchmark contracts

- `AGENTS.md`: repository workflow, canonical document rule, test and commit
  requirements.
- `docs/superpowers/specs/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md`:
  current attempt behavior, access policy, four-axis adjudication, and historical
  replay contract.
- `docs/superpowers/specs/2026-07-16-benchmark-forbidden-path-root-containment-spec.md`:
  protected-root containment and transcript path evidence.
- `docs/superpowers/specs/2026-07-15-verifier-grounded-openclaw-single-llm-integration-usage-spec.md`:
  verifier-grounded dataset exposure and isolated scoring contract.
- `benchmarking/resources/verifier_grounded/release.json`: current pinned
  verifier-grounded release identity.

### Operational runbooks and component contracts

- `README.md`: verifier-grounded CLI usage and local GROBID/MinerU operations.
- `docs/benchmark-dashboard-usage.md`: dashboard launch, data sources, and review
  workflow.
- `skills/debateclaw-v1/SKILL.md` and `skills/debateclaw-v1/references/`:
  DebateClaw presets, runtime conventions, model/slot mapping, and recovery.
- `skills/chemqa-review/SKILL.md` and
  `skills/chemqa-review/references/contracts.md`: ChemQA fixed-lane runtime and
  artifact contract.
- `skills/benchmark-cleanroom/SKILL.md` and
  `skills/benchmark-cleanroom/references/runtime-surfaces.md`: cleanup manifest,
  lease, and retention contract.
- Each provider skill's `SKILL.md` and optional `references/contracts.md` are the
  authority for that provider's request, dependency, and output contract.
