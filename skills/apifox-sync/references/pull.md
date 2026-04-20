# Pull 步骤 1-7：配置、目录选择、导出、精简、保存

API 常量见 `data/api-config.json`。临时文件统一放 `.claude/.tmp/`。每次 Bash 调用开头：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
```
Python 子进程一律通过环境变量 `TMPPREFIX` 读取前缀。

---

## 步骤 1：加载配置

按优先级 env > `.claude/apifox.json`。调用 `load_config.py`（stdout 两行 `HAS_TOKEN=yes|no` + `PID=<id>`，不回显 Token）：
```bash
eval "$(python3 skills/apifox-sync/scripts/load_config.py "$PROJECT_ROOT")"
```
Token 本体由 Claude 对话层读取 `.claude/apifox.json` 的 `apiToken`（或 `$APIFOX_API_TOKEN`）赋值给 shell 变量 `TOKEN`；`PROJECT_ID="${APIFOX_PROJECT_ID:-$PID}"`。

> Token 或 ProjectId 为空时，**不要提示手动 init**，自动读 `references/init.md` 执行步骤 2-4 重配后继续。

---

## 步骤 2：获取目录结构

调用 export-openapi：
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

空输出 → 提示"项目中尚无接口"，中止。

---

## 步骤 3：用户选择目录

用 `AskUserQuestion`（`multiSelect: true`）：
- 选项 = 步骤 2 提取到的所有文件夹（已排序）
- 用户可同时选多个

示例：
```
请选择要拉取的目录（可多选）：
☐ 用户管理
☐ 用户管理/基础操作
☐ 设备管理
☐ 订单管理
```

把选中的名称列表写入 JSON 数组临时文件：
```bash
cat > "${TMPPREFIX}folders.json" << 'FEOF'
["用户管理", "设备管理"]
FEOF
```
**用 shell heredoc 写 JSON**（非 `python3 -c`），保证白名单只需允许 `cat` 的固定形态。

---

## 步骤 4：按接口切片 + 精简

`pull_extract.py` 完成全部工作：按 `x-apifox-folder` 分组（精确 + 前缀匹配）→ **为每个接口单独生成一个切片** → 递归收集该接口引用的 schema → 精简扩展字段 → 每个接口写一个 `${TMPPREFIX}pull-op-<hash>.json`（`hash` 由 `api_path.hash_key(folder, METHOD, path)` 生成，sha1 前 16 位）。

```bash
python3 skills/apifox-sync/scripts/pull_extract.py \
  --folders-file "${TMPPREFIX}folders.json" \
  "${TMPPREFIX}export.json"
```

stdout 按 folder 分组打印：`-- folder: "<folder>" (N 个接口) --` 标题 + 每接口 `OK: "<folder>" — <METHOD> <path> (<M>Schema) → <path>`。结尾 `TOTAL: N 个接口分布在 K 个 folder`。

> **为什么切到接口粒度**：folder 级聚合文件在接口多时很容易超过 Read 工具的单次读取上限。拆到接口粒度后，每个文件只含一个 operation + 它递归引用到的 schemas，AI 和人都能按需单独读取，互不干扰。

> **v1.4 命名规则**：最终落盘路径 `.claude/apis/<Apifox folder 原样层级>/<接口名>.json`，不再按 URL path 展开目录。`<接口名>` 取 `operation.summary`，非法字符 `<>:"/\|?*` 替换为 `_`；同 folder 内两个接口 summary 清洗后相同时，冲突双方的文件名都追加 `.<METHOD>` 后缀（例 `用户.POST.json`/`用户.GET.json`）。summary 为空时回退到 path 最后一段；根路径回退到 `_root`。


---

## 步骤 5：精简规则（内聚到 pull_extract.py）

脚本内实现；仅参考：
1. 不含顶层 `openapi`/`info`/`servers`/`tags`/`externalDocs`/`security`
2. 保留 `paths` + `components.schemas`
3. 删除冗余扩展（`x-apifox-name`/`x-apifox-id` 等）
4. 保留 `x-apifox-folder`/`x-apifox-status`/`x-apifox-enum`/`x-source-controller`/`x-source-method-fq`
5. 仅含被 paths 递归引用的 schemas

> **锚点保留**：`x-source-controller`/`x-source-method-fq` 由 push 写入，pull 保留便于识别来源；手动创建的接口无此字段。

---

## 步骤 5.5：本地/远程 diff 预览

落盘前先 diff，避免静默覆盖用户本地修改。**UX 仍按 folder 聚合提示**：虽然存储切到接口粒度，但用户心智模型依然是"选文件夹"。

```bash
python3 skills/apifox-sync/scripts/pull_diff.py "$PROJECT_ROOT"
```

**本地 ↔ 远程匹配方式**：不再依赖本地文件名（v1.3 按 URL path 拆目录的旧布局用户可能也想迁到新布局）。脚本递归扫描 `.claude/apis/<folder>/` 下所有 `.json`，**读取文件内部 `paths` 第一个 entry** → `(METHOD, path)`，与远端按 `(METHOD, path)` 对齐。v1.2 旧聚合文件 `.claude/apis/<folder>.json` 的多个接口也按同样方式展开，匹配上远端的会被重命名为新布局、未匹配上的算作远端已删除。

stdout 打印 `[NEW] / [SAME] / [DIFF]` 摘要 + 每个 folder 的"**目标结构**"预览（最终每个接口落到什么文件名）。`${TMPPREFIX}pull-diff.json` 每 folder 含：
- `new` / `updated` / `unchanged` / `removed`：接口级摘要，`updated` 条目若涉及重命名会带 `(重命名自 <旧文件名>)`；若来自 v1.2 旧聚合会带 `(拆分自 <xxx>.json(内部))`
- `target_layout`：该 folder 最终的目标文件清单（供用户直观确认）
- `legacy_file`：是否存在 v1.2 旧聚合文件

解析摘要后用 `AskUserQuestion` 询问：
- **全部覆盖**（推荐）→ move 所有接口切片到目标、迁移旧 folder 文件
- **逐目录选择**（multiSelect）→ 仅覆盖勾选的 folder，其他 folder 的切片删除
- **取消** → 删除所有临时文件，中止

特殊：
- 全部 `nochange` 且 `legacy_file: false` → 跳过询问，提示"远程无变化"，进入步骤 7 清理
- 全部 `nochange` 但 `legacy_file: true` → 仍询问（因为要做旧文件迁移）
- 全部 `new-file`（首次 pull）→ 建议默认全部保存，可跳过询问

写 `${TMPPREFIX}pull-approved.json`（actual_folder 字符串数组）：

**全部覆盖** → 调用：
```bash
python3 skills/apifox-sync/scripts/pull_approve_all.py
```

**逐目录选择** → Claude 按用户勾选写 JSON 数组文件：
```bash
cat > "${TMPPREFIX}pull-approved.json" << 'APEOF'
["分批测试/A", "用户管理"]
APEOF
```

**取消** → 不写 approved 文件，直接进入步骤 7 清理。

---

## 步骤 6：保存文件

`pull_save.py` 按 approved 清单落盘（每接口一个文件）：

- approved 中的 folder：
  1. 若存在 v1.2 旧聚合文件 `.claude/apis/<folder>.json` → 拆分其内部接口到临时占位文件后删除旧文件
  2. 扫描该 folder 目录（含新布局 + 步骤 1 占位），建 `(METHOD, path) → 本地文件` 索引
  3. 对远端每个接口：按 `<folder>/<接口名>.json` 计算目标；若同 `(METHOD, path)` 在本地是另一个文件名/路径 → 先删旧再写新；否则覆盖写入
  4. 该 folder 本地存在、但远程不再返回的接口 → 删除，并递归清理 folder 内空子目录
- 未 approve 的 folder：删除其所有接口切片临时文件

**命名规则**（v1.4）：由 `api_path.op_filename(summary, method, path, with_method)` 生成：
- `summary` 清洗非法字符后作为文件名主体
- 同 folder 内多接口清洗后重名 → 冲突双方都追加 `.<METHOD>` 后缀
- `summary` 为空 → 用 `path` 最后一段作为 fallback（根路径 `/` → `_root`）

**路径映射示例**（Apifox folder = `用户服务/v1/用户管理`）：
- summary=`创建用户`，POST `/api/users`
  → `.claude/apis/用户服务/v1/用户管理/创建用户.json`
- summary=`用户`，POST `/api/users` + summary=`用户`，GET `/api/users/{id}` 冲突
  → 双方都带 METHOD：`用户.POST.json` / `用户.GET.json`
- summary=`` (空)，GET `/api/users` → `users.json`（path 末段）

```bash
python3 skills/apifox-sync/scripts/pull_save.py "$PROJECT_ROOT"
```

stdout 按行打印 `SAVED:` / `REMOVED:` / `MIGRATED:` / `SKIP:`。

---

## 步骤 7：结果摘要与清理

输出：拉取的目录数、每个目录的接口数、保存路径。接口以独立文件落盘。

示例：
```
拉取完成！

| 目录 | 接口数 | 保存路径 |
|------|--------|---------|
| 用户管理 | 5 | .claude/apis/用户管理/ |
| 订单管理/基础 | 8 | .claude/apis/订单管理/基础/ |

共拉取 2 个目录，13 个接口文件。
```

清理：
```bash
rm -f "${TMPPREFIX}export.json" \
      "${TMPPREFIX}pull-diff.json" \
      "${TMPPREFIX}pull-approved.json" \
      "${TMPPREFIX}folders.json" \
      "${TMPPREFIX}existing.json" \
      "${TMPPREFIX}by-source.json"
find "${PROJECT_ROOT}/.claude/.tmp" -maxdepth 1 -name "apifox-sync-pull-op-*.json" -delete 2>/dev/null
```
