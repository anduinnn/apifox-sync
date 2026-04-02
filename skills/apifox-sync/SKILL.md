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

- `init` → 读取 `references/init.md` 并执行
- `push` → 按下方 [Push 流程](#push-流程) 执行
- 无参数或未知子命令 → 提示用法：
  > 用法：
  > - `/apifox-sync init` — 配置 Apifox API Token 和项目 ID
  > - `/apifox-sync push @Controller.java` — 推送整个 Controller
  > - `/apifox-sync push @Controller.java#L35` — 推送单个接口

---

## Push 流程

按以下步骤顺序执行，每步的详细指令在对应的参考文件中。

### 阶段一：解析与提取（步骤 1-5）

读取 `references/push-parse.md` 执行：

1. **解析参数** — 从 `{{ARGUMENTS}}` 提取文件路径和可选行号
2. **加载配置** — 读取 Token 和 ProjectId（环境变量 > `.claude/apifox.json`）
3. **读取 Controller** — 验证文件并提取类级路径前缀和 Tag
4. **定位方法** — 按模式（整个 Controller / 单个方法）找到目标方法
5. **提取方法信息** — 接口名、HTTP 方法/路径、请求参数、响应类型

### 阶段二：类型展开（步骤 6-7）

6. **递归展开类型** — 读取 `references/type-resolution.md` 执行
   - 类型映射使用 `data/type-mappings.json`
   - 框架骨架使用 `data/framework-schemas.json`
   - 两级降级定位：Glob → 降级为 `{type: object}`
7. **枚举识别** — 读取 `references/enum-detection.md` 执行
   - Integer 字段尝试匹配枚举，提取 code + desc

### 阶段三：文件夹选择与生成（步骤 8-9）

读取 `references/push-api.md` 的「步骤 8」执行：

8. **获取文件夹结构并选择** — 调用 export-openapi 获取现有文件夹，让用户选择目标文件夹

然后读取 `references/openapi-gen.md` 执行：

9. **生成 OpenAPI Spec** — 将解析结果 + 用户选择的文件夹路径组装为 OpenAPI 3.0 JSON
   - Schema 命名规则和清洗规则参考 `data/framework-schemas.json`

### 阶段四：验证与推送（步骤 10-12）

继续读取 `references/push-api.md` 执行步骤 10-12：

10. **JSON 预验证** — 写入临时文件并用 python3 验证语法，最多重试 3 次
11. **分批推送** — 按冲突分类（安全更新 / 新建 / 跳过），分别调用 import-openapi
12. **报告结果** — 显示推送结果并清理临时文件

---

## 数据文件

| 文件 | 用途 |
|------|------|
| `data/type-mappings.json` | Java → OpenAPI 基础类型映射、集合类型规则、框架注入参数忽略列表、验证注解映射 |
| `data/framework-schemas.json` | Page/R/PageRequest 骨架模板、Schema 命名与清洗规则、泛型占位符替换规则 |
| `data/api-config.json` | Apifox API 常量（base URL、版本头、端点路径、临时文件前缀） |

---

## 注意事项

1. **只读操作原则**：skill 不修改项目源代码，仅读取 Controller 和 DTO 文件
2. **allowed-tools**: Read, Glob, Grep, Bash, AskUserQuestion — 使用 Read/Glob/Grep 解析源码，Bash 执行 curl 和 python3，AskUserQuestion 交互
3. **跨模块查找**：Glob 从项目根目录搜索，覆盖所有子模块
4. **不可解析类型**：降级为 `{type: object}`，不中断整体流程
5. **敏感信息**：Token 存储在项目级 `.claude/apifox.json`，建议加入 `.gitignore`。也可通过环境变量 `APIFOX_API_TOKEN` 配置。**禁止将 Token 明文输出到终端**
6. **项目根目录定位**：所有需要 PROJECT_ROOT 的步骤统一使用 `PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")`
7. **幂等性与文件夹隔离**：同一文件夹内重复推送同一 Controller 不会产生重复接口（`AUTO_MERGE` 更新）；若推送到不同文件夹，则会在新文件夹中创建独立接口（`CREATE_NEW`），不影响其他文件夹中的同名接口。**已知限制**：当同一 path+method 已存在于多个文件夹时，后续更新可能不准确
