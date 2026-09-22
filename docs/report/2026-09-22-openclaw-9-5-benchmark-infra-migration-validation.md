# OpenClaw 9.5 Benchmark Infra Migration Validation

日期：2026-09-22  
范围：benchmarking 单 LLM Docker 执行、OpenClaw session evidence、run/container config、provider probe。  
状态：IMPLEMENTED WITH LOCAL VALIDATION LIMITS

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
`agents.entries` 及容器路径投影 smoke test。针对 adapter、provider、container 和
finalization 的定向测试共 31 项通过。全量 suite 当前为 `918 passed, 92 failed,
7 skipped`；配置迁移后剩余失败集中在旧测试对 `agents.list`、host backend、live
JSONL/session mutation 的断言，以及尚未迁移的 judge/host-only fixtures。下一阶段应
删除或改写为 Docker contract fixtures。

尚未在本机执行真实 9.5 Docker image、Gateway migration 或 live provider smoke；这些
需要 Docker daemon、目标镜像构建和独立 state backup，不能由本地纯 Python fixture 证明。
