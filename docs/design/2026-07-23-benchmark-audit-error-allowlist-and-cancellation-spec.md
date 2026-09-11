# Benchmark Audit Error Allowlist and Cancellation Specification

状态：`DONE`

日期：2026-07-23

适用项目：OpenClaw benchmark workspace audit 与 CLI orchestration

## 1. 摘要

本规格保留 OpenClaw agent 的原生 `exec` 和配套 `process` 能力，不新增
`benchmark_exec`，也不改变 benchmark agent 的正常工具面。

当前问题发生在工具执行之后：agent 已经提交并运行了原生 shell 命令，tool result 也已经返回，
但 workspace transcript audit 在重建命令路径语义时，可能因 heredoc 中混合真实换行与字面量
`\\n` 而抛出解析异常。现有实现把任意单次解析异常提升为整个 attempt 的
`audit_execution_status=unavailable`，继而将已经生成或仍可生成的答案判为
`non_evaluable`。

本规格引入“可恢复审计错误白名单”。白名单不是按异常文本直接跳过审计，而是把已知解析条件映射到
一个专用恢复器。只有恢复器能够对原始 tool call 建立完整、保守的审计投影时，该错误才被转换为
可读 warning，审计继续执行，答案仍可进入 evaluator。恢复器发现 protected path 时仍按现有规则
判定；无法恢复或遇到未知解析错误时继续 fail closed。

首个白名单项是 `exec_unterminated_heredoc_eof`。对该条件，审计器按真实 shell 的 EOF heredoc
语义处理剩余 body，继续扫描命令前缀、heredoc body、显式 cwd/workdir 和已知环境变量，不把
`Unterminated here-document` 本身等同于 workspace 污染。

本规格同时保留 benchmark CLI 的取消收口改动：`SIGINT`/`SIGTERM` 后停止调度、终止 owned
进程、seal 或 quarantine workspace、释放 lease，并持久化明确的取消终态，不得残留
`running`。

## 2. 已确认决策

1. benchmark agent 继续使用 OpenClaw 原生 `exec` 和 `process`。
2. 不新增结构化执行工具，不移除 shell，不修改现有工具 allowlist 来限制 agent 的通用执行能力。
3. workspace audit 增加稳定错误码和显式 allowlist，不以异常 message substring 直接决定放行。
4. 每个 allowlist 项必须绑定专用恢复器；不存在“忽略所有 parser error”的全局开关。
5. 首个 allowlist 项只处理 heredoc 到输入 EOF 仍未出现 delimiter 的条件。
6. allowlist 命中后必须继续扫描该 tool call 的所有可执行前缀和完整 heredoc body。
7. 恢复后的 protected-path finding 优先于 allowlist warning，现有 contamination/adjudication
   规则不变。
8. tool result 的非零 exit code 或 `isError=true` 不能单独证明命令没有副作用。
9. 恢复成功且未发现越界证据时，audit 保持 complete，记录 warning，不阻止答案评分。
10. 恢复失败、证据不完整或错误未在 allowlist 中时，audit 继续 unavailable/fail closed。
11. heredoc 和内联多行解释器脚本的规避说明写入 canonical `AGENTS.base.md`，不注入每题 prompt。
12. CLI 取消必须形成持久、可读、幂等的终态。

## 3. 事实与第一性原理

### 3.1 工具执行和事后审计是两个阶段

当前路径为：

~~~text
model tool call
  -> OpenClaw native exec
  -> shell/process execution
  -> tool result
  -> transcript persistence
  -> workspace audit parser
  -> workspace adjudication
~~~

workspace audit parser 的异常发生在 tool result 返回之后。它不等于工具未执行，也不等于 agent
读取了受保护信息。当前缺陷是把“审计器未完成解析”直接投影为“信息污染不确定”，从而否定整个
attempt。

### 3.2 未终止 heredoc 不代表命令未执行

本轮实际 transcript 中，异常命令在 heredoc delimiter 缺失的情况下仍然执行了前置
`command -v` 和 Python，并返回了可见 stdout/stderr 与 `exitCode=1`。bash 和 zsh 也都会把
EOF 前的剩余文本作为 heredoc body 交给目标进程。

因此不能采用以下规则：

~~~text
if exception_message == "Unterminated here-document":
    declare_no_contamination()
~~~

恶意或偶发命令可以先读取 protected path，再进入未终止 heredoc；也可以在 heredoc body 中读取
protected path。按异常名称无条件放行会制造明确的审计绕过。

### 3.3 可恢复条件应由语义恢复，而不是忽略

heredoc 到 EOF 未闭合是一个可建立确定投影的条件：shell 会把从 heredoc 起始行之后直到 EOF 的
内容作为 body。审计器可以在投影末尾补一个“仅用于审计的 body end marker”，继续使用既有路径
候选扫描。该 marker 不修改 transcript、不重放命令，也不声称实际 shell 收到了 delimiter。

因此本次修复的最小正确边界是：

~~~text
known parser condition
  -> dedicated audit recovery
  -> full candidate scan
  -> normal policy adjudication
~~~

而不是：

~~~text
known exception text
  -> skip tool call
  -> assume clean
~~~

## 4. 目标

- 保留 agent 使用原生 exec 完成通用计算、脚本和工具调用的能力；
- 让已知且可恢复的 audit parser condition 不再使整个 attempt 不可评估；
- 让恢复后的 tool call 继续接受与正常命令相同的 protected-path 检查；
- 用稳定错误码、恢复器和结构化 finding 形成可测试、可扩展的白名单；
- 使普通工具失败、语法失败和 agent 后续修正保持可恢复；
- 把行为规避指引放在 workspace contract，而不是 benchmark record prompt；
- 为 CLI 中断建立确定的进程和状态收口。

## 5. 非目标

- 不新增 `benchmark_exec` 或其他替代 exec 工具；
- 不移除、deny 或削弱原生 `exec`/`process`；
- 不自动把原始 tool arguments 中的 `\\n` 替换成真实换行；
- 不修改、重放或重新执行已经发生的 shell 命令；
- 不把所有 `ValueError`、`shlex` 错误或 `transcript_audit_failed` 加入白名单；
- 不以非零 exit code、stderr 或 `isError=true` 推断命令无副作用；
- 不改变 protected path、confirmed exposure 或 indeterminate exposure 的现有判定原则；
- 不在 benchmark prompt 中追加 shell 使用规则；
- 不实现 OS sandbox、run 级熔断或历史结果自动重评；
- 不改变 benchmark 题目、模型、verifier、gold 或评分算法。

## 6. 核心不变量

### BAEC-01：原生执行能力保留

run-scoped OpenClaw 配置继续暴露现有原生 exec/process 工具。实现不得以本修复为由改变 skills-on、
skills-off、ChemQA 或 judge 的有效工具集合。

### BAEC-02：白名单必须绑定恢复器

allowlist key 是代码定义的稳定 `audit_error_code`，每个 code 必须绑定唯一 recovery handler。
禁止按 exception class 或 message substring 直接标记 clean。

### BAEC-03：恢复先于放行

只有 recovery handler 成功产出完整 audit projection，才允许把 parser failure 转为 warning。
handler 抛错、返回不完整或声明 unsupported 时，原始事件继续使 audit unavailable。

### BAEC-04：不跳过任何可执行区域

恢复投影必须覆盖 heredoc 前的命令、所有 heredoc body、后续可判定内容以及显式 cwd/workdir。
不得只检查触发异常的行或只检查 tool result。

### BAEC-05：protected finding 优先

恢复扫描发现 protected root 后，必须生成正常 `protected_path_access` finding。allowlist 不能覆盖、
删除或降低该 finding 的 information exposure。

### BAEC-06：执行失败不等于无副作用

operation outcome 继续来自成对的 tool result，但 `failed` 只参与现有 exposure 计算，不能成为
allowlist 的充分条件。

### BAEC-07：恢复可观测

每次白名单恢复必须写入稳定 warning finding，包含错误码、恢复器版本、tool call id、call/result
line 和 operation outcome。不得静默吞掉 parser condition。

### BAEC-08：未知错误继续 fail closed

未注册错误码、无法分类的 parser exception、transcript JSON 损坏或恢复器自身异常仍保持
`audit_execution_status=unavailable`。

### BAEC-09：取消终态持久化

CLI 接受取消后，run 不得回到 `running`。最终状态只能是 `cancelled` 或
`cancelled_with_errors`。

### BAEC-10：取消后不再调度

cancellation token 设置后不得启动新的 record、retry、wave、judge 或 detached analysis。

## 7. 审计错误白名单契约

### 7.1 稳定分类

新增内部类型：

~~~python
@dataclass(frozen=True)
class AuditParserCondition:
    code: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class AuditProjection:
    text: str
    recovery_code: str | None = None
    recovery_version: int | None = None
~~~

`_command_audit_projection()` 不再用通用 `ValueError` 表达已知条件。已知条件返回或抛出带稳定
code 的 typed condition；未知实现异常保持异常并由现有 fail-closed 路径处理。

首个稳定 code：

~~~text
exec_unterminated_heredoc_eof
~~~

分类依据来自 parser state：输入 EOF 时 pending heredoc delimiter 非空，而不是匹配
`"Unterminated here-document"` 文本。

### 7.2 Allowlist registry

allowlist 是代码内显式 registry：

~~~python
AUDIT_ERROR_RECOVERY_HANDLERS = {
    "exec_unterminated_heredoc_eof": recover_unterminated_heredoc_eof,
}
~~~

首版本不提供环境变量、CLI flag 或 benchmark 配置来任意扩展 registry。新增 code 必须经过代码、
测试和规格审核。

### 7.3 EOF heredoc 恢复

`recover_unterminated_heredoc_eof` 必须：

1. 接收原始 command 和 parser 已构造的投影状态；
2. 保留 heredoc 之前已经投影的全部命令；
3. 把 EOF 前剩余内容保持为 heredoc body；
4. 只在 audit projection 末尾插入 body end marker；
5. 让既有 tokenizer 扫描 prefix 和完整 body 中的路径候选；
6. 保留显式 `cwd`/`workdir` 的既有扫描；
7. 返回 recovery code 与 recovery version。

恢复器不得：

- 修改 tool arguments；
- 把 `\\n` 全局正规化；
- 调用 shell 验证、重放命令或执行 body；
- 丢弃 body 中的 quote、路径或环境变量候选；
- 根据 tool result 内容宣称 body 未执行。

### 7.4 Finding 与判定

恢复成功时增加：

~~~json
{
  "rule_id": "transcript_audit_recovered",
  "audit_error_code": "exec_unterminated_heredoc_eof",
  "recovery_handler": "recover_unterminated_heredoc_eof",
  "recovery_version": 1,
  "tool_call_id": "call-id",
  "tool_name": "exec",
  "operation_outcome": "failed",
  "information_exposure": "none",
  "boundary_effect": "warning",
  "evidence": {
    "call_line": 6,
    "result_line": 7
  }
}
~~~

`command_excerpt` 继续使用现有 redaction 和长度限制。finding 不保存完整 secret-bearing command。

判定矩阵：

| 条件 | audit execution | boundary | contamination | adjudication |
| --- | --- | --- | --- | --- |
| allowlist recovery 成功，无 protected finding | complete | warning | clear | scoreable |
| recovery 成功，只有 write-only boundary finding | complete | violated | clear | scoreable_degraded |
| recovery 成功，确认或可能读取 protected root | complete | violated/unknown | confirmed/indeterminate | non_evaluable |
| recovery 失败或 projection 不完整 | unavailable | unknown | indeterminate | non_evaluable |
| parser condition 未在 allowlist | unavailable | unknown | indeterminate | non_evaluable |

warning 表示审计使用了受控恢复路径，不表示 workspace 已污染。

### 7.5 与历史 transcript 的关系

历史 transcript 继续使用同一审计入口。实现完成后可以用只读 replay 验证本轮失败记录会如何判定，
但本次不自动改写、重评或发布历史分数。

## 8. Agent Workspace 规则

修改 canonical：

~~~text
benchmarking/resources/agent-workspace-templates/common/AGENTS.base.md
~~~

保留原生 exec 的现有 scratch 进入方式，并增加：

- 原生 exec 仍可用于正常单行 shell 命令和本地工具；
- 不使用 heredoc、here-string 或内联多行 `python -c`/`node -e`；
- 多行脚本先通过结构化 write 写入 `scratch/tmp/<name>.<ext>`；
- 再用原生 exec 从 runner-provided scratch 环境运行该脚本；
- 工具失败后可以修正命令并继续。

这些是降低已知 provider/serialization 交互风险的行为指引，不是安全边界。规则只进入
`AGENTS.base.md`，不得复制到 `SingleLLMRunner._attach_scratch_prompt()` 或每题 prompt。

## 9. CLI 取消收口

### 9.1 状态

run/wave/group 支持：

~~~text
pending
running
cancelling
cancelled
cancelled_with_errors
completed
completed_with_errors
failed
~~~

record 支持：

~~~text
pending
running
cancelled
failed
completed
~~~

cancelled record 不生成伪造 score；未进入 evaluator 的记录设置 `evaluable=false`、
`scored=false` 和 `execution_error_kind=cancelled`。

### 9.2 信号与收口顺序

CLI 在 worker 启动前安装 `SIGINT`/`SIGTERM` handler。handler 只设置 cancellation token 并记录
首次 signal；主控制流按以下顺序收口：

~~~text
stop scheduling
  -> persist run_cancelling
  -> cancel active runners
  -> terminate owned process groups
  -> wait grace period
  -> force-kill owned survivors
  -> collect available transcript/result metadata
  -> audit where possible
  -> seal or quarantine workspace
  -> release lease
  -> persist record/group/wave terminal states
  -> write runtime manifest terminal status
  -> persist run_cancelled
~~~

第二次信号可以缩短 grace period，但只能作用于 process registry/lease 证明属于本 run 的进程。

### 9.3 Runner 接口

runner 统一暴露：

~~~python
cancel(reason: CancellationReason) -> None
wait_cancelled(deadline: float) -> CancellationOutcome
~~~

single-LLM wrapper、ChemQA driver、judge 和 detached analysis launcher 都要登记 owned process
group。不能只终止 `uv`/CLI 父进程。

### 9.4 状态持久化与 stale reconciliation

新增 `run_cancelling`、`record_cancelled`、`group_cancelled` 和 `run_cancelled` progress events。
`progress/state.json`、wave、group 和 `runtime-manifest.json` 必须投影一致。

取消中发生 kill、seal、quarantine、lease release 或 persistence 错误时，最终状态为
`cancelled_with_errors` 并保留结构化 errors。

启动或 dashboard reconciliation 发现 owner/lease 已确定失效的 `running/cancelling` run 时，
必须写入可读的 stale recovery 终态，不得继续显示为正常 running。

## 10. 模块改动计划

| 模块 | 改动 |
| --- | --- |
| `benchmarking/runtime/workspace_audit.py` | typed parser condition、allowlist registry、EOF heredoc recovery、warning finding |
| `benchmarking/runtime/workspace_policy.py` | 复用现有 warning/scoreable 判定；必要时校验新 finding 字段 |
| `benchmarking/runtime/agent_workspace.py` | 让单个可恢复 condition 继续审计，不再提前返回 unavailable |
| `benchmarking/resources/agent-workspace-templates/common/AGENTS.base.md` | 保留 exec，增加脚本落盘和 heredoc/inline multiline 规避指引 |
| `benchmarking/workflow/orchestration.py` | cancellation token、停止调度、cancelled record/group 投影 |
| `benchmarking/workflow/progress.py` | cancelling/cancelled events 与终态投影 |
| `benchmarking/workflow/cli.py` | signal handler、owned process 收口、terminal manifest |
| `benchmarking/workflow/runners/*` | 统一 cancel/wait_cancelled 接口和 process registration |
| `benchmarking/dashboard/` | 显示 cancelling/cancelled/cancelled_with_errors 和 stale reconciliation |
| `GLOBAL_DEV_SPEC.md` | 实现完成后更新当前 audit recovery 与取消执行流 |

不得借本次改动修改 run-scoped agent tool allowlist，也不得把 allowlist 判定塞进 dashboard 或 scorer。

## 11. 测试计划

### 11.1 审计恢复单元测试

- 本轮混合真实换行/字面量 `\\n` 的命令命中 `exec_unterminated_heredoc_eof`；
- 恢复后 audit complete、boundary warning、contamination clear、adjudication scoreable；
- completed heredoc 行为不变且不产生 recovery warning；
- heredoc 前读取 protected path 仍产生 `protected_path_access`；
- 未终止 heredoc body 内读取 protected path 仍产生 `protected_path_access`；
- body 中通过已知环境变量引用 protected root 仍被解析；
- 命令以 exit 0 或 exit 1 结束都不能绕过 protected finding；
- tool result 含 stdout/stderr 不作为无污染证明；
- recovery handler 抛错或返回 incomplete 时 audit unavailable；
- unclosed quote、损坏 JSON 和其他未注册 parser error 继续 fail closed；
- warning finding 包含稳定 code/version/call/result evidence 且 command excerpt 已 redaction。

### 11.2 对抗性测试

至少覆盖：

~~~sh
cat "$PROTECTED_FILE"; python3 - <<'PY'
payload
~~~

~~~sh
python3 - <<'PY'
from pathlib import Path
print(Path("/protected/root/file").read_text())
~~~

~~~sh
false; python3 - <<'PY'
payload
~~~

前两类不能因 heredoc parser condition 被放行；第三类证明非零退出本身不参与白名单资格。

### 11.3 Agent contract 测试

- `AGENTS.base.md` 保留原生 exec 指令；
- heredoc/inline multiline 规避规则只存在于 workspace contract；
- benchmark record prompt 不新增执行规则；
- 多行脚本示例使用 structured write 到 `scratch/tmp`，再由原生 exec 执行。

### 11.4 集成与回归测试

- agent 首次提交该类畸形 exec、工具返回失败、随后修正并产出完整答案时仍进入 evaluator；
- 有 protected-path evidence 的相同 parser condition 仍 non-evaluable；
- skills-on、skills-off、ChemQA 和 judge 的原生工具面保持不变；
- historical replay 对本轮五条记录产生可解释、逐记录的 dry-run 判定；
- `tests/test_agent_workspace.py`、`tests/test_benchmark_workdir_guard.py`、
  `tests/test_single_llm_session_wrapper.py` 和 `tests/test_benchmark_test.py` 通过。

### 11.5 取消测试

- running record 收到 SIGINT/SIGTERM 后停止新增任务；
- 子进程、孙进程和计算程序全部退出，无 orphan；
- 当前 workspace seal；失败时 quarantine；lease 释放；
- progress、wave、group 和 manifest 不残留 running；
- 已完成 record 保持原结果，当前/未开始 record 明确 cancelled；
- 清理错误生成 cancelled_with_errors；
- 重复信号和重复收口保持幂等；
- stale running run 被 reconcile 为可读终态；
- dashboard 正确区分 running、cancelling、cancelled 和 cancelled_with_errors。

## 12. 实施顺序

### 阶段 1：审计分类与恢复

1. 引入 typed parser condition 和 code registry。
2. 实现 `exec_unterminated_heredoc_eof` recovery projection。
3. 让恢复投影进入既有 candidate/policy/adjudication 路径。
4. 生成 `transcript_audit_recovered` warning。
5. 添加正常、失败和对抗性单元测试。

### 阶段 2：Agent contract

1. 更新 `AGENTS.base.md`，保留 exec 并增加脚本落盘指引。
2. 验证规则未进入 benchmark prompt。
3. 增加 workspace materialization 测试。

### 阶段 3：取消收口

1. 增加 cancellation token、signal handler 和 runner cancel 接口。
2. 建立 owned process group registry 与 grace/force termination。
3. 增加 progress events、状态投影、terminal manifest 和 stale reconciliation。
4. 接入 workspace seal/quarantine 与 lease release。
5. 更新 dashboard 和取消测试。

### 阶段 4：验收与文档

1. 运行专项和相关回归测试。
2. 对本轮失败 records 执行只读 adjudication replay，保存 dry-run 报告。
3. 运行最小 verifier-grounded smoke case。
4. 按实际实现更新 `GLOBAL_DEV_SPEC.md`。
5. 测试通过后提交代码和文档。

## 13. 验收标准

1. benchmark agent 继续拥有当前原生 exec/process 能力。
2. 本轮 `Unterminated here-document: PY` 类型被稳定分类，不依赖 exception message。
3. 恢复器扫描命令前缀和完整 heredoc body，不跳过可执行区域。
4. 无 protected evidence 的恢复结果 audit complete、scoreable，并有结构化 warning。
5. protected path、未知 parser error 和恢复失败继续 fail closed。
6. 非零 exit code 不能单独触发白名单放行。
7. agent 修正失败命令后的完整答案可以进入 evaluator。
8. heredoc/inline multiline 规避规则只写入 `AGENTS.base.md`。
9. SIGINT/SIGTERM 后停止调度并终止全部 owned 子进程。
10. 取消后的 progress、wave、group 和 manifest 均具有明确终态。
11. workspace 被 seal 或 quarantine，lease 被释放，取消收口可重复执行。
12. 专项、对抗性、回归和 smoke tests 全部通过。
13. `GLOBAL_DEV_SPEC.md` 已按最终实现更新，代码与文档一致。

## 14. 明确禁止的实现捷径

- 不得按 `ValueError` 或 `Unterminated here-document` 文本直接 return clean；
- 不得在 allowlist 命中后跳过整个 tool call；
- 不得全局替换 `\\n` 或改写原始 transcript；
- 不得重放命令来推断其访问行为；
- 不得根据 exit code、stderr 或 `isError` 推断无副作用；
- 不得吞掉 protected-path finding；
- 不得把 allowlist 配置开放给 benchmark agent；
- 不得以此为由移除原生 exec/process；
- 不得只 kill CLI 父进程；
- 不得通过 dashboard 状态覆盖掩盖未完成的取消收口；
- 不得把未实现设计提前写入 `GLOBAL_DEV_SPEC.md`。

## 15. 完成定义

本规格从 `PROPOSED` 变为 `DONE` 前，必须满足：

- audit error code、allowlist registry、EOF heredoc recovery 和 warning finding 已实现；
- protected-path 对抗性测试证明白名单不是审计绕过；
- 原生 exec/process 工具面保持不变；
- AGENTS canonical contract 已更新；
- CLI 取消状态机、owned process 清理、workspace 收口和持久终态已实现；
- 本轮失败样本的只读 replay 结果可解释且不会自动改写历史成绩；
- 所有专项与回归测试通过；
- `GLOBAL_DEV_SPEC.md` 与当前实现一致；
- 实现已提交到 Git。
