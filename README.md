# NeoMAGI 项目理念
NeoMAGI的目标是一个开源的，拥有持久记忆、代表用户信息利益的 personal agent，是一个渐进式的信息自主权方案，——先用商业 API 建立 harness 和 memory 基础设施，等本地模型能力追上来后逐步迁移。

## 核心设计理念
考虑上充分，实现上极简。不要过度设计，不要过度工程化。

## 最小化实现需要的模块
参见 design_docs/modules.md

## 目录结构

```
src/
├── gateway/        # Gateway 服务器、RPC 协议、路由
├── config/         # 配置 schema (zod)、验证、热加载
├── agents/         # Agent Runtime、System Prompt 组装、Sandbox
├── channels/       # 内置 Channel（telegram 等）
├── memory/         # Memory 索引管理、Embedding、Hybrid Search
├── tools/          # Tool Registry、内置工具（exec, read 等）
├── sessions/       # Session 管理、Transcript 存储
├── cli/            # CLI 命令
└── infra/          # 基础设施（env, errors, ports）
```

## 系统提示词设计理念
参见 design_docs/system_prompt.md

## 记忆系统设计
参见 design_docs/memory_architecture.md