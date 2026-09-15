# Benchmark Runtime 架构优化问题交接文档

状态：`OPEN`

整理日期：2026-09-15

范围：基于当前 benchmark runtime 的只读代码扫描，记录最值得优先处理的
性能、内存、稳定性和可维护性问题，并给出下一次会话可以直接执行的分阶段
重构方案。本文没有修改运行逻辑，也没有执行 benchmark 或性能测试；当前代码
和 [GLOBAL_DEV_SPEC.md](../../GLOBAL_DEV_SPEC.md) 仍是系统现状的唯一来源。

## 1. 接手须知

### 1.1 仓库边界和工作流

- Canonical project root：`/Users/xutao/.openclaw/workspace`
- OpenClaw runtime home：`/Users/xutao/.openclaw`
- 开始实施前必须重新阅读仓库 `AGENTS.md`、`GLOBAL_DEV_SPEC.md`、本文和
  `docs/AGENTS.md`。
- 项目命令使用 workspace 的 `uv run ...` 或 `.venv`，不要使用系统 Python。
- 修改代码后先运行受影响的定向测试，再运行完整测试；通过后提交 Git。
- 若模块边界、执行流、持久化行为或状态契约发生变化，必须同步更新
  `GLOBAL_DEV_SPEC.md`。

本轮扫描结束时工作树干净，基线提交为 `92ef332`（`Remove redundant web search
preflight`）。本轮没有新增代码、配置、测试或运行产物。

### 1.2 当前运行架构

```text
workflow.cli.main
  -> dataset_selection / core.datasets
  -> run-scoped ConfigPool + WorkspaceManager + cancellation
  -> Docker preflight / network / image / orphan recovery
  -> AttemptQueueExecutor
  -> workflow.orchestration.run_group
  -> SingleLLMRunner.run (attempt + retry)
  -> DockerContainerRuntime 或 host subprocess
  -> service.single.openclaw_wrapper
       -> session lifecycle / timeout reminder / finalization rescue
       -> typed RunnerResult
  -> transcript audit + dependency evidence + workspace seal
  -> 独立 scoring worker
       -> JudgeClient 或 verifier-grounded bridge
  -> per-record / progress / results.json / runtime-manifest.json
  -> detached automated analysis
```

关键职责位置：

- 调度和最终聚合：[benchmarking/workflow/cli.py](../../benchmarking/workflow/cli.py:307)
- attempt/retry/scoring 队列：[benchmarking/workflow/attempt_queue.py](../../benchmarking/workflow/attempt_queue.py:83)
- record 结果编排：[benchmarking/workflow/orchestration.py](../../benchmarking/workflow/orchestration.py:103)
- 单一 LLM 生命周期：[benchmarking/service/single/runner.py](../../benchmarking/service/single/runner.py:916)
- Docker 生命周期：[benchmarking/runtime/container_runtime.py](../../benchmarking/runtime/container_runtime.py:294)
- workspace 准备、审计、归档：[benchmarking/runtime/agent_workspace.py](../../benchmarking/runtime/agent_workspace.py:394)
- transcript 审计：[benchmarking/runtime/workspace_audit.py](../../benchmarking/runtime/workspace_audit.py:891)
- 结果落盘：[benchmarking/workflow/run_state.py](../../benchmarking/workflow/run_state.py:272)
- progress 快照：[benchmarking/dashboard/progress.py](../../benchmarking/dashboard/progress.py:61)
- verifier bridge：[benchmarking/runtime/vgb_bridge.py](../../benchmarking/runtime/vgb_bridge.py:275)

现有设计已经具备 attempt 隔离、session ownership、四轴 workspace audit、取消
传播、retry outcome precedence、Docker ownership recovery 和即时 per-record
结果写入。后续优化必须保留这些契约，不能用减少审计、减少证据、共享 live
session、共享 workspace 或放宽容器边界来换取速度。

## 2. 扫描结论

### 2.1 优先级定义

- **P0**：会破坏运行恢复、结果持久化或状态一致性；应先建立保护和观测。
- **P1**：对批量运行的总耗时、峰值内存或评分吞吐有明显影响，并且能提升模块
  边界；需要在不改变分数和审计结论的前提下实施。
- **P2**：局部开销或维护成本，收益明确但不应阻塞 P0/P1。

“提升程度”表示对整个 runtime 架构的改善范围，而不是单个函数的微优化。

### 2.2 问题总表

| ID | 优先级 | 提升程度 | 问题 | 主要证据 |
| --- | --- | --- | --- | --- |
| RT-01 | P0 | 很高 | 运行状态和结果 JSON 不是统一的原子提交模型，且 per-record/result/progress 存在重复写入 | [run_state.py](../../benchmarking/workflow/run_state.py:272)、[progress.py](../../benchmarking/dashboard/progress.py:61)、[cli.py](../../benchmarking/workflow/cli.py:674) |
| RT-02 | P1 | 很高 | 同一 transcript 被完整读取、JSON 解码和扫描多次，增加时间、内存和解析分歧风险 | [agent_workspace.py](../../benchmarking/runtime/agent_workspace.py:933)、[convergence.py](../../benchmarking/core/convergence.py:173)、[attempt_environment.py](../../benchmarking/runtime/attempt_environment.py:297) |
| RT-03 | P1 | 很高 | CLI 长时间保留完整结果对象和序列化副本，最终 aggregate 还会重复构造大 payload | [cli.py](../../benchmarking/workflow/cli.py:576)、[orchestration.py](../../benchmarking/workflow/orchestration.py:329)、[reporting.py](../../benchmarking/core/reporting.py:272) |
| RT-04 | P1 | 高 | verifier-grounded 每条记录重复校验 runtime、启动新 Python 进程，评分吞吐受进程启动和 wheel hash 影响 | [vgb_bridge.py](../../benchmarking/runtime/vgb_bridge.py:237)、[vgb_bridge.py](../../benchmarking/runtime/vgb_bridge.py:289) |
| RT-05 | P2 | 中高 | Docker `wait` 轮询和 workspace archive 多次全树扫描带来大量外部命令和 I/O | [container_runtime.py](../../benchmarking/runtime/container_runtime.py:299)、[agent_workspace.py](../../benchmarking/runtime/agent_workspace.py:584) |

辅助性问题：`WorkspaceAccessPolicy.digest`、container path replacement、skill
tree rendering、dataset payload deep-copy 和 reporting bucket 多次遍历也有缓存或
合并遍历空间，但应放在上述主线之后，避免先做局部优化而掩盖生命周期问题。

## 3. 第一优先：RT-01 持久化一致性与 I/O 放大

### 3.1 问题与影响

`run_state.save_json()` 和 `ProgressWriter._write_json()` 都直接覆盖目标文件。
如果进程在写入中途退出，`state.json`、`wave-*.json`、`runtime-manifest.json`
或 `results.json` 可能留下截断内容。dashboard 和 resume 路径虽然有部分回退，
但这会把“正在写入”误判成“文件损坏”或丢失最后一次状态。

同一条记录当前至少经历这些写入：

1. `run_group()` 完成记录后写入 `per-record/<group>/<record>.json`；
2. CLI 聚合阶段再次写入所有 `per-record`；
3. CLI 最后构造和写入完整 `results.json`；
4. progress 每个事件追加 `events.jsonl` 后又重写完整 `progress/state.json`。

状态列表中的 `completed_records` 在每个事件里复制并线性查重。运行规模增大后，
这部分 I/O 和 Python 对象复制会从常数开销变成接近 O(N²) 的放大。

### 3.2 目标设计

引入统一的 **atomic evidence writer**：

- 临时文件与目标文件位于同一目录；
- 写完后 flush，必要时 fsync，再使用 `os.replace`；
- 目标路径或任一父目录是 symlink 时继续 fail closed；
- 保留现有 JSON schema、缩进、编码和路径布局；
- progress 事件仍即时追加，state 快照可采用短周期 checkpoint，但 terminal、
  cancellation 和每个 wave 完成必须立即刷新。

结果写入引入 **ResultSink** 或等价的最小接口：

- attempt 结束时每条记录只写一次 canonical per-record 文件；
- 只有 reporting reference 或历史 resume 合并确实发生变化时才重写记录；
- `results.json` 从 per-record 文件按确定顺序流式构造，避免同时持有全部
  `GroupRecordResult` 和全部 `asdict()` 副本；
- 保留 `merge_existing_per_record`、group 顺序、record 顺序和 dashboard 优先级。

### 3.3 代码边界

- 主要改动：[benchmarking/workflow/run_state.py](../../benchmarking/workflow/run_state.py)
- 主要改动：[benchmarking/dashboard/progress.py](../../benchmarking/dashboard/progress.py)
- 编排接入：[benchmarking/workflow/orchestration.py](../../benchmarking/workflow/orchestration.py)
- CLI 生命周期：[benchmarking/workflow/cli.py](../../benchmarking/workflow/cli.py)
- 可复用的原子写入参考：[benchmarking/runtime/attempt_finalization.py](../../benchmarking/runtime/attempt_finalization.py:13)

不要在这一步改变 evaluator、RunnerResult 字段、workspace archive manifest 或
dashboard 的业务含义。

### 3.4 验收门槛

- 原子写入测试：写入过程中注入异常，目标文件保持旧版本或不存在，不出现半份 JSON。
- resume 测试：在 per-record 完成后、results 聚合前模拟进程退出，下一次运行能
  正确恢复并保持 record/group 顺序。
- cancellation 测试：第一次取消和 `cancelled_with_errors` 都能写出完整
  progress、results 和 runtime manifest。
- 结果字节级语义测试：重构前后同一固定 fixture 的 per-record 和 aggregate
  JSON 内容等价，允许仅有生成时间字段差异。
- 大量 dummy records 的 I/O 基准：记录写入次数、总字节数和峰值 RSS，确认
  不再重复重写未变化的 per-record 文件。

## 4. 第二优先：RT-02 transcript 单次索引和统一证据消费

### 4.1 问题与影响

一次 attempt 的 transcript 目前至少被以下路径独立处理：

- workspace audit：`read_text().splitlines()` 后 JSON 解码、工具事件匹配和路径审计；
- convergence：再次读取、生成 message 列表，提取答案和错误统计；
- dependency evidence：再次读取、解析 process 事件和安装命令；
- session lifecycle：读取 trajectory 并分析 provider 状态；
- finalization context：冻结 snapshot 后又读取全部行、倒序构造 context。

长 transcript 会产生多份字符串、行列表、dict 和事件列表；不同消费者还可能对
同一异常输入得出不同解析结果。当前实现的安全恢复逻辑很完整，问题在于它们
没有共享解析边界和中间结果。

### 4.2 目标设计

新增 runtime-owned `TranscriptIndex`/`TranscriptReader`（建议放在
`benchmarking/runtime/transcript_index.py`），职责是一次按行读取原始 transcript，
提供以下只读视图：

- tool call/result 配对及行号；
- standalone tool result；
- assistant 可见文本与最新完整答案候选；
- prompt error、tool error、dependency command 和 process terminal event；
- provider lifecycle 所需的最小事件摘要；
- 原始文件 fingerprint、行数、字节数和解析失败位置。

设计约束：

- 不改写原始 transcript，不在索引中存储 secret 或完整外部输出的额外副本；
- 路径投影只在内存视图上应用，仍保留现有 `RuntimePathProjection` 语义；
- 需要 archive recovery 时，可以从归档 transcript 建立新索引；不能把 active
  transcript 当作可变共享上下文；
- audit 的 heredoc、nested substitution、parser recovery 和 unavailable 语义
  必须保持不变；
- 对超大事件使用 bounded excerpt，保留行号和 hash，避免为诊断复制整段 stdout。

### 4.3 代码边界

- 新模块：`benchmarking/runtime/transcript_index.py`
- 适配：[benchmarking/runtime/workspace_audit.py](../../benchmarking/runtime/workspace_audit.py)
- 适配：[benchmarking/runtime/agent_workspace.py](../../benchmarking/runtime/agent_workspace.py)
- 适配：[benchmarking/core/convergence.py](../../benchmarking/core/convergence.py)
- 适配：[benchmarking/runtime/attempt_environment.py](../../benchmarking/runtime/attempt_environment.py)
- 适配：[benchmarking/runtime/session_lifecycle.py](../../benchmarking/runtime/session_lifecycle.py)
- 只读 context 仍由 [benchmarking/core/finalization_context.py](../../benchmarking/core/finalization_context.py) 生成。

不要把 `TranscriptIndex` 变成新的业务状态源；它只能是一次性、可验证、可丢弃的
证据索引。

### 4.4 验收门槛

- 用同一份固定 transcript 对比旧实现和索引实现的：tool event 数量、行号、答案、
  prompt error、依赖安装事件和 provider classification。
- 覆盖普通 JSONL、截断行、缺失 tool result、standalone result、heredoc EOF、
  nested command substitution、path projection 和 archive replay。
- 测量一次 attempt 的 transcript read 次数、解码次数和峰值 RSS；目标是每个
  active transcript 主流程一次线性读取，恢复流程最多对归档来源再读取一次。
- audit 四轴结果必须逐 fixture 等价；任何差异先停止优化并修正契约。

## 5. 第三优先：RT-03 有界内存和增量聚合

### 5.1 问题与影响

CLI 在队列执行过程中保留 `future_map`、`group_results`、`RunnerResult`、
`GroupRecordResult` 和最终 `asdict()` payload。`run_group()` 又会在持久化后继续
持有完整 result。最后 `results.json` 同时保存完整 per-record 内容和 summary。

抽查现有运行产物：单条 per-record JSON 约 69–121 KB，一个 4 题双 group 的
`results.json` 约 855 KB。真实题目产生的 `raw`、tool audit、runner metadata 和
workspace evidence 更大时，峰值内存会随记录数线性增长。

### 5.2 目标设计

- attempt 完成后立即转换为稳定的持久化 payload，释放 runner 原始对象和大型
  临时字符串；
- 运行态只保留必要的 record identity、状态、score 和文件引用；
- aggregate 使用增量 accumulator，按 group/eval_kind/subset 维护计数和数值累计；
- 需要 dashboard 详情时从 per-record 文件加载，不在 CLI 内存中保留所有详情；
- final `results.json` 继续提供兼容的 `results` 数组，但通过确定性迭代器/流式
  writer 生成；
- `gc.collect()` 不作为主要内存控制手段。只有明确验证了 Python 级循环引用或
  native resource 后才保留针对性调用。

### 5.3 代码边界

- [benchmarking/core/reporting.py](../../benchmarking/core/reporting.py)
- [benchmarking/workflow/orchestration.py](../../benchmarking/workflow/orchestration.py)
- [benchmarking/workflow/cli.py](../../benchmarking/workflow/cli.py)
- [benchmarking/workflow/run_state.py](../../benchmarking/workflow/run_state.py)

先做独立 `AggregateAccumulator`，再决定是否调整 `GroupRecordResult` 的运行态和
持久化态。不要直接删字段或压缩历史 JSON。

### 5.4 验收门槛

- 固定输入下 summary、group order、per-record order、score 和 error 计数与现有
  实现一致。
- `merge_existing_per_record=true/false`、历史 schema up-conversion、取消和
  失败结果都要覆盖。
- 用 1k/10k 条轻量 dummy record 做内存基准，确认峰值 RSS 不再随全部详情线性
  增长；同时测量 JSON 输出耗时。
- 不能通过丢弃 `runner_meta`、`raw`、audit findings 或回答全文来降低 RSS。

## 6. 第四优先：RT-04 verifier-grounded 评分进程模型

### 6.1 问题与影响

`evaluate_answer()` 每条记录都会：

1. 检查 wheel size 和 SHA-256；
2. 读取 runtime manifest；
3. 启动新的 isolated Python 进程；
4. 导入 pinned verifier package；
5. 解析 JSON stdout。

这对安全隔离很清晰，但在多题 run 中重复付出 runtime validation、Python startup
和 package import 成本。当前 scoring worker 固定为 1，因此评分阶段会串行承受
全部进程启动时间。

### 6.2 目标设计

分两步实施：

**第一步：invocation 级 immutable validation cache**

- run 开始时验证 release config、wheel fingerprint、runtime manifest 和 runtime
  Python；
- 以 `(release identity, wheel inode/size/mtime or digest, manifest fingerprint)`
  为 key 缓存成功结果；
- 任何 fingerprint 变化都重新验证；异常不缓存为成功。

**第二步：有界 verifier worker（需单独设计审查）**

- scoring worker 向一个 invocation-owned verifier process 发送 JSON request；
- worker 使用 `python -I`、固定 runtime root 和白名单环境；
- request 必须携带 release identity、track、task id 和 answer text；
- worker 崩溃、超时或输出非法 JSON 时由 bridge 产生 typed failure，并允许
  有界重启；不得跨 invocation 复用隐藏状态；
- 仍保留现有独立 wheel API 作为 compatibility/fallback path，直到基准证明 worker
  不改变分数、错误分类和证据。

### 6.3 代码边界

- [benchmarking/runtime/vgb_bridge.py](../../benchmarking/runtime/vgb_bridge.py)
- [benchmarking/scoring/evaluators/verifier_grounded.py](../../benchmarking/scoring/evaluators/verifier_grounded.py)
- [benchmarking/workflow/attempt_queue.py](../../benchmarking/workflow/attempt_queue.py)
- CLI invocation context 负责创建和关闭 worker，不把它放入全局 registry。

### 6.4 验收门槛

- 固定 release 下新旧 bridge 的 score、properties、constraint scores、failure type
  和错误信息等价。
- wheel、manifest、runtime Python 任一 fingerprint 变化时缓存失效。
- worker 超时、崩溃、非法 JSON、取消和重复关闭都能产生可读证据。
- 测量每题评分延迟、整个 scoring 阶段耗时、verifier process 数量和 RSS；只有
  在分数/错误契约等价后才考虑默认切换到 worker。

## 7. 第五优先：RT-05 Docker 等待与 workspace archive 扫描

### 7.1 Docker 等待

`DockerContainerRuntime.collect()` 当前循环执行 `docker wait`，默认每秒一次；
`terminate()` 在最长 420 秒 grace 内同样重复执行 Docker CLI。长题目会产生不必要
的外部进程、JSON/文本管道和 daemon 交互。

建议先做低风险替换：启动一个有界的 `docker wait <id>` client subprocess，
通过 owned process group 监控取消；取消时先终止容器，再停止 wait client。保留：

- 首次取消原因和二次取消加速；
- container supervisor 的 evidence finalization 窗口；
- wait/stop/kill/remove 的 typed timeout 和 cleanup report；
- unresolved container 的后续 orphan recovery。

涉及：[benchmarking/runtime/container_runtime.py](../../benchmarking/runtime/container_runtime.py:294)
及其 Docker contract tests。不要在没有 Docker acceptance 的情况下直接引入新运行时依赖。

### 7.2 Workspace archive

`seal()` 前后会重复执行 runtime tree validation、tree stats、symlink stats；跨文件
系统复制还会再次统计和校验。安全校验不能删除，但可以合并为一个遍历结果对象：

- 同一遍历完成 regular-file count/bytes、symlink manifest、forbidden path 检查；
- copy 后仍对目标树重新验证，源/目标的比较项由 inventory 明确列出；
- 保持 symlink 不解引用、dangling link 规则、sentinel hash 和 fail-closed quarantine；
- 不把 archive metadata 缓存到跨 attempt 的全局对象。

涉及：[benchmarking/runtime/agent_workspace.py](../../benchmarking/runtime/agent_workspace.py:119)、
[benchmarking/runtime/agent_workspace.py](../../benchmarking/runtime/agent_workspace.py:559)。

## 8. 分阶段执行路线

### Phase 0：建立可比较的观测基线

目标：先知道优化是否真的改善了 runtime，不改变业务行为。

工作项：

1. 为 invocation、attempt、audit、archive、score 增加 monotonic duration。
2. 记录 transcript bytes/lines、JSON decode count、Docker command count、VGB
   process count、per-record bytes、peak RSS 和 state write bytes。
3. 为固定小 run 建立 deterministic fixture；至少包括 VGB、skills-on/off、失败、
   retry、取消和大 transcript。
4. 明确目标基线：结果等价、audit 四轴等价、峰值 RSS、wall time、外部命令数。

交付：观测字段、fixture 和报告，不改变现有结果 schema。若新增 runtime manifest
字段，必须更新 `GLOBAL_DEV_SPEC.md`。

### Phase 1：先修持久化，再改变内存生命周期

顺序：RT-01 atomic writer → ResultSink → progress checkpoint → 增量 aggregate。

原因：没有可靠的持久化边界，就不能安全地释放运行态对象，也不能诊断后续性能
变化是否来自丢失结果或错误 resume。

完成标准：异常退出、取消、resume、dashboard fallback 和历史结果读取全部通过。

### Phase 2：统一 transcript 证据消费

顺序：建立 TranscriptIndex → 接入 convergence → 接入 audit → 接入 dependency
 evidence → 接入 rescue/replay。

每次接入都要与旧实现做 fixture-by-fixture 对比。先保留旧 parser 作为 shadow
checker；稳定后再删除重复读取路径。不要在同一个提交里同时修改路径审计规则。

完成标准：一次线性主读取、解析结果等价、任何 audit 差异有明确诊断。

### Phase 3：有界结果内存和聚合

仅在 Phase 1/2 稳定后实施。先让 runner result 在落盘后尽早释放，再引入
AggregateAccumulator 和流式 final results writer。通过 1k/10k dummy records
验证 RSS 曲线；如果详情 payload 本身必须在内存中，报告真实上限而不是强行压缩。

### Phase 4：VGB worker/cache

先上线 invocation validation cache，再以 feature flag 或 compatibility mode 引入
worker。至少完成一轮旧 bridge vs worker 的 shadow score 对比后，才允许改变默认路径。

### Phase 5：Docker 和 archive 微优化

最后处理 Docker wait 和 archive inventory 合并。它们触及取消、孤儿恢复、归档
证据和同用户安全边界，收益应以 Phase 0 的命令数/耗时数据证明后再改。

## 9. 下一会话的具体执行清单

下一次会话应按以下顺序开始，不要直接从微优化着手：

1. 重新阅读 `AGENTS.md`、`GLOBAL_DEV_SPEC.md`、`docs/AGENTS.md` 和本文。
2. 检查 `git status`，确认没有未授权工作树变更。
3. 先实现 Phase 0 的观测 fixture；运行定向测试并记录基线。
4. 进入 RT-01：先改 atomic JSON writer 和故障注入测试，再改 ResultSink。
5. 每完成一个阶段，运行相关测试、检查 `results.json`/manifest schema，并提交
   一个可回滚的 commit。
6. 只有在前一阶段验收通过后，才继续下一阶段；不要把 RT-02、RT-04、RT-05
   混在一个大提交中。
7. 每次行为或模块边界变化后更新 `GLOBAL_DEV_SPEC.md` 对应段落，并在 handoff
   文档中追加“已完成/待完成/验证限制”。

## 10. 明确不做的事情

- 不改变 benchmark prompt、模型选择、评分公式或记录选择逻辑。
- 不通过缩短 timeout、减少 retry、降低并发隔离或跳过 judge 来制造速度提升。
- 不删除 transcript、workspace archive、dependency manifest、audit findings
  或 runtime cleanup evidence。
- 不把 host execution 误称为 OS sandbox，也不扩大当前 cooperative-agent guard
  的安全承诺。
- 不在没有真实基准和契约测试的情况下默认启用持久 verifier worker 或新的
  Docker runtime 依赖。

## 11. 完成定义

本交接可以关闭的条件：

- RT-01 至少完成 atomic persistence、ResultSink 和 resume/cancellation 验收；
- RT-02 至少完成 transcript index 的等价性和一次线性主读取验收；
- RT-03 有固定规模 RSS 和 wall-time 对比，且结果 payload 没有被删减；
- RT-04 若启用 worker，必须有旧/新 bridge 的 shadow score 和故障恢复证据；
- RT-05 的 Docker/archive 改动必须有 cancellation、orphan recovery、archive
  manifest 和跨文件系统测试；
- 全量测试通过，`GLOBAL_DEV_SPEC.md` 与实际代码一致，所有变更已提交 Git；
- 另附一份 `docs/report/` 验收报告，记录实际收益、测试范围和遗留限制。
