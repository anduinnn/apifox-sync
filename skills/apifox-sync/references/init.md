# Init 子命令

配置 Apifox API Token 和项目 ID。

## 步骤 1：读取现有配置

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
eval "$(python3 skills/apifox-sync/scripts/load_config.py "$PROJECT_ROOT")"
echo "HAS_TOKEN=$HAS_TOKEN" && echo "PID=$PID"
```
若 `HAS_TOKEN=yes`，告知"已检测到现有配置（Token: 已配置, ProjectId: ${PID}）"并询问是否覆盖。

## 步骤 2：收集凭证

用 `AskUserQuestion` 收集：
- **API Token**：从 Apifox 头像 → 账号设置 → API 访问令牌 → 新建令牌，格式 `afxp_...`，建议选"永不过期"
- **Project ID**：从项目设置 → 基本设置 → 项目 ID，格式为纯数字

收集后在 Bash 中通过变量赋值使用（不回显 Token）。

## 步骤 3：验证连通性

在同一 Bash 调用中赋值并 curl（确保 TOKEN/PROJECT_ID 在同一 shell 可用）：
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
```

状态码处理：`200` 成功；`401/403` Token 无效；`404` ProjectId 无效；`422` API 版本头缺失；其他网络错误显示状态码。

## 步骤 4：保存配置

```bash
mkdir -p "${PROJECT_ROOT}/.claude"
cat > "${PROJECT_ROOT}/.claude/apifox.json" << EOF
{
  "apiToken": "${TOKEN}",
  "projectId": "${PROJECT_ID}"
}
EOF
```

提示：配置已保存到 `.claude/apifox.json`，建议加入 `.gitignore`（含 apiToken）。
