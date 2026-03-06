# M5 运营可靠性 — PM 阶段汇总

> 日期：2026-03-04
> 状态：已合并，待 post-review 修复

## 1. Milestone 总结

M5 运营可靠性三个 Phase 全部通过 Gate 验收，代码已合并到 main。用户 post-review 发现 3 个 P1 + 2 个 P2，待修复。

| Phase | 交付 | Gate 结论 | Backend Commit | 测试增量 |
|-------|------|-----------|---------------|---------|
| Phase 0: Preflight 统一框架 | CheckStatus/CheckResult/PreflightReport 数据模型, run_preflight() C2~C11 检查项, lifespan 重构 + ValidationError 结构化日志 | PASS | 11cf795 | 690→731 (+41) |
| Phase 1: 健康接口 + Doctor CLI | /health/live + /health/ready 端点, run_doctor() D1~D4 + DD1~DD3, CLI 入口 + justfile | PASS (P1 fix) | 5836d8e + 9eb7946 | 731→787 (+56) |
| Phase 2: 备份与恢复闭环 | backup.py (pg_dump 按表) + restore.py (8 步恢复) + reindex/reconcile CLI + runbook S1~S5 | PASS | ec5e2b2 | 787→810 (+23) |

最终测试总数：810 tests，0 failures，ruff clean。
基线增长：690 → 810（+120 tests，+17%）。

## 2. 变更清单（文件级）

### 新增文件（15 个）

**源码（4 个）**
- `src/infra/health.py` — CheckStatus/CheckResult/PreflightReport/DoctorReport 数据模型
- `src/infra/preflight.py` — run_preflight() + C2~C11 统一启动检查
- `src/infra/doctor.py` — run_doctor() + D1~D4 + DD1~DD3 运行期诊断
- `src/backend/cli.py` — doctor/reindex/reconcile CLI 入口

**脚本（2 个）**
- `scripts/backup.py` — pg_dump 按表备份 + workspace tar + manifest
- `scripts/restore.py` — 8 步恢复序列

**测试（7 个）**
- `tests/test_preflight.py` — preflight 检查项测试 (450 行)
- `tests/test_doctor.py` — doctor 检查项 + 只读保证测试 (649 行)
- `tests/test_health_models.py` — 数据模型测试
- `tests/test_health_endpoints.py` — /health/live + /health/ready 端点测试
- `tests/test_cli.py` — CLI 入口 smoke test
- `tests/test_backup.py` — backup mock subprocess 测试
- `tests/test_restore.py` — restore 8 步顺序验证测试

**文档（2 个）**
- `dev_docs/runbooks/recovery.md` — S1~S5 恢复场景 runbook
- `src/backend/__init__.py` — 包初始化

### 修改文件（3 个）

- `src/gateway/app.py` — lifespan preflight 集成 + /health/live + /health/ready 端点
- `tests/test_app_integration.py` — lifespan 集成测试更新
- `justfile` — 新增 doctor/doctor-deep/backup/restore/reindex/reconcile

总计：**18 files changed, +3,749 insertions, −30 deletions**。

## 3. Gate 验收报告索引

| Gate | 报告 | 结论 |
|------|------|------|
| m5-g0 (P0→P1) | `dev_docs/reviews/phase1/m5_g0_phase0_review.md` | PASS |
| m5-g1 (P1→P2) | `dev_docs/reviews/phase1/m5_g1_phase1_review.md` | PASS_WITH_RISK → P1 fix → PASS |
| m5-g1-r2 (re-review) | `dev_docs/reviews/phase1/m5_g1_phase1_review_r2.md` | PASS |
| m5-g2 (P2→完成) | `dev_docs/reviews/phase1/m5_g2_phase2_review.md` | PASS |

## 4. Tester Findings 汇总

### Gate 0 (Phase 0)
- P2-01: C2 unknown provider 分支缺测试覆盖
- P3-01: C4/C3 同步 FS I/O 在 async 上下文调用（微秒级，可接受）
- P3-02: C10 + adapter check_ready() 双重 Telegram getMe 调用
- P3-03: test_workspace_path_mismatch_fails RuntimeWarning

### Gate 1 (Phase 1)
- **P1-01**: D2/DD3 对 MEMORY.md 使用 `---` 分隔符，与 indexer `_split_by_headers()` 不一致 → **已修复** (9eb7946)
- P2-01: D1 缺 status chain 异常检查
- P3-01: D1 evidence 暴露完整文件路径

### Gate 2 (Phase 2)
- P3-01: CLI smoke test 硬编码 worktree 路径

### 用户 Post-Review（合并后）
- **P1-01**: /health/ready 只反映启动快照，运行期依赖掉线仍返回 ready
- **P1-02**: restore 解压前不清空 workspace，残留文件被重新索引
- **P1-03**: backup/restore workspace 路径写死 `Path("workspace")`，不尊重配置
- **P2-01**: D2/DD3 daily notes 计数不过滤纯 metadata 条目（indexer 会跳过）
- **P2-02**: C4 只验证 workspace 根可写，未验证 workspace/memory 子目录

## 5. 过程经验

| 事件 | 影响 | 改进 |
|------|------|------|
| coord.py phase-complete 在 open-gate 之前调用失败 | open-gate 自动创建 gate/phase issues，phase-complete 依赖其存在 | 改进：PM 流程调整为先 open-gate 再 phase-complete |
| 遗留 shutdown_request 误杀同名新 teammate | 新 spawn 的 tester/backend 收到上轮残留的 shutdown_request 自动退出 | 改进：换名字 spawn（backend→backend2, tester→reviewer） |
| bd create --deps discovered-from:X 不等于 parent | 以为 `--deps discovered-from:` 能设 parent-child 关系，实际只是依赖 | 改进：设 parent 用 `bd update --parent` |
| bd create --set-metadata 不存在 | 正确语法是 `--metadata '{"key":"val"}'` | 改进：操作前先查 `bd create --help` |
| bd close 批量执行部分静默失败 | 用 `&&` 链式执行时前面命令失败后面被跳过 | 改进：每个 close 独立执行并验证 |
| devcoord milestone-close 遗漏 | 关 Gate 后忘记关闭 coord.py 自动创建的控制面 issues | 改进：milestone 完成时固定执行 milestone-close |

## 6. 心跳日志

`dev_docs/logs/phase1/m5_2026-03-04/heartbeat_events.jsonl` — 22 条事件，覆盖 3 Gate 完整生命周期。

## 7. Git 合并记录

| 操作 | Commit / 状态 |
|------|---------------|
| Backend 合并到 main | `0d98171` (merge commit) |
| Tester review 分支 | g0: `d98bf66`, g1: `6a056c2`, g1-r2: `2496b58`/`28fee80`, g2: `e71ba67` |
| Main 最终 HEAD | `2758cf7` (含 review 报告同步) |
| Worktree 清理 | 5 个 worktree 已移除 |
| Beads 同步 | `just beads-push` 完成 |

## 8. 未完成项

用户 post-review 发现 3 个 P1 + 2 个 P2，需要修复后才能认为 M5 真正闭环。修复计划见 `dev_docs/plans/phase1/m5_post-review-fix_2026-03-04.md`。
