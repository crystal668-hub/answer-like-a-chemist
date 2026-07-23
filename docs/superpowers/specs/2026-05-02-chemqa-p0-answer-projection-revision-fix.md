# ChemQA P0 Answer Projection and Revision Propagation Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the P0 ChemQA failure mode where the transcript or long answer contains the correct result, but the benchmark-visible final answer uses a stale or malformed short-answer projection.

**Architecture:** Keep benchmark scoring simple and fix the canonical Artifact Flow boundary. `chemqa_artifact_flow.py` must produce a trustworthy `FinalAnswerArtifact.evaluator_answer`, including answer-kind-aware projection repair and latest valid `answer_revision` propagation. `benchmark_test.py` should continue consuming the final artifact rather than independently mining long answers.

**Tech Stack:** Python 3.12, PyYAML, pytest, ChemQA Artifact Flow scripts, benchmark runner compatibility layer

---

## 中文摘要

2026-04-29 的 ChemQA run 显示一个 P0 级别结果投影问题：模型推理和长答案中已经包含正确答案，但最终 `final_answer_artifact.json` 的 `evaluator_answer` 没有拿到该结果，导致 benchmark judge 比较了错误的短答案。

最典型样例是 SrF2 溶解度题：

- `summary/full_answer` 中包含正确结果 `7.59 μg`。
- `direct_answer/evaluator_answer` 却是题目背景句 `After mixing 100 mL ... total volume is 200 mL.`。
- `numeric_short_answer` 校验只检查是否包含数字，因此背景句中的 `100`、`0.250` 等数字让错误短答案通过。
- benchmark 读取 `final_answer_artifact.evaluator_answer` 作为 `short_answer_text`，不会从 `full_answer` 抢救 `7.59 μg`。

第二个相关样例是 NaCl/KCl 题：

- protocol 的 proposer rebuttal 中存在 `mode: answer_revision` 和 `updated_answer.evaluator_answer`。
- 最终 `candidate_view/final_answer_artifact` 仍保留初始候选答案。
- 根因是 `finalization_from_protocol()` 构建 candidate view 时传入空的 `review_artifacts=[]`、`rebuttal_artifacts=[]`，没有应用 protocol 中的 rebuttal revision。

本计划只解决 P0：最终可评估答案必须正确投影，且 `answer_revision` 必须传播到最终 artifact。P1 的 review gating、协议过重、skills 触发规则收窄不在本计划范围内。

## Scope

### In Scope

- Strengthen `numeric_short_answer` projection so process/setup sentences do not pass as evaluator answers.
- Add deterministic rescue from `summary/full_answer` into `evaluator_answer` when the short field is invalid but the long answer has a clear final-answer phrase.
- Extract and apply valid `answer_revision` rebuttals from `protocol.proposer_trajectory`.
- Ensure `final_answer_artifact.json`, `qa_result.json`, `candidate_view.json`, and benchmark `short_answer_text` reflect the latest valid projected answer.
- Add regression tests for the SrF2 and NaCl/KCl failure modes.

### Out of Scope

- Do not change benchmark judge scoring rules.
- Do not implement P1 review-item blocking semantics.
- Do not redesign role prompts or reviewer policy.
- Do not change chemistry skill trigger rules.
- Do not change acceptance status semantics except where finalization must use the corrected candidate view.
- Do not introduce an LLM-based answer extractor.

## Root Cause Summary

### Root Cause 1: Numeric Projection Accepts Any Text With Any Number

Current code path:

- `validate_candidate_artifact()` chooses `evaluator_answer` from `evaluator_answer`, `direct_answer`, `answer`, `value`, `final_answer`.
- `_validate_answer_projection("numeric_short_answer", ...)` calls `_numeric_value(answer)`.
- `_numeric_value()` returns the first number in the string.

This allows an answer like:

```text
After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.
```

to pass numeric projection even though the benchmark answer should be:

```text
7.59 μg
```

### Root Cause 2: Summary Has Correct Answer But Is Not Used for Short Projection

Current code stores:

- `evaluator_answer`: benchmark-visible short answer
- `display_answer`: human display short answer
- `full_answer`: long explanation or summary

The long answer may contain a clear final answer phrase, but finalization copies only `evaluator_answer` into:

```json
{
  "final_answer": {
    "direct_answer": "...",
    "answer": "...",
    "value": "..."
  }
}
```

Benchmark extraction then reads `final_answer_artifact.evaluator_answer`; it does not parse `full_answer`.

### Root Cause 3: Protocol Rebuttal Revisions Are Not Applied

`build_current_candidate_view()` already knows how to apply rebuttal artifacts:

```python
if mode == "answer_revision" and isinstance(rebuttal_payload.get("updated_answer"), dict):
    for key in ("evaluator_answer", "display_answer", "full_answer"):
        if clean_text(updated.get(key)):
            payload[key] = clean_text(updated[key])
```

But `finalization_from_protocol()` calls it with empty lists:

```python
candidate_state = build_current_candidate_view(
    candidate_artifact=candidate.artifact,
    review_artifacts=[],
    rebuttal_artifacts=[],
)
```

So any `protocol.proposer_trajectory[*].rebuttals[*].payload.updated_answer` is ignored during finalization.

## Target Behavior

### SrF2 Numeric Short Answer

Input candidate:

```yaml
direct_answer: After mixing 100 mL ... total volume is 200 mL.
summary: ... Mass of dissolved Sr²⁺ = ... = 7.59 μg.
answer_kind: numeric_short_answer
```

Expected final artifact:

```json
{
  "answer_kind": "numeric_short_answer",
  "evaluator_answer": "7.59 μg",
  "display_answer": "7.59 μg",
  "full_answer": "... Mass of dissolved Sr²⁺ = ... = 7.59 μg."
}
```

Expected benchmark record:

```json
{
  "short_answer_text": "7.59 μg",
  "answer_text": "...7.59 μg..."
}
```

### NaCl/KCl Answer Revision Propagation

Input protocol has initial candidate:

```yaml
candidate_submission:
  direct_answer: The mass gain of the Cu strip...
```

and rebuttal:

```yaml
proposer_trajectory:
  rebuttals:
    - payload:
        mode: answer_revision
        updated_answer:
          evaluator_answer: "NaCl: 69.9%, KCl: 30.1%"
          display_answer: "NaCl 69.9%, KCl 30.1% by mass"
          full_answer: "The mixture contains 17.48 g NaCl..."
```

Expected final artifact:

```json
{
  "evaluator_answer": "NaCl: 69.9%, KCl: 30.1%",
  "display_answer": "NaCl 69.9%, KCl 30.1% by mass",
  "full_answer": "The mixture contains 17.48 g NaCl..."
}
```

This test verifies propagation only. It does not assert that the chemistry answer should score full credit.

## Design

### Projection Function

Add a single answer projection function in `chemqa_artifact_flow.py`:

```python
def project_candidate_answer(payload: dict[str, Any], *, answer_kind: str) -> tuple[dict[str, Any], list[str]]:
    ...
```

Responsibilities:

- Read raw short fields using current precedence.
- Read full fields using current precedence.
- Validate the raw short answer against answer-kind-specific rules.
- For `numeric_short_answer`, if raw short answer is invalid or non-answer-like, try deterministic extraction from `full_answer` or `summary`.
- Return normalized candidate payload and projection warnings.

Do not use an LLM. Do not call external tools.

### Numeric Short Answer Heuristic

Introduce helpers:

```python
def _numeric_answer_candidate(text: str) -> str:
    ...

def _is_numeric_answer_like(text: str) -> bool:
    ...

def _extract_numeric_final_answer(text: str) -> str:
    ...
```

Rules:

- A valid numeric short answer should be concise.
- It should not look like a setup/procedure sentence.
- It may include a unit.
- It may include one or more numbers only when the answer naturally requires them, but for this P0 fix handle the common one-number final answer path first.
- If the text contains many chemistry setup units and no final-answer anchor, treat it as non-answer-like.

Suggested implementation constraints:

```python
NUMERIC_SETUP_MARKERS = (
    "after mixing",
    "initial concentration",
    "total volume",
    "reaction quotient",
    "using ksp",
    "let ",
    "solving the system",
)
```

`_is_numeric_answer_like()` should reject text if:

- it has more than 160 characters, or
- it has more than 2 sentence-ending punctuation marks, or
- it contains obvious setup markers, or
- it has 3 or more distinct numeric tokens and no final-answer marker.

Final-answer extraction should prefer anchored patterns:

```text
Mass of dissolved Sr²⁺ = ... = 7.59 μg
final answer: 7.59 μg
answer is 7.59 micrograms
yields 7.59 μg
= 7.59 μg
```

Do not make the extractor too broad. If no confident final value exists, fail validation rather than guessing.

### Projection Metadata

When projection repairs the short answer, preserve traceability in the candidate payload:

```json
{
  "projection_metadata": {
    "source": "full_answer",
    "repair": "numeric_final_answer_extraction",
    "raw_evaluator_answer": "After mixing 100 mL..."
  }
}
```

Then propagate it into `final_answer_artifact.json`:

```json
{
  "projection_metadata": { ... }
}
```

This makes repaired answers auditable without changing benchmark scoring.

### Rebuttal Extraction

Add protocol extraction helpers in `chemqa_artifact_flow.py`:

```python
def rebuttals_from_protocol(protocol: dict[str, Any], *, answer_kind: str, run_id: str = "") -> list[dict[str, Any]]:
    ...
```

Expected sources:

- `protocol["proposer_trajectory"]["rebuttals"]`
- `protocol["proposer_trajectory"]` as a list of event-like dictionaries, if present in older data

For each rebuttal:

- Extract `payload` if present; otherwise treat the object itself as payload.
- Pass payload through `validate_rebuttal_artifact()`.
- Keep only valid rebuttal artifacts for P0 finalization.
- Sort by `epoch`, then `round`.

Do not mine reviewer text or natural language summaries as answer revisions. Only structured `mode: answer_revision` plus `updated_answer` or `updated_direct_answer` may update the answer.

### Finalization Wiring

Modify `finalization_from_protocol()`:

```python
candidate = candidate_from_protocol(protocol, answer_kind=answer_kind, run_id=run_id)
rebuttals = rebuttals_from_protocol(protocol, answer_kind=answer_kind, run_id=run_id)
candidate_state = build_current_candidate_view(
    candidate_artifact=candidate.artifact,
    review_artifacts=[],
    rebuttal_artifacts=rebuttals,
)
```

Then finalization uses the updated candidate view as it already does.

### Benchmark Layer

Keep `benchmark_test.py` behavior mostly unchanged:

- Continue reading `final_answer_artifact.evaluator_answer` as the scoreable answer.
- Do not add broad parsing of `full_answer` in benchmark code.
- Add or update a focused test proving that if Artifact Flow produces repaired `evaluator_answer`, benchmark extraction uses it.

The benchmark runner should not become the place that fixes ChemQA projection bugs.

## File Change Map

### Modify: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`

Responsibilities:

- Add answer projection helper functions.
- Strengthen `numeric_short_answer` validation.
- Use projection helper in `validate_candidate_artifact()` and `validate_rebuttal_artifact()`.
- Extract structured rebuttals from protocol.
- Pass rebuttal artifacts into `build_current_candidate_view()` in `finalization_from_protocol()`.
- Preserve `projection_metadata` into `candidate_view` and `final_answer_artifact`.

### Modify: `skills/chemqa-review/scripts/collect_artifacts.py`

Expected minimal change:

- No direct answer logic should be added here.
- Only update if the finalization payload needs to expose new `projection_metadata` in `qa_result` or manifest output.
- `collect_artifacts.py` remains a converter/orchestrator and should not duplicate projection logic.

### Modify or Add: `tests/test_chemqa_artifact_flow.py`

Responsibilities:

- Import `chemqa_artifact_flow.py` directly from `skills/chemqa-review/scripts`.
- Test projection repair.
- Test rebuttal revision propagation.
- Test `response_only` does not mutate the answer.

### Modify: `tests/test_benchmark_test.py`

Responsibilities:

- Add only one compatibility test if needed: final artifact `evaluator_answer` is the scoreable short answer while `full_answer` remains long.
- Avoid expanding benchmark tests with Artifact Flow internals.

### Modify: `GLOBAL_DEV_SPEC.md`

Only update after implementation if behavior changes are complete and verified. Suggested update:

- Mention that ChemQA Artifact Flow now applies structured rebuttal `answer_revision` during finalization.
- Mention that numeric short-answer projection rejects setup/process sentences and can repair from anchored final values in `full_answer`.

## Implementation Tasks

### Task 1: Add Focused Artifact Flow Test Harness

**Files:**

- Create: `tests/test_chemqa_artifact_flow.py`
- Read: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`

- [ ] **Step 1: Create direct module import helper**

Use this import pattern so tests do not require packaging changes:

```python
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "chemqa-review"
    / "scripts"
    / "chemqa_artifact_flow.py"
)
SPEC = importlib.util.spec_from_file_location("chemqa_artifact_flow", MODULE_PATH)
chemqa_artifact_flow = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = chemqa_artifact_flow
SPEC.loader.exec_module(chemqa_artifact_flow)
```

- [ ] **Step 2: Add failing SrF2 projection test**

```python
class ChemQAArtifactFlowProjectionTests(unittest.TestCase):
    def test_numeric_short_answer_repairs_setup_sentence_from_summary_final_value(self) -> None:
        candidate = {
            "owner": "proposer-1",
            "direct_answer": "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.",
            "summary": (
                "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, "
                "the total volume is 200 mL. Initial concentrations: [F-] = 0.125 M, "
                "[Sr2+] = 0.0125 M. Using Ksp = [Sr2+][F-]^2, residual [Sr2+] = "
                "4.33e-7 M. Mass of dissolved Sr2+ = (4.33e-7 mol/L * 0.200 L) "
                "* 87.6 g/mol = 7.59 μg."
            ),
            "reasoning_summary": "Correct Ksp calculation gives 7.59 μg.",
        }

        result = chemqa_artifact_flow.validate_candidate_artifact(
            candidate,
            answer_kind="numeric_short_answer",
            run_id="sr-demo",
        )

        self.assertTrue(result.valid, result.errors)
        payload = result.artifact["payload"]
        self.assertEqual("7.59 μg", payload["evaluator_answer"])
        self.assertEqual("7.59 μg", payload["display_answer"])
        self.assertIn("7.59 μg", payload["full_answer"])
        self.assertEqual("numeric_final_answer_extraction", payload["projection_metadata"]["repair"])
```

- [ ] **Step 3: Run test and verify it fails before implementation**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py::ChemQAArtifactFlowProjectionTests::test_numeric_short_answer_repairs_setup_sentence_from_summary_final_value -v
```

Expected:

```text
FAILED
AssertionError: '7.59 μg' != 'After mixing 100 mL...'
```

### Task 2: Implement Numeric Projection Repair

**Files:**

- Modify: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`
- Test: `tests/test_chemqa_artifact_flow.py`

- [ ] **Step 1: Add numeric answer helpers**

Add constants near `FORMULA_SIGNAL_RE`:

```python
NUMERIC_SETUP_MARKERS = (
    "after mixing",
    "initial concentration",
    "initial concentrations",
    "total volume",
    "reaction quotient",
    "using ksp",
    "let ",
    "solving the system",
    "step:",
    "anchor:",
)

FINAL_NUMERIC_ANCHOR_RE = re.compile(
    r"(?i)(?:final answer|answer is|mass of [^.=:;]+|yields?|therefore|=)\s*[:=]?\s*"
    r".*?([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*"
    r"(?:μg|ug|micrograms?|mg|g|kg|mol|mmol|m|M|%|percent)?)"
)
```

- [ ] **Step 2: Add answer-likeness helpers**

```python
def _numeric_tokens(text: str) -> list[str]:
    return re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", clean_text(text))


def _is_numeric_answer_like(text: str) -> bool:
    value = clean_text(text)
    if not value:
        return False
    if _numeric_value(value) is None:
        return False
    lowered = value.lower()
    if len(value) > 160:
        return False
    if sum(value.count(mark) for mark in (".", ";", ":")) > 3:
        return False
    if any(marker in lowered for marker in NUMERIC_SETUP_MARKERS):
        return False
    if len(_numeric_tokens(value)) >= 3 and not re.search(r"(?i)\b(final answer|answer is|therefore)\b", value):
        return False
    return True
```

- [ ] **Step 3: Add anchored final numeric extraction**

```python
def _normalize_numeric_answer_candidate(value: str) -> str:
    answer = clean_text(value)
    answer = re.sub(r"(?i)\b(?:final answer|answer is|therefore)\b\s*[:=]?\s*", "", answer).strip()
    return answer.rstrip(" .;")


def _extract_numeric_final_answer(text: str) -> str:
    value = clean_text(text)
    if not value:
        return ""
    matches = list(FINAL_NUMERIC_ANCHOR_RE.finditer(value))
    for match in reversed(matches):
        candidate = _normalize_numeric_answer_candidate(match.group(1))
        if _is_numeric_answer_like(candidate):
            return candidate
    final_line_match = re.search(r"(?im)^\s*final answer\s*:\s*(.+?)\s*$", value)
    if final_line_match:
        candidate = _normalize_numeric_answer_candidate(final_line_match.group(1))
        if _is_numeric_answer_like(candidate):
            return candidate
    return ""
```

- [ ] **Step 4: Add projection helper**

```python
def _project_evaluator_answer(
    *,
    answer_kind: str,
    evaluator_answer: str,
    display_answer: str,
    full_answer: str,
    summary: str,
) -> tuple[str, str, str, dict[str, Any] | None, list[str]]:
    raw_evaluator = clean_text(evaluator_answer)
    raw_display = clean_text(display_answer) or raw_evaluator
    raw_full = clean_text(full_answer) or clean_text(summary) or raw_evaluator
    warnings: list[str] = []

    if answer_kind == "numeric_short_answer" and not _is_numeric_answer_like(raw_evaluator):
        repaired = _extract_numeric_final_answer(raw_full) or _extract_numeric_final_answer(summary)
        if repaired:
            metadata = {
                "source": "full_answer" if repaired in raw_full else "summary",
                "repair": "numeric_final_answer_extraction",
                "raw_evaluator_answer": raw_evaluator,
            }
            warnings.append("numeric_short_answer evaluator_answer repaired from anchored full answer")
            return repaired, repaired, raw_full, metadata, warnings

    return raw_evaluator, raw_display, raw_full, None, warnings
```

- [ ] **Step 5: Use projection in `validate_candidate_artifact()`**

Replace direct assignment of `evaluator_answer`, `display_answer`, and `full_answer` with:

```python
raw_evaluator_answer = _first_text(payload, "evaluator_answer", "direct_answer", "answer", "value", "final_answer")
raw_display_answer = _first_text(payload, "display_answer") or raw_evaluator_answer
raw_full_answer = _first_text(payload, "full_answer", "final_markdown", "final_text") or clean_text(payload.get("summary"))
reasoning_summary = clean_text(payload.get("reasoning_summary") or payload.get("summary") or payload.get("justification"))
evaluator_answer, display_answer, full_answer, projection_metadata, projection_warnings = _project_evaluator_answer(
    answer_kind=answer_kind,
    evaluator_answer=raw_evaluator_answer,
    display_answer=raw_display_answer,
    full_answer=raw_full_answer,
    summary=clean_text(payload.get("summary")),
)
```

Add to `candidate_payload`:

```python
"projection_metadata": projection_metadata or {},
```

Pass `warnings=projection_warnings` into `_common_artifact()`.

- [ ] **Step 6: Strengthen `_validate_answer_projection()`**

Change numeric branch:

```python
if answer_kind == "numeric_short_answer":
    if not _is_numeric_answer_like(answer):
        errors.append("numeric_short_answer requires a concise numeric evaluator_answer")
```

- [ ] **Step 7: Run SrF2 projection test**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py::ChemQAArtifactFlowProjectionTests::test_numeric_short_answer_repairs_setup_sentence_from_summary_final_value -v
```

Expected:

```text
PASSED
```

### Task 3: Propagate Projection Metadata to Final Artifacts

**Files:**

- Modify: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`
- Test: `tests/test_chemqa_artifact_flow.py`

- [ ] **Step 1: Add finalization test**

```python
    def test_finalization_writes_repaired_numeric_evaluator_answer(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.",
                "summary": "Mass of dissolved Sr2+ = (4.33e-7 mol/L * 0.200 L) * 87.6 g/mol = 7.59 μg.",
                "reasoning_summary": "Correct Ksp calculation gives 7.59 μg.",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="sr-final-demo",
                answer_kind="numeric_short_answer",
            )

            self.assertEqual("completed", result.terminal_state)
            final = result.qa_result["final_answer"]
            self.assertEqual("7.59 μg", final["direct_answer"])
            self.assertEqual("7.59 μg", final["answer"])
            self.assertEqual("7.59 μg", final["value"])
```

- [ ] **Step 2: Preserve metadata in `finalize_success()`**

Read metadata from candidate payload:

```python
projection_metadata = clone_jsonish(candidate_payload.get("projection_metadata") or {})
```

Add to `final_answer_artifact`:

```python
"projection_metadata": projection_metadata,
```

Optionally add to `qa_result["final_answer"]`:

```python
"projection_metadata": projection_metadata,
```

- [ ] **Step 3: Run finalization test**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py::ChemQAArtifactFlowProjectionTests::test_finalization_writes_repaired_numeric_evaluator_answer -v
```

Expected:

```text
PASSED
```

### Task 4: Extract Rebuttal Artifacts From Protocol

**Files:**

- Modify: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`
- Test: `tests/test_chemqa_artifact_flow.py`

- [ ] **Step 1: Add failing answer-revision propagation test**

```python
    def test_protocol_answer_revision_updates_final_evaluator_answer(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "The mass gain of the Cu strip (1.52 g) from Cu + 2Ag+ -> Cu2+ + 2Ag displacement",
                "summary": "Initial incorrect calculation.",
            },
            "proposer_trajectory": {
                "rebuttals": [
                    {
                        "rebuttal_round": 1,
                        "payload": {
                            "artifact_kind": "rebuttal",
                            "phase": "rebuttal",
                            "owner": "proposer-1",
                            "mode": "answer_revision",
                            "response_summary": "Corrected the algebra error.",
                            "updated_answer": {
                                "evaluator_answer": "NaCl: 69.9%, KCl: 30.1%",
                                "display_answer": "NaCl 69.9%, KCl 30.1% by mass",
                                "full_answer": "The mixture contains 17.48 g NaCl (69.9%) and 7.52 g KCl (30.1%) by mass.",
                            },
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="nacl-kcl-demo",
                answer_kind="multi_part_research_answer",
            )

            final = result.qa_result["final_answer"]
            self.assertEqual("NaCl: 69.9%, KCl: 30.1%", final["direct_answer"])
            self.assertEqual("NaCl 69.9%, KCl 30.1% by mass", final["display_answer"])
            self.assertIn("17.48 g NaCl", final["full_answer"])
```

- [ ] **Step 2: Add `rebuttals_from_protocol()`**

```python
def rebuttals_from_protocol(protocol: dict[str, Any], *, answer_kind: str, run_id: str = "") -> list[dict[str, Any]]:
    trajectory = protocol.get("proposer_trajectory")
    raw_rebuttals: list[Any] = []
    if isinstance(trajectory, dict):
        raw_rebuttals.extend(trajectory.get("rebuttals") or [])
    elif isinstance(trajectory, list):
        for event in trajectory:
            if isinstance(event, dict) and (event.get("artifact_kind") == "rebuttal" or event.get("payload")):
                raw_rebuttals.append(event)

    artifacts: list[dict[str, Any]] = []
    for item in raw_rebuttals:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
        validation = validate_rebuttal_artifact(payload, answer_kind=answer_kind, run_id=run_id)
        if validation.valid:
            artifact = validation.artifact
            if item.get("rebuttal_round") and not artifact.get("round"):
                artifact["round"] = int(item.get("rebuttal_round") or 0)
            artifacts.append(artifact)
    return sorted(artifacts, key=lambda artifact: (int(artifact.get("epoch") or 1), int(artifact.get("round") or 0)))
```

- [ ] **Step 3: Wire rebuttals into finalization**

Change:

```python
candidate_state = build_current_candidate_view(
    candidate_artifact=candidate.artifact,
    review_artifacts=[],
    rebuttal_artifacts=[],
)
```

to:

```python
candidate_state = build_current_candidate_view(
    candidate_artifact=candidate.artifact,
    review_artifacts=[],
    rebuttal_artifacts=rebuttals_from_protocol(protocol, answer_kind=answer_kind, run_id=run_id),
)
```

- [ ] **Step 4: Run answer-revision propagation test**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py::ChemQAArtifactFlowProjectionTests::test_protocol_answer_revision_updates_final_evaluator_answer -v
```

Expected:

```text
PASSED
```

### Task 5: Ensure `response_only` Does Not Mutate Final Answer

**Files:**

- Modify: `tests/test_chemqa_artifact_flow.py`
- Read: `skills/chemqa-review/scripts/chemqa_artifact_flow.py`

- [ ] **Step 1: Add regression test for false repair claim**

```python
    def test_response_only_rebuttal_does_not_update_answer_even_if_summary_claims_fix(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "42",
                "summary": "Original answer.",
            },
            "proposer_trajectory": {
                "rebuttals": [
                    {
                        "rebuttal_round": 1,
                        "payload": {
                            "artifact_kind": "rebuttal",
                            "phase": "rebuttal",
                            "owner": "proposer-1",
                            "mode": "response_only",
                            "response_summary": "Fixed direct_answer to 7.59 μg.",
                            "updated_answer": None,
                            "updated_direct_answer": None,
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="response-only-demo",
                answer_kind="numeric_short_answer",
            )

            self.assertEqual("42", result.qa_result["final_answer"]["direct_answer"])
```

- [ ] **Step 2: Run the test**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py::ChemQAArtifactFlowProjectionTests::test_response_only_rebuttal_does_not_update_answer_even_if_summary_claims_fix -v
```

Expected:

```text
PASSED
```

### Task 6: Add Benchmark Compatibility Test

**Files:**

- Modify: `tests/test_benchmark_test.py`
- Read: `benchmark_test.py`

- [ ] **Step 1: Add test for final artifact short-answer precedence**

Add near existing ChemQA answer extraction tests:

```python
    def test_build_chemqa_full_response_uses_final_artifact_evaluator_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "final_answer_artifact.json"
            path.write_text(
                json.dumps(
                    {
                        "evaluator_answer": "7.59 μg",
                        "display_answer": "7.59 μg",
                        "full_answer": "Long derivation ending in 7.59 μg.",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            short, full = benchmark_test.build_chemqa_full_response(
                qa_result={"artifact_paths": {"final_answer_artifact": str(path)}}
            )

        self.assertEqual("7.59 μg", short)
        self.assertEqual("Long derivation ending in 7.59 μg.", full)
```

- [ ] **Step 2: Run compatibility test**

Run:

```bash
pytest tests/test_benchmark_test.py::BenchmarkTestModuleTests::test_build_chemqa_full_response_uses_final_artifact_evaluator_answer -v
```

Expected:

```text
PASSED
```

### Task 7: Run Focused Test Suite

**Files:**

- Test: `tests/test_chemqa_artifact_flow.py`
- Test: `tests/test_benchmark_test.py`

- [ ] **Step 1: Run Artifact Flow tests**

Run:

```bash
pytest tests/test_chemqa_artifact_flow.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 2: Run benchmark compatibility tests**

Run:

```bash
pytest tests/test_benchmark_test.py -v
```

Expected:

```text
all tests passed
```

- [ ] **Step 3: Run broader relevant benchmark tests**

Run:

```bash
pytest tests/test_benchmark_evaluators.py tests/test_benchmark_status.py tests/test_benchmark_contracts.py -v
```

Expected:

```text
all tests passed
```

### Task 8: Update Documentation and Commit

**Files:**

- Modify: `GLOBAL_DEV_SPEC.md`
- Modify: implementation files from previous tasks
- Modify: test files from previous tasks

- [ ] **Step 1: Update `GLOBAL_DEV_SPEC.md` if implementation changes behavior**

Add one concise sentence to the ChemQA Artifact Flow capability entry:

```markdown
  - `DONE`: Collect ChemQA protocol outputs through Artifact Flow into canonical terminal artifacts, `artifact_manifest.json`, and legacy-compatible `qa_result.json` via `workspace/skills/chemqa-review/scripts/chemqa_artifact_flow.py` and `collect_artifacts.py`; finalization applies structured `answer_revision` rebuttals and repairs numeric short-answer projections from anchored final values in the full answer when the raw direct answer is a setup/process sentence.
```

- [ ] **Step 2: Check git diff**

Run:

```bash
git diff --stat
git diff -- skills/chemqa-review/scripts/chemqa_artifact_flow.py tests/test_chemqa_artifact_flow.py tests/test_benchmark_test.py GLOBAL_DEV_SPEC.md
```

Expected:

```text
Only P0 projection/revision propagation changes are present.
```

- [ ] **Step 3: Commit after tests pass**

Run:

```bash
git status --short
git add skills/chemqa-review/scripts/chemqa_artifact_flow.py tests/test_chemqa_artifact_flow.py tests/test_benchmark_test.py GLOBAL_DEV_SPEC.md
git commit -m "fix: repair chemqa final answer projection"
```

Expected:

```text
[branch ...] fix: repair chemqa final answer projection
```

## Acceptance Criteria

- SrF2-style candidate with setup text in `direct_answer` and anchored `7.59 μg` in `summary/full_answer` finalizes with `evaluator_answer = "7.59 μg"`.
- NaCl/KCl-style protocol with structured `mode: answer_revision` finalizes with the latest `updated_answer.evaluator_answer`.
- `response_only` rebuttals never mutate `evaluator_answer`, even if their natural-language summary claims a fix.
- `final_answer_artifact.json`, `qa_result.json`, and benchmark `short_answer_text` all agree on the final evaluator answer.
- `benchmark_test.py` remains a consumer of canonical final artifacts, not a second answer-repair engine.
- Focused Artifact Flow and benchmark compatibility tests pass.

## Rollback Plan

If projection repair causes false positives:

1. Revert only the numeric extraction repair path while keeping answer-revision propagation.
2. Keep strengthened numeric validation so setup sentences fail instead of silently scoring.
3. Mark affected runs as `candidate_validation_failed` rather than producing malformed `native_final` answers.

If rebuttal propagation causes unexpected final answers:

1. Restrict `rebuttals_from_protocol()` to `proposer_trajectory.rebuttals[*].payload` only.
2. Ignore event-list fallback temporarily.
3. Require `mode == "answer_revision"` and non-empty `updated_answer.evaluator_answer`.

## Notes for Future P1 Work

This P0 plan intentionally does not close all protocol issues. After P0 lands, separate P1 work should address:

- reviewer items targeting `direct_answer/evaluator_answer` should remain blocking until an actual `answer_revision` changes the canonical field
- protocol prompt simplification so models spend less effort on artifact ceremony
- narrower chemistry skill trigger rules to reduce unnecessary tool-trace noise
- clearer status semantics for rejected-but-evaluable versus accepted final answers
