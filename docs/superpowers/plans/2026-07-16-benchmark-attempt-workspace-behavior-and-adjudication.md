# Benchmark Attempt Workspace Behavior and Adjudication Implementation Plan

状态：`DONE`

日期：2026-07-16

分支：`feat/benchmark-attempt-workspace-adjudication`

规格依据：
`docs/superpowers/specs/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md`

## 1. 完成定义

本计划只实现规格定义的 workspace behavior、runtime prevention、audit evidence、adjudication、
reporting 和 deterministic history recovery，不修改 benchmark prompt 的解题内容、answer schema、
evaluator、verifier wheel 或评分公式。

完成时必须同时满足：

1. `WorkspaceAccessPolicy` 是 guard、auditor、manifest、prompt suffix 和最终 `AGENTS.md` 的唯一权限来源。
2. 所有 active workspace 使用稳定的 `scratch/{requests,outputs,notes,tmp}` 布局，structured file tools
   使用 `scratch/...` 相对路径。
3. finding 关联 tool call/result，并包含 access mode、operation outcome、policy 和 bounded evidence。
4. audit execution、boundary、contamination 和 adjudication 是四个独立状态轴。
5. boundary-only violation 保留完整答案并评分；confirmed/indeterminate contamination 仍阻止评分。
6. skills-off 和 judge policy 不暴露 skills root 或 `run_skill.py`；ChemQA 只开放当前 run 的显式交换面。
7. schema v3、aggregate、dashboard 和 automated analysis 一致呈现新语义，legacy v2 仍可读。
8. audit unavailable 在最终 non-evaluable 前至少完成一次 deterministic transcript/archive recovery。
9. history replay 默认 dry-run，apply 使用 snapshot/hash/atomic rewrite，且不调用模型。
10. 规格要求的 targeted/full tests 全部通过，文档更新，分支提交存在且 worktree clean。
11. 指定 xTB run 的两条记录完成 dry-run 和人工证据复核；只有满足规格证据门槛才 apply/re-score。

## 2. 实施顺序

### Phase 1: Tests and policy model

- 增加 adjudication matrix、xTB typo、property heredoc、tool outcome/access mode fixtures。
- 引入不可变的 `AccessScope`、`WorkspaceAccessPolicy`、稳定 normalization/serialization/digest。
- 为 single-LLM on/off、judge、ChemQA 构造显式 role policy，并验证 skills-off/judge 最小权限。
- 验证：policy digest 与输入顺序无关；exact file scope 不扩大为目录；allowed-first containment 保留。

### Phase 2: Workspace contract and guard

- 将 scratch 迁移为 `scratch/{requests,outputs,notes,tmp}`，manifest 写入 `scratch_contract_version=2`。
- 提取 canonical `AGENTS.base.md`，materialization 时合并 role overlay；修订 `TOOLS.md` 机械配方。
- 将 workdir-only plugin 扩展为 policy-driven structured tool preflight guard，输出稳定 block code/evidence。
- 验证：relative resolution、read/write/edit/move/delete、exec cwd、symlink escape、unknown tool diagnostics。

### Phase 3: Evidence and adjudication

- transcript parser 保留 tool call id、assistant line、tool result、error/exit/block evidence。
- 使用显式 semantics registry 生成 read/list/search/execute/write/mutate/workdir/unknown finding。
- 分离 path extraction、boundary evidence、information exposure 和最终 adjudication。
- 实现 out-of-scope owned-write cleanup；无法证明 ownership 的路径禁止递归删除。
- 验证：规格 14.2 矩阵逐行测试，blocked/failed 不伪造 succeeded，write-only 不伪造 contamination。

### Phase 4: Runner and result integration

- single-LLM、ChemQA、judge 和 web preflight 消费 role policy 与 adjudication。
- `scoreable_degraded` 保留原 answer/candidate contract/evaluator input；`non_evaluable` 保留 forensic answer。
- 升级 per-record/results/status axes 为 schema v3，并增加 legacy v2 reader adapter。
- 聚合独立统计 boundary warning/violation、contamination、unavailable 和 cleanup failure。
- dashboard/detail/analysis 文案区分“已评分但违规”和“因污染不可评测”。

### Phase 5: Audit and history recovery

- audit unavailable 按 session transcript、archive exact reference、current parser 的顺序恢复一次。
- 新增 dry-run-first replay/re-adjudication CLI/module，复用 evaluator registry。
- apply 前原子 snapshot，记录 source hashes、git commit、policy digest、scorer identity 和 model_calls=0。
- 仅覆盖显式选择的 records，并原子重建 per-record、results、progress 和 recovery report。

### Phase 6: Verification, docs, commit, designated recovery

- 运行规格 19.4 的四组命令及 dashboard/frontend tests。
- 运行 `git diff --check`，更新 `GLOBAL_DEV_SPEC.md`、本规格和本计划状态。
- 测试全部通过后提交分支。
- 对指定 xTB records 先 dry-run，再人工检查 provenance、后续读取和 cleanup 证据；满足门槛才 apply。
- apply 后核验 snapshot、hash、aggregate、official pinned verifier identity 和未调用模型证明。

## 3. 关键失败语义

- policy、prepare、archive 或 recovery infrastructure 失败继续 fail closed。
- `audit_execution_status=unavailable` 在 recovery 前不是最终裁决；恢复失败后才映射 non-evaluable。
- boundary-only diagnostics 不进入 top-level execution errors，不清空答案，也不伪造 evaluator score 0。
- confirmed/indeterminate contamination 不调用 evaluator，但保留原始答案用于 forensic review。
- cleanup failure 是 operational diagnostic，不反向改变 information contamination 事实。
- historical evidence 无法证明 current-attempt ownership 时保持 unknown，不为恢复分数降低证据标准。

## 4. 验证命令

```bash
uv run pytest -q tests/test_agent_workspace.py
uv run pytest -q tests/test_benchmark_workdir_guard.py
uv run pytest -q tests/test_benchmarking_cli.py tests/test_benchmark_test.py
uv run pytest -q tests/test_single_llm_timeout_retry.py
uv run pytest -q tests/test_benchmark_dashboard.py tests/test_benchmark_dashboard_app.py
uv run pytest -q tests/test_automated_evaluation.py
uv run pytest -q
```

所有测试通过前不得把规格或计划标记为 `DONE`，不得提交实现，也不得 apply 历史结果恢复。

## 5. 完成记录

- 代码与文档实现在分支 `feat/benchmark-attempt-workspace-adjudication` 完成；
- 最终全量回归：`708 passed, 99 subtests passed`；
- 指定历史恢复 apply 报告：
  `state/benchmark-runs/verifier-grounded-xtb-qwen3.7-max-20260716-114656/recovery/workspace-adjudication-replay-20260716T095651Z-f081fd8d.json`；
- 原始状态快照：
  `state/benchmark-runs/verifier-grounded-xtb-qwen3.7-max-20260716-114656/recovery/workspace-adjudication-snapshot-20260716T095651Z`；
- `xtb_formula_dipole_min_014` 与 `xtb_c10_f2_gap_min_016` 分别通过官方
  `isolated_wheel_api` 得分 `0.8989499999999999` 和 `0.8707013143352`；
- apply 记录 `model_calls=0`、source hashes、git commit、policy digests 和 scorer identities。
