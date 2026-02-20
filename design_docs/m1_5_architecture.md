# M1.5 Architecture（计划）

> 状态：planned  
> 对应里程碑：M1.5 可控执行闭环（Tool Modes）  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0024、ADR 0025、已讨论的工具分组与模式化授权

## 1. 目标
- 在不引入复杂权限系统的前提下，建立 mode 授权框架与双闸门，并以 `chat_safe` 验证可控执行边界。

## 2. 当前基线（输入）
- 工具注册为启动时静态注册，模型可见当前 registry 全量工具。
- 当前可用工具为 `current_time`、`read_file`、`memory_search`（占位）。
- `memory_append` 尚未加入当前内置工具集合。
- 尚无“按模式过滤工具”与“执行前二次授权校验”。
- Memory 组在本阶段仅做模式授权框架预留，不在本阶段落地持久化写入能力。

实现参考：
- `src/tools/registry.py`
- `src/tools/builtins/__init__.py`
- `src/agent/agent.py`
- `src/gateway/app.py`

## 3. 目标架构（高层）

### 3.1 工具分组
- Code 组：`read/write/edit/bash`
- Memory 组（框架预留）：`memory_search`、`memory_append`
- World 组：`current_time`

### 3.2 模式定义
- `chat_safe`：默认模式，仅开放低风险工具能力面。
- `coding`：任务模式，开放代码闭环所需工具能力面（M1.5 仅预留，不开放切换）。

### 3.3 模式切换归属
- 切换权归用户，不归模型。
- 采用会话级显式切换，不做自动意图识别切换。
- M1.5 阶段固定 `chat_safe` 运行，`coding` 不纳入本阶段验收。

### 3.4 授权策略（双闸门）
- 暴露闸门：根据 mode 决定“哪些工具 schema 给模型可见”。
- 执行闸门：工具实际执行前再次按 mode 校验，拒绝越权调用。

### 3.5 风险边界
- 高风险执行（尤其 `bash`）必须具备明确的拒绝或确认机制，不允许静默穿透。

## 4. 边界
- In:
  - 模式化授权与工具能力面收敛。
  - 固定 `chat_safe` 下的可控执行边界验证。
- Out:
  - 不引入组织级 RBAC、策略中心或复杂审批工作流。
  - 不在本阶段开放 `coding` 模式切换与代码执行闭环。
  - 不在本阶段交付会话外记忆写入闭环（`memory_append` 实现归 M3）。

## 5. 验收对齐（来自 roadmap）
- M1.5 阶段仅 `chat_safe` 生效，模型不可自行切换 mode。
- `chat_safe` 模式下，写入/执行类工具请求会被明确拒绝并解释原因。
- 高风险命令不会静默执行，用户可感知控制点。
