# VGB Runtime Legacy Dataset Cleanup Report

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

旧数据集 `chembench`、`frontierscience_Olympiad`、`frontierscience_Research`、`superchem_multimodal`、`hle_chemistry` 在默认 VGB runtime 中的接入面和本机输入数据可以进入清理范围。ChemQA/DebateClaw 则是已经明确标记为 legacy/frozen 的独立功能，源码、三个依赖 skill bundle、显式入口、持久化标识、历史读取能力和保全测试均不属于本次直接删除范围。

推荐的清理策略是：先把默认 VGB 路径与 frozen ChemQA 路径的注册、参数和依赖边界显式分开，再从默认 single-LLM 路径移除旧数据集行为，最后单独清理旧输入数据。共享 evaluator、judge、multimodal bundle 或 dataset loader 只要仍服务 frozen entrypoint，就保留为 frozen dependency，不做全局删除。

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

这里的“保留”不代表默认 VGB composition 无需重构。应简化的是 active VGB service 对旧能力的注册和调用，不是仍由 frozen service 使用的 shared implementation；共享模块不能作为旧设施整体移除。

### 2.2 旧数据集行为

旧数据集在当前生产代码中的直接分支包括：

| 旧行为 | 当前位置 | 本次边界内的处理 |
| --- | --- | --- |
| `chembench`、FrontierScience、SUPERChem、HLE subset 分类 | `benchmarking/core/datasets.py::classify_subset` | 默认 VGB service 不调用旧分类；shared helper 保留给 frozen service |
| SUPERChem 成对抽样和旧 subset order | `benchmarking/workflow/dataset_selection.py` | 从默认 VGB 参数/调用链移出，保留为 frozen service helper |
| dataset-specific evaluator registry | `benchmarking/scoring/registry.py`、`benchmarking/scoring/evaluators/` | 改为 service-scoped registration；默认只注册 VGB，旧 evaluator/generic 保留给 frozen service |
| ChemBench/FrontierScience/SUPERChem/HLE answer prompt | `benchmarking/service/single/prompts.py`、`openclaw_wrapper.py`、`runner.py` | 只清理 active single-LLM 分支；ChemQA 自有 prompt/driver 不改 |
| FrontierScience research rescue/convergence | `benchmarking/core/convergence.py` | 默认 VGB 不进入该分支；若 frozen/history 仍依赖则保留 shared parser 和 tests |
| HLE answer type、confidence/calibration | `benchmarking/core/prompt_inputs.py`、`benchmarking/core/reporting.py` | 默认 VGB 不进入该分支；保留 frozen service 和历史汇总所需 API |
| SUPERChem/HLE input image bundle | `benchmarking/runtime/bundles.py` | 默认 VGB adapter 不 materialize；保留通用 path projection 和 frozen materialization |
| old dataset automated-analysis metrics | `benchmarking/analysis/automated.py` | 默认 VGB analysis 不再生成旧 metric；保留 frozen ChemQA 和历史只读解析 |
| dashboard legacy display branches | `benchmarking/dashboard/service.py` | 保留 VGB dataset/track 展示以及 frozen ChemQA/历史 run 的只读解析 |

### 2.3 ChemQA/DebateClaw 冻结保留边界

这部分是独立的 legacy/frozen business path，不是默认 VGB 基础设施，但也不是本次直接删除对象：

- `benchmarking/service/chemdebate/`，共 19 个 tracked files，约 2,818 行；
- `skills/chemqa-review/`，共 41 个 tracked files，约 12,798 行；
- `skills/debateclaw-v1/`，共 65 个 tracked files，约 8,857 行；
- `skills/benchmark-cleanroom/`，共 5 个 tracked files，约 850 行；
- 对应的 ChemQA status、artifact reconstruction、slot provisioning、review protocol、DebateClaw state/launch、cleanroom 和保全测试。

`benchmarking/service/chemdebate/LEGACY.md` 是该边界的直接契约：显式历史入口 `uv run python -m benchmarking.service.chemdebate.cli`、上述三个 skill bundle 的物理路径、persisted `chemqa`/`chemqa_skills_on` 标识、artifact filenames、历史结果和 dashboard/analysis 只读支持继续保留；provider skills、shared scoring 和 distribution name `chemqa` 也没有被废弃。`GLOBAL_DEV_SPEC.md` 同样将其定义为“legacy, frozen”，不是待删除功能。

因此，本次允许做的是默认 VGB 路径解耦：默认 catalog 不加载 ChemQA、默认 CLI 不暴露 ChemQA 参数、active VGB config 不创建 ChemQA runtime。不得删除 `chemdebate` service、三个 skill bundle、`runner_kind == "chemqa"` 的显式 legacy composition、ChemQA dashboard/analysis 只读解析或其保全测试，也不得通过删除 shared evaluator/judge/bundle 间接使显式入口不可导入。

### 2.4 不能误删的 chemistry skills

`skills/` 顶层的大部分 chemistry provider skill 不是旧数据集设施。当前 `benchmarking/skills/tree.py` 从 matrix 暴露 85 个 `single_agent_exposure=true` 技能，skills-on 的 VGB run 会将它们注入 agent routing inventory。不能因为它们的领域覆盖了旧 benchmark，就把它们归为 legacy。

以下 orchestration skill bundle 不进入 VGB single-agent exposure，但属于 frozen ChemQA dependency，必须保留：

- `skills/chemqa-review/`；
- `skills/debateclaw-v1/`；
- `skills/benchmark-cleanroom/`。

`benchmark-cleanroom` 虽然名称包含 benchmark，但当前 VGB single-LLM runtime 使用的是 `benchmarking/runtime/` 内的 attempt cleanup/owned process 机制，不依赖该 skill bundle；它仍由 frozen ChemQA cleanroom integration 使用。`benchmarking/skills/tree.py` 应继续将这三个 bundle 排除在 VGB single-agent exposure 外，而不是从仓库删除它们。

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

## 4. 推荐清理范围

### 4.1 审批后可直接删除的本机输入数据

本次没有整目录删除 source package 或 frozen tests 的建议。可直接删除的对象仅限 Phase 0 清点并再次审批后的本机旧输入数据：

1. `/Users/xutao/.openclaw/data/formal-benchmarks/chembench`。
2. `/Users/xutao/.openclaw/data/formal-benchmarks/frontierscience`。
3. `/Users/xutao/.openclaw/data/formal-benchmarks/hle`。
4. `/Users/xutao/.openclaw/data/formal-benchmarks/superchem`。
5. `/Users/xutao/.openclaw/data/temp-benchmarks/` 下对应四个旧数据目录。

删除这些本机输入会使 frozen ChemQA entrypoint 不再具备默认旧数据源，但不会删除其代码、显式入口或历史结果读取能力。若仍需本机重放 ChemQA，Phase 3 应跳过数据删除，或先将输入移到独立归档位置。

### 4.2 默认 VGB 路径需要定向清理的代码

这些文件仍是 VGB 当前链路或 frozen legacy 共享层的一部分，必须保留，只能定向收窄默认 VGB 行为：

- `benchmarking/service/single/prompts.py`：移除旧 eval kind prompt 分支，只保留 VGB prompt 与 skills catalog。
- `benchmarking/service/single/openclaw_wrapper.py`：默认 single-LLM wrapper 只保留 VGB rescue prompt；不要修改 ChemQA 自有 prompt/driver。
- `benchmarking/service/single/runner.py`：移除旧答案 contract 和 eval-kind 分支，保留 VGB contract、Docker/host attempt 与 lifecycle。
- `benchmarking/workflow/cli.py`：默认 service 只接受 release inventory 中的 VGB datasets，并不再显示旧 subset sampling 参数；显式 `chemdebate` service 仍可保留其 legacy 参数和行为。
- `benchmarking/workflow/dataset_selection.py`：将 VGB selection/目录映射与 legacy subset sampling 分成明确入口；默认 service 只调用 VGB 路径，不能全局删除仍被 frozen service 使用的 loader/sampling helper。
- `benchmarking/scoring/registry.py`：默认 service 只注册 `verifier_grounded`；旧 evaluator 和 `generic_semantic` fallback 保留给 frozen service/shared scoring，不在 active VGB registry 中注册。
- `benchmarking/workflow/orchestration.py`：VGB 评分路径不创建或调用 judge；保留 frozen service 所需的 judge-compatible orchestration contract，除非先拆成独立 legacy adapter。
- `benchmarking/runtime/config_pool.py`、`benchmarking/runtime/config.py`、`benchmarking/service/single/config.py`：默认 VGB config 不物化 judge agent；judge provisioning 仍供 frozen ChemQA/shared scoring 使用。
- `benchmarking/runtime/bundles.py`：默认 VGB single-LLM adapter 使用无 input bundle 路径；保留 `RuntimePathProjection`，并保留 frozen ChemQA 所需的 SUPERChem/HLE materialization。
- `benchmarking/core/datasets.py`：保留共享 `BenchmarkRecord` 和 frozen dataset normalization；默认 VGB service 在加载后额外执行 VGB allowlist/release validation。
- `benchmarking/core/convergence.py`、`benchmarking/core/reporting.py`：VGB active path 不进入 FrontierScience/HLE 分支；若这些分支仍被 frozen tests/entrypoint 使用则保留，不做全局删除。
- `benchmarking/analysis/automated.py`、`benchmarking/dashboard/service.py`、`benchmarking/workflow/run_state.py`：保留 ChemQA 与历史 schema 的只读支持；默认新 run 只产生 VGB 数据。
- `benchmarking/workflow/runner_adapters.py`：保留 explicit frozen service 使用的 `chemqa` lazy branch；默认 experiment catalog 继续只暴露 `single_llm`。
- `benchmarking/__init__.py`：只有在 public exports 与 frozen import contract 均确认后才能收窄。

### 4.3 明确禁止直接删除的 frozen surface

以下内容是本次清理的保护对象：

- `benchmarking/service/chemdebate/` 全目录；
- `skills/chemqa-review/`、`skills/debateclaw-v1/`、`skills/benchmark-cleanroom/`；
- `benchmarking/runtime/judge.py`、judge workspace template 与 judge provisioning；
- ChemBench、FrontierScience、SUPERChem、HLE、generic shared evaluator 源文件；
- ChemQA artifact/group 的 dashboard 和 automated-analysis 只读解析；
- `chemqa`、`chemqa_skills_on`、artifact filenames 和 historical result schema；
- distribution name `chemqa` 和现有 extras 名称；
- 历史设计、计划、handoff、report 文档。

这些内容不再新增功能、不做模型适配、不跟进未来 OpenClaw compatibility，但“冻结”不等于“删除”。本次清理若需要调整 shared composition，必须证明显式 legacy entrypoint 仍可导入，现有 frozen regression tests 仍通过，且 physical paths 与 persisted identifiers 未变化。

### 4.4 测试清理边界

ChemQA/DebateClaw/cleanroom 测试及其 skill-local tests 全部保留，作为 frozen surface 的保全哨兵，包括：

- `tests/test_benchmark_status.py`；
- `tests/test_benchmark_cleanroom.py`；
- `tests/test_benchmarking_cleanroom_module.py`；
- `tests/test_check_runtime_environment.py`；
- `tests/test_chemqa_artifact_flow.py`；
- `tests/test_chemqa_convergence.py`；
- `tests/test_chemqa_epoch_flow.py`；
- `tests/test_chemqa_workspace_isolation.py`；
- `skills/chemqa-review/tests/`；
- `tests/test_service_boundaries.py` 中 frozen import/entrypoint assertions；
- `tests/test_automated_evaluation.py`、`tests/test_benchmark_dashboard.py` 中 ChemQA 历史只读 cases。

旧 shared evaluator、judge、SUPERChem/HLE bundle 仍被 frozen surface 使用，因此对应测试也保留。可以删除或改写的仅是默认 `service.single` 对旧 eval kind 的 cases，以及把旧 dataset 名当作无关占位值的 active VGB/runtime tests：

- `tests/test_benchmark_prompts.py`、`tests/test_benchmark_convergence.py`、`tests/test_single_llm_session_wrapper.py`：删除默认 single-LLM 的旧 answer format cases，保留 VGB 和 shared/frozen coverage。
- `tests/test_benchmarking_cli.py`：默认 service cases 收敛到四个 VGB dataset；legacy service 参数/entrypoint cases 保留。新增 release inventory 与 canonical mapping key 完全一致的测试。
- `tests/test_benchmark_test.py`：先按 active single 与 frozen ChemQA/shared scoring 责任拆分；只删除 active single 的旧 dataset cases，不能整文件或批量删除 ChemQA/judge coverage。
- `tests/test_benchmarking_orchestration.py`：将 active-path 中作为中性 fixture 的 `chembench`/`generic_semantic` records 改为最小合法 VGB records，同时保留 frozen evaluator orchestration coverage。
- `tests/test_benchmark_skill_tree.py`、`tests/test_experimental_chemistry_skill_matrix.py`、`tests/test_act_like_a_chemist_skill.py`：继续断言三个 frozen orchestration bundles 不会暴露给 VGB single agent；不能移除这些 bundle 的存在性边界。

最终 production scan 不能简单要求旧名称零命中。正确标准是：默认 VGB service 的 import、参数、registry 和 execution branches 中无旧 dataset 能力；命中只允许存在于 frozen `chemdebate` surface、明确的 shared dependency、历史只读解析、拒绝测试和历史文档中。

## 5. 推荐执行顺序

### Phase 0：删除前快照与边界锁定

1. 确认 Git worktree clean，记录当前 HEAD。
2. 导出 tracked file inventory、formal/temp dataset inventory、run inventory 和当前测试基线。
3. 检查 OpenClaw benchmark 进程、Docker container、active workspace、cleanroom lease 和未完成 run。
4. 对 `data/formal-benchmarks/{chembench,frontierscience,hle,superchem}` 和 `data/temp-benchmarks/*` 做内容/大小/SHA-256 清单。
5. 明确保留 `state/benchmark-runs/` 下的 VGB results、VGB archive evidence、dashboard metadata 和 legacy-workspace evidence，除非有单独批准。

### Phase 1：锁定 frozen surface

1. 把 `benchmarking/service/chemdebate/LEGACY.md` 和 `GLOBAL_DEV_SPEC.md` 中的 frozen contract 转成保全测试：显式 entrypoint 可导入，三个 skill path 存在，persisted IDs 不变，dashboard/analysis 只读 fixture 可读取。
2. 记录 frozen service 对 shared dataset loader、evaluator、judge、bundle、orchestration 和 config 的 import graph。
3. 保持 `runner_kind == "chemqa"` 只由显式 legacy service 选择；默认 experiment catalog 继续不暴露 ChemQA。
4. 后续每个清理提交先运行 frozen contract tests，再运行 VGB tests 和 full suite。

### Phase 2：收敛默认 VGB runtime

1. 默认 service 从 `release.json` 派生允许的四个 VGB dataset，并对非 VGB dataset/eval kind fail closed；显式 legacy service 继续使用 shared loader。
2. 把 subset sampling 等旧参数移到 frozen service-owned argument path；默认 VGB CLI 不再显示或处理这些参数。
3. 将 evaluator 注册改为 service-scoped：默认 service 只注册 `verifier_grounded`，frozen service 显式注册旧 evaluator 和 generic fallback；保留 evaluator 源文件。
4. 默认 VGB CLI 不构建 `JudgeClient`、judge config 或 judge workspace；frozen service 的 judge path、模板和 tests 保持不变。
5. 默认 `service.single` 的 prompt、rescue 和 answer-contract 分支只保留 VGB；ChemQA 自有 prompt/driver 与 shared frozen helpers 不改。
6. 默认 VGB adapter 固定使用无 dataset input bundle 的路径；保留 `RuntimePathProjection` 以及 frozen service 使用的 SUPERChem/HLE materialization。
7. 保留 dashboard/analysis 对 ChemQA 和历史 result schema 的只读支持，同时确保新默认 run 只能写入 VGB records。
8. 建立 release inventory 与 canonical output mapping 的一致性测试，确保四个 track/dataset 与默认目录映射不会独立漂移。

### Phase 3：删除旧输入数据

仅在 Phase 0 清单确认且用户单独批准后执行：

1. 删除 `/Users/xutao/.openclaw/data/formal-benchmarks/chembench`、`frontierscience`、`hle`、`superchem`。
2. 删除 `/Users/xutao/.openclaw/data/temp-benchmarks/` 下对应旧数据目录。
3. 删除前明确选择：若仍需在本机通过默认 benchmark root 重放 frozen ChemQA，则不删除或先独立归档这些输入；否则 frozen code 仍可通过显式 `--files` 使用外部输入。
4. 检查 `agents/`、`benchmark/`、`logs/`、`flows/`、`tasks/` 和 dashboard DB 是否仍包含未完成旧 benchmark 引用；本次不删除 ChemQA/DebateClaw generated state 或历史结果。
5. 不删除 VGB release cache、VGB runtime、VGB resources、VGB run records、VGB skills-on skill tree、frozen service 或其 skill bundles。

### Phase 4：文档与当前规范收口

1. 更新 `GLOBAL_DEV_SPEC.md`，把默认 VGB runtime 与 frozen ChemQA surface 的依赖和支持级别分开描述。
2. 保留历史设计、计划、handoff 和 report；必要时增加 supersession 说明，不改写历史事实。
3. 保持 distribution name `chemqa` 和 extras 名称不变；它们由 frozen contract 明确保留，不属于本次清理候选。

## 6. 验收矩阵

删除会话完成前必须同时满足：

| 验收项 | 目标 |
| --- | --- |
| active import scan | 默认 VGB entrypoint 的 import closure 不 eager-load `chemdebate` 或旧 evaluator；frozen entrypoint 仍可加载其 shared dependencies |
| dataset contract | 默认 service 只接受 `release.json` 声明的四个 VGB dataset，所有记录 `eval_kind=verifier_grounded`，track/task/release identity 校验仍有效；frozen service 的 shared loader contract 保留 |
| output layout | 四个单数据集和 mixed VGB 运行都进入 canonical formal/temporary 目录；不再生成旧 dataset slug 目录 |
| scoring | 四个 VGB track 均可完成 isolated verifier scoring；worker/cache opt-in 测试仍通过 |
| single-LLM | skills-on/off、Docker 默认 backend、host fallback、timeout/retry、session isolation、cancellation、workspace audit 仍通过 |
| skill exposure | 85 个 matrix skills 仍可生成 routing inventory；三个 frozen orchestration bundles 保留但继续不暴露给 VGB single agent |
| frozen surface | explicit ChemQA entrypoint、三个依赖 skill path、persisted IDs、shared evaluator/judge/bundle 和历史只读 fixtures 均保持可用 |
| dashboard | VGB run discovery、property advanced/basic track facets、record detail 和 progress reconciliation 正常；ChemQA/历史 schema 只读支持不回归 |
| no active legacy data | formal/temp benchmark input root 中不再默认发现旧数据集；默认 VGB CLI 不暴露旧 dataset/subset 参数 |
| tests | VGB、runtime/infrastructure、frozen contract 和全量测试均通过；不得通过 skip 或删除 frozen tests 获得绿色结果 |
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

- `JudgeClient`、旧 evaluator、generic fallback 和 multimodal bundle 当前是 frozen service 的 shared dependencies，不能因默认 VGB 不使用而全局删除；正确动作是 service-scoped registration/composition。
- `runtime/bundles.py` 同时承担 container path projection 和 frozen multimodal input materialization；默认 VGB 可绕开后者，但模块和 frozen branches 保留。
- `benchmarking/core/datasets.py` 的 `BenchmarkRecord`、normalization 和 subset helpers 被 active/frozen 两条路径共享；默认 VGB allowlist 应放在 service 边界，不应破坏 shared loader。
- `benchmarking/analysis/automated.py` 和 dashboard 的 ChemQA/历史只读解析由 frozen contract 明确保留。
- `state/benchmark-runs/`、ChemQA/DebateClaw generated state、agents/session/logs 和 dashboard DB 不属于本次删除范围。
- 历史 docs 保留是当前文档治理规则，不代表旧设施仍受支持；可以在清理完成后增加 supersession 说明，但不要静默改写历史报告。

本报告完成后，下一会话应先等待用户对 Phase 1–4 的修订范围是否批准。未明确批准的旧输入数据不得删除；ChemQA/DebateClaw frozen source、skills、tests、persisted identifiers、历史只读支持和 distribution name 均视为禁止删除。
