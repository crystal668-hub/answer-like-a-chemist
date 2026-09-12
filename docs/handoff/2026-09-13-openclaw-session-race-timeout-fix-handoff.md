# OpenClaw Session Race 与历史 Timeout 误重试修复交接文档

状态：`CLOSED`

实施与验收结果见
[OpenClaw Session Race and Timeout Retry Fix Validation](../report/2026-09-13-openclaw-session-race-timeout-fix-validation.md)。

整理日期：2026-09-13

本文供下一会话的工程师或 agent 独立接手，目标是完成 benchmark 单一
LLM 运行链路中两个相互放大的架构问题：

1. OpenClaw embedded session 的并发写入/接管竞态，表现为
   `EmbeddedAttemptSessionTakeoverError` 和
   `session file changed while embedded prompt lock was released`。
2. benchmark runner 把同一 session 的历史 `LLM idle timeout` 当作当前
   attempt 仍然失败，导致已经有完整答案的成功结果被再次重试，并可能被
   后续失败结果覆盖。

本次交付是修复计划，不是已完成的实现。接手者必须以当前代码为准，先阅读
仓库 `AGENTS.md`、`GLOBAL_DEV_SPEC.md` 和本文，再实施、测试、更新规范并
提交 Git。

## 1. 必读基线和证据

### 1.1 仓库边界

- Canonical project root：`/Users/xutao/.openclaw/workspace`
- 运行时 home：`/Users/xutao/.openclaw`，不是源码根目录。
- 项目命令使用 `uv run ...` 或 workspace `.venv`，不要使用系统 Python。
- 本文对应的最新基线 run：

  `state/benchmark-runs/formal/vgb-property-calculation-advanced/qwen3-8-flash/verifier-grounded-property-calculation-qwen3-8-flash-20260912-145746`

### 1.2 当前实现入口

阅读以下文件的当前版本；本文中的行号只用于定位，不是接口契约：

- `benchmarking/service/single/runner.py`
- `benchmarking/service/single/openclaw_wrapper.py`
- `benchmarking/runtime/container_attempt.py`
- `benchmarking/runtime/container_runtime.py`
- `benchmarking/runtime/session_isolation.py`
- `benchmarking/runtime/error_capture.py`
- `benchmarking/core/convergence.py`
- `benchmarking/workflow/attempt_queue.py`
- `tests/test_benchmark_test.py`
- `tests/test_benchmark_convergence.py`

规范和设计文档：

- `GLOBAL_DEV_SPEC.md`
- `docs/design/2026-07-15-verifier-grounded-openclaw-single-llm-integration-usage-spec.md`
- `docs/design/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md`
- `docs/design/2026-09-08-benchmark-single-llm-containerization-plan.md`

### 1.3 最新 run 的事实基线

最新 run 是 3 道题 × 2 个 group，共 6 个执行单元：

- `single_llm_skills_on`：1 个成功、2 个失败。
- `single_llm_skills_off`：1 个成功、2 个失败。
- 所有已归档 attempt 的 workspace/session audit 均为隔离通过；没有跨
  容器 workspace 污染。
- Docker image 固定为同一个 arm64 image digest。
- Docker cleanup 记录为 `removed=true`，失败 attempt 没有
  `OOMKilled`、外层 7200 秒超时或取消证据。
- Docker 网络预检为 `status=ready`；`agent-team-api.myrimate.cn` 的
  `http_status=404` 只说明探测 URL 到达服务器但路径不是模型接口。
  本 run 没有该域名的 `ENOTFOUND`。

关键证据文件：

- `results.json`
- `progress/state.json`
- 每个 attempt 的
  `scratch/outputs/container-spool/stdout.log`、`stderr.log`、`cleanup.json`
- 每个 attempt 的 `scratch/session/agents/*/sessions/*.jsonl` 和
  `*.trajectory.jsonl`

## 2. 已验证的根因判断

### 2.1 运行链路

当前单一 LLM 的调用链是：

```text
CLI / attempt queue
  -> SingleLLMRunner.run(record, group)
    -> _run_isolated_attempt(...)
      -> DockerContainerRuntime.create/start/collect
        -> container_attempt
          -> openclaw_wrapper
            -> openclaw agent --local --session-id <explicit-id>
              -> Qwen openai-responses provider
```

这里存在三套不同状态：

1. OpenClaw 单次 embedded prompt 的内部状态；
2. benchmark attempt 的 `RunnerResult` 状态；
3. transcript 中累计的历史 convergence diagnostics。

当前实现没有把这三套状态严格分开，导致终态判断被历史事件污染。

### 2.2 历史 timeout 误触发 retry：确定性 bug

H-bond 题的 skills-on group 提供了最强证据：

- attempt-0 已经是 `completed`，答案为
  `{"answer":12,"unit":"count"}`；
- 同一个 attempt 的 convergence 仍记录早先发生的
  `LLM idle timeout (120s): no response from model`；
- `SingleLLMRunner._timeout_retry_decision()` 没有先判断当前结果是否
  已经是成功终态，而是直接调用 `is_runner_meta_timeout_family()`；
- 因此 attempt-0 被标记为
  `retryable=true, retry_reason=runner_meta_timeout_family`；
- 后续 attempt-1/3 失败后，最终 per-record 结果被写成失败。

当前问题位置：

- `benchmarking/service/single/runner.py` 的 `_timeout_retry_decision()`：
  `runner_meta.convergence.latest_prompt_error_is_timeout` 可以在
  `result.failure is None` 时触发 retry。
- `benchmarking/core/convergence.py` 的
  `summarize_transcript_convergence()`：它正确地保留历史错误，但调用方
  把诊断字段误当成当前终态信号。

项目设计文档已经规定：历史 idle/transport timeout 只能保留在诊断中；
如果 native output、transcript recovery 或 finalization rescue 已产生完整、
符合 schema 的答案，历史 timeout 不得使答案不可评分，也不得触发新的
attempt。这个约束当前缺少 runner-level 回归测试。

### 2.3 `LLM idle timeout (120s)`：provider/adapter 层响应空窗

trajectory 中可见：

```text
timedOut=true
idleTimedOut=true
timedOutDuringToolExecution=false
promptError=LLM idle timeout (120s): no response from model
```

这不是 Docker 外层 wall-clock timeout，也不是 shell/process 工具超时；
它表示 OpenClaw 等待模型响应或流式数据时超过内部 120 秒没有收到可接受
的响应。

H-bond attempt-0 还出现了：第一次 embedded session idle timeout 后，几乎
立即再次开始同一 session，随后最终成功。这说明 OpenClaw 内部可能会在
一次 CLI 运行中重新驱动 embedded prompt；最终 native output 可以成功，
但 transcript 仍包含早先的失败事件。

待实现目标不是简单把所有 timeout 都屏蔽，而是：

- 保留 provider timeout 的真实诊断；
- 只对当前没有完整答案的 attempt 进行 retry；
- 对 provider 长时间无 token 的行为增加可观测性，区分首 token 延迟、
  stream gap、HTTP transport error 和 OpenClaw 自身 idle watchdog。

### 2.4 Session takeover：OpenClaw embedded session 生命周期竞态

三个失败 attempt 的原始 stderr 都出现了类似内容：

```text
[agent/embedded] embedded attempt cleanup detected session takeover after prompt failure; preserving prompt error: ...
EmbeddedAttemptSessionTakeoverError: session file changed while embedded prompt lock was released: /benchmark/session/agents/.../sessions/<session>.jsonl
[diagnostic] lane task error: lane=main ...
[diagnostic] lane task error: lane=session:agent:... ...
```

这些错误有以下特征：

- `aborted=false`、`timedOut=false`、`idleTimedOut=false`；
- OpenClaw 子进程以返回码 1 退出；
- Docker 容器本身正常结束并被清理；
- 失败路径中的 session 文件是当前容器自己的 bind mount；
- 不同 attempt 使用独立 workspace、独立 session root 和显式 session id；
- session/workspace audit 没有发现跨容器写入。

因此应将其定位为 OpenClaw 内部 embedded prompt/session writer/cleanup
lane 的竞态，而不是 benchmark 容器互相覆盖 session 文件。

常见前置活动包括：

- 长时间后台 `exec` 与 `process` 会话；
- 高 thinking 级别和大上下文；
- 多次 `edit` 工具调用；
- `edit` 的 no-op 错误：
  `No changes made ... The replacement produced identical content.`

no-op edit 是相关性线索或放大器，目前不能直接证明它是唯一触发器。修复
必须围绕 session 所有权和写入序列化，而不是放宽 workspace 访问策略或吞掉
这个异常。

## 3. 修复目标和不变量

### 3.1 必须达成的目标

1. OpenClaw 每个 attempt 只有一个明确的 session writer/owner；内部重试、
   cleanup、transcript/trajectory 写入不能相互覆盖未完成的 session。
2. attempt 的成功终态优先于历史 convergence diagnostics。
3. 只有当前 attempt 没有完整答案，且当前失败满足 retry policy 时，才允许
   重新创建 attempt。
4. provider timeout、OpenClaw session takeover、Docker 生命周期错误和
   benchmark retry decision 必须在结果中分别呈现，不能用 generic
   `subprocess exited` 丢失原始层级。
5. 每次 retry 仍使用新 workspace/session，但不得把新 attempt 的失败覆盖
   已确认成功且可评分的旧 attempt；结果持久化必须保留完整 attempt history。
6. 修复不能削弱现有 workspace isolation、container mount policy、cleanup
   或 hidden verifier 边界。

### 3.2 不应采用的方案

- 不要简单删除 120 秒 watchdog；它是重要诊断和故障检测机制。
- 不要把所有 `runner_meta_timeout_family` 都标成不可重试。
- 不要通过放宽 `/benchmark/session` 或绝对路径策略掩盖 takeover。
- 不要让多个 OpenClaw CLI 进程共享同一个显式 session 文件。
- 不要在结果聚合层用“最后写入优先”覆盖此前的完整成功结果。
- 不要修改 provider 凭据、正式数据集、评分公式或 Docker 安全边界来绕过
  失败。

## 4. 目标架构

### 4.1 明确 attempt outcome 与 diagnostics 两个平面

引入或重构一个纯数据层的 attempt outcome 判定（可以放在
`benchmarking/core/`，具体模块名由接手者根据当前代码选择）：

```text
AttemptEvidence
  - native_result_status
  - native_payload_complete
  - transcript_complete_answer
  - finalization_rescue_complete
  - current_process_exit
  - current_error_classification
  - historical_prompt_errors

AttemptOutcome
  - terminal_status: completed | recovered | failed
  - answer_source: native | transcript | rescue | none
  - retry_decision: no_retry | retry
  - retry_reason: typed current failure only
```

规则顺序必须固定：

1. 先验证当前 native output 是否有完整答案；
2. 再验证 transcript/recovery/rescue 是否有完整答案；
3. 只要答案完整，attempt 立即视为成功或 recovered，历史 timeout 只进
   diagnostics，不参与 retry；
4. 只有没有完整答案时，才根据当前失败层级和 retryable 属性决定 retry；
5. `session takeover` 默认是 terminal execution error，除非另有明确、
   可验证的恢复策略，不能被模糊归入 provider timeout。

### 4.2 OpenClaw session supervisor

不要让 `openclaw_wrapper` 依赖 OpenClaw 自己的隐式 session takeover 行为。
在 wrapper 与 OpenClaw CLI 之间建立一个小型 supervisor 状态机，职责包括：

- 为一次 attempt 分配唯一 `session_id`、session file 和 owner token；
- 启动 OpenClaw CLI，并记录 PID、启动时间、session file inode/size/mtime；
- 在 OpenClaw 返回、idle timeout、signal 或异常时，读取并冻结当前证据；
- 任何 continuation/retry 使用新 session file，不得在旧 session file 上并发
  写入；
- cleanup 只能由 owner 或持有有效 lease 的 supervisor 执行；
- 在结束前写入结构化 `session-lifecycle.json`，记录 writer、takeover、
  cleanup 和最终状态。

如果 OpenClaw CLI 本身会在一次调用内重启 embedded prompt，supervisor 必须
把它视为同一 attempt 的内部事件，而不是 benchmark retry；但要能识别：

- 同一 session file 的连续 embedded run 是否由同一 owner 产生；
- 是否有两个 PID/线程同时写同一 session；
- session file 是否在 lock release 期间被非 owner 修改。

### 4.3 Session file 写入协议

目标协议应至少包含：

- 每个 attempt 一个独立 session directory；
- append-only transcript 或由单 writer 负责的 serialized append；
- metadata/summary 使用原子 rename，但不替换仍在写入的 transcript；
- cleanup 不删除或重写旧 transcript，只写终态 marker；
- 检测到 inode/owner token 不匹配时，停止继续写入并保留原始证据；
- benchmark audit 读取冻结快照，不读取可能仍在变化的 live file。

实现可复用现有 `session_isolation.py` 的显式 session identity 校验，但
不能把“postflight pointer 正确”误认为“运行期间没有并发 writer”。需要
新增运行期间的 ownership/lifecycle 证据。

### 4.4 Docker 生命周期保持单向、可观测

Docker 仍负责隔离 filesystem、session root 和 attempt environment：

```text
create -> start -> collect(wait) -> capture logs/inspect -> stop/kill -> remove -> seal
```

要求：

- 不在 host 与 container 之间共享 live OpenClaw session store；
- `container_attempt` 在 child 结束后才做 dependency manifest/cleanup；
- container stdout/stderr 和 session lifecycle evidence 都必须归档；
- Docker `return_code=0` 但 runner payload 表示 timeout/failed 时，结果层应
  保留两者，不得把容器码当作 agent 成功；
- Docker cleanup 失败仍按现有 fail-closed 规则传播，不得与 provider/session
  错误混为一类。

## 5. 分阶段实施计划

### Phase 0：建立确定性复现和失败证据测试

- [ ] 固定一份最小 OpenClaw transcript fixture：先写入 prompt error，再写入
      完整 `FINAL ANSWER`，验证最终 outcome 为 completed，且 retry=false。
- [ ] 固定一份 session takeover fixture：模拟 session file owner/mtime/inode
      在 lock release 期间变化，验证原始错误、writer 身份和终态都被保留。
- [ ] 固定 provider idle fixture：无任何完整答案、`idleTimedOut=true`，验证
      retry=true。
- [ ] 固定 Docker wrapper fixture：容器返回码 0，但 payload 有 timeout
      diagnostics；验证结果层根据 payload 判断，不根据 Docker code 猜测。
- [ ] 保存 fixture 所需的最小 JSONL，不复制正式 run 的大 prompt 或凭据。

建议测试文件：

- `tests/test_single_llm_timeout_retry.py`（若当前不存在则创建）
- `tests/test_single_llm_session_wrapper.py`
- `tests/test_benchmark_convergence.py`
- `tests/test_container_runtime.py`

### Phase 1：修复 outcome/retry 状态机

- [ ] 把“当前结果是否已有完整答案”提取为单一、纯函数判断，覆盖 native
      payload、transcript recovery 和 finalization rescue。
- [ ] 修改 `_timeout_retry_decision()`：
      `result.failure is None` 且答案完整时必须返回 no-retry；不能再因为
      `runner_meta.convergence.latest_prompt_error_is_timeout` 触发 retry。
- [ ] 将 retry decision 的输入从“历史 runner_meta”收窄为“当前 terminal
      failure evidence”；历史 `prompt_error_count/latest_prompt_error` 只
      作为 diagnostics。
- [ ] 让 `RunnerResult.COMPLETED`/`RECOVERED` 成为不可被后续失败 attempt
      覆盖的逻辑终态；若业务确实需要继续验证，必须产生独立的 diagnostic
      attempt，不得替换 scoreable answer。
- [ ] 在 `attempt_history` 中区分：
      `current_failure_code`、`historical_prompt_errors`、`answer_source`。
- [ ] 保持现有 provider HTTP 408/504、transport timeout 和明确 retryable
      error 的重试测试。

必须新增的回归断言：

```text
completed answer + historical idle timeout -> 1 attempt, retry=false
failed timeout + no answer -> retry=true
recovered transcript answer + historical idle timeout -> retry=false
session takeover + no answer -> terminal session error, no silent retry
```

### Phase 2：实现 OpenClaw session supervisor/ownership

- [ ] 先确认 OpenClaw 2026.6.9 的 embedded prompt/session writer 行为；不要
      仅根据错误字符串猜测。
- [ ] 在 `openclaw_wrapper.py` 中增加生命周期事件记录，至少包括：
      `attempt_id`、`session_id`、wrapper PID、OpenClaw PID、session path、
      owner token、start/end、exit code、prompt error、takeover detection、
      cleanup result。
- [ ] 为 live session file 增加 owner/lease 检查；检测到非 owner 修改时，
      进入 terminal `session_takeover` 状态，停止继续写入，保存 evidence。
- [ ] 将一次 OpenClaw 内部 continuation 与 benchmark-level retry 分开：
      continuation 不创建新的 benchmark attempt；benchmark retry 必须经过
      runner decision 且使用全新的 workspace/session。
- [ ] 对 OpenClaw 可能的 prompt failure cleanup 做单 writer 序列化；cleanup
      不能在另一个 embedded prompt 仍可能写入时重写 session。
- [ ] 如果 OpenClaw CLI 无法提供可靠的 owner hook，采用 wrapper 侧的
      append-only event journal + immutable snapshot，避免直接竞争 OpenClaw
      live JSONL；不要通过强制删除 session 文件解决竞态。

### Phase 3：把 session lifecycle 接入 Docker evidence

- [ ] `container_attempt.py` 写出 `scratch/notes/session-lifecycle.json`。
- [ ] `_run_attempt_in_docker()` 将 container metadata、stdout/stderr、session
      lifecycle 和 cleanup report 统一挂入 `runner_meta`，失败路径也必须有
      container identity 与原始 stderr 摘要。
- [ ] `capture_execution_error()` 增加稳定的
      `openclaw_session_takeover` 分类，保留完整原始行、session path、lane
      和 return code。
- [ ] 不因 generic `openclaw_subprocess_failed` 丢弃原始
      `EmbeddedAttemptSessionTakeoverError`。
- [ ] 验证 Docker 失败 attempt 的容器被清理、session snapshot 可读、audit
      仍能完成；验证 cleanup 失败时仍 fail closed。

### Phase 4：provider idle timeout 可观测性和策略收敛

- [ ] 在不取消 120 秒 watchdog 的前提下，记录 provider request start、首个
      response chunk、最后一个 chunk、结束原因和 OpenClaw prompt error source。
- [ ] 区分以下类型：
      `provider_first_token_timeout`、`provider_stream_gap_timeout`、
      `provider_http_timeout`、`openclaw_idle_watchdog`、
      `openclaw_session_takeover`。
- [ ] 对高 thinking/长工具链任务做小规模并发实验，比较并发 1 与 2、skills-on
      与 skills-off、同一 provider 不同任务长度；只收集证据，不用实验结果
      直接放宽安全边界。
- [ ] 如果确认 provider 允许更长的 first-token latency，再增加模型/track
      可配置的 idle policy；配置必须进入 run manifest 和结果 metadata。
- [ ] 不把 provider idle timeout 自动降级成“正常成功”；只有完整答案才可
      成功或 recovered。

### Phase 5：端到端验证和规范同步

- [ ] 运行现有相关单元测试和全量测试。
- [ ] 重建当前 Docker image，执行无模型 contract/integration tests。
- [ ] 至少执行一轮小型真实 qwen Docker run，覆盖：
      一个会出现历史 timeout 后成功的场景、一个真实 provider timeout、一个
      session takeover 注入/仿真场景，以及 skills-on/off。
- [ ] 验证结果中成功答案没有被后续失败 attempt 覆盖。
- [ ] 如果实现改变了 session lifecycle、retry semantics、error taxonomy 或
      execution flow，更新 `GLOBAL_DEV_SPEC.md` 的现状章节；不要把本计划
      内容直接写成已实现行为。
- [ ] 在 `docs/report/` 增加验收报告，记录 run 路径、测试命令、通过结果、
      未解决限制和原始错误证据。
- [ ] 代码、测试、规范和报告通过后提交 Git。

## 6. 预期文件边界

优先修改：

- `benchmarking/service/single/runner.py`
- `benchmarking/service/single/openclaw_wrapper.py`
- `benchmarking/runtime/error_capture.py`
- `benchmarking/runtime/session_isolation.py` 或新的 session lifecycle 模块
- `benchmarking/runtime/container_attempt.py`
- `benchmarking/service/single/runner.py` 的 Docker failure metadata 组装路径

可能新增：

- `benchmarking/core/attempt_outcome.py` 或等价纯状态模块
- `benchmarking/runtime/session_lifecycle.py`
- `tests/test_single_llm_timeout_retry.py`
- `tests/test_single_llm_session_lifecycle.py`
- `tests/fixtures/...` 下的最小 transcript/session fixtures
- `docs/report/2026-09-13-openclaw-session-race-timeout-fix-validation.md`

必须谨慎检查但通常不应改动：

- `benchmarking/workflow/attempt_queue.py`：只有在确认 retry 重新入队或终态
  覆盖问题跨越 queue 边界时才修改；不要把 runner outcome bug 推给 queue。
- `benchmarking/runtime/container_runtime.py`：当前证据不支持把 Docker wait、
  host network 或 cleanup 作为主因；改动必须由新的容器级复现证明。
- `benchmarking/core/convergence.py`：历史事件保留本身是设计要求，优先修改
  调用方的终态判定，而不是删除 diagnostics。

## 7. 验收矩阵

| 场景 | 预期结果 |
| --- | --- |
| 当前 native output 有完整 schema 答案，transcript 有旧 timeout | `COMPLETED`/`RECOVERED`，不 retry |
| 当前没有答案，OpenClaw idle timeout | typed timeout，按 policy retry |
| provider 408/504/transport timeout | 保留原始 provider evidence，按 retry policy 处理 |
| session takeover 且无答案 | `openclaw_session_takeover`，原始 stderr 可读，不静默降级 |
| session takeover 后 transcript 已有完整答案 | 先按完整答案恢复；takeover 作为诊断，不覆盖答案 |
| 两个 benchmark attempts 同时运行 | session/workspace 完全独立，无 takeover 由跨容器写入造成 |
| OpenClaw 内部 continuation | 计入同一 attempt，不新增 benchmark retry |
| 后续 retry 失败但先前 attempt 已成功 | 最终 per-record 保留成功答案，attempt history 保留失败详情 |
| Docker 自然退出 | container cleanup 成功，stdout/stderr/session lifecycle 可审计 |
| Docker OOM/外层超时/cleanup failure | 与 provider/session error 分层，终态和 recovery policy 一致 |
| skills-on 与 skills-off | 只差技能暴露，不改变 outcome/retry/session contract |

## 8. 关闭条件

只有同时满足以下条件才可将本文标为 `CLOSED`：

- 新增的成功+历史 timeout 回归测试通过，证明不会误重试；
- 新增的 session takeover ownership/lifecycle 测试通过；
- 原始 `EmbeddedAttemptSessionTakeoverError` 不再被 generic error 丢失；
- provider idle timeout 与 session takeover 在 metadata 中可区分；
- Docker 失败 attempt 的容器、session、stdout/stderr 和 cleanup evidence 可
  复核；
- 全量测试和必要的真实 Docker run 通过；
- `GLOBAL_DEV_SPEC.md` 与实际实现一致；
- 验收报告已写入 `docs/report/`；
- 工作区没有未解释的容器、进程或锁文件残留；
- 所有代码、测试和文档已提交 Git。

## 9. 给下一会话的第一步

接手后不要直接修改 retry 条件。先按以下顺序完成：

1. 阅读本文列出的规范、实现和测试。
2. 用 `jq`/`rg` 重现 H-bond attempt-0 的“completed + historical timeout”
   证据，并重现三条 session takeover 的原始 stderr。
3. 新增失败测试，先让测试证明当前实现会错误重试/错误分类。
4. 设计并实现 outcome/retry 状态机，再设计 session lifecycle ownership。
5. 每完成一个阶段都运行对应测试并保留结果；不要把真实 benchmark run
   当作唯一回归测试。
