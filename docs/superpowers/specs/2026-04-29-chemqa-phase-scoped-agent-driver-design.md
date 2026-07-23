# ChemQA Phase-Scoped Agent Driver Design

## 中文设计摘要

本设计针对 ChemQA benchmark 真实 run 中反复出现的“agent 意外提前退出”问题。复盘 transcript 后可以确认，关键根因不是某个模型不听指令、不是单纯 timeout，也不是提示词需要继续加硬，而是当前 driver 架构把两个不同概念错误地绑定在一起：

- 一次 OpenClaw agent turn 结束
- 一个 ChemQA role 在当前 phase 的工作完成或失败

当前 `chemqa_review_openclaw_driver.py` 的 artifact 生成路径默认认为：调用一次 `openclaw agent --local` 后，工作区必须留下 `proposal.yaml`、`review-proposer-1.yaml`、`rebuttal.yaml` 或 `chemqa_review_protocol.yaml`。如果 OpenClaw 正常返回 0，但模型只输出“下一步我要检查工具 / 继续分析 / 查状态”，driver 随后发现 artifact 缺失，就把这次 turn 直接记为 `missing proposal.yaml after model turn` 一类 lane failure。

这是 driver 层的契约设计错误。`stopReason=stop` 只表示一次模型回合自然结束，不应表示 role phase 已经失败。真实 agent 在复杂化学任务里可能需要多个 turn 才能完成一个 phase：先查状态、调用工具、处理工具失败、继续推理、再写 YAML artifact。driver 应该负责管理这个 phase 级闭环，而不是要求模型在单次 turn 内满足最终 artifact contract。

目标架构是把现有“一次模型调用生成一个 artifact”的模型，替换为 **Phase-Scoped Agent Loop**：

```text
Protocol Loop
  -> Role Phase Executor
       -> repeated OpenClaw turns in the same session
       -> artifact observation and validation after each turn
       -> runtime feedback if artifact is missing/invalid/stale
       -> submit only when artifact validates
       -> fail only when phase-level budgets are exhausted
```

修复后的核心语义是：

- `missing artifact after turn` 是 `incomplete_turn`，不是 `lane_failure`。
- `stopReason=stop` 是普通 turn boundary，不是 role failure。
- driver 以同一个 `session_id` 持续推进当前 role phase，保留上下文。
- artifact contract 在 phase executor 中检查；通过后再提交给 DebateClaw state。
- 失败只发生在明确 hard error、phase wall-clock timeout、phase turn budget exhausted 或 artifact repair budget exhausted 之后。

另一个必须同时修复的诱因是：materialized prompt 中的 compact state snapshot 命令没有带 `--runtime-dir`。在 run-scoped `HOME` 下，`chemqa_review_state_snapshot.py` 默认去 `$HOME/.clawteam/debateclaw/bin/debate_state.py` 找 helper，真实 transcript 中已经出现 `Missing runtime helper`。这会把 agent 带入“找路径 / 找工具”的轨道，加剧 artifact 缺失。但它是诱因，不是根因；根因仍是 driver 把单 turn 结束误判为 phase 失败。

## Goal

Redesign the ChemQA OpenClaw driver so a role phase can span multiple OpenClaw agent turns without being misclassified as an early agent exit.

The target behavior is:

- one DebateClaw phase action may require several OpenClaw turns
- each turn may reason, call tools, inspect state, or write files
- the driver observes and validates required artifacts after every turn
- missing artifacts after a normal turn are treated as incomplete progress, not failure
- the role phase fails only after explicit phase-level exhaustion or hard errors
- run-status and blocker diagnostics preserve enough detail to distinguish model behavior, tool failures, artifact validation failures, and true liveness failures

## Background

Recent benchmark transcript review showed three relevant patterns.

### Pattern 1: Proposer stopped without artifact

`proposer-1` sometimes ended a turn with natural language such as:

```text
Let me check if there's a chem-calculator skill available:
```

The OpenClaw transcript recorded `stopReason=stop`, and the wrapper returned 0. No `proposal.yaml` was written. The driver then recorded:

```text
proposer-1 failed to produce a valid candidate submission: missing `proposal.yaml` after model turn
```

This is not a true role failure. It is an incomplete turn inside a longer phase.

### Pattern 2: Tool/environment issue diverted the agent

Some transcripts show the agent first calling the compact state snapshot helper, receiving:

```text
Missing runtime helper: .../.clawteam/debateclaw/bin/debate_state.py
```

The prompt pointed the agent to a helper path that did not exist under the run-scoped `HOME`. The agent then spent its only turn trying to recover orientation instead of writing the required artifact.

### Pattern 3: Coordinator aborted during terminal protocol refinement

Terminal `debate-coordinator` transcripts show `stopReason=aborted` with only partial thinking. This happens in the coordinator protocol refinement turn, where the model receives a deterministic protocol scaffold plus completed debate summary and is expected to rewrite `chemqa_review_protocol.yaml` within the model timeout.

This is a timeout/abort case and should be handled separately from normal `stop` without artifact.

## Non-Goals

- Do not redesign DebateClaw's `propose -> review -> rebuttal -> done` state machine in this change.
- Do not change the ChemQA collaboration topology of one candidate owner plus four fixed reviewer lanes.
- Do not require models to emit final artifacts in a single turn.
- Do not solve all chemistry tool availability issues in this design.
- Do not replace the two-layer Artifact Flow design. This design complements it by stabilizing how artifacts are produced.
- Do not make benchmark runner recovery the primary solution. Recovery remains a fallback; the driver should produce durable artifacts during normal execution.

## Design Principles

1. A model turn is not a protocol phase.
2. A normal OpenClaw return code does not mean the required artifact was produced.
3. `stopReason=stop` is a turn boundary, not a failure boundary.
4. Artifact absence is incomplete progress until a phase-level budget says otherwise.
5. Driver feedback should be incremental and stateful, using the same session id.
6. Artifact validity is determined by validators, not by transcript wording.
7. Hard failures, soft incomplete turns, invalid artifacts, stale artifacts, and timeouts must be classified separately.
8. Benchmark-visible run status should report the current phase executor state, not only protocol phase state.

## Current Architecture Problem

The current flow is effectively:

```text
run_worker_loop()
  -> ensure_candidate_submission()
       -> attempt_model_artifact()
            -> call_model() once
            -> check proposal.yaml
            -> if missing: record lane failure / raise DriverError
```

This makes a single call to `openclaw agent --local` the unit of correctness. It works only when the model immediately writes the expected YAML file in the first turn.

The driver already validates artifacts after model calls, which is good. The design flaw is that validation failure is scoped to the turn but recorded as if the role phase failed.

## Target Architecture

### Layer 1: Protocol Loop

The existing worker/coordinator loops continue to own DebateClaw protocol state:

- fetch `status`
- fetch `next-action`
- advance phase when appropriate
- submit proposal/review/rebuttal after artifact validation
- save sessions
- update task status
- update benchmark-visible run status

Protocol Loop should delegate role work to a phase executor:

```text
if phase == "propose" and action == "propose":
    phase_executor.run_required_artifact_phase(candidate_submission_contract)

if phase == "review" and action == "review":
    phase_executor.run_required_artifact_phase(formal_review_contract)

if phase == "rebuttal" and action == "rebuttal":
    phase_executor.run_required_artifact_phase(rebuttal_contract)
```

Protocol Loop should not decide that one model turn failed because an artifact was missing. It should only observe the final phase executor result.

### Layer 2: Role Phase Executor

Role Phase Executor owns one role's current phase work. It is the new unit of driver correctness.

It receives:

- role name
- team id
- session id
- phase name
- artifact contract
- current `next-action` payload
- phase budgets
- prompt instructions

It performs:

1. Observe current artifact state before the turn.
2. Build the next runtime message.
3. Call OpenClaw once with the same session id.
4. Capture turn outcome.
5. Re-observe artifact state.
6. If artifact valid, return success.
7. If artifact missing/invalid/stale and budget remains, continue with corrective feedback.
8. If budget exhausted, return phase failure with structured diagnostics.

The executor replaces `attempt_model_artifact()` as the primary artifact production API.

### Layer 3: Artifact Contract Layer

Artifact Contract Layer describes what artifact is required and how to validate it.

Each contract includes:

```python
ArtifactContract(
    artifact_kind="candidate_submission",
    filename="proposal.yaml",
    checker=check_candidate_submission,
    submitter=submit_proposal,
    stale_policy="must_change_if_existing",
    minimal_template_lines=[...],
)
```

Contract validation returns a structured outcome:

```python
ArtifactOutcome(
    state="missing" | "present_invalid" | "present_stale" | "present_valid",
    path=Path(...),
    validation_errors=[...],
    changed_since_turn=True | False,
    normalized_text="...",
)
```

The layer does not call the model and does not advance protocol. It only observes and validates files.

### Layer 4: Turn Outcome Layer

The wrapper should produce a structured turn result after each OpenClaw call.

Minimum fields:

```json
{
  "returncode": 0,
  "stop_reason": "stop",
  "timed_out": false,
  "aborted": false,
  "session_id": "...",
  "transcript_path": "...",
  "tool_call_count": 2,
  "message_count_delta": 4,
  "last_assistant_text_preview": "Let me check ...",
  "started_at": "...",
  "completed_at": "..."
}
```

This avoids forcing the driver to infer turn behavior from process return code alone.

## New State Model

### TurnOutcome

`TurnOutcome` represents one OpenClaw invocation.

Fields:

- `returncode`: subprocess return code
- `stop_reason`: parsed final assistant stop reason when available
- `timed_out`: whether driver killed the process by timeout
- `aborted`: whether transcript or wrapper indicates model abort
- `hard_error`: non-timeout wrapper or subprocess error
- `transcript_path`: session JSONL path if known
- `tool_call_count`: number of tool calls in the turn if available
- `assistant_text_tail`: short final assistant preview for diagnostics
- `stdout_preview`
- `stderr_preview`

Interpretation:

- `returncode=0` with `stop_reason=stop` means only that the turn ended normally.
- `stop_reason=stop` with no artifact is `incomplete_turn`.
- `timed_out=True` with valid artifact on disk may be salvageable.
- `aborted=True` with no valid artifact consumes budget and may trigger fallback behavior.

### ArtifactOutcome

`ArtifactOutcome` represents required file state after a turn.

States:

- `missing`: file does not exist
- `present_invalid`: file exists but validator rejects it
- `present_stale`: file exists but was not updated when the contract requires a fresh artifact
- `present_valid`: file exists and validator accepts it

The outcome carries:

- `filename`
- `path`
- `validation_errors`
- `normalized_text`
- `pre_turn_snapshot`
- `post_turn_snapshot`
- `changed_since_turn`

### PhaseAttemptState

`PhaseAttemptState` persists phase-level progress.

Fields:

- `role`
- `phase`
- `artifact_kind`
- `turn_index`
- `max_phase_turns`
- `phase_started_at`
- `phase_deadline_at`
- `last_turn_outcome`
- `last_artifact_outcome`
- `last_feedback`
- `classification`

Classifications:

- `running`
- `waiting_for_artifact`
- `repairing_invalid_artifact`
- `repairing_stale_artifact`
- `submitted`
- `failed_budget_exhausted`
- `failed_hard_error`
- `failed_timeout`

## Control Flow

### Candidate proposal phase

Target flow:

```text
proposer-1 action=propose
  -> contract requires proposal.yaml
  -> phase executor starts with same session id
  -> turn 1 may reason/tool-call/stop
  -> if proposal.yaml missing:
       continue same session with feedback
  -> turn 2 may write proposal.yaml
  -> validator normalizes and accepts
  -> driver submits proposal
  -> protocol progress is marked
```

Missing file feedback should be explicit:

```text
Previous turn ended normally, but the required artifact was not found.

Required artifact:
- proposal.yaml

This phase is still in progress. Continue from your prior context.
You may use tools if needed, but the phase is not complete until proposal.yaml exists and validates.
When ready, write proposal.yaml in the current workspace.
```

### Formal review phase

Reviewer lanes use the same executor, but with `review-proposer-1.yaml`.

Transport placeholder reviews remain deterministic and do not require a model phase executor.

### Rebuttal phase

Candidate owner uses the same executor with `rebuttal.yaml`.

If a rebuttal artifact exists but is stale, feedback should say:

```text
The existing rebuttal.yaml appears unchanged from before this turn.
Rewrite it for the current rebuttal round and address the listed review items.
```

### Terminal coordinator phase

Coordinator protocol generation should change from “model must rewrite the full protocol” to “deterministic protocol is primary, model refinement is optional.”

Target behavior:

1. Write deterministic protocol scaffold.
2. Validate deterministic protocol.
3. Optionally ask model to refine it under a bounded phase executor.
4. If model aborts, times out, or leaves invalid output, keep deterministic protocol.
5. Finalization must not fail solely because model refinement did not complete.

This treats coordinator model refinement as a quality enhancement, not a critical path for benchmark evaluability.

## Budget Model

Replace the overloaded `max_model_attempts` semantics with explicit budgets.

### New settings

- `max_phase_turns`
  - Default candidate: 4
  - Default formal review: 3
  - Default rebuttal: 3
  - Default coordinator refinement: 1 or 2

- `phase_wall_timeout_seconds`
  - Default candidate: 900
  - Default formal review: 900
  - Default rebuttal: 600
  - Default coordinator refinement: 360

- `per_turn_timeout_seconds`
  - Existing artifact-specific model timeouts can remain as per-turn timeout defaults.

- `artifact_repair_turns`
  - Number of additional turns allowed after a file exists but fails schema validation.

- `tool_error_grace_turns`
  - Number of turns allowed after known recoverable tool/runtime helper errors before counting against failure severity.

### Compatibility

Existing `--max-model-attempts` can remain as a deprecated alias for `max_phase_turns` during transition.

The driver should log a compatibility warning if both are supplied.

## Failure Classification

New failure classification should separate these cases:

```text
incomplete_turn_no_artifact
artifact_invalid
artifact_stale
turn_timeout_no_artifact
turn_timeout_artifact_salvaged
model_aborted_no_artifact
wrapper_hard_error
phase_budget_exhausted
phase_wall_timeout_exhausted
submit_rejected_duplicate
submit_rejected_state_mismatch
```

Only these should become lane failures:

- `phase_budget_exhausted`
- `phase_wall_timeout_exhausted`
- `wrapper_hard_error`
- repeated `artifact_invalid` after repair budget
- repeated `artifact_stale` after repair budget
- non-recoverable submit rejection

These should not be lane failures by themselves:

- one normal `stop` without artifact
- one tool call failure followed by available budget
- one invalid YAML artifact with repair budget remaining
- coordinator refinement abort when deterministic protocol is valid

## Run Status and Diagnostics

Benchmark-visible run-status should expose phase executor state.

Example:

```json
{
  "run_id": "...",
  "status": "running",
  "phase": "propose",
  "role_phase": {
    "role": "proposer-1",
    "artifact_kind": "candidate_submission",
    "turn_index": 2,
    "max_phase_turns": 4,
    "classification": "waiting_for_artifact",
    "last_turn": {
      "returncode": 0,
      "stop_reason": "stop",
      "tool_call_count": 1,
      "assistant_text_tail": "Let me check if..."
    },
    "last_artifact": {
      "state": "missing",
      "filename": "proposal.yaml",
      "validation_errors": []
    }
  }
}
```

Blocker files should use the new classification:

```json
{
  "status": "phase_incomplete",
  "classification": "incomplete_turn_no_artifact",
  "role": "proposer-1",
  "phase": "propose",
  "turn_index": 1,
  "artifact_state": "missing",
  "next_driver_action": "continue_same_session"
}
```

Only final phase exhaustion should write a terminal blocker or terminal failure.

## Prompt and Runtime Command Fixes

### Compact snapshot command

`materialize_runplan.py` must include the resolved runtime dir in the generated compact snapshot command:

```text
python chemqa_review_state_snapshot.py --skill-root ... --runtime-dir <runtime_root> --team ... --agent ...
```

The fallback commands already include `<runtime_root>/debate_state.py`; compact snapshot must use the same runtime root.

### Runtime feedback messages

Initial role prompts can continue to say that the role should write the artifact directly. However, driver-enforced feedback should avoid saying the previous turn “failed” when it merely lacked an artifact.

Use wording like:

```text
The previous turn did not complete the phase because the artifact is still missing.
Continue from the same session context.
```

Do not use wording like:

```text
Previous turn failed to leave a valid file.
```

unless the artifact repair budget is actually being consumed after invalid output.

## Implementation Scope

The implementation should be staged.

### Stage 1: Make missing artifact non-fatal within phase

- Introduce `TurnOutcome`, `ArtifactOutcome`, and `PhaseAttemptState`.
- Replace immediate missing-artifact failure with phase-loop continuation.
- Keep existing validators and submitters.
- Keep existing DebateClaw state machine.
- Preserve current command-line options while adding phase-level aliases.

### Stage 2: Structured wrapper turn result

- Extend `openclaw_debate_agent.py` to write a turn-result JSON sidecar.
- Parse transcript tail and stop reason when available.
- Have driver consume sidecar when present and fall back to current behavior when absent.

### Stage 3: Runtime command correctness

- Pass `--runtime-dir` into compact state snapshot prompt command.
- Add a test that materialized prompts point compact snapshot and fallback commands to the same runtime root.

### Stage 4: Coordinator fallback hardening

- Make deterministic protocol primary.
- Treat model refinement as optional and bounded.
- Ensure deterministic valid protocol survives model timeout, abort, or invalid rewrite.

### Stage 5: Diagnostics and benchmark observability

- Add role phase state to run-status.
- Update blocker payloads and cleanup reports to preserve phase executor diagnostics.
- Update tests that currently expect immediate missing-artifact lane failures.

## Files Expected To Change

Primary files:

- `skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
  - Introduce phase executor and state objects.
  - Replace `attempt_model_artifact()` call sites.
  - Reclassify missing artifacts.
  - Add phase-level budgets.
  - Update run-status diagnostics.

- `skills/debateclaw-v1/scripts/openclaw_debate_agent.py`
  - Write structured turn-result sidecar.
  - Preserve current subprocess return behavior for compatibility.

- `skills/chemqa-review/scripts/materialize_runplan.py`
  - Include `--runtime-dir` in compact state snapshot command.

- `skills/chemqa-review/scripts/chemqa_review_state_snapshot.py`
  - Keep existing `--runtime-dir`; tests should verify prompt generation supplies it.

Test files:

- `skills/chemqa-review/tests/test_chemqa_review_runtime.py`
  - Add phase executor tests.
  - Add missing artifact continuation tests.
  - Add invalid/stale artifact repair tests.
  - Add coordinator deterministic fallback tests.
  - Add materialized prompt runtime-dir tests.

Potential support file if `chemqa_review_openclaw_driver.py` becomes too large:

- `skills/chemqa-review/scripts/chemqa_phase_executor.py`
  - Houses `TurnOutcome`, `ArtifactOutcome`, `PhaseAttemptState`, and `RolePhaseExecutor`.

Splitting is recommended if the implementation would make `chemqa_review_openclaw_driver.py` materially harder to review.

## Test Strategy

### Unit tests

1. Normal `stop` without artifact continues the phase.
2. Missing artifact after first turn does not call `record_lane_failure`.
3. Missing artifact after budget exhaustion records `phase_budget_exhausted`.
4. Invalid YAML receives repair feedback and continues while repair budget remains.
5. Stale artifact is rejected when `require_file_change=True`, but continues while budget remains.
6. Timeout with valid artifact on disk salvages the artifact.
7. Timeout with no artifact consumes turn budget and continues if phase budget remains.
8. Coordinator deterministic protocol remains valid when model refinement aborts.
9. Compact snapshot command includes `--runtime-dir`.
10. Existing deterministic placeholder proposal/review paths remain unchanged.

### Integration tests

Use a fake OpenClaw wrapper or monkeypatched `call_model_turn` to simulate:

- turn 1: normal stop, no artifact
- turn 2: normal stop, valid `proposal.yaml`
- expected result: proposal is submitted and phase advances

Use another simulation:

- turn 1: writes invalid YAML
- turn 2: rewrites valid YAML after feedback
- expected result: no lane failure, submitted artifact

Use coordinator simulation:

- deterministic protocol scaffold valid
- model refinement raises timeout or returns aborted
- expected result: deterministic protocol is finalized

### Regression tests

Existing tests that check artifact validation should still pass. Any test that expected immediate `missing proposal.yaml after model turn` should be updated to assert:

- first missing artifact is `phase_incomplete`
- terminal failure occurs only after configured phase budget exhaustion

## Acceptance Criteria

The design is implemented when all of these are true:

1. A normal OpenClaw `stop` without the required artifact no longer records a lane failure on the first turn.
2. The same role phase continues in the same session with explicit feedback.
3. A later turn in the same phase can write the artifact and complete normally.
4. Missing, invalid, stale, timed-out, aborted, and hard-error cases have distinct diagnostic classifications.
5. `proposer-1` candidate generation no longer uses a hard one-turn artifact attempt.
6. `--max-model-attempts` no longer silently means “one chance to write YAML”; phase-level budgets are explicit.
7. Compact snapshot prompt commands include the materialized runtime dir.
8. Coordinator model refinement failure does not destroy or block deterministic final protocol output.
9. Benchmark run-status can show phase executor state for stalled/incomplete role phases.
10. Tests cover the early-exit transcript pattern that originally produced `missing proposal.yaml after model turn`.

## Rollout Plan

1. Implement behind default-compatible behavior but change missing artifact classification immediately.
2. Run focused ChemQA runtime tests.
3. Run a small benchmark smoke test using a fake or low-cost model profile that intentionally stops once before writing.
4. Confirm run-status shows `waiting_for_artifact` rather than `lane_failure`.
5. Run a real single-record ChemQA benchmark and inspect transcripts, blocker files, final artifacts, and cleanup.
6. Update `GLOBAL_DEV_SPEC.md` after implementation because driver execution flow and status semantics will change.

## Risks and Mitigations

### Risk: More turns increase token/runtime cost

Mitigation:

- Use explicit `max_phase_turns` and wall-clock budgets.
- Keep defaults conservative.
- Continue using deterministic paths for transport artifacts.

### Risk: Agent loops without progress

Mitigation:

- Track artifact state, transcript delta, and assistant tail.
- Stop after phase budget exhaustion.
- Classify no-progress loops separately.

### Risk: Existing tests assume immediate failure

Mitigation:

- Update tests to distinguish turn-level incomplete state from phase-level failure.
- Add explicit low-budget tests to preserve failure behavior when budgets are exhausted.

### Risk: Large driver file becomes harder to maintain

Mitigation:

- Extract focused phase executor classes into `chemqa_phase_executor.py` if the patch grows beyond a small local refactor.

### Risk: Wrapper sidecar parsing is brittle

Mitigation:

- Make sidecar optional.
- Fall back to existing return-code behavior.
- Add transcript parsing tests with minimal JSONL fixtures.

## Open Decisions

Recommended defaults for first implementation:

- candidate `max_phase_turns`: 4
- review `max_phase_turns`: 3
- rebuttal `max_phase_turns`: 3
- coordinator refinement `max_phase_turns`: 1
- candidate phase wall timeout: 900 seconds
- review phase wall timeout: 900 seconds
- rebuttal phase wall timeout: 600 seconds
- coordinator refinement wall timeout: 360 seconds

These values should be easy to override from materialized run stop-loss settings.

## Relationship To Existing Designs

This design complements:

- `2026-04-27-chemqa-evaluable-answer-recovery-design.md`
  - That design handles preserving scoreable answers after degraded or failed runs.
  - This design reduces the number of false degraded/failed runs by making role execution robust before recovery is needed.

- `2026-04-28-chemqa-two-layer-artifact-protocol-design.md`
  - That design separates Protocol Flow from Artifact Flow.
  - This design adds the missing execution layer beneath them: phase-scoped production of the typed artifacts consumed by Artifact Flow.

Together, the three designs move ChemQA from transcript-dependent recovery toward durable, typed, phase-aware execution.
