# Cases README

`dev_docs/cases/` 用于记录运行期发现的可复现 case（含已修复/延后/不修复），保证问题上下文连续、可追溯。

## 规则

- append-only：只追加，不回写历史条目。
- 纠错也追加：新增 `Correction of <case-id>` 条目，不修改旧条目。
- 每条 case 必须有可核对证据（日志、SQL、截图、测试、commit）。
- 脱敏：禁止写入密钥、token、隐私原文。

## 建议字段模板

- `Case ID`: 唯一编号，建议 `RC-YYYY-MM-DD-XXX`
- `Date`: 发现时间（local）
- `Status`: `open | deferred | fixed | wont_fix`
- `Severity`: `P0 | P1 | P2 | P3`
- `Milestone`: 归属里程碑
- `Scenario`: 场景简述
- `Reproduction`: 最小复现步骤
- `Expected`: 预期行为
- `Actual`: 实际行为
- `Evidence`: 日志/SQL/截图/测试证据
- `Analysis`: 判因结论（系统层/模型层/数据层）
- `Decision`: 当前处置与时机
- `Follow-up`: 后续动作与责任归属

## 文件约定

- 运行期综合案例台账：`runtime_casebook.md`
- 如后续需要可按域拆分：`memory_casebook.md`、`gateway_casebook.md` 等
