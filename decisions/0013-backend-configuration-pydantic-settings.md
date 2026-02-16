# 0013-backend-configuration-pydantic-settings

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 后端配置管理采用 `pydantic-settings`（Pydantic v2 体系）。
- 配置来源基线为：环境变量与本地 `.env`，模板文件使用 `.env_template`。

## 为什么
- 与现有 FastAPI + Pydantic 技术栈一致，降低认知和维护成本。
- 类型化配置和启动期校验可提前暴露配置错误，减少运行时故障。
- 支持按领域组织配置结构，便于后续规模化扩展。

## 放弃了什么
- 方案 A：手写 `os.getenv` + 自定义解析逻辑。
  - 放弃原因：重复样板代码多，校验与可维护性较弱。
- 方案 B：分散在多个配置库（dotenv + 自定义 dataclass）。
  - 放弃原因：配置来源与校验链路分裂，增加复杂度。

## 影响
- 配置模型默认以 `pydantic-settings` 定义并在启动时统一校验（fail fast）。
- 敏感信息仅保存在本地 `.env`，`.env_template` 仅保留键名与说明。
- 新增配置项时需同步更新配置模型与 `.env_template`。
