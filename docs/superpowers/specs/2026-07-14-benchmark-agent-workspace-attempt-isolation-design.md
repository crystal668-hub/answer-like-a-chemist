# Benchmark Agent Workspace Attempt Isolation Specification

状态：`PROPOSED`

日期：2026-07-14

适用项目：OpenClaw chemistry benchmark orchestration

## 1. 摘要

当前 benchmark runner 为固定 agent id 复用固定 workspace。历史运行留下的
`.benchmark-scratch/`、根目录脚本、XYZ/CIF、xTB 输出、请求 JSON、下载文件和 Git
历史会继续存在。后续 agent 即使使用新的 session id 和 scratch 路径，也可能读取这些
历史内容，造成跨 attempt、跨 record 或跨 run 的答案污染。

本规格规定：每个 benchmark attempt 使用一个新建的、受 sentinel 管理的完整
workspace。attempt 结束后，不选择性删除文件，而是把整个 workspace 移出活动路径并归档，
随后从可信模板重建同一路径。下一个 attempt 只能看到模板文件和自己的 scratch。

本规格是 workspace 生命周期契约，不改变 benchmark package 的评分 API、题目内容、模型、
skill allowlist 或结果评分规则。

## 2. 已确认决策

1. 隔离单位是 `attempt`，不是 run、group、record 或 session store pointer。
2. 轮换单位是完整 agent workspace，不只是 `.benchmark-scratch/`。
3. attempt workspace 不包含 `.git`，也不从历史 agent workspace 做 Git clone。
4. attempt 结束后归档完整 workspace，不直接销毁其中的脚本、数据和工具输出。
5. 下一个 attempt 开始前必须从可信模板重建活动 workspace。
6. `single_llm_skills_on`、`single_llm_skills_off`、ChemQA 全部 role slots 和 benchmark
   judge 都适用同一隔离原则。
7. 所有 single-LLM attempt 都获得 scratch 路径和 scratch 使用指令，不再只覆盖
   skills-on group。
8. workspace 隔离、归档或污染审计失败时 fail closed；该 attempt 不评分。
9. 已存在的固定 legacy workspace 不自动删除，也不再被新 run 配置引用。
10. verifier-grounded scorer、wheel、gold、sample answers 和 verifier specs 继续留在 agent
    workspace 之外。

## 3. 背景与问题边界

### 3.1 当前污染面

固定 workspace 中已经存在以下类别的历史数据：

- `.benchmark-scratch/<record>/<session>/` 下的 requests、outputs、notes 和临时脚本；
- workspace 根目录下的 `*.py`、`*.xyz`、`*.json`、xTB restart/log/property 文件；
- 与历史任务名称直接关联的目录和候选结构；
- workspace `.git` 中仍可恢复的历史或已删除文件；
- skills-off agent 直接在 workspace 根目录生成的文件。

因此，“创建新 scratch”本身不构成隔离。选择性删除扩展名也不能覆盖 Git 历史、任意目录名、
未知工具输出或未来新增的文件类型。

### 3.2 本规格解决的问题

- 防止正常 benchmark agent 从其活动 workspace 看到任何先前 attempt 的产物；
- 防止并行 group、retry、record 和 run 共享可写 workspace；
- 在保留审计材料的同时，把历史材料移出后续 agent 的活动 workspace；
- 给污染检测、失败分类、恢复和验收测试提供稳定契约。

### 3.3 非目标

- 不把同一 OS 用户下的 agent 进程视为强对抗安全边界；
- 不通过本规格阻止恶意进程遍历整个 home 目录；强隔离需要容器、sandbox 或独立用户；
- 不删除历史 benchmark run、transcript、session store 或既有 legacy workspace；
- 不改变 verifier-grounded package 的 PyPI 发布、安装或 hash pin 方案；
- 不允许 attempt 之间共享未显式进入 benchmark 输入契约的缓存或推理产物；
- 不实现通用文件备份系统或用户 workspace 清理工具。

### 3.4 威胁模型

本规格的基础威胁模型是“防历史记录意外污染”：agent 可能列出当前 workspace、递归搜索文件、
运行 Git 命令、读取自己能发现的路径或在根目录写文件，但不会主动攻击 OS 隔离边界。

对于主动访问父目录、其他 run archive、verifier source repo 或 scorer runtime 的行为，系统必须
通过 tool/transcript audit 检测并把 attempt 标为污染。若未来要求在技术上禁止这些访问，必须在
本规格之上增加 filesystem sandbox；不能仅依赖 prompt 或 chmod 宣称强隔离。

## 4. 术语

- `run_id`：一次 benchmark 输出根对应的稳定标识。
- `invocation_id`：一次 CLI 进程调用的 UUID。使用 `--exact-output-dir` 续跑时，run_id 可相同，
  invocation_id 必须不同。
- `group_id`：实验组，例如 `single_llm_skills_on`。
- `record_id`：当前 benchmark record 的 id。
- `attempt_index`：同一 record 内从 0 开始的执行序号；timeout retry 递增。
- `session_id`：传给 OpenClaw 的唯一 session id。
- `active workspace`：OpenClaw 当前配置中该 agent 的 workspace 路径。
- `attempt archive`：attempt 结束后从 active workspace 原子移出的完整目录。
- `workspace template`：canonical source 中经过审查的最小只读输入集合。
- `workspace lease`：一个 attempt 对一个 active workspace 的排他所有权记录。
- `sentinel`：证明目录由 benchmark workspace manager 管理的 JSON 文件。

## 5. 核心不变量

### BAWI-01：Attempt 独占

一个 active workspace 在任意时刻最多绑定一个 `(run_id, invocation_id, group_id,
record_id, attempt_index, agent_id, session_id)` 元组。

### BAWI-02：无历史可见内容

OpenClaw 开始 attempt 前，active workspace 中只能存在：

- 本 attempt 的 sentinel；
- 可信模板定义的控制文件；
- 本 attempt 新建的 scratch 目录；
- 本 attempt 明确物化的输入 bundle 引用或副本。

### BAWI-03：无 Git 历史

active workspace 及其子目录不得包含 `.git` 文件或目录。发现 `.git` 时 preflight 必须失败。

### BAWI-04：完整轮换

attempt 完成、失败、超时、取消或抛出异常后，都必须执行 seal。seal 的对象是完整 active
workspace，不按文件名或扩展名筛选。

### BAWI-05：先收集后轮换

runner 必须先完成 OpenClaw stdout、session postflight、transcript 路径、tool audit 和必要结果
读取，再 seal workspace。seal 不得早于这些读取。

### BAWI-06：归档后重建

seal 成功后，旧 workspace 不得继续作为 active workspace。下一 attempt 必须重新物化模板和
sentinel，不得在旧目录上做增量清理。

### BAWI-07：路径所有权

workspace manager 只能操作 run-scoped benchmark runtime root 下且 sentinel 匹配的路径。
非托管路径、路径穿越、错误 sentinel 或符号链接一律 fail closed。

### BAWI-08：并发隔离

不同 group、agent 或 invocation 的 active workspace 路径必须不同。即使
`--single-agent-id-override` 令 agent id 相同，也不得共享 workspace。

### BAWI-09：归档不可回挂

attempt archive 不得通过 symlink、bind mount、workspace-local shortcut 或 prompt 路径重新暴露
给后续 attempt。

### BAWI-10：失败不评分

workspace preflight、lease、seal、archive 或 contamination audit 任一失败时，attempt 必须产生
结构化 execution failure，`evaluable=false` 且 `scored=false`。

## 6. 路径布局

### 6.1 Runtime 根

新增 run-scoped runtime 根：

```text
~/.openclaw/benchmark/workspaces/runs/
  <run_id>/
    <invocation_id>/
      active/
        <group_id>/
          <agent_id>/
      locks/
        <group_id>--<agent_id>.lock
```

`run_id`、`invocation_id`、`group_id` 和 `agent_id` 必须经过现有 slug 规则处理，并保留 hash
后缀防碰撞。

### 6.2 Attempt scratch

每个 active workspace 内只创建当前 attempt 的 scratch：

```text
<active-workspace>/
  .benchmark-workspace.json
  AGENTS.md
  TOOLS.md                 # 需要时
  .openclaw/
    workspace-state.json  # 需要时生成，不从历史目录复制
  .benchmark-scratch/
    <record-slug>/
      <session-id>/
        requests/
        outputs/
        notes/
```

因为完整 workspace 会在 attempt 后轮换，所以 `.benchmark-scratch/` 下不得存在其他 record 或
session。

### 6.3 归档根

attempt archive 存放于本次 benchmark 输出根：

```text
<output-root>/
  agent-workspace-archives/
    <group_id>/
      <record-slug>/
        attempt-<attempt_index>-<session-id>/
          workspace/
          workspace-archive-manifest.json
```

archive 路径必须包含 session id，避免 resume、retry 或重复 record id 覆盖历史归档。

## 7. Sentinel 契约

文件名固定为 `.benchmark-workspace.json`。

```json
{
  "kind": "openclaw-benchmark-attempt-workspace",
  "schema_version": 1,
  "run_id": "benchmark-run-id",
  "invocation_id": "uuid",
  "group_id": "single_llm_skills_on",
  "runner_kind": "single_llm",
  "agent_id": "benchmark-single-skills-on",
  "record_id": "xtb_gap_window_001",
  "attempt_index": 0,
  "session_id": "benchmark-...",
  "workspace_path": "/absolute/managed/path",
  "template_id": "single-llm-skills-on-v1",
  "template_sha256": "sha256",
  "created_at": "RFC3339 timestamp"
}
```

约束：

- `workspace_path` 必须等于目录 `resolve()` 后的真实路径；
- 所有 identity 字段必须与当前 lease 完全相等；
- sentinel 不得包含 token、provider secret、reference answer 或 verifier metadata；
- sentinel 写入必须采用临时文件加原子 rename；
- sentinel 缺失或损坏时，不允许尝试“猜测清理”。

## 8. Workspace Template 契约

### 8.1 Template 来源

模板必须来自 canonical source，而不是当前 runtime workspace。推荐位置：

```text
workspace/benchmarking/resources/agent-workspace-templates/
  single-llm-skills-on/
  single-llm-skills-off/
  judge/
```

ChemQA 的 role template 继续复用
`skills/debateclaw-v1/scripts/templates/debate-slot-AGENTS.md`，但其 workspace 目录仍由本规格的
manager 新建。

### 8.2 Template 内容

模板只包含执行所需的最小控制文件。不得包含：

- `.git`；
- `memory/`、历史 transcript 或 session store；
- benchmark task、sample answer、gold、verifier spec；
- 历史请求、输出、下载文件、结构文件或计算日志；
- 指向 legacy workspace 或 archive 的 symlink。

模板必须有稳定 `template_id` 和按相对路径、文件内容计算的 `template_sha256`。preflight 在 agent
启动前验证模板物化结果。

### 8.3 控制文件可写性

attempt 运行期间 agent 可能修改控制文件，因此 postflight 不依赖它们保持不变。所有修改随完整
workspace 归档；下一 attempt 从 canonical template 重建。

## 9. Attempt 状态机

```text
UNALLOCATED
  -> PREPARING
  -> READY
  -> ACTIVE
  -> COLLECTING
  -> SEALING
  -> ARCHIVED

PREPARING | READY | ACTIVE | COLLECTING | SEALING
  -> QUARANTINED
  -> FAILED
```

### 9.1 PREPARING

1. 获取 workspace lock。
2. 验证 runtime root、output root 和目标路径 containment。
3. 确认目标路径不是 symlink。
4. 若 active path 已存在：
   - sentinel 与当前 lease 相同：视为同一 attempt 的幂等恢复；
   - sentinel 属于已终止 attempt：先 seal 到其唯一 archive；
   - sentinel 合法且能证明由 benchmark 管理、但属于不完整的其他 attempt：移动到 quarantine，
     当前 attempt fail closed；
   - sentinel 缺失、损坏或无法证明目录所有权：保持目录不变，当前 attempt fail closed；
   - 永远不递归删除未知目录。
5. 从可信模板创建临时 workspace。
6. 写入 sentinel 和 scratch 目录。
7. 验证允许路径集合、无 `.git`、无 symlink。
8. 原子 rename 为 active path。

### 9.2 READY

runner 把 active workspace 写入 run-scoped OpenClaw config，并设置：

- `BENCHMARK_WORKSPACE_DIR`
- `BENCHMARK_SKILL_SCRATCH_DIR`
- `BENCHMARK_SKILL_REQUEST_DIR`
- `BENCHMARK_SKILL_OUTPUT_DIR`
- `BENCHMARK_SKILL_NOTES_DIR`

这些变量对 skills-on 和 skills-off single-LLM attempt 都存在。ChemQA role processes 接收各自的
active workspace，但共享同一 record/attempt identity。

### 9.3 ACTIVE

OpenClaw turn 或 ChemQA workflow 正在运行。不得在此状态轮换、清理或重建 workspace。

### 9.4 COLLECTING

runner 已取得 terminal subprocess 状态，按以下顺序收集：

1. OpenClaw stdout/stderr 边界结果；
2. session isolation postflight；
3. transcript 和 tool-call audit；
4. ChemQA canonical artifacts、status 和 fallback 所需文件；
5. workspace contamination audit；
6. 需要写入 `RunnerResult.runner_meta` 的 workspace metadata。

### 9.5 SEALING

1. 阻止该 lease 启动新 agent turn。
2. 写 archive manifest 临时文件。
3. 将完整 active workspace 原子 rename 到 archive 的 `workspace/`。
4. 原子提交 archive manifest。
5. 释放 lock。

active workspace 与 output root 默认位于同一文件系统。若检测到跨文件系统，必须使用
copy-to-temp、完整性校验、rename-commit，再移除原路径；任一步失败均进入 quarantine，不能静默
降级为部分 archive。

### 9.6 ARCHIVED

archive 只供人工审计、dashboard 或自动分析读取。后续 agent config 不得引用该路径。

### 9.7 QUARANTINED / FAILED

目录所有权已经由合法 sentinel 确认，但发生部分移动、manifest 不一致或 lifecycle 状态损坏时，
把该 managed 目录移动到：

```text
<output-root>/agent-workspace-quarantine/<unique-id>/
```

保留诊断，停止该 attempt，不继续运行模型或评分。无法用合法 sentinel 证明所有权的目录不得移动，
只能报告错误并保持原状。

## 10. Archive Manifest

`workspace-archive-manifest.json` 至少包含：

```json
{
  "kind": "openclaw-benchmark-workspace-archive",
  "schema_version": 1,
  "run_id": "...",
  "invocation_id": "...",
  "group_id": "...",
  "agent_id": "...",
  "record_id": "...",
  "attempt_index": 0,
  "session_id": "...",
  "runner_status": "completed|recovered|failed|aborted",
  "archive_reason": "attempt_terminal|timeout_retry|exception|shutdown_recovery",
  "started_at": "...",
  "sealed_at": "...",
  "source_workspace": "...",
  "archive_workspace": "...",
  "file_count": 0,
  "total_bytes": 0,
  "sentinel_sha256": "...",
  "contamination_audit": {
    "status": "clean|contaminated|unavailable",
    "finding_count": 0
  }
}
```

首版不要求为每个大文件计算 hash；sentinel、manifest 和重要文本诊断必须有 hash。后续如需要不可
变归档，可扩展独立内容清单，不改变 attempt 生命周期。

## 11. 污染审计

### 11.1 Preflight 文件审计

模型调用前必须验证：

- active workspace 只包含模板、sentinel 和当前 scratch；
- `.benchmark-scratch` 中只有当前 record/session；
- 不存在 `.git`；
- 不存在 symlink、socket、device 或命名管道；
- 模板文件 hash 与 sentinel 一致；
- active workspace 中不包含 verifier package、formal dataset 副本或历史 archive。

### 11.2 Tool/transcript 审计

在现有 tool audit 上增加 forbidden path detection。至少识别：

- 其他 run/invocation/group/record/session workspace；
- `agent-workspace-archives` 和 quarantine；
- legacy fixed benchmark workspace；
- verifier-grounded source checkout；
- `data/verifier-grounded-releases`；
- package sample answers、gold、task YAML 和 verifier specs；
- 通过 `..`、绝对路径、symlink 或 shell expansion 访问上述位置。

允许访问：

- 当前 active workspace 和 scratch；
- canonical skills root 中被 allowlist 暴露的 skill docs/scripts/assets；
- 当前 input bundle；
- runner 明确公开的网络和工具资源。

### 11.3 判定

发现 forbidden access 后：

- `RunnerResult.status = FAILED`；
- `failure.code = "benchmark_workspace_contamination"`；
- `evaluable = false`；
- `scored = false`；
- `runner_meta.workspace_isolation.contaminated = true`；
- 保存脱敏后的命令、工具名和命中规则，不把 secret 写入结果。

## 12. Runner 接入契约

### 12.1 新模块

新增：

```text
benchmarking/runtime/agent_workspace.py
```

建议公开最小接口：

```python
class AttemptWorkspaceManager:
    def prepare(self, identity: AttemptIdentity) -> AttemptWorkspaceLease: ...
    def seal(self, lease: AttemptWorkspaceLease, outcome: AttemptOutcome) -> WorkspaceArchive: ...
    def recover_incomplete(self, invocation_id: str) -> list[WorkspaceArchive]: ...
```

`AttemptWorkspaceLease` 提供 active workspace、scratch 子目录、sentinel、lock 和用于
`runner_meta` 的 metadata。调用方不得自行删除或移动 workspace。

### 12.2 ConfigPool

`benchmarking/runtime/config_pool.py` 必须：

- 接收 `run_id`、`invocation_id` 和 workspace manager；
- 不再把 runner 指向 `baseline_workspace_root / agent_id`；
- 为并行 group 生成互不相同的 active workspace 路径；
- 不引用 legacy fixed workspace；
- 保留 run-scoped config 文件缓存，但 config 中 workspace 必须属于当前 invocation。

由于 config 目前按 group 缓存，而 workspace 在 attempt 后以相同 active path 重建，所以不需要为
每个 retry 重新生成 config。

### 12.3 SingleLLMRunner

`benchmarking/workflow/runners/single_llm.py` 必须：

- 在每个 attempt 调用 `prepare()`；
- skills-on/off 都创建并传递 scratch 环境变量；
- 把 scratch 指令附加到两类 prompt；
- 在 wrapper 返回后的 `finally` 路径执行 collect/audit/seal；
- timeout retry 前先 seal 上一 attempt，再 prepare 全新 workspace；
- 将 lease/archive/audit 信息写入 `runner_meta.workspace_isolation`；
- seal 失败时覆盖原本可评分状态，返回 workspace execution failure。

### 12.4 ChemQARunner

一个 ChemQA record attempt 内，coordinator 和 proposer/reviewer slots 可以按协议共享本 record 的
run state，但每个 slot 必须有独立 active workspace。所有 slot lease 形成一个
`WorkspaceLeaseSet`：

- 全部 prepare 成功后才能启动 ChemQA；
- 任一 prepare 失败则回滚并 quarantine 已准备的 lease；
- canonical artifacts 收集完成、cleanroom process cleanup 完成后统一 seal；
- recovery attempt 使用全新的 lease set；
- 旧 slot workspace 不得成为 fallback artifact source。

### 12.5 JudgeClient

每次 judge 调用使用独立 attempt workspace。judge session postflight 和 JSON verdict 读取完成后立即
seal。judge workspace contamination 使 judge call 失败，不接受 verdict。

### 12.6 Orchestration

`benchmarking/workflow/orchestration.py` 继续负责 record 级结果持久化，但 runner 必须在返回
`RunnerResult` 前完成 seal。orchestration 不直接操作 workspace 文件。

## 13. 错误契约

新增 failure codes：

| code | 含义 | retryable |
| --- | --- | --- |
| `workspace_path_unsafe` | containment、symlink 或特殊文件检查失败 | false |
| `workspace_sentinel_invalid` | sentinel 缺失、损坏或 identity 不匹配 | false |
| `workspace_lock_conflict` | workspace 已被其他 attempt 持有 | false |
| `workspace_template_invalid` | 模板缺失、含 `.git` 或 hash 不匹配 | false |
| `workspace_prepare_failed` | 无法创建或提交 active workspace | false |
| `workspace_archive_failed` | 无法完整归档 active workspace | false |
| `benchmark_workspace_contamination` | agent 访问 forbidden path | false |
| `workspace_recovery_failed` | crash recovery 无法确定目录所有权 | false |

这些错误属于 `layer=benchmark_runtime`、`source=workspace_isolation`。不得降级为普通 answer parse
error，也不得把已有候选答案继续送入 evaluator。

## 14. 崩溃与恢复

### 14.1 启动恢复

CLI 创建新 invocation 后扫描当前 run runtime root：

- 只处理带合法 sentinel 的 managed active workspace；
- 根据 sentinel 移动到上一次 invocation 的 archive/quarantine；
- 不删除无 sentinel 的目录；
- recovery 完成前不启动新 agent。

### 14.2 信号与异常

workspace manager 注册的 cleanup 只负责 seal/quarantine managed workspace，不负责 kill 未知进程。
进程清理由现有 cleanroom/runner lifecycle 负责。信号处理必须幂等，并保留当前 sentinel 供下次启动
恢复。

### 14.3 Resume

`--merge-existing-per-record` 或相同 `--exact-output-dir` 续跑时：

- 创建新 invocation_id；
- 已存在 per-record result 不作为 agent 输入；
- 被跳过的 record 不创建 workspace；
- 新执行的 record 使用新 attempt archive 路径，不覆盖旧 invocation。

## 15. Legacy Workspace 策略

以下固定目录视为 untrusted legacy runtime state：

```text
~/.openclaw/benchmark/workspaces/benchmark-single-skills-on
~/.openclaw/benchmark/workspaces/benchmark-single-skills-off
~/.openclaw/benchmark/workspaces/benchmark-judge
~/.openclaw/benchmark/workspaces/custom-single-agent
```

实现本规格后：

- 新 config 不再引用这些路径；
- benchmark 启动不自动删除、reset 或修改它们；
- 可提供独立、显式的 maintenance 命令把它们移动到 legacy archive；
- maintenance 不属于 benchmark 正常启动路径；
- tool audit 把对这些路径的访问判为污染。

## 16. 配置策略

attempt workspace isolation 对正式 benchmark 默认开启且不可通过普通 benchmark CLI 关闭。

测试可以通过依赖注入使用临时 manager。开发者若需要保留活动 workspace 调试，仍必须执行 archive，
只能选择 archive retention，不允许让同一活动目录进入下一 attempt。

首版不新增大量 CLI flags。允许的维护入口仅包括：

- 显式 legacy workspace archive 命令；
- archive retention/prune 命令；
- read-only workspace isolation doctor/check 命令。

## 17. 结果与运行清单

`RunnerResult.runner_meta.workspace_isolation` 至少包含：

```json
{
  "schema_version": 1,
  "run_id": "...",
  "invocation_id": "...",
  "group_id": "...",
  "record_id": "...",
  "attempt_index": 0,
  "session_id": "...",
  "active_workspace": "...",
  "archive_manifest": "...",
  "template_id": "...",
  "template_sha256": "...",
  "preflight_ok": true,
  "archive_ok": true,
  "contaminated": false,
  "findings": []
}
```

benchmark `runtime-manifest.json` 增加：

- isolation schema version；
- run_id 与 invocation_id；
- runtime workspace root；
- archive root；
- template ids/hashes；
- legacy workspace paths 被禁止引用的声明。

aggregate report 增加：

- `workspace_isolation_ok_count`；
- `workspace_isolation_failed_count`；
- `workspace_contaminated_count`；
- `workspace_archive_failed_count`。

## 18. 测试规格

### 18.1 单元测试

必须覆盖：

1. 合法 sentinel 的创建、读取和 identity 精确比较；
2. 非 runtime root 路径拒绝；
3. `..`、symlink、broken symlink 和特殊文件拒绝；
4. 模板含 `.git` 时拒绝；
5. active workspace 原子创建；
6. 完整 workspace archive；
7. archive collision 不覆盖；
8. seal 幂等；
9. lock conflict fail closed；
10. 损坏 sentinel 进入 quarantine；
11. resume 使用新 invocation_id；
12. root-level 未知文件随 workspace 一起归档；
13. archive 后重建目录不含旧文件；
14. forbidden path audit 的绝对路径、相对路径和 shell expansion 识别。

### 18.2 Single-LLM 集成测试

构造带有以下污染物的 legacy fixture：

- `.benchmark-scratch/old-record/old-session/run_verifier.py`；
- workspace 根目录 `final_answer.xyz`；
- `.git` 中可恢复的历史答案；
- xTB 输出和任意扩展名目录。

验证：

- 新 config 不引用 fixture；
- attempt workspace 无 `.git` 和旧文件；
- skills-on/off 都得到当前 scratch；
- attempt 在 root 和 scratch 写入文件后，下一 attempt 均不可见；
- 上一 attempt 文件出现在唯一 archive；
- timeout retry 使用新 archive 和干净 workspace；
- seal 失败导致不评分。

### 18.3 ChemQA 集成测试

- 六个 slot prepare 要么全部成功，要么全部回滚；
- slot 之间不共享 workspace；
- 同一 record 的 protocol artifacts 正常收集；
- 下一个 record 看不到前一 record 的 role 文件；
- recovery 使用新 lease set；
- archive 在 artifact collection 和 process cleanup 之后发生。

### 18.4 Judge 集成测试

- 每个 judge call 使用干净 workspace；
- verdict 收集后 archive；
- 历史 judge prompt/verdict 不可见；
- contamination finding 使 verdict 无效。

### 18.5 崩溃测试

- PREPARING、ACTIVE、COLLECTING 和 SEALING 各阶段模拟进程退出；
- 下一 invocation 能 archive 或 quarantine managed workspace；
- 未知目录保持不变；
- 恢复失败时不启动 agent。

### 18.6 并发测试

- 并行 groups 的 workspace 路径不同；
- 相同 agent override 不造成共享；
- lock 阻止两个进程持有同一 workspace；
- archive 和 active path 不互相覆盖。

## 19. 验收标准

只有全部满足时才可将功能标记为 `DONE`：

1. 正式 runner 不再引用任何 legacy fixed workspace。
2. single-LLM skills-on/off、ChemQA 和 judge 全部按 attempt 获取 workspace lease。
3. 所有 active workspace 均无 `.git`。
4. 每个 attempt 的 workspace 都有唯一 archive 或明确 quarantine 诊断。
5. 下一 attempt 无法从当前 workspace 看到上一 attempt 的任意文件。
6. timeout retry 不继承前一 attempt 的 scratch 或 root 文件。
7. forbidden path 访问会使结果不可评分。
8. workspace 隔离失败不会被 answer recovery 覆盖。
9. runtime manifest 和 per-record result 包含 isolation metadata。
10. 单元、runner、ChemQA、judge、crash 和并发测试全部通过。
11. 至少运行一次 skills-on、skills-off 和 ChemQA 的单 record smoke，并人工检查 archive。
12. `GLOBAL_DEV_SPEC.md` 从风险/计划状态更新为真实已实现状态。

## 20. 实施顺序

### Phase 1：Workspace Manager

- 实现 identity、sentinel、path safety、template、lock、prepare、seal、archive、quarantine；
- 完成单元测试；
- 不改 runner 行为。

### Phase 2：Single-LLM

- ConfigPool 改用 run-scoped active workspace；
- skills-on/off 都使用 scratch；
- attempt/retry 接入 prepare/collect/seal；
- 增加 metadata、错误分类和污染 audit；
- 完成 poisoned-workspace 与 retry 集成测试。

### Phase 3：ChemQA 与 Judge

- ChemQA 接入原子 lease set；
- judge 每次调用独立 lease；
- 对齐 artifact collection、cleanroom 和 seal 顺序；
- 完成多 slot、judge 和 recovery 测试。

### Phase 4：Crash Recovery 与 Rollout

- 启动恢复和 quarantine；
- aggregate/reporting 指标；
- 三组单 record smoke；
- 更新 `GLOBAL_DEV_SPEC.md`；
- 合并前禁止正式 benchmark 全量跑分。

## 21. 与 Verifier-Grounded Package 接入的关系

本规格不改变已确定的 package 接入边界：

- agent 只接收 sanitized prompt 和 public answer schema；
- scorer 使用独立 runtime 和 public `load_track(...).evaluate_one(...)`；
- package 不安装进 agent 主 `.venv`；
- wheel、sample answers、gold 和 verifier specs 不复制进 attempt workspace。

本规格补齐的是 agent 执行侧的历史状态隔离。package pin 和 workspace rotation 必须同时成立，才能
把 verifier-grounded 正式 run 视为未受本机历史记录污染。
