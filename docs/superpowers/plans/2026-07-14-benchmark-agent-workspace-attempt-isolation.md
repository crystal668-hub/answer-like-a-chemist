# Benchmark Agent Workspace Attempt Isolation Implementation Plan

状态：`DONE`

日期：2026-07-14

分支：`feature/benchmark-attempt-workspace-isolation`

设计依据：
`docs/superpowers/specs/2026-07-14-benchmark-agent-workspace-attempt-isolation-design.md`

## 1. 目标与完成定义

本计划严格实现设计规格定义的 attempt 级完整 workspace 隔离，不改变 benchmark 题目、模型、
skill allowlist、评分 API 或 verifier-grounded package 边界。

完成时必须同时满足：

1. `single_llm_skills_on`、`single_llm_skills_off`、ChemQA 全部 role slots 和 judge 的每次
   attempt/call 都使用独占 workspace lease。
2. active workspace 只含 sentinel、可信模板、当前 scratch 和显式输入，不含 `.git`、历史文件、
   symlink 或特殊文件。
3. stdout/session/transcript/tool/artifact/污染审计完成后，完整 workspace 才能 seal 到唯一 archive。
4. prepare、lease、audit、seal、archive 或 recovery 失败时 fail closed，候选答案不得进入 scorer。
5. runtime manifest、per-record metadata 和 aggregate counters 暴露规格要求的 isolation 信息。
6. 规格第 18 节的单元、runner、ChemQA、judge、crash 和并发测试通过。
7. skills-on、skills-off、ChemQA 各完成一个 single-record smoke，并人工核对 archive。
8. `GLOBAL_DEV_SPEC.md` 和设计文档状态与真实实现一致，相关测试通过后提交分支。

## 2. 不变量映射

| 规格 | 实现约束 | 验证方式 |
| --- | --- | --- |
| BAWI-01 | lock 与 sentinel identity 共同保证 active workspace 只属于一个 attempt | lock conflict、identity mismatch、并发测试 |
| BAWI-02 | prepare 仅物化可信模板、sentinel、当前 scratch 和显式 input bundle | preflight allowset 与 poisoned-workspace 测试 |
| BAWI-03 | template 与 active tree 递归拒绝 `.git` 文件或目录 | template/preflight 单元测试 |
| BAWI-04 | runner 的 terminal/timeout/cancel/exception 路径统一进入 seal | runner、crash、retry 测试 |
| BAWI-05 | collect 顺序固定为 subprocess、session、transcript/tool、artifact、污染、metadata | runner 调用顺序测试 |
| BAWI-06 | seal 移走完整目录，下一 attempt 从 canonical template 原子重建 | root/scratch 隔离测试 |
| BAWI-07 | 只操作 runtime root 内且 sentinel 匹配的真实路径 | containment、traversal、symlink、unknown-dir 测试 |
| BAWI-08 | active path 包含 run/invocation/group/agent，lock key 使用 group/agent | parallel group 与相同 override 测试 |
| BAWI-09 | template、active tree 和 archive 回挂路径均拒绝 symlink | symlink 与 forbidden-path audit 测试 |
| BAWI-10 | workspace failure 统一映射为不可重试的 benchmark runtime execution failure | evaluator 未调用与 status/scored 测试 |

## 3. Phase 1：Workspace Manager

### 3.1 数据与错误契约

- 在 `benchmarking/runtime/agent_workspace.py` 定义 `AttemptIdentity`、`AttemptWorkspaceLease`、
  `AttemptOutcome`、`WorkspaceArchive`、污染 finding/audit 结构和 workspace isolation 异常。
- identity 包含 `run_id`、`invocation_id`、`group_id`、`runner_kind`、`agent_id`、`record_id`、
  `attempt_index`、`session_id` 和 template identity。
- 失败码严格限定为规格第 13 节的八类，统一
  `layer=benchmark_runtime`、`source=workspace_isolation`、`retryable=false`。

### 3.2 Template 与 sentinel

- 新增 canonical templates：single-LLM skills-on/off 与 judge；ChemQA 从现有
  `debate-slot-AGENTS.md` 物化角色模板。
- template hash 按排序后的相对路径、文件类型和内容计算；任何 `.git`、symlink 或特殊文件使
  template invalid。
- sentinel 使用临时文件加 `os.replace` 原子提交；读取时做 schema、kind、真实路径和全 identity
  精确校验，不包含 secret、答案或 verifier metadata。

### 3.3 Prepare、seal、quarantine 与 recovery

- runtime 路径固定为
  `benchmark/workspaces/runs/<run>/<invocation>/active/<group>/<agent>`；slug 保留 hash 后缀。
- 使用非阻塞文件锁获得排他 lease；拒绝 path traversal、symlink、broken symlink、socket、device、
  FIFO 与非托管目录。
- prepare 在同级 temp 目录物化 template、sentinel、scratch，然后 preflight 并原子 rename。
- seal 先准备 manifest，再把完整 active workspace 原子移到
  `agent-workspace-archives/<group>/<record>/attempt-<index>-<session>/workspace`，最后原子提交
  manifest；跨文件系统时 copy 到 temp、校验、rename commit，再移除源目录。
- archive collision 不覆盖；重复 seal 返回同一 archive；部分失败只对合法 managed workspace
  quarantine。
- `recover_incomplete()` 只处理合法 sentinel；未知目录原地保留并返回
  `workspace_recovery_failed`。

### 3.4 Phase 1 验证

- 新增 manager 单元测试覆盖规格 18.1 的 14 项。
- 先运行 manager 测试，再运行 config/provisioning 既有回归测试。

## 4. Phase 2：Single-LLM

### 4.1 ConfigPool

- `ConfigPool` 接收 `run_id`、每次 CLI 创建的 `invocation_id` 和 workspace manager。
- config 按 group 缓存，但 agent workspace 只指向当前 invocation 的 active path；不创建、不修改、
  不引用 legacy fixed workspace。
- 即使 `single_agent_id_override` 相同，不同 group 的 active path 仍不同。

### 4.2 Attempt 生命周期

- `SingleLLMRunner` 在每个 primary/retry attempt 前 `prepare()`，使用 attempt index 与本次 session。
- skills-on/off 都创建并注入 `BENCHMARK_WORKSPACE_DIR` 及四个 scratch 环境变量。
- 两类 prompt 都获得 scratch 使用要求；skills-off 仍禁止 skills，但所有临时产物必须留在 scratch。
- wrapper terminal 后按规格顺序收集 session/transcript/tool audit，再做 contamination audit，最后在
  `finally` 路径 seal。
- timeout retry 只在上一 attempt 成功 seal 后开始；新 attempt 不能继承 root 或 scratch 文件。
- seal/audit 失败覆盖原本成功或 recovered 的候选答案，并阻止 evaluator。

### 4.3 污染审计与结果

- 扩展现有 transcript/tool audit，检测其他 run/invocation/group/record/session、archive、quarantine、
  legacy fixed workspace、verifier source/release、sample/gold/task/verifier spec 的绝对路径、相对路径、
  `..` 和 shell expansion 访问。
- 脱敏保存命令、工具名与规则；命中后返回 `benchmark_workspace_contamination`。
- `runner_meta.workspace_isolation` 写入 lease、archive、template、preflight、audit 和 finding。

### 4.4 Phase 2 验证

- legacy poisoned fixture 不被新 config 引用。
- skills-on/off 环境与 prompt 均指向当前 scratch。
- root/scratch 文件进入唯一 archive，下一 attempt 不可见。
- timeout retry 使用干净 workspace 和不同 archive。
- seal 失败使结果不可评分且 evaluator 不被调用。

## 5. Phase 3：ChemQA 与 Judge

### 5.1 ChemQA lease set

- 为 coordinator、proposer/reviewer slots 建立 `WorkspaceLeaseSet`；每个 slot 路径独立，但共享当前
  record/attempt identity。
- 全部 prepare 成功后才启动 workflow；任一失败时 quarantine/seal 已准备 lease 并 fail closed。
- run-scoped ChemQA config 只引用 lease paths；旧 slot workspace 不作为 fallback artifact source。
- canonical artifact/status/fallback 收集与 cleanroom process cleanup 完成后统一 seal。
- recovery attempt 必须先 seal 原 lease set，再 prepare 全新 set。

### 5.2 Judge call

- 每次 `JudgeClient` 调用建立独立 judge identity/lease/session。
- session postflight 与 JSON verdict 读取完成后 seal；污染或 seal 失败使 verdict 无效。
- judge archive 不得暴露给后续 judge call。

### 5.3 Phase 3 验证

- 六 slot prepare 全成或全回滚，slot 路径互不共享。
- protocol artifacts 正常收集，下一 record 不可见前一 record role 文件。
- recovery 使用全新 lease set，archive 晚于 artifact collection 与 cleanup。
- judge 每次使用干净 workspace，verdict 收集后 archive，污染 finding 拒绝 verdict。

## 6. Phase 4：Recovery、Manifest、Reporting 与 Rollout

### 6.1 CLI 与 recovery

- 每次 CLI invocation 生成 UUID；同一 exact output dir/resume 也生成新 invocation id。
- runner 启动前扫描同 run 的旧 invocation active roots：合法 managed workspace archive/quarantine，
  unknown directory 原地保留；recovery 失败时不启动 agent。
- 被已有 per-record 结果跳过的 record 不创建 workspace。

### 6.2 Manifest 与 aggregate

- `runtime-manifest.json` 增加 isolation schema、run/invocation id、runtime/archive roots、template
  ids/hashes 与 forbidden legacy path 声明。
- aggregate 增加 `workspace_isolation_ok_count`、`workspace_isolation_failed_count`、
  `workspace_contaminated_count`、`workspace_archive_failed_count`。

### 6.3 Crash 与并发验证

- 模拟 PREPARING、ACTIVE、COLLECTING、SEALING 中断；下一 invocation 只 archive/quarantine 合法
  managed workspace，未知目录保持不变。
- 验证并行 groups、相同 agent override、跨进程 lock、archive/active path 均不碰撞。

### 6.4 Smoke 与文档

- 各运行一个 skills-on、skills-off、ChemQA single-record smoke，检查 active path 已移走、archive
  manifest identity 正确、workspace 完整、下一 attempt 无历史内容。
- smoke 前禁止正式全量跑分。
- 全部验收通过后把设计文档状态改为 `DONE`，同步 `GLOBAL_DEV_SPEC.md` 的 capability、架构、实际
  行为、风险和 next steps。

## 7. 测试与提交顺序

1. `uv run pytest` 运行新增 manager 单元测试。
2. `uv run pytest` 运行 config、single-LLM、ChemQA、judge、orchestration、reporting 相关测试。
3. `uv run pytest` 运行完整测试集。
4. 执行三组 single-record smoke 并检查每个 archive manifest/workspace。
5. 检查 `git diff --check`、`git status` 和设计/全局规范状态。
6. 测试全部通过后在功能分支提交；测试或 smoke 未通过时不得标记计划完成或提交。
