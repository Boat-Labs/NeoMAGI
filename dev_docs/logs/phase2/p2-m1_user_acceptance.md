# P2-M1 用户验收测试记录

> 对应测试指导：`design_docs/phase2/p2_m1_user_test_guide.md`

## 执行记录模板

每次执行时复制下方表格，填入日期和结果。多次执行时追加新表格。

---

### YYYY-MM-DD 执行

环境：
- PG: running / not running
- 是否执行了 reset-user-db: yes / no

**A 层：WebChat 手工测试**

| 用例 | 状态 | 备注 |
|------|------|------|
| T01 启动与基础对话 | | |
| T02 soul_status | | |
| T03 soul_propose (CLI) | | |
| T03-webchat soul_propose (可选) | | |
| T03b 生成第二版本 | | |
| T04 soul_rollback | | |
| T05 教学意图 + DB 验证 | | |
| T06 chat_safe 边界 | | |

**B 层：受控回放测试**

| 用例 | 状态 | 备注 |
|------|------|------|
| T07 Skill Runtime e2e | | |
| T08 GC-1 | | |
| T09 GC-2 | | |
| T10 Builder Work Memory | | |
| T11 Wrapper Tool 启动恢复 | | |

**产物检查**

| 检查项 | 状态 | 备注 |
|--------|------|------|
| 7.1 workspace artifacts | | |
| 7.2 governance tables | | |
| 7.3 bd issue 索引 | | |

**结论**：PASS / FAIL / PARTIAL（说明阻塞项）
