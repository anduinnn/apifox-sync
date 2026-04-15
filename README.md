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
- 输出精简 OpenAPI JSON（仅保留 paths + schemas，去掉冗余元数据）
- 本地目录结构与 Apifox 目录一致（如 `设备管理/无人机` → `.claude/apis/设备管理/无人机.json`）
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

pull 时会先读本地 `.claude/apis/*.json`，与拉取回来的最新数据做 diff：

```
[DIFF ] 用户管理  → .claude/apis/用户管理.json
    + POST /api/users/batch
    ~ PUT /api/users/{id}
    - GET /api/users/list  （远程已删除，本地将被清理）
```

提供「全部覆盖 / 逐目录选择 / 取消」三种处理方式，不会再静默覆盖本地文件。

## 注意事项

**同 path+method 跨文件夹的限制**：Apifox 允许同一个 path+method（如 `POST /api/users`）存在于不同文件夹中，但 OpenAPI 规范以 path+method 为唯一键，导出时只会保留其中一个。这会影响：

- **push**：当同一 path+method 已存在于多个文件夹时，更新可能无法精确命中目标文件夹的接口
- **pull**：跨文件夹的重复接口只会被拉取到其中一个文件夹的文件中，其他文件夹会丢失该接口

建议避免在不同文件夹中创建相同 path+method 的接口。