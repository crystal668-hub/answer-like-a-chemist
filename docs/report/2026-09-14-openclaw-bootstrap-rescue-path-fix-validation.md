# OpenClaw Bootstrap, Rescue Context, and Path Projection Validation

日期：2026-09-14  
范围：run/container 配置、session snapshot、finalization context 和容器路径投影  
状态：`CLOSED`

本轮已完成双层 `skipBootstrap=true` 强制、skills-on/off 模板契约保持、primary
transcript 原子冻结与 SHA-256 元数据、受限 rescue context bundle 原子写入，以及
边界安全的容器路径映射。bundle 仅保留任务、eval kind、answer schema 和可见
assistant/tool 事件，并移除凭据字段与绝对路径。

验证证据：

- 聚焦配置、容器、workspace、session lifecycle、wrapper 和 outcome 测试通过。
- 全量测试结果为 `928 passed, 9 skipped, 5 warnings, 164 subtests passed`。
- `uv run ruff check` 对新增 context 模块及修改 import 通过；既有 runner 中的
  B023/SIM102 告警与本修复无关。
- `uv run python -m compileall -q benchmarking` 通过。
- Docker daemon 可用（29.7.2）。

真实 Docker 验收：

- 单题 skills-off 定向 run：完成，session/workspace isolation 正常，
  `takeover_detected=false`，模型返回格式有效但 verifier 得分为 0。
- skills-on/off 并发 2、360 秒窗口 run：skills-on 完成并可评分；skills-off
  产生 typed execution failure（exec/tool failure），无 takeover 或污染证据，
  属于模型执行层失败，与本修复无关。
- 两次 run 的归档工作区均未发现 `BOOTSTRAP.md`、`SOUL.md` 或 `USER.md`。
- Docker 容器、OpenClaw 进程和非空 benchmark owner lock 检查均为空。

旧 wrapper 测试已迁移到新契约：timeout/transport/blocked payload 不再触发
finalization rescue。
