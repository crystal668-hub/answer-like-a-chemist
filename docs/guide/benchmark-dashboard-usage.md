# Benchmark Dashboard 使用说明

该 dashboard 是本机单人使用的 benchmark 信息监控台，支持运行中进度和容器观测，也支持完成后的逐题行为复盘。它只读取 benchmark artifact；收藏、隐藏和人工备注写入 dashboard 自己的 SQLite，不会修改评分结果。

## 启动

```bash
cd /Users/xutao/.openclaw/workspace
uv run --extra web-ui python -m benchmarking.dashboard.app --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。默认扫描 `state/benchmark-runs`，也可以重复传入 `--run-root`；`--annotation-db` 修改 dashboard 元数据数据库位置。

## 信息结构

页面采用高密度实验室监控台布局：左侧 run rail，主区为 run overview，选择题目后切换到单题复盘。顶部搜索同时过滤 run 和 record。

Run overview 包含：

- 进度和当前执行题目；
- 每个 group 的 score、平均/P95 单题时间、token、tool failure、包安装和资源峰值；
- Active attempts 及最近 CPU、内存、PID 窗口；超过 30 秒没有心跳的 attempt 标为 `stale`；
- 可按 record ID、总耗时、token、tool failure 或 score 排序的题目表。

单题复盘按 group 切换，包含 `Overview`、`Timeline`、`Exec`、`Packages`、`Tokens`、`Resources`、`Evidence`：

- `Timeline` 显示全部 attempt、重试、agent 时间、backoff、scoring wait 和 overhead；
- `Exec` 显示脱敏后的完整命令、请求/实际 cwd、持续时间、exit code、结果摘要和状态；原始命令只在 transcript 中保留；
- `Packages` 显示 baseline、agent 结束 inventory、策略清理后的 inventory、直接请求包、传递依赖、版本变化和失败安装；
- `Tokens` 显示 input、output、cache read/write、reasoning、total，以及 provider total 一致性；
- `Resources` 显示容器 CPU、内存、网络、block I/O、PID 的 5 秒窗口曲线和峰值；host backend 显示不可用；
- `Evidence` 显示状态轴、workspace 隔离和结构化执行错误。

## 指标口径

当前 run 的 per-record schema 为 v5，`observability` 包含 `coverage`、`totals`、`attempts` 和 `final_attempt`。单题总成本包含全部 retry、reminder 和 rescue invocation；最终 attempt 仍单独列出。

工具状态统一为 `success`、`failure`、`blocked`、`timeout`、`cancelled`、`incomplete`。除 `success` 外全部进入失败总数，同时保留 subtype。只有明确执行 `scripts/run_skill.py` 的命令才计入 skill runner call；普通 exec 不再被误称为 skill call。

容器资源通过持续 Docker stats reader 采集并按 5 秒窗口落盘到 `resources.jsonl`。采集失败只降低 telemetry coverage，不改变答案评分资格；不可用指标显示 `—`，不会以零值代替。

## Artifact 与历史兼容

新 run 目录会包含：

```text
observability/
  active/<attempt-id>.json
  attempts/<group>/<record>/attempt-<n>-<session-hash>/summary.json
  attempts/<group>/<record>/attempt-<n>-<session-hash>/resources.jsonl
```

`runtime-manifest.json` 的 `observability` 段记录 schema 和资源窗口。依赖 manifest v3 保留原 `distributions` 作为策略修复前证据，并增加 baseline、effective inventory 和 delta。

旧 v1-v4 run 不会被重写。Dashboard 只读投影已有字段：耗时通常是 `exact`，工具/包/token 多为 `partial`，历史资源为 `unavailable`。旧 run 仍可查看答案、评分、隔离和原 transcript。

## API

- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/records`
- `GET /api/runs/{run_id}/records/{record_id}`
- `GET /api/runs/{run_id}/progress`
- `GET /api/runs/{run_id}/monitor`
- `GET /api/runs/{run_id}/records/{record_id}/groups/{group_id}/attempts/{attempt_index}/resources`
- `GET /api/runs/{run_id}/assets/{asset_path}`
- `POST/PATCH/DELETE /api/annotations...`

资源 API 最多返回 2,000 个点；超过上限时保留首尾、CPU 峰值和内存峰值。所有 artifact 路径由服务端从 run root 解析，并拒绝 symlink 和路径逃逸。

## 安全与边界

默认只监听 `127.0.0.1`，不是多用户服务，也不提供 benchmark 启动能力。资产 API 只允许当前 run root 内的文件。dashboard 不会修改 `results.json`、per-record JSON、runtime manifest、transcript 或资源 artifact。
