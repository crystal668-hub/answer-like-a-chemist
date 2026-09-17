# VGB Runtime Legacy Facility Cleanup Report

日期：2026-09-17

范围：`/Users/xutao/.openclaw/workspace` 项目库、其 benchmark runtime 代码与本地 benchmark 数据根

状态：DRAFT，待用户审批；本报告只提供清理依据和执行顺序，不执行删除

证据基线：Git `7006711`（`fix: canonicalize benchmark output directories`）；本轮对 production imports、dataset payload、formal/temp 数据目录、formal run metadata、skill inventory 和测试引用进行了只读检查。该提交最近一次全量测试结果为 `1001 passed, 11 skipped, 164 subtests passed`。本轮没有运行删除模拟、真实模型调用或 Docker acceptance，也没有检查仓库外部调用者，因此删除会话仍须执行 Phase 0 preflight。

## 1. 结论摘要

当前默认 benchmark CLI 的实际运行面是 verifier-grounded benchmark（VGB）自建数据集：

- `verifier_grounded_rdkit`，track `rdkit`；
- `verifier_grounded_xtb_xyz`，track `xtb`；
- `verifier_grounded_property_calculation`，track `property_calculation_advanced`；
- `verifier_grounded_property_calculation_easy`，track `property_calculation_basic`。

这四个数据集的记录统一使用 `eval_kind=verifier_grounded`，通过 pinned verifier release、隔离运行时和 single-LLM OpenClaw runner 完成执行与评分。当前正式 run 目录中没有发现非 VGB 数据集；现有 `mixed-datasets` run 也只包含上述 VGB 数据集。

旧数据集 `chembench`、`frontierscience_Olympiad`、`frontierscience_Research`、`superchem_multimodal`、`hle_chemistry` 以及配套的 ChemQA/DebateClaw/cleanroom 设施可以进入删除范围，但不能直接按目录名删除共享 runtime。它们仍嵌入以下共享组件：dataset model、subset 抽样、answer/convergence 分支、multimodal bundle、judge/evaluator registry、automated analysis、dashboard、测试和历史结果读取逻辑。

推荐的清理策略是：先从共享组件中移除旧数据集行为，再删除旧 evaluator、ChemQA 专用服务和旧 skill bundle，最后清理 runtime 数据与历史旧测试；每个阶段都以 VGB-only 测试和 smoke check 作为门槛。

## 2. 当前边界

### 2.1 当前 VGB 基础设施

以下模块属于当前 VGB 执行基础设施，应保留：

| 层次 | 必须保留的内容 | 证据/入口 |
| --- | --- | --- |
| 数据与 release | 四个 VGB JSONL、`release.json`、track/task inventory | `benchmarking/resources/verifier_grounded/`、`scripts/sync_verifier_grounded_datasets.py` |
| 选择与输出 | 数据发现、JSONL 解析、record selection、formal/temporary 分类、canonical benchmark 目录映射 | `benchmarking/workflow/dataset_selection.py`、`benchmarking/workflow/cli.py` |
| single-LLM 执行 | skills-on/off 两个 group、OpenClaw wrapper、answer recovery、session isolation、timeout/cancellation | `benchmarking/service/single/`、`benchmarking/core/convergence.py`、`benchmarking/runtime/session_*.py` |
| 尝试隔离 | workspace policy/audit、Docker attempt、attempt Python、dependency evidence、owned process cleanup、transcript index | `benchmarking/runtime/agent_workspace.py`、`workspace_policy.py`、`workspace_audit.py`、`container_*.py`、`attempt_*.py`、`transcript_index.py` |
| VGB 评分 | pinned release bridge、isolated scorer、可选 invocation worker、release validation cache | `benchmarking/runtime/vgb_bridge.py`、`vgb_worker.py`、`benchmarking/scoring/evaluators/verifier_grounded.py` |
| skill exposure | chemistry routing matrix 投影和 85 个 `single_agent_exposure=true` 技能 | `skills/chemistry-routing-matrix.json`、`benchmarking/skills/tree.py` |
| 结果与 dashboard | per-record persistence、streaming aggregate、progress、run inspection、VGB property track 展示 | `benchmarking/workflow/run_state.py`、`benchmarking/core/reporting.py`、`benchmarking/dashboard/` |
| VGB 运维脚本 | dataset sync、worker/cache validation、shadow scoring、runtime baseline、archive/inventory/replay 工具 | `scripts/` 中对应 `vgb`、runtime 和 archive 脚本 |

这里的“保留”不代表所有现有代码都无需重构。共享模块中的非 VGB 分支应在删除旧功能时一并简化，但模块本身不能作为旧设施整体移除。

### 2.2 旧数据集行为

旧数据集在当前生产代码中的直接分支包括：

| 旧行为 | 当前位置 | 删除前置工作 |
| --- | --- | --- |
| `chembench`、FrontierScience、SUPERChem、HLE subset 分类 | `benchmarking/core/datasets.py::classify_subset` | 将 subset 逻辑收敛为 VGB dataset/track 语义 |
| SUPERChem 成对抽样和旧 subset order | `benchmarking/workflow/dataset_selection.py` | 移除 `SUBSET_ORDER`、`SUPERCHEM_SUBSETS` 及其抽样路径 |
| dataset-specific evaluator registry | `benchmarking/scoring/registry.py`、`benchmarking/scoring/evaluators/` | registry 只保留 `verifier_grounded`，并移除 generic fallback 的调用契约 |
| ChemBench/FrontierScience/SUPERChem/HLE answer prompt | `benchmarking/service/single/prompts.py`、`openclaw_wrapper.py`、`runner.py` | 保留 VGB schema contract，删除旧 eval kind 的 marker/format 分支 |
| FrontierScience research rescue/convergence | `benchmarking/core/convergence.py` | 删除 research marker、research rescue parser 及对应测试 |
| HLE answer type、confidence/calibration | `benchmarking/core/prompt_inputs.py`、`benchmarking/core/reporting.py` | 删除 HLE-only API 和 aggregate field |
| SUPERChem/HLE input image bundle | `benchmarking/runtime/bundles.py` | 保留通用 path projection；删除旧数据集图片定位、下载/复制和 markdown 生成逻辑 |
| old dataset automated-analysis metrics | `benchmarking/analysis/automated.py` | 保留 VGB run analysis 和通用 run evidence，删除 SUPERChem/HLE/ChemQA 专属统计 |
| dashboard legacy display branches | `benchmarking/dashboard/service.py` | 保留 VGB dataset/track 展示；删除旧数据集 facet、旧 run schema 兼容分支前先确认历史 run 是否仍需浏览 |

### 2.3 ChemQA/DebateClaw 专用设施

这部分是独立的 legacy business path，不是 VGB 的基础设施：

- `benchmarking/service/chemdebate/`，共 19 个 tracked files，约 2,818 行；
- `skills/chemqa-review/`，共 41 个 tracked files，约 12,798 行；
- `skills/debateclaw-v1/`，共 65 个 tracked files，约 8,857 行；
- `skills/benchmark-cleanroom/`，共 5 个 tracked files，约 850 行；
- 对应的 ChemQA status、artifact reconstruction、slot provisioning、review protocol、DebateClaw state/launch 和 cleanroom 测试。

`benchmarking/workflow/runner_adapters.py` 仍保留 `runner_kind == "chemqa"` 的 lazy branch，`benchmarking/workflow/experiments.py` 也保留 legacy 错误提示。这些必须在删除 service 之前移除，否则删除目录会留下不可导入的运行分支。

`benchmarking/analysis/automated.py` 和 `benchmarking/dashboard/service.py` 还包含 ChemQA artifact/group 的只读解析。当前正式 run inventory 没有 ChemQA run；按“不保留旧设施兼容入口”的目标，这些分支也应删除。通用 historical schema up-conversion 仍被旧 VGB run 使用，不属于 ChemQA 兼容层，必须保留。

### 2.4 不能误删的 chemistry skills

`skills/` 顶层的大部分 chemistry provider skill 不是旧数据集设施。当前 `benchmarking/skills/tree.py` 从 matrix 暴露 85 个 `single_agent_exposure=true` 技能，skills-on 的 VGB run 会将它们注入 agent routing inventory。不能因为它们的领域覆盖了旧 benchmark，就把它们归为 legacy。

应删除的 skill bundle 仅限于 ChemQA business path 使用的：

- `skills/chemqa-review/`；
- `skills/debateclaw-v1/`；
- `skills/benchmark-cleanroom/`。

`benchmark-cleanroom` 虽然名称包含 benchmark，但当前 VGB single-LLM runtime 使用的是 `benchmarking/runtime/` 内的 attempt cleanup/owned process 机制，不依赖该 skill bundle；`benchmarking/skills/tree.py` 已将这三个 bundle 排除在 single-agent exposure 外。

## 3. 当前数据和历史状态清点

### 3.1 正式输入数据

本机 formal benchmark root 仍包含以下旧数据目录：

- `data/formal-benchmarks/chembench`，183 records；
- `data/formal-benchmarks/frontierscience`，60 records；
- `data/formal-benchmarks/hle`，187 records；
- `data/formal-benchmarks/superchem`，239 records。

VGB formal 输入为 20、51、14、20 records，分别对应 advanced、basic、RDKit、xTB。旧数据目录属于 runtime home generated/input state，不是 canonical Git source；删除时必须作为独立的本机数据清理步骤执行，不能把它们与 Git 文件删除混在一次不可审计的递归删除中。

`data/temp-benchmarks/` 当前仍有 `chembench`、`frontierscience`、`hle`、`superchem` 目录。它们也应单独清点是否存在未完成任务或外部引用后再删除。

### 3.2 正式 run 状态

当前 `state/benchmark-runs/formal/` 顶层只有：

- `vgb-rdkit`；
- `vgb-xtb`；
- `vgb-property-calculation-advanced`；
- `vgb-property-calculation-basic`；
- `mixed-datasets`，但现有 mixed run 只组合 VGB 数据集。

没有发现 `chembench`、`frontierscience`、`superchem` 或 `hle` 的正式 run 目录。因此这次清理不应删除 VGB run evidence，也不需要将旧数据集 run 迁移到别的 benchmark 目录。`legacy-workspace-archives/` 和其他 generated evidence 仍需按文件内容单独判定，不能按名字递归删除。

## 4. 推荐删除范围

### 4.1 可以在解耦后删除的源代码

完成共享模块收敛后，以下内容可删除：

1. `benchmarking/scoring/evaluators/chembench.py`。
2. `benchmarking/scoring/evaluators/frontierscience.py`。
3. `benchmarking/scoring/evaluators/superchem.py`。
4. `benchmarking/scoring/evaluators/hle.py`。
5. `benchmarking/scoring/evaluators/generic.py`，前提是 registry 不再保留 generic fallback。
6. `benchmarking/core/prompt_inputs.py`，前提是 HLE prompt API 已移除。
7. `benchmarking/service/chemdebate/` 全目录。
8. `skills/chemqa-review/`、`skills/debateclaw-v1/`、`skills/benchmark-cleanroom/` 全目录。
9. 只服务于上述模块的 tests、fixtures 和 test-only imports。

### 4.2 需要修改而不是直接删除的共享文件

这些文件仍是 VGB 当前链路的一部分，必须保留并做定向清理：

- `benchmarking/core/datasets.py`：只保留 VGB record/grading/track 语义；去除旧 dataset 分类。
- `benchmarking/core/convergence.py`：只保留 generic final answer 和 VGB schema answer contract。
- `benchmarking/core/reporting.py`：去掉 HLE calibration 等旧 metric，但保留 VGB aggregate schema。
- `benchmarking/runtime/bundles.py`：保留 `RuntimeBundle`、`RuntimePathProjection` 和 VGB 通用路径审计；移除旧图片 bundle。
- `benchmarking/service/single/prompts.py`：移除旧 eval kind prompt 分支，保留 VGB prompt 以及 skills catalog。
- `benchmarking/service/single/openclaw_wrapper.py`：移除旧 rescue prompt 分支，保留 VGB rescue。
- `benchmarking/service/single/runner.py`：移除旧答案 contract 和 eval-kind 分支，保留 VGB contract、Docker/host attempt 与 lifecycle。
- `benchmarking/workflow/dataset_selection.py`：移除旧 subset sampling，保留 VGB dataset selection、canonical output mapping 和 formal/temporary classification。
- `benchmarking/workflow/cli.py`：移除旧 subset 参数帮助、judge 初始化和 legacy group 参数；保留 VGB CLI、VGB release validation、run persistence 和 output path。
- `benchmarking/workflow/orchestration.py`：去除通用 judge/evaluator 依赖后保留 VGB runner-result orchestration。
- `benchmarking/workflow/runner_adapters.py`：删除 `chemqa` 分支，只保留 `single_llm`。
- `benchmarking/workflow/experiments.py`、`benchmarking/service/single/experiments.py`：保留两个 single-LLM group，清理 legacy 错误信息和不再使用的 runner kind。
- `benchmarking/runtime/config_pool.py`、`benchmarking/runtime/config.py`、`benchmarking/service/single/config.py`：移除 judge agent provisioning 后，重新定义只含 VGB single-LLM agent 的 run config。
- `benchmarking/runtime/judge.py`、`benchmarking/resources/agent-workspace-templates/judge/`：在确认 VGB evaluator 不再接受 judge 参数后删除。
- `benchmarking/analysis/automated.py`、`benchmarking/dashboard/service.py`：删除旧 dataset/ChemQA 分支，但保留 VGB evidence inspection。
- `benchmarking/__init__.py`、`benchmarking/scoring/registry.py`：同步缩小 public export 和 evaluator registry。

### 4.3 暂不删除的项目级依赖

`pyproject.toml` 的 distribution name 仍是 `chemqa`，extras 也使用 `chemqa[...]`。这属于项目身份/打包契约，不是旧 dataset 设施本身。建议不要在第一次代码删除中顺便改名；如需改成 VGB 名称，应另开一个有独立迁移和 `uv.lock` 重建的任务。

同样，历史 `docs/design/`、`docs/plan/`、`docs/handoff/`、`docs/report/` 不应因旧内容而静默删除。项目文档规则要求保留历史决策和证据；只有在明确批准文档归档/清理时，才另行处理。

### 4.4 测试清理边界

可以随 legacy implementation 整文件删除的测试候选包括：

- `tests/test_benchmark_status.py`；
- `tests/test_benchmark_cleanroom.py`；
- `tests/test_benchmarking_cleanroom_module.py`；
- `tests/test_check_runtime_environment.py`；
- `tests/test_chemqa_artifact_flow.py`；
- `tests/test_chemqa_convergence.py`；
- `tests/test_chemqa_epoch_flow.py`；
- `tests/test_chemqa_workspace_isolation.py`；
- `tests/test_benchmarking_runtime_bundles.py`，前提是 `RuntimePathProjection` 的 VGB/container 覆盖保留在 `tests/test_infra_input_projection.py` 等测试中；
- 被删除 skill bundle 自带的 `skills/chemqa-review/tests/`。

以下是混合测试文件，不能整文件删除，应移除 legacy cases 并保留或改写 VGB/runtime cases：

- `tests/test_benchmark_test.py`：同时包含大量 ChemQA、旧 dataset、judge 和仍有效的 single-LLM/runtime contract；建议先按责任拆分再删除旧 cases。
- `tests/test_benchmark_evaluators.py`：保留 VGB evaluator tests，删除五个旧 evaluator tests。
- `tests/test_benchmark_datasets.py`：保留 VGB JSONL/release fields，删除旧 dataset normalization cases，并新增非 VGB dataset 拒绝测试。
- `tests/test_benchmark_prompts.py`、`tests/test_benchmark_convergence.py`、`tests/test_single_llm_session_wrapper.py`：保留 VGB answer-schema/finalization tests，删除旧 answer format branches。
- `tests/test_benchmarking_cli.py`：保留四个 VGB dataset、目录映射、selection 和 lifecycle tests，删除旧 subset sampling fixtures；新增 release inventory 与 canonical mapping key 完全一致的测试。
- `tests/test_benchmark_config_runtime.py`、`tests/test_benchmark_cancellation.py`、`tests/test_service_boundaries.py`：删除 judge/ChemQA cases，保留 single-LLM config、cancellation 和 import-boundary assertions。
- `tests/test_automated_evaluation.py`、`tests/test_benchmark_dashboard.py`：删除 ChemQA/旧 metric cases，保留 VGB historical run inspection。
- `tests/test_experimental_chemistry_skill_matrix.py`、`tests/test_benchmark_skill_tree.py`、`tests/test_act_like_a_chemist_skill.py`：移除三个 legacy orchestration bundle 的断言，但保留 85 个 VGB skills-on inventory 的完整性检查。
- `tests/test_benchmarking_orchestration.py`：将作为中性 fixture 的 `chembench`/`generic_semantic` records 改为最小合法 VGB records，不能因此删除调度、持久化或 error-path coverage。

旧 dataset 名被大量用作与业务无关的测试占位值；删除会话必须区分“测试数据标签”与“被测旧行为”。最终 production scan 应为零命中，测试 scan 只允许历史文档或明确的拒绝测试命中。

## 5. 推荐执行顺序

### Phase 0：删除前快照与边界锁定

1. 确认 Git worktree clean，记录当前 HEAD。
2. 导出 tracked file inventory、formal/temp dataset inventory、run inventory 和当前测试基线。
3. 检查 OpenClaw benchmark 进程、Docker container、active workspace、cleanroom lease 和未完成 run。
4. 对 `data/formal-benchmarks/{chembench,frontierscience,hle,superchem}` 和 `data/temp-benchmarks/*` 做内容/大小/SHA-256 清单。
5. 明确保留 `state/benchmark-runs/` 下的 VGB results、VGB archive evidence、dashboard metadata 和 legacy-workspace evidence，除非有单独批准。

### Phase 1：删除 legacy business path

1. 从 workflow runner selection、experiments、config pool 和 CLI 中移除 `chemqa` runner。
2. 删除 `benchmarking/service/chemdebate/`。
3. 删除 `skills/chemqa-review/`、`skills/debateclaw-v1/`、`skills/benchmark-cleanroom/`。
4. 删除仅用于这三套设施的 tests 和 fixtures。
5. 从 `benchmarking/skills/tree.py` 的 orchestration exclusion 和相关 tests 中删除已经不存在的 bundle 名称。
6. 运行 service-boundary、workspace、runtime config 和 full suite，确认 import graph 不再加载 legacy service。

### Phase 2：收敛 VGB-only shared runtime

1. 将 evaluator registry 缩为 `verifier_grounded`，同时删除 `judge` 参数在 VGB-only 调用链上的传递；让 dataset loader 或 CLI 对非 VGB dataset/eval kind fail closed。
2. 删除 judge config、judge workspace template、`JudgeClient` 和 judge-specific tests。
3. 收敛 dataset model、subset、sampling、prompt、answer contract、convergence、reporting、analysis 和 dashboard 的旧分支。
4. 将 `runtime/bundles.py` 重构为只支持 VGB 所需的通用 bundle/path projection；VGB 四个内置数据集没有 image bundle 依赖，若确认 VGB 输入永远只含 JSON prompt，可进一步移除 `ensure_runtime_bundle` 及其所有调用。
5. 删除 `generic_semantic` fallback，令未知 eval kind 在加载或 registry 阶段显式失败，避免旧数据重新进入当前 runtime。
6. 建立 release inventory 与 canonical output mapping 的一致性测试，确保 `release.json` 的四个 track/dataset 与默认目录映射不会独立漂移。

### Phase 3：删除旧输入数据与生成状态

仅在 Phase 0 清单确认且用户单独批准后执行：

1. 删除 `/Users/xutao/.openclaw/data/formal-benchmarks/chembench`、`frontierscience`、`hle`、`superchem`。
2. 删除 `/Users/xutao/.openclaw/data/temp-benchmarks/` 下对应旧数据目录。
3. 检查 `agents/`、`benchmark/`、`logs/`、`flows/`、`tasks/` 和 dashboard DB 是否仍包含未完成旧 benchmark 引用；只清理确认属于旧设施的 generated state。
4. 不删除 VGB release cache、VGB runtime、VGB resources、VGB run records 或 VGB skills-on skill tree。

### Phase 4：可选项目身份清理

单独评审是否将 distribution name 从 `chemqa` 改为 `vgb-benchmark`。此项会影响 `pyproject.toml`、`uv.lock`、extras 文本、错误消息、文档和安装缓存，不应与功能删除隐式合并。

## 6. 验收矩阵

删除会话完成前必须同时满足：

| 验收项 | 目标 |
| --- | --- |
| production import scan | `benchmarking/`、`scripts/` 中不再引用被删除的 ChemQA/DebateClaw/旧 evaluator 模块 |
| dataset contract | 只接受 `release.json` 声明的四个 VGB dataset，所有记录 `eval_kind=verifier_grounded`，track/task/release identity 校验仍有效；未知 dataset/eval kind fail closed |
| output layout | 四个单数据集和 mixed VGB 运行都进入 canonical formal/temporary 目录；不再生成旧 dataset slug 目录 |
| scoring | 四个 VGB track 均可完成 isolated verifier scoring；worker/cache opt-in 测试仍通过 |
| single-LLM | skills-on/off、Docker 默认 backend、host fallback、timeout/retry、session isolation、cancellation、workspace audit 仍通过 |
| skill exposure | 85 个 matrix skills 仍可生成 routing inventory；只有 legacy orchestration bundles 不再出现 |
| dashboard | VGB run discovery、property advanced/basic track facets、record detail、progress reconciliation 正常 |
| no legacy data | formal/temp benchmark input root 中没有旧数据集目录；运行时配置不再暴露旧 dataset/subset 参数 |
| tests | VGB 相关测试、runtime/infrastructure 测试和全量测试均通过；删除后的旧测试已移除而非简单 skip |
| repository state | `git diff --check` 通过，文档索引和 `GLOBAL_DEV_SPEC.md` 与实际实现一致；每个代码变更按 AGENTS 要求单独提交 |

建议 VGB smoke 命令至少覆盖：

```bash
cd /Users/xutao/.openclaw/workspace
uv run python -m benchmarking.workflow.cli --groups single_llm_skills_on --datasets verifier_grounded_rdkit --limit 1 --print-selected-records
uv run python -m benchmarking.workflow.cli --groups single_llm_skills_on --datasets verifier_grounded_xtb_xyz --limit 1 --print-selected-records
uv run python -m benchmarking.workflow.cli --groups single_llm_skills_on --datasets verifier_grounded_property_calculation --limit 1 --print-selected-records
uv run python -m benchmarking.workflow.cli --groups single_llm_skills_on --datasets verifier_grounded_property_calculation_easy --limit 1 --print-selected-records
uv run pytest -q
```

这些 smoke 命令只做 selection/record contract 验证，不代表一次真实模型调用；真实模型或 Docker acceptance 需要另行安排和记录 run artifact。

## 7. 风险和审批边界

- 删除 `JudgeClient` 是共享调用链改动，不应因为 VGB evaluator 当前不使用 judge 就直接删除；必须先修改 orchestration/config/CLI 并通过 VGB-only tests。
- `runtime/bundles.py` 同时承担 container path projection 和旧 multimodal input materialization；只能删除数据集专属部分，不能删除整个模块。
- `benchmarking/core/datasets.py` 的通用 `BenchmarkRecord` 是 VGB 的输入模型；只能删除旧分类分支，不能删除 record loader。
- `benchmarking/analysis/automated.py` 和 dashboard 的 dataset/ChemQA 专属只读解析应随旧设施删除；但与 dataset 无关的 historical schema up-conversion 仍服务旧 VGB run，不得一并删除。
- `state/benchmark-runs/`、runtime home data、agents/session/logs 和 dashboard DB 都是本机生成状态，不应与 Git source 删除混合执行。
- 历史 docs 保留是当前文档治理规则，不代表旧设施仍受支持；可以在清理完成后增加 supersession 说明，但不要静默改写历史报告。

本报告完成后，下一会话应先等待用户对 Phase 1、Phase 2、Phase 3 是否全部批准；未明确批准的数据状态删除和项目 distribution rename 不得执行。
