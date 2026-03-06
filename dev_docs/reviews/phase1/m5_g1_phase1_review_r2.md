# M5 Gate 1 — Phase 1 Re-review (R2): P1-01 Fix Verification

**Date**: 2026-03-04
**Reviewer**: Tester (Claude Code)
**Gate**: m5-g1
**Target commit**: `9eb7946`
**Branch**: `feat/backend-m5-reliability`

## P1-01 原始问题

D2 (`_check_memory_index_health`) 和 DD3 (`_check_memory_reindex_dryrun`) 对 MEMORY.md 使用 `---` 分隔符计数，但 `MemoryIndexer.index_curated_memory()` 通过 `_split_by_headers()` 按 `## ` markdown headers 分隔。这导致 D2/DD3 报告 false WARN mismatch。

## 修复验证

### 1. `_count_curated_sections()` 实现 (`src/infra/doctor.py:38-60`)

新增 helper 函数逻辑：
- 按 `## ` headers 分隔 section（与 indexer `_split_by_headers()` 一致）
- `# ` 仅在无 current_title 时作为 title（与 indexer 一致）
- 只计数 body 非空的 section（与 indexer `index_curated_memory()` 的 `if not body.strip(): continue` 过滤一致）

### 2. 与 indexer `_split_by_headers()` 对比 (`src/memory/indexer.py:245-269`)

| 逻辑点 | indexer `_split_by_headers()` | doctor `_count_curated_sections()` |
|--------|------------------------------|-------------------------------------|
| 分隔符 | `line.startswith("## ")` | `line.startswith("## ")` |
| `# ` 处理 | `not current_title` 时作 title | `not current_title` 时作 title |
| 空 body 过滤 | `index_curated_memory`: `if not body.strip(): continue` | `"\n".join(body_lines).strip()` truthy check |
| 其他行 | append to `current_body` | append to `body_lines` |

**结论**: 逻辑完全对齐。

### 3. D2 和 DD3 对 MEMORY.md 使用新 helper

- **D2** (`_check_memory_index_health`, line ~210): `file_count += _count_curated_sections(content)` — 仅对 `MEMORY.md` 使用
- **DD3** (`_check_memory_reindex_dryrun`, line ~310): `count = _count_curated_sections(content)` — 仅对 `MEMORY.md` 使用
- `memory/` 目录下的 daily notes 继续使用 `re.split(r"^---$", ...)` — 正确保持不变

### 4. 新增测试覆盖

`tests/test_doctor.py` 新增以下测试：

**`TestCountCuratedSections` (6 tests)**:
- `test_empty`: 空字符串返回 0
- `test_h2_sections`: 3 个 `## ` section → 3
- `test_h1_plus_h2`: `# ` title + 2 个 `## ` section → 2（只计 body 非空的 section）
- `test_empty_body_skipped`: 空 body section 被跳过
- `test_no_headers`: 无 header 内容 → 1 section
- `test_triple_dash_not_counted`: `---` 在 body 中不作为分隔符

**`TestCheckMemoryIndexHealth` 新增 (2 tests)**:
- `test_memory_md_uses_header_split`: MEMORY.md 按 `## ` headers 计数
- `test_memory_md_with_dashes_in_body`: MEMORY.md body 中的 `---` 不膨胀计数

**`TestCheckMemoryReindexDryrun` 新增 (1 test)**:
- `test_memory_md_uses_header_split`: DD3 对 MEMORY.md 按 `## ` headers 计数

### 5. 测试结果

- `tests/test_doctor.py`: **44 passed** (0 failures)
- 全量回归: **723 passed**, 64 errors (全部为 integration/DB 环境依赖, 非本次修复相关)
- `just lint`: **All checks passed**

## 结论

**PASS** — P1-01 已修复。

`_count_curated_sections()` 正确镜像了 indexer 的 `_split_by_headers()` + 空 body 过滤逻辑。D2 和 DD3 对 MEMORY.md 使用新 helper，daily notes 保持 `---` 分隔。测试覆盖充分，全量通过。
