# Push 步骤 8、10-12：文件夹选择、验证、推送、报告

API 常量参考 `data/api-config.json`。所有临时文件统一放在项目的 `.claude/.tmp/` 目录下（该目录已被 `.claude/` 规则 gitignore），每次 Bash 调用开头赋值 `TMPPREFIX` 并确保目录存在。Python 子进程通过环境变量 `TMPPREFIX` 读取前缀：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
```

---

## 步骤 8：获取文件夹结构并选择

**重要**：此步骤必须在生成 OpenAPI Spec（步骤 9）之前执行，因为 spec 中的 `x-apifox-folder` 属性需要用户选择的文件夹路径。

调用 Apifox export-openapi 获取现有文件夹结构。先用 Bash 赋值 TOKEN 和 PROJECT_ID（从步骤 2 加载的配置中取值，不回显 Token）：

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
- 其他 → 显示错误信息，中止推送

状态码为 200 时，将结果写入临时文件并解析 `x-apifox-folder` 属性：
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

用 `AskUserQuestion` 让用户选择目标文件夹：
- 选项包含发现的所有文件夹 + "新建文件夹（输入路径）" + "项目根目录（不指定文件夹）"
- 如果用户选择"新建文件夹"，再用 `AskUserQuestion` 让用户输入文件夹路径（支持多级路径，用 `/` 分隔，如 `设备管理/无人机`）

如果文件夹列表为空（新建项目），直接用 `AskUserQuestion` 询问用户是否需要指定文件夹路径，还是推送到项目根目录。

**说明**：目标文件夹路径将写入 OpenAPI spec 每个 operation 的 `x-apifox-folder` 属性。Apifox 在导入时会自动创建不存在的文件夹层级。

选择完毕后，将文件夹路径保存为变量 `TARGET_FOLDER`，传递给步骤 9（生成 spec）使用。

---

## 步骤 10：JSON 预验证

**关键步骤**：OpenAPI JSON 格式容错为零，必须在推送前验证。

1. 将生成的 OpenAPI JSON 以美化格式（indent=2）写入临时文件：
```bash
cat > "${TMPPREFIX}spec.json" << 'SPECEOF'
{生成的 JSON，美化格式}
SPECEOF
```

2. 用 python3 验证 JSON 语法：
```bash
python3 -c "import json; json.load(open('${TMPPREFIX}spec.json')); print('JSON_VALID')"
```

3. 如果验证失败（`json.JSONDecodeError`）：
   - 读取错误信息，定位问题位置
   - 修复 JSON 格式（常见问题：未转义的引号、尾逗号、注释）
   - 重新验证，最多重试 3 次

4. 验证成功后继续推送流程。

## 步骤 11：推送到 Apifox

Apifox 以 path+method 为匹配键。为支持**同 path+method 在不同文件夹中作为独立接口**，需要按文件夹匹配情况分批推送。

### 11.1 构建现有接口查找表

复用步骤 8 导出的 `${TMPPREFIX}export.json`，提取每个 operation 的 `{method}:{path}` → `x-apifox-folder` 映射：

```bash
python3 -c "
import json, re
raw = open('${TMPPREFIX}export.json').read()
raw = re.sub(r'\\\\(?![\"\\\\\/bfnrtu])', r'\\\\\\\\', raw)
data = json.loads(raw, strict=False)
existing = {}
for path, methods in data.get('paths', {}).items():
    for method, detail in methods.items():
        if isinstance(detail, dict):
            folder = detail.get('x-apifox-folder', '')
            key = f'{method.upper()}:{path}'
            existing.setdefault(key, []).append(folder)
json.dump(existing, open('${TMPPREFIX}existing.json', 'w'))
print(f'已索引 {len(existing)} 个现有接口')
"
```

### 11.2 分批生成 payload

读取生成的 spec 和查找表，将 operations 分为三类：安全更新、新建、冲突跳过。各自生成独立的 OpenAPI spec（共享 info 和 components/schemas）：

```bash
python3 << 'PYEOF'
import json, copy, os

tmpprefix = os.environ['TMPPREFIX']
spec = json.load(open(f'{tmpprefix}spec.json'))
existing = json.load(open(f'{tmpprefix}existing.json'))

update_paths = {}   # 目标文件夹中已存在，且无其他文件夹重复 → 安全更新
create_paths = {}   # 目标文件夹中不存在 → 新建
skipped = []        # 目标文件夹中已存在，但其他文件夹也有同 path+method → 跳过

for path, methods in spec.get('paths', {}).items():
    for method, detail in methods.items():
        if not isinstance(detail, dict):
            continue
        target_folder = detail.get('x-apifox-folder', '')
        key = f'{method.upper()}:{path}'
        existing_folders = existing.get(key, [])

        if target_folder in existing_folders:
            other_folders = [f for f in existing_folders if f != target_folder]
            if other_folders:
                skipped.append(f'{method.upper()} {path} (目标: {target_folder}, 冲突: {", ".join(other_folders)})')
            else:
                update_paths.setdefault(path, {})[method] = detail
        else:
            create_paths.setdefault(path, {})[method] = detail

def build_spec(base_spec, paths):
    s = copy.deepcopy(base_spec)
    s['paths'] = paths
    return s

def build_payload(spec_obj, behavior, update_folder):
    return {
        'input': json.dumps(spec_obj, ensure_ascii=False),
        'options': {
            'endpointOverwriteBehavior': behavior,
            'schemaOverwriteBehavior': 'OVERWRITE_EXISTING',
            'updateFolderOfChangedEndpoint': update_folder,
            'prependBasePath': False
        }
    }

if update_paths:
    payload = build_payload(build_spec(spec, update_paths), 'AUTO_MERGE', False)
    json.dump(payload, open(f'{tmpprefix}payload-update.json', 'w'), ensure_ascii=False)

if create_paths:
    payload = build_payload(build_spec(spec, create_paths), 'CREATE_NEW', True)
    json.dump(payload, open(f'{tmpprefix}payload-create.json', 'w'), ensure_ascii=False)

update_count = sum(len(m) for m in update_paths.values())
create_count = sum(len(m) for m in create_paths.values())
print(f'更新批次: {update_count} 个接口')
print(f'新建批次: {create_count} 个接口')
if skipped:
    print(f'跳过(冲突): {len(skipped)} 个接口')
    for s in skipped:
        print(f'  - {s}')
    print('提示: 以上接口在多个文件夹中存在同 path+method，AUTO_MERGE 无法精确更新目标文件夹的接口。请在 Apifox 中手动删除其他文件夹的重复接口后重新推送。')
PYEOF
```

### 11.3 分别推送

对每个非空批次执行推送。用 Bash 检查 payload 文件是否存在，存在则推送：

**更新批次**（`AUTO_MERGE`）：
```bash
if [ -f "${TMPPREFIX}payload-update.json" ]; then
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" \
    -d @"${TMPPREFIX}payload-update.json")
  echo "=== 更新批次 ==="
  echo "$RESULT"
fi
```

**新建批次**（`CREATE_NEW`）：
```bash
if [ -f "${TMPPREFIX}payload-create.json" ]; then
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" \
    -d @"${TMPPREFIX}payload-create.json")
  echo "=== 新建批次 ==="
  echo "$RESULT"
fi
```

判断每个批次的结果：
- `200` → 推送成功
- `401` / `403` → Token 失效，自动进入初始化流程：读取 `references/init.md` 并执行步骤 2-4（收集凭证、验证连通性、保存配置），完成后用新的 Token 和 ProjectId 重试推送
- `400` → spec 格式错误，显示 Apifox 返回的错误信息
- 其他 → 显示状态码和响应体

## 步骤 12：报告结果

推送成功时报告：
- 推送的接口数量（paths 中的 operation 数）
- 目标文件夹名称（如选择了文件夹）
- Apifox 项目链接：`https://app.apifox.com/project/${PROJECT_ID}`

清理临时文件：
```bash
rm -f "${TMPPREFIX}"spec.json "${TMPPREFIX}"export.json "${TMPPREFIX}"existing.json "${TMPPREFIX}"payload-update.json "${TMPPREFIX}"payload-create.json
```
