# Push 步骤 9：生成 OpenAPI 3.0 Spec

将步骤 5-7 的解析结果 + 步骤 8 用户选择的文件夹路径，组装为标准 OpenAPI 3.0 JSON。

## 9.1 整体结构

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
        "summary": "{接口名称}",
        "operationId": "{方法名}",
        "x-apifox-folder": "{步骤 8 用户选择的目标文件夹路径，若选择根目录则不添加此属性}",
        "parameters": [
          {
            "name": "paramName",
            "in": "query|path|header|cookie",
            "required": true,
            "schema": {"type": "string"}
          }
        ],
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
        "properties": { "..." : "..." },
        "required": ["..."]
      }
    }
  }
}
```

## 9.2 Schema 命名与清洗

参考 `data/framework-schemas.json`：
- 命名规则：`schemaNameRules`（DTO 用类名，R 包装用 `R_{InnerType}`，Page 包装用 `Page_{InnerType}`）
- **清洗规则**：`schemaNameSanitization` — 泛型字符 `<>`, 替换为 `_`（如 `R<Map<String, List<XxxVO>>>` → `R_Map_String_List_XxxVO`）
- 使用 `$ref: "#/components/schemas/{Name}"` 引用，避免重复定义
- 同一个类只定义一次 schema，多处通过 `$ref` 引用

## 9.3 MultipartFile 接口

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

## 9.4 继承表示

使用 `allOf` 组合父类和子类字段：
```json
{
  "allOf": [
    {"$ref": "#/components/schemas/PageBaseRequest"},
    {
      "type": "object",
      "properties": { "子类字段": { "..." : "..." } }
    }
  ]
}
```
