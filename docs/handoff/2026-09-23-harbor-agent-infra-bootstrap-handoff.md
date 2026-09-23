# Harbor Agent Infra 独立仓库初始化与验收交接设计

日期：2026-09-23

范围：新建 `/Users/xutao/harbor-agent-infra/`，作为可复用的 Harbor Agent
实验控制面。
状态：**PROPOSED HANDOFF**。本文是交给新仓库会话的初始化、框架搭建和验收
依据；本文所述功能在新仓库完成前均不得视为已实现。

## 1. 交接结论

新建一个独立 Git 仓库 `harbor-agent-infra`。它负责通用实验调度、Harbor
Job/Trial 生命周期、Docker 环境、镜像引用和分发、资源准入、Agent Adapter
加载以及通用证据。现有
`/Users/xutao/.openclaw/workspace` 继续负责 verifier-grounded benchmark
（VGB）的 Track 数据、领域 prompt、评分器、当前结果 schema 和 dashboard。

两边通过版本化的 domain bridge 和通用 trial envelope 通信。Infra Core 不得
直接 import 现有仓库的 `benchmarking.*` 模块，也不得把 VGB hidden verifier、
VGB wheel、gold data 或现有运行时复制进 Agent 容器。

第一版只实现当前四个 VGB Track 的 OpenClaw 适配，但核心接口必须允许以后
加入 Codex、Claude Code、Hermes 或其他 Agent CLI。适配器目录名称确定为：

```text
adapters/openclaw/
```

不要使用 `adapters/openclaw_vgb/`。`openclaw` 表示 CLI 运行时适配器，VGB
属于外部 domain bridge，不应成为适配器的类型名称。

本仓库的首个目标是 Docker 本机执行。Harbor Registry 作为可选的镜像分发层
接入；本地 smoke 和单机验收不要求先部署 Harbor Registry。Harbor Framework
与 Harbor Registry 是两个独立产品边界：

```text
Harbor Framework：Job、Trial、Environment、Agent 的实验执行框架
Harbor Registry：OCI 镜像/制品保存、权限、扫描、复制和分发
harbor-agent-infra：调度、资源准入、适配器、镜像策略和跨域证据控制面
```

## 2. 已确认的不可变设计决策

以下决策已由需求方确认，新仓库会话不得自行改写：

1. 只支持当前四个 VGB Track：`open_generation_rdkit`、
   `open_generation_xtb`、`property_calculation_advanced`、
   `property_calculation_basic`。旧 benchmark、ChemQA、非 VGB 数据和历史
   host backend 不属于第一版执行范围。
2. 现有 benchmark 的 `RunnerResult`、per-record result、`results.json`、
   schema-v5 observability、workspace archive 和 dashboard read model 仍是
   结果真源。Harbor 原生 trial result、verifier result 和 metrics 不能替换
   它们。
3. Harbor 初期只负责 Job/Trial 编排和容器/镜像生命周期。OpenClaw 的
   primary、reminder、timeout retry、finalization rescue、session identity、
   VGB scoring 和结果投影必须保持现有语义。
4. CPU、内存、swap、PID、`/dev/shm`、临时存储和并发预算全部来自外部资源
   profile 配置。代码中禁止写死生产资源值，也禁止在配置缺失时静默回退到
   Python 默认值。
5. 旧仓库不增加 Harbor runtime 目录。新仓库通过 adapter/bridge 访问旧仓库
   的公开输入和评分能力；两个仓库可以独立发布和测试。
6. 一个容器生命周期只能有一个 owner。Harbor Environment 和旧仓库的
   `DockerContainerRuntime` 不得同时创建、停止或删除同一个容器。
7. Harbor 重试和 Infra 重试只能有一个最终 owner。第一版将 Harbor
   `n_attempts=1`、`retry.max_retries=0`，retry 由 Infra scheduler 根据
   adapter/domain contract 处理。

## 3. 当前参考实现与边界

参考源码位于 `/Users/xutao/harbor-agent-framework/`。它可以提供结构和
Harbor 0.4.0 接入样例，但不是新仓库的 Git 历史，也不是旧 benchmark 的
替代实现。

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

参考实现的资源配置只有 Harbor 原生的 `override_cpus`、
`override_memory_mb`、`override_storage_mb` 和 `override_gpus`。它没有满足本
项目需要的 PID、swap、共享内存和按 profile 的总量准入，所以新仓库必须在
Harbor Environment 外再建立自己的资源契约和验证层。

## 4. 目标架构

```text
hai CLI
  └─ ExperimentScheduler
       ├─ Record/Domain Bridge
       ├─ ResourceProfileLoader + CapacityAdmission
       ├─ ImageManager
       ├─ HarborJobPlanner
       └─ HarborJobRunner
            └─ Harbor Job
                 └─ Trial
                      ├─ AgentAdapter (adapters/openclaw/)
                      ├─ BenchDockerEnvironment
                      ├─ TrialEnvelope / resource evidence
                      └─ DomainBridge result projection
                           └─ existing VGB result contract
```

### 4.1 Infra Core

Infra Core 只处理通用对象和生命周期：

- 实验配置加载、schema 校验和配置 hash；
- work item 队列、profile 分组和资源准入；
- Harbor Job 配置物化与运行；
- Trial identity、attempt/retry 关系和取消；
- ImageSpec 解析、digest 校验、pull policy 和 platform 检查；
- Docker Environment 启停、挂载、资源参数、cleanup 和 OOM 证据；
- 通用 `TrialEnvelope`、run manifest、evidence manifest 和日志索引；
- Adapter 发现、版本锁定和 capability 检查。

Infra Core 不知道具体模型供应商、VGB 评分公式或 OpenClaw session 数据库
表结构。

### 4.2 Agent Adapter

Agent Adapter 将一个 Agent CLI 接入 Harbor，负责命令、环境、挂载和 agent
产物。第一版实现 `adapters/openclaw/`；以后新增 CLI 时只新增适配器，不改
调度器的核心资源和 Job/Trial 逻辑。

Adapter 至少提供以下协议（可以使用 Python `Protocol` 或抽象基类，但字段
语义必须保持一致）：

```python
class AgentAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def capabilities(self) -> AdapterCapabilities: ...
    def validate(self, context: AdapterValidationContext) -> None: ...
    def prepare_trial(self, context: TrialContext) -> AdapterPreparation: ...
    def command(self, context: TrialContext) -> tuple[str, ...]: ...
    def environment(self, context: TrialContext) -> dict[str, str]: ...
    def mounts(self, context: TrialContext) -> tuple[MountSpec, ...]: ...
    def collect_artifacts(self, context: TrialContext) -> AdapterArtifacts: ...
    def classify_failure(self, context: TrialContext) -> FailureClassification: ...
```

`AdapterPreparation` 应包含命令、脱敏后的环境摘要、挂载声明、超时和
session identity；不得包含明文 provider key。`AdapterArtifacts` 只包含宿主
可读的路径、大小、hash 和类型，不把大型 transcript 全部复制到内存。

### 4.3 OpenClaw Adapter

`adapters/openclaw/` 是 OpenClaw CLI 的通用适配器。VGB 只是它的第一个
domain 使用方。它负责：

- 使用 OpenClaw 9.5 的显式 `agentId`、`sessionKey`、`sessionId`；
- 在每个 Trial 使用独立的 `OPENCLAW_STATE_DIR` 和 workspace；
- 生成 OpenClaw 9.5 的 `agents.entries` 配置投影；
- 调用容器内 OpenClaw CLI/wrapper；
- primary、reminder 和 finalization rescue 的 invocation 编排；
- 调用公开的 session inventory 和 `sessions export-trajectory`；
- 导出 SQLite/trajectory/session evidence 的 manifest；
- 将原始 agent 输出写成通用 `AgentOutputEnvelope`；
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

### 4.4 Domain Bridge

Domain Bridge 是 Infra 与具体 benchmark 之间的版本化边界。它可以是一个
Python package、一个受控 subprocess 或一个 HTTP/JSONL bridge，但第一版建议
先使用显式文件和 subprocess contract，避免两个仓库互相 import。

通用输入：

```json
{
  "schema_version": "agent-trial-input.v1",
  "domain": "verifier-grounded",
  "track": "open_generation_rdkit",
  "record_id": "rdkit_001_qed_max",
  "prompt": "<public prompt>",
  "source_ref": "<opaque source path or dataset identity>",
  "grading_ref": "<opaque evaluator identity>",
  "input_artifacts": []
}
```

通用 agent 输出：

```json
{
  "schema_version": "agent-output.v1",
  "record_id": "rdkit_001_qed_max",
  "status": "completed",
  "answer": {
    "short_text": "...",
    "full_text": "..."
  },
  "artifacts": [],
  "session_evidence": {},
  "failure": null
}
```

VGB bridge 再将 `agent-output.v1` 投影为现有 benchmark 的
`RunnerResult`/schema-v5 输入，并调用 pinned VGB evaluator。此投影必须保留：

- `run_lifecycle_status`；
- `protocol_completion_status`；
- `answer_availability`；
- `answer_reliability`；
- `evaluable`、`scored`、`recovery_mode`、`degraded_execution`；
- `execution_error_kind`；
- observability、resource series、session lifecycle 和 attempt artifact refs。

VGB bridge 不得重新实现 prompt、Track 选择、评分公式或 release identity。
它应引用现有仓库的公开 bridge entrypoint；如果该 entrypoint 尚未存在，新
仓库只能提供 contract fixture，并把真实 VGB E2E 标为未完成，不能复制一份
第二 scorer。

### 4.5 Harbor Environment

实现 `BenchDockerEnvironment`，优先复用 Harbor 0.4.0 的 Docker Environment
生命周期和 prebuilt image 行为。由于 Harbor 0.4.0 的通用环境配置不能完整
表达 PID、swap 和 `/dev/shm`，需要在 Infra 侧增加明确的资源映射和 post-start
验证。

Harbor Environment 和 Docker runtime 的 owner 关系必须明确：

- Harbor backend 启用时，只有 `BenchDockerEnvironment` 创建、启动、等待、
  停止和删除容器；
- 旧 benchmark backend 的 `DockerContainerRuntime` 只能在旧 backend 运行时
  使用；
- 两者不得共享 container name、labels、workspace mount 或 cleanup registry；
- 每个 Trial 的容器必须带可验证的 Infra labels，包括 run、job、trial、attempt、
  adapter、profile 和 owner PID。

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
│   └── images/
│       └── openclaw.example.yaml
├── src/harbor_agent_infra/
│   ├── __init__.py
│   ├── cli.py
│   ├── contracts/
│   │   ├── adapter.py
│   │   ├── experiment.py
│   │   ├── image.py
│   │   ├── resources.py
│   │   ├── trial.py
│   │   └── evidence.py
│   ├── scheduler/
│   │   ├── planner.py
│   │   ├── admission.py
│   │   ├── lifecycle.py
│   │   └── cancellation.py
│   ├── harbor/
│   │   ├── config.py
│   │   ├── job_runner.py
│   │   ├── trial_mapping.py
│   │   └── environment.py
│   ├── images/
│   │   ├── manager.py
│   │   ├── digest.py
│   │   └── registry.py
│   ├── resources/
│   │   ├── loader.py
│   │   ├── validation.py
│   │   └── capacity.py
│   ├── evidence/
│   │   ├── envelope.py
│   │   ├── resource_events.py
│   │   └── manifest.py
│   └── bridges/
│       ├── domain.py
│       └── subprocess.py
├── adapters/
│   └── openclaw/
│       ├── __init__.py
│       ├── adapter.py
│       ├── config.py
│       ├── session.py
│       ├── command.py
│       └── evidence.py
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
│   └── verify_acceptance.py
└── docs/
    ├── architecture.md
    ├── configuration.md
    └── acceptance.md
```

`configs/resources/local.yaml` 只能由本机维护，必须加入 `.gitignore`。提交
`profiles.example.yaml` 和测试 fixture；不要提交 API key、registry robot
token、真实镜像私有地址或本机路径中的敏感信息。

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
retry:
  max_attempts: 1
  retryable_failure_codes: []
domain_bridge:
  type: subprocess
  command: ["<configured bridge command>"]
```

配置 materializer 必须在运行前解析环境变量、校验 digest 和 profile，并将
脱敏后的 materialized config 写入 run root。未解析的 placeholder、tag-only
镜像、缺失 profile 或不支持的 Track 必须在分配 Trial 前失败。

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

资源 profile 必须是外部 YAML/JSON 配置，不允许代码默认资源值。建议 schema：

```yaml
schema_version: resource-profiles.v1

capacity:
  source: docker-info             # docker-info 或 explicit
  cpu_reserve: 0.0                # 由用户填写；不是代码默认
  memory_reserve_bytes: 0         # 由用户填写；不是代码默认
  pids_reserve: 0                 # 由用户填写；不是代码默认
  max_concurrent_trials: 1       # 由用户填写

profiles:
  local-smoke:
    cpus: 1.0
    memory_bytes: 536870912
    memory_swap_bytes: 536870912
    pids_limit: 128
    shm_size_bytes: 67108864
    storage_bytes: 1073741824
    admission_weight: 1.0
```

上面的数字只表示配置格式的测试示例，不能被复制成不可修改的 Python 常量。
正式环境应由用户创建 `configs/resources/local.yaml` 并填写目标值。若尚未
确定目标值，可以先使用 `tests/fixtures/resources/` 中的短生命周期 fake
image fixture 完成框架测试；真实 OpenClaw 运行必须显式指定 profile。

字段规则：

| 字段 | 规则 |
|---|---|
| `capacity.source` | `docker-info` 读取 Docker daemon 总量；`explicit` 使用配置值 |
| `cpu_reserve` | 非负数，代表给 Docker/系统/runner 预留的 CPU |
| `memory_reserve_bytes` | 非负整数，代表不可分配给 Trial 的内存 |
| `pids_reserve` | 非负整数，代表系统和 runner 的 PID 预留 |
| `max_concurrent_trials` | 正整数，上限由用户配置 |
| `cpus` | 正数，对应 Docker `--cpus` |
| `memory_bytes` | 正整数，对应 Docker `--memory` |
| `memory_swap_bytes` | `null` 或不小于 `memory_bytes`；对应 `--memory-swap` |
| `pids_limit` | 正整数，对应 Docker `--pids-limit` |
| `shm_size_bytes` | 正整数，对应 `--shm-size` |
| `storage_bytes` | 正整数；若当前 Docker backend 无法强制，必须记录为 unsupported 并阻止宣称已限制 |
| `admission_weight` | 正数，用于 profile 分组和调度报告 |

Loader 必须拒绝未知字段、负数、`memory_swap_bytes < memory_bytes`、空 profile、
重复 profile 名和缺失的被选中 profile。所有解析后的字段、配置文件 SHA-256、
Docker capacity snapshot 和校验结果都进入 run manifest。

### 6.4 容量准入

第一版按 profile 将 work items 分批。每个 Harbor Job 只使用一个 profile，
这样 Harbor 的 `n_concurrent_trials` 对应同质资源单元。Planner 计算：

```text
available_cpu = daemon_cpu - cpu_reserve
available_memory = daemon_memory - memory_reserve_bytes
available_pids = daemon_pids - pids_reserve

profile_concurrency = min(
    max_concurrent_trials,
    floor(available_cpu / profile.cpus),
    floor(available_memory / profile.memory_bytes),
    floor(available_pids / profile.pids_limit),
)
```

实际实现应处理 `null`/unsupported daemon capacity，并在无法证明容量安全
时拒绝启动，而不是无界并发。Planner 将计算出的数量传给 Harbor Job 的
`n_concurrent_trials`；Harbor `n_attempts` 固定为 1。不同 profile 之间的
排队由 Infra Scheduler 管理。

### 6.5 TrialEnvelope

每个 Trial 必须写一个通用 envelope。建议 schema：

```json
{
  "schema_version": "trial-envelope.v1",
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
  "resource_profile": {
    "name": "...",
    "config_sha256": "sha256:...",
    "cpus": null,
    "memory_bytes": null,
    "memory_swap_bytes": null,
    "pids_limit": null,
    "shm_size_bytes": null
  },
  "lifecycle": {
    "status": "completed",
    "started_at": "...",
    "finished_at": "...",
    "return_code": 0,
    "retry_index": 0
  },
  "container": {
    "id": "...",
    "name": "...",
    "inspect_path": "...",
    "resource_series_path": "...",
    "cleanup_path": "..."
  },
  "failure": null,
  "artifacts": [],
  "domain_result_path": null
}
```

Harbor 的原生 result 可以作为 `harbor-result.json` 保存，但不能覆盖
`TrialEnvelope` 或旧仓库的 schema-v5 per-record result。

### 6.6 OOM 与资源失败

执行结束时必须同时读取 Docker inspect、stats summary 和可用的 cgroup 事件。
最少保存：

- `State.ExitCode`；
- `State.OOMKilled`；
- `HostConfig` 的实际 cpus、memory、memory-swap、pids 和 shm；
- memory peak、memory limit、PID peak；
- cgroup `memory.events` 中的 `oom`/`oom_kill`（可读取时）；
- stdout、stderr、cleanup 和 Docker event evidence。

建议 failure code：

```text
container_oom
container_resource_limit
host_capacity_exhausted
container_pids_exhausted
container_shm_exhausted
container_storage_exhausted
container_cleanup_failed
```

`exit code 137` 单独不足以判定 OOM。只有 `OOMKilled=true`、cgroup event 或
明确的 Docker daemon evidence 才能给出 `container_oom`；否则记录
`nonzero_exit` 并保留原始证据。

OOM 默认不可自动 retry，除非 experiment config 明确允许该 failure code。这样
可以避免在同一容量不足的 Docker Desktop VM 上重复制造 OOM。

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

- Python `3.12.x`；Harbor 0.4.0 的参考 lock 使用 `3.12.13`；
- uv `>=0.8.4`，当前 `0.11.7` 可以使用；
- Git、curl、jq；
- Docker Desktop 或 Docker Engine，Docker CLI 和 Compose v2 可用；
- Docker daemon 开启 memory、swap、CPU quota 和 PID limit 能力；
- 至少一个可执行的 Linux image，目标 platform 与本机 daemon 一致；
- 如果构建 OpenClaw 9.5 image，image 内 Node 必须满足 OpenClaw 的 engine；
- provider credentials 只通过本地环境、credential helper 或未跟踪 `.env` 注入。

新仓库不要求宿主机安装 OpenClaw、VGB package 或化学科学依赖。领域 verifier
仍由旧 benchmark 的隔离运行时负责。

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
新仓库不能依赖未锁定的 latest CLI；项目依赖必须固定到 Harbor 0.4.0 及
参考源码使用的 commit：

```text
9e156f1f8f05d5d531a29fd300df21aa53b2226e
```

`pyproject.toml` 应使用等价的 Git pin（具体 PEP 508 写法由 uv 生成并验证），
而不是只写一个浮动版本。初始依赖可从 Harbor 0.4.0 `pyproject.toml` 对照：

```toml
[project]
requires-python = ">=3.12,<3.14"
dependencies = [
  "harbor @ git+https://github.com/laude-institute/harbor.git@9e156f1f8f05d5d531a29fd300df21aa53b2226e",
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
中记录最终版本。不要把旧仓库的大型化学依赖复制到 Infra。

安装和验证：

```bash
cd /Users/xutao/harbor-agent-infra
uv lock
uv sync --dev
uv run harbor --version
uv run python -c 'import harbor; print(getattr(harbor, "__version__", "import-ok"))'
docker info
docker compose version
```

如果需要独立验证官方 CLI，也可以执行：

```bash
uv tool install harbor==0.4.0
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

- Harbor Framework：[https://github.com/laude-institute/harbor](https://github.com/laude-institute/harbor)
- Harbor Framework 文档：[https://harborframework.com/docs](https://harborframework.com/docs)
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
- VGB hidden release/runtime；
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
- Harbor 0.4.0 pinned import smoke。

验收：

```bash
uv sync --dev
uv run hai doctor
uv run harbor --version
docker info
```

### Phase 1：通用 contracts 和资源配置

交付：

- `ExperimentSpec`、`ImageSpec`、`ResourceProfile`、`TrialEnvelope`、
  `AgentAdapter`、`DomainBridge`；
- YAML loader、unknown-field/范围校验、配置 hash；
- capacity snapshot 和 profile-aware admission planner；
- test fixture，不提供代码默认资源值。

验收：

- 合法 profile 可 round-trip；
- 缺失 profile、负数、swap 小于 memory、未知字段和 tag-only image 都被拒绝；
- 资源计算使用 fixture capacity，不启动容器也能确定性测试；
- 修改 YAML 后 hash 和 run manifest 变化，旧 manifest 不被重写。

### Phase 2：Harbor Job/Trial 和 Fake Adapter

交付：

- `BenchDockerEnvironment`；
- Job config materializer；
- fake agent adapter 和 fake image；
- Harbor result 与通用 envelope 分离；
- 资源 flags、labels、mounts、cleanup 和 OOM evidence。

验收：

- 一条 fake Trial 能完成并清理；
- Docker inspect 证明实际 resource config 等于 profile；
- workspace、stdout、stderr、trial envelope 和 cleanup evidence 可读；
- 中断不会留下 owned container；cleanup 失败会阻止后续调度。

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

### Phase 4：VGB Domain Bridge

交付：

- 四个 Track 的 allowlist 和 canonical identity；
- `agent-trial-input.v1` 到现有 VGB bridge 的映射；
- VGB evaluator 调用、release identity 和 schema-v5 result projection；
- 不复制第二套 scorer。

验收：

- 四个 Track 各选一条固定 fixture，输入 prompt 与旧仓库一致；
- Harbor backend 输出可被旧仓库 `ResultSink` 和 dashboard 读取；
- 固定 agent-output fixture 下，新旧 backend 的 scoring、status axes 和
  observability projection 等价；
- Harbor 原生 result 不进入 `results.json` 的 score 字段。

### Phase 5：Registry 和真实 provider 对照

只有前四阶段通过后执行：

- Harbor Registry digest pull/push（如环境具备 Registry）；
- OpenClaw provider preflight；
- 四个 Track 各一条真实题目；
- 并发、retry、取消和 OOM 对照；
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
- Docker capacity snapshot 和按 CPU/memory/PID 的准入计算；
- ImageSpec tag-only、digest mismatch、platform mismatch 和 pull policy；
- Harbor Job config `n_attempts=1`、`retry.max_retries=0` 和 profile concurrency；
- Trial identity、retry identity 和 labels；
- Adapter protocol discovery 和 source hash；
- OpenClaw `agentId/sessionKey/sessionId` 生成；
- `agents.entries` 配置投影；
- result/evidence precedence；
- secrets redaction 和 path containment；
- cancellation、cleanup failure 和 orphan recovery；
- Harbor result 与 domain result 的隔离。

### 10.3 Docker integration 测试

使用 fake image，避免 provider 和 VGB hidden runtime：

1. 正常退出：返回 `agent-output.v1`，写 trial envelope，清理容器。
2. 非零退出：保留 stdout/stderr，映射 typed failure。
3. 超时：发送终止信号，保存 cleanup evidence，不重复创建 container。
4. OOM：在受控小容器中触发 memory limit，确认 `container_oom`、
   `OOMKilled` 和不自动 retry。
5. PID：创建超过 profile 的子进程，确认 PID 资源 evidence。
6. 并发：两个 profile 同时排队，证明总预算不超出 fixture capacity。
7. 取消：SIGINT 后不再启动新 Trial，已运行 Trial 在有限窗口内清理。
8. image digest：本地 tag 被改写时，digest 校验阻止运行。

测试必须使用临时目录和专用 labels，不能删除用户已有的 benchmark container。

### 10.4 VGB 对照验收

必须在旧仓库和新仓库之间做固定输入对照：

- 四个 Track 各一条固定 record；
- 相同 prompt、model、image digest 和 resource profile；
- 相同 OpenClaw session identity 规则；
- 只比较现有结果契约和证据，不比较 Harbor 原生显示文本；
- 记录旧 backend/new backend 的 result JSON、score、status axes、resource
  summary 和 image digest。

对照失败时，不得为了“对齐”修改旧仓库 scorer 或放宽新仓库 evidence gate。

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
- Agent Adapter、Domain Bridge、Image Manager、Resource Admission 和 Harbor
  Environment 具有独立 contract；
- Harbor 原生 result 没有替换旧仓库结果 schema；
- VGB hidden runtime 不进入 agent image 或 container mount；
- 一个 Trial/container 只有一个生命周期 owner。

### C. 资源和 OOM

- 资源 profile 只能从外部配置读取，代码没有生产默认值；
- 缺失或非法 profile 会在 Trial 分配前失败；
- actual Docker inspect 与 profile 一致；
- admission 证明 CPU、memory 和 PID 总预算不超过配置容量；
- OOM、PID、shm、storage 和 cleanup failure 有稳定 typed evidence；
- OOM 默认不盲目 retry；
- run manifest 保存 profile snapshot、capacity snapshot 和 config hash。

### D. 生命周期和证据

- fake image 的正常、失败、超时、取消、OOM、cleanup failure 测试通过；
- retry 使用新的 Trial/attempt/workspace/session identity；
- OpenClaw trajectory export identity 可追溯；
- stdout/stderr、image evidence、resource series、cleanup、trial envelope 和
  domain result 路径完整；
- orphan recovery 不会删除 owner 仍存活或身份无法证明的容器。

### E. VGB 兼容性

- 四个 Track 的固定对照 fixture 全部通过；
- 新 backend 的 VGB result 可被旧仓库 `ResultSink` 和 dashboard 读取；
- score、status axes、schema-v5 observability 和 historical read contract
  不变；
- 旧仓库不需要为了 Harbor backend 修改评分公式、Track identity 或 gold answer。

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
2. 写入 Harbor 0.4.0 commit pin 与 `uv.lock`；
3. 实现 `hai doctor` 和 resource loader；
4. 创建 fake image、fake adapter 和 unit/contract tests；
5. 先通过 provider-free Docker acceptance；
6. 再实现 `adapters/openclaw/`；
7. 最后接入 VGB domain bridge 和四 Track 对照。

任何真实 provider run、Harbor Registry 部署、删除旧容器或修改现有 benchmark
仓库，都必须在对应工作说明中单独记录，不得为了通过 fake smoke 擅自改变旧
仓库的结果契约。
