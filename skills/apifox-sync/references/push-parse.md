# Push 步骤 1-5：解析、配置、读取、定位、提取

## 步骤 1：解析参数

从 `{{ARGUMENTS}}` 去掉 `push` 后解析：
- `@path/to/Controller.java` → 整个 Controller（去掉 `@` 前缀）
- `@path/to/Controller.java#L35` → 单个方法（`@` 到 `#L` 之间为路径，`#L` 后为行号）

相对路径基于当前工作目录解析为绝对路径。

## 步骤 2：加载配置

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
mkdir -p "${PROJECT_ROOT}/.claude/.tmp"
export TMPPREFIX="${PROJECT_ROOT}/.claude/.tmp/apifox-sync-"
eval "$(python3 skills/apifox-sync/scripts/load_config.py "$PROJECT_ROOT")"
```
eval 后 `TOKEN`、`PID`、`HAS_TOKEN` 等变量直接可用；`PROJECT_ID="${APIFOX_PROJECT_ID:-$PID}"`。`HAS_TOKEN=no` 或 `PID` 为空时，自动读 `references/init.md` 步骤 2-4 重配后继续。

**Debug 模式传递**：eval 后 `APIFOX_DEBUG` 变量即可用。当 `APIFOX_DEBUG=1` 时，设置 trap 输出执行摘要：
```bash
if [ "$APIFOX_DEBUG" = "1" ]; then
  export APIFOX_DEBUG APIFOX_DEBUG_LOG APIFOX_SESSION_ID
  trap 'python3 skills/apifox-sync/scripts/debug_log.py --summary "$APIFOX_DEBUG_LOG"' EXIT
fi
```

## 步骤 3：读取 Controller 文件

用 Read 工具读取 Controller 文件，验证含 `@RestController`（或 `@Controller` + `@ResponseBody`），提取：
- **包名**：`package xxx.yyy.zzz;` 首行
- **简单类名**：`public class XxxController`
- **全限定类名**：`包名.简单类名`（用于锚点 `x-source-controller`）
- **路径前缀**：类上 `@RequestMapping("/xxx")` 的值（无则为空）
- **Tag 名称**：类名去掉 `Controller` 后缀

## 步骤 4：定位方法

**整个 Controller 模式**：找所有带 `@GetMapping`/`@PostMapping`/`@DeleteMapping`/`@PutMapping`/`@PatchMapping`/`@RequestMapping(method=...)` 的 public 方法。

**单个方法模式**：从指定行号向上最多 20 行找最近方法声明（含 JavaDoc 和映射注解）。

## 步骤 5：提取方法信息

### 5.0 方法锚点

| 字段 | 格式 | 用途 |
|------|------|------|
| `sourceMethodFq` | `{fqcn}#{方法名}`，重载追加参数类型简名 | push 分类主匹配键（写入 `x-source-method-fq`） |
| `operationId` | `{简单类名}_{方法名}`，重载追加数字后缀 | OpenAPI 标准字段 |
| `x-source-controller` | 步骤 3 全限定类名 | 整 Controller 推送时的孤儿检测范围 |

`x-source-method-fq` 必须保证同一 Java 方法每次 push 生成完全相同的字符串。

### 5.1 接口名称

从方法上方 JavaDoc (`/** ... */`) 取第一个有实际文字的行（跳过空行和 `@` 标签行）作为接口名称；无 JavaDoc 则用方法名。

### 5.2 HTTP 方法和路径

`@PostMapping("/xxx")` → `POST /xxx`，以此类推。完整路径 = 类级前缀 + 方法级路径。`@RequestMapping(value="/xxx", method=RequestMethod.GET)` → `GET /xxx`。

### 5.3 请求参数

| 注解/类型 | OpenAPI 映射 | 备注 |
|-----------|-------------|------|
| `@RequestBody ClassName` | requestBody, `$ref: ClassName` | `List<T>` → array of T |
| `@PathVariable` | path parameter | `@PathVariable("alias")` 用括号内名称 |
| `@RequestParam` | query parameter | `required=false`/`defaultValue` → `required:false` + `default`；`List<T>` → array + `style:form,explode:true` |
| `@RequestHeader` | header parameter | `required` 处理同 `@RequestParam` |
| `@CookieValue` | cookie parameter | — |
| `@ModelAttribute ClassName` | 每字段展开为独立 query parameter | — |
| `MultipartFile`（`@RequestParam`/`@RequestPart`） | `multipart/form-data`, `{type:string,format:binary}` | — |
| 无注解参数 | query parameter（Spring 默认）| 在 `frameworkIgnored` 列表中则跳过 |

框架注入参数（`HttpServletRequest` 等）参考 `data/type-mappings.json` 的 `frameworkIgnored` 列表，一律跳过。

### 5.4 响应类型

| 返回类型 | schema 映射 |
|----------|------------|
| `R<XxxVO>` | R 骨架，data `$ref: XxxVO` |
| `R<Page<XxxVO>>` | R 骨架 + Page 骨架，records `$ref: XxxVO` |
| `R<List<XxxVO>>` | R 骨架，data `array of XxxVO` |
| `R<Void>` | R 骨架，data `nullable:true` |
| `R<Long/String/Boolean>` | R 骨架，data 对应基础类型 |
| `ResponseEntity<T>` | 等同直接返回 T |
| `void` | 不生成响应 schema |
| `SseEmitter` / 其他 | `{type:object}` |

骨架模板和泛型占位符替换规则参考 `data/framework-schemas.json`。
