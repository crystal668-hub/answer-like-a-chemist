# GLOBAL DEV SPEC

## 1. Project Overview
- Purpose
  - `.openclaw/` is a local OpenClaw runtime home that also contains a Python workspace for chemistry benchmark orchestration, DebateClaw debate workflows, ChemQA-style review workflows, paper retrieval/access/parse/rerank utilities, and benchmark cleanup tooling.
  - The main executable source code lives under `workspace/`.
  - The repo root also stores live runtime state for OpenClaw and ClawTeam: agent configs, generated workspaces, SQLite state, logs, device/auth files, and task/session registries.
- Current capabilities (ONLY what works)
  - `DONE`: Run benchmark batches across three skills experiment groups: `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on` via `workspace/benchmarking/workflow/cli.py`; `workspace/benchmark_test.py` is a thin compatibility facade for the historical script/import path. All groups keep websearch enabled, and the experiment variable is the health-filtered benchmark skills allowlist.
  - `DONE`: Load benchmark JSONL datasets into a normalized `BenchmarkRecord` model via `workspace/benchmarking/core/datasets.py`.
  - `DONE`: Score outputs with registered evaluators for ChemBench, FrontierScience Olympiad/Research, SuperChem, HLE, and generic semantic matching via `workspace/benchmarking/scoring/evaluators.py` and `workspace/benchmarking/scoring/evaluation.py`.
  - `DONE`: Provision run-scoped OpenClaw configs and DebateClaw/ChemQA slot workspaces via `workspace/benchmarking/runtime/config_pool.py`, `workspace/benchmarking/runtime/config.py`, and `workspace/benchmarking/runtime/provisioning.py`.
  - `DONE`: Run a single-agent OpenClaw baseline through a benchmark wrapper that gives each record a run-scoped `sessionId`, clears only stale `agent:<id>:main` session-store pointers before the turn, injects time-budget and coverage-checklist-aware answer instructions, validates OpenClaw stdout against a strict agent result schema before answer extraction, preserves payload error markers such as `payloads[].isError`, applies runner-level convergence policy metadata and transcript recovery for complete benchmark answers, classifies OpenClaw agent error payloads such as `stream_read_error` and no-response fallbacks before candidate-answer contract validation, classifies wrapper/OpenClaw subprocess failures at the stdout/stderr boundary into structured execution errors with `code`, `layer`, `source`, and `retryable` fields before retry decisions, performs one bounded same-session finalization rescue for error-like payloads when transcript recovery finds no complete answer and session isolation is healthy, gives the outer wrapper subprocess enough wall-clock budget to cover the primary OpenClaw turn plus finalization-rescue grace plus cleanup buffer, adds cross-session retry focus guidance only for skills-enabled single-agent timeout retries so later attempts reduce tool exploration and finalize promptly, validates an internal candidate-answer contract before evaluation, treats unrecovered OpenClaw timeout sentinel payloads as failed/non-scoreable runs, and preserves historical transcript files via `workspace/benchmarking/workflow/runners/single_llm.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/runtime/session_isolation.py`, `workspace/benchmarking/core/convergence.py`, and `workspace/benchmarking/core/result_contract.py`. Prompt convergence guidance asks agents to follow the `act-like-a-chemist` Benchmark Coverage Checklist, call tools only for concrete checklist gaps, mark unresolved gaps blocked after the failure budget, and finalize once coverage is sufficient or blocked. Tool/turn counts and checklist diagnostics remain metadata only and are not enforced as hard exploration limits. Complete-answer marker handling accepts plain `FINAL ANSWER:` lines and the common Markdown-bold forms `**FINAL ANSWER:** answer` and `**FINAL ANSWER: answer**` when a non-empty answer follows the marker.
  - `DONE`: Run a ChemQA multi-agent workflow by compiling/materializing a ChemQA launch, monitoring benchmark-visible run-status, applying runner-level convergence policy to unchanged-status recovery attempts, consuming canonical Artifact Flow outputs, archiving outputs, and cleaning runtime leftovers via `workspace/benchmarking/workflow/runners/chemqa.py`.
  - `DONE`: Manage DebateClaw V1 runtime, slot provisioning, prompt/materialization, and launch commands via `workspace/skills/debateclaw-v1/scripts/*.py`.
  - `DONE`: Maintain live debate protocol state in SQLite and expose CLI commands for init/status/next-action/submit/advance via `workspace/skills/debateclaw-v1/scripts/debate_state.py`.
  - `DONE`: Drive ChemQA reviewer/proposer/coordinator loops on top of DebateClaw state via `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`, including phase-scoped multi-turn artifact production, role-phase run-status diagnostics, and deterministic coordinator fallback when model refinement aborts or leaves no valid protocol rewrite.
  - `DONE`: Recover stalled ChemQA runs, respawn dead workers, and repair invalid protocol state via `workspace/skills/chemqa-review/scripts/recover_run.py`.
  - `DONE`: Collect ChemQA protocol outputs through Artifact Flow into canonical terminal artifacts, `artifact_manifest.json`, and legacy-compatible `qa_result.json` via `workspace/skills/chemqa-review/scripts/chemqa_artifact_flow.py` and `collect_artifacts.py`; finalization applies structured `answer_revision` rebuttals and repairs numeric short-answer projections from anchored final values in the full answer when the raw direct answer is a setup/process sentence.
  - `DONE`: Provide deterministic first-batch chemistry provider skills for local structure reasoning, name resolution, public compound lookup, and numeric chemistry calculations via `workspace/skills/rdkit`, `workspace/skills/opsin`, `workspace/skills/pubchem`, and `workspace/skills/chem-calculator`.
  - `DONE`: Provide an experimental medium-or-higher-value chemistry skill inventory via `workspace/skills/chemistry-routing-matrix.json`, covering 85 local skills from a general chemistry solving SOP, structure/materials, atomistic simulation, quantum chemistry, bioactivity/safety, molecular/materials ML, databases, spectra/formats, paper retrieval/access/parse/rerank, and workflow automation. Despite the historical filename, runtime benchmark prompts now treat it as inventory data, not as a deterministic router.
  - `DONE`: Run benchmark skill health checks before skills-on groups. Startup health checks verify declared Python imports through workspace `uv run`, paper PDF backend imports through the `paper-parse` optional extra, executables, API keys loaded from process env or the OpenClaw runtime `.env`, data files, and network providers with per-skill probe timeouts for slower providers such as ChEMBL; unavailable skills are removed from effective runtime allowlists and reported in `skill-health.json` plus `runtime-manifest.json`. Benchmark startup also runs a generic `web_search` preflight for websearch-enabled groups, retries transient OpenClaw/DuckDuckGo failures up to three attempts, and fails those groups early only when web search remains unavailable after those attempts.
  - `DONE`: Provide a fixed skill script runner via `scripts/run_skill.py`; agent-invoked skill scripts run through workspace `uv run --project <canonical workspace> python` while preserving the agent workspace as the execution cwd for relative input/output paths, with `paper-parse` scripts executed via `uv run --project <canonical workspace> --extra paper-parse python`, and return structured unavailable payloads such as `invalid_workspace_root`, `missing_dependency`, `missing_executable`, `missing_api_key`, and `provider_failure`. Benchmark OpenClaw subprocess environments also prefix the workspace `.venv/bin` on `PATH` and set `VIRTUAL_ENV`/`PYTHONNOUSERSITE` as a compatibility fallback for legacy skill docs that still show direct `python` or `python3` examples; `scripts/run_skill.py` remains the canonical skill execution path.
  - `DONE`: Provide autonomous benchmark skill discovery and audit via `workspace/benchmarking/skills/tree.py`, `workspace/benchmarking/skills/audit.py`, and `workspace/benchmarking/core/reporting.py`: skills-on benchmark runs expose the health-filtered benchmark skill allowlist, prompts render a compact Hierarchical Skill Tree with `chemist-sop` as the SOP entry, ask skills-on agents to read `act-like-a-chemist` before selecting provider skills, explicitly show local script execution as `exec {"command": "..."}` with positive and negative examples, and post-run reporting tracks actual tool calls, tool errors, missing skill-doc reads, request-shape errors, and coverage-checklist presence separately from answer scoring.
  - `DONE`: Launch a default non-blocking automated benchmark evaluation and experience-extraction pass after every completed benchmark aggregation via `workspace/benchmarking/analysis/launcher.py` and `workspace/benchmarking/analysis/automated.py`. The analysis builds a run-local evidence bundle from `results.json`, per-record JSON, single-LLM transcripts, and ChemQA archived artifacts, resolves an executable Codex binary including the macOS Codex app bundle fallback, runs a read-only `hello` preflight, then invokes a read-only `codex exec` session with `gpt-5.5` and `xhigh` reasoning to write `analysis/report.json` and `analysis/report.md`; user-facing analysis content defaults to Chinese while preserving JSON field names and identifiers, and the Markdown report includes a deterministic per-record result table rendered from the local evidence bundle. Launch or analysis failure is diagnostic only and does not change the benchmark exit code.
  - `DONE`: Retrieve literature candidates from OpenAlex, Semantic Scholar, and Crossref via `workspace/skills/paper-retrieval/scripts/paper_retrieval.py`.
  - `DONE`: Resolve accessible paper artifacts using direct OA URLs and optional Unpaywall lookup via `workspace/skills/paper-access/scripts/paper_access.py`.
  - `DONE`: Parse local PDF/text documents with MinerU or PyMuPDF fallback via `workspace/skills/paper-parse/scripts/paper_parse.py`.
  - `DONE`: Rerank papers by building GROBID profiles and calling an OpenAI-compatible chat-completions endpoint via `workspace/skills/paper-rerank/scripts/paper_rerank.py`.
  - `DONE`: Terminate benchmark-owned leftover processes from manifests/leases while preserving session files, session stores, run-scoped artifacts, manifests, and cleanup reports via `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`.
  - `DONE`: Manage local Docker-backed GROBID via `workspace/scripts/docker_services.sh` and native macOS MinerU API via `workspace/scripts/mineru_service.sh`.
  - `DONE`: Repair temporary SuperChem benchmark JSONL image path state with `workspace/scripts/repair_temp_superchem_image_paths.py`, including stale absolute path rewriting and optional pruning of unused image paths based on per-record `/media/uploads/...` locators.
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
    - Defines global Anthropic-compatible MiniMax routes `minimax/MiniMax-M2.7` and `minimax/MiniMax-M2.7-highspeed` through `${MINIMAX_ANTHROPIC_BASE_URL}/v1` and `${MINIMAX_API_KEY}` with `authHeader: true`, `api: anthropic-messages`, a 204800-token context window, and a 65536-token single-turn output cap. The default primary model remains `su8/gpt-5.4`.
    - Defines global OpenAI-compatible Qwen route `qwen/qwen3.6-plus` through `${QWEN_BASE_URL}` and `${QWEN_API_KEY}` with `api: openai-completions`, Qwen thinking-format compatibility, a 1000000-token context window, and a 65536-token single-turn output cap.

- Source modules
  - `workspace/benchmarking/`
    - The canonical implementation is clustered by responsibility under subpackages. Flat compatibility modules under `workspace/benchmarking` have been removed; real implementation files live under `core/`, `scoring/`, `runtime/`, `skills/`, `analysis/`, and `workflow/`.
    - `core/`
      - `contracts.py`: defines `RunStatus`, `AnswerPayload`, `FailureInfo`, `RecoveryInfo`, `RunnerResult`.
      - `datasets.py`: normalizes benchmark records from JSONL.
      - `experiments.py`: defines `ExperimentSpec`.
      - `convergence.py`: defines runner convergence policy, transcript summary helpers, prompt/tool/checklist diagnostics, and complete-answer recovery from session transcripts.
      - `result_contract.py`: validates and normalizes OpenClaw agent stdout so only schema-valid `payloads[].text` entries become benchmark answers.
      - `status.py`: normalizes ChemQA run-status payloads and derives benchmark result status axes.
      - `reporting.py`: defines per-record result schema and aggregate summary buckets, including diagnostic tool/checklist counters that do not affect scoring.
    - `scoring/`
      - `evaluation.py`: registry/dispatch for evaluator functions.
      - `evaluators.py`: implements ChemBench, FrontierScience, SuperChem, HLE, and generic semantic scoring plus answer parsing helpers.
    - `runtime/`
      - `config.py`, `config_pool.py`, `provisioning.py`: render run-scoped OpenClaw configs, provision slot workspaces, and manage config path pooling.
      - `bundles.py`: materializes SuperChem/HLE run-local visual input bundles.
      - `cleanroom.py`: owns cleanup manifest helper loading, pending-manifest registry, signal/atexit cleanup glue, and process-finalizer invocation.
      - `openclaw_env.py`: builds OpenClaw subprocess environments, including workspace `.venv/bin` PATH prefixing, macOS proxy detection, and credential-redacted proxy reports.
      - `session_isolation.py`: provides shared OpenClaw agent session-store isolation helpers for single-agent runners and judge calls.
      - `single_llm_openclaw_wrapper.py`: wraps `openclaw agent` for single-LLM turns, validates stdout through the result contract, emits session isolation/stdout diagnostics, and can recover a latest complete answer from transcripts.
      - `web_search_preflight.py`: probes web search with bounded retries before websearch-enabled groups are dispatched.
    - `skills/`
      - `tree.py`: loads the historical chemistry skill inventory, defines the benchmark allowlist, and renders the three-layer skill discovery tree.
      - `health.py`: defines skill health requirements and startup checks.
      - `runtime.py`: provides the workspace `uv run python` skill runner and structured unavailable/failure payload normalization.
      - `audit.py`: extracts conservative post-run skill-use audit metadata from tool summaries and transcript convergence diagnostics.
    - `analysis/`
      - `automated.py`: builds the post-run automated evaluation bundle, invokes read-only `codex exec`, validates/writes reports, and renders Markdown.
      - `launcher.py`: starts detached automated analysis and records launch status under `analysis/status.json`.
    - `workflow/`
      - `cli.py`: owns the three-group benchmark CLI, wave scheduling, aggregate result writing, runtime manifest writing, judge OpenClaw invocation, and legacy-compatible wrapper functions formerly exposed by `benchmark_test.py`.
      - `orchestration.py`: owns per-group runner initialization, per-record runner/evaluator orchestration, result-axis derivation, and per-record persistence.
      - `prompts.py`: builds single-agent and ChemQA benchmark prompts.
      - `runners/single_llm.py`: baseline single-agent runner.
      - `runners/chemqa.py`: ChemQA launch/monitor/archive/cleanup runner.
  - `workspace/benchmark_test.py`
    - Thin compatibility facade for historical `python benchmark_test.py ...` execution and `import benchmark_test.xxx` callers.
    - Re-exports the package-owned CLI symbols directly from `workspace/benchmarking/workflow/cli.py` without re-registering evaluators or redefining shared result dataclasses.
  - `workspace/runtime_paths.py`
    - Central path resolution for repo, skills, benchmarks, runtime roots, and config files.

- Skill bundles under `workspace/skills/`
  - `debateclaw-v1/`
    - Installable DebateClaw runtime bundle.
    - Owns preset compilation/materialization, slot provisioning, launch helpers, runtime checks, model profiles, and live debate state CLI.
  - `chemqa-review/`
    - Installable ChemQA review protocol bundle layered on top of DebateClaw V1.
    - Owns ChemQA launch pipeline, driver loop, artifact reconstruction, liveness/recovery tooling, and prompt/runtime dependency wiring for sibling chemistry provider skills.
  - `rdkit/`, `pubchem/`, `opsin/`, `chem-calculator/`
    - First-batch chemistry provider bundles used for deterministic structure, nomenclature, compound lookup, and numeric subproblems.
  - `act-like-a-chemist/`
    - General chemistry solving SOP bundle used for chemistry questions, including but not limited to skills-on benchmark runs.
    - Guides agents to solve as rigorous chemists by tracking given/derived/tool-verified/source-supported/assumption claims, verifying uncertain structures/calculations/source facts with provider skills, applying organic mechanism guardrails, preserving numerical units/rounding paths, and producing auditable final-answer traces. For benchmark runs it also owns the Benchmark Coverage Checklist SOP with `todo`, `done`, and `blocked` states, per-tool coverage targets, and a two-failure budget per verification target.
  - `chemistry-routing-matrix.json`
    - Historical experimental chemistry skill inventory for medium-or-higher-value chemistry capabilities. Despite the historical filename, runtime benchmark prompts treat this as inventory data, not as a deterministic router.
    - `workspace/benchmarking/skills/tree.py` defines the benchmark skill allowlist and a three-layer discovery tree: Domain -> Skill Family -> Concrete Skill. The first tree domain is `chemist-sop` containing `act-like-a-chemist`; skills-on prompts include only a short instruction to read it first and follow its Benchmark Coverage Checklist, and do not inline its SOP body or expose a nonexistent `benchmark-solving-protocol` skill path.
    - Benchmark startup checks the allowlist with `workspace/benchmarking/skills/health.py`; only health-available skills remain in effective skills-on runtime configs and prompts. Health checks merge API keys from the OpenClaw runtime `.env` when they are not present in the process environment.
    - Single-agent skills-on runs expose the health-filtered benchmark skill allowlist to the model. Prompts include a lightweight hierarchical skill tree and rely on the model to choose and call relevant skills only when they close a coverage checklist gap.
    - Agent-invoked skill scripts should go through `workspace/scripts/run_skill.py`, which validates that `--workspace-root` points to the canonical project root containing `pyproject.toml` and `uv.lock`, executes target scripts via `uv run --project <workspace-root> python` or `uv run --project <workspace-root> --extra paper-parse python` for `paper-parse` scripts, keeps `--execution-cwd` as the subprocess cwd for relative artifacts, and reports structured unavailable/failure payloads instead of raw shell failures. Benchmark agents are instructed to use the literal OpenClaw call form `exec {"command": "<wrapper command>"}` for this wrapper, not to call skill scripts directly with `python`, `python3`, `pip`, temporary runner scripts, or alternate runner searches, and not to invent pseudo-tools such as `script`, `cmd`, `command`, `bash`, `reasoning`, or `system-event-scheduler`; usage/request-shape errors count against the verification target's failure budget.
    - Post-run reporting records actual tool-use audit metadata such as tool-call counts, tool failure counts, transcript tool-result errors, missing `benchmark-solving-protocol` doc reads, request-shape errors, coverage-checklist presence, model-declared skipped traces, skill-health summary, and no-tool-call outcomes. These diagnostics are reported in aggregate summaries but do not change benchmark scoring.
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
  - `benchmark_test.py` -> `benchmarking.workflow.cli`
    - Preserves the historical root script/import facade and delegates execution directly to the package entrypoint.
  - `benchmarking.workflow.cli` -> `benchmarking.core` / `benchmarking.scoring` / `benchmarking.runtime` / `benchmarking.skills` / `benchmarking.analysis` / `benchmarking.workflow`
    - Uses dataset loading, runtime config orchestration, visual bundle materialization, cleanroom glue, evaluator dispatch, per-group orchestration, and reporting.
  - `benchmarking.workflow.cli` -> `skills/chemqa-review`
    - Launches ChemQA preset flow, passes resolved `answer_kind`, polls benchmark-visible run status, prefers canonical Artifact Flow paths, archives outputs.
  - `benchmarking.runtime.cleanroom` / `benchmarking.workflow.cli` -> `skills/benchmark-cleanroom`
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
    - Input: benchmark root or dataset files, optional dataset/subset filters, group list, timeouts, config path, model/profile overrides.
    - Output: `results.json`, `results.partial.json`, `runtime-manifest.json`, `runtime-config/*.json`, `per-record/*/*.json`, CSV summaries.
    - After writing final result artifacts, the runner also starts a detached automated evaluation process by default and records its launch state under `runtime-manifest.json.automated_evaluation`; the analysis process writes under `analysis/`.
    - Per-record JSON entries are on schema version `2` and include `skills_enabled` plus explicit evaluability axes such as run lifecycle status, protocol completion/acceptance status, answer availability/reliability, evaluable/scored flags, recovery mode, degraded execution, and execution error kind.
    - Aggregate summaries in `results.json` and CSV exports retain legacy score fields and also expose `skills_enabled`, operational counters such as completed vs failed runs, protocol completion, evaluable/scored counts, recovered-evaluable counts, degraded execution counts, and HLE calibration RMSE for confidence diagnostics.
  - Implementation location: `workspace/benchmarking/workflow/cli.py`, `workspace/benchmarking/*`; `workspace/benchmark_test.py` is the legacy facade.
  - Status: `DONE`

### Automated Benchmark Evaluation
- Each full benchmark completion writes the usual final artifacts first, then starts `python -m benchmarking.analysis.automated run ...` in a detached process through `benchmarking.analysis.launcher.launch_automated_evaluation`.
- The benchmark CLI does not expose user-facing switches for this feature. It is a default post-run diagnostic path and is not a scoring gate.
- Launch status is written to `output_root/analysis/status.json` and copied into `runtime-manifest.json` under `automated_evaluation`.
- Analysis inputs are written to `output_root/analysis/input-bundle.json`. The bundle groups results by `record_id`, includes final answers, evaluator/judge details, reference answers, status axes, skill-use audit metadata, visible transcript summaries for single-agent runs, and ChemQA artifact summaries for ChemQA runs.
- Single-agent transcript summarization intentionally extracts only visible text, tool calls, and tool results; hidden `thinking` content and signatures are not carried into the analysis bundle.
- ChemQA summarization reads archived `qa_result.json`, `artifact_manifest.json`, `candidate_view.json`, final/failure artifacts, and proposer/reviewer trajectory files when present.
- The analysis process resolves the Codex binary from an explicit internal override, `PATH`, or `/Applications/Codex.app/Contents/Resources/codex`, then runs a read-only `codex exec` `hello` preflight before the full report request. The preflight writes `analysis/codex-preflight-events.jsonl` and `analysis/codex-preflight-last-message.txt`.
- The full analysis calls `codex --ask-for-approval never exec --sandbox read-only --json --model gpt-5.5 -c model_reasoning_effort="xhigh"` from the canonical workspace root and writes `analysis/codex-events.jsonl`, `analysis/report.json`, and `analysis/report.md`.
- User-facing automated-evaluation content defaults to Chinese: the Codex analysis prompt requests Chinese summaries/recommendations while preserving JSON field names and identifiers, local fallback analysis text is Chinese, and `analysis/report.md` renders Chinese headings and labels.
- `analysis/report.md` includes a deterministic per-record result table before the narrative sections. The table is rendered locally from `input-bundle.json`, keeps `analysis/report.json` unchanged, uses one row per benchmark record and one column per experiment group, displays correctness for answer-only metrics, score/max score for rubric metrics, answer/RPF summaries for SuperChem RPF metrics, and appends an average row from aggregate group summaries.
- If Codex launch, execution, report parsing, or report validation fails, the analysis status becomes `failed` and a fallback report is still written from the local evidence bundle. Benchmark pass/fail, scoring, and process exit code are unchanged.

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
  - Implementation location: `workspace/benchmarking/core/datasets.py`
  - Status: `DONE`

- Name: HLE chemistry pool extraction
  - Description: Extracts chemistry-related Humanity's Last Exam rows from `cais/hle` into the local benchmark JSONL style under `workspace/benchmarks/hle/data/`, matching records by `category` and `raw_subject`, preserving HLE question/answer/answer_type/image/canary metadata, and writing a manifest with selection counts.
  - Input / Output:
    - Input: authenticated Hugging Face `cais/hle` test split or a local HLE JSONL export.
    - Output: `hle_chemistry_pool.jsonl` plus `hle_chemistry_pool.manifest.json`.
  - Implementation location: `workspace/benchmarks/hle/extract_hle_chemistry_pool.py`
  - Status: `DONE`

- Name: Benchmark visual input bundle materialization
  - Description: Creates per-record local input bundles for benchmark records with visual inputs. SuperChem runtime bundles expose only images directly referenced by the current question/options text, rewrite those locators to local `images/*` paths in `question.md`, and fail fast when a visible locator cannot be resolved. Reference-reasoning images may remain in the dataset for evaluation context but are not exposed to answer-generation prompts. HLE image fields are materialized from base64 data URIs or local files into the bundle, and remote-only HLE images fail fast instead of silently dropping visual context.
  - Input / Output:
    - Input: SuperChem/HLE `BenchmarkRecord` payloads plus a run-local bundle root.
    - Output: `question.md` and localized `images/*` files referenced by single-agent and ChemQA prompts.
  - Implementation location: `workspace/benchmarking/runtime/bundles.py`, `workspace/benchmarking/workflow/prompts.py`
  - Status: `DONE`

- Name: Evaluator registry and dispatch
  - Description: Maps `eval_kind` to evaluator function with `generic_semantic` fallback. Scoreable benchmark answers are judged from the complete candidate `answer_text`/full response, while `short_answer_text` remains a legacy/display field and is not used to decide `passed` or score. The OpenClaw judge agent uses the same shared run-scoped session isolation preflight/postflight checks as the single-agent runner, and a failed judge session postflight raises a benchmark execution error before judge text is parsed. Judge JSON extraction tolerates invalid non-JSON backslash escapes commonly produced inside LaTeX snippets, such as `\(` and `\)`, so a parseable judge verdict is not upgraded to an execution error only because of LaTeX escaping.
  - Input / Output:
    - Input: `BenchmarkRecord`, short/full answer text, judge object.
    - Output: evaluator payload/dataclass.
  - Implementation location: `workspace/benchmarking/scoring/evaluation.py`, `workspace/benchmarking/scoring/evaluators.py`
  - Status: `DONE`

- Name: ChemBench open-ended scoring
  - Description: Scores numeric or text answers for ChemBench open-ended tasks through the LLM judge using the complete candidate answer text; local numeric/string matching does not decide pass/fail.
  - Input / Output:
    - Input: `BenchmarkRecord`, model answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/scoring/evaluators.py`
  - Status: `DONE`

- Name: FrontierScience Olympiad scoring
  - Description: Evaluates olympiad-style answers through the LLM judge using the complete candidate answer text, covering numeric answers, molecule names embedded in tagged InChI/SMILES/IUPAC references, and formula-style symbolic expressions without local heuristic short-circuiting.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/scoring/evaluators.py`
  - Status: `DONE`

- Name: FrontierScience Research scoring
  - Description: Uses rubric parsing plus LLM judge scoring for research track outputs; parsed rubric items structure the prompt, and the judge decides item satisfaction from the complete candidate answer text. Rubric points and `normalized_score` retain partial-credit information, while `passed` is true only when every rubric item receives full credit.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/scoring/evaluators.py`
  - Status: `DONE`

- Name: SuperChem multimodal scoring
  - Description: Extracts reference checkpoints/options for prompt context and asks the LLM judge to decide answer accuracy plus checkpoint matches/RPF from the complete candidate answer text.
  - Input / Output:
    - Input: record + answer text.
    - Output: `EvaluationResult`.
  - Implementation location: `workspace/benchmarking/scoring/evaluators.py`
  - Status: `DONE`

- Name: HLE chemistry scoring
  - Description: Scores Humanity's Last Exam chemistry-subset records with the official HLE-style LLM judge rule over the complete candidate answer text: extract the final answer from the response, compare against the precise reference answer with small numeric tolerance allowed by the judge prompt, and return binary accuracy plus confidence metadata. Aggregate reporting computes HLE calibration RMSE from the candidate response confidence and binary correctness as a diagnostic of self-reported reliability; it does not affect per-record `score`, `normalized_score`, or `passed`.
  - Input / Output:
    - Input: HLE chemistry `BenchmarkRecord` plus model response text.
    - Output: `EvaluationResult` with `primary_metric = hle_judge_accuracy` and judge details including extracted final answer and confidence.
  - Implementation location: `workspace/benchmarking/scoring/evaluators.py`, `workspace/benchmarking/scoring/evaluation.py`
  - Status: `DONE`

- Name: Run-scoped OpenClaw config orchestration
  - Description: Builds per-group benchmark config payloads, provisions judge/single-agent/ChemQA slot workspaces, toggles web search/plugin state, injects judge/runner agent entries, writes runner-only benchmark skill allowlists plus `skills.load.extraDirs`, strips `thinking` from managed agents, and writes pooled runtime config paths.
  - Input / Output:
    - Input: base config payload/path, experiment group/spec, runtime roots, model overrides, slot template.
    - Output: modified config payloads and config JSON files under `runtime-config/`.
  - Implementation location: `workspace/benchmarking/runtime/config_pool.py`, `workspace/benchmarking/runtime/config.py`, `workspace/benchmarking/runtime/provisioning.py`
  - Status: `DONE`

- Name: Benchmark OpenClaw subprocess environment and web search preflight
  - Description: Constructs a shared environment for benchmark-owned OpenClaw subprocesses, prefixing the canonical workspace `.venv/bin` on `PATH`, setting `VIRTUAL_ENV` and `PYTHONNOUSERSITE=1`, auto-injecting macOS system proxy settings into `HTTP_PROXY`/`HTTPS_PROXY`, and enabling Node's `NODE_USE_ENV_PROXY=1` when no explicit proxy variables are already set. Before dispatching websearch-enabled groups, the benchmark runs a real `web_search` probe through `benchmarking/runtime/single_llm_openclaw_wrapper.py`, saves `web-search-preflight.json`, includes the summary in `runtime-manifest.json` and `results.json`, and materializes group-level execution failures instead of letting records spend turns on repeated failed search calls.
  - Input / Output:
    - Input: run-scoped OpenClaw config path, benchmark agent id, host/system proxy environment.
    - Output: OpenClaw subprocess env plus structured preflight report with provider/result/error/proxy metadata.
  - Implementation location: `workspace/benchmarking/runtime/openclaw_env.py`, `workspace/benchmarking/runtime/web_search_preflight.py`, `workspace/benchmarking/workflow/cli.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/workflow/runners/chemqa.py`
  - Status: `DONE`

- Name: Slot workspace provisioning
  - Description: Creates workspaces with `AGENTS.md` and `.debateclaw-slot.json`.
  - Input / Output:
    - Input: workspace path, slot id, template text.
    - Output: initialized runtime workspace.
  - Implementation location: `workspace/benchmarking/runtime/provisioning.py`
  - Status: `DONE`

- Name: Single-agent OpenClaw baseline runner
  - Description: Builds a time-budget-aware prompt from the runner convergence policy, includes run-local visual bundle instructions when a record has localized visual inputs, shells out through the single-LLM OpenClaw wrapper, validates wrapper stdout against the strict agent result contract before answer extraction, normalizes answer tracks only from schema-valid `payloads[].text`, records transcript tool/turn diagnostics without enforcing them as limits, recovers a complete transcript answer from timeout-like OpenClaw output as scoreable `RunStatus.RECOVERED`, and retries model/request timeout-family failures in fresh sessions before final classification. Timeout retry defaults to three retries after the initial attempt, uses session ids `<initial>-retry1` through `<initial>-retry3`, applies per-attempt `--single-timeout`, and waits with exponential backoff seconds from `--single-timeout-retry-backoff-seconds` (default `5,15,45`). It marks a record failed/unscored if stdout is invalid, wrapper postflight metadata shows the fixed agent's `main` session entry did not point to the requested run-scoped `sessionId`, retries are exhausted, or the normalized candidate answer fails the internal answer contract. Prompts ask for visible derivation/checks rather than compressed answers. The answer contract rejects empty full responses, OpenClaw timeout sentinels such as `LLM request timed out.`, missing final-answer markers for SuperChem/ChemBench/FrontierScience Olympiad, and HLE responses without an `Answer:` field. Final-answer markers may be plain `FINAL ANSWER:` lines or Markdown-bold `**FINAL ANSWER:** answer` / `**FINAL ANSWER: answer**` lines, but must contain a non-empty answer.
  - Input / Output:
    - Input: benchmark record, group config, runtime bundle root.
    - Output: `RunnerResult` with `runner_meta.session_isolation`, `runner_meta.stdout_diagnostics`, `runner_meta.convergence_policy`, `runner_meta.convergence`, `runner_meta.candidate_answer_contract`, `runner_meta.execution_error`, `runner_meta.timeout_retry`, and `runner_meta.skill_use_audit` metadata. Invalid stdout returns `RunStatus.FAILED` and `FailureInfo.code = agent_result_contract_invalid`; recovered transcript answers return `RunStatus.RECOVERED` with `RecoveryInfo.source = single-llm-session-transcript`; wrapper/OpenClaw subprocess failures return structured `FailureInfo.details` and `runner_meta.execution_error` fields including `layer`, `source`, `returncode`, stderr/stdout excerpts, and `retryable`; retryable provider transport/timeout errors can be retried, while config/startup/auth/request errors do not trigger timeout retry. Unrecovered OpenClaw agent timeout sentinel payloads after retry exhaustion return `RunStatus.FAILED`, `FailureInfo.code = agent_response_timeout`, empty answer tracks, and raw payload text retained only in diagnostics instead of evaluation input. Other missing candidate-answer fields return `FailureInfo.code = candidate_answer_contract_invalid`.
    - Prompt behavior: `build_single_llm_prompt` receives the per-record convergence timeout, references the `act-like-a-chemist` Benchmark Coverage Checklist SOP without inlining its full checklist, and tells the model to produce the required final answer format once coverage is sufficient or unresolved gaps are blocked. The prompt asks SuperChem agents to cover checkpoint-like option reasoning and answer immediately when evidence distinguishes candidates; it does not impose a provider-skill call count cap. ChemBench prompts distinguish numeric scalar answers from exact string/structure/name/count answers. FrontierScience Olympiad prompts require a single exact `FINAL ANSWER:` target, while FrontierScience Research prompts explicitly ask for complete rubric-style multi-part reasoning without a standalone short-answer final line. HLE prompts keep the official `Explanation`/`Answer`/`Confidence` format and specialize the `Answer:` field for multiple-choice versus exact-match tasks. The prompt is advisory; enforcement/recovery lives in runner/wrapper metadata and transcript handling.
  - Implementation location: `workspace/benchmarking/workflow/runners/single_llm.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/runtime/session_isolation.py`, `workspace/benchmarking/core/convergence.py`, `workspace/benchmarking/core/result_contract.py`
  - Status: `DONE`

- Name: ChemQA benchmark runner
  - Description: Launches ChemQA preset flow with run-local visual bundle context when present, derives an immutable benchmark answer kind, waits for benchmark-visible terminal run-status under the shared convergence policy, triggers bounded recovery when run-status stops changing, fails with structured `convergence_limit_exceeded` metadata when policy limits are exceeded, prefers canonical Artifact Flow paths, archives artifacts, keeps legacy reconstruction/fallback for compatibility, marks evaluable recovered candidate submissions as scoreable degraded executions, and writes cleanup manifest.
  - Input / Output:
    - Input: benchmark record, ChemQA skill root, config path, slot set, profile/round overrides.
    - Output: `RunnerResult` plus archived artifact tree including canonical final/failure artifacts when available.
  - Implementation location: `workspace/benchmarking/workflow/runners/chemqa.py`
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
  - Description: Skills-on benchmark runs keep the health-available benchmark skill allowlist available for each single-LLM record, use a compact Hierarchical Skill Tree instead of deterministic selected-skill routing, include few-shot guidance that local skill scripts must be launched as `exec {"command": "..."}` wrapper calls rather than pseudo-tool names, and report post-run skill-use audit counters from actual tool execution metadata plus startup health summary.
  - Input / Output:
    - Input: benchmark record prompt plus effective configured benchmark skill allowlist.
    - Output: normal benchmark answer plus `runner_meta.skill_use_audit`, `skill-health.json`, runtime manifest skill-health summary, and aggregate skill tool-use counters.
  - Implementation location: `workspace/benchmarking/skills/tree.py`, `workspace/benchmarking/skills/audit.py`, `workspace/benchmarking/workflow/prompts.py`, `workspace/benchmarking/core/reporting.py`
  - Status: `DONE`

- Name: Benchmark skill runtime health and fixed script runner
  - Description: Runs startup health checks for benchmark-visible skills and routes agent-invoked local skill scripts through a fixed workspace runner. Health checks verify declared Python modules, paper PDF backend imports via the `paper-parse` optional extra, executables, API keys from process env/OpenClaw `.env`, data files, and network providers with per-skill probe timeouts. Runtime skill invocation uses `uv run --project <canonical workspace>` for dependency resolution and a separate execution cwd so agent-relative output paths stay in the benchmark slot workspace. Unavailable skills are removed from effective skills-on allowlists and emitted as structured diagnostics.
  - Input / Output:
    - Input: benchmark skill allowlist plus workspace root and environment variables.
    - Output: `skill-health.json`, runtime manifest health summary/effective allowlists, and structured skill runner payloads with `available=false`, `error_kind`, and `reason` on failure.
  - Implementation location: `workspace/benchmarking/skills/health.py`, `workspace/benchmarking/skills/runtime.py`, `workspace/scripts/run_skill.py`, `workspace/benchmarking/workflow/cli.py`
  - Status: `DONE`

- Name: Experimental chemistry skill optional dependencies
  - Description: Declares installable optional dependency groups for the subset of experimental chemistry skills that have stable Python-package dependencies. These extras support benchmark trials without making heavy materials, MD, ML, database, or workflow packages part of the default runtime.
  - Input / Output:
    - Input: `chemqa[chem-materials]`, `chemqa[chem-quantum-parse]`, `chemqa[chem-bioactivity]`, `chemqa[chem-md]`, `chemqa[chem-cheminformatics-ml]`, `chemqa[chem-materials-ml]`, `chemqa[chem-workflows]`, or aggregate `chemqa[chem-experimental]`.
    - Output: Optional Python package dependencies for skill scripts and examples where packages are resolvable through PyPI/uv.
  - Implementation location: `workspace/pyproject.toml`, `workspace/uv.lock`
  - Status: `DONE_EXPERIMENTAL`

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
  - `workspace/benchmarking/workflow/cli.py` parses CLI args and discovers benchmark JSONL files under `workspace/benchmarks/*/data/*.jsonl` unless explicit files/datasets are provided. It can further filter normalized records by comma-separated `--subsets` labels such as `frontierscience_Research,superchem_multimodal`, and rejects unknown subset names instead of silently running a different range. `workspace/benchmark_test.py` imports and re-exports that module so historical absolute-path execution and `import benchmark_test` callers keep working.
  - On import, the facade and package CLI ensure the workspace source root is on `sys.path` and import benchmark internals through canonical subpackages plus `runtime_paths`, so loading the legacy entrypoint by absolute path does not depend on a resolvable parent `workspace` package.
  - It normalizes records through `benchmarking.core.datasets.load_records`.
  - It runs benchmark skill health checks through `benchmarking.skills.health.check_all_skill_health`, writes `output_root/skill-health.json`, derives effective experiment specs by removing unavailable skills from skills-on allowlists, and includes the health summary/effective allowlists in `runtime-manifest.json`.
  - It builds per-group run-scoped OpenClaw configs in `output_root/runtime-config/` through `benchmarking.runtime.config_pool.ConfigPool`; the ConfigPool receives the effective experiment specs after health filtering. `benchmarking.workflow.cli` exposes a small set of root-script facade wrappers for `benchmark_test.py`.
  - For OpenClaw subprocesses, `benchmarking.runtime.openclaw_env.build_openclaw_subprocess_env` prefixes the canonical workspace `.venv/bin` on `PATH`, sets `VIRTUAL_ENV` to the workspace `.venv`, sets `PYTHONNOUSERSITE=1`, preserves explicit proxy variables, and otherwise imports macOS system HTTP/HTTPS proxy settings from `scutil --proxy`, setting `NODE_USE_ENV_PROXY=1` so Node `fetch()` honors the proxy. The wrapper, judge client, and ChemQA launcher use this shared environment builder. The `.venv/bin` prefix is a compatibility fallback for legacy direct `python`/`python3` examples in skill docs; benchmark prompts still direct agents to use `scripts/run_skill.py` for structured skill execution.
  - It runs `benchmarking.runtime.web_search_preflight.run_web_search_preflight` for every selected group with `websearch=True`, writes `output_root/web-search-preflight.json`, and includes the report in `results.json` and `runtime-manifest.json`. A failed preflight materializes all records in the affected group as failed execution results before wave dispatch, preventing repeated failed `web_search` attempts inside model trajectories.
  - After writing `results.json`, CSV summaries, and the final `runtime-manifest.json`, it starts the detached automated evaluation process and then rewrites `runtime-manifest.json` with the `automated_evaluation` launch status. If launcher setup raises, the CLI writes a `launch_failed` status under `analysis/status.json` and still returns according to the benchmark run outcome.
  - Default groups are `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on`; all set `websearch=True`, so the old web-on/web-off matrix is no longer an experiment axis.
  - `BENCHMARK_SKILLS_ALLOWLIST` is loaded by `benchmarking.skills.tree.benchmark_skill_allowlist()` from the historical `workspace/skills/chemistry-routing-matrix.json` inventory (`skills[].skill`). Startup health filtering writes only available skills to skills-on runner agents. `single_llm_skills_off` writes an explicit empty runner `skills: []`. Judge configs do not receive the benchmark allowlist.
  - Runtime configs add `workspace/skills` to `skills.load.extraDirs` so run-scoped benchmark workspaces can discover the newly available local skills.
  - Before dispatching a record, `benchmarking.runtime.bundles` materializes run-local input bundles for benchmark visual inputs. SuperChem bundles parse `/media/uploads/...` locators from the current question/options text, copy only those visible per-record images into `images/`, rewrite `question.md` to reference the local files, and do not expose full shared asset buckets or reference-reasoning images to answer-generation prompts. Required visible multimodal images that cannot be resolved raise a package runtime-bundle error, which `benchmarking.workflow.cli` translates to `BenchmarkError` for legacy callers. HLE bundles decode base64 image data or copy local image files so prompts with "provided information" retain their visual context.
  - For `single_llm_*` groups:
    - The runner shells out through `benchmarking/runtime/single_llm_openclaw_wrapper.py`, which invokes `openclaw agent --local ... --json`, validates stdout with `benchmarking.core.result_contract`, and normalizes invalid stdout into `result.meta.stdout_diagnostics`.
    - It does not use a native Python OpenClaw API.
    - Invalid stdout, such as tool argument JSON without schema-valid answer payloads, is never passed to answer extraction or the evaluator. The record becomes failed/unscored with `agent_result_contract_invalid`.
    - Schema-valid stdout still must pass the runner's internal candidate-answer contract before evaluation. Timeout sentinels such as `LLM request timed out.` return `agent_response_timeout`; missing required answer markers/fields return `candidate_answer_contract_invalid`; both paths keep `answer` empty and retain raw payload text only in `runner_meta.candidate_answer_contract`. Plain `FINAL ANSWER:` and Markdown-bold `**FINAL ANSWER:** answer` / `**FINAL ANSWER: answer**` lines are accepted when the marker carries a non-empty answer.
    - Timeout retry is benchmark-wrapper scoped: `subprocess.TimeoutExpired`, OpenClaw/model request timeout sentinels, `meta.error.kind == "timeout"`, timeout-family transcript `openclaw:prompt-error` events, HTTP 408/499/500/502/503/504, gateway/deadline exceeded, and transport timeout/reset codes can trigger fresh-session retry. Auth/billing/quota/rate-limit/context-overflow/model-not-found/image-size/role-ordering/format errors, approval timeouts, and sandbox/exec/tool/skill-script timeouts do not retry. Plain `replayInvalid` or `livenessState=abandoned` retries only when payload/meta/transcript also contains timeout-family evidence.
    - Runner-level convergence policy and timeout retry configuration are written into top-level results/manifests and per-record `runner_meta`; the wrapper records transcript assistant-turn/tool-call diagnostics plus `prompt_error_count`, `latest_prompt_error`, and `latest_prompt_error_is_timeout`, and can recover the latest complete benchmark answer from the session transcript after timeout-like OpenClaw output using the same final-answer marker handling as the candidate-answer contract. Tool/turn counts are never hard failure limits.
    - `single_llm_skills_on` includes the compact health-filtered Hierarchical Skill Tree in the prompt; `single_llm_skills_off` omits it and explicitly forbids OpenClaw/local skill tools.
    - When an input bundle exists, the prompt names the bundle directory, tells the agent to read `question.md`, and explicitly instructs it to inspect referenced local images before answering.
    - The prompt tells the model to run local skill scripts using the literal OpenClaw form `exec {"command": "..."}`, where the command invokes `workspace/scripts/run_skill.py` with `--workspace-root /Users/xutao/.openclaw/workspace` and `--execution-cwd "$PWD"`. It includes positive and negative examples that reject empty `exec` arguments, direct `python skills/...`, and nonexistent tool names such as `python3`, `script`, `cmd`, `command`, `bash`, `reasoning`, and `system-event-scheduler`. The runner uses the canonical project root for `uv --project` dependency resolution and preserves the agent workspace for relative artifacts.
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
    - Default scoring reads only canonical `final_answer_artifact.json`. If a completed/accepted output lacks that artifact, the runner may migrate completed legacy `qa_result`/protocol/final-submission data into a canonical final artifact before scoring; otherwise the run is non-scoreable with `missing_canonical_terminal_artifact`.
    - Proposer proposal files, `final_answer_preview`, and `failure_artifact.answer_projection` are diagnostic-only in default runs and do not create scoreable ChemQA recovered results.
  - All per-record outputs are persisted immediately under `per-record/<group>/<slug>.json`.
  - LLM-judge evaluation calls use a fresh `benchmark-judge-<id>` OpenClaw session id, clear stale `agent:benchmark-judge:main` pointers before the call, verify postflight session/file/model state before parsing judge stdout, and preserve runner raw/meta diagnostics when judge/evaluator execution fails.
  - Cleanup manifests are registered and benchmark-cleanroom process finalization runs in `finally`/signal/atexit paths; session stores, transcripts, and run artifacts remain on disk for audit.

- Real ChemQA control path
  - The operational state machine is `workspace/skills/debateclaw-v1/scripts/debate_state.py`.
  - Compiled ChemQA run plans declare `runtime_context.chemqa_review.control_plane = debate_state_driver` and do not include native workflow package metadata.
  - `chemqa_review_openclaw_driver.py` loops by repeatedly calling `debate_state.py` subcommands in subprocesses.
  - The driver updates ClawTeam task state, saves sessions, opens/removes cleanup leases, emits role-specific artifacts, and now treats one OpenClaw turn as a turn boundary rather than a phase-failure boundary.
  - Candidate / formal-review / rebuttal production is phase-scoped: the driver can reuse the same `session_id` across multiple turns, observe the required artifact after each turn, feed back missing/invalid/stale state, and only mark lane failure after phase budget exhaustion or a hard wrapper error.
  - Materialized ChemQA role prompts now pass `--runtime-dir` to the compact state snapshot helper so compact snapshot and fallback commands resolve the same run-scoped DebateClaw runtime helpers.
  - When DebateClaw reports protocol terminal conditions, the driver publishes `artifact_flow_state=finalizing` while keeping legacy `status=running`; after `collect_artifacts.py` / Artifact Flow writes terminal artifacts, run status carries `artifact_flow_state=finalized|finalization_failed`, `benchmark_terminal_state`, canonical paths, and legacy-compatible terminal fields.
  - Coordinator protocol generation treats the deterministic protocol scaffold as primary; model refinement is optional quality improvement and falls back to the deterministic scaffold when the refinement turn aborts, times out without a valid rewrite, or leaves invalid protocol output.
  - Rebuttal artifacts now carry explicit `mode`: `response_only`, `answer_revision`, or `concession`. Only `answer_revision` updates the Artifact Flow current candidate view.
  - `chemqa-review/scripts/bundle_common.py` and the prompt pack now treat all skills listed in `skills/chemistry-routing-matrix.json` as required sibling skills alongside DebateClaw and the paper pipeline.
  - ChemQA proposer prompts use full-availability skill discovery wording: provider skills can be used directly when they help, full `SKILL.md` files should be read only for skills about to be used, and unexecuted skills are not valid provider traces.
  - `benchmarking/workflow/prompts.py` injects the compact Hierarchical Skill Tree into single-agent benchmark prompts, so single-agent skills-on runs receive domain/family discovery guidance rather than record-level route selection.
  - Single-agent benchmark turns use a fixed OpenClaw agent per experiment group and rely on `benchmarking/runtime/single_llm_openclaw_wrapper.py` to delete stale `agent:<id>:main` entries before each run-scoped session turn; old transcript files are not deleted.
  - `pyproject.toml` exposes optional experimental chemistry extras for PyPI-resolvable dependency families. `chemqa[chem-experimental]` aggregates those families but is intentionally not included in `chemqa[full]`, and OpenFF/tooluniverse/HPC executable stacks remain conda, preinstalled, API, or external-service dependencies described by their skill docs rather than default pip dependencies.
  - The shared ChemQA prompt module is named for the fixed-lane protocol rather than native workflow-package execution.
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
  - `benchmarking.workflow.cli` is still broad: it owns CLI parsing, wave scheduling, subprocess helpers, result aggregation, and root-script facade wrappers. Runtime config, prompts, status axes, evaluator implementations, per-group orchestration, runtime bundles, and cleanup glue are package-owned modules, and `benchmark_test.py` is no longer a real implementation boundary.
  - `benchmark_test.py` remains only as a compatibility facade and intentionally does not preserve implementation-shape details such as `GroupRecordResult.__module__ == "benchmark_test"`.
  - The obsolete native workflow-package scaffold has been retired; current live ChemQA execution uses CLI/state-script orchestration.
  - Run-scoped OpenClaw configs are produced by mutating a copy of the user’s local `~/.openclaw/openclaw.json`.
  - Recovery and artifact collection rely on specific file naming conventions such as `proposer-1.md`, `chemqa_review_protocol.yaml`, `qa_result.json`.
  - Cleanup correctness depends on manifests being written before launch and on command/session naming matching run ids.

## 5. Gap Analysis
- Missing features
  - `NOT_IMPLEMENTED`: No actual FastAPI/Gradio/uvicorn application code despite optional `web-ui` dependencies in `workspace/pyproject.toml`.

- Incomplete implementations
  - No ChemQA native workflow package remains in the source tree; the previous inactive scaffold and unused loader were removed rather than implemented.
- Architectural inconsistencies
  - Intended architecture suggests package-based workflows and reusable modules.
  - Actual behavior is still script-heavy and subprocess-heavy:
    - `benchmarking.workflow.cli` is still a broad package CLI/entrypoint with embedded scheduling and aggregation logic.
    - ChemQA runs are controlled through external state scripts.
  - `workspace/benchmarking/` is now the real benchmark implementation layer, and its old flat compatibility modules have been removed; the legacy root `benchmark_test.py` entrypoint is retained for compatibility.
  - `workspace/pyproject.toml` advertises `web-ui` extras, but there is no corresponding app module.
  - Top-level repo contains a mix of source, runtime state, generated artifacts, logs, and secret-bearing config in one tree; module boundaries are not clean at the repository level.

## 6. Risks & Technical Debt
- Fragile logic
  - Artifact recovery depends on specific filenames and directory heuristics in `workspace/benchmarking/workflow/runners/chemqa.py`.
  - Cleanup depends on manifests and process command-line matching in `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`; session/artifact retention is intentional and may require separate manual pruning outside benchmark correctness paths.
  - ChemQA recovery depends on `spawn_registry.json`, `/proc`-style process inspection when available, and workspace naming conventions in `workspace/skills/chemqa-review/scripts/recover_run.py`.

- Hardcoded values
  - Default OpenClaw home/config roots are hardcoded in `workspace/runtime_paths.py`.
  - Default model ids, agent ids, workspace roots, slot sets, and timeouts are hardcoded in `workspace/benchmarking/workflow/cli.py`.
  - GROBID and MinerU default URLs are hardcoded in docs/scripts.
  - ChemQA role topology is fixed to one candidate owner plus four reviewer lanes across the DebateClaw state script and ChemQA artifact/driver scripts.

- Missing abstractions
  - `benchmarking.workflow.cli` still combines CLI parsing, scheduling, aggregate result persistence, runtime manifest writing, and root-script facade wrappers.
  - Paper tools are standalone scripts with no shared higher-level orchestrator.
  - OpenClaw/ClawTeam integration is done through subprocess calls everywhere; there is no local adapter interface.

- Operational risks
  - `openclaw.json` at repo root contains live gateway/auth/provider configuration and is reused as a mutable base for runtime configs.
  - Repo root stores live runtime state, backups, SQLite DBs, session logs, and generated artifacts beside source.
  - Optional dependencies listed in `pyproject.toml` may imply capabilities that do not actually exist in code.

## 7. Suggested Next Steps
- Continue shrinking the package CLI:
  - Move more scheduling, result persistence, and aggregation glue from `workspace/benchmarking/workflow/cli.py` into smaller package modules once their contracts stabilize.
- Separate source from runtime state:
  - Move generated workspaces, logs, DBs, and mutable OpenClaw runtime state outside the analyzed source tree or document them as runtime-only roots.
- Remove or implement misleading declared surfaces:
  - Either add a real web UI/API module for the `web-ui` extras or drop those extras from the project metadata.
- Harden artifact and cleanup flows:
  - Continue reducing filename/path guessing in legacy ChemQA artifact recovery paths now that canonical Artifact Flow paths exist.
  - Centralize run manifest/session/process metadata contracts used by runners, drivers, cleanup, and single-LLM session-isolation audits.
- Add clearer ownership boundaries:
  - Separate DebateClaw engine logic, ChemQA protocol logic, benchmark orchestration, and paper pipeline into smaller modules with fewer embedded subprocess wrappers.
