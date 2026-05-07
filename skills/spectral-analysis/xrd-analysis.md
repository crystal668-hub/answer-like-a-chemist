# XRD 谱图分析

对实验 XRD 谱图进行峰统计、物相预测和 Rietveld 精修。

## 子功能路由

```
XRD 分析请求
  ├─ 统计峰位（无需元素组成）→ peak_statistics
  ├─ 快速物相识别（需提供元素组成）→ xrd-predict
  └─ Rietveld 精修（需提供元素组成）→ xrd-xerus
```

## 输入文件格式

JSON 格式，`x_label` 为 `"2-Theta(deg)"`，`y_label` 为 `"Intensity(cps)"`：

```json
{
  "basicInformation": {
    "x_label": "2-Theta(deg)",
    "y_label": "Intensity(cps)",
    "x_range": {"rangeMin": 6.0, "rangeMax": 94.0}
  },
  "lines": [{
    "parameter": {"type": "line", "color": "#1f77b4", "style": "solid"},
    "data": [[10.0, 4100.0], [10.01, 4100.0], ...]
  }]
}
```

---

## 功能一：峰统计（peak_statistics）

无需提供元素组成，自动检测峰位并统计。

```bash
FILE_URL=$(curl -s -X POST http://114.214.215.131:40080/worker/file/upload \
  -H "identifies: 691c9f24af764bd6ac955a0e8dd0dba9" \
  -F "file=@xrd.json" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']
print(d if d.startswith('http') else 'http://114.214.215.131:40080' + d)")

curl -s -X POST "http://114.214.255.82:8396/api/scripts/name/peak_statistics/execute" \
  -H "Content-Type: application/json" \
  -d "{
    \"arg\": \"\",
    \"attachments\": {
      \"inputfile\": \"${FILE_URL}\",
      \"script_params.json\": \"{\\\"inputfile\\\":\\\"${FILE_URL}\\\"}\"
    }
  }"
```

---

## 功能二：物相预测（xrd-predict）

根据谱图和元素组成预测物相归属，支持单 JSON 或多 JSON 打包为 ZIP。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `inputfile` | 是 | JSON 或 ZIP（多个 JSON） |
| `target_elements` | 是 | 元素组成数组（JSON 字符串） |

`target_elements` 格式：`[{"sample": "文件名（不含扩展名）", "element": "Fe,O"}]`

```bash
curl -s -X POST "http://114.214.255.82:8396/api/scripts/name/xrd-predict/execute" \
  -H "Content-Type: application/json" \
  -d "{
    \"arg\": \"\",
    \"attachments\": {
      \"inputfile\": \"${FILE_URL}\",
      \"script_params.json\": \"{\\\"inputfile\\\":\\\"${FILE_URL}\\\",\\\"target_elements\\\":\\\"[{\\\\\\\"sample\\\\\\\":\\\\\\\"xrd\\\\\\\",\\\\\\\"element\\\\\\\":\\\\\\\"Fe,O\\\\\\\"}]\\\"}\"
    }
  }"
```

---

## 功能三：Rietveld 精修（xrd-xerus）

参数格式与 xrd-predict 相同，计算更精确。

```bash
curl -s -X POST "http://114.214.255.82:8396/api/scripts/name/xrd-xerus/execute" \
  -H "Content-Type: application/json" \
  -d "{
    \"arg\": \"\",
    \"attachments\": {
      \"inputfile\": \"${FILE_URL}\",
      \"script_params.json\": \"{\\\"inputfile\\\":\\\"${FILE_URL}\\\",\\\"target_elements\\\":\\\"[{\\\\\\\"sample\\\\\\\":\\\\\\\"xrd\\\\\\\",\\\\\\\"element\\\\\\\":\\\\\\\"Bi,O\\\\\\\"}]\\\"}\"
    }
  }"
```

---

## 结果查询

```bash
curl -s "http://114.214.255.82:8396/api/scripts/<task_id>/result/<task_id>"
```

下载链接中 `10.88.0.32` → `114.214.255.82`，端口不变。
