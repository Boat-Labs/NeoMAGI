# M7 Devcoord Skill Layering Draft

- Date: 2026-03-01
- Status: draft
- Scope: post-M7 follow-up; improve devcoord skill information layering without changing protocol semantics
- Driver: absorb useful structure from beads upstream Claude skills while preserving NeoMAGI repo-level SSOT and avoiding skill sprawl

## Context

Current NeoMAGI devcoord skills are effective but increasingly dense:

- [`devcoord-pm`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/SKILL.md)
- [`devcoord-backend`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-backend/SKILL.md)
- [`devcoord-tester`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-tester/SKILL.md)

beads upstream takes a different approach:

- a thin main skill entrypoint:
  - [beads/SKILL.md](https://github.com/steveyegge/beads/blob/main/claude-plugin/skills/beads/SKILL.md)
- explicit maintenance guidance:
  - [beads/CLAUDE.md](https://github.com/steveyegge/beads/blob/main/claude-plugin/skills/beads/CLAUDE.md)
- ADR and on-demand resources:
  - [beads/adr](https://github.com/steveyegge/beads/tree/main/claude-plugin/skills/beads/adr)
  - [beads/resources](https://github.com/steveyegge/beads/tree/main/claude-plugin/skills/beads/resources)

The useful lesson is not "create many more skills". The useful lesson is:

1. keep the main skill short and route-oriented
2. move deeper conceptual guidance into referenced resources
3. keep CLI syntax out of hand-maintained skill prose when a stronger source of truth already exists

## Decision

Adopt beads-style information layering for NeoMAGI devcoord skills, but do it with a smaller footprint:

- keep the existing three role skills
- do not add a new tree of sub-skills
- keep repository governance docs as SSOT
- extract repeated conceptual guidance into small shared resource docs under `dev_docs/devcoord/`
- reduce duplication inside the three `SKILL.md` files by turning them into thinner routing contracts

## Goals

- preserve current skill activation behavior and names
- reduce repeated protocol prose across PM/backend/tester skills
- make future updates cheaper and less drift-prone
- keep role-specific instructions clear without copying the whole protocol into each skill

## Non-Goals

- no protocol change
- no CLI or control-plane code change
- no new role model
- no migration from repo-level docs into `.claude/skills/` private copies
- no attempt to mirror the full beads `resources/` tree

## Proposed File Layout

Keep:

- [`.claude/skills/devcoord-pm/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/SKILL.md)
- [`.claude/skills/devcoord-backend/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-backend/SKILL.md)
- [`.claude/skills/devcoord-tester/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-tester/SKILL.md)

Add small shared resources:

- [`dev_docs/devcoord/skill_gate_flow.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/skill_gate_flow.md)
  - gate state machine
  - ACK effective rules
  - who may issue what
- [`dev_docs/devcoord/skill_recovery_and_preflight.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/skill_recovery_and_preflight.md)
  - recovery-check / state-sync expectations
  - `HEAD == target_commit`
  - worktree / branch constraints
- [`dev_docs/devcoord/skill_append_first_and_audit.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/skill_append_first_and_audit.md)
  - append-first
  - render -> audit -> gate-close ordering
  - reconciliation guards

Optional, only if the three resources above still leave duplication:

- [`dev_docs/devcoord/skill_write_path.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/skill_write_path.md)
  - `CLI -> CoordService -> IssueStore/bd`
  - structured payload preference
  - no direct `bd` writes

## Refactor Shape

### Phase A: Shared resource extraction

Create the three small `dev_docs/devcoord/skill_*.md` resource files.

Rules:

- each file should stay short
- each file should explain concepts, not restate every CLI argument
- each file should point back to:
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)
  - [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md)
  - [`beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md)

### Phase B: Thin the role skills

Update the three `SKILL.md` files so they:

- keep frontmatter, trigger description, and hard boundaries
- keep role-specific required actions and payload checklists
- remove repeated long-form explanations already covered by the new shared resources
- explicitly reference the shared resource docs

The result should be:

- PM skill stays the coordination router
- backend skill stays the backend write contract
- tester skill stays the review/write contract
- shared conceptual detail lives outside the three skill files

### Phase C: Maintenance note

Add one short maintenance note:

- [`dev_docs/devcoord/skill_maintenance.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/skill_maintenance.md)

This note should capture the beads lesson we actually want to preserve:

- main skill files should remain routing-focused
- repo-level governance docs remain SSOT
- CLI syntax belongs in code and help output, not duplicated everywhere in skill prose

This is intentionally lighter than beads' full `CLAUDE.md + adr + resources` maintenance structure.

## Why This Shape

### Worth learning from beads

- thin entry skill plus progressive disclosure
- explicit maintenance guidance
- conceptual resources instead of monolithic prompt prose

### Not worth copying directly

- a large sub-skill hierarchy
- duplicating repository governance inside skill-local docs
- adding many resource files before real duplication exists

## Comparison

| Topic | beads upstream | NeoMAGI proposed |
|------|----------------|------------------|
| Main skill | thin router | thin role router |
| Resource docs | large `resources/` tree | 3-4 focused `dev_docs/devcoord/skill_*.md` files |
| ADR around skill | yes | reuse repo ADRs; no dedicated skill ADR now |
| Maintenance guide | yes | one short maintenance note only |
| CLI syntax source | `bd prime` / `--help` | `scripts/devcoord/coord.py --help` plus SSOT docs |

## Risks

- over-thinning a skill can weaken activation reliability
- adding too many resource docs recreates the complexity we are trying to remove
- moving wording around can accidentally drop role-specific safety constraints

## Mitigations

- keep skill names and trigger descriptions stable
- do not move role boundaries or hard rules out of the role skill files
- validate with Claude Code CLI debug after edits
- keep the first pass to three shared resource docs only

## Acceptance

- all three skills remain named and triggered exactly as today
- each skill file is materially shorter and less repetitive
- role-specific payload and boundary rules remain explicit
- shared devcoord concepts are documented once, not repeated three times
- Claude Code CLI slash-trigger validation still shows skill activation

## Proposed Execution Order

1. create shared resource docs
2. slim `devcoord-pm`
3. slim `devcoord-backend`
4. slim `devcoord-tester`
5. add short maintenance note
6. validate slash-trigger behavior with CLI debug

## Output of This Draft

If approved, implementation should be one narrow documentation refactor:

- mostly `SKILL.md` and `dev_docs/devcoord/` edits
- no Python code changes unless validation reveals a real trigger regression
