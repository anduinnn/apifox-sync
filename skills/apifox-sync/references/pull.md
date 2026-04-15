# Pull 步骤 1-7：配置、目录选择、导出、精简、保存

API 常量参考 `data/api-config.json`。所有临时文件统一放在项目的 `.claude/.tmp/` 目录下（该目录已被 `.claude/` 规则 gitignore），每次 Bash 调用开头赋值 `TMPPREFIX` 并确保目录存在。Python 子进程通过环境变量 `TMPPREFIX` 读取前缀：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
```

---

## 步骤 1：加载配置

先定位项目根目录（参见 SKILL.md 注意事项第 6 条）。

按优先级读取（环境变量 > 项目配置文件）：

1. `APIFOX_API_TOKEN` 环境变量 → Token
2. `APIFOX_PROJECT_ID` 环境变量 → ProjectId
3. `${PROJECT_ROOT}/.claude/apifox.json` 的 `apiToken` / `projectId`

读取配置文件时用 Bash，**不要将 Token 明文输出到终端**：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
python3 -c "
import json,os
cfg = {}
try: cfg = json.load(open(os.path.join('${PROJECT_ROOT}', '.claude', 'apifox.json')))
except: pass
t = os.environ.get('APIFOX_API_TOKEN', cfg.get('apiToken', ''))
p = os.environ.get('APIFOX_PROJECT_ID', cfg.get('projectId', ''))
print(f'HAS_TOKEN={\"yes\" if t else \"no\"}')
print(f'PID={p}')
" 2>/dev/null
```

将 Token 和 ProjectId 保存为后续 Bash 调用中的 shell 变量（通过读取配置文件赋值，不回显 Token）。

如果 Token 或 ProjectId 为空，**不要提示用户手动运行 init**，而是自动进入初始化流程：读取 `references/init.md` 并执行其中的步骤 2-4（收集凭证、验证连通性、保存配置）。完成后将获取到的 Token 和 ProjectId 赋值给 shell 变量，继续执行下方的拉取步骤。

---

## 步骤 2：获取目录结构

调用 Apifox export-openapi 获取现有文件夹结构。先用 Bash 赋值 TOKEN 和 PROJECT_ID（从步骤 1 加载的配置中取值，不回显 Token）：

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
EXPORT_RESULT=$(curl -s -w "\n%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"oasVersion":"3.0","exportFormat":"JSON","options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true}}')
HTTP_CODE=$(echo "$EXPORT_RESULT" | tail -1)
BODY=$(echo "$EXPORT_RESULT" | sed '$d')
echo "HTTP_CODE=$HTTP_CODE"
```

**必须检查 HTTP 状态码**：
- `200` → 继续解析
- `401` / `403` → Token 失效，自动进入初始化流程：读取 `references/init.md` 并执行步骤 2-4（收集凭证、验证连通性、保存配置），完成后用新的 Token 和 ProjectId 重试当前请求
- 其他 → 显示错误信息，中止流程

状态码为 200 时，将结果写入临时文件并解析 `x-apifox-folder` 属性，提取所有文件夹路径：
```bash
echo "$BODY" > "${TMPPREFIX}export.json"
python3 -c "
import json, re, sys
raw = open('${TMPPREFIX}export.json').read()
raw = re.sub(r'\\\\(?![\"\\\\\/bfnrtu])', r'\\\\\\\\', raw)
data = json.loads(raw, strict=False)
if 'paths' not in data:
    print('EXPORT_ERROR: 导出数据无 paths 字段，可能 API 返回了错误', file=sys.stderr)
    sys.exit(1)
folders = set()
for path_data in data.get('paths', {}).values():
    for method_data in path_data.values():
        if isinstance(method_data, dict):
            folder = method_data.get('x-apifox-folder', '')
            if folder:
                folders.add(folder)
for f in sorted(folders):
    print(f)
"
```

如果文件夹列表为空，提示用户当前 Apifox 项目中尚无接口，中止流程。

---

## 步骤 3：用户选择目录

用 `AskUserQuestion` 展示目录列表，**支持多选**（`multiSelect: true`）：

- 选项为步骤 2 中提取到的所有文件夹路径（已排序）
- 用户可同时选择多个目录

示例：
```
请选择要拉取的目录（可多选）：
☐ 用户管理
☐ 用户管理/基础操作
☐ 设备管理
☐ 订单管理
```

记录用户选择的目录名称列表，传递给步骤 4。

---

## 步骤 4：按目录导出

从步骤 2 导出的全量数据中，按实际的 `x-apifox-folder` 值分组，每个子目录生成一个独立文件。

**重要**：不需要再次调用 API，直接从 `${TMPPREFIX}export.json` 中按 `x-apifox-folder` 属性筛选即可。

**目录结构映射规则**：
- 用户选择 `分批测试` → 筛选所有 `x-apifox-folder` 为 `分批测试` 或以 `分批测试/` 开头的接口
- 按实际的 `x-apifox-folder` 值分组，每个唯一的文件夹路径生成一个 `.json` 文件
- 示例：`分批测试/A` → `.claude/apis/分批测试/A.json`，`分批测试/B` → `.claude/apis/分批测试/B.json`

```bash
python3 << 'PYEOF'
import json, re, sys, os

tmpprefix = os.environ['TMPPREFIX']
raw = open(f'{tmpprefix}export.json').read()
raw = re.sub(r'\\(?!["\\\/bfnrtu])', r'\\\\', raw)
data = json.loads(raw, strict=False)

# 用户选择的目录列表（由上一步传入）
selected_folders = [SELECTED_FOLDERS_LIST]

# 第一步：收集所有匹配的接口，按实际 x-apifox-folder 分组
folder_groups = {}  # {实际文件夹路径: {api_path: {method: detail}}}
for sel_folder in selected_folders:
    for path, methods in data.get('paths', {}).items():
        for method, detail in methods.items():
            if isinstance(detail, dict):
                op_folder = detail.get('x-apifox-folder', '')
                if op_folder == sel_folder or op_folder.startswith(sel_folder + '/'):
                    folder_groups.setdefault(op_folder, {}).setdefault(path, {})[method] = detail

if not folder_groups:
    print('WARN: 所选目录下未找到任何接口')
    sys.exit(0)

# 需要保留的扩展属性白名单
KEEP_EXTENSIONS = {'x-apifox-folder', 'x-apifox-status', 'x-apifox-enum'}

def clean_extensions(obj):
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key.startswith('x-') and key not in KEEP_EXTENSIONS:
                del obj[key]
        for v in obj.values():
            clean_extensions(v)
    elif isinstance(obj, list):
        for item in obj:
            clean_extensions(item)

def collect_refs(obj, refs):
    if isinstance(obj, dict):
        if '$ref' in obj:
            ref = obj['$ref']
            if ref.startswith('#/components/schemas/'):
                refs.add(ref.split('/')[-1])
        for v in obj.values():
            collect_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            collect_refs(item, refs)

all_schemas = data.get('components', {}).get('schemas', {})

# 第二步：对每个实际文件夹路径，生成独立的精简 JSON
for actual_folder in sorted(folder_groups.keys()):
    filtered_paths = folder_groups[actual_folder]

    # 收集引用的 schema 名称（递归）
    refs = set()
    collect_refs(filtered_paths, refs)

    resolved = set()
    def resolve_schema_refs(name):
        if name in resolved or name not in all_schemas:
            return
        resolved.add(name)
        collect_refs(all_schemas[name], refs)
        for ref_name in list(refs):
            if ref_name not in resolved:
                resolve_schema_refs(ref_name)

    for ref_name in list(refs):
        resolve_schema_refs(ref_name)

    filtered_schemas = {name: all_schemas[name] for name in refs if name in all_schemas}

    # 清理冗余扩展属性
    clean_extensions(filtered_paths)
    clean_extensions(filtered_schemas)

    result = {
        'paths': filtered_paths,
        'components': {'schemas': filtered_schemas}
    }

    # 保存到临时文件（供步骤 6 使用）
    safe_name = actual_folder.replace('/', '__')
    out_path = f'{tmpprefix}pull-{safe_name}.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    api_count = sum(len(m) for m in filtered_paths.values())
    schema_count = len(filtered_schemas)
    print(f'OK: "{actual_folder}" — {api_count} 个接口, {schema_count} 个 Schema → {out_path}')
PYEOF
```

**说明**：
- `SELECTED_FOLDERS_LIST` 需替换为实际的 Python 列表字面量，如 `'用户管理', '设备管理'`
- 目录匹配采用精确匹配 + 子目录前缀匹配（`op_folder == folder or op_folder.startswith(folder + '/')`），确保选择父目录时包含子目录的接口
- 递归收集 schema 引用，确保嵌套引用的 schema 不遗漏

---

## 步骤 5：精简 OpenAPI

> 此步骤已内联到步骤 4 的 python3 脚本中。

精简规则汇总：
1. **不包含**顶层 `openapi`、`info`、`servers`、`tags`、`externalDocs`、`security` 字段
2. **保留** `paths` 和 `components`（仅 schemas 部分）
3. **删除**冗余扩展属性：`x-apifox-name`、`x-apifox-id` 及其他内部标识
4. **保留**有价值的扩展属性：`x-apifox-folder`、`x-apifox-status`、`x-apifox-enum`、`x-source-controller`、`x-source-method-fq`
5. 仅包含被 paths 引用的 schemas（递归解析引用链），不含全量 schemas

> **锚点保留说明**：`x-source-controller` 和 `x-source-method-fq` 在 push 时由本工具写入，pull 时保留可以帮助用户在本地 JSON 中识别接口来源；如果远程接口没有这两个字段（手动在 Apifox 创建的），pull 结果中自然也不会有。

---

## 步骤 5.5：本地/远程 diff 预览

在把临时文件写回 `.claude/apis/` 前，先做一次 diff 预览，避免静默覆盖掉用户本地的修改。

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export PROJECT_ROOT
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"

python3 << 'PYEOF'
import os, json, glob

project_root = os.environ.get('PROJECT_ROOT', '') or os.popen("git rev-parse --show-toplevel 2>/dev/null || echo $PWD").read().strip()
tmpprefix = os.environ['TMPPREFIX']
apis_dir = os.path.join(project_root, '.claude', 'apis')

def flatten_ops(data):
    """将 paths 扁平化为 {METHOD path: detail_dict}，比较时直接用 dict 相等。"""
    out = {}
    for path, methods in data.get('paths', {}).items():
        if not isinstance(methods, dict):
            continue
        for method, detail in methods.items():
            if not isinstance(detail, dict):
                continue
            out[f'{method.upper()} {path}'] = detail
    return out

def folder_from_tmp(tmp_path):
    """pull-分批测试__B.json → 分批测试/B"""
    basename = os.path.basename(tmp_path)
    folder_safe = basename.replace('apifox-sync-pull-', '').replace('.json', '')
    return folder_safe.replace('__', '/')

def local_file_for(actual_folder):
    parts = actual_folder.rsplit('/', 1)
    if len(parts) == 2:
        return os.path.join(apis_dir, parts[0], parts[1] + '.json')
    return os.path.join(apis_dir, actual_folder + '.json')

summary = {}  # {actual_folder: {'new'|'updated'|'removed'|'unchanged': [...], 'status': 'new-file'|'diff'|'nochange'}}

for tmp_path in sorted(glob.glob(f'{tmpprefix}pull-*.json')):
    actual_folder = folder_from_tmp(tmp_path)
    new_data = json.load(open(tmp_path, encoding='utf-8'))
    new_ops = flatten_ops(new_data)

    local_path = local_file_for(actual_folder)
    if not os.path.exists(local_path):
        summary[actual_folder] = {
            'status': 'new-file',
            'new': sorted(new_ops.keys()),
            'updated': [],
            'removed': [],
            'unchanged': []
        }
        continue

    try:
        old_data = json.load(open(local_path, encoding='utf-8'))
    except Exception as e:
        summary[actual_folder] = {
            'status': 'new-file',
            'new': sorted(new_ops.keys()),
            'updated': [],
            'removed': [],
            'unchanged': [],
            '_warn': f'本地文件解析失败，按全新文件处理: {e}'
        }
        continue

    old_ops = flatten_ops(old_data)
    added    = sorted(new_ops.keys() - old_ops.keys())
    removed  = sorted(old_ops.keys() - new_ops.keys())
    changed  = sorted([k for k in new_ops.keys() & old_ops.keys() if new_ops[k] != old_ops[k]])
    same     = sorted([k for k in new_ops.keys() & old_ops.keys() if new_ops[k] == old_ops[k]])

    if not (added or removed or changed):
        summary[actual_folder] = {'status': 'nochange', 'new': [], 'updated': [], 'removed': [], 'unchanged': same}
    else:
        summary[actual_folder] = {'status': 'diff', 'new': added, 'updated': changed, 'removed': removed, 'unchanged': same}

json.dump(summary, open(f'{tmpprefix}pull-diff.json', 'w'), ensure_ascii=False, indent=2)

# 人类可读摘要
for folder in sorted(summary.keys()):
    info = summary[folder]
    local = os.path.relpath(local_file_for(folder), project_root)
    if info['status'] == 'new-file':
        print(f'\n[NEW  ] {folder}  → {local}（本地不存在，将新建）')
        for k in info['new']:
            print(f'    + {k}')
        if info.get('_warn'):
            print(f'    ⚠️  {info["_warn"]}')
    elif info['status'] == 'nochange':
        print(f'\n[ SAME] {folder}  → {local}（无变化，{len(info["unchanged"])} 个接口）')
    else:
        print(f'\n[DIFF ] {folder}  → {local}')
        for k in info['new']:     print(f'    + {k}')
        for k in info['updated']: print(f'    ~ {k}')
        for k in info['removed']: print(f'    - {k}  （远程已删除，本地将被清理）')
PYEOF
```

解析上面打印的摘要后，用 `AskUserQuestion` 询问用户如何处理：

- **全部覆盖**（推荐）→ 把所有临时文件 move 到目标路径
- **逐目录选择** → 再次用 `AskUserQuestion`（multiSelect），仅覆盖勾选的目录，其他目录对应的临时文件删除不落盘
- **取消** → 删除所有临时文件，中止流程

特殊情况：
- 若所有目录的 `status` 都是 `nochange`，直接跳过询问，提示"远程无变化"并进入步骤 7 的清理。
- 若所有目录的 `status` 都是 `new-file`（即本地首次 pull），建议默认全部保存，可以跳过询问直接写入。

确认结果保存为临时文件 `${TMPPREFIX}pull-approved.json`（字符串数组，装着用户同意覆盖的 `actual_folder` 列表）：

```bash
# 全部覆盖时
python3 -c "
import json, glob, os
tmpprefix = os.environ['TMPPREFIX']
folders = []
for p in glob.glob(f'{tmpprefix}pull-*.json'):
    if p.endswith('pull-diff.json') or p.endswith('pull-approved.json'):
        continue
    base = os.path.basename(p).replace('apifox-sync-pull-','').replace('.json','')
    folders.append(base.replace('__','/'))
json.dump(sorted(folders), open(f'{tmpprefix}pull-approved.json','w'), ensure_ascii=False)
"
```

---

## 步骤 6：保存文件

只把用户在步骤 5.5 确认过的目录（`${TMPPREFIX}pull-approved.json` 中列出的）从临时文件移动到 `.claude/apis/`。未在白名单里的临时文件直接删除，不写入本地。

**路径映射规则**：每个实际的 `x-apifox-folder` 路径直接映射为本地目录 + 文件名：
- `分批测试/A` → `.claude/apis/分批测试/A.json`
- `分批测试/B` → `.claude/apis/分批测试/B.json`
- `设备管理/无人机` → `.claude/apis/设备管理/无人机.json`
- `用户管理` → `.claude/apis/用户管理.json`（无子目录时直接作为文件名）

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export PROJECT_ROOT
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"

python3 << 'PYEOF'
import os, shutil, glob, json

project_root = os.environ.get('PROJECT_ROOT', '') or os.popen("git rev-parse --show-toplevel 2>/dev/null || echo $PWD").read().strip()
tmpprefix = os.environ['TMPPREFIX']
apis_dir = os.path.join(project_root, '.claude', 'apis')

# 读取已确认的白名单
approved_path = f'{tmpprefix}pull-approved.json'
try:
    approved = set(json.load(open(approved_path, encoding='utf-8')))
except Exception:
    approved = set()  # 缺失视为全部跳过（步骤 5.5 应该已经写入）

saved_files = []
skipped_files = []

for tmp_path in sorted(glob.glob(f'{tmpprefix}pull-*.json')):
    if tmp_path.endswith('pull-diff.json') or tmp_path.endswith('pull-approved.json'):
        continue

    # 从临时文件名还原实际文件夹路径：pull-分批测试__B.json → 分批测试/B
    basename = os.path.basename(tmp_path)
    folder_safe = basename.replace('apifox-sync-pull-', '').replace('.json', '')
    actual_folder = folder_safe.replace('__', '/')

    if actual_folder not in approved:
        os.remove(tmp_path)
        skipped_files.append(actual_folder)
        continue

    # 构建目标路径
    parts = actual_folder.rsplit('/', 1)
    if len(parts) == 2:
        parent_dir = os.path.join(apis_dir, parts[0])
        os.makedirs(parent_dir, exist_ok=True)
        target_path = os.path.join(apis_dir, parts[0], parts[1] + '.json')
    else:
        os.makedirs(apis_dir, exist_ok=True)
        target_path = os.path.join(apis_dir, actual_folder + '.json')

    shutil.move(tmp_path, target_path)
    rel_path = os.path.relpath(target_path, project_root)
    saved_files.append((actual_folder, rel_path))

for folder, rel_path in saved_files:
    print(f'SAVED: "{folder}" → {rel_path}')
for folder in skipped_files:
    print(f'SKIP : "{folder}" （用户未勾选，本地保持不变）')
PYEOF
```

---

## 步骤 7：输出结果摘要并清理

输出拉取结果摘要，包含：
- 拉取的目录数量
- 每个目录的接口数量
- 保存的文件路径

格式示例：
```
拉取完成！

| 目录 | 接口数 | 保存路径 |
|------|--------|---------|
| 用户管理 | 5 | .claude/apis/用户管理.json |
| 设备管理 | 8 | .claude/apis/设备管理.json |

共拉取 2 个目录，13 个接口。
```

清理临时文件：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
TMPDIR="${PROJECT_ROOT}/.claude/.tmp"
rm -f "${TMPDIR}/apifox-sync-export.json" \
      "${TMPDIR}/apifox-sync-pull-diff.json" \
      "${TMPDIR}/apifox-sync-pull-approved.json"
find "${TMPDIR}" -maxdepth 1 -name "apifox-sync-pull-*.json" -delete 2>/dev/null
```
