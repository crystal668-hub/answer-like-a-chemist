# Skill Runner Deadline, Process Group, and Async Skill Specification

状态：`DRAFT`

日期：2026-07-23

适用项目：OpenClaw benchmark agent local skill execution

## 1. 摘要

当前 `WorkspaceUvSkillRunner` 对每次本地 skill 脚本调用设置固定 60 秒
wall-clock timeout。这个上限覆盖 `uv` 环境解析、Python 启动、脚本读取输入、
本地计算、网络上传、远程任务轮询、结果下载、文件写入和 stdout/stderr 收集的
完整过程。

真实 benchmark transcript 已出现 MACE 调用在 60 秒被终止，随后 agent 绕过
canonical wrapper，直接执行 `uv run ... skills/mace/scripts/mace_run.py`。同时，
当前 MACE 脚本使用同步无限轮询和多行文本 stdout，不符合 SkillRunner 要求的单一
JSON object 输出契约。

本规格进行三个直接相关的修改：

1. 删除 SkillRunner 固定 60 秒默认限制，改用 benchmark attempt 提供的绝对
   deadline；独立调用且没有 deadline 时，SkillRunner 不自行设置运行上限。
2. 每次 `uv run` 使用独立进程组；timeout、取消或父级终止时，按
   `SIGTERM -> grace -> SIGKILL` 终止整个进程组，并用 cleanroom lease 提供异常
   退出后的兜底清理。
3. 将 MACE 改为短生命周期的异步操作协议，拆分为 `submit`、`status`、
   `collect` 和 `cancel`，禁止 benchmark 路径在单次脚本调用中无限轮询。

本规格不修改全技能 health check 或 skill exposure 规则。全技能声明式 runtime
contract、统一 probe 和 fail-closed 暴露将作为后续独立模块化优化处理。

## 2. 背景与根因

### 2.1 固定 timeout 与上层预算冲突

`benchmarking/skills/runtime.py` 当前定义：

```python
timeout_seconds: int = 60
```

`scripts/run_skill.py` 未传入覆盖值，因此 agent 通过 canonical wrapper 执行的所有
skill 都使用 60 秒。这个值没有从 benchmark record、skill 类型、provider SLA、
attempt 剩余时间或脚本请求推导，是独立硬编码的第二层预算。

benchmark 已经拥有上层预算：

- single-LLM 每题默认 900 秒；
- ChemQA 每题默认 1800 秒；
- single-LLM `--no-timeout` 模式保留 24 小时进程级 guard。

固定 60 秒会在上层预算仍充足时提前终止合法化学计算，并使 script 自己的
120 秒网络 timeout、轮询策略或远程任务生命周期失效。

### 2.2 `subprocess.run()` 不提供完整进程树回收

当前 runner 使用：

```python
subprocess.run(command, timeout=..., capture_output=True)
```

timeout 只直接处理 `subprocess.run()` 启动的进程。若 `uv`、Python skill 或 skill
内部工具继续生成子进程，终止顶层 PID 不构成对完整进程树的确定回收。

benchmark-cleanroom 已支持基于 lease 的 PID/PGID 清理，但 single-LLM skill 调用
目前不创建独立 PGID，也不登记 skill process lease。ChemQA 有 cleanroom 环境变量，
single-LLM 没有对应的通用 skill-process lease 生命周期。

### 2.3 MACE 同步接口不适合 benchmark tool call

当前 `skills/mace/scripts/mace_run.py` 在一次进程内完成：

```text
上传输入 -> 提交远程任务 -> 无限轮询 -> 下载结果
```

上传、提交和下载各自允许最长 120 秒，轮询没有总体 deadline。脚本还向 stdout
打印多行中文日志和格式化 JSON 片段，而 SkillRunner 成功契约要求 stdout 可整体
解析为一个 JSON object。

因此，即使仅删除 60 秒限制，MACE 仍可能无限占用 tool call，或者在成功后被
SkillRunner 判为 `invalid_output`。

## 3. 目标

### 3.1 功能目标

- SkillRunner 不再拥有与 benchmark 总预算无关的固定 60 秒默认上限；
- bounded attempt 中，每次 skill 调用不得越过当前 attempt 为 skill 保留的绝对
  deadline；
- 无 deadline 的独立调用不被 SkillRunner 任意截断；
- 每次 skill 调用拥有独立进程组，不与 agent、OpenClaw 或 benchmark parent 共用
  PGID；
- timeout 和取消能终止 `uv`、skill Python 及其后代进程；
- runner 异常退出后，cleanroom 能从 lease 找到并清理仍存活的 skill process group；
- MACE 每次调用只执行一个有界操作并输出一个 JSON object；
- MACE 远程 job identity 和状态持久化在 attempt scratch output 中，可被后续
  `status` 或 `collect` 调用读取；
- agent 不需要、也不得绕过 `scripts/run_skill.py` 执行 MACE source。

### 3.2 质量目标

- deadline 只有一个上层事实来源，runner 只消费，不重新发明预算；
- timeout payload 能区分 attempt deadline、显式 runner timeout 和外部取消；
- process termination 结果可审计，包括 PID、PGID、signal 和 escalation；
- MACE submit 重试不会在结果不确定时静默创建重复远程任务；
- stdout 始终保持 machine-readable，诊断进入 payload 或 stderr；
- 新逻辑可使用短时 synthetic subprocess 和 mocked provider 完成自动化测试，测试
  不需要真实等待 60 秒或调用真实 MACE provider。

## 4. 非目标

本规格明确不包含：

- 全技能可用性预检、声明式 runtime manifest 或统一 health probe；
- 修改 `benchmarking/skills/health.py` 的检测策略；
- 修改 chemistry inventory、skill tree 或 `single_agent_exposure`；
- 将所有现有同步 provider skill 一次性改为异步；
- 跨 benchmark attempt 的 deferred-job scheduler；
- benchmark 结束后自动恢复数小时或数天的远程任务；
- 容器、独立 OS 用户、cgroup、syscall sandbox 或完整操作系统安全边界；
- 对 Windows 提供与 POSIX process group 完全相同的强保证；
- 重构通用 provider error substring classifier，timeout 分类除外；
- 为当前不兼容 skill 增加临时兼容 stdout parser。

## 5. 核心不变量

### SRD-01：无固定 60 秒默认值

`WorkspaceUvSkillRunner` 的默认 timeout 必须是 `None`。代码、模板、skill 文档和
测试不得继续把 60 秒作为所有 skill 的隐式通用上限。

### SRD-02：Attempt deadline 是 bounded benchmark 的时间权威

bounded benchmark attempt 必须向 agent subprocess 注入绝对 monotonic deadline。
SkillRunner 只能计算剩余时间并消费该 deadline，不得在其下再应用更短的隐藏默认值。

### SRD-03：Finalization reserve 在上层扣除

workflow 必须在发布 skill deadline 前扣除 finalization reserve。SkillRunner 不解释
题型、不估算回答时间，也不自行决定 reserve。

### SRD-04：一个调用一个独立进程组

每次 `uv run --project ... python <script>` 必须在新的 session/process group 中启动。
终止 skill group 不得向 agent 或 benchmark parent 所在进程组发送 signal。

### SRD-05：Timeout 必须终止整个进程组

一旦 runner deadline 到期，必须先向 skill PGID 发送 `SIGTERM`，在 grace period
后仍有进程时发送 `SIGKILL`。只终止顶层 PID 不满足本规格。

### SRD-06：MACE 调用不执行无限轮询

benchmark 可调用的 MACE 操作必须在一次上传、提交、查询、下载或取消请求后返回。
脚本内部不得使用无 deadline 的 `while True` 等待远程终态。

### SRD-07：成功 stdout 是单一 JSON object

所有新的 MACE 操作和 SkillRunner 自身都必须在 stdout 只写一个 JSON object。
进度文字不得与 JSON 混写；诊断写入 JSON 字段或 stderr。

### SRD-08：未知提交结果不得盲目重试

若 provider 可能已经接受 submit，但 client 未收到可确认响应，MACE job state 必须
进入 `submission_unknown`。在没有 provider reconciliation 证据前，不得自动生成新
task ID 再次提交。

### SRD-09：Health 行为保持不变

本规格实施后，现有 startup health report schema、allowlist 过滤方式和
`REQUIREMENT_OVERRIDES` 保持不变。不得借本次修改引入 MACE-only health 特例。

## 6. Deadline 契约

### 6.1 环境变量

bounded runner 在启动 agent turn 前设置：

```text
BENCHMARK_SKILL_DEADLINE_MONOTONIC=<absolute float>
```

该值使用 `time.monotonic()` 的同机绝对时钟。workflow 计算：

```text
skill_deadline = attempt_start_monotonic
               + attempt_timeout_seconds
               - finalization_reserve_seconds
```

本规格固定默认 reserve：

```text
single-LLM: 30 seconds
ChemQA role turn: 30 seconds
```

reserve 是 workflow policy，应在现有 convergence/runner metadata 中持久化。未来调整
该值不需要修改 SkillRunner。

`--no-timeout` 模式不设置 `BENCHMARK_SKILL_DEADLINE_MONOTONIC`。现有 24 小时
wrapper guard 继续作为上层兜底，不复制到 SkillRunner。

### 6.2 Runner 数据模型

`WorkspaceUvSkillRunner` 调整为：

```python
@dataclass(frozen=True)
class WorkspaceUvSkillRunner:
    workspace_root: Path
    execution_cwd: Path | None = None
    uv_executable: str | None = None
    timeout_seconds: float | None = None
    deadline_monotonic: float | None = None
    termination_grace_seconds: float = 5.0
```

有效 timeout 计算规则：

```text
explicit = timeout_seconds, if provided
remaining = deadline_monotonic - time.monotonic(), if provided

both present: min(explicit, remaining)
one present: that value
neither present: None
```

若 `remaining <= 0`，runner 不启动 subprocess，直接返回 `execution_timeout`。

`scripts/run_skill.py` 从环境读取并严格解析 deadline。非法、NaN 或 infinite 值返回：

```json
{
  "available": false,
  "error_kind": "invalid_execution_deadline",
  "reason": "BENCHMARK_SKILL_DEADLINE_MONOTONIC must be a finite number",
  "runner": "workspace_uv"
}
```

agent CLI 不新增可自由放大 deadline 的 `--timeout-seconds` 参数。可信测试或内部调用方
可以直接构造 `WorkspaceUvSkillRunner(timeout_seconds=...)`。

### 6.3 Timeout payload

deadline 到期后返回 exit code `2` 和：

```json
{
  "available": false,
  "error_kind": "execution_timeout",
  "reason": "skill execution exceeded the available deadline",
  "runner": "workspace_uv",
  "command": ["uv", "run", "--project", "..."],
  "timeout": {
    "source": "attempt_deadline",
    "limit_seconds": 169.4,
    "elapsed_seconds": 169.5
  },
  "termination": {
    "pid": 1234,
    "pgid": 1234,
    "term_sent": true,
    "kill_sent": false,
    "remaining": false
  }
}
```

timeout 不再归类为 `provider_failure`。stdout/stderr 可按现有 2000 字符上限附带 excerpt。

## 7. Process Group 执行契约

### 7.1 模块归属

`benchmarking.runtime.subprocess_utils` 已拥有共享 subprocess helper。新增 POSIX
process-group executor：

```python
def run_process_group(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float | None,
    termination_grace_seconds: float,
    on_started: Callable[[int, int], None] | None = None,
) -> ProcessGroupResult:
    ...
```

`benchmarking.skills.runtime` 负责把 skill command、deadline 和 JSON error contract
绑定到该 helper，不在多个模块复制 signal 逻辑。

### 7.2 启动

POSIX 使用：

```python
subprocess.Popen(
    command,
    cwd=str(cwd),
    env=env,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
)
```

新 session leader 的 PID 同时作为初始 PGID。启动成功后立即调用 `on_started(pid,
pgid)` 写 lease；lease 写入失败时必须终止新进程组并返回结构化错误，不能让未登记进程
继续运行。

### 7.3 正常结束

顶层进程退出后继续收集 stdout/stderr，并检查该 PGID 是否仍有已登记后代进程。正常
skill 不应 daemonize。若顶层成功退出但进程组仍存活，runner 终止残留组并返回：

```text
error_kind = orphaned_skill_processes
```

### 7.4 Timeout 和取消

终止算法固定为：

```text
os.killpg(pgid, SIGTERM)
-> wait up to termination_grace_seconds
-> if group still exists: os.killpg(pgid, SIGKILL)
-> reap direct child
-> postcheck group absence
```

`SIGINT`、`SIGTERM` 和 `KeyboardInterrupt` 必须进入同一个终止路径。signal handler
只操作当前调用创建的 PGID，然后恢复或传播原 signal 语义。

不得对 `os.getpgrp()`、PID 0、PID 1、runner parent PGID 或无法证明由当前调用创建的
PGID 调用 `killpg`。

### 7.5 非 POSIX 行为

本规格的完整 guarantee 适用于 macOS/Linux。Windows 可使用 direct child terminate
fallback，但必须在 payload 中标记：

```text
termination.process_group_supported = false
```

Windows process-tree parity 是后续独立工作，不得伪装为已满足。

## 8. Cleanroom Lease 契约

### 8.1 通用环境

single-LLM 和 ChemQA skill-enabled attempt 都必须设置：

```text
BENCHMARK_CLEANROOM_RUN_ID=<run-scoped id>
BENCHMARK_CLEANROOM_LEASE_DIR=<output-root>/cleanroom/leases
BENCHMARK_CLEANROOM_SESSION_ID=<agent session id>
```

workflow 在开始可能运行 skill 的 attempt 前创建或更新 cleanroom manifest，并注册为
pending cleanup。manifest 必须覆盖 single-LLM 和 ChemQA，而不再只依赖 ChemQA launch
路径提供 lease 环境。

### 8.2 Skill process lease

SkillRunner 启动进程组后写兼容 `benchmark-cleanroom-lease` version 1 的 lease：

```json
{
  "kind": "benchmark-cleanroom-lease",
  "version": 1,
  "run_id": "...",
  "role": "skill",
  "slot": "mace",
  "session_id": "...",
  "pid": 1234,
  "pgid": 1234,
  "ppid": 1200,
  "cwd": ".../scratch",
  "status": "active",
  "script": "skills/mace/scripts/mace_run.py",
  "command_sha256": "...",
  "started_at": "2026-07-23T12:00:00+0800"
}
```

lease 不保存完整 command 或 environment，避免持久化 token、API key 或用户输入。

正常结束且 postcheck clean 后删除 lease。timeout 终止成功后也删除 lease，但 timeout
payload 保留 termination evidence。若 runner 崩溃，lease 保留供 cleanroom 使用。

### 8.3 Cleanup 校验

cleanroom 对 skill lease 发送 `killpg` 前必须验证：

- lease run ID 与 manifest 一致；
- PGID 为正数且不同于 cleanup 自身 PGID；
- process snapshot 中至少一个成员 PID 属于该 PGID；
- 成员 command 能匹配 lease 中记录的 script path 或对应 runner identity；
- PID/PGID 不匹配时只记录 stale lease，不发送 signal。

cleanup 完成后记录 term/kill/postcheck，并删除已确认无进程的 stale lease。

### 8.4 生命周期

```text
benchmark start
-> recover persisted pending cleanroom manifests
-> create/register current manifest
-> skill runner creates/removes process leases
-> attempt timeout or signal invokes cleanup
-> run completion invokes cleanup and unregisters manifest
```

不可捕获的 machine power loss 或 `SIGKILL` 依赖下一次 benchmark startup 的 persisted
manifest recovery，不依赖原进程内 registry 仍然存在。

## 9. MACE 异步协议

### 9.1 Canonical CLI

`skills/mace/scripts/mace_run.py` 直接替换为统一入口：

```bash
python scripts/run_skill.py \
  --workspace-root "$BENCHMARK_PROJECT_ROOT" \
  --execution-cwd "$BENCHMARK_SKILL_SCRATCH_DIR" \
  --script skills/mace/scripts/mace_run.py -- \
  --request-json requests/mace.json \
  --output-dir outputs/mace \
  --json
```

`--request-json`、`--output-dir` 和 `--json` 为必需参数。旧的直接
`--inputfile`、`--query_task`、`--download_result` 和内部默认轮询接口不保留为
benchmark compatibility path。

### 9.2 通用 request envelope

所有操作使用：

```json
{
  "schema_version": 1,
  "operation": "submit|status|collect|cancel",
  "client_request_id": "record-id:mace:attempt-0",
  "job": {},
  "input": {},
  "options": {}
}
```

`client_request_id` 在 submit 时必需，其他操作从 `job.json` 继承并校验。
所有操作必须使用与 submit 相同的 `--output-dir`。该目录下的 `job.json` 是 job state
的唯一权威位置；request 不接受任意 job-state filesystem path。

### 9.3 Submit

本地文件 submit request：

```json
{
  "schema_version": 1,
  "operation": "submit",
  "client_request_id": "property-calc-002:mace:attempt-0",
  "input": {"path": "requests/structures.zip"},
  "options": {"created_by": "benchmark-single-skills-on"}
}
```

submit 固定执行：

```text
validate request
-> generate task_id
-> atomically persist submitting job.json
-> upload local file when needed
-> submit provider request
-> atomically persist provider job_id and submitted state
-> return immediately
```

成功 payload：

```json
{
  "status": "success",
  "operation": "submit",
  "terminal": false,
  "job": {
    "provider": "mace",
    "client_request_id": "property-calc-002:mace:attempt-0",
    "job_id": "12345",
    "task_id": "uuid",
    "state": "submitted",
    "poll_after_seconds": 10
  },
  "artifacts": ["outputs/mace/job.json"],
  "diagnostics": [],
  "errors": []
}
```

若网络 timeout 或断连发生在 submit request 发送后且无法确认 provider 是否接受，写入
`submission_unknown` 并返回 handled error。不得删除 `job.json` 或自动换 task ID 重试。

### 9.4 Status

status request 只读取 job state 并执行一次 provider GET：

```json
{
  "schema_version": 1,
  "operation": "status",
  "job": {"task_id": "expected-uuid"}
}
```

脚本固定读取 `<output-dir>/job.json`。request 中可选的 `task_id` 只用于防止 agent
误用其他 job 的 output directory；它必须与持久化 state 一致，不参与路径解析。

provider 状态映射为：

```text
PENDING    -> queued
DISPATCHED -> queued
RUNNING    -> running
SUCCEEDED  -> succeeded
FAILED     -> failed
CANCELLED  -> cancelled
TIMEOUT    -> failed
other      -> unknown
```

status 不 sleep、不循环。非终态返回 `terminal=false` 和 `poll_after_seconds`；终态返回
`terminal=true`。

### 9.5 Collect

collect 只允许 job state 为 `succeeded`。它执行一次 download request，将 archive 写入：

```text
outputs/mace/artifacts/<provider-filename>.zip
```

下载使用 temporary file、flush/fsync 和 atomic rename。成功后更新 `job.json` 为
`collected`，返回 artifact 相对路径、大小和 SHA-256。collect 重试若目标 artifact hash
已存在且匹配，必须幂等返回，不重复下载。

### 9.6 Cancel

当前 provider 没有已验证的 cancel endpoint。为保持 operation envelope 稳定，cancel
返回 handled error：

```json
{
  "status": "error",
  "operation": "cancel",
  "terminal": false,
  "job": {"state": "running"},
  "errors": [
    {
      "kind": "unsupported_operation",
      "message": "MACE provider does not expose a verified cancel endpoint"
    }
  ]
}
```

不得将本地 skill process group 的终止误报为远程 job 已取消。

### 9.7 Process exit contract

可识别的 request validation、provider error、unsupported operation 和远程 terminal
failure 都必须打印合法 JSON 并以进程 exit code `0` 返回。payload 使用
`status=error` 和 typed `errors` 表达业务失败；SkillRunner 仍可把脚本识别为
`available=true`。

只有未捕获异常、Python 启动失败或无法建立 machine-readable payload 时使用非零 exit，
由 SkillRunner 归类为 execution unavailable。

### 9.8 Job state

`outputs/mace/job.json` 使用 schema version 1，至少包含：

```text
provider
client_request_id
task_id
job_id
state
input reference
created_at
updated_at
poll_after_seconds
last_provider_payload_excerpt
artifact metadata
```

不得持久化 API key、Authorization header、完整环境或未脱敏 provider response。

允许状态转换：

```text
new -> submitting -> submitted -> queued -> running -> succeeded -> collected
                  -> submission_unknown
                                      -> failed
                                      -> cancelled
                                      -> unknown
```

已处于 terminal state 的 job 不得回退到 `queued` 或 `running`，除非 provider response
明确包含更高版本/更新时间证据；本规格第一版直接拒绝 terminal regression。

若后续操作发现 `job.json` 长时间停留在 `submitting`，说明本地进程可能在 provider
响应前被终止。脚本必须先原子转换为 `submission_unknown`，不得自动再次 submit。
只有 provider 提供可按 `task_id` reconcile 的接口后，才允许从该状态恢复为
`submitted`。

本地 SkillRunner timeout 或 process-group cleanup 只停止本地上传、请求、查询或下载
进程。若远程 provider 已接受任务，远程 job 可能继续运行；本规格不把本地 termination
映射为远程 `cancelled`。

## 10. Agent 调用流程

agent 的 MACE 工具流程固定为：

```text
write submit request
-> run_skill submit
-> inspect returned job state
-> perform other reasoning or wait until poll_after_seconds
-> run_skill status once
-> repeat bounded status calls while attempt deadline permits
-> when succeeded, run_skill collect
-> inspect downloaded result
-> answer
```

当剩余 attempt 时间不足以完成下一次 status/collect 时，agent 必须停止新 tool call，说明
远程任务仍在进行并完成当前 benchmark answer。当前 benchmark 不持久化 deferred record，
也不会在后续 attempt 自动接续这个 job。

## 11. 文件级改动

### 11.1 `benchmarking/skills/runtime.py`

- 默认 timeout 改为 `None`；
- 增加 `deadline_monotonic` 和 grace 配置；
- 使用 shared process-group executor；
- timeout 改为 `execution_timeout`；
- 保留 JSON object validation 和现有 unavailable payload 风格；
- 将 `uv` 解析放入结构化异常边界；
- 在启动前区分 invalid execution cwd 与 missing executable；
- 使用 cleanroom process lease callback。

### 11.2 `benchmarking/runtime/subprocess_utils.py`

- 增加 `ProcessGroupResult`、process-group timeout/termination evidence；
- 实现 POSIX `start_new_session`；
- 实现 TERM/grace/KILL/postcheck；
- 支持 injectable clock、sleep、Popen 或 signal helper，避免慢测试；
- 不改变现有普通 `run_subprocess()` 调用方语义。

### 11.3 `scripts/run_skill.py`

- 解析 `BENCHMARK_SKILL_DEADLINE_MONOTONIC`；
- 解析 cleanroom run/session/lease 环境；
- 构造新的 runner 参数；
- 保持 stdout 单一 JSON 和 exit code `0/2`；
- 不新增 agent 可任意扩大预算的 CLI flag。

### 11.4 `benchmarking/workflow/runners/single_llm.py`

- bounded attempt 注入 skill deadline；
- 注入通用 cleanroom run/session/lease 环境；
- 在 runner metadata 记录 deadline policy 和 reserve；
- attempt 结束后触发 cleanroom postcheck；
- `--no-timeout` 时不注入 skill deadline。

### 11.5 ChemQA runner

- 复用相同 skill deadline 变量；
- 保留现有 cleanroom run/lease 变量并补齐 session ID；
- 不修改 DebateClaw state machine、recovery 或 Artifact Flow。

### 11.6 `benchmarking/runtime/cleanroom.py` 和 `skills/benchmark-cleanroom/`

- cleanroom manifest 支持 single-LLM skill process leases；
- startup 恢复 persisted pending manifests；
- cleanup 校验 skill PGID 和 command identity；
- 清理成功后移除 stale lease；
- 保持 sessions、transcripts、run artifacts 和 archived workspaces 的 retention policy。

### 11.7 `skills/mace/`

- 替换 `mace_run.py` CLI 和 stdout contract；
- 增加 job-state atomic persistence helper；
- 增加 request fixtures 和 mocked provider tests；
- 更新 `SKILL.md` 与 references，移除直接 Python、curl 和内部无限轮询调用指导；
- 所有示例通过 canonical `scripts/run_skill.py`。

### 11.8 文档

实现完成后更新 `GLOBAL_DEV_SPEC.md` 的 skill execution、attempt deadline、cleanup 和
MACE async flow。当前规格为计划文档，不在实现前把未完成行为写入 current-state spec。

## 12. 测试规格

### 12.1 SkillRunner unit tests

- 无 explicit timeout 且无 deadline 时向 executor 传 `timeout=None`；
- explicit timeout 正确传递；
- deadline 计算使用剩余时间；
- explicit timeout 与 deadline 同时存在时取较小值；
- deadline 已过期时不启动进程；
- missing `uv` 返回结构化 `missing_executable`，不 traceback；
- missing execution cwd 返回独立错误，不误报 executable；
- timeout payload 包含 source、elapsed 和 termination evidence；
- invalid deadline 环境返回 `invalid_execution_deadline`。

### 12.2 Process group tests

使用 synthetic script 启动一个 child 和一个 grandchild：

- 正常完成时两个进程自然退出；
- timeout 时两个进程都收到 TERM；
- 忽略 TERM 时两个进程都在 grace 后收到 KILL；
- cleanup postcheck 确认 PGID 无成员；
- runner parent 和测试进程保持存活；
- lease 写入失败时新进程组被立即回收；
- runner 被 SIGTERM 时 lease/cleanroom 路径能回收 group。

测试 timeout 使用亚秒级值，不真实等待 60 秒。

### 12.3 Cleanroom tests

- single-LLM manifest 能发现 skill lease；
- run ID 不匹配的 lease 不清理；
- current PGID 和 cleanup PGID 永不成为 target；
- stale/reused PID 不满足 command/PGID 校验时不发送 signal；
- active skill PGID 执行 TERM/KILL/postcheck；
- persisted manifest 能在新 benchmark process startup 被恢复；
- cleanup 不删除 sessions、transcripts 或 artifact roots。

### 12.4 MACE contract tests

mock `urlopen` 或 provider client，覆盖：

- submit with local upload；
- submit with existing URL；
- submitted/queued/running/succeeded/failed 状态映射；
- status 恰好请求一次且不 sleep；
- collect atomic download、hash 和幂等重试；
- cancel 返回 unsupported 而不修改远程状态；
- submit response 丢失进入 `submission_unknown`；
- terminal regression 被拒绝；
- stdout 可整体 `json.loads()`；
- provider secret 不进入 job state 或 runner payload。

### 12.5 Integration tests

- canonical wrapper 执行一个超过旧 60 秒语义但在 injected test deadline 内完成的
  synthetic skill；该测试使用 fake/injected executor，不真实等待 60 秒；
- bounded single-LLM attempt 中 skill deadline 小于 answer deadline reserve；
- `--no-timeout` 不注入 skill deadline；
- MACE submit/status/collect 全部通过 `scripts/run_skill.py`；
- transcript 不出现直接 `uv run ... skills/mace/...` fallback；
- attempt timeout 后没有对应 `uv`、Python 或 skill descendant process。

### 12.6 Regression suite

至少运行：

```bash
uv run pytest tests/test_skill_runtime.py -q
uv run pytest tests/test_skill_health.py -q
uv run pytest tests/test_agent_workspace.py -q
uv run pytest tests/test_benchmark_cleanroom.py -q
uv run pytest tests/test_benchmarking_cli.py -q
uv run pytest tests/test_benchmark_test.py -q
uv run pytest skills/mace/tests -q
```

若实际测试文件名不同，实施时使用 `rg --files tests skills/mace` 定位对应 suite，并在提交
说明中记录最终命令。

## 13. 实施顺序

### 阶段 A：Process group 基础设施

1. 为现有 SkillRunner 补齐 deadline 和 process-group unit test；
2. 在 `subprocess_utils` 实现 process-group executor；
3. SkillRunner 切换 executor，删除固定 60 秒；
4. 修复 missing `uv` 和 invalid cwd 的结构化边界；
5. 运行 SkillRunner 与 subprocess 相关测试。

### 阶段 B：Cleanroom 接入

1. 定义通用 skill process lease 环境；
2. single-LLM 创建/register cleanroom manifest；
3. SkillRunner 写入和清除 lease；
4. cleanup 增加 identity validation 和 persisted recovery；
5. 运行 signal、timeout、stale lease 和 retention 测试。

### 阶段 C：MACE 异步替换

1. 先写 provider mock 和四操作 contract tests；
2. 实现 job state 和 atomic write；
3. 实现 submit/status/collect/cancel；
4. 删除 benchmark 同步无限轮询入口；
5. 更新 MACE skill 文档；
6. 运行 MACE unit 和 canonical wrapper integration tests。

### 阶段 D：端到端验证和文档

1. 运行定向 regression suite；
2. 运行一个不调用真实付费计算的 local benchmark smoke；
3. 检查 transcript 只通过 canonical wrapper 调用 MACE；
4. 检查 timeout 后无残留进程；
5. 更新 `GLOBAL_DEV_SPEC.md` current-state sections；
6. 按 repository workflow 提交全部实现和测试。

## 14. 验收标准

全部条件满足后，本规格才可标记为 `DONE`：

- `WorkspaceUvSkillRunner.timeout_seconds` 默认值为 `None`；
- repository 中不存在对所有 skill 生效的隐式 60 秒 runner limit；
- bounded attempt 的 skill 调用只受显式 timeout 和 attempt skill deadline 中较小者限制；
- deadline timeout 返回 `execution_timeout`，不返回 `provider_failure`；
- timeout、SIGTERM 和 SIGINT 后 process-group postcheck 无存活成员；
- runner 异常退出后 cleanroom 能根据 persisted lease 回收 skill PGID；
- MACE benchmark path 不包含无限轮询；
- MACE submit/status/collect/cancel stdout 都是单一 JSON object；
- MACE job state 可跨同一 attempt 的多次 tool call 读取；
- uncertain submit 不产生自动重复 job；
- MACE 文档不再指导 agent 绕过 `scripts/run_skill.py`；
- 现有 skill health report 和 exposure 行为无变化；
- 所有定向和相关 regression tests 通过；
- `GLOBAL_DEV_SPEC.md` 已更新为实现后的真实行为。

## 15. 后续独立优化

全技能可用性预检应在本规格完成后单独设计和实施。后续模块至少需要解决：

- 全 inventory runtime profile；
- 每个 exposed skill 的 canonical entrypoint 和无副作用 probe；
- Python dependency、external executable、credential 和 provider readiness；
- health report、OpenClaw config、prompt tree 和 access policy 的同源 effective list；
- 缺少 runtime contract 时 fail closed；
- 去除按 skill 名称增长的 `REQUIREMENT_OVERRIDES`。

这些内容不应以 MACE 特例夹带进入本次 runner/async 修改。
