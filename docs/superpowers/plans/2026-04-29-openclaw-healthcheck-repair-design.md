# OpenClaw Healthcheck Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本次全仓库体检的四个发现收敛为一条低风险修复路线：先恢复测试可复现性，再澄清 ChemQA 真实控制面，再把 provider skill 使用从 prompt 约束升级为可审计运行时合同，最后逐步瘦身 `benchmark_test.py`。

**Architecture:** 保持当前可运行主路径不变：ChemQA 继续由 `debate_state.py` + `chemqa_review_openclaw_driver.py` + Artifact Flow 控制，不在第一阶段切换到 skeletal native workflow。所有高风险改动先以兼容 wrapper、audit mode、明确 metadata 状态落地，避免 flag-day rewrite。

**Tech Stack:** Python 3.12, pytest, stdlib subprocess/json/dataclasses, existing `benchmarking/*`, existing ChemQA/DebateClaw script runtime.

---

## Design Decisions

### 1. PubChem Test Reproducibility

**Decision:** 修测试，不先改 PubChem runtime 行为。

当前失败是测试进程使用系统 `python3`，而不是 `.venv` 解释器。`requests` 在 `.venv` 中可用，系统 Python 3.14 中不可用。第一步应把测试里的 subprocess 命令改成 `sys.executable`，让子进程继承 pytest 当前解释器。

**Non-goal:** 不在这一阶段给所有 PubChem 脚本实现 missing dependency structured error。`requests` 是项目基础依赖，先恢复测试入口一致性。

### 2. ChemQA Native Workflow Control Plane

**Decision:** 不把 `runtime/workflow.py` 立即实现为真实控制面；先将它降级为明确的 scaffold / inactive package。

真实控制面目前是：

- `skills/debateclaw-v1/scripts/debate_state.py`: 协议状态机和 SQLite source of truth
- `skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`: role loop、OpenClaw turn、artifact 生产/修复、run-status 发布
- `skills/chemqa-review/scripts/chemqa_artifact_flow.py`: 终态 artifact 和 `qa_result.json`

`compile_runplan.py` 不应把 `workflow_package` 放在看起来会被运行时加载的位置。保留 scaffold 元数据可以，但必须显式标为 inactive/deprecated，避免后续开发把协议逻辑写进不会执行的类。

### 3. Provider Skill Usage Enforcement

**Decision:** 先做 runtime audit，再对高置信触发器开启 enforcement。

prompt 已要求 ChemQA 使用 `chem-calculator`、`rdkit`、`opsin`、`pubchem`，但 artifact validator 只检查基本 YAML 形状。应新增一层 provider trace policy：

- 根据 `answer_kind`、`eval_kind`、dataset、prompt text、candidate trace 判断是否触发 provider skill 要求。
- 接受两类证据：
  - `submission_trace` / `claim_anchors` 中的 structured `tool_trace`
  - 可解析、存在的 provider JSON artifact 路径
- 对缺失证据先写入 validation warning / run-status diagnostics。
- 对高置信触发器再切换为 validation error，驱动现有 repair loop。

**Important contract cleanup:** prompt 目前倾向要求 literal `result.json`，但 PubChem contracts 使用 `name_to_cid_result.json` 等脚本特定文件。provider trace validator 应接受任意稳定 JSON result path，并在 prompt 中改成 “provider result JSON artifact”，避免合同不一致。

### 4. Benchmark Entrypoint Slimming

**Decision:** 沿现有 `benchmarking/` 包继续迁移，不做一次性大重写。

`benchmark_test.py` 保持 CLI 入口和兼容 re-export；新逻辑下沉到 focused modules。测试先覆盖新模块，再保留旧函数 wrapper，最后按阶段删除过时 wrapper。

---

## Target File Structure

### Modify

- `skills/pubchem/tests/test_pubchem_skill.py`
  - Replace hardcoded `python3` subprocess launcher with `sys.executable`.

- `skills/chemqa-review/scripts/compile_runplan.py`
  - Move active-looking `workflow_package` out of the live runtime binding.
  - Add explicit `workflow_package_scaffold` metadata with `active: false`.
  - Add `control_plane: "debate_state_driver"` under `runtime_context.chemqa_review`.

- `skills/chemqa-review/workflows/chemqa-review@1.json`
  - Mark `workflow_package.status` as `scaffold` or `inactive`.
  - Document that live execution does not load it.

- `skills/chemqa-review/scripts/materialize_runplan.py`
  - Replace wording `ChemQA native workflow` with `ChemQA fixed-lane review protocol` to avoid implying `runtime/workflow.py` is active.

- `skills/chemqa-review/scripts/chemqa_review_artifacts.py`
  - Integrate provider trace validation into candidate checks without changing existing basic artifact normalization.

- `skills/chemqa-review/prompts/contracts/proposer-main.md`
- `skills/chemqa-review/prompts/modules/context/required-skills.md`
  - Align prompt text with the runtime validator.
  - Replace literal `result.json` wording with provider result JSON artifact path where appropriate.

- `benchmark_test.py`
  - Keep compatibility wrappers.
  - Gradually delegate config, prompt, result-axis, and evaluator code to `benchmarking/*`.

- `GLOBAL_DEV_SPEC.md`
  - Update only when a phase changes actual behavior or architecture status.

### Create

- `skills/chemqa-review/scripts/provider_trace_policy.py`
  - Pure helper module for trace normalization, trigger detection, and validation.

- `tests` / existing test files
  - Add targeted tests near the owning module:
    - PubChem test interpreter inheritance
    - inactive workflow package metadata
    - provider trace audit/enforcement
    - benchmark module wrappers still preserve public behavior

Optional later modules for benchmark slimming:

- `benchmarking/status.py`
  - ChemQA run-status normalization and result-axis derivation.

- `benchmarking/prompts.py`
  - `build_single_llm_prompt`, `build_chemqa_goal`, answer-kind prompt hints.

- `benchmarking/evaluators.py` or `benchmarking/evaluators/*`
  - Benchmark evaluator implementations currently inside `benchmark_test.py`.

- `benchmarking/runtime_config.py`
  - `ConfigPool` and group/judge config path orchestration.

---

## Phase 1: Restore Test Reproducibility

**Files:**
- Modify: `skills/pubchem/tests/test_pubchem_skill.py`

- [ ] **Step 1: Write the minimal failing assertion**

Confirm current behavior:

```bash
.venv/bin/python -m pytest skills/pubchem/tests/test_pubchem_skill.py::test_cli_writes_structured_error_on_invalid_request -q
```

Expected before fix: six failures with `ModuleNotFoundError: No module named 'requests'` from system `python3`.

- [ ] **Step 2: Use the active test interpreter**

Change:

```python
import subprocess
```

to:

```python
import subprocess
import sys
```

Change subprocess command from:

```python
["python3", str(script_path), "--request-json", str(request_path), "--output-dir", str(output_dir), "--json"]
```

to:

```python
[
    sys.executable,
    str(script_path),
    "--request-json",
    str(request_path),
    "--output-dir",
    str(output_dir),
    "--json",
]
```

- [ ] **Step 3: Verify focused tests**

Run:

```bash
.venv/bin/python -m pytest skills/pubchem/tests/test_pubchem_skill.py -q
```

Expected: all PubChem tests pass.

- [ ] **Step 4: Verify full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: no PubChem interpreter failures.

- [ ] **Step 5: Commit**

```bash
git add skills/pubchem/tests/test_pubchem_skill.py
git commit -m "test: run pubchem cli tests with active interpreter"
```

---

## Phase 2: Clarify ChemQA Control Plane

**Files:**
- Modify: `skills/chemqa-review/scripts/compile_runplan.py`
- Modify: `skills/chemqa-review/workflows/chemqa-review@1.json`
- Modify: `skills/chemqa-review/scripts/materialize_runplan.py`
- Modify: `skills/chemqa-review/tests/test_chemqa_review_runtime.py`
- Modify: `GLOBAL_DEV_SPEC.md`

- [ ] **Step 1: Add tests for inactive workflow package metadata**

Add a test that compiles a dry-run plan and asserts:

```python
runtime_context = plan["runtime_context"]
self.assertNotIn("workflow_package", runtime_context)
self.assertEqual("debate_state_driver", runtime_context["chemqa_review"]["control_plane"])
self.assertFalse(runtime_context["chemqa_review"]["workflow_package_scaffold"]["active"])
self.assertEqual("ChemQAWorkflow", runtime_context["chemqa_review"]["workflow_package_scaffold"]["class"])
```

- [ ] **Step 2: Update `compile_runplan.py` runtime context**

Replace the active-looking block:

```python
"workflow_package": {
    "kind": "python-path",
    "path": str((root / "runtime" / "workflow.py").resolve()),
    "class": "ChemQAWorkflow"
},
```

with metadata under `chemqa_review`:

```python
"control_plane": "debate_state_driver",
"workflow_package_scaffold": {
    "active": False,
    "status": "scaffold",
    "kind": "python-path",
    "path": str((root / "runtime" / "workflow.py").resolve()),
    "class": "ChemQAWorkflow",
    "reason": "Live ChemQA execution uses debate_state.py plus chemqa_review_openclaw_driver.py.",
},
```

- [ ] **Step 3: Mark workflow JSON as scaffold**

Add explicit metadata to `skills/chemqa-review/workflows/chemqa-review@1.json`:

```json
"workflow_package": {
  "kind": "python-path",
  "path": "runtime/workflow.py",
  "class": "ChemQAWorkflow",
  "status": "scaffold",
  "active": false,
  "live_control_plane": "debate_state_driver"
}
```

- [ ] **Step 4: Fix prompt wording**

In `materialize_runplan.py`, replace:

```python
"This run uses the ChemQA native workflow: only `proposer-1` is the candidate owner.",
```

with:

```python
"This run uses the ChemQA fixed-lane review protocol: only `proposer-1` is the candidate owner.",
```

- [ ] **Step 5: Update docs**

Update `GLOBAL_DEV_SPEC.md` to say:

- Native workflow package is scaffold/inactive metadata.
- Live control plane is explicitly `debate_state_driver`.
- Run plans no longer advertise the scaffold as an active workflow package.

- [ ] **Step 6: Verify**

Run:

```bash
.venv/bin/python -m pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py -q
.venv/bin/python skills/chemqa-review/scripts/compile_runplan.py \
  --root skills/chemqa-review \
  --preset chemqa-review@1 \
  --goal "Question: control-plane metadata smoke test" \
  --run-id control-plane-smoke \
  --dry-run \
  --json
```

Expected:

- Tests pass.
- Dry-run JSON contains `chemqa_review.control_plane = "debate_state_driver"`.
- Dry-run JSON does not contain active top-level `runtime_context.workflow_package`.

- [ ] **Step 7: Commit**

```bash
git add \
  skills/chemqa-review/scripts/compile_runplan.py \
  skills/chemqa-review/workflows/chemqa-review@1.json \
  skills/chemqa-review/scripts/materialize_runplan.py \
  skills/chemqa-review/tests/test_chemqa_review_runtime.py \
  GLOBAL_DEV_SPEC.md
git commit -m "refactor: mark chemqa workflow package as inactive scaffold"
```

---

## Phase 3: Add Provider Trace Policy in Audit Mode

**Files:**
- Create: `skills/chemqa-review/scripts/provider_trace_policy.py`
- Modify: `skills/chemqa-review/scripts/chemqa_review_artifacts.py`
- Modify: `skills/chemqa-review/tests/test_chemqa_review_runtime.py`
- Modify: `skills/chemqa-review/prompts/contracts/proposer-main.md`
- Modify: `skills/chemqa-review/prompts/modules/context/required-skills.md`
- Modify: `GLOBAL_DEV_SPEC.md`

- [ ] **Step 1: Add provider trace policy tests**

Add tests covering these cases:

```python
def test_numeric_answer_kind_audits_missing_chem_calculator_trace(self) -> None:
    candidate = """
artifact_kind: candidate_submission
phase: propose
owner: proposer-1
direct_answer: "42"
summary: numeric answer
submission_trace:
  - step: reasoning
    status: success
    detail: mental arithmetic only
"""
    checked = transport.check_candidate_submission(
        candidate,
        owner="proposer-1",
        answer_kind="numeric_short_answer",
        provider_trace_mode="audit",
    )
    self.assertTrue(checked.ok)
    self.assertTrue(any("chem-calculator" in warning for warning in checked.warnings))
```

```python
def test_numeric_answer_kind_enforces_valid_chem_calculator_trace(self) -> None:
    candidate = """
artifact_kind: candidate_submission
phase: propose
owner: proposer-1
direct_answer: "42"
summary: numeric answer
submission_trace:
  - step: chem-calculator
    status: success
    skill: chem-calculator
    result_path: /tmp/chemcalc/result.json
    detail: computed the requested value
"""
    checked = transport.check_candidate_submission(
        candidate,
        owner="proposer-1",
        answer_kind="numeric_short_answer",
        provider_trace_mode="enforce",
        require_existing_provider_paths=False,
    )
    self.assertTrue(checked.ok)
```

```python
def test_triggered_skill_skip_requires_reason_and_risk(self) -> None:
    candidate = """
artifact_kind: candidate_submission
phase: propose
owner: proposer-1
direct_answer: "42"
summary: numeric answer
submission_trace:
  - step: chem-calculator
    status: skipped
    trigger: numeric_short_answer
    reason: calculator unsupported for this symbolic prompt
    risk: arithmetic was checked manually only
"""
    checked = transport.check_candidate_submission(
        candidate,
        owner="proposer-1",
        answer_kind="numeric_short_answer",
        provider_trace_mode="enforce",
    )
    self.assertTrue(checked.ok)
```

- [ ] **Step 2: Implement `provider_trace_policy.py`**

Core API:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderTraceRequirement:
    skill: str
    trigger: str
    reason: str


@dataclass(frozen=True)
class ProviderTraceValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def requirements_for_candidate(*, answer_kind: str, prompt: str = "", eval_kind: str = "", dataset: str = "") -> list[ProviderTraceRequirement]:
    text = " ".join([answer_kind, eval_kind, dataset, prompt]).lower()
    requirements: list[ProviderTraceRequirement] = []
    if answer_kind == "numeric_short_answer" or any(token in text for token in ("stoichiometric", "equilibrium", "acid-base", "gas-law", "unit-conversion")):
        requirements.append(ProviderTraceRequirement("chem-calculator", "numeric_or_formula_math", "Numeric or formula-math answer requires deterministic calculation trace."))
    if any(token in text for token in ("smiles", "inchi", "stereochemistry", "substructure", "conformer", "ring count")):
        requirements.append(ProviderTraceRequirement("rdkit", "structure_check", "Structure-sensitive answer requires deterministic RDKit trace."))
    if any(token in text for token in ("iupac", "systematic name")):
        requirements.append(ProviderTraceRequirement("opsin", "systematic_name", "Systematic-name answer requires OPSIN trace."))
        requirements.append(ProviderTraceRequirement("rdkit", "opsin_structure_validation", "OPSIN-derived structures require RDKit validation trace."))
    if any(token in text for token in ("pubchem", "cid", "synonym", "common name")):
        requirements.append(ProviderTraceRequirement("pubchem", "public_compound_lookup", "Public compound identity/property answer requires PubChem trace."))
    return requirements
```

Validation should:

- scan `submission_trace` entries
- scan `claim_anchors[*].tool_trace` when present
- accept `skill`, `tool`, or `provider` matching the required skill
- accept successful provider traces with `status in {"success", "partial"}`
- accept skipped traces only if `trigger`, `reason`, and `risk` are non-empty
- optionally check that `result_path` exists when `require_existing_provider_paths=True`

- [ ] **Step 3: Integrate audit mode into candidate validation**

Extend `check_candidate_submission` signature:

```python
def check_candidate_submission(
    text: str,
    *,
    owner: str = CANDIDATE_OWNER,
    answer_kind: str = "",
    prompt: str = "",
    eval_kind: str = "",
    dataset: str = "",
    provider_trace_mode: str = "audit",
    require_existing_provider_paths: bool = False,
) -> ArtifactCheck:
```

Behavior:

- `provider_trace_mode == "off"`: no provider checks
- `provider_trace_mode == "audit"`: append provider validation errors as warnings
- `provider_trace_mode == "enforce"`: append provider validation errors as errors

Default should be `audit` for this phase.

- [ ] **Step 4: Align prompt wording**

Replace overly narrow text:

```text
cite the generated script `result.json` path
```

with:

```text
cite the generated provider result JSON artifact path
```

Keep `tool_trace` examples, but make the accepted keys match the validator:

```yaml
submission_trace:
  - step: chem-calculator
    status: success
    skill: chem-calculator
    result_path: /path/to/provider-result.json
    detail: Computed the numeric subproblem deterministically.
```

- [ ] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py -q
.venv/bin/python -m pytest skills/chemqa-review/tests/test_chemqa_artifact_flow.py -q
```

Expected: provider trace tests pass; existing artifact-flow tests still pass.

- [ ] **Step 6: Commit**

```bash
git add \
  skills/chemqa-review/scripts/provider_trace_policy.py \
  skills/chemqa-review/scripts/chemqa_review_artifacts.py \
  skills/chemqa-review/tests/test_chemqa_review_runtime.py \
  skills/chemqa-review/prompts/contracts/proposer-main.md \
  skills/chemqa-review/prompts/modules/context/required-skills.md \
  GLOBAL_DEV_SPEC.md
git commit -m "feat: audit chemqa provider skill traces"
```

---

## Phase 4: Enforce Provider Traces for High-Confidence Triggers

**Files:**
- Modify: `skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
- Modify: `skills/chemqa-review/scripts/chemqa_review_artifacts.py`
- Modify: `skills/chemqa-review/tests/test_chemqa_review_runtime.py`
- Modify: `GLOBAL_DEV_SPEC.md`

- [ ] **Step 1: Add driver test for enforced provider trace repair**

Test that when the driver validates a numeric candidate in enforce mode and no provider trace exists:

- the artifact is invalid
- role-phase diagnostic classification is `repairing_invalid_artifact`
- feedback mentions `chem-calculator`

- [ ] **Step 2: Add runtime mode resolution**

In driver runtime config, resolve mode from:

1. runplan `runtime_context.chemqa_review.provider_trace_mode`
2. env `CHEMQA_PROVIDER_TRACE_MODE`
3. default `"audit"` for one release cycle

Allowed values:

```python
{"off", "audit", "enforce"}
```

- [ ] **Step 3: Enable enforcement only for deterministic high-confidence answer kinds**

Start with:

- `numeric_short_answer` -> `chem-calculator`
- explicit prompt tokens `SMILES`, `InChI`, `stereochemistry`, `substructure`, `conformer` -> `rdkit`

Do not enforce PubChem/OPSIN globally in the first enforcement phase because public lookup and name classification can be ambiguous. Keep those in audit until false-positive rates are measured.

- [ ] **Step 4: Surface diagnostics**

Add run-status fields:

```json
"provider_trace_policy": {
  "mode": "audit|enforce|off",
  "requirements": [...],
  "warnings": [...],
  "errors": [...]
}
```

This makes benchmark failures explainable without reading transcripts.

- [ ] **Step 5: Verify**

Run:

```bash
.venv/bin/python -m pytest skills/chemqa-review/tests/test_chemqa_review_runtime.py -q
.venv/bin/python -m pytest tests/test_benchmark_test.py -q
```

Run dry-run compile smoke:

```bash
.venv/bin/python skills/chemqa-review/scripts/compile_runplan.py \
  --root skills/chemqa-review \
  --preset chemqa-review@1 \
  --goal "Question: compute the molar mass of H2O" \
  --answer-kind numeric_short_answer \
  --run-id provider-trace-smoke \
  --dry-run \
  --json
```

- [ ] **Step 6: Commit**

```bash
git add \
  skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py \
  skills/chemqa-review/scripts/chemqa_review_artifacts.py \
  skills/chemqa-review/tests/test_chemqa_review_runtime.py \
  GLOBAL_DEV_SPEC.md
git commit -m "feat: enforce deterministic chemqa provider traces"
```

---

## Phase 5: Slim `benchmark_test.py` Without Breaking Public Imports

**Files:**
- Create: `benchmarking/status.py`
- Create: `benchmarking/prompts.py`
- Create: `benchmarking/runtime_config.py`
- Optional create: `benchmarking/evaluators.py`
- Modify: `benchmark_test.py`
- Modify: benchmark tests under `tests/`
- Modify: `GLOBAL_DEV_SPEC.md`

### Task 5A: Move ChemQA Status Normalization

- [ ] **Step 1: Add tests for new module**

Move existing status tests from `tests/test_benchmark_test.py` into new focused tests while keeping old wrapper assertions.

New imports:

```python
from benchmarking.status import normalize_chemqa_run_status, is_chemqa_terminal_status, is_chemqa_success_status
```

- [ ] **Step 2: Create `benchmarking/status.py`**

Move:

- `normalize_chemqa_run_status`
- `is_chemqa_terminal_status`
- `is_chemqa_success_status`
- `normalize_run_status_value`
- `build_result_axes_from_runner`

Keep thin wrappers in `benchmark_test.py`:

```python
from benchmarking.status import normalize_chemqa_run_status as normalize_chemqa_run_status
```

- [ ] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_test.py tests/test_benchmark_contracts.py -q
```

### Task 5B: Move Prompt Builders

- [ ] **Step 1: Create `benchmarking/prompts.py`**

Move:

- `build_single_llm_prompt`
- `build_chemqa_goal`
- `resolve_chemqa_answer_kind` if needed by ChemQA prompt construction

Keep benchmark CLI behavior identical.

- [ ] **Step 2: Verify prompt tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_test.py::BenchmarkHelpersTest -q
```

### Task 5C: Move ConfigPool

- [ ] **Step 1: Create `benchmarking/runtime_config.py`**

Move:

- `ConfigPool`
- `build_run_scoped_config_payload`
- slot helper dependencies that are config/provisioning specific

Avoid moving CLI parsing, evaluator code, or runner code in the same commit.

- [ ] **Step 2: Verify config tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_config_runtime.py tests/test_benchmark_test.py -q
```

### Task 5D: Move Evaluators

- [ ] **Step 1: Create evaluator module**

Move benchmark-specific evaluator functions from `benchmark_test.py` into:

```text
benchmarking/evaluators.py
```

or split later into:

```text
benchmarking/evaluators/chembench.py
benchmarking/evaluators/frontierscience.py
benchmarking/evaluators/superchem.py
benchmarking/evaluators/conformabench.py
```

Start with one file to reduce migration risk.

- [ ] **Step 2: Preserve registry behavior**

Keep registration in one explicit function:

```python
def register_default_evaluators() -> None:
    register_evaluator("chembench_open_ended", evaluate_chembench_open_ended)
    register_evaluator("conformabench_constructive", evaluate_conformabench_constructive)
    register_evaluator("frontierscience_olympiad", evaluate_frontierscience_olympiad)
    register_evaluator("frontierscience_research", evaluate_frontierscience_research)
    register_evaluator("superchem_multiple_choice_rpf", evaluate_superchem_multiple_choice_rpf)
    register_evaluator("generic_semantic", evaluate_generic_semantic)
```

Call this from `benchmark_test.py` at import time for compatibility.

- [ ] **Step 3: Verify evaluator tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_benchmark_test.py -q
```

- [ ] **Step 4: Commit each subtask separately**

Use one commit per moved responsibility:

```bash
git commit -m "refactor: move benchmark status helpers"
git commit -m "refactor: move benchmark prompt builders"
git commit -m "refactor: move benchmark runtime config orchestration"
git commit -m "refactor: move benchmark evaluator implementations"
```

---

## Phase 6: Remove Misleading Legacy Surface Area

Only start this phase after Phases 1-5 are green.

**Targets:**

- Remove or reduce compatibility wrappers in `benchmark_test.py` once tests import new modules directly.
- Drop `web-ui` extras from `pyproject.toml` unless a real server/UI is implemented.
- Decide whether `workflow_loader.py` should become active or be documented as future/deprecated.
- Audit ChemQA prompts for duplicated policy text after provider trace enforcement is live.

**Verification:**

```bash
.venv/bin/python -m pytest -q
```

**Commit:**

```bash
git add benchmark_test.py pyproject.toml GLOBAL_DEV_SPEC.md
git commit -m "chore: remove misleading inactive surfaces"
```

---

## Risk Controls

- Keep `provider_trace_mode=audit` before enforcement.
- Preserve `benchmark_test.py` public function names until tests move.
- Keep ChemQA live control plane as `debate_state_driver`; do not switch to `runtime/workflow.py` in this repair pass.
- Update `GLOBAL_DEV_SPEC.md` only when runtime behavior or architecture status changes.
- Commit after each independently testable phase.

---

## Acceptance Criteria

The repair is complete when:

1. `.venv/bin/python -m pytest -q` passes.
2. Dry-run ChemQA compile output clearly marks `debate_state_driver` as live control plane.
3. Run plans no longer present `ChemQAWorkflow` as an active runtime package.
4. ChemQA candidate validation emits provider trace diagnostics in audit mode.
5. High-confidence numeric/structure tasks can enforce deterministic provider trace evidence.
6. `benchmark_test.py` is reduced to CLI orchestration plus compatibility exports, with core logic living in `benchmarking/*`.
7. `GLOBAL_DEV_SPEC.md` reflects the actual system after each behavior-changing phase.

---

## Recommended Execution Order

1. Phase 1: Fix tests first, because full-suite signal is currently noisy.
2. Phase 2: Remove native workflow ambiguity before adding new ChemQA contracts.
3. Phase 3: Add provider trace audit mode without breaking runs.
4. Phase 4: Enforce only high-confidence deterministic triggers.
5. Phase 5: Refactor benchmark entrypoint in small, reversible commits.
6. Phase 6: Remove misleading optional surfaces only after tests and docs are stable.
