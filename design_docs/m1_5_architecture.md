# M1.5 Architecture（计划）

> 状态：planned  
> 对应里程碑：M1.5 可控执行闭环（Tool Modes）  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0024、已讨论的工具分组与模式化授权

## 1. 目标
- 在不引入复杂权限系统的前提下，让 agent 在可控边界内完成“读-写-改-执行”任务闭环。

## 2. 当前基线（输入）
- 工具注册为启动时静态注册，模型可见当前 registry 全量工具。
- 当前可用工具为 `current_time`、`read_file`、`memory_search`（占位）。
- `memory_append` 尚未加入当前内置工具集合。
- 尚无“按模式过滤工具”与“执行前二次授权校验”。

实现参考：
- `src/tools/registry.py`
- `src/tools/builtins/__init__.py`
- `src/agent/agent.py`
- `src/gateway/app.py`

## 3. 目标架构（高层）

### 3.1 工具分组
- Code 组：`read/write/edit/bash`
- Memory 组：`memory_search`、`memory_append`
- World 组：`current_time`

### 3.2 模式定义
- `chat_safe`：默认模式，仅开放低风险工具能力面。
- `coding`：任务模式，开放代码闭环所需工具能力面。

### 3.3 授权策略（双闸门）
- 暴露闸门：根据 mode 决定“哪些工具 schema 给模型可见”。
- 执行闸门：工具实际执行前再次按 mode 校验，拒绝越权调用。

### 3.4 风险边界
- 高风险执行（尤其 `bash`）必须具备明确的拒绝或确认机制，不允许静默穿透。

## 4. 边界
- In:
  - 模式化授权与工具能力面收敛。
  - 支撑可控的代码任务闭环。
- Out:
  - 不引入组织级 RBAC、策略中心或复杂审批工作流。

## 5. 验收对齐（来自 roadmap）
- `coding` 模式下，可完成修复代码并执行验证的完整流程。
- `chat_safe` 模式下，写入/执行类工具请求会被明确拒绝并解释原因。
- 高风险命令不会静默执行，用户可感知控制点。
