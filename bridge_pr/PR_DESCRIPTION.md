# PR Description: Hermem V6 Bridge — Sprint 2/3/4 cumulative

## 概要

Hermem V6 桥层在 Sprint 2/3/4 累计新增 4 个工具,Sprint 1 + Sprint 0.5 已在上游(`0ee4f8cbf` / `e98a1de0f` commit)。本 PR 累计 3 个 commit,改动只涉及 `plugins/memory/hermem/__init__.py` 单文件(+216 -14 行)。

## Sprint 对应

| Sprint | 主题 | 工具 | commit |
|---|---|---|---|
| 2 | 预测性召回 | `hermem_search_predictive` | `526c2f64e` |
| 3 | 可解释包装 | `hermem_explain_chunk` | `d3567f99d` |
| 3 | 反射 API | `hermem_reflect` | `d3567f99d` |
| 4 | 概念权重重排(隐式) | `search_with_tier` 自动 normalize | `197bd4016` |

## 改动详情

### Sprint 2 — `hermem_search_predictive` (74+ -6)
- 新增 `HERMEM_SEARCH_PREDICTIVE_SCHEMA`(query + top_k)
- `_impl_cache[predictor]`:lazy import,失败不影响其他工具
- `handle_tool_call` 分支:调 `impl.predictor.search_predictive`,返回 high/medium + predictor_used + predictor_metrics
- 注释同步:`__init__.py:1029` 方案 A 二分类调用注释从 2b 改 4b(2026-06-10 全面复核)

### Sprint 3 — `hermem_explain_chunk` + `hermem_reflect` (140+ -7)
- `HERMEM_EXPLAIN_CHUNK_SCHEMA`:chunk_id + query + use_llm,默认模板零 LLM 延迟,4b opt-in
- `HERMEM_REFLECT_SCHEMA`:query + top_k + write_l4 + session_id,4 路召回 + 4b 综合 + 可选 L4 写
- `_impl_cache[explain]` + `_impl_cache[reflect]`:lazy import
- `_v5_inject_chunk` 改调 `explain_chunk()` 模板路径,失败降级 V5 旧格式
- 桥层 SQL 适配:chunks 表无 similarity 列,explain 传 0.7 高置信默认
- `handle_tool_call` 2 个新分支

### Sprint 4 — `search_with_tier` 修复 (1 file 同步)
- 不直接改 `search_with_tier` 桥层接口(那是 impl 仓库逻辑)
- 但 Sprint 4 修了根因:`impl/vector_search.py:normalize_query()` 内置自动 normalize(所有调用方零改动受益)

## 依赖

- `phase3/impl/predictor.py`(hermem impl repo,`oxdh9019/hermem` commit `81ebc95` + `cbb0bf7` + `31d0783`)
- `phase3/impl/explain.py` + `explain_templates.py`(`oxdh9019/hermem` commit `86b2c86`)
- `phase3/impl/reflect.py`(同上)
- `phase3/impl/vector_search.py`(`oxdh9019/hermem` commit `226e277`,normalize_query 提到内置)
- `phase3/impl/embedding.py`(同上,修根因 B 零向量检测)
- `phase3/impl/concept_weight.py` + `reranker.py`(`oxdh9019/hermem` commit `2df6ad9`)

## 验证

- 273/273 pytest(impl 仓库)
- 桥层 e2e 端到端:explain_chunk 模板路径返回 1 句话解释;reflect 4 路召回 3 sources
- 5s timeout 让 predictive 真实激活(从 0% 兜底到 60.3% Recall@5)
- 3 commit 在 `oxdh9019/hermes-agent` 本地分支验证过

## 风险

- **Sprint 2 桥层 commit 未生产验证**:Sprint 2 决策修订(2b→4b)后续跟进的 3.0s → 5.0s timeout 修改在 `impl/predictor.py`,**桥层只关心 predictor 模块可用**,不影响 PR 完整性
- **桥层不修改其他插件**:只改 `plugins/memory/hermem/__init__.py` 一个文件
- **依赖的 impl 仓库**:需要用户也部署 hermem V6 impl 仓库(否则 predictor/explain/reflect 模块 import 失败,_impl_cache 跳过不崩)

## 关联

- 关联 impl 仓库:`oxdh9019/hermem` 6 个 V6 commit(Sprint 1 `0ee4f8cbf`/0.5 `e98a1de0f` 已在 PR 之前)

## Checklist

- [x] 单文件改动,合并 3 commit 为 1 个清晰主题
- [x] 工具 schema 含 description(LLM 友好)
- [x] 失败兜底(`_impl_cache` lazy import 失败 → 跳过)
- [x] 端到端实测(20 query 评测,Hit@5 70%)
- [x] 依赖文档(impl 仓库 commit 链)
- [x] Sprint 4 修根因同步(此 PR 隐式受益)
