# Benchmark Runtime Phase 5 Archive Inventory Validation

日期：2026-09-16

状态：`ACCEPTED_WITH_CROSS_DEVICE_SIMULATION`

范围：RT-05 attempt workspace archive inventory 合并；不改变 workspace 或 archive
schema、symlink 规则、sentinel hash 或 quarantine 策略。

## 实现与安全契约

`_TreeInventory` 由一次 `os.scandir` 树遍历同时产生 regular-file count/bytes、symlink
count/digest、dangling symlink count/digest，并在同一遍历中执行 forbidden `.git`、
special file 和 scratch symlink 校验。实现不保留全树路径列表，仅为确定性摘要保留
symlink 条目。

同文件系统 seal 在 rename 前验证源 inventory，rename 后独立验证 archive 目标并用
目标 inventory 写 manifest。跨文件系统分支在 copy 前验证源，copy 后重新验证源和
目标，比较 regular-file 与 symlink inventory，并独立检查目标 sentinel SHA-256；
copy 期间源 count/bytes 或 symlink inventory 变化会失败。symlink 仍不解引用，合法
dangling link 规则不变；不安全目标、copy 错误和 inventory 不匹配均 fail closed，
managed source 被 quarantine。

## 性能证据

`scripts/benchmark_archive_inventory.py` 创建 10,000 个独立 1 KiB 文件、100 个正常
symlink 和 1 个 dangling symlink。每个 mode/repetition 使用独立进程，三轮交替顺序，
并校验 tree/symlink inventory 完全相同。证据：
[`report.json`](../../state/benchmark-runs/temporary/archive-inventory/no-model/archive-inventory-no-model-20260916-184000/report.json)
和同目录 `raw-results.jsonl`。

| 路径 | Mode | Traversals | Entries visited | Wall time |
| --- | --- | ---: | ---: | ---: |
| same filesystem | legacy equivalent | 4 | 40,812 | 0.865 s |
| same filesystem | consolidated | 2 | 20,406 | 0.449 s |
| cross-device copy branch | legacy equivalent | 8 | 81,624 | 1.704 s |
| cross-device copy branch | consolidated | 3 | 30,609 | 0.678 s |

数值为三轮中位数，不含文件生成/copy 时间，只测量校验和 inventory I/O。旧路径数字
由原执行序列等价调用构成；`workspace_inventory_traversal_count` 和 entry count 来自
实际遍历。

## 验证与限制

测试覆盖同文件系统 seal、合法与 dangling symlink、不安全 control-plane/scratch
link、special file/`.git`、目标 inventory 损坏、源 copy 期间变化、sentinel hash、
copy 失败 quarantine 及恢复路径。定向结果：`102 passed, 66 subtests passed`。

完整测试：`997 passed, 11 skipped, 164 subtests passed`，另有 5 个既有 SWIG
deprecation warnings。

本机所有可写临时目录与项目目录都位于同一 APFS data volume，无法执行真正异盘
rename/copy acceptance。跨设备测试通过强制 `st_dev` 分支后运行真实
`copytree(symlinks=True)`、独立源/目标验证和失败恢复；因此不能声称真实跨文件系统
I/O 已验收。
