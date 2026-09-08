# Benchmark Infra 单一 LLM Attempt 容器化改动计划与接口设计

状态：`PROPOSED`

日期：2026-09-08

适用范围：OpenClaw benchmark single-LLM runner

## 1. 摘要

本计划将每个 single-LLM agent attempt（一次 record 的一次执行，包括 timeout
retry）从宿主机 subprocess 迁移到一个临时 Docker 容器中。现有 attempt workspace、
session、transcript、审计、归档、取消和结果契约继续保留；容器只替换 agent 执行边界。

本计划不改造 ChemQA，不改变 `verifier-grounded-benchmark` 的对外接口、题目内容、
固定 release、评分公式或 evaluator 结果形状。VGB hidden runtime 和评分继续由宿主
orchestrator 通过现有 `benchmarking.runtime.vgb_bridge` 调用。

已确认的产品决策：

- 首期使用 Docker Engine/Desktop；
- 每个 single-LLM attempt 一个临时容器；
- 使用资源感知的 attempt-level 并发调度；
- 默认使用 `network_mode=host` 以兼容现有 provider 和代理；
- `skills-on` 与 `skills-off` 使用完全相同的基础镜像；
- 两个实验组都默认允许通过白名单 registry 按需安装依赖；
- 不执行 skill health check，不过滤技能列表；
- `skills-on` 返回完整 benchmark skill routing inventory，`skills-off` 不暴露技能路由和 source；
- ChemQA 保持现有宿主机执行方式。

## 2. 目标与非目标

### 2.1 目标

1. 为每个 single-LLM attempt 提供独立的 filesystem、process、依赖和清理边界。
2. 保留现有 workspace lease、workspace audit、transcript recovery 和 fail-closed 语义。
3. 将并发调度单位从 group 提升为 attempt，同时受 CPU、内存、PID 和 provider 限制约束。
4. 让 skills-on/off 的唯一实验差异是技能 source 和路由是否暴露，而不是镜像或预装依赖差异。
5. 保留完整的容器、依赖、资源、退出和清理证据，支持恢复和事后审计。

### 2.2 非目标

- 不容器化 ChemQA coordinator、reviewer 或 judge。
- 不把 VGB wheel、hidden verifier、scorer source 或 hidden task data 放入 agent 容器。
- 不改变 `vgb_bridge`、`evaluate_answer`、`load_public_reference_answers` 或 evaluator 公共接口。
- 不把 Docker host network 宣称为完整网络隔离。
- 不在本阶段实现通用 OCI runtime 抽象、Podman 支持或无网络 provider gateway。
- 不使用 skill health check 结果决定技能是否进入路由表。

## 3. 当前实现与改造边界

当前 `benchmarking.runtime.agent_workspace.AttemptWorkspaceManager` 已负责 workspace
模板、lease、sentinel、审计、seal 和 archive；`single_llm` runner 已负责 OpenClaw
调用、timeout retry、候选答案契约、session postflight 和依赖 manifest。改造应保持这些
职责，通过新的 container runtime 替换宿主机 OpenClaw subprocess。

当前 `benchmarking.workflow.cli` 按 group wave 使用 `ThreadPoolExecutor`。改造后 group
仍用于配置、展示和聚合，但实际 admission 单位变为 single-LLM attempt。

当前 skills runtime 拥有 benchmark skill inventory、health checks 和固定入口。改造后
single-LLM 容器路径取消 health filtering，但保留 inventory projection、受控 source 暴露
和 post-run diagnostics。

## 4. 目标架构

```text
Benchmark CLI
  ├─ dataset/run state/progress
  ├─ AttemptAdmissionController
  └─ SingleLLMRunner
       ├─ AttemptWorkspaceManager.prepare()
       ├─ ContainerRuntime.create/start()
       ├─ container: OpenClaw + wrapper + agent tools
       ├─ collect stdout/session/transcript/spool
       ├─ WorkspaceAccessPolicy + transcript audit
       ├─ seal workspace archive
       └─ ContainerRuntime.stop/kill/remove()
              ↓
        existing RunnerResult
              ↓
        host-side evaluator / vgb_bridge
```

一个 attempt 的容器、workspace、session、venv/cache 和 result spool 必须具有一一对应的
ownership identity：

```text
(run_id, invocation_id, group_id, record_id, attempt_index, agent_id, session_id)
```

任何 retry、恢复或取消后的新执行都必须使用新的 `attempt_index` 和新的容器。

## 5. 改动计划

### 5.1 新增 Docker runtime 层

新增 `benchmarking/runtime/container_runtime.py`，负责：

- Docker daemon 可用性和版本探测；
- image reference/digest 校验；
- container create/start/inspect/stats/logs；
- stop -> kill -> remove 生命周期；
- labels 和 attempt identity 校验；
- ownership-safe orphan recovery；
- Docker 错误、OOM、signal、timeout 和 daemon failure 结构化映射。

Docker socket 只由宿主 orchestrator 使用，绝不挂载到 agent 容器内。

### 5.2 改造 single-LLM runner

修改 `benchmarking/workflow/runners/single_llm.py`：

- 保留当前 prompt、answer extraction、timeout retry、transcript recovery 和审计流程；
- 将 OpenClaw 执行由宿主 `subprocess.Popen` 改为容器内 wrapper 执行；
- 在读取 stdout、session、transcript 和 audit 证据之后再 seal workspace；
- 在所有成功、失败、timeout、取消和异常路径清理容器；
- 将容器退出信息合并到既有 `runner_meta` 和 execution error，不新增对外结果类型；
- VGB record 仍只把最终候选答案交给宿主 evaluator。

### 5.3 统一镜像策略

维护一个 single-LLM 基础镜像，`skills-on` 和 `skills-off` 使用相同 image digest。基础镜像
只包含：

- Python、Node.js、`uv`；
- OpenClaw runtime 和 benchmark wrapper；
- 基础 shell、证书和通用系统工具；
- 结果解析、session/transcript 处理所需依赖。

基础镜像不预装 benchmark skill 专用依赖。两个实验组都默认允许 agent 通过白名单 registry
按需安装依赖；安装结果不跨 attempt 复用。

### 5.4 移除 skill health filtering

修改 `benchmarking.skills`、`benchmarking.workflow.experiments`、`config_pool` 和 CLI：

- 不再在 run startup 执行 skill health check 作为路由前置条件；
- 不再根据依赖、API 或外部工具检查结果裁剪 `skill_allowlist`；
- `skills-on` 直接使用完整 benchmark skill routing inventory；
- `skills-off` 保持 `skills: []` 且不开放技能 source 或 `scripts/run_skill.py`；
- 缺依赖、provider 不可用和技能执行失败改为 attempt-level execution diagnostics。

建议将启动产物从 `skill-health.json` 改为 `skill-routing-inventory.json`。若保留旧文件名
用于历史兼容，必须把内容和 schema 明确改成 routing inventory，不得继续写入未经验证的
health 结论。

### 5.5 引入 attempt-level admission

新增 `benchmarking/runtime/attempt_admission.py`，根据以下信息做资源 admission：

- Docker 可用 CPU 和内存；
- attempt 的 CPU、memory、PID 配置；
- 当前运行容器数；
- provider/API 并发上限；
- 宿主磁盘和 archive 空间；
- 用户配置的最大 attempt 并发。

group 仍保留为展示和结果聚合单位；retry 重新进入 admission queue。现有
`--max-concurrent-groups` 作为兼容参数保留，新增 `--max-concurrent-attempts`，并在
runtime manifest 中记录最终生效的调度策略。

### 5.6 取消、崩溃恢复和 orphan GC

取消时停止新 admission，并按以下顺序处理活动容器：

```text
run cancelling
  -> stop container
  -> grace timeout 后 kill
  -> collect logs/session/audit evidence
  -> seal/archive workspace
  -> remove container
  -> write cancelled/cancelled_with_errors
```

宿主异常退出后，启动恢复流程根据 Docker labels 找到 benchmark-owned 容器；只有通过 run、
invocation、attempt identity 和 workspace sentinel 校验的容器才允许自动 stop/remove。
无法证明归属的容器必须保留并报告。

## 6. 接口设计

以下接口为内部 Python 接口草案，不改变 benchmark 对外 CLI 结果和 VGB API。

### 6.1 ContainerAttemptSpec

```python
@dataclass(frozen=True)
class ContainerAttemptSpec:
    identity: AttemptIdentity
    image: str
    command: tuple[str, ...]
    environment: Mapping[str, str]
    mounts: tuple[ContainerMount, ...]
    network_mode: str = "host"
    cpu_limit: float | None = None
    memory_limit_bytes: int | None = None
    pids_limit: int | None = None
    timeout_seconds: float | None = None
    stop_grace_seconds: float = 10.0
    labels: Mapping[str, str] = field(default_factory=dict)
```

约束：

- `identity` 必须完整且与 workspace sentinel 一致；
- `image` 在启动前解析为 immutable digest；
- `mounts` 只能来自 runtime policy 生成器；
- command、environment 和 labels 不得包含 provider secret 明文；
- network mode 目前只允许 `host`，后续可扩展为受限 bridge profile。

### 6.2 ContainerMount

```python
@dataclass(frozen=True)
class ContainerMount:
    source: Path
    target: PurePosixPath
    mode: Literal["ro", "rw"]
    kind: Literal["workspace", "input", "config", "session", "spool"]
```

创建前必须验证：

- source 为已解析的受控路径；
- target 位于固定 `/benchmark/...` 前缀；
- `input` 和 `config` 只能为 `ro`；
- 不允许 workspace、output 或 project root 的递归扩大挂载；
- 不允许 source 为 symlink、Docker socket 或未知 special file。

### 6.3 ContainerAttemptHandle

```python
@dataclass(frozen=True)
class ContainerAttemptHandle:
    container_id: str
    container_name: str
    identity: AttemptIdentity
    image_digest: str
    started_at: str
    labels: Mapping[str, str]
```

### 6.4 ContainerAttemptResult

```python
@dataclass(frozen=True)
class ContainerAttemptResult:
    handle: ContainerAttemptHandle
    return_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool
    oom_killed: bool
    inspect: Mapping[str, Any]
    stats: Mapping[str, Any]
    cleanup: Mapping[str, Any]
```

`ContainerAttemptResult` 只作为 runner 内部输入；runner 最终仍返回现有 `RunnerResult`。

### 6.5 ContainerRuntime Protocol

```python
class ContainerRuntime(Protocol):
    def check_ready(self) -> RuntimeAvailability: ...

    def create(self, spec: ContainerAttemptSpec) -> ContainerAttemptHandle: ...

    def start(self, handle: ContainerAttemptHandle) -> None: ...

    def collect(
        self,
        handle: ContainerAttemptHandle,
        *,
        timeout_seconds: float | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ContainerAttemptResult: ...

    def stop(self, handle: ContainerAttemptHandle, *, grace_seconds: float) -> None: ...

    def kill(self, handle: ContainerAttemptHandle) -> None: ...

    def remove(self, handle: ContainerAttemptHandle, *, force: bool = False) -> CleanupReport: ...

    def recover_orphans(self, *, owner: AttemptOwner) -> list[CleanupReport]: ...
```

`ContainerRuntime` 的所有实现都必须保证 remove 前已经尝试 collect；如果 collect、audit 或
archive 失败，必须保留诊断并让 attempt fail closed。

### 6.6 AttemptAdmissionController

```python
class AttemptAdmissionController(Protocol):
    def acquire(self, spec: ResourceRequest) -> AdmissionLease: ...

    def release(self, lease: AdmissionLease) -> None: ...

    def capacity_snapshot(self) -> CapacitySnapshot: ...

    def cancel_pending(self, reason: str) -> int: ...
```

`AdmissionLease` 必须在容器创建前获得，在容器 remove 后释放；任何异常路径都必须释放 lease。

### 6.7 SkillRoutingInventory

```python
@dataclass(frozen=True)
class SkillRoutingInventory:
    schema_version: int
    skills: tuple[SkillRouteEntry, ...]
    inventory_sha256: str


@dataclass(frozen=True)
class SkillRouteEntry:
    skill_id: str
    source_path: str
    container_source_path: str
    route_metadata: Mapping[str, Any]
    manifest_digest: str
```

该接口只表达路由和 source inventory，不表达“技能健康”“依赖可用”或“外部服务可达”。
`skills-on` 使用完整 inventory；`skills-off` 使用空 inventory。

### 6.8 DependencyInstallPolicy

不设置 per-run profile 开关；策略是 single-LLM 默认运行时契约：

```python
@dataclass(frozen=True)
class DependencyInstallPolicy:
    enabled: bool = True
    allowed_indexes: tuple[str, ...] = ("https://pypi.org/simple",)
    allow_direct_urls: bool = False
    allow_local_paths: bool = False
    allow_editable: bool = False
    deny_distributions: frozenset[str] = frozenset({"verifier-grounded-benchmark"})
```

两个实验组必须使用同一个 policy digest。每个 attempt 记录 install events、freeze、
distribution inventory、RECORD hash 和 replay requirements。

## 7. 容器内文件与环境契约

容器内固定路径：

```text
/benchmark/workspace       # 当前 attempt workspace，rw
/benchmark/input           # 当前公开 input bundle，ro
/benchmark/config          # run-scoped OpenClaw config，ro
/benchmark/session         # 当前 session store，rw
/benchmark/result-spool   # stdout/stderr/metadata，rw
/tmp                       # tmpfs
```

agent-facing prompt 应继续使用相对 `scratch/...` 路径；不得要求模型复制包含 run、invocation、
record 或 session slug 的宿主绝对路径。

环境变量至少包括：

- `OPENCLAW_CONFIG_PATH=/benchmark/config/openclaw.json`；
- `BENCHMARK_ATTEMPT_WORKSPACE=/benchmark/workspace`；
- `BENCHMARK_ATTEMPT_PYTHON`；
- `BENCHMARK_ATTEMPT_UV_CACHE`；
- `BENCHMARK_RESULT_SPOOL=/benchmark/result-spool`；
- `BENCHMARK_PYPI_CUTOFF`；
- 当前 attempt/session identity 的非敏感字段。

provider token、proxy credential 等 secret 只通过受控环境注入，不写入 image、sentinel、
label、prompt 或 manifest 明文。

## 8. 产物与 schema 设计

现有结果文件继续保留，并增加：

```text
<run-output>/
  runtime-manifest.json
  skill-routing-inventory.json
  container-manifest.json
  container-events.jsonl
  container-spool/
    <group>/<record>/<attempt>/
      stdout.log
      stderr.log
      inspect.json
      stats.json
      cleanup.json
  agent-workspace-archives/
```

`runtime-manifest.json` 新增字段组：

```json
{
  "container_runtime": {
    "backend": "docker",
    "client_version": "...",
    "server_version": "...",
    "network_mode": "host",
    "image_digest": "sha256:...",
    "security": {
      "non_root": true,
      "cap_drop_all": true,
      "no_new_privileges": true,
      "privileged": false
    },
    "resource_limits": {
      "cpus": 1.0,
      "memory_bytes": 0,
      "pids": 256
    }
  },
  "skill_routing_inventory": {
    "schema_version": 1,
    "sha256": "...",
    "health_check_applied": false
  },
  "dependency_install_policy_digest": "..."
}
```

每个 per-record runner metadata 增加 container identity、exit/OOM/timeout、image digest、
resource usage 和 cleanup report，但不改变 `RunnerResult` 顶层结构。

## 9. 测试与验收

### 9.1 单元测试

- Docker adapter 的 create/start/collect/stop/kill/remove；
- image digest 和 labels identity 校验；
- mount allowlist、只读属性和禁止路径；
- non-root、capability、seccomp、CPU/memory/PID 参数；
- timeout、OOM、signal、daemon unavailable、image missing；
- orphan recovery 只处理 identity 匹配容器；
- admission acquire/release、排队、取消和资源不足；
- install policy 白名单、denylist 和审计 manifest；
- routing inventory 完整性和 deterministic digest；
- skills-on 不执行 health filtering；skills-off 使用空 inventory。

### 9.2 集成测试

- single-LLM attempt 在测试 Docker image 中完成并返回现有 `RunnerResult`；
- retry 产生新的容器、workspace、session 和 archive；
- 容器看不到其他 attempt、run、project root 或 VGB hidden runtime；
- container remove 后 transcript audit 仍可完成；
- cancel 不遗留 benchmark-owned running container；
- skills-on/off 使用相同 image digest、install policy 和资源限制；
- 两个组都可以从白名单 registry 按需安装依赖；
- 缺依赖和技能运行错误保留为执行诊断，不导致启动前路由过滤；
- VGB evaluator 仍只在宿主调用 `vgb_bridge`。

### 9.3 回归测试

至少运行：

- `tests/test_single_llm_session_wrapper.py`
- `tests/test_single_llm_timeout_retry.py`
- `tests/test_attempt_environment.py`
- `tests/test_agent_workspace.py`
- `tests/test_benchmark_cancellation.py`
- `tests/test_benchmarking_orchestration.py`
- `tests/test_vgb_bridge.py`
- `tests/test_benchmark_result_contract.py`

验收条件：

1. 每个 single-LLM attempt 都有独立临时容器。
2. skills-on/off 基础镜像、安装权限、资源配置和网络策略一致。
3. skills-on 暴露完整 routing inventory，skills-off 不暴露技能 source/routing。
4. 不再通过 health check 改变技能列表。
5. VGB 对外接口和评分行为保持不变。
6. timeout、取消、OOM、Docker failure 和技能执行失败都有结构化证据。
7. workspace、container、session、archive 清理可恢复、可审计。

## 10. 分阶段迁移

### Phase 0：后端兼容层

- 新增 Docker runtime adapter；
- 保留 `host|docker` 执行后端切换；
- 完成 fake Docker client、mount/security、manifest 和故障注入测试；
- 不改变现有默认运行行为。

### Phase 1：single-LLM skills-off 灰度

- 统一基础镜像；
- skills-off 使用 Docker；
- 验证 provider、proxy、session、workspace archive 和 cancellation；
- 对比 host 与 Docker 的结果和诊断。

### Phase 2：skills-on 完整路由

- 移除 health filtering；
- 写入完整 `skill-routing-inventory.json`；
- 受控只读挂载完整 benchmark skill source；
- 验证 skills-on/off 依赖和镜像完全一致。

### Phase 3：attempt-level 并发

- 启用 admission controller；
- 调整 Docker Desktop CPU/memory/provider 上限；
- 启用 orphan recovery/GC；
- Docker 成为 single-LLM 默认 backend。

## 11. 风险与操作约束

- `network_mode=host` 保留 provider 兼容性，但降低网络隔离强度，必须在 manifest 和文档中显式声明。
- Docker Desktop 虚拟机资源上限可能成为并发瓶颈，不能仅以宿主 CPU 核数决定并发数。
- 默认允许按需安装会引入网络时延和 registry 波动；安装证据必须完整记录，且不复用跨 attempt cache。
- 不做 health filtering 后，技能依赖缺失会在运行期暴露，失败应作为诊断而不是启动前排除条件。
- Docker daemon 崩溃或宿主进程异常时，不能删除无法确认归属的容器。
- 当前容器边界增强了隔离，但仍不能等同于完整的多租户或主动对抗安全边界。

## 12. 实施后文档同步

实现完成后必须更新 `GLOBAL_DEV_SPEC.md` 的当前实现章节，反映：

- single-LLM attempt 使用临时 Docker 容器；
- container lifecycle、mount/security 和 cleanup 契约；
- attempt-level admission；
- skills-on/off 的统一基础镜像和按需安装规则；
- health filtering 已移除，skills-on 使用完整 routing inventory；
- VGB 评分仍位于宿主隔离 runtime；
- 新增 container manifest、spool 和 routing inventory 产物。

本文件保留为设计与实施计划，不替代 `GLOBAL_DEV_SPEC.md` 对已实现系统的描述。
