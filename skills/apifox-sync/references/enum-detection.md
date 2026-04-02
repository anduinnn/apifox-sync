# Push 步骤 7：枚举识别

对步骤 6 中提取的 **Integer 类型字段**，尝试识别关联枚举。

## 7.1 优先级 1：类内直接引用

检查 DTO/VO 类中的 import 语句和方法体：
- import 包含 `enums` 路径 → 提取枚举类名
- 方法体包含 `XxxEnum.getDescByCode(fieldName)` → 字段名与枚举的映射
- 字段注释包含 `{@link XxxEnum}` 或 `@see XxxEnum` → 直接引用

## 7.2 优先级 2：名称后缀匹配

1. 用 Glob 从项目根目录搜索 `**/enums/*.java`
2. 将字段名 camelCase 转为 PascalCase（如 `routeType` → `RouteType`）
3. 在枚举文件名中匹配：
   - 精确匹配优先：枚举核心名（去掉 `Enum` 后缀）== PascalCase 字段名
   - 后缀匹配：枚举核心名以 PascalCase 字段名结尾
4. 消歧规则（按优先级）：
   - 精确匹配 > 后缀匹配
   - 多个后缀匹配时，去掉 `Enum` 后缀后的核心名长度更短者优先（如 `RouteTypeEnum` 优于 `DeviceRouteTypeEnum`）
   - 如果仍无法消歧 → 跳过该字段的枚举识别

## 7.3 读取枚举值

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
