# Init 子命令

配置 Apifox API Token 和项目 ID。

## 步骤 1：读取现有配置

`init` 可脱离 push/pull 单独运行，拿不到它们写出的 `env.sh`，需完整 bootstrap 解析 `SKILL_DIR`：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
SKILL_DIR=""
for c in "${CLAUDE_PLUGIN_ROOT}/skills/apifox-sync" "${CLAUDE_PLUGIN_ROOT}" \
         "$(python3 -c 'import json,pathlib;d=json.loads((pathlib.Path.home()/".claude/plugins/installed_plugins.json").read_text("utf-8"));print(next((e[0]["installPath"] for k,e in d.get("plugins",{}).items() if k.split("@")[0]=="apifox-sync" and e),""))' 2>/dev/null)/skills/apifox-sync" \
         "$PROJECT_ROOT/skills/apifox-sync"; do
  [ -f "$c/scripts/load_config.py" ] && SKILL_DIR="$c" && break
done
[ -z "$SKILL_DIR" ] && echo "ERROR: 无法定位 apifox-sync skill 目录" && exit 1
cat > "${TMPPREFIX}env.sh" <<EOF
export SKILL_DIR="$SKILL_DIR"
export PROJECT_ROOT="$PROJECT_ROOT"
export TMPPREFIX="$TMPPREFIX"
eval "\$(python3 "\$SKILL_DIR/scripts/load_config.py" "\$PROJECT_ROOT")"
export PROJECT_ID="\${APIFOX_PROJECT_ID:-\$PID}"
export TOKEN PID PROJECT_ID APIFOX_DEBUG APIFOX_DEBUG_LOG APIFOX_SESSION_ID
EOF
source "${TMPPREFIX}env.sh"
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

合并写入，保留现有额外字段（如 `debug`）：
```bash
mkdir -p "${PROJECT_ROOT}/.claude"
python3 -c "
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / '.claude' / 'apifox.json'
cfg = {}
if p.is_file():
    try: cfg = json.loads(p.read_text('utf-8'))
    except Exception: pass
cfg['apiToken'] = sys.argv[2]
cfg['projectId'] = sys.argv[3]
p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', 'utf-8')
" "$PROJECT_ROOT" "$TOKEN" "$PROJECT_ID"
```

提示：配置已保存到 `.claude/apifox.json`，建议加入 `.gitignore`（含 apiToken）。

## 步骤 5：清理

`init` 可能被单独调用（不接续 push/pull），步骤 1 写出的 `${TMPPREFIX}env.sh` 若不清理会留在用户项目的 `.claude/.tmp/` 目录里：
```bash
rm -f "${TMPPREFIX}"env.sh
```
