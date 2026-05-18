# Push 步骤 7：枚举识别

对步骤 6 中提取的 **Integer 类型字段**，尝试识别关联枚举。

## 7.1 优先级 1：类内直接引用

- import 含 `enums` 路径 → 提取枚举类名
- 方法体含 `XxxEnum.getDescByCode(fieldName)` → 字段名与枚举的映射
- 字段注释含 `{@link XxxEnum}` 或 `@see XxxEnum` → 直接引用

## 7.2 优先级 2：名称后缀匹配

1. Glob 从项目根目录搜索 `**/enums/*.java`
2. 字段名 camelCase → PascalCase（如 `routeType` → `RouteType`）
3. 匹配枚举文件名：精确匹配（枚举核心名 == PascalCase 字段名）> 后缀匹配
4. 消歧：精确 > 后缀；多个后缀匹配时核心名更短者优先；仍无法消歧 → 跳过

## 7.3 读取枚举值

提取常量格式 `CONSTANT_NAME(code, "desc", ...)`，只取第一个 Integer 参数为 code，第一个 String 参数为 desc。

在 OpenAPI schema 中表示：
```json
{
  "type": "integer",
  "enum": [1, 2, 3],
  "description": "航线类型: 1-航点航线 2-块状航线 3-仿地航线"
}
```
