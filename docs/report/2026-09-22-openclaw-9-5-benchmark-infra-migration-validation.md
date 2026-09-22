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

验证结果：`uv run python -m compileall -q benchmarking`、`git diff --check` 通过；使用
mock CLI 完成 session row 与 trajectory export contract smoke test；使用临时配置完成
`agents.entries` 及容器路径投影 smoke test。现有仓库中部分旧测试仍断言
`agents.list` 和 host session mutation，需随新契约更新，不能作为 9.5 行为验收依据。

尚未在本机执行真实 9.5 Docker image、Gateway migration 或 live provider smoke；这些
需要 Docker daemon、目标镜像构建和独立 state backup，不能由本地纯 Python fixture 证明。
