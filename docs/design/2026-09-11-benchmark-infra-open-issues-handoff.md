# Benchmark Infra 迁移遗留问题交接文档

状态：`OPEN`

后续实现：六项均已接入修复及自动化回归，见
[本轮验收记录](2026-09-11-benchmark-infra-fix-validation.md)。交接暂不关闭：
SuperChem 完整模型验收及 VGB 模型安装后评分受到连接失败影响，尚未完成。
下文保留原审查基线；依赖重放 lock 缺失的评分规则以后续验收记录中的用户确认决策为准。

整理日期：2026-09-11

审查基线：`4cdedd7`，分支 `feat/benchmark-single-llm-containerization`

## 1. 委托目标与使用方式

本文供没有前序会话上下文的工程师或 agent 独立接手。目标是重新验证并修复以下六项问题，使 benchmark infra 能支持多模态输入、受控依赖安装、批量调度、取消及崩溃恢复，而不只是跑通少量文本题。

本次交付只整理审查结果，没有修复这些问题。接手者应先检查最新 Git 状态和实现；行号仅定位审查基线，不能代替阅读当前代码。推荐修复方向不是未经验证的最终设计，应先确认模块责任、数据流和状态契约，再实施。

必读文件：

1. `/Users/xutao/.openclaw/workspace/AGENTS.md`
2. `/Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md`
3. 本文
4. `docs/design/infra-fix-handoff.md`：第一轮原始交接范围。
5. `docs/design/infra-fix-validation.md`：第一轮实现及有限验收证据。

代码与文档冲突时，以代码为准。项目根目录为 `/Users/xutao/.openclaw/workspace`，不是上级运行时目录。使用 `uv run ...` 或项目 `.venv` 执行脚本和测试。实现后同步更新当前规范，运行定向和全量测试，通过后提交 Git。不要把待实现设计描述成现状。

## 2. 当前结论与已完成基线

当前状态是“部分真实 benchmark 可以运行，迁移仍未完成”。第一轮将原交接标为 `CLOSED` 过早；本交接重新列出未关闭项。

已经完成、应保留的能力：

- Skill Health 实现、透传参数、writer 字段和专属测试已删除；routing inventory、digest 和技能使用统计仍保留。
- CLI、adapter 和 single-LLM runner 的默认 backend 已统一为 Docker；host fallback 保留。
- 所有 Docker records 使用容器内创建的 `scratch/venv`；host VGB 保留原环境生命周期。
- skills-on/off 使用相同镜像和安装策略入口；skills-on 技能源只读挂载。
- admission 已简化为计数限制，默认 `--max-concurrent-attempts=2`，等待可取消。
- 同组 records 已有独立 agent/config/workspace 身份；每次 retry 重新申请 admission lease。
- Docker daemon/image preflight 和带 ownership 检查的 orphan recovery 已有生产调用。
- 已有真实文本题 skills-on/off 和 VGB property 评分通过的证据。

审查时重新运行：

```text
uv run pytest -q
841 passed, 4 skipped, 5 warnings, 143 subtests passed

BENCHMARK_TEST_CONTAINER_IMAGE=openclaw-benchmark-single-llm:latest uv run pytest -q tests/test_container_attempt_integration.py
4 passed
```

四个 skip 是显式启用的 Docker 集成测试。它们已单独运行通过，但测试范围不覆盖本文中的全部异常路径。通过数量不是迁移完成的充分条件。

## 3. 问题总表

| ID | 优先级 | 问题 | 证据级别 |
| --- | --- | --- | --- |
| INFRA-01 | P1 | 多模态 bundle 的 prompt、挂载与读取策略不一致 | 静态路径核对及 prompt/config 小型复现 |
| INFRA-02 | P1 | orphan 删除后，残留 venv 导致 workspace recovery 失败 | 已复现归档异常；完整真实崩溃链待补测 |
| INFRA-03 | P1 | 不完整依赖证据仍可能标记 complete 并评分 | 已通过子进程替身复现采集函数正常返回 |
| INFRA-04 | P1 | 安装 guard 漏查 requirements，误拦普通 HTTP 命令 | 已调用真实 Node guard hook 复现 |
| INFRA-05 | P2 | executor 仍调度整个 record，未形成真正 attempt queue | 静态调用链确认；吞吐/公平性故障测试待补 |
| INFRA-06 | P2 | Docker 取消、命令超时、清理失败终态未统一 | 静态调用链确认；daemon/删除失败注入待补 |

## 4. INFRA-01：多模态输入的容器路径迁移

### 现状与影响

- `benchmarking/workflow/prompts.py:117,140` 将 `input_bundle.bundle_dir` 和 `question_markdown` 的宿主绝对路径写入 HLE/SuperChem prompt。
- `benchmarking/workflow/runners/single_llm.py:1353` 构造 Docker command 时仍传原 prompt，bundle 实际挂载到 `/benchmark/input`。
- `benchmarking/runtime/config_pool.py:215` 生成的 guard 读取范围包含 workspace 和按需技能目录，没有该 input bundle 范围。
- `benchmarking/runtime/container_runtime.py:309` 的配置投影未补齐 input bundle 的读取策略。

小型复现：传入 bundle `/host/run/input-bundles/r1` 后，prompt 仍提示读取 `/host/run/input-bundles/r1/question.md`；生成的容器 guard read scopes 不包含 `/benchmark/input`。这些宿主路径在容器中不可用，即使模型猜到挂载路径，也可能被 guard 阻止。

### 修复方向与验收

- 明确 host bundle、容器可见 bundle、归档 metadata 的各自路径语义，统一由一处负责投影。
- 检查 `benchmarking/runtime/bundles.py` 生成的 question Markdown、图片引用及内嵌路径，不能只替换 prompt 中一行。
- 把当前 record 的 bundle 加入精确只读范围，保持技能源和 hidden verifier 的边界。
- 保留原始 transcript；在线 audit 使用受控路径映射。对历史读取/replay 做回归，不能用整体字符串替换污染原始证据。
- 真实 Docker 测试必须从 prompt 出发读取题目 Markdown 和图片，覆盖 HLE/SuperChem、skills-on/off；加入禁止读取另一个 record bundle 的反例。
- host 输入 bundle 路径及旧产物读取不回退。

## 5. INFRA-02：崩溃后的环境清理与 workspace 恢复

### 现状与已复现证据

- `benchmarking/runtime/container_runtime.py:301` 对已证明归属且 owner 已退出的容器直接 `rm -f`。
- `benchmarking/runtime/agent_workspace.py:729` 随后直接 seal 遗留 workspace，没有处理未完成的环境收尾。
- 正常完成时 `container_attempt.py` 会清理 venv/cache/plugin links；进程崩溃或强制删除时，该正常收尾可能未执行。

复现方法：使用 `tests/test_agent_workspace.py` 的 `AttemptWorkspaceManagerTests` fixture，prepare 一个合法 lease，在 `scratch/venv/bin/python` 创建指向 `/usr/local/bin/python` 的符号链接，释放 `lease._lock_handle`，再用相同 run、新 invocation 的 manager 执行 `recover_all_incomplete()`。

实际结果：

```text
WorkspaceIsolationError: Benchmark scratch symbolic links must use relative targets.
code=workspace_path_unsafe
reason=absolute_symlink_target
```

该链接是容器 venv 的正常形态，不能通过放宽整个 workspace 的符号链接校验解决。现有 orphan 测试只覆盖简单容器和 sentinel，不包含真实初始化后的 attempt 环境。

### 修复方向与验收

- 设计“证明 ownership/失活 → 停止执行 → 保存现有证据 → 受控环境清理或隔离 → archive”的异常恢复生命周期。
- 明确已退出容器与仍运行孤儿容器的处理差异；无法采集的证据必须标为 unavailable，不能伪造 complete。
- 清理只针对验证后的 runner-owned 环境和可证明来源的缓存；不跟随不可信链接、不扩大 `rm -f` 范围。
- 覆盖 venv/cache/plugin-skill links、部分初始化、重复恢复、身份不匹配和活 owner。
- 新增真实故障测试：独立 orchestrator 创建 attempt 并初始化 venv 后退出，启动下一 invocation，确认恢复能结束且证据/归档可读，无遗留容器或阻塞的 workspace 锁。

## 6. INFRA-03：依赖证据状态与评分门禁

### 现状与已复现证据

- `benchmarking/runtime/attempt_environment.py:163,184,259`：lock 编译非零退出只返回 `unavailable`；inventory JSON 解析失败则置空数组。
- `benchmarking/runtime/container_attempt.py:96`：采集函数没有抛异常时，直接写 `manifest.status="complete"`，未验证 replay lock 或 remediation 状态。
- `benchmarking/workflow/runners/single_llm.py:1047`：仅检查顶层 status；manifest/cleanup JSON 读取也不在统一异常收尾保护中。

复现方法：为 `collect_dependency_manifest()` 注入 subprocess 替身，让 freeze 成功返回 `demo==1.0`，compile 返回非零和 network unavailable，inventory 返回退出码 0 但 stdout 为 invalid JSON。函数正常返回：

```text
replay_lock.status=unavailable
replay_lock.returncode=1
distributions=[]
```

外层据此可写 complete。此复现没有实际安装违规包，也没有证明所有 remediation 失败场景；这些属于下一轮必须补充的测试。

### 修复方向与验收

- 定义证据各阶段的成功/失败/缺失状态，验证 identity、解释器、freeze、inventory、RECORD hash、lock 和依赖审计之间的一致性。
- 明确哪些证据缺失使 attempt 不可评分，哪些仅允许诊断降级；严格落实交接中的可重放依赖证据要求。不能只让 writer 自报 complete。
- forbidden distribution 的发现、移除失败如何影响结果，必须有显式契约，且不能覆盖更早的 provider 错误原始证据。
- 无效或截断的 manifest、权限错误、清理失败均要执行 audit/seal 或可审计的隔离路径，不能跳过收尾。
- 测试覆盖 freeze 失败、compile 失败/超时、非法 inventory、manifest 截断、remediation 失败以及正常一致性。
- 保留 host VGB 原职责，并根据实际影响执行共享函数回归。

## 7. INFRA-04：依赖 guard 的命令识别和间接输入

### 现状与已复现证据

入口：`benchmarking/runtime/openclaw_plugins/benchmark-workdir-guard/index.js:197` 的 `validateAttemptPackageCommand()`。

调用 `tests/test_benchmark_workdir_guard.py` 的 `_run_hook(..., attempt_python=True)`，真实 Node hook 返回：

| 命令 | 当前结果 |
| --- | --- |
| `uv pip install rdkit` | 允许，符合预期 |
| `uv pip install --python=other rdkit` | 阻止，符合预期 |
| `uv pip install -r requirements.txt` | 允许，但没有读取/校验文件内容 |
| `VIRTUAL_ENV=other uv pip install rdkit` | guard 允许；最终安装目标尚需结合 uv 环境实测 |
| `curl https://example.com` | 被 dependency 规则阻止，属于误拦 |

requirements 文件可包含 direct URL、alternate index 或禁止 distribution；当前命令级扫描不能支持“所有明确安装入口都遵守策略”的声明。普通 HTTP 命令则在识别依赖命令之前被 URL 正则误拦。

### 修复方向与验收

- 先按命令/参数识别依赖操作，再执行安装策略检查。不能把所有包含 URL 的 exec 都归为依赖变更。
- 对 requirements/constraints 及递归引用选择明确方案：在受控范围解析验证，或明确拒绝尚不支持的入口。不要未经检查直接放行。
- 检查环境赋值、解释器形式、绝对可执行路径、参数别名和目标覆盖的语义；避免不断追加孤立正则。
- 测试允许的 registry 安装，以及禁止的 URL、本地源、editable、alternate index、target override 和 verifier distribution。
- 普通网络命令仍受现有网络/访问策略约束；修复误拦不等于新增 web search/fetch 能力或改变实验组网络政策。
- skills-on/off 行为一致；保持 cooperative-agent 威胁模型，不扩张为任意恶意代码沙箱项目。

## 8. INFRA-05：record executor 与 attempt queue 的职责

### 当前调用链和影响

```text
CLI ThreadPoolExecutor(max_workers=max_concurrent_attempts)
  -> run_group([record])
    -> runner.run()
      -> acquire -> attempt -> release
      -> retry backoff -> next acquire ...
    -> evaluate_answer_fn() / judge
```

定位：`benchmarking/workflow/cli.py:673,716`、`benchmarking/workflow/orchestration.py:300,307`、`benchmarking/workflow/runners/single_llm.py:1252,1288`。

admission lease 已在 backoff/评分前释放，但 executor worker 没有释放。两个 worker 都在等待 retry 或评分时，即使没有活跃容器，下一条 record 仍不能启动。重试只在原 worker 内循环，没有真正重新进入共享 attempt queue。CLI 还按 group 顺序提交该组所有 records，跨组公平性没有明确保证。

### 修复方向与验收

- 明确 record 生命周期、单次 attempt、retry timer、评分和聚合的调度边界。
- 以单次 attempt 为执行队列单位；retry 保留 record 身份并按既定规则重新入队，等待不占 attempt 执行槽位。
- 评分使用独立且有界的资源路径；避免通过无限增加线程“解决”问题。
- 保留默认并发 2、每题即时持久化、group progress namespace，以及 ChemQA wave/cleanroom 契约。混合组调度顺序若改变，明确说明并验证。
- 新增确定性测试：慢 judge 不阻塞下一 attempt；retry backoff 期间其他题启动；同组和跨组并发受限；取消队列无新启动；重复/乱序完成不丢结果。
- runtime manifest 描述实际存在的调度层，不能将 record executor 命名为 attempt queue 后即宣告完成。

## 9. INFRA-06：取消、Docker 操作时限和清理终态

### 现状与影响

- `benchmarking/runtime/container_runtime.py:229,250`：取消后调用固定 300 秒 grace 的 stop；未随第二次取消缩短，stop/kill 等 Docker 命令缺少外层 subprocess timeout。
- `benchmarking/workflow/runners/single_llm.py:1478`：remove 失败触发 token.cancel 并抛错。
- `benchmarking/workflow/cli.py:798,929`：`cancelled_with_errors` 取决于 `cancellation_errors`，目前收集宿主 process registry/cleanroom 错误，没有统一接入容器删除失败报告。
- Docker 正常超时/取消测试已通过，但这不能证明 daemon 无响应、重复信号和 remove 失败情况下终态正确。

### 修复方向与验收

- 所有 Docker 生命周期命令具有有界外层时限，时限与容器退出 grace 分开定义。
- 保留首次取消原因；后续取消可加速终止。清理不能无限等待 daemon。
- 将 Docker stop/kill/remove outcome 汇入 run 级清理结果，传播到 progress、results 和 runtime manifest。
- 删除失败停止新调度，终态为 `cancelled_with_errors` 或契约定义的明确错误状态，保留容器身份、失败阶段与诊断。
- 不将未清理资源当作正常空闲容量；也不能让 lease 永久卡住导致无法落盘退出。设计一个明确的失败终止策略。
- 测试覆盖 wait/stop/kill/remove 超时、删除失败、重复 SIGINT/SIGTERM、取消与完成竞争、退出码和终态一致性。

## 10. 保持不变的边界

- 不改变 VGB 公共接口、release pin、题目、评分公式或 gold 输出；hidden verifier 不进入 agent 容器。
- 保留 host fallback、ChemQA、judge、cleanroom、历史 archive/replay 和 dashboard 读取。
- 不重新引入 Skill Health，不用健康过滤缩小 skills-on inventory。
- 保留 `RunnerResult`、`EvaluationResult` 和 workspace audit 四轴；不以清理错误抹掉原始 provider 证据。
- 不放宽 workspace 符号链接/受保护路径规则来绕过归档或多模态问题。
- 不直接修改正式数据集、旧评分、live provider 配置或凭据。真实验收使用独立 run 目录，失败证据也保留。

## 11. 实施顺序与验收矩阵

建议先为每项建立失败回归，再按以下顺序实施：

1. INFRA-01：统一输入路径契约，完成真实图片读取。
2. INFRA-03 和 INFRA-04：依赖证据状态及安装策略闭环。
3. INFRA-02 和 INFRA-06：统一异常退出、恢复、清理与取消终态。
4. INFRA-05：把已明确的单次 attempt 生命周期接到真正的共享队列。
5. 更新规范和验收记录，逐项重新审查后关闭交接。

至少执行相关现有测试和新增回归，包括：

```sh
uv run pytest -q tests/test_benchmarking_cli.py tests/test_benchmarking_orchestration.py tests/test_attempt_admission.py tests/test_benchmark_cancellation.py
uv run pytest -q tests/test_container_runtime.py tests/test_attempt_environment.py tests/test_agent_workspace.py tests/test_benchmark_workdir_guard.py
uv run pytest -q tests/test_single_llm_timeout_retry.py tests/test_single_llm_session_wrapper.py tests/test_vgb_bridge.py
uv run pytest -q
```

重建镜像后启用真实 Docker 合约测试，并新增以下端到端场景：

| 场景 | 关键验收 |
| --- | --- |
| HLE/SuperChem 图片题，skills-on/off | 读到实际图片、路径一致、guard/audit 正常 |
| VGB 安装及评分 | 实际 venv、freeze/inventory/lock 一致、宿主 verifier 边界 |
| 真实初始化后 orchestrator 崩溃再启动 | 不误删活跃/不匹配容器，环境残留不阻断恢复 |
| 错误或缺失依赖证据 | 不伪造 complete，评分契约明确，收尾完成 |
| 多题、跨组、慢 judge、retry backoff | attempt 容量可被其他题使用，结果即时落盘 |
| 重复取消、daemon timeout、remove 失败 | 有界退出，禁止新 attempt，终态与清理证据一致 |
| host/ChemQA/judge/历史读取 | 相关回归通过；必要时增加针对共享改动的集成覆盖 |

benchmark 记录放在 `/Users/xutao/.openclaw/workspace/state/benchmark-runs`，run 名称为 `<benchmark>-<single-llm-model>-<timestamp>`。无模型合约测试用 `no-llm` 标记。避免对真实未归属进程/容器做故障注入。

关闭条件：六项均有明确处理结果和对应测试证据，全量及所需真实集成通过；当前规范准确；无本轮遗留资源；代码已提交。供应商故障导致未完成的验收应明确列为未完成，不能用另一个简单场景替代全部覆盖。

## 12. 既有运行证据与 provider 错误说明

第一轮 run 根目录：`state/benchmark-runs/temporary/infra-fix/gpt-5.6-sol/`。

- `infra-fix-gpt-5.6-sol-20260910-02`：skills-off 文本题成功。
- `infra-fix-gpt-5.6-sol-20260910-04`：skills-on 文本题成功。
- `infra-fix-vgb-gpt-5.6-sol-20260910-05`：RDKit 安装和计算执行过，随后 provider 错误。
- `infra-fix-vgb-gpt-5.6-sol-20260910-07`：VGB property、thinking off，完成评分约 0.67。

第 05 次的原始错误为：

```text
provider=openai api=openai-responses model=gpt-5.6-sol
status=400 code=null type=upstream_error
message=400 Upstream request failed
```

`openai` 是配置名称，endpoint 来自 `${SU8_BASE_URL}`，不能据此认定官方 OpenAI 直接返回。`provider_request_invalid` 是 benchmark 分类；“provider rejected the request schema or tool payload”是 OpenClaw 分类描述。上游没有给出具体字段原因，根因未确认。该问题与本文六项独立，不应当作为跳过 infra 修复的理由，也不属于本次已验证的修复目标。

无需创建新会话、委托子代理或启动修复来使用本文；将本文路径提供给后续会话并明确授权实施即可。
