# MACE Skill — 输出标准

本文档说明 MACE 能量推断任务的输出结果结构。

---

## 下载接口

```
GET http://114.214.211.25:30082/api/tasks/download?taskId=<uuid>
```

返回一个 zip 压缩包，文件名通常为 `<taskId>.zip`。

---

## 输出 zip 内容

| 文件/目录 | 说明 |
|-----------|------|
| `results/` | MACE 能量推断结果目录 |
| `results/*.csv` 或 `results/*.json` | 各结构的势能、原子力、应力张量数值 |
| `logs/` | 任务运行日志 |

> **注意**：实际内容以服务端返回为准，README_mace.md 未详细说明内部文件名，请以实际下载包为准。

---

## 数据字段说明

MACE 推断输出通常包含以下物理量：

| 字段 | 单位 | 说明 |
|------|------|------|
| `energy` | eV | 晶体结构的总势能 |
| `forces` | eV/Å | 各原子受力（3N 维向量）|
| `stress` | eV/Å³ | 应力张量（3×3 矩阵）|
| `energy_per_atom` | eV/atom | 每原子平均势能（用于跨结构比较）|

---

## 汇报格式

```
[upload] 文件上传成功: http://124.71.201.245/aichem-lab-edge/<filename>.zip
[execute] 任务提交响应:
{
  "code": 200,
  "data": { "id": 12345, "taskId": "<uuid>", "status": "PENDING" }
}
[done] 任务已提交，job_id=12345，taskId=<uuid>
[poll] 开始轮询任务 job_id=12345，每 10 秒查询一次...
[query] 任务状态: RUNNING
[poll] 任务已完成，最终状态: SUCCEEDED
[result] 计算成功！下载结果中...
[download] 结果已下载: <uuid>.zip
```
