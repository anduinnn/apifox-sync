# Pull 步骤 1-7：配置、目录选择、导出、精简、保存

临时文件统一放 `.claude/.tmp/`。每次 Bash 调用开头：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
```

## 步骤 1：加载配置

```bash
eval "$(python3 skills/apifox-sync/scripts/load_config.py "$PROJECT_ROOT")"
```

**Debug 模式传递**：`load_config.py` 会输出 `APIFOX_DEBUG=0|1`。当 `APIFOX_DEBUG=1` 时，还会输出 `APIFOX_DEBUG_LOG` 和 `APIFOX_SESSION_ID`。eval 后这些变量即可用。在首次 eval 后设置 trap 以确保流程结束时（无论成功或失败）输出执行摘要：
```bash
if [ "$APIFOX_DEBUG" = "1" ]; then
  export APIFOX_DEBUG APIFOX_DEBUG_LOG APIFOX_SESSION_ID
  trap 'python3 skills/apifox-sync/scripts/debug_log.py --summary "$APIFOX_DEBUG_LOG"' EXIT
fi
```
eval 后 `TOKEN`、`PID`、`HAS_TOKEN` 等变量直接可用；`PROJECT_ID="${APIFOX_PROJECT_ID:-$PID}"`。`HAS_TOKEN=no` 或 `PID` 为空时，自动读 `references/init.md` 步骤 2-4 重配后继续。

## 步骤 2：获取目录结构

调用 export-openapi 获取全量数据 → 写入 `${TMPPREFIX}export.json`（`200` 写文件；`401/403` 读 `references/init.md` 重配后重试；其他中止）：
```bash
python3 skills/apifox-sync/scripts/list_folders.py "${TMPPREFIX}export.json"
```
stdout 每行一个 folder（按字典序；空行代表根目录）。空输出 → 提示"项目中尚无接口"，中止。

## 步骤 2.5：解析 pull 参数

从 `{{ARGUMENTS}}` 中去掉 `pull` 后，将剩余参数传入 `detect_mode.py`，由脚本自动完成匹配：

```bash
DETECT_RESULT=$(python3 skills/apifox-sync/scripts/detect_mode.py \
  "${TMPPREFIX}export.json" "用户参数")
```

无参数时省略第二个 argv：
```bash
DETECT_RESULT=$(python3 skills/apifox-sync/scripts/detect_mode.py \
  "${TMPPREFIX}export.json")
```

**退出码非 0 → 脚本已在 stderr 列出可用目录，中止流程。**

脚本输出 JSON，按 `mode` 字段设置后续变量：

| mode            | 含义                               | 后续动作                                                   |
|-----------------|------------------------------------|------------------------------------------------------------|
| `interactive`   | 无参数                             | `PULL_MODE=interactive`，用 `all_folders` 展示给用户       |
| `folder`        | 精确目录匹配                       | `PULL_MODE=folder`，`PULL_FOLDER=folder` 字段值            |
| `api`           | 接口匹配（目录/接口名 或关键词搜索） | `PULL_MODE=api`，从 `apis` 数组提取 folder 和接口列表      |

匹配优先级（脚本内部实现，无需 Claude 手动判断）：
1. 无参数 → interactive
2. 参数与已有 folder 精确匹配 → folder
3. 参数格式 `<目录>/<接口名>` 且目录匹配 → api
4. 以上都不匹配 → 搜索所有 operation 的 summary（包含匹配）→ api
5. 仍无匹配 → 非零退出码，中止

## 步骤 3：确定目标目录

**交互模式**（`PULL_MODE=interactive`）：`AskUserQuestion`（`multiSelect: true`）展示文件夹列表，把选中列表写入：
```bash
cat > "${TMPPREFIX}folders.json" << 'FEOF'
["用户管理", "设备管理"]
FEOF
```

**直接模式**（`PULL_MODE=folder`）：验证 `PULL_FOLDER` 在步骤 2 的目录列表中存在，直接写入。不存在时列出可用目录，中止。

**接口模式**（`PULL_MODE=api`）：
- 若 `PULL_FOLDER` 已确定（`目录/接口名` 格式）→ 写入该目录
- 若由关键词搜索确定 → 从匹配结果中提取所有涉及的 folder，写入这些 folder

```bash
cat > "${TMPPREFIX}folders.json" << 'FEOF'
["用户管理"]
FEOF
```

## 步骤 4：按接口切片 + 精简

`pull_extract.py` 完成：按 `x-apifox-folder` 分组（精确 + 前缀匹配）→ 每个接口单独切片 → 递归收集引用的 schema → 精简扩展字段 → 写 `${TMPPREFIX}pull-op-<hash>.json`。
```bash
python3 skills/apifox-sync/scripts/pull_extract.py \
  --folders-file "${TMPPREFIX}folders.json" \
  "${TMPPREFIX}export.json"
```
stdout 结尾 `TOTAL: N 个接口分布在 K 个 folder`。

**命名规则（v1.4）**：落盘路径 `.claude/apis/<folder 原样>/<接口名>.json`；接口名取 `summary`，非法字符替换为 `_`；同 folder 内重名时双方追加 `.<METHOD>` 后缀；summary 为空回退到 path 末段；根路径回退到 `_root`。

## 步骤 5：精简规则（内聚到 pull_extract.py）

保留 `paths` + `components.schemas`（仅被引用的 schemas）；删除顶层 `openapi/info/servers/tags` 等及冗余扩展（`x-apifox-name/id` 等）；保留 `x-apifox-folder/status/enum`、`x-source-controller/method-fq`。

## 步骤 5.5：本地/远程 diff 预览

```bash
python3 skills/apifox-sync/scripts/pull_diff.py "$PROJECT_ROOT"
```
本地 ↔ 远端按 `(METHOD, path)` 对齐（不依赖文件名）；v1.2 旧聚合文件自动展开。stdout 打印 `[NEW]/[SAME]/[DIFF]` 摘要 + 每 folder 目标结构预览。写 `${TMPPREFIX}pull-diff.json`（含 `new/updated/unchanged/removed/target_layout/legacy_file`）。

**直接模式**（`PULL_MODE=folder`）：跳过询问，自动全量 approve：
```bash
python3 skills/apifox-sync/scripts/pull_approve_all.py
```

**接口模式**（`PULL_MODE=api`）：跳过询问，遍历步骤 4 生成的所有 `${TMPPREFIX}pull-op-*.json` 切片，读取每个切片内部 operation 的 `summary`，筛选出 summary 包含 `PULL_API_NAME` 关键词的接口（若由关键词搜索进入则使用步骤 2.5 已匹配的结果），自动写 API 模式 approved：
```bash
cat > "${TMPPREFIX}pull-approved.json" << 'APEOF'
{"mode": "api", "items": [
  {"folder": "用户管理", "method": "GET", "path": "/api/users"},
  {"folder": "用户管理", "method": "POST", "path": "/api/users"}
]}
APEOF
```
若无匹配接口，列出所有可用接口供参考，中止。

**交互模式**（`PULL_MODE=interactive`）：`AskUserQuestion` 询问：
- **全部覆盖**（推荐）→ `python3 skills/apifox-sync/scripts/pull_approve_all.py`
- **逐接口选择** → 按下方「逐接口选择流程」处理
- **取消** → 删除临时文件，中止

特殊：全部 `nochange` 且无旧文件 → 跳过询问提示"远程无变化"；全部首次新增 → 建议默认全部保存。

### 逐接口选择流程

读取 `${TMPPREFIX}pull-diff.json`，对每个 `status` 为 `"new-file"` 或 `"diff"` 的 folder：
1. 从该 folder 的 `new` + `updated` 列表提取可选接口（格式：`METHOD path → filename`）
2. 用 `AskUserQuestion`（`multiSelect: true`）展示，header 为 folder 名
3. 用户勾选要更新的接口

将所有选中的接口写入 `${TMPPREFIX}pull-approved.json`，**使用 API 模式格式**：
```bash
cat > "${TMPPREFIX}pull-approved.json" << 'APEOF'
{"mode": "api", "items": [
  {"folder": "用户管理", "method": "GET", "path": "/api/users"},
  {"folder": "用户管理", "method": "POST", "path": "/api/users"}
]}
APEOF
```

`pull_save.py` 在 API 模式下只写入选中的接口，**不会删除**本地未选中但已存在的接口。

## 步骤 6：保存文件

```bash
python3 skills/apifox-sync/scripts/pull_save.py "$PROJECT_ROOT"
```
按 approved 清单落盘：v1.2 旧文件迁移 + `(METHOD,path)` 索引匹配 + 删除远端已不存在的本地接口；未 approve 的 folder 删除临时切片。stdout 按行打印 `SAVED:/REMOVED:/MIGRATED:/SKIP:`。

## 步骤 7：结果摘要与清理

输出拉取目录数、每目录接口数、保存路径表格。清理：
```bash
rm -f "${TMPPREFIX}export.json" "${TMPPREFIX}pull-diff.json" \
      "${TMPPREFIX}pull-approved.json" "${TMPPREFIX}folders.json" \
      "${TMPPREFIX}existing.json" "${TMPPREFIX}by-source.json"
find "${PROJECT_ROOT}/.claude/.tmp" -maxdepth 1 -name "apifox-sync-pull-op-*.json" -delete 2>/dev/null
```
