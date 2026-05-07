# IR 谱图分析

对实验红外光谱进行官能团和物相预测。

## 输入文件格式

JSON 格式，`x_label` 为 `"Wave number(cm-1)"`，`y_label` 为 `"Transmittance(%)"`：

```json
{
  "basicInformation": {
    "x_label": "Wave number(cm-1)",
    "y_label": "Transmittance(%)",
    "x_range": {"rangeMin": 219.6, "rangeMax": 4180.2}
  },
  "lines": [{
    "parameter": {"type": "line", "color": "#1f77b4", "style": "solid"},
    "data": [[399.68, -66.07], [400.16, 7.64], ...]
  }]
}
```

> 与 XRD 谱图的区别：x 轴为波数（非 2θ），y 轴为透过率（非强度）。

## 调用示例

```bash
# 上传 IR JSON 文件
FILE_URL=$(curl -s -X POST http://114.214.215.131:40080/worker/file/upload \
  -H "identifies: 691c9f24af764bd6ac955a0e8dd0dba9" \
  -F "file=@ir_spectrum.json" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']
print(d if d.startswith('http') else 'http://114.214.215.131:40080' + d)")

# 提交分析
curl -s -X POST "http://114.214.255.82:8396/api/scripts/name/ir-predict/execute" \
  -H "Content-Type: application/json" \
  -d "{
    \"arg\": \"\",
    \"attachments\": {
      \"inputfile\": \"${FILE_URL}\",
      \"script_params.json\": \"{\\\"inputfile\\\":\\\"${FILE_URL}\\\"}\"
    }
  }"
```

## 结果查询

```bash
curl -s "http://114.214.255.82:8396/api/scripts/<task_id>/result/<task_id>"
```

响应中 `data.response` 包含：
- `红外光谱分析结果`：分析结果 ZIP
- `分子结构标签集`：CSV（官能团标签）
- `分子结构候选集`：CSV（候选物相）

下载链接中 `10.88.0.32` → `114.214.255.82`，端口不变。
