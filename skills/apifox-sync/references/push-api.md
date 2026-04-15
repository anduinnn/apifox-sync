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

### 11.1 构建双向索引

复用步骤 8 导出的 `${TMPPREFIX}export.json`，同时建立两个查找表：

1. **path+method 索引** `existing`：`{METHOD}:{path}` → `[folders]`，用于判断跨文件夹冲突
2. **源码锚点索引** `by_source`：`x-source-method-fq` → `{path, method, folder, apifox_id}`，用于识别同一 Java 方法 path/method 变更

```bash
python3 -c "
import json, re
raw = open('${TMPPREFIX}export.json').read()
raw = re.sub(r'\\\\(?![\"\\\\\/bfnrtu])', r'\\\\\\\\', raw)
data = json.loads(raw, strict=False)

def extract_apifox_id(detail):
    # 优先 x-apifox-id（如果将来 Apifox 放回来了）
    aid = detail.get('x-apifox-id')
    if aid:
        return str(aid)
    # 降级：从 x-run-in-apifox URL 里提取，格式 .../apis/api-{id}-run
    run_url = detail.get('x-run-in-apifox', '')
    m = re.search(r'/apis/api-(\d+)(?:-run|$)', run_url)
    return m.group(1) if m else None

existing = {}
by_source = {}
for path, methods in data.get('paths', {}).items():
    for method, detail in methods.items():
        if not isinstance(detail, dict):
            continue
        folder = detail.get('x-apifox-folder', '')
        key = f'{method.upper()}:{path}'
        existing.setdefault(key, []).append(folder)
        source_fq = detail.get('x-source-method-fq')
        if source_fq:
            by_source[source_fq] = {
                'path': path,
                'method': method.upper(),
                'folder': folder,
                'apifox_id': extract_apifox_id(detail),
                'controller': detail.get('x-source-controller', ''),
                'summary': detail.get('summary', '')
            }
json.dump(existing, open('${TMPPREFIX}existing.json', 'w'))
json.dump(by_source, open('${TMPPREFIX}by-source.json', 'w'), ensure_ascii=False)
print(f'已索引 {len(existing)} 个现有接口，其中 {len(by_source)} 个带源码锚点')
"
```

> **Apifox 行为注记**：Apifox 的 export-openapi 不会输出 `x-apifox-id`，但会输出 `x-run-in-apifox`（形如 `https://app.apifox.com/web/project/{pid}/apis/api-{id}-run`），`extract_apifox_id` 从这个 URL 用正则提取接口 ID。这是 `DELETE /http-apis/{id}` 能工作的**必要前提**——如果某接口没有 `x-run-in-apifox`，说明它可能是旧版本接口或手工创建的未落库状态，该条不能自动删除，11.4 需在报告中显式列出。

### 11.2 分类并生成 payload

读取生成的 spec 和双索引，将 operations 分为四类：

| 类别 | 判定条件 | 后续动作 |
|------|---------|---------|
| **update** | 源码锚点命中 & 远程 path+method 与 spec 相同 | 归入更新批次（`AUTO_MERGE`） |
| **rename** | 源码锚点命中 & 远程 path/method 与 spec 不同 | 输出到死接口确认清单（11.3） |
| **create** | 源码锚点未命中 & 目标文件夹中无同 path+method | 归入新建批次（`CREATE_NEW`） |
| **skip** | 目标文件夹中已有同 path+method，且其他文件夹也有 | 列为跨文件夹冲突，跳过 |

没有锚点的老数据降级到原有 path+method 匹配逻辑，保证平滑升级。

```bash
python3 << 'PYEOF'
import json, copy, os

tmpprefix = os.environ['TMPPREFIX']
spec = json.load(open(f'{tmpprefix}spec.json'))
existing = json.load(open(f'{tmpprefix}existing.json'))
by_source = json.load(open(f'{tmpprefix}by-source.json'))

update_paths = {}   # AUTO_MERGE 批次
create_paths = {}   # CREATE_NEW 批次
rename_list  = []   # 待用户确认的死接口 [{source_fq, old, new, apifox_id, summary}]
skipped      = []   # 跨文件夹冲突

for path, methods in spec.get('paths', {}).items():
    for method, detail in methods.items():
        if not isinstance(detail, dict):
            continue
        target_folder = detail.get('x-apifox-folder', '')
        source_fq = detail.get('x-source-method-fq')
        key = f'{method.upper()}:{path}'

        # 1) 先按源码锚点匹配
        if source_fq and source_fq in by_source:
            prev = by_source[source_fq]
            if prev['path'] == path and prev['method'] == method.upper():
                # path/method 都没变 → 走正常更新逻辑，继续做跨文件夹冲突检查
                existing_folders = existing.get(key, [])
                other_folders = [f for f in existing_folders if f != target_folder]
                if target_folder in existing_folders and other_folders:
                    skipped.append(f'{method.upper()} {path} (目标: {target_folder}, 冲突: {", ".join(other_folders)})')
                else:
                    update_paths.setdefault(path, {})[method] = detail
            else:
                # path 或 method 变了 → 重命名候选
                rename_list.append({
                    'source_fq': source_fq,
                    'old': {'path': prev['path'], 'method': prev['method'], 'folder': prev['folder']},
                    'new': {'path': path, 'method': method.upper(), 'folder': target_folder},
                    'apifox_id': prev['apifox_id'],
                    'summary': prev.get('summary') or detail.get('summary', '')
                })
                # 新接口先放到 create_paths（删除旧接口的动作在 11.3 决定）
                create_paths.setdefault(path, {})[method] = detail
            continue

        # 2) 无锚点：降级到原有 path+method 匹配
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

if rename_list:
    json.dump(rename_list, open(f'{tmpprefix}rename-list.json', 'w'), ensure_ascii=False)

update_count = sum(len(m) for m in update_paths.values())
create_count = sum(len(m) for m in create_paths.values())
print(f'更新批次: {update_count} 个接口')
print(f'新建批次: {create_count} 个接口')
print(f'重命名候选(死接口待确认): {len(rename_list)} 个')
for r in rename_list:
    old, new = r['old'], r['new']
    tag = r.get('summary') or r['source_fq']
    print(f'  · {tag}')
    print(f'    旧: {old["method"]} {old["path"]}  (folder={old["folder"]})')
    print(f'    新: {new["method"]} {new["path"]}  (folder={new["folder"]})')
if skipped:
    print(f'跳过(冲突): {len(skipped)} 个接口')
    for s in skipped:
        print(f'  - {s}')
    print('提示: 以上接口在多个文件夹中存在同 path+method，AUTO_MERGE 无法精确更新目标文件夹的接口。请在 Apifox 中手动删除其他文件夹的重复接口后重新推送。')
PYEOF
```

### 11.3 死接口用户确认

如果步骤 11.2 生成了 `${TMPPREFIX}rename-list.json`，说明存在"源码里同一个方法，但 path/method 已变更"的接口。这些就是即将残留的"死接口"。

**Apifox 的 import-openapi 无法按 operationId 精确更新跨 path 的接口**，所以必须通过独立的 DELETE 调用清理旧接口，否则它会原地不动。

用 `AskUserQuestion` 让用户决定：

```
检测到 {N} 个接口路径/method 发生变更，旧接口会变成死文档：
  · UserController#updateUser
    旧: PUT /api/users  (folder=用户管理)
    新: PUT /api/users/{id}  (folder=用户管理)
  · UserController#listUsers
    旧: GET /api/user/list  (folder=用户管理)
    新: GET /api/users  (folder=用户管理)

如何处理？
  [ ] 全部删除旧接口
  [ ] 逐项选择（再次确认每一条）
  [ ] 全部保留（只推送新接口，不清理旧的）
```

- **全部删除** → 走步骤 11.4，对 `rename-list.json` 中所有条目调用 DELETE
- **逐项选择** → 再次用 `AskUserQuestion`（multiSelect 模式）给出每条的勾选项；把被勾选的过滤写回 `${TMPPREFIX}rename-confirmed.json`，未勾选的略过
- **全部保留** → 写入空的 `${TMPPREFIX}rename-confirmed.json`（`[]`）

如果某条的 `apifox_id` 为空（极少数老接口没有 `x-apifox-id`），跳过该条并在提示中说明"无法自动删除，请到 Apifox 手动处理"。

Bash 伪代码（由 Claude 把 AskUserQuestion 的结果写入确认清单）：
```bash
# 如果 rename-list.json 存在但 rename-confirmed.json 未生成，说明用户选择了"全部保留"
# 直接 cp 空数组
if [ -f "${TMPPREFIX}rename-list.json" ] && [ ! -f "${TMPPREFIX}rename-confirmed.json" ]; then
  echo '[]' > "${TMPPREFIX}rename-confirmed.json"
fi
```

### 11.4 调用 DELETE 清理旧接口

对 `${TMPPREFIX}rename-confirmed.json` 中每一条，通过 Apifox 开放 API 的"删除接口"端点清理。

**重要**：此端点的 URL 前缀与 import/export 不同，baseUrl 是 `/api/v1/` 而非 `/v1/`。首次调用若返回 404，降级改用 fallback URL（见 `data/api-config.json` 的 `deleteApi` / `deleteApiFallback`）。

**实现要点**：
- 必须在**父 shell 内**直接 curl（不要生成独立 .sh 用 `bash` 子进程执行）。子 shell 默认拿不到父 shell 未 export 的变量，会导致 `Authorization: Bearer ` 空值 → Apifox 返回 403/401。
- 用 python3 把确认清单铺成 `{id}\t{method} {path}` 格式，bash 用 `while read` 在当前 shell 迭代。

```bash
if [ -f "${TMPPREFIX}rename-confirmed.json" ]; then
  while IFS=$'\t' read -r api_id label; do
    if [ -z "$api_id" ]; then
      echo "⚠️ 无 apifox_id，无法自动删除：${label}（请到 Apifox 手动清理）"
      continue
    fi
    R=$(curl -s -o "${TMPPREFIX}del-response.out" -w "%{http_code}" -X DELETE \
      "https://api.apifox.com/api/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "X-Apifox-Api-Version: 2024-03-28")
    if [ "$R" = "404" ]; then
      # 降级 fallback：/v1/ 前缀
      R=$(curl -s -o "${TMPPREFIX}del-response.out" -w "%{http_code}" -X DELETE \
        "https://api.apifox.com/v1/projects/${PROJECT_ID}/http-apis/${api_id}" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "X-Apifox-Api-Version: 2024-03-28")
    fi
    echo "DELETE ${label} (id=${api_id}) -> HTTP ${R}"
  done < <(python3 -c "
import json, os
tmp=os.environ['TMPPREFIX']
for it in json.load(open(f'{tmp}rename-confirmed.json')):
    aid=it.get('apifox_id') or ''
    print(f\"{aid}\t{it['old']['method']} {it['old']['path']}\")
")
fi
```

**状态码处理**：
- `200` / `204` → 删除成功
- `302` / `404` → 接口已不存在（可能被后台删过了，或本次 push 中被重复确认），视为成功
- `401` / `403` → Token 失效或无权限（需要"项目维护者"角色才能删除接口），提示用户在 Apifox 控制台确认 Token 权限
- 其他 → 在最终报告里列出"删除失败清单"，提示用户手动处理

> **实测说明**：Apifox 的 DELETE 端点在删除**已不存在**的接口时会返回 HTTP 302（重定向），这不是真正的重定向，而是"目标已失效"的一种信号。因此把 302 与 404 同等对待。

### 11.5 分别推送

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
- 更新批次、新建批次的接口数量
- 删除的旧接口数量（若 11.4 有执行）
- 跳过的跨文件夹冲突数量
- 目标文件夹名称（如选择了文件夹）
- Apifox 项目链接：`https://app.apifox.com/project/${PROJECT_ID}`

示例格式：
```
推送完成（目标文件夹：用户管理）
  · 更新 3 个接口
  · 新建 1 个接口
  · 清理 2 个死接口（path 变更）
  · 跳过 0 个跨文件夹冲突
项目链接：https://app.apifox.com/project/${PROJECT_ID}
```

如果 11.4 有删除失败的条目，单独列出：
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
