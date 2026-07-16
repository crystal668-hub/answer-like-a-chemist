# Benchmark Attempt Workspace Behavior and Contamination Adjudication Specification

状态：`DONE`

日期：2026-07-16

适用项目：OpenClaw benchmark attempt workspace behavior contract、runtime boundary guard、
transcript audit、result evaluability adjudication 和历史结果恢复

## 1. 摘要

当前 benchmark attempt workspace 已具备独立 workspace、scratch、显式 protected roots、
transcript 路径审计和 fail-closed 结果处理，但其行为引导和最终裁决仍存在两个方向相反的问题：

1. 对诚实 agent 的普通路径错误处罚过重。任何路径 finding、任何 `workdir_fallback`，以及任何
   audit unavailable 都会使整题不可评测，不区分读取、写入、执行结果或是否获得外部信息；
2. 现有静态 transcript auditor 不是真正的 filesystem sandbox。它无法可靠观察脚本内部访问、
   动态路径构造和 syscall 结果，因此不能独立承担强隔离职责。

本规格将 workspace boundary 的三类事实拆开：

- **信息污染（information contamination）**：agent 成功获得了当前 attempt 未公开的外部信息；
- **边界违规（boundary violation）**：agent 请求或完成了允许 scope 外的操作，但没有证据表明
  答案吸收了外部信息；
- **审计不可用（audit unavailable）**：系统无法建立足够证据判断前两者。

只有已确认的信息污染，或在恢复后仍无法排除信息污染的 indeterminate 状态，才使答案
`non_evaluable`。被阻断/失败的越界尝试、纯写出 workspace、回退到 allowed scope 等事件必须
保留为结构化诊断，但不得自动吞掉完整答案。

本规格同时建立一套规范化的 agent workspace 行为契约：所有角色共享同一基础 `AGENTS.md`
规则，角色只增加最小权限覆盖；所有非 shell 文件工具使用稳定的 workspace-relative `scratch/`
路径，`exec` 使用 runner 注入的环境变量，禁止 agent 复制或手工改写包含 run/invocation/session
标识的长绝对路径。

## 2. 已确认的设计决策

本规格记录以下已经审核同意的决定，实施会话不应重新打开这些产品决策：

1. 保留现有显式 `ProtectedRoot` 和 allowed-first containment 作为路径证据层；
2. 不再使用 `audit.status != clean -> 整题失败` 的二元裁决；
3. 读取外部信息与写出 workspace 必须分开判定；
4. 工具调用是否成功、失败或被 guard 阻断必须参与裁决；
5. 纯 write-only 边界违规不得自动使答案不可评测；
6. `workdir_fallback` 回退到 allowed scope 时只作为 warning；
7. audit unavailable 继续 fail closed，但必须先执行确定性的重审/恢复；
8. skills-on、skills-off、judge 和 ChemQA 使用同一基础行为契约，只通过显式 role overlay 改变权限；
9. skills-off 的 audit/guard allowed scopes 不得包含 skills root 或 `run_skill.py`；
10. active workspace 内提供稳定相对 `scratch/` 布局，取代 agent 对深层绝对 scratch 路径的复制；
11. `AGENTS.md` 只描述隔离和文件操作契约，不提供解题策略、领域 SOP 或工具选择建议；
12. agent instructions 是防误操作层，不是安全边界；运行时 guard 和审计仍需独立执行；
13. 本规格实施后，应在不重新调用模型的前提下重审并恢复符合新裁决规则的历史结果。

## 3. 与现有规格的关系

### 3.1 保留的现有契约

`2026-07-16-benchmark-forbidden-path-root-containment-spec.md` 中以下契约继续有效：

- `ProtectedRoot` 必须显式注入，不从名称猜测资源身份；
- candidate、allowed root 和 protected root 使用真实路径 containment；
- allowed scope 优先于 protected root；
- 环境变量、`~`、relative traversal、existing symlink prefix 和 `cd` 执行上下文按现有规则解析；
- finding 必须记录 `policy_id`、`resolved_path`、`matched_root` 和 `candidate_source`；
- 普通文本中的 `gold`、`verifier-grounded`、`task.yaml` 等名称没有安全语义；
- parser failure 不得伪造 `forbidden_path`。

### 3.2 被本规格替换的行为

以下当前行为由本规格取代：

- `ContaminationAudit.status` 同时表达“审计是否完成”“是否越界”“是否污染”的设计；
- 任意 finding 都直接将 runner result 替换为 workspace failure；
- 任意 `workdir_fallback` 都标记 contaminated；
- single-LLM 的 allowed skill roots 不区分 skills-on/off；
- agent 需要复制 `.benchmark-scratch/<record>/<session>/...` 长绝对路径；
- `AGENTS.md`、`TOOLS.md` 和动态 scratch prompt 分别维护可能冲突的文件操作规则。

### 3.3 兼容边界

- 新 run 的 per-record/result schema 必须升级版本，不得把新语义塞进旧字段而不改 schema；
- 历史 schema 必须可读，但新 writer 不继续生成旧二元语义；
- evaluator 输入、verifier-grounded wheel、任务 prompt 和评分公式不属于本规格修改范围；
- 当前 root-containment parser 的 heredoc 和 simple-`cd` 修复不得回退。

## 4. 背景证据

### 4.1 Property calculation parser false positive

`property_calc_free_energy_001 / single_llm_skills_off` 曾因合法 heredoc 中的 Python 三引号触发
`ValueError`，audit unavailable 后答案被判不可评测。修复 parser 并重放同一 transcript 后，
audit 为 clean，官方 verifier 可正常评分。这说明 audit infrastructure failure 不能与答案质量混合。

### 4.2 xTB write-only boundary violations

重新运行的 xTB skills-on 中，以下两个 record 各因一次路径拼写错误而不可评测：

- `xtb_formula_dipole_min_014`；
- `xtb_c10_f2_gap_min_016`。

agent 将当前 run slug 中的 `xtb` 手误写成 `xtx`，`write` 工具成功把 agent 自己生成的脚本写到
一个新建 sibling runtime tree。transcript 随后明确识别该 typo，并回到正确 scratch 继续执行。
finding 中没有外部 read、list、execute、workdir fallback 或 audit unavailable 证据。

该事件确实违反 write boundary，并在 filesystem 上产生需要清理的旁路文件，但没有证据证明
答案受到历史记录、其他 agent 或隐藏 verifier 信息影响。按本规格应判为：

```text
boundary_status = violated
contamination_status = clear
adjudication = scoreable_degraded
```

## 5. 目标

### 5.1 功能目标

- 用独立字段表示 audit execution、boundary compliance、information contamination 和 evaluability；
- 对 tool call 与对应 tool result 建立关联，记录 succeeded/failed/blocked/unknown；
- 区分 read/list/execute/write/workdir/unknown access mode；
- 只有外部信息暴露或无法排除暴露时阻止评分；
- 对 write-only、blocked、failed 和 allowed fallback 保留答案并记录 degraded/diagnostic；
- 为所有 agent workspace 提供统一、简短、无解题偏置的行为规范；
- 使用 stable relative scratch paths 消除长绝对路径复制错误；
- 从同一机器可读 policy 生成 guard 配置、audit allowed scopes、runtime manifest 和 agent contract；
- 对 skills-off 移除 skill filesystem 权限；
- 支持对历史 transcript 进行无模型重放、重新裁决和重新评分。

### 5.2 公平性目标

- 不因 tools 使用较多、trajectory 较长而放大与答案质量无关的 invalidation 风险；
- skills-on/off 除实验变量外使用同一 isolation wording 和相同 adjudication；
- 被 guard 阻断的操作不会因“尝试过”而自动判定模型答案无效；
- confirmed contamination 仍严格 fail closed，不允许被 degraded 标记稀释；
- 评分统计区分 answer quality、runtime stability、boundary compliance 和 contamination。

### 5.3 可运维目标

- 每个裁决可从结构化 finding 和 tool outcome 复核；
- runtime manifest 序列化实际 policy，而不是只记录 protected roots；
- history recovery 必须有 snapshot、hash、replay report 和 scorer identity；
- dashboard/analysis 能明确显示“已评分但边界违规”与“因污染不可评测”；
- 新 policy 不依赖当前 run/track 名称或本机目录 basename。

## 6. 非目标

- 本规格不实现完整 POSIX shell parser；
- 本规格不把 transcript audit 描述为 syscall-level security boundary；
- 本规格不自动推断任意脚本内部所有文件访问；
- 本规格不改变 benchmark 答题 prompt、answer schema 或 verifier 评分标准；
- 本规格不允许 agent 因 instructions 声称“没有读取”而覆盖机器证据；
- 本规格不把所有 tool error 都视为 boundary violation；
- 本规格不允许为了降低误报而开放整个 project root、state root、data root 或 output root；
- OS sandbox/container/separate-user 是后续强对抗隔离方向，不是本轮实现的前置条件，但必须继续
  在文档和 runtime manifest 中声明当前边界并非 OS sandbox。

## 7. 威胁模型与术语

### 7.1 目标威胁

本规格主要防止：

- 当前 agent 读取其他 attempt workspace 的文件；
- 当前 agent 查看历史 answer、results、transcript 或 agent state；
- 当前 agent 读取 raw dataset、hidden verifier/scorer resources 或其他未公开 record；
- 当前 agent 因 workdir/path 错误意外落入其他 workspace，并将其内容作为证据；
- 当前 agent 污染其他 run/workspace 的 filesystem state。

### 7.2 Information contamination

满足以下任一条件时为 confirmed information contamination：

- 成功的 read/list/search 操作返回了 protected、pre-existing external resource 的内容或目录信息；
- 成功执行/导入了 protected、pre-existing external executable/script/module；
- 成功的 unknown-semantics 命令引用 protected external path，且现有证据不能排除 read/execution；
- workdir fallback 到 allowed scope 外，命令成功或 outcome unknown，且不能排除外部输入；
- 其他结构化证据明确表明 agent 获得了当前 prompt/input bundle/allowed skill scopes 之外的信息。

### 7.3 Boundary violation

以下事件属于 boundary violation，但不自动等于 information contamination：

- write/edit/create/delete/move/copy target 位于 write scope 外；
- read/list/execute 请求越界，但被 guard 阻断或 tool result 明确失败且没有返回外部内容；
- agent 写入自己本 attempt 新建的 sibling path，且没有后续外部 read；
- explicit workdir 无效并被 guard 阻断；
- fallback 到 allowed scope，但执行上下文与请求不一致；
- 其他违反 workspace contract、但没有外部信息暴露证据的操作。

### 7.4 Audit unavailable / indeterminate

- `audit_unavailable` 表示 transcript 缺失、损坏、不可读或 parser 无法完成；
- `contamination_indeterminate` 表示 audit 已完成，但一个成功/unknown 的访问无法确定是否暴露信息；
- 两者都不得伪装为 confirmed contamination；
- 两者在确定性恢复后仍无法解决时保持 non-evaluable。

### 7.5 Current-attempt-owned resource

只有 runtime guard/event recorder 能证明以下全部事实时，路径才可标记
`current_attempt_owned`：

- 第一次相关事件前 exact path 不存在；
- 首个成功事件是当前 attempt 的 create/write；
- 该路径没有来自其他 invocation 的 sentinel/ownership；
- 当前 attempt 在任何 read/list/execute 前已记录 ownership；
- provenance evidence 写入 finding。

历史 replay 无法证明这些条件时必须使用 `unknown`，除非 recovery report 提供独立文件时间、
transcript 顺序、invocation path 和内容来源证据并接受人工审核。

## 8. 核心不变量

### BAWA-01：信息污染与边界违规分离

`boundary_status=violated` 不得单独推出 `evaluable=false`。

### BAWA-02：结果感知

所有用于裁决的 tool finding 必须记录 `tool_call_id` 和
`operation_outcome in {succeeded, failed, blocked, unknown}`。缺少 tool result 时不得默认为成功。

### BAWA-03：读写方向感知

所有 finding 必须记录
`access_mode in {read, list, search, execute, write, mutate, workdir, unknown}`。
`write/mutate` 不得仅因目标位于 protected root 就推导信息暴露。

### BAWA-04：Unknown 保守但不伪造

成功或 outcome unknown 的 protected-path `access_mode=unknown` 可以判为
`contamination_indeterminate`，但不得标记 confirmed。失败或被阻断且无外部输出的 unknown access
只能是 boundary violation。

### BAWA-05：Allowed fallback 不污染

`workdir_fallback` 的 fallback path 若位于 allowed execution scope，必须是 warning/diagnostic；
只有 fallback 到 allowed scope 外且可能执行时才影响 evaluability。

### BAWA-06：稳定相对 scratch

agent-facing non-shell file paths 必须使用 `scratch/...` 相对路径。agent instructions 不得要求复制
包含 run、invocation、record 或 session slug 的 benchmark runtime absolute path。

### BAWA-07：单一 policy 来源

guard、auditor、runtime manifest、prompt suffix 和 `AGENTS.md` role overlay 必须从同一个
`WorkspaceAccessPolicy` 构建。不得在各模块独立硬编码 allowed roots。

### BAWA-08：Group 权限精确

skills-off 和 judge policy 不得包含 skills root；skills-on 只允许 health-filtered skills 所需的
canonical read scope 和 exact wrapper entrypoint。

### BAWA-09：Instructions 不构成证据

agent 是否遵守或声明遵守 `AGENTS.md` 不参与污染裁决。裁决只使用 runtime/transcript evidence。

### BAWA-10：完整答案优先保留

boundary-only violation 必须保留原始 complete answer、candidate contract 和 evaluator input。
不得用 workspace failure result 覆盖这些字段。

### BAWA-11：审计不可用先恢复

audit unavailable 必须先尝试读取 archive/session transcript 重新审计；只有恢复仍失败时才
non-evaluable。不得自动把 unavailable 降级为 clean。

### BAWA-12：评分与合规分别聚合

aggregate score 只由 `scored=true` 记录计算；boundary violation、contamination 和 audit unavailable
使用独立计数，不复用 `pass_count` 或 `run_failed_count` 表达。

## 9. WorkspaceAccessPolicy

### 9.1 数据模型

引入不可变、可序列化的 `WorkspaceAccessPolicy`，至少包含：

```python
@dataclass(frozen=True)
class AccessScope:
    scope_id: str
    path: Path
    kind: Literal["directory", "file"]
    source: str


@dataclass(frozen=True)
class WorkspaceAccessPolicy:
    schema_version: int
    role: str
    skills_enabled: bool
    read_scopes: tuple[AccessScope, ...]
    write_scopes: tuple[AccessScope, ...]
    exec_workdir_scopes: tuple[AccessScope, ...]
    protected_roots: tuple[ProtectedRoot, ...]
```

约束：

- 所有 scope 在构造时做与 `ProtectedRoot` 相同的 normalization 和 validation；
- read/write/exec scopes 不得由 transcript 或 agent 参数提供；
- exact file scope 只允许 exact resolved file，不允许其伪造子路径；
- policy 写入 runtime manifest，并在每条 per-record 记录 policy digest；
- policy digest 必须稳定，不包含无关顺序；
- path overlap 合法，但 write scope 必须比 read scope更窄。

### 9.2 Single-LLM skills-on policy

Read scopes：

- 当前 active workspace；
- 当前 record input bundle；
- health-filtered exposed skills 所需的 canonical skill scope；
- exact `scripts/run_skill.py`。

Write scopes：

- 当前 active workspace 下的 `scratch/`。

Exec workdir scopes：

- 当前 active workspace；
- 当前 `scratch/` 及其子目录。

### 9.3 Single-LLM skills-off policy

Read scopes：

- 当前 active workspace；
- 当前 record input bundle。

Write/exec scopes 与 skills-on 相同，但不得包含 skills root、skill docs 或 `run_skill.py`。

### 9.4 Judge policy

- Judge 只能使用调用 prompt 中提供的材料；
- filesystem read scope 只保留当前 judge workspace 的 benchmark-managed files；
- 默认不提供 input bundle、skills root 或外部 artifact scope；
- write scope 为空或仅包含一个明确的 judge scratch（若 runtime 确实需要）；
- judge 不得使用 filesystem 内容补充裁决证据。

### 9.5 ChemQA role policy

- 每个角色可读写自己的 active workspace/scratch；
- 角色间交换只通过当前 ChemQA run 的显式 protocol/artifact scopes；
- 每个 exchange scope 必须携带 run id 和 role ownership；
- 当前 run 之外的 generated/artifacts/protocol tree 保持 protected；
- role overlay 只说明被分配的交换面，不开放整个 ChemQA source/runtime tree。

## 10. Stable Scratch Contract

### 10.1 目录布局

每个 active workspace 使用以下 agent-facing canonical layout：

```text
scratch/
  requests/
  outputs/
  notes/
  tmp/
```

active workspace 已由 attempt identity 隔离，因此 agent-facing scratch 不再重复 record/session slug。
record、session、attempt 和 archive identity 继续由 sentinel、lease metadata 和 archive path 表达。

### 10.2 环境变量

继续提供：

- `BENCHMARK_WORKSPACE_DIR=<active_workspace>`；
- `BENCHMARK_SKILL_SCRATCH_DIR=<active_workspace>/scratch`；
- `BENCHMARK_SKILL_REQUEST_DIR=<active_workspace>/scratch/requests`；
- `BENCHMARK_SKILL_OUTPUT_DIR=<active_workspace>/scratch/outputs`；
- `BENCHMARK_SKILL_NOTES_DIR=<active_workspace>/scratch/notes`。

这些绝对值用于 shell 环境，不要求 agent 复制到 structured tool path。

### 10.3 Tool path contract

- `exec`：省略 `workdir`，命令以 `cd "$BENCHMARK_SKILL_SCRATCH_DIR" &&` 开始；
- `read/write/edit` 等 structured file tools：只使用 `scratch/...` workspace-relative paths；
- runtime 必须验证所有 structured file tools 确实以 active workspace 作为 relative base；
- 如 OpenClaw 某工具不支持稳定 relative base，实施必须先增加 guard-side normalization，不得退回让
  agent 复制长绝对路径；
- child output directory 在同一 shell command 中 `mkdir -p` 后进入；
- agent 不得手工替换 run slug、invocation id、record slug 或 session id。

### 10.4 迁移

- 新 writer 直接使用 `scratch/`，不创建 `.benchmark-scratch/<record>/<session>` compatibility symlink；
- archive/reporting/tests 改读 lease metadata，不从 scratch pathname 反推 record/session；
- historical archives 保持只读，不迁移目录结构；
- runtime manifest 记录 `scratch_contract_version=2`。

## 11. Agent Behavior Contract

### 11.1 文件组织

建立一个 canonical base contract，例如：

```text
benchmarking/resources/agent-workspace-templates/common/AGENTS.base.md
```

role template 只保存 overlay。workspace materialization 生成最终单一 `AGENTS.md`。若实现选择 build-time
materialization，也必须有测试证明各模板基础条款字节级或结构级一致。

### 11.2 Base contract 必须表达的规则

最终 `AGENTS.md` 应使用简短、直接、非任务特定的英文表达，至少覆盖：

1. 当前 workspace 只属于当前 attempt；
2. 只能把 current prompt、current input bundle、current workspace 和 role policy 暴露的 scopes
   作为证据；
3. 所有生成文件、下载、脚本、结构、输出和笔记写入 `scratch/`；
4. 禁止读取、搜索或列举 parent directories、other workspaces、benchmark results、archives、
   quarantine、agent sessions、raw datasets 和 verifier resources；
5. 禁止通过修改绝对路径、猜测 run id 或搜索 filesystem 定位资源；
6. `exec` 不使用 `workdir`，从 runner 提供的 scratch 环境变量进入；
7. structured file tools 使用 `scratch/...` relative paths；
8. 路径错误后只重新使用当前 relative path/env，不尝试 parent/sibling/similar run name；
9. 被 guard 阻断或工具失败后纠正当前操作并继续，不把它当作任务失败；
10. 外部文件即使可见也不得作为 benchmark evidence；
11. 不运行 Git，不创建 `.git`；
12. 返回 prompt 要求的最终答案格式。

### 11.3 Role overlays

Skills-on overlay：

- 可以读取本次明确暴露的 skill docs；
- skill scripts 只能通过 canonical wrapper 调用；
- request/output/notes 遵循 stable scratch contract；
- 不规定 agent 必须使用任何 skill。

Skills-off overlay：

- skills 和 local skill scripts 对本 attempt 不可用；
- 不读取、发现或调用 canonical skills tree；
- 其余文字与 skills-on base contract 相同。

Judge overlay：

- 只依据 judge call prompt；
- 不探索 filesystem、skills 或外部 artifacts；
- 只输出指定 JSON verdict。

ChemQA overlay：

- 只使用当前 run 分配给该 role 的 protocol/artifact exchange scope；
- 不读取其他 role 的非共享 scratch；
- 不读取其他 ChemQA run。

### 11.4 TOOLS.md 修订

- `TOOLS.md` 只保留机械工具调用配方，不重复 policy rationale；
- 删除“AGENTS 允许 temporary scripts、TOOLS 又全面禁止 temporary runner scripts”的冲突；
- 允许 agent 在 `scratch/tmp` 创建当前计算所需的临时脚本；
- 继续禁止直接执行 canonical `skills/...` source，必须通过 `run_skill.py`；
- 所有示例使用 stable env vars 或 `scratch/...` relative paths；
- 禁止在模板中嵌入某次 run 的绝对 runtime path。

### 11.5 防止 benchmark prompt 污染

- base contract 不包含 chemistry SOP、检查清单、工具推荐或答案格式复述；
- on/off 的 base contract 完全相同，只有 skills permission overlay 不同；
- isolation suffix 在 official task prompt 之后出现时只描述文件边界，不增加解题要求；
- prompt snapshot tests 必须证明 skills-off official VGB prompt 除 isolation contract 外不被改写；
- 行为契约的 token 长度应受控，避免长篇 policy 改变模型推理预算。

## 12. Runtime Prevention

### 12.1 Structured tool preflight guard

扩展 `benchmark-workdir-guard` 或替换为更通用的 `benchmark-workspace-guard`：

- 在 `before_tool_call` 检查已知 structured file tool 的 path-bearing arguments；
- read-like operation 只允许 `read_scopes`；
- write/edit/delete/move target 只允许 `write_scopes`；
- `exec.workdir/cwd` 只允许 `exec_workdir_scopes`；
- blocked result 返回稳定错误码、policy id、resolved candidate 和 correctable guidance；
- guard decision 写入 transcript/tool result，使 auditor 可识别 `operation_outcome=blocked`；
- relative path 必须相对 active workspace resolve；
- symlink escape 在执行前阻断；
- path keys/tool semantics 使用显式 registry，并有 unknown-tool fail-safe/diagnostic policy。

### 12.2 Exec command 边界

现阶段继续使用 transcript parser 识别 exec command 中的 protected paths，但必须承认：

- guard 只验证 workdir 不能阻止 command 内部的任意 filesystem syscall；
- agent 写脚本再执行、command substitution、动态拼接和 skill subprocess 内部访问可能绕过静态审计；
- runtime manifest 和 docs 必须明确当前不是 OS sandbox；
- 需要强对抗保证时，应新增 filesystem sandbox/container/separate-user 项目，而不是继续扩展 ad hoc
  shell parser。

### 12.3 Write cleanup

- 任何成功的 out-of-scope write/mutate 必须记录 exact path；
- 若路径由当前 attempt 新建且安全归属可证明，seal/finalizer 应删除或 quarantine 该 path；
- 不得递归删除无法证明 ownership 的 protected path；
- cleanup failure 记录 operational error，但不改变 information contamination 事实；
- runtime report 记录 cleanup attempted/succeeded/failed。

## 13. Audit Event Model

### 13.1 Tool call/result 关联

transcript parser 必须保留：

- `tool_call_id`；
- tool name；
- arguments；
- assistant message line/event id；
- 对应 tool result；
- `isError`、exit code、block code 和 result status；
- bounded/redacted result evidence。

不得继续只返回 `(tool_name, arguments)` 后丢失 call/result 关系。

### 13.2 Finding schema

每个新 finding 至少包含：

```json
{
  "rule_id": "protected_path_access",
  "tool_call_id": "call_...",
  "tool_name": "write",
  "candidate_source": "write.path",
  "access_mode": "write",
  "operation_outcome": "succeeded",
  "resolved_path": "/resolved/path",
  "matched_root": "/protected/root",
  "policy_id": "benchmark_runtime_root",
  "resource_provenance": "current_attempt_owned",
  "information_exposure": "none",
  "boundary_effect": "violated",
  "evidence": {
    "call_line": 10,
    "result_line": 11,
    "result_is_error": false,
    "exit_code": null
  }
}
```

敏感 result text 只保存 redacted/bounded excerpt；不得把 agent hidden thinking 写入 reporting bundle。

### 13.3 Tool semantics registry

建立显式 registry，而不是按任意工具名称猜测：

- read-like：`read`、本地 image/file input 等；
- write-like：`write`、`edit`、create target；
- mutate-like：delete/move/copy target；
- workdir：exec family 的 `workdir/cwd`；
- exec command：默认 `unknown`，除非现有 deterministic evidence 足以确定；
- unregistered path-bearing tool：标记 `unknown`，不得静默视为 write 或 read。

registry 必须覆盖 OpenClaw 当前实际工具 schema，并通过 fixture 锁定。

### 13.4 Operation outcome

优先级：

1. guard stable block code -> `blocked`；
2. tool result `isError=true` 或明确 non-zero exit -> `failed`；
3. tool result success -> `succeeded`；
4. 缺失或无法解析 result -> `unknown`。

failed/blocked result 如果仍返回 protected content，必须单独评估 information exposure，不能只看
`isError`。

## 14. Adjudication Model

### 14.1 独立状态轴

每条 attempt 输出：

```text
audit_execution_status = complete | unavailable
boundary_status = clean | warning | violated | unknown
contamination_status = clear | confirmed | indeterminate
adjudication = scoreable | scoreable_degraded | non_evaluable
```

推导规则：

- `confirmed -> non_evaluable`；
- `indeterminate -> non_evaluable`；
- `clear + violated -> scoreable_degraded`；
- `clear + warning -> scoreable`，但保留 diagnostic；
- `clear + clean -> scoreable`；
- `audit unavailable` 在 recovery 前不作最终裁决；recovery 后仍 unavailable -> non_evaluable。

### 14.2 判定矩阵

| 事件 | Boundary | Contamination | Adjudication |
| --- | --- | --- | --- |
| 成功读取/list/search pre-existing protected resource | violated | confirmed | non_evaluable |
| 成功执行/import pre-existing protected code/data | violated | confirmed | non_evaluable |
| 成功或 unknown 的 protected exec，无法排除读取 | violated/unknown | indeterminate | non_evaluable |
| 被 guard 阻断的 read/write/exec | violated | clear | scoreable_degraded |
| 明确失败且未返回 protected content 的 read/exec | violated | clear | scoreable_degraded |
| 成功 write-only 到 write scope 外，无后续 external read | violated | clear | scoreable_degraded |
| 成功读回 current-attempt-owned out-of-scope file，provenance 已证明 | violated | clear | scoreable_degraded |
| fallback 到 allowed scope | warning | clear | scoreable |
| fallback 到 scope 外且 command failed/blocked、无外部输出 | violated | clear | scoreable_degraded |
| fallback 到 scope 外且 command success/unknown | violated | indeterminate | non_evaluable |
| transcript/parser unavailable，recovery 成功且 clean | clean | clear | scoreable |
| transcript/parser unavailable，recovery 后仍不可用 | unknown | indeterminate | non_evaluable |

### 14.3 Runner result mapping

- `scoreable`：保留原始 runner status/answer，正常 evaluation；
- `scoreable_degraded`：保留原始 answer，`evaluable=true`、`scored=true`，
  `degraded_execution=true`，附 boundary diagnostics；
- `non_evaluable`：保留原始 answer 作为 forensic evidence，但不得调用 evaluator；
- boundary-only event 不进入 top-level `errors`，进入 structured diagnostics/summary counters；
- contamination/audit unavailable 进入 operational errors，但不得伪造 evaluator score 0；
- complete-answer contract 不因 scoreable boundary violation 被重置。

## 15. Result Schema and Reporting

### 15.1 Schema bump

将 per-record/results schema 从当前版本升级，建议使用 `schema_version=3`。新增：

```json
{
  "workspace_isolation": {
    "policy_digest": "...",
    "audit_execution_status": "complete",
    "boundary_status": "violated",
    "contamination_status": "clear",
    "adjudication": "scoreable_degraded",
    "findings": [],
    "cleanup": {}
  }
}
```

旧 `audit_status` 可由 reader adapter 映射用于历史展示，但新 writer 不把它作为最终裁决来源。

### 15.2 Aggregate counters

至少新增：

- `boundary_warning_count`；
- `boundary_violation_count`；
- `scoreable_degraded_boundary_count`；
- `information_contamination_count`；
- `contamination_indeterminate_count`；
- `audit_unavailable_count`；
- `boundary_cleanup_failed_count`。

现有 `evaluable_count`、`scored_count`、`run_completed_count` 继续保留，但不得从单一 audit status
推导所有值。

### 15.3 Dashboard/analysis

- UI/Markdown 明确区分“有分数但存在边界违规”和“因信息污染不可评测”；
- finding 展示 access mode、outcome、policy、resolved path、result evidence 和 cleanup；
- analysis prompt 不得把 write-only violation描述为 answer cheating；
- 历史 schema 显示 legacy badge，避免把旧 `contaminated` 自动解释为 confirmed information exposure。

## 16. Audit Recovery

### 16.1 自动恢复顺序

audit unavailable 时按以下顺序执行一次确定性恢复：

1. 重新读取 session isolation 指向的 transcript；
2. 若 active transcript 不可用，读取 sealed archive/reference 中记录的 exact transcript；
3. 使用当前 parser 重放；
4. 验证 session id、agent id、record id 和 invocation evidence；
5. 生成 recovery metadata；
6. 仅在仍 unavailable 时最终 non-evaluable。

恢复不得重新调用模型，也不得从 preview/analysis 文本猜测 tool trajectory。

### 16.2 历史结果工具

提供 project-owned replay/re-adjudication 命令或模块，支持：

- 输入 run root、group 和 record id；
- 读取旧 per-record、results、transcript 和 archive；
- 使用新 policy/adjudicator 生成报告；
- 可选调用原 evaluator 重新评分；
- 写入前创建原子 snapshot；
- 记录 source hashes、git commit、policy digest 和 scorer identity；
- 只覆盖明确选择的 records，并重新聚合 progress/results；
- 默认 dry-run，显式 apply 才改历史结果。

### 16.3 本规格实施后的指定恢复

实施完成并通过测试后，对以下 run 做 dry-run 和人工复核：

```text
state/benchmark-runs/verifier-grounded-xtb-qwen3.7-max-20260716-114656
```

目标 records：

- `single_llm_skills_on / xtb_formula_dipole_min_014`；
- `single_llm_skills_on / xtb_c10_f2_gap_min_016`。

若新 replay 证明 finding 均为 write-only、current-attempt-generated、无 external read，则：

- 覆盖为 `scoreable_degraded`；
- 保留 boundary finding 和 cleanup evidence；
- 使用原始完整答案交给 pinned verifier 评分；
- 原子更新 per-record、results、progress 和 recovery report；
- 不重新调用 qwen 模型。

### 16.4 指定恢复完成记录

2026-07-16 已对第 16.3 节两条记录完成 dry-run、人工 transcript 复核和显式
`--approve-historical-ownership` apply。原始状态快照与 apply 报告为：

```text
state/benchmark-runs/verifier-grounded-xtb-qwen3.7-max-20260716-114656/recovery/workspace-adjudication-snapshot-20260716T095651Z
state/benchmark-runs/verifier-grounded-xtb-qwen3.7-max-20260716-114656/recovery/workspace-adjudication-replay-20260716T095651Z-f081fd8d.json
```

恢复结果：

- `xtb_formula_dipole_min_014`：`scoreable_degraded`，官方 `isolated_wheel_api` 分数
  `0.8989499999999999`；
- `xtb_c10_f2_gap_min_016`：`scoreable_degraded`，官方 `isolated_wheel_api` 分数
  `0.8707013143352`；
- 每条记录只保留一个 `write.path` / `operation_outcome=succeeded` finding，
  `contamination_status=clear`；
- apply 报告记录 source hashes、git commit、policy digests、scorer identities 和
  `model_calls=0`，未重新调用 qwen 模型。

## 17. Implementation Boundaries

### 17.1 `benchmarking/runtime/agent_workspace.py`

- 保留 path candidate/root containment；
- 引入 policy、tool event、finding 和 adjudication 类型；
- parser 保留 tool call ids 并关联 results；
- 把 path evidence extraction 与 final adjudication 分离；
- 实现 audit recovery entrypoint；
- stable scratch layout 由 lease/manager 提供。

### 17.2 OpenClaw guard plugin

- 将 workdir-only guard 扩展为 policy-driven structured file tool guard；
- 生成稳定 blocked diagnostics；
- runtime config 注入 per-agent policy scopes/digest；
- 增加 write/read/edit/symlink escape tests。

### 17.3 Runners

- `single_llm.py`、`chemqa.py` 和 judge path 根据 adjudication 决定是否评分；
- scoreable_degraded 不覆盖 answer；
- role/group 构造精确 policy；
- skills-off 移除 skill scopes；
- prompt suffix 改为 stable scratch contract。

### 17.4 Templates

- 提取 common `AGENTS` base；
- 修订 role overlays；
- 修订 skills-on `TOOLS.md`，消除 temporary script 冲突；
- 添加 snapshot/semantic parity tests。

### 17.5 Reporting/dashboard

- schema v3 writer/reader；
- historical v2 adapter；
- aggregate counters；
- dashboard/detail/automated-analysis evidence rendering。

### 17.6 Recovery tooling

- 新增 dry-run-first historical audit replay；
- 复用 evaluator registry，不自行实现 scorer；
- snapshot/hash/atomic overwrite 与 property recovery 的证据标准保持一致。

## 18. Implementation Sequence

按以下顺序实施，避免在裁决层尚未准备好时放松 fail-closed：

1. 增加 failing tests，锁定 adjudication matrix 和 xTB typo regression；
2. 引入 `WorkspaceAccessPolicy` 和 policy manifest/digest；
3. 改 stable scratch layout 和 template/prompt contract；
4. 扩展 preflight guard，先阻止 structured tool 新越界；
5. 增加 tool call/result correlation 和 outcome/access-mode finding；
6. 实现独立 adjudicator；
7. 修改 runners，使 scoreable_degraded 保留答案并评分；
8. 升级 result schema、aggregation、dashboard 和 automated analysis；
9. 实现 unavailable recovery 和 historical replay；
10. 运行完整回归；
11. 更新 `GLOBAL_DEV_SPEC.md` 和本规格状态为 `DONE`；
12. 提交代码；
13. dry-run 并恢复两条指定 xTB 历史记录。

不得先把 write-only finding 简单改成 clean 条件而跳过 policy、evidence 和 reporting 变更。

## 19. Test Requirements

### 19.1 Unit tests

必须覆盖：

- adjudication matrix 每一行；
- tool call/result id 关联；
- succeeded/failed/blocked/unknown；
- read/list/execute/write/mutate/workdir/unknown access mode；
- failed tool result 仍含 protected content；
- write-only out-of-scope 保留答案；
- successful protected read 阻止评分；
- allowed/outside workdir fallback；
- audit unavailable recovery success/failure；
- stable `scratch/` paths；
- structured file tool relative resolution；
- skills-off policy 不含 skills root；
- judge/ChemQA role scopes；
- policy digest determinism；
- legacy schema reader；
- cleanup ownership safety。

### 19.2 Regression fixtures

从实际事件提取脱敏 fixture，不依赖本机 live runtime：

- property heredoc triple-quote parser case；
- xTB `xtb` -> `xtx` successful write + later correction；
- `cd "$BENCHMARK_SKILL_SCRATCH_DIR/outputs/..."` relative path case；
- current workspace nested under protected runtime root；
- write content contains protected path but script execution blind spot；
- command substitution/dynamic path blind spots作为明确 residual-risk tests/docs，不伪装成已解决。

### 19.3 Integration tests

- clean attempt -> scoreable/scored；
- blocked out-of-scope structured write -> scoreable_degraded/scored；
- successful out-of-scope write fixture -> scoreable_degraded/scored + cleanup；
- successful protected read -> non_evaluable/unscored；
- missing transcript recovered from archive -> scoreable；
- unrecoverable transcript -> non_evaluable；
- on/off policy difference仅为 skills permissions；
- aggregate counters 和 dashboard rendering 与 per-record 一致。

### 19.4 Required commands

实施会话至少运行：

```bash
uv run pytest -q tests/test_agent_workspace.py
uv run pytest -q tests/test_benchmarking_cli.py tests/test_benchmark_test.py
uv run pytest -q tests/test_single_llm_timeout_retry.py
uv run pytest -q
```

如 dashboard snapshot 或 frontend tests 存在，也必须运行对应 suite。所有测试通过后再提交。

## 20. Acceptance Criteria

满足以下全部条件才可把本规格标记为 `DONE`：

1. 所有 attempt 最终 workspace 含规范化 base contract 和正确 role overlay；
2. agent 不再需要复制 benchmark runtime 长绝对 scratch 路径；
3. skills-off runtime policy 和 auditor 均不允许 skills root；
4. structured out-of-scope writes 在执行前被 guard 阻断；
5. finding 含 tool call id、access mode、outcome 和 evidence；
6. write-only violation 不再自动覆盖完整答案；
7. confirmed protected read 仍 non-evaluable；
8. allowed workdir fallback 不再标记 information contamination；
9. audit unavailable 至少执行一次确定性恢复；
10. schema v3 明确区分 boundary、contamination、audit execution 和 adjudication；
11. aggregate/dashboard/analysis 一致展示新状态；
12. 所有 required tests 通过；
13. `GLOBAL_DEV_SPEC.md` 已更新为实际实现；
14. git worktree clean，实施提交已创建；
15. 两条指定 xTB record 已 dry-run 重审；符合条件时完成原子恢复和官方重评分；
16. recovery report 能证明未重新调用模型，并记录 pinned scorer identity。

## 21. Residual Risks

即使本规格完成，仍保留以下已知风险：

- 同一 OS 用户下的 exec subprocess 仍可能通过动态脚本访问未 mount 隔离的 filesystem；
- transcript auditor 无法证明 syscall-level 数据流；
- agent 可能编码、拼接或通过 trusted subprocess 间接访问路径；
- tool result schema 变化可能导致 outcome unknown；
- current-attempt ownership 的历史重建可能缺少强证据；
- AGENTS instructions 能降低意外违规，不能约束恶意进程。

这些风险不得通过继续堆叠关键词或脆弱 shell heuristic 来掩盖。若 threat model 提升为主动对抗，
下一阶段应采用只挂载 current workspace、current input bundle 和 role-specific read-only dependencies
的 filesystem sandbox/container/separate-user。

## 22. 下一会话交付清单

实施会话开始时：

1. 阅读 `GLOBAL_DEV_SPEC.md`、本规格和现有 root-containment spec；
2. 以当前代码为 source of truth；
3. 先建立 failing tests 和脱敏 xTB fixtures；
4. 按第 18 节顺序实现，不做单条件兼容补丁；
5. 对任何需要偏离本规格的决定记录原因和替代不变量；
6. 实现后更新本规格状态和 `GLOBAL_DEV_SPEC.md`；
7. 运行第 19.4 节测试并提交；
8. 最后执行第 16.3 节历史恢复，不重新调用模型。

本规格的首要验收问题是：

> 当前 attempt 是否从未公开的外部资源获得了会影响答案的信息？

“agent 是否曾产生一个越界路径字符串”只能作为证据之一，不能再单独决定答案是否可评测。
