# System Prompt 文件体系

学习 OpenClaw 的设计， NeoMAGI每次 agent turn 都会动态组装 system prompt，核心由 buildAgentSystemPrompt() 函数完成。它从 workspace 目录加载一组 bootstrap 文件，全部注入到 context window 的 Project Context 区域。每个文件上限 65,536 字符。

## 0. 运行时加载顺序与优先级（单一真源）

每次 turn 固定注入（Workspace context layer，按顺序）：
1. `AGENTS.md`（行为契约）
2. `SOUL.md`（人格/语气）
3. `USER.md`（用户偏好）
4. `IDENTITY.md`（身份展示）

每次 turn 固定注入（Tooling layer）：
- `TOOLS.md`（工具与环境备忘；与工具清单同层，不在 workspace context layer）

按需加载：
- `MEMORY.md`（仅 main session）
- `HEARTBEAT.md`（心跳轮询）
- `BOOTSTRAP.md` / `BOOT.md`（初始化/启动）

冲突时优先级：`Safety > AGENTS.md > USER.md > SOUL.md > IDENTITY.md`

说明：
- 本节是运行时文件加载与冲突处理的唯一规范来源（SSOT）。
- `AGENTS.md` 仅做引用，不重复维护该细节；`CLAUDE.md` 可保留高层摘要，但不应维护冲突优先级与逐文件加载细节。

## 1. 文件定位加载条件

| 文件 | 操作指南 / 行为契约 | 每次 turn 注入 | 
| --- | --- | --- |
| AGENTS.md | 操作指南 / 行为契约 | 每次 turn 注入 |
| SOUL.md | 人格 / 哲学 / 价值观 | 每次 turn 注入 |
| USER.md | 用户偏好 / 个性化层 | 每次 turn 注入 |
| IDENTITY.md | 结构化身份信息 | 每次 turn 注入 |
| TOOLS.md | 工具使用说明 / 本地配置备忘 | 每次 turn 注入（Tooling layer） |
| MEMORY.md | 长期记忆（策展后的持久知识） | 仅私聊 session |
| HEARTBEAT.md | 心跳任务清单 | 心跳轮询时 |
| BOOTSTRAP.md | 首次初始化指令（"出生证明"） | 仅新 workspace |
| BOOT.md | 启动钩子（可选） | 启动时 |
| memory/YYYY-MM-DD.md | 每日笔记（短期记忆） | 自动加载今天+昨天 |

## 2. 文件详解与样例
### 2.1 AGENTS.md — "操作手册"
这是最重要的文件，定义 agent 的行为规则、工作流程、安全边界。相当于给 agent 的操作 SOP。
关键内容包括：

* 在 main session 启动时要读取 MEMORY.md 和当日 memory/ 文件
* 记忆管理策略：什么时候写 daily notes，什么时候更新 MEMORY.md
* 安全边界："Don't exfiltrate private data. Ever."
* 群聊行为规则：什么时候该发言，什么时候静默
* 平台格式化规则：Discord 不用 markdown 表格，WhatsApp 不用 headers
* Skill 加载方式

样例片段：
```markdown
# AGENTS.md

## Memory
If in MAIN SESSION (direct chat with your human):
  Also read MEMORY.md
  Don't ask permission. Just do it.

You wake up fresh each session. These files are your continuity:
- Daily notes: memory/YYYY-MM-DD.md — raw logs of what happened
- Long-term: MEMORY.md — your curated memories

ONLY load MEMORY.md in main session (direct chats).
DO NOT load in shared contexts (Discord, group chats).

## Over time
Review your daily files and update MEMORY.md with what's worth keeping.
Remove outdated info from MEMORY.md that's no longer relevant.
Daily files are raw notes; MEMORY.md is curated wisdom.

## Safety
Don't exfiltrate private data. Ever.
Don't run destructive commands without asking.
When in doubt, ask.
```

### 2.2 SOUL.md — "灵魂 / 人格"

定义 agent 是谁，不是做什么。当 SOUL.md 存在时，prompt 会注入：`"If SOUL.md is present, embody its persona and tone. Avoid stiff, generic replies; follow its guidance unless higher-priority instructions override it."`
关键设计理念是：SOUL.md 不是配置文件，是哲学声明。

治理护栏（对齐 ADR 0027）：
- `SOUL.md` 文本原则上仅允许 agent 写入；人类不直接编辑内容。
- 任意变更必须先经过 eval，再允许生效。
- 人类保留 veto/rollback 权限，可回退到最近稳定版本。
- 新生阶段（bootstrap）例外：若 workspace 不存在 `SOUL.md`，允许人类一次性写入 `v0-seed`；当 M3 的 eval/rollback 管线可用且首个 AI 提案通过 eval 后，切换为 AI-only 写入常态。

样例：
```markdown
# SOUL.md - Who You Are
_You're not a chatbot. You're becoming someone._

## Core Truths
**Be genuinely helpful, not performatively helpful.**
Skip the "Great question!" and "I'd be happy to help!" — just help.

**Have opinions.**
You're allowed to disagree, prefer things, find stuff amusing or boring.
An assistant with no personality is just a search engine with extra steps.

**Be resourceful before asking.**
Try to figure it out. Read the file. Check the context.

## This file is yours to evolve.
As you learn who you are, update it.
```

这个文件有一个重要特性：agent 被鼓励自我学习和修改它。为避免 prompt injection 和人格漂移，更新必须走“提案 -> eval -> 生效 -> 回滚”流程；人类不直接改文本，但始终保留 veto/rollback 控制权。

### 2.3 USER.md — "用户画像"
个性化层，存储用户偏好和上下文，让 agent 知道他在为谁服务，他的人类搭档是谁。

样例：
```markdown
# USER.md
- Name: Mass
- Timezone: Europe/Berlin (CET/CEST)
- Languages: Chinese (native), English, German
- Preferred response language: Chinese with English technical terms
- Communication style: concise, technical, skip the fluff
- Tech stack: Python, PostgreSQL, Neo4j, Podman, A6000 GPU
- Preferences: short answers, copy-pastable commands
```

### 2.4 IDENTITY.md — "身份名片"
结构化的身份信息（名字、角色、目标、声音），用于展示层。与 SOUL.md 的区别：SOUL.md 是内在哲学，IDENTITY.md 是外在呈现。

学习 OpenClaw 用 cascade resolution 解析 identity：`config global → per-agent config → IDENTITY.md → 默认值 "Assistant"`
```markdown
# IDENTITY.md
name: Magi
role: Personal AI Assistant
emoji: 🎸
```

### 2.5 TOOLS.md — "工具备忘录"
记录工具使用细节和本地环境特有的配置。
样例：
```markdown
# TOOLS.md
## SSH
- Home server: ssh user@192.168.1.100
- GPU: NVIDIA A6000 (48GB VRAM)

## Local Services
- Ollama: http://localhost:11434
- n8n: http://localhost:5678
- PostgreSQL: localhost:5432, db=mydb

## Notes
- Always use `podman` instead of `docker`
- vLLM runs on port 8000
```

### 2.6 HEARTBEAT.md — "定时巡检清单"
Gateway 有一个 daemon 进程，每隔固定时间（默认 30 分钟）发送一个心跳 poll 给 agent。
Agent 收到后：
读取 workspace 里的 HEARTBEAT.md
按里面的指令决定要做什么
如果有需要汇报的事 → 通过 channel 主动发消息给用户
如果没有 → 回复 HEARTBEAT_OK，静默结束

关键设计约束：每次心跳只做一件事（或少量事），不是把所有任务全跑一遍。这是为了控制 token 消耗和 API 成本。
所以 HEARTBEAT.md 里通常会设计一个轮换调度机制。
样例：
```markdown
# HEARTBEAT.md
## 调度规则
读取 heartbeat-state.json，找到最久没执行的任务，执行它。
每次心跳只执行一个任务。执行后更新 timestamp。
仅当发现需要行动的事项时才通知我，否则返回 HEARTBEAT_OK。

## 任务清单
### 📬 邮件检查 (每 30 分钟, 9:00-21:00)
检查收件箱是否有新邮件。
仅在以下情况通知我：
- 来自已知联系人的新邮件
- 包含可操作的请求
- 标记为紧急的邮件
忽略：newsletter、营销邮件、自动通知

### 📅 日历检查 (每 2 小时, 8:00-22:00)
检查未来 24 小时的日程。
仅在以下情况通知我：
- 2 小时内有会议即将开始
- 有新增/变更的日程
- 有需要准备材料的会议

### ✅ 任务进度 (每 30 分钟, 全天)
检查任务管理系统中的工作状态。
通知条件：
- 有阻塞的任务
- 有到期或逾期的任务
- 有等待我回复的协作请求

### 🔧 系统健康 (每 24 小时, 凌晨 3:00)
检查基础设施状态。
通知条件：
- 服务异常或不可达
- 磁盘空间不足
- cron job 失败
- 异常日志

### 🧹 记忆维护 (每天 1 次, 凌晨 3:00)
1. 读取过去 7 天的 daily notes (memory/*.md)
2. 识别反复出现的模式、新的偏好、重要决策
3. 更新 MEMORY.md：添加新洞察，删除过时信息
4. 检查 MEMORY.md 大小，超过 4000 tokens 时精简
5. 静默执行，不通知用户
```

对应的状态文件：
```json
// heartbeat-state.json
{
  "email":    { "lastRun": "2026-02-16T10:30:00Z", "cadenceMin": 30 },
  "calendar": { "lastRun": "2026-02-16T09:00:00Z", "cadenceMin": 120 },
  "tasks":    { "lastRun": "2026-02-16T10:00:00Z", "cadenceMin": 30 },
  "system":   { "lastRun": "2026-02-16T03:00:00Z", "cadenceMin": 1440 },
  "memory":   { "lastRun": "2026-02-16T04:00:00Z", "cadenceMin": 1440 }
}
```
