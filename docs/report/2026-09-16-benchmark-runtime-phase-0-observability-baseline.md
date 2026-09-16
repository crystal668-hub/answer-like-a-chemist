# Benchmark Runtime Phase 0 Observability Baseline

日期：2026-09-16

状态：`COMPLETE_WITH_LIMITS`

范围：验证 benchmark runtime 架构优化交接文档的 Phase 0，并建立后续 RT-01 至
RT-05 可比较的观测契约。行为基线为 `a095aa6`，观测实现提交为 `644bf96`。

## 结论

Phase 0 的代码级交付已完成。CLI invocation 结束时写入独立的
`runtime-metrics.json`，`results.json` 的 schema、评分、audit findings 和结果字段
没有变化。观测文件自身在 collector 关闭后写入，因此不会递归计入 state-write
字节数。

观测范围包括：

- invocation、attempt、audit、archive、score、Docker command 和 VGB process 的
  monotonic duration；
- transcript read count/bytes/lines 和 JSON decode/error count；
- Docker command count（按子命令分桶）和 VGB process count（按 action 分桶）；
- atomic state/evidence write count/bytes（按 per-record、progress、results、manifest、
  attempt-result、scoring-pending 等类别分桶）；
- progress event append count/bytes 和进程 peak RSS。

## 确定性场景

`tests/fixtures/runtime_observability/scenario.json` 固定了两种 skills 状态以及 VGB
评分、timeout retry、terminal failure、取消和 1000 行 transcript。观测契约测试
使用真实 atomic writer、attempt evidence writer、Docker command adapter 和 VGB
subprocess bridge，外部进程返回值为固定 stub。既有 runner、队列、取消、audit 和
VGB evaluator 测试继续承担业务语义验证。

## 聚合基线

命令：

```bash
PYTHONPATH=<revision-root> .venv/bin/python scripts/benchmark_runtime_baseline.py \
  --records <1000|10000> --payload-bytes 1024
```

每组独立进程运行三次，下表为中位数。每条记录生成独立的 1 KiB 详情字符串；
输出 `summary_bytes` 和 group order 在两个 revision 间相同。

| Records | Revision | Aggregation median | Peak RSS median | Summary bytes |
| ---: | --- | ---: | ---: | ---: |
| 1,000 | `a095aa6` | 12.84 ms | 28,409,856 B | 17,790 |
| 1,000 | `644bf96` | 9.95 ms | 28,672,000 B | 17,790 |
| 10,000 | `a095aa6` | 172.62 ms | 64,847,872 B | 17,912 |
| 10,000 | `644bf96` | 112.14 ms | 64,946,176 B | 17,912 |

bucket accumulator 的耗时分别下降约 22% 和 35%。RSS 没有实质下降，因为当前
`aggregate_results`、CLI 和最终 JSON 构造仍保留完整记录列表；这正是 Phase 3
需要解决的生命周期问题，当前结果不能视为有界内存验收。

## 验证

完整测试：

```text
927 passed, 11 skipped, 164 subtests passed
```

覆盖范围包括固定结果聚合、atomic persistence、progress、attempt retry、取消、
workspace audit/archive、transcript recovery、Docker adapter、VGB bridge/evaluator
和 CLI manifest。新增 accumulator 测试使用冻结期望字段和 weak reference，避免
新实现与自身比较或仅检查属性名。

## 限制

- 本报告没有启动真实模型、Docker attempt 或 pinned VGB scoring runtime；真实
  wall time、Docker command 分布、VGB process latency 和运行期 RSS 必须在后续
  acceptance run 中由 `runtime-metrics.json` 采集。
- peak RSS 是进程 high-water mark，适合独立进程间比较，不能解释为阶段瞬时内存。
- transcript 指标当前统计各消费者的实际读取和解码次数，用于建立 RT-02 优化前
  基线；它有意记录重复读取，而不是对路径去重。
- Phase 1 尚缺 ResultSink、checkpoint 和完整 resume/cancellation 故障注入验收；
  Phase 2 开始前应先完成这些持久化边界。
