# 0052-project-beads-backup-git-tracked-jsonl-exports

- Status: proposed
- Date: 2026-03-08
- Note: 本 ADR 只讨论项目级 `beads` issue 数据的远端备份路径；不改变 `bd` 的本地运行时，也不改变产品运行时数据库边界。

## 背景

- 当前仓库仍把 `beads` 远端同步写成 `just beads-pull` / `just beads-push`，底层通过 [`.beads/dolt/NeoMAGI`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/dolt/NeoMAGI) 执行 `dolt pull origin main` / `dolt push origin main`。
- 这一链路在当前项目上已经暴露出稳定性问题：
  - `bd` 写操作后的 Dolt auto-push 持续报 `fatal: remote 'origin' not found`。
  - 手工 `dolt pull` / `dolt push` 在 Git remote 路径上长时间卡住，难以作为 session closeout 的可靠步骤。
- 与此同时，仓库当前的 Git 跟踪事实已经说明我们真正需要的不是“Dolt Git remote 语义”，而是“项目级 issue 数据的可恢复备份”：
  - [`.beads/.gitignore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/.gitignore) 明确忽略了 `dolt/` 目录，说明 Dolt 内部库并不适合直接进 Git。
  - 仓库已经跟踪 [`.beads/backup/issues.jsonl`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/backup/issues.jsonl)、[`.beads/backup/dependencies.jsonl`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/backup/dependencies.jsonl)、[`.beads/backup/events.jsonl`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/backup/events.jsonl) 等 JSONL 备份文件。
  - `bd backup restore` 已支持从这些 JSONL 备份恢复本地数据库。
- 本项目对 `beads` 的需求是“项目级 issue 跟踪和辅助开发”，不是 NeoMAGI 的跨项目全局记忆；因此 issue 数据在不同项目间天然隔离，并不是当前问题。

## 选了什么

- 保留本地 `bd + Dolt` 作为当前项目的本地运行时存储，不在本 ADR 中切换到 `no-db`。
- 停止把 `dolt push` / `dolt pull` 视为项目级 `beads` 远端备份的 canonical 路径。
- 采用“Git 跟踪的 JSONL 备份”作为项目级 `beads` 的远端恢复路径：
  - 用 `bd backup --force` 生成并刷新 [`.beads/backup/`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/backup) 下的 JSONL 备份。
  - 通过当前项目仓库的普通 `git add / commit / push` 同步这些备份文件。
- 明确禁止把 [`.beads/dolt/`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/dolt) 或其他 Dolt 内部目录直接纳入主仓库 Git 版本控制。
- 将“session closeout 中与 beads 相关的强制动作”从 `just beads-pull` / `just beads-push` 收敛为“在本轮确实修改了 beads issue 数据时，执行 `bd backup --force` 并把 `.beads/backup/*` 一并提交推送”。
- 将“从新 clone 或机器故障恢复 beads issue 数据”的标准路径固定为：
  - `bd init`
  - `bd backup restore`
- `no-db: true` 只作为后续可选优化方向，不作为本 ADR 的立即迁移目标。

## 为什么

- 当前真正需要的是“可恢复备份”，不是 Dolt remote 的细粒度版本历史语义。
- Git 跟踪 JSONL 备份比 Git 跟踪 Dolt 内部库更符合当前仓库的长期可维护性：
  - 文件可读、可 diff、可审阅。
  - 与当前仓库已经跟踪的 `.beads/backup/*.jsonl` 事实一致。
  - 避免把 runtime 状态、锁文件、内部格式和恢复工件混进主仓库提交。
- 保留本地 Dolt 运行时而不立即切 `no-db`，是更保守的迁移路径：
  - 不改变开发者当前 `bd create/update/close/list/show` 的本地使用方式。
  - 不要求在同一轮里同时验证“本地存储后端切换”和“远端备份路径切换”。
  - 将迁移风险控制在“备份与恢复口径调整”，而不是“运行时语义调整”。
- 本项目对 `beads` 的预期边界是项目级 issue 跟踪；缺少跨项目可见性不会削弱当前协作目标。
- 与其继续维护一个不稳定的 Dolt Git remote，不如把仓库已有的 JSONL 备份机制提升为正式做法。

## 放弃了什么

- 方案 A：继续把 `dolt push` / `dolt pull` 当作 canonical 远端同步路径，只做重试和文档修补。
  - 放弃原因：当前失败模式已经说明这条路径在本项目上不够可靠，继续把它写进 closeout 只会让 session 落地不稳定。
- 方案 B：把 [`.beads/dolt/`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/dolt) 整体纳入项目 Git，当作“网盘备份”。
  - 放弃原因：Dolt 内部库存储颗粒度和运行时文件形态都不适合直接进入主仓库 Git，冲突和噪音成本高于收益。
- 方案 C：立即把 `beads` 切到 `no-db: true`，让 JSONL 直接成为 source of truth。
  - 放弃原因：方向上可能更干净，但这会把“远端备份策略迁移”和“本地运行时后端迁移”绑成同一件事，当前阶段没有必要同时承担这两个变化面。
- 方案 D：为 `beads` 维护单独的专用 Git 仓库继续做备份。
  - 放弃原因：当前项目只需要项目级备份，不需要额外 repo 和额外 closeout 路径；直接跟随主仓库提交更简单。

## 影响

- 如果本 ADR 被接受，项目文档与 closeout 规则应从“Dolt remote sync”改写为“JSONL backup + Git push”。
- [`justfile`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/justfile) 后续应从 `beads-pull` / `beads-push` 迁移到更贴近实际行为的命令，例如 `beads-backup`、`beads-backup-status`、`beads-restore-dry-run`。
- [`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md) 与 [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md) 的 session completion 规则需要更新：
  - 代码 / 文档改动仍走普通 `git push`
  - 只有在本轮修改了 beads issue 数据时，才额外刷新并提交 `.beads/backup/*`
- [`.beads/config.yaml`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.beads/config.yaml) 中当前的 `sync.git-remote` 不再应被视为日常依赖路径；后续可选择删除、注释化，或仅作为历史兼容配置保留。
- 恢复演练需要成为迁移验收的一部分，至少验证：
  - `bd backup --force`
  - `bd backup status`
  - `bd init && bd backup restore --dry-run`
- 这里的“远端备份”仅指通过主仓库 Git 保存恢复工件，不意味着 `beads` 变成产品运行时记忆层，也不意味着要为跨项目可见性新增抽象。
