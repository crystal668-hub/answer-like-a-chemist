# GLOBAL DEV SPEC

## 1. Project Overview
- Purpose
  - `.openclaw/` is a local OpenClaw runtime home that also contains a Python workspace for chemistry benchmark orchestration, DebateClaw debate workflows, ChemQA-style review workflows, paper retrieval/access/parse/rerank utilities, and benchmark cleanup tooling.
  - The main executable source code lives under `workspace/`.
  - The repo root also stores live runtime state for OpenClaw and ClawTeam: agent configs, generated workspaces, SQLite state, logs, device/auth files, and task/session registries.
- Current capabilities (ONLY what works)
  - `DONE`: The canonical current benchmark workspace contract is the four-axis policy/adjudication model from `docs/superpowers/specs/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md`; it supersedes older binary `audit_status`/`contaminated` wording elsewhere in this document. Immutable `WorkspaceAccessPolicy` objects now provide normalized read/write/exec-workdir scopes, exact-file handling, deterministic digests, protected roots, and serialized runtime evidence for single-LLM, judge, and ChemQA roles. Skills-off and judge policies exclude the skills root and `scripts/run_skill.py`; skills-on policies include only the exposed skill filesystem scope and exact wrapper entrypoint.
  - `DONE`: Attempt workspaces use scratch contract v2 with stable `scratch/{requests,outputs,notes,tmp}` paths. A canonical base contract plus minimal role overlays materializes one final `AGENTS.md`; structured tools use `scratch/...` relative paths, while `exec` enters runner-injected scratch through environment variables. The policy-driven `benchmark-workdir-guard` plugin preflights structured read/write/edit/move/delete path arguments and explicit exec cwd/workdir, resolves existing symlink prefixes, and returns stable blocked evidence before execution.
  - `DONE`: Transcript audit preserves tool-call ids and call/result line correlation, classifies `succeeded|failed|blocked|unknown` outcomes and `read|list|search|execute|write|mutate|workdir|unknown` access modes, and separates `audit_execution_status`, `boundary_status`, `contamination_status`, and final `adjudication`. Write-only, blocked, failed, and allowed-fallback boundary events remain diagnostics and can be `scoreable_degraded`; confirmed or indeterminate information exposure remains non-evaluable. Missing or parser-failed active transcripts are deterministically retried from exact archive references before final fail-closed adjudication.
  - `DONE`: Benchmark per-record/results writers use schema v3 and aggregate scores only from `scored=true` records. Reporting adds independent boundary warning/violation, degraded, contamination, unavailable, and cleanup counters; the dashboard and automated-analysis bundle expose finding access mode, outcome, policy, path evidence, and cleanup while legacy v2 isolation payloads receive a visible legacy adapter instead of being reinterpreted as confirmed exposure.
  - `DONE`: `scripts/replay_workspace_adjudication.py` and `benchmarking/runtime/history_recovery.py` provide dry-run-first, record-selective historical transcript replay without model calls. Apply mode requires explicit historical ownership approval for degraded write-only recovery, snapshots original per-record/results/progress/manifest inputs, records source hashes, git commit, policy digest and scorer identity, optionally reuses the registered evaluator, and atomically rewrites selected records plus aggregate results/progress metadata.
  - `DONE`: Run benchmark batches across three skills experiment groups: `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on` via `workspace/benchmarking/workflow/cli.py`; `workspace/benchmark_test.py` is a thin compatibility facade for the historical script/import path. Default experiment groups keep websearch disabled, and the experiment variable is the health-filtered benchmark skills allowlist plus the single-agent/ChemQA runner shape.
  - `DONE`: Load benchmark JSONL datasets into a normalized `BenchmarkRecord` model via `workspace/benchmarking/core/datasets.py`.
  - `DONE`: Score outputs with registered evaluators for ChemBench, FrontierScience Olympiad/Research, SuperChem, HLE, verifier-grounded RDKit/xTB/property_calculation tasks, and generic semantic matching via `workspace/benchmarking/scoring/evaluators.py` and `workspace/benchmarking/scoring/evaluation.py`. The verifier-grounded integration is governed by `workspace/docs/superpowers/specs/2026-07-15-verifier-grounded-openclaw-single-llm-integration-usage-spec.md`, is pinned to `verifier-grounded-benchmark==0.1.1` wheel SHA256 `1eb64265449f844e8a1b1d0b2cd6010ecb543ca239d1d8929c6fd9df5c935a05`, provisions agent-facing records only through `verifier_grounded_benchmark.load_track(...).prompts()`, runs scoring in a dedicated non-agent virtual environment through only `load_track(...).evaluate_one(...)`, and returns a continuous `verifier_score` with `passed = None` instead of applying a binary pass threshold. Property-calculation public gold is obtained separately through the official `load_track(...).sample_answers()` API only after agent execution, validated against the pinned task inventory, and written into final results/per-record reporting; synchronized JSONL, prompts, configs, and attempt workspaces remain gold-free, while RDKit/xTB references remain hidden.
  - `DONE`: Provision run-scoped OpenClaw configs whose single-LLM, ChemQA role, and judge agents point only at sentinel-managed attempt workspaces via `workspace/benchmarking/runtime/config_pool.py`, `workspace/benchmarking/runtime/config.py`, and `workspace/benchmarking/runtime/agent_workspace.py`; legacy fixed benchmark workspaces remain untouched and are forbidden audit targets rather than runtime inputs.
  - `DONE`: Enforce attempt-scoped benchmark agent workspace isolation for single-LLM skills-on/off attempts, ChemQA coordinator/proposer/reviewer lease sets, timeout/recovery attempts, every judge call, and web-search preflight. `AttemptWorkspaceManager` creates each active workspace atomically from a canonical `.git`-free template, writes an identity sentinel and current-only scratch tree, rejects unsafe paths/symlinks/special files, audits the exact session transcript for forbidden path access and OpenClaw unavailable-workdir fallback warnings, then archives the complete workspace after stdout/session/tool/artifact collection. Single-LLM OpenClaw child processes use the active attempt workspace as their process cwd, so an OpenClaw tool fallback cannot write into the canonical source workspace; an observed workdir fallback still marks the attempt contaminated and fails closed. Single-LLM run-scoped configs also load the project-owned `benchmark-workdir-guard` OpenClaw plugin, whose `before_tool_call` hook blocks nonexistent, non-directory, symlink-escaped, or outside-attempt explicit `exec.workdir` values before command execution and returns a correctable tool error to the model. Attempt prompts tell both skills-on and skills-off agents to omit `exec.workdir`, enter the pre-created scratch directory through environment variables inside the command, and atomically create child output directories before entering them. Under `workspace/docs/superpowers/specs/2026-07-16-benchmark-forbidden-path-root-containment-spec.md`, the transcript auditor extracts only determinate path expressions from exec command/workdir and structured path-bearing arguments, expands attempt-provided `$VAR`/`${VAR}` and `HOME`-backed `~`, resolves real paths including existing symlink prefixes, allows the current active workspace and runner-injected public scopes first, then reports `forbidden_path` only when the resolved candidate falls inside an explicitly injected immutable `ProtectedRoot`. Exec parsing recognizes quoted/unquoted `<<` and `<<-` heredocs, projects embedded bodies into quote-neutral path tokens while retaining `exec.heredoc` findings for determinate protected paths, ignores `<<<` here-strings, and tracks the effective directory across simple deterministic `cd <path> &&` / `cd <path>;` chains so later relative paths resolve from the directory the shell would use instead of the workspace root. Production policy roots come directly from `runtime_paths`, the manager runtime/output roots, and four explicit legacy paths; directory/file names carry no policy meaning. Each finding records `policy_id`, `candidate_source`, `resolved_path`, and `matched_root`, while runtime manifests serialize the exact normalized policy used by the run. Transcript parser failures retain fail-closed `audit_status=unavailable` behavior and now report the transcript line, tool name, redacted command excerpt, exception type, and redacted exception message. Prepare, policy, audit, contamination, recovery, or archive failures fail closed and prevent scoring; runtime/per-record metadata and aggregate reports expose preflight, audit, contamination, and archive status.
  - `DONE`: Run a single-agent OpenClaw baseline through a benchmark wrapper that gives each record a run-scoped `sessionId`, clears only stale `agent:<id>:main` session-store pointers before the turn, validates postflight session isolation by the requested session id and transcript file while keeping model/provider drift as diagnostic metadata, uses official VGB prompts with only optional bounded-mode time-budget and skills-on catalog prefixes, validates OpenClaw stdout against a strict agent result schema before answer extraction, preserves payload error markers such as `payloads[].isError`, applies runner-level convergence policy metadata and transcript recovery for complete benchmark answers, classifies OpenClaw agent error payloads such as `stream_read_error` and no-response fallbacks before candidate-answer contract validation, classifies wrapper/OpenClaw subprocess failures at the stdout/stderr boundary into structured execution errors with `code`, `layer`, `source`, and `retryable` fields before retry decisions, re-inspects the requested agent/session after wrapper/provider/subprocess failures so an already-written transcript remains available to workspace audit, tracks whether the primary single-agent turn naturally passes the 600-second time-reminder threshold without interrupting that turn and sends a same-session reminder after it returns when no complete answer is available and main-budget time remains, performs one same-session finalization rescue for error-like payloads when transcript recovery finds no complete answer and session isolation is healthy, gives the outer wrapper subprocess enough wall-clock budget to cover the primary OpenClaw turn plus finalization-rescue safety window plus cleanup buffer, validates an internal candidate-answer contract before evaluation, treats unrecovered OpenClaw timeout sentinel payloads as failed/non-scoreable runs, lets a later schema-complete native/recovered/finalization answer supersede historical idle-timeout diagnostics from the same session, and preserves historical transcript files via `workspace/benchmarking/workflow/runners/single_llm.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/runtime/session_isolation.py`, `workspace/benchmarking/core/convergence.py`, and `workspace/benchmarking/core/result_contract.py`. Single-LLM user prompts no longer force `act-like-a-chemist`, prescribe checklist reasoning, or add VGB verifier/single-candidate/answer-schema restatements. Skills-on prompts prepend a neutral, health-filtered Domain -> Family -> Skill -> description catalog that leaves skill selection and use to the model; skills-off prompts receive no catalog. In `--no-timeout` mode the official VGB task prompt is unchanged for skills-off and follows only the neutral catalog for skills-on before attempt scratch paths are appended. Tool/turn counts and checklist diagnostics remain metadata only and are not enforced as hard exploration limits. Finalization rescue is now an English format/evaluability repair turn for cases where the previous session output was not a compliant final answer: it tells the agent not to call tools or inspect files, to consolidate existing reasoning/tool evidence, to check consistency, and to output only the current `eval_kind`'s format requirements; the rescue turn does not receive an OpenClaw per-turn `--timeout`, while the outer wrapper subprocess still supplies the run-level safety guard through an internal fixed `finalization_safety_seconds` buffer, not a user-facing finalization grace flag. Primary complete-answer marker handling accepts plain `FINAL ANSWER:` lines and the common Markdown-bold forms `**FINAL ANSWER:** answer` and `**FINAL ANSWER: answer**` when a non-empty answer follows the marker; FrontierScience Research prompts and finalization rescue now prefer a `## FINAL RESEARCH ANSWER` section, while native payload and transcript recovery retain compatible family markers (`FINAL RESEARCH RESPONSE`, `FINAL RESEARCH SYNTHESIS`, `FINAL RESEARCH CONCLUSION`, `RESEARCH FINAL ANSWER`) plus legacy final/conclusion markers (`FINAL ANSWER`, `FINAL SYNTHESIS`, `FINAL CONCLUSION`, `FINAL / CONCLUSION`, `FINAL AND CONCLUSION`, `SUPPORTED CONCLUSION`, `CONCLUSION`) and non-empty next-line marker answers. Research marker matching is case-insensitive and accepts Markdown headings/bold, optional numbering, and punctuation; process-only sections such as references and coverage checklists remain incomplete. Candidate-answer contract metadata records `has_research_final_marker` for FrontierScience Research records when the preferred/family research marker is present, but missing that new marker is not a hard failure if a compatible fallback yields a complete research answer. `replayInvalid` payloads record structured `replay_invalid_diagnostics` under convergence metadata and propagated failure details so the underlying replay/error/liveness evidence remains visible.
  - `DONE`: Single-agent complete-answer detection is schema-aware for verifier-grounded `final_answer_block` records: the runner passes the record `answer_schema` into the wrapper, recognizes `FINAL ANSWER:` followed by a non-empty fenced `xyz` or `cif` block using that schema's prefix/fence language, uses the same schema-aware check for transcript recovery, replayInvalid/error-payload classification, finalization rescue success, and candidate-answer contract validation, and exposes `has_complete_answer_for_eval` plus answer-schema diagnostics in contract metadata while preserving single-line `FINAL ANSWER: <answer>` behavior for line-answer tasks.
  - `DONE`: Run a ChemQA multi-agent workflow by compiling/materializing a ChemQA launch, monitoring benchmark-visible run-status, applying runner-level convergence policy to unchanged-status recovery attempts, consuming canonical Artifact Flow outputs, archiving outputs, and cleaning runtime leftovers via `workspace/benchmarking/workflow/runners/chemqa.py`.
  - `DONE`: Manage DebateClaw V1 runtime, slot provisioning, prompt/materialization, and launch commands via `workspace/skills/debateclaw-v1/scripts/*.py`.
  - `DONE`: Maintain live debate protocol state in SQLite and expose CLI commands for init/status/next-action/submit/advance via `workspace/skills/debateclaw-v1/scripts/debate_state.py`.
  - `DONE`: Drive ChemQA reviewer/proposer/coordinator loops on top of DebateClaw state via `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`, including phase-scoped multi-turn artifact production, role-phase run-status diagnostics, and deterministic coordinator fallback when model refinement aborts or leaves no valid protocol rewrite.
  - `DONE`: Recover stalled ChemQA runs, respawn dead workers, and repair invalid protocol state via `workspace/skills/chemqa-review/scripts/recover_run.py`.
  - `DONE`: Collect ChemQA protocol outputs through Artifact Flow into canonical terminal artifacts, `artifact_manifest.json`, and legacy-compatible `qa_result.json` via `workspace/skills/chemqa-review/scripts/chemqa_artifact_flow.py` and `collect_artifacts.py`; finalization applies structured `answer_revision` rebuttals and repairs numeric short-answer projections from anchored final values in the full answer when the raw direct answer is a setup/process sentence.
  - `DONE`: Provide deterministic first-batch chemistry provider skills for local structure reasoning, name resolution, public compound lookup, and numeric chemistry calculations via `workspace/skills/rdkit`, `workspace/skills/opsin`, `workspace/skills/pubchem`, and `workspace/skills/chem-calculator`; `chem-calculator` uses Pint-backed unit parsing/conversion through a small compatibility alias layer and exposes SymPy-backed symbolic expression equivalence helpers for deterministic formula checks.
  - `DONE`: Provide an experimental medium-or-higher-value chemistry skill inventory via `workspace/skills/chemistry-routing-matrix.json`, covering 86 local skills from a general chemistry solving SOP, structure/materials, atomistic simulation, quantum chemistry, bioactivity/safety, molecular/materials ML, databases, spectra/formats, paper retrieval/access/parse/rerank, and workflow automation. Despite the historical filename, benchmark runtime provisioning treats it as inventory data, not as a deterministic router. Each inventory entry carries `capability_domain`, `provider_role`, and `single_agent_exposure` metadata so the agent-readable provider trigger contract can stay separate from the machine-readable skill catalog.
  - `DONE`: Run benchmark skill health checks before skills-on groups. Startup health checks verify declared Python imports through workspace `uv run`, paper PDF backend imports through the `paper-parse` optional extra, executables, API keys loaded from process env or the OpenClaw runtime `.env`, data files, and network providers with per-skill probe timeouts for slower providers such as ChEMBL; unavailable skills are removed from effective runtime allowlists and reported in `skill-health.json` plus `runtime-manifest.json`. Benchmark startup also runs a generic `web_search` preflight for websearch-enabled groups, retries transient OpenClaw/DuckDuckGo failures up to three attempts with default `5s, 10s` exponential backoff between failed attempts, and fails those groups early only when web search remains unavailable after those attempts.
  - `DONE`: Provide a fixed skill script runner via `scripts/run_skill.py`; agent-invoked skill scripts run through workspace `uv run --project <canonical workspace> python` while preserving the agent workspace as the execution cwd for relative input/output paths, with `paper-parse` scripts executed via `uv run --project <canonical workspace> --extra paper-parse python`, and return structured unavailable payloads such as `invalid_workspace_root`, `missing_dependency`, `missing_executable`, `missing_api_key`, and `provider_failure`. Benchmark OpenClaw subprocess environments also prefix the workspace `.venv/bin` on `PATH` and set `VIRTUAL_ENV`/`PYTHONNOUSERSITE` as a compatibility fallback for legacy skill docs that still show direct `python` or `python3` examples; `scripts/run_skill.py` remains the canonical skill execution path.
  - `DONE`: Provide autonomous benchmark skill discovery and audit via `workspace/benchmarking/skills/tree.py`, `workspace/benchmarking/skills/audit.py`, and `workspace/benchmarking/core/reporting.py`: skills-on benchmark runs expose the health-filtered benchmark skill allowlist through OpenClaw and prepend the same available skills as a neutral Domain -> Family -> Skill -> description catalog; the model decides whether and how to use any listed skill. Skills-off prompts receive no catalog. Neither path forces the SOP or prescribes checklist reasoning. Benchmark-managed workspace `TOOLS.md` retains local script execution guidance, and post-run reporting tracks OpenClaw tool calls, exec tool calls, skills-enabled exec-backed skill script calls, true tool-result errors, missing skill-doc reads, true request-shape errors, and coverage-checklist presence separately from answer scoring. `exec_tool_*` audit counters represent generic `exec` tool usage. `skill_tool_*` audit/aggregate counters are populated only when the group has benchmark skills enabled and represent local skill script invocations; ordinary OpenClaw tools such as `read`, `image`, `web_search`, `web_fetch`, and `write` remain in `openclaw_tool_*` / generic tool diagnostics, not skill-tool usage. Transcript audit ignores normal `read` results from skill docs, contracts, and source files even when those texts contain request-contract examples such as `--request-json`. Single-LLM skills-on attempts create a workspace-local `.benchmark-scratch/<record>/<session_id>/` tree and expose `BENCHMARK_SKILL_SCRATCH_DIR`, `BENCHMARK_SKILL_REQUEST_DIR`, `BENCHMARK_SKILL_OUTPUT_DIR`, and `BENCHMARK_SKILL_NOTES_DIR` so request JSON files, skill outputs, downloaded papers, temporary scripts, and notes stay grouped by record/session instead of being scattered in the agent workspace root.
  - `DONE`: Launch a default non-blocking automated benchmark evaluation and experience-extraction pass after completed benchmark aggregation unless the run uses `--no-analysis`, via `workspace/benchmarking/analysis/launcher.py` and `workspace/benchmarking/analysis/automated.py`. The analysis builds a run-local evidence bundle from `results.json`, per-record JSON, single-LLM transcripts, and ChemQA archived artifacts, resolves an executable Codex binary including the macOS Codex app bundle fallback, runs a read-only `hello` preflight, then invokes a read-only `codex exec` session with `gpt-5.5` and `xhigh` reasoning to write `analysis/report.json` and `analysis/report.md`; user-facing analysis content defaults to Chinese while preserving JSON field names and identifiers, and the Markdown report includes a deterministic per-record result table rendered from the local evidence bundle. Launch, skip, or analysis failure is diagnostic only and does not change the benchmark exit code.
  - `DONE`: Retrieve literature candidates from OpenAlex, Semantic Scholar, and Crossref via `workspace/skills/paper-retrieval/scripts/paper_retrieval.py`.
  - `DONE`: Resolve accessible paper artifacts using direct OA URLs and optional Unpaywall lookup via `workspace/skills/paper-access/scripts/paper_access.py`.
  - `DONE`: Parse local PDF/text documents with MinerU or PyMuPDF fallback via `workspace/skills/paper-parse/scripts/paper_parse.py`.
  - `DONE`: Rerank papers by building GROBID profiles and calling an OpenAI-compatible chat-completions endpoint via `workspace/skills/paper-rerank/scripts/paper_rerank.py`.
  - `DONE`: Terminate benchmark-owned leftover processes from manifests/leases while preserving session files, session stores, run-scoped artifacts, manifests, and cleanup reports via `workspace/skills/benchmark-cleanroom/scripts/cleanup_benchmark_run.py`.
  - `DONE`: Manage local Docker-backed GROBID via `workspace/scripts/docker_services.sh` and native macOS MinerU API via `workspace/scripts/mineru_service.sh`.
  - `DONE`: Serve a local benchmark result dashboard through `workspace/benchmarking/dashboard/app.py` when launched with the `web-ui` optional dependencies. The dashboard scans benchmark run directories, reads immutable result artifacts, displays per-record group comparisons and progress, serves run-local visual assets with path containment checks, and stores only review metadata in `state/benchmark-dashboard/dashboard.sqlite`.

## 2. System Architecture
- Top-level repo roles
  - `workspace/`
    - Main Python package and scripts.
    - Contains benchmark orchestration, skill bundles, dataset prep scripts, tests, docs, and Docker helpers.
  - `agents/`
    - OpenClaw agent runtime directories with `agent/models.json` and `sessions/sessions.json`.
    - Used as live runtime/config state, not source modules.
  - `benchmark/workspaces/`
    - `runs/<run>/<invocation>/active/` contains only current sentinel-managed attempt workspaces for single-LLM, ChemQA roles, and judge calls; complete workspaces are moved into the benchmark output root after collection/audit.
    - Older fixed workspace directories are untrusted legacy runtime state: new benchmark configs do not reference, reset, or delete them.
  - `debateclaw/workspaces/`
    - Generated DebateClaw slot workspaces for live debate runs.
  - `flows/`, `tasks/`, `memory/`
    - SQLite runtime stores.
  - `logs/`, `devices/`, `identity/`, `qqbot/`
    - Operational state and logs; not code modules.
  - `openclaw.json`
    - Base OpenClaw config used and rewritten into run-scoped configs by benchmark launchers.
    - Defines global Anthropic-compatible MiniMax routes `minimax/MiniMax-M2.7` and `minimax/MiniMax-M2.7-highspeed` through `${MINIMAX_ANTHROPIC_BASE_URL}/v1` and `${MINIMAX_API_KEY}` with `authHeader: true`, `api: anthropic-messages`, a 204800-token context window, and a 65536-token single-turn output cap. Defines global OpenAI-compatible `openai/gpt-5.5`, `openai/gpt-5.4`, `openai/gpt-5.6-sol`, `openai/gpt-5.6-terra`, and `openai/gpt-5.6-luna` routes through `${SU8_BASE_URL}` and `${SU8_API_KEY}` with `api: openai-responses`, replacing the historical `su8/gpt-5.4` provider route; `openai/gpt-5.5` remains the default primary model.
    - Defines global OpenAI-compatible Qwen routes `qwen/qwen3.6-plus`, `qwen/deepseek-v4-pro`, `qwen/qwen3.7-max`, and `qwen/qwen3.7-plus` through `${QWEN_BASE_URL}` and `${QWEN_API_KEY}` with `api: openai-completions`, Qwen thinking-format compatibility, a 1000000-token context window, and a 65536-token single-turn output cap. `workspace/scripts/sync_openclaw_qwen_provider.py` keeps the global runtime config environment-backed and removes legacy token-plan agent provider caches that would otherwise override updated `.env` values; explicit `--agent <id>` cleanup removes that agent's Qwen provider override regardless of its prior base URL, while default bulk cleanup preserves non-token-plan agent overrides. The OpenClaw Gateway must be restarted after Qwen `.env` changes because the running process retains resolved credentials.
    - Defines global OpenAI-compatible Kimi route `kimi/kimi-k2.6` through `${KIMI_BASE_URL}` and `${KIMI_API_KEY}` with `api: openai-completions`, text/image input metadata, and a 32768-token single-turn output cap matching Kimi's official default `max_tokens`; the historical `dashscope-compatible` provider route has been removed from the runtime config and managed agent model caches.

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
      - `reporting.py`: defines per-record schema v3, legacy workspace-isolation display adaptation, scored-only aggregate metrics, diagnostic tool/checklist counters, and independent boundary/contamination/audit/cleanup/archive counters.
    - `scoring/`
      - `evaluation.py`: registry/dispatch for evaluator functions.
      - `evaluators.py`: implements ChemBench, FrontierScience, SuperChem, HLE, verifier-grounded RDKit/xTB/property_calculation, and generic semantic scoring plus answer parsing helpers.
      - `verifier_grounded_runtime.py`: validates the pinned release identity, wheel SHA256, runtime manifest, track/task inventory, and launches an isolated `python -I` process that uses the public verifier-grounded package API for prompt description, single-answer evaluation, and property-calculation public sample answers. It deliberately removes agent `PYTHONPATH` and `VIRTUAL_ENV` state.
    - `runtime/`
      - `config.py`, `config_pool.py`, `provisioning.py`: render run-scoped OpenClaw configs and manage config path pooling without referencing legacy fixed benchmark workspaces; general DebateClaw slot provisioning remains available outside the benchmark attempt lifecycle.
      - `agent_workspace.py`: owns attempt identity, canonical base-plus-overlay templates, stable scratch v2, sentinel/lock contracts, atomic prepare, immutable `WorkspaceAccessPolicy`, protected-root containment, tool-call/result correlation, outcome/access-mode findings, four-axis adjudication, deterministic transcript/archive recovery, ownership-safe boundary cleanup, full-workspace archive/quarantine, lease sets, and startup recovery.
      - `history_recovery.py`: replays historical transcripts through the current policy/auditor/adjudicator, optionally reuses the registered evaluator, and applies explicitly approved record-selective recovery with snapshot/hash/scorer evidence and no model calls.
      - `bundles.py`: materializes SuperChem/HLE run-local visual input bundles.
      - `cleanroom.py`: owns cleanup manifest helper loading, pending-manifest registry, signal/atexit cleanup glue, and process-finalizer invocation.
      - `openclaw_env.py`: builds OpenClaw subprocess environments, including workspace `.venv/bin` PATH prefixing, macOS proxy detection, and credential-redacted proxy reports.
      - `session_isolation.py`: provides shared OpenClaw agent session-store isolation helpers for single-agent runners, run-local ChemQA explicit sessions, and judge calls.
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
    - The default persisted benchmark dataset root is the OpenClaw home data directory at `data/formal-benchmarks/`; temporary benchmark datasets live at `data/temp-benchmarks/`. `OPENCLAW_BENCHMARKS_ROOT` and `OPENCLAW_DATA_ROOT` can override these defaults.

- Skill bundles under `workspace/skills/`
  - `debateclaw-v1/`
    - Installable DebateClaw runtime bundle.
    - Owns preset compilation/materialization, slot provisioning, launch helpers, runtime checks, model profiles, and live debate state CLI.
  - `chemqa-review/`
    - Installable ChemQA review protocol bundle layered on top of DebateClaw V1.
    - Owns ChemQA launch pipeline, driver loop, artifact reconstruction, liveness/recovery tooling, and prompt/runtime dependency wiring for sibling chemistry provider skills.
  - `rdkit/`, `pubchem/`, `opsin/`, `chem-calculator/`
    - First-batch chemistry provider bundles used for deterministic structure, nomenclature, compound lookup, and numeric subproblems.
  - `xtb-cli/`
    - Local xTB executable provider for structured JSON benchmark calls against XYZ geometries. It runs single-point, optimization, Hessian/optimization-Hessian, vertical electrophilicity, and Fukui-index xTB modes through `scripts/xtb_runner.py`, captures command/version/stdout/stderr/artifacts, and parses conservative scalar properties such as total energy, HOMO/LUMO/gap, dipole, polarizability, Gsolv, and imaginary-frequency count. This is distinct from `hpc-xtb/`, which remains HPC/CREST workflow and template guidance.
  - `act-like-a-chemist/`
    - General chemistry solving SOP bundle used for chemistry questions, including but not limited to skills-on benchmark runs.
    - Provides an optional rigorous chemistry workflow for tracking given/derived/tool-verified/source-supported/assumption claims, verifying uncertain structures/calculations/source facts with provider skills, preserving numerical units/rounding paths, and producing auditable final-answer traces without separate topic-specific SOP sections. Its standard answering flow starts from a compact Atomic Coverage Checklist with `todo`, `done`, and `blocked` states. The checklist atomizes known givens, formulas, unit conversions, intermediate values, mechanism steps, comparison classes, prompt constraints, and final-answer slots, not just unresolved evidence gaps. It limits provider skills to concrete checklist atoms, treats tool results as scoped evidence rather than verdicts, and marks atoms `done` only after their local derivation or evidence is complete. The external `workspace/skills/act-like-a-chemist/contract/skill-triggers.md` is a neutral capability reference: agents may identify a capability need and choose any suitable provider from the single-agent-exposed `workspace/skills/chemistry-routing-matrix.json` inventory; no provider order is mandatory. Workflow/runtime bundles such as `benchmark-cleanroom`, `debateclaw-v1`, and `chemqa-review` remain non-provider infrastructure not exposed to single-agent chemistry answering. When this optional workflow is selected, it requires candidate/hypothesis verification when tools check a guessed answer: agents must distinguish intermediate-step support from decisive final-answer support, compare plausible competing candidates, narrow enumeration by deterministic prompt constraints before verifying a reduced candidate set, solve numeric identity/composition/formula unknowns directly when possible, and reject single-candidate tool confirmation such as an isolated database hit, formula match, approximate numeric match, or valid structure. It stops new tool paths once coverage is sufficient or blocked. Before final answers, it re-checks blocked atoms and requires a prompt/reasoning consistency review covering derived values, units/dimensionality/rounding, formula or concept fit, and structure/count/option constraints, then organizes all atomic reasoning steps into a smooth, complete reasoning trace with verified visible checkpoints before outputting the requested format. For benchmark runs it retains per-tool coverage targets, benchmark runner constraints, and a two-failure budget per verification target.
  - `chemistry-routing-matrix.json`
    - Historical experimental chemistry skill inventory for medium-or-higher-value chemistry capabilities. Despite the historical filename, benchmark runtime provisioning treats this as inventory data, not as a deterministic router. Each `skills[]` entry records the skill name plus `capability_domain`, `provider_role`, and `single_agent_exposure` metadata; `skill-triggers.md` remains an agent-readable capability reference while this JSON remains the machine-readable inventory source.
    - `workspace/benchmarking/skills/tree.py` defines the benchmark skill allowlist from inventory entries where `single_agent_exposure` is `true` and retains the three-layer Domain -> Skill Family -> Concrete Skill inventory helpers. Skills-on single-agent prompts render every health-available leaf and its summary as a neutral catalog without forcing `act-like-a-chemist` or any other skill; skills-off prompts do not render the catalog.
    - Benchmark startup checks the allowlist with `workspace/benchmarking/skills/health.py`; only health-available skills remain in effective skills-on runtime configs. Health checks merge API keys from the OpenClaw runtime `.env` when they are not present in the process environment.
    - Single-agent skills-on runs expose the health-filtered benchmark skill allowlist through OpenClaw. The model independently decides whether to discover, read, and call relevant skills.
    - Agent-invoked skill scripts should go through `workspace/scripts/run_skill.py`, which validates that `--workspace-root` points to the canonical project root containing `pyproject.toml` and `uv.lock`, executes target scripts via `uv run --project <workspace-root> python` or `uv run --project <workspace-root> --extra paper-parse python` for `paper-parse` scripts, keeps `--execution-cwd` as the subprocess cwd for relative artifacts, and reports structured unavailable/failure payloads instead of raw shell failures. Single-LLM skills-on benchmark agents receive a benchmark-managed workspace `TOOLS.md` with a two-step uppercase-placeholder template: first write `REQUEST_JSON_PATH` with `REQUEST_JSON_STRING`, then call `exec {"command": "<wrapper command>"}` with `SCRIPT_PATH`, `REQUEST_JSON_PATH`, and `OUTPUT_DIR` filled in. The runner injects a per-attempt `.benchmark-scratch/<record>/<session_id>/` directory and scratch environment variables; `TOOLS.md` tells agents to place request JSON files under `$BENCHMARK_SKILL_REQUEST_DIR`, outputs under `$BENCHMARK_SKILL_OUTPUT_DIR`, and other exploration artifacts under `$BENCHMARK_SKILL_SCRATCH_DIR` rather than in the workspace root.
    - Post-run reporting records actual tool-use audit metadata such as OpenClaw tool-call counts, generic exec tool counts, skills-enabled exec-backed skill script call counts, transcript tool-result errors, missing `benchmark-solving-protocol` doc reads, request-shape errors, coverage-checklist presence, model-declared skipped traces, skill-health summary, and no-tool-call outcomes. Aggregate summaries expose `exec_tool_call_total` / `exec_tool_failure_total` for generic exec usage, and populate `skill_tool_call_total`, `skill_tool_executed_count`, `skill_no_tool_call_count`, and `skill_tool_failure_total` only for skills-enabled groups. Aggregate summaries also expose `openclaw_tool_call_total` and `openclaw_tool_failure_total` for ordinary OpenClaw tool usage. These diagnostics are reported in aggregate summaries but do not change benchmark scoring. Request-shape and tool-result counters are restricted to real error payloads/CLI failures/preflight failures; normal documentation or source-code reads are not counted as errors solely because they include request-contract text.
    - Core executable wrappers for `cclib`, `pymatgen`, `molecular-dynamics`, `chembl-database`, and `xtb-cli` return structured error payloads for missing dependencies or executables, missing input files, parse failures, and provider/API/CLI failures instead of crashing.
  - `benchmark-cleanroom/`
    - Run-scoped cleanup manifests and lease management plus a process-only cleanup executor that intentionally preserves benchmark session and artifact state.
  - `paper-retrieval/`, `paper-access/`, `paper-parse/`, `paper-rerank/`
    - Standalone paper-processing pipeline stages.

- Dataset prep modules
  - `data/formal-benchmarks/chembench/extract_open_ended_reasoning_pool.py`
  - `data/formal-benchmarks/frontierscience/extract_chemistry_pool.py`
  - `data/formal-benchmarks/hle/extract_hle_chemistry_pool.py`
  - `data/formal-benchmarks/superchem/extract_superchem_pool.py`
  - `workspace/scripts/sync_verifier_grounded_datasets.py`
    - Installs the pinned verifier-grounded wheel into a hash-addressed virtual environment, validates public track descriptions, writes sanitized tracked snapshots under `workspace/benchmarking/resources/verifier_grounded/datasets/`, and synchronizes runtime copies under `data/formal-benchmarks/`.
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
  - Description: Runs single-agent baselines with and without the benchmark skills allowlist, plus ChemQA with the same skills allowlist. Default groups disable websearch/duckduckgo, wave-batch groups in requested order, and save per-record and aggregate outputs.
  - Input / Output:
    - Input: benchmark root or dataset files, optional dataset/subset filters, group list, timeouts, config path, model/profile overrides, and single-agent/judge OpenClaw thinking overrides.
    - Output: `results.json`, `results.partial.json`, `runtime-manifest.json`, `runtime-config/*.json`, `per-record/*/*.json`, and automated evaluation artifacts under `analysis/`.
    - After writing final result artifacts, the runner also starts a detached automated evaluation process by default and records its launch or skip state under `runtime-manifest.json.automated_evaluation`; `--no-analysis` records a skipped status without launching the analysis process.
    - Per-record JSON entries are on schema version `2` and include `skills_enabled` plus explicit evaluability axes such as run lifecycle status, protocol completion/acceptance status, answer availability/reliability, evaluable/scored flags, recovery mode, degraded execution, and execution error kind.
    - Aggregate summaries in `results.json` retain legacy score fields and also expose `skills_enabled`, operational counters such as completed vs failed runs, protocol completion, evaluable/scored counts, recovered-evaluable counts, degraded execution counts, and HLE calibration RMSE for confidence diagnostics; readable summaries are carried by the automated evaluation report in `analysis/report.md`.
  - Implementation location: `workspace/benchmarking/workflow/cli.py`, `workspace/benchmarking/*`; `workspace/benchmark_test.py` is the legacy facade.
  - Status: `DONE`

### Automated Benchmark Evaluation
- Each full benchmark completion writes the usual final artifacts first, then starts `python -m benchmarking.analysis.automated run ...` in a detached process through `benchmarking.analysis.launcher.launch_automated_evaluation` unless `--no-analysis` is set.
- `--no-analysis` is the per-run opt-out for temporary/test benchmark runs that should not spend tokens on automated evaluation. It writes `automated_evaluation.status = "skipped"` and `reason = "disabled_by_cli"` in `runtime-manifest.json` and `analysis/status.json`.
- Launch status is written to `output_root/analysis/status.json` and copied into `runtime-manifest.json` under `automated_evaluation`.
- Analysis inputs are written to `output_root/analysis/input-bundle.json`. The bundle groups results by `record_id`, includes complete per-group final `answer_text` values for review, evaluator/judge details, reference answers, status axes, skill-use audit metadata, visible transcript summaries for single-agent runs, and ChemQA artifact summaries for ChemQA runs; prompt/reference/trajectory evidence remains preview-summarized where needed to bound bundle size.
- Single-agent transcript summarization intentionally extracts only visible text, tool calls, and tool results; hidden `thinking` content and signatures are not carried into the analysis bundle.
- ChemQA summarization reads archived `qa_result.json`, `artifact_manifest.json`, `candidate_view.json`, final/failure artifacts, and proposer/reviewer trajectory files when present.
- The analysis process resolves the Codex binary from an explicit internal override, `PATH`, or `/Applications/Codex.app/Contents/Resources/codex`, then runs a read-only `codex exec` `hello` preflight before the full report request. The preflight writes `analysis/codex-preflight-events.jsonl` and `analysis/codex-preflight-last-message.txt`.
- The full analysis calls `codex --ask-for-approval never exec --sandbox read-only --json --model gpt-5.5 -c model_reasoning_effort="xhigh"` from the canonical workspace root and writes `analysis/codex-events.jsonl`, `analysis/report.json`, and `analysis/report.md`.
- User-facing automated-evaluation content defaults to Chinese: the Codex analysis prompt requires Simplified Chinese for all model-generated natural-language string values in `report.json` while preserving JSON field names and identifiers, local fallback analysis text is Chinese, and `analysis/report.md` renders Chinese headings, labels, and structured per-record analysis fields such as summaries, group results, evidence, trajectory evidence, comparisons, and recommendations.
- `analysis/report.md` includes a deterministic per-record result table before the narrative sections. The table is rendered locally from `input-bundle.json`, keeps `analysis/report.json` unchanged, uses one row per benchmark record and one column per experiment group, displays correctness for answer-only metrics, score/max score for rubric metrics, answer/RPF summaries for SuperChem RPF metrics, continuous score values for verifier-grounded `verifier_score` metrics, and appends an average row whose cells always include correctness rate, include normalized average score only for rubric/process-score records, include answer/RPF means only for SuperChem RPF records, and use `Verifier 平均分 <avg>` for groups where all scored records are verifier-grounded continuous scores.
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
  - Description: Extracts chemistry-related Humanity's Last Exam rows from `cais/hle` into the local benchmark JSONL style under `data/formal-benchmarks/hle/data/`, matching records by `category` and `raw_subject`, preserving HLE question/answer/answer_type/image/canary metadata, and writing a manifest with selection counts.
  - Input / Output:
    - Input: authenticated Hugging Face `cais/hle` test split or a local HLE JSONL export.
    - Output: `hle_chemistry_pool.jsonl` plus `hle_chemistry_pool.manifest.json`.
  - Implementation location: `data/formal-benchmarks/hle/extract_hle_chemistry_pool.py`
  - Status: `DONE`

- Name: Benchmark visual input bundle materialization
  - Description: Creates per-record local input bundles for benchmark records with visual inputs. SuperChem runtime bundles expose only images directly referenced by the current question/options text, rewrite those locators to local `images/*` paths in `question.md`, and fail fast when a visible locator cannot be resolved. Reference-reasoning images may remain in the dataset for evaluation context but are not exposed to answer-generation prompts. HLE image fields are materialized from base64 data URIs or local files into the bundle, and remote-only HLE images fail fast instead of silently dropping visual context.
  - Input / Output:
    - Input: SuperChem/HLE `BenchmarkRecord` payloads plus a run-local bundle root.
    - Output: `question.md` and localized `images/*` files referenced by single-agent and ChemQA prompts.
  - Implementation location: `workspace/benchmarking/runtime/bundles.py`, `workspace/benchmarking/workflow/prompts.py`
  - Status: `DONE`

- Name: Evaluator registry and dispatch
  - Description: Maps `eval_kind` to evaluator function with `generic_semantic` fallback. Scoreable benchmark answers are judged from the complete candidate `answer_text`/full response, while `short_answer_text` remains a legacy/display field and is not used to decide `passed` or score. The OpenClaw judge agent uses the same shared run-scoped session isolation preflight/postflight checks as the single-agent runner, and a failed judge session postflight raises a benchmark execution error before judge text is parsed. Judge JSON extraction tolerates invalid non-JSON backslash escapes commonly produced inside LaTeX snippets, such as `\(` and `\)`, so a parseable judge verdict is not upgraded to an execution error only because of LaTeX escaping. `verifier_grounded` dispatch runs the local verifier-grounded benchmark evaluator and reports continuous `verifier_score` without a pass/fail threshold.
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

- Name: Pinned verifier-grounded RDKit, xTB XYZ, and property-calculation scoring
  - Description: Runs the three formal verifier-grounded tracks through one fixed `0.1.1` wheel in a hash-addressed virtual environment outside the benchmark agent workspaces. The synchronized inventories are RDKit 11, xTB 18, and property_calculation 2. OpenClaw JSONL rows contain only the public prompt, answer schema, track/task ID, timeout, package version, and wheel SHA256; they do not contain source repository paths, verifier specs, sample answers, or gold. The evaluator validates the record release identity and deployed wheel/runtime manifest, then submits the model response through `verifier_grounded_benchmark.load_track(track).evaluate_one(...)` in `python -I`. After agent execution, final reporting obtains property_calculation public gold through `load_track("property_calculation").sample_answers()`, validates its task-ID order against the pinned inventory, and rewrites final results/per-record `reference_answer` fields without exposing gold to prompts or workspaces; RDKit/xTB references remain hidden. Prompts ask single-agent and ChemQA runners to return a final candidate in the public answer schema, including fenced `xyz` blocks for xTB and JSON final lines for property-calculation tasks.
  - Input / Output:
    - Input: Sanitized OpenClaw JSONL records synchronized from the pinned wheel's public `track.prompts()` API plus a candidate answer such as a SMILES string, fenced XYZ geometry, or property JSON.
    - Output: `EvaluationResult` with `primary_metric = verifier_score`, `score`/`normalized_score` equal to the verifier score, `passed = None`, and details including verifier status, failure type, canonical SMILES, properties, constraint scores, and version metadata when supplied by the verifier.
  - Implementation location: `workspace/scripts/sync_verifier_grounded_datasets.py`, `workspace/benchmarking/resources/verifier_grounded/`, `workspace/benchmarking/core/datasets.py`, `workspace/benchmarking/scoring/evaluation.py`, `workspace/benchmarking/scoring/evaluators.py`, `workspace/benchmarking/scoring/verifier_grounded_runtime.py`, `workspace/benchmarking/workflow/prompts.py`, `workspace/benchmarking/workflow/runners/single_llm.py`
  - Status: `DONE`

- Name: Run-scoped OpenClaw config orchestration
  - Description: Builds per-group benchmark config payloads whose judge/single-agent/ChemQA entries point at the current invocation's managed active paths, toggles benchmark web access by setting both `tools.web.search.enabled`/DuckDuckGo and `tools.web.fetch.enabled`, injects judge/runner agent entries, writes runner-only benchmark skill allowlists plus `skills.load.extraDirs`, strips `thinking` from managed agents, and writes pooled runtime config paths. It never references or mutates legacy fixed benchmark workspaces.
  - Input / Output:
    - Input: base config payload/path, experiment group/spec, runtime roots, model overrides, slot template.
    - Output: modified config payloads and config JSON files under `runtime-config/`.
  - Implementation location: `workspace/benchmarking/runtime/config_pool.py`, `workspace/benchmarking/runtime/config.py`, `workspace/benchmarking/runtime/agent_workspace.py`
  - Status: `DONE`

- Name: Benchmark OpenClaw subprocess environment and web search preflight
  - Description: Constructs a shared environment for benchmark-owned OpenClaw subprocesses, prefixing the canonical workspace `.venv/bin` on `PATH`, setting `VIRTUAL_ENV` and `PYTHONNOUSERSITE=1`, auto-injecting macOS system proxy settings into `HTTP_PROXY`/`HTTPS_PROXY`, and enabling Node's `NODE_USE_ENV_PROXY=1` when no explicit proxy variables are already set. Before dispatching websearch-enabled groups, the benchmark runs a real `web_search` probe through `benchmarking/runtime/single_llm_openclaw_wrapper.py`, retries failed probes up to three attempts with default `5s, 10s` exponential backoff, saves `web-search-preflight.json`, includes the summary in `runtime-manifest.json` and `results.json`, and materializes group-level execution failures instead of letting records spend turns on repeated failed search calls.
  - Input / Output:
    - Input: run-scoped OpenClaw config path, benchmark agent id, host/system proxy environment.
    - Output: OpenClaw subprocess env plus structured preflight report with provider/result/error/proxy metadata.
  - Implementation location: `workspace/benchmarking/runtime/openclaw_env.py`, `workspace/benchmarking/runtime/web_search_preflight.py`, `workspace/benchmarking/workflow/cli.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/workflow/runners/chemqa.py`
  - Status: `DONE`

- Name: General DebateClaw slot workspace provisioning
  - Description: Creates non-benchmark DebateClaw workspaces with `AGENTS.md` and `.debateclaw-slot.json`; benchmark workspaces use the attempt workspace manager and add the DebateClaw sentinel only after the manager's preflight succeeds.
  - Input / Output:
    - Input: workspace path, slot id, template text.
    - Output: initialized runtime workspace.
  - Implementation location: `workspace/benchmarking/runtime/provisioning.py`
  - Status: `DONE`

- Name: Single-agent OpenClaw baseline runner
  - Description: Acquires a fresh managed workspace for every primary/retry attempt, injects current-only scratch paths for both skills-on and skills-off, builds a convergence-policy-aware prompt, shells out through the single-LLM OpenClaw wrapper, and collects stdout/session/transcript/tool diagnostics before contamination audit and full-workspace archive. Timeout retries begin only after the preceding workspace is sealed. Workspace isolation failures discard otherwise valid or recovered answers and prevent scoring. The runner otherwise retains its existing schema-valid stdout, convergence, eval-aware transcript recovery, finalization rescue, and candidate-answer contract behavior.
  - Input / Output:
    - Input: benchmark record, group config, runtime bundle root.
    - Output: `RunnerResult` with `runner_meta.workspace_isolation` plus the existing session/stdout/convergence/candidate-answer/execution/timeout/skill-use diagnostics. Workspace metadata includes run/invocation/attempt/session/template identity, preflight and audit status, findings, and archive paths; isolation failures use `layer=benchmark_runtime`, `source=workspace_isolation`, and are never scoreable.
    - Prompt behavior: `build_single_llm_prompt` prepends a time-budget line only in bounded mode and prepends the neutral health-filtered skill catalog only for skills-on. It does not inject shared solution-strategy guidance, mandatory skill routing, verifier implementation details, single-candidate restatements, or duplicated task-type instructions. VGB and FrontierScience Olympiad otherwise use the official prompt unchanged. FrontierScience Research retains only its `## FINAL RESEARCH ANSWER` requirement and prohibition on the short-answer marker; ChemBench retains only `FINAL ANSWER: <answer>`; SuperChem retains only uppercase `FINAL ANSWER: <option letters>` with `|` separation plus runtime bundle/image paths; HLE retains its `Explanation:`, `Answer:`, and `Confidence:` fields, answer-type specialization, no-`FINAL ANSWER:` rule, and runtime bundle/image paths. Candidate-answer enforcement and recovery remain in runner/wrapper metadata and transcript handling.
  - Implementation location: `workspace/benchmarking/workflow/runners/single_llm.py`, `workspace/benchmarking/runtime/agent_workspace.py`, `workspace/benchmarking/runtime/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/runtime/session_isolation.py`, `workspace/benchmarking/core/convergence.py`, `workspace/benchmarking/core/result_contract.py`
  - Status: `DONE`

- Name: ChemQA benchmark runner
  - Description: Atomically prepares an independent managed workspace lease for the coordinator and all five proposer/reviewer slots before launching ChemQA, uses fresh lease sets for recovery, and resolves each role's explicit session transcript from the run-local launch home. It collects canonical Artifact Flow outputs and cleanroom cleanup first, then audits and archives all six complete workspaces; any unavailable/contaminated audit or partial archive discards the candidate answer. Existing convergence, canonical artifact, legacy reconstruction, and degraded recovery behavior remains in place inside that lifecycle.
  - Input / Output:
    - Input: benchmark record, ChemQA skill root, config path, slot set, profile/round overrides.
    - Output: `RunnerResult` plus archived artifact tree including canonical final/failure artifacts when available.
  - Implementation location: `workspace/benchmarking/workflow/runners/chemqa.py`, `workspace/benchmarking/runtime/agent_workspace.py`, `workspace/benchmarking/runtime/session_isolation.py`
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
  - Description: Runs deterministic local cheminformatics helpers for canonicalization, descriptors, functional groups, substructure, ring/aromaticity analysis, stereochemistry, similarity, reactions, and conformers. RDKit no longer exposes a graph-symmetry NMR peak-count helper; NMR peak-count tasks should use RDKit only for structural facts and rely on manual spectroscopic interpretation for signal counts.
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
  - Description: Runs deterministic local chemistry calculations for stoichiometry, concentration, equilibria, acid/base, gas-law, electrochemistry, units, and answer checks. Shared unit parsing/conversion is Pint-backed with local aliases only for common chemistry spellings; shared symbolic expression equivalence checks use SymPy.
  - Input / Output:
    - Input: request JSON plus output directory.
    - Output: stable `result.json` payloads with structured calculation traces and diagnostics.
  - Implementation location: `workspace/skills/chem-calculator/*`
  - Status: `DONE`

- Name: Autonomous benchmark skill discovery and audit
  - Description: Skills-on benchmark runs keep the health-available benchmark skill allowlist available for each single-LLM record and expose its Domain -> Family -> Skill -> description hierarchy as a neutral prompt catalog; the model decides whether and how to use any skill. Skills-off prompts receive no catalog, and neither group is forced into a chemistry SOP. Benchmark-managed `TOOLS.md` provides single-LLM skills-on local script execution as a two-step request-file/write plus `exec {"command": "..."}` wrapper pattern rather than pseudo-tool names, and reporting derives post-run skill-use audit counters from actual tool execution metadata plus startup health summary. `exec_tool_*` counters count generic `exec` tool usage. `skill_tool_*` counters count `exec` calls as local skill script invocations only for skills-enabled groups with a benchmark skill allowlist; skill-off groups may still expose `exec_tool_*` diagnostics but do not report those as skill calls. Ordinary OpenClaw tools such as `read`, `image`, `web_search`, `web_fetch`, and `write` are tracked separately as OpenClaw/generic tool diagnostics. Audit counters distinguish true error payloads and request-shape failures from normal skill documentation/source reads.
  - Input / Output:
    - Input: benchmark record prompt plus effective configured benchmark skill allowlist.
    - Output: normal benchmark answer plus `runner_meta.skill_use_audit`, `skill-health.json`, runtime manifest skill-health summary, aggregate skill tool-use counters for skills-enabled groups, generic exec diagnostics, and aggregate OpenClaw tool-use counters.
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
  - Implementation location: `data/formal-benchmarks/chembench/extract_open_ended_reasoning_pool.py`
  - Status: `DONE`

- Name: FrontierScience dataset extraction
  - Description: Merges olympiad and research JSONL inputs into a chemistry-only pool.
  - Input / Output:
    - Input: olympiad/research JSONL files.
    - Output: JSONL pool + manifest.
  - Implementation location: `data/formal-benchmarks/frontierscience/extract_chemistry_pool.py`
  - Status: `DONE`

- Name: SuperChem dataset extraction
  - Description: Reads SUPERChem rows from datasets-server or zip/parquet fallback, localizes assets, emits a multimodal pool with asset paths stored relative to the output JSONL directory when that context is available.
  - Input / Output:
    - Input: dataset name, output JSONL/assets paths.
    - Output: JSONL pool + manifest + assets.
  - Implementation location: `data/formal-benchmarks/superchem/extract_superchem_pool.py`
  - Status: `DONE`

- Name: Web UI / API server
  - Description: Provides a localhost FastAPI benchmark dashboard for reading benchmark run outputs, reviewing per-record answers, comparing experiment groups, monitoring progress, and maintaining dashboard-only review metadata. The run list does not compute or display an ambiguous overall run average score; instead it uses immutable `summary.groups` data to show a compact `single_llm_skills_on` / `single_llm_skills_off` normalized-score comparison such as `on 0.61 · off 0.54 · Δ +0.07` when those group averages are available. Dashboard display taxonomy groups verifier-grounded tracks under dataset `vgb` and keeps concrete tracks such as `verifier_grounded_rdkit`, `verifier_grounded_xtb_xyz`, and `verifier_grounded_property_calculation` as subsets for filtering and record display. Score review remains at the per-record/per-group comparison level and original aggregate summaries remain available inside immutable result artifacts. Per-group detail cards display skill diagnostics for skills-enabled groups and exec diagnostics for skills-off groups so `Exec calls` and `Skill calls` are not shown redundantly. Dashboard HTML/JS/CSS responses use `Cache-Control: no-store` so local UI changes are picked up on page refresh.
  - Input / Output:
    - Input: `state/benchmark-runs/*` or configured run roots containing `results.json`, `runtime-manifest.json`, `waves/`, `progress/`, `per-record/`, `input-bundles/`, and `analysis/` artifacts.
    - Output: Local HTTP API/static UI on `127.0.0.1`, plus review metadata in `state/benchmark-dashboard/dashboard.sqlite`.
  - Implementation location: `workspace/benchmarking/dashboard/*`; script entrypoint `benchmark-dashboard` in `workspace/pyproject.toml`
  - Status: `DONE`

## 4. Actual Behavior
- Primary execution flow: three-group skills benchmark
  - `workspace/benchmarking/workflow/cli.py` parses CLI args and discovers benchmark JSONL files under `data/formal-benchmarks/*/data/*.jsonl` unless explicit files/datasets are provided. It can further filter normalized records by comma-separated `--subsets` labels or ordered `--record-ids`, and rejects unknown selectors instead of silently running a different range. `workspace/benchmark_test.py` imports and re-exports that module so historical absolute-path execution and `import benchmark_test` callers keep working. New verifier-grounded runs invoke the canonical module CLI directly; there is no VGB-specific launcher, parser, or track alias translation layer.
  - On import, the facade and package CLI ensure the workspace source root is on `sys.path` and import benchmark internals through canonical subpackages plus `runtime_paths`, so loading the legacy entrypoint by absolute path does not depend on a resolvable parent `workspace` package.
  - It normalizes records through `benchmarking.core.datasets.load_records`.
  - Verifier-grounded records are synchronized from the pinned wheel's public prompt API before benchmark runs. Their grading config carries only release identity, track/task ID, answer schema, and timeout. During scoring, `benchmarking.scoring.verifier_grounded_runtime` verifies the fixed wheel in `data/verifier-grounded-releases/`, verifies the hash-addressed runtime manifest under `workspace/state/verifier-grounded-runtimes/`, strips agent Python path/venv variables, and invokes the installed package with `python -I`. The verifier package is not installed in the workspace `.venv` used by OpenClaw agent subprocesses, and sample answers/gold/verifier specs are not copied into agent-visible JSONL or prompts. Final aggregation separately obtains only property_calculation's public gold through the official `sample_answers()` API and writes canonical JSON references into final reporting artifacts.
  - It runs benchmark skill health checks through `benchmarking.skills.health.check_all_skill_health`, writes `output_root/skill-health.json`, derives effective experiment specs by removing unavailable skills from skills-on allowlists, and includes the health summary/effective allowlists in `runtime-manifest.json`.
  - It creates a new `invocation_id` for every CLI process, performs fail-closed startup recovery of sentinel-proven active workspaces from older invocations of the same run, and records the workspace runtime/archive/quarantine roots plus template identities in `runtime-manifest.json` before dispatch.
  - It builds per-group run-scoped OpenClaw configs in `output_root/runtime-config/` through `benchmarking.runtime.config_pool.ConfigPool`; the ConfigPool receives the effective experiment specs after health filtering and points managed agents only at the current invocation's active workspace paths. `benchmarking.workflow.cli` exposes a small set of root-script facade wrappers for `benchmark_test.py`.
  - For OpenClaw subprocesses, `benchmarking.runtime.openclaw_env.build_openclaw_subprocess_env` prefixes the canonical workspace `.venv/bin` on `PATH`, sets `VIRTUAL_ENV` to the workspace `.venv`, sets `PYTHONNOUSERSITE=1`, preserves explicit proxy variables, and otherwise imports macOS system HTTP/HTTPS proxy settings from `scutil --proxy`, setting `NODE_USE_ENV_PROXY=1` so Node `fetch()` honors the proxy. The wrapper, judge client, and ChemQA launcher use this shared environment builder. The `.venv/bin` prefix is a compatibility fallback for legacy direct `python`/`python3` examples in skill docs; benchmark-managed `TOOLS.md` directs single-LLM skills-on agents to use `scripts/run_skill.py` for structured skill execution.
  - It runs `benchmarking.runtime.web_search_preflight.run_web_search_preflight` for every selected group with `websearch=True`, retrying failed probes up to three attempts with default `5s, 10s` exponential backoff between failed attempts, writes `output_root/web-search-preflight.json`, and includes the report in `results.json` and `runtime-manifest.json`. A failed preflight materializes all records in the affected group as failed execution results before wave dispatch, preventing repeated failed `web_search` attempts inside model trajectories.
  - It creates `output_root/progress/events.jsonl` and `output_root/progress/state.json` through `benchmarking.dashboard.progress.ProgressWriter`. Events include run/group/record start and completion plus errors, so the local dashboard can report current record ids for active groups. When reading progress, the dashboard preserves state-file status/current-record metadata but reconciles stale `total`, `completed`, and per-group completed counts against final `per-record` artifacts and the expected result total, so resumed runs cannot display outdated counts such as `3/3` when four per-group outputs exist. Historical runs without progress artifacts remain readable by falling back to `waves/` status and `per-record/` counts.
  - After writing `results.json`, the CLI removes stale legacy `summary_by_group.csv` and `summary_by_group_and_subset.csv` files from the output root, writes the final `runtime-manifest.json`, starts the detached automated evaluation process, and then rewrites `runtime-manifest.json` with the `automated_evaluation` launch status. If launcher setup raises, the CLI writes a `launch_failed` status under `analysis/status.json` and still returns according to the benchmark run outcome.
  - Default groups are `single_llm_skills_on`, `single_llm_skills_off`, and `chemqa_skills_on`; all set `websearch=False`, so the old web-on/web-off matrix is no longer an experiment axis. A false benchmark web flag disables both OpenClaw `web_search` and the general `web_fetch` tool in the run-scoped config; the single-LLM skills-on/skills-off comparison therefore cannot use generic web retrieval unless a future experiment group explicitly opts into benchmark web access.
  - `BENCHMARK_SKILLS_ALLOWLIST` is loaded by `benchmarking.skills.tree.benchmark_skill_allowlist()` from entries in the historical `workspace/skills/chemistry-routing-matrix.json` inventory where `single_agent_exposure` is `true`. Startup health filtering writes only available skills to skills-on runner agents and renders those same available leaves and summaries in the skills-on user prompt as a neutral catalog. Runtime/orchestration bundles such as `benchmark-cleanroom`, `debateclaw-v1`, and `chemqa-review` are not chemistry provider skills and are not included in the single-agent allowlist. `single_llm_skills_off` writes an explicit empty runner `skills: []` and receives no catalog. Judge configs do not receive the benchmark allowlist.
  - Runtime configs add `workspace/skills` to `skills.load.extraDirs` so run-scoped benchmark workspaces can discover the newly available local skills.
  - Before dispatching a record, `benchmarking.runtime.bundles` materializes run-local input bundles for benchmark visual inputs. SuperChem bundles parse `/media/uploads/...` locators from the current question/options text, copy only those visible per-record images into `images/`, rewrite `question.md` to reference the local files, and do not expose full shared asset buckets or reference-reasoning images to answer-generation prompts. Required visible multimodal images that cannot be resolved raise a package runtime-bundle error, which `benchmarking.workflow.cli` translates to `BenchmarkError` for legacy callers. HLE bundles decode base64 image data or copy local image files so prompts with "provided information" retain their visual context.
  - For `single_llm_*` groups:
    - Every primary or timeout-retry attempt prepares a new sentinel-managed workspace from the appropriate canonical skills-on/off template. Both groups receive `BENCHMARK_WORKSPACE_DIR` and current-only scratch/request/output/notes directories; the previous attempt's complete workspace is archived before a retry starts.
    - The runner shells out through `benchmarking/runtime/single_llm_openclaw_wrapper.py`, which invokes `openclaw agent --local ... --json`, validates stdout with `benchmarking.core.result_contract`, and normalizes invalid stdout into `result.meta.stdout_diagnostics`.
    - It does not use a native Python OpenClaw API.
    - Invalid stdout, such as tool argument JSON without schema-valid answer payloads, is never passed to answer extraction or the evaluator. The record becomes failed/unscored with `agent_result_contract_invalid`.
    - Schema-valid stdout still must pass the runner's internal candidate-answer contract before evaluation. Timeout sentinels such as `LLM request timed out.` return `agent_response_timeout`; missing required answer markers/fields return `candidate_answer_contract_invalid`; both paths keep `answer` empty and retain raw payload text only in `runner_meta.candidate_answer_contract`. A schema-complete answer produced later in the same session by native output, transcript recovery, or finalization rescue takes precedence over an earlier idle-timeout diagnostic, which remains metadata and no longer poisons the candidate contract. Plain `FINAL ANSWER:` and Markdown-bold `**FINAL ANSWER:** answer` / `**FINAL ANSWER: answer**` lines are accepted when the marker carries a non-empty answer. FrontierScience Research candidate-answer contract metadata also reports `has_research_final_marker` for the preferred/family research markers while continuing to score complete legacy fallback sections during the transition.
    - OpenClaw `replayInvalid` is treated as a replay-safety diagnostic, not as proof that a native final answer is unavailable. When a schema-valid payload already contains a complete answer for the current `eval_kind`, the single-LLM runner keeps it as `RunStatus.COMPLETED`/`native_final`, preserves `runner_meta.convergence.replay_invalid_diagnostics`, and does not perform transcript recovery. No-response fallbacks, stream errors, timeout sentinels, and blocked/abandoned outputs without a complete answer still trigger the existing failure, retry, transcript-recovery, or finalization-rescue paths.
    - Timeout retry is benchmark-wrapper scoped: `subprocess.TimeoutExpired`, OpenClaw/model request timeout sentinels, `meta.error.kind == "timeout"`, timeout-family transcript `openclaw:prompt-error` events, HTTP 408/499/500/502/503/504, gateway/deadline exceeded, and transport timeout/reset codes can trigger fresh-session retry. Auth/billing/quota/rate-limit/context-overflow/model-not-found/image-size/role-ordering/format errors, approval timeouts, and sandbox/exec/tool/skill-script timeouts do not retry. Plain `replayInvalid` or `livenessState=abandoned` retries only when payload/meta/transcript also contains timeout-family evidence. When the wrapper/provider/subprocess exits nonzero, the parent runner re-inspects the explicit session store and deterministic transcript path before workspace audit; an existing clean transcript therefore preserves the original execution-error classification instead of being replaced by `transcript_unavailable`.
    - Runner-level convergence policy and timeout retry configuration are written into top-level results/manifests and per-record `runner_meta`; the wrapper records transcript assistant-turn/tool-call diagnostics plus `prompt_error_count`, `latest_prompt_error`, `latest_prompt_error_is_timeout`, `time_reminder`, and `replay_invalid_diagnostics` diagnostics, and can recover the latest complete benchmark answer from the session transcript after timeout-like or error-like OpenClaw output using eval-aware complete-answer detection. Generic/SuperChem/ChemBench/FrontierScience Olympiad recovery remains strict about required final-answer markers, while FrontierScience Research recovery prefers `FINAL RESEARCH ANSWER`, accepts the conservative compatible research and legacy final/conclusion fallback list, and still rejects marker blocks that contain only references, checklists, or other process-only material. Tool/turn counts are never hard failure limits.
    - `--single-agent-thinking` controls the OpenClaw `--thinking` level forwarded to single-agent benchmark turns; default `high` preserves historical behavior. This does not affect ChemQA workers.
    - `single_llm_skills_on` prepends a neutral catalog for the health-filtered allowlist while `single_llm_skills_off` configures an empty skill list and receives no catalog. The catalog describes basic skill capabilities but does not require reading or using `act-like-a-chemist`, impose a provider order, prescribe an SOP, or add a skills-on/off command.
    - When an input bundle exists, the prompt names the bundle directory, tells the agent to read `question.md`, and explicitly instructs it to inspect referenced local images before answering.
    - For `single_llm_skills_on`, runtime config provisioning writes a benchmark-managed `TOOLS.md` into the runner workspace. That file tells the model to run local skill scripts with a two-step uppercase-placeholder pattern: `write {"path": "REQUEST_JSON_PATH", "content": "REQUEST_JSON_STRING"}` followed by `exec {"command": "python /Users/xutao/.openclaw/workspace/scripts/run_skill.py --workspace-root /Users/xutao/.openclaw/workspace --execution-cwd \"$BENCHMARK_SKILL_SCRATCH_DIR\" --script SCRIPT_PATH -- --request-json REQUEST_JSON_PATH --output-dir OUTPUT_DIR --json"}` after every placeholder is replaced. It rejects empty `exec` arguments, direct `python skills/...`, pipes, redirects, inline Python, `head`/`tail`, temporary runner scripts, and nonexistent tool names such as `python3`, `script`, `cmd`, `command`, `bash`, `reasoning`, and `system-event-scheduler`. The runner uses the canonical project root for `uv --project` dependency resolution and injects `.benchmark-scratch/<record>/<session_id>/` plus `BENCHMARK_SKILL_*` directories so local skill request files, outputs, downloads, temporary scripts, and notes stay grouped under the current record/session scratch directory. This benchmark-managed `TOOLS.md` is not written for `single_llm_skills_off`, judge agents, or ChemQA slot workspaces.
    - The single-agent runner writes `runner_meta.skill_use_audit` after OpenClaw returns, including configured skill count/list, startup skill-health summary, generic OpenClaw tool-call counts/names, generic exec tool-call counts/names, skills-enabled skill script call counts/names, model-declared skipped traces, generic no-tool-call flags, and no-skill-tool-call flags.
  - For `chemqa_*` groups:
    - Coordinator plus five proposer/reviewer slots are prepared as an all-or-fail `WorkspaceLeaseSet`; each slot has an independent active path and shared record/attempt identity. Recovery seals the previous set before preparing a fresh set.
    - The runner shells out to ChemQA skill scripts to compile/materialize/launch the run.
    - ChemQA worker thinking levels remain controlled by the selected `--chemqa-model-profile` and its slot `thinking` entries, not by `--single-agent-thinking`.
    - The same convergence policy controls unchanged-status recovery attempts and max recovery attempts before a structured `convergence_limit_exceeded` failure.
    - When an input bundle exists, the ChemQA goal names the bundle and instructs workers to open `question.md` and inspect referenced images; the bundle directory is also passed as an additional file workspace.
    - It monitors run status via files under `chemqa-review/control/run-status/`.
    - If run-status remains unchanged across polling intervals, it invokes `chemqa-review/scripts/recover_run.py` with the run-scoped `CLAWTEAM_DATA_DIR`; repeated recovery attempts are rate-limited while the status signature remains unchanged.
    - While a worker phase is still in progress, run status may carry a `role_phase` block with turn index, max turns, classification such as `waiting_for_artifact` / `repairing_invalid_artifact` / `repairing_stale_artifact`, and the last structured turn/artifact diagnostics.
    - It treats DebateClaw `phase=done/status=done` as protocol terminal only while Artifact Flow is still `finalizing`; benchmark-visible `status=done/terminal_state=completed|failed` is published only after canonical final/failure artifacts, manifest, and `qa_result.json` are readable.
    - It prefers canonical `qa_result_path`, `final_answer_artifact_path`, `failure_artifact_path`, and `artifact_manifest_path` from run status. If artifacts are missing, it tries to rebuild them from protocol files with `collect_artifacts.py`.
    - Default scoring reads only canonical `final_answer_artifact.json`. If a completed/accepted output lacks that artifact, the runner may migrate completed legacy `qa_result`/protocol/final-submission data into a canonical final artifact before scoring; otherwise the run is non-scoreable with `missing_canonical_terminal_artifact`.
    - Proposer proposal files, `final_answer_preview`, and `failure_artifact.answer_projection` are diagnostic-only in default runs and do not create scoreable ChemQA recovered results.
  - All per-record outputs are persisted immediately under `per-record/<group>/<slug>.json`; final aggregation rewrites those files once to attach property_calculation public gold to the report-facing `reference_answer` field.
  - LLM-judge evaluation calls use a fresh `benchmark-judge-<id>` OpenClaw session id and independent judge attempt workspace, clear stale `agent:benchmark-judge:main` pointers before the call, verify postflight session/file/model state before parsing judge stdout, audit the transcript, and archive the complete workspace after verdict collection. Contamination/audit/archive failures invalidate the verdict. `--judge-agent-thinking` controls the judge OpenClaw `--thinking` level; default `high` preserves historical behavior.
  - Cleanup manifests are registered and benchmark-cleanroom process finalization runs in `finally`/signal/atexit paths; session stores, transcripts, run artifacts, and sealed workspace archives remain on disk for audit. Aggregate reports count a workspace as isolated only when preflight and archive succeed and contamination audit status is exactly `clean`.

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
  - `benchmarking/workflow/prompts.py` exposes a neutral health-filtered skill catalog only to skills-on single-agent prompts and otherwise avoids skill-use commands or solution-strategy guidance; concrete skill selection and use remain delegated to the model.
  - Single-agent benchmark turns keep a fixed OpenClaw agent id per experiment group but rotate its complete active workspace per attempt; `benchmarking/runtime/single_llm_openclaw_wrapper.py` deletes stale `agent:<id>:main` pointers before each run-scoped session turn, while historical transcripts remain outside later active workspaces for audit.
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
  - No known missing web dashboard implementation surface for the current localhost-only review scope.

- Incomplete implementations
  - No ChemQA native workflow package remains in the source tree; the previous inactive scaffold and unused loader were removed rather than implemented.
- Architectural inconsistencies
  - Intended architecture suggests package-based workflows and reusable modules.
  - Actual behavior is still script-heavy and subprocess-heavy:
    - `benchmarking.workflow.cli` is still a broad package CLI/entrypoint with embedded scheduling and aggregation logic.
    - ChemQA runs are controlled through external state scripts.
  - `workspace/benchmarking/` is now the real benchmark implementation layer, and its old flat compatibility modules have been removed; the legacy root `benchmark_test.py` entrypoint is retained for compatibility.
  - `workspace/pyproject.toml` keeps broad `web-ui` extras for dashboard/runtime tooling; the current implemented app surface is the localhost-only benchmark dashboard under `workspace/benchmarking/dashboard/`.
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
  - `workspace/scripts/sync_openclaw_qwen_provider.py` updates the live environment-backed Qwen provider routes in `openclaw.json` and removes legacy token-plan-backed `agents/*/agent/models.json` provider caches; caches with a different Qwen base URL are intentionally skipped.
  - Repo root stores live runtime state, backups, SQLite DBs, session logs, and generated artifacts beside source.
  - Optional dependencies listed in `pyproject.toml` may imply capabilities that do not actually exist in code.
  - Verifier-grounded dataset synchronization intentionally fails closed when the configured wheel, SHA256, runtime manifest, package version, or task inventory differs. A new verifier release therefore requires an explicit release-config update, isolated runtime install, dataset resync, and regression test run; silently falling back to a source checkout is forbidden.
  - Attempt workspace isolation prevents accidental visibility of prior benchmark workspace contents and audits forbidden path access, but it is not an OS security boundary. A process running as the same user can still traverse the home directory unless a future filesystem sandbox, container, or separate user boundary is added.

## 7. Suggested Next Steps
- Continue shrinking the package CLI:
  - Move more scheduling, result persistence, and aggregation glue from `workspace/benchmarking/workflow/cli.py` into smaller package modules once their contracts stabilize.
- Separate source from runtime state:
  - Move generated workspaces, logs, DBs, and mutable OpenClaw runtime state outside the analyzed source tree or document them as runtime-only roots.
- Continue hardening the benchmark dashboard:
  - Add richer transcript/trajectory views and optional launch controls only if those workflows become needed; the current dashboard intentionally reviews and monitors runs but does not start benchmark processes.
- Harden artifact and cleanup flows:
  - Continue reducing filename/path guessing in legacy ChemQA artifact recovery paths now that canonical Artifact Flow paths exist.
  - Centralize run manifest/session/process metadata contracts used by runners, drivers, cleanup, and single-LLM session-isolation audits.
- Harden attempt workspace isolation only if the threat model expands:
  - Add a filesystem sandbox/container or separate OS user when active prevention of parent/archive/scorer path access is required; the current implementation intentionally provides lifecycle isolation plus transcript/tool auditing for accidental-history contamination.
- Add clearer ownership boundaries:
  - Separate DebateClaw engine logic, ChemQA protocol logic, benchmark orchestration, and paper pipeline into smaller modules with fewer embedded subprocess wrappers.
