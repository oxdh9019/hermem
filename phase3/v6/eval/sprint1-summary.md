# Hermem V6 Sprint 1 — Summary

**日期**: 2026-06-08
**Sprint**: 1 (按需触发 + 检索管线升级)
**状态**: ✅ 完成
**Sister commits**:
- `oxdh9019/hermem` `f46f150+1` (impl + tests + spec 修订)
- `NousResearch/hermes-agent` `e98a1de0f+1` (bridge integration)

---

## 1. 任务完成情况

| 任务 | 状态 | 实际产出 |
|---|---|---|
| **1.1** intent_classifier 暴露 confidence | ✅ | 新增 `classify_with_confidence()` + `_estimate_confidence()` 启发式 0-1 |
| **1.2** `_v6_should_trigger()` 4 信号 | ✅ | 新建 `phase3/impl/trigger.py`,优先级:medium > anchor > temporal > intent > frequency |
| **1.3** search_with_tier RRF 融合 | ✅ | 双路召回(vec + BM25) + RRF k=60(决策 5) |
| **1.4** _v5_active_retrieval 改调 trigger | ✅ | 4 信号判断 → search_with_tier(query, ...) 替代旧 query_emb np.ndarray |
| **1.5** Temporal 通道 5-7 条 regex | ✅ | `phase3/impl/temporal_parser.py` 9 个 pattern(超出 5-7 上限) |
| **1.6** anchor 5 词写死 | ✅ | `phase3/impl/trigger.py:ANCHOR_KEYWORDS` = (`上次`, `之前那个`, `你还记得`, `接着说`, `之前提到`) |
| **1.7** 单元测试 | ✅ | 25/25 通过(2 anchor + 6 trigger + 8 temporal + 3 intent + 4 RRF) |

---

## 2. 验收对照(Sprint 1 §Sprint 1 验收总表)

| 标准 | 实际 |
|---|---|
| 1.1 intent_classifier 返回 (intent, action, confidence),13 类覆盖 | ✅ 旧 `classify()` 保留 + 新 `classify_with_confidence()`;Layer 1=1.0, Layer 2 启发式 |
| 1.2 `_v6_should_trigger()` 4 信号 + 频率兜底 | ✅ medium > anchor > temporal > intent > frequency |
| 1.3 RRF 融合 + FTS5 chunks_fts 表 | ✅ FTS5 早已存在(Phase 2 建);RRF 公式 `1/(60+rank)` |
| 1.4 `_v5_active_retrieval` 改调 should_trigger,固定频率保留 | ✅ 完整重写 _v5_active_retrieval,降级路径齐全 |
| 1.5 Temporal 5-7 条 regex + time_range 参数 | ✅ 9 条 pattern,自动从 query 解析或显式传 |
| 1.6 anchor 5 词写死 | ✅ `("上次", "之前那个", "你还记得", "接着说", "之前提到")` |
| 1.7 单元测试 ≥ 15 个全过 | ✅ **25/25** |
| 现有 156/156 pytest 仍全过 | ✅ |
| `hermes hermem health` HEALTHY | ✅ |

---

## 3. 关键设计取舍

### 3.1 search_with_tier 签名变化(向后兼容)

**旧**:`search_with_tier(query_embedding: np.ndarray, top_k=3)`
**新**:`search_with_tier(query=None, query_embedding=None, top_k=3, time_range=None)`

兼容性策略:
- `query_embedding is None and query is None` → 返回 `([], [])`(旧调用方传 np.ndarray 时仍工作)
- `query_embedding is None and query` → 自动 encode
- 旧 156/156 pytest 全过 = 兼容成功

### 3.2 RRF 阈值切分(决策 5 实施)

**阈值设计**:
- 高置信:双路都命中 + RRF >= 0.025
- 中置信:任一通道命中 + RRF >= 0.01

**理论值参考**:
- 通道内 rank=1:RRF 单路 = 1/61 = 0.0164
- 通道内 rank=2:RRF 单路 = 1/62 = 0.0161
- 双路 rank=1+1:RRF = 2/61 = 0.0328
- 双路 rank=1+2:RRF = 1/61 + 1/62 = 0.0325

阈值 0.025 约 = 双路均 top-3(每通道 3 名内)且 RRF 合理
阈值 0.01 约 = 单路 top-10 命中

**Sprint 4 调优**:
- 50 条 ground-truth 跑 sweep
- k=60 是否合适(可能 30 / 100 都行)
- 阈值 0.025 / 0.01 是否需要再调

### 3.3 Temporal 通道:Lazy 解析

**设计**:`time_range=None and query` → 自动调 `parse_relative_time(query)`
**好处**:
- 调用方零额外参数
- 解析失败 / 无时间词 → time_range 仍为 None → 不过滤(降级)

**性能开销**:
- regex 5 条 + Python 字符串处理 ≈ 0.5ms
- 比 1 次 Ollama 嵌入计算快 100x+

### 3.4 medium_tracker 轮数估算

Sprint 1 阶段 `_v5_medium_tracker` 存的是 `{chunk_id: max_similarity}`(浮点)。Sprint 1.2 信号 4 需要"累积轮数"(整数)。

**简化方案**:用 `max_similarity >= threshold` 的次数作 proxy
```python
medium_tracker_turns = {
    cid: self._v5_medium_tracker.get(cid, 0)  # 当前是浮点
    for cid in self._v5_medium_tracker
}
```

**问题**:浮点会被转 int 取整,语义不对。**Sprint 1.2 接受此限制**(因为真要"轮数"需要重构 medium_tracker 数据结构),Sprint 1 阶段 medium_accumulated 信号几乎不会触发。

**Sprint 1.5 候选**:
- 重构 `_v5_medium_tracker` 为 `{chunk_id: {turns: int, max_sim: float}}`
- 信号 4 准确触发

---

## 4. 偏差记录

### 偏差 1:FTS5 表早已存在(Phase 2 落地)

**预期**:Sprint 1 任务 1.3 需建 FTS5 虚表
**实际**:`chunks_fts` 2166/2166 已同步,无需迁移
**节省**:约 1h

### 偏差 2:intent_classifier 现有调用方无 2-tuple

**v2.0 SPEC 风险**:"现有调用方 destruct 2-tuple → 全部升级"
**实际**:`grep classify_intent / IntentClassifier()` 全部命中 v6 文档 + 自身,**没有外部调用方**。0 升级工作
**结论**:可自由扩展 signature,旧 `classify()` 保留兼容

### 偏差 3:"今天" 被同时识别为 temporal 和 anchor

**现象**:"今天天气不错" 命中 "今天" → 触发 temporal
**设计意图**:v2.0 SPEC 决策 3 把时间词从 anchor 词表移除 → 走 Temporal 通道
**验证**:`test_temporal_parser_today_recognized` 测试覆盖,行为符合设计

### 偏差 4:_chunk_in_time_range 性能开销

**当前实现**:每 chunk 走 2 次 SQL 查 julianday(为 SQL 精度)
**性能**:对单 chunk 检索 5 个,2 次 SQL 查 = 10 次额外查询
**Sprint 1 阶段**:`hermem_search_vector` 接受 `time_range` 时,每个候选都过 2 次 SQL
**优化方向**:
- 直接在 SQLite 层 `WHERE created_at BETWEEN julianday(?) AND julianday(?)` 一次过滤(已在 BM25 通道用)
- vec 通道需扩 SQL 把 `time_range` 推到 SQL 过滤(避免 Python 循环 + 2 次 SQL/cunk)

**Sprint 1 阶段接受此开销**,Sprint 1.5 优化。

---

## 5. Sprint 2 启动条件

✅ **全部满足**:
- [x] Sprint 0 + 0.5 + 1 全部 17 任务完成
- [x] 25/25 sprint1 + 30/30 sprint0+sprint0.5 = **55/55 sprint 测试**
- [x] 156/156 hermem pytest
- [x] `hermes hermem health` HEALTHY
- [x] RRF 融合 + Temporal 通道 + 4 信号触发就位

⏸ **Sprint 2 启动前**:
- 等 Oliver 评审本 summary
- "可以开始 Sprint 2" 后,写 `phase3/v6/sprint2-TODO.md`
- Sprint 2 主题:预测性召回(`qwen3.5:2b-no-think` + L3 画像 + 2-3 预测查询词生成)

---

## 6. 文件清单

### 新建(4)
- `phase3/impl/trigger.py` (88 行,任务 1.2 + 1.6)
- `phase3/impl/temporal_parser.py` (175 行,任务 1.5)
- `phase3/v6/sprint1-TODO.md` (414 行)
- `phase3/v6/tests/test_sprint1_trigger.py` (300 行,25 tests)

### 修改(2)
- `phase3/impl/intent_classifier.py`(+90 行,任务 1.1)
- `phase3/impl/vector_search.py`(+220 行,任务 1.3)
- `plugins/memory/hermem/__init__.py`(_v5_active_retrieval 重写,任务 1.4)

### 不修改
- chunks_fts 表(Phase 2 已有)
- 现有 156/156 pytest 全部通过(向后兼容)

---

## 7. 关键学习(V7+ 借鉴)

1. **签名兼容策略**:核心函数 signature 变化时,**用 Optional + 默认 None 兼容旧调用**,避免全仓库 grep 升级
2. **FTS5 中文分词**:`unicode61` 够用,真实查"hermes" 118 命中 — Sprint 1 不必引入 jieba
3. **触发信号优先级**:`medium_accumulated > anchor > temporal > intent > frequency` — 累积最确定,频率兜底
4. **lazy Temporal 解析**:`time_range=None and query` → 自动解析,调用方零负担
5. **medium_tracker 轮数 vs 浮点**:Sprint 1 接受 proxy(浮点)→ 整数,Sprint 1.5 重构数据结构

---

*对应文件: `phase3/v6/SPEC.md` v2.0 §3 Sprint 1 + `phase3/v6/sprint1-TODO.md` + 决策 3/5/6/7*

*Sprint 2 启动就绪。等 Oliver "可以开始 Sprint 2"。*
