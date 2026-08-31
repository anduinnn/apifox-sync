# push 流程加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 push 时 schema 被静默覆盖的风险，让参考文档照做即可执行，并补齐推送后的正确性校验。

**Architecture:** 三个新脚本（冲突预检、回读校验、folder 推荐）挂进现有 push 管线；同时把散落在 19 处的脚本相对路径统一到一次性解析出的 `$SKILL_DIR`，并用一个 `env.sh` 取代每次 Bash 调用重写的样板。所有脚本沿用本仓既有约定：单文件、内嵌 `--self-test`、`debug_log` 接线、退出码 0/1/2。

**Tech Stack:** Python 3（标准库，不新增依赖）、Bash、Markdown 参考文档、Apifox Open API。

**Spec:** `docs/superpowers/specs/2026-08-28-push-hardening-design.md`

## Global Constraints

- **不新增依赖**：只用 Python 3 标准库；shell 侧只用 `git` / `curl` / `python3`。
- **中文**：代码注释、日志、stdout 提示一律中文；标识符保持英文。
- **脚本约定**：每个脚本含模块 docstring（用法/argv/env/输出/stdout 摘要/退出码）、`from __future__ import annotations`、`sys.path.insert(0, ...)` 后导入同目录模块、`self_test()` 成功时打印 `SELFTEST_OK`、`main(argv)` 处理 `-h` / `--self-test` / 用法错误。
- **退出码**：`0` 成功 / `1` 运行时错误 / `2` 参数用法错误。
- **debug 接线**：`main()` 中计时并调用 `debug_log("push.<脚本名>", "success"|"error", 耗时ms, ...)`。
- **Token 安全**：禁止把 Token 值写入任何文件或 stdout。
- **版本**：`1.6.3` → `1.7.0`，改 `.claude-plugin/plugin.json` 的 `version` 和 `.claude-plugin/marketplace.json` 的 `plugins[0].version` 与顶层 `version`（2 文件 3 字段），**折进最后一个功能 commit**，不单独成 commit。
- **分支**：全程在 `feature/issue-2-push-hardening` 上提交，禁止直接提交 `main`。
- **commit message**：中文，纯描述式无前缀（本仓惯例，见 `git log`），只写功能概要不附技术细节。

---

### Task 1: SKILL_DIR 解析与 env.sh preamble

把 19 处脚本相对路径统一到 `$SKILL_DIR`，并用一次性 bootstrap 写出的 `env.sh` 取代每次 Bash 调用的 4 行样板。这是后续所有文档改动的基础，必须先做。

**Files:**
- Modify: `skills/apifox-sync/references/push-parse.md`（步骤 2 改 bootstrap，trap 内路径）
- Modify: `skills/apifox-sync/references/pull.md`（preamble + 步骤 1 改 bootstrap，共 10 处路径）
- Modify: `skills/apifox-sync/references/push-api.md`（preamble 改 source，6 处路径，步骤 12 的 `rm -f` 列表）
- Modify: `skills/apifox-sync/references/init.md`（**独立 bootstrap**，1 处路径）
- Modify: `skills/apifox-sync/SKILL.md`（注意事项 6 增补 SKILL_DIR 约定）

**Interfaces:**
- Produces：后续所有任务的文档改动统一使用 `python3 "$SKILL_DIR/scripts/xxx.py"` 形式；`${TMPPREFIX}env.sh` 提供 `SKILL_DIR` / `PROJECT_ROOT` / `TMPPREFIX` / `TOKEN` / `PID` / `PROJECT_ID` / `APIFOX_DEBUG` / `APIFOX_DEBUG_LOG` / `APIFOX_SESSION_ID`。

**注意**：`init` 可脱离 push/pull 单独运行（`/apifox-sync init`），拿不到 push/pull 写出的 env.sh，因此 `init.md` 必须有自己的 bootstrap，不能只写 `source`。

- [ ] **Step 1: 验证三级降级的第 2、3 级真实可解析**

先确认解析逻辑在本机成立，再写进文档。

```bash
# 第 2 级：installed_plugins.json 的 installPath
python3 -c 'import json,pathlib;d=json.loads((pathlib.Path.home()/".claude/plugins/installed_plugins.json").read_text("utf-8"));print(next((e[0]["installPath"] for k,e in d.get("plugins",{}).items() if k.split("@")[0]=="apifox-sync" and e),""))'
# 第 3 级：本仓路径
ls skills/apifox-sync/scripts/load_config.py
```

Expected：第 1 条打印一个以 `/apifox-sync/` 结尾的绝对路径且该路径下存在 `skills/apifox-sync/scripts/load_config.py`；第 2 条正常列出文件。

- [ ] **Step 2: 端到端验证 bootstrap 片段**

把下面片段存成临时文件跑一遍，确认它能解析出 SKILL_DIR、写出 env.sh、且 `source` 后变量齐全。

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
echo "SKILL_DIR=$SKILL_DIR"
echo "HAS_TOKEN=$HAS_TOKEN PID=$PID"
```

Expected：打印出 `SKILL_DIR=` 一个存在的绝对路径，`HAS_TOKEN` 有值。**不得打印 Token 本身**。

- [ ] **Step 3: 确认 env.sh 里没有 Token 明文**

Run: `grep -c 'afxp_' "${TMPPREFIX}env.sh" || echo 0`
Expected：`0`。env.sh 只含 `eval load_config.py` 这一行，不含 Token 值。

- [ ] **Step 4: 改写 push-parse.md 步骤 2**

把现有 4 行 preamble + `eval load_config` 替换为 Step 2 的完整 bootstrap 片段。trap 那段的路径改为：

```bash
if [ "$APIFOX_DEBUG" = "1" ]; then
  trap 'python3 "$SKILL_DIR/scripts/debug_log.py" --summary "$APIFOX_DEBUG_LOG"' EXIT
fi
```
（`export APIFOX_DEBUG ...` 一行删除——env.sh 已 export。）

- [ ] **Step 5: 改写 pull.md**

顶部 preamble 改为两行简版；步骤 1 改为完整 bootstrap（同 Step 2 片段）；其余 9 处路径改 `"$SKILL_DIR/scripts/xxx.py"`。

顶部简版 preamble：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
source "${PROJECT_ROOT}/.claude/.tmp/apifox-sync-env.sh"
```

- [ ] **Step 6: 改写 push-api.md preamble 与路径**

顶部 preamble 换成 Step 5 的两行简版（删掉 `apifox-debug-env.sh` 那行）；6 处路径改 `$SKILL_DIR`；步骤 12 的 `rm -f` 列表里把 `apifox-debug-env.sh` 换成 `"${TMPPREFIX}"env.sh`。

- [ ] **Step 7: 改写 init.md**

步骤 1 换成完整 bootstrap（init 独立运行拿不到 env.sh）。

- [ ] **Step 8: SKILL.md 注意事项增补**

在注意事项 6 后追加：

```markdown
7. **SKILL_DIR 定位**：脚本不在用户项目内，须经三级降级解析：`$CLAUDE_PLUGIN_ROOT` → `~/.claude/plugins/installed_plugins.json` 的 `installPath` → `$PROJECT_ROOT/skills/apifox-sync`。由 push 步骤 2 / pull 步骤 1 / init 步骤 1 的 bootstrap 一次性写入 `${TMPPREFIX}env.sh`，后续 Bash 调用只需 `source`
```
（原第 7 条「幂等性」顺延为第 8 条。）

- [ ] **Step 9: 验证替换彻底**

```bash
echo "残留相对路径（应为 0）:"; grep -rn 'python3 skills/apifox-sync/scripts/' skills/ | wc -l
echo "未走 SKILL_DIR 的脚本路径（应为 0）:"; grep -rn 'scripts/[a-z_]*\.py' skills/ --include='*.md' | grep -v 'SKILL_DIR' | wc -l
echo "已无 apifox-debug-env 引用（应为 0）:"; grep -rn 'apifox-debug-env' skills/ | wc -l
```
Expected：三行都是 `0`。

- [ ] **Step 10: Commit**

```bash
git add skills/apifox-sync/SKILL.md skills/apifox-sync/references/
git commit -m "统一脚本路径为 SKILL_DIR，preamble 收敛为 env.sh 单次加载"
```

---

### Task 2: export-openapi 命令补齐 + folder 推荐预填

同时修掉 issue 2.1（步骤 8 与 pull 步骤 2 都缺 export-openapi 命令）和 3.4（folder 预填），因为两者改的是同一段步骤 8。

**Files:**
- Create: `skills/apifox-sync/scripts/suggest_folder.py`
- Modify: `skills/apifox-sync/references/push-api.md`（步骤 8）
- Modify: `skills/apifox-sync/references/pull.md`（步骤 2）

**Interfaces:**
- Consumes：Task 1 的 `$SKILL_DIR`、`$TMPPREFIX`、`$TOKEN`、`$PROJECT_ID`。
- Produces：`suggest_folder.py <export_json> <controller_fq>`，stdout 每行一个 folder，按接口数降序、同数按名称字典序；无匹配则无输出、退出码 0。

- [ ] **Step 1: 写 suggest_folder.py 的失败自测**

创建 `skills/apifox-sync/scripts/suggest_folder.py`，先只写 docstring + `self_test()` + `main()`，`suggest()` 留空未定义。自测内容：

```python
def self_test() -> int:
    fixture = {
        "paths": {
            "/a": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "用户管理"}},
            "/b": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "用户管理"}},
            "/c": {"get": {"x-source-controller": "com.x.UserController",
                           "x-apifox-folder": "旧模块"}},
            "/d": {"get": {"x-source-controller": "com.x.OtherController",
                           "x-apifox-folder": "其他"}},
            "/e": {"get": {"x-apifox-folder": "无锚点"}},
        }
    }
    # 1) 按接口数降序：用户管理(2) 在 旧模块(1) 前
    assert suggest(fixture, "com.x.UserController") == ["用户管理", "旧模块"]
    # 2) 只命中一个
    assert suggest(fixture, "com.x.OtherController") == ["其他"]
    # 3) 无匹配 → 空列表
    assert suggest(fixture, "com.x.GhostController") == []
    # 4) 同数按名称字典序（确定性，不依赖插入序）
    tie = {"paths": {
        "/x": {"get": {"x-source-controller": "C", "x-apifox-folder": "乙"}},
        "/y": {"get": {"x-source-controller": "C", "x-apifox-folder": "甲"}},
    }}
    assert suggest(tie, "C") == ["甲", "乙"], suggest(tie, "C")
    # 5) paths 缺失不崩溃
    assert suggest({}, "C") == []
    print("SELFTEST_OK")
    return 0
```

- [ ] **Step 2: 跑自测确认失败**

Run: `python3 skills/apifox-sync/scripts/suggest_folder.py --self-test`
Expected：FAIL，`NameError: name 'suggest' is not defined`

- [ ] **Step 3: 实现 suggest()**

```python
def suggest(export: dict, controller_fq: str) -> list[str]:
    """返回该 Controller 已有接口所在的 folder，按接口数降序、同数按名称字典序。"""
    counts: dict[str, int] = {}
    paths = export.get("paths", {})
    if not isinstance(paths, dict):
        return []
    for methods in paths.values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            if detail.get("x-source-controller", "") == controller_fq:
                folder = detail.get("x-apifox-folder", "")
                counts[folder] = counts.get(folder, 0) + 1
    return [f for f, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def run(export_path: str, controller_fq: str) -> int:
    data = load_json_loose(export_path)
    for folder in suggest(data, controller_fq):
        print(folder)
    return 0
```

模块头部按本仓约定：
```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_safe import load_json_loose  # noqa: E402
from debug_log import debug_log  # noqa: E402
```

`main()` 接受 2 个位置参数（`<export_json> <controller_fq>`），`-h` / `--self-test` 各占 1 参，其余返回 2。

- [ ] **Step 4: 跑自测确认通过**

Run: `python3 skills/apifox-sync/scripts/suggest_folder.py --self-test`
Expected：`SELFTEST_OK`

- [ ] **Step 5: push-api.md 步骤 8 补 curl 与推荐**

把步骤 8 那句纯文字替换为完整命令，并加必需性标注：

````markdown
## 步骤 8：获取文件夹并选择

**必须在步骤 9 之前执行**，spec 的 `x-apifox-folder` 需用户选择的路径。

```bash
HTTP=$(curl -s -o "${TMPPREFIX}export.json" -w "%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"scope":{"type":"ALL"},"options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true},"oasVersion":"3.0","exportFormat":"JSON"}')
```

⚠️ **`includeApifoxExtensionProperties: true` 必需**。漏掉则导出不含 `x-source-method-fq` / `x-apifox-folder` / `x-source-controller`，`push_index.py` 建出空索引，`push_classify.py` 会把**全部接口判为 create** 导致重复创建——且 counters 表面正常，故障完全静默。

`401/403` → 读 `references/init.md` 重配后重试；其他非 200 → 中止。

```bash
python3 "$SKILL_DIR/scripts/list_folders.py" "${TMPPREFIX}export.json"
python3 "$SKILL_DIR/scripts/suggest_folder.py" "${TMPPREFIX}export.json" "<步骤3的全限定类名>"
```

第一条 stdout 每行一个 folder（按字典序；空行代表根目录）；第二条输出该 Controller 上次推送的 folder（可能为空）。

`AskUserQuestion` 选目标：**若 `suggest_folder.py` 有输出，把首行作为第一个选项并标注「当前 Controller 上次推送位置」**，其后接其余现有文件夹 + "新建（输入路径）" + "项目根目录"。"新建"再问路径。空输出 → 直接问根目录或新建。结果保存为 `TARGET_FOLDER` 传给步骤 9。

**仍须询问**，不因有推荐就自动选定：选错目录代价高且事后难清理。
````

- [ ] **Step 6: pull.md 步骤 2 补同一条 curl**

pull 步骤 2 有和步骤 8 完全相同的缺口。在 `list_folders.py` 调用前补上同样的 curl 块与 `includeApifoxExtensionProperties: true` 必需标注（pull 侧后果是 `pull_extract.py` 精简扩展字段时拿不到锚点）。

- [ ] **Step 7: 验证**

```bash
python3 skills/apifox-sync/scripts/suggest_folder.py --self-test
python3 skills/apifox-sync/scripts/suggest_folder.py -h >/dev/null && echo "HELP_OK"
grep -c 'includeApifoxExtensionProperties' skills/apifox-sync/references/push-api.md skills/apifox-sync/references/pull.md
```
Expected：`SELFTEST_OK`、`HELP_OK`、两个文件各 ≥1。

- [ ] **Step 8: Commit**

```bash
git add skills/apifox-sync/scripts/suggest_folder.py skills/apifox-sync/references/push-api.md skills/apifox-sync/references/pull.md
git commit -m "补齐 export-openapi 命令与扩展字段必需说明，folder 选择预填推荐项"
```

---

### Task 3: schema 冲突检测

新脚本的检测模式。核心难点是**传递闭包**——只归属 operation 的直接 `$ref` 会漏报仅被其他 schema 间接引用的 schema。

**Files:**
- Create: `skills/apifox-sync/scripts/push_schema_conflict.py`

**Interfaces:**
- Produces：
  - `refs_of(node) -> set[str]`：递归收集节点内所有 `$ref` 指向的 schema 简名
  - `build_owners(export: dict) -> dict[str, set[str]]`：schema 名 → 占用它的 controller 集合（含传递闭包）；无 `x-source-controller` 的 operation 归属为 `""`
  - `find_conflicts(spec: dict, export: dict) -> list[dict]`：返回 `[{"schema": str, "owners": list[str]}, ...]`，按 schema 名字典序
  - 检测模式写 `${TMPPREFIX}schema-conflicts.json`（仅在有冲突时写）

- [ ] **Step 1: 写失败自测**

创建脚本，先写 docstring + `self_test()` + `main()`，三个核心函数留空未定义。

```python
def self_test() -> int:
    export = {
        "paths": {
            # 同源：UserController 引用 Location
            "/u": {"get": {"x-source-controller": "com.x.UserController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Location"}}}}}}},
            # 跨源：OrderController 引用 Money
            "/o": {"get": {"x-source-controller": "com.x.OrderController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Money"}}}}}}},
            # 跨源 + 间接：DeviceController 只直接引用 Wrapper，Wrapper 引用 Detail
            "/d": {"get": {"x-source-controller": "com.x.DeviceController",
                           "responses": {"200": {"content": {"application/json": {
                               "schema": {"$ref": "#/components/schemas/Wrapper"}}}}}}},
        },
        "components": {"schemas": {
            "Location": {"type": "object"},
            "Money": {"type": "object"},
            "Wrapper": {"type": "object",
                        "properties": {"detail": {"$ref": "#/components/schemas/Detail"}}},
            "Detail": {"type": "object"},
            "Ghost": {"type": "object"},          # 孤儿：远端存在但无接口引用
        }},
    }
    spec = {
        "paths": {"/u": {"get": {"x-source-controller": "com.x.UserController"}}},
        "components": {"schemas": {
            "Location": {}, "Money": {}, "Detail": {}, "Ghost": {}, "Brand新": {},
        }},
    }

    owners = build_owners(export)
    # 传递闭包：Detail 虽只被 Wrapper 引用，也应归属 DeviceController
    assert owners["Detail"] == {"com.x.DeviceController"}, owners.get("Detail")
    assert owners["Location"] == {"com.x.UserController"}
    assert "Ghost" not in owners, "孤儿 schema 不应有归属"

    conflicts = find_conflicts(spec, export)
    names = [c["schema"] for c in conflicts]
    # Location 同源 → 不报；Money/Detail 跨源 → 报；Ghost 无主 → 报；Brand新 远端没有 → 不报
    assert names == ["Detail", "Ghost", "Money"], names
    assert conflicts[2]["owners"] == ["com.x.OrderController"], conflicts[2]
    ghost = [c for c in conflicts if c["schema"] == "Ghost"][0]
    assert ghost["owners"] == [""], ghost   # 来源未知
    print("SELFTEST_OK")
    return 0
```

- [ ] **Step 2: 跑自测确认失败**

Run: `python3 skills/apifox-sync/scripts/push_schema_conflict.py --self-test`
Expected：FAIL，`NameError: name 'build_owners' is not defined`

- [ ] **Step 3: 实现三个核心函数**

```python
def refs_of(node) -> set[str]:
    """递归收集节点内所有 $ref 指向的 schema 简名。"""
    found: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            ref = cur.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                found.add(ref.rsplit("/", 1)[-1])
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return found


def build_owners(export: dict) -> dict[str, set[str]]:
    """schema 名 → 占用它的 x-source-controller 集合，含传递闭包。

    仅被其他 schema 间接引用的 schema 也会被正确归属；无 x-source-controller
    的 operation 归属为空串（来源未知）。
    """
    schemas = export.get("components", {}).get("schemas", {})
    graph = {name: refs_of(body) for name, body in schemas.items()}
    owners: dict[str, set[str]] = {}
    paths = export.get("paths", {})
    if not isinstance(paths, dict):
        return owners
    for methods in paths.values():
        if not isinstance(methods, dict):
            continue
        for detail in methods.values():
            if not isinstance(detail, dict):
                continue
            controller = detail.get("x-source-controller", "") or ""
            queue = list(refs_of(detail))
            seen = set(queue)
            while queue:
                name = queue.pop()
                owners.setdefault(name, set()).add(controller)
                for nxt in graph.get(name, set()):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
    return owners


def find_conflicts(spec: dict, export: dict) -> list[dict]:
    """本次 spec 中会覆盖到「非本次来源」的远端 schema，按名字典序返回。"""
    export_schemas = set(export.get("components", {}).get("schemas", {}))
    spec_schemas = set(spec.get("components", {}).get("schemas", {}))
    mine: set[str] = set()
    paths = spec.get("paths", {})
    if isinstance(paths, dict):
        for methods in paths.values():
            if not isinstance(methods, dict):
                continue
            for detail in methods.values():
                if isinstance(detail, dict) and detail.get("x-source-controller"):
                    mine.add(detail["x-source-controller"])
    owners = build_owners(export)
    conflicts = []
    for name in sorted(spec_schemas & export_schemas):
        own = owners.get(name) or {""}   # 远端有但无人引用 → 孤儿，来源未知，从严计冲突
        foreign = own - mine
        if foreign:
            conflicts.append({"schema": name, "owners": sorted(foreign)})
    return conflicts
```

- [ ] **Step 4: 实现 run() 与 stdout 摘要**

```python
def run_detect(spec_path: str, export_path: str, tmpprefix: str) -> int:
    spec = load_json_loose(spec_path)
    export = load_json_loose(export_path)
    conflicts = find_conflicts(spec, export)
    print(f"schema 冲突: {len(conflicts)} 个")
    for c in conflicts:
        who = "、".join(o if o else "(来源未知)" for o in c["owners"])
        print(f'  · {c["schema"]}  被占用: {who}')
    if conflicts:
        with open(f"{tmpprefix}schema-conflicts.json", "w", encoding="utf-8") as f:
            json.dump(conflicts, f, ensure_ascii=False)
        print("提示: 继续推送将以 OVERWRITE_EXISTING 覆盖上述 schema 的远端定义。")
    return 0
```

检测模式恒返回 0（有无冲突都算成功执行），由对话层决策。

- [ ] **Step 5: 跑自测确认通过**

Run: `python3 skills/apifox-sync/scripts/push_schema_conflict.py --self-test`
Expected：`SELFTEST_OK`

- [ ] **Step 6: Commit**

```bash
git add skills/apifox-sync/scripts/push_schema_conflict.py
git commit -m "新增 schema 命名冲突检测，按来源控制器归属并计算引用传递闭包"
```

---

### Task 4: schema 冲突改名与流程接入

给冲突检测加 `--apply-prefix` 改名模式，并把整个预检挂进 push 管线的步骤 10.5。

**Files:**
- Modify: `skills/apifox-sync/scripts/push_schema_conflict.py`
- Modify: `skills/apifox-sync/references/push-api.md`（新增步骤 10.5，步骤 12 清理列表）
- Modify: `skills/apifox-sync/SKILL.md`（Push 流程步骤表）

**Interfaces:**
- Consumes：Task 3 的 `find_conflicts`、`${TMPPREFIX}schema-conflicts.json`
- Produces：`apply_prefix(spec: dict, names: set[str], prefix: str) -> int` 返回改名数量；`--apply-prefix <前缀>` 就地重写 `<spec_json>`

- [ ] **Step 1: 追加改名的失败自测**

在 `self_test()` 末尾（`print("SELFTEST_OK")` 之前）追加：

```python
    # --apply-prefix：键改名 + 全部 $ref 同步（含数组、allOf 等嵌套位置）
    spec2 = {
        "paths": {"/u": {"get": {
            "responses": {"200": {"content": {"application/json": {
                "schema": {"type": "array",
                           "items": {"$ref": "#/components/schemas/Money"}}}}}},
        }}},
        "components": {"schemas": {
            "Money": {"type": "object"},
            "Order": {"type": "object", "allOf": [
                {"$ref": "#/components/schemas/Money"},
                {"type": "object",
                 "properties": {"m": {"$ref": "#/components/schemas/Money"}}},
            ]},
        }},
    }
    n = apply_prefix(spec2, {"Money"}, "Order")
    assert n == 1, n
    schemas2 = spec2["components"]["schemas"]
    assert "OrderMoney" in schemas2 and "Money" not in schemas2, list(schemas2)
    # 未列入改名的 schema 保持原名
    assert "Order" in schemas2
    # 所有 $ref 均已重写，spec 中不应再出现旧引用
    dumped = json.dumps(spec2, ensure_ascii=False)
    assert "#/components/schemas/Money" not in dumped, dumped
    assert dumped.count("#/components/schemas/OrderMoney") == 3, dumped
    # 不存在的名字不报错、不计数
    assert apply_prefix(spec2, {"NotThere"}, "X") == 0
```

- [ ] **Step 2: 跑自测确认失败**

Run: `python3 skills/apifox-sync/scripts/push_schema_conflict.py --self-test`
Expected：FAIL，`NameError: name 'apply_prefix' is not defined`

- [ ] **Step 3: 实现改名**

```python
def _rewrite_refs(node, mapping: dict[str, str]) -> None:
    """就地重写节点内所有指向 mapping 旧名的 $ref。"""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            old = ref.rsplit("/", 1)[-1]
            if old in mapping:
                node["$ref"] = f"#/components/schemas/{mapping[old]}"
        for v in node.values():
            _rewrite_refs(v, mapping)
    elif isinstance(node, list):
        for v in node:
            _rewrite_refs(v, mapping)


def apply_prefix(spec: dict, names: set[str], prefix: str) -> int:
    """把 names 中的 schema 键加上 prefix，并同步全部 $ref。返回改名数量。"""
    schemas = spec.get("components", {}).get("schemas", {})
    mapping = {n: f"{prefix}{n}" for n in names if n in schemas}
    if not mapping:
        return 0
    for old, new in mapping.items():
        schemas[new] = schemas.pop(old)
    _rewrite_refs(spec, mapping)
    return len(mapping)


def run_apply(spec_path: str, prefix: str, tmpprefix: str) -> int:
    conflict_file = Path(f"{tmpprefix}schema-conflicts.json")
    if not conflict_file.is_file():
        print("无冲突清单，跳过改名")
        return 0
    conflicts = json.loads(conflict_file.read_text(encoding="utf-8"))
    names = {c["schema"] for c in conflicts}
    spec = load_json_loose(spec_path)
    n = apply_prefix(spec, names, prefix)
    Path(spec_path).write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    print(f"已改名 {n} 个 schema（前缀 {prefix}）")
    for name in sorted(names):
        print(f"  · {name} → {prefix}{name}")
    return 0
```

`main()` 分派（`argv` 含脚本名本身）：

- `len(argv) == 4 and argv[2] == "--apply-prefix"` → `run_apply(argv[1], argv[3], tmpprefix)`
- `len(argv) == 3` → `run_detect(argv[1], argv[2], tmpprefix)`
- `len(argv) == 2 and argv[1] in ("-h", "--help")` → 打印 `__doc__`，返回 0
- `len(argv) == 2 and argv[1] == "--self-test"` → `self_test()`
- 其余 → 打印 usage 到 stderr，返回 2

两种运行模式都要求 env `TMPPREFIX`，缺失时报错返回 1。

- [ ] **Step 4: 跑自测确认通过**

Run: `python3 skills/apifox-sync/scripts/push_schema_conflict.py --self-test`
Expected：`SELFTEST_OK`

- [ ] **Step 5: push-api.md 新增步骤 10.5**

插在「步骤 10：JSON 预验证」与「步骤 11：推送到 Apifox」之间：

````markdown
## 步骤 10.5：schema 命名冲突预检

**必须在步骤 11 任何写操作之前**。push 以 `OVERWRITE_EXISTING` 提交 schema，同名即覆盖且无提示。

```bash
python3 "$SKILL_DIR/scripts/push_schema_conflict.py" "${TMPPREFIX}spec.json" "${TMPPREFIX}export.json"
```

输出 `schema 冲突: 0 个` → 直接进入步骤 11。

否则用 `AskUserQuestion` 展示冲突清单（schema 名 + 占用方 Controller），三选一：

- **自动加前缀改名**：前缀取本次 Controller 简单类名去掉 `Controller` 后缀
  ```bash
  python3 "$SKILL_DIR/scripts/push_schema_conflict.py" "${TMPPREFIX}spec.json" --apply-prefix "<前缀>"
  ```
  改名后**必须重跑步骤 10 的 `verify_json.py`** 确认 spec 仍合法。
- **确认覆盖**：原样进入步骤 11。
- **中止**：停止推送，不做任何远端写操作。

标注「来源未知」的是远端存在但无接口引用的孤儿 schema，或缺 `x-source-controller` 的历史接口——无法证明同源，故从严计入冲突。
````

- [ ] **Step 6: push-api.md 步骤 12 清理列表补文件**

`rm -f` 列表追加 `"${TMPPREFIX}"schema-conflicts.json`。

- [ ] **Step 7: SKILL.md 步骤表更新**

Push 流程表中 `10-12` 那行拆开，改为：

```markdown
| 10 | `references/push-api.md` 步骤 10 | JSON 预验证 |
| 10.5 | `references/push-api.md` 步骤 10.5 | schema 命名冲突预检，冲突时交互决策 |
| 11-12 | `references/push-api.md` 步骤 11-12 | 分类推送（锚点匹配/死接口清理/import）、回读校验、报告 |
```

- [ ] **Step 8: Commit**

```bash
git add skills/apifox-sync/scripts/push_schema_conflict.py skills/apifox-sync/references/push-api.md skills/apifox-sync/SKILL.md
git commit -m "schema 冲突支持加前缀改名，接入 push 步骤 10.5 交互决策"
```

---

### Task 5: 推送后回读校验

**Files:**
- Create: `skills/apifox-sync/scripts/push_verify.py`
- Modify: `skills/apifox-sync/references/push-api.md`（步骤 12）

**Interfaces:**
- Produces：`verify(spec: dict, remote: dict) -> list[str]` 返回问题描述列表，空列表表示全部通过；有问题时脚本退出码 `1`。

- [ ] **Step 1: 写失败自测**

```python
def self_test() -> int:
    spec = {
        "paths": {"/a": {"get": {
            "x-apifox-folder": "用户管理",
            "x-source-method-fq": "UserController#list",
        }}},
        "components": {"schemas": {"UserVO": {"properties": {"id": {}, "name": {}}}}},
    }
    # 1) 完全一致 → 无问题
    ok = {
        "paths": {"/a": {"get": {
            "x-apifox-folder": "用户管理",
            "x-source-method-fq": "UserController#list",
        }}},
        "components": {"schemas": {"UserVO": {"properties": {"id": {}, "name": {}}}}},
    }
    assert verify(spec, ok) == [], verify(spec, ok)

    # 2) 接口不存在
    assert any("远端不存在" in p for p in verify(spec, {"paths": {}, "components": {}}))

    # 3) folder 落错
    bad_folder = json.loads(json.dumps(ok))
    bad_folder["paths"]["/a"]["get"]["x-apifox-folder"] = "根目录"
    assert any("folder 不符" in p for p in verify(spec, bad_folder)), verify(spec, bad_folder)

    # 4) 锚点丢失
    bad_fq = json.loads(json.dumps(ok))
    del bad_fq["paths"]["/a"]["get"]["x-source-method-fq"]
    assert any("锚点不符" in p for p in verify(spec, bad_fq)), verify(spec, bad_fq)

    # 5) schema 字段缺失
    bad_schema = json.loads(json.dumps(ok))
    del bad_schema["components"]["schemas"]["UserVO"]["properties"]["name"]
    problems = verify(spec, bad_schema)
    assert any("缺少字段" in p and "name" in p for p in problems), problems

    # 6) schema 整体不存在
    no_schema = json.loads(json.dumps(ok))
    no_schema["components"]["schemas"] = {}
    assert any("schema UserVO" in p and "远端不存在" in p for p in verify(spec, no_schema))
    print("SELFTEST_OK")
    return 0
```

- [ ] **Step 2: 跑自测确认失败**

Run: `python3 skills/apifox-sync/scripts/push_verify.py --self-test`
Expected：FAIL，`NameError: name 'verify' is not defined`

- [ ] **Step 3: 实现 verify()**

```python
def verify(spec: dict, remote: dict) -> list[str]:
    """比对本次 spec 与推送后回读的远端数据，返回问题列表（空 = 全部通过）。"""
    problems: list[str] = []
    remote_paths = remote.get("paths", {}) or {}
    for path, methods in (spec.get("paths", {}) or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            label = f"{method.upper()} {path}"
            r = (remote_paths.get(path) or {}).get(method)
            if not isinstance(r, dict):
                problems.append(f"{label}: 远端不存在")
                continue
            want_folder = detail.get("x-apifox-folder", "")
            got_folder = r.get("x-apifox-folder", "")
            if want_folder != got_folder:
                problems.append(
                    f'{label}: folder 不符（期望 "{want_folder}"，实际 "{got_folder}"）'
                )
            want_fq = detail.get("x-source-method-fq")
            if want_fq and want_fq != r.get("x-source-method-fq"):
                problems.append(
                    f'{label}: 锚点不符（期望 {want_fq}，实际 {r.get("x-source-method-fq")}）'
                )

    remote_schemas = remote.get("components", {}).get("schemas", {}) or {}
    for name, body in (spec.get("components", {}).get("schemas", {}) or {}).items():
        r = remote_schemas.get(name)
        if r is None:
            problems.append(f"schema {name}: 远端不存在")
            continue
        want = set((body or {}).get("properties", {}) or {})
        got = set((r or {}).get("properties", {}) or {})
        if want - got:
            problems.append(f"schema {name}: 远端缺少字段 {sorted(want - got)}")
        if got - want:
            problems.append(f"schema {name}: 远端多出字段 {sorted(got - want)}")
    return problems


def run(spec_path: str, remote_path: str) -> int:
    spec = load_json_loose(spec_path)
    remote = load_json_loose(remote_path)
    problems = verify(spec, remote)
    if not problems:
        print("✅ 回读校验通过：接口、folder、锚点、schema 字段均与本次推送一致")
        return 0
    print(f"❌ 回读校验发现 {len(problems)} 处不一致：")
    for p in problems:
        print(f"  - {p}")
    return 1
```

- [ ] **Step 4: 跑自测确认通过**

Run: `python3 skills/apifox-sync/scripts/push_verify.py --self-test`
Expected：`SELFTEST_OK`

- [ ] **Step 5: push-api.md 步骤 12 接入**

在「步骤 12：报告与清理」开头、报告之前插入：

````markdown
### 12.1 回读校验

`import-openapi` 的 counters 只说明请求被接受，不能说明 folder 落对、schema 正确。推送后重新导出比对：

```bash
curl -s -o "${TMPPREFIX}verify.json" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"scope":{"type":"ALL"},"options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true},"oasVersion":"3.0","exportFormat":"JSON"}'
python3 "$SKILL_DIR/scripts/push_verify.py" "${TMPPREFIX}spec.json" "${TMPPREFIX}verify.json"
```

退出码 `1` 表示存在不一致，**必须在步骤 12 报告中原样列出**，不得只报 counters。
````

步骤 12 的 `rm -f` 列表追加 `"${TMPPREFIX}"verify.json`。

- [ ] **Step 6: Commit**

```bash
git add skills/apifox-sync/scripts/push_verify.py skills/apifox-sync/references/push-api.md
git commit -m "推送后回读校验 folder、锚点与 schema 字段一致性"
```

---

### Task 6: 静态内部类命名规则

**Files:**
- Modify: `skills/apifox-sync/data/framework-schemas.json`
- Modify: `skills/apifox-sync/references/type-resolution.md`（6.1）
- Modify: `skills/apifox-sync/references/openapi-gen.md`（9.3）

**Interfaces:**
- Consumes：无（纯规则/文档）
- Produces：静态内部类 schema 名统一为 `{OuterClass}{InnerClass}`

- [ ] **Step 1: framework-schemas.json 增规则**

`schemaNameRules` 由：
```json
  "schemaNameRules": {
    "dto": "{ClassName}",
    "rWrapper": "R_{InnerType}",
    "pageWrapper": "Page_{InnerType}"
  },
```
改为：
```json
  "schemaNameRules": {
    "dto": "{ClassName}",
    "staticInnerClass": "{OuterClass}{InnerClass}",
    "rWrapper": "R_{InnerType}",
    "pageWrapper": "Page_{InnerType}",
    "_note": "静态内部类必须带外层类名前缀。Location/Detail/Item 这类通用内部类名极易与其他模块的顶层类撞名，而 push 以 OVERWRITE_EXISTING 提交 schema"
  },
```

- [ ] **Step 2: type-resolution.md 6.1 补命名**

「静态内部类」那句改为：

```markdown
**静态内部类**：Glob 返回 0 结果时，先在已读文件中搜索 `static class {ClassName}`，再用 Grep 搜 `class {ClassName}` 定位外部类文件。**schema 名必须为 `{外部类名}{内部类名}`**（如 `DeviceVO` 的内部类 `Location` → `DeviceVOLocation`），不可用简单类名——`Location`/`Detail`/`Item` 这类通用名极易与其他模块的顶层类撞名。
```

- [ ] **Step 3: openapi-gen.md 9.3 补一条**

在「DTO 用类名；R 包装用 `R_{InnerType}`；Page 包装用 `Page_{InnerType}`」这条后追加一条：

```markdown
- **静态内部类用 `{OuterClass}{InnerClass}`**（如 `DeviceVOLocation`），不用简单类名
```

- [ ] **Step 4: 验证三处一致**

```bash
python3 -c "import json;d=json.load(open('skills/apifox-sync/data/framework-schemas.json'));print(d['schemaNameRules']['staticInnerClass'])"
grep -c '内部类名' skills/apifox-sync/references/type-resolution.md
grep -c 'OuterClass' skills/apifox-sync/references/openapi-gen.md
```
Expected：第一条打印 `{OuterClass}{InnerClass}`；后两条均 ≥1。（`type-resolution.md` 用中文表述「{外部类名}{内部类名}」，`openapi-gen.md` 用英文占位符，故两条 grep 的关键词不同。）

- [ ] **Step 5: Commit**

```bash
git add skills/apifox-sync/data/framework-schemas.json skills/apifox-sync/references/type-resolution.md skills/apifox-sync/references/openapi-gen.md
git commit -m "静态内部类 schema 改用外部类名前缀命名，降低跨模块撞名"
```

---

### Task 7: 枚举识别放宽至 String

**Files:**
- Modify: `skills/apifox-sync/references/enum-detection.md`
- Modify: `skills/apifox-sync/SKILL.md`（Push 流程表步骤 7 描述）

- [ ] **Step 1: enum-detection.md 开头放宽**

第 3 行由：
```markdown
对步骤 6 中提取的 **Integer 类型字段**，尝试识别关联枚举。
```
改为：
```markdown
对步骤 6 中提取的 **Integer / String 类型标量字段**，尝试识别关联枚举。
```

- [ ] **Step 2: 7.3 改取值规则并补 String 示例**

整节 7.3 替换为：

````markdown
## 7.3 读取枚举值

提取常量格式 `CONSTANT_NAME(code, "desc", ...)`：

- **code**：取第一个标量参数（Integer 或 String）
- **desc**：取 code 之后的第一个 String 参数

  当 code 本身是 String 时（形如 `NONE("NONE", "无")`），第一个 String 作 code，**第二个** String 作 desc。

schema 中按 code 的**实际类型**生成。

Integer code：
```json
{
  "type": "integer",
  "enum": [1, 2, 3],
  "description": "航线类型: 1-航点航线 2-块状航线 3-仿地航线"
}
```

String code：
```json
{
  "type": "string",
  "enum": ["NONE", "SINGLE", "FLOAT", "FIXED"],
  "description": "定位模式: NONE-无 SINGLE-单点 FLOAT-浮动解 FIXED-固定解"
}
```
````

- [ ] **Step 3: SKILL.md 步骤 7 描述同步**

Push 流程表中 `| 7 | references/enum-detection.md | Integer 字段匹配枚举，提取 code+desc |` 改为：

```markdown
| 7 | `references/enum-detection.md` | Integer/String 字段匹配枚举，提取 code+desc |
```

- [ ] **Step 4: 验证**

```bash
grep -c 'Integer / String\|Integer/String' skills/apifox-sync/references/enum-detection.md skills/apifox-sync/SKILL.md
grep -c '"type": "string"' skills/apifox-sync/references/enum-detection.md
grep -c '只取第一个 Integer' skills/apifox-sync/references/enum-detection.md || echo "旧规则已清除"
```
Expected：前两条 ≥1；第三条输出 `旧规则已清除`。

- [ ] **Step 5: Commit**

```bash
git add skills/apifox-sync/references/enum-detection.md skills/apifox-sync/SKILL.md
git commit -m "枚举识别放宽至 String 类型 code，按实际类型生成 enum"
```

---

### Task 8: README 升级说明、版本号与全量回归

收尾任务：迁移提示写进 README，版本升到 1.7.0，全部脚本自测回归。

**Files:**
- Modify: `README.md`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`

- [ ] **Step 1: 全量自测回归**

```bash
fail=0
for f in skills/apifox-sync/scripts/*.py; do
  out=$(python3 "$f" --self-test 2>&1) || { echo "FAIL(exit): $f"; fail=1; continue; }
  case "$out" in *SELFTEST_OK*) : ;; *) echo "FAIL(no OK): $f"; fail=1 ;; esac
done
[ "$fail" = 0 ] && echo "ALL_SELFTESTS_OK"
```
Expected：`ALL_SELFTESTS_OK`，共 18 个脚本（原 15 + 新 3）全部通过。**任何 FAIL 必须先修掉再继续。**

- [ ] **Step 2: 确认 push_classify.py 既有行为未被破坏**

本次未改其逻辑，其自测必须原样通过。

Run: `python3 skills/apifox-sync/scripts/push_classify.py --self-test`
Expected：`SELFTEST_OK`

- [ ] **Step 3: README 加 1.7.0 升级说明**

在 README 版本/更新说明处追加：

```markdown
### 1.7.0

- **schema 命名冲突预检**：push 前检测本次 schema 是否会覆盖其他 Controller 的同名 schema，命中时可选择加前缀改名 / 确认覆盖 / 中止
- **静态内部类改名**：内部类 schema 由简单类名改为 `{外部类名}{内部类名}`
- **推送后回读校验**：核对 folder、锚点与 schema 字段是否真的落对
- **枚举支持 String 类型 code**
- **文档修复**：补齐 export-openapi 命令，脚本路径统一为 `$SKILL_DIR`

> ⚠️ **升级注意**：静态内部类改名后，已推送过的项目再次 push 会以新名建立 schema，
> 旧的简单名 schema 会成为孤儿留在 Apifox 中。孤儿 schema 不会影响新接口文档，
> 但需要你手动清理。本工具不自动删除——删除不可逆，且旧名可能仍被其他 Controller 的接口引用。
```

- [ ] **Step 4: 版本号升到 1.7.0（2 文件 3 字段）**

```bash
python3 - <<'EOF'
import json, pathlib
for path, keys in [
    (".claude-plugin/plugin.json", [("version",)]),
    (".claude-plugin/marketplace.json", [("version",), ("plugins", 0, "version")]),
]:
    p = pathlib.Path(path)
    d = json.loads(p.read_text("utf-8"))
    for k in keys:
        if len(k) == 1:
            d[k[0]] = "1.7.0"
        else:
            d[k[0]][k[1]][k[2]] = "1.7.0"
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(path, "→ 1.7.0")
EOF
```

- [ ] **Step 5: 验证三个字段都改到了**

```bash
python3 -c "
import json
a=json.load(open('.claude-plugin/plugin.json'))
b=json.load(open('.claude-plugin/marketplace.json'))
print('plugin.version', a['version'])
print('marketplace.version', b['version'])
print('marketplace.plugins[0].version', b['plugins'][0]['version'])
assert a['version']==b['version']==b['plugins'][0]['version']=='1.7.0'
print('VERSION_OK')"
```
Expected：三行都是 `1.7.0`，末行 `VERSION_OK`

- [ ] **Step 6: Commit**

```bash
git add README.md .claude-plugin/plugin.json .claude-plugin/marketplace.json
git commit -m "补充 1.7.0 升级说明与内部类改名迁移提示，升级至 1.7.0"
```

- [ ] **Step 7: 合并回 main**

```bash
git checkout main
git merge --no-ff feature/issue-2-push-hardening -m "合入 push 流程加固：schema 冲突预检、SKILL_DIR 约定、回读校验"
git log --oneline --graph -12
```

---

## 实施后待人工确认

以下两项计划内无法自动验证，需实际跑一次 push 确认：

1. **SKILL_DIR 第 1 级降级**：`$CLAUDE_PLUGIN_ROOT` 在 skill 执行态下是否非空。三级降级保证功能正确，但值得实测确认走的是哪一级。
2. **步骤 10.5 的交互体验**：冲突清单在 `AskUserQuestion` 里的呈现是否清晰，前缀默认值是否合理。
