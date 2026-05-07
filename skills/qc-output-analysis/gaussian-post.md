# Gaussian 输出文件解析

**镜像：** `114.214.255.82:18080/internal/gaussian-post:latest.arm`

**功能：** 解析 Gaussian 量子化学软件输出文件（g16.out.log），根据计算类型路由提取能量、结构、光谱等计算结果，返回结构化 JSON 数据。

---

## 输入参数

| 参数 | 类型 | 必填 | 取值范围 | 描述 |
|------|------|------|----------|------|
| `inputfile` | URL | ✅ | 合法 HTTP URL | ZIP 压缩包，含 Gaussian 输出文件 (g16.out.log) 和可选输入文件 (input.gjf) |
| `objective` | ENUM | ✅ | 见下表 | 计算类型路由，决定调用哪个解析器 |

**objective 取值范围：**

| 值 | 解析内容 |
|----|----------|
| 单点能 | 提取能量、偶极矩、电荷分布等 |
| 几何结构优化 | 提取优化后能量、收敛信息，返回 XYZ 结构文件 |
| 红外光谱 | 提取振动频率、红外强度 |
| 拉曼光谱 | 提取振动频率、拉曼活性 |
| 紫外可见光谱 | 提取跃迁能量、波长、振子强度 |
| 核磁共振谱 | 提取化学位移 |
| 过渡态搜索 | 提收过渡态能量、虚频信息 |
| 势能面扫描 | 提取扫描路径上各点能量 |
| 极化率和超极化率 | 提取极化率张量、超极化率 |

---

## 输出

| 参数 | 类型 | 描述 |
|------|------|------|
| `analysis` | dict | 解析结果载荷，含 computational_info / data_type / data / units 等子字段，结构因 objective 而异 |
| `xyz` | URL | 优化后分子结构 XYZ 文件 OSS 地址（仅 "几何结构优化" 类型返回） |
| `error` | str | 错误信息（仅未知 objective 时返回） |

---

## 提交示例

```bash
TASK_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

curl -X POST 'http://114.214.211.25:30082/api/jobs' \
  -H 'Content-Type: application/json' \
  -d '{
    "params": {
        "inputfile": "http://oss/.../gaussian_output.zip",
        "objective": "几何结构优化"
    },
    "image": "114.214.255.82:18080/internal/gaussian-post:latest.arm",
    "parentId": "${TASK_ID}",
    "taskId": "${TASK_ID}",
    "createdBy": "user"
}'
```

---

## 完整调用流程

### 第一步：提交任务

```bash
TASK_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

NUMERIC_ID=$(curl -s -X POST 'http://114.214.211.25:30082/api/jobs' \
  -H 'Content-Type: application/json' \
  -d "{
    \"params\": {
      \"inputfile\": \"${FILE_URL}\",
      \"objective\": \"几何结构优化\"
    },
    \"image\": \"114.214.255.82:18080/internal/gaussian-post:latest.arm\",
    \"parentId\": \"${TASK_ID}\",
    \"taskId\": \"${TASK_ID}\",
    \"createdBy\": \"user\"
  }" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
```

### 第二步：轮询状态

```bash
curl -X GET "http://114.214.211.25:30082/api/jobs?id=${NUMERIC_ID}"
# 状态机：PENDING → DISPATCHED → RUNNING → SUCCEEDED / FAILED
```

### 第三步：下载结果

```bash
curl -OJ "http://114.214.211.25:30082/api/tasks/download?taskId=${TASK_ID}"
```

---

## 与上游 Skill 联动

```
mol-qc/gaussian（执行 Gaussian 计算）
  → gaussian-post（解析输出，提取能量、结构、光谱）
  → 下游分析
```