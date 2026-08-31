# push 流程加固设计（issue #2）

- 日期：2026-08-28
- 来源：https://github.com/anduinnn/apifox-sync/issues/2
- 目标版本：1.6.3 → 1.7.0
- 分支：`feature/issue-2-push-hardening`

## 1. 背景

issue #2 是以 v1.6.3 完整跑通一次 push（单方法模式、约 470 接口的多模块 Apifox 项目）后的实测反馈，含 2 个 bug、4 项增强。本设计一次性覆盖全部 7 个可动项。

## 2. 对 issue 原文的核实与更正

实施前逐条核对了 issue 中的代码引用，结论如下。**带 ⚠️ 的三条与原文不符，实施时以本节为准。**

| issue 论断 | 核实结果 |
|---|---|
| `push_classify.py:130` 硬编码 `OVERWRITE_EXISTING` | ✅ 属实 |
| 现有冲突判断只覆盖接口 path 跨文件夹（`:78`/`:109`/`:238`） | ✅ 属实，确实不涉及 `components.schemas` |
| `push-api.md:32` 缺 export-openapi 的 curl | ✅ 属实（对照 11.4/11.5 均有完整命令） |
| 脚本相对路径共 **18** 处 | ⚠️ 实为 **19** 处：`push-api.md` 6、`pull.md` 10、`push-parse.md` 2、`init.md` 1 |
| `type-resolution.md` 6.5 规定 DTO schema 用简单类名 | ⚠️ 6.5 实为「Schema 名称清洗」。该规则真实位置是 `data/framework-schemas.json` 的 `schemaNameRules.dto: "{ClassName}"` 与 `openapi-gen.md:31`。**结论不变**（静态内部类确实落成简单类名），但改动点在别处 |
| 建议用 `${CLAUDE_PLUGIN_ROOT}` 定义 `SKILL_DIR` | ⚠️ 实测该变量在 Bash 工具调用中**为空**，直接照搬无效。详见 §4.1 |

### 2.1 关于 `schemaOverwriteBehavior` 的补充调研

Apifox 的 `schemaOverwriteBehavior` 另接受 `AUTO_MERGE` / `KEEP_EXISTING` / `CREATE_NEW`。**均不适合作为更安全的默认值**：

- `AUTO_MERGE`：把两个同名但无关的类字段并成一个 schema，同时污染双方，比覆盖更难察觉。
- `KEEP_EXISTING`：本次推送的 `$ref` 会静默指向一个无关的既有 schema；且同源重复推送的正常更新会一并失效。

正确行为取决于「撞名是否同源」，而这正是预检能判定的。故维持 `OVERWRITE_EXISTING`，在其之前加预检。

## 3. 目标与非目标

**目标**：消除 schema 静默覆盖风险；让文档照做即可执行；降低每次 Bash 调用的样板成本；补齐推送后的正确性校验。

**非目标**：不改 pull 的核心切片/落盘逻辑；不引入除 python3 外的新依赖；不改 Apifox API 版本头。

## 4. 设计

### 4.1 SKILL_DIR 解析与 env.sh（覆盖 issue 2.2 + 3.1）

**前提说明**：实测 `CLAUDE_PLUGIN_ROOT` 在 Bash 工具中为空，但该测试是在非 skill 执行态下进行的，不排除它仅在 skill 执行期间注入。因此解析设计为三级降级，两种情况均正确，不依赖对该变量的假设。

解析顺序（逐个校验 `scripts/load_config.py` 是否存在，命中即停）：

1. `$CLAUDE_PLUGIN_ROOT/skills/apifox-sync`，以及 `$CLAUDE_PLUGIN_ROOT` 本身（兼容单 skill 插件布局）
2. `~/.claude/plugins/installed_plugins.json` 中 `plugins` 下 key 以 `apifox-sync@` 开头的条目的 `installPath` + `/skills/apifox-sync`
3. `$PROJECT_ROOT/skills/apifox-sync`（本仓自身开发自测）

全部落空 → 报错中止，不静默降级。

**一次性 bootstrap**，置于 push 步骤 2 与 pull 步骤 1：

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
```

**后续所有 Bash 调用**的 preamble 从 4 行降为 2 行：

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
source "${PROJECT_ROOT}/.claude/.tmp/apifox-sync-env.sh"
```

**TOKEN 不落盘**：env.sh 内部调用 `load_config.py` 取 Token，不把 Token 值写进文件。理由：用户项目的 `.claude/` 普遍未整体 gitignore（其下常有 CLAUDE.md、settings.json），而 `init.md:57` 只叮嘱了 `apifox.json`。写入 Token 会新增一处用户未被告知的明文落盘点。

**合并既有 debug env 文件**：现有 `apifox-debug-env.sh` 与 env.sh 职责重合，合并进 env.sh 后删除，相应清理其在 preamble 与步骤 12 `rm -f` 中的引用。

**19 处调用点**统一改为 `python3 "$SKILL_DIR/scripts/xxx.py"`。

### 4.2 schema 命名冲突预检（issue 一 · 方案 A）

新脚本 `scripts/push_schema_conflict.py`，插入为**步骤 10.5**（spec 已生成、export.json 已在手，早于步骤 11 的任何写操作）。

**归属算法**：

1. `refs_of(node)`：递归收集节点内所有 `$ref` 指向的 schema 名。
2. 遍历 `export.json` 的每个 operation，`refs_of(operation)` 得到直接引用集，归属于该 operation 的 `x-source-controller`；该字段为空时记为「来源未知」。
3. **传递闭包**：以 `components.schemas` 建图（schema → 它引用的 schema），从每个直接引用出发 BFS，把归属沿边传播。
   - issue 原文未提这一层。省略它会把「仅被其他 schema 间接引用」的 schema 判为无主，从而漏报冲突。
4. 得到 `owners: {schema_name: set(controller)}`。

**判定**：设 `mine` 为本次 spec 中所有 `x-source-controller` 的集合。schema 名 `n` 构成冲突，当且仅当 `n` 存在于 export 的 `components.schemas` 且 `owners.get(n) - mine` 非空。「来源未知」计入冲突（无法证明同源，从严）。

**输出**：`${TMPPREFIX}schema-conflicts.json`，stdout 打印冲突数与明细（schema 名、占用方 controller 列表）。无冲突时不写文件。检测态恒返回 0，由流程决策。

**交互**：冲突非空时 `AskUserQuestion` 三选一：

- **自动加前缀改名** → `push_schema_conflict.py <spec> --apply-prefix <前缀>`，就地重写 spec 的 `components.schemas` 键及全部 `$ref`。前缀默认取本次 Controller 简单类名去掉 `Controller` 后缀。
- **确认覆盖** → 原样继续。
- **中止** → 停止推送。

### 4.3 静态内部类命名（issue 一 · 方案 B）

静态内部类 schema 命名改为 `{OuterClass}{InnerClass}`。改动三处：

- `data/framework-schemas.json` 的 `schemaNameRules` 增加 `"staticInnerClass": "{OuterClass}{InnerClass}"`
- `references/type-resolution.md` 6.1 的「静态内部类」分支
- `references/openapi-gen.md` 9.3

**迁移副作用（issue 未提，必须写进 README 升级说明）**：已推送过的项目升级后再次 push，内部类会以新名建立新 schema，旧的简单名 schema 成为孤儿留在 Apifox 中，需用户手动清理。本设计不自动删除旧 schema——删除是不可逆的外部操作，且旧名可能仍被其他 Controller 的接口引用。

A 与 B 互补：B 降低撞名概率但仍是静默的，且拦不住非内部类的跨模块重名（如两个模块各有一个 `UserVO`）；A 兜住剩余情况。

### 4.4 补齐 export-openapi 命令（issue 2.1）

`references/push-api.md` 步骤 8 补完整 curl：

```bash
HTTP=$(curl -s -o "${TMPPREFIX}export.json" -w "%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"scope":{"type":"ALL"},"options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true},"oasVersion":"3.0","exportFormat":"JSON"}')
```

`includeApifoxExtensionProperties: true` 显式标注为**必需**，并写明漏掉的后果：导出不含 `x-source-method-fq` / `x-apifox-folder` / `x-source-controller` → `push_index.py:63-73` 建出空索引 → `push_classify.py` 把全部接口判为 create → 静默重复创建，且 counters 表面正常。

`references/pull.md` 步骤 2 存在**同样的缺口**（issue 未提），一并补齐。

### 4.5 推送后回读校验（issue 3.2）

新脚本 `scripts/push_verify.py`，步骤 12 在报告前重新调用 export-openapi 写入 `${TMPPREFIX}verify.json`，逐项核对：

- 每个 operation 的 path+method 在远端存在
- `x-apifox-folder` 与 spec 中一致
- `x-source-method-fq` 存在且相等
- 本次 spec 的每个 schema 在远端存在，且 properties 键集合一致（分别列出缺失与多余的键）

stdout 逐项 ✅/❌ 并汇总；存在 ❌ 时退出码 1，由流程在报告中显式标注失败项。

### 4.6 枚举识别放宽至 String（issue 3.3）

`references/enum-detection.md`：

- 开头「对步骤 6 中提取的 **Integer 类型字段**」→ 「**Integer / String 类型标量字段**」
- 7.3「只取第一个 Integer 参数为 code」→ 「取第一个标量参数（Integer 或 String）为 code，第一个 String 参数为 desc；code 为 String 时，desc 取其后的下一个 String 参数」
- schema 按 code 实际类型生成，补一个 `type: string` + `enum: ["NONE","SINGLE"]` 的示例

`SKILL.md` 步骤 7 的描述「Integer 字段匹配枚举」同步改。

**歧义澄清**：当 code 为 String 时，构造参数形如 `CONSTANT("NONE", "无")`，两个参数都是 String。规则定为「第一个 String 作 code，第二个 String 作 desc」，避免二义。

### 4.7 folder 推荐预填（issue 3.4）

新脚本 `scripts/suggest_folder.py <export_json> <controller_fqcn>`，按 `x-source-controller` 反查该 Controller 已有接口所在的 `x-apifox-folder`，stdout 每行一个，按接口数降序。

**注意**：该脚本直接读 `export.json`，不依赖 `by-source.json`——后者在步骤 11.1 才生成，晚于步骤 8。

步骤 8 的 `AskUserQuestion` 把推荐项排在首位并标注「当前 Controller 上次推送位置」。**保留这一问**，不自动选定：选错目录代价高且事后难清理。

## 5. 文件清单

**新增（3）**：`scripts/push_schema_conflict.py`、`scripts/push_verify.py`、`scripts/suggest_folder.py`

**修改（10）**：

| 文件 | 改动 |
|---|---|
| `SKILL.md` | 步骤 7 描述、步骤表加 10.5、注意事项加 SKILL_DIR 约定 |
| `references/push-api.md` | 步骤 8 补 curl + folder 推荐；新增 10.5；步骤 12 加回读校验；preamble；6 处路径；清理 rm 列表 |
| `references/push-parse.md` | 步骤 2 改为 bootstrap；2 处路径 |
| `references/pull.md` | preamble 改 source；步骤 2 补 curl；10 处路径 |
| `references/init.md` | 1 处路径 |
| `references/enum-detection.md` | Integer → Integer/String |
| `references/type-resolution.md` | 6.1 静态内部类命名 |
| `references/openapi-gen.md` | 9.3 静态内部类命名 |
| `data/framework-schemas.json` | `schemaNameRules` 加 `staticInnerClass` |
| `README.md` | 1.7.0 升级说明 + 内部类改名迁移提示 |

**版本（2 文件 3 字段）**：`.claude-plugin/plugin.json` 的 `version`；`.claude-plugin/marketplace.json` 的 `plugins[0].version` 与顶层 `version`。均为 `1.7.0`。

按本仓惯例，版本号**折进最后一个功能 commit**（message 末尾附「升级至 1.7.0」），不单独成一个版本 commit。`plugin.json` 的 version 是 Claude Code 判断插件是否需要更新的依据，两个文件必须同时改。

## 6. 测试策略

- 3 个新脚本各自带 `--self-test`，与现有 15 个脚本约定一致，用内联 fixture 覆盖：
  - `push_schema_conflict.py`：同源不报、跨源报、**间接引用（传递闭包）报**、来源未知报、`--apply-prefix` 改名后 `$ref` 全部同步
  - `push_verify.py`：folder 不符、锚点缺失、schema 字段缺失各命中一次
  - `suggest_folder.py`：多 folder 按接口数降序、无匹配时空输出
- 合并前跑全量：所有 `scripts/*.py --self-test` 必须 SELFTEST_OK
- `push_classify.py` 未改逻辑，其既有 self-test 须保持通过
- SKILL_DIR 三级降级：至少验证第 2 级（installed_plugins.json）与第 3 级（本仓路径）实际可解析

## 7. 风险

| 风险 | 应对 |
|---|---|
| `CLAUDE_PLUGIN_ROOT` 实际行为与实测不符 | 三级降级，任一情况都能解析 |
| 内部类改名产生孤儿 schema | README 升级说明明确告知，不自动删除 |
| 回读校验拉长 push 耗时（多一次 export） | 接受；正确性优先，且仅在 push 末尾一次 |
| 预检误报（来源未知一律计冲突） | 交互提供「确认覆盖」出口，不硬阻断 |

## 8. 提交与合并

按功能拆 commit（preamble 重构 / 预检 / 命名规则 / 文档修复 / 回读校验 / 枚举 / folder 推荐），中文 message，描述只写功能概要、不附技术细节。本仓无 commit 前缀惯例（见 `git log`），保持纯描述式。完成后 `--no-ff` 合回 `main`：

```bash
git merge --no-ff feature/issue-2-push-hardening -m "合入 push 流程加固：schema 冲突预检、SKILL_DIR 约定、回读校验"
```
