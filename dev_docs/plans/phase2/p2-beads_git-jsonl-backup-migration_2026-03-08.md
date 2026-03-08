# P2 Beads Git-JSONL Backup Migration

- Date: 2026-03-08
- Status: approved
- Scope: 项目级 `beads` 备份路径迁移；将当前文档与 closeout 规则从 Dolt remote sync 收敛为 Git 跟踪的 JSONL 备份，不改变 `bd` 本地运行时
- Track Type: parallel governance / developer-workflow repair track; outside the `P2-M*` product milestone series
- Driver: 现有 `dolt push` / `dolt pull` 在 Git remote 路径上不稳定，而项目真实需要的是“可恢复备份”，不是 Dolt 远端历史语义
- Basis:
  - [`decisions/0042-devcoord-control-plane-beads-ssot-with-dev-docs-projection.md`](../../../decisions/0042-devcoord-control-plane-beads-ssot-with-dev-docs-projection.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](../../../decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md`](../../../decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md)
  - [`AGENTS.md`](../../../AGENTS.md)
  - [`CLAUDE.md`](../../../CLAUDE.md)
  - [`justfile`](../../../justfile)
  - [`.beads/.gitignore`](../../../.beads/.gitignore)
  - [`.beads/config.yaml`](../../../.beads/config.yaml)
  - [`.beads/README.md`](../../../.beads/README.md)
- Approval Coupling:
  - 本计划已批准；ADR 0052 在同一轮文档落地中从 `proposed` 改为 `accepted`，并同步更新 `decisions/INDEX.md`。

## Context

当前项目在 `beads` 备份语义上存在三个层面的错位：

- 运行时层面：
  - 本地 `bd` 仍然用 Dolt 作为底层数据库。
  - 这件事本身不是问题；项目当前并不需要在这一轮切换本地存储后端。
- 远端备份层面：
  - 文档要求在 session closeout 时执行 `just beads-pull` / `just beads-push`。
  - 实际运行中，`dolt pull` / `dolt push` 对 Git remote 路径不稳定，已经不适合作为强制 closeout 步骤。
- 仓库事实层面：
  - [`.beads/dolt/`](../../../.beads/dolt) 被忽略，不是 Git 跟踪对象。
  - [`.beads/backup/`](../../../.beads/backup) 下的 JSONL 备份已经是 Git 跟踪对象，并且 `bd backup restore` 已支持恢复。

这意味着当前系统已经自然分化出两层：

1. 本地运行时：`bd + Dolt`
2. 恢复工件：`.beads/backup/*.jsonl`

问题不在于“是否还要保留 Dolt 本地库”，而在于“是否继续把一个不稳定的 Dolt remote 当作备份真源”。

## Core Decision

采用保守迁移路线：

- 保留 `bd + Dolt` 作为当前项目的本地 issue 运行时。
- 不再把 `just beads-pull` / `just beads-push` 作为项目级备份的 canonical 路径。
- 把 `.beads/backup/*.jsonl` 提升为正式远端恢复工件，并通过主仓库普通 Git 提交与推送完成同步。
- 运行时后端是否切到 `no-db: true`，推迟到后续单独决策，不纳入本次迁移。

## Goals

- 让 beads 相关 closeout 规则重新可靠。
- 让“修改了 issue 数据后该怎么落地”变成普通 Git 流程，而不是依赖不稳定的 Dolt remote。
- 明确 `.beads/dolt/` 与 `.beads/backup/` 的职责边界。
- 保留当前 `bd` 本地使用习惯，不在本轮引入新的运行时后端风险。
- 补上可执行的恢复演练路径。

## Non-Goals

- 不改变 `bd create/update/close/list/show` 的本地运行机制。
- 不把 `.beads/dolt/` 直接纳入主仓库 Git。
- 不立即切换 `no-db: true`。
- 不为 beads 建立独立备份仓库。
- 不把 beads 扩展成 NeoMAGI 的跨项目全局记忆层。

## Current Baseline

### 1. Closeout 规则仍依赖 Dolt remote

- [`AGENTS.md`](../../../AGENTS.md) 与 [`CLAUDE.md`](../../../CLAUDE.md) 仍要求在改动了 beads 数据时执行 `just beads-pull` / `just beads-push`。
- [`justfile`](../../../justfile) 的 `beads-pull` / `beads-push` 直接调用 `dolt pull origin main` / `dolt push origin main`。

### 2. Git 实际跟踪的是 JSONL 备份，而不是 Dolt 内部库

- [`.beads/.gitignore`](../../../.beads/.gitignore) 忽略 `dolt/`。
- `git ls-files .beads` 当前可见的是：
  - [`.beads/backup/issues.jsonl`](../../../.beads/backup/issues.jsonl)
  - [`.beads/backup/dependencies.jsonl`](../../../.beads/backup/dependencies.jsonl)
  - [`.beads/backup/events.jsonl`](../../../.beads/backup/events.jsonl)
  - [`.beads/backup/comments.jsonl`](../../../.beads/backup/comments.jsonl)
  - [`.beads/backup/labels.jsonl`](../../../.beads/backup/labels.jsonl)
  - [`.beads/backup/config.jsonl`](../../../.beads/backup/config.jsonl)
  - 以及 [`.beads/backup/backup_state.json`](../../../.beads/backup/backup_state.json)、[`.beads/metadata.json`](../../../.beads/metadata.json) 等辅助文件
- [`.beads/interactions.jsonl`](../../../.beads/interactions.jsonl) 也被 Git 跟踪，但不在 `backup/` 下；本次迁移默认不把它当作 canonical restore payload，除非后续核查发现 `bd backup restore` 对它存在显式依赖。

### 3. 恢复路径已经存在，但还没成为默认操作心智

- `bd backup --force` 可刷新备份导出。
- `bd backup status` 可查看最近备份状态。
- `bd init && bd backup restore` 已经构成恢复路径。

缺失的是：

- 把它写进 canonical runbook。
- 让 closeout 明确围绕它组织。
- 删除 Dolt remote 的默认心智。

## Target State

迁移完成后，目标边界应固定为：

```text
bd local runtime
  -> .beads/dolt/*          # 本机运行时内部库，不进 Git

bd backup / restore
  -> .beads/backup/*.jsonl  # Git 跟踪的恢复工件

project git repo
  -> commit + push .beads/backup/*
```

对应操作口径：

- 本轮未修改 beads issue 数据：
  - 无需额外 beads 备份动作。
- 本轮修改了 beads issue 数据：
  - 运行 `bd backup --force`
  - 提交 `.beads/backup/*`
  - 普通 `git push`
- 新 clone / 丢库恢复：
  - `bd init`
  - `bd backup restore`

## Migration Slices

### Slice A: 文档与 runbook 口径切换

目标：

- 把当前治理文档从“Dolt remote sync”改成“Git-backed JSONL backup”

建议文件：

- [`AGENTS.md`](../../../AGENTS.md)
- [`CLAUDE.md`](../../../CLAUDE.md)
- [`.beads/README.md`](../../../.beads/README.md)
- [`decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md`](../../../decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md)
- [`decisions/INDEX.md`](../../../decisions/INDEX.md)

预期改动：

- 在 `AGENTS.md` 中明确修改两处强约束：
  - `### Auto-Sync` 改为“本地运行时仍是 Dolt，但远端恢复工件以 `.beads/backup/*` 为准”
  - `Landing the Plane` checklist / critical rules 改为“修改了 beads 数据时运行 `bd backup --force` 并提交 `.beads/backup/*`”
- 在 `CLAUDE.md` 中把“beads 远端同步统一使用 `just beads-pull` / `just beads-push`”改成“beads 备份统一使用 JSONL backup + Git push”，避免继续暗示 Dolt remote 是 canonical 路径
- 在 [`.beads/README.md`](../../../.beads/README.md) 中替换过时示例：
  - 去掉 `bd dolt push`
  - 去掉 “Stored in `.beads/issues.jsonl`” 这类与当前仓库事实不一致的表述
  - 改成“本地运行时是 `.beads/dolt/*`，恢复工件是 `.beads/backup/*.jsonl`”
- 同步把 ADR 0052 标记为 `accepted`，并更新 `decisions/INDEX.md`
- 明确 [`.beads/dolt/`](../../../.beads/dolt) 不是 Git 跟踪对象

### Slice B: 命令入口重命名与过渡废弃

目标：

- 让 `just` 命令名与实际行为一致

建议文件：

- [`justfile`](../../../justfile)

建议方向：

- 不直接删除旧入口，保留一个过渡期：
  - `beads-pull`
  - `beads-push`
- 旧入口执行时必须输出 deprecation warning，并指向新的 canonical 命令 / runbook；不再继续调用 `dolt pull` / `dolt push`
- 新增更贴合现状的入口，例如：
  - `beads-backup` -> `bd backup --force`
  - `beads-backup-status` -> `bd backup status`
  - `beads-restore-dry-run` -> `bd backup restore --dry-run`
- 推荐过渡语义：
  - `beads-push` 输出 warning 后委托到 `beads-backup`
  - `beads-pull` 输出 warning 后提示用户改走主仓库 `git pull --rebase` 与 restore runbook，而不是伪装成新的“拉取备份”动作

说明：

- 这一切片不要求改变 `bd` 自身实现，只调整项目侧入口和默认操作心智。

### Slice C: 配置口径收敛

目标：

- 让 `.beads` 配置文件不再暗示 Git remote Dolt sync 是日常依赖

建议文件：

- [`.beads/config.yaml`](../../../.beads/config.yaml)

建议方向：

- 不删除 `sync.git-remote` 的历史信息，改为注释化保留
- 在配置旁增加说明：该 remote 已废弃，不再是项目级 canonical 备份路径；见 ADR 0052
- 核对 [`.beads/hooks/pre-push`](../../../.beads/hooks/pre-push) 及相关 hooks，确认仓库内没有额外的项目自定义 Dolt sync 逻辑
- 如果 hooks 仍只是 `bd hooks run ...` 的薄封装，则记录“已核查，无需改动”；若发现额外 sync 逻辑，再纳入同一切片

### Slice D: 恢复演练与验收

目标：

- 证明“没有 Dolt remote 也能完成备份与恢复”

建议步骤：

1. 在主仓库中执行一次 `bd create/update/close`
2. 执行 `bd backup --force`
3. 执行 `bd backup status`，并确认 `git status` 能看到 `.beads/backup/*` 的变化
4. 在 disposable clone 或临时目录中确认初始状态不存在 [`.beads/dolt/`](../../../.beads/dolt)
5. 在 disposable clone 或临时目录中执行：
   - `bd init`
   - `bd backup restore --dry-run`
   - `bd backup restore`
6. 恢复后执行 `bd list --json`（或等价的全量列举命令），并与 `.beads/backup/issues.jsonl` 的记录数做对照，确认恢复后的数据完整性
7. 记录恢复演练结果与限制

## Acceptance

- 文档不再把 `just beads-pull` / `just beads-push` 写成必须步骤。
- 项目存在明确的 canonical 备份命令入口。
- `beads-pull` / `beads-push` 在过渡期内只输出 deprecation warning 并指向新入口，不再执行 Dolt remote sync。
- `sync.git-remote` 以“注释化历史兼容信息”保留，而不是继续作为日常配置要求。
- `.beads/dolt/` 继续保持不进 Git。
- 修改 issue 数据后，`.beads/backup/*` 能稳定形成可提交变更。
- 在干净环境且初始不存在 `.beads/dolt/` 的前提下，`bd backup restore --dry-run` 能识别备份内容，且实际 restore 后的数据计数与 `.beads/backup/issues.jsonl` 一致。
- session closeout 语义与实际可靠路径一致，不再要求一个已知不稳定的 Dolt remote。

## Rollback

如果迁移验证失败，回滚原则如下：

- 不动本地 Dolt 运行时数据。
- 恢复文档中的 Dolt remote 同步口径。
- 恢复 [`justfile`](../../../justfile) 中原有 `beads-pull` / `beads-push` 作为 canonical 入口。
- 保留本次新增的 JSONL 备份文件，不做删除性回滚，因为它们仍然是合法恢复工件。

## Resolved Direction / Follow-ups

- `sync.git-remote` 采用“注释化保留 + 标注废弃”的路径，不直接删除，旁注指向 ADR 0052。
- 不在本 track 单独新增自动化 smoke test；轻量验证折叠进 `beads-backup-status` 可观测性与 Slice D 的恢复演练。
- `no-db: true` 评估延后到本方案稳定运行两周后，再按需要创建单独 follow-up issue。

## Implementation Order

实施按两步推进：

1. 先改文档、runbook 和 `just` 入口，让项目口径与真实可靠路径一致。
2. 再做一次恢复演练，把 JSONL backup 方案从“理论可用”提升为“仓库明确验证过的恢复路径”。
