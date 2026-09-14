# OpenClaw Bootstrap, Rescue Context, and Path Projection Validation

日期：2026-09-14  
范围：run/container 配置、session snapshot、finalization context 和容器路径投影  
状态：`IMPLEMENTED_WITH_VALIDATION_LIMITS`

本轮已完成双层 `skipBootstrap=true` 强制、skills-on/off 模板契约保持、primary
transcript 原子冻结与 SHA-256 元数据、受限 rescue context bundle 原子写入，以及
边界安全的容器路径映射。bundle 仅保留任务、eval kind、answer schema 和可见
assistant/tool 事件，并移除凭据字段与绝对路径。

验证证据：

- 聚焦配置、容器、workspace、session lifecycle、wrapper 和 outcome 测试通过。
- 全量测试结果为 `922 passed, 9 skipped, 5 failed`；5 个失败均为旧 wrapper
  测试对 transport/blocked payload 触发 rescue 的断言。
- `uv run ruff check` 对新增 context 模块及修改 import 通过；既有 runner 中的
  B023/SIM102 告警与本修复无关。
- `uv run python -m compileall -q benchmarking` 通过。
- Docker daemon 可用（29.7.2）。

限制：仓库中的部分旧 wrapper 测试仍断言 transport/blocked payload 可以进入
finalization rescue，这与本交接文档要求“timeout、transport/process failure 不得
rescue”冲突；这些断言需要随测试契约更新。GPT-5.6 SOL 定向真实模型复跑、并发
Docker 验收、残留容器/owner-lock 证据尚未在本环境完成，因此本文和交接文档尚未
标记为 `CLOSED`。
