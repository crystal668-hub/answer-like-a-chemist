# Benchmark Runtime Phase 3 Validation

日期：2026-09-16

状态：`ACCEPTED_OFFLINE_WITH_LIMITS`

范围：验证 RT-03 有界结果内存、单遍增量聚合和 canonical per-record 流式结果写出。

实现保持 `GroupRecordResult` 持久化字段完整。attempt 结果写入后，运行态仅保留
record identity、状态、score、错误和 canonical 文件引用。`aggregate_results` 接受
任意 iterable，在一次遍历中维护 group、eval_kind、subset 三层 accumulator。最终
`results.json` 仍包含兼容的 `results` 数组，并通过同目录临时文件、fsync 和原子替换
逐条读取 canonical per-record 文件生成；历史 payload 在写出前执行 schema up-conversion。

离线 fixture 逐字段对比了重构前后的完整 summary，并验证 group/eval/subset 首次
出现顺序、record 顺序、score/error/evaluable/degraded 计数、失败与取消状态、完整
`raw`、`runner_meta`、audit 和回答全文。历史 schema 验收使用真正缺少
`schema_version` 和当前状态轴字段的 v1 payload；此前同名测试实际输入 schema 3，
不能作为历史 schema 证据。merge=true 按 aggregate group 顺序和组内排序文件名读取；
merge=false 只使用本次轻量引用，按 selected group 和输入 record 顺序写出，不扫描
或混入陈旧文件。

取消轻量引用现在显式保留 `archive_ok is False`，而不是用可空的 `archive_error`
文本代替失败状态。缺失、空字符串或空对象错误详情都会生成默认
`workspace archive failed`，保持 `cancelled_with_errors`；archive 成功与 cleanup
失败独立判定。CLI 的批量 failure 路径逐条持久化后立即轻量化，单条 orchestration
路径也清除 answer/evaluation 临时引用。

流式 writer 保持普通 `indent=2` writer 的字节布局。读取、JSON 编码或
`os.replace` 注入失败时，旧 `results.json` 不变且同目录临时文件被清理；canonical
per-record 修复后可重建。定向验收（Phase 3、ResultSink、CLI、dashboard、history
recovery、progress 和 service boundaries）：`204 passed, 2 skipped, 14 subtests passed`。

## 等工作量性能验收

legacy 和 streaming 都执行相同的 per-record 原子写入、summary 聚合和完整
`results.json` 原子写出，并逐配对校验输出字节数、SHA-256、summary 大小和保留字段。
每条详情内容由 record index 决定，保留在 prompt、answer、raw、runner metadata 和
完整回答中；每个 mode/repetition 是独立进程，三轮交替执行顺序。证据：
[`report.json`](../../state/benchmark-runs/temporary/runtime-baseline/offline/runtime-baseline-offline-20260916-172414/report.json)、
同目录 `raw-results.jsonl`、`runner-meta.json` 和 `audit-findings.json`。

| Records | 独立详情 | Legacy RSS | Streaming RSS | Legacy wall | Streaming wall | Final bytes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0 B | 42.5 MB | 30.0 MB | 0.224 s | 0.281 s | 1,619,955 |
| 10,000 | 0 B | 188.7 MB | 48.9 MB | 2.267 s | 2.834 s | 16,005,089 |
| 1,000 | 64 KiB | 1.19 GB | 31.2 MB | 1.595 s | 1.996 s | 329,299,955 |
| 10,000 | 16 KiB | 2.14 GB | 50.3 MB | 6.398 s | 7.333 s | 835,205,089 |

数值为三轮中位数。流式路径以额外读取/解码换取显著降低的 RSS，目标不是降低输出
字节或 wall time。标准 JSON 的最终文件仍随详情线性增长。`10k × 64 KiB` 未执行，
因为等工作量 JSON 会超过 3 GB；10k 大详情使用 16 KiB，1k 场景覆盖 64 KiB。

`runtime-baseline-offline-20260916-172133` 是保留的失败测量审计：旧脚本用
`read_bytes()` 计算最终 SHA-256，把整个输出重新载入内存并污染 streaming RSS。
该目录不用于结论。历史单次 smoke 的约 874 MB→39 MB 对照工作量不相等，也不再
作为端到端验收证据。

完整测试：`982 passed, 11 skipped, 164 subtests passed`，另有 5 个既有 SWIG
deprecation warnings。

限制：未启动真实付费模型、Docker attempt 或 pinned verifier runtime；这是离线
结果生命周期验收，不代表完整 benchmark wall time。
