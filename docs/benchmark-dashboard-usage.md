# Benchmark Dashboard 使用说明

本文档说明如何启动和使用 OpenClaw benchmark 本地 dashboard。该 dashboard 用于查看已有或正在运行的 benchmark run，重点支持逐题审查、测试组对比、实时进度监控和人工复核备注。

## 适用范围

- 适用于本机单人审查 benchmark 结果。
- 默认只监听 `127.0.0.1`，不提供公网访问、多用户账号或权限系统。
- dashboard 只读取 benchmark 原始结果；收藏、隐藏、备注、标签、人工结论等只写入 dashboard 自己的 SQLite 元数据。
- dashboard 不负责启动 benchmark。启动 benchmark 仍使用现有 benchmark CLI。

## 快速启动

在项目根目录启动：

```bash
cd /Users/xutao/.openclaw/workspace
uv run --extra web-ui python -m benchmarking.dashboard.app --host 127.0.0.1 --port 8765
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

也可以使用项目脚本入口：

```bash
cd /Users/xutao/.openclaw/workspace
uv run --extra web-ui benchmark-dashboard --host 127.0.0.1 --port 8765
```

第一次使用 `--extra web-ui` 时，`uv` 可能会安装 FastAPI、uvicorn 等可选依赖。

## 常用启动参数

```bash
uv run --extra web-ui python -m benchmarking.dashboard.app \
  --host 127.0.0.1 \
  --port 8765 \
  --run-root state/benchmark-runs \
  --annotation-db state/benchmark-dashboard/dashboard.sqlite
```

参数说明：

- `--host`：服务监听地址。默认 `127.0.0.1`。
- `--port`：服务端口。默认 `8765`。
- `--run-root`：要扫描的 benchmark run 根目录。可以重复传入多个 `--run-root`。
- `--annotation-db`：dashboard 复核元数据数据库路径。

默认扫描：

```text
state/benchmark-runs
```

默认元数据数据库：

```text
state/benchmark-dashboard/dashboard.sqlite
```

## Dashboard 读取哪些数据

dashboard 会从 run 目录读取以下 benchmark 产物：

- `results.json`
- `runtime-manifest.json`
- `waves/*.json`
- `progress/events.jsonl`
- `progress/state.json`
- `per-record/<group_id>/<record>.json`
- `input-bundles/**/question.md`
- `input-bundles/**/images/*`
- `analysis/report.md`
- `analysis/input-bundle.json`

其中 `results.json`、`per-record/*/*.json` 等原始结果文件不会被 dashboard 修改。

## 首页：Run 列表

打开 dashboard 后会直接进入 run 列表，不会显示营销页或额外入口页。

首页左侧展示每个 benchmark run：

- run ID 或别名
- run 状态
- 完成进度
- 测试组数量
- `single_llm_skills_on` / `single_llm_skills_off` 的平均归一化分摘要，例如 `on 0.61 · off 0.54 · Δ +0.07`
- dataset 信息

这个摘要来自 `results.json.summary.groups.<group_id>.avg_normalized_score`，只用于快速比较 skills-on 与 skills-off；它不是 run 的整体平均分。如果只有 on 或 off 一组有可用分数，则只显示该组分数，不显示 `Δ`；如果没有可用分数，则整行不显示。

顶部支持：

- 搜索 run ID 或题目 ID
- 按 run 状态筛选
- 按 dataset 筛选
- 按 subset 筛选
- 刷新 run 列表
- 显示或隐藏已隐藏的 run

常用操作：

- 点击 run 卡片进入该 run 的逐题审查页面。
- 点击星标按钮收藏或取消收藏当前 run。
- 点击隐藏按钮隐藏当前 run。隐藏只写入 dashboard 元数据，不删除 run 目录。
- 打开“显示隐藏 run”后，可以看到并恢复已隐藏的 run。

## Run 页面：逐题审查

进入某个 run 后，页面分为三块：

- 左侧：run 列表。
- 中间：当前 run 的题目目录和进度条。
- 右侧：题目内容、参考答案、复核备注、测试组对比。

题目目录每条记录显示：

- record ID
- dataset / subset
- eval kind
- `on` / `off` 并排分数预览，分别对应 `single_llm_skills_on` 和 `single_llm_skills_off`
- 备注数量

如果某个 run 缺少 `single_llm_skills_on` 或 `single_llm_skills_off`，题目目录只显示实际存在的组；如果两者都不存在，则回退显示第一个可用测试组的分数或状态。

点击题目后，右侧会展示该题详情。

## 单题详情

单题详情包含：

- record ID
- dataset / subset / eval kind
- 题目内容
- 本地图片预览
- 标准答案或参考答案
- 参考解题路径
- judge checkpoint 或其他评分细节
- 各测试组答案与评分
- tool / skill 诊断信息
- 人工复核备注

对于 SuperChem 或 HLE 等含图片题，dashboard 会读取 run-local `input-bundles`，把 `question.md` 中的本地图片引用渲染为图片预览。图片只通过 dashboard asset API 读取，并限制在当前 run 目录内。

## 测试组对比

右侧“测试组对比”区域按优先顺序展示测试组结果：

1. `single_llm_skills_on`
2. `single_llm_skills_off`
3. `chemqa_skills_on`
4. 其他 group ID

每个测试组卡片展示：

- group ID
- score label
- normalized score 或原始 score
- 耗时
- answer availability
- degraded execution 标记
- 被测试者答案
- OpenClaw tool call 数
- skills-on 组的 skill call 数
- skills-on 组的 skill failure 数
- skills-off 组的 exec tool call 数
- skills-off 组的 exec tool failure 数
- recovery mode

`OpenClaw tools` 表示当前题目回答过程中 OpenClaw 工具调用总数。Dashboard 详情卡片按测试组语义二选一显示诊断：`skills_enabled=true` 的组显示 `Skill calls` / `Skill failures`，不额外显示 `Exec calls`；`single_llm_skills_off` 显示 `Exec calls` / `Exec failures`，不展示 skill 指标。

对于 verifier-grounded benchmark，卡片还会展示：

- verifier status
- canonical SMILES
- verifier score
- property / constraint score 相关信息

verifier-grounded 的 `passed = null` 不代表失败；dashboard 会按连续 verifier score 展示为已评分结果。

## 人工复核备注

点击题目详情右上角的编辑按钮可以新增复核备注。

备注字段包括：

- 状态，例如 `reviewed`、`needs_review`
- 标签，使用英文逗号分隔
- 人工结论，例如 `correct`、`incorrect`、`uncertain`
- 备注正文

已有备注会显示在“复核备注”区域，并支持：

- 编辑备注
- 删除备注

删除备注是软删除，只在 dashboard SQLite 元数据中标记删除，不影响 benchmark 原始结果。

## 实时进度监控

新 benchmark run 会写入：

```text
progress/events.jsonl
progress/state.json
```

dashboard 每 2 秒轮询当前 run 的进度，显示：

- run 当前状态
- 已完成数量 / 总数量
- 各 group 当前正在执行的 record ID
- 已完成 record 数
- 错误事件

如果旧 run 没有 `progress/` 目录，dashboard 会回退到以下信息推断进度：

- `per-record/<group_id>/*.json` 的数量
- `waves/*.json` 的状态
- `results.json` 是否存在

回退进度通常能显示完成率，但无法准确显示“当前正在做哪一题”。

## 数据安全和文件边界

dashboard 的资产接口只允许读取当前 run 目录内的文件。

例如允许读取：

```text
state/benchmark-runs/<run_id>/input-bundles/<record_id>/images/img01.png
```

不允许通过 `../` 跳出 run 目录读取任意本地文件。

dashboard 会修改的只有：

```text
state/benchmark-dashboard/dashboard.sqlite
```

不会修改：

- `results.json`
- `per-record/*/*.json`
- `runtime-manifest.json`
- `input-bundles/**`
- `analysis/**`

## API 参考

前端只调用本地 API，不直接读取本地文件路径。

常用 API：

- `GET /api/runs`
- `GET /api/runs?include_hidden=true`
- `GET /api/runs/{run_id}`
- `PATCH /api/runs/{run_id}`
- `GET /api/runs/{run_id}/records`
- `GET /api/runs/{run_id}/records/{record_id}`
- `GET /api/runs/{run_id}/progress`
- `GET /api/runs/{run_id}/assets/{asset_path}`
- `POST /api/annotations`
- `PATCH /api/annotations/{annotation_id}`
- `DELETE /api/annotations/{annotation_id}`

示例：

```bash
curl http://127.0.0.1:8765/api/runs
curl http://127.0.0.1:8765/api/runs/<run_id>/records
curl http://127.0.0.1:8765/api/runs/<run_id>/progress
```

## 常见问题

### 启动时报缺少 FastAPI 或 uvicorn

使用 `--extra web-ui` 启动：

```bash
uv run --extra web-ui python -m benchmarking.dashboard.app
```

### 页面里没有 run

检查默认 run 目录是否存在：

```bash
ls state/benchmark-runs
```

如果 run 存在于其他目录，启动时传入：

```bash
uv run --extra web-ui python -m benchmarking.dashboard.app --run-root /path/to/benchmark-runs
```

### run 被隐藏后找不到

点击顶部的“显示隐藏 run”按钮，再选择该 run，点击恢复按钮。

也可以直接删除或备份 dashboard 元数据数据库，但这会清除所有 dashboard 复核元数据：

```bash
rm state/benchmark-dashboard/dashboard.sqlite
```

### 旧 run 没有实时进度

旧 run 没有 `progress/events.jsonl` 和 `progress/state.json` 时，dashboard 只能根据 `per-record` 和 `waves` 推断进度。后续通过新版 runner 执行的 run 会自动生成进度文件。

### 图片没有显示

确认 run 目录内存在 `input-bundles`：

```bash
find state/benchmark-runs/<run_id>/input-bundles -maxdepth 3 -type f | head
```

如果原始 run 没有 materialized input bundle，dashboard 只能展示 per-record JSON 中的 prompt 文本。

## 关闭 dashboard

在启动 dashboard 的终端中按 `Ctrl-C` 即可停止服务。
