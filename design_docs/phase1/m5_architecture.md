# M5 Architecture（计划）

> 状态：planned（触发式进入）  
> 对应里程碑：M5 运营可靠性  
> 依据：`design_docs/phase1/roadmap_milestones_v3.md`、M4/M6/M7 已完成后的当前实现基线

## 1. 技术目标
- 在 personal agent 的单机/轻部署形态下，补齐“能发现问题、能判断影响、能按步骤恢复”的最小运行可靠性闭环。
- 将 M5 明确收敛为：`preflight`、`doctor`、`readiness`、`backup/restore`、`恢复 runbook`。

## 2. 当前基线（输入）
- 启动阶段已具备若干 fail-fast 校验：
  - `workspace_path` 与 `workspace_dir` 一致性校验。
  - PostgreSQL 必连，schema 启动时 `ensure_schema()`。
  - active provider 必须已配置可注册。
  - Telegram 启用时会执行 `check_ready()`。
- 已具备结构化日志、统一错误语义、会话串行化、预算闸门、provider 路由、渠道 fail-fast 基线。
- `/health` 已存在，但目前仅表示进程存活，不表达依赖健康与降级状态。

实现参考：
- `src/gateway/app.py`
- `src/config/settings.py`
- `src/session/database.py`
- `src/gateway/budget_gate.py`
- `src/infra/logging.py`
- `.github/workflows/ci.yml`

## 3. M5 要解决的核心问题

### 3.1 抽象健康检查不够用
- 当前 `/health` 仅返回静态 `ok`，无法判断 DB、provider、Telegram connector、workspace 是否真实可用。
- 当前启动 fail-fast 分散在多处实现中，缺少统一的检查视图和证据输出。

### 3.2 已有恢复语义尚未形成统一闭环
- `SOUL.md` 已定义为 DB 的 projection，而非唯一真源。
- memory 检索索引可重建，但当前没有面向运维的统一 reindex/doctor 入口。
- provider 切换和 Telegram fail-fast 已有实现，但缺少统一恢复 runbook。

### 3.3 关键资产没有分层表达
- 当前设计没有明确区分“必须备份的真源数据”和“可重建的派生数据”。
- 若不先定义资产分层，后续 backup/restore 设计会混淆 scope、索引、projection 与审计语义。

## 4. 设计原则
- 轻运维优先：只建设个人使用场景真正需要的能力。
- 诊断与修复分离：`doctor` 默认只读，不自动修复。
- 先阻断，再诊断：启动路径采用阻断式 `preflight`；运行期采用可重复执行的 `doctor`。
- 区分真源与派生物：backup/restore 只围绕真源保证一致性；派生数据优先采用重建策略。
- 安全默认收紧：诊断能力默认本地可用，不向普通聊天用户或外部渠道暴露。

## 5. 资产分层与恢复语义

### 5.1 真源数据（必须保护）
- PostgreSQL 主数据：
  - `sessions`
  - `messages`
  - `soul_versions`
  - `budget_state`
  - `budget_reservations`
- workspace 文件真源：
  - `workspace/memory/*.md` daily notes
  - `workspace/MEMORY.md`
- 决策理由：`SOUL` 是受治理对象，要求版本状态机、审批/eval、rollback 与审计链，因此真源保持在 DB；`workspace/SOUL.md` 只作为运行时 projection（见 ADR 0036）。
- 决策理由：memory 更像可累积、可检查、可再组织的原始材料，因此真源保持在 workspace；PostgreSQL 主要承担检索、缓存与派生加速层（见 `design_docs/memory_architecture_v2.md`）。

### 5.2 Projection / 派生数据（允许重建）
- `workspace/SOUL.md`
  - 真源不是文件本身，而是 DB 中 active soul version。
  - 启动时允许按既有对账语义重写 projection。
- `memory_entries`
  - 用于搜索与 recall 的索引表，不是记忆真源。
  - 恢复策略优先为 reindex，而不是把它当作唯一不可丢资产。

### 5.3 恢复原则
- 真源优先恢复，再恢复 projection / index。
- 若 DB 与 projection 不一致：
  - `SOUL.md` 以 DB 为准。
  - `memory_entries` 以 workspace memory files 为准重建。

## 6. 目标架构

### 6.1 `preflight`
- 作用：服务启动时强制执行一次，决定是否允许进入 ready 状态。
- 特征：
  - 阻断式。
  - 输出结构化结果。
  - 对关键失败项 fail-fast。
- 检查项：
  - 配置完整性：
    - 必填 env/key 是否存在。
    - active provider 配置是否闭合。
    - Telegram 启用时 token/allowed user 配置是否合法。
  - 运行时路径：
    - `workspace_dir` / `memory.workspace_path` 一致。
    - 必要目录存在且可访问。
  - 数据库契约：
    - DB 可连接。
    - schema 正确。
    - 必要表存在。
    - 必要列存在且满足当前契约。
    - search trigger / budget tables / soul tables 存在。
  - connector readiness：
    - provider 至少有一个可用。
    - 启用的 Telegram connector 可完成 ready check。
  - 启动对账：
    - `SOUL.md` projection reconcile。

### 6.2 运行期 `doctor`
- 作用：在服务启动后、故障排查时或发布前，重复执行同一套健康诊断。
- 特征：
  - 默认只读。
  - 可在本地 CLI 调用。
  - 输出 `ok | warn | fail` 三态结果。
  - 每个检查项包含 `status / evidence / impact / next_action`。
- 模式：
  - `doctor`
    - 默认轻量，只做本地和 DB introspection。
  - `doctor --deep`
    - 显式 opt-in，可增加 connector 连通性探测、provider smoke check、memory reindex dry-run 等成本更高的检查。
- `SOUL` 一致性检查属于 `doctor` 必检项：
  - DB 中 active `soul_versions` 是否存在且唯一。
  - `workspace/SOUL.md` 是否存在。
  - `workspace/SOUL.md` 是否与 DB active version 一致。
  - `proposed / active / rolled_back / vetoed` 状态链是否存在明显异常。
- `doctor` 只报告，不修复；任何 reconcile、reindex、projection rewrite 或其他修复动作都应通过独立、显式、单用途的恢复命令执行。
- `repair` 在 M5 中仅作为职责边界概念存在，用于强调“诊断不等于修复”；M5 不实现通用 `repair` 入口或自动修复框架。

### 6.3 健康接口分层
- `liveness`
  - 只表达“进程活着”，保持极简。
- `readiness`
  - 表达“是否适合接流量”。
  - 必须综合 DB、provider、connector、关键 startup reconcile 结果。
- `doctor`
  - 不作为公开健康探针。
  - 只用于维护者诊断。

### 6.4 备份与恢复闭环
- 备份对象：
  - PostgreSQL 逻辑备份。
  - `workspace/` 下 memory 真源文件。
- 恢复顺序：
  1. 恢复 DB 真源。
  2. 恢复 workspace memory 文件。
  3. 启动服务并执行 preflight。
  4. 执行 `SOUL.md` reconcile。
  5. 执行 memory reindex。
- 目标：
  - 服务恢复步骤固定、可演练、可文档化。

## 7. 检查项分级

### 7.1 `FAIL`（阻断服务或判定不可接流量）
- DB 无法连接。
- active provider 未配置完整或未注册成功。
- 必要 schema / 关键表缺失。
- soul 真源表不可读。
- workspace 真源路径不可访问。
- 已启用 Telegram connector 认证失败。

### 7.2 `WARN`（服务可启动，但存在可靠性风险）
- memory search 索引缺失但可重建。
- projection 与真源存在漂移但已可自动 reconcile。
- budget 累计接近阈值。
- 可选 provider 不可用，但默认 provider 可用。
- 某些 deep-check 失败但不影响主链路。

### 7.3 `OK`
- 主链路必需依赖全部健康。
- 真源和 projection / index 状态一致，或可接受差异已被对账消除。

## 8. `doctor` 的安全边界

### 8.1 暴露边界
- 默认仅提供本地 CLI，不面向普通聊天会话开放。
- 不对 Telegram / WebChat 用户开放完整诊断能力。
- 若未来提供调用式入口，必须限定为 maintainer/admin-only。

### 8.2 输出脱敏
- 不回显 API key、bot token、DB password、完整 DSN。
- 只输出：
  - `configured / missing / invalid / unreachable`
  - 经过脱敏的 provider / connector / path 状态

### 8.3 默认只读
- `doctor` 默认不执行 schema 修复、不写 DB、不改文件。
- 修复动作必须单独走显式命令，如 `reindex` / `reconcile` / `restore`；不通过通用 `repair` 入口隐式聚合执行。
- `doctor` 与修复职责分离：前者负责诊断与证据输出，后者负责显式修复与恢复。

### 8.4 外部探测控制
- provider / connector deep-check 必须带超时。
- 默认不发送真实用户内容。
- 默认不做高成本模型调用；如需 smoke check，必须显式 opt-in。

### 8.5 审计与最小泄露
- 记录谁、何时运行了 `doctor`，以及摘要结果。
- 不把 workspace 文件内容、完整 SQL、敏感路径细节直接写入公开日志。

## 9. 非目标（技术层）
- 不建设 Prometheus/Grafana/Sentry/Kubernetes 等重型平台。
- 不把 `doctor` 设计成普通用户可直接调用的聊天工具。
- 不做“一键自动修复所有问题”。
- 不在 M5 实现通用 `repair` 调度器、`repair all` 入口或自动修复框架。
- 不把检索质量、模型效果调优等能力优化混入 M5 技术架构。
