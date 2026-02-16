# 0012-backend-framework-fastapi-uvicorn

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 后端框架采用 `FastAPI`。
- ASGI 服务器采用 `Uvicorn`。

## 为什么
- 与 Python-first 路线和现有团队能力高度匹配。
- 对 async I/O、HTTP API 和 WebSocket 场景支持成熟，符合当前网关需求。
- 与 Pydantic、测试和文档生态衔接自然，利于快速迭代与维护。

## 放弃了什么
- 方案 A：Flask + 扩展插件补齐异步与接口能力。
  - 放弃原因：异步与实时通信场景下工程负担更高。
- 方案 B：Django / Django Channels 作为主框架。
  - 放弃原因：当前阶段框架体量偏重，超出最小可用范围。

## 影响
- 后续后端接口和网关能力默认在 FastAPI + Uvicorn 组合上实现与验证。
- 运行与部署文档应以该组合为基线维护。
