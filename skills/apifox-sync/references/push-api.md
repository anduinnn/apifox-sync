# Push 步骤 8、10-12：文件夹选择、验证、推送、报告

临时文件统一放 `.claude/.tmp/`。每次 Bash 调用开头：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
source "${PROJECT_ROOT}/.claude/.tmp/apifox-sync-env.sh"
```

**Debug 模式**：debug 环境变量由 preamble 自动从 env 文件恢复。当 `APIFOX_DEBUG=1` 时，curl 调用前后通过 `debug_log.py --format-entry` 记录日志：
```bash
if [ "$APIFOX_DEBUG" = "1" ]; then
  _start=$(python3 -c "import time; print(int(time.time()*1000))")
fi
# ... curl 调用 ...
if [ "$APIFOX_DEBUG" = "1" ]; then
  _end=$(python3 -c "import time; print(int(time.time()*1000))")
  _dur=$((_end - _start))
  python3 "$SKILL_DIR/scripts/debug_log.py" --format-entry \
    --session-id "$APIFOX_SESSION_ID" --step "push.<step_name>" \
    --status "success" --duration-ms "$_dur" --http-status "$_http_code" \
    --command "curl *** <url>" >> "$APIFOX_DEBUG_LOG"
fi
```
对步骤 11.4（DELETE 死接口）和步骤 11.5（import-openapi）的 curl 调用均适用此模式。

## 步骤 8：获取文件夹并选择

**必须在步骤 9 之前执行**，spec 的 `x-apifox-folder` 需用户选择的路径。

调用 export-openapi 获取全量数据 → 写入 `${TMPPREFIX}export.json`（`401/403` → 读 `references/init.md` 重配后重试；其他非 200 → 中止）：
```bash
python3 "$SKILL_DIR/scripts/list_folders.py" "${TMPPREFIX}export.json"
```
stdout 每行一个 folder（按字典序；空行代表根目录）。`AskUserQuestion` 选目标：现有文件夹 + "新建（输入路径）" + "项目根目录"。"新建"再问路径。空输出 → 直接问根目录或新建。结果保存为 `TARGET_FOLDER` 传给步骤 9。

## 步骤 10：JSON 预验证

写入临时文件后验证（成功 stdout `JSON_VALID` 退出 0；失败 stderr `JSON_INVALID: line X col Y: <msg>` 退出 1）：
```bash
cat > "${TMPPREFIX}spec.json" << 'SPECEOF'
{生成的 JSON}
SPECEOF
python3 "$SKILL_DIR/scripts/verify_json.py" "${TMPPREFIX}spec.json"
```
失败 → 按 line/col/msg 定位修复（未转义引号、尾逗号、注释），最多 3 次。

## 步骤 11：推送到 Apifox

### 11.1 构建双向索引

```bash
python3 "$SKILL_DIR/scripts/push_index.py" "${TMPPREFIX}export.json"
```
写 `existing.json`（`METHOD:path` → folders）和 `by-source.json`（`x-source-method-fq` → 接口元数据）。

### 11.2 分类并生成 payload

```bash
python3 "$SKILL_DIR/scripts/push_classify.py" "${TMPPREFIX}spec.json"
```
四类：**update**（锚点命中 & path+method 同）→ `AUTO_MERGE`；**rename**（锚点命中 & path/method 变）→ 死接口清单；**create**（锚点未命中 & 无冲突）→ `CREATE_NEW`；**skip**（跨文件夹冲突）。按需写 `payload-update.json`/`payload-create.json`/`rename-list.json`，打印摘要。

### 11.3 死接口用户确认

若 `rename-list.json` 存在，用 `AskUserQuestion` 展示变更列表（旧 path → 新 path），选项：**全部删除**（`cp rename-list.json rename-confirmed.json`）/ **逐项选择**（multiSelect 过滤后写）/ **全部保留**（写入 `[]`）。兜底：`[ ! -f "${TMPPREFIX}rename-confirmed.json" ] && echo '[]' > "${TMPPREFIX}rename-confirmed.json"`

### 11.4 DELETE 清理旧接口

**关键要点**：在父 shell 直接 curl（禁生成 .sh 子进程，否则 TOKEN 不可见）；用进程替换 `< <(...)` 消费（禁用 pipe）。URL 先试 `/api/v1/`，404 fallback 到 `/v1/`。

状态码：`200/204` 成功；`302/404` 已不存在；`401/403` Token 失效；其他 → 入删除失败清单。

```bash
if [ -f "${TMPPREFIX}rename-confirmed.json" ]; then
  while IFS=$'\t' read -r api_id label; do
    [ -z "$api_id" ] && echo "⚠️ 无 apifox_id，跳过：${label}" && continue
    R=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      "https://api.apifox.com/api/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
      -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28")
    [ "$R" = "404" ] && R=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
      "https://api.apifox.com/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
      -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28")
    echo "DELETE ${label} -> HTTP ${R}"
  done < <(python3 "$SKILL_DIR/scripts/push_delete_list.py")
fi
```

### 11.5 分别推送

```bash
for BATCH in update create; do
  F="${TMPPREFIX}payload-${BATCH}.json"
  [ -f "$F" ] || continue
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" -d @"$F")
  echo "=== ${BATCH} 批次 ===" && echo "$RESULT"
done
```
`200` 成功；`401/403` 读 `references/init.md` 重配后重试；`400` spec 错误显示返回信息；其他显示状态码和响应体。

## 步骤 12：报告与清理

报告更新/新建/清理/跳过数量、目标文件夹、项目链接 `https://app.apifox.com/project/${PROJECT_ID}`。删除失败的旧接口单独列出提示手动清理。

```bash
rm -f "${TMPPREFIX}"spec.json "${TMPPREFIX}"export.json \
      "${TMPPREFIX}"existing.json "${TMPPREFIX}"by-source.json \
      "${TMPPREFIX}"payload-update.json "${TMPPREFIX}"payload-create.json \
      "${TMPPREFIX}"rename-list.json "${TMPPREFIX}"rename-confirmed.json \
      "${TMPPREFIX}"del-response.out \
      "${TMPPREFIX}"env.sh "${PROJECT_ROOT}/.claude/.tmp/apifox-debug-env.sh"
```
