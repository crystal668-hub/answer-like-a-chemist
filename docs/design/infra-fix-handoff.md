# Benchmark Infra 重构待修复问题交接文档

状态：`PARTIALLY COMPLETED / REOPENED`。第一轮实现见 `infra-fix-validation.md`；复审发现的六项未关闭问题以 [2026-09-11 后续交接](2026-09-11-benchmark-infra-open-issues-handoff.md) 为准。下文保留原始交接范围。

更新时间：2026-09-11

适用分支：`feat/benchmark-single-llm-containerization`

## 1. 文档目的

本文档用于把 benchmark infra 容器化重构完成后的遗留问题交接给一个没有本次对话上下文的工程师或 agent。内容以当前代码为依据，区分：

- 已被新架构替代、可以直接删除的代码；
- 仍承担兼容或其他 runner 职责、不能直接删除的代码；
- 名义上已经实现，但实际只完成部分接入的模块；
- 修复顺序、接口边界、测试要求与验收标准。

开始修改前必须重新阅读仓库根部 `AGENTS.md` 和 `GLOBAL_DEV_SPEC.md`。代码是最终事实来源。修改代码后必须运行相关测试和全量测试；测试通过后提交到当前 Git 分支。

## 2. 当前系统基线

### 2.1 已完成并验证的能力

- single-LLM CLI 默认使用 Docker backend，host backend 仍可通过 `--execution-backend host` 选择。
- 每次 single-LLM attempt 使用独立 workspace、session root 和临时容器。
- OpenClaw wrapper 和 embedded `--local` agent turn 在容器内执行。
- 容器以 UID/GID 1000、只读 rootfs、`cap_drop=ALL`、`no-new-privileges` 和 tmpfs 运行。
- attempt workspace、session、container logs/manifest/cleanup 会随完整 workspace 一起归档。
- VGB evaluator、固定 wheel 和 hidden verifier runtime 仍留在宿主 orchestrator，不进入 agent 容器。
- skills-on/off 使用相同基础镜像；skills-on 暴露 routing inventory 和 skill source，skills-off 不暴露。
- benchmark startup 已不再使用 skill health check 过滤技能。

### 2.2 最近一次验证

真实 one-attempt run：

```text
record: hle-chem-1
group: single_llm_skills_off
model: openai/gpt-5.6-sol
backend: docker
result: completed, evaluable, scored
score: 1.0
session isolation: ok
workspace audit: complete / clean / clear
archive: ok
container cleanup: removed=true
```

全量测试基线：

```text
833 passed, 5 warnings, 139 subtests passed
```

## 3. 修复范围与约束

### 3.1 必须保持不变

- 不改变 `verifier-grounded-benchmark` 的公开接口、题目、release pin、评分公式或输出内容。
- 不把 VGB hidden material 放入 agent 容器。
- 不容器化 ChemQA 或 judge；它们仍可依赖宿主 subprocess 和 cleanroom。
- 不删除历史 run artifact 的读取兼容性。旧 JSON 中多余字段可以被忽略，不要求新 writer 继续生成。
- 保留 `RunnerResult`、`EvaluationResult` 和 workspace audit 四轴的稳定行为。

### 3.2 本次待修复目标

1. 删除完整的 skill health 旧架构及伪兼容数据链。
2. 修复 Docker VGB attempt 环境与依赖审计的错误边界。
3. 让 admission controller 的能力与实际行为一致。
4. 接入或明确删除未使用的 Docker readiness/orphan recovery 能力。
5. 收敛 group wave 与 attempt queue 的并发职责。
6. 清理相关测试、文档和命名，避免继续表达已废弃语义。

## 4. P1：删除 Skill Health 旧架构

### 4.1 问题定义

生产 CLI 已经直接使用完整 `skill-routing-inventory.json`，不再调用 health check。但旧 health 架构仍以实现模块、参数、结果字段和测试替身的形式贯穿代码。

这不是有效兼容层：dashboard、analysis、scoring 和历史恢复都没有读取 `skill_health_summary` 或 `runtime-manifest.skill_health`。当前代码只是把 routing inventory 伪装成一份“所有技能均健康”的 summary，语义错误且增加维护成本。

### 4.2 可直接删除的内容

| 内容 | 当前作用 | 处理 |
| --- | --- | --- |
| `benchmarking/skills/health.py` | 仅被自己的测试和 CLI 兼容 import 引用 | 删除文件 |
| `tests/test_skill_health.py` | 测试已废弃 health 实现 | 删除文件 |
| CLI 的 `check_all_skill_health` / `summarize_skill_health` imports | 只为旧测试 monkeypatch 保留 | 删除 imports，并更新测试 |
| CLI 局部 `skill_health_summary` | 从 routing inventory 伪造健康状态 | 删除 |
| `results.json.skill_health_summary` | 无生产读取者 | 停止写入 |
| `runtime-manifest.json.skill_health` | 无生产读取者且与 routing inventory 重复 | 停止写入 |
| `build_effective_experiment_specs(..., skill_health_reports=...)` | 当前只复制原 spec | 删除函数，CLI 直接使用 `EXPERIMENT_SPECS` |
| runner/adapter/orchestration 的 `skill_health_summary` 参数 | 纯透传旧字段 | 全部删除 |
| `skill_use_audit.skill_health_summary` | 无下游读取者 | 删除字段和参数 |
| layout 测试中的 `skills/health.py` 存在性要求 | 锁定废弃架构 | 删除断言 |

### 4.3 必须保留的内容

- `benchmarking/skills/audit.py` 本身仍用于 tool/skill 使用诊断。
- `skill_use_audit` 的 tool call、failure、exec 和 skill execution 计数仍被 reporting、dashboard 和 analysis 使用。
- `skill-routing-inventory.json` 及其 deterministic digest 必须保留。
- `benchmark_skill_allowlist()` 和 `benchmark_skill_routing_inventory()` 必须保留。

### 4.4 命名清理

`skill_use_audit` 中的：

```text
available_skill_count
available_skills
```

不再代表 health 检查结果。推荐改为：

```text
configured_skill_count
configured_skills
```

若 dashboard 或历史报告依赖旧名称，可在 reader 侧对旧字段做 fallback；新 writer 不应继续产生错误语义。

### 4.5 验收标准

- `rg "check_all_skill_health|check_skill_health|summarize_skill_health|skill_health_summary" benchmarking tests` 无结果。
- `benchmarking/skills/health.py` 和 `tests/test_skill_health.py` 不存在。
- 新 run 只写 `skill-routing-inventory.json`，不写 `skill-health.json` 或 health summary。
- skills-on 暴露完整 routing inventory；skills-off 使用空技能配置。
- reporting/dashboard/analysis 的 skill tool 统计回归通过。

## 5. P1：修复 Docker VGB Attempt Environment

### 5.1 问题定义

`SingleLLMRunner._run_isolated_attempt()` 当前只要 `record.eval_kind == "verifier_grounded"`，就会在宿主 workspace 创建 `scratch/venv`，并在 attempt 后采集该 venv 的 dependency manifest。

Docker backend 实际执行时却使用：

```text
/opt/benchmark/.venv/bin/python
```

这意味着：

- 宿主创建的 VGB venv 没有被容器使用；
- dependency manifest 记录的不是 agent 实际环境；
- agent 对容器内只读 `/opt/benchmark/.venv` 没有按需安装能力；
- 当前产物可能错误声明 package inventory 和 replay lock；
- skills-on/off 公平性与 attempt-local dependency contract 没有真正实现。

### 5.2 目标设计

按 backend 分开管理 attempt Python：

```text
host backend
  -> 保留 benchmarking.runtime.attempt_environment
  -> scratch/venv + scratch-local uv cache

docker backend
  -> 在挂载 workspace 的 scratch/venv 创建 attempt-local venv
  -> BENCHMARK_ATTEMPT_PYTHON=/benchmark/workspace/scratch/venv/bin/python
  -> UV_CACHE_DIR=/benchmark/workspace/scratch/tmp/cache/uv
  -> OpenClaw 和 run_skill 都使用该 Python
  -> 从实际容器环境采集 dependency manifest
```

Docker image 的 `entrypoint.sh` 已包含 `BENCHMARK_CREATE_ATTEMPT_VENV` 分支，但当前没有调用方。可以接入该机制，也可以把 venv 初始化交给 container command 前置阶段；只能保留一个权威实现。

### 5.3 实现要求

- Docker backend 不得再调用宿主 `create_attempt_environment()`。
- Docker attempt venv 必须位于被归档的 `scratch/venv`。
- PyPI policy 对 skills-on/off 完全一致，默认允许受控 registry 安装。
- 保留现有限制：禁止 direct URL、本地源、editable、alternate index、target override 和 `verifier-grounded-benchmark`。
- install events、freeze、distribution inventory、RECORD hash、replay requirements、PyPI cutoff 必须来自实际容器 venv。
- 在 seal 前清除体积大的 venv/cache，但保留 dependency manifest；行为与现有归档策略一致。
- 非 VGB record 是否也使用 attempt-local venv，应由统一 contract 决定，不应由 skills-on/off 区分。当前已确认两组安装权限必须一致。

### 5.4 不可直接删除的内容

`benchmarking/runtime/attempt_environment.py` 不能整块删除，因为 host backend 仍使用它。只有在正式删除 host backend 后，才能重新判断该模块是否还有其他调用方。

### 5.5 验收标准

- Docker VGB attempt 不创建宿主执行用 venv。
- 容器内 `BENCHMARK_ATTEMPT_PYTHON` 指向 scratch 下的可写 venv。
- 容器中可通过允许的 `uv pip install <registry-package>` 安装包。
- 禁止的安装形式被 guard 阻止。
- dependency manifest 与容器内实际 `uv pip freeze` 一致。
- attempt 归档不保留 venv/cache，但保留可重放依赖证据。
- host backend 原有 VGB 测试继续通过。

## 6. P1：收敛 Admission Controller

### 6.1 问题定义

`AttemptAdmissionController` 定义了 CPU、memory 和 PID 容量，但 orchestration 始终调用：

```python
admission_controller.acquire(ResourceRequest())
```

CLI 初始化又把：

```text
total_memory_bytes = 0
total_pids = 0
```

解释为不限制。因此当前真正生效的主要是 `max_attempts` 和默认 CPU 计数，不是设计文档声称的完整资源感知 admission。

此外：

- `cancel_pending()` 没有生产调用者；
- 当前实现只 `notify_all()`，不会让等待线程因取消退出；
- `capacity_snapshot()` 只被测试调用，没有进入 runtime manifest 或监控。

### 6.2 必须做出的实现决策

推荐选择简化方案，除非确实需要动态资源调度：

#### 方案 A：固定 attempt semaphore（推荐）

- 以 `--max-concurrent-attempts` 作为唯一 attempt admission 约束；
- 删除 `ResourceRequest` 的 CPU/memory/PID 字段；
- 删除 `CapacitySnapshot`、`cancel_pending()` 和虚假的资源感知描述；
- CPU/memory/PID 继续只是每个 Docker container 的硬限制，不参与 admission；
- 使用 cancellation-aware semaphore/condition，取消后不再启动新 attempt。

#### 方案 B：完整资源 admission

- 从 Docker Desktop/daemon 读取真实 CPU、memory 容量；
- 用 CLI container limits 构造每个 attempt 的 `ResourceRequest`；
- 定义未配置 memory/PID limit 时的明确资源权重；
- `capacity_snapshot()` 写入 progress/runtime manifest；
- `cancel_pending()` 设置稳定 cancelled 状态并让等待者抛出 cancellation exception。

不要保留当前“接口看似完整、实际只执行计数限制”的中间状态。

### 6.3 验收标准

- 实现与 runtime manifest 的 admission 描述一致。
- cancellation 时等待中的 attempt 不会继续启动。
- lease 在 container remove 后释放，所有异常路径也释放。
- 并发测试覆盖同组多 record、跨 group、timeout retry 和 cancellation。

## 7. P2：Docker Readiness 与 Orphan Recovery

### 7.1 问题定义

以下方法当前没有生产调用者：

- `DockerContainerRuntime.check_ready()`；
- `DockerContainerRuntime.recover_orphans()`。

它们属于设计文档承诺但未接入的能力，不应当长期作为死代码存在。

### 7.2 推荐处理

推荐接入而不是删除：

```text
CLI startup (Docker backend)
  -> check_ready()
  -> inspect/resolve configured image digest
  -> recover containers matching benchmark ownership labels
  -> persist startup recovery report
  -> begin scheduling
```

Orphan recovery 必须满足：

- 只处理 `benchmark.*` ownership labels 完整匹配的容器；
- 同时验证 run/invocation/attempt identity；
- 有 workspace 时验证 sentinel；
- 无法证明归属的容器必须保留并报告；
- cleanup result 写入 runtime manifest；
- 不得使用宽泛 label 过滤后无条件 `rm -f`。

若产品决定不支持 orphan recovery，应同步删除该方法、对应设计承诺和测试，而不是保留无调用实现。

### 7.3 验收标准

- Docker backend 在调度前 fail-fast 检查 daemon 和 image。
- 缺失 image、daemon unavailable 和 invalid digest 产生明确配置错误。
- identity 匹配的 stale container 可恢复；不匹配容器不被修改。
- startup recovery 报告进入 runtime manifest。

## 8. P2：收敛 Group Wave 与 Attempt Queue

### 8.1 当前状态

Group wave 仍承担 ChemQA 和混合实验组调度，不能整块删除。single-LLM attempt batching 目前仅在显式传入 `--max-concurrent-attempts` 时启用；默认值 `None` 时，同一 group 内 records 仍由 `run_group()` 串行处理。

当前嵌套结构为：

```text
group waves
  -> ThreadPoolExecutor
      -> one-record batch or whole group
          -> admission controller
              -> runner attempt/retries
```

这会让 group concurrency、executor workers 和 attempt admission 三层共同决定吞吐，行为难以推断。

### 8.2 目标边界

- 保留 group wave，专门用于 ChemQA 和 group-level lifecycle/status。
- single-LLM records 应进入一个统一 attempt queue。
- group 只提供配置、progress namespace 和结果聚合，不决定 single-LLM 的串行执行。
- timeout retry 重新申请 admission lease，但保持 record retry 语义和新容器/新 workspace/session。
- 定义 `--max-concurrent-attempts` 的稳定默认值；不能让 `None` 意味着退回串行。

### 8.3 验收标准

- 单个 single-LLM group 的多个 records 可按默认 attempt limit 并行。
- mixed groups 中 ChemQA 仍按原有 wave/cleanroom 语义运行。
- progress events、per-record immediate persistence 和 cancellation 不回退。
- runtime manifest 只描述实际存在的调度层。

## 9. P2：其他可清理项

### 9.1 `render_top_level_skill_tree(available_skills=...)`

参数名来自 health-filtered 时代。当前 runner 仍传 `set(self.configured_skills)`，因此功能仍被使用，不能直接删除函数或过滤能力。建议将参数重命名为 `configured_skills` 或 `exposed_skills`，同步更新 prompt tests。

### 9.2 `benchmarking/skills/__init__.py`

模块 docstring 仍写着 `health`。删除 health 模块后应更新为 inventory、routing、runtime 和 audit helpers。

### 9.3 `container_runtime.py` 未使用 import

`time` 已不再使用，可直接删除。

### 9.4 默认 backend 定义不一致

CLI 默认 `docker`，但以下内部构造函数仍默认 `host`：

- `workflow/orchestration.py`；
- `workflow/runner_adapters.py`；
- `workflow/runners/single_llm.py`。

这对直接调用 API 的测试或外部调用者会产生与 CLI 不一致的行为。需要明确：

- 若 Docker 是 canonical default，则内部默认统一为 `docker`；
- 若内部 API 保持 host 是为了测试，测试应显式传 `host`，不能依赖隐式默认。

### 9.5 历史设计文档

`docs/superpowers/plans/` 和 `docs/superpowers/specs/` 中的旧 health-filtered 描述属于历史决策记录，不建议删除或批量改写。`GLOBAL_DEV_SPEC.md` 只描述当前系统，必须随实现更新；当前设计文档可增加 superseded 注释，但不应伪造历史。

## 10. 明确不能删除的模块

以下代码虽然包含旧式 subprocess、compatibility 或 legacy 逻辑，但当前仍有职责：

| 模块/能力 | 保留原因 |
| --- | --- |
| `benchmarking/runtime/subprocess_utils.py` | ChemQA、judge、cleanroom、web-search preflight 和 analysis 仍使用 |
| `benchmarking/runtime/openclaw_env.py` | host backend、judge 和其他 subprocess 环境仍使用 |
| single-LLM host `_run_attempt()` | `--execution-backend host` 明确保留为回退 |
| `benchmarking/runtime/attempt_environment.py` | host VGB backend 仍使用 |
| group wave artifacts | ChemQA 和混合 group lifecycle 仍使用 |
| cleanroom runtime | ChemQA cleanup 仍使用 |
| legacy workspace archive/replay | 历史 evidence 与恢复工具仍使用 |
| dashboard/analysis 对旧 JSON 的宽松读取 | 保证历史 run 可查看 |

删除这些模块前必须先做独立的产品决策和调用方迁移。

## 11. 推荐实施顺序

### Phase 1：Skill health 删除

1. 删除 health module 和专属测试。
2. 删除 `skill_health_summary` 全链路参数与 artifact 字段。
3. CLI 直接使用 `EXPERIMENT_SPECS`。
4. 重命名 skill audit 中的 `available_*` 字段。
5. 更新 layout、CLI、reporting/dashboard tests 和 `GLOBAL_DEV_SPEC.md`。

### Phase 2：Docker dependency environment

1. 为 Docker attempt 建立实际可写 scratch venv。
2. 切断 Docker 路径上的宿主 VGB venv。
3. 在容器内执行 dependency inventory/remediation。
4. seal 前删除 venv/cache，保留 manifest。
5. 增加允许/禁止安装和 manifest 一致性测试。

### Phase 3：并发模型

1. 选择固定 semaphore 或完整 resource admission。
2. 让 cancellation 能终止 pending admission。
3. 建立 single-LLM attempt queue，保留 ChemQA group waves。
4. 更新 runtime manifest 和并发测试。

### Phase 4：Docker preflight/recovery

1. 接入 daemon/image readiness。
2. 接入 ownership-safe orphan recovery。
3. 写入 startup recovery artifact。
4. 增加 crash/restart 故障注入测试。

### Phase 5：最终收口

1. 清理命名、unused imports、过时 docstrings。
2. 更新 `GLOBAL_DEV_SPEC.md`。
3. 运行定向测试、全量测试和真实 one-attempt Docker run。
4. 核对无残留容器、workspace archive 完整、session/audit 可读。
5. 提交 Git commit。

## 12. 测试清单

至少运行：

```bash
uv run pytest -q tests/test_benchmark_skill_audit.py
uv run pytest -q tests/test_benchmark_skill_tree.py
uv run pytest -q tests/test_benchmarking_cli.py
uv run pytest -q tests/test_benchmarking_orchestration.py
uv run pytest -q tests/test_attempt_environment.py
uv run pytest -q tests/test_attempt_admission.py
uv run pytest -q tests/test_container_runtime.py
uv run pytest -q tests/test_single_llm_timeout_retry.py
uv run pytest -q tests/test_single_llm_session_wrapper.py
uv run pytest -q tests/test_vgb_bridge.py
uv run pytest -q
```

真实 Docker 验收至少覆盖：

- 一个 skills-off 非 VGB record；
- 一个 skills-on record，确认 skill source 只读可见；
- 一个 VGB record，确认 attempt venv、安装 policy、dependency manifest 和宿主 verifier 边界；
- cancellation/timeout，确认容器和 admission lease 无残留；
- 模拟 stale owned container，确认 orphan recovery 不误删不匹配容器。

## 13. 完成定义

只有同时满足以下条件，交接问题才算关闭：

1. skill health 代码、参数、writer 字段和测试已从当前实现中删除。
2. Docker 依赖 manifest 来自实际容器 attempt 环境，不再来自未使用的宿主 venv。
3. skills-on/off 使用相同 image 和 dependency install policy。
4. admission 行为与对外参数、runtime manifest 描述一致。
5. Docker readiness/orphan recovery 已接入，或经明确决策连同承诺一起删除。
6. single-LLM attempt 并发不再依赖隐式 group 串行。
7. host fallback、ChemQA、judge、VGB scorer 和历史 run 读取均无回归。
8. 全量测试通过，真实 Docker 验收通过，工作树干净并已提交。

## 14. 当前证据索引

- 当前开发规范：`GLOBAL_DEV_SPEC.md`
- 原容器化设计：`docs/design/2026-09-08-benchmark-single-llm-containerization-plan.md`
- Docker runner：`benchmarking/workflow/runners/single_llm.py`
- Docker runtime：`benchmarking/runtime/container_runtime.py`
- Admission controller：`benchmarking/runtime/attempt_admission.py`
- Skill routing：`benchmarking/skills/tree.py`
- Skill audit：`benchmarking/skills/audit.py`
- CLI scheduling/artifacts：`benchmarking/workflow/cli.py`
- Host VGB attempt environment：`benchmarking/runtime/attempt_environment.py`
- Docker image：`docker/single-llm/Dockerfile`
