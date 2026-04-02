# Init 子命令

配置 Apifox API Token 和项目 ID。

## 步骤 1：读取现有配置

**配置文件位置**：项目级 `.claude/apifox.json`（每个项目独立配置，包含 apiToken 和 projectId）。

先定位项目根目录（参见 SKILL.md 注意事项第 6 条）：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
```

检查是否已有配置（按优先级），**不要将 Token 明文输出到终端**：

1. 用 Bash 检查环境变量（仅检查是否存在，不输出 Token 值）：
```bash
echo "HAS_TOKEN=${APIFOX_API_TOKEN:+yes}" && echo "PID=${APIFOX_PROJECT_ID:-}"
```

2. 用 Bash 读取项目配置文件（仅输出是否存在和 ProjectId，不输出 Token 值）：
```bash
cat "${PROJECT_ROOT}/.claude/apifox.json" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'HAS_TOKEN={\"yes\" if d.get(\"apiToken\") else \"no\"}')
print(f'PID={d.get(\"projectId\",\"\")}')
" 2>/dev/null || echo "NO_CONFIG"
```

如果已有有效配置，告知用户"已检测到现有配置（Token: 已配置, ProjectId: {值}）"并询问是否覆盖。

## 步骤 2：收集凭证

用 `AskUserQuestion` 收集 API Token：
- 提示：从 Apifox 头像 → 账号设置 → API 访问令牌 → 新建令牌
- 格式：`afxp_xxxxxxxxxx`
- 建议选择"永不过期"

用 `AskUserQuestion` 收集 Project ID：
- 提示：从 Apifox 项目设置 → 基本设置 → 基本信息 → 项目 ID
- 格式：纯数字字符串

收集完毕后，在后续 Bash 调用中通过变量赋值使用（不回显 Token）：
```bash
TOKEN="用户输入的Token值"
PROJECT_ID="用户输入的ProjectId值"
```

## 步骤 3：验证连通性

在同一个 Bash 调用中赋值变量并执行 curl（确保 TOKEN 和 PROJECT_ID 在同一 shell 中可用）：
```bash
TOKEN="<步骤2收集的Token>"
PROJECT_ID="<步骤2收集的ProjectId>"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"oasVersion":"3.0","exportFormat":"JSON","options":{"includeApifoxExtensionProperties":false}}')
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
echo "HTTP_CODE=$HTTP_CODE"
```

判断结果：
- `200` → 连接成功
- `401` / `403` → Token 无效，提示用户检查令牌
- `404` → Project ID 无效，提示用户检查项目 ID
- `422` → API 版本头缺失（内部错误）
- 其他 → 网络错误，显示状态码

## 步骤 4：保存配置

验证成功后，用 Bash 写入项目级配置（在同一 shell 调用中使用 TOKEN 和 PROJECT_ID 变量）：
```bash
CONFIG_DIR="${PROJECT_ROOT}/.claude"
CONFIG_FILE="${CONFIG_DIR}/apifox.json"
mkdir -p "$CONFIG_DIR"
python3 -c "
import json
d = {'apiToken': '${TOKEN}', 'projectId': '${PROJECT_ID}'}
json.dump(d, open('${CONFIG_FILE}', 'w'), indent=2)
print('OK')
"
```

提示用户：配置已保存到 `${PROJECT_ROOT}/.claude/apifox.json`，可使用 `/apifox-sync push` 推送接口。建议将 `.claude/apifox.json` 加入 `.gitignore`（因为包含 apiToken）。
