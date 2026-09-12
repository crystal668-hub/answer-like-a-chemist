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
- Maintained documentation under `docs/` is tracked source content, organized by
  purpose into `design/`, `plan/`, `handoff/`, `report/`, `guide/`, and `research/`.
  `docs/AGENTS.md` defines classification and maintenance rules;
  `docs/README.md` indexes the complete collection. Runtime artifacts remain
  under `state/` and local metadata remains subject to repository ignore rules.

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
- Explicitly retained fixed-workspace evidence lives under
  `workspace/state/benchmark-runs/legacy-workspace-archives/<workspace>-<timestamp>`.
  These snapshots are maintenance artifacts, not classified benchmark runs or
  attempt workspace archives.
- Active attempt workspaces default to `.openclaw/benchmark/workspaces`; live
  DebateClaw workspaces default to `.openclaw/debateclaw/workspaces`.

## 2. Module Ownership

### Benchmark package

| Module | Ownership |
| --- | --- |
| `benchmarking/core/` | Dataset normalization, runner/result dataclasses, convergence and answer recovery, stateless answer/agent-response processing, result status axes, reporting, and stdout result validation. |
| `benchmarking/scoring/` | Evaluator registry plus per-track implementations and result/error contracts for ChemBench, FrontierScience, SuperChem, HLE, verifier-grounded tracks, and generic semantic fallback. |
| `benchmarking/runtime/` | Shared path resolution, run-scoped OpenClaw configuration, attempt workspace lifecycle, access policy and adjudication, transcript audit and typed recovery, structured execution-error capture, cancellation and owned process groups, session isolation, visual input bundles, subprocess execution utilities, Docker attempt runtime primitives, attempt concurrency admission, judge execution, verifier-grounded isolation, cleanroom integration, web-search preflight, historical adjudication replay, and verified legacy-workspace evidence archival. |
| `benchmarking/skills/` | Matrix-backed benchmark skill inventory/routing projection, derived skills-on presentation tree, fixed skill-script runtime, and post-run tool/skill diagnostics. Startup health checks are not used to filter benchmark skill exposure. |
| `benchmarking/workflow/` | CLI entrypoint and top-level scheduling, experiment definitions, dataset selection, persisted run state, shared result orchestration and lazy runner selection; business implementations live in `benchmarking/service/single/` and `benchmarking/service/chemdebate/`. |
| `benchmarking/analysis/` | Detached post-run evidence bundling and automated analysis reports. |
| `benchmarking/dashboard/` | Local FastAPI dashboard, progress reconciliation, immutable run inspection, asset containment, dashboard-only annotations, and synchronized dataset/subset facets across filters, run summaries, and record details. |

`benchmarking.runtime.paths` is the shared path authority used by the package
and scripts. The benchmark CLI is owned directly by `benchmarking.workflow.cli`;
there is no root-level compatibility facade.

Attempt workspace responsibilities are split by dependency direction:
`benchmarking.runtime.workspace_policy` owns immutable access policy and audit
adjudication, `benchmarking.runtime.workspace_audit` owns transcript and path
evidence parsing, and `benchmarking.runtime.agent_workspace` owns workspace
templates, leases, recovery, sealing, quarantine, and audit orchestration.

Benchmark workflow responsibilities follow the same ownership rule:
`benchmarking.workflow.experiments` owns group definitions and effective specs,
`benchmarking.workflow.dataset_selection` owns discovery, filtering, sampling,
and output-root classification, `benchmarking.workflow.run_state` owns persisted
results and run metadata, and `benchmarking.workflow.runner_adapters` lazily selects business runners.
`benchmarking.service.single` owns active experiments, prompts, runner assembly,
per-record configuration, and the OpenClaw wrapper.
`benchmarking.service.chemdebate` owns frozen ChemQA experiments, prompts,
protocol status, artifact reconstruction, slot provisioning, cleanroom binding,
and role templates. Shared record orchestration receives a runner-options
factory; the configuration pool receives a business configuration builder.
The two business modules do not import each other.
`benchmarking.runtime.subprocess_utils` owns shared subprocess and stdout helpers,
`benchmarking.runtime.error_capture` owns execution-error evidence extraction,
provider/config error classification, and preservation of original upstream
status codes, error codes, messages, and matched log events,
`benchmarking.runtime.cancellation` owns run cancellation tokens, reasons, and
owned process-group termination,
`benchmarking.runtime.attempt_finalization` owns atomic attempt evidence I/O,
environment ownership registration, and bounded-path cleanup;
`benchmarking.runtime.dependency_evidence` validates dependency evidence independently
of collector status. `benchmarking.workflow.attempt_queue` owns staged synchronous
execution and the shared attempt/retry/scoring scheduler,
`benchmarking.runtime.judge` owns judge execution and isolation, and
`benchmarking.runtime.vgb_bridge` owns the pinned verifier-grounded release,
isolated process bridge, and public package API calls. The scoring evaluator
only maps benchmark records and verifier results.
`benchmarking.service.chemdebate.cleanroom.CleanroomRuntime` is the cleanroom dependency
binding. `benchmarking.workflow.cli` does not re-export these component APIs.

Scoring responsibilities are split by dependency direction:
`benchmarking.core.answer_processing` owns answer-track normalization and pure
agent-response JSON extraction, `benchmarking.scoring.registry` owns evaluator
registration and dispatch, and `benchmarking.scoring.evaluators/` owns only
benchmark-specific scoring strategies. `benchmarking.scoring.results` owns the
stable `EvaluationResult` shape and execution-error construction;
`benchmarking.scoring.errors` owns scoring and registry exceptions.

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
  `skills/chemistry-routing-matrix.json` is the sole machine-readable source
  for skill IDs, capability metadata, single-agent exposure, and skills-on
  display taxonomy. Matrix version 3 entries include display order, domain, and
  family metadata. `benchmarking.skills.tree` derives the compatibility tree,
  family lookup, and compact prompt catalog from that matrix; it contains no
  hard-coded skill list. The matrix is projected directly for skills-on runs
  and is not health-filtered or a deterministic router.
- The RDKit skill exposes neutral, explicit conformer force-field selection:
  its generic conformer entrypoint requires `MMFF` or `UFF`, and dedicated MMFF
  and UFF scripts implement each family without cross-family fallback. Every
  conformer request also requires explicit `num_conformers` and `random_seed`
  values; the skill defines no sampling defaults or preferred values.
- `skills/paper-retrieval/`, `paper-access/`, and `paper-parse/` are independent
  paper-processing stages.

ChemQA runtime checks expose a redacted process-environment report for the
MinerU API settings. The current process environment takes precedence over
the configured `OPENCLAW_ENV_FILE` (defaulting to the runtime home's `.env`)
when determining effective endpoint and token availability; values are never
printed in the report.

### Project scripts and resources

- `scripts/` is an importable project package containing the maintenance
  entrypoints below; its modules can be reused by tests without resolving to an
  unrelated installed package with the same name.
- `scripts/run_skill.py` is the fixed entrypoint for benchmark-agent execution
  of local skill scripts through the workspace `uv` environment.
- `scripts/sync_verifier_grounded_datasets.py` validates a pinned release,
  synchronizes public prompt datasets and isolated scoring runtime metadata, and
  after a successful sync retains all runtime instances for the newest two
  distinct semantic versions while removing older managed runtimes. Cleanup
  failures make provisioning fail and identify the paths that could not be
  removed; unrecognized runtime directories are preserved.

Verifier runtime provisioning completes installation, runtime validation, and
dataset synchronization before applying this retention policy. Same-version
runtime directories with different wheel hashes are all retained; only managed
directories older than the two newest distinct versions are removed. The wheel
cache under `data/verifier-grounded-releases` is not part of this cleanup.
- `scripts/replay_workspace_adjudication.py` replays stored transcript evidence
  without a model call, recovers archived final answers from per-record data,
  runner metadata, or the session transcript, reconstructs a missing
  `results.json` from per-record payloads, and can apply record-selective
  recovery only after writing a snapshot. Explicit manual adjudication requires
  selected record IDs and a reason, preserves the original audit and error, and
  cannot override confirmed contamination.
- `scripts/archive_legacy_benchmark_workspaces.py` copies complete fixed legacy
  workspaces into an independent evidence archive, records a path/metadata/SHA-256
  inventory, verifies every archive and unchanged source, and deletes sources
  only when all requested archives pass those checks.
- `scripts/analyze_vgb_shadow_scoring.py` performs read-only nonlinear shadow
  scoring analysis over an existing verifier-grounded comparison report. It
  validates the pinned v0.9.1 task/profile inventory and public gold answers,
  reconstructs official scores before calculating diagnostic score-space,
  error-space, and aggregation candidates, including finite-error tail kernels
  such as generalized exponential, rational, and logistic mappings, and writes
  independent JSON, CSV, Markdown, and SHA-256 manifest artifacts without
  changing formal scores. Direct error-kernel summaries rank arithmetic-mean
  candidates by model separation while preserving the official linear result as
  a baseline.
- `scripts/sync_openclaw_qwen_provider.py` updates the live runtime-home Qwen
  provider configuration for `qwen3.6-plus`, `deepseek-v4-pro`,
  `qwen3.7-max`, `qwen3.7-plus`, and `qwen3.8-flash` using the
  `openai-responses` API, and removes applicable stale agent provider caches.
- `scripts/patch_openclaw_minimax_ui.py` applies the local OpenClaw 2026.6.9
  Control UI runtime patch: the model picker exposes only `off`/`adaptive` for
  MiniMax-M3, derives that picker from the current per-session model override
  (including the `minimax-m3` alias), selects `adaptive` when entering M3, and
  clears stale thinking overrides when leaving M3. It also versions the
  service-worker registration and main bundle URL so stale cache-first assets
  cannot keep serving the old picker. The installed bundle remains runtime
  state rather than canonical project source.
- The benchmark CLI and fixed-lane OpenClaw drivers accept the `adaptive`
  thinking level required by MiniMax-M3; the Benchmark Orchestrator validates
  the model-specific level before launching a run.
- All Docker single-LLM attempts and host VGB attempts create a fresh
  `scratch/venv` with `uv venv --seed --no-project`. Docker environment creation,
  execution, dependency inventory, and cleanup are owned by
  `benchmarking.runtime.container_attempt` inside the container; host VGB
  lifecycle remains in `benchmarking.runtime.attempt_environment`. Host non-VGB
  records use the workspace environment.
- `benchmarking/resources/agent-workspace-templates/` contains the canonical
  benchmark workspace base contract and role overlays.
- `benchmarking/resources/verifier_grounded/` contains the pinned release
  identity and sanitized public dataset snapshots. The current pinned VGB
  runtime is v0.9.2.

## 3. Core Execution Flows

### Benchmark CLI

The canonical entrypoint is:

```bash
uv run python -m benchmarking.workflow.cli
```

The implemented default experiment groups are:

- `single_llm_skills_on`: one OpenClaw agent with the complete benchmark skill
  routing inventory.
- `single_llm_skills_off`: one OpenClaw agent with an explicit empty skill list.

ChemQA is excluded from the default catalog. Its frozen legacy entrypoint is
`uv run python -m benchmarking.service.chemdebate.cli`; persisted runner and group
identifiers remain `chemqa` and `chemqa_skills_on`. No new features or future
OpenClaw compatibility updates are planned for this business.

Both active group definitions disable generic web search and web fetch.
For each invocation, the CLI:

1. Uses `benchmarking.workflow.dataset_selection` to discover or accept JSONL
   datasets, normalize them to `BenchmarkRecord`, apply record selection, and
   classify the run output root. Runner adapters materialize run-local visual
   bundles when required.
2. Projects the complete benchmark skill routing inventory without startup
   dependency/API health filtering, prepares a unique invocation identity, captures the verifier-grounded release identity for the
   lifetime of the invocation, recovers sentinel-proven stale active workspaces,
   and writes run-scoped OpenClaw configs.
3. Checks Docker daemon readiness and resolves the configured image to an
   immutable image ID before scheduling Docker single-LLM work. Startup orphan
   recovery requires a local dead owner PID, complete attempt ownership labels,
   and a matching managed-workspace sentinel. Live or unverifiable containers
   are preserved and reported; unresolved containers for the current run stop
   startup before workspace recovery. `docker-startup.json` and the runtime
   manifest retain startup evidence, including failures. Before scheduling,
   `container_network` resolves one immutable network configuration for the
   invocation from process environment, runtime `.env`, and system proxy
   fallback. On macOS, loopback proxy URLs map to `host.docker.internal` with
   their original port; Linux host networking retains loopback URLs. Lowercase
   proxy overrides take precedence and both cases receive the same values,
   including `NO_PROXY`. The exact configuration is passed to the preflight
   probe and every attempt/retry, and recorded with proxy credentials redacted.
   A bounded unauthenticated GET uses the pinned image's OpenClaw guarded model
   fetch transport against the configured provider base URL. Proxy paths are
   tested rather than skipped; connection, DNS, TLS, HTTP 407, and HTTP 5xx
   failures stop startup with `provider_connectivity_failed`. HTTP 401/403/404
   can establish connectivity but do not establish authentication or model
   health. Providers without an explicit endpoint record a skipped check.
   Probe results are retained as `provider_connectivity` in `docker-startup.json`.
4. Installs `SIGINT`/`SIGTERM` cancellation handlers, then dispatches single-LLM
   attempts through one shared queue across selected single-LLM groups. The
   explicit legacy entrypoint uses ChemQA group waves separately. `--max-concurrent-attempts` defaults to 2 and must be
   positive. Each queued record has an independent agent identity and runtime
   config, so group configuration and active workspaces are not shared across
   concurrent records. Group progress completes after all records return.
   Groups rotate round-robin; ready records within a group are FIFO. Retry
   deadlines use a monotonic clock and rejoin the group tail without holding a
   worker. A separate single scoring worker uses persisted runner-result
   references, so judge latency does not occupy attempt workers. Attempt
   results are retained under `attempt-results/`, pending scoring inputs under
   `scoring-pending/`, and final per-record results are written immediately.
   `--max-concurrent-groups` controls ChemQA waves only. Each record runs through either the
   single-LLM runner or the ChemQA runner, then through the registered evaluator
   when the runner result is scoreable.
5. Uses `benchmarking.workflow.run_state` to persist each record immediately,
   update run artifacts, aggregate only `scored=true` records, and support
   historical per-record resume data; the CLI writes the final results and
   runtime manifest.
6. Starts detached automated analysis unless `--no-analysis` is selected. A
   cancelled run never launches detached analysis.

Cancellation is run-scoped and cooperative. The first signal fixes the stable
cancellation reason; later signals shorten process termination grace. Scheduling
stops before another record, retry, wave, judge, or analysis launch. Registered
subprocesses start in owned process groups so termination covers descendants.
Active runners still audit and seal their attempt workspaces; cleanroom handles
manifest-owned ChemQA processes. Progress, waves, results, and the runtime
manifest finish as `cancelled` or `cancelled_with_errors`, and cancelled records
are non-evaluable, unscored, and use `execution_error_kind=cancelled`.

### Single-LLM runner

- The default Docker backend runs the OpenClaw wrapper and embedded (`--local`)
  agent turn inside the attempt container. It uses an attempt-local OpenClaw
  state/session root under the managed workspace; container transcript paths
  are translated back to their host archive paths before audit. Provider
  credentials and endpoints are injected from the runner environment, with the
  runtime `.env` as fallback. Environment SecretRefs are projected to local
  environment substitutions instead of requiring the host gateway. Docker
  inspect environment values are redacted in persisted diagnostics.
- Single-LLM Docker requests inherit the invocation's frozen `ContainerNetworkConfig`;
  the runner does not independently rediscover system proxy settings. The same
  network mode and proxy environment are used by the startup transport probe.
  Per-attempt container manifests and the runtime manifest expose the redacted
  network configuration. The connectivity probe adapter targets the pinned
  OpenClaw 2026.6.9 guarded-fetch exports and must be validated on version upgrades.
- Docker container names combine a bounded readable prefix with a SHA-256
  suffix over every attempt identity field, including invocation, group, agent,
  record, retry index, session, and template. Truncation cannot discard the
  identity suffix. Ownership and recovery still use labels and workspace
  sentinels, including for containers created with older names.
- Single-LLM admission uses a FIFO cancellation-aware count limit. Each retry
  acquires a new lease; backoff and scoring do not hold the lease. CPU, memory,
  and PID options are per-container hard limits, not admission resource weights.
  Failed container removal cancels further scheduling. Docker wait polls for
  cancellation and gives the container supervisor a bounded evidence-finalizing
  stop window before removal.
- Docker lifecycle CLI commands have explicit outer timeouts. Graceful termination
  sends TERM and polls for up to 420 seconds; a repeated cancellation skips the
  remaining grace and forces termination. Removal failures stop admission and
  enter run-level cleanup errors, producing `cancelled_with_errors`. A workspace
  whose container removal is unconfirmed remains in place for later recovery.
  The runtime manifest retains all container cleanup outcomes, including
  termination grace and forced-stop diagnostics.
- Startup orphan recovery acquires an existing workspace lock after proving
  ownership, preserves logs before removal, and allows running orphan supervisors
  a bounded finalization window. Incomplete workspace recovery removes fixed
  runner-owned environment paths before sealing; unsafe or uncleanable inactive
  workspaces are quarantined. General symlink validation is not relaxed.
- Bounded single-LLM attempts default to 7200 seconds (2 hours). The runner
  forwards this budget to OpenClaw as `--timeout`; the wrapper subprocess guard
  adds the 90-second finalization safety window and 30-second process margin,
  for a default outer limit of 7320 seconds.
- The runner prepends the effective budget to the agent prompt as `Time budget:
  <seconds> seconds for the whole answer attempt.` For bounded positive
  budgets, the wrapper tracks the primary turn and, when it returns without a
  complete answer after roughly five sixths of the budget (6000 seconds at the
  default), sends a same-session reminder with the remaining time.
- Every primary or timeout-retry attempt receives a fresh sentinel-managed
  workspace and run-scoped session id.
- Docker records and host records with `eval_kind=verifier_grounded` receive a
  fresh attempt-local Python environment and uv cache. All attempts in an invocation
  share its run-start PyPI cutoff, while each retry starts from a new empty
  environment. The agent may install registry packages with `uv pip`; pip
  mutations, direct URLs, local/editable sources, alternate indexes, dependency
  target overrides, and the pinned verifier distribution are blocked for
  explicit commands under the cooperative-agent threat model.
- Dependency commands are classified before source/target checks. Requirements
  and constraints files are unsupported and rejected, including compact option
  forms. Literal registry requirements remain supported. Environment target,
  registry, cutoff, cache, and configuration overrides are rejected. Ordinary
  HTTP command arguments are not classified as dependency operations.
- The runner materializes the role contract, attaches current scratch paths,
  invokes `benchmarking.service.single.openclaw_wrapper`, validates OpenClaw
  JSON stdout, and enforces the eval-aware candidate-answer contract.
- The canonical workspace contract requires agent-created Python virtual
  environments under `scratch/` to use `python3 -m venv --copies venv`.
  Runner-created VGB environments use `uv venv --seed --no-project`, are
  inventoried after the agent returns, and are removed before archival.
- Nonzero OpenClaw subprocess results are classified from structured error
  evidence before diagnostic excerpts are truncated. Provider failures retain
  a terminal `primary_error` plus ordered `observed_errors`; internal error
  categories and retry policy do not replace the original upstream status code,
  error code, message, or matched log text.
- Timeout-family failures may create a fresh attempt. Transcript recovery and a
  same-session finalization repair can preserve a complete answer; incomplete or
  unreliable output remains non-scoreable.
- Canonical skill scripts continue through `scripts/run_skill.py`. Within a VGB
  attempt it executes them directly with `BENCHMARK_ATTEMPT_PYTHON`, without
  resolving the workspace project or implicitly installing project extras.
- After a Docker or host VGB attempt returns, its environment owner records dependency commands from the
  transcript, including the terminal `process` result of background execs, the
  installed distribution inventory, RECORD hashes, a hashed
  replay requirements file, the run-start PyPI cutoff, credential names, and
  allowlisted native-tool fingerprints. It removes any detected exact-denylist
  distributions, then deletes the venv, uv cache, and native-tool wrappers
  before sealing the workspace; the manifest remains in archived scratch and
  runner metadata.
- Docker Python and skill scripts use
  `/benchmark/workspace/scratch/venv/bin/python`; the uv cache is
  `scratch/tmp/cache/uv`. Container configs set `tools.exec.pathPrepend` to the
  attempt `.runtime-bin`, so OpenClaw login shells resolve the fixed-config uv
  wrapper after their profile initialization. Dependency manifests use schema version 2 and independently
  validate attempt identity, actual interpreter prefix/executable, registry cutoff,
  freeze/inventory agreement, RECORD presence/hashes, replay lock content/hashes,
  and dependency policy. Docker and host VGB share this scoring contract: invalid
  necessary evidence or forbidden dependencies reject scoring while retaining the
  answer; unavailable replay locks or native-tool fingerprints permit scoring with
  `degraded_execution=true` and `status=partial`. A generated lock that fails
  validation is invalid, not an unavailable-lock downgrade. Successful removal of
  a forbidden distribution does not make its attempt scoreable. Prior provider
  failures retain their original evidence. The container removes only generated plugin-skill cache links
  into OpenClaw's immutable image extension tree, recording their targets before
  archival; general workspace symlink validation remains unchanged.
- Container transcript path projections are applied in memory during host
  audit, including recovery, while raw transcripts remain unchanged.
- `RuntimePathProjection` supplies container-visible bundle paths, config policy
  projection, and persisted audit mappings. Bundled questions use localized
  Markdown and relative image references. Only the current record bundle mounts
  read-only at `/benchmark/input`; records without a bundle have no input mount.
  Guard read scopes include the exact bundle in both backends. Historical replay
  consumes persisted path mappings when present and preserves legacy reads.
  The image tool's `image` and `images` arguments, including every array member,
  are checked by the guard and parsed by transcript audit.
- The transcript is audited under the attempt access policy before the complete
  workspace is archived. A `non_evaluable` adjudication or archive failure
  rejects an otherwise complete answer; `scoreable_degraded` preserves it with
  degraded-execution metadata. If the runner already has a terminal execution
  failure and the audit is unavailable with indeterminate contamination, the
  audit remains attached as diagnostic workspace-isolation metadata and does
  not replace the original failure. Confirmed contamination and archive
  failures retain precedence. Exec auditing first builds a structured shell
  projection that tracks quotes, escapes, heredocs, command substitutions,
  nested substitutions, backticks, and arithmetic substitutions without
  executing transcript commands. Parser recovery is represented by stable
  recovery codes and versions; a successful `exec` result may be retained as a
  warning only when the original shell syntax is valid, the projection is
  complete, and the normal protected-path scan (including literal paths inside
  nested substitutions) finds no forbidden access. Unknown or incomplete shell
  constructs, missing results, syntax failures, unresolved recovery, and
  protected-root references remain non-evaluable or contaminated according to
  the normal audit rules.

### ChemQA runner (legacy, frozen)

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

- `benchmarking.scoring.registry` dispatches by `record.grading.kind` with
  `generic_semantic` fallback. LLM-judge calls use a fresh isolated judge
  session and attempt workspace; pure answer and agent-response parsing lives in
  `benchmarking.core.answer_processing`.
- Verifier-grounded tasks use `benchmarking.runtime.vgb_bridge` to call the
  pinned package through a hash-addressed, non-agent virtual environment and
  `python -I`; agent-visible datasets contain public prompts and answer schemas,
  not hidden verifier material. Final reporting references for every
  release-declared property-calculation track come from that pinned release's
  public `task(..., include_gold=True)` view; scoring-profile identifiers are
  removed before the references enter reporting artifacts.
- Completed aggregation writes run-local evidence and may launch
  `benchmarking.analysis.automated`. Analysis failure is diagnostic and does not
  change benchmark scoring or the CLI exit outcome.
- The dashboard recursively discovers classified run directories and stops
  scanning below each detected run. It skips the reserved
  `legacy-workspace-archives` maintenance tree rather than traversing retained
  workspace evidence. It writes its annotation SQLite database and may persist a
  `cancelled_with_errors` terminal projection when a progress owner PID proves
  that a `running` or `cancelling` run is stale. It does not rewrite record scores
  or launch benchmark processes. When an aggregate `results.json` is present,
  per-record outputs are merged into the dashboard view and take precedence for
  duplicate group/record keys so active or resumed runs expose results written
  after the last aggregate snapshot, while an unenriched verifier placeholder
  cannot replace an aggregate reporting reference. For active verifier-grounded
  property-calculation runs, the detail view derives the standard answer from
  the scored result's release-specific `properties.gold_answers` when the
  per-record reporting reference is still the public-data placeholder. Dataset
  facets use the canonical
  `source_file` dataset segment when it follows the standard
  `<dataset>/data/<file>.jsonl` layout, correcting inconsistent persisted result
  labels without rewriting run artifacts. Verifier-grounded property-calculation
  records are displayed under the release track names
  `property_calculation_advanced` and `property_calculation_basic`, derived from
  their record IDs while retaining historical dataset file names. Manual dashboard refreshes expose
  their pending state through the refresh control and restore the control after
  either success or failure. Favorited runs are pinned to the top of the run
  list; within favorited and non-favorited groups, discovery keeps the existing
  newest-first ordering. Record detail timing prefers the agent execution
  duration from `runner_meta.durationMs`, converted from milliseconds to
  seconds, and falls back to persisted `elapsed_seconds` for legacy results
  without that metadata.

### Paper pipeline

Paper processing is an explicit fixed sequence of independent scripts:

```text
retrieval -> access -> parse
```

Parsing uses the official MinerU Agent API for small documents, the optional
Precision API for larger documents, and PyMuPDF as the final local fallback.
The stages exchange explicit JSON artifacts and are not exposed as one
transactional orchestration service. No local MinerU CLI or `mineru-api`
service is required.

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
- Structured runner execution errors retain a stable internal `code`, `layer`,
  and `retryable` decision alongside original `primary_error` and
  `observed_errors` evidence. Primary provider evidence is selected by
  structured-field and parser specificity rather than terminal log position;
  punctuation-only diagnostic fragments are not error evidence. Provider
  transport failures such as `stream_read_error` are retryable. Retry attempt
  history retains the complete structured execution error for each failed
  attempt. Unsupported OpenClaw thinking levels are classified as explicit,
  non-retryable configuration failures and retain the original diagnostic.
- `benchmarking.runtime.subprocess_utils.summarize_payloads` excludes payloads
  marked `isError=true` and the OpenClaw fallback warning shape
  `⚠️ 🛠️ \`...\` failed` from formal answer text. Raw provider payloads,
  transcripts, and tool-failure audit counts remain unchanged.
- `passed` is an evaluator quality outcome, not a runtime-health field.
  Verifier-grounded continuous scores use `passed = null`.
- Aggregate score denominators contain only records with `scored=true`.
- Run, wave, and group lifecycle projections distinguish `cancelling`,
  `cancelled`, and `cancelled_with_errors`; records use `cancelled` without a
  fabricated evaluator score.

The final run artifact set includes:

- `results.json` and `runtime-manifest.json`;
- `per-record/<group>/<record>.json`;
- `progress/events.jsonl` and `progress/state.json`;
- `runtime-config/*.json`, `input-bundles/`, and archived attempt workspaces;
- `skill-routing-inventory.json`, `web-search-preflight.json`, and (when the
  Docker backend is selected) per-attempt container manifests, logs, stats, and
  cleanup spools;
- `analysis/` status, evidence, and reports when automated analysis is enabled.

Legacy fixed-workspace evidence uses a separate archive kind and schema. It
retains the complete source tree, including Git metadata, plus an inventory of
directories and regular files with modes, modification times, sizes, and SHA-256
digests. The maintenance command rejects symlinks and special files, refuses
overwrites, detects source mutation during copying, verifies the completed
archive independently, and rechecks every source before optional deletion. It
does not synthesize attempt identities or place legacy snapshots inside a run's
`agent-workspace-archives/` tree.

### Attempt workspace contract

- Attempt workspaces use scratch contract version `2` with stable
  `scratch/requests`, `scratch/outputs`, `scratch/notes`, and `scratch/tmp`.
- Workspace tree validation permits regular files named `.git` anywhere under
  `scratch/tmp/cache/uv/`, which `uv` may create in a scratch-local cache. It
  also permits relative symbolic links located under `scratch/` when their
  strict resolved targets remain under the same attempt scratch tree and are
  regular files or directories. A dangling relative link is permitted only when
  its lexically normalized target is strictly inside that same scratch tree and
  its existing target-parent components are real directories. Control-plane,
  absolute, escaping, chained-dangling, cyclic, special-file-targeting, and
  other `.git` paths remain forbidden.
- Attempt archives preserve validated scratch-relative symbolic links rather
  than dereferencing them, revalidate the relocated archive tree, and record a
  count plus deterministic link-manifest digest, with separate count and digest
  fields for dangling links. Cross-filesystem copies must match both regular-file
  statistics and the symbolic-link inventory.
- Structured file tools use workspace-relative `scratch/...` paths. Shell
  commands enter scratch through runner-provided environment variables.
- A canonical base `AGENTS.md` plus a minimal role overlay defines the same
  isolation behavior for single-LLM, judge, and ChemQA roles.
- The canonical base keeps native `exec` available for single-line commands and
  directs multiline scripts through a structured write to `scratch/tmp` before
  native execution; heredocs, here-strings, and inline multiline interpreter
  commands are discouraged in the workspace contract rather than record prompts.
- Immutable `WorkspaceAccessPolicy` objects define normalized read, write, and
  exec-workdir scopes, exact-file scopes, protected roots, and a deterministic
  digest. Skills-off and judge policies do not grant access to the skill source
  tree or `scripts/run_skill.py`.
- The `benchmark-workdir-guard` plugin preflights structured path arguments,
  explicit exec working directories, and absolute paths embedded in exec
  commands. Exec command paths inside the active workspace are allowed, known
  system executable/device paths are allowed, and other absolute paths or
  protected roots are blocked before execution. Transcript audit independently
  correlates tool calls and results and records access mode, outcome, resolved
  path, policy, and matched protected root.

Workspace audit has four independent axes:

- `audit_execution_status`: `complete` or `unavailable`;
- `boundary_status`: `clean`, `warning`, `violated`, or `unknown`;
- `contamination_status`: `clear`, `confirmed`, or `indeterminate`;
- `adjudication`: `scoreable`, `scoreable_degraded`, or `non_evaluable`.

Confirmed or indeterminate external information exposure is `non_evaluable`.
Guard-blocked operations are recorded as boundary violations with
`operation_outcome=blocked` and `information_exposure=none`; they do not by
themselves prove contamination and remain scoreable as degraded execution.
Write-only, other failed, or allowed-fallback boundary events do not by
themselves prove information contamination. A write-only boundary violation can
be `scoreable_degraded`; an allowed fallback is a warning and remains
`scoreable`. Audit evidence recovery is attempted before an unavailable audit is
finalized. Archive failure remains fail closed.

Known audit parser conditions use stable codes and an in-code recovery-handler
registry. `exec_unterminated_heredoc_eof` projects the complete EOF heredoc body
through recovery version 1, emits `transcript_audit_recovered`, and continues the
normal protected-path scan. Dynamic shell constructs use a quote-aware, nested
projection before `shlex` tokenization; literal paths discovered inside command
substitutions are audited with the same immutable policy. Recovery success is a
boundary warning; an unknown condition, incomplete projection, unresolved
dynamic construct, or recovery exception remains unavailable. Historical
dry-run replay can use a persisted per-record workspace policy when an
interrupted legacy run lacks final aggregate artifacts; apply mode still
requires `results.json` and `runtime-manifest.json`.

This lifecycle, guard, and transcript audit is not an operating-system security
boundary. Processes still run as the same local user.

### Session, skill, artifact, and cleanup contracts

- Single-LLM and judge calls clear only stale main-session pointers, use explicit
  run-scoped session ids, and verify the requested session and transcript after
  the turn. Historical transcripts remain available for audit.
- Skills-on exposure uses the complete benchmark skill routing inventory.
  Skills-off runner configs contain `skills: []`. Both groups use the same base
  runtime and may install dependencies through the registry allowlist during an
  attempt. Skill choice is left to the model; tool and skill diagnostics do not
  change answer scores.
- The skills-on prompt catalog is a presentation projection of the same matrix;
  its domain/family grouping and route summaries do not define a separate
  exposure or routing source.
- Agent-invoked local skill scripts run through `scripts/run_skill.py`, which uses
  the attempt Python when configured and otherwise the canonical workspace for
  dependency resolution. Relative artifacts use the attempt scratch directory.
  The skills-on `TOOLS.md` documents this wrapper invocation; arguments after
  `--` follow each selected skill's CLI contract rather than a universal request
  JSON interface. The role overlay retains only skill permissions and optional
  use; shared scratch and isolation rules remain in the canonical base.
- Skill diagnostics write `configured_skill_count` and `configured_skills`.
  The deprecated skill-health implementation and writer fields are absent;
  routing inventory and tool-use statistics remain independent of dependency
  availability. Historical JSON may contain ignored extra health fields.
- ChemQA terminal output is `final_answer_artifact.json` or
  `failure_artifact.json`, accompanied by `artifact_manifest.json`,
  `candidate_view.json`, validation diagnostics, and the compatibility projection
  `qa_result.json`.
- Benchmark cleanroom cleanup terminates benchmark-owned processes from manifests
  and leases. It intentionally retains session stores, transcripts, run artifacts,
  manifests, and archived workspaces for audit.

## 5. Current Risks and Non-goals

### Current risks

- Docker migration fixes and acceptance evidence are tracked in
  `docs/report/2026-09-11-benchmark-infra-fix-validation.md`; the six-item
  handoff is closed after latest-image real-model and scheduler acceptance.
  Dependency replay
  unavailability is explicitly diagnostic degradation, while invalid inventory,
  policy violations, and isolation failures remain non-scoreable. The command
  guard remains a cooperative-agent policy, not an arbitrary shell sandbox.
- Attempt isolation detects and adjudicates filesystem evidence but cannot prevent
  every same-user filesystem access performed inside arbitrary subprocesses.
- The benchmark CLI still owns argument parsing, wave scheduling, final
  aggregation, and runtime-manifest composition; changes to these concerns can
  therefore affect the whole benchmark entrypoint.
- OpenClaw and ClawTeam integration is subprocess- and file-contract-based;
  correctness depends on session identifiers, manifests, status files, and
  process metadata remaining consistent.
- ChemQA recovery and artifact reconstruction retain compatibility with specific
  protocol filenames and directory layouts.
- The live runtime-home `openclaw.json` is the mutable base for run-scoped configs
  and may contain provider and gateway configuration. It must be treated as local
  operational state.
- Many chemistry skills require optional Python packages, external executables,
  API credentials, network providers, or optional MinerU API access. Skills-on
  routing exposes the complete inventory; dependency and provider failures are
  recorded during attempt execution.

### Non-goals of the current system

- The host execution backend still runs attempt workspaces as the same local
  user; the optional Docker backend adds container isolation but is not a
  complete syscall or multi-user security boundary. The CLI defaults to the
  Docker backend for single-LLM attempts; `--execution-backend host` remains a
  compatibility fallback.
- The benchmark dashboard is a localhost review surface, not a benchmark launcher,
  multi-user service, or authority that rewrites immutable result artifacts.
- Automated post-run analysis is not part of benchmark scoring.
- The chemistry inventory does not prescribe deterministic skill routing.
- The paper stages are not exposed as one transactional orchestration service.
- Benchmark cleanup does not prune retained sessions or historical run artifacts.

## 6. Specification and Runbook Index

- `docs/README.md`: complete document catalog grouped by purpose.
- `docs/AGENTS.md`: documentation classification, naming, status, and link rules.

### Normative project and benchmark contracts

- `AGENTS.md`: repository workflow, canonical document rule, test and commit
  requirements.
- `docs/design/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md`:
  current attempt behavior, access policy, four-axis adjudication, and historical
  replay contract.
- `docs/design/2026-07-16-benchmark-forbidden-path-root-containment-spec.md`:
  protected-root containment and transcript path evidence.
- `docs/design/2026-07-23-benchmark-audit-error-allowlist-and-cancellation-spec.md`:
  typed audit recovery, EOF heredoc handling, owned-process cancellation, and
  persistent cancellation terminal states.
- `docs/design/2026-07-15-verifier-grounded-openclaw-single-llm-integration-usage-spec.md`:
  verifier-grounded dataset exposure and isolated scoring contract.
- `benchmarking/resources/verifier_grounded/release.json`: current pinned
  verifier-grounded release identity.

### Operational runbooks and component contracts

- `README.md`: verifier-grounded CLI usage and paper-processing operations.
- `docs/guide/benchmark-dashboard-usage.md`: dashboard launch, data sources, and review
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
