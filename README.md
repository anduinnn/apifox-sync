# apifox-sync

Claude Code 插件：将 Spring Boot Controller 接口同步到 Apifox 项目。

## 功能

- 解析 Spring Boot Controller 源码，自动生成 OpenAPI 3.0 spec
- 支持递归展开 DTO/VO/Entity 类型
- 自动识别枚举字段
- 推送到 Apifox 指定项目和文件夹

## 安装

```bash
claude plugin add --from github:anduinnn/apifox-sync
```

## 使用

```bash
# 配置 Apifox API Token 和项目 ID
/apifox-sync init

# 推送整个 Controller
/apifox-sync push @path/to/Controller.java

# 推送单个接口（指定行号）
/apifox-sync push @path/to/Controller.java#L35
```