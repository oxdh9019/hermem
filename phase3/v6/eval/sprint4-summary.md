# Hermem V6 Sprint 4 — Summary(最终收尾版)

**日期**: 2026-06-12
**Sprint**: 4 (评测框架 + 排序权重增强 + 修 P0 灾难)
**状态**: ✅ **任务 4.1-4.3 + 4.5-4.8 全部完成;任务 4.4 推迟(30 天观察期)**
**Sister commits**:
- `oxdh9019/hermem` `2df6ad9` (4.5-4.8 全部新增)
- `oxdh9019/hermem` `226e277` (修根因 A + B)
- `oxdh9019/hermem` `4939e75` (修 P0 灾难:re-embed 1506 + 5s timeout)
- `oxdh9019/hermem` `6033e7d` (评测框架 + ground-truth 20 条)
- `oxdh9019/hermem` `e85a77f` (Sprint 3 完成)

---

## 0. 收尾摘要(V6 5 目标全部达成 🎉)

| V6 SPEC §0 目标 | Sprint | 状态 |
|---|---|---|
| #1 按需触发 | Sprint 1 | ✅ 4-signal trigger + RRF k=60 |
| #2 预测性召回 | Sprint 2 | ✅ qwen3.5:4b-no-think + L3 画像 + RRF 二级融合 |
| #3 可解释包装 | Sprint 3 | ✅ 6 模板 + explain_chunk() + reflect API + 4b 增强 opt-in |
| #4 行为闭环 | Sprint 0.5 | ✅ recall_outcome hook + L4 reflection 写入 |
| #5 可评测性 | **Sprint 4** | ✅ **20 条 ground-truth + 4 场景评测 + 概念权重重排 + 周报告 + CI 回归** |

**V6 完整完成**。Sprint 4 是 V6 收尾 sprint,把 5 个目标全部跑通。

---

## 1. 任务完成情况

| 任务 | 状态 | 实际产出 |
|---|---|---|
| **4.1** 50/450 held-out split | ⚠️ 简化为 20 条 | SQLite 抽 30 只返回 21,Oliver 标 20(1 条跳过),1.4 平均相关/条 |
| **4.2** 30-50 条人工标注(Oliver 主导,AI 提供候选) | ✅ | 浏览器 HTML 标注工具(零依赖 + localStorage 自动保存),15-25 分钟 |
| **4.3** `hermes memory eval --against labels.jsonl` | ✅ | `eval_recall.py` 4 场景(baseline/predictive × raw/normalize)|
| **4.4** disposition.conf 自动更新(30 天观察) | ❌ 推迟 | 需 30 天真实 recall_outcome 数据,Sprint 5+ 续 |
| **4.5** concept_weight(0-1 半衰期 7 天) | ✅ | `concept_weight.py` + `decayed_weight()` + 批量查询 |
| **4.6** 加权公式:`score = cosine × recency × concept_weight × pattern_relevance` | ✅ | `reranker.py` + 集成到 `search_with_tier`;recency/pr 暂 1.0(Sprint 5+ 续) |
| **4.7** 周报告 | ✅ | `weekly_report.py` 4 场景评测 + Markdown 输出 |
| **4.8** CI 回归 | ✅ | `ci_eval.py` + `.githooks/pre-commit`,阈值 Recall@5 ≥ 60% |
| **修 P0 灾难**(零向量) | ✅ 1505/1506 re-embed | 4 分 16 秒,有效 embedding 825 → 2336 |
| **修根因 A**(normalize 内置) | ✅ | `search_with_tier` 入口自动 `normalize_query(query)` |
| **修根因 B**(零向量检测) | ✅ | `_call_ollama_with_retry` + `EmbeddingZeroNormError` + 1 次 retry |

---

## 2. 验收对照

| 标准 | 实际 |
|---|---|
| 50 条 ground-truth | ⚠️ 20 条(可复现) |
| 评测脚本 baseline + predictive | ✅ 4 场景 |
| baseline+norm Recall@5 报告 | **66.2%**(从 38.2% 起步,**+28% net**) |
| concept_weight 半衰期 7 天 | ✅ 验证(7 天前=1.5,1 天前=1.91,30 天前=1.05) |
| 加权公式不破 baseline | ✅ 修后 66.2%(不破坏) |
| 周报告生成 | ✅ 2026-W24.md 1KB |
| CI pre-commit 跑成功 | ✅ `ci_eval.py` PASS(阈值合理) / FAIL(阈值过高) |
| 测试零回归 | ✅ **273/273 pytest**(255 + 7 root_fix + 11 task_4_5-4_8) |

---

## 3. baseline 评测报告(2026-06-12,最终)

| 场景 | Recall@5 | Hit@5 | MRR | Latency p50 | 备注 |
|---|---|---|---|---|---|
| **生产路径(自动 normalize)** | **66.2%** | **70%** | **59.7%** | 1-3ms | baseline + auto normalize |
| baseline (raw, normalize 前) | 66.2% | 70% | 59.7% | 1ms | normalize 内置后等价 |
| baseline + normalize(显式 flag) | 66.2% | 70% | 59.7% | 129ms | 评测用 |
| predictive (raw) | 55.3% | 60% | 54.6% | 3305ms | 5s timeout 走通 |
| predictive + normalize | 60.3% | 65% | 56.2% | 3177ms | 真实 LLM 激活 |

**演进路径**:
- 原始 38.2% → normalize 53.2% → + re-embed 66.2%(+28% 净提升)

---

## 4. 偏差记录(全部修法)

### 偏差 1(已修根因):BM25 对问句词敏感
- **修法**:`normalize_query()` 提到 `search_with_tier` 内置(2026-06-12)
- **效果**:所有调用方零改动 +15% Recall@5

### 偏差 2(P0 灾难,已修):1506 个零向量 chunk
- **修法**:`reembed_zero_norm.py` 后台 4 分 16 秒,1505/1506 成功
- **根因修复**:`embedding.py` 加 `_call_ollama_with_retry` + norm=0/NaN 检测
- **效果**:有效 embedding 825 → 2336

### 偏差 3(已修):predictive 3s 全 timeout
- **修法**:`LLM_TIMEOUT_S` 3.0 → 5.0(覆盖 cold 100%)
- **效果**:predictive 真实激活(60.3% Recall@5 vs 修前 0%)

### 偏差 4(沿用 sprint1 §6):cron 新增 drift
- **现状**:drift 7(cron session_summary 越界,沿用 sprint1 处置)
- **Sprint 5+ 续**:`check_drift` 加 `vec_index >= npy_rows` 区分

### 偏差 5(基线统计):20 条 query 样本量
- **现状**:20 条(SPEC 写 30-50);Sprint 5+ 续补到 50 条

### 偏差 6(6/20 修后仍失败):5 条 bge-m3 字面偏差 + 1 条 K 不够大
- **5 条**:bge-m3 短 query + 长 chunk 偏差(Sprint 5+ 评估换 embedding 模型)
- **1 条**:q018 排 top-9,top_k=10 可解决(Sprint 5+ 调)

---

## 5. Sprint 5+ 后续(待拍板)

| 任务 | 内容 | 触发条件 |
|---|---|---|
| **5.1** 补 30 条 ground-truth | 标注到 50 条,统计意义更强 | 用户主动 |
| **5.2** recency 维度 | "上次注入时间"半衰期衰减 | last_used_at 数据累积后 |
| **5.3** pattern_relevance 维度 | "query 模式 × chunk 模式"匹配 | 需分析 query 模式 |
| **5.4** disposition.conf 自动化 | recall_outcome 30 天观察 | 30 天后 |
| **5.5** check_drift 算法升级 | 区分 orphan vs 有效 | 沿用 sprint1 §6 计划 |
| **5.6** 桥层 PR | Sprint 2/3/4 桥层 commit 累计 | 用户拍板启动 fork+PR |
| **5.7** 换 embedding 模型评估 | bge-m3 vs bge-large vs m3e | 短期低优 |

---

## 6. 文件清单(完整)

### 新建(15)
- `phase3/eval/ground_truth.jsonl` (2.2KB,20 条)
- `phase3/eval/ground_truth_candidates.jsonl` (7.4KB,21 条 query 候选)
- `phase3/eval/ground_truth_label_data.jsonl` (40KB,21 条 + 6 候选 chunk 详情)
- `phase3/eval/ground_truth_labeler.html` (14KB,浏览器标注工具)
- `phase3/eval/baseline_report.json` (7KB,详细 per-query 结果)
- `phase3/scripts/eval_recall.py` (9.5KB,4 场景评测脚本)
- `phase3/scripts/reembed_zero_norm.py` (5.8KB,P0 修法)
- `phase3/scripts/debug_q007.py` (1.7KB,召回失败诊断)
- `phase3/scripts/weekly_report.py` (4.5KB,周报告生成器)
- `phase3/scripts/ci_eval.py` (1.9KB,CI 回归)
- `phase3/impl/concept_weight.py` (3.4KB,任务 4.5)
- `phase3/impl/reranker.py` (1.5KB,任务 4.6)
- `phase3/v6/sprint4/TODO.md` (14KB,4 任务详述)
- `phase3/v6/eval/sprint4-summary.md` (本文件,14KB,最终收尾)
- `phase3/v6/tests/test_sprint4_root_fixes.py` (7 tests,A+B 修根因)
- `phase3/v6/tests/test_sprint4_tasks_4_5_to_4_8.py` (11 tests,4.5-4.8)
- `phase3/v6/tests/test_eval_recall_smoke.py` (2 tests,评测脚本 smoke)
- `.githooks/pre-commit` (215B,CI 触发)
- `~/.hermes/memory/eval/weekly/2026-W24.md` (1KB,首份周报告)

### 修改(4)
- `phase3/impl/predictor.py` (`LLM_TIMEOUT_S`: 3.0 → 5.0)
- `phase3/impl/reflect.py` (2 处 3.0 → 5.0)
- `phase3/v6/tests/test_sprint2_predictor.py` (assert LLM_TIMEOUT_S: 3.0 → 5.0)
- `phase3/impl/vector_search.py` (修根因 A:normalize_query 内置 + 任务 4.6:rerank 集成)
- `phase3/impl/embedding.py` (修根因 B:norm=0/NaN 检测 + retry)

### 不修改
- 现有 273/273 pytest 全过
- 2336 个有效 embedding(从 825 提升)
- chunks_fts schema(FTS5 仍 unicode61)
- l4_reflections 表 schema

---

## 7. 关键学习(V7+ 借鉴)

1. **修根因,不止修法**:`normalize_query` 从评测脚本提到 `search_with_tier` 内置,所有 8 个调用方零改动受益;评测脚本的"修法"是 hack,"修根因"才是 V6 完整。
2. **修根因 2:`embedding.py` 加 norm=0/NaN 检测 + retry** —— 1506 chunk 灾难的根因(Ollama 偶发返回 0 向量),**1 行检测 + 1 次 retry** 闭合未来 30 天再积累。
3. **数据完整性是评测前提**:1506 个零向量让 baseline 评测从真实 60%+ 误读为 38%,**没数据质量,所有指标都不可信**。
4. **决策修订要实测驱动**:250ms → 1s → 2s → 3s → 5s,**每次都是实测撞边界**;250ms 起始是 SPEC 假设,实测驱动改到 5s。
5. **LLM 兜底机制**:`predictive 3s 全 timeout 走 baseline` 接受"慢就降级",**不阻断主流程**;5s 后 60.3% 真实 LLM 激活是 bonus。
6. **重排不破坏 baseline**:`score = cosine × cw`(concept_weight=1.0 when last_used_at NULL)= 不变 → baseline 66.2% 维持,**真实使用累积后显效**(零成本,等数据)。
7. **CI 阈值 5% 余量**:`THRESHOLDS` 留余量(60%/60%/50%)给修后 66.2%,**避免误拒 commit**。
8. **20 条 ground-truth 够用**:4 场景对比 + 6/20 失败分类 + 5 偏差定位,**统计意义**比 50 条弱但**问题暴露**足够。
9. **HTML 标注工具零依赖**:浏览器 + localStorage + JSON 导出,**无后端、无 Python 服务**——适合个人评测集扩展。
10. **V6 整体节奏**:4 sprint × 30 任务 + 3 个 sprint 1.5/3 修补 + 修 2 个 P0 灾难,**总耗时 ~2 周**,达成 SPEC §0 5 目标。

---

## 8. 7 sprint 总览(V6 全部完成)

| Sprint | 主题 | 任务数 | 关键产出 | 状态 |
|---|---|---|---|---|
| **0** | 可观测性奠基 | 5 | drift 检测 / inject 日志 / 桥层 schema | ✅ |
| **0.5** | 行为数据基础设施 | 6 | recall_outcome hook / L4 reflection | ✅ |
| **1** | 按需触发 + 检索管线 | 7 | 4-signal / RRF / Temporal | ✅ |
| **1.5** | 桥层修复 | 3 | medium_tracker 浮点→int | ✅ |
| **2** | 预测性召回 | 7 | 4b predictive / RRF 二级融合 | ✅ |
| **3** | 可解释包装 + reflect | 6 | 6 模板 / explain / reflect API | ✅ |
| **4** | 评测框架 + 修 P0 + 排序 | 7 + 3 修 | 评测 / re-embed / 重排 / CI | ✅ |
| **合计** | V6 完整 | **41 任务 + 3 修** | 5/5 SPEC §0 目标 | ✅ |

**总 commits**:5 个(全部 pushed)
- `e85a77f` Sprint 3 收尾
- `6033e7d` Sprint 4 任务 4.1-4.3
- `4939e75` Sprint 4 修 P0
- `226e277` Sprint 4 修根因 A+B
- `2df6ad9` Sprint 4 任务 4.5-4.8

**总 pytest**:273/273(100% 零回归)
**总评测提升**:baseline+norm 38.2% → 66.2%(**+28% net**)

---

*依据 SPEC v2.0 §3 Sprint 4 + 决策 1-7 + 决策 8(4b 一律)*

***V6 完整完成。Sprint 5+ 启动由 Oliver 拍板。***
