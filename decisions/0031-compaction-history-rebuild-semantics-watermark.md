# 0031-compaction-history-rebuild-semantics-watermark

- Status: accepted
- Date: 2026-02-21

## 选了什么
- M2 的 compaction 后 history 重建采用“Pi 同构语义、NeoMAGI 数据结构实现”：以 `last_compaction_seq` 作为唯一裁剪水位线重建上下文。
- 当轮请求的有效 history 统一按 `seq > last_compaction_seq` 读取；`compacted_context` 通过 system prompt 注入，不与原始 messages 混拼为双重来源。
- compaction 执行时必须排除当前未完成 turn（当前请求刚写入的 user turn），避免当轮输入被压缩或丢失。
- compaction 结果持久化采用 fencing 更新（带 `lock_token`），并保证 `last_compaction_seq` 单调递增。
- 当 compaction 结果为 `noop`（无可压缩区间）时，不执行 `store_compaction_result` 持久化写入，不推进水位线。
- `preserved_messages` 不作为主路径真相来源，仅可作为调试辅助信息。

## 为什么
- 以水位线做重建语义可判定、可测试、可幂等，避免“内存替换片段 + DB 状态”双源漂移。
- 当轮排除未完成 turn 可避免 compaction 后丢失最新用户输入，保证请求语义连续。
- 与已有会话串行化/fencing 机制（ADR 0021/0022）一致，降低并发覆盖与 stale worker 写入风险。
- 该语义与 Pi 的“summary + cut point（firstKept）重建”一致，但更贴合 NeoMAGI 的 `seq` 数据模型，复杂度更低。
- `noop` 跳过写库可避免与“水位线单调递增”约束冲突，保持边界行为稳定。

## 放弃了什么
- 方案 A：以 `CompactionResult.preserved_messages` 直接替换全量 history 作为运行时主路径。
  - 放弃原因：容易与持久化状态分叉，且在边界场景下可能遗漏当前 turn 或造成重放不一致。
- 方案 B：compaction 与重建混用多条来源（summary、preserved_messages、全量 history）动态拼装。
  - 放弃原因：语义不唯一，排障和验收复杂度高，不利于长期维护。
- 方案 C：`noop` 仍写入 compaction 状态（仅 metadata 或同值水位线更新）。
  - 放弃原因：会增加无效写入，并与水位线单调保护产生冲突。

## 影响
- `SessionManager` 需要提供稳定的“按水位线读取有效历史”接口，作为 agent loop 唯一重建入口。
- `store_compaction_result` 必须执行带 `lock_token` 的原子更新，并对水位线做单调保护。
- compaction 返回 `noop` 时仅记录观测日志，不写会话 compaction 字段。
- 端到端测试需新增断言：当轮 compaction 后仍包含当前 user turn，且多次 compaction 后水位线单调递增。
