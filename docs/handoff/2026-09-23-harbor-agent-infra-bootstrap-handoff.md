# Harbor Agent Infra 独立仓库初始化与验收交接设计

日期：2026-09-23

范围：新建 `/Users/xutao/harbor-agent-infra/`，作为可复用的 Harbor Agent
实验控制面。
状态：**PROPOSED HANDOFF**。本文是交给新仓库会话的初始化、框架搭建和验收
依据；本文所述功能在新仓库完成前均不得视为已实现。

最后修订：2026-09-24。根据补充设计决策，本版优先复用 Harbor Framework
已有 Job/Trial/Agent/Environment 能力；非原生资源限制留作后续迭代。VGB
数据准备与评分调用迁入新仓库，直接使用官方发布包，替代此前经旧 workspace
中转的方案；既有结果契约继续保留。本次仅修订设计，尚未实施迁移。

## 1. 交接结论

新建一个独立 Git 仓库 `harbor-agent-infra`。它负责实验定义物化、Harbor
Job/Trial 调度入口、镜像引用和分发策略、资源 profile 到 Harbor 原生字段的
映射、Agent Adapter 加载，以及对官方 VGB package 的直接集成。Harbor
Framework 继续负责成熟的 Job/Trial 生命周期、Docker 环境、队列、重试和
cleanup。

官方 `verifier-grounded-benchmark` package 是 VGB Track 数据、公开 prompt、
任务定义和评分器的运行时来源。`harbor-agent-infra` 直接锁定并调用该 package
的公开 API，不通过
`/Users/xutao/.openclaw/workspace/benchmarking/runtime/vgb_bridge.py` 或其他
旧 workspace 中间层。

对于新 Harbor 执行链，现有 `/Users/xutao/.openclaw/workspace` 仅作为历史结果和
dashboard 的兼容读取来源、schema-v5 数据契约及回归基线。新 run 不依赖它提供
题目、评分、Python 环境或结果写入服务。本次设计修订不删除或停用旧仓库
仍在使用的执行能力。

新仓库将 Harbor 执行证据、官方 VGB 原始评分与既有 schema-v5 最终结果关联。
Infra Core 不得 import 现有仓库的 `benchmarking.*` 模块，也不得把
VGB private scoring resources、gold data 或 VGB runtime 复制进 Agent 容器。

第一版只实现当前四个 VGB Track 的 OpenClaw 适配，但接入方式必须遵循 Harbor
已有 Agent contract，以后可以通过 Harbor 的 agent adapter 机制加入 Codex、
Claude Code、Hermes 或其他 Agent CLI。适配器目录名称确定为：

```text
adapters/openclaw/
```

不要使用 `adapters/openclaw_vgb/`。`openclaw` 表示 CLI 运行时适配器，VGB
属于 `integrations/vgb/`，不应成为适配器的类型名称。

本仓库的首个目标是 Docker 本机执行。Harbor Registry 作为可选的镜像分发层
接入；本地 smoke 和单机验收不要求先部署 Harbor Registry。Harbor Framework
与 Harbor Registry 是两个独立产品边界：

```text
Harbor Framework：Job、Trial、Environment、Agent 的实验执行框架
Harbor Registry：OCI 镜像/制品保存、权限、扫描、复制和分发
harbor-agent-infra：实验物化、适配器、镜像策略和跨域证据适配层
```

## 2. 已确认的不可变设计决策

以下决策已由需求方确认，新仓库会话不得自行改写：

1. 只支持当前四个 VGB Track：`open_generation_rdkit`、
   `open_generation_xtb`、`property_calculation_advanced`、
   `property_calculation_basic`。旧 benchmark、ChemQA、非 VGB 数据和历史
   host backend 不属于第一版执行范围。
2. 此前确认的 `RunnerResult` 语义、per-record result、`results.json`、
   schema-v5 observability、归档引用和 dashboard read model 继续作为最终结果
   契约，必须在新仓库独立生成。Harbor trial result 是执行证据，官方 VGB
   evaluation 是评分证据，二者原样保留；它们均不替换最终结果 schema。
   数据契约兼容不要求运行旧仓库的 writer 或 scorer。
3. Harbor 初期只负责 Job/Trial 编排和容器/镜像生命周期。OpenClaw 的
   primary、reminder、timeout retry、finalization rescue 和 session identity
   保持现有语义；VGB prompt/materialization/evaluation 由新仓库的官方 VGB
   integration 负责。
4. 本轮资源限制只采用 Harbor Framework v0.23.0 的 Job/Environment 原生
   配置。当前本机 Docker 第一版实际启用 CPU、memory enforcement 和
   `n_concurrent_trials`；`storage_mb`、GPU、TPU 只有在 Harbor provider
   capability 明确支持时才允许启用。PID、swap、`/dev/shm`、tmpfs、ulimits
   和 storage-driver quota 不在本轮实现。资源值仍必须来自外部 profile，代码
   禁止写死生产默认值，也不能在 profile 缺失时静默回退。
5. 数据准备和评分调用由新仓库的 `integrations/vgb/` 直接对接官方
   `verifier-grounded-benchmark` package。新仓库拥有独立的 release lock、
   VGB 环境及运行产物；不调用旧 workspace 的 CLI、bridge、scorer 或 writer。
   官方 VGB 源码继续在原项目维护，迁移的是调用职责，不是评分算法。
6. 一个容器生命周期只能有一个 owner。Harbor Environment 和旧仓库的
   `DockerContainerRuntime` 不得同时创建、停止或删除同一个容器。
7. Harbor 是 Job/Trial 队列、`n_concurrent_trials`、`n_attempts`、retry 和
   cancellation 的唯一生命周期 owner。Infra 不复制 Harbor scheduler；
   OpenClaw adapter 只负责同一 Trial 内的 primary、reminder、finalization
   rescue 以及把每次 Harbor attempt 映射到新的 OpenClaw session identity。

## 3. 当前参考实现与边界

参考源码位于 `/Users/xutao/harbor-agent-framework/`。它锁定的是 Harbor
0.4.0，可以提供结构和接入样例，但不是新仓库的 Git 历史，也不是旧
benchmark 的替代实现。本文更新时 Harbor Framework 的最新稳定版本是
`v0.23.0`（2026-09-12 发布，release commit
`1e5c5c6db929a10a140d05e606882c671ae20729`）；新仓库应以该版本或经过单独
验证的更新版本为实现基线，不应继续沿用 0.4.0 pin。

重点参考文件：

- `README.md`：Job/Trial → Python adapter → Docker final image → agent CLI
  → verifier/evidence 的基本控制链路；
- `config-templates/generic-prebuilt-job.yaml`：Harbor Job 配置形状；
- `environments/prebuilt_local_docker.py`：预构建镜像、pull policy、Compose
  生命周期和清理策略；
- `runtime-lock.json`：Harbor、agent package、adapter source hash 和运行时
  版本固化方式；
- `src/skillsomething/orchestration/final_image_manifest.py`：镜像 digest、
  platform 和 validation evidence 的绑定方式；
- `src/skillsomething/contracts/metrics.py`、`trial_outcome.py`、`health.py`：
  将执行、任务结果和证据完整性分开记录的方式。

参考实现的资源配置只有 Harbor 0.4.0 原生的 `override_cpus`、
`override_memory_mb`、`override_storage_mb` 和 `override_gpus`。Harbor
`v0.23.0` 已增加 `cpu_enforcement_policy`、`memory_enforcement_policy`，并
支持 `extra_docker_compose`。本轮只使用 Harbor Docker provider 已声明支持的
CPU/memory limit，以及 Harbor Job 原生的 `n_concurrent_trials`；不为 PID、
swap、`/dev/shm`、tmpfs、ulimits 或 storage-driver quota 增加第二套资源实现。
`storage_mb`、GPU、TPU 只有在实际 provider capability 通过 Harbor preflight
时才允许作为以后 profile 扩展，当前本机 Docker profile 不启用它们。

## 4. 目标架构

```text
official VGB wheel（锁定版本和 SHA-256，宿主隔离环境）
  ├─ integrations/vgb/prompts.py -> Track.prompts()
  │    └─ Harbor task / JobConfig
  │         └─ Harbor Job/Trial + DockerEnvironment
  │              └─ adapters/openclaw/ -> candidate answer + artifacts
  └─ integrations/vgb/evaluator.py <- candidate answer + task identity
       └─ Track.evaluate_one() -> vgb-evaluation.json
            └─ result_projection.py
                 └─ 既有 schema-v5 per-record / results.json
                      └─ dashboard 兼容读取（不参与运行）
```

### 4.0 Harbor 复用边界

初始化时先建立下面的复用矩阵。实现只有在右侧的必要缺口存在时才新增代码，
不得把 Harbor 已有实现复制到 `harbor-agent-infra`：

| Harbor v0.23.0 能力 | 本项目直接复用 | Infra 只增加的适配 |
|---|---|---|
| `JobConfig`、YAML config composition | Job 物化、`n_attempts`、retry、并发字段 | 将 VGB experiment/profile 投影为 Harbor config |
| local orchestrator / Trial queue | 排队、worker、取消、重试、cleanup | 记录外部 run 与 Harbor job/trial identity |
| `BaseInstalledAgent`、`AgentContext`、structured capabilities | Agent adapter 生命周期、路径和 artifact contract | `adapters/openclaw/` 的 OpenClaw command/session hook |
| `DockerEnvironment` / prebuilt image | Compose、挂载、CPU/memory policy、日志和环境生命周期 | 只增加 OpenClaw 所需的配置投影与 domain 文件 |
| Harbor trial result / artifacts / trajectory | 执行状态、日志、artifact 和 evidence | VGB result projection，不覆盖 Harbor result |
| 官方 VGB package | Track、prompt、task 定义、`evaluate_one`/`Evaluator` | 只做 Harbor 输入与结果 envelope 的薄映射 |
| Docker image reference / digest | image pull/build/inspect 基础流程 | digest manifest 与跨仓库 image identity |

Infra 不 fork Harbor 的 queue、Docker lifecycle、resource enforcement、result
writer 或 cleanup 实现。Harbor public API/contract 不足时，优先提交最小 adapter
或 extension；只有无法通过公开 hook 接入时，才评估上游贡献或极小 subclass。

### 4.1 Infra Core

Infra Core 只处理 Harbor 之上的薄适配和实验物化：

- 实验配置加载、schema 校验和配置 hash；
- 调用 `integrations/vgb/` 的公开题目导出、release 验证和评分入口；
- 将 integration 返回的 task selection 物化为 Harbor JobConfig；
- Harbor 原生 ResourceProfile 到 Job/Environment 字段的映射；
- image reference/digest/platform manifest；
- Harbor trial 目录之外的最小 infra manifest 和结果投影；
- Agent adapter 版本锁定、能力检查及结果关联。

Harbor Framework 继续负责 Job/Trial 排队、重试、取消、Docker Environment
生命周期、日志和 trial result。Infra 不复制这些机制，也不直接管理容器。
Infra Core 不知道具体模型供应商、VGB 评分公式或 OpenClaw session 数据库表
结构；VGB integration 内部才 import 官方 package，Core 和 OpenClaw adapter
不依赖其内部模块。

### 4.2 Agent Adapter

Agent Adapter 必须优先实现 Harbor 当前版本的 Agent contract，而不是在 Infra
中重新定义一套平行的 `AgentAdapter` 生命周期。第一版应参照 Harbor 的
`BaseInstalledAgent`、`AgentContext`、`EnvironmentPaths`、trajectory/result
类型和 structured capability 声明；只在 OpenClaw 需要时增加薄 wrapper。

Adapter 的自有代码仅负责：

- 将 OpenClaw CLI 参数和模型配置接到 Harbor Agent hook；
- 生成独立 state/session identity；
- 将 Harbor 提供的环境路径投影到 OpenClaw 9.5 配置；
- 导出 OpenClaw trajectory/session evidence；
- 把 Harbor agent result 转换为 VGB integration 可消费的文件。

不要在 Infra 中复制 Harbor 的 trial queue、container lifecycle、exec、log
download 或 cleanup contract。

### 4.3 OpenClaw Adapter

`adapters/openclaw/` 是 OpenClaw CLI 的通用适配器。VGB 只是它的第一个
domain 使用方。它负责：

- 使用 OpenClaw 9.5 的显式 `agentId`、`sessionKey`、`sessionId`；
- 使用 Harbor 为每个 Trial 创建的独立 agent/workspace 路径，并在其中设置
  独立的 `OPENCLAW_STATE_DIR`；
- 生成 OpenClaw 9.5 的 `agents.entries` 配置投影；
- 调用容器内 OpenClaw CLI/wrapper；
- primary、reminder 和 finalization rescue 的 invocation 编排；
- 调用公开的 session inventory 和 `sessions export-trajectory`；
- 导出 SQLite/trajectory/session evidence 的 manifest；
- 将原始 agent 输出写成 VGB integration contract 约定的 `agent-output.v1` 文件；
- 将 OpenClaw state lock、session owner mismatch、trajectory export failure、
  provider failure 和非零退出映射成稳定的 typed failure code。

Adapter 不得：

- 读取或修改宿主机 `sessions.json`、live JSONL 或 SQLite 内部表作为活动执行
  的唯一证据；
- 删除 `main` session row；
- 将 VGB hidden verifier、VGB wheel 或 gold answer 挂进 agent 容器；
- 直接写现有仓库的 `results.json`；
- 把 OpenClaw 特殊字段扩散到 Infra Core 的公共契约。

OpenClaw image 的 Node engine 必须满足当前 9.5 运行要求（至少
`>=24.16.0 <25` 或官方支持的下一个 major），但宿主机不必安装 OpenClaw。
宿主执行通过 Docker image 固定版本和 digest。

### 4.4 官方 VGB Integration

VGB integration 是新仓库的第一方 domain module，不是对旧
`benchmarking.runtime.vgb_bridge` 的兼容调用。建议目录为：

```text
integrations/vgb/
  __init__.py
  release_lock.py
  tracks.py
  prompts.py
  evaluator.py
  result_projection.py
  runtime.py
```

它直接依赖官方 `verifier-grounded-benchmark` package 的公开接口。第一版只
允许四个 canonical tracks：

```text
open_generation_rdkit
open_generation_xtb
property_calculation_advanced
property_calculation_basic
```

官方 package 是 task pack、公开 prompt、Track 定义和 scorer 的唯一来源。新仓库
不要复制 `tasks.yaml`、`verifier_specs.yaml`、`scoring.yaml` 或 workspace 中的
JSONL 快照；只在自己的 `runtime-lock.json`/`configs/domains/vgb.yaml` 保存
release identity、四个 Track allowlist 和预期 task inventory hash。

官方 API 的最小调用面是：

```python
import verifier_grounded_benchmark as vgb

track = vgb.load_track("open_generation_rdkit")
public_prompts = track.prompts()
public_task = track.task("rdkit_001_qed_max")
evaluation = track.evaluate_one({
    "task_id": "rdkit_001_qed_max",
    "response": candidate_answer_text,
})
```

多 Track 选择可以使用 `vgb.load_suite([...])`，但实验 materializer 仍必须
校验每个 record 的 canonical Track 和 task ID，不能依赖 package 的隐式默认
全量选择。

VGB integration 分成两个宿主侧阶段：

1. **Prompt materialization**：调用 `load_track(...).prompts()`，为每个 Harbor
   task 生成公开的 `agent-trial-input.v1`，只包含 Track、task ID、prompt 和
   answer schema。这个文件可以进入 agent workspace；不要把 package、gold
   answers、verifier specs 或 scoring config 挂载进 agent container。
2. **Host-side evaluation**：Harbor Trial 完成后，读取 adapter 产生的
   candidate answer，按官方 `task(...)["answer_schema"]` 和 Track 类型构造
   官方 answer record，再调用同一个 `Track.evaluate_one()`；官方返回对象原样
   保存为 `vgb-evaluation.json`，再由 `result_projection.py` 映射到 Infra result
   和可选的旧 schema-v5 兼容输出。Open-generation 使用官方支持的
   `candidates` 或 `response` 形状；property calculation 使用 task schema
   对应的 typed `answer`/`unit` 形状，不能把所有 Track 强行转成同一个字段。

评分调用不能在 agent container 内执行。VGB package 的私有 verifier resources
和 scoring data 只存在于新仓库管理的 host-side VGB runtime 中。若 Harbor
Infra 的主 venv 与 VGB package 依赖冲突，`runtime.py` 可以创建新仓库自己管理
的隔离 VGB venv，并在该 venv 中 import 官方 package；这仍是直接调用官方 API，
不是经由旧 workspace 的 bridge。隔离 runtime 的 wheel、Python、package
version、source tag/commit 和 SHA-256 必须进入 `runtime-lock.json`。

当前可用的 package lock 参考为：

```text
package: verifier-grounded-benchmark
version: 0.10.0
source_tag: v0.10.0
source_commit: 5e4d345e47ff5e0cac16297972ce7a70e6577075
wheel: verifier_grounded_benchmark-0.10.0-py3-none-any.whl
wheel_sha256: 019b7c90031355b804d8d6873cdc81b52153496b06681962775f54367c19140a
release_manifest_sha256: 7d0b97bec75c30792dd737d89840f29dd5cefbad3604a7b4019aafebb08ef67f
task_inventory_sha256: 52cc8eadd09cc1e64897841f25fd8f6cbca740484f060ab9654c968609781245
```

新仓库必须验证当前官方 package release 是否已升级；上面的值是已安装本机
wheel 的基线示例，不是无条件的永久版本。若 release 变更，必须同时更新
wheel hash、source identity、task inventory 和四 Track acceptance fixtures。

`provision_vgb_runtime.py` 应从官方 release wheel 安装隔离 runtime，而不是从
旧 workspace 的 editable checkout 安装。建议流程是：校验 release manifest 和
wheel SHA-256，创建 Python 3.12 venv，安装 wheel 及其声明依赖，执行四 Track
`load_track()`/`prompts()` smoke，并把 `pip freeze`、Python 路径和 package
metadata 写入 runtime manifest。对于 `open_generation_xtb`，宿主侧 VGB runtime
还必须在评分前验证 package 所需的 xTB executable/version（当前正式 pack
要求 xTB `6.7.1`）；该 executable 属于 host-side verifier environment，不得
挂载给 agent container。`property_calculation_*` 不依赖外部 verifier executable。

通用 agent 输出保持独立于 VGB：

```json
{
  "schema_version": "agent-output.v1",
  "record_id": "rdkit_001_qed_max",
  "status": "completed",
  "answer": {"short_text": "...", "full_text": "..."},
  "artifacts": [],
  "session_evidence": {},
  "failure": null
}
```

VGB result projection 至少保存 `status`、`task_id`、`scores`、`failure_type`、
`message`、`properties`、`constraint_scores`、`versions`、`raw_answer` 和
`extracted_answer`。如果需要兼容旧 workspace 的 schema-v5，兼容写入只能是
结果投影步骤，不能反向成为新执行链路的依赖。

### 4.5 Harbor Environment

第一版直接使用 Harbor `v0.23.0` 的内置 `DockerEnvironment`、prebuilt image
处理、CPU/memory enforcement policy 和 JobConfig 资源覆盖。不要先实现
`BenchDockerEnvironment` 或自有 Docker CLI 生命周期；只有 Harbor public hook
无法满足已确认的 OpenClaw mount/evidence 需求时，才增加最小 subclass，并为
缺口提交独立 contract test。

本轮不生成 PID、swap、`/dev/shm`、tmpfs、ulimits 或 storage-driver overlay。
Harbor 原生支持的 CPU/memory 字段由 JobConfig 直接映射，实际容器生命周期、
cleanup、logs 和 resource policy validation 由 Harbor 负责。

Harbor Environment 和旧 benchmark Docker runtime 的 owner 关系必须明确：

- Harbor backend 启用时，只有 Harbor `DockerEnvironment` 创建、启动、等待、
  停止和删除容器；
- 旧 benchmark backend 的 `DockerContainerRuntime` 只能在旧 backend 运行时
  使用；
- 两者不得共享 container name、workspace mount 或 cleanup registry；
- Infra 记录 Harbor job/trial identity 和 profile manifest，不自行接管 Harbor
  container labels、owner PID 或 cleanup registry。

## 5. 新仓库目录契约

初始化完成后，仓库至少应具有以下结构：

```text
/Users/xutao/harbor-agent-infra/
├── README.md
├── AGENTS.md                         # 新仓库自己的开发约束
├── pyproject.toml
├── uv.lock
├── runtime-lock.json
├── .env.example
├── .gitignore
├── configs/
│   ├── resources/
│   │   ├── profiles.example.yaml
│   │   └── local.yaml                # 本地私有文件，不提交真实值
│   ├── experiments/
│   │   └── openclaw-vgb-smoke.yaml
│   ├── domains/
│   │   └── vgb.yaml
│   └── images/
│       └── openclaw.example.yaml
├── src/harbor_agent_infra/
│   ├── __init__.py
│   ├── cli.py
│   ├── contracts/
│   │   ├── experiment.py
│   │   ├── image.py
│   │   ├── resource_profile.py
│   │   └── manifest.py
│   ├── preparation/
│   │   ├── config_materializer.py
│   │   ├── resource_profiles.py
│   │   └── image_manifest.py
│   ├── harbor/
│   │   ├── job_config.py
│   │   ├── adapter_loading.py
│   │   └── result_projection.py
├── adapters/
│   └── openclaw/
│       ├── __init__.py
│       ├── adapter.py
│       ├── config.py
│       ├── session.py
│       ├── command.py
│       └── evidence.py
├── integrations/
│   └── vgb/
│       ├── __init__.py
│       ├── release_lock.py
│       ├── tracks.py
│       ├── prompts.py
│       ├── evaluator.py
│       ├── result_projection.py
│       └── runtime.py
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── fixtures/
│   │   ├── resources/
│   │   ├── fake-agent/
│   │   └── trial-input/
│   └── conftest.py
├── scripts/
│   ├── doctor.py
│   ├── build_test_image.sh
│   ├── provision_vgb_runtime.py
│   └── verify_acceptance.py
└── docs/
    ├── architecture.md
    ├── configuration.md
    └── acceptance.md
```

`runtime-lock.json` 至少包含 Harbor pin、OpenClaw image/adapter pin，以及
`verifier-grounded-benchmark` 的 package release manifest：package/version、
source tag/commit、wheel filename/SHA-256/size、result schema version、scoring
version 和四 Track task inventory hash。它不保存 provider secret，也不要求
把 VGB private resources 复制到 Git。

`configs/resources/local.yaml` 只能由本机维护，必须加入 `.gitignore`。提交
`profiles.example.yaml`、`configs/domains/vgb.yaml` 和测试 fixture；不要提交
API key、registry robot token、真实镜像私有地址或本机路径中的敏感信息。

## 6. 运行时契约

### 6.1 ExperimentSpec

实验配置至少包含：

```yaml
schema_version: experiment.v1
experiment_id: openclaw-vgb-smoke
domain: verifier-grounded
tracks:
  - open_generation_rdkit
  - open_generation_xtb
  - property_calculation_advanced
  - property_calculation_basic
agent:
  adapter: openclaw
  model: ${OPENCLAW_MODEL}
image:
  reference: ${OPENCLAW_IMAGE_REFERENCE}
  digest: ${OPENCLAW_IMAGE_DIGEST}
  platform: linux/arm64
  pull_policy: if_missing
resources:
  profile: ${RESOURCE_PROFILE}
  config_file: ${RESOURCE_PROFILE_FILE}
vgb:
  package_lock: runtime-lock.json
  track: ${VGB_TRACK}
  task_ids: ${VGB_TASK_IDS}
retry:
  n_attempts: 1
  max_retries: 0
  # fake smoke 的安全默认；正式实验从 domain/experiment policy 物化，
  # 并直接投影到 Harbor JobConfig，不另建 retry owner
```

配置 materializer 必须在运行前解析环境变量、校验 digest 和 profile，并将
脱敏后的 materialized config 写入 run root。VGB integration 必须先用官方
package 校验 Track、task ID 和 package lock，再生成公开 prompt。未解析的
placeholder、tag-only 镜像、缺失 profile、package lock 不一致或不支持的 Track
必须在分配 Trial 前失败。

### 6.2 ImageSpec

可执行 Trial 必须使用 immutable digest：

```text
registry.example/bench/openclaw@sha256:<64 lowercase hex>
```

`reference` 可保留 tag 作为人类可读名称，但 `digest` 是实际执行身份。Image
Manager 在运行前完成：

1. 解析 local image ID、RepoDigest、OS、architecture；
2. 按 pull policy 决定是否拉取；
3. 对远程镜像使用 digest pull，禁止把 tag 解析后不记录 digest；
4. 验证目标 platform 与 Docker daemon 可运行 platform；
5. 写入 `image-evidence.json`；
6. 发现 digest mismatch 时停止 Job，不能继续使用本地同名 tag。

Image Manager 的 registry 操作使用 Docker credential helper 或预先完成的
`docker login`。密码、token 和完整 `docker inspect Config.Env` 不得写入
evidence。

### 6.3 ResourceProfile

资源 profile 必须是外部 YAML/JSON 配置，不允许代码默认资源值。第一版只暴露
Harbor v0.23.0 原生、且 Docker provider 明确支持的资源字段：

- `cpus` / `override_cpus`；
- `memory_mb` / `override_memory_mb`；
- `cpu_enforcement_policy`；
- `memory_enforcement_policy`；
- `max_concurrent_trials`，映射到 Harbor Job 的原生并发字段。

`storage_mb`、GPU、TPU 不是本机 Docker 第一版默认启用项；只有 Harbor
provider capability 明确通过并且 profile 显式声明时，materializer 才能接受。
PID、swap、`/dev/shm`、tmpfs、ulimits 和 storage-driver quota 不出现在本轮
profile schema，也不在本轮生成 overlay 或做自定义 enforcement。

建议 schema：

```yaml
schema_version: resource-profiles.v1

capacity:
  source: harbor-job              # Harbor local orchestrator owns admission
  max_concurrent_trials: 1        # 由用户填写；映射 n_concurrent_trials

profiles:
  local-smoke:
    cpus: 1.0
    memory_mb: 512
    cpu_enforcement_policy: limit
    memory_enforcement_policy: limit
```

上面的数字只表示配置格式的测试示例，不能被复制成不可修改的 Python 常量。
正式环境应由用户创建 `configs/resources/local.yaml` 并填写目标值。若尚未
确定目标值，可以先使用 `tests/fixtures/resources/` 中的短生命周期 fake
image fixture 完成框架测试；真实 OpenClaw 运行必须显式指定 profile。

字段规则：

| 字段 | 规则 |
|---|---|
| `capacity.source` | `harbor-job`；准入由 Harbor local orchestrator 执行 |
| `max_concurrent_trials` | 正整数，映射 Harbor Job 的原生并发值 |
| `cpus` | 正数，映射 Harbor `override_cpus` / task environment `cpus` |
| `memory_mb` | 正整数，映射 Harbor `override_memory_mb` / task environment `memory_mb` |
| `cpu_enforcement_policy` | Harbor `auto`、`limit`、`ignore` 等已支持值 |
| `memory_enforcement_policy` | Harbor `auto`、`limit`、`ignore` 等已支持值 |
| `storage_mb` | 仅在 provider capability 明确支持时可选；默认不启用 |
| `gpus` / `tpu` | 仅在对应 Harbor provider capability 明确支持时可选；默认不启用 |

Loader 必须拒绝未知字段、负数、空 profile、重复 profile 名和缺失的被选中
profile。它还必须通过 Harbor 的 provider capability preflight，不能自己推断
某个资源字段“应该有效”。解析后的 profile、配置文件 SHA-256、Harbor JobConfig
快照和 preflight 结果写入 run manifest。

### 6.4 容量准入

第一版按 profile 生成 Harbor JobConfig，每个 Job 只使用一个 profile，直接把
`max_concurrent_trials` 传给 Harbor 的 `n_concurrent_trials`。Harbor 负责
queue、worker admission、retry、cancellation 和 trial cleanup；Infra 只记录
materialized JobConfig 和 profile hash。不要在 Infra 中再实现按 CPU/memory
预算计算的第二套 scheduler。

### 6.5 Infra Trial Manifest

每个 Harbor Trial 额外写一个轻量 infra manifest（Harbor 自己的 trial result
仍是生命周期和执行状态真源）。建议 schema：

```json
{
  "schema_version": "infra-trial-manifest.v1",
  "run_id": "...",
  "job_id": "...",
  "trial_id": "...",
  "attempt_id": "...",
  "record_id": "...",
  "track": "...",
  "adapter": {
    "id": "openclaw",
    "version": "...",
    "source_hash": "sha256:..."
  },
  "image": {
    "reference": "...",
    "digest": "sha256:...",
    "platform": "linux/arm64"
  },
  "vgb": {
    "package": "verifier-grounded-benchmark",
    "version": "0.10.0",
    "source_tag": "v0.10.0",
    "source_commit": "...",
    "wheel_sha256": "sha256:...",
    "track": "open_generation_rdkit",
    "task_id": "rdkit_001_qed_max",
    "evaluation_path": "vgb-evaluation.json"
  },
  "resource_profile": {
    "name": "...",
    "config_sha256": "sha256:...",
    "cpus": null,
    "memory_mb": null,
    "cpu_enforcement_policy": "limit",
    "memory_enforcement_policy": "limit",
    "max_concurrent_trials": null
  },
  "lifecycle": {
    "status": "completed",
    "started_at": "...",
    "finished_at": "...",
    "return_code": 0,
    "retry_index": 0
  },
  "harbor": {
    "job_config_path": "...",
    "trial_dir": "...",
    "result_path": "...",
    "artifacts_path": "..."
  },
  "failure": null,
  "artifacts": [],
  "domain_result_path": null
}
```

Harbor 的原生 trial result 直接保留；infra manifest 只保存 JobConfig、profile、
image、adapter、VGB release identity 和 evaluation result 的关联，不能覆盖
Harbor result。旧 schema-v5 per-record result 只能由显式 compatibility projection
生成。

### 6.6 Trial failure 与资源结果投影

Harbor 负责 Docker Environment 的生命周期、日志和资源 policy validation。
Infra 第一版只读取 Harbor trial result、EnvironmentConfig/JobConfig 快照和
Harbor 公开 artifacts；不读取 cgroup、不调用 Docker CLI 重新采样，也不自定义
OOM/PID/shm/storage failure code。若 Harbor trial result 标记环境或 agent 失败，
Infra 原样保留并由 VGB integration 投影到 Infra/domain result。旧 schema-v5
字段只在显式兼容输出时生成。后续迭代若需要更细粒度
OOM 证据，再单独增加 capability 和 evidence design；本轮验收只要求 Harbor
原生 result/artifact 可以被稳定读取。

## 7. Harbor Framework 与 Registry 安装指南

### 7.1 本机必需环境

当前本机基线已经具备：

```text
macOS Apple Silicon
Docker 29.7.2
Docker Compose v5.3.1
uv 0.11.7
Node v24.15.0（宿主机仅作诊断；OpenClaw 9.5 运行在 image 内）
```

新仓库建议的最低可复现环境：

- Python `3.12.x`；Harbor `v0.23.0` 要求 Python `>=3.12`，建议继续使用
  `3.12.13` 作为可复现基线；
- uv `>=0.8.4`，当前 `0.11.7` 可以使用；
- Git、curl、jq；
- Docker Desktop 或 Docker Engine，Docker CLI 和 Compose v2 可用；
- Docker daemon 开启 Harbor 所需的 CPU quota 和 memory limit 能力；
- 至少一个可执行的 Linux image，目标 platform 与本机 daemon 一致；
- 如果构建 OpenClaw 9.5 image，image 内 Node 必须满足 OpenClaw 的 engine；
- provider credentials 只通过本地环境、credential helper 或未跟踪 `.env` 注入。

新仓库不要求宿主机安装 OpenClaw。VGB package 必须由新仓库通过官方 release
wheel 安装到它自己管理的 host-side VGB runtime；不使用旧 workspace 的 VGB
venv、wheel cache 或 Python import path。RDKit 等 package dependencies 和
Open-generation verifier 的外部可执行依赖由这个 VGB runtime/host preflight
管理；它们不进入 agent image。Docker daemon 只需满足 Harbor v0.23.0
声明的 CPU/memory enforcement capability；本轮不要求为未采用的 PID、swap 或
shared-memory 选项额外配置 daemon。

首次初始化新仓库：

```bash
mkdir -p /Users/xutao/harbor-agent-infra
cd /Users/xutao/harbor-agent-infra
git init
uv python install 3.12.13       # 已有时可跳过
uv venv --python 3.12.13 .venv
```

### 7.2 Harbor Framework 项目依赖

Harbor 官方 README 提供 `uv tool install harbor` 和 `pip install harbor`。
新仓库不能依赖未锁定的 latest CLI；项目依赖必须固定到 Harbor `v0.23.0`
及其 release commit：

```text
1e5c5c6db929a10a140d05e606882c671ae20729
```

`pyproject.toml` 应使用等价的 Git pin（具体 PEP 508 写法由 uv 生成并验证），
而不是只写一个浮动版本。初始依赖可从 Harbor v0.23.0 `pyproject.toml` 对照：

```toml
[project]
requires-python = ">=3.12,<3.14"
dependencies = [
  "harbor @ git+https://github.com/harbor-framework/harbor.git@1e5c5c6db929a10a140d05e606882c671ae20729",
  "pydantic>=2.11.7",
  "PyYAML>=6.0.2",
  "packaging>=25.0",
]

[dependency-groups]
dev = [
  "pytest>=8.4",
  "pytest-asyncio>=1.2",
  "pytest-cov>=7",
  "ruff>=0.15",
]
```

实际文件可根据 uv resolver 调整下限，但必须在 `runtime-lock.json` 和 `uv.lock`
中记录最终版本。不要把旧仓库的大型化学依赖复制到 Infra 主 venv；VGB 官方
依赖只进入隔离的 VGB runtime。

安装和验证：

```bash
cd /Users/xutao/harbor-agent-infra
uv lock
uv sync --dev
uv run harbor --version
uv run python -c 'import harbor; print(getattr(harbor, "__version__", "import-ok"))'
docker info
docker compose version
uv run python scripts/provision_vgb_runtime.py --lock runtime-lock.json --check
```

如果需要独立验证官方 CLI，也可以执行：

```bash
uv tool install harbor==0.23.0
harbor --version
```

但验收命令必须使用新仓库环境中的 `uv run harbor`，避免全局 tool 和项目 lock
不一致。若 Harbor 发布包与指定 commit 的版本元数据不一致，以 Git commit lock
和新仓库 `uv.lock` 为准。

### 7.3 Harbor Framework 的本地 smoke

不需要先部署 Harbor Registry。使用本地预构建 fake image 即可验证 Harbor
Framework、custom environment、adapter 和 evidence：

```bash
cd /Users/xutao/harbor-agent-infra
uv run hai doctor --resource-config tests/fixtures/resources/local.yaml
uv run hai image inspect --reference hai-fake-agent:acceptance
uv run hai run --experiment configs/experiments/fake-agent-smoke.yaml
```

新会话必须先实现 `fake-agent-smoke.yaml` 和最小 fake image，再实现真实
OpenClaw provider E2E。Fake image 只需写出确定性的 `agent-output.v1` 并退出，
不能访问宿主项目或 provider。

### 7.4 Harbor Registry 的边界和安装建议

Harbor Registry 不是 macOS 上的必需依赖，也不应为了本次本机 smoke 把 Registry
服务和 benchmark 容器挤进同一个小型 Docker Desktop VM。需要远程镜像保存、
RBAC、扫描、保留策略或多机器分发时，再部署 Harbor Registry。

推荐部署目标：独立 Linux 主机或 Kubernetes，具备持久磁盘、TLS、备份和监控。
不要把本机 Docker Desktop 当作生产 Registry 主机。部署时遵循 Harbor 官方
文档和对应 release 的 installer，不要在本仓库自制 Harbor Registry compose。

典型 Linux 安装流程（版本号必须由运维在执行前选定并校验 release checksum）：

```bash
curl -LO https://github.com/goharbor/harbor/releases/download/v<HARBOR_REGISTRY_VERSION>/harbor-online-installer-v<HARBOR_REGISTRY_VERSION>.tgz
tar xzf harbor-online-installer-v<HARBOR_REGISTRY_VERSION>.tgz
cd harbor
cp harbor.yml.tmpl harbor.yml
# 设置 hostname、HTTPS certificate/key、data_volume 和必要的 admin 密码
sudo ./install.sh --with-trivy
docker login <registry-host>
```

在 Harbor Agent Infra 中使用 Registry 时只记录：

- registry host/project；
- image reference；
- immutable digest；
- platform；
- pull timestamp 和结果；
- scanner/replication 的非敏感状态。

不要把 Harbor admin 密码、Robot token 或 Docker credential store 内容提交到
仓库。正式 Registry 的 TLS、证书、备份、垃圾回收、Trivy 和项目配额是独立
运维任务，不计入本仓库第一阶段的 Framework 验收。

官方入口：

- Harbor Framework 源码：[https://github.com/harbor-framework/harbor](https://github.com/harbor-framework/harbor)
- Harbor Framework 文档：[https://docs.harborframework.com/](https://docs.harborframework.com/)
- Harbor Registry 文档：[https://goharbor.io/docs/](https://goharbor.io/docs/)

## 8. 配置与安全要求

### 8.1 配置优先级

建议优先级：

```text
CLI 显式参数 > 实验文件字段 > 环境变量 > 示例文件
```

`profiles.example.yaml` 不能作为生产 fallback。运行命令必须显式获得资源
profile 文件和 profile 名称，或从实验文件中得到同等明确的值。

### 8.2 Secrets

允许的输入方式：

- 当前 shell 的环境变量；
- 未跟踪 `.env`；
- Docker credential helper；
- CI secret store。

禁止：

- 写入 Git tracked YAML/JSON；
- 写入 Trial envelope；
- 将完整环境变量写入 Docker inspect evidence；
- 将 provider token 放进 OpenClaw image layer；
- 将 Harbor Registry robot token 放进 `runtime-lock.json`。

### 8.3 路径和挂载

Agent 容器只允许获得当前 Trial 的 workspace、输入和 session/config mount。
禁止挂载：

- 旧 benchmark 项目根目录；
- VGB package 的 host-side runtime、scoring resources 或 release wheel；
- 其他 Trial workspace；
- Docker socket；
- Harbor Registry credentials 文件；
- 用户 home 的完整目录。

挂载源必须拒绝 symlink、special file 和 workspace boundary 外路径。每个
mount 写入脱敏 manifest，包含 source hash（必要时）、target、mode 和 kind。

## 9. 初始化实施顺序

新仓库会话按以下顺序实施，不要一开始把所有 Agent 和真实 provider 都接入：

### Phase 0：仓库与环境基线

交付：

- Git 仓库、`pyproject.toml`、`uv.lock`、`runtime-lock.json`；
- `README.md`、`AGENTS.md`、`.env.example`、`.gitignore`；
- `hai doctor`，能检查 Python、uv、Docker、Compose、Harbor import 和 image
  platform；
- Harbor v0.23.0 pinned import smoke；
- 官方 VGB wheel/runtime lock 校验和无模型四 Track `load_track()` smoke。

验收：

```bash
uv sync --dev
uv run hai doctor
uv run harbor --version
docker info
```

### Phase 1：通用 contracts 和资源配置

交付：

- `ExperimentSpec`、`ImageSpec`、`ResourceProfile`、infra manifest 和官方 VGB
  integration contract；
- YAML loader、unknown-field/范围校验、配置 hash；
- Harbor provider capability preflight 和 JobConfig materializer；
- 官方 VGB release loader、Track/task allowlist 和 prompt materializer；
- test fixture，不提供代码默认资源值，也不实现第二套 admission scheduler。

验收：

- 合法 profile 可 round-trip；
- 缺失 profile、负数、未知字段、tag-only image 和 provider 不支持的资源字段都被拒绝；
- materialized JobConfig 的 CPU/memory policy 和 `n_concurrent_trials` 与 profile 一致；
- 修改 YAML 后 hash 和 run manifest 变化，旧 manifest 不被重写。

### Phase 2：Harbor 原生 Job/Trial 和 Fake Adapter

交付：

- Harbor 原生 JobConfig materializer；
- fake agent adapter 和 fake image；
- Harbor result 与 infra manifest 分离；
- 通过 Harbor public APIs 验证 mounts、logs、artifacts 和 resource policy。

验收：

- 一条 fake Trial 能完成并清理；
- Harbor trial result、workspace、stdout/stderr 和 artifacts 可读；
- JobConfig 的 CPU/memory 和并发设置等于 profile；
- 中断、retry 和 cleanup 行为由 Harbor 原生测试/contract 覆盖。

### Phase 3：OpenClaw Adapter

交付：

- `adapters/openclaw/`；
- OpenClaw 9.5 image manifest 和 digest contract；
- explicit session identity、state root、trajectory export；
- primary/reminder/rescue 生命周期；
- typed failure 和 artifact collection。

验收：

- 无 provider 的 contract fixture 能验证命令、session selector、state root
  和 JSON envelope；
- session export identity 与 trial identity 一致；
- export failure 不覆盖原始 agent failure；
- retry 使用新的 Trial/attempt identity；
- agent image 不包含 VGB hidden material。

### Phase 4：官方 VGB Integration

交付：

- 锁定官方 VGB package wheel、source identity、manifest 和 task inventory；
- `integrations/vgb/` 的四个 Track discovery、公开 prompt materialization 和
  host-side evaluator 调用；
- Harbor task 到 `Track.evaluate_one()` 的输入映射；
- VGB evaluation result、infra manifest 和 schema-v5 compatibility projection；
- 不复制第二套 scorer，不调用旧 workspace 的 VGB bridge。

验收：

- 四个 Track 各选一条固定 task，prompt 和 task schema 来自官方 package；
- 离线无模型流程能够生成四 Track 的 `agent-trial-input.v1`；
- 固定 candidate answer fixture 能通过官方 `Track.evaluate_one()` 评分；
- 官方 package lock、wheel hash、task inventory 和 evaluation result 可追溯；
- Harbor 原生 result 与 VGB evaluation result 均保留，schema-v5 只作为显式兼容投影；
- 在没有 `/Users/xutao/.openclaw/workspace` Python import 的环境中，VGB
  prompt/score smoke 仍能通过。

### Phase 5：Registry 和真实 provider 对照

只有前四阶段通过后执行：

- Harbor Registry digest pull/push（如环境具备 Registry）；
- OpenClaw provider preflight；
- 四个 Track 各一条真实题目；
- Harbor 原生并发、retry、取消和 memory-limit 对照；
- 再评估是否将 Harbor backend 设为默认执行入口。

## 10. 测试和验收命令

### 10.1 必须执行的静态检查

```bash
cd /Users/xutao/harbor-agent-infra
uv run ruff check .
uv run python -m compileall -q src adapters scripts
uv run pytest -m 'not integration'
```

若使用 mypy/ty 等类型工具，可作为加分项，但不能替代 pytest 和真实 Docker
acceptance。

### 10.2 Unit/contract 测试矩阵

必须覆盖：

- ResourceProfile 解析、范围和 hash；
- Harbor provider capability preflight；
- Harbor JobConfig CPU/memory policy 和 `n_concurrent_trials` 映射；
- ImageSpec tag-only、digest mismatch、platform mismatch 和 pull policy；
- Harbor Job config 的 `n_attempts`、retry 和 profile concurrency 投影；
- Harbor Agent adapter discovery、Harbor capability 声明和 source hash；
- VGB package lock、Track prompt fixture 和 evaluator result fixture；
- OpenClaw `agentId/sessionKey/sessionId` 生成；
- `agents.entries` 配置投影；
- result/evidence precedence；
- secrets redaction 和 path containment；
- Harbor cancellation、cleanup 和 artifact collection contract；
- Harbor result 与 domain result 的隔离；
- VGB package lock、wheel hash、Track allowlist 和 task inventory hash；
- VGB `Track.prompts()` 输出的公开字段和 `Track.evaluate_one()` 结果保存；
- VGB runtime 与旧 workspace 完全断开时，prompt/score fixture 仍可运行。

### 10.3 Harbor integration 测试

使用 fake image 和 Harbor 官方 Docker Environment，避免 provider 和 VGB hidden
runtime：

1. 正常退出：返回 `agent-output.v1`，写 infra manifest，等待 Harbor 完成 cleanup。
2. 非零退出：保留 stdout/stderr，映射 typed failure。
3. 超时：验证 Harbor trial timeout 和公开 result 状态。
4. memory limit：使用 Harbor 原生 memory policy，确认 JobConfig 和 trial
   failure evidence 可读。
5. 并发：确认 `n_concurrent_trials` 由 Harbor local orchestrator 生效。
6. 取消：验证 Harbor 的 Job/Trial cancellation 和 artifact finalization。
7. image digest：本地 tag 被改写时，Infra image manifest 校验阻止 Job materialization。

测试必须使用临时目录和专用 labels，不能删除用户已有的 benchmark container。

### 10.4 VGB Integration 验收

必须在新仓库内独立完成以下检查：

- 四个 Track 各一条固定 task，prompt、answer schema 和 task ID 来自官方 package；
- 四个 Track 的固定 candidate answer fixture 由官方 package 评分；
- package version、source tag/commit、wheel SHA-256、task inventory 和 scoring
  result 都写入 run artifacts；
- 使用临时干净 venv 执行，不把 `/Users/xutao/.openclaw/workspace` 放进
  `PYTHONPATH`，不 import `benchmarking.*`；
- 结果投影后可以生成当前 schema-v5 兼容 payload，但不依赖旧 workspace
  的 `ResultSink`、VGB bridge 或 dashboard。

旧 workspace 对照只作为附加回归测试：相同官方 package release 和 candidate
answer 下，比较 score、status axes 和结果投影；对照失败时不得复制旧 scorer，
应先检查 release lock、task inventory 和官方 API 输入是否一致。

### 10.5 Registry 验收（可选但必须可重复）

如果配置了 Harbor Registry：

```bash
docker login <registry-host>
uv run hai image pull --reference <registry-host>/bench/openclaw@sha256:<digest>
uv run hai image inspect --reference <registry-host>/bench/openclaw@sha256:<digest>
```

必须证明 pull 后本地 digest、platform 和 manifest 与配置一致；错误凭据、
不存在 digest、TLS 错误和平台不匹配必须在 Job 创建前失败。

## 11. 最终放行标准

新仓库只有同时满足以下条件，才能标记第一版 acceptance complete：

### A. 环境和可复现性

- `uv.lock` 存在且可在干净 `.venv` 中 `uv sync --frozen`；
- Harbor Framework 版本和 commit 与 `runtime-lock.json` 一致；
- `uv run harbor --version`、Docker CLI、Compose 和 platform doctor 通过；
- 所有可执行 image 使用 digest，平台写入 manifest；
- provider key、Registry token 和本机私有路径没有进入 Git 或 evidence。

### B. 架构和边界

- 新仓库不 import 旧仓库的 `benchmarking.*`；
- `adapters/openclaw/` 存在且没有 `adapters/openclaw_vgb/`；
- Agent Adapter、官方 VGB Integration、Image Manager、JobConfig materialization 和 Harbor
  Environment 具有独立 contract；
- Harbor 原生 result 没有替换旧仓库结果 schema；
- VGB hidden runtime 不进入 agent image 或 container mount；
- 一个 Trial/container 只有一个生命周期 owner。

### C. 资源配置和 Harbor 原生执行

- 资源 profile 只能从外部配置读取，代码没有生产默认值；
- 缺失或非法 profile 会在 Trial 分配前失败；
- Harbor materialized JobConfig 与 profile 一致；
- `n_concurrent_trials`、CPU policy 和 memory policy 由 Harbor 原生执行；
- Harbor trial result/artifacts 可以稳定读取；
- 本轮不承诺 PID、swap、shm、tmpfs、ulimits、storage-driver quota 或自定义 OOM 分类；
- run manifest 保存 profile snapshot、Harbor JobConfig snapshot 和 config hash。

### D. 生命周期和证据

- fake image 的正常、失败、超时、取消、memory-limit 和 Harbor cleanup 测试通过；
- retry 使用新的 Trial/attempt/workspace/session identity；
- OpenClaw trajectory export identity 可追溯；
- stdout/stderr、image evidence、Harbor artifacts、infra manifest 和 domain
  result 路径完整；
- Harbor 原生 cancellation/cleanup contract 和 trial artifact finalization 通过。

### E. VGB 官方集成和兼容投影

- 四个 Track 的官方 package prompt/score fixture 全部通过；
- 新执行链不 import 旧 workspace，不依赖旧 VGB bridge/scorer/writer；
- package release identity、wheel hash、task inventory 和 evaluation result 可追溯；
- score、status axes 和 schema-v5 compatibility projection 保持既有语义；
- 旧 workspace dashboard/reader 可以读取显式兼容输出，但不是新 run 的执行依赖。

### F. 文档和交接

- 新仓库 README 能从零完成安装、fake smoke 和资源配置；
- `docs/architecture.md`、`docs/configuration.md`、`docs/acceptance.md` 与代码
  一致；
- acceptance run 的命令、时间、image digest、profile hash 和结果路径已记录；
- 所有测试和限制明确区分 provider-free、Docker integration、Registry
  integration 和真实模型 E2E。

## 12. 新会话第一条执行清单

新仓库会话启动后应按下面顺序执行：

```bash
cd /Users/xutao/harbor-agent-infra
pwd
git status --short --branch
test -f /Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md
python3 --version
uv --version
docker version
docker compose version
```

然后：

1. 创建仓库骨架和 `.venv`；
2. 写入 Harbor v0.23.0 commit pin 与 `uv.lock`；
3. 实现 `hai doctor`、resource profile loader 和 Harbor JobConfig materializer；
4. 创建 fake image、Harbor Agent adapter 和 unit/contract tests；
5. 先通过 provider-free Harbor Docker acceptance；
6. 实现 `integrations/vgb/`，锁定官方 wheel 并通过四 Track prompt/score fixtures；
7. 再实现 `adapters/openclaw/` 并连接 VGB integration 的 task/result 文件。

任何真实 provider run、Harbor Registry 部署、删除旧容器或修改现有 benchmark
仓库，都必须在对应工作说明中单独记录，不得为了通过 fake smoke 擅自改变旧
仓库的结果契约。
