# Benchmark Runtime Phase 3 Validation

日期：2026-09-16

状态：`COMPLETE_WITH_LIMITS`

范围：验证 RT-03 有界结果内存、单遍增量聚合和 canonical per-record 流式结果写出。

实现保持 `GroupRecordResult` 持久化字段完整。attempt 结果写入后，运行态仅保留
record identity、状态、score、错误和 canonical 文件引用。`aggregate_results` 接受
任意 iterable，在一次遍历中维护 group、eval_kind、subset 三层 accumulator。最终
`results.json` 仍包含兼容的 `results` 数组，并通过同目录临时文件、fsync 和原子替换
逐条读取 canonical per-record 文件生成；历史 payload 在写出前执行 schema up-conversion。

离线 fixture 验证了 group/eval/subset 顺序、score/error/evaluable/degraded 计数、失败
与取消状态、完整 `raw`、`runner_meta`、audit 和回答全文，以及 per-record 历史 schema
读取。完整测试结果为 `939 passed, 11 skipped, 164 subtests passed`。

独立进程基准（Apple Silicon，单次 smoke，详情同时写入 raw、runner metadata 和回答
字段）如下：

| Records | Detail | Legacy peak RSS | Streaming peak RSS | Streaming final bytes |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 1 KiB | 约 29 MB | 约 29 MB | 约 5.1 MB |
| 10,000 | 1 KiB | 约 65 MB | 约 47 MB | 约 51 MB |
| 1,000 | 64 KiB | 约 110 MB | 约 27 MB | 约 263 MB |
| 10,000 | 64 KiB | 约 874 MB | 约 39 MB | 约 2.63 GB |

流式路径的聚合和最终写出 wall time 会包含 per-record 文件 I/O；收益目标是构造期间
的内存上界，而不是降低结果文件字节数。标准 JSON 要求完整数组，因此最终文件本身
仍随记录详情线性增长。stream writer 的临时文件失败或读取失败不会替换已有
`results.json`，下一次可从 canonical per-record 文件重建。

限制：未启动真实付费模型、Docker attempt 或 pinned verifier runtime。Phase 4 的
invocation 级 VGB validation cache 仍需独立设计和等价性验证。
