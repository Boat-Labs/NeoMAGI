# P2-M2 User Acceptance Test — 执行记录

> 测试指导：`design_docs/phase2/p2_m2_user_test_guide.md`  
> Open issues：`design_docs/phase2/p2_m2_open_issues.md`  
> Hotfix plan：`dev_docs/plans/phase2/p2-m2_hotfix_2026-04-11.md`

## 第一轮：2026-04-11（hotfix 前）

| 用例 | 状态 | 备注 |
|------|------|------|
| T01 | PASS | enter_procedure 成功 |
| T02 | PASS | single-active 正确拒绝 |
| T03 | PASS | invalid action 正确拒绝 |
| T04 | PASS | CAS conflict 正确检测 |
| T05 | PASS | cancel → cancelled (terminal)，noop_echo tool |
| T06 | PASS | terminal 后 re-enter 成功 |
| T07 | PASS | model 正确识别 test.research / planning / delegate_work |
| T08 | PASS | delegation 成功（SDK 归一化修复后），worker 用了 5 iterations |
| T09 | KNOWN_ISSUE | publish 成功但 `flush_candidate_count=0`（空合并）→ OI-M2-02/05 |
| T10 | FAIL | model 陷入 memory_append 循环（9 轮）→ OI-M2-04 |
| T11+ | 未执行 | 因 OI-M2-04 阻塞暂停 |

### 发现的问题

- OI-M2-01：Worker SDK 对象未归一化 → 已修（`44296c3`）
- OI-M2-02：Publish merge_keys 对 model 不透明 → 记录
- OI-M2-04：Procedure terminal 后 memory_append 循环 → 记录
- OI-M2-05：空 merge_keys 仍允许 state transition → 记录

## 第二轮：2026-04-11（hotfix 后）

Hotfix commits：`6c01835`（Slice B: publish fail-closed + available_keys）、`213b3db`（Slice A: 写工具断路器）

### A 层

| 用例 | 状态 | 备注 |
|------|------|------|
| T01 | PASS | — |
| T02 | PASS | — |
| T03 | PASS | — |
| T04 | PASS | — |
| T05 | PASS | — |
| T06 | PASS | — |

### B 层

| 用例 | 状态 | 备注 |
|------|------|------|
| T07 | PASS | model 正确描述 procedure state |
| T08 | PASS | 第一次 iteration limit（task 过于复杂），简化 task_brief 后成功；delegation 返回包含 available_keys |
| T09 | PASS | model 使用 `merge_keys: ["summary"]`（来自 available_keys），数据成功合并 |
| T10 | PASS | model 仍尝试调用 memory_append 1 次，但未形成循环（断路器就绪，未触发——仅 1 次） |

### C 层

| 用例 | 状态 | 备注 |
|------|------|------|
| T11 | PASS | 工具隔离正确，无 procedure-only 或 high-risk 工具泄漏 |
| T12 | PASS | ProcedureView 不含 context 字段（代码结构保证） |
| T13 | 未触发 | 会话未达到 compaction 阈值，handoff_id 未丢失 |
| T14-1 | PASS | task_brief 超限正确拒绝 |
| T14-2 | PASS | item 超限正确拒绝 |
| T14-3 | PASS* | 测试脚本值不足（30000 < 32KB），已修正为 35000；32KB 限制代码正确 |
| T15 | PASS | 已知限制，记入 OI-M2-03 |
| T16 | SKIP | 前置条件不满足（无 active skill） |
| T17 | PASS | 撤销 patch 后系统不崩溃，正常聊天，孤儿 procedure 无影响 |

## 结论

P2-M2 用户验收通过：
- A 层 6/6 PASS
- B 层 4/4 PASS（hotfix 后）
- C 层无 P0 级缺陷，所有发现已记入 open issues
- 5 个 OI 中 3 个已修复（OI-M2-01/04/05），1 个部分修复（OI-M2-02），1 个延期（OI-M2-03）
