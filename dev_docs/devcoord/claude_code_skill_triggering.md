# Claude Code Skill Triggering Notes

> 日期：2026-03-01
> 范围：NeoMAGI M7 teammate cutover

## 1. 结论

- Claude Code 的 skill 触发不能只看自然语言回答，必须看 CLI debug 日志。
- frontmatter 的 `description` 是自动发现的关键入口，必须同时写清楚：
  - 这个 skill 做什么
  - 什么时候必须使用
  - 用户/PM 可能说出的关键词
- 对 devcoord 这类高约束流程，显式提及 skill 名称仍是当前最稳的测试方式。
- 进一步实验表明，slash 形式 `/devcoord-<role>` 比只写 “Use the devcoord-<role> skill” 更稳定。

## 2. 官方口径

- Claude 官方文档明确要求 `description` 既描述能力，也描述使用时机。
- 官方建议用具体 trigger/context 测试 skill，而不是只写泛泛说明。
- Claude Code project skill 更新后，需要重启会话才能稳定加载新版本。

参考：
- `https://docs.claude.com/en/docs/claude-code/skills`
- `https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices`
- `https://support.claude.com/en/articles/12512198-how-to-create-custom-skills`

## 3. 本地 CLI 实验结果

环境：
- Claude Code CLI `2.1.63`
- 项目 skill 目录：`.claude/skills/`

显式触发实验：
- 命令提示中直接写 `Use the devcoord-tester skill for this repository`
- debug 日志出现：
  - `Loading skills from: ... project=[.../.claude/skills]`
  - `Loaded 10 unique skills ... project: 3`
  - `Metadata string for devcoord-tester`
  - `processPromptSlashCommand creating 3 messages for devcoord-tester`
  - `SkillTool returning 2 newMessages for skill devcoord-tester`

这说明：
- project skills 被 CLI 正常加载
- `devcoord-tester` 被当作实际 `Skill` 工具执行，而不是普通文本记忆

slash 触发实验：
- `/devcoord-pm Before gate-close, what exact verification sequence is required?`
- `/devcoord-backend After commit and push for the current phase, what exact devcoord action should backend take next?`
- debug 日志都出现了：
  - `Metadata string for devcoord-pm` / `devcoord-backend`
  - `processPromptSlashCommand creating ... messages for devcoord-pm` / `devcoord-backend`

这说明：
- slash 形式对 PM / backend 也能稳定进入技能处理链
- 在当前 CLI 版本下，slash 形式比自然语言显式提及 skill 名称更可靠

## 4. 当前已知黑盒点

- `--disable-slash-commands` 在本机 `2.1.63` 上不应被视为可靠的负对照。
- 在一次显式命名 skill 的实验中，disabled 模式下 debug 仍出现了：
  - `Metadata string for devcoord-tester`
  - `processPromptSlashCommand ... devcoord-tester`
  - `SkillTool returning ... devcoord-tester`
- 因此，当前更可靠的判据是：
  - 是否出现 `SkillTool returning ...`
  - 或是否出现 `processPromptSlashCommand ...`
  - 是否命中了预期 skill 名
  - 是否把 skill 正文作为 tool attachment 注入
- 另一个黑盒点是：只写 “Use the devcoord-<role> skill” 并不保证一定进入 `SkillTool`。在本地实验里，`devcoord-pm` / `devcoord-backend` 有过“回答正确但未进入 skill 处理链”的情况。
- 因此，显式 skill 名称是弱触发；slash skill 名称是当前更强的触发方式。

## 5. 对 devcoord skills 的写法要求

- `description` 必须带角色语境：`PM` / `backend teammate` / `tester teammate`
- `description` 必须带命令触发词：如 `open-gate`、`heartbeat`、`phase-complete`、`recovery-check`、`gate-review`
- skill 正文继续保留角色边界、必需动作、payload 约束
- 不把复杂状态机逻辑塞进 skill；skill 只负责调用规范和触发提示
- spawn prompt 对高约束流程优先直接写 `/devcoord-pm`、`/devcoord-backend`、`/devcoord-tester`

## 6. 推荐验证方法

使用：

```bash
scripts/devcoord/check_skill_activation.sh devcoord-tester \
  "Use the devcoord-tester skill for this repository. After gate-review, what exact next-step behavior is required?"
```

更强的 deterministic 测试方式：

```bash
claude -p --no-session-persistence \
  "/devcoord-tester After gate-review, what exact next-step behavior is required?"
```

关注输出：
- Claude 最终回答
- `debug_log=/tmp/...`
- `processPromptSlashCommand ... <skill-name>`
- `SkillTool returning ... skill <skill-name>`

若要试验当前 CLI 对 disabled 模式的行为，可加：

```bash
DISABLE_SLASH_COMMANDS=1 scripts/devcoord/check_skill_activation.sh devcoord-tester \
  "Use the devcoord-tester skill for this repository. After gate-review, what exact next-step behavior is required?"
```

但 disabled 模式目前只可作为行为观察，不可作为严格否证。

## 7. 社区经验摘要

- 社区普遍反馈：纯自然语言自动匹配不够稳定，尤其在 skill 多、任务复杂时更明显。
- 比较一致的经验是：
  - `description` 要具体，不要写成泛能力词
  - 明确的 trigger phrases 比抽象职责描述更有效
  - 对高风险流程，显式 slash 命令通常比自然语言自动匹配更可控
  - 需要重复验证，而不是一次命中就当作稳定
  - 对关键流程可用 hook /显式 skill 名称降低漏触发概率

这与 devcoord 当前“高约束、低容错”的场景一致，因此 M7 teammate cutover 阶段应优先追求可验证性，而不是追求完全隐式自动触发。

## 8. M7 Phase 3 Live Cutover Findings

- backend 和 tester 的 slash skill 已在真实 worktree 中命中并完成控制面写入，debug 判据仍以 `processPromptSlashCommand` 为准。
- 当前 slash prompt 重跑不是幂等的；同一 gate 下重复重试会追加重复 `ACK`、`RECOVERY_CHECK`、`PHASE_COMPLETE` 等事件。
- 因此，对 devcoord live 写操作的当前操作要求是：
  - 每个 slash prompt 只提交一次。
  - 若首次 run 未确认成功，先检查 beads/audit/debug，再决定是否补发。
  - 不要把“无输出”直接当作“未写入”。
- `render -> audit -> projection read` 必须串行执行。若把 `render` 与 `audit` 或文件读取并行跑，容易看到旧投影，形成假阳性的“gate 仍 open / projection 未更新”判断。
- tester 场景对 prompt 具体度更敏感；当目标是 `recovery-check` 或 `gate-review` 时，直接给出 payload/命令形状比只给自然语言意图更稳。
- `G-M7-P4` 的污染 drill 说明：即使 skill 已命中，若 teammate 运行在旧 worktree，Claude Code CLI 仍可能沿用错误的本地执行路径。最小有效护栏不是再加 wrapper，而是把 `git rev-parse HEAD == target_commit` 作为写前强制 preflight。
- `G-M7-P5` 的 clean drill 说明：在 fresh worktree + committed 代码下，`ACK`、`RECOVERY_CHECK`、`PHASE_COMPLETE` 的重复重放已不会追加新事件。当前“补发前先对账”的要求主要保留给未做去重的其他动作。
