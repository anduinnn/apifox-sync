# apifox-sync

Claude Code 插件：Apifox 接口同步工具，支持双向操作。

## 功能

### Push — 推送接口到 Apifox

- 解析 Spring Boot Controller 源码，自动生成 OpenAPI 3.0 spec
- 支持递归展开 DTO/VO/Entity 类型
- 自动识别枚举字段
- 推送到 Apifox 指定项目和文件夹

### Pull — 从 Apifox 拉取接口定义

- 从 Apifox 项目中按目录交互式拉取接口定义
- 输出精简 OpenAPI JSON（仅保留单接口 paths + 递归引用的 schemas，去掉冗余元数据）
- **以接口为维度落盘**（v1.3.0+），每个接口一个独立 JSON 文件，避免单文件过大读不完
- **按 Apifox 文件夹层级组织**（v1.4.0+），文件名用接口的中文名（`operation.summary`）
  - 文件路径：`.claude/apis/<Apifox folder 原样层级>/<接口名>.json`
  - 示例：folder=`用户服务/v1/用户管理`，summary=`创建用户`（POST `/api/users`）
    → `.claude/apis/用户服务/v1/用户管理/创建用户.json`
  - 示例：同一 folder 内两个接口 summary 相同时，冲突双方文件名都追加 `.<METHOD>` 后缀
    （如 `用户.POST.json` / `用户.GET.json`）
  - 示例：summary 为空时，回退到 path 最后一段（如 `GET /api/users` → `users.json`）
- 老版本产出（v1.2 `<folder>.json` 聚合 / v1.3 URL 路径展开目录）在下一次 pull 选中对应 folder 时会**自动迁移**为新结构（按文件内部 method+path 匹配，保证不误删用户修改）
- 拉取后由用户自行决定后续操作（AI 对接、代码生成等）

## 安装

### Claude Code

在 Claude Code 对话中依次输入：

```
/plugin marketplace add https://github.com/anduinnn/apifox-sync
/plugin install apifox-sync@apifix-sync
```

### OpenCode

项目级安装（仅当前项目生效）：

```bash
git clone https://github.com/anduinnn/apifox-sync.git .opencode/skills/apifox-sync
```

全局安装（所有项目生效）：

```bash
git clone https://github.com/anduinnn/apifox-sync.git ~/.config/opencode/skills/apifox-sync
```

OpenCode 启动时会自动加载插件目录下的插件。

## 使用

```bash
# 配置 Apifox API Token 和项目 ID
/apifox-sync init

# 推送整个 Controller
/apifox-sync push @path/to/Controller.java

# 推送单个接口（指定行号）
/apifox-sync push @path/to/Controller.java#L35

# 从 Apifox 拉取指定目录的接口定义
/apifox-sync pull
```

## 关键能力

### 重命名追踪（死接口治理，v1.2.0+）

每个 push 的接口在 Apifox 里会带上稳定锚点 `x-source-method-fq`（`{全限定类名}#{方法名}`）。再次 push 时：

- 源码锚点命中且 path+method 没变 → 走 `AUTO_MERGE` 更新
- 源码锚点命中但 path 或 method 变了 → 识别为**重命名**，列出旧/新对比，让用户选择「全部删除 / 逐项选择 / 全部保留」
- 用户确认要删的旧接口，通过 Apifox 开放 API 的 `DELETE /http-apis/{id}` 端点清理，避免死接口残留
- 源码锚点未命中 → 走 `CREATE_NEW` 新建

没有锚点的历史数据自动降级到原有 path+method 匹配规则，升级不破坏老项目。

### Pull diff 预览（v1.2.0+）

pull 时会先扫描本地 `.claude/apis/`（新接口级布局优先，未命中回落到 v1.2.x 的 folder 聚合文件），与拉取回来的最新数据按 folder 聚合做 diff：

```
[DIFF ] 用户管理  → .claude/apis/用户管理/（将迁移旧文件并拆分为单接口文件）
    + POST /api/users/batch
    ~ PUT /api/users/{id}
    - GET /api/users/list  （远程已删除，本地将被清理）
```

提供「全部覆盖 / 逐目录选择 / 取消」三种处理方式，不会再静默覆盖本地文件。选中对应 folder 时会自动迁移老版本的 `<folder>.json` 聚合文件到新的接口级布局。

## 注意事项

**同 path+method 跨文件夹的限制**：Apifox 允许同一个 path+method（如 `POST /api/users`）存在于不同文件夹中，但 OpenAPI 规范以 path+method 为唯一键，导出时只会保留其中一个。这会影响：

- **push**：当同一 path+method 已存在于多个文件夹时，更新可能无法精确命中目标文件夹的接口
- **pull**：跨文件夹的重复接口只会被拉取到其中一个文件夹的文件中，其他文件夹会丢失该接口

建议避免在不同文件夹中创建相同 path+method 的接口。