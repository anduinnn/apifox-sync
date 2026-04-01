---
name: apifox-sync
description: 将 Spring Boot Controller 接口同步到 Apifox 项目
argument-hint: "<init|push> [args]"
level: 2
---

# Apifox Sync

将 Spring Boot Controller 接口定义从源码解析后推送到 Apifox 指定项目。

## 子命令路由

解析 `{{ARGUMENTS}}` 的第一个词：

- `init` → 执行 [Init 子命令](#init-子命令)
- `push` → 执行 [Push 子命令](#push-子命令)
- 无参数或未知子命令 → 提示用法：
  > 用法：
  > - `/apifox-sync init` — 配置 Apifox API Token 和项目 ID
  > - `/apifox-sync push @Controller.java` — 推送整个 Controller
  > - `/apifox-sync push @Controller.java#L35` — 推送单个接口

---

## Init 子命令

### 步骤 1：读取现有配置

**配置文件位置**：项目级 `.claude/apifox.json`（每个项目独立配置，包含 apiToken 和 projectId）。

先定位项目根目录：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
```

检查是否已有配置（按优先级）：

1. 用 Bash 检查环境变量：
```bash
echo "TOKEN=${APIFOX_API_TOKEN:-}" && echo "PID=${APIFOX_PROJECT_ID:-}"
```

2. 用 Bash 读取项目配置文件：
```bash
cat "${PROJECT_ROOT}/.claude/apifox.json" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'TOKEN={d.get(\"apiToken\",\"\")}'); print(f'PID={d.get(\"projectId\",\"\")}')" 2>/dev/null || echo "NO_CONFIG"
```

如果已有有效配置，展示当前配置并询问是否覆盖。

### 步骤 2：收集凭证

用 `AskUserQuestion` 收集 API Token：
- 提示：从 Apifox 头像 → 账号设置 → API 访问令牌 → 新建令牌
- 格式：`afxp_xxxxxxxxxx`
- 建议选择"永不过期"

用 `AskUserQuestion` 收集 Project ID：
- 提示：从 Apifox 项目设置 → 基本设置 → 基本信息 → 项目 ID
- 格式：纯数字字符串

### 步骤 3：验证连通性

用 Bash 执行：
```bash
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"oasVersion":"3.0","exportFormat":"JSON","options":{"includeApifoxExtensionProperties":false}}')
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
```

判断结果：
- `200` → 连接成功
- `401` / `403` → Token 无效，提示用户检查令牌
- `404` → Project ID 无效，提示用户检查项目 ID
- `422` → API 版本头缺失（内部错误）
- 其他 → 网络错误，显示状态码

### 步骤 4：保存配置

验证成功后，用 Bash 写入项目级配置：
```bash
CONFIG_DIR="${PROJECT_ROOT}/.claude"
CONFIG_FILE="${CONFIG_DIR}/apifox.json"
mkdir -p "$CONFIG_DIR"
python3 -c "
import json
d = {'apiToken': '${TOKEN}', 'projectId': '${PROJECT_ID}'}
json.dump(d, open('${CONFIG_FILE}', 'w'), indent=2)
print('OK')
"
```

提示用户：配置已保存到 `${PROJECT_ROOT}/.claude/apifox.json`，可使用 `/apifox-sync push` 推送接口。建议将 `.claude/apifox.json` 加入 `.gitignore`（因为包含 apiToken）。

---

## Push 子命令

### 步骤 1：解析参数

从 `{{ARGUMENTS}}` 中去掉 `push` 后解析剩余参数：

- `@path/to/Controller.java` → 整个 Controller（所有方法）
  - 提取文件路径：去掉 `@` 前缀
- `@path/to/Controller.java#L35` → 单个方法
  - 提取文件路径：`@` 到 `#L` 之间
  - 提取行号：`#L` 后的数字

如果是相对路径，基于当前工作目录解析为绝对路径。

### 步骤 2：加载配置

先定位项目根目录：
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
```

按优先级读取（环境变量 > 项目配置文件）：

1. `APIFOX_API_TOKEN` 环境变量 → Token
2. `APIFOX_PROJECT_ID` 环境变量 → ProjectId
3. `${PROJECT_ROOT}/.claude/apifox.json` 的 `apiToken` / `projectId`

如果 Token 或 ProjectId 为空，提示用户先运行 `/apifox-sync init`。

### 步骤 3：读取 Controller 文件

用 Read 工具读取指定的 Controller 文件。验证：
- 文件存在
- 包含 `@RestController` 或 `@Controller` 注解

提取类级信息：
- **路径前缀**：类上的 `@RequestMapping("/xxx")` 的值。如果没有类级 `@RequestMapping`，前缀为空字符串。
- **Tag 名称**：类名（去掉 `Controller` 后缀），如 `PersonController` → `Person`

### 步骤 4：定位方法

**整个 Controller 模式**：
找出所有带以下注解的 public 方法：
- `@GetMapping`
- `@PostMapping`
- `@DeleteMapping`
- `@PutMapping`
- `@PatchMapping`
- `@RequestMapping(method = RequestMethod.XXX)`

**单个方法模式（指定行号）**：
从指定行号向上查找最近的方法声明（最多 20 行），包括其上方的 JavaDoc 注释和映射注解。

### 步骤 5：提取方法信息

对每个定位到的方法，提取：

#### 5.1 接口名称
从方法上方的 JavaDoc 注释 (`/** ... */`) 第一行提取。如果无 JavaDoc，使用方法名。

#### 5.2 HTTP 方法和路径
- `@PostMapping("/xxx")` → POST, `/xxx`
- `@GetMapping("/xxx")` → GET, `/xxx`
- `@DeleteMapping("/xxx/{id}")` → DELETE, `/xxx/{id}`
- `@PutMapping("/xxx")` → PUT, `/xxx`
- `@PatchMapping("/xxx")` → PATCH, `/xxx`
- `@RequestMapping(value="/xxx", method=RequestMethod.GET)` → GET, `/xxx`
- 完整路径 = 类级前缀 + 方法级路径

#### 5.3 请求参数

逐个分析方法的参数：

**框架注入参数（跳过，不纳入 spec）**：
`HttpServletRequest`、`HttpServletResponse`、`BindingResult`、`SseEmitter`、`RedirectAttributes` — 这些是 Spring 框架注入的，不是 API 参数。

**`@RequestBody ClassName param`**：
- 如果 ClassName 是 `List<T>`（如 `List<DeviceGroupSortReq>`）→ 请求体 schema 为 `{type: array, items: {$ref: T}}`
- 如果 ClassName 是 `List<String>` / `List<Long>` 等基础类型集合 → `{type: array, items: {type: string/integer}}`
- 否则 → 读取 ClassName 源文件，提取所有字段作为 request body schema（见步骤 6）

**`@PathVariable Long id`**：
- 作为 path parameter
- 提取参数名和类型

**`@RequestParam String name`**：
- 作为 query parameter
- 提取参数名和类型
- 如果有 `@RequestParam("alias")`，使用括号内的名称

**`@RequestParam("file") MultipartFile` / `@RequestPart("file") MultipartFile`**：
- 请求体为 `multipart/form-data`
- Schema: `{type: string, format: binary}`

#### 5.4 响应类型
提取方法返回类型：
- `R<XxxVO>` → 使用 R 骨架模板，data 字段引用 XxxVO schema
- `R<Page<XxxVO>>` → 使用 R 骨架 + Page 骨架，records 引用 XxxVO
- `R<List<XxxVO>>` → 使用 R 骨架，data 字段为 `{type: array, items: {$ref: XxxVO}}`
- `R<Void>` → 使用 R 骨架，data 字段设置 `nullable: true`，不生成 data schema
- `R<Long>` / `R<String>` / `R<Boolean>` → 使用 R 骨架，data 字段为对应基础类型
- `void` → 不生成响应 schema
- `SseEmitter` / 其他非 R 类型 → `{type: object}`

### 步骤 6：递归展开类型

对步骤 5 中涉及的所有自定义类型（DTO/VO/Entity/REQ/RESP），递归读取和展开。

**维护已访问类型集合**：防止循环引用导致无限递归。如果遇到已访问类型，使用 `$ref` 引用。

#### 6.1 定位类文件（三级降级）

**第一级**：从项目根目录（多模块项目的最顶层目录）Glob 搜索：
```
**/{ClassName}.java
```
这会覆盖所有子模块（包括 `*-interface` 模块）。

**第二级**：如果第一级未找到，根据 import 语句中的包名推断外部项目路径：
- 从 import 中提取包名前缀（如 `com.xxx.{module}.*`），在工作区的附加工作目录（additional working directories）或同级目录中查找匹配的项目
- 如果包名太短无法推断 → 直接降级

**第三级**：降级处理 — 将该类型映射为 `{type: object, additionalProperties: true}`，继续处理其他字段。

**静态内部类处理**：
如果 Glob 搜索 `**/{ClassName}.java` 返回 0 结果：
1. 在当前已读取的文件中搜索 `static class {ClassName}`
2. 如果未找到，用 Grep 在项目中搜索 `class {ClassName}` 定位其所在外部类文件
3. 如果仍未找到 → 降级为 `{type: object}`

#### 6.2 提取字段

读取类文件，提取所有字段：
- 字段名（camelCase）
- 字段类型（含泛型）
- 字段描述（JavaDoc 注释 / 行内注释 / `@see` 引用）
- 验证注解（`@NotNull`、`@NotBlank`、`@NotEmpty` → 标记为 required）

#### 6.3 处理继承

如果类有 `extends BaseClass`：
- 递归读取父类文件，提取父类字段
- 合并父类字段和子类字段（子类字段覆盖同名父类字段）

#### 6.4 类型映射

**基础类型（直接映射，不 Glob）**：

| Java 类型 | OpenAPI Schema |
|-----------|---------------|
| `String` | `{type: string}` |
| `Integer` / `int` | `{type: integer, format: int32}` |
| `Long` / `long` | `{type: integer, format: int64}` |
| `BigDecimal` | `{type: number}` |
| `Double` / `double` / `Float` / `float` | `{type: number}` |
| `Boolean` / `boolean` | `{type: boolean}` |
| `LocalDateTime` / `LocalDate` / `Date` | `{type: string, format: date-time}` |
| `JSONObject` (Fastjson2) | `{type: object, additionalProperties: true}` |
| `JSONArray` (Fastjson2) | `{type: array, items: {type: object}}` |
| `MultipartFile` | `{type: string, format: binary}` |
| `Object` | `{type: object}` |

**集合类型**：
- `List<T>` / `Set<T>` / `Collection<T>` → `{type: array, items: {T 的 schema}}`
- `Map<K, V>` → `{type: object, additionalProperties: {V 的 schema}}`

**框架内置骨架类型（不需要读取源文件）**：

**`Page<T>` / `IPage<T>`**（MyBatis Plus 分页对象）：
```json
{
  "type": "object",
  "properties": {
    "records": {"type": "array", "items": {"<T 的 schema>"}},
    "total": {"type": "integer", "format": "int64", "description": "总记录数"},
    "size": {"type": "integer", "format": "int64", "description": "每页大小"},
    "current": {"type": "integer", "format": "int64", "description": "当前页码"},
    "pages": {"type": "integer", "format": "int64", "description": "总页数"}
  }
}
```

**项目自定义类型（优先读取源文件，降级时使用通用结构）**：

对于统一返回包装类（如 `R<T>`、`Result<T>`、`Response<T>` 等）和分页请求基类（如 `PageBaseRequest`、`PageQuery` 等）：
1. **优先**：通过 Glob 在项目和工作区中查找源文件，读取实际字段结构
2. **降级**：如果源文件不可达（来自二方库/三方库），根据方法返回类型推断常见结构：
   - 统一返回类通常包含：`code`（状态码）、`msg`/`message`（消息）、`data`（数据）、`success`（是否成功，可能是 getter 计算字段）
   - 分页请求基类通常包含：`pageIndex`/`pageNum`/`current`（页码）、`pageSize`/`size`（每页大小）
3. 如果无法确定结构 → 降级为 `{type: object}`

### 步骤 7：枚举识别

对步骤 6 中提取的 **Integer 类型字段**，尝试识别关联枚举：

#### 7.1 优先级 1：类内直接引用
检查 DTO/VO 类中的 import 语句和方法体：
- import 包含 `enums` 路径 → 提取枚举类名
- 方法体包含 `XxxEnum.getDescByCode(fieldName)` → 字段名与枚举的映射
- 字段注释包含 `{@link XxxEnum}` 或 `@see XxxEnum` → 直接引用

#### 7.2 优先级 2：名称后缀匹配
1. 用 Glob 从项目根目录搜索 `**/enums/*.java`
2. 将字段名 camelCase 转为 PascalCase（如 `routeType` → `RouteType`）
3. 在枚举文件名中匹配：
   - 精确匹配优先：枚举核心名（去掉 `Enum` 后缀）== PascalCase 字段名
   - 后缀匹配：枚举核心名以 PascalCase 字段名结尾
4. 消歧：精确 > 后缀；名称更短的优先

#### 7.3 读取枚举值
匹配到枚举文件后读取，提取常量：
- 格式：`CONSTANT_NAME(code, "desc", ...)` — **只取第一个 Integer 参数作为 code，第一个 String 参数作为 desc**，忽略其余参数
- 示例：`WAYPOINT_ROUTE(1, "航点航线")` → code=1, desc="航点航线"

在 OpenAPI schema 中表示：
```json
{
  "type": "integer",
  "enum": [1, 2, 3],
  "description": "航线类型: 1-航点航线 2-块状航线 3-仿地航线"
}
```

### 步骤 8：生成 OpenAPI 3.0 Spec

将步骤 5-7 的解析结果组装为标准 OpenAPI 3.0 JSON。

#### 8.1 整体结构

```json
{
  "openapi": "3.0.3",
  "info": {
    "title": "{ControllerName} API",
    "version": "1.0.0",
    "description": "从 {Controller文件路径} 自动生成"
  },
  "tags": [
    {"name": "{Tag名称}", "description": "{类JavaDoc或类名}"}
  ],
  "paths": {
    "/path": {
      "post": {
        "tags": ["{Tag名称}"],
        "summary": "{JavaDoc第一行}",
        "operationId": "{方法名}",
        "x-apifox-folder": "{用户选择的目标文件夹路径，若选择根目录则不添加此属性}",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {"$ref": "#/components/schemas/{ClassName}"}
            }
          }
        },
        "responses": {
          "200": {
            "description": "成功",
            "content": {
              "application/json": {
                "schema": {"$ref": "#/components/schemas/R_{ResponseType}"}
              }
            }
          }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "{ClassName}": {
        "type": "object",
        "properties": { ... },
        "required": [...]
      }
    }
  }
}
```

#### 8.2 Schema 命名规则
- 普通 DTO: `{ClassName}`（如 `PersonListREQ`）
- R 包装: `R_{InnerType}`（如 `R_Page_PersonListRESP`）
- Page 包装: `Page_{InnerType}`（如 `Page_PersonListRESP`）
- 使用 `$ref: "#/components/schemas/{Name}"` 引用，避免重复定义
- 同一个类只定义一次 schema，多处通过 `$ref` 引用

#### 8.3 MultipartFile 接口
请求体使用 `multipart/form-data`，而非 `application/json`：
```json
{
  "requestBody": {
    "content": {
      "multipart/form-data": {
        "schema": {
          "type": "object",
          "properties": {
            "file": {"type": "string", "format": "binary"}
          }
        }
      }
    }
  }
}
```

#### 8.4 继承表示
使用 `allOf` 组合父类和子类字段：
```json
{
  "allOf": [
    {"$ref": "#/components/schemas/PageBaseRequest"},
    {
      "type": "object",
      "properties": { "子类字段": { ... } }
    }
  ]
}
```

### 步骤 9：JSON 预验证

**关键步骤**：OpenAPI JSON 格式容错为零，必须在推送前验证。

1. 将生成的 OpenAPI JSON 写入临时文件：
```bash
cat > /tmp/apifox-sync-spec.json << 'SPECEOF'
{生成的 JSON}
SPECEOF
```

2. 用 python3 验证 JSON 语法：
```bash
python3 -c "import json; json.load(open('/tmp/apifox-sync-spec.json')); print('JSON_VALID')"
```

3. 如果验证失败（`json.JSONDecodeError`）：
   - 读取错误信息，定位问题位置
   - 修复 JSON 格式（常见问题：未转义的引号、尾逗号、注释）
   - 重新验证，最多重试 3 次

4. 验证成功后继续推送流程。

### 步骤 10：获取文件夹结构

调用 Apifox export-openapi 获取现有文件夹结构：

```bash
EXPORT_RESULT=$(curl -s -X POST \
  "https://api.apifox.com/v1/projects/${PROJECT_ID}/export-openapi" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Apifox-Api-Version: 2024-03-28" \
  -H "Content-Type: application/json" \
  -d '{"oasVersion":"3.0","exportFormat":"JSON","options":{"includeApifoxExtensionProperties":true,"addFoldersToTags":true}}')
```

将结果写入临时文件并解析 `x-apifox-folder` 属性：
```bash
echo "$EXPORT_RESULT" > /tmp/apifox-sync-export.json
python3 -c "
import json
data = json.load(open('/tmp/apifox-sync-export.json'))
folders = set()
for path_data in data.get('paths', {}).values():
    for method_data in path_data.values():
        if isinstance(method_data, dict):
            folder = method_data.get('x-apifox-folder', '')
            if folder:
                folders.add(folder)
for f in sorted(folders):
    print(f)
" 2>/dev/null
```

用 `AskUserQuestion` 让用户选择目标文件夹：
- 选项包含发现的所有文件夹 + "新建文件夹（输入路径）" + "项目根目录（不指定文件夹）"
- 如果用户选择"新建文件夹"，再用 `AskUserQuestion` 让用户输入文件夹路径（支持多级路径，用 `/` 分隔，如 `设备管理/无人机`）

如果文件夹列表为空（新建项目），直接用 `AskUserQuestion` 询问用户是否需要指定文件夹路径，还是推送到项目根目录。

**说明**：目标文件夹路径将写入 OpenAPI spec 每个 operation 的 `x-apifox-folder` 属性。Apifox 在导入时会自动创建不存在的文件夹层级。

### 步骤 11：推送到 Apifox

Apifox 以 path+method 为匹配键。为支持**同 path+method 在不同文件夹中作为独立接口**，需要按文件夹匹配情况分批推送。

#### 11.1 构建现有接口查找表

复用步骤 10 导出的 `/tmp/apifox-sync-export.json`，提取每个 operation 的 `{method}:{path}` → `x-apifox-folder` 映射：

```bash
python3 -c "
import json
data = json.load(open('/tmp/apifox-sync-export.json'))
existing = {}
for path, methods in data.get('paths', {}).items():
    for method, detail in methods.items():
        if isinstance(detail, dict):
            folder = detail.get('x-apifox-folder', '')
            key = f'{method.upper()}:{path}'
            existing.setdefault(key, []).append(folder)
json.dump(existing, open('/tmp/apifox-sync-existing.json', 'w'))
print(f'已索引 {len(existing)} 个现有接口')
"
```

#### 11.2 分批生成 payload

读取生成的 spec 和查找表，将 operations 分为三类：安全更新、新建、冲突跳过。各自生成独立的 OpenAPI spec（共享 info 和 components/schemas）：

```bash
python3 << 'PYEOF'
import json, copy

spec = json.load(open('/tmp/apifox-sync-spec.json'))
existing = json.load(open('/tmp/apifox-sync-existing.json'))

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
            # 同文件夹已存在，检查是否有其他文件夹的重复
            other_folders = [f for f in existing_folders if f != target_folder]
            if other_folders:
                # 其他文件夹也有同 path+method → AUTO_MERGE 会误更新，跳过
                skipped.append(f'{method.upper()} {path} (目标: {target_folder}, 冲突: {", ".join(other_folders)})')
            else:
                # 仅目标文件夹有 → 安全更新
                update_paths.setdefault(path, {})[method] = detail
        else:
            # 目标文件夹中不存在 → 新建
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
    json.dump(payload, open('/tmp/apifox-sync-payload-update.json', 'w'), ensure_ascii=False)

if create_paths:
    payload = build_payload(build_spec(spec, create_paths), 'CREATE_NEW', True)
    json.dump(payload, open('/tmp/apifox-sync-payload-create.json', 'w'), ensure_ascii=False)

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

#### 11.3 分别推送

对每个非空批次执行推送。用 Bash 检查 payload 文件是否存在，存在则推送：

**更新批次**（`AUTO_MERGE`）：
```bash
if [ -f /tmp/apifox-sync-payload-update.json ]; then
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" \
    -d @/tmp/apifox-sync-payload-update.json)
  echo "=== 更新批次 ==="
  echo "$RESULT"
fi
```

**新建批次**（`CREATE_NEW`）：
```bash
if [ -f /tmp/apifox-sync-payload-create.json ]; then
  RESULT=$(curl -s -w "\n%{http_code}" -X POST \
    "https://api.apifox.com/v1/projects/${PROJECT_ID}/import-openapi" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Apifox-Api-Version: 2024-03-28" \
    -H "Content-Type: application/json" \
    -d @/tmp/apifox-sync-payload-create.json)
  echo "=== 新建批次 ==="
  echo "$RESULT"
fi
```

判断每个批次的结果：
- `200` → 推送成功
- `401` / `403` → Token 失效，建议重新运行 `init`
- `400` → spec 格式错误，显示 Apifox 返回的错误信息
- 其他 → 显示状态码和响应体

### 步骤 12：报告结果

推送成功时报告：
- 推送的接口数量（paths 中的 operation 数）
- 目标文件夹名称（如选择了文件夹）
- Apifox 项目链接（如果能拼接）

清理临时文件：
```bash
rm -f /tmp/apifox-sync-spec.json /tmp/apifox-sync-export.json /tmp/apifox-sync-existing.json /tmp/apifox-sync-payload-update.json /tmp/apifox-sync-payload-create.json
```

---

## 注意事项

1. **只读操作原则**：skill 不修改项目源代码，仅读取 Controller 和 DTO 文件
2. **allowed-tools**: Read, Glob, Grep, Bash, AskUserQuestion — 使用 Read/Glob/Grep 解析源码，Bash 执行 curl 和 python3，AskUserQuestion 交互
3. **跨模块查找**：Glob 从项目根目录搜索，覆盖所有子模块
4. **不可解析类型**：降级为 `{type: object}`，不中断整体流程
5. **敏感信息**：Token 存储在项目级 `.claude/apifox.json`，建议加入 `.gitignore`。也可通过环境变量 `APIFOX_API_TOKEN` 配置
6. **幂等性与文件夹隔离**：同一文件夹内重复推送同一 Controller 不会产生重复接口（`AUTO_MERGE` 更新）；若推送到不同文件夹，则会在新文件夹中创建独立接口（`CREATE_NEW`），不影响其他文件夹中的同名接口。**已知限制**：当同一 path+method 已存在于多个文件夹时，后续更新可能不准确（Apifox import API 以 path+method 全局匹配，无法按文件夹精确定位；且 OpenAPI 导出格式无法表示同 path+method 的多个副本，导致冲突检测不完整）。此场景仅在跨微服务推送到同一项目时出现，如遇到冲突提示，请在 Apifox 中手动删除多余的重复接口后重新推送