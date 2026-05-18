# Push 步骤 6：递归展开类型

对步骤 5 中涉及的所有自定义类型（DTO/VO/Entity/REQ/RESP），递归读取和展开。

**维护已访问类型集合**：防止循环引用。已访问类型直接用 `$ref` 引用。

## 6.1 定位类文件（两级降级）

**第一级**：从项目根目录 Glob 搜索 `**/{ClassName}.java`（覆盖所有子模块）。多结果时优先 import 语句包名匹配的文件。

**静态内部类**：Glob 返回 0 结果时，先在已读文件中搜索 `static class {ClassName}`，再用 Grep 搜 `class {ClassName}` 定位外部类文件。

**第二级降级**：仍未找到 → 映射为 `{type:object, additionalProperties:true}`，继续处理，不中断流程。

## 6.2 提取字段

读取类文件，提取所有字段：
- 字段名（camelCase）、字段类型（含泛型）
- 描述优先级：JavaDoc 行内 → `@see`/`{@link}` XxxEnum → 同行 `//` 注释 → 不设置
- 必填：`@NotNull`/`@NotBlank`/`@NotEmpty` → `required`
- 验证约束：参考 `data/type-mappings.json` 的 `validationToOpenAPI` 转换（如 `@Size(max=50)` → `maxLength:50`）

## 6.3 处理继承

`extends BaseClass` → 递归读取父类，合并字段（子类覆盖同名父类字段）。

## 6.4 类型映射

- **基础类型**：读 `data/type-mappings.json` 的 `primitives` 直接映射，不 Glob
- **集合类型**：读 `collections`：`List<T>/Set<T>/Collection<T>` → `array of T`；`Map<K,V>` → `{type:object, additionalProperties: V schema}`
- **框架骨架类型**（`Page<T>`/`IPage<T>`/`R<T>`/`PageBaseRequest` 等）：优先 Glob 找源文件读取实际字段；不可达时用 `data/framework-schemas.json` 模板；无法确定则降级为 `{type:object}`

## 6.5 Schema 名称清洗

按 `data/framework-schemas.json` 的 `schemaNameSanitization`：`<`、`>`、`,`、空格 → `_`，连续 `_` 合并。示例：`R<Map<String, List<XxxVO>>>` → `R_Map_String_List_XxxVO`。
