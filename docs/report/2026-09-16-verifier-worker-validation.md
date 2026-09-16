# Verifier Worker Validation

日期：2026-09-16
状态：`PINNED_RELEASE_ACCEPTED_EXPERIMENTAL`
范围：RT-04 第二步；代码基线 `24a998b`，默认评分路径仍为 isolated。

## 实现与设计审查

按照 [设计](../design/2026-09-16-verifier-worker-design.md)，新增
`--verifier-mode isolated|worker`，默认 isolated。worker 使用同一 pinned API body，
每题加载 track，保留完整评分结果。CLI 创建 invocation-owned worker，并在调度结束、
取消和外层异常退出时关闭。每 100 个成功请求回收；故障后最多允许两次后续请求重启，
失败题不自动重放。compatibility/fallback 是显式选择 isolated，不以自动回退掩盖故障。

只读设计审查指出 registry 对已退出 leader 的后代清理不足、队列会等待评分线程、
native stdout 可能污染协议等问题。实现采用直接清理 owned process group、I/O/锁
等待轮询取消及 FD 层 stdout/stderr 分离。测试覆盖这些具体边界。

16 MiB 帧上限明确报错，不截断答案形成分数。生命周期 JSONL 记录 stable code、请求
identity/hash、generation、PID 及 stderr 路径。失败 traceback 和完整 stderr 在磁盘保留。
库返回的 failure type/message 完全保留；worker 自身崩溃/超时/协议错误有新的 typed
诊断，不能称为与旧 subprocess traceback 逐字相同。取消仍是 BenchmarkCancelledError。

## 验证

先运行现有基线定向测试：47 passed、2 skipped。
实现后定向测试：80 passed、2 skipped。
完整 `uv run pytest -q`：972 passed、11 skipped、164 subtests，5 个既有 SWIG warnings。

新增 30 个案例使用真实 fixture venv 子进程，覆盖完整 result 与 evaluator 错误消息
等价、交错 track、A→B→A、成功/失败混合、重复请求、回收、invocation 隔离、故障后的
下一请求恢复、重启上限、启动失败、非法 JSON/identity、截断帧、不读 stdin、大请求与
响应上限、native/Python 输出隔离、wheel/manifest/Python 变化、取消及等待锁时取消、
重复关闭、cleanup failure、leader 崩溃后子进程清理，以及真实 CLI 默认/opt-in 接线。

## 离线 shadow 与性能

命令：`uv run python scripts/benchmark_vgb_worker.py --records 120 --repeats 3`。
证据：[shadow-report.json](../../state/benchmark-runs/temporary/vgb-worker/offline/vgb-worker-offline-20260916-162555/shadow-report.json)。
每个 mode/repetition 为独立测量进程，交替顺序，包含 100 请求回收边界；所有六份
输入 hash、完整输出 hash 和输出字节数相等。固定科学 failure payload 也参与对比。

| 指标（三轮中位数） | isolated | worker |
| --- | ---: | ---: |
| 120 次 wall time | 4.698 s | 0.114 s |
| 首题 latency | 40.006 ms | 34.085 ms |
| 单题 latency 中位数 | 38.152 ms | 0.302 ms |
| verifier processes | 120 | 2 |
| parent peak RSS | 26,869,760 B | 26,968,064 B |
| largest reaped child peak RSS | 23,904,256 B | 24,100,864 B |
| result bytes | 39,588 | 39,588 |

RSS 来自独立进程的 getrusage high-water mark；child 数值不是所有后代同时 RSS 之和。
这是轻量确定性 fixture 的 IPC/import 收益，不代表真实化学计算加速比例。

## Pinned v0.9.2 shadow acceptance

本机 pinned v0.9.2 wheel、manifest 和 runtime Python 已重新验证。固定请求由历史
RDKit/xTB 答案及 pinned public property gold 物化，不调用模型。请求覆盖 rdkit、xtb、
property_calculation_basic、property_calculation_advanced，包含重复、乱序、A→B→A、
成功→失败→成功及 100 次回收边界。只有便宜请求循环到 104 条，每个测量进程只执行
一次成功和一次 parse-failure xTB 请求。

```bash
uv run python scripts/benchmark_vgb_worker.py \
  --release-config benchmarking/resources/verifier_grounded/release.json \
  --requests state/benchmark-runs/temporary/vgb-worker/pinned/vgb-worker-pinned-20260916-requests.jsonl \
  --cycle-requests-to 104 --repeats 3
```

证据：[`shadow-report.json`](../../state/benchmark-runs/temporary/vgb-worker/pinned/vgb-worker-pinned-20260916-174145/shadow-report.json)，
同目录保留物化后的完整 `prepared-requests.jsonl`、每轮完整 results、runtime metrics、
worker lifecycle 和 stderr。三轮共 624 个 isolated/worker 响应逐字段比较，score、
properties、constraint scores、failure type、message、versions 和完整响应均无差异；
没有剔除动态字段。每轮 worker 104 次成功请求并在 100 次后回收，process count 为 2。

| 指标（三轮中位数） | isolated | worker |
| --- | ---: | ---: |
| 104 次 wall time | 18.037 s | 13.057 s |
| 首题 latency | 265.99 ms | 262.49 ms |
| 单题 latency 中位数 | 134.49 ms | 86.77 ms |
| verifier processes | 104 | 2 |
| parent peak RSS | 27,459,584 B | 27,344,896 B |
| largest reaped child peak RSS | 67,092,480 B | 67,076,096 B |
| result bytes | 110,648 | 110,648 |

parent cwd 和 environment hash 在每个进程评分前后不变。对 wheel Python 源码的静态
审查发现 module-level `@cache`/`@lru_cache` 以及单次 evaluate 内的 evidence cache，
但未发现评分路径改变 cwd。真实重复/乱序输出相同降低了已选任务的状态残留风险，
不能证明所有 105 个任务或 native 库状态等价。定期回收不构成 native 内存硬上限，
stderr 保留也有磁盘成本，因此默认仍为 isolated。没有付费模型或 Docker run。

本批收尾后的完整测试：`987 passed, 11 skipped, 164 subtests passed`，另有 5 个既有
SWIG deprecation warnings。
