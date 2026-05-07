# MACE Skill — REST API 合约

本文档记录 `mace` skill 所使用的 REST API 请求/响应格式（基于 README_mace.md 确认）。

---

## 平台

| 字段 | 值 |
|------|----|
| 调度平台地址 | `http://114.214.211.25:30082` |
| 任务接口 | `/api/jobs/tpl`（模型推断类型） |
| 模型名称 | `mace` |
| taskId 格式 | UUID（`uuid.uuid4()` 生成） |

---

## 1. 文件上传

```
POST http://114.214.211.25:30082/api/file/upload
Content-Type: multipart/form-data
```

**请求：**
```bash
curl -X POST "http://114.214.211.25:30082/api/file/upload" \
  -F "file=@structures.zip"
```

**响应：**
```json
{
  "code": 0,
  "data": "http://124.71.201.245/aichem-lab-edge/20260413134101201zip.zip",
  "message": ""
}
```

- `code`: `0` 或 `200` 均为成功
- `data`: 完整文件 URL，直接作为 `inputfile` 参数使用

---

## 2. 提交任务（/api/jobs/tpl）

```
POST http://114.214.211.25:30082/api/jobs/tpl
Content-Type: application/json
```

**请求体：**
```json
{
  "params": {
    "inputfile": "http://124.71.201.245/aichem-lab-edge/20260413134101201zip.zip"
  },
  "model": "mace",
  "parentId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "taskId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "createdBy": "user"
}
```

**字段说明：**
- `model`：固定为 `"mace"`（区别于 `/api/jobs` 使用 `image` 字段）
- `taskId`：**UUID 格式**（调用方生成），用于最终下载结果
- `parentId`：与 `taskId` 相同
- `params.inputfile`：CIF 结构包的完整 HTTP URL

**响应：**
```json
{
  "code": 200,
  "data": {
    "id": 12345,
    "taskId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "status": "PENDING"
  }
}
```

- `data.id`（**整数**）：用于**轮询状态**
- `data.taskId`（**UUID 字符串**）：与提交时相同，用于**下载结果**

---

## 3. 查询任务状态

```
GET http://114.214.211.25:30082/api/jobs?id=12345
```

**响应：**
```json
{
  "code": 200,
  "data": {
    "id": 12345,
    "taskId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "status": "RUNNING",
    "output": {}
  }
}
```

**状态机：**

| 状态 | 含义 | 是否终态 |
|------|------|---------|
| `PENDING` | 等待调度 | 否 |
| `DISPATCHED` | 已下发到集群 | 否 |
| `RUNNING` | 执行中 | 否 |
| `SUCCEEDED` | 执行成功 | ✅ 是 |
| `FAILED` | 执行失败 | ✅ 是 |
| `CANCELLED` | 已取消 | ✅ 是 |
| `TIMEOUT` | 超时终止 | ✅ 是 |

> `data.output` 始终为空对象 `{}`，实际结果通过下载接口获取。

---

## 4. 下载结果

```
GET http://114.214.211.25:30082/api/tasks/download?taskId=f47ac10b-58cc-4372-a567-0e02b2c3d479
```

- 参数为提交时的 `taskId`（UUID 字符串），**不是** `data.id`（整数）
- 返回 `application/octet-stream`，`Content-Disposition` 含文件名
- 内容为 zip 压缩包，见 `output-standard.md`

---

## taskId vs data.id 速查

```
提交时：taskId = "f47ac10b-..."  （UUID，调用方生成）
响应中：data.id = 12345           （整数，服务端生成）

轮询用：GET /api/jobs?id=12345               ← data.id（整数）
下载用：GET /api/tasks/download?taskId=f47... ← taskId（UUID）
```
