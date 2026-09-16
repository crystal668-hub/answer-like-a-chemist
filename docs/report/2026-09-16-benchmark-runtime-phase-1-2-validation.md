# Benchmark Runtime Phase 1-2 Validation

日期：2026-09-16

状态：`COMPLETE_WITH_LIMITS`

范围：验证 RT-01 的持久化边界以及 RT-02 的 transcript 单次索引。Phase 0
观测基线见 [配套报告](2026-09-16-benchmark-runtime-phase-0-observability-baseline.md)。

## 持久化结果

`ResultSink` 现在是 canonical per-record 写入入口。attempt 完成时先原子提交记录；
CLI 聚合阶段遇到字节级相同 payload 会跳过写入，只有 public reporting reference
等字段确实变化时才更新记录。sink 拒绝 output-root 边界内任一 symlink，并在
runtime manifest 中记录实际写入与跳过次数。

progress journal 继续逐事件 append、flush 和 fsync。完整 state 使用一秒 checkpoint；
run/group terminal、cancelling 和 cancelled 状态强制立即原子刷新。故障注入验证
`os.replace` 失败时旧目标保持完整、临时文件被清理。per-record 已提交而
`results.json` 尚未生成时，resume 会把该记录识别为完成。

## Transcript Index

`TranscriptIndex` 一次读取 JSONL，记录 SHA-256、字节数、行数、成功 decode 数和
失败行的位置/hash。它不保存原始文本或失败行内容的第二份副本。

- convergence summary、可见 assistant 文本和最新完整答案共享同一索引；
- dependency install evidence 与 workspace audit 在父 runtime 共享同一索引；
- audit 使用 strict view，截断行仍产生 unavailable/fail-closed 结果；
- recovery-oriented consumer 继续忽略截断行并可恢复此前完整答案；
- path projection 只作用于内存派生 view，不修改索引或 transcript；
- provider trajectory、archive transcript 和 frozen finalization snapshot 是独立来源，
  分别建立索引。

wrapper 与父 runtime 是独立进程。为避免持久化包含完整外部 tool output 的第二份
索引，同一物理 transcript 最多在这两个进程各读取一次。父进程内的 dependency
evidence 与 audit 已达到一次线性读取；archive recovery 最多额外读取 archive 来源
一次。

## 性能基线

`scripts/benchmark_transcript_index.py` 生成每行独立 1 KiB payload，并比较三个
consumer 各自读取/解码与一次索引后三个 view。每组独立进程运行三次，下表为中位数。

| Lines | Mode | Elapsed median | Peak RSS median | Transcript bytes |
| ---: | --- | ---: | ---: | ---: |
| 1,000 | legacy three reads | 6.85 ms | 30,343,168 B | 1,110,890 |
| 1,000 | indexed one read | 3.89 ms | 30,621,696 B | 1,110,890 |
| 10,000 | legacy three reads | 86.03 ms | 84,688,896 B | 11,118,890 |
| 10,000 | indexed one read | 37.73 ms | 86,458,368 B | 11,118,890 |

索引将该合成读取路径耗时降低约 43%/56%。RSS 增加约 0.9%/2.1%，来源是为了在
consumer 间复用而保留的解析对象和行号引用；它避免重复 decode，但不是压缩结构。
该结果低于继续保留多份完整 consumer payload 的风险，但 Phase 3 仍需结合真实
transcript 与 CLI 详情生命周期验证总进程 RSS。

## 验证

完整测试结果：

```text
937 passed, 11 skipped, 164 subtests passed
```

新增契约覆盖普通 JSONL、截断行、tool call/result、dependency command、prompt
error、完整答案、path projection、fingerprint、单次读取计数和 index/direct audit
逐 payload 等价。既有 workspace audit suite 继续覆盖 standalone result、缺失
tool result、heredoc EOF、nested command substitution、archive replay 和四轴
adjudication。

## 限制

- 性能数据为离线合成负载，不代表模型、Docker 或 verifier wall time。
- 索引有意保留 parsed payload，以便 audit 读取完整证据；没有截断正式审计输入。
- Phase 3 的完整结果列表、final `results.json` 构造和 RSS 线性增长尚未处理。
