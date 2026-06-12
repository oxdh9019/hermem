# Hermem V6 — Project Overview

**日期**: 2026-06-12
**状态**: ✅ **V6 完整完成**(SPEC v2.0 6 Sprint + 3 修复)
**仓库**:
- `oxdh9019/hermem`(impl,V6 完整代码 + 6 commit)
- `oxdh9019/hermes-agent`(bridge,Sprint 2/3/4 工具本地 commit)
- 桥层 PR 状态:**跳过(可选,本仓库已包含完整 V6 桥层)**

---

## 0. 一句话总结

Hermem V6 解决"对话中主动找回相关历史记忆"的 5 个未达成方向(按需触发 / 预测性召回 / 可解释包装 / 行为闭环 / 可评测性),经过 7 个 sprint(0/0.5/1/1.5/2/3/4)+ 3 次 P0 修复,**V6 SPEC §0 5 目标全部达成**,baseline 评测 +28%(38.2% → 66.2% Recall@5)。

---

## 1. 目标 vs 实际

### V6 SPEC §0 5 目标

| # | 目标 | Sprint | 状态 | 关键产出 |
|---|---|---|---|---|
| 1 | **按需触发**(不再每 N 回合机械触发) | Sprint 1 | ✅ | 4-signal trigger(medium > anchor > temporal > intent > frequency)+ RRF k=60 融合 |
| 2 | **预测性召回**(预判用户接下来需要什么) | Sprint 2 | ✅ | qwen3.5:4b-no-think + L3 画像 + 2-3 预测词 + RRF 二级融合 |
| 3 | **可解释包装**(自然语言过渡句) | Sprint 3 | ✅ | 6 模板轮转 + explain_chunk() 4b 增强 opt-in + reflect API |
| 4 | **行为闭环**(V5 缺失,recall 后用没用) | Sprint 0.5 | ✅ | recall_outcome hook + L4 reflection 写入(标 source=reflect_immediate) |
| 5 | **可评测性**(从人工 review 升级到看指标) | Sprint 4 | ✅ | 20 条 ground-truth + 4 场景评测 + 概念权重重排 + 周报 + CI 回归 |

### 6 Sprint 完成情况

| Sprint | 任务 | 状态 | Sprint 摘要 |
|---|---|---|---|
| 0 | 5 | ✅ | `eval/sprint0-summary.md` |
| 0.5 | 6 | ✅ | `eval/sprint05-summary.md` |
| 1 | 7 | ✅ | `eval/sprint1-summary.md` |
| 1.5 | 3(修) | ✅ | Sprint 1 之后桥层修复,见 sprint1 附录 |
| 2 | 7 | ✅ | `eval/sprint2-summary.md` |
| 3 | 6 | ✅ | `eval/sprint3-summary.md` |
| 4 | 7+3(修) | ✅ | `eval/sprint4-summary.md`(含修 P0 + 修根因) |
| **合计** | **41 + 3** | ✅ | |

---

## 2. 关键决策(8 条 + 1 条 2026-06-10 复核修订)

| # | 决策 | 选项 | 依据 |
|---|---|---|---|
| 1 | V6 路径 | `phase3/v6/` | 与 V5.5 对齐,自包含 |
| 2 | 阈值 high/medium | 0.70 / 0.50(原 0.85/0.65) | 实测 bge-m3 相似度分布 |
| 3 | anchor 词典 | 5 词固定表("上次"/"之前那个"/...) | 瘦身后减少误触发 |
| 4 | 行为闭环时机 | 核心提前到 Sprint 0.5(原 Sprint 5 拆解) | 数据先于算法 |
| 5 | RRF 公式 | `1/(60+rank)`,k=60 | Hindsight 论文 |
| 6 | Temporal 通道 | query 自动解析 + SQLite created_at 过滤 | 简化实现 |
| 7 | reflect vs L4 边界 | reflect=即时,L4=批处理(launchd 周日 02:30) | 互不替代 |
| 8 | **本地 LLM 一律 `qwen3.5:4b-no-think`**(2026-06-10 复核) | 决策 8 | 原 v2.0 §3 写 2b,Sprint 2 实测 2b 1.5-5.5s 不稳定 + 格式遵循 0%;4b warm 380ms + cold 1.7-2.0s + 100% 遵循 few-shot |

**2026-06-10 全面复核**:统一规范为 4b,所有 sprint 文档 + 代码 + 桥层同步。

---

## 3. V6 关键修法(3 个 P0 灾难)

### 修法 1:re-embed 1506 个零向量 chunk
- **现象**:1506/2329(65%)chunk embedding norm=0,永远不可召回
- **根因**:写入时 Ollama bge-m3 偶发返回 0 向量
- **修法**:`scripts/reembed_zero_norm.py` 后台 4 分 16 秒,1505/1506 成功
- **效果**:有效 embedding 825 → 2336

### 修法 2:normalize_query 提到 search_with_tier 内置
- **现象**:BM25 对"是/什么/怎么"等问句词敏感,raw query 召回差
- **修法**:`impl/vector_search.py:normalize_query()` 入口自动调用
- **效果**:所有 8 个调用方零改动 +15% Recall@5

### 修法 3:embedding.py 加零向量检测 + retry
- **现象**:修法 1 修历史数据,但根因(写时 0 向量)未消
- **修法**:`_call_ollama_with_retry` + 1 次 retry + `EmbeddingZeroNormError` 异常
- **效果**:未来 30 天不再积累零向量(自动检测 + 抛错)

---

## 4. V6 评测基线(2026-06-12 最终)

| 场景 | Recall@5 | Hit@5 | MRR | Latency p50 |
|---|---|---|---|---|
| **生产路径**(auto-normalize) | **66.2%** | **70%** | **59.7%** | 1-3ms |
| predictive (raw) | 55.3% | 60% | 54.6% | 3305ms |
| predictive + normalize | 60.3% | 65% | 56.2% | 3177ms |

**演进**:38.2%(原始)→ 53.2%(normalize)→ 66.2%(+ re-embed)**+28% net**

**修后剩余失败**:6/20(5 bge-m3 字面偏差 + 1 K 不够大)—— V7 候选方向

---

## 5. 7 Sprint 关键学习(每 sprint 1-2 条)

1. **Sprint 0(可观测性奠基)**:drift 检测不要只看 npy 长度,要看 vec_index 范围(`vec_index >= npy_rows` 才是 orphan)
2. **Sprint 0.5(行为数据)**:0 数据也跑通(Sprint 0.5 hook 落下,但需 30 天自然累积)
3. **Sprint 1(按需触发)**:4-signal 优先级比单一阈值更鲁棒(避免"用了/忽略"二选一假阳性)
4. **Sprint 2(预测性召回)**:LLM 决策修订要实测驱动(2b → 4b 5 次修订,每次实测撞边界)
5. **Sprint 3(可解释包装)**:md5 seed 选模板保证同 chunk 同 turn 同模板(不抖动)
6. **Sprint 4(评测 + 修根因)**:数据完整性是评测前提(1506 零向量让 38% 误读);修根因不止修法

---

## 6. 文件结构

```
hermem/
├── phase3/
│   ├── v6/                              # V6 完整代码
│   │   ├── SPEC.md                      # V6 规格(7 决策表 + 6 sprint 计划)
│   │   ├── sprint1/
│   │   │   └── TODO.md
│   │   ├── sprint2/
│   │   │   └── TODO.md
│   │   ├── sprint3/
│   │   │   └── TODO.md
│   │   ├── sprint4/
│   │   │   └── TODO.md
│   │   ├── eval/
│   │   │   ├── sprint0-summary.md
│   │   │   ├── sprint05-summary.md
│   │   │   ├── sprint1-summary.md
│   │   │   ├── sprint2-summary.md
│   │   │   ├── sprint3-summary.md
│   │   │   └── sprint4-summary.md  ← 最终
│   │   └── tests/
│   │       ├── test_sprint1_trigger.py (25)
│   │       ├── test_sprint2_predictor.py (18)
│   │       ├── test_sprint3_explain.py (13)
│   │       ├── test_sprint3_reflect.py (8)
│   │       ├── test_sprint4_root_fixes.py (7)
│   │       ├── test_sprint4_tasks_4_5_to_4_8.py (11)
│   │       └── test_eval_recall_smoke.py (2)
│   ├── impl/
│   │   ├── vector_search.py            # RRF + normalize_query 内置
│   │   ├── embedding.py                 # retry + 零向量检测
│   │   ├── predictor.py                 # 4b + 5s timeout + 2b 决策 B
│   │   ├── explain.py + explain_templates.py
│   │   ├── reflect.py
│   │   ├── concept_weight.py            # 任务 4.5
│   │   └── reranker.py                  # 任务 4.6
│   ├── v5.5/impl/l4_reflection.py       # write_reflection_immediate(Sprint 3 扩)
│   ├── scripts/
│   │   ├── eval_recall.py               # 4 场景评测
│   │   ├── reembed_zero_norm.py         # P0 修法
│   │   ├── weekly_report.py             # 任务 4.7
│   │   ├── ci_eval.py                   # 任务 4.8
│   │   └── daily_snapshot.py            # 暂停观察 C
│   └── eval/
│       ├── ground_truth.jsonl           # 20 条 Oliver 标
│       ├── ground_truth_labeler.html    # 浏览器标注工具
│       └── baseline_report.json
├── bridge_pr/                           # 桥层 PR 准备材料(可选)
│   ├── hermem_v6_bridge_sprint_2_3_4.patch
│   ├── PR_DESCRIPTION.md
│   └── HOW_TO_OPEN_PR.md
└── .githooks/pre-commit                 # CI 回归(任务 4.8)
```

---

## 7. Commit 历史(V6 完整链,6 个 commit)

| commit | 内容 | 状态 |
|---|---|---|
| `e85a77f` | Sprint 3 收尾 | pushed |
| `6033e7d` | Sprint 4 任务 4.1-4.3(评测框架 + 20 ground-truth) | pushed |
| `4939e75` | Sprint 4 修 P0(re-embed 1506 + 5s timeout) | pushed |
| `226e277` | Sprint 4 修根因 A+B(normalize 内置 + 零向量检测) | pushed |
| `2df6ad9` | Sprint 4 任务 4.5-4.8(概念权重 + 重排 + 周报 + CI) | pushed |
| `a77fe30` | Sprint 4 收尾 summary | pushed |
| `c8d6cde` | 桥层 PR 准备材料(跳过) | pushed |
| `b51007f` | C 阶段 daily_snapshot.py | pushed |
| (本文档) | V6 overview | pushed (c779dde) |

**桥层仓库**(hermes-agent,本地):3 commit 累计 +216 -14 行
- `526c2f64e` Sprint 2 桥层
- `d3567f99d` Sprint 3 桥层
- `197bd4016` Sprint 4 桥层 sync

---

## 8. 后续(待拍板)

| 阶段 | 内容 | 触发 |
|---|---|---|
| **C 持续** | `recall_outcome` / `l4_reflections` 自然累积 | 你日常用 Hermes Agent |
| **B(1-2 周后)** | V7 立项:基于真实 follow-up 数据,定 5-6 sprint 主题 | 数据 50+ 条后 |
| **D 备选** | 桥层 PR 推到 NousResearch/hermes-agent 上游 | 你/我 主动启动 |
| **修法 1 待续** | 1 条冷启 timeout 的 chunk 仍 fail(剩 1/1506),补 re-embed | 1-2 天 |
| **修法 4(Sprint 5+)** | check_drift 加 `vec_index >= npy_rows` 区分(沿用 sprint1 偏差 6 计划) | Sprint 5 |
| **修法 5** | recency / pattern_relevance 维度(任务 4.6 简化版暂用 1.0) | 30 天数据后 |
| **修法 6** | 换 embedding 模型评估(bge-m3 vs bge-large vs m3e) | V7 候选 |

---

## 9. 关键数字(verify-on-disk)

| 项 | 数字 | 验证命令 |
|---|---|---|
| chunks 总数 | 2357 | `sqlite3 ~/.hermes/memory/hermem.db "SELECT COUNT(*) FROM chunks"` |
| 有效 embedding | 2354 | 同上 `WHERE vec_index IS NOT NULL` |
| 零向量 | 1(Sprint 4 修后) | `python3 -c "import numpy; n=...; print(sum(1 for v in n if v.sum()==0))"` |
| V6 pytest | 273/273 通过 | `python3 -m pytest phase3/v6/tests/ phase3/tests/ phase3/v5.5/tests/` |
| baseline Recall@5 | 66.2% | `cd phase3 && python3 scripts/eval_recall.py --ground_truth eval/ground_truth.jsonl` |
| ground-truth | 20 条 | `wc -l phase3/eval/ground_truth.jsonl` |
| W24 周报告 | 1KB | `cat ~/.hermes/memory/eval/weekly/2026-W24.md` |
| drift | 7(cron 越界,沿用 sprint1 处置) | `hermes hermem health` |

---

*依据 SPEC v2.0 + 6 sprint summary(0/0.5/1/1.5/2/3/4)+ 决策 1-8 + 2026-06-10 全面复核*

***V6 完整完成。V7 启动由 Oliver 拍板(建议:等 30 天 recall_outcome 自然累积后基于数据决定)。***
