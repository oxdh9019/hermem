# Hermem V6 — 总览(Overview)

**版本**: v2.0(Sprint 0 立项 → Sprint 4 收尾)
**日期**: 2026-06-12
**状态**: ✅ **V6 完整完成**

---

## 0. V6 SPEC §0 5 目标达成

| # | 目标 | Sprint | 关键产出 | 状态 |
|---|---|---|---|---|
| **1** | 按需触发 | Sprint 1 | 4-signal trigger(medium > anchor > temporal > intent > frequency)+ RRF k=60 | ✅ |
| **2** | 预测性召回 | Sprint 2 | qwen3.5:4b-no-think + L3 画像 + RRF 二级融合(显式 vs 预测) | ✅ |
| **3** | 可解释包装 | Sprint 3 | 6 模板(中文 4 + 英文 2)+ explain_chunk() + reflect API + 4b 增强 opt-in | ✅ |
| **4** | 行为闭环 | Sprint 0.5 | recall_outcome hook + L4 reflection 写入 + 桥层 medium_tracker 修 | ✅ |
| **5** | 可评测性 | Sprint 4 | 20 条 ground-truth + 4 场景评测 + 概念权重重排 + 周报告 + CI 回归 | ✅ |

---

## 1. 7 Sprint 总览

| Sprint | 主题 | 任务 | 状态 | 关键 commit |
|---|---|---|---|---|
| **0** | 可观测性奠基 | 5 | ✅ | `oxdh9019/hermem` early commits |
| **0.5** | 行为数据基础设施 | 6 | ✅ | `e85a77f` 前 |
| **1** | 按需触发 + 检索管线 | 7 | ✅ | `e85a77f` 前 |
| **1.5** | 桥层修复(medium_tracker 浮点 bug) | 3(修) | ✅ | sprint1 期间 amend |
| **2** | 预测性召回 | 7 | ✅ | `81ebc95` |
| **3** | 可解释包装 + reflect | 6 | ✅ | `86b2c86` |
| **4** | 评测框架 + 排序权重 | 7 + 3(修) | ✅ | `6033e7d` → `2df6ad9` |
| **合计** | | **41 任务 + 3 修** | ✅ | `a77fe30` 收尾 |

**总 commits**(Sprint 2-4 阶段):6 个全部 pushed

---

## 2. Sprint 4 关键修法(SPEC 没写但必修)

V6 SPEC v2.0 计划 6 sprint,但**实施中发现 3 个 P0 灾难**,Sprint 4 全部闭合:

| 灾难 | 现象 | 修法 | 效果 |
|---|---|---|---|
| **P0 零向量** | 1506/2329 (65%) chunk 嵌入 norm=0,永久不可召回 | `reembed_zero_norm.py` 后台 4 分 16 秒 + `embedding.py` retry 机制 | 有效 825 → 2336 |
| **修根因 A** | `normalize_query` 只在评测脚本生效,生产路径不受益 | 提到 `search_with_tier` 入口 | 8 调用方零改动 +15% Recall@5 |
| **修根因 B** | Ollama 偶发返回 0 向量 | `_call_ollama_with_retry` + `EmbeddingZeroNormError` | 1 次 retry 覆盖冷启 |

---

## 3. 评测演进(38% → 66%)

| 阶段 | Recall@5 | Hit@5 | MRR | 关键改动 |
|---|---|---|---|---|
| 原始 baseline (raw) | 38.2% | 40% | 22.9% | 起点 |
| + normalize | 53.2% | 55% | 29.2% | +15% (Sprint 4 决策 1) |
| + re-embed 1506 零向量 | **66.2%** | **70%** | **59.7%** | +13% (P0 灾难修复) |
| + 5s timeout(predictive 激活) | 60.3% | 65% | 56.2% | LLM 路径真实工作 |
| **修根因 A 后**(生产路径) | **66.2%** | **70%** | **59.7%** | **8 调用方零改动受益** |

**净提升 +28%**(38.2% → 66.2%)。

---

## 4. 关键架构决策(7 + 1 = 8)

| # | 决策 | 选项 | 理由 |
|---|---|---|---|
| 1 | V6 路径 | `phase3/v6/` | 与 V5.5 对齐,自包含 |
| 2 | 行为闭环时机 | **拆解,核心提前到 Sprint 0.5** | 数据先于算法 |
| 3 | anchor 词典 | 5 词固定 | 瘦身后覆盖典型 |
| 4 | (略) | — | — |
| 5 | RRF k | 60 | Hindsight 论文公式 15 |
| 6 | 阈值 | HIGH=0.70 / MEDIUM=0.50 | 0.85/0.65 实测太高,降下来 |
| 7 | reflect vs L4 | reflect 即时,L4 批处理 | 互不替代 |
| **8** | **本地 LLM 模型** | **`qwen3.5:4b-no-think` 一律** | 2026-06-10 全面复核(2b 不稳定) |

---

## 5. Sprint 5+ 候选(SPEC 范围外)

V6 完整收尾后,**V7 立项** 前可做:

| 候选 | 触发条件 | 预估 |
|---|---|---|
| 桥层 PR(Sprint 2/3/4 累计 600 行) | 现在 | 30-60 分钟 |
| 任务 4.4 disposition.conf 自动化 | 30 天真实 recall_outcome 数据 | 1 天 |
| 补 30 条 ground-truth(到 50) | 用户主动 | 半天 |
| recency 维度加权 | last_used_at 数据累积 | 半天 |
| pattern_relevance 维度 | 需分析 query 模式 | 1 天 |
| 换 embedding 模型(bge-large / m3e) | 6/20 失败根因持续 | 评估 1-2 天 |
| `check_drift` 升级(区分 orphan vs 有效) | 沿用 sprint1 §6 计划 | 1-2 小时 |
| V7 SPEC 立项 | 1-2 周生产数据后 | 1-2 小时 |

**优先级建议**(基于"短答案 + verify-on-disk"原则):
1. **本周**:桥层 PR(立即可见收益)
2. **持续**:观察生产数据(被动)
3. **1-2 周后**:基于 recall_outcome 数据决定 V7 方向

---

## 6. 文件导航

- **Spec 索引**:`phase3/v6/SPEC.md` v2.0(7 决策 + 5 目标)
- **Sprint summaries**:`phase3/v6/eval/sprint0/05/1/2/3/4-summary.md`
- **TODO 文档**:`phase3/v6/sprint{1,2,3,4}/TODO.md`
- **桥层**:`~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py`(Sprint 2/3/4 commit 本地,未 push)
- **测试**:`phase3/v6/tests/test_sprint{0,05,1,2,3,4}*.py`(273 tests)
- **评测**:`phase3/scripts/eval_recall.py` + `eval/ground_truth.jsonl` + `eval/weekly/`
- **CI**:`.githooks/pre-commit` + `phase3/scripts/ci_eval.py`

---

*文档定位:V6 完整叙事的"门面",从这页可索引到所有 Sprint 0-4 的细节。*
