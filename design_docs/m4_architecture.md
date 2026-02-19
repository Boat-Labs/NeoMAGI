# M4 Architecture（计划）

> 状态：planned  
> 对应里程碑：M4 第二渠道适配（Telegram）  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0003、当前 WebChat-first 结构

## 1. 目标
- 在 WebChat 之外新增 Telegram 入口，保持核心能力和行为策略一致。

## 2. 当前基线（输入）
- WebChat 已打通 Gateway -> Agent -> Session -> Tool 的完整闭环。
- `channels` 包目前为空实现，尚无第二渠道适配器。

实现参考：
- `src/frontend/`
- `src/gateway/app.py`
- `src/channels/__init__.py`

## 3. 目标架构（高层）
- 新增 Telegram adapter，将平台事件映射为统一请求语义。
- 复用现有 Gateway/Agent/Session/Tool 主链路，不复制核心业务逻辑。
- 渠道层仅负责协议转换、消息收发与身份映射。

## 4. 边界
- In:
  - Telegram 单渠道打通。
  - 与 WebChat 一致的核心行为边界。
- Out:
  - 不扩展到多平台并行适配。
  - 不引入渠道运营功能。

## 5. 验收对齐（来自 roadmap）
- Telegram 可独立完成核心任务流程。
- 渠道切换不改变核心能力边界与安全策略。
