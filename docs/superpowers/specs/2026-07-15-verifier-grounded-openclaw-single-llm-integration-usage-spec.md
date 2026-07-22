# Verifier-Grounded Benchmark 与 OpenClaw Single-LLM 标准接入使用规格

- 日期：2026-07-15
- 状态：`IMPLEMENTED`
- 适用项目：OpenClaw chemistry benchmark orchestration
- 适用 package：`verifier-grounded-benchmark==0.2.0`

## 1. 目的

本规格定义如何使用 `verifier-grounded-benchmark` 的标准公共 API，将 OpenClaw
single-LLM agent 作为外部模型调用方运行 package 已有题目，并在隔离环境中评分。

本规格同时限制接入层的复杂度：只保留 OpenClaw orchestration 与隔离运行所必需的 adapter，
不得为了缩短命令或兼容历史格式创建 VGB 专属脚手架。

## 2. 核心决策

1. VGB package 是 task/prompt/scorer 的唯一来源。
2. OpenClaw 位于 package 外部，符合 package README 规定的模型调用边界。
3. prompt 只通过 `Track.prompts()` 获取。
4. 单题评分只通过 `Track.evaluate_one()` 完成。
5. `property_calculation` 的公开 gold 只通过 `Track.sample_answers()` 获取，并且只写入最终报告面。
6. 批量评分如有需要只通过 `Track.evaluate_answers()` 或 package 自带 `vgb-score` 完成。
7. 不 import 或复制 package 的内部 `benchmark.*`、`verifiers.*` 实现。
8. package 安装在独立 scorer runtime，不安装进 agent/workspace 主 `.venv`。
9. 日常运行直接使用项目 canonical CLI：`python -m benchmarking.workflow.cli`。
10. 不新增 VGB 专属 CLI、track 别名转译器或 shell launcher。
11. 主要测试路径是 `single_llm_skills_on` / `single_llm_skills_off`，不以 ChemQA 为主路径。
12. 不定义 official run 认证层；单题、部分题和全量运行都是合法实验。

## 3. 标准 VGB 用法

### 3.1 获取 prompt

标准 package 用法：

```python
import verifier_grounded_benchmark as vgb

track = vgb.load_track("rdkit")
prompts = track.prompts()
```

OpenClaw provisioning 必须使用等价调用获取 public prompt。不得读取 package 源码目录中的
`tasks.yaml`、`verifier_specs.yaml` 或 `sample_answers.jsonl` 来替代该 API。

### 3.2 外部模型调用

package 不调用模型。OpenClaw runner 从 `prompts()` 产生的 public view 中取得：

```text
track
task_id
prompt
answer_schema
```

随后由 OpenClaw agent 生成 response。该责任划分与 package README 的标准流程一致。

### 3.3 单题评分

标准 package 用法：

```python
result = vgb.load_track(track_name).evaluate_one(
    {
        "task_id": task_id,
        "response": model_response,
    }
)
```

OpenClaw 当前采用这一公共 API，因为 orchestration 按 record 持久化答案、错误和 attempt
metadata。逐题调用不是兼容路径，也不改变 package scorer 语义。

### 3.4 公开标准答案报告

`property_calculation` 是 fixed-input track，package 将其 sample answers 定义为公开 gold。最终结果
报告必须通过公共 API 获取：

```python
gold = vgb.load_track("property_calculation").sample_answers()
```

该调用在隔离 runtime 中执行，并校验返回 task ID 与 pinned inventory 完全一致。gold 只替换最终
`results.json`、per-record 和后续 dashboard/analysis 使用的 `reference_answer`；不得写入 synchronized
JSONL、agent prompt、attempt workspace、scratch 或 runtime config。`rdkit` 与 `xtb` 继续报告隐藏
reference 占位文本。

### 3.5 批量评分

需要 package 原生 coverage/`benchmark_score` 语义时，应使用：

```python
report = vgb.load_track(track_name).evaluate_answers(answer_records)
```

或 package 自带 CLI：

```bash
vgb-score --track rdkit --answers answers.jsonl
```

不得在 OpenClaw 中重新实现 `coverage.complete` 或 package batch summary 算法。本规格当前不要求
日常 single-LLM test 使用 batch API。

## 4. 必要 Adapter 与禁止脚手架

### 4.1 允许的必要 Adapter

以下 adapter 是 OpenClaw runtime 边界所必需的：

1. **Release pin**：固定 package version、wheel SHA 和 track inventory。
2. **Isolated process bridge**：通过隔离 Python 进程调用公共 VGB API，防止 package 进入 agent
   环境。
3. **Sanitized prompt cache**：把 `track.prompts()` 返回值物化为通用 benchmark runner 可加载的
   JSONL；它只是 public prompt view 的确定性缓存。
4. **Result mapping**：把 package result 映射为项目统一 `EvaluationResult`，不改变 score。
5. **OpenClaw orchestration**：负责模型调用、attempt workspace、session、retry、audit 和 archive。
6. **Report-only public reference mapping**：在 agent attempts 全部结束后，通过
   `property_calculation.sample_answers()` 把公开 gold 写入最终通用结果 artifacts。

隔离进程的 JSON stdin/stdout 只是跨虚拟环境传输协议，不是对旧 VGB 版本或私有格式的兼容层。

### 4.2 禁止的脚手架

不得新增或恢复：

- `vgb-openclaw` 一类自定义 VGB 命令；
- VGB track 名到另一套快捷参数的专属 CLI 转译模块；
- package 内部 task/verifier 数据结构的本地副本；
- 本地重写的 answer extraction、constraint scoring 或 coverage 计算；
- 为未发布/旧 package 版本保留的 schema fallback；
- 将 sample answer、gold 或 verifier spec 注入 agent prompt/workspace 的桥接逻辑；
- 为了调用 package 而在 agent 主 `.venv` 中安装 VGB；
- 恢复已移除的根级 benchmark CLI facade，或为新接入新增替代 shim。

新文档和自动化必须调用 canonical module：

```bash
uv run python -m benchmarking.workflow.cli ...
```

## 5. 范围与非目标

### 5.1 包含

- 三个 formal VGB tracks；
- single-LLM skills-on/off；
- 单题、指定 task IDs、部分题和完整 dataset；
- model、thinking、timeout、retry 等现有通用 benchmark 参数；
- attempt-scoped workspace/session isolation；
- 隔离 VGB scoring；
- 通用 per-record/aggregate/report/archive 输出。

### 5.2 不包含

- official/diagnostic 认证层；
- 强制完整 coverage；
- VGB package 内部任务开发；
- ChemQA 专属快捷路径；
- 自定义 VGB CLI；
- 每次运行时在线安装 package；
- PyPI 发布自动化。

通用 benchmark CLI 仍可运行 ChemQA，但本规格的推荐命令只选择 single-LLM groups。

## 6. 固定 Release 身份

| 字段 | 固定值 |
| --- | --- |
| package | `verifier-grounded-benchmark` |
| version | `0.2.0` |
| source tag | `v0.2.0` |
| source commit | `81c50b42516a5e154ba91106052c954a64550708` |
| wheel | `verifier_grounded_benchmark-0.2.0-py3-none-any.whl` |
| wheel size | `143055` bytes |
| wheel SHA256 | `d2c2e12ec171bf5879dbf1fa74bde45fbf0a4de2e90339d9b98cce38d030a5a9` |
| Python | `>=3.12,<3.13` |

Release pin：

```text
benchmarking/resources/verifier_grounded/release.json
```

Verified wheel cache：

```text
~/.openclaw/data/verifier-grounded-releases/0.2.0/
```

Isolated scorer runtime：

```text
state/verifier-grounded-runtimes/0.2.0-d2c2e12ec171/
```

## 7. Track 与 Dataset 映射

项目 canonical CLI 使用通用 dataset 名；这些 dataset 均由对应 VGB track 的 public
`prompts()` 生成。

| VGB track | OpenClaw dataset | 题数 | verifier timeout | JSONL SHA256 |
| --- | --- | ---: | ---: | --- |
| `rdkit` | `verifier_grounded_rdkit` | 11 | 180 秒 | `71c5343e77f2fb62b58fc2fb6765703f178e2df7322cb7441353a480052e914d` |
| `xtb` | `verifier_grounded_xtb_xyz` | 18 | 1800 秒 | `02c14b303beaf75878dd9473cbb2bdd13876a70f3a40e2c4910f64fb6103fe69` |
| `property_calculation` | `verifier_grounded_property_calculation` | 2 | 180 秒 | `238cfccd73b9ed40ddfd03e52ea7a0fe5dedfdf937a738da7d5ba2d07886debf` |

Runtime JSONL：

```text
~/.openclaw/data/formal-benchmarks/<dataset>/data/<dataset>.jsonl
```

每条 synchronized record 只允许包含 public prompt、public answer schema、track/task ID、release
identity、verifier timeout 和不暴露 reference 的占位 answer 文本。`property_calculation` 的公开 gold
不进入该缓存，而是在最终报告阶段通过 `sample_answers()` 单独取得。

## 8. Provisioning

### 8.1 标准流程

Provisioning 是一次性管理员操作，不属于每次 benchmark run：

1. 获取固定 release wheel；
2. 校验 filename、size 和 SHA256；
3. 安装到 hash-addressed scorer `.venv`；
4. 在该环境中 import `verifier_grounded_benchmark`；
5. 调用每个 formal track 的 `prompts()`；
6. 仅保留 public answer schema 字段并写入 sanitized JSONL；
7. 验证 version、track inventory 和 task ID 顺序；
8. 写入 scorer runtime manifest。

实现位置：

```text
scripts/sync_verifier_grounded_datasets.py
```

该脚本不得从 package 源码 task 目录同步私有资源。

### 8.2 PyPI 发布后

外部用户从 PyPI 获取 `verifier-grounded-benchmark==0.2.0`，无需从本地 `dist/` 构建。获取的
wheel 仍必须在安装前匹配固定 SHA。

后续可以增强 provisioning 的 PyPI 下载方式，但不得增加 agent 侧 fallback 或改变标准 API
调用路径。

## 9. Canonical 运行入口

所有新运行和文档使用：

```bash
cd ~/.openclaw/workspace
uv run python -m benchmarking.workflow.cli [options]
```

不得为 VGB 增加第二套 parser。实验灵活性通过现有通用参数提供：

| 参数 | 用途 |
| --- | --- |
| `--groups` | 显式选择 skills-on/off single-LLM group |
| `--datasets` | 选择一个或多个 synchronized VGB dataset |
| `--record-ids` | 按给定顺序精确选择 task IDs |
| `--limit` / `--offset` | 部分运行 |
| `--single-agent-model` | model override |
| `--single-agent-thinking` | thinking override |
| `--single-timeout` | bounded attempt timeout |
| `--single-timeout-retries` | timeout-family retry 上限 |
| `--single-timeout-retry-backoff-seconds` | retry backoff |
| `--no-timeout` | 无模型作答上限模式 |
| `--no-analysis` | 关闭可选自动分析 |
| `--print-selected-records` | 只预览题目，不调用 agent |
| `--exact-output-dir` | 可选；覆盖默认分类目录并指定符合运行命名规范的输出目录 |

必须显式提供 `--groups`。不得依赖通用 CLI 的三组默认值，因为本规格主要运行 single-LLM。

## 10. 使用示例

### 10.1 预览一题

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_rdkit \
  --limit 1 \
  --print-selected-records
```

### 10.2 运行一题

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_rdkit \
  --limit 1 \
  --single-agent-model qwen3.5-plus \
  --single-agent-thinking high \
  --no-analysis
```

CLI 自动使用启动时的当前时间生成 run ID 和分类输出路径。

### 10.3 按 task ID 运行

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_xtb_xyz \
  --record-ids xtb_gap_window_001 \
  --no-analysis
```

### 10.4 多题且保持给定顺序

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_rdkit \
  --record-ids rdkit_sa_min_002,rdkit_qed_max_001 \
  --no-analysis
```

### 10.5 Skills-off

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_off \
  --datasets verifier_grounded_property_calculation \
  --limit 1 \
  --no-analysis
```

### 10.6 Skills-on/off 对照

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on,single_llm_skills_off \
  --datasets verifier_grounded_rdkit \
  --limit 2 \
  --max-concurrent-groups 1 \
  --no-analysis
```

### 10.7 完整 track

省略 `--limit`、`--offset` 和 `--record-ids`：

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_rdkit \
  --no-analysis
```

### 10.8 全部三个 tracks

```bash
uv run python -m benchmarking.workflow.cli \
  --groups single_llm_skills_on \
  --datasets verifier_grounded_rdkit,verifier_grounded_xtb_xyz,verifier_grounded_property_calculation \
  --no-analysis
```

## 11. Record 选择契约

通用 CLI 按以下顺序选择记录：

1. `--files` 或 `--datasets`；
2. `--subsets`；
3. `--record-ids`；
4. optional random sampling；
5. `--offset`；
6. `--limit`。

`--record-ids` 使用逗号分隔，必须：

- 按参数给定顺序执行；
- 拒绝重复 ID；
- 拒绝未知 ID；
- 拒绝跨已选 dataset 的歧义 ID。

该能力属于通用 benchmark record selection，不包含 VGB track alias 或 package 兼容逻辑。

## 12. 执行流程

1. canonical CLI 加载 synchronized JSONL；
2. records 经通用 `BenchmarkRecord` normalization；
3. runner 根据显式 group 创建 run-scoped OpenClaw config；
4. 每个 attempt 创建新 workspace/session；
5. agent 接收 public prompt/answer schema 并生成 response；
6. runner 收集 stdout、transcript、tool 和 scratch artifacts；
7. contamination audit 后归档完整 workspace；
8. 可评分 response 被提交到隔离 scorer；
9. scorer 校验 release identity 和 runtime manifest；
10. scorer 通过标准 `load_track(track).evaluate_one(...)` 评分；
11. package result 映射为统一 `EvaluationResult`；
12. 所有 agent attempts 结束后，CLI 通过隔离 runtime 的 `property_calculation.sample_answers()`
    校验并填充公开 report reference；
13. CLI 保存/重写 per-record、aggregate、runtime manifest 和 progress state。

Agent-facing prompt policy：

- synchronized record 中的 package public prompt 是 VGB 作答内容的唯一来源；
- bounded 模式只在 public prompt 前添加一行 attempt time budget；
- `--no-timeout` 模式不在 public prompt 前添加 time budget；
- single-LLM user prompt 不注入 skill tree，不强制读取 `act-like-a-chemist`，也不添加通用解题策略；
- user prompt 不复述 verifier 实现、单候选要求或 package prompt 已包含的 answer format；
- skills-on/off 使用相同的 VGB base prompt，差异只来自 OpenClaw config/system context 中的 skill availability 与 skills-on workspace `TOOLS.md`；
- primary/retry attempt 都沿用同一 base prompt，不为某个 group 追加 retry strategy guidance；
- attempt scratch 路径、workspace 规则、bounded time reminder 和 finalization rescue 仍属于 OpenClaw runtime orchestration；VGB finalization rescue 只要求基于已有会话完成原题格式，不复述 verifier 类型或 answer schema 模板。

## 13. Agent Workspace 隔离

每个 primary/retry attempt 必须：

- 使用唯一 session ID；
- 从 canonical template 创建完整新 workspace；
- 不包含 `.git`；
- 只包含当前 record/session scratch；
- 不复用 legacy fixed workspace；
- 在返回 runner result 前完成 audit/archive；
- contamination、audit unavailable 或 archive failure 时 fail closed；
- 不把 package wheel、sample answers、gold 或 verifier specs 写入 workspace。

历史 archive 只用于审计，不得成为后续 attempt 输入。

## 14. Scorer 隔离与映射

scorer runtime 必须：

- 使用 release hash 寻址；
- 在调用前校验 wheel size/SHA 和 runtime manifest；
- 使用 Python 3.12 与 `python -I`；
- 不继承 agent `PYTHONPATH`、`VIRTUAL_ENV`；
- 只调用 package public API；
- 对 xTB track 从允许的 `PATH` 查找本机 `xtb`。

OpenClaw `EvaluationResult` 映射：

- `primary_metric = "verifier_score"`；
- `score == normalized_score == package scores.score`；
- `passed = None`；
- 保存 verifier status、failure type、properties、constraint scores 和 versions；
- 不增加本地 pass threshold；
- 不重算 package constraint score。

## 15. Timeout 与 Retry

Agent 默认行为继承通用 CLI：

- bounded timeout：900 秒；
- timeout-family retries：3；
- backoff：`5,15,45` 秒；
- 每次 retry 使用新 workspace/session；
- `--no-timeout` 保留进程安全阀。

LLM idle timeout、transport timeout 等历史事件保留在 convergence diagnostics 中，但同一 session
后续由 native output、transcript recovery 或 finalization rescue 生成的完整、schema-aware 最终答案
优先于历史 timeout 状态。只有没有完整答案的 timeout sentinel/timeout-family 结果才不可评分。

Verifier timeout 固定于 release config：

- RDKit：180 秒；
- property_calculation：180 秒；
- xTB：1800 秒。

xTB 实际评分要求 `xtb` executable。当前本机验证版本为 `6.7.1 (edcfbbe)`。

## 16. 输出

所有 benchmark records 分类存放在：

```text
state/benchmark-runs/<formal|temporary>/<benchmark>/<model>/<run-id>/
```

输入文件全部位于临时 benchmark 根时使用 `temporary`，其他 run 使用 `formal`。单数据集使用
dataset 名作为 benchmark；多数据集使用 `mixed-datasets`。benchmark、模型和 run ID 均使用
文件系统安全 slug。`--exact-output-dir` 仅在调用者明确需要自定义路径时绕过该默认布局。

run 名必须符合：

```text
<benchmark name>-<single llm model name>-<timestamp>
```

主要 artifacts：

```text
results.json
runtime-manifest.json
skill-health.json
web-search-preflight.json
per-record/<group>/<record>.json
runtime-config/*.json
progress/state.json
agent-workspace-archives/
agent-workspace-quarantine/
analysis/status.json
```

`--no-analysis` 仍写入 `analysis/status.json`，状态为 `skipped`；只有启用并成功完成自动分析时才
产生 analysis report。

完成聚合时，`property_calculation` 的 `reference_answer` 必须是 public gold 的 canonical JSON；即使
agent 未作答或 record 不可评分也必须报告 gold。RDKit/xTB reference 仍保持隐藏占位文本。

## 17. 失败行为

调用 agent 前应失败：

- dataset 缺失；
- selector 非法；
- task ID 重复、未知或歧义；
- workspace startup recovery 失败。

结构化 per-record failure：

- OpenClaw/provider/subprocess 失败；
- timeout retry 耗尽；
- candidate answer contract 失败；
- workspace contamination/audit/archive 失败；
- package runtime、xTB 或 verifier execution 失败。

wrapper/provider/subprocess 非零退出时，runner 必须用已知 agent/session ID 执行 postflight session
定位，并在 transcript 已落盘时把该路径交给 workspace audit。干净 transcript 不得因父进程未解析到
wrapper stdout 而被误报为 `transcript_unavailable`，原始结构化 execution error 必须保留。

不得用本地 fallback scorer 或旧 schema 兼容分支掩盖 package 错误。

## 18. 实现位置

| 模块 | 责任 |
| --- | --- |
| `scripts/sync_verifier_grounded_datasets.py` | 标准 `prompts()` provisioning 与 sanitized JSONL 同步 |
| `benchmarking/runtime/vgb_bridge.py` | 隔离 runtime、release 校验以及 `prompts()`、`evaluate_one()`、`sample_answers()` 公共 API 调用 |
| `benchmarking/scoring/evaluators.py` | package result 到统一 `EvaluationResult` 的直接映射 |
| `benchmarking/workflow/cli.py` | canonical benchmark CLI、通用 record selection 与 report-only public gold 映射 |
| `benchmarking/workflow/runners/single_llm.py` | OpenClaw 外部模型调用与 attempt 生命周期 |
| `benchmarking/runtime/agent_workspace.py` | workspace prepare/audit/archive/quarantine |
| `benchmarking/resources/verifier_grounded/release.json` | 固定 release identity 与 inventory |

以下文件不得存在：

```text
vgb-openclaw
benchmarking/workflow/verifier_grounded_cli.py
```

## 19. 验收标准

1. Provisioning 只通过 package `track.prompts()` 获取 agent-facing task view。
2. Scoring 只通过 package `load_track(...).evaluate_one(...)`。
3. package 不安装进 agent/workspace 主 `.venv`。
4. sanitized JSONL 不包含 sample answer、gold、verifier specs 或 source repo 路径。
5. canonical CLI 可以显式选择 skills-on/off single-LLM groups。
6. `--record-ids` 保序并拒绝重复/未知/歧义 ID。
7. 不存在 VGB 专属 launcher 或 parser。
8. 每个 attempt 继续满足 workspace/session isolation 契约。
9. package score 直接映射，不增加本地评分逻辑。
10. `property_calculation` 最终结果通过公共 `sample_answers()` 报告完整 gold，且 gold 不进入 agent-facing artifacts。
11. 完整恢复答案不因同 session 的历史 idle timeout 被 candidate contract 否决。
12. provider/subprocess 失败且 transcript 已存在时，workspace audit 使用该 transcript 并保留原始 execution error。
13. README 和 `GLOBAL_DEV_SPEC.md` 推荐 canonical module CLI。
14. canonical CLI compatibility、dataset sync、runtime 和 evaluator 测试全部通过。

## 20. Release Metadata Follow-Up

最终 OpenClaw 接入 commit 推送后，package repo `releases/v0.2.0/manifest.json` 中的
`integrations.openclaw.commit` 必须更新到最终 commit。

三份 dataset 与 `release.json` 已从固定 `0.2.0` wheel 的公共 API 重新生成并冻结；该
back-reference 是 release provenance，不构成新的运行时 adapter。
