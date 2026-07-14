# Single-LLM Skill-Tree Benchmark Blocker Diagnosis

Date: 2026-05-09
Branch: `skill-autonomous-discovery-audit`
Run output:
`/Users/xutao/.config/superpowers/worktrees/workspace/skill-autonomous-discovery-audit/state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench`

## Context

This diagnosis is based on the fixed 12-record benchmark set under:

```bash
/Users/xutao/.openclaw/workspace/temp-benchmarks/frontierscience/data/frontierscience_chemistry_pool.jsonl
/Users/xutao/.openclaw/workspace/temp-benchmarks/hle/data/hle_chemistry_pool.jsonl
/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl
```

The run command was:

```bash
uv run python benchmark_test.py \
  --groups single_llm_skills_on \
  --files /Users/xutao/.openclaw/workspace/temp-benchmarks/frontierscience/data/frontierscience_chemistry_pool.jsonl,/Users/xutao/.openclaw/workspace/temp-benchmarks/hle/data/hle_chemistry_pool.jsonl,/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl \
  --single-agent-model openai/gpt-5.4 \
  --judge-model su8/gpt-5.4 \
  --max-concurrent-groups 1 \
  --inter-wave-delay-seconds 0 \
  --exact-output-dir state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench
```

Run-level facts:

- `run_completed_count=12`, `run_failed_count=0`.
- `session_isolation_ok_count=12`, `session_isolation_failed_count=0`, `session_contaminated_count=0`.
- `single_agent_model=openai/gpt-5.4`; OpenAI provider shim resolves to SU8 endpoint/key.
- `judge_model=su8/gpt-5.4`.
- Old prompt strings were absent from all 12 prompts:
  - `Web search may be used if helpful.`
  - `Do not use web search or external browsing.`

## Blocker 1: Final Answer Extraction Drops Valid Visible Answers

### Symptom

Several records have correct final answers visible in the OpenClaw transcript, but the per-record benchmark JSON stores:

```json
"answer_text": "",
"short_answer_text": ""
```

The evaluator then sees an empty candidate and scores the record as wrong. This makes aggregate benchmark scores unusable for merge gating.

### High-Impact Evidence

All paths below are from the completed 12-record run.

#### `fs-chem-olympiad-1440c195-fba2-48dc-a03d-7b9420891daf`

Per-record JSON:

```bash
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on/fs-chem-olympiad-1440c195-fba2-48dc-a03d-7b9420891daf.json
```

Transcript:

```bash
/Users/xutao/.openclaw/agents/benchmark-single-skills-on/sessions/benchmark-single_llm_skills_on-fs-chem-olympiad-1440c195-fba2--6524eef8-08fe1b04.jsonl
```

Observed:

- Reference answer: `7.59 micrograms.`
- Per-record `answer_text`: empty.
- Transcript line 30 includes visible assistant text ending with `FINAL ANSWER: 7.59 µg`.
- Judge score is 0 because candidate answer is empty.

#### `fs-chem-olympiad-23cb57a5-1c57-4f6e-8205-7ffb6116a16a`

Per-record JSON:

```bash
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on/fs-chem-olympiad-23cb57a5-1c57-4f6e-8205-7ffb6116a16a.json
```

Transcript:

```bash
/Users/xutao/.openclaw/agents/benchmark-single-skills-on/sessions/benchmark-single_llm_skills_on-fs-chem-olympiad-23cb57a5-1c57--8d9eed73-0108cf27.jsonl
```

Observed:

- Reference answer includes IUPAC `3-(trifluoromethyl)aniline`.
- Per-record `answer_text`: empty.
- Transcript line 12 contains `FINAL ANSWER: 3-(trifluoromethyl)aniline`.
- Judge score is 0 because candidate answer is empty.

#### `hle-chemistry-66ec5c9bf633b774fa320b36`

Per-record JSON:

```bash
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on/hle-chemistry-66ec5c9bf633b774fa320b36.json
```

Transcript:

```bash
/Users/xutao/.openclaw/agents/benchmark-single-skills-on/sessions/benchmark-single_llm_skills_on-hle-chemistry-66ec5c9bf633b774fa320b36-fa8f5608.jsonl
```

Observed:

- Reference answer: `273`.
- Per-record `answer_text`: empty.
- Transcript line 60 contains `Answer: 273 kcal/mol`.
- Judge score is 0 because candidate answer is empty.

#### SuperChem records

All three SuperChem records have correct visible option letters in transcript but empty per-record answer fields:

| Record | Reference | Visible transcript final answer | Per-record answer |
| --- | --- | --- | --- |
| `superchem-2327dcfe-f7d0-426c-bf65-503fc7bd84c6-mm` | `A` | `FINAL ANSWER: A` | empty |
| `superchem-52ca56e9-0bac-441f-83bc-f6a006a71719-mm` | `H` | `FINAL ANSWER: H` | empty |
| `superchem-bf841154-c942-43d3-97dd-a5aa5aa1a439-mm` | `D` | `FINAL ANSWER: D` | empty |

### Likely Root Cause

`SingleLLMRunner` derives the scoreable response only from `payloads` returned by the OpenClaw wrapper:

```python
payloads = list((result_payload.get("payloads") or []))
full_response_text = self._summarize_payloads(payloads)
short_answer_text, full_response_text = self._normalize_answer_tracks(full_response_text=full_response_text)
```

Relevant location:

```bash
benchmarking/runners/single_llm.py
```

When OpenClaw returns a JSON payload that omits final visible assistant text from `payloads`, the runner does not fall back to:

- `runner_meta.finalAssistantVisibleText`
- `runner_meta.finalAssistantRawText`
- visible assistant text in the session transcript named by `runner_meta.session_isolation.postflight_entry_session_file`

As a result, valid final answers are present in the persisted transcript but absent from the benchmark result JSON.

### Required Fix

Implement robust single-agent answer extraction with ordered fallback:

1. Current `payloads` summary.
2. `runner_meta.finalAssistantVisibleText`.
3. `runner_meta.finalAssistantRawText`, only after stripping provider/tool metadata if needed.
4. Last visible assistant text from the transcript file in `runner_meta.session_isolation.postflight_entry_session_file`.

The fallback should preserve the full visible response as `answer_text`/`full_response_text`, then derive `short_answer_text` with existing `extract_candidate_short_answer`.

For session-isolation failure records, keep the existing behavior that marks the record failed/non-evaluable.

### Tests To Add

Add or update runner tests so that:

- If `payloads=[]` but `runner_meta.finalAssistantVisibleText` contains `FINAL ANSWER: A`, the per-record `answer_text` is non-empty and `short_answer_text == "A"` for SuperChem.
- If both payloads and meta visible text are empty but `postflight_entry_session_file` contains visible assistant text, the runner recovers the answer from transcript.
- The transcript fallback reads only the requested session transcript, not stale `main` or any other session.
- A malformed or missing transcript does not crash the runner; it should leave answer empty and attach diagnostic metadata.
- Existing session-isolation-failed path still produces `session_isolation_failed` and does not enter normal answer evaluation.

Suggested relevant tests:

```bash
uv run pytest tests/test_benchmark_test.py tests/test_single_llm_session_wrapper.py -q
```

## Blocker 2: Chemistry Tool / Asset Environment Prevents Skill Execution From Producing Reliable Evidence

### Symptom

The skill tree successfully nudges the model toward relevant chemistry skills, but many attempted tool paths fail at execution time. This confounds the evaluation of whether the skill tree improves answer quality.

Transcript-level audit of the 12-record run found:

- 11/12 records emitted structured tool calls.
- 10/12 records read concrete chemistry `SKILL.md` files.
- 7/12 records used `web_search` or `web_fetch`.
- 9/12 records used `exec` or `process`.

However, failures included:

- `web_search` returning `fetch failed`.
- `web_fetch` returning 403/security-wrapper content.
- Missing Python modules: `bs4`, `requests`, `sympy`.
- RDKit skill discovered but execution returns `rdkit_missing`.
- SuperChem image assets are not materialized into runtime bundles.

### Evidence: Tool/Dependency Failures

Observed failure categories in transcript audit:

```text
web_fetch_failed: 12
web_403_security: 11
python_missing_module: 4
rdkit_missing: 4
missing_file_or_asset: 8
skill_contract_missing_request: 11
exec_preflight_refusal: 1
```

Concrete examples:

- `fs-chem-research-1567b1bb...`
  - Reads `literature-review/SKILL.md` and `paper-access/SKILL.md`.
  - `web_search` returns `fetch failed`.
  - Python scraping fails with `ModuleNotFoundError: No module named 'bs4'`.
  - `web_fetch` hits 403/security wrapper.

- `hle-chemistry-66e8ae613aa94517d4573b33`
  - Attempts web search/fetch and local retrieval.
  - Python fails with `ModuleNotFoundError: No module named 'requests'`.
  - ACS-like web paths hit 403/security or SSL issues.

- `superchem-52ca56e9-0bac-441f-83bc-f6a006a71719-mm`
  - Reads `rdkit/SKILL.md`.
  - RDKit execution fails with `rdkit_missing`.
  - Initial image reads fail with `ENOENT`.

### Evidence: SuperChem Runtime Bundles Have No Images

For all three SuperChem records in this run:

```json
"runtime_bundle": {
  "image_files": []
}
```

The bundle directories exist, but `images/` is empty:

```bash
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/input-bundles/superchem-2327dcfe-f7d0-426c-bf65-503fc7bd84c6-mm
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/input-bundles/superchem-52ca56e9-0bac-441f-83bc-f6a006a71719-mm
state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/input-bundles/superchem-bf841154-c942-43d3-97dd-a5aa5aa1a439-mm
```

The source temp benchmark JSONL stores machine-local paths such as:

```text
/home/dministrator/.openclaw/benchmarks/superchem/assets/_shared/...
```

At runtime, `Path(...).expanduser().resolve()` turns these into non-existent macOS paths under `/System/Volumes/Data/home/dministrator/...`. Since the files do not exist, `ensure_runtime_bundle` writes the unresolved source paths into `question.md` instead of failing fast or remapping them.

Relevant code:

```bash
benchmark_test.py
```

Functions:

- `superchem_image_paths`
- `ensure_runtime_bundle`
- `build_superchem_question_markdown`

Current behavior:

```python
if source_path.is_file():
    shutil.copy2(source_path, target_path)
    image_files.append(target_path)
    image_relpaths.append(str(target_path.relative_to(bundle_dir)))
else:
    image_relpaths.append(str(source_path))
```

This silently preserves broken absolute paths. It gives the model a `Local images to inspect` section that points to files it cannot open.

### Likely Root Causes

There are two separate root causes.

#### 2A. Tool runtime dependency mismatch

The benchmark/OpenClaw execution environment does not include dependencies that the newly exposed chemistry skill surface assumes are available:

- RDKit for `rdkit` skill.
- Common Python scraping/scientific packages: `requests`, `bs4`, `sympy`.
- PDF/document tools seen in related traces may also be missing, such as `pdfinfo`, `pdftoppm`, `mutool`, `magick`, `pypdf`, `PyPDF2`, `fitz`, or `pdfminer`.

This turns valid model intent into tool-result errors.

#### 2B. SuperChem dataset/bundle portability issue

The fixed temp benchmark records include absolute paths from another machine/user. Runtime bundle materialization does not remap these paths relative to the JSONL dataset location or known local benchmark asset roots, and it does not fail fast when required multimodal assets are missing.

This contradicts the current dev spec expectation that SuperChem image paths are cleaned at the data layer and required multimodal images fail fast when unavailable.

### Required Fixes

#### Tool/runtime environment

Choose one of these designs and make it explicit:

1. Install required dependencies into the benchmark/OpenClaw execution environment.
2. Route skill script execution through the workspace `.venv` that owns the dependencies.
3. Add graceful capability gating so unavailable skills are not advertised as executable, or are advertised with explicit unavailable status.

Minimum dependencies to verify:

```bash
uv run python - <<'PY'
mods = ["requests", "bs4", "sympy", "rdkit"]
for name in mods:
    try:
        __import__(name)
        print(name, "ok")
    except Exception as exc:
        print(name, "missing", repr(exc))
PY
```

Recommended behavior for skill execution:

- A listed skill should either execute its documented first path successfully, or return a clear structured unavailable result before the model invests multiple turns.
- `skill_use_audit` should distinguish:
  - skill documentation read,
  - skill script/tool successfully executed,
  - skill execution attempted but unavailable,
  - generic shell/web fallback.

#### SuperChem asset materialization

Do not silently preserve broken absolute image paths.

Preferred behavior:

1. Normalize image paths at dataset load or extraction time:
   - Strip machine-local prefixes like `/home/dministrator/.openclaw/`.
   - Resolve relative to the source JSONL directory or a configured local benchmark asset root.
2. During `ensure_runtime_bundle`, copy every required image into `input-bundles/<record>/images/`.
3. Rewrite `question.md` to reference only local bundle-relative paths like `images/img01.png`.
4. If a required multimodal image is missing, fail the record as non-evaluable with a specific failure code such as `missing_required_visual_asset`.

Add metadata to `runtime_bundle`:

```json
{
  "image_files": [".../images/img01.png"],
  "missing_image_paths": [],
  "source_image_paths": [],
  "asset_resolution_status": "ok"
}
```

### Tests To Add

Add SuperChem bundle tests:

- Absolute stale path from `/home/dministrator/.openclaw/...` is remapped to a local asset root when the file exists.
- Existing image files are copied to `input-bundles/<record>/images/`.
- Generated `question.md` contains only local relative image paths.
- Missing required image produces a deterministic non-evaluable record/failure, not a normal benchmark answer with broken paths.
- `runtime_bundle.image_files` is non-empty for multimodal records with images.

Add environment/skill availability tests:

- RDKit skill availability check returns `available=true` only when import succeeds.
- If a configured skill is unavailable, prompt/audit metadata reflects that instead of implying it can run normally.
- `skill_use_audit` counts transcript-level skill reads and execution attempts accurately enough for benchmark analysis.

## Reproduction / Audit Commands

Check summary:

```bash
cat state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/summary_by_group.csv
cat state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/summary_by_group_and_subset.csv
```

Check global OpenAI shim:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/Users/xutao/.openclaw/openclaw.json").read_text())
openai = d["models"]["providers"]["openai"]
print(openai["baseUrl"])
print(openai["apiKey"]["id"])
print(openai["api"])
print(openai["models"][0]["id"])
print(d["agents"]["defaults"]["models"]["openai/gpt-5.4"])
print(d["agents"]["defaults"]["model"]["primary"])
PY
```

Check prompt tightening:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
root = Path("state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on")
phrases = ["Web search may be used if helpful.", "Do not use web search or external browsing."]
for phrase in phrases:
    hits = []
    for p in root.glob("*.json"):
        d = json.loads(p.read_text())
        blob = "\n".join(str(x or "") for x in [d.get("prompt"), (d.get("runner_meta") or {}).get("finalPromptText")])
        if phrase in blob:
            hits.append(p.name)
    print(repr(phrase), hits)
PY
```

Check session isolation:

```bash
uv run python - <<'PY'
import json, os
from pathlib import Path
root = Path("state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on")
for p in sorted(root.glob("*.json")):
    d = json.loads(p.read_text())
    iso = (d.get("runner_meta") or {}).get("session_isolation") or {}
    req = iso.get("requested_session_id")
    post = iso.get("postflight_entry_session_id")
    tf = iso.get("postflight_entry_session_file") or ""
    ok = iso.get("session_isolation_ok") and req == post and req in os.path.basename(tf)
    print(d.get("record_id"), ok, os.path.basename(tf))
PY
```

Check transcript-visible answers where per-record answers are empty:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
root = Path("state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/per-record/single_llm_skills_on")
for p in sorted(root.glob("*.json")):
    d = json.loads(p.read_text())
    if (d.get("answer_text") or "").strip():
        continue
    iso = (d.get("runner_meta") or {}).get("session_isolation") or {}
    t = Path(iso.get("postflight_entry_session_file") or "")
    finals = []
    if t.exists():
        for i, line in enumerate(t.read_text(errors="replace").splitlines(), 1):
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get("message") or obj
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                for c in msg.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text = c.get("text") or ""
                        if "FINAL ANSWER" in text or "Answer:" in text:
                            finals.append((i, text[-220:].replace("\\n", " ")))
    print("\\n", d.get("record_id"), "reference=", d.get("reference_answer"))
    print("per-record answer_text empty; transcript finals:", finals[-3:])
PY
```

Check SuperChem bundle image materialization:

```bash
uv run python - <<'PY'
from pathlib import Path
root = Path("state/benchmark-runs/temp-benchmark-20260509-single-llm-skill-on-openai-shim-gpt54-tempbench/input-bundles")
for d in sorted(root.glob("superchem-*")):
    imgs = list((d / "images").glob("*")) if (d / "images").exists() else []
    print(d.name, "images:", len(imgs))
    q = d / "question.md"
    if q.exists():
        broken = [line for line in q.read_text(errors="replace").splitlines() if "/home/dministrator/" in line or "/System/Volumes/Data/home/dministrator/" in line]
        print("broken path refs:", len(broken))
PY
```

## Merge Gate Recommendation

Do not use this run's aggregate score as a merge gate until these blockers are fixed. The run gives useful evidence that GPT-5/openai guard and the skill tree improve structured tool intent, but it cannot reliably measure answer quality while:

1. valid final answers are dropped before evaluation, and
2. advertised chemistry skills/assets frequently fail at execution/materialization time.

After fixing both blockers, rerun the same fixed 12-record set under `/Users/xutao/.openclaw/workspace/temp-benchmarks` with `--single-agent-model openai/gpt-5.4`.

## Resolution Plan Status

The implementation plan in `docs/superpowers/plans/2026-05-09-benchmark-result-contract-skill-runtime-health.md` addresses the two merge-blocking architecture issues:

- malformed OpenClaw stdout can no longer enter answer extraction or evaluation as payload data;
- benchmark skills-on runs use startup health checks and a workspace `uv run` skill runner so unavailable skill paths become explicit diagnostics instead of repeated ambiguous tool failures.
