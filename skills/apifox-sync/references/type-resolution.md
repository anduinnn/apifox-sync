# Push 步骤 6：递归展开类型

对步骤 5 中涉及的所有自定义类型（DTO/VO/Entity/REQ/RESP），递归读取和展开。

**维护已访问类型集合**：防止循环引用导致无限递归。如果遇到已访问类型，使用 `$ref` 引用。

## 6.1 定位类文件（两级降级）

**第一级**：从项目根目录（多模块项目的最顶层目录）Glob 搜索：
```
**/{ClassName}.java
```
这会覆盖所有子模块（包括 `*-interface` 模块）。如果 Glob 返回多个结果，优先选择 import 语句中包名匹配的文件。

**第二级**：降级处理 — 将该类型映射为 `{type: object, additionalProperties: true}`，继续处理其他字段。不中断整体流程。

**静态内部类处理**：
如果 Glob 搜索 `**/{ClassName}.java` 返回 0 结果：
1. 在当前已读取的文件中搜索 `static class {ClassName}`
2. 如果未找到，用 Grep 在项目中搜索 `class {ClassName}` 定位其所在外部类文件
3. 如果仍未找到 → 降级为 `{type: object}`

## 6.2 提取字段

读取类文件，提取所有字段：
- 字段名（camelCase）
- 字段类型（含泛型）
- 字段描述（优先级从高到低）：
  1. 字段上方的 JavaDoc 行内描述（`/** xxx */`）
  2. `@see XxxEnum` 或 `{@link XxxEnum}` → 写"参见 XxxEnum"
  3. 字段同行的 `//` 行内注释
  4. 如果以上均无 → 不设置 description
- 必填标记：`@NotNull`、`@NotBlank`、`@NotEmpty` → 标记为 required
- 验证约束映射：参考 `data/type-mappings.json` 的 `validationToOpenAPI` 表，将 Bean Validation 注解转换为 OpenAPI schema 约束字段（如 `@Size(max=50)` → `maxLength: 50`）

## 6.3 处理继承

如果类有 `extends BaseClass`：
- 递归读取父类文件，提取父类字段
- 合并父类字段和子类字段（子类字段覆盖同名父类字段）

## 6.4 类型映射

**基础类型**：读取 `data/type-mappings.json` 的 `primitives` 表直接映射，不 Glob 搜索。

**集合类型**：读取 `data/type-mappings.json` 的 `collections` 表：
- `List<T>` / `Set<T>` / `Collection<T>` → `{type: array, items: {T 的 schema}}`
- `Map<K, V>` → `{type: object, additionalProperties: {V 的 schema}}`

**框架内置骨架类型**：读取 `data/framework-schemas.json`：

- **`Page<T>` / `IPage<T>`**（MyBatis Plus 分页对象）：使用 `Page` 模板，按 `genericPlaceholderRules` 替换 `<T>`：
  - T 是自定义类型 → 替换为 `{"$ref": "#/components/schemas/{TypeName}"}`
  - T 是基础类型 → 替换为对应 primitives 的 inline schema
  - T 是集合类型 → 替换为对应的 array/object schema
- **统一返回包装类**（`R<T>`、`Result<T>`、`Response<T>` 等）和**分页请求基类**（`PageBaseRequest`、`PageQuery` 等）：
  1. **优先**：通过 Glob 在项目和工作区中查找源文件，读取实际字段结构
  2. **降级**：如果源文件不可达（来自二方库/三方库），使用 `data/framework-schemas.json` 中的 `R` 或 `PageRequest` 模板
  3. 如果无法确定结构 → 降级为 `{type: object}`

## 6.5 Schema 名称清洗

生成 schema 名称时，按 `data/framework-schemas.json` 的 `schemaNameSanitization` 规则清洗：
- 将 `<`、`>`、`,` 和空格替换为 `_`
- 多个连续 `_` 合并为一个
- 示例：`R<Map<String, List<XxxVO>>>` → `R_Map_String_List_XxxVO`
