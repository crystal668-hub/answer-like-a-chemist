# Autonomous Skill Discovery and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace deterministic record-level skill routing with full benchmark skill availability, lightweight skill discovery guidance, model-driven skill use, and post-run audit metrics.

**Architecture:** Keep `single_llm_skills_on` configured with the full benchmark skill allowlist for every record. Replace the compact routing matrix prompt with a short discovery guide that helps the model choose skills without narrowing its options or offering a skipped-trace compliance path. Record actual tool-use evidence after each run and report execution metrics separately from answer quality.

**Tech Stack:** Python 3.12, pytest/unittest, OpenClaw config JSON, existing benchmark modules under `benchmarking/`, local skill bundles under `skills/`, ChemQA prompt contracts under `skills/chemqa-review/prompts/`.

---

## Current Branch And Old Branch Decision

New implementation branch:

- Branch: `skill-autonomous-discovery-audit`
- Worktree: `/Users/xutao/.config/superpowers/worktrees/workspace/skill-autonomous-discovery-audit`
- Base: local `master` at `d02027d`
- Baseline check already run: `uv run pytest tests/test_benchmark_prompts.py tests/test_benchmark_config_runtime.py tests/test_experimental_chemistry_skill_matrix.py -q`
- Baseline result: `19 passed`

Old router repair branch:

- Branch: `skill-injection-routing-repair`
- Worktree: `/Users/xutao/.config/superpowers/worktrees/workspace/skill-injection-routing-repair`
- Status: clean
- Merge status: not merged into `master`
- Unique commits: 9 commits from `f5b3151` through `caa3e60`
- Decision: the branch can be deleted after this plan is committed if the team accepts discarding the record-scoped router implementation. It should not be merged because its core architecture conflicts with the approved full-availability design.

Deletion commands when approved:

```bash
git -C /Users/xutao/.openclaw/workspace tag archive/skill-injection-routing-repair caa3e60
git -C /Users/xutao/.openclaw/workspace worktree remove /Users/xutao/.config/superpowers/worktrees/workspace/skill-injection-routing-repair
git -C /Users/xutao/.openclaw/workspace branch -D skill-injection-routing-repair
```

The archive tag preserves a named recovery point before deleting the unmerged branch ref.

## File Structure

Create:

- `benchmarking/skill_inventory.py`
  - Owns loading the chemistry skill inventory, deriving the full benchmark skill allowlist, and rendering a concise discovery guide.
- `benchmarking/skill_audit.py`
  - Owns conservative post-run tool-use audit extraction from runner metadata and final answer text.
- `tests/test_benchmark_skill_inventory.py`
  - Tests inventory loading, allowlist coverage, discovery-guide compactness, and absence of routing language.
- `tests/test_benchmark_skill_audit.py`
  - Tests audit classification for actual tool calls, model-declared skipped traces, and no-tool-call runs.

Modify:

- `benchmarking/prompts.py`
  - Replace `render_compact_skill_routing_table()` with `render_skill_discovery_guide()`.
  - Remove route-selection and skipped-route wording.
- `benchmarking/config_renderer.py`
  - Preserve full runner skill allowlists for skills-on groups; add regression tests that no per-record selected subset exists.
- `benchmarking/runners/single_llm.py`
  - Add `skill_audit` metadata after OpenClaw returns a result.
- `benchmarking/reporting.py`
  - Add aggregate counts for actual skill tool use and skipped-trace declarations.
- `benchmark_test.py`
  - Import inventory loader from `benchmarking.skill_inventory`.
- `tests/test_benchmark_prompts.py`
  - Update expectations from routing table to autonomous discovery guide.
- `tests/test_benchmark_config_runtime.py`
  - Assert full paper pipeline and provider skills remain available in rendered skills-on configs.
- `tests/test_benchmark_test.py`
  - Update loader import expectations and reporting summary expectations.
- `tests/test_experimental_chemistry_skill_matrix.py`
  - Rename or replace routing tests with inventory tests.
- `skills/chemqa-review/scripts/provider_trace_policy.py`
  - Remove deterministic route-trigger requirements and skipped-as-valid acceptance.
- `skills/chemqa-review/prompts/contracts/proposer-main.md`
  - Remove `not applicable` / `intentionally skipped` as a normal compliance path.
- `skills/chemqa-review/prompts/contracts/reviewer-evidence-trace.md`
  - Treat missing tool evidence as a finding without accepting skipped trace as equivalent.
- `skills/chemqa-review/prompts/contracts/reviewer-counterevidence.md`
  - Same trace-policy cleanup for counterevidence reviews.
- `skills/chemqa-review/prompts/contracts/reviewer-reasoning-consistency.md`
  - Same trace-policy cleanup for reasoning reviews.
- `skills/chemqa-review/prompts/modules/context/required-skills.md`
  - Replace routing-policy wording with inventory/discovery wording.
- `GLOBAL_DEV_SPEC.md`
  - Update architecture and feature matrix after implementation.

Delete or retire:

- `benchmarking/chemistry_routing.py`
  - Delete after all imports move to `benchmarking.skill_inventory`.
  - The JSON inventory can stay at `skills/chemistry-routing-matrix.json` for the first implementation pass to keep file churn low, but all runtime language must treat it as an inventory, not as a router.

## Task 1: Introduce Skill Inventory And Discovery Guide

**Files:**

- Create: `benchmarking/skill_inventory.py`
- Modify: `benchmark_test.py`
- Create: `tests/test_benchmark_skill_inventory.py`
- Modify: `tests/test_experimental_chemistry_skill_matrix.py`

- [ ] **Step 1: Write failing inventory tests**

Create `tests/test_benchmark_skill_inventory.py`:

```python
from __future__ import annotations

from benchmarking.skill_inventory import (
    benchmark_skill_allowlist,
    load_chemistry_skill_inventory,
    render_skill_discovery_guide,
)


def test_benchmark_skill_allowlist_includes_literature_pipeline() -> None:
    allowlist = benchmark_skill_allowlist()

    assert "paper-retrieval" in allowlist
    assert "paper-access" in allowlist
    assert "paper-parse" in allowlist
    assert "paper-rerank" in allowlist
    assert "chem-calculator" in allowlist
    assert "rdkit" in allowlist


def test_discovery_guide_is_compact_and_not_a_router() -> None:
    guide = render_skill_discovery_guide()

    assert "Available chemistry skills" in guide
    assert "Use skills directly when they help answer the record" in guide
    assert "paper-retrieval" in guide
    assert "paper-access" in guide
    assert "paper-parse" in guide
    assert "paper-rerank" in guide
    assert "first matching primary route" not in guide
    assert "selected skill route" not in guide.lower()
    assert "SKILL TRACE: skipped" not in guide
    assert "If you skip" not in guide


def test_inventory_loader_preserves_skill_entries() -> None:
    inventory = load_chemistry_skill_inventory()
    skills = [entry["skill"] for entry in inventory["skills"]]

    assert len(skills) >= 80
    assert len(skills) == len(set(skills))
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_benchmark_skill_inventory.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarking.skill_inventory'`.

- [ ] **Step 3: Implement `benchmarking/skill_inventory.py`**

Create `benchmarking/skill_inventory.py`:

```python
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "skills" / "chemistry-routing-matrix.json"


@lru_cache(maxsize=1)
def load_chemistry_skill_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def benchmark_skill_allowlist() -> tuple[str, ...]:
    return tuple(str(entry["skill"]) for entry in load_chemistry_skill_inventory().get("skills", []))


def render_skill_discovery_guide() -> str:
    return "\n".join(
        [
            "Available chemistry skills are loaded for this run.",
            "Use skills directly when they help answer the record; do not replace a useful skill call with a verbal note.",
            "Discovery guide:",
            "- Calculations: use `chem-calculator` for pH, Ksp, stoichiometry, molar mass, concentration, gas laws, thermodynamics, redox, Nernst, units, and formula math.",
            "- Molecular structure: use `rdkit` for SMILES/InChI, descriptors, rings, stereochemistry, substructure, reactions, molecular formula, molecular mass, and NMR symmetry checks.",
            "- Name and compound identity: use `opsin` for systematic/IUPAC names and `pubchem` for common names, CIDs, synonyms, public properties, and identity disambiguation.",
            "- Literature pipeline: use `paper-retrieval` to find candidates, `paper-access` to resolve/download readable artifacts, `paper-rerank` to prioritize candidates, and `paper-parse` to extract full text or sections.",
            "- Materials, simulation, quantum chemistry, spectra, databases, and ML skills are also available through the OpenClaw skill list; read the relevant `SKILL.md` only after choosing to use that skill.",
            "Record actual tool outputs or file paths when a skill contributes to the answer.",
        ]
    )
```

- [ ] **Step 4: Replace benchmark allowlist imports**

In `benchmark_test.py`, replace both import blocks:

```python
from benchmarking.skill_inventory import benchmark_skill_allowlist, load_chemistry_skill_inventory
```

and:

```python
from workspace.benchmarking.skill_inventory import benchmark_skill_allowlist, load_chemistry_skill_inventory
```

Replace `BENCHMARK_SKILLS_ALLOWLIST` with:

```python
BENCHMARK_SKILLS_ALLOWLIST = list(benchmark_skill_allowlist())
```

If tests still call `benchmark_test.load_chemistry_routing_matrix()`, replace those expectations with `load_chemistry_skill_inventory()`.

- [ ] **Step 5: Run inventory and benchmark import tests**

Run:

```bash
uv run pytest tests/test_benchmark_skill_inventory.py tests/test_benchmark_test.py::BenchmarkRuntimeConfigTests -q
```

Expected: PASS.

- [ ] **Step 6: Commit inventory changes**

Run:

```bash
git add benchmark_test.py benchmarking/skill_inventory.py tests/test_benchmark_skill_inventory.py tests/test_experimental_chemistry_skill_matrix.py
git commit -m "feat: add autonomous skill inventory"
```

## Task 2: Replace Routing Prompt With Autonomous Discovery Prompt

**Files:**

- Modify: `benchmarking/prompts.py`
- Modify: `tests/test_benchmark_prompts.py`
- Modify: `tests/test_experimental_chemistry_skill_matrix.py`

- [ ] **Step 1: Write failing prompt tests**

In `tests/test_benchmark_prompts.py`, update `test_single_llm_prompt_respects_skills_enabled_flag`:

```python
def test_single_llm_prompt_respects_skills_enabled_flag(self) -> None:
    record = BenchmarkRecord(
        record_id="fs-1",
        dataset="frontierscience",
        source_file="/tmp/frontierscience.jsonl",
        eval_kind="frontierscience_olympiad",
        prompt="Calculate the pH.",
        reference_answer="4.7",
        payload={"track": "olympiad"},
    )

    skills_on = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)
    skills_off = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=False)

    self.assertIn("Available chemistry skills are loaded for this run.", skills_on)
    self.assertIn("Use skills directly when they help answer the record", skills_on)
    self.assertIn("paper-retrieval", skills_on)
    self.assertIn("chem-calculator", skills_on)
    self.assertNotIn("Experimental chemistry skill routing rules", skills_on)
    self.assertNotIn("first matching primary route", skills_on)
    self.assertNotIn("SKILL TRACE: skipped", skills_on)
    self.assertNotIn("Do not use OpenClaw skills", skills_on)
    self.assertNotIn("Available chemistry skills are loaded for this run.", skills_off)
    self.assertIn("Do not use OpenClaw skills", skills_off)
```

- [ ] **Step 2: Run prompt tests and verify failure**

Run:

```bash
uv run pytest tests/test_benchmark_prompts.py::BenchmarkPromptsTests::test_single_llm_prompt_respects_skills_enabled_flag -q
```

Expected: FAIL because `build_single_llm_prompt()` still imports and injects the compact routing table.

- [ ] **Step 3: Update `benchmarking/prompts.py`**

Replace:

```python
from .chemistry_routing import render_compact_skill_routing_table
```

with:

```python
from .skill_inventory import render_skill_discovery_guide
```

Replace:

```python
instructions.append(render_compact_skill_routing_table())
```

with:

```python
instructions.append(render_skill_discovery_guide())
```

Do not add no-route, selected-route, or skipped-route wording.

- [ ] **Step 4: Retire routing-table tests**

In `tests/test_experimental_chemistry_skill_matrix.py`, remove tests that assert `route_skill_for_text()` behavior and replace routing-table checks with discovery-guide checks:

```python
from benchmarking.skill_inventory import render_skill_discovery_guide


def test_skill_discovery_guide_mentions_core_skill_families() -> None:
    guide = render_skill_discovery_guide()

    assert "chem-calculator" in guide
    assert "rdkit" in guide
    assert "opsin" in guide
    assert "pubchem" in guide
    assert "paper-retrieval" in guide
    assert "first matching primary route" not in guide
```

- [ ] **Step 5: Run prompt and matrix tests**

Run:

```bash
uv run pytest tests/test_benchmark_prompts.py tests/test_experimental_chemistry_skill_matrix.py tests/test_benchmark_skill_inventory.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit prompt changes**

Run:

```bash
git add benchmarking/prompts.py tests/test_benchmark_prompts.py tests/test_experimental_chemistry_skill_matrix.py
git commit -m "feat: use autonomous skill discovery prompt"
```

## Task 3: Delete Deterministic Router Interfaces

**Files:**

- Delete: `benchmarking/chemistry_routing.py`
- Modify: `tests/test_experimental_chemistry_skill_matrix.py`
- Modify: `skills/chemqa-review/scripts/provider_trace_policy.py`

- [ ] **Step 1: Verify remaining imports**

Run:

```bash
rg -n "chemistry_routing|route_skill_for_text|requirements_for_text|render_compact_skill_routing_table" .
```

Expected before this task: matches remain in tests, docs, and `provider_trace_policy.py`.

- [ ] **Step 2: Update provider trace policy tests or add coverage**

Add a test to the existing ChemQA runtime/provider trace test file that validates skipped status is not accepted as a successful provider trace:

```python
def test_provider_trace_policy_does_not_accept_skipped_as_success() -> None:
    from skills.chemqa_review.scripts.provider_trace_policy import validate_provider_traces

    payload = {
        "submission_trace": [
            {
                "skill": "chem-calculator",
                "status": "skipped",
                "trigger": "numeric_or_formula_math",
                "reason": "model chose ordinary reasoning",
                "risk": "calculation may be wrong",
            }
        ]
    }

    result = validate_provider_traces(
        payload,
        answer_kind="numeric_short_answer",
        prompt="Calculate the pH from the given concentrations.",
        eval_kind="frontierscience_olympiad",
        dataset="frontierscience",
    )

    assert result.errors
```

If the existing test package cannot import `skills.chemqa_review` because the directory name contains a hyphen, load the script with the repository's existing dynamic import helper used in `tests/test_chemqa_artifact_flow.py`.

- [ ] **Step 3: Remove router dependency from `provider_trace_policy.py`**

Delete this import:

```python
from benchmarking.chemistry_routing import requirements_for_text  # noqa: E402
```

Replace `requirements_for_candidate()` with a minimal numeric-answer-only audit requirement:

```python
def requirements_for_candidate(
    *,
    answer_kind: str,
    prompt: str = "",
    eval_kind: str = "",
    dataset: str = "",
) -> list[ProviderTraceRequirement]:
    text = " ".join([answer_kind, eval_kind, dataset, prompt]).lower()
    if answer_kind not in {"numeric_short_answer", "formula_short_answer"} and not any(
        token in text
        for token in (
            "stoichiometric",
            "stoichiometry",
            "equilibrium",
            "acid-base",
            "gas-law",
            "unit-conversion",
            "concentration",
            "electrochemistry",
            "formula-math",
            "molar mass",
            "ksp",
            "ph",
        )
    ):
        return []
    return [
        ProviderTraceRequirement(
            "chem-calculator",
            "numeric_or_formula_math",
            "Numeric or formula-math answer benefits from a deterministic calculation trace.",
        )
    ]
```

Replace `_entry_is_acceptable()` skipped handling:

```python
if status == "skipped":
    return False
```

Update error text from "provide ... skipped trace" to:

```python
"provide status success/partial with a provider result JSON artifact path or structured tool_trace conclusion."
```

- [ ] **Step 4: Delete `benchmarking/chemistry_routing.py`**

Run:

```bash
git rm benchmarking/chemistry_routing.py
```

- [ ] **Step 5: Verify no router imports remain**

Run:

```bash
rg -n "chemistry_routing|route_skill_for_text|requirements_for_text|render_compact_skill_routing_table" benchmarking tests skills/chemqa-review/scripts
```

Expected: no matches.

- [ ] **Step 6: Run relevant tests**

Run:

```bash
uv run pytest tests/test_experimental_chemistry_skill_matrix.py tests/test_benchmark_prompts.py tests/test_chemqa_artifact_flow.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit router deletion**

Run:

```bash
git add tests/test_experimental_chemistry_skill_matrix.py skills/chemqa-review/scripts/provider_trace_policy.py
git commit -m "refactor: remove deterministic skill router"
```

## Task 4: Guard Full Skill Availability In Runtime Config

**Files:**

- Modify: `tests/test_benchmark_config_runtime.py`
- Modify: `tests/test_benchmark_test.py`

- [ ] **Step 1: Add config regression test for full skills-on availability**

In `tests/test_benchmark_config_runtime.py`, add or update a test that renders the `single_llm_skills_on` config and asserts full availability:

```python
def test_single_llm_skills_on_config_keeps_full_benchmark_skill_allowlist(self) -> None:
    payload = render_run_config(
        base_payload=self.base_payload,
        spec=self.experiment_specs["single_llm_skills_on"],
        provisioned=self.provisioned_single,
        judge_model="judge-model",
        runner_model="runner-model",
    )

    agents = payload["agents"]["list"]
    runner = next(agent for agent in agents if agent["id"] == "benchmark-single-skills-on")
    skills = runner["skills"]

    self.assertIn("chem-calculator", skills)
    self.assertIn("rdkit", skills)
    self.assertIn("paper-retrieval", skills)
    self.assertIn("paper-access", skills)
    self.assertIn("paper-parse", skills)
    self.assertIn("paper-rerank", skills)
    self.assertGreaterEqual(len(skills), 80)
```

Adjust fixture names to match the existing test class setup exactly.

- [ ] **Step 2: Run config regression test**

Run:

```bash
uv run pytest tests/test_benchmark_config_runtime.py -q
```

Expected: PASS on current `master` behavior, because full group allowlist is already used.

- [ ] **Step 3: Add negative grep guard for old per-record override**

Add a test in `tests/test_benchmark_test.py`:

```python
def test_single_llm_runner_does_not_use_record_scoped_skill_config(self) -> None:
    source = Path("benchmarking/runners/single_llm.py").read_text(encoding="utf-8")

    self.assertNotIn("selected_skills", source)
    self.assertNotIn("config_for_record", source)
```

Import `Path` if the file does not already import it.

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/test_benchmark_config_runtime.py tests/test_benchmark_test.py::BenchmarkRuntimeConfigTests -q
```

Expected: PASS.

- [ ] **Step 5: Commit config guards**

Run:

```bash
git add tests/test_benchmark_config_runtime.py tests/test_benchmark_test.py
git commit -m "test: guard full skill availability"
```

## Task 5: Add Post-Run Skill Use Audit

**Files:**

- Create: `benchmarking/skill_audit.py`
- Modify: `benchmarking/runners/single_llm.py`
- Create: `tests/test_benchmark_skill_audit.py`
- Modify: `tests/test_benchmark_test.py`

- [ ] **Step 1: Write failing audit tests**

Create `tests/test_benchmark_skill_audit.py`:

```python
from __future__ import annotations

from benchmarking.skill_audit import build_skill_use_audit


def test_skill_use_audit_detects_tool_calls() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("chem-calculator", "rdkit"),
        runner_meta={"toolSummary": {"calls": 2, "tools": ["write", "exec"], "failures": 0}},
        final_response_text="FINAL ANSWER: 7.59",
    )

    assert audit["skills_enabled"] is True
    assert audit["available_skill_count"] == 2
    assert audit["tool_call_count"] == 2
    assert audit["skill_tool_executed"] is True
    assert audit["model_declared_skip"] is False
    assert audit["no_tool_call"] is False


def test_skill_use_audit_detects_declared_skip_without_counting_execution() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("rdkit",),
        runner_meta={"toolSummary": {"calls": 0, "tools": [], "failures": 0}},
        final_response_text="SKILL TRACE: skipped rdkit because this is qualitative.",
    )

    assert audit["skill_tool_executed"] is False
    assert audit["model_declared_skip"] is True
    assert audit["no_tool_call"] is True


def test_skill_use_audit_handles_missing_tool_summary() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("paper-retrieval",),
        runner_meta={},
        final_response_text="FINAL ANSWER: A",
    )

    assert audit["tool_call_count"] == 0
    assert audit["tool_names"] == []
    assert audit["no_tool_call"] is True
```

- [ ] **Step 2: Run audit tests and verify failure**

Run:

```bash
uv run pytest tests/test_benchmark_skill_audit.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarking.skill_audit'`.

- [ ] **Step 3: Implement `benchmarking/skill_audit.py`**

Create `benchmarking/skill_audit.py`:

```python
from __future__ import annotations

import re
from typing import Any


SKIP_TRACE_RE = re.compile(r"\bskill\s+trace\s*:\s*skipped\b", re.IGNORECASE)


def build_skill_use_audit(
    *,
    skills_enabled: bool,
    configured_skills: tuple[str, ...] | list[str],
    runner_meta: dict[str, Any],
    final_response_text: str,
) -> dict[str, Any]:
    tool_summary = runner_meta.get("toolSummary") or {}
    calls = int(tool_summary.get("calls") or 0) if isinstance(tool_summary, dict) else 0
    raw_tools = tool_summary.get("tools") if isinstance(tool_summary, dict) else []
    tool_names = [str(item) for item in raw_tools] if isinstance(raw_tools, list) else []
    configured = [str(skill) for skill in configured_skills]
    declared_skip = bool(SKIP_TRACE_RE.search(str(final_response_text or "")))
    return {
        "skills_enabled": bool(skills_enabled),
        "available_skill_count": len(configured),
        "available_skills": configured,
        "tool_call_count": calls,
        "tool_names": tool_names,
        "tool_failure_count": int(tool_summary.get("failures") or 0) if isinstance(tool_summary, dict) else 0,
        "skill_tool_executed": bool(calls > 0),
        "model_declared_skip": declared_skip,
        "no_tool_call": bool(calls == 0),
    }
```

- [ ] **Step 4: Wire audit into `SingleLLMRunner`**

In `benchmarking/runners/single_llm.py`, import:

```python
from ..skill_audit import build_skill_use_audit
```

Add a `configured_skills` constructor parameter:

```python
configured_skills: tuple[str, ...] | list[str] = (),
```

Store it:

```python
self.configured_skills = tuple(str(skill) for skill in configured_skills)
```

After `runner_meta = dict(result_payload.get("meta") or {})`, add:

```python
runner_meta["skill_use_audit"] = build_skill_use_audit(
    skills_enabled=bool(getattr(group, "skills_enabled", True)),
    configured_skills=self.configured_skills,
    runner_meta=runner_meta,
    final_response_text=full_response_text,
)
```

In `benchmark_test.py`, pass the full allowlist when constructing `SingleLLMRunner`:

```python
configured_skills=tuple(spec.skill_allowlist or ()),
```

Use the existing local variable that holds the `ExperimentSpec` for the group.

- [ ] **Step 5: Update runner tests**

In `tests/test_benchmark_test.py`, update the fake runner construction to pass `configured_skills=("chem-calculator", "paper-retrieval")` where direct `SingleLLMRunner` construction is used. Assert `runner_meta["skill_use_audit"]["available_skill_count"] == 2` and `runner_meta["skill_use_audit"]["skill_tool_executed"]` matches the fake `toolSummary`.

- [ ] **Step 6: Run audit and runner tests**

Run:

```bash
uv run pytest tests/test_benchmark_skill_audit.py tests/test_benchmark_test.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit audit changes**

Run:

```bash
git add benchmark_test.py benchmarking/runners/single_llm.py benchmarking/skill_audit.py tests/test_benchmark_skill_audit.py tests/test_benchmark_test.py
git commit -m "feat: audit actual skill tool use"
```

## Task 6: Add Aggregate Skill-Use Metrics

**Files:**

- Modify: `benchmarking/reporting.py`
- Modify: `tests/test_benchmark_test.py`

- [ ] **Step 1: Write failing reporting test**

In `tests/test_benchmark_test.py`, update the existing aggregate reporting test that builds `GroupRecordResult` items. Add `runner_meta` examples:

```python
runner_meta={
    "skill_use_audit": {
        "skills_enabled": True,
        "tool_call_count": 2,
        "skill_tool_executed": True,
        "model_declared_skip": False,
        "no_tool_call": False,
    }
}
```

Add assertions on the group bucket:

```python
self.assertEqual(1, bucket["skill_tool_executed_count"])
self.assertEqual(1, bucket["skill_model_declared_skip_count"])
self.assertEqual(1, bucket["skill_no_tool_call_count"])
self.assertEqual(2, bucket["skill_tool_call_total"])
```

- [ ] **Step 2: Run reporting test and verify failure**

Run the exact aggregate test:

```bash
uv run pytest tests/test_benchmark_test.py::BenchmarkReportingTests -q
```

Expected: FAIL because the new aggregate keys do not exist.

- [ ] **Step 3: Add aggregate fields**

In `benchmarking/reporting.py`, add helper functions:

```python
def skill_audit(item: GroupRecordResult) -> dict[str, Any]:
    audit = (item.runner_meta or {}).get("skill_use_audit") or {}
    return audit if isinstance(audit, dict) else {}


def skill_tool_call_count(item: GroupRecordResult) -> int:
    value = skill_audit(item).get("tool_call_count")
    return int(value) if isinstance(value, (int, float)) else 0
```

Add to `aggregate_bucket()`:

```python
"skill_tool_executed_count": sum(1 for item in items if skill_audit(item).get("skill_tool_executed")),
"skill_model_declared_skip_count": sum(1 for item in items if skill_audit(item).get("model_declared_skip")),
"skill_no_tool_call_count": sum(1 for item in items if skill_audit(item).get("no_tool_call")),
"skill_tool_call_total": sum(skill_tool_call_count(item) for item in items),
```

- [ ] **Step 4: Run reporting tests**

Run:

```bash
uv run pytest tests/test_benchmark_test.py::BenchmarkReportingTests tests/test_benchmark_skill_audit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit reporting metrics**

Run:

```bash
git add benchmarking/reporting.py tests/test_benchmark_test.py
git commit -m "feat: report skill tool-use metrics"
```

## Task 7: Clean ChemQA Prompt Policy

**Files:**

- Modify: `skills/chemqa-review/prompts/contracts/proposer-main.md`
- Modify: `skills/chemqa-review/prompts/contracts/reviewer-evidence-trace.md`
- Modify: `skills/chemqa-review/prompts/contracts/reviewer-counterevidence.md`
- Modify: `skills/chemqa-review/prompts/contracts/reviewer-reasoning-consistency.md`
- Modify: `skills/chemqa-review/prompts/modules/context/required-skills.md`
- Modify: `skills/chemqa-review/tests/test_chemqa_review_runtime.py`

- [ ] **Step 1: Add text regression test**

In `skills/chemqa-review/tests/test_chemqa_review_runtime.py`, add:

```python
def test_chemqa_prompts_do_not_accept_skipped_skill_trace_as_compliance(self):
    prompt_root = SKILL_ROOT / "prompts"
    checked = [
        prompt_root / "contracts" / "proposer-main.md",
        prompt_root / "contracts" / "reviewer-evidence-trace.md",
        prompt_root / "contracts" / "reviewer-counterevidence.md",
        prompt_root / "contracts" / "reviewer-reasoning-consistency.md",
        prompt_root / "modules" / "context" / "required-skills.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    self.assertNotIn("intentionally skipped", combined)
    self.assertNotIn("not applicable after inspecting the prompt", combined)
    self.assertNotIn("status: skipped", combined)
    self.assertNotIn("valid `submission_trace` entry with `status: skipped`", combined)
```

- [ ] **Step 2: Run prompt-policy test and verify failure**

Run:

```bash
uv run pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py::ChemQAReviewRuntimeTests::test_chemqa_prompts_do_not_accept_skipped_skill_trace_as_compliance -q
```

Expected: FAIL because current prompts contain skipped-trace language.

- [ ] **Step 3: Update proposer prompt**

In `skills/chemqa-review/prompts/contracts/proposer-main.md`, replace line 41 with:

```markdown
- If a skill call fails because the tool is unavailable or returns an error, record `status: error`, the skill name, request summary, error text, and the risk to the answer. Do not treat an unexecuted skill as equivalent to a provider result.
```

Keep lines 33-40, because they correctly say triggered provider routes are execution requirements.

- [ ] **Step 4: Update reviewer prompts**

In each reviewer prompt, replace the skipped-trace acceptance clause with:

```markdown
- Treat a missing required tool trace as a blocking finding when the prompt or candidate answer depends on calculation, molecular structure, compound identity, or literature evidence and the candidate provides no provider result JSON artifact path or structured `tool_trace`.
```

Apply this to:

- `skills/chemqa-review/prompts/contracts/reviewer-evidence-trace.md`
- `skills/chemqa-review/prompts/contracts/reviewer-counterevidence.md`
- `skills/chemqa-review/prompts/contracts/reviewer-reasoning-consistency.md`

- [ ] **Step 5: Update required-skills context**

In `skills/chemqa-review/prompts/modules/context/required-skills.md`, replace routing policy lines with:

```markdown
Inventory policy:
- Benchmark chemistry skills are available from the sibling `skills/` directory.
- Use provider skills directly when they help answer a calculation, structure, identity, database, literature, spectra, materials, simulation, or workflow subproblem.
- Read full `SKILL.md` files only for skills you are about to use.
- Do not treat an unexecuted skill as a valid provider trace.
- Literature or external-fact claims that are not covered by local chemistry providers should use `paper-retrieval` -> `paper-access` -> `paper-rerank` -> `paper-parse`.
```

- [ ] **Step 6: Run ChemQA prompt-policy tests**

Run:

```bash
uv run pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py::ChemQAReviewRuntimeTests::test_chemqa_prompts_do_not_accept_skipped_skill_trace_as_compliance -q
```

Expected: PASS.

- [ ] **Step 7: Commit ChemQA policy cleanup**

Run:

```bash
git add skills/chemqa-review/prompts/contracts/proposer-main.md skills/chemqa-review/prompts/contracts/reviewer-evidence-trace.md skills/chemqa-review/prompts/contracts/reviewer-counterevidence.md skills/chemqa-review/prompts/contracts/reviewer-reasoning-consistency.md skills/chemqa-review/prompts/modules/context/required-skills.md skills/chemqa-review/tests/test_chemqa_review_runtime.py
git commit -m "fix: remove skipped skill compliance path"
```

## Task 8: Update Global Dev Spec

**Files:**

- Modify: `GLOBAL_DEV_SPEC.md`

- [ ] **Step 1: Update architecture text**

In `GLOBAL_DEV_SPEC.md`, replace the existing routing-matrix architecture bullet with text that describes inventory/discovery:

```markdown
- `chemistry-routing-matrix.json`
  - Experimental chemistry skill inventory for medium-or-higher-value chemistry capabilities. Despite the historical filename, runtime benchmark prompts treat this as a skill inventory, not as a deterministic router.
  - Single-agent skills-on runs expose the full benchmark skill allowlist to the model. Prompts include a lightweight discovery guide and rely on the model to choose and call relevant skills when they help answer the record.
  - Post-run reporting records actual tool-use audit metadata such as tool-call counts, model-declared skipped traces, and no-tool-call outcomes. Skipped traces are diagnostic only and do not count as executed skill use.
```

- [ ] **Step 2: Update feature matrix**

Add or update a feature entry:

```markdown
- Name: Autonomous benchmark skill discovery and audit
  - Description: Skills-on benchmark runs keep the full benchmark skill allowlist available for each single-LLM record, use a compact discovery prompt instead of deterministic selected-skill routing, and report post-run skill-use audit counters from actual tool execution metadata.
  - Input / Output:
    - Input: benchmark record prompt plus full configured benchmark skill allowlist.
    - Output: normal benchmark answer plus `runner_meta.skill_use_audit` and aggregate skill tool-use counters.
  - Implementation location: `workspace/benchmarking/skill_inventory.py`, `workspace/benchmarking/skill_audit.py`, `workspace/benchmarking/prompts.py`, `workspace/benchmarking/reporting.py`
  - Status: `DONE`
```

- [ ] **Step 3: Remove stale router references**

Run:

```bash
rg -n "chemistry_routing|selected skill route|first matching primary route|skipped triggered route|record-scoped route" GLOBAL_DEV_SPEC.md
```

Expected after edits: no matches.

- [ ] **Step 4: Commit spec update**

Run:

```bash
git add GLOBAL_DEV_SPEC.md
git commit -m "docs: document autonomous skill discovery"
```

## Task 9: Final Verification And Benchmark Smoke

**Files:**

- No source edits unless a verification failure reveals a task-specific bug.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
uv run pytest tests/test_benchmark_prompts.py tests/test_benchmark_config_runtime.py tests/test_benchmark_skill_inventory.py tests/test_benchmark_skill_audit.py tests/test_experimental_chemistry_skill_matrix.py tests/test_benchmark_test.py -q
```

Expected: PASS.

- [ ] **Step 2: Run ChemQA-focused tests touched by prompt/policy changes**

Run:

```bash
uv run pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py tests/test_chemqa_artifact_flow.py -q
```

Expected: PASS.

- [ ] **Step 3: Generate one prompt preview**

Run:

```bash
uv run python - <<'PY'
from benchmarking.datasets import BenchmarkRecord
from benchmarking.prompts import build_single_llm_prompt

record = BenchmarkRecord(
    record_id="demo",
    dataset="frontierscience",
    source_file="/tmp/demo.jsonl",
    eval_kind="frontierscience_olympiad",
    prompt="Calculate the mass of dissolved Sr2+ from Ksp and concentrations.",
    reference_answer="7.59",
    payload={"track": "olympiad"},
)
prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)
print(prompt.split("QUESTION:", 1)[0])
PY
```

Expected output contains `Available chemistry skills are loaded for this run.` and does not contain `Experimental chemistry skill routing rules`, `first matching primary route`, or `SKILL TRACE: skipped`.

- [ ] **Step 4: Render one runtime config preview**

Run:

```bash
uv run python - <<'PY'
import benchmark_test

skills = benchmark_test.BENCHMARK_SKILLS_ALLOWLIST
print(len(skills))
print("paper-retrieval" in skills, "paper-access" in skills, "paper-parse" in skills, "paper-rerank" in skills)
PY
```

Expected output has a count of at least `80`, followed by `True True True True`.

- [ ] **Step 5: Inspect final diff for router residue**

Run:

```bash
rg -n "selected_skills|config_for_record|SkillPlan|preexecute_skill_plan|SKILL TRACE: skipped|first matching primary route|Use ordinary reasoning and avoid loading unrelated skills" .
```

Expected: no matches in source, tests, prompts, or docs except historical benchmark artifacts outside the tracked source tree. If matches appear in tracked implementation files, remove them before completion.

- [ ] **Step 6: Commit any verification fixes**

If Step 5 required edits, commit them:

```bash
git add .
git commit -m "fix: remove routing residue"
```

If no edits were required, do not create an empty commit.

## Task 10: Optional Old Branch Deletion

**Files:**

- No source files.

- [ ] **Step 1: Confirm current branch is not the old branch**

Run:

```bash
git -C /Users/xutao/.config/superpowers/worktrees/workspace/skill-autonomous-discovery-audit branch --show-current
```

Expected: `skill-autonomous-discovery-audit`.

- [ ] **Step 2: Confirm old branch has no local modifications**

Run:

```bash
git -C /Users/xutao/.config/superpowers/worktrees/workspace/skill-injection-routing-repair status --short
```

Expected: no output.

- [ ] **Step 3: Archive old branch head**

Run:

```bash
git -C /Users/xutao/.openclaw/workspace tag archive/skill-injection-routing-repair caa3e60
```

Expected: command succeeds. If the tag already exists at `caa3e60`, keep it and continue.

- [ ] **Step 4: Remove old worktree**

Run:

```bash
git -C /Users/xutao/.openclaw/workspace worktree remove /Users/xutao/.config/superpowers/worktrees/workspace/skill-injection-routing-repair
```

Expected: worktree removed.

- [ ] **Step 5: Delete old branch ref**

Run:

```bash
git -C /Users/xutao/.openclaw/workspace branch -D skill-injection-routing-repair
```

Expected: branch deleted. The archive tag remains as the recovery point.

