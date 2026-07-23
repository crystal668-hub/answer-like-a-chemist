# Benchmark Unattended Completion Diagnosis Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:systematic-debugging` before any code changes. This document is a diagnosis and analysis plan, not an implementation plan. Do not expand scope beyond the bottlenecks that prevent unattended completion of the current benchmark run.

**Goal:** Locate the smallest root-cause fix set that allows the current ChemQA benchmark path to complete without manual recovery intervention or premature execution-failure classification.

**Architecture:** Diagnose the failure as a multi-component runtime problem, not an answer-quality problem. Trace the control path from `benchmark_test.py` into `benchmarking/runners/chemqa.py`, `chemqa_review_openclaw_driver.py`, `recover_run.py`, DebateClaw state, and archived protocol artifacts. Treat `run-status`, protocol YAML, and collected artifacts as separate evidence surfaces and explicitly reconcile them before proposing any fix.

**Tech Stack:** Python 3.12, `benchmark_test.py`, `benchmarking/runners/chemqa.py`, `skills/chemqa-review` runtime scripts, DebateClaw `state.db`, JSON/YAML run-status and artifact collectors, existing `unittest` regression suite.

---

## Problem Statement

当前 benchmark 脚本在运行 `chemqa_web_on` 时，存在“无法在无人工干预情况下正常完成”的瓶颈，表现为：

1. `coordinator` 或 reviewer lane 在 `review` / `rebuttal` 阶段停滞。
2. 运行需要依赖 `recover_run.py` 才能继续推进。
3. 即使恢复后仍可能被 benchmark 统计为 execution fail。
4. 部分 run 已经生成了可归档 protocol / `qa_result.json`，但 benchmark 结果仍被写成失败。

本计划只针对这些运行时瓶颈做诊断与最小修复准备，禁止：

- 扩展 ChemQA workflow 语义
- 改写 acceptance policy
- 做大规模重构
- 把“答题被 reviewer reject”误判成系统 bug
- 因局部异常顺手引入新的平台能力或抽象层

## Scope

### In Scope

- `chemqa_web_on` 在 benchmark 运行中的停滞、恢复、终态判定、结果归档一致性
- `run-status` 写入链路
- `recover_run.py` 的恢复行为和状态语义
- `chemqa_review_openclaw_driver.py` 的停滞检测、respawn、phase repair 行为
- `benchmarking/runners/chemqa.py` 的终态消费与结果分类
- 与上述瓶颈直接相关的最小测试补强

### Out of Scope

- 模型回答内容本身是否足够好
- reviewer 对候选答案的 blocking 是否合理
- protocol 设计是否“更优”
- benchmark 统计口径的产品化调整
- 对 single-LLM 路径的非必要改动

## Current Evidence Baseline

以下事实在进入修复前应视为已发现、但仍需复核为可复现实证：

1. `recover_run.py` 在 `--max-steps` 用尽且 run 尚未真正完成时，会写入 `status=done`、`terminal_state=failed`、`terminal_reason_code=stalled`。
2. `chemqa_review_openclaw_driver.py` 在停滞时调用 `recover_run.py --max-steps 1`，因此一次“单步恢复未完成”可能污染全局终态。
3. `benchmarking/runners/chemqa.py` 只要读到非 success terminal status，就直接把 run 记为失败，即使归档目录里已经存在有效 protocol 或 `qa_result.json`。
4. `recover_run.py` 对 formal review / rebuttal 的恢复主要依赖“复用已有 artifact”，对“artifact 缺失或 schema 不合法”的场景缺乏真正的再生成路径。
5. 停滞预算较低，可能把瞬时异常直接放大成 terminal failure。

这些结论在修复前必须用单次可复现 run 和时间线证据重新确认，不能仅凭历史观察直接编码。

## Diagnosis Principles

1. 先确认哪个组件先写出了错误状态，再讨论修复。
2. 先区分“系统卡死”和“答案被拒绝”，再决定是否需要修复。
3. 任何 fix 必须直接对应一个可定位的根因，不接受“顺手优化一批相关代码”。
4. 优先修正状态机语义，其次修正恢复路径，最后才调整预算参数。
5. 如果某个问题可以通过更小的约束校正解决，不做机制扩张。

## Working Hypotheses

### H1: Recovery writes false terminal failure

`recover_run.py` 把“尚未完成的恢复中间态”错误写成全局 terminal fail，导致 runner 过早结束并记 execution error。

### H2: Required formal review / rebuttal cannot self-heal

当 reviewer 或 proposer-1 留下无效 YAML 或根本没有留下 artifact 时，恢复逻辑只能报 blocker，不能让 lane 真正补齐需要的 artifact，导致 coordinator 持续停滞。

### H3: Repair / respawn budget is too low

当前 stale/recovery/respawn 阈值过低，导致短暂故障还没完成自恢复就被升级为 failure。

### H4: Runner classification is stricter than archived evidence

即使 protocol 已完成并可收集，runner 仍只相信早先的 `run-status`，没有对账归档证据，最终把“完成但 rejected”错算成 execution fail。

## Diagnostic Tasks

### Task 1: Reconstruct Ground Truth Timeline for Representative Runs

**Objective:** 为至少三个代表 run 建立逐层时间线，明确“谁先写错、谁后继续推进、谁最终被 benchmark 消费”。

**Representative runs:**

- `benchmark-chemqa_web_on-conformabench-0004-20260425-205916`
- `benchmark-chemqa_web_on-conformabench-0005-20260425-210447`
- `benchmark-chemqa_web_on-conformabench-0001-20260425-202952`

**Evidence surfaces to align:**

- `workspace/state/benchmark-runs/.../per-record/chemqa_web_on/*.json`
- `workspace/state/benchmark-runs/.../artifacts/.../chemqa_review_protocol.yaml`
- `workspace/skills/chemqa-review/control/run-status/<run-id>.json` if still available
- cleanroom cleanup report
- archived session logs and blocker files if present

**Questions to answer:**

1. 第一个 terminal/failure 状态是谁写入的？
2. terminal/failure 写入时，DebateClaw engine 实际 phase 是什么？
3. failure 写入后，protocol 是否仍继续生成或被归档？
4. runner 消费的是哪份状态，是否早于最终 protocol？

**Exit criteria:** 对每个样本 run 形成一段按时间排序的事件链，能够指出“错误终态写入点”和“最终归档证据点”。

### Task 2: Reproduce One Stalled Run Under Minimal Load

**Objective:** 用最小数据量复现“需要 recovery 或提前 fail”的路径，避免仅依赖历史残留产物分析。

**Recommended reproduction shape:**

- 只跑 `chemqa_web_on`
- 只跑 1 个 `conformabench` item
- 使用独立输出目录，避免旧产物干扰

**What to capture during reproduction:**

- `run-status` 文件每次变更
- coordinator `next-action` / `status` 摘要
- `recover_run.py` 返回 payload
- phase signature 变化
- respawn 事件
- protocol 和 `qa_result.json` 的生成时机

**Exit criteria:** 至少捕获一次“人工本应不用干预，但系统自己没完成”的复现场景；如果无法复现，必须解释为何历史产物不能代表当前代码。

### Task 3: Audit Every Writer to `run-status`

**Objective:** 明确哪些脚本会写 `skills/chemqa-review/control/run-status/<run-id>.json`，以及它们写入的状态语义是否彼此兼容。

**Writers to inspect first:**

- `workspace/skills/chemqa-review/scripts/recover_run.py`
- `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
- any launch/finalize path that marks completion or artifact collection

**For each writer, record:**

- 何时写 `status`
- 是否会写 `terminal_state`
- 该状态是“引擎终态”还是“恢复观察结果”
- 是否可能覆盖更权威的终态

**Exit criteria:** 产出一张 writer matrix，明确唯一权威终态来源应是谁，其他 writer 是否只能写 running/recovery diagnostics。

### Task 4: Validate Formal Review / Rebuttal Recovery Failure Modes

**Objective:** 区分 transport 正常补位与 formal review/rebuttal 无法恢复这两类路径。

**Focus paths:**

- malformed formal review: 文件存在，但 schema 不合法
- missing formal review: 文件不存在，review lane 当前不可用
- missing rebuttal: proposer-1 在 rebuttal 前退出或未留下文件
- transport placeholder path: 是否会误计入 acceptance 或误阻塞 phase

**Concrete questions:**

1. recovery 是否能修复 malformed formal review，还是只能记录 blocker？
2. recovery 是否有能力触发 lane 重新生成 formal review / rebuttal？
3. 进程 respawn 后，worker loop 会不会自动继续补齐 artifact？
4. 哪些 blocker 是真正不可恢复，哪些只是“恢复脚本没有继续做下一步”？

**Exit criteria:** 列出每种失败模式的实际处理结果，并标记“可通过最小代码修复”还是“需要更大设计调整”。

### Task 5: Check Stagnation Thresholds Against Real Recovery Latency

**Objective:** 判断失败是否源于逻辑错误，还是仅仅因为预算太小。

**Parameters to audit:**

- `stale_timeout_seconds`
- `phase_repair_budget`
- `lane_retry_budget`
- `max_respawns_per_role_phase_signature`
- `recover_run.py --max-steps`

**Method:**

- 用复现 run 中真实的 artifact 生成时延、respawn 到可见进展的时延做对比
- 检查当前阈值是否小于正常恢复所需时间

**Decision rule:**

- 如果状态语义错误已足以解释 fail，不优先改预算
- 只有在状态语义正确但恢复需要更长时间时，才考虑小幅调参

**Exit criteria:** 给出“预算是否是主因/次因/非因”的明确结论。

### Task 6: Reconcile Runner Classification with Archived Artifacts

**Objective:** 确认 benchmark runner 是否把已经完成归档的 run 错算为 execution_error。

**Checks:**

- 若 `run-status` 为 failed，但 archive 中有合法 `chemqa_review_protocol.yaml` / `qa_result.json`，runner 当前怎么分类
- protocol 的 `terminal_state` 与 `run-status.terminal_state` 不一致时，当前优先级是什么
- “completed + rejected” 是否应被记为 0 分完成，而不是 execution_error

**Exit criteria:** 明确 runner 的错误分类边界，并判定是否需要引入一条最小 reconciliation 逻辑。

## Minimal Fix Decision Tree

只有完成前述诊断任务后，才允许选择修复入口。选择规则如下：

1. **如果 H1 成立：**
   - 第一优先级修 `recover_run.py` 的状态写入语义
   - 原则：恢复脚本不得把非引擎终态写成 terminal done/failed

2. **如果 H2 成立且 H1 已修：**
   - 第二优先级补 formal review / rebuttal 的最小再生成恢复路径
   - 原则：只覆盖当前卡死的 artifact 类型，不扩展通用工作流能力

3. **如果 H3 单独成立：**
   - 仅做最小参数修正
   - 禁止顺手改其他策略

4. **如果 H4 成立：**
   - 在 runner 中增加最小对账逻辑
   - 原则：只在 archived protocol / `qa_result.json` 已存在且合法时兜底，不重写主流程

## Planned Test Additions After Root Cause Confirmation

以下测试仅在根因确认后补充，不提前编码：

1. recovery 单步返回未完成时，不得污染全局 `run-status` 为 `done/failed`
2. malformed formal review 不应直接导致 benchmark execution fail；应先进入可恢复路径
3. proposer-1 在 rebuttal 前退出时，系统应能在无人干预下完成恢复或明确进入真实 terminal failure
4. archived protocol 已完成但 `run-status` 曾被错误写成 failure 时，runner 不得再把该 run 记为 execution_error

## Verification Plan for the Eventual Minimal Fix

修复后验证只做三层：

1. **Unit/regression tests**
   - 覆盖根因对应脚本与 runner 分类逻辑

2. **Single-record unattended smoke**
   - 单题 `chemqa_web_on`
   - 不允许人工调用 recovery
   - 运行结束后检查 run-status、protocol、`qa_result.json` 一致性

3. **Small benchmark wave**
   - 小规模 `conformabench` 子集
   - 验证不会再出现“中途已归档但最终被 execution fail”这一类 split-brain

## Success Criteria

本计划完成后的修复验收标准必须同时满足：

1. benchmark 在目标复现场景下无需人工手动执行恢复脚本
2. 非真正终态的恢复动作不会把 run 过早写成 failed
3. formal review / rebuttal 的可恢复故障不会立即升级为 execution fail
4. 已完成并归档的 protocol 不会再被 runner 错算成 execution_error
5. 实现改动局限在最小必要文件和测试，未引入额外功能面

## File Map for Diagnosis Work

**Primary code paths**

- `workspace/benchmark_test.py`
- `workspace/benchmarking/runners/chemqa.py`
- `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
- `workspace/skills/chemqa-review/scripts/recover_run.py`
- `workspace/skills/chemqa-review/scripts/chemqa_review_artifacts.py`
- `workspace/skills/chemqa-review/scripts/control_store.py`

**Primary evidence paths**

- `workspace/state/benchmark-runs/<run>/per-record/chemqa_web_on/*.json`
- `workspace/state/benchmark-runs/<run>/artifacts/chemqa_web_on/**/chemqa_review_protocol.yaml`
- `workspace/state/benchmark-runs/<run>/artifacts/chemqa_web_on/**/qa_result.json`
- `workspace/state/benchmark-runs/<run>/cleanroom/reports/*.cleanup-report.json`
- `workspace/skills/chemqa-review/control/run-status/*.json`

## Notes for Implementation Phase

- 修复阶段必须单点切入，禁止同时改 runner、recovery、driver 的多处语义，除非诊断已经证明它们构成同一条因果链且无法拆开。
- 如果第一处最小修复已经使 unattended completion 恢复，则停止继续扩面。
- 如果修复过程中发现第三类根因，先更新本计划或补充一份更小的 follow-up plan，再继续编码。
