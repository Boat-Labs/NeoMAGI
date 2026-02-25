# Design Docs Index

> 目的：为 agent 提供“渐进式披露”的稳定入口，先读必要文档，再按任务补充读取。  
> 维护原则：新增/重命名 design 文档后，同步更新本索引。

## 1. 默认读取顺序（最小上下文）

1. `design_docs/roadmap_milestones_v3.md`  
   - 产品目标、阶段边界、验收标准、优先级（当前生效版本）。

2. `design_docs/modules.md`  
   - 系统模块现状（已实现/计划中）与模块边界。

3. 当前里程碑对应 architecture 文档  
   - 按 `design_docs/roadmap_milestones_v3.md` 当前阶段读取对应文档。  
   - 当前建议优先阅读：`design_docs/m2_architecture.md`（会话内连续性，含反漂移基线）。

## 2. 里程碑 Architecture 文档映射

- `design_docs/m1_architecture.md`：M1 已完成总结（实现基线）
- `design_docs/m1_5_architecture.md`：M1.5 计划（Tool Modes，可控执行闭环）
- `design_docs/m2_architecture.md`：M2 计划（会话内连续性）
- `design_docs/m3_architecture.md`：M3 计划（会话外持久记忆 + 自我进化治理闭环）
- `design_docs/m4_architecture.md`：M4 计划（Telegram 第二渠道）
- `design_docs/m5_architecture.md`：M5 计划（运营可靠性，触发式）
- `design_docs/m6_architecture.md`：M6 计划（模型迁移验证）

## 3. 场景化按需加载

- 记忆系统相关：
  - `design_docs/memory_architecture.md`
  - 适用：memory 写入/检索、M2-M3 衔接、记忆边界讨论

- Prompt 文件体系相关：
  - `design_docs/system_prompt.md`
  - 适用：AGENTS/SOUL/USER/IDENTITY/TOOLS 等注入策略讨论

- 用户验收与手工测试相关：
  - `design_docs/m3_user_test_guide.md`
  - 适用：M3 完成态下的启动步骤、功能测试脚本、预期结果对照

## 4. 历史文档（默认不作为当前依据）

- `design_docs/roadmap_milestones.md`（v1）
- `design_docs/roadmap_milestones_v2.md`（v2）

说明：
- 历史文档用于回溯，不作为当前计划的默认依据。
- 当前 roadmap 以 `roadmap_milestones_v3.md` 为准。

## 5. 与其他目录的关系

- 产品与阶段进度：`dev_docs/progress/project_progress.md`
- 计划执行细节：`dev_docs/plans/`
- 关键技术取舍：`decisions/`（含 `decisions/INDEX.md`）

## 6. 外部参考（按需）

- `OpenClaw`: [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
  - 主要架构参考方向：`src/agents/`、`src/memory/`、`src/gateway/`
- `pi-mono`: [https://github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono)
- `OpenClaw DeepWiki`: [https://deepwiki.com/openclaw/openclaw](https://deepwiki.com/openclaw/openclaw)

建议读取策略：
- 先看本索引 -> 再按默认顺序读取 -> 遇到具体主题再加载场景文档 -> 需要取舍依据时查 ADR。
