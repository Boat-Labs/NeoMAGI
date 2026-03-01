# 0043-devcoord-direct-script-entrypoint-instead-of-just-wrapper

- Status: accepted
- Date: 2026-03-01

## 选了什么
- 将 M7 devcoord 控制面的正式运行时入口从 `just -> scripts/devcoord` 调整为 `scripts/devcoord` 直接入口。
- 保留 `scripts/devcoord` 作为协议语义唯一实现层；不允许 agent 或人类通过自由拼装 `bd` 写控制面状态。
- 为 `scripts/devcoord/coord.py` 增加结构化 payload 入口，优先使用 JSON file / stdin 传参，减少长参数串、shell quoting 和注入风险。
- `just` 继续保留为仓库常规开发任务入口（测试、lint、frontend 等），但不再作为 devcoord 控制面的强制中间层。
- 将共享控制面默认目录固定为仓库根 `.beads`；此前的 `.coord/beads` 仅视为早期草案路径，不再作为隐式 fallback。

## 为什么
- M7 Phase 1 实际实现和 live smoke 已暴露 `just` 在 devcoord 场景中的边际收益很低，而 task 文本、多参数命令在不同 shell / agent 环境下容易出现拆参与转义问题。
- devcoord 控制面主要由 agent 调用而非人类频繁手工操作；继续保留一层 `just` 只会增加一跳字符串传递与排障表面积，不能提高协议正确性。
- 真正需要稳定和 deterministic 的是 `scripts/devcoord`，不是 `just`。把入口收敛到脚本层，可以减少中间层漂移，同时保留 beads 后端可替换性。
- 直接调用 beads 仍不合适，因为 ACK 生效、gate 状态迁移、projection 重建、fail-closed 校验等协议语义不在 beads 内部，而在 `scripts/devcoord`。
- 当前 live 环境已经稳定运行在仓库根 `.beads`；如果默认值仍保留 `.coord/beads`，后续会留下第二套控制面被静默初始化的 split-brain 风险。

## 放弃了什么
- 方案 A：保持 `just -> scripts/devcoord` 不变，仅修补 quoting 细节。
  - 放弃原因：只能缓解表象，不能消除中间层带来的额外参数编排和注入面。
- 方案 B：让 agent / 人类直接写 `bd`。
  - 放弃原因：会把 deterministic 状态机重新暴露给模型和手工操作，违背 ADR 0042 的核心边界。
- 方案 C：完全取消命令行入口，只保留 Python API。
  - 放弃原因：当前协作仍需要终端环境中的可调用入口，纯 API 方案会抬高接入门槛。

## 影响
- M7 最新计划与控制面文档需要把运行链路改为 `LLM/human -> scripts/devcoord -> beads`。
- `justfile` 中的 devcoord recipes 需要删除，避免形成双入口。
- skill / prompt / review 文案中对 devcoord 的调用说明需要从 `just coord-*` 改为 `scripts/devcoord/coord.py`。
- 仓库级“常用开发任务优先使用 just”基线继续有效，但要明确 devcoord 是例外，不得回退到 `just` 包装层。
- 共享控制面路径的默认值需要与 live 环境对齐为仓库根 `.beads`；若检测到孤立的 `.coord/beads`，应 fail-closed 或显式 override，而不是静默复用。
