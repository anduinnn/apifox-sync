# Push 步骤 9：生成 OpenAPI 3.0 Spec

将步骤 5-7 的解析结果 + 步骤 8 用户选择的文件夹路径，组装为标准 OpenAPI 3.0 JSON。

## 9.1 整体结构

顶层字段：`openapi: "3.0.3"`、`info`（title/version/description）、`tags`（Tag 名称 + 描述）、`paths`、`components.schemas`。

每个 operation 必填字段：
- `tags`、`summary`（接口名称）、`operationId`（步骤 5.0）
- `x-source-controller`（步骤 3 全限定类名）
- `x-source-method-fq`（步骤 5.0 的 sourceMethodFq）
- `x-apifox-folder`（步骤 8 选择的路径；根目录时省略此字段）
- `parameters`（path/query/header/cookie 参数列表）
- `requestBody`（有请求体时，`application/json` 或 `multipart/form-data`）
- `responses.200`（`application/json`，schema `$ref` 到 components）

## 9.2 稳定锚点字段（死接口追踪依据）

| 字段 | 来源 | 用途 |
|------|------|------|
| `operationId` | 步骤 5.0 | OpenAPI 标准字段，展示友好 ID |
| `x-source-controller` | 步骤 3 全限定类名 | 整 Controller 推送时孤儿检测范围 |
| `x-source-method-fq` | 步骤 5.0 sourceMethodFq | push 分类**主匹配键**，格式 `{fqcn}#{method}(参数类型简名,...)` |

`x-source-method-fq` 同一 Java 方法每次 push 必须生成完全相同字符串；重载方法参数类型只保留类名（去掉 import 前缀），按源码顺序，泛型按字面保留。

## 9.3 Schema 命名与清洗

参考 `data/framework-schemas.json` 的 `schemaNameRules` 和 `schemaNameSanitization`：
- DTO 用类名；R 包装用 `R_{InnerType}`；Page 包装用 `Page_{InnerType}`
- 泛型字符 `<>`、`,`、空格 替换为 `_`，多个连续 `_` 合并（如 `R<Map<String,List<XxxVO>>>` → `R_Map_String_List_XxxVO`）
- 同一个类只定义一次 schema，多处通过 `$ref: "#/components/schemas/{Name}"` 引用

## 9.4 MultipartFile 接口

请求体使用 `multipart/form-data`（非 `application/json`），schema 为 `{type:object, properties:{file:{type:string,format:binary}}}`。

## 9.5 继承表示

用 `allOf` 组合父类和子类字段：`[{"$ref":"#/components/schemas/ParentClass"}, {"type":"object","properties":{子类字段}}]`。
