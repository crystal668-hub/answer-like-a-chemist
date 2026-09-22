# OpenClaw 2026.9.5 Benchmark Infra 更新与重构计划

日期：2026-09-22  
范围：本机 OpenClaw CLI 从 `2026.6.9` 升级到 `2026.9.5`，以及
`benchmarking` 单 LLM/Docker infra 的同步重构。  
状态：PROPOSED；本文只记录拟实施的工作，不代表功能已经完成。

## 1. 基线与官方证据

本机当前 CLI 为 `OpenClaw 2026.6.9 (c645ec4)`。目标 npm 包为
`openclaw@2026.9.5`，官方仓库 tag 为 `v2026.9.5`，release commit 为
`ec9c1a13db8938e5a3eaa51fca2e981cde2395a9`，npm tarball SHA-1 为
`734278f0f61d9fa9efa68edd11a3c97674f24e58`。目标包的
`dist/build-info.json` 与 release commit 一致。

官方 9.5 包和文档给出的架构事实：

- Node engine 已变为 `>=24.16.0 <25 || >=26.1.0`；本机当前 Node 为
  `v24.15.0`，必须先升级 Node 或明确使用满足条件的独立 runtime。
- agent database schema 为 21。sessions/transcripts 已在 2026.7.2 beta
  翻转到 per-agent SQLite；9.5 的运行时路径是
  `agents/<agentId>/agent/openclaw-agent.sqlite`，旧的 `sessions.json` 与
  JSONL 只是迁移、归档、导出或兼容输入。
- session row 与 transcript event 是两个 SQLite persistence layer；transcript
  是带 `id`/`parentId` 的 append-only tree，compaction/reset 是事件或
  generation 语义，不应再用文件名推断生命周期。
- `sessionFile` 仍可能作为兼容字段出现，但 SQLite target 会使用
  `sqlite:<agentId>:<sessionId>:<storePath>` marker；活动 session 的公开接口
  已转为 `agentId + sessionKey + sessionId` identity。
- canonical config 使用 `agents.entries` keyed roster；9.5 的 Doctor 会把
  `agents.list` 迁移到 `agents.entries`。多 agent 配置还涉及
  `agents.ownership: "explicit"` 与 agent owner 校验。
- `openclaw agent --local` 现在需要有效的 session selector，显式的
  `--session-key`/`--session-id` 会参与 owner resolution；embedded run 会获取
  整个 state directory 的 `agent-embedded` lock，与 Gateway 或其他 embedded
  writer 互斥。
- `openclaw agent exec` 是新的 headless/CI 入口：默认临时 state dir、稳定的
  `ok/status/final/sessionId` JSON envelope、独立 cleanup 和不同的 timeout exit
  code。它是候选入口，不在第一阶段直接替换现有 wrapper。
- trajectory 默认写入 per-agent SQLite；`sessions export-trajectory` 将其导出为
  `events.jsonl`、`session-branch.json`、`artifacts.json` 等支持包。活动运行不应
  爬取 live JSONL 文件。
- 9.5 release notes 明确修复 stale transcript write、compaction ownership、新
  session 在首个 transcript write 前登记、大历史读写阻塞和多 agent session
  inventory 问题。这些修复建立在 Gateway/SQLite owner 上，不能继续假设单个
  JSON 文件是唯一协调面。

官方参考：

- [2026.9.5 release changelog](https://github.com/openclaw/openclaw/blob/v2026.9.5/CHANGELOG.md)
- [Agent CLI](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/cli/agent.md)
- [Session state on disk](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/reference/session-management-compaction/store.md)
- [Session keys, ids, and transcript events](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/reference/session-management-compaction/schema.md)
- [Database layout](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/reference/database-schemas/layout.md)
- [Agent schema history](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/reference/database-schemas/agent-schema-history.md)
- [Plugin runtime agent/session helpers](https://github.com/openclaw/openclaw/blob/v2026.9.5/docs/plugins/sdk-runtime/agent.md)

## 2. 当前 infra 与差距

当前 benchmark 默认 Docker，但代码仍同时实现 host MVP。关键现状如下：

| 现状 | 位置 | 9.5 后的问题 |
| --- | --- | --- |
| 根据 `agentDir` 推导 `sessions/sessions.json` | `benchmarking/runtime/session_isolation.py` | 运行时真源已是 `openclaw-agent.sqlite`，写 JSON 会绕过 owner/transaction/migration。 |
| 通过 `agent:<id>:main`、`explicit:<id>` 查 row，并以 `sessionId + <id>.jsonl` 判定隔离 | `session_isolation.py` | key 可以保留为逻辑 identity，但 transcript 文件名不再是有效证据。 |
| wrapper 在 preflight 删除 stale main entry | `benchmarking/service/single/openclaw_wrapper.py` | 直接修改 session store 与 9.5 Gateway/SQLite writer 竞争；独立 attempt state 下也没有必要。 |
| lifecycle supervisor 记录 session file inode/size/mtime，冻结 JSONL snapshot | `benchmarking/runtime/session_lifecycle.py` | 只能观测 legacy file writer，无法覆盖 SQLite transcript generation、row owner 和 state lock。 |
| convergence、observability、workspace audit 从 transcript path/trajectory JSONL 读取 | `benchmarking/runtime/transcript_index.py`、`agent_workspace.py`、`attempt_observability.py` | 9.5 活动 transcript/trajectory 在 SQLite 中，需要一次 identity-based export 或 CLI adapter。 |
| run config 和 container config 使用 `agents.list` | `benchmarking/runtime/config.py`、`container_runtime.py` | 9.5 canonical roster 是 `agents.entries`。 |
| provider preflight 只加载后缀为 `.js` 的旧 dist exports | `benchmarking/resources/provider-connectivity-probe.mjs` | 9.5 对应 transport exports 为 `.mjs`，现有 probe 会报告 transport unavailable。 |
| host backend 与 Docker 分支并存 | `workflow/cli.py`、`single/runner.py`、`single/adapter.py`、测试 | host 是初始探索 MVP，当前不再使用；继续保留会拖累 state-lock 与证据契约迁移。 |

用户已确认：本次计划删除 host benchmark backend。历史 host run 只读读取、报告和 dashboard 兼容继续保留。

## 3. 目标架构

### 3.1 Docker-only execution

单 LLM 执行只保留 Docker attempt：每个 attempt 拥有唯一的
`/benchmark/session` state root、唯一 workspace、唯一 container 和唯一
session identity。每次 benchmark retry 都创建新的 container/state/session；同一
attempt 内的 primary、time reminder 可以在前一进程退出后复用同一个 logical
session，finalization rescue 仍使用新 session 加受限 context bundle。

删除 host-only runtime、CLI choice、分支和测试；历史结果 reader 不删除。
Docker image 固定 `openclaw@2026.9.5`、满足 engine 的 Node 版本和可复核 digest，
不再依赖宿主机 OpenClaw 版本。

### 3.2 Identity-first session backend

新增 `benchmarking/runtime/openclaw_session.py`（名称可在实现时调整）作为唯一
session adapter，定义 storage-neutral contract：

```text
SessionTarget:
  agent_id
  session_key
  session_id
  state_dir
  config_path

SessionEvidence:
  source: sqlite | trajectory_export | legacy_jsonl
  row_identity
  transcript_identity
  trajectory_events_path
  transcript_branch_path
  export_manifest_path
  lifecycle_generation / event cursor when available
```

adapter 负责：

1. 生成合法的 `agent:<id>:explicit:<benchmark-session-id>` key，并同时传递
   `--agent`、`--session-key`、`--session-id`；不扫描或删除 main row。
2. 调用受支持的 `openclaw sessions --json` 或等价 CLI surface 读取 session row，
   记录真实 owner、key、id、model、status 和 store identity。
3. 在 invocation 结束后通过
   `openclaw sessions export-trajectory --session-key ... --workspace ... --json`
   导出一次不可变 evidence bundle；后续 convergence、answer recovery、audit、
   observability 都只读该 bundle。
4. 将 9.5 的 SQLite/trajectory schema 映射到现有
   `TranscriptIndex`、tool event、token usage 和 answer recovery contract；不让
   下游模块直接依赖 `.jsonl` 路径。历史 `legacy_jsonl` 适配器只用于旧 run。
5. 将 state-lock refusal、session owner mismatch、missing export、cold transcript
   unavailable 分类为 typed execution evidence。state lock/conflict 在没有可恢复
   答案时 terminal、默认不 retry。

session audit 的新稳定字段应包括 `session_key`、`session_id`、`agent_id`、
`state_dir`、`store_kind`、`store_identity`、`requested_model`、
`observed_model`、`row_found_before/after`、`trajectory_export`、
`transcript_generation`、`owner_conflict` 和 `evidence_paths`。保留旧字段只为
历史读取，并标注 `legacy_compatibility`，不再以 `sessionFile` 是否匹配作为成功条件。

### 3.3 Wrapper/lifecycle 简化

`SessionLifecycleSupervisor` 改为 wrapper invocation/state-root supervisor：

- 保留 attempt ID、wrapper/OpenClaw PID、invocation kind、开始/结束、return code、
  provider terminal events、cleanup 和 immutable export evidence。
- 删除 session file inode takeover 作为活动 session 的主判据；改为 state lock、
  session identity、export generation 和 owner admission 证据。
- 同一 wrapper 内的 reminder 使用同一个 session key/id，只有 benchmark retry 和
  finalization rescue 分配新 session。
- 不直接 `atomic_write_json` 修改 OpenClaw session DB；所有 session state mutation
  由 OpenClaw CLI/Gateway owner 完成。
- 9.5 的 `session.ended`、`prompt.submitted`、`model.completed` 从导出的
  `events.jsonl` 读取；没有 chunk timestamp 时继续保留 unknown，而不推断。

### 3.4 Canonical config and runtime

- `render_run_config` 把 legacy `agents.list` 归一为 keyed `agents.entries`，保留
  model、workspace、agentDir、skills、thinking 等字段；单 runner config 只保留
  选中的 entry。
- `materialize_container_config` 对 `agents.entries` 做路径投影，并设置
  `agents.ownership: "explicit"`（仅在实际为 multi-agent 时设置），不再写回
  `agents.list`。
- container 继续设置 `OPENCLAW_STATE_DIR=/benchmark/session`，使 SQLite、WAL、
  plugin state、trajectory 都落在 attempt mount；session root 不与任何其他 attempt
  共享。
- run config 和 container config 都保留 `agents.defaults.skipBootstrap=true`。
- upgrade 前对 live config 做备份和 `openclaw doctor --fix` dry-run/inspection；
  benchmark run config 不依赖 live migration 成功才能生成。

### 3.5 Provider probe and image

将 `provider-connectivity-probe.mjs` 的 dist loader 从“只找 `.js`”改为支持
`.mjs`/`.js`，并通过 9.5 package manifest/build identity 校验 export prefix。probe
不能读取 host global OpenClaw。Dockerfile pin：Node runtime、OpenClaw npm
version/integrity、benchmark Python lock 和最终 image digest；启动 manifest 记录
这些 fingerprints。

## 4. 分阶段实施顺序

### Phase 0：升级前冻结与可回滚基线

- 记录当前 CLI、Node、npm prefix、live config hash、Gateway supervisor、现有
  Docker image digest。
- 备份 `~/.openclaw/openclaw.json`、`state/openclaw.sqlite` 和所有
  `agents/*/agent/openclaw-agent.sqlite`；不要把凭据写入 benchmark artifact。
- 用 9.5 package 做 `openclaw doctor --session-sqlite inspect
  --session-sqlite-all-agents`，确认 legacy `sessions.json` 覆盖范围。
- 若要保留旧 6.9 可回滚路径，按官方顺序保存 migration manifest 和 legacy
  transcript artifacts；SQLite flip 后不要直接用旧 CLI 打开新 DB。
- Node 先升级到 `24.16+`；若不升级宿主 Node，Docker 必须成为唯一执行入口。

验收：live config/state backup 可读，9.5 CLI `--version`、Doctor inspect 和
provider auth preflight 在独立 state copy 中成功。

### Phase 1：建立 9.5 adapter，暂不改业务结果契约

优先文件：

- 新增 `benchmarking/runtime/openclaw_session.py`
- 重构 `benchmarking/runtime/session_isolation.py`
- 重构 `benchmarking/runtime/session_lifecycle.py`
- `benchmarking/service/single/openclaw_wrapper.py`
- `benchmarking/core/result_contract.py`
- `benchmarking/runtime/attempt_observability.py`
- `benchmarking/runtime/agent_workspace.py`

任务：

- 用 `SessionTarget` 取代 session-file path 参数；旧 `session_isolation` API 转为
  historical adapter 或显式 deprecated shim。
- 先实现 CLI capability probe 和 trajectory export fixture，不在第一步直接读
  SQLite 内部表。
- 让 export failure 有独立 evidence，不覆盖原始 provider/agent failure；有完整
  native answer 时仍按现有 outcome precedence 结束，不因 export diagnostic retry。
- 为 row identity、session key mismatch、state lock conflict、missing export、
  cold transcript 建立稳定 error codes。

### Phase 2：切换配置和 Docker image

优先文件：

- `benchmarking/runtime/config.py`
- `benchmarking/runtime/container_runtime.py`
- `docker/single-llm/Dockerfile`
- `benchmarking/resources/provider-connectivity-probe.mjs`
- `benchmarking/service/single/runner.py`

任务：

- `agents.list` -> `agents.entries`，并测试旧 base config 到新 run config 的
  canonical projection。
- image 安装 `openclaw@2026.9.5`，固定 Node engine、npm integrity 和 image digest。
- 每个 container 继续使用独立 state root；确认 SQLite WAL、agent DB、trajectory
  export、plugin state 在 workspace/archive 前完整收集。
- 修正 `.mjs` transport loader，并做 6.9/9.5 probe contract fixtures。
- 运行一个无模型 contract attempt，验证 `agent --local` 的 session selector、
  state lock、JSON stdout 与 cleanup。

### Phase 3：删除 host MVP，统一调度契约

删除/收窄范围：

- `benchmarking/workflow/cli.py` 的 `--execution-backend host` 选项与 host-only
  分支。
- `benchmarking/service/single/runner.py` 中 host environment、host subprocess、
  host session fallback 和 host cleanup 分支。
- `benchmarking/service/single/adapter.py`、`orchestration.py`、`execution.py`
  中的 backend 选择参数；默认逻辑改为显式 Docker。
- 只为 host backend 存在的测试与 fixtures；将仍有价值的结果/outcome 测试改为
  Docker contract fixture。
- `GLOBAL_DEV_SPEC.md` 中“host VGB compatibility fallback”等当前实现描述，
  仅在代码完成并通过验收后更新。

保留：历史 host result/workspace reader、dashboard projection、replay/adjudication
和旧 `legacy_jsonl` session adapter；它们不得重新获得执行入口。

### Phase 4：评估 `agent exec`，再决定是否替换 wrapper

建立 A/B matrix：相同 model、prompt、workspace、timeout、provider 和 Docker image，
分别比较：

- `agent --local --session-key ... --session-id ... --json`
- `agent exec --state-dir ... --config ... --cwd ... --json`

必须比较：exit code、JSON envelope、sessionId/key 可追溯性、trajectory export、
tool/plugin 行为、cleanup 失败保留 state、time reminder/rescue 能力、Docker path
projection 和 token/tool observability。

只有当 `agent exec` 能提供显式 benchmark session identity、可导出的完整证据、
同等 workspace guard、可重复的 timeout/cancellation 和不改变结果契约时，才在后续
变更中替换 wrapper。否则保留 `agent --local`，继续使用 adapter。

### Phase 5：端到端上线与文档同步

- 重建并签名/记录 image digest；运行 1-record、skills-on/off、retry、reminder、
  finalization rescue 和 provider failure 小矩阵。
- 执行并归档完整 Docker benchmark；确认所有 attempt 的 SQLite/WAL、trajectory
  export、session audit、workspace audit 和 cleanup evidence 可读。
- 运行全量 Python tests、Docker acceptance、dashboard historical read tests。
- 更新 `GLOBAL_DEV_SPEC.md` 现状章节：Docker-only、9.5 package/Node、identity-first
  session evidence、配置边界和已删除 host backend。
- 在 `docs/report/` 记录真实 run 路径、image/package fingerprints、已知限制和
  rollback 结果；本计划保留为实施历史。

## 5. 测试与验收矩阵

### Deterministic tests

- `agents.list` legacy config 投影到 `agents.entries`，model/skills/path/defaults
  不丢失。
- 显式 session key/id 在空 SQLite state 中创建唯一 row；重复 selector 不串 agent。
- 同一 attempt 的 primary -> reminder 可以复用 logical identity；retry/rescue
  使用不同 identity。
- state lock conflict、owner mismatch、missing trajectory export、cold transcript
  分别得到稳定 typed error，不被 generic subprocess error 覆盖。
- SQLite/trajectory evidence 恢复出的答案与旧 JSONL fixture 的结果等价；历史
  JSONL reader 继续可用。
- trajectory export 中 malformed/partial event 的审计 fail-closed，answer recovery
  仍保留现有 bounded behavior。
- provider probe 同时覆盖 9.5 `.mjs` 和旧 fixture `.js`，不依赖机器 global install。
- `execution_backend=host` 不再可由 CLI 或 active runner 进入；历史 artifact reader
  可以读取旧 metadata。

### Docker acceptance

- Node engine、OpenClaw version/build SHA、package integrity、image digest 全部进入
  runtime manifest。
- 每个 container 的 state root 不共享；多个并发 attempt 不争用 SQLite/Gateway lock。
- workspace archive 前 SQLite DB、`-wal`、trajectory export 和 lifecycle summary
  均已 flush；cleanup 失败仍保留可复核的 attempt state。
- 9.5 `agent --json` 的 stdout 可被 result parser 解析，stderr 不污染 JSON。
- Docker path projection 不重写原始 error/transcript text；历史路径仍可读。

### Live smoke matrix

1. 一个无模型 contract run。
2. 一个 skills-on 与一个 skills-off 的短真实 run。
3. 一个 primary 完整答案、一个 provider timeout、一个 session lock conflict。
4. 一个 reminder continuation 和一个 finalization rescue。
5. 并发度 2 的两个不同 agent/state root attempt。
6. 旧 run 的 dashboard/replay/read-only adjudication。

## 6. 回滚与风险控制

- package rollback 只能通过完整备份和官方 SQLite restore/migration sequence；
  不允许手工把 SQLite 改回 `sessions.json`。
- Docker image 回滚与 benchmark code 回滚必须成对记录；9.5 生成的 session DB 不应
  被 6.9 直接打开。
- 若 9.5 provider/trajectory export 证据不足，暂停 active benchmark 发布，保留
  9.5 adapter fixtures 和失败 state；不得通过读取 live JSONL 或删除 session row
  恢复“看起来能跑”的兼容路径。
- release notes 里的 large-history/cold-storage 行为意味着 export 可能返回
  restore-required/unavailable；这种情况应成为明确的 evidence coverage degradation，
  不能伪造空 transcript。
- 9.5 Node engine 与现有 host `v24.15.0` 不满足要求；Docker-only 删除 host MVP
  后，宿主 CLI 升级仍需单独完成并通过 Gateway/service smoke test。

## 7. 完成定义

本次升级只有同时满足以下条件才算完成：

1. 本机 OpenClaw CLI 与 Docker image 都运行 9.5，Node engine 满足要求。
2. Active benchmark 不再读写 `sessions.json`、live session JSONL 或 host backend。
3. 所有 session 取证都能回溯到 `agentId/sessionKey/sessionId` 和一次可验证的
   SQLite/trajectory evidence export。
4. `agents.entries`、state lock、retry/reminder/rescue、provider probe 和 result
   contract 有 deterministic + Docker acceptance 覆盖。
5. 历史结果/dashboard/replay 仍可读，失败证据不被 generic error 或 cleanup 覆盖。
6. 代码、测试、`GLOBAL_DEV_SPEC.md`、验收报告完成后提交 Git。
