# GLOBAL DEV SPEC

## 1. Project Overview
- Purpose
  - `.openclaw/` is a local OpenClaw runtime home that also contains a Python workspace for chemistry benchmark orchestration, DebateClaw debate workflows, ChemQA-style review workflows, paper retrieval/access/parse/rerank utilities, and benchmark cleanup tooling.
  - The main executable source code lives under `workspace/`.
  - The repo root also stores live runtime state for OpenClaw and ClawTeam: agent configs, generated workspaces, SQLite state, logs, device/auth files, and task/session registries.
- Current capabilities (ONLY what works)
  - `DONE`: Run benchmark batches across three skills experiment groups: `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on` via `workspace/benchmark_test.py`; all groups keep websearch enabled, and the experiment variable is the health-filtered benchmark skills allowlist.
  - `DONE`: Load benchmark JSONL datasets into a normalized `BenchmarkRecord` model via `workspace/benchmarking/datasets.py`.
  - `DONE`: Score outputs with registered evaluators for ChemBench, FrontierScience Olympiad/Research, SuperChem, HLE, and generic semantic matching via `workspace/benchmarking/evaluators.py` and `workspace/benchmarking/evaluation.py`.
  - `DONE`: Provision run-scoped OpenClaw configs and DebateClaw/ChemQA slot workspaces via `workspace/benchmarking/runtime_config.py`, `workspace/benchmarking/config_renderer.py`, and `workspace/benchmarking/provisioning.py`.
  - `DONE`: Run a single-agent OpenClaw baseline through a benchmark wrapper that gives each record a run-scoped `sessionId`, clears only stale `agent:<id>:main` session-store pointers before the turn, injects time-budget-aware answer instructions, validates OpenClaw stdout against a strict agent result schema before answer extraction, applies runner-level convergence policy metadata and transcript recovery for complete benchmark answers, treats unrecovered OpenClaw response-timeout sentinel payloads as failed/non-scoreable runs, and preserves historical transcript files via `workspace/benchmarking/runners/single_llm.py`, `workspace/benchmarking/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/convergence.py`, and `workspace/benchmarking/result_contract.py`.
  - `DONE`: Run a ChemQA multi-agent workflow by compiling/materializing a ChemQA launch, monitoring benchmark-visible run-status, applying runner-level convergence policy to unchanged-status recovery attempts, consuming canonical Artifact Flow outputs, archiving outputs, and cleaning runtime leftovers via `workspace/benchmarking/runners/chemqa.py`.
  - `DONE`: Manage DebateClaw V1 runtime, slot provisioning, prompt/materialization, and launch commands via `workspace/skills/debateclaw-v1/scripts/*.py`.
  - `DONE`: Maintain live debate protocol state in SQLite and expose CLI commands for init/status/next-action/submit/advance via `workspace/skills/debateclaw-v1/scripts/debate_state.py`.
  - `DONE`: Drive ChemQA reviewer/proposer/coordinator loops on top of DebateClaw state via `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`, including phase-scoped multi-turn artifact production, role-phase run-status diagnostics, and deterministic coordinator fallback when model refinement aborts or leaves no valid protocol rewrite.
  - `DONE`: Recover stalled ChemQA runs, respawn dead workers, and repair invalid protocol state via `workspace/skills/chemqa-review/scripts/recover_run.py`.
  - `DONE`: Collect ChemQA protocol outputs through Artifact Flow into canonical terminal artifacts, `artifact_manifest.json`, and legacy-compatible `qa_result.json` via `workspace/skills/chemqa-review/scripts/chemqa_artifact_flow.py` and `collect_artifacts.py`; finalization applies structured `answer_revision` rebuttals and repairs numeric short-answer projections from anchored final values in the full answer when the raw direct answer is a setup/process sentence.
  - `DONE`: Provide deterministic first-batch chemistry provider skills for local structure reasoning, name resolution, public compound lookup, and numeric chemistry calculations via `workspace/skills/rdkit`, `workspace/skills/opsin`, `workspace/skills/pubchem`, and `workspace/skills/chem-calculator`.
  - `DONE`: Provide an experimental medium-or-higher-value chemistry skill inventory via `workspace/skills/chemistry-routing-matrix.json`, covering 84 local skills from structure/materials, atomistic simulation, quantum chemistry, bioactivity/safety, molecular/materials ML, databases, spectra/formats, paper retrieval/access/parse/rerank, and workflow automation. Despite the historical filename, runtime benchmark prompts now treat it as inventory data, not as a deterministic router.
  - `DONE`: Run benchmark skill health checks before skills-on groups. Startup health checks verify declared Python imports through workspace `uv run`, paper PDF backend imports through the `paper-parse` optional extra, executables, API keys loaded from process env or the OpenClaw runtime `.env`, data files, and network providers with per-skill probe timeouts for slower providers such as ChEMBL; unavailable skills are removed from effective runtime allowlists and reported in `skill-health.json` plus `runtime-manifest.json`.
  - `DONE`: Provide a fixed skill script runner via `scripts/run_skill.py`; agent-invoked skill scripts run through workspace `uv run python`, with `paper-parse` scripts executed via `uv run --extra paper-parse python`, and return structured unavailable payloads such as `missing_dependency`, `missing_executable`, `missing_api_key`, and `provider_failure`.
  - `DONE`: Provide autonomous benchmark skill discovery and audit via `workspace/benchmarking/skill_tree.py`, `workspace/benchmarking/skill_audit.py`, and `workspace/benchmarking/reporting.py`: skills-on benchmark runs expose the health-filtered benchmark skill allowlist, prompts render a compact Hierarchical Skill Tree, and post-run reporting tracks actual tool calls separately from answer scoring.
  - `DONE`: Retrieve literature candidates from OpenAlex, Semantic Scholar, and Crossref via `workspace/skills/paper-retrieval/scripts/paper_retrieval.py`.
  - `DONE`: Resolve accessible paper artifacts using direct OA URLs and optional Unpaywall lookup via `workspace/skills/paper-access/scripts/paper_access.py`.
  - `DONE`: Parse local PDF/text documents with MinerU or PyMuPDF fallback via `workspace/skills/paper-parse/scripts/paper_parse.py`.
  - `DONE`: Rerank papers by building GROBID profiles and calling an OpenAI-compatible chat-completions endpoint via `workspace/skills/paper-rerank/scripts/paper_rerank.py`.
  - `DONE`: Terminate benchmark-owned leftover processes from manifests/leases while preserving session files, session stores, run-scoped artifacts, manifests, and cleanup reports via `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`.
  - `DONE`: Manage local Docker-backed GROBID via `workspace/scripts/docker_services.sh` and native macOS MinerU API via `workspace/scripts/mineru_service.sh`.
  - `PARTIAL`: Native workflow-package support exists for `chemqa-review@1`, but the package implementation is explicitly inactive scaffold metadata and is not the live runtime path.
  - `NOT_IMPLEMENTED`: No actual web UI/server is implemented in the repo despite `web-ui` optional dependencies in `workspace/pyproject.toml`.

## 2. System Architecture
- Top-level repo roles
  - `workspace/`
    - Main Python package and scripts.
    - Contains benchmark orchestration, skill bundles, dataset prep scripts, tests, docs, and Docker helpers.
  - `agents/`
    - OpenClaw agent runtime directories with `agent/models.json` and `sessions/sessions.json`.
    - Used as live runtime/config state, not source modules.
  - `benchmark/workspaces/`
    - Generated benchmark slot workspaces for chemqa/baseline/judge runs.
    - Used by benchmark scripts as runtime workspace roots.
  - `debateclaw/workspaces/`
    - Generated DebateClaw slot workspaces for live debate runs.
  - `flows/`, `tasks/`, `memory/`
    - SQLite runtime stores.
  - `logs/`, `devices/`, `identity/`, `qqbot/`
    - Operational state and logs; not code modules.
  - `openclaw.json`
    - Base OpenClaw config used and rewritten into run-scoped configs by benchmark launchers.
    - Defines a global `openai/gpt-5.4` provider shim that uses the `openai` provider name while routing requests to the SU8 OpenAI-compatible endpoint via `${SU8_BASE_URL}` and `${SU8_API_KEY}`. This preserves OpenClaw provider-name guard behavior for GPT-5 runs without changing the default primary model, which remains `su8/gpt-5.4`.

- Source modules
  - `workspace/benchmarking/`
    - `contracts.py`
      - Defines `RunStatus`, `AnswerPayload`, `FailureInfo`, `RecoveryInfo`, `RunnerResult`.
    - `datasets.py`
      - Normalizes benchmark records from JSONL.
    - `evaluation.py`
      - Registry/dispatch for evaluator functions.
    - `evaluators.py`
      - Implements benchmark scoring functions, answer parsing helpers, and the `EvaluationResult` payload.
    - `experiments.py`
      - Defines `ExperimentSpec`.
    - `convergence.py`
      - Defines benchmark runner convergence policy, transcript summary helpers, and complete-answer recovery from session transcripts. Tool/turn counts are diagnostics only and are not enforced as hard limits.
    - `result_contract.py`
      - Validates and normalizes OpenClaw agent stdout so only schema-valid `payloads[].text` entries become benchmark answers; invalid stdout is retained only as diagnostics.
    - `config_renderer.py`
      - Produces run-scoped OpenClaw configs, toggles web search, injects agent entries, and applies runner-only benchmark skill allowlists.
    - `provisioning.py`
      - Creates slot workspaces and `.debateclaw-slot.json` sentinels.
    - `runtime_config.py`
      - Orchestrates run-scoped config payloads, ChemQA slot id mapping, slot workspace provisioning, and config-path pooling for benchmark runs.
    - `prompts.py`
      - Builds single-agent and ChemQA benchmark prompts, adds run-local visual bundle instructions when present, resolves ChemQA answer-kind hints, and keeps websearch availability controlled by runtime config rather than prompt wording.
    - `reporting.py`
      - Defines the per-record benchmark result schema and aggregates per-record results into summary buckets.
    - `skill_tree.py`
      - Loads the historical chemistry skill inventory, defines the full benchmark skill allowlist, and renders a three-layer discovery tree: Domain -> Skill Family -> Concrete Skill, optionally filtered to health-available skills.
    - `skill_audit.py`
      - Extracts conservative post-run skill-use audit metadata from OpenClaw runner metadata, final answer text, and benchmark skill-health summary.
    - `skill_health.py`
      - Defines benchmark skill health requirements and startup checks for Python imports, optional-extra PDF backends, executables, API keys from process env/OpenClaw `.env`, data files, and network providers with per-skill probe timeouts.
    - `skill_runtime.py`
      - Provides the workspace `uv run python` skill runner, paper-parse optional-extra command selection, and structured unavailable/failure payload normalization.
    - `status.py`
      - Normalizes ChemQA run-status payloads and derives benchmark result status axes from runner results.
    - `single_llm_openclaw_wrapper.py`
      - Wraps `openclaw agent` for single-LLM benchmark turns, resets stale fixed-agent `main` session-store entries before a run-scoped `sessionId` turn, validates stdout through the result contract, emits session isolation plus stdout diagnostics metadata, and can recover a latest complete benchmark answer from the run-scoped transcript after timeout-like OpenClaw output.
    - `runners/`
      - `single_llm.py`: baseline single-agent runner.
      - `chemqa.py`: ChemQA launch/monitor/archive/cleanup runner.
  - `workspace/benchmark_test.py`
    - Main three-group skills benchmark CLI.
    - Also contains runtime bundle helpers for SuperChem/HLE visual inputs, cleanup registration, runner wiring, and compatibility wrappers for runtime config and evaluator helpers.
  - `workspace/runtime_paths.py`
    - Central path resolution for repo, skills, benchmarks, runtime roots, and config files.

- Skill bundles under `workspace/skills/`
  - `debateclaw-v1/`
    - Installable DebateClaw runtime bundle.
    - Owns preset compilation/materialization, slot provisioning, launch helpers, runtime checks, model profiles, and live debate state CLI.
  - `chemqa-review/`
    - Installable ChemQA review protocol bundle layered on top of DebateClaw V1.
    - Owns ChemQA launch pipeline, driver loop, artifact reconstruction, liveness/recovery tooling, an inactive native workflow-package scaffold, and prompt/runtime dependency wiring for sibling chemistry provider skills.
  - `rdkit/`, `pubchem/`, `opsin/`, `chem-calculator/`
    - First-batch chemistry provider bundles used for deterministic structure, nomenclature, compound lookup, and numeric subproblems.
  - `chemistry-routing-matrix.json`
    - Historical experimental chemistry skill inventory for medium-or-higher-value chemistry capabilities. Despite the historical filename, runtime benchmark prompts treat this as inventory data, not as a deterministic router.
    - `workspace/benchmarking/skill_tree.py` defines the benchmark skill allowlist and a three-layer discovery tree: Domain -> Skill Family -> Concrete Skill.
    - Benchmark startup checks the allowlist with `workspace/benchmarking/skill_health.py`; only health-available skills remain in effective skills-on runtime configs and prompts. Health checks merge API keys from the OpenClaw runtime `.env` when they are not present in the process environment.
    - Single-agent skills-on runs expose the health-filtered benchmark skill allowlist to the model. Prompts include a lightweight hierarchical skill tree and rely on the model to choose and call relevant skills when they help answer the record.
    - Agent-invoked skill scripts should go through `workspace/scripts/run_skill.py`, which executes target scripts via workspace `uv run python` or `uv run --extra paper-parse python` for `paper-parse` scripts, and reports structured unavailable/failure payloads instead of raw shell failures.
    - Post-run reporting records actual tool-use audit metadata such as tool-call counts, model-declared skipped traces, skill-health summary, and no-tool-call outcomes. Skipped traces are diagnostic only and do not count as executed skill use.
    - Core executable wrappers for `cclib`, `pymatgen`, `molecular-dynamics`, and `chembl-database` return structured error payloads for missing dependencies, missing input files, parse failures, and provider/API failures instead of crashing.
  - `benchmark-cleanroom/`
    - Run-scoped cleanup manifests and lease management plus a process-only cleanup executor that intentionally preserves benchmark session and artifact state.
  - `paper-retrieval/`, `paper-access/`, `paper-parse/`, `paper-rerank/`
    - Standalone paper-processing pipeline stages.

- Dataset prep modules
  - `workspace/benchmarks/chembench/extract_open_ended_reasoning_pool.py`
  - `workspace/benchmarks/frontierscience/extract_chemistry_pool.py`
  - `workspace/benchmarks/hle/extract_hle_chemistry_pool.py`
  - `workspace/benchmarks/superchem/extract_superchem_pool.py`
- Module interactions
  - `benchmark_test.py` -> `benchmarking/*`
    - Uses dataset loading, runtime config orchestration, runner construction, and reporting.
  - `benchmark_test.py` -> `skills/chemqa-review`
    - Launches ChemQA preset flow, passes resolved `answer_kind`, polls benchmark-visible run status, prefers canonical Artifact Flow paths, archives outputs.
  - `benchmark_test.py` -> `skills/benchmark-cleanroom`
    - Writes cleanup manifests and runs process-finalizer cleanup hooks on exit/failure without deleting benchmark session or artifact state.
  - `chemqa_review_openclaw_driver.py` -> `debate_state.py`
    - Subprocess-driven control loop; asks for next action, submits artifacts, advances state.
  - `collect_artifacts.py` -> protocol YAML/JSON emitted by coordinator
    - Converts protocol state through `chemqa_artifact_flow.py` into `final_answer_artifact.json` or `failure_artifact.json`, `artifact_manifest.json`, `candidate_view.json`, diagnostics, and legacy-compatible `qa_result.json`.
  - `paper-rerank.py` -> `paper-access`/`paper-parse` outputs
    - Expects local PDFs and calls GROBID + OpenAI-compatible LLM endpoint.

## 3. Feature Matrix
- Name: Three-group skills benchmark batch runner
  - Description: Runs single-agent baselines with and without the benchmark skills allowlist, plus ChemQA with the same skills allowlist. All three groups enable websearch/duckduckgo, wave-batch groups in requested order, and save per-record and aggregate outputs.
  - Input / Output:
    - Input: benchmark root or dataset files, group list, timeouts, config path, model/profile overrides.
    - Output: `results.json`, `results.partial.json`, `runtime-manifest.json`, `runtime-config/*.json`, `per-record/*/*.json`, CSV summaries.
    - Per-record JSON entries are on schema version `2` and include `skills_enabled` plus explicit evaluability axes such as run lifecycle status, protocol completion/acceptance status, answer availability/reliability, evaluable/scored flags, recovery mode, degraded execution, and execution error kind.
    - Aggregate summaries in `results.json` and CSV exports retain legacy score fields and also expose `skills_enabled`, operational counters such as completed vs failed runs, protocol completion, evaluable/scored counts, recovered-evaluable counts, degraded execution counts, and HLE calibration RMSE for confidence diagnostics.
  - Implementation location: `workspace/benchmark_test.py`, `workspace/benchmarking/*`
  - Status: `DONE`

### Benchmark Result Status Axes
- `results.json` now carries top-level `schema_version = 2` and a `status_axes_description` block that documents the evaluability axes used by per-record entries.
- `run_lifecycle_status` reports whether the benchmark run finished operationally, while `protocol_completion_status` reports whether the ChemQA protocol itself completed, failed, or is missing.
- `answer_availability` and `answer_reliability` distinguish native final answers from recovered candidate answers, preview-only fallbacks, and missing answers.
- `evaluable` means the system preserved a trustworthy answer that should count for benchmark scoring. `scored` means the evaluator actually ran. `passed` remains the task-quality outcome inside `evaluation`.
- `pass_count` remains a legacy score summary and should not be treated as an operational stability metric. Operational reporting should use the explicit status/evaluability counters instead.

- Name: Benchmark record normalization
  - Description: Loads JSONL records, validates prompt/answer presence, derives grading config and subset labels.
  - Input / Output:
    - Input: benchmark JSONL files.
    - Output: `BenchmarkRecord` objects with `GradingSpec`.
  - Implementation location: `workspace/benchmarking/datasets.py`
  - Status: `DONE`

- Name: HLE chemistry pool extraction
  - Description: Extracts chemistry-related Humanity's Last Exam rows from `cais/hle` into the local benchmark JSONL style under `workspace/benchmarks/hle/data/`, matching records by `category` and `raw_subject`, preserving HLE question/answer/answer_type/image/canary metadata, and writing a manifest with selection counts.
  - Input / Output:
    - Input: authenticated Hugging Face `cais/hle` test split or a local HLE JSONL export.
    - Output: `hle_chemistry_pool.jsonl` plus `hle_chemistry_pool.manifest.json`.
  - Implementation location: `workspace/benchmarks/hle/extract_hle_chemistry_pool.py`
  - Status: `DONE`

- Name: Benchmark visual input bundle materialization
  - Description: Creates per-record local input bundles for benchmark records with visual inputs. SuperChem image paths are expected to be relative to the record JSONL directory, temp benchmark path repair rewrites stale machine-local absolute paths into portable relative references, and the SuperChem extractor localizes only images directly referenced by the current question/options/reference reasoning text. HLE image fields are materialized from base64 data URIs or local files into the bundle, and remote-only HLE images fail fast instead of silently dropping visual context.
  - Input / Output:
    - Input: SuperChem/HLE `BenchmarkRecord` payloads plus a run-local bundle root.
    - Output: `question.md` and localized `images/*` files referenced by single-agent and ChemQA prompts.
  - Implementation location: `workspace/benchmark_test.py`, `workspace/benchmarking/prompts.py`
  - Status: `DONE`

- Name: Evaluator registry and dispatch
  - Description: Maps `eval_kind` to evaluator function with `generic_semantic` fallback. Scoreable benchmark answers are judged from the complete candidate `answer_text`/full response, while `short_answer_text` remains a legacy/display field and is not used to decide `passed` or score. Judge JSON extraction tolerates invalid non-JSON backslash escapes commonly produced inside LaTeX snippets, such as `\(` and `\)`, so a parseable judge verdict is not upgraded to an execution error only because of LaTeX escaping.
  - Input / Output:
    - Input: `BenchmarkRecord`, short/full answer text, judge object.
    - Output: evaluator payload/dataclass.
  - Implementation location: `workspace/benchmarking/evaluation.py`, `workspace/benchmarking/evaluators.py`
  - Status: `DONE`

- Name: ChemBench open-ended scoring
  - Description: Scores numeric or text answers for ChemBench open-ended tasks through the LLM judge using the complete candidate answer text; local numeric/string matching does not decide pass/fail.
  - Input / Output:
    - Input: `BenchmarkRecord`, model answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/evaluators.py`
  - Status: `DONE`

- Name: FrontierScience Olympiad scoring
  - Description: Evaluates olympiad-style answers through the LLM judge using the complete candidate answer text, covering numeric answers, molecule names embedded in tagged InChI/SMILES/IUPAC references, and formula-style symbolic expressions without local heuristic short-circuiting.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/evaluators.py`
  - Status: `DONE`

- Name: FrontierScience Research scoring
  - Description: Uses rubric parsing plus LLM judge scoring for research track outputs; parsed rubric items structure the prompt, and the judge decides item satisfaction from the complete candidate answer text. Rubric points and `normalized_score` retain partial-credit information, while `passed` is true only when every rubric item receives full credit.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/evaluators.py`
  - Status: `DONE`

- Name: SuperChem multimodal scoring
  - Description: Extracts reference checkpoints/options for prompt context and asks the LLM judge to decide answer accuracy plus checkpoint matches/RPF from the complete candidate answer text.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/evaluators.py`
  - Status: `DONE`

- Name: HLE chemistry scoring
  - Description: Scores Humanity's Last Exam chemistry-subset records with the official HLE-style LLM judge rule over the complete candidate answer text: extract the final answer from the response, compare against the precise reference answer with small numeric tolerance allowed by the judge prompt, and return binary accuracy plus confidence metadata. Aggregate reporting computes HLE calibration RMSE from the candidate response confidence and binary correctness as a diagnostic of self-reported reliability; it does not affect per-record `score`, `normalized_score`, or `passed`.
  - Input / Output:
    - Input: HLE chemistry `BenchmarkRecord` plus model response text.
    - Output: `EvaluationResult` with `primary_metric = hle_judge_accuracy` and judge details including extracted final answer and confidence.
  - Implementation location: `workspace/benchmarking/evaluators.py`, `workspace/benchmark_test.py`
  - Status: `DONE`

- Name: Run-scoped OpenClaw config orchestration
  - Description: Builds per-group benchmark config payloads, provisions judge/single-agent/ChemQA slot workspaces, toggles web search/plugin state, injects judge/runner agent entries, writes runner-only benchmark skill allowlists plus `skills.load.extraDirs`, strips `thinking` from managed agents, and writes pooled runtime config paths.
  - Input / Output:
    - Input: base config payload/path, experiment group/spec, runtime roots, model overrides, slot template.
    - Output: modified config payloads and config JSON files under `runtime-config/`.
  - Implementation location: `workspace/benchmarking/runtime_config.py`, `workspace/benchmarking/config_renderer.py`, `workspace/benchmarking/provisioning.py`
  - Status: `DONE`

- Name: Slot workspace provisioning
  - Description: Creates workspaces with `AGENTS.md` and `.debateclaw-slot.json`.
  - Input / Output:
    - Input: workspace path, slot id, template text.
    - Output: initialized runtime workspace.
  - Implementation location: `workspace/benchmarking/provisioning.py`
  - Status: `DONE`

- Name: Single-agent OpenClaw baseline runner
  - Description: Builds a time-budget-aware prompt from the runner convergence policy, includes run-local visual bundle instructions when a record has localized visual inputs, shells out through the single-LLM OpenClaw wrapper, validates wrapper stdout against the strict agent result contract before answer extraction, normalizes answer tracks only from schema-valid `payloads[].text`, records transcript tool/turn diagnostics without enforcing them as limits, recovers a complete transcript answer from timeout-like OpenClaw output as scoreable `RunStatus.RECOVERED`, and marks a record failed/unscored if stdout is invalid, wrapper postflight metadata shows the fixed agent's `main` session entry did not point to the requested run-scoped `sessionId`, or a schema-valid OpenClaw timeout sentinel payload reports an aborted/blocked response with no recoverable complete answer.
  - Input / Output:
    - Input: benchmark record, group config, runtime bundle root.
    - Output: `RunnerResult` with `runner_meta.session_isolation`, `runner_meta.stdout_diagnostics`, `runner_meta.convergence_policy`, `runner_meta.convergence`, and `runner_meta.skill_use_audit` metadata. Invalid stdout returns `RunStatus.FAILED` and `FailureInfo.code = agent_result_contract_invalid`; recovered transcript answers return `RunStatus.RECOVERED` with `RecoveryInfo.source = single-llm-session-transcript`; unrecovered OpenClaw response-timeout sentinel payloads with aborted/blocked metadata return `RunStatus.FAILED`, `FailureInfo.code = agent_response_timeout`, empty answer tracks, and raw payload text retained in diagnostics instead of evaluation input.
    - Prompt behavior: `build_single_llm_prompt` receives the per-record convergence timeout and tells the model to stop starting new tool/skill exploration once roughly 20% or less of the budget remains, then produce the required final answer format from evidence already gathered. The prompt is advisory; enforcement/recovery lives in runner/wrapper metadata and transcript handling.
  - Implementation location: `workspace/benchmarking/runners/single_llm.py`, `workspace/benchmarking/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/convergence.py`, `workspace/benchmarking/result_contract.py`
  - Status: `DONE`

- Name: ChemQA benchmark runner
  - Description: Launches ChemQA preset flow with run-local visual bundle context when present, derives an immutable benchmark answer kind, waits for benchmark-visible terminal run-status under the shared convergence policy, triggers bounded recovery when run-status stops changing, fails with structured `convergence_limit_exceeded` metadata when policy limits are exceeded, prefers canonical Artifact Flow paths, archives artifacts, keeps legacy reconstruction/fallback for compatibility, marks evaluable recovered candidate submissions as scoreable degraded executions, and writes cleanup manifest.
  - Input / Output:
    - Input: benchmark record, ChemQA skill root, config path, slot set, profile/round overrides.
    - Output: `RunnerResult` plus archived artifact tree including canonical final/failure artifacts when available.
  - Implementation location: `workspace/benchmarking/runners/chemqa.py`
  - Status: `DONE`

- Name: DebateClaw preset compile/materialize/launch
  - Description: Compiles run plan from preset, materializes prompt bundles/command map/template, optionally prints or runs `clawteam launch`.
  - Input / Output:
    - Input: preset, goal, optional run/model/round overrides.
    - Output: compiled run-plan JSON, materialized runtime files, optional launch command/result.
  - Implementation location: `workspace/skills/debateclaw-v1/scripts/compile_runplan.py`, `materialize_runplan.py`, `launch_from_preset.py`, `launch_from_config.py`
  - Status: `DONE`

- Name: DebateClaw fixed-slot provisioning
  - Description: Ensures OpenClaw debate slots exist, injects provider/model config, writes command-map payload.
  - Input / Output:
    - Input: provider families, proposer count, env/config paths.
    - Output: slot workspaces, updated OpenClaw config, command map.
  - Implementation location: `workspace/skills/debateclaw-v1/scripts/ensure_openclaw_debate.py`
  - Status: `DONE`

- Name: Debate state machine CLI
  - Description: Stores debate state in SQLite, handles proposal/review/rebuttal submission, computes next action, advances phases/epochs, renders summaries.
  - Input / Output:
    - Input: CLI subcommands plus team/agent/file arguments.
    - Output: JSON/text protocol state and stored artifacts under ClawTeam data dir.
  - Implementation location: `workspace/skills/debateclaw-v1/scripts/debate_state.py`
  - Status: `DONE`

- Name: ChemQA coordinator/worker driver
  - Description: Runs long-lived coordinator/worker loops for each role, queries debate state, updates task status, saves sessions, writes cleanroom leases, executes candidate/review/rebuttal work as phase-scoped multi-turn loops inside one OpenClaw session, classifies missing/invalid/stale artifacts separately from true lane failure, and separates DebateClaw protocol terminal state from ChemQA benchmark terminal state while Artifact Flow finalizes outputs.
  - Input / Output:
    - Input: team, role, slot, session id, prompt/config/runtime paths.
    - Output: live task/session side effects, role-phase diagnostics in run status/blocker payloads, and protocol artifacts.
  - Implementation location: `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
  - Status: `DONE`

- Name: DebateClaw OpenClaw turn wrapper
  - Description: Runs one OpenClaw turn for a fixed DebateClaw slot, keeps slot session/workspace isolation, and can emit a structured turn-result sidecar with transcript path, stop reason, tool-call count, and assistant tail for ChemQA driver diagnostics.
  - Input / Output:
    - Input: slot id, session id, prompt/message, timeout/thinking overrides, optional `--turn-result-file`.
    - Output: OpenClaw subprocess exit code plus optional turn-result JSON sidecar.
  - Implementation location: `workspace/skills/debateclaw-v1/scripts/openclaw_debate_agent.py`
  - Status: `DONE`

- Name: ChemQA Artifact Flow
  - Description: Validates typed candidate/review/rebuttal artifacts, resolves answer-kind-specific projections, applies answer-revision rebuttals to a current candidate view, tracks review item closure state, writes canonical final/failure artifacts, writes an artifact manifest with hashes, and projects legacy-compatible `qa_result.json`. Supported answer projections include numeric, formula-style short symbolic expressions, short text, multi-part research, multiple-choice, structure, and generic semantic answers.
  - Input / Output:
    - Input: resolved `answer_kind`, protocol payloads, candidate/review/rebuttal artifacts, finalization metadata.
    - Output: `final_answer_artifact.json` or `failure_artifact.json`, `candidate_view.json`, `validation_summary.json`, `artifact_manifest.json`, `qa_result.json`, and run-status overlay fields.
  - Implementation location: `workspace/skills/chemqa-review/scripts/chemqa_artifact_flow.py`, `workspace/skills/chemqa-review/scripts/collect_artifacts.py`
  - Status: `DONE`

- Name: ChemQA artifact reconstruction
  - Description: Validates `chemqa_review_protocol`, preserves legacy artifact files, and delegates terminal answer/failure projection to Artifact Flow.
  - Input / Output:
    - Input: protocol file/source directory.
    - Output: normalized artifact directory with legacy files plus canonical terminal artifacts and manifest.
  - Implementation location: `workspace/skills/chemqa-review/scripts/collect_artifacts.py`
  - Status: `DONE`

- Name: ChemQA run recovery
  - Description: Repairs invalid review state, respawns missing workers and a dead coordinator control loop, replays placeholder transport reviews, advances stalled runs, and reports respawn-only recovery cycles as progress so coordinator stagnation handling does not terminal-fail while replacement workers are still booting. Respawn budget tracking is stored in `spawn_registry.json` and is updated while iterating a snapshot of role entries so missing process recovery can initialize budget metadata without aborting the recovery pass.
  - Input / Output:
    - Input: team id, workspace/runtime roots, max steps/respawn budget.
    - Output: JSON recovery summary plus runtime mutations.
  - Implementation location: `workspace/skills/chemqa-review/scripts/recover_run.py`
  - Status: `DONE`

- Name: ChemQA liveness check
  - Description: Fetches compact state snapshot and ClawTeam task list, reports missing roles and recommendation.
  - Input / Output:
    - Input: skill root, team, agent.
    - Output: JSON health payload.
  - Implementation location: `workspace/skills/chemqa-review/scripts/check_run_liveness.py`
  - Status: `DONE`

- Name: RDKit provider skill
  - Description: Runs deterministic local cheminformatics helpers for canonicalization, descriptors, substructure, stereochemistry, similarity, reactions, conformers, and symmetry heuristics.
  - Input / Output:
    - Input: request JSON plus output directory.
    - Output: stable `result.json` payloads with structured diagnostics/tool traces.
  - Implementation location: `workspace/skills/rdkit/*`
  - Status: `DONE`

- Name: PubChem provider skill
  - Description: Resolves names, CIDs, formulas, properties, synonyms, and similarity lookups through PubChem PUG REST.
  - Input / Output:
    - Input: request JSON plus output directory.
    - Output: stable `result.json` payloads with provider health, source traces, and structured diagnostics.
  - Implementation location: `workspace/skills/pubchem/*`
  - Status: `DONE`

- Name: OPSIN provider skill
  - Description: Resolves systematic chemical names to structures through OPSIN with structured diagnostics and optional RDKit validation handoff.
  - Input / Output:
    - Input: request JSON plus output directory.
    - Output: stable `result.json` payloads with parse diagnostics and optional validation status.
  - Implementation location: `workspace/skills/opsin/*`
  - Status: `DONE`

- Name: Chemistry calculator skill
  - Description: Runs deterministic local chemistry calculations for stoichiometry, concentration, equilibria, acid/base, gas-law, electrochemistry, units, and answer checks.
  - Input / Output:
    - Input: request JSON plus output directory.
    - Output: stable `result.json` payloads with structured calculation traces and diagnostics.
  - Implementation location: `workspace/skills/chem-calculator/*`
  - Status: `DONE`

- Name: Autonomous benchmark skill discovery and audit
  - Description: Skills-on benchmark runs keep the health-available benchmark skill allowlist available for each single-LLM record, use a compact Hierarchical Skill Tree instead of deterministic selected-skill routing, and report post-run skill-use audit counters from actual tool execution metadata plus startup health summary.
  - Input / Output:
    - Input: benchmark record prompt plus effective configured benchmark skill allowlist.
    - Output: normal benchmark answer plus `runner_meta.skill_use_audit`, `skill-health.json`, runtime manifest skill-health summary, and aggregate skill tool-use counters.
  - Implementation location: `workspace/benchmarking/skill_tree.py`, `workspace/benchmarking/skill_audit.py`, `workspace/benchmarking/prompts.py`, `workspace/benchmarking/reporting.py`
  - Status: `DONE`

- Name: Benchmark skill runtime health and fixed script runner
  - Description: Runs startup health checks for benchmark-visible skills and routes agent-invoked local skill scripts through a fixed workspace `uv run python` runner. Health checks verify declared Python modules, paper PDF backend imports via the `paper-parse` optional extra, executables, API keys from process env/OpenClaw `.env`, data files, and network providers with per-skill probe timeouts. Unavailable skills are removed from effective skills-on allowlists and emitted as structured diagnostics.
  - Input / Output:
    - Input: benchmark skill allowlist plus workspace root and environment variables.
    - Output: `skill-health.json`, runtime manifest health summary/effective allowlists, and structured skill runner payloads with `available=false`, `error_kind`, and `reason` on failure.
  - Implementation location: `workspace/benchmarking/skill_health.py`, `workspace/benchmarking/skill_runtime.py`, `workspace/scripts/run_skill.py`, `workspace/benchmark_test.py`
  - Status: `DONE`

- Name: Experimental chemistry skill optional dependencies
  - Description: Declares installable optional dependency groups for the subset of experimental chemistry skills that have stable Python-package dependencies. These extras support benchmark trials without making heavy materials, MD, ML, database, or workflow packages part of the default runtime.
  - Input / Output:
    - Input: `chemqa[chem-materials]`, `chemqa[chem-quantum-parse]`, `chemqa[chem-bioactivity]`, `chemqa[chem-md]`, `chemqa[chem-cheminformatics-ml]`, `chemqa[chem-materials-ml]`, `chemqa[chem-workflows]`, or aggregate `chemqa[chem-experimental]`.
    - Output: Optional Python package dependencies for skill scripts and examples where packages are resolvable through PyPI/uv.
  - Implementation location: `workspace/pyproject.toml`, `workspace/uv.lock`
  - Status: `DONE_EXPERIMENTAL`

- Name: Native ChemQA workflow package
  - Description: Declares an inactive scaffold class with hooks for initialize/next-action/submit/advance/status/summary/finalize. It is retained only as future workflow-package metadata; live ChemQA runs do not load it as the control plane.
  - Input / Output:
    - Input: run config/state/role/payload.
    - Output: updated state or action/status payload.
  - Implementation location: `workspace/skills/chemqa-review/runtime/workflow.py`, `workspace/skills/chemqa-review/workflows/chemqa-review@1.json`
  - Status: `PARTIAL_INACTIVE_SCAFFOLD`

- Name: Workflow package loader
  - Description: Loads a workflow package from module/path and validates required attributes/methods.
  - Input / Output:
    - Input: workflow package spec payload.
    - Output: instantiated workflow object.
  - Implementation location: `workspace/skills/debateclaw-v1/scripts/workflow_loader.py`
  - Status: `PARTIAL`

- Name: Paper retrieval
  - Description: Queries OpenAlex, Semantic Scholar, and Crossref; deduplicates candidates and scores them heuristically.
  - Input / Output:
    - Input: query, must/exclude terms, year range, preferred sources, limit.
    - Output: `papers`, provider diagnostics, provider health.
  - Implementation location: `workspace/skills/paper-retrieval/scripts/paper_retrieval.py`
  - Status: `DONE`

- Name: Paper access
  - Description: Resolves open-access source URL, optionally probes for PDF, downloads PDF/text/binary artifact, writes `access_result.json`.
  - Input / Output:
    - Input: request JSON with document candidates and optional Unpaywall email.
    - Output: localized artifacts and access metadata.
  - Implementation location: `workspace/skills/paper-access/scripts/paper_access.py`
  - Status: `DONE`

- Name: Paper parsing
  - Description: Parses PDF/text documents, chooses MinerU/PyMuPDF backends, extracts sections/blocks/snippets, writes `parse_result.json`.
  - Input / Output:
    - Input: local file path, output dir, optional parser config JSON.
    - Output: normalized parsed document artifacts.
  - Implementation location: `workspace/skills/paper-parse/scripts/paper_parse.py`
  - Status: `DONE`

- Name: Paper reranking
  - Description: Builds GROBID XML profiles for local PDFs, then calls an OpenAI-compatible chat-completions API to lock/drop candidates.
  - Input / Output:
    - Input: request JSON with candidate list, local PDFs, GROBID config, LLM config.
    - Output: `rerank_result.json` with decisions and profile status.
  - Implementation location: `workspace/skills/paper-rerank/scripts/paper_rerank.py`
  - Status: `DONE`

- Name: Benchmark cleanroom manifest and lease tracking
  - Description: Tracks per-run processes/session assignments/artifact roots and writes/removes lease files.
  - Input / Output:
    - Input: run metadata, role/slot/session identifiers.
    - Output: manifest JSON and lease JSON files.
  - Implementation location: `workspace/skills/benchmark-cleanroom/scripts/runtime_lease.py`
  - Status: `DONE`

- Name: Benchmark cleanup executor
  - Description: Terminates related processes from leases and process scans, but does not scrub session stores or remove run-scoped session/artifact/config/manifest files.
  - Input / Output:
    - Input: cleanup manifest or explicit run parameters.
    - Output: cleanup report JSON with retained `session_store_scrub` and `removed_paths` fields left empty for compatibility.
  - Implementation location: `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`
  - Status: `DONE`

- Name: Local paper service control
  - Description: Starts/stops/checks Docker-backed GROBID and native macOS MinerU API services.
  - Input / Output:
    - Input: subcommand such as `up`, `down`, `health`, `logs`, `install`, or `download-models`.
    - Output: Docker Compose actions for GROBID, native process actions for MinerU, and HTTP health checks.
  - Implementation location: `workspace/scripts/docker_services.sh`, `workspace/scripts/mineru_service.sh`
  - Status: `DONE`

- Name: ChemBench dataset extraction
  - Description: Pulls ChemBench rows from Hugging Face datasets-server and extracts open-ended reasoning tasks into a pool.
  - Input / Output:
    - Input: dataset name and output paths.
    - Output: JSONL pool + manifest.
  - Implementation location: `workspace/benchmarks/chembench/extract_open_ended_reasoning_pool.py`
  - Status: `DONE`

- Name: FrontierScience dataset extraction
  - Description: Merges olympiad and research JSONL inputs into a chemistry-only pool.
  - Input / Output:
    - Input: olympiad/research JSONL files.
    - Output: JSONL pool + manifest.
  - Implementation location: `workspace/benchmarks/frontierscience/extract_chemistry_pool.py`
  - Status: `DONE`

- Name: SuperChem dataset extraction
  - Description: Reads SUPERChem rows from datasets-server or zip/parquet fallback, localizes assets, emits a multimodal pool with asset paths stored relative to the output JSONL directory when that context is available.
  - Input / Output:
    - Input: dataset name, output JSONL/assets paths.
    - Output: JSONL pool + manifest + assets.
  - Implementation location: `workspace/benchmarks/superchem/extract_superchem_pool.py`
  - Status: `DONE`

- Name: Web UI / API server
  - Description: Optional dependencies suggest planned FastAPI/Gradio/OpenAI-based UI surfaces.
  - Input / Output:
    - Input: UNKNOWN
    - Output: UNKNOWN
  - Implementation location: `workspace/pyproject.toml` only
  - Status: `NOT_IMPLEMENTED`

## 4. Actual Behavior
- Primary execution flow: three-group skills benchmark
  - `workspace/benchmark_test.py` parses CLI args and discovers benchmark JSONL files under `workspace/benchmarks/*/data/*.jsonl` unless explicit files/datasets are provided.
  - On import, `workspace/benchmark_test.py` ensures the workspace source root is on `sys.path` and imports benchmark internals via top-level `benchmarking.*`/`runtime_paths`, so loading the entrypoint by absolute path does not depend on a resolvable parent `workspace` package.
  - It normalizes records through `benchmarking.datasets.load_records`.
  - It runs benchmark skill health checks through `benchmarking.skill_health.check_all_skill_health`, writes `output_root/skill-health.json`, derives effective experiment specs by removing unavailable skills from skills-on allowlists, and includes the health summary/effective allowlists in `runtime-manifest.json`.
  - It builds per-group run-scoped OpenClaw configs in `output_root/runtime-config/` through `benchmarking.runtime_config.ConfigPool`; the ConfigPool receives the effective experiment specs after health filtering. `benchmark_test.py` keeps compatibility wrappers around that package module.
  - Default groups are `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on`; all set `websearch=True`, so the old web-on/web-off matrix is no longer an experiment axis.
  - `BENCHMARK_SKILLS_ALLOWLIST` is loaded by `benchmarking.skill_tree.benchmark_skill_allowlist()` from the historical `workspace/skills/chemistry-routing-matrix.json` inventory (`skills[].skill`). Startup health filtering writes only available skills to skills-on runner agents. `single_llm_skills_off` writes an explicit empty runner `skills: []`. Judge configs do not receive the benchmark allowlist.
  - Runtime configs add `workspace/skills` to `skills.load.extraDirs` so run-scoped benchmark workspaces can discover the newly available local skills.
  - Before dispatching a record, `benchmark_test.py` materializes run-local input bundles for benchmark visual inputs. SuperChem bundles copy resolved images into `images/` and rewrite `question.md` to reference those local files; required multimodal images that cannot be resolved raise `BenchmarkError`. HLE bundles decode base64 image data or copy local image files so prompts with "provided information" retain their visual context.
  - For `single_llm_*` groups:
    - The runner shells out through `benchmarking/single_llm_openclaw_wrapper.py`, which invokes `openclaw agent --local ... --json`, validates stdout with `benchmarking.result_contract`, and normalizes invalid stdout into `result.meta.stdout_diagnostics`.
    - It does not use a native Python OpenClaw API.
    - Invalid stdout, such as tool argument JSON without schema-valid answer payloads, is never passed to answer extraction or the evaluator. The record becomes failed/unscored with `agent_result_contract_invalid`.
    - Runner-level convergence policy is written into top-level results/manifests and per-record `runner_meta`; the wrapper records transcript assistant-turn/tool-call diagnostics and can recover the latest complete benchmark answer from the session transcript after timeout-like OpenClaw output. Tool/turn counts are never hard failure limits.
    - `single_llm_skills_on` includes the compact health-filtered Hierarchical Skill Tree in the prompt; `single_llm_skills_off` omits it and explicitly forbids OpenClaw/local skill tools.
    - When an input bundle exists, the prompt names the bundle directory, tells the agent to read `question.md`, and explicitly instructs it to inspect referenced local images before answering.
    - The prompt tells the model to run local skill scripts through `scripts/run_skill.py`, which executes target scripts with workspace `uv run python`.
    - The single-agent runner writes `runner_meta.skill_use_audit` after OpenClaw returns, including configured skill count/list, startup skill-health summary, tool-call counts, tool names, model-declared skipped traces, and no-tool-call flags.
  - For `chemqa_*` groups:
    - The runner shells out to ChemQA skill scripts to compile/materialize/launch the run.
    - The same convergence policy controls unchanged-status recovery attempts and max recovery attempts before a structured `convergence_limit_exceeded` failure.
    - When an input bundle exists, the ChemQA goal names the bundle and instructs workers to open `question.md` and inspect referenced images; the bundle directory is also passed as an additional file workspace.
    - It monitors run status via files under `chemqa-review/control/run-status/`.
    - If run-status remains unchanged across polling intervals, it invokes `chemqa-review/scripts/recover_run.py` with the run-scoped `CLAWTEAM_DATA_DIR`; repeated recovery attempts are rate-limited while the status signature remains unchanged.
    - While a worker phase is still in progress, run status may carry a `role_phase` block with turn index, max turns, classification such as `waiting_for_artifact` / `repairing_invalid_artifact` / `repairing_stale_artifact`, and the last structured turn/artifact diagnostics.
    - It treats DebateClaw `phase=done/status=done` as protocol terminal only while Artifact Flow is still `finalizing`; benchmark-visible `status=done/terminal_state=completed|failed` is published only after canonical final/failure artifacts, manifest, and `qa_result.json` are readable.
    - It prefers canonical `qa_result_path`, `final_answer_artifact_path`, `failure_artifact_path`, and `artifact_manifest_path` from run status. If artifacts are missing, it tries to rebuild them from protocol files with `collect_artifacts.py`.
    - If the final `qa_result.json` is still missing or unusable, it can fall back to the latest archived `proposer-1` proposal or `final_answer_preview`.
  - All per-record outputs are persisted immediately under `per-record/<group>/<slug>.json`.
  - Cleanup manifests are registered and benchmark-cleanroom process finalization runs in `finally`/signal/atexit paths; session stores, transcripts, and run artifacts remain on disk for audit.

- Real ChemQA control path
  - The operational state machine is `workspace/skills/debateclaw-v1/scripts/debate_state.py`, not `workspace/skills/chemqa-review/runtime/workflow.py`.
  - Compiled ChemQA run plans declare `runtime_context.chemqa_review.control_plane = debate_state_driver`; the `ChemQAWorkflow` package is nested under `workflow_package_scaffold` with `active: false` and is not advertised as an active runtime package.
  - `chemqa_review_openclaw_driver.py` loops by repeatedly calling `debate_state.py` subcommands in subprocesses.
  - The driver updates ClawTeam task state, saves sessions, opens/removes cleanup leases, emits role-specific artifacts, and now treats one OpenClaw turn as a turn boundary rather than a phase-failure boundary.
  - Candidate / formal-review / rebuttal production is phase-scoped: the driver can reuse the same `session_id` across multiple turns, observe the required artifact after each turn, feed back missing/invalid/stale state, and only mark lane failure after phase budget exhaustion or a hard wrapper error.
  - Materialized ChemQA role prompts now pass `--runtime-dir` to the compact state snapshot helper so compact snapshot and fallback commands resolve the same run-scoped DebateClaw runtime helpers.
  - When DebateClaw reports protocol terminal conditions, the driver publishes `artifact_flow_state=finalizing` while keeping legacy `status=running`; after `collect_artifacts.py` / Artifact Flow writes terminal artifacts, run status carries `artifact_flow_state=finalized|finalization_failed`, `benchmark_terminal_state`, canonical paths, and legacy-compatible terminal fields.
  - Coordinator protocol generation treats the deterministic protocol scaffold as primary; model refinement is optional quality improvement and falls back to the deterministic scaffold when the refinement turn aborts, times out without a valid rewrite, or leaves invalid protocol output.
  - Rebuttal artifacts now carry explicit `mode`: `response_only`, `answer_revision`, or `concession`. Only `answer_revision` updates the Artifact Flow current candidate view.
  - `chemqa-review/scripts/bundle_common.py` and the prompt pack now treat all skills listed in `skills/chemistry-routing-matrix.json` as required sibling skills alongside DebateClaw and the paper pipeline.
  - ChemQA proposer prompts use full-availability skill discovery wording: provider skills can be used directly when they help, full `SKILL.md` files should be read only for skills about to be used, and unexecuted skills are not valid provider traces.
  - `benchmarking/prompts.py` injects the compact Hierarchical Skill Tree into single-agent benchmark prompts, so single-agent skills-on runs receive domain/family discovery guidance rather than record-level route selection.
  - Single-agent benchmark turns use a fixed OpenClaw agent per experiment group and rely on `benchmarking/single_llm_openclaw_wrapper.py` to delete stale `agent:<id>:main` entries before each run-scoped session turn; old transcript files are not deleted.
  - `pyproject.toml` exposes optional experimental chemistry extras for PyPI-resolvable dependency families. `chemqa[chem-experimental]` aggregates those families but is intentionally not included in `chemqa[full]`, and OpenFF/tooluniverse/HPC executable stacks remain conda, preinstalled, API, or external-service dependencies described by their skill docs rather than default pip dependencies.
  - The shared ChemQA prompt module is named for the fixed-lane protocol rather than native workflow-package execution, so prompt assembly does not imply that `ChemQAWorkflow` is active.
  - ChemQA candidate submissions are validated in provider-trace audit mode by default. The policy audits provider traces the model actually submits; skipped provider traces are invalid and do not satisfy tool-backed evidence.
  - Materialized role commands can pass `--provider-trace-mode off|audit|enforce`, and `CHEMQA_PROVIDER_TRACE_MODE` is a fallback when the flag is absent. `enforce` turns incomplete or skipped model-submitted provider traces into candidate artifact validation errors.
  - Reviewer prompt contracts now treat a missing provider artifact or structured `tool_trace` as a finding when the candidate explicitly relies on tool-backed calculation, molecular structure, compound identity, literature evidence, database lookup, spectra, materials, simulation, or workflow evidence.
  - This integration phase does not add a dedicated image-reading or OCSR skill to ChemQA prompt discovery.
  - Recovery is externalized:
    - `recover_run.py` inspects the same runtime files and database,
    - repairs invalid review phases,
    - respawns missing role processes from `spawn_registry.json`, including the coordinator when the protocol is not terminal and the coordinator action is `advance` or `wait`,
    - treats concrete recovery actions such as respawn/submit/advance as protocol progress for coordinator stagnation accounting, even before the DebateClaw phase signature changes,
    - writes respawn stdout/stderr to per-role files under `spawn-logs/`,
    - may inject placeholder/transport artifacts to keep the run moving.

- Real DebateClaw control path
  - Debate runs are compiled from JSON presets and materialized into:
    - runplans,
    - prompt bundles,
    - command maps,
    - template files,
    - run-scoped OpenClaw configs.
  - `launch_from_preset.py` and `launch_from_config.py` are wrappers around compile/materialize/launch subprocesses.
  - Slot isolation is enforced by `.debateclaw-slot.json` sentinel files plus workspace resets when session id changes.

- Real paper-processing path
  - Retrieval -> access -> parse -> rerank are independent scripts, not a single orchestrated service.
  - `paper-rerank.py` requires already-downloaded local PDFs.
  - `paper-parse.py` can use a long-lived MinerU API URL from env/config or local backend fallback logic.
  - GROBID is treated as a required long-lived Docker-backed local HTTP service.
  - MinerU is treated as a required long-lived native macOS local HTTP service when complex PDF parsing/OCR is needed; the service helper installs the native runtime, pre-downloads models, and defaults runtime model loading to `MINERU_MODEL_SOURCE=local`.

- Shortcuts, hacks, implicit logic
  - Benchmark scripts duplicate a large amount of logic that also exists in `workspace/benchmarking/*`; the package is not the sole orchestration layer.
  - `benchmark_test.py` contains direct JSON parsing, subprocess wrappers, cleanup wiring, and runner glue instead of delegating all logic to package modules, though ChemQA run-status normalization/result-axis derivation, benchmark prompt construction, runtime config orchestration, and evaluator implementations now live in `workspace/benchmarking/status.py`, `workspace/benchmarking/prompts.py`, `workspace/benchmarking/runtime_config.py`, and `workspace/benchmarking/evaluators.py`.
  - Native workflow package support exists as inactive scaffold metadata, but current live ChemQA execution bypasses it in favor of CLI/state-script orchestration.
  - Run-scoped OpenClaw configs are produced by mutating a copy of the user’s local `~/.openclaw/openclaw.json`.
  - Recovery and artifact collection rely on specific file naming conventions such as `proposer-1.md`, `chemqa_review_protocol.yaml`, `qa_result.json`.
  - Cleanup correctness depends on manifests being written before launch and on command/session naming matching run ids.

## 5. Gap Analysis
- Missing features
  - `NOT_IMPLEMENTED`: No actual FastAPI/Gradio/uvicorn application code despite optional `web-ui` dependencies in `workspace/pyproject.toml`.
  - `NOT_IMPLEMENTED`: No active code path that uses `workflow_loader.py` to load `chemqa-review` native workflow packages.

- Incomplete implementations
  - `PARTIAL_INACTIVE_SCAFFOLD`: `workspace/skills/chemqa-review/runtime/workflow.py`
    - `advance()` returns the state unchanged.
    - `submit_artifact()` just appends generic artifacts.
    - No real review/rebuttal/acceptance logic.
  - `PARTIAL`: `workspace/skills/chemqa-review/runtime/state_models.py`
    - Provides only initial state defaults.
    - Does not implement transitions or validation.
  - `PARTIAL_INACTIVE_SCAFFOLD`: Workflow JSON under `workspace/skills/chemqa-review/workflows/chemqa-review@1.json`
    - Declares the inactive scaffold package and parameters, while the operational runtime explicitly depends on `debate_state.py` and driver scripts.
  - `PARTIAL`: `workspace/skills/debateclaw-v1/scripts/workflow_loader.py`
    - Implemented loader/validator, but repository search shows no active caller.
- Architectural inconsistencies
  - Intended architecture suggests package-based workflows and reusable modules.
  - Actual behavior is still script-heavy and subprocess-heavy:
    - `benchmark_test.py` is a monolithic entrypoint with embedded orchestration logic.
    - ChemQA runs are controlled through external state scripts instead of the inactive native workflow-package scaffold.
  - `workspace/benchmarking/` exists as a reusable layer, but benchmark entry scripts still duplicate significant behavior.
  - `workspace/pyproject.toml` advertises `web-ui` extras, but there is no corresponding app module.
  - Top-level repo contains a mix of source, runtime state, generated artifacts, logs, and secret-bearing config in one tree; module boundaries are not clean at the repository level.

## 6. Risks & Technical Debt
- Fragile logic
  - Artifact recovery depends on specific filenames and directory heuristics in `workspace/benchmarking/runners/chemqa.py`.
  - Cleanup depends on manifests and process command-line matching in `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`; session/artifact retention is intentional and may require separate manual pruning outside benchmark correctness paths.
  - ChemQA recovery depends on `spawn_registry.json`, `/proc`-style process inspection when available, and workspace naming conventions in `workspace/skills/chemqa-review/scripts/recover_run.py`.

- Hardcoded values
  - Default OpenClaw home/config roots are hardcoded in `workspace/runtime_paths.py`.
  - Default model ids, agent ids, workspace roots, slot sets, and timeouts are hardcoded in `workspace/benchmark_test.py`.
  - GROBID and MinerU default URLs are hardcoded in docs/scripts.
  - ChemQA role topology is fixed to one candidate owner plus four reviewer lanes in `workspace/skills/chemqa-review/runtime/state_models.py` and associated scripts.

- Missing abstractions
  - Benchmark CLI scripts combine CLI parsing, orchestration, evaluation, config generation, and fallback handling in single files.
  - Native workflow-package abstraction exists but is not the live control plane.
  - Paper tools are standalone scripts with no shared higher-level orchestrator.
  - OpenClaw/ClawTeam integration is done through subprocess calls everywhere; there is no local adapter interface.

- Operational risks
  - `openclaw.json` at repo root contains live gateway/auth/provider configuration and is reused as a mutable base for runtime configs.
  - Repo root stores live runtime state, backups, SQLite DBs, session logs, and generated artifacts beside source.
  - Optional dependencies listed in `pyproject.toml` may imply capabilities that do not actually exist in code.

## 7. Suggested Next Steps
- Replace or retire the inactive native workflow package scaffold:
  - Either make `workspace/skills/chemqa-review/runtime/workflow.py` the real execution engine behind parity tests or remove the scaffold once no planned workflow-package migration remains.
- Collapse duplicated benchmark orchestration logic:
  - Move more logic from `workspace/benchmark_test.py` into `workspace/benchmarking/`.
- Separate source from runtime state:
  - Move generated workspaces, logs, DBs, and mutable OpenClaw runtime state outside the analyzed source tree or document them as runtime-only roots.
- Remove or implement misleading declared surfaces:
  - Either add a real web UI/API module for the `web-ui` extras or drop those extras from the project metadata.
- Harden artifact and cleanup flows:
  - Continue reducing filename/path guessing in legacy ChemQA artifact recovery paths now that canonical Artifact Flow paths exist.
  - Centralize run manifest/session/process metadata contracts used by runners, drivers, cleanup, and single-LLM session-isolation audits.
- Add clearer ownership boundaries:
  - Separate DebateClaw engine logic, ChemQA protocol logic, benchmark orchestration, and paper pipeline into smaller modules with fewer embedded subprocess wrappers.
