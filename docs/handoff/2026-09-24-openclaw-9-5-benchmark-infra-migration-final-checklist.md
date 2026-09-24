# OpenClaw 9.5 Benchmark Infra Migration Final Checklist

日期：2026-09-24  
依据：[原始实施计划](../plan/2026-09-22-openclaw-2026-9-5-benchmark-infra-migration.md)  
状态：CODE MIGRATION COMPLETE; RELEASE ACCEPTANCE INCOMPLETE

本文是交给下一会话的事实清单。原始计划仍是实施要求和历史记录；本文描述当前代码、测试和本机运行证据，不把未执行的 Docker/live 项标记为完成。

## 当前基线

| 项目 | 当前事实 | 状态 |
| --- | --- | --- |
| Git HEAD | `f50fb59` (`record final OpenClaw 9.5 entrypoint validation`) | 已提交 |
| Python suite | `922 passed, 83 skipped, 5 warnings, 150 subtests` | 通过；跳过项为 retired host/live-JSONL 或显式 opt-in Docker fixtures |
| Node | nvm `v26.10.0` 已安装并设为 default；部分非-login shell 仍可能优先 `/opt/homebrew/bin/node` `v24.15.0` | 部分完成；下一会话先统一 PATH |
| OpenClaw CLI | `/Users/xutao/.nvm/versions/node/v26.10.0/bin/openclaw` -> `2026.9.5 (ec9c1a1)` | CLI 已完成 |
| Docker daemon | Server `29.7.2`; `docker info` 显示内部 relay `http.docker.internal:3128`；settings-store 保存用户要求的 `http://127.0.0.1:7892` | 配置已写入，但 daemon 实际链路未证明可用 |
| 9.5 image | `openclaw-benchmark-single-llm:2026.9.5` 不存在 | 未完成 |
| Cached image | `openclaw-benchmark-single-llm:latest` 是旧 OpenClaw `2026.6.9`，digest `sha256:7b5cf7de75a82e3afbd8aa92e38e6850cfa50cc1f94270eb9a6c9358c92a2777` | 仅历史/旧缓存，不可作 9.5 acceptance |

## Phase Checklist

### Phase 0: Freeze and rollback baseline

- [x] 记录目标 release facts：version `2026.9.5`、build `ec9c1a1`、npm SHA-1 `734278f0f61d9fa9efa68edd11a3c97674f24e58`、Node engine、npm integrity。
- [x] 安装 Node `26.10.0` 并设置 nvm default；新 login shell 已验证。
- [ ] 统一所有 shell/PATH 到 Node `26.10.0`。当前普通 `functions.exec` shell 仍可能解析 `/opt/homebrew/bin/node` `v24.15.0`。
- [ ] 提交并验证 live `openclaw.json`、`state/openclaw.sqlite`、所有 agent SQLite DB 的独立备份清单。
- [ ] 在独立 9.5 state copy 执行 `openclaw doctor --session-sqlite inspect --session-sqlite-all-agents`。
- [ ] 完成 Gateway/service smoke 和可回滚 migration manifest。

### Phase 1: Identity-first session adapter

- [x] 新增 `benchmarking/runtime/openclaw_session.py`，使用 `SessionTarget` 和显式 `agent:<id>:explicit:<session-id>` identity。
- [x] wrapper/judge 传递 `--agent`、`--session-key`、`--session-id`，不再删除 stale main row。
- [x] adapter 使用公开 `sessions --json` 和 `sessions export-trajectory` surface。
- [x] 校验 `outputDir`、`manifest.json`、`events.jsonl`、`session-branch.json`、身份一致性和路径 containment。
- [x] typed evidence 覆盖 `session_key_mismatch`、`session_owner_mismatch`、`session_lock_conflict`、`trajectory_export_missing`、`cold_transcript_unavailable` 等路径；mock/deterministic fixtures 已覆盖主要错误。
- [ ] 真实 9.5 SQLite row、state lock、trajectory export 尚未运行；当前 adapter contract 主要由 mock CLI fixture 证明。
- [ ] `SessionLifecycleSupervisor` 仍保留 legacy lifecycle snapshot/fingerprint 兼容逻辑；若要完全满足目标架构，应进一步将活动生命周期证据改为 state-root/export generation 主判据。

### Phase 2: Config, provider probe, and image

- [x] `agents.list` legacy input 归一为 keyed `agents.entries`。
- [x] container projection 只保留 selected `entries`，多 agent 设置 `agents.ownership: "explicit"`，保留 `skipBootstrap`。
- [x] provider probe 支持 `.mjs` 和 `.js`。
- [x] Dockerfile 声明 Node `24.16-bookworm-slim`、`openclaw@2026.9.5`、npm integrity 和 Python lock。
- [x] container manifest 代码记录 OpenClaw/package/Node/integrity，并在 container create 后写入 resolved image digest。
- [ ] 9.5 image 尚未成功构建，无法验证最终 digest、容器内实际 package/build identity。
- [ ] SQLite/WAL、plugin state、trajectory export 在真实 archive 前 flush/containment 尚未执行。
- [ ] 无模型 contract attempt 尚未执行。

### Phase 3: Remove host MVP

- [x] CLI `--execution-backend` 只接受 `docker`。
- [x] active single runner 删除 host environment、host subprocess、host cleanup、host session fallback 分支。
- [x] adapter/orchestration/execution 删除 active backend selection；runner 固定 Docker。
- [x] 历史 result/dashboard/replay/legacy reader 保留为只读兼容路径。
- [x] 旧 host/live-JSONL tests 已明确 skip，并由 Docker contract/deterministic coverage 替代；没有恢复 host 执行入口。

### Phase 4: `agent exec` evaluation

- [x] 核对官方 9.5 `agent exec` 文档、稳定 JSON envelope、timeout/cleanup/state-dir 语义。
- [x] 新增 `benchmarking/runtime/agent_exec_contract.py` 离线 envelope comparison helper 和 deterministic tests。
- [x] 当前决策：保留 `agent --local` wrapper + identity adapter；若 exec 没有可验证 trajectory evidence，不替换 wrapper。
- [ ] 同 model/prompt/workspace/timeout/provider/image 的真实 A/B matrix 未执行。
- [ ] exec 的真实 tool/plugin、cleanup retention、reminder/rescue、Docker path projection 和 token observability 未比较。

### Phase 5: E2E and release

- [ ] 9.5 image rebuild/signing/digest。
- [ ] 1-record、skills-on/off、retry、reminder、rescue、provider failure live matrix。
- [ ] 并发度 2 的两个真实 isolated Docker attempts。
- [ ] 完整 Docker benchmark archive inventory。
- [x] 全量 Python tests、dashboard historical read tests和 deterministic contracts 已通过当前 suite。
- [x] `GLOBAL_DEV_SPEC.md`、验收报告和本 handoff 已更新并提交。

## Acceptance Matrix

| 验收项 | 结论 | 证据/缺口 |
| --- | --- | --- |
| Legacy config projection | 已完成 | config/container tests；全量 suite 通过 |
| Explicit session identity | 部分完成 | adapter fixtures；缺真实 9.5 SQLite row |
| Primary/reminder/retry/rescue identity | 部分完成 | 代码路径存在；缺真实 live matrix |
| Typed lock/owner/export/cold errors | 已完成（deterministic） | `openclaw_session` tests；缺真实 daemon lock |
| SQLite/trajectory vs legacy equivalence | 部分完成 | branch projection + historical readers；缺真实 SQLite export |
| Malformed/partial export behavior | 已完成（deterministic） | adapter fixture |
| `.mjs`/`.js` provider probe | 已完成（deterministic） | provider preflight tests |
| Host backend inaccessible | 已完成 | CLI rejects host; active runner has no host path |
| Node/OpenClaw local CLI | 已完成（PATH caveat） | nvm Node 26.10 + OpenClaw 9.5; non-login PATH caveat |
| Docker runtime manifest | 代码完成，运行未验 | 9.5 image unavailable |
| Docker state root/WAL/plugin/export archive | 未验 | no successful 9.5 container |
| `agent --json` parsing | deterministic only | no live 9.5 container |
| Dashboard/replay historical compatibility | 已完成 | full Python suite |
| Docker live smoke | 未完成 | Docker Hub token timeout |

## Blocking Evidence

The Docker Desktop settings store contains the requested values:

```text
OverrideProxyHTTP=http://127.0.0.1:7892
OverrideProxyHTTPS=http://127.0.0.1:7892
ProxyHTTPMode=manual
```

Docker Desktop normalizes these to its internal relay in `docker info`:

```text
HTTP Proxy: http.docker.internal:3128
HTTPS Proxy: http.docker.internal:3128
No Proxy: hubproxy.docker.internal
```

Repeated builds fail while fetching Docker Hub OAuth metadata, for example:

```text
failed to fetch oauth token: Post "https://auth.docker.io/token": dial tcp ...:443: i/o timeout
```

The next session should first test the relay with a minimal `docker pull` for both `node:24.16-bookworm-slim` and `python:3.12-slim-bookworm`, then retry the project build. Do not mark the old `openclaw-benchmark-single-llm:latest` image as 9.5.

## Next Session

1. Normalize login/non-login Node PATH and capture `node --version`, `which node`, `which openclaw`, and `openclaw --version` in one shell.
2. Verify Docker Desktop relay connectivity with minimal base-image pulls and inspect builder logs.
3. Build `openclaw-benchmark-single-llm:2026.9.5`; verify container Node/OpenClaw versions and immutable image digest.
4. Run opt-in real Docker contract tests with `BENCHMARK_TEST_CONTAINER_IMAGE=openclaw-benchmark-single-llm@<digest>`.
5. Execute the live smoke matrix and append paths/fingerprints to a dated report.
6. Only after those pass, update the original plan status and mark completion definition items 1, 3, and 4 as complete.
