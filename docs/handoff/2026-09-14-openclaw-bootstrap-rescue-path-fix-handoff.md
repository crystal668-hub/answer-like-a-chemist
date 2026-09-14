# OpenClaw Benchmark Bootstrap、Rescue Context 与路径投影修复交接文档

状态：`CLOSED`

整理日期：2026-09-14

范围：对 session race 与历史 timeout 修复后的真实 GPT-5.6 SOL benchmark
run 进行后续修复。本文记录已经确认的诊断事实、用户批准的架构约束、待实施
改动和验收要求。它是实施交接，不描述已经完成的功能；当前代码和
`GLOBAL_DEV_SPEC.md` 仍是现状来源。

## 1. 接手基线

### 1.1 仓库与工作流

- Canonical project root：`/Users/xutao/.openclaw/workspace`
- 运行时 home：`/Users/xutao/.openclaw`
- 开始修改前必须阅读仓库 `AGENTS.md`、`GLOBAL_DEV_SPEC.md`、本文，以及
  `docs/AGENTS.md`。
- 项目命令使用 workspace 的 `uv run ...` 或 `.venv`，不要使用系统 Python。
- 当前基线提交：`8a17ae0 fix: isolate OpenClaw session outcomes`
- 本轮交接创建前工作树干净，分支 `master` 相对 `origin/master` ahead 5。
- 修改代码后必须运行相关测试和全量测试；测试通过后提交 Git。

### 1.2 前序修复

前序修复和验收见：

- [OpenClaw Session Race 与历史 Timeout 误重试修复交接文档](2026-09-13-openclaw-session-race-timeout-fix-handoff.md)
- [OpenClaw Session Race and Timeout Retry Fix Validation](../report/2026-09-13-openclaw-session-race-timeout-fix-validation.md)

前序修复已经实现：

- attempt outcome 优先于历史 convergence diagnostics；
- 成功或 recovered 答案不会因历史 timeout 重试；
- `openclaw_session_takeover` typed classification；
- wrapper session owner mutex、lifecycle journal 和 transcript snapshot；
- Docker stdout、stderr、cleanup 和 lifecycle evidence 归档。

本次不得退回到共享 live session writer、删除 idle watchdog、用 generic
subprocess error 覆盖原始错误，或放宽 workspace/verifier 安全边界。

## 2. 真实模型验证 run

本次诊断基线：

`state/benchmark-runs/formal/vgb-property-calculation-advanced/gpt-5-6-sol/verifier-grounded-property-calculation-gpt-5-6-sol-20260913-105823`

运行配置：

- 模型：`openai/gpt-5.6-sol`
- 数据集：`vgb-property-calculation-advanced`
- 2 道题 × skills-on/off，共 4 个执行单元
- `max_concurrent_attempts=2`
- 单题预算 7200 秒
- timeout retry 上限 3
- Docker image：
  `sha256:ba72e64e150da19d3081ed7c91e153bff1f8f5c8aa95253f1d640374f31dc12c`
- provider connectivity：`ready`，HTTP 404 仅表示探测端点可达

结果：

| Group | Record | Runner outcome | Scoreable |
| --- | --- | --- | --- |
| skills-on | free energy | completed/native | yes |
| skills-on | crystal phase | completed/native | yes |
| skills-off | crystal phase | completed/native | yes |
| skills-off | free energy | failed/none | no |

所有 4 个执行单元均满足：

- 只有 1 个 benchmark attempt；
- `takeover_detected=false`；
- 没有 `EmbeddedAttemptSessionTakeoverError`；
- 没有 `LLM idle timeout` 或历史 timeout retry；
- container return code 为 0；
- 无 OOM、Docker 外层 timeout 或 cancellation；
- `container_cleanup.removed=true`；
- session/workspace audit 为 `complete / clear / scoreable`；
- owner lock 已释放并清空；
- primary transcript snapshot 可读。

因此，前序 session ownership/outcome 修复在本轮并发真实模型运行中没有复发。
本 run 没有自然覆盖“成功答案 + 历史 timeout”或 provider timeout，相关保证仍
由前序 deterministic tests 提供。

## 3. 已确认的新问题

### 3.1 Benchmark workspace 被 OpenClaw 首次启动 bootstrap 污染

失败执行单元：

`single_llm_skills_off / property_calculation_advanced_001_free_energy`

归档 workspace 中出现了模板未提供的文件：

- `BOOTSTRAP.md`
- `SOUL.md`
- `USER.md`
- `IDENTITY.md`
- `HEARTBEAT.md`
- `openclaw-workspace-state.json`

当前 canonical templates 实际只声明：

- `single-llm-skills-on-v1`：合成的 `AGENTS.md` 与显式 `TOOLS.md`
- `single-llm-skills-off-v1`：仅合成的 `AGENTS.md`

对应代码位于：

- `benchmarking/runtime/agent_workspace.py::default_workspace_templates`
- `benchmarking/resources/agent-workspace-templates/`

OpenClaw 2026.6.9 在首次运行时自动 seed 了 profile/bootstrap 文件。run-scoped
和 container config 都没有强制 `agents.defaults.skipBootstrap=true`。

失败 primary transcript 的直接证据：模型已进行工具调用和部分计算，随后把
`BOOTSTRAP.md` 视为需要完成的首次启动指令，最终输出：

```text
Hey. I just came online. Who am I, and who are you?
```

该文本与归档 `BOOTSTRAP.md` 第 13 行的示例完全一致。OpenClaw 自身把这次
turn 记为 `success`、`timedOut=false`、`idleTimedOut=false`；benchmark runner
正确将其判为无可评分答案：

```text
current_failure_code=agent_response_unavailable
answer_source=none
retry_decision=no_retry
historical_prompt_errors=[]
```

### 3.2 独立 finalization rescue session 没有 primary context

前序 session race 修复让所有 wrapper follow-up 都分配新 session。这个方向对
writer 隔离是正确的，但 finalization rescue prompt 仍假定“已有推理存在于当前
session”。实际 rescue 使用：

`<primary-session-id>-finalization_rescue-1`

新 transcript 只有 generic rescue prompt，没有原始题目、答案 schema 或
primary transcript 内容。模型因此返回：

```text
I can’t produce a verifier-grounded final answer because the original question,
prior reasoning, verification results, and required output format are not present
in the available context.
```

当前 rescue 结果正确保留为失败，但 rescue 本身无法发挥“已有输出的格式纠正”
作用。

### 3.3 Container 路径投影会误匹配相似 host 路径

`SingleLLMRunner._translate_container_paths()` 当前依次执行无边界
`str.replace()`：

```python
value.replace("/benchmark/session", ...)
translated.replace("/benchmark/workspace", ...)
```

当第一步已经生成 host 路径，例如：

`/home/.../benchmark/workspaces/.../scratch/session/...`

第二步会把其中的 `/benchmark/workspace` 当作 container prefix 再次替换，
产生重复、不可读的路径。该错误影响本 run 的所有
`runner_meta.session_lifecycle.session_path` 和 invocation session paths。

现有归档中的以下结构化路径是有效的：

- `container.stdout_path`
- `container.stderr_path`
- `container.session_lifecycle_path`
- lifecycle `owner_released.snapshots[*].path`

以下字段仍可能指向已经 seal 后移走的 active workspace：

- `agentMeta.sessionFile`
- `convergence.transcript_path`
- `session_isolation.postflight_entry_session_file`
- workspace recovery transcript path

因此核心 evidence 没有丢失，但部分 runner metadata 不能直接用于复核。

## 4. 用户确认的架构约束

以下决策已经由用户确认，接手者不得重新解释为其他方案。

### 4.1 Workspace 文件集合

- Benchmark run config 和 container config 都必须禁用 OpenClaw 首次启动
  bootstrap。
- skills-on agent workspace 的 agent template 文件只能是：
  `AGENTS.md`、`TOOLS.md`。
- skills-off agent workspace 的 agent template 文件只能是：`AGENTS.md`。
- `.benchmark-workspace.json` 和 `scratch/` 是 workspace manager 控制面，
  不属于 agent template 文件集合。
- 不增加 postflight template drift 检查，不引入
  `workspace_template_drift`，也不通过事后扫描/fail-closed 实现约束。
- 正常通过 OpenClaw 支持的 `skipBootstrap` 配置阻止额外 profile/bootstrap
  文件生成。
- 历史 run 保持只读，不回写或删除已有文件。

### 4.2 Finalization rescue 的语义与隔离

- Finalization rescue 仅用于：primary 已经产生答案输出，但输出不满足当前
  answer schema 或可评分格式时，纠正答案形式。
- 无输出、timeout、transport error、OpenClaw process failure 不进入
  finalization rescue；它们继续走 current typed failure 和常规 benchmark
  retry policy。
- Rescue 必须保留新的 session id 和新的 transcript，避免重新进入 primary
  session writer/cleanup 竞态。
- Primary 返回且 OpenClaw PID 结束后，wrapper 必须先冻结 primary transcript，
  再构建 rescue context，再启动 rescue。
- Rescue 通过显式受限 context bundle 获取原始任务、答案 schema 和必要的
  primary evidence；不能假设新 session 自动拥有旧 session context，也不能
 读取 live primary transcript。

### 4.3 路径替换

- Container-to-host 映射必须使用正确的前缀正则和路径边界。
- 必须规避 `/benchmark/sessions`、`/benchmark/workspaces`、路径中间片段和
  已经翻译过的 host path。
- 每个字符串最多应用一次映射。
- 原始 stderr、错误消息、matched error line 等诊断文本不得重写。

## 5. 目标设计

### 5.1 Config 双层禁用 bootstrap

在 run-scoped config 生成处强制：

```json
{
  "agents": {
    "defaults": {
      "skipBootstrap": true
    }
  }
}
```

首选入口：

- `benchmarking/runtime/config.py::render_run_config`

在 Docker container config 物化处再次强制同一字段，防止调用方传入未规范化
的 source config：

- `benchmarking/runtime/container_runtime.py::materialize_container_config`

必须保留 `agents.defaults` 中已有 model、alias、compaction、thinking 等配置，
只设置 `skipBootstrap=true`。不能替换整个 `agents.defaults`。

### 5.2 Rescue 触发状态机

规则顺序：

1. 解析 primary native output。
2. 从已经结束的 primary transcript 恢复完整答案；若成功，直接 recovered。
3. 判断 current primary output 是否为非空的 assistant answer candidate。
4. 若 candidate 已满足 schema，completed，不 rescue。
5. 若 current failure 是 timeout、transport/process/session error，按 typed policy
   处理，不 rescue。
6. 仅对“有非空输出但格式/contract 不完整”的 candidate 构建 rescue bundle。
7. Rescue 使用新 session；完整 rescue answer 为 recovered，不完整 rescue 保留
   primary failure 和 rescue diagnostics。

不能因为 transcript 中存在 thinking/tool calls 就把空 final assistant output
视为“已有答案输出”。触发判断应针对 native payload/最终 assistant text。

### 5.3 Primary transcript freeze

Session supervisor 增加显式冻结动作，时序为：

```text
primary PID exits
  -> inspect/fingerprint primary session
  -> atomically copy primary transcript snapshot
  -> record primary_snapshot_frozen event + SHA-256
  -> build rescue context only from snapshot
  -> allocate rescue session id
  -> start rescue PID
```

冻结应复用 `SessionLifecycleSupervisor` 的原子 snapshot 能力，避免再实现一套
live-copy 协议。Snapshot metadata 至少包含：

- source session id
- frozen snapshot path
- SHA-256
- byte size
- source fingerprint
- frozen timestamp

Final `owner_released` 仍可执行幂等 snapshot 收尾，但不能覆盖 rescue 使用过的
primary snapshot 或改变其 digest。

### 5.4 受限 rescue context bundle

建议新增纯数据模块：

`benchmarking/core/finalization_context.py`

也可放在 wrapper 内，但必须保持提取逻辑可单测且不依赖 live process。

建议数据形状：

```text
FinalizationContextBundle
  schema_version
  source_session_id
  source_snapshot_sha256
  eval_kind
  answer_schema
  original_task
  primary_native_output
  evidence_events[]
  omitted_event_count
  included_chars
  max_chars
```

`evidence_events` 只允许：

- 可见 assistant text；
- tool call 的 tool name 和任务相关的必要参数；
- 对应 tool result 的可见文本；
- 时间顺序和 tool call/result pairing 标识。

明确排除：

- system prompt；
- `AGENTS.md`、`TOOLS.md` 和任何 workspace bootstrap/profile 内容；
- 环境变量和 provider credentials；
- hidden verifier、reference answer 和 scoring config；
- encrypted reasoning、thinking signature、provider replay metadata；
- OpenClaw internal metadata 和大段 token/usage report；
- host/container 绝对路径；
- 与答案形式纠正无关的 binary/media payload。

Bundle 必须有固定字符预算。原始任务、eval kind 和 answer schema 为必选且不可
静默截断；若它们本身超过安全预算，应跳过 rescue 并产生 typed diagnostic。
其余 evidence 从最近的相关事件向前选取，最终恢复为正序。记录被省略事件数和
截断信息，不能让截断后的 tool call/result pairing 失配。

Bundle 应原子写入：

`scratch/notes/finalization-rescue-context.json`

Runner metadata 和 lifecycle 记录路径、digest、included/omitted counts，但不把
整个 bundle 再复制进 results metadata。

Rescue prompt 直接包含该 bundle 的受限、可读投影；rescue agent 不读取旧
session 文件，不调用工具，只根据 bundle 纠正最终答案格式。

### 5.5 Session lifecycle 表达

一次 benchmark attempt 的 lifecycle 可含：

- primary invocation：primary session/PID
- optional rescue invocation：new rescue session/PID

两者：

- 共用 attempt id 和 wrapper owner lifecycle；
- 使用不同 session id、session path 和 OpenClaw PID；
- 时间区间不得重叠；
- rescue 必须引用已经冻结的 primary snapshot digest；
- `takeover_detected` 和每个 invocation 的 provider/error evidence 均保留。

新增 lifecycle events：

- `primary_snapshot_frozen`
- `rescue_context_built`
- 现有 `followup_allocated`、`openclaw_started`、`openclaw_finished` 可继续使用

### 5.6 边界安全路径投影

替换 `_translate_container_paths()` 中的 substring replacement。匹配规则：

```regex
^/benchmark/session(?=/|$)
^/benchmark/workspace(?=/|$)
```

实现要求：

- 仅对“结构化路径字段”调用映射函数；不要递归改写任意 diagnostic string。
- 精确路径或以 `/` 为边界的 descendant 才匹配。
- 选择最长、唯一的 source prefix，每个值只映射一次。
- `/benchmark/sessions`、`/benchmark/workspaces`、
  `/x/benchmark/session` 保持原样。
- host target 本身包含 `/benchmark/workspaces` 时不会二次匹配。
- 路径映射函数建议接收 source→target mapping，并用 compiled regex 或
  `PurePosixPath` 前缀判定；测试必须证明相似路径安全。

Container payload 进入 host 后，至少映射：

- structured `agentMeta.sessionFile`
- structured `session_isolation` paths
- structured `convergence.transcript_path`
- structured lifecycle session/snapshot paths

Workspace seal 后，再将 active workspace prefix 映射到 archive workspace，覆盖：

- `container.stdout_path`
- `container.stderr_path`
- `container.session_lifecycle_path`
- lifecycle session/snapshot/context paths
- `agentMeta.sessionFile`
- `convergence.transcript_path`
- `session_isolation.postflight_entry_session_file`
- workspace recovery transcript path

原始 stderr、stdout excerpts、matched error evidence 和 transcript 内容保持原样。

## 6. 分阶段实施计划

### Phase 0：先补失败测试

- [x] Run config 即使输入 `skipBootstrap=false`，输出仍为 true，其他
      `agents.defaults` 字段不变。
- [x] Container config 二次强制 `skipBootstrap=true`。
- [x] Default skills-on template 精确为 `AGENTS.md`、`TOOLS.md`；skills-off
      精确为 `AGENTS.md`。
- [x] Finalization rescue 只在 primary native assistant output 非空且 contract
      不完整时触发。
- [x] 空 payload、timeout、transport failure、process failure、session takeover
      均不触发 rescue。
- [x] Primary snapshot 在 rescue invocation start 前写出并记录 digest。
- [x] Rescue 使用新 session id，context bundle 含原题/schema/必要 evidence。
- [x] Bundle 不含 secrets、env、system prompt、encrypted reasoning、hidden
      verifier 或绝对路径。
- [x] Bundle 字符预算和 tool call/result pairing 在截断时稳定。
- [x] 路径测试覆盖正确 prefix、相似路径、host target 中含
      `/benchmark/workspaces`、单次映射、diagnostic text 不变。

### Phase 1：Config 和 workspace 模板契约

- [x] 修改 `benchmarking/runtime/config.py`。
- [x] 修改 `benchmarking/runtime/container_runtime.py`。
- [x] 保持 `default_workspace_templates()` 的 skills-on/off 文件声明不扩张。
- [x] 更新 config/template tests。

### Phase 2：Primary snapshot 与 context bundle

- [x] 在 `session_lifecycle.py` 增加幂等 primary snapshot freeze API。
- [x] 实现 snapshot SHA-256 和 metadata。
- [x] 实现纯 context bundle 提取和脱敏。
- [x] 原子持久化 `finalization-rescue-context.json`。
- [x] lifecycle 记录 snapshot/context 构建时序。

### Phase 3：Rescue 触发和独立 session

- [x] 收窄 `merge_convergence_metadata()` 的 rescue eligibility。
- [x] 把 `args.message`、`eval_kind`、answer schema、primary native output 和
      frozen transcript evidence 交给 bundle builder。
- [x] 继续使用 `allocate_followup_session("finalization_rescue")`。
- [x] Rescue prompt 只引用显式 bundle，不宣称新 session 已有旧 context。
- [x] 保持 timeout/current execution error 的 retry semantics。

### Phase 4：路径投影

- [x] 用边界安全映射替换两次 `str.replace()`。
- [x] 限定结构化路径字段，保护原始诊断文本。
- [x] seal 后统一重写所有 path-bearing runner metadata。
- [x] 验证每个预期归档文件路径实际存在。

### Phase 5：验证、规范和提交

- [x] 运行聚焦测试。
- [x] 运行 `uv run pytest -q` 全量测试。
- [x] 运行 changed-file Ruff/compile/diff checks。
- [x] 重建 `openclaw-benchmark-single-llm:latest`。
- [x] 运行无模型 Docker contract/integration tests。
- [x] 使用失败题
      `property_calculation_advanced_001_free_energy` 和 GPT-5.6 SOL 做
      skills-off 定向真实模型复跑。
- [x] 再做 skills-on/off、`max_concurrent_attempts=2` 验收。
- [x] 更新 `GLOBAL_DEV_SPEC.md` 的当前实现描述。
- [x] 在 `docs/report/` 新增验收报告，并把本文状态改为 `CLOSED`。
- [x] 更新 `docs/README.md` 的 report/handoff 计数和链接。
- [x] 确认没有 benchmark 容器、进程或非空 owner lock 残留。
- [x] 提交 Git。

## 7. 预期文件边界

优先修改：

- `benchmarking/runtime/config.py`
- `benchmarking/runtime/container_runtime.py`
- `benchmarking/runtime/session_lifecycle.py`
- `benchmarking/service/single/openclaw_wrapper.py`
- `benchmarking/service/single/runner.py`
- `tests/test_benchmark_test.py`
- `tests/test_container_runtime.py`
- `tests/test_agent_workspace.py`
- `tests/test_single_llm_session_wrapper.py`
- `tests/test_single_llm_session_lifecycle.py`
- `tests/test_attempt_outcome.py` 或新的 path projection test

可能新增：

- `benchmarking/core/finalization_context.py`
- `tests/test_finalization_context.py`
- `tests/fixtures/single_llm/finalization-context-*.jsonl`
- `docs/report/2026-09-14-openclaw-bootstrap-rescue-path-fix-validation.md`

通常不应修改：

- `benchmarking/workflow/attempt_queue.py`：本次没有 retry queue 错误证据。
- `benchmarking/core/attempt_outcome.py`：现有 outcome 判定符合本 run 事实。
- `benchmarking/runtime/error_capture.py`：takeover/provider 分类没有复发。
- 正式 dataset、provider credentials、verifier package、评分公式和 Docker
  mount/security policy。

## 8. 验收矩阵

| 场景 | 预期结果 |
| --- | --- |
| skills-on fresh workspace | agent template 仅 `AGENTS.md`、`TOOLS.md` |
| skills-off fresh workspace | agent template 仅 `AGENTS.md` |
| OpenClaw first run | `skipBootstrap=true`，不生成 bootstrap/profile 文件 |
| primary 完整 schema answer | completed，不 rescue，不 retry |
| primary 非空答案但格式错误 | 冻结 primary，构建 bundle，新 session rescue |
| rescue 成功 | recovered，`answer_source=rescue`，不 retry |
| rescue 输出仍不完整 | 保留 primary failure 和 rescue diagnostics |
| primary 空输出 | 不 rescue，按 current failure/retry policy |
| provider/idle/transport timeout | 不 rescue，typed timeout，按 policy retry |
| session takeover | 不 rescue，typed session error，保留原始 evidence |
| rescue lifecycle | primary/rescue session 与 PID 不同，时序不重叠 |
| context bundle | 原题/schema/必要 evidence 可用，敏感和内部内容缺失 |
| `/benchmark/session/...` | 映射到正确 host/archive session path |
| `/benchmark/workspace/...` | 映射到正确 host/archive workspace path |
| `/benchmark/sessions`、`/benchmark/workspaces` | 保持不变 |
| 原始 stderr 中含 container path | 原文保持不变 |
| seal 后 path-bearing metadata | 指向实际存在的 archive 文件 |
| skills-on/off 并发 2 | 无跨 attempt session writer/takeover |

## 9. 完成条件

只有同时满足以下条件才可关闭本文：

- 双层 config 均强制 `skipBootstrap=true`；
- skills-on/off workspace 文件集合符合用户确认的精确契约；
- finalization rescue 只处理非空但格式不合格的 primary output；
- rescue 使用新 session，并只消费 frozen primary snapshot 派生的受限 bundle；
- bundle 脱敏、预算、pairing 和 digest tests 通过；
- 相似路径不再被误替换，所有结构化归档路径可读；
- session takeover、历史 timeout、provider timeout 和常规 retry 回归仍通过；
- 全量测试和 Docker contracts 通过；
- GPT-5.6 SOL 定向真实模型复跑通过或留下可解释、与本修复无关的 typed
  failure evidence；
- `GLOBAL_DEV_SPEC.md` 和验收报告与代码一致；
- 无残留容器、进程或非空 owner lock；
- 所有代码、测试和文档已提交 Git。

## 10. 新会话第一步

1. 读取本文和第 1.2 节的前序文档。
2. 用 `jq`/`rg` 重现第 3 节三个问题，不修改历史 run。
3. 先添加 Phase 0 regression tests 并确认它们在当前实现上失败。
4. 按 Phase 1→4 的依赖顺序实施；不要先从 retry queue 或评分层打补丁。
5. 每个阶段验证对应不变量，最后才运行真实模型验收。
