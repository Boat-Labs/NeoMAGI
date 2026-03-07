# P2-Devcoord Stage D 实施计划 Draft：Beads Cutover and Closeout Hardening

- Date: 2026-03-07
- Status: draft
- Scope: `P2-Devcoord Stage D` only; complete the hard cutover from beads-backed devcoord compatibility to a SQLite-only control plane, harden milestone closeout, and retire active `.beads` control-plane guidance
- Track Type: parallel development-process repair track; outside the `P2-M*` product milestone series
- Basis:
  - [`dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-c_grouped-cli-surface_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-c_grouped-cli-surface_2026-03-07.md)
  - [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`dev_docs/devcoord/beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)

## Context

`Stage A` 已完成 `CoordStore` seam，`Stage B` 已完成 `.devcoord/control.db`、`SQLiteCoordStore` 与 `render/audit` 切换。按当前用户口径，`Stage C` 正在进行中，意味着人类可见 CLI surface 仍处于从 flat commands 向 grouped commands 收敛的阶段。

当前剩余问题已经不再是“SQLite 能不能工作”，而是“系统何时停止把 beads 兼容层当成现役 runtime 组成部分”：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py) 仍暴露：
  - `--backend sqlite|beads|auto`
  - `--beads-dir`
  - `--bd-bin`
  - `--dolt-bin`
- [`scripts/devcoord/model.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py) 仍保留 `CoordPaths.beads_dir` 与 `LEGACY_BEADS_SUBDIR`
- [`scripts/devcoord/store.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/store.py) 仍包含 `BeadsCoordStore`
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py) 仍覆盖 beads compatibility、legacy `.beads` 检测与 `BeadsCoordStore` contract
- [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)、[`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)、[`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md) 仍写着“repo 根 `.beads` 为 SSOT”
- [`.claude/skills/devcoord-pm/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/SKILL.md) 仍要求把 repo-root `.beads` 当作默认共享控制面
- [`dev_docs/devcoord/beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md) 仍是已批准的运行时说明文档
- [`dev_docs/logs/README.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/logs/README.md) 与 session completion 规则仍把 devcoord 写入与 beads sync 绑定在一起

这说明 `Stage B` 的兼容层已经完成了它的任务：它让 SQLite path 可以落地，但也把 beads 时代的 runtime、help surface、治理文案和 closeout 心智一起留在了仓库里。

因此 `Stage D` 的任务不是“再支持一种 backend”，而是把这些残留从**运行时、测试、文档和操作流程**里一起收掉，让：

- `scripts/devcoord` 只对 SQLite 控制面负责
- `beads / bd` 回到 backlog / issue graph
- closeout 顺序明确且不再依赖 beads 同步
- 历史 beads control-plane 痕迹保留为历史，而不是继续作为当前操作说明

## Precondition

`Stage D` 启动前必须满足：

- [`p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md) 已实现并落地
- [`p2-devcoord-stage-b_sqlite-backend_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md) 已实现并落地
- [`p2-devcoord-stage-c_grouped-cli-surface_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-c_grouped-cli-surface_2026-03-07.md) 已完成、review 通过、commit/push 落地
- `Stage C` 的 grouped CLI canonical path 已稳定，不再有待定命令重命名
- 当前不存在仍依赖 `beads` backend 或 repo-root `.beads` 作为 devcoord 真源的 active milestone
- SQLite fresh-start 路径下的 `projection render -> projection audit` 与至少一次完整 gate closeout 已可稳定运行

按当前用户提供的状态：

- `Stage A` 已完成
- `Stage B` 已完成
- `Stage C` 正在进行中

因此本文件当前只能作为 **Stage D draft / 预备方案**，不应在 `Stage C` 仍未定版时提前执行 hard cutover。

## Core Decision

`Stage D` 采用**SQLite-only hard cutover + closeout hardening + active-doc supersede** 的策略，而不是继续保留长期 dual-backend 兼容：

1. `scripts/devcoord` 的 steady-state runtime 收敛为 SQLite-only。
2. `--backend`、`--beads-dir`、`--bd-bin`、`--dolt-bin` 与 `BeadsCoordStore` 不再作为当前 runtime 支持面保留。
3. 对 legacy 参数和旧调用路径，优先提供**明确、可操作的 fail-fast 错误**，而不是静默 fallback 或继续自动兼容。
4. closeout 顺序明确固定为：
   - `projection render`
   - `projection audit`
   - `gate close`
   - `projection render`
   - `projection audit`
   - `milestone close`
5. 历史 `coord` beads 对象不导入 SQLite；它们通过一次性 cutover checklist 关闭或归档。
6. 全局治理文档、skills、runbook 与 logs README 全部切到 `.devcoord/control.db` 口径。
7. 历史证据文档保持原样，不做“把历史改写成现在”的清洗。

`Stage D` 的成功标准不是“代码里又少了几个参数”，而是：

- 运行时已经无法再把 beads 当 control plane backend 使用
- 当前操作文档不再误导人把 `.beads` 当 SSOT
- closeout 顺序、测试与错误提示都围绕 SQLite 真源组织
- `bd list --status open` 回到 backlog 视角，不再混入现役 devcoord 控制面对象

## Goals

- 将 devcoord runtime 收敛为 SQLite-only。
- 退役 `CoordPaths.beads_dir`、legacy `.beads` 路径探测与 `BeadsCoordStore` 运行路径。
- 移除或正式退场 `--backend`、`--beads-dir`、`--bd-bin`、`--dolt-bin`。
- 固化 SQLite closeout 顺序与 guard，避免沿用 beads 时代的操作心智。
- 更新 active governance docs、skills、runbook、README 入口，使 `.devcoord/control.db` 成为唯一 SSOT 口径。
- 提供一次性的 historical beads cleanup/checklist，使 `bd list --status open` 恢复 backlog 视图。
- 补足 cutover 后的 CLI / service / e2e smoke coverage。

## Non-Goals

- 不改变 `AGENTTEAMS.md` 的 Gate / ACK / recovery / audit 协议语义。
- 不重新设计 `Stage C` 的 grouped CLI 结构。
- 不把历史 beads control-plane 数据迁入 `.devcoord/control.db`。
- 不为历史清理新增长期维护的通用迁移平台或 workflow engine。
- 不批量改写 [`dev_docs/reviews/phase1/`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/reviews/phase1/) 或旧计划文档中真实存在的 `.beads` 历史叙述。
- 不修改产品运行时 PostgreSQL / memory / user data 的存储边界。
- 不把 `bd` 从仓库中彻底移除；`bd` 仍然用于 backlog / issue graph。

## Current Baseline

### 1. Runtime 仍暴露 beads compatibility

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py) 当前既能选 `sqlite`，也能选 `beads` 或 `auto`
- `_resolve_paths()` 仍负责 repo-root `.beads` 与 legacy `.coord/beads` 逻辑
- 帮助文案仍把 shared control plane 理解为 `BEADS_DIR`

这会造成一个坏信号：虽然 SQLite 已经是设计目标，但 runtime 仍暗示 beads 是同级正式后端。

### 2. 类型与路径命名仍保留旧后端心智

- [`CoordPaths`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py) 仍保留 `beads_dir`
- 这会让后续代码继续把“共享 control root”和“beads path”混为一谈
- `Stage B` 已引入 `control_root` / `control_db`，但尚未把旧命名彻底退役

### 3. Closeout 文案仍带 beads 时代残留

- `milestone-close` 的 help 仍写成 “Close all control-plane beads”
- 全局治理文档仍把 devcoord 写入与 beads sync 绑定
- 操作者容易误以为“milestone close 成功”仍需要某种 beads remote / sync 语义兜底

### 4. Active docs 与 historical docs 尚未分层

- 当前活跃文档仍把 `.beads` 当现实说明
- 但不少 phase1 review / plan / log 文件中的 `.beads` 叙述其实是历史证据
- 若不先明确“哪些该切、哪些不该切”，Stage D 很容易演变成无边界全文替换

## Target Runtime Boundary

`Stage D` 结束后，目标边界应固定为：

```text
LLM / skill
  -> scripts/devcoord/coord.py
    -> .devcoord/control.db
    -> dev_docs/logs/<phase>/*
    -> dev_docs/progress/project_progress.md

bd / beads
  -> backlog / issues / epics / discovered follow-up only
```

关键口径：

- `scripts/devcoord` 不再 shell-out 到 `bd` / `dolt` 以执行 control-plane runtime
- `bd` 只负责 issue tracking，不再承载 devcoord 当前状态机
- `.devcoord/control.db` 是唯一 control-plane SSOT
- `dev_docs/logs/*` 与 `project_progress.md` 仍然只是 projection

## Target Closeout Order

`Stage D` 要把 closeout 流程从“依赖 beads 心智的历史串行动作”收敛为明确的 SQLite closeout checklist：

1. `projection render`
2. `projection audit`
3. `gate close`
4. `projection render`
5. `projection audit`
6. `milestone close`

补充规则：

- `gate close` 前仍必须满足 `audit.reconciled=true`
- `milestone close` 只依赖 SQLite store + 最新 projection，不依赖 beads sync
- 若 projection 过期导致 `audit.reconciled=false`，错误提示必须明确要求重新执行 `projection render` 与 `projection audit`

## Implementation Shape

`Stage D` 复杂度评估为**中等偏高**。

代码量未必大于 `Stage B`，但它同时触及：

- runtime 删除与 public CLI 变更
- closeout 守卫与测试回归
- 三份治理文档 + 三个 skills + devcoord runtime docs 的统一改口径
- 一次性历史 cutover checklist

建议拆成 4 个切片，并要求每个切片都能独立验证。

### Slice D1: Runtime Hard Cutover and Path Cleanup

目标：

- 删除 beads runtime 兼容层
- 让 path model 与 CLI 只表达 SQLite control plane

建议文件：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
- [`scripts/devcoord/model.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py)
- [`scripts/devcoord/store.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/store.py)
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

产出：

- `coord.py` 不再暴露 `--backend sqlite|beads|auto`
- `coord.py` 不再接受 `--beads-dir`、`--bd-bin`、`--dolt-bin`
- `_resolve_paths()` 仅解析 shared repo root 下的 `.devcoord/control.db`
- `CoordPaths.beads_dir` 退役；路径模型只保留与 SQLite 控制面真实相关的字段
- `LEGACY_BEADS_SUBDIR` 与 legacy `.beads` fallback/guard 退役
- `BeadsCoordStore` 从 runtime 中移除；若仓库内已无合法调用方，直接删除实现与相应 contract tests
- 对旧参数/旧 backend 入口提供明确 fail-fast 提示：
  - 说明 beads control plane 已退役
  - 指向 `.devcoord/control.db`
  - 指向新的 canonical CLI 用法

验收：

- `scripts/devcoord` 的 steady-state runtime 不再依赖 `bd` 或 `dolt`
- `coord.py --help` 不再出现 beads backend / beads path 参数
- 使用 legacy flags 时得到明确、可操作的错误，而不是隐式 fallback
- SQLite-only path resolution 在多 worktree shared-root 前提下仍成立

### Slice D2: Closeout Workflow Hardening

目标：

- 把 milestone closeout 从 beads-era copy 和隐性前提中解耦出来
- 固化 SQLite closeout 顺序、错误提示与回归覆盖

建议文件：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
- [`scripts/devcoord/service.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/service.py)
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

产出：

- `milestone close` 的 help / error copy 改为 SQLite control-plane 语义
- `close_milestone()` 的失败信息在以下场景下都明确可操作：
  - projection 未对齐
  - gate 未全部关闭
  - pending ACK message 仍存在
- closeout 文档与测试统一采用：
  - `projection render -> projection audit -> gate close`
  - `projection render -> projection audit -> milestone close`
- 保留现有 fail-closed 语义，不放宽 guard
- 回归测试继续覆盖 `render after close_milestone` 不应破坏 `heartbeat_events.jsonl`

验收：

- closeout 相关帮助文案不再出现 “control-plane beads”
- `milestone close` 的失败场景都能给出明确的下一步提示
- SQLite closeout e2e smoke test 通过
- `render -> audit -> milestone close` 成为文档、测试与实现一致的正式收口路径

### Slice D3: Governance / Skill / Runtime Doc Cutover

目标：

- 将 active docs 与 skills 的 SSOT 口径切换到 SQLite control plane
- 保持历史文档可追溯，但不再把历史说明误当成当前 runbook

建议文件：

- [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)
- [`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)
- [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md)
- [`.claude/skills/devcoord-pm/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/SKILL.md)
- [`.claude/skills/devcoord-backend/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-backend/SKILL.md)
- [`.claude/skills/devcoord-tester/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-tester/SKILL.md)
- [`dev_docs/logs/README.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/logs/README.md)
- [`dev_docs/devcoord/beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md)
- 新增一个 SQLite runtime/runbook 文档，例如：
  - [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md)

产出：

- “repo 根 `.beads` 为 SSOT” 全部改为 `.devcoord/control.db`
- session completion / landing-the-plane 规则改为：
  - 仅当本轮实际修改 beads issue 数据时才需要 `just beads-pull` / `just beads-push`
  - devcoord control-plane 写入本身不再触发 beads sync 要求
- skills 的 narrative / checklist 切到 `Stage C` 的 canonical grouped CLI
- `references/payloads.md` 仍可保留 `apply` 作为 machine-first 入口
- `beads_control_plane.md` 改为 superseded/archive note，并指向：
  - [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)
  - 新的 runtime/runbook 文档

验收：

- active docs 与 skills 中不再把 `.beads` 当当前 SSOT
- current runbook 不再要求为 devcoord 写入执行 beads sync
- `Stage C` grouped CLI 与 `Stage D` runtime 文档口径一致
- 历史 beads 文档被清楚 supersede，而不是继续与新文档并列冒充当前说明

### Slice D4: Historical Cleanup and Cutover Checklist

目标：

- 清理历史 `coord` beads 对象对 backlog 视图的污染
- 把这件事收敛为一次性 cutover checklist，而不是长期 runtime 能力

建议文件：

- [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md) 或专门的 cutover note
- 如确有必要，再评估是否需要一个一次性辅助脚本；默认先不新增长期维护脚本

产出：

- 一次性 cutover checklist，至少包含：
  - 确认无 active milestone 仍依赖 beads backend
  - 列出历史 `coord` beads 对象
  - 执行关闭或归档
  - 再次检查 `bd list --status open`
  - 再次检查 SQLite closeout smoke
- 明确写出“不导入历史 beads control-plane 数据到 SQLite”
- 明确写出 historical review / plan / logs 文件不做改写

验收：

- `bd list --status open` 不再被 live devcoord 对象污染
- 历史 beads control-plane 对象只保留为 archive/history，不再参与现役操作
- 无需保留一个长期维护的 “archive legacy beads control plane” runtime 子命令

## Test Strategy

`Stage D` 的测试重点不是新增协议能力，而是**证明 cutover 后运行时、closeout 和文档口径一起收敛**。

### 0. Runtime Surface Removal Tests

至少覆盖：

- `coord.py --help` 不再暴露 beads backend / path 参数
- legacy flags 触发明确错误
- `_resolve_paths()` 不再依赖 `.beads` / `.coord/beads`
- 无 `bd` / `dolt` binary 时，SQLite runtime 仍能正常工作

### 1. SQLite Closeout Integration Tests

至少覆盖：

- `init -> gate open -> ack -> phase complete -> gate review -> projection render -> projection audit -> gate close -> projection render -> projection audit -> milestone close`
- `milestone close` 在以下场景 fail-closed：
  - `reconciled=false`
  - `open_gates` 非空
  - `pending_ack_messages` 非空
- `render after close_milestone` 仍保持 projection 稳定

### 2. Dead Code and Vocabulary Sweep

至少覆盖：

- 删除或替换 `BeadsCoordStore` 相关 runtime tests
- 删除或替换 `CoordPaths.beads_dir` 相关断言
- 测试名、断言文案、help snapshot 中不再把当前控制面称为 beads

### 3. Active Doc / Skill Verification

自动化或半自动检查至少覆盖：

- `rg -n "\\.beads|BEADS_DIR|--backend|--beads-dir|--bd-bin|--dolt-bin"` 在 active governance docs / skills / devcoord runtime docs 中不再命中当前口径
- `rg` 命中若仍存在，只允许出现在：
  - 历史 review / plan / archive note
  - superseded 说明中的历史上下文

### 4. Manual Cutover Checks

至少执行：

- `bd list --status open --json`
- `uv run python scripts/devcoord/coord.py projection render --milestone <smoke-milestone>`
- `uv run python scripts/devcoord/coord.py projection audit --milestone <smoke-milestone>`
- `uv run python scripts/devcoord/coord.py milestone close --milestone <smoke-milestone>`

重点确认：

- closeout 不再依赖 beads sync
- active docs 与 CLI help 一致
- backlog 视图未被 live devcoord 对象污染

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | 隐藏脚本或旧 prompt 仍传 `--backend beads` / `--beads-dir` | cutover 后调用直接失败 | 中 | 用明确 fail-fast 错误提示迁移方式；将 Stage C canonical CLI 与 Stage D doc cutover 同步落地 |
| R2 | 在仍有 active beads-backed milestone 时就删除兼容层 | 控制面状态分裂或 stranded | 中 | 把“无 active beads-backed milestone”设为 Stage D 强前置条件；cutover 前先做人工盘点 |
| R3 | 为了改口径而批量改写历史文档 | 破坏历史证据真实性 | 中 | 仅改 active docs / skills / README；旧 review/plan 文档保持原样 |
| R4 | closeout guard/错误提示改动引入行为回归 | gate/milestone 收口失败或误放行 | 中 | 以现有 fail-closed 语义为边界，只增强文案与 smoke coverage，不放宽 guard |
| R5 | dead-code cleanup 不彻底 | beads 术语或 runtime 依赖残留，继续放大熵增 | 中 | 做 grep-driven sweep，并把测试名/帮助文案/skills 一并检查 |
| R6 | Stage C 命令面仍在变化时，Stage D 提前切文档 | 文档与 CLI 真实 surface 再次漂移 | 中 | 以 Stage C merged/approved 为 Stage D 必备前置条件；若 Stage C 命令集变动，先回改本 draft 再审批 Stage D |

## Acceptance Criteria

- [ ] devcoord steady-state runtime 已收敛为 SQLite-only
- [ ] `BeadsCoordStore` 不再作为当前 runtime 路径存在
- [ ] `CoordPaths.beads_dir` 与 legacy `.beads` 路径语义已退役
- [ ] `coord.py --help` 不再暴露 beads backend / path 参数
- [ ] legacy flags 会明确 fail-fast，而不是静默 fallback
- [ ] `milestone close` / closeout 文档 / closeout 测试全部对齐 SQLite-only 收口路径
- [ ] active governance docs、skills、runtime docs 已将 `.devcoord/control.db` 作为唯一 SSOT
- [ ] devcoord control-plane 写入不再触发 `just beads-pull` / `just beads-push` 要求
- [ ] `beads_control_plane.md` 已被 supersede 或 archive note 清楚接管
- [ ] `bd list --status open` 不再被 live devcoord 对象污染
- [ ] 历史 beads control-plane 数据未导入 SQLite，历史证据文档未被错误重写

## Resolved Positions

- `Stage D` 采用 hard cutover，而不是继续保留 `auto|sqlite|beads` 三态 runtime
  - 理由：`Stage B` 的目标已经达成；继续保留 beads backend 只会让 SSOT 继续模糊
- 移除的 CLI 参数应 fail-fast 并附带迁移提示
  - 理由：直接变成“unrecognized arguments”虽然技术上可行，但对 PM / teammate / skill 调试不够可操作
- `beads_control_plane.md` 应作为历史文档被 supersede，而不是继续修订成 SQLite 时代 runbook
  - 理由：它记录的是 beads control-plane 设计，不应通过重写抹平架构切换痕迹
- 历史 plan/review/log 证据不做批量改写
  - 理由：这些文档描述的是当时真实发生的 beads 控制面阶段，改写会损伤证据链
- 历史 `coord` beads cleanup 优先采用一次性 checklist，而不是新增长期 runtime 子命令
  - 理由：这是一次性 cutover 事务，不值得为此永久扩张 `coord.py`
- `Stage D` 文档口径默认承接 `Stage C` grouped CLI
  - 理由：当前阶段的 active docs 不应再继续扩散 flat/beads-era 命令心智；若 `Stage C` 最终 canonical path 与本 draft 假设不同，必须先回改本 draft 再审批执行
