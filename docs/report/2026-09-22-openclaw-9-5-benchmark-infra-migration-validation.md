# OpenClaw 9.5 Benchmark Infra Migration Validation

日期：2026-09-22  
范围：benchmarking 单 LLM Docker 执行、OpenClaw session evidence、run/container config、provider probe。  
状态：ACTIVE CODE MIGRATION COMPLETE; LIVE DOCKER VALIDATION BLOCKED

本次实现将 active single-LLM backend 收敛为 Docker，Dockerfile 使用 Node 24.16 和
`openclaw@2026.9.5`。run-scoped 与 container config 将 legacy `agents.list` 归一为
keyed `agents.entries`，多 agent 配置设置 `agents.ownership: "explicit"`，并保留
`agents.defaults.skipBootstrap=true`。

新增 `benchmarking.runtime.openclaw_session` adapter，基于显式
`agentId/sessionKey/sessionId` 调用 `openclaw sessions --json` 和
`sessions export-trajectory`。active wrapper 不再清理 main row 或依赖 live
`sessions.json`/JSONL；旧 session reader 只作为历史兼容路径保留。provider probe
同时加载 `.mjs` 和 `.js` transport export。

第二阶段将官方 9.5 导出 bundle 的 `outputDir`、`manifest.json`、`events.jsonl` 和
`session-branch.json` 纳入校验，并将 branch 投影为现有 transcript consumer 可读的
临时证据。容器 manifest 记录 OpenClaw 版本、Node engine、npm integrity 和 resolved
image digest。新增 adapter deterministic tests 覆盖 owner mismatch、export failure、
missing artifacts 和 partial export。

本阶段继续将 judge 调用切换为显式 `agentId/sessionKey/sessionId`，移除 judge
路径对 stale main row 的清理和 postflight JSONL 扫描。judge 的 session evidence
现在与 single-LLM wrapper 使用同一 adapter。

验证结果：`uv run python -m compileall -q benchmarking`、`git diff --check` 通过；使用
mock CLI 完成 session row 与 trajectory export contract smoke test；使用临时配置完成
`agents.entries` 及容器路径投影 smoke test。active runner、orchestration、adapter、
CLI 和 judge 已移除 host execution entrypoint；旧 host/live-JSONL tests 已明确标记为
skipped，Docker contract 取代其 active coverage。全量 Python suite 当前为
`920 passed, 83 skipped`，跳过项仅包含 retired host/live-JSONL fixtures 或显式 opt-in
Docker tests。

新增 `benchmarking.runtime.agent_exec_contract`，记录 `agent --local` 与 `agent exec`
的 envelope、session identity、cleanup/evidence 覆盖和推荐结论。离线 fixture 结论为：
在 exec 无法提供可验证 trajectory evidence 时保留 `agent --local`。本机直接运行 9.5
CLI 被 Node `24.15.0` engine 诊断阻断；Docker 重建又被 Docker Hub token 网络超时阻断。
现有缓存镜像 `openclaw-benchmark-single-llm:latest` 仍为 OpenClaw `2026.6.9`
（digest `sha256:7b5cf7de75a82e3afbd8aa92e38e6850cfa50cc1f94270eb9a6c9358c92a2777`），
因此没有将其误标为 9.5 acceptance image。

已核对本地 9.5 npm tarball：version `2026.9.5`、build `ec9c1a1`、Node engine
`>=24.16.0 <25 || >=26.1.0`、npm integrity 已写入 Docker manifest。真实 Docker image
重建在 Docker Hub token 请求阶段因网络超时失败；因此 image digest、Gateway migration、
live provider、无模型 contract run 和完整 benchmark smoke 仍待网络可用后执行。
