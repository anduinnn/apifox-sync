# Push 步骤 1-5：解析、配置、读取、定位、提取

## 步骤 1：解析参数

从 `{{ARGUMENTS}}` 中去掉 `push` 后解析剩余参数：

- `@path/to/Controller.java` → 整个 Controller（所有方法）
  - 提取文件路径：去掉 `@` 前缀
- `@path/to/Controller.java#L35` → 单个方法
  - 提取文件路径：`@` 到 `#L` 之间
  - 提取行号：`#L` 后的数字

如果是相对路径，基于当前工作目录解析为绝对路径。

## 步骤 2：加载配置

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

如果 Token 或 ProjectId 为空，提示用户先运行 `/apifox-sync init`。

## 步骤 3：读取 Controller 文件

用 Read 工具读取指定的 Controller 文件。验证：
- 文件存在
- 包含 `@RestController` 注解，或同时包含 `@Controller` 和 `@ResponseBody` 注解

提取类级信息：
- **路径前缀**：类上的 `@RequestMapping("/xxx")` 的值。如果没有类级 `@RequestMapping`，前缀为空字符串。
- **Tag 名称**：类名（去掉 `Controller` 后缀），如 `PersonController` → `Person`

## 步骤 4：定位方法

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

## 步骤 5：提取方法信息

对每个定位到的方法，提取：

### 5.1 接口名称

从方法上方的 JavaDoc 注释 (`/** ... */`) 中提取描述：
- 跳过空行和以 `@` 开头的标签行（如 `@param`、`@return`、`@deprecated`）
- 取第一个有实际文字内容的行作为接口名称
- 如果无 JavaDoc 或 JavaDoc 中无有效文字行，使用方法名

### 5.2 HTTP 方法和路径
- `@PostMapping("/xxx")` → POST, `/xxx`
- `@GetMapping("/xxx")` → GET, `/xxx`
- `@DeleteMapping("/xxx/{id}")` → DELETE, `/xxx/{id}`
- `@PutMapping("/xxx")` → PUT, `/xxx`
- `@PatchMapping("/xxx")` → PATCH, `/xxx`
- `@RequestMapping(value="/xxx", method=RequestMethod.GET)` → GET, `/xxx`
- 完整路径 = 类级前缀 + 方法级路径

### 5.3 请求参数

逐个分析方法的参数：

**框架注入参数（跳过，不纳入 spec）**：
参考 `data/type-mappings.json` 的 `frameworkIgnored` 列表。

**`@RequestBody ClassName param`**：
- 如果 ClassName 是 `List<T>`（如 `List<DeviceGroupSortReq>`）→ 请求体 schema 为 `{type: array, items: {$ref: T}}`
- 如果 ClassName 是 `List<String>` / `List<Long>` 等基础类型集合 → `{type: array, items: {type: string/integer}}`
- 否则 → 读取 ClassName 源文件，提取所有字段作为 request body schema（见 `references/type-resolution.md`）

**`@PathVariable Long id`**：
- 作为 path parameter
- 提取参数名和类型
- 如果有 `@PathVariable("alias")`，使用括号内的名称

**`@RequestParam String name`**：
- 作为 query parameter
- 提取参数名和类型
- 如果有 `@RequestParam("alias")`，使用括号内的名称
- `required` 属性处理：
  - `@RequestParam(required = false)` → 参数 `required: false`
  - `@RequestParam(defaultValue = "xxx")` → 参数 `required: false`，schema 中加 `default: "xxx"`
  - 无 required/defaultValue 属性 → 默认 `required: true`
- 如果参数类型是 `List<String>` / `List<Long>` 等集合 → schema 为 `{type: array, items: {type: string/integer}}`，并设置 `style: form, explode: true`

**`@RequestHeader("X-Custom") String header`**：
- 作为 header parameter
- 提取参数名（优先使用注解 value）和类型
- `required` 属性处理同 `@RequestParam`

**`@CookieValue("session") String cookie`**：
- 作为 cookie parameter
- 提取参数名和类型

**`@ModelAttribute ClassName param`**：
- 读取 ClassName 源文件，提取所有字段
- 每个字段作为独立的 query parameter（而非 request body）

**`@RequestParam("file") MultipartFile` / `@RequestPart("file") MultipartFile`**：
- 请求体为 `multipart/form-data`
- Schema: `{type: string, format: binary}`

**无注解的参数**：
- 如果参数类型在 `frameworkIgnored` 列表中 → 跳过
- 否则 → 作为 query parameter（Spring 默认行为）

### 5.4 响应类型

提取方法返回类型：
- `R<XxxVO>` → 使用 R 骨架模板，data 字段引用 XxxVO schema
- `R<Page<XxxVO>>` → 使用 R 骨架 + Page 骨架，records 引用 XxxVO
- `R<List<XxxVO>>` → 使用 R 骨架，data 字段为 `{type: array, items: {$ref: XxxVO}}`
- `R<Void>` → 使用 R 骨架，data 字段设置 `nullable: true`，不生成 data schema
- `R<Long>` / `R<String>` / `R<Boolean>` → 使用 R 骨架，data 字段为对应基础类型
- `ResponseEntity<T>` → 提取泛型参数 T，按 T 的类型处理（等同于直接返回 T）
- `void` → 不生成响应 schema
- `SseEmitter` / 其他非 R 类型 → `{type: object}`

骨架模板和泛型占位符替换规则参考 `data/framework-schemas.json`。
