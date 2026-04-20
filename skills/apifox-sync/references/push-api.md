# Push 步骤 8、10-12：文件夹选择、验证、推送、报告

API 常量见 `data/api-config.json`。临时文件统一放 `.claude/.tmp/`（已被 `.claude/` gitignore）。每次 Bash 调用开头：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
```
Python 子进程通过环境变量 `TMPPREFIX` 读取前缀。

---

## 步骤 8：获取文件夹并选择

**必须在 spec（步骤 9）之前执行**，spec 的 `x-apifox-folder` 需用户选择的路径。

调用 export-openapi（TOKEN/PROJECT_ID 由步骤 2 赋值，不回显）：
```bash
EXPORT_RESULT=$(curl -s -w "\n%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"oasVersion":"3.0","exportFormat":"JSON","options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true}}')
HTTP_CODE=$(echo "$EXPORT_RESULT" | tail -1); BODY=$(echo "$EXPORT_RESULT" | sed '$d')
```

HTTP：`200` → `echo "$BODY" > "${TMPPREFIX}export.json"`；`401/403` → 读 `references/init.md` 步骤 2-4 重配后重试；其他 → 中止。

**文件夹枚举由 `list_folders.py` 完成**（内置 Apifox 非法 `\` 转义容错，避免对话层内联 python 撞 `Invalid \escape`）：
```bash
python3 skills/apifox-sync/scripts/list_folders.py "${TMPPREFIX}export.json"
```
stdout 每行一个 folder 路径（按字典序；空行代表根目录）。对话层按行读入作为 `AskUserQuestion` 的候选。

`AskUserQuestion` 选目标：现有文件夹 + "新建（输入路径）" + "项目根目录"。"新建" 再问路径（`/` 多级）。空输出 → 直接问根目录或新建。

结果保存为 `TARGET_FOLDER` 传给步骤 9。Apifox 导入自动创建不存在的层级。

---

## 步骤 10：JSON 预验证

OpenAPI JSON 容错为零，推送前必须验证。

1. spec（indent=2 美化）写入临时文件：
```bash
cat > "${TMPPREFIX}spec.json" << 'SPECEOF'
{生成的 JSON}
SPECEOF
```
2. 验证（成功：stdout `JSON_VALID`，退出 0；失败：stderr `JSON_INVALID: line X col Y: <msg>`，退出 1）：
```bash
python3 skills/apifox-sync/scripts/verify_json.py "${TMPPREFIX}spec.json"
```
3. 失败 → 按 line/col/msg 定位修复（未转义引号、尾逗号、注释），最多 3 次。

---

## 步骤 11：推送到 Apifox

Apifox 以 path+method 匹配。为支持**同 path+method 跨文件夹独立存在**，按文件夹匹配分批推送。

> **已知限制**：一次性同步时，同 path+method 跨文件夹无法区分——AUTO_MERGE 会按 path+method 撞到任意一条。此类冲突在 11.2 被标为 skipped，需用户在 Apifox 手动清理后重推。

### 11.1 构建双向索引

`push_index.py` 读 `export.json`，写：
- `existing.json`：`{METHOD}:{path}` → `[folders]`（跨文件夹冲突判定）
- `by-source.json`：`x-source-method-fq` → `{path, method, folder, apifox_id, controller, summary}`（同 Java 方法 path/method 变更识别）

```bash
python3 skills/apifox-sync/scripts/push_index.py "${TMPPREFIX}export.json"
```

> **apifox_id 提取**：Apifox export 不回 `x-apifox-id`，脚本从 `x-run-in-apifox` URL（`.../apis/api-{id}-run`）正则提取。无 URL 的老接口 `apifox_id` 为空，11.4 显式列出"无法自动删除"。

### 11.2 分类并生成 payload

`push_classify.py` 读 spec + 双索引分四类：

- **update**：源码锚点命中 & 远程 path+method 与 spec 相同 → `AUTO_MERGE` 批次
- **rename**：源码锚点命中 & 远程 path/method 与 spec 不同 → 死接口清单
- **create**：锚点未命中 & 目标文件夹无同 path+method → `CREATE_NEW` 批次
- **skip**：目标文件夹与其他文件夹都有同 path+method → 跨文件夹冲突

无锚点老数据降级到 path+method 匹配。

```bash
python3 skills/apifox-sync/scripts/push_classify.py "${TMPPREFIX}spec.json"
```

按需写 `payload-update.json` / `payload-create.json` / `rename-list.json`，打印"更新/新建/重命名候选/跳过"摘要。

### 11.3 死接口用户确认

若 `rename-list.json` 存在，Apifox import-openapi 无法跨 path 精确更新，必须独立 DELETE 清理。

`AskUserQuestion` 决定：
```
检测到 {N} 个接口 path/method 变更：
  · UserController#updateUser
    旧: PUT /api/users           (folder=用户管理)
    新: PUT /api/users/{id}      (folder=用户管理)
  [ ] 全部删除旧接口
  [ ] 逐项选择
  [ ] 全部保留（只推新接口）
```

- **全部删除** → `cp rename-list.json rename-confirmed.json`
- **逐项选择** → multiSelect `AskUserQuestion`，过滤后写 `rename-confirmed.json`
- **全部保留** → 写入 `[]`

兜底：
```bash
if [ -f "${TMPPREFIX}rename-list.json" ] && [ ! -f "${TMPPREFIX}rename-confirmed.json" ]; then
  echo '[]' > "${TMPPREFIX}rename-confirmed.json"
fi
```

`apifox_id` 为空 → 跳过并提示手动处理。

### 11.4 调用 DELETE 清理旧接口

对 `rename-confirmed.json` 每一条调 Apifox 删除端点。URL baseUrl 是 `/api/v1/`（不是 `/v1/`）；首次 404 → fallback 改用 `/v1/`（见 `data/api-config.json`）。

**实现要点**：
- 必须在**父 shell 内**直接 curl（禁生成 .sh 用 `bash` 子进程）。子 shell 拿不到父 shell 未 export 的 TOKEN/PROJECT_ID，会 401/403。
- `push_delete_list.py` 铺平为 `{id}\t{method} {path}`，父 shell 用进程替换 `< <(...)` 消费。**禁用 pipe**（`|` 右侧在子 shell）。

```bash
if [ -f "${TMPPREFIX}rename-confirmed.json" ]; then
  while IFS=$'\t' read -r api_id label; do
    if [ -z "$api_id" ]; then
      echo "⚠️ 无 apifox_id，无法自动删除：${label}"
      continue
    fi
    R=$(curl -s -o "${TMPPREFIX}del-response.out" -w "%{http_code}" -X DELETE \
      "https://api.apifox.com/api/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
      -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28")
    if [ "$R" = "404" ]; then
      R=$(curl -s -o "${TMPPREFIX}del-response.out" -w "%{http_code}" -X DELETE \
        "https://api.apifox.com/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
        -H "Authorization: Bearer ${TOKEN}" -H "X-Apifox-Api-Version: 2024-03-28")
    fi
    echo "DELETE ${label} (id=${api_id}) -> HTTP ${R}"
  done < <(python3 skills/apifox-sync/scripts/push_delete_list.py)
fi
```

**状态码**：`200/204` 成功；`302/404` 视为已不存在（302 是 Apifox 对"目标已失效"的信号）；`401/403` Token 失效或缺"项目维护者"权限；其他 → 入"删除失败清单"。

### 11.5 分别推送

两批各自推（存在即推）：
```bash
for BATCH in update create; do
  F="${TMPPREFIX}payload-${BATCH}.json"
  [ -f "$F" ] || continue
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" \
    -d @"$F")
  echo "=== ${BATCH} 批次 ==="
  echo "$RESULT"
done
```

结果：`200` 成功；`401/403` 读 `references/init.md` 重配后重试；`400` spec 错误，显示返回信息；其他 → 显示状态码和响应体。

---

## 步骤 12：报告与清理

报告更新/新建/清理死接口/跨文件夹冲突数量、目标文件夹、项目链接 `https://app.apifox.com/project/${PROJECT_ID}`。

```
推送完成（目标文件夹：用户管理）
  · 更新 3 个接口
  · 新建 1 个接口
  · 清理 2 个死接口（path 变更）
  · 跳过 0 个跨文件夹冲突
```

删除失败单独列：
```
⚠️ 以下旧接口未能自动删除，请到 Apifox 手动清理：
  · PUT /api/users (HTTP 500)
```

清理临时文件：
```bash
rm -f "${TMPPREFIX}"spec.json "${TMPPREFIX}"export.json \
      "${TMPPREFIX}"existing.json "${TMPPREFIX}"by-source.json \
      "${TMPPREFIX}"payload-update.json "${TMPPREFIX}"payload-create.json \
      "${TMPPREFIX}"rename-list.json "${TMPPREFIX}"rename-confirmed.json \
      "${TMPPREFIX}"del-response.out
```
