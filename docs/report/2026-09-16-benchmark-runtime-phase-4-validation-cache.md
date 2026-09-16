# Benchmark Runtime Phase 4 Validation Cache

日期：2026-09-16

状态：`COMPLETE_WITH_LIMITS`

本阶段只完成 RT-04 的第一步：invocation 级 immutable runtime validation cache。CLI
在 verifier-grounded invocation 中创建 cache，并把同一实例传给 `evaluate_one` 和
property `reference_answers`。cache key 包含 release/config identity、
wheel inode/size/mtime、runtime manifest SHA-256 和 runtime Python inode/size/mtime；
fingerprint 变化会重新校验，异常不会写入成功缓存。缓存不包含 answer、task 或评分
结果，因此每题仍保持独立 verifier 进程和原有评分/错误契约。

验证覆盖成功复用、fingerprint 失效、失败不缓存、release identity 校验和现有 bridge
payload。定向测试：`42 passed, 2 skipped`。完整测试在 Phase 3 基线之后仍保持通过，
本阶段没有真实付费模型、Docker 或 pinned verifier runtime acceptance。

离线 validation-only microbenchmark 使用确定性 stub：1,000 次验证从约 125 ms 降至
约 37 ms；没有启动 VGB API 子进程（process count 为 0）。实际 invocation 的
`evaluate_one` process count 不变，cache 只消除了重复 wheel 校验和 manifest 解析。

边界：启动 preflight 在 invocation 产物初始化前执行，缺失 wheel/manifest/runtime
会直接失败；后续若需要完整失败证据，应把 preflight failure 写入 run manifest。
持久 verifier worker 尚未设计或启用，必须等待旧/新 bridge shadow score 和故障恢复
证据后再决定默认路径。
