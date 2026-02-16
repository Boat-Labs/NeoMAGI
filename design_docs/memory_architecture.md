# 记忆架构
NeoMAGI的记忆不是 fine-tuning 也不是 model 层面的记忆，而是一套"写文件 + 检索文件"的管道。
核心原则是：**"Mental notes don't survive session restarts. Files do."**

## 记忆分层架构
```
┌─────────────────────────────────────────────────┐
│           Context Window (每次 turn)              │
│  ┌─────────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ AGENTS.md   │  │ SOUL.md  │  │  USER.md    │ │
│  │ IDENTITY.md │  │ TOOLS.md │  │  MEMORY.md* │ │
│  └─────────────┘  └──────────┘  └─────────────┘ │
│            * 仅 main session 加载                  │
├─────────────────────────────────────────────────┤
│           Session History (当前对话)              │
│  transcript stored as JSONL per session          │
├─────────────────────────────────────────────────┤
│  Short-term Memory (今天+昨天)                    │
│  memory/2026-02-16.md  (自动加载)                 │
│  memory/2026-02-15.md  (自动加载)                 │
├─────────────────────────────────────────────────┤
│           Long-term Memory Search (按需检索)       │
│  Hybrid Search: Vector (70%) + BM25 (30%)        │
│  SQLite + sqlite-vec + FTS5                      │
│  搜索范围: 所有 memory/*.md + MEMORY.md +          │
│           session transcripts (可选)              │
└─────────────────────────────────────────────────┘
```

## 记忆写入：三种方式

**方式一：用户显式要求**

用户说"记住这个"，agent 直接写入 `memory/YYYY-MM-DD.md` 或相关文件。
也可以显式要求写入 USER.md，比如 "Add to USER.md that I prefer short answers"。

**方式二：Agent 主动记录**

AGENTS.md 里的指令告诉 agent：遇到重要决策、上下文、教训时，主动写入 daily notes。
agent 还被要求定期从 daily notes 中提炼有价值的信息，晋升到 MEMORY.md。
重要学习内容要和用户进行1on1会议确认。

**方式三：Pre-compaction Memory Flush **

当 session 接近 context window 上限时（比如 200K window，在约 176K tokens 时触发），NeoMAGI应该在压缩之前自动发起一个 **silent agentic turn**，提示 model 把值得保留的信息写入 `memory/YYYY-MM-DD.md`，然后才执行 compaction。如果没有什么值得保存的，model 回复 `NO_REPLY` 或 `NO_FLUSH`，不会产生垃圾。
这解决了一个关键问题：长对话被 compaction 截断时，重要信息不会丢失。

### 记忆检索：Hybrid Search

Agent 有一个内置工具 `memory_search`，当需要回忆历史信息时调用。检索流程：
```
Query → 同时触发两路搜索
  ├─ Vector Search (sqlite-vec, cosine similarity) → 70% 权重
  │   概念匹配："gateway host" ≈ "machine running gateway"
  └─ BM25 Search (SQLite FTS5) → 30% 权重
      精确匹配：error codes, 函数名, 唯一标识符

→ Union (取并集，不是交集)
→ finalScore = vectorWeight × vectorScore + textWeight × textScore
→ 返回 top-k chunks 注入到当前 turn 的 context