---
name: apifox-sync
description: Apifox 接口同步工具：推送 Spring Boot Controller 到 Apifox，或从 Apifox 拉取接口定义
argument-hint: "<init|push|pull> [args]"
level: 2
---

# Apifox Sync

Apifox 接口同步工具，支持双向操作：
- **push**：将 Spring Boot Controller 接口定义从源码解析后推送到 Apifox
- **pull**：从 Apifox 拉取指定目录的接口定义到本地

## 子命令路由

解析 `{{ARGUMENTS}}` 的第一个词：

- `init` → 读取 `references/init.md` 并执行
- `push` → 按下方 [Push 流程](#push-流程) 执行
- `pull` → 按下方 [Pull 流程](#pull-流程) 执行
- 无参数或未知子命令 → 提示用法：
  > 用法：
  > - `/apifox-sync init` — 配置 Apifox API Token 和项目 ID
  > - `/apifox-sync push @Controller.java` — 推送整个 Controller
  > - `/apifox-sync push @Controller.java#L35` — 推送单个接口
  > - `/apifox-sync pull` — 从 Apifox 拉取指定目录的接口定义

---

## Push 流程

读取对应参考文件后按步骤顺序执行：

| 步骤 | 参考文件 | 内容 |
|------|---------|------|
| 1-5 | `references/push-parse.md` | 解析参数、加载配置、读取 Controller、定位方法、提取接口信息 |
| 6 | `references/type-resolution.md` | 递归展开 DTO/VO 类型，两级降级定位 |
| 7 | `references/enum-detection.md` | Integer 字段匹配枚举，提取 code+desc |
| 8 | `references/push-api.md` 步骤 8 | export-openapi 获取文件夹，AskUserQuestion 选目标 |
| 9 | `references/openapi-gen.md` | 组装 OpenAPI 3.0 JSON，写入 spec |
| 10-12 | `references/push-api.md` 步骤 10-12 | JSON 预验证、分类推送（锚点匹配/死接口清理/import）、报告 |

---

## Pull 流程

读取 `references/pull.md` 按步骤顺序执行：

| 步骤 | 内容 |
|------|------|
| 1 | 加载配置（env > `.claude/apifox.json`） |
| 2 | export-openapi 获取全量，`list_folders.py` 枚举目录 |
| 3 | AskUserQuestion 多选目录 |
| 4 | `pull_extract.py` 按接口粒度切片 + 精简扩展字段 |
| 5 | 精简规则（内聚到脚本，仅保留 paths+schemas） |
| 5.5 | `pull_diff.py` diff 预览，AskUserQuestion 确认覆盖 |
| 6 | `pull_save.py` 落盘，自动迁移 v1.2 旧聚合文件 |
| 7 | 输出摘要，清理临时文件 |

---

## 数据文件

| 文件 | 用途 |
|------|------|
| `data/type-mappings.json` | Java → OpenAPI 基础类型映射、集合类型规则、框架注入参数忽略列表、验证注解映射 |
| `data/framework-schemas.json` | Page/R/PageRequest 骨架模板、Schema 命名与清洗规则、泛型占位符替换规则 |
| `data/api-config.json` | Apifox API 常量（base URL、版本头、端点路径、临时文件前缀） |

---

## 注意事项

1. **只读原则**：不修改项目源代码，仅读取 Controller 和 DTO 文件
2. **allowed-tools**: Read, Glob, Grep, Bash, AskUserQuestion
3. **跨模块查找**：Glob 从项目根目录搜索，覆盖所有子模块
4. **不可解析类型**：降级为 `{type: object}`，不中断流程
5. **敏感信息**：Token 存于 `.claude/apifox.json`，建议加 `.gitignore`；也可用 `APIFOX_API_TOKEN` 环境变量。**禁止明文输出 Token**
6. **PROJECT_ROOT 定位**：统一使用 `PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")`
7. **幂等性**：同文件夹重复推送同一 Controller 使用 `AUTO_MERGE`；推到不同文件夹则 `CREATE_NEW`。**已知限制**：同 path+method 跨文件夹已存在时后续更新可能不准确
