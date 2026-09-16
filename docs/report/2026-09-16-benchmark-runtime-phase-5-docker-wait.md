# Benchmark Runtime Phase 5 Docker Wait Validation

日期：2026-09-16

状态：`ACCEPTED_NO_MODEL_DOCKER`

范围：RT-05 Docker wait client；不调用模型，不改变容器 supervisor 或归档逻辑。

## 实现与契约

`DockerContainerRuntime.collect()` 为每个 container lifecycle 启动一个受管理的
`docker wait <id>` client，使用独立进程组和有界关闭。collect 正常完成、timeout 或
cancel 都共享该 client；TERM 后的 supervisor evidence finalization 不再每秒启动新
CLI。独立调用 `terminate()` 时按需创建一个 wait client。启动失败和非零退出分别使用
`docker_wait_start_failed` 与 `docker_wait_failed` typed diagnostics。

首次取消原因仍由 `CancellationToken` 固定；二次取消跳过剩余 grace 并强制 kill。
stop/kill/remove 的 timeout、cleanup report、unresolved container 和 startup orphan
recovery 不变。注入 command runner 的单元测试继续使用兼容轮询路径，生产默认使用
managed client；没有增加 Docker SDK 或其他运行时依赖。

## 真实 Docker 验收

环境：Docker Server 29.7.2，本地 `python:3.12-slim-bookworm` 镜像。受控容器不访问
网络、不调用模型；timeout/cancel 容器捕获 TERM，打印 `supervisor-finalized` 后退出。
每个 legacy/managed 场景交替运行三轮。证据：
[`report.json`](../../state/benchmark-runs/temporary/docker-wait/no-llm/docker-wait-no-llm-20260916-181000/report.json)
和同目录完整 `raw-results.json`。

| 场景 | Mode | Docker commands | wait commands | wait timeouts | Wall time |
| --- | --- | ---: | ---: | ---: | ---: |
| complete | legacy | 6 | 1 | 0 | 0.487 s |
| complete | managed | 6 | 1 | 0 | 0.496 s |
| timeout | legacy | 8 | 2 | 1 | 0.566 s |
| timeout | managed | 7 | 1 | 0 | 0.588 s |
| cancel | legacy | 8 | 2 | 1 | 1.218 s |
| cancel | managed | 7 | 1 | 0 | 0.760 s |

数值为三轮中位数。另一个 2 秒基线容器使旧实现执行 3 次 wait（2 次 timeout）、
总命令 10 次；managed 执行 1 次 wait、总命令 8 次，collect wall 分别 2.102 和
2.122 秒。短 complete 矩阵在第一次 wait 内完成，所以 legacy 没有重复轮询，不能用
该行主张命令数收益。

全部 18 个矩阵样本均返回期望 terminal flag，保留 finalization stdout 并成功移除。
真实 orphan acceptance 证明 stale-owner 容器被移除、live-owner 容器被保留。定向测试：
`34 passed`，真实 orphan：`1 passed`。完整测试：`991 passed, 11 skipped, 164
subtests passed`，另有 5 个既有 SWIG deprecation warnings。

## 限制

没有运行真实模型或完整 benchmark attempt image。真实验收覆盖 Docker daemon、wait
client、TERM finalization、取消、timeout、remove 和 orphan ownership，但不覆盖两小时
长任务或 daemon 重启期间的真实行为。
