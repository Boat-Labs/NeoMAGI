# P2-Devcoord 达成性测试设计：SQLite Control Plane

- Date: 2026-03-07
- Status: completed
- Scope: 判定 [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md) 是否已被当前 runtime、测试、文档和 cutover 边界完整兑现
- Track Type: parallel verification track; outside the `P2-M*` product milestone series
- Basis:
  - [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md)
  - [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
  - [`scripts/devcoord/service.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/service.py)
  - [`scripts/devcoord/sqlite_store.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/sqlite_store.py)
  - [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)
  - [`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)

## Goal

本文件不是“再列一遍 pytest 命令”，而是定义一套**达成性判定**：

- 设计文档中的关键边界是否已在代码里落地。
- 协议语义是否仍可执行，且不依赖 `beads` 作为 control-plane backend。
- projection / audit / closeout 是否仍构成可审计闭环。
- cutover 后 `bd` 是否重新回到 backlog 视角。

判定结果只允许三类：

- `ACHIEVED`
  - 所有阻断项通过，且没有未解释的设计缺口。
- `ACHIEVED_WITH_RISK`
  - 阻断项通过，但仍有非阻断项缺证据或存在残余操作风险。
- `NOT_ACHIEVED`
  - 任一阻断项失败，或关键设计目标没有证据闭环。

## Current Baseline

当前仓库已经有一批强相关自动化覆盖，尤其集中在 [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)：

- SQLite schema bootstrap、schema version fail-closed、split-brain guard、retired flags
- grouped CLI top-level surface、flat alias rewrite、`apply` payload 入口
- `gate open -> ack -> heartbeat -> phase-complete -> review -> close -> milestone close`
- `render / audit` projection、recovery handshake、watchdog/stale/log-pending

但仅凭现有绿测，还不能直接判定“整个设计文档已达成”。当前至少有 4 个高风险点需要单独验证：

1. `command send` 设计面要求覆盖 `STOP / WAIT / RESUME / PING`，而当前 parser 仅显式接受 `PING`。
2. 设计文档要求 `ACK`、`gate close`、`event_seq + state update` 在**单事务**内完成；当前实现看起来仍是多次 `create/update + commit`。
3. 设计文档要求 projection 可丢弃、可全量重建；当前测试主要覆盖“能 render”，还缺“projection 被篡改后可从 SQLite 重新纠正”。
4. 设计文档把 `bd list --status open` 去污染作为验收条件，但当前仓库内没有自动化证据证明该点。

上面 4 点里，前 2 点应视为**阻断项**；后 2 点至少要补自动化或 smoke evidence。

## Acceptance Mapping

| Design acceptance target | Test IDs | Blocking | Pass condition |
| --- | --- | --- | --- |
| `bd list --status open` 不再被 control-plane 对象污染 | CUT-02 | Yes | open 视图中不再出现 live devcoord milestone / gate / message / event 对象 |
| `AGENTTEAMS.md` 协议语义继续可执行 | PROTO-01 ~ PROTO-08, E2E-01, E2E-02 | Yes | Gate、ACK、生效、恢复握手、append-first、closeout guard 均可按协议工作 |
| `render` / `audit` 继续产生同类证据 | PROJ-01 ~ PROJ-05 | Yes | projection 文件完整、可重建，`audit.reconciled=true` 可稳定成立 |
| `gate open -> ack -> review -> close` 不依赖 `beads` | E2E-01, CUT-03 | Yes | 全链路在 SQLite-only runtime 下完成，且 control-plane 不触发 beads backend 依赖 |
| restart / resume handshake 继续成立 | PROTO-04, E2E-02 | Yes | `RECOVERY_CHECK -> STATE_SYNC_OK` 路径在 SQLite store 上稳定成立 |
| `milestone close` 不触碰 backlog issue 即可完成 closeout | PROTO-06, E2E-01, CUT-03 | Yes | 关闭 milestone 不需要 `bd sync` / beads backend，且所有 control-plane record 关闭 |
| `coord.py` 顶层命令面已收敛为更少的 grouped commands | CLI-01 ~ CLI-05 | Yes | 主 help 仅显示 grouped top-level，flat alias 仅作兼容层 |

## Test Matrix

### A. 静态边界与 schema 合同

| ID | Type | Verify | Method / command | Expected | Coverage status |
| --- | --- | --- | --- | --- | --- |
| STA-01 | Automated | `.devcoord/` 进入 `.gitignore` | `rg -n '^\\.devcoord/$' .gitignore` | 命中 `.devcoord/` | 已有静态证据，建议纳入 smoke checklist |
| STA-02 | Automated | SQLite store 只创建 6 张核心表，且 `PRAGMA user_version` 正确 | `uv run pytest -q tests/test_devcoord.py -k "SQLiteSchemaBootstrap"` | DB 存在，表集包含 `milestones/phases/gates/roles/messages/events`，`user_version == SQLITE_SCHEMA_VERSION` | 已覆盖 |
| STA-03 | Automated | `journal_mode=WAL` 与 `busy_timeout` 生效 | 新增单测：直接读取 `PRAGMA journal_mode`、`PRAGMA busy_timeout` | 分别为 `wal`、`>= 5000` | 待补，非可选 |
| STA-04 | Automated | schema version 不匹配 fail-closed | 现有 `test_schema_version_mismatch_fails_closed` | 报错并要求删除 `.devcoord/` 重建 | 已覆盖 |
| STA-05 | Automated | retired flags fail-fast | `uv run pytest -q tests/test_devcoord.py -k "retired_"` | `--backend --beads-dir --bd-bin --dolt-bin` 均直接报错 | 已覆盖 |
| STA-06 | Automated | legacy `.beads` / `.coord/beads` split-brain guard | `uv run pytest -q tests/test_devcoord.py -k "split_brain or legacy_beads"` | 无 `.devcoord/control.db` 时直接拒绝 | 已覆盖 |
| STA-07 | Automated | runtime 不再暴露 beads backend 类型/路径模型 | `rg -n "BeadsCoordStore|beads_dir|LEGACY_BEADS_SUBDIR" scripts/devcoord` | 无命中 | 已有静态证据，建议固定到 release checklist |

### B. 协议语义与 store/runtime 行为

| ID | Type | Verify | Method / command | Expected | Coverage status |
| --- | --- | --- | --- | --- | --- |
| PROTO-01 | Automated | `gate open` 创建 pending gate + pending `GATE_OPEN` message + `GATE_OPEN_SENT` event | 现有 `test_init_open_ack_heartbeat_phase_complete` / Memory path 对应测试 | gate=`pending`，message=`effective=false` | 已覆盖 |
| PROTO-02 | Automated | `ACK` 只有在存在 pending message 时才生效，重复 ACK fail-closed / idempotent | `uv run pytest -q tests/test_devcoord.py -k "ack_"` | 无 pending message 报错；重复 ACK 不产生重复 effective | 已覆盖 |
| PROTO-03 | Automated | `phase-complete` 维护 phase/gate 聚合状态与 `last_commit` | 现有 phase-complete tests | `phase_state`、`last_commit`、agent status 正确 | 已覆盖 |
| PROTO-04 | Automated | `RECOVERY_CHECK -> STATE_SYNC_OK` 恢复握手 | `uv run pytest -q tests/test_devcoord.py -k "recovery_check or state_sync_ok"` | 事件与 watchdog action 正确 | 已覆盖 |
| PROTO-05 | Automated | `PING / UNCONFIRMED_INSTRUCTION / STALE_DETECTED / LOG_PENDING` 路径 | 现有对应 tests | 事件写入、watchdog 风险与 pending ACK 正确 | 已覆盖 |
| PROTO-06 | Automated | `gate review / gate close / milestone close` 的 fail-closed 守卫 | `uv run pytest -q tests/test_devcoord.py -k "gate_close or milestone_close"` | 缺 render、缺 visible report、gates 未关时均拒绝 | 已覆盖 |
| PROTO-07 | Automated | `command send` surface 覆盖 `STOP / WAIT / RESUME / PING` | 新增 parser/CLI 合同测试 + store/runtime 测试 | `command send --name STOP|WAIT|RESUME|PING` 都可创建 pending message，并可被 `command ack` 处理 | 待补，阻断项 |
| PROTO-08 | Automated | `ACK` 与 `gate close` 的事务原子性 | 新增 fault-injection tests：在 message/gate 已更新后、event 未写入前注入异常 | 出错后 message/gate/event_seq 不留下半提交状态 | 待补，阻断项 |

`PROTO-08` 的设计意图必须非常具体：

- 为 `ack()` 增加一个可注入故障的 SQLite store double，模拟“message 已标 `effective=true`，但 `ACK` 事件写入失败”。
- 为 `gate_close()` 增加一个可注入故障的 store double，模拟“gate 已标 closed，但 `GATE_CLOSE` 事件或 report evidence 回写失败”。
- 断言失败后：
  - `message.effective` 仍维持旧值
  - `gate_state` 仍维持旧值
  - `event_seq` 未被提前占用
  - `audit` 不会看到一半新状态、一半旧状态

只要任一原子性断言失败，就不能宣称设计文档第 7 节“事务规则”已达成。

### C. projection / audit / closeout 闭环

| ID | Type | Verify | Method / command | Expected | Coverage status |
| --- | --- | --- | --- | --- | --- |
| PROJ-01 | Automated | `render` 生成 `heartbeat_events.jsonl / gate_state.md / watchdog_status.md / project_progress.md` | `uv run pytest -q tests/test_devcoord.py -k "render_and_audit_with_sqlite or full_flow_renders_projection_files"` | 4 类 projection 文件均可生成 | 已覆盖 |
| PROJ-02 | Automated | `audit` 可报告 `reconciled`、`open_gates`、`pending_ack_messages`、`log_pending_events` | 现有 audit tests | `audit` 输出与 projection 对齐 | 已覆盖 |
| PROJ-03 | Automated | `gate close` 前必须先 `render -> audit(reconciled=true)` | 现有 `test_gate_close_requires_rendered_reconciliation` | 未 render/未对账时 fail-closed | 已覆盖 |
| PROJ-04 | Automated | projection 是可丢弃、可重建的 | 新增测试：render 后手工篡改 `heartbeat_events.jsonl` / `gate_state.md`，再次 render | projection 被 SQLite 当前真源重写回正确内容 | 待补 |
| PROJ-05 | Automated | 关 gate 后必须再次 `render -> audit` 再 `milestone close` | 新增 CLI 或 service smoke | 第二轮 render/audit 后 `milestone close` 成功，且 closeout 结果与最新 gate state 一致 | 建议补强 |

### D. CLI surface 与 machine-first 入口

| ID | Type | Verify | Method / command | Expected | Coverage status |
| --- | --- | --- | --- | --- | --- |
| CLI-01 | Automated | 主 parser 仅暴露 grouped top-level commands | 现有 `test_top_level_parser_only_has_grouped_commands` | 仅 `init / gate / command / event / projection / milestone / apply` | 已覆盖 |
| CLI-02 | Automated | flat alias 被重写到 grouped canonical path | 现有 `_normalize_argv()` tests | `open-gate -> gate open` 等兼容映射成立 | 已覆盖 |
| CLI-03 | Automated | grouped CLI help 与 flat alias help 都可用 | 现有 `test_flat_alias_help_resolves_to_grouped` | 帮助面参数一致 | 已覆盖 |
| CLI-04 | Automated | `apply --payload-file / --payload-stdin` 继续是 machine-first 入口 | 现有 `test_apply_payload_file_executes_open_gate`、`test_apply_payload_stdin_executes_init` | JSON payload 路径稳定可用 | 已覆盖 |
| CLI-05 | Automated | runtime docs 与实际 CLI surface 对齐 | 新增 doc-smoke：对 [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md) 中列出的 grouped commands 做 help smoke | 文档中的 grouped commands 都能运行 `--help`，且无文档中不存在的必需参数漂移 | 待补 |

### E. 真实仓库 smoke 与 cutover 边界

这组测试不要在主工作区直接执行。建议从当前 `HEAD` 做一个**临时 clone**，避免污染真实 `dev_docs/logs/`、`project_progress.md` 与 `.devcoord/`：

```bash
tmpdir="$(mktemp -d)"
git clone . "$tmpdir/NeoMAGI-devcoord-smoke"
cd "$tmpdir/NeoMAGI-devcoord-smoke"
uv sync
```

| ID | Type | Verify | Method / command | Expected | Coverage status |
| --- | --- | --- | --- | --- | --- |
| E2E-01 | Manual smoke | SQLite-only 全链路 closeout | 在临时 clone 内执行 `init -> gate open -> command ack -> event heartbeat -> event phase-complete -> gate review -> projection render -> projection audit -> gate close -> projection render -> projection audit -> milestone close` | 全链路成功，`.devcoord/control.db` 存在，最终 `audit.reconciled=true`，milestone 关闭 | 必做 |
| E2E-02 | Manual smoke | restart / resume live trace | 在 `gate open + ack` 后执行 `event recovery-check` 与 `event state-sync-ok` | 生成对应事件，watchdog/progress 输出与 gate/commit 对齐 | 必做 |
| CUT-01 | Manual smoke | 多 worktree 指向同一个 repo-root `.devcoord/control.db` | 在临时 clone 下 `git worktree add ../wt-a`、`git worktree add ../wt-b`，分别执行 `_resolve_paths().control_db` 检查与读写 smoke | 两个 worktree 看到相同 `control_db` 路径，A 打开的 gate 在 B 能 audit 到 | 必做 |
| CUT-02 | Manual smoke | `bd list --status open` 已回到 backlog 视角 | 在真实仓库跑 `bd list --status open --json > /tmp/bd-open.json`，再 `rg -n '"coord-kind-|Coord milestone|GATE_OPEN|GATE_EFFECTIVE|phase-complete"' /tmp/bd-open.json` | 无 live control-plane 对象命中 | 必做 |
| CUT-03 | Manual smoke | devcoord-only 操作不依赖 beads sync | 在临时 clone 的 E2E smoke 中，不运行 `just beads-pull` / `just beads-push`，仅走 `coord.py` | control-plane 全链路不因 beads sync 缺失而失败 | 必做 |

## Suggested Smoke Script

以下是 `E2E-01` 的最小命令序列。使用 scratch milestone，避免与真实里程碑混用：

```bash
milestone="P2-DEVCOORD-SMOKE"
gate="G-${milestone}-P1"
run_date="2026-03-07"
report="dev_docs/reviews/phase2/p2-devcoord-smoke_${run_date}.md"
target_commit="$(git rev-parse --short HEAD)"

uv run python scripts/devcoord/coord.py init \
  --milestone "$milestone" \
  --run-date "$run_date"

uv run python scripts/devcoord/coord.py gate open \
  --milestone "$milestone" \
  --phase 1 \
  --gate "$gate" \
  --allowed-role backend \
  --target-commit "$target_commit" \
  --task "open smoke gate"

uv run python scripts/devcoord/coord.py command ack \
  --milestone "$milestone" \
  --role backend \
  --cmd GATE_OPEN \
  --gate "$gate" \
  --phase 1 \
  --commit "$target_commit" \
  --task "ack smoke gate"

uv run python scripts/devcoord/coord.py event heartbeat \
  --milestone "$milestone" \
  --role backend \
  --phase 1 \
  --status working \
  --gate "$gate" \
  --target-commit "$target_commit" \
  --task "smoke heartbeat"

uv run python scripts/devcoord/coord.py event phase-complete \
  --milestone "$milestone" \
  --role backend \
  --phase 1 \
  --gate "$gate" \
  --commit "$target_commit" \
  --task "smoke phase complete"
```

后半段需要先准备 git 可见的 review evidence：

```bash
mkdir -p "$(dirname "$report")"
printf '# smoke review\n' > "$report"
git add "$report"
git commit -m "docs(devcoord): add sqlite control plane smoke review"
report_commit="$(git rev-parse --short HEAD)"

uv run python scripts/devcoord/coord.py gate review \
  --milestone "$milestone" \
  --role tester \
  --phase 1 \
  --gate "$gate" \
  --result PASS \
  --report-commit "$report_commit" \
  --report-path "$report" \
  --task "smoke review pass"

uv run python scripts/devcoord/coord.py projection render --milestone "$milestone"
uv run python scripts/devcoord/coord.py projection audit --milestone "$milestone"

uv run python scripts/devcoord/coord.py gate close \
  --milestone "$milestone" \
  --phase 1 \
  --gate "$gate" \
  --result PASS \
  --report-commit "$report_commit" \
  --report-path "$report" \
  --task "smoke close gate"

uv run python scripts/devcoord/coord.py projection render --milestone "$milestone"
uv run python scripts/devcoord/coord.py projection audit --milestone "$milestone"
uv run python scripts/devcoord/coord.py milestone close --milestone "$milestone"
```

通过标准：

- `.devcoord/control.db` 存在且未被 git 跟踪
- `projection audit` 两次都返回 `reconciled=true`
- `milestone close` 成功
- `dev_docs/logs/*` 与 `project_progress.md` 中可见该 scratch milestone 的闭环痕迹

## Blocking Gaps To Resolve Before Claiming ACHIEVED

以下任一项未通过，都应直接判定 `NOT_ACHIEVED`：

1. `PROTO-07`
   - 若 `command send --name STOP|WAIT|RESUME` 仍不可用，则设计文档第 6.5 / 9.3 节尚未兑现。
   - 若项目决定不实现它们，就必须先更新 [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md) 与 [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md) 的口径，再谈“达成”。

2. `PROTO-08`
   - 若 `ACK` 或 `gate close` 的 fault injection 会留下半提交状态，则设计文档第 7 节“事务规则”尚未兑现。
   - 这不是低优先级 polish，而是控制面 SSOT 的一致性底线。

3. `CUT-02`
   - 若 `bd list --status open` 仍混入 live devcoord 控制面对象，则第 12 节首条验收条件尚未兑现。

## Recommended Run Order

1. 先跑现有自动化基线：
   - `uv run pytest -q tests/test_devcoord.py`
2. 再补静态检查：
   - `rg -n '^\\.devcoord/$' .gitignore`
   - `rg -n "BeadsCoordStore|beads_dir|LEGACY_BEADS_SUBDIR" scripts/devcoord`
3. 补新增阻断测试：
   - `PROTO-07`
   - `PROTO-08`
   - `STA-03`
   - `PROJ-04`
4. 在临时 clone 内执行 `E2E-01 / E2E-02 / CUT-01 / CUT-03`
5. 在真实仓库执行 `CUT-02`
6. 汇总结果，按 `ACHIEVED / ACHIEVED_WITH_RISK / NOT_ACHIEVED` 出结论

## Exit Criteria

只有同时满足以下条件，才能宣称 [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md) “已达成”：

- 所有 blocking tests 通过
- 没有 active runtime 仍依赖 beads control-plane backend
- `projection render -> projection audit -> gate close -> projection render -> projection audit -> milestone close` 在 SQLite-only 路径上完成
- `bd list --status open` 已恢复 backlog 视角
- 文档口径与实际 CLI / runtime surface 一致

否则：

- blocking 项失败或缺证据：`NOT_ACHIEVED`
- blocking 项通过，但仅剩少量非阻断文档或 smoke 缺证据：`ACHIEVED_WITH_RISK`
