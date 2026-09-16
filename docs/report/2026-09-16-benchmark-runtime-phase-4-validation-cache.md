# Benchmark Runtime Phase 4 Validation Cache

日期：2026-09-16

状态：`ACCEPTED_WITH_LIMITS`

本阶段只完成 RT-04 的第一步：invocation 级 immutable runtime validation cache。CLI
在 verifier-grounded invocation 中创建 cache，并把同一实例传给 `evaluate_one` 和
property `reference_answers`。cache key 包含 release/config identity、
wheel inode/size/mtime、runtime manifest SHA-256 和 runtime Python inode/size/mtime；
fingerprint 变化会重新校验，异常不会写入成功缓存。缓存不包含 answer、task 或评分
结果，因此每题仍保持独立 verifier 进程和原有评分/错误契约。

验证覆盖成功复用、fingerprint 失效、失败不缓存、release identity 校验和现有 bridge
payload。新增真实临时文件测试会修改 manifest 内容并验证失效、失败后恢复；16 个
并发调用只执行一次底层校验；release 配置变化会改变 fingerprint。

旧报告中的 deterministic stub 约 125→37 ms 没有 raw 产物和可复现 runner，不能
作为验收证据。修正后的 `scripts/benchmark_vgb_validation_cache.py` 对真实 pinned
v0.9.2 wheel、manifest 和 runtime Python 执行 1,000 次校验，每个 mode/repetition
使用独立进程并交替顺序。三轮中位 wall time 为 uncached 121.85 ms、cached
35.22 ms；cached 每轮 1 miss/999 hits，manifest 完全相同。证据：
[`report.json`](../../state/benchmark-runs/temporary/vgb-validation-cache/pinned/vgb-validation-cache-pinned-20260916-175000/report.json)
和同目录 `raw-results.jsonl`。没有启动 VGB API 子进程；实际 isolated 评分的每题
process count 不变，cache 只消除重复 wheel hash、manifest 读取和文件校验。

边界：启动 preflight 在 invocation 产物初始化前执行，缺失 wheel/manifest/runtime
会直接失败；后续若需要完整失败证据，应把 preflight failure 写入 run manifest。
cache 不缓存 answer、task 或评分结果。worker 的 pinned-release 验收见配套报告；
其默认仍关闭。
