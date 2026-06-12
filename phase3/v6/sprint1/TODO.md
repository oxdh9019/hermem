# Hermem V6 Sprint 1 TODO:按需触发 + 检索管线升级

**版本**: v2.0
**日期**: 2026-06-08
**状态**: Sprint 0 + 0.5 全绿,启动 Sprint 1
**依据**:
- `phase3/v6/SPEC.md` v2.0 §3 Sprint 1
- `phase3/v6/TODO.md` v2.0 §Sprint 0.5 后续 Sprint 占位
- `archive/v1.0-v1.3-SPEC.md` 借鉴 5/6/7 决策原始论证
- Sprint 0.5 `f46f150` + Sprint 0 `7993e38` 已落地基础设施

> **范围声明**:本 TODO 覆盖 Sprint 1 全部 7 任务。Sprint 2-4 启动时另立 `sprint{N}-TODO.md`。

---

## Sprint 1 任务总览

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|---|
| **1.1** | P0 | `intent_classifier` 路径加 `confidence` 字段返回 | `phase3/impl/intent_classifier.py` | 半天 |
| **1.2** | P0 | `_v6_should_trigger()` 综合 4 信号 | `phase3/impl/trigger.py`(新)| 半天 |
| **1.3** | P0 | `search_with_tier` 重构:加 BM25 + RRF 融合(决策 5) | `phase3/impl/vector_search.py` | 半天 |
| **1.4** | P0 | `_v5_active_retrieval()` 改调 `_v6_should_trigger()`,固定频率保留兜底 | `plugins/memory/hermem/__init__.py` | 1 h |
| **1.5** | P0 | Temporal 通道:`hermem_search(time_range=None)` + 5-7 条中文 regex(决策 6) | `phase3/impl/vector_search.py` + `phase3/impl/temporal_parser.py`(新)| 半天 |
| **1.6** | P0 | anchor 5 词写死(决策 3) | `phase3/impl/trigger.py` | 30 min |
| **1.7** | P0 | 单元测试:4 信号触发 + RRF 排序 + Temporal 过滤 + anchor 命中 | `phase3/v6/tests/test_sprint1_trigger.py` | 1 天 |

**Sprint 1 总预估**:2-3 天(一人)

---

## 已拍板决策(本 Sprint 直接落地)

| 决策 | 内容 | 落地位置 |
|---|---|---|
| **3** anchor 词典 | 5 词固定表:`"上次" / "之前那个" / "你还记得" / "接着说" / "之前提到"`(Sprint 1 v2.0 决策 3 修订) | `phase3/impl/trigger.py:ANCHOR_KEYWORDS` |
| **5** hybrid 融合 | RRF 公式 `rrf = 1/(60+rank_vec) + 1/(60+rank_bm25)`,未出现 doc 分数 0 | `phase3/impl/vector_search.py:search_with_tier_rrf` |
| **6** Temporal 通道 | 5-7 条中文 regex,不引入 dateparser 依赖 | `phase3/impl/temporal_parser.py:parse_relative_time` |

**v2.0 决策 3 备注**:Sprint 1 阶段 anchor 词典瘦身 5 词,时间类 anchor("上周" / "昨天做的")直接走 Temporal 通道(任务 1.5),不进 anchor 词表。

---

## Step 0:现状核查(改代码前必做)

```bash
# 1. intent_classifier 现有签名
grep -n "def classify\|return.*intent\|class Intent" ~/.hermes/projects/hermem/phase3/impl/intent_classifier.py

# 2. search_with_tier 现有结构
grep -n "def search_with_tier\|def.*search\|threshold=" ~/.hermes/projects/hermem/phase3/impl/vector_search.py

# 3. _v5_active_retrieval 在 bridge 哪里
grep -n "_v5_active_retrieval\|FREQUENCY" ~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py | head -10

# 4. SQLite FTS5 是否可用(BM25 通道)
sqlite3 ~/.hermes/memory/hermem.db "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts';"
```

**已知状态**(v0 验证):
- intent_classifier 13 类,当前返回 `(intent_label, action)` 元组
- search_with_tier 单一 vec 通道,高/中置信分层
- _v5_active_retrieval 每 3 回合触发(Sprint 1 后改为按需)
- SQLite FTS5 — **需要建** chunks_fts 虚表(Sprint 1 新增)

---

## Step 1:intent_classifier 暴露 confidence(任务 1.1)

**目标**:返回 `(intent_label, action, confidence)` 三元组,confidence 0-1。

**设计原则**(v2.0 SPEC 决策 — 借鉴 Memory Box 1.1 "LLM 不决策只生成"):
- LLM 负责生成 intent_label + action,**不**返回 confidence
- confidence 由 LLM logit 派生(若不可得,降级为 action 字符串匹配度)
- 不破坏现有 13 类标签

**实现位置**:`phase3/impl/intent_classifier.py`

**预计改动**:
```python
def classify(message: str, history: list = None) -> tuple[str, str, float]:
    """返回 (intent_label, action, confidence)."""
    # 现有逻辑生成 intent_label + action
    # 新增:confidence 计算
    # 1) LLM 路径:从 response.choices[0].logprobs 取
    # 2) 降级路径:基于 action 字符串匹配度 + 关键词命中数 → heuristic 0-1
    ...
```

**风险**:
- 13 类现有调用方可能 destruct 2-tuple → 全部升级为 3-tuple
- LLM logit 在 OpenAI/Claude API 多数不暴露 → 降级路径必须工作

**验证**:
- 单元测试:13 类各给 1 个标准消息 → confidence 0.7-1.0
- 模糊消息(无明确意图)→ confidence < 0.5
- 现有调用方兼容(grep `intent_classifier.classify` 用法 → 全部升级)

---

## Step 2:_v6_should_trigger() 4 信号(任务 1.2)

**目标**:新建 `phase3/impl/trigger.py`,提供 `_v6_should_trigger()` 函数,综合 4 信号。

**4 信号**(任一触发即返回 True + 触发来源):
1. **intent 置信度低**:`intent_classifier.classify()` 返回 `confidence < 0.7`
2. **anchor 关键词命中**:用户消息含 5 词 anchor 之一(决策 3)
3. **Temporal 关键词命中**:用户消息含时间词("上周"/"昨天"等)→ 走 Temporal 通道(决策 6,任务 1.5 接入)
4. **中置信累积**:`_medium_tracker` 中同一 chunk 连续 3 轮中置信但未注入

**接口**:
```python
def should_trigger(
    message: str,
    intent_confidence: float,
    medium_tracker: dict,
    turn_count: int,
) -> tuple[bool, str]:
    """返回 (should_trigger, source).

    source ∈ {'intent_low' / 'anchor_keyword' / 'temporal' / 'medium_accumulated' / 'frequency_fallback'}
    """
```

**降级**:
- 固定频率(每 3 回合)保留作为最后兜底 → 返回 `(True, 'frequency_fallback')`
- 4 信号全无 → 返回 `(False, None)`

**5 词 anchor**(决策 3):`["上次", "之前那个", "你还记得", "接着说", "之前提到"]`
**实现**:`any(kw in message for kw in ANCHOR_KEYWORDS)`

---

## Step 3:search_with_tier 重构为 RRF 融合(任务 1.3)

**目标**:`phase3/impl/vector_search.py:search_with_tier` 改为双路召回 + RRF 融合。

**新流程**:
```
1. vec 通道:bge-m3 cosine → top-K1
2. BM25 通道:SQLite FTS5 → top-K2
3. RRF 融合:每 doc 算 rrf_score = 1/(60+rank_vec) + 1/(60+rank_bm25)
   - 仅出现在 vec → 只有 1/(60+rank_vec)
   - 仅出现在 BM25 → 只有 1/(60+rank_bm25)
4. 按 rrf_score 降序 → top-K3
5. 高置信(rrf_score >= 阈值_high)直接注入
6. 中置信(rrf_score >= 阈值_medium)累积
```

**SQLite FTS5 表**(新):
```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61'
);
```

**触发器保持同步**:
```sql
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
```

**迁移脚本**:`phase3/impl/migrate_v6_sprint1_fts.py`(新)

**API 签名变化**:
```python
# 旧
def search_with_tier(query_emb, top_k=3) -> tuple[list, list]:
    """返回 (high_tier, medium_tier)"""

# 新
def search_with_tier(query, query_emb=None, top_k=3, time_range=None) -> tuple[list, list]:
    """双路召回 + RRF 融合。

    Args:
        query: 原始查询文本(BM25 用)
        query_emb: 预计算 embedding(vec 用;None 时内部计算)
        top_k: 每通道 top-K
        time_range: (start, end) 元组或 None(Sprint 1.5 Temporal 通道)
    """
```

**风险**:
- 现有 156/156 pytest 全部调旧 `search_with_tier(query_emb, ...)` → 签名变化需兼容或升级测试（2026-06-08 启动基线；2026-06-12 收尾为 273/273）
- FTS5 中文分词(unicode61 不完美,可后续优化)
- RRF k=60 需 sweep(写一个 50 条 ground-truth 测试脚本,Sprint 4 评测时调)

---

## Step 4:_v5_active_retrieval 改调 _v6_should_trigger(任务 1.4)

**目标**:`plugins/memory/hermem/__init__.py` 的 `_v5_active_retrieval` 不再用固定频率,改调 `_v6_should_trigger()`。

**改动**:
```python
def _v5_active_retrieval(self) -> None:
    """Sprint 1: 改为按需触发(4 信号),固定频率保留兜底。"""
    self._turn_count += 1

    # 1) 调 _v6_should_trigger
    intent_conf = ...  # 调 intent_classifier
    should, source = _v6_should_trigger(
        message=self._last_user_message,
        intent_confidence=intent_conf,
        medium_tracker=self._v5_medium_tracker,
        turn_count=self._turn_count,
    )

    if should:
        # 2) 执行检索(走 RRF 融合)
        chunks = search_with_tier(self._last_user_message, top_k=3)
        for c in chunks:
            self._v5_inject_chunk(c)
        # 3) 记录触发来源(给 recall_outcome.anchor_source 用)
        self._v6_last_trigger_source = source
```

**降级**:
- `_v6_should_trigger` 异常 → 回退到固定频率(`turn_count % FREQUENCY == 0`)
- intent_classifier 异常 → 用 `confidence = 1.0`(始终触发 anchor 检查 + 频率兜底)

---

## Step 5:Temporal 通道(任务 1.5)

**目标**:新建 `phase3/impl/temporal_parser.py`,5-7 条中文 regex 解析"上周"/"上个月"等。

**接口**:
```python
def parse_relative_time(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """返回 (start, end) 时间区间;None 表示无时间词命中。"""
```

**5-7 条 regex**(决策 6):
```python
_PATTERNS = [
    # 1. 上周 / 上个周
    (r"上周", lambda now: (now - timedelta(days=now.weekday() + 7), now - timedelta(days=now.weekday()))),
    # 2. 上个月 / 上月
    (r"上(个)?月", lambda now: ...),
    # 3. 昨天
    (r"昨天", lambda now: ...),
    # 4. 前天
    (r"前天", lambda now: ...),
    # 5. YYYY-MM 格式
    (r"\d{4}-\d{2}", lambda now: ...),
    # 6. Q1 2026 格式
    (r"[Qq][1-4]\s*\d{4}", lambda now: ...),
    # 7. N 天前
    (r"(\d+)\s*天前", lambda now: ...),
]
```

**集成**:`hermem_search(query, time_range=None)` — `time_range` 非 None 时:
- 触发 Temporal parser 解析 query(优先)
- 或调用方显式传 `(start, end)`
- SQLite 层 `chunks.created_at BETWEEN ?` 硬过滤(必须用 julianday,因为 chunks.created_at 是浮点)

**风险**:
- 5-7 条 regex 漏掉长尾时间词 → Sprint 1 阶段不补,Sprint 1.5 评估覆盖率
- 中文 + 英文混排("Q1 2026 上周做了")需要 multi-match + 优先级

---

## Step 6:anchor 5 词写死(任务 1.6)

**目标**:`phase3/impl/trigger.py:ANCHOR_KEYWORDS` 常量定义。

**5 词**(决策 3):
```python
ANCHOR_KEYWORDS = ("上次", "之前那个", "你还记得", "接着说", "之前提到")
```

**触发逻辑**:
```python
def _has_anchor(text: str) -> bool:
    return any(kw in text for kw in ANCHOR_KEYWORDS)
```

**集成**:任务 1.2 `_v6_should_trigger()` 内调用。

**v2.0 决策 3 备注**:
- 时间类 anchor("上周" / "昨天做的")**不**进 anchor 词表
- 直接由任务 1.5 Temporal 通道处理
- anchor 词表只保留"显式追问历史"语义

---

## Step 7:单元测试(任务 1.7)

**测试文件**:`phase3/v6/tests/test_sprint1_trigger.py`

**测试维度**(目标 ≥ 15 个 test):
1. **anchor 命中**(5 个,每个词 1 个测试)
2. **Temporal 解析**(5-7 个,每个 regex 1 个)
3. **should_trigger 4 信号**(4 个,各 1 个边界用例)
4. **RRF 融合**(3 个:双路命中 / 仅 vec / 仅 BM25)
5. **search_with_tier 新签名**(2 个:含 query_emb / 不含)
6. **Temporal 过滤**(2 个:区间内 / 区间外)

**关键回归测试**:
- 现有 156/156 pytest 必须全过（2026-06-08 启动基线；2026-06-12 收尾为 273/273）
- 30/30 sprint0+sprint0.5 测试必须全过
- 任何 signature break 都视为 P0

---

## Sprint 1 验收总表

- [ ] **1.1** intent_classifier 返回 (intent, action, confidence),13 类覆盖
- [ ] **1.2** `_v6_should_trigger()` 4 信号 + 频率兜底
- [ ] **1.3** RRF 融合 + FTS5 chunks_fts 表
- [ ] **1.4** `_v5_active_retrieval` 改调 should_trigger,固定频率保留
- [ ] **1.5** Temporal 5-7 条 regex + time_range 参数
- [ ] **1.6** anchor 5 词写死
- [ ] **1.7** 单元测试 ≥ 15 个全过
- [ ] 现有 156/156 pytest 仍全过（2026-06-08 启动基线；2026-06-12 收尾为 273/273）
- [ ] `hermes hermem health` HEALTHY
- [ ] `phase3/v6/eval/sprint1-summary.md` 追加

---

## 风险与回滚

| 风险 | 严重度 | 缓解 |
|---|---|---|
| intent_classifier 现有调用方不兼容(2-tuple → 3-tuple) | 高 | grep 所有调用点,统一升级;测试覆盖 |
| RRF k=60 是否合适 | 中 | Sprint 4 评测 sweep;Sprint 1 接受"先用着" |
| FTS5 中文分词不够好 | 中 | unicode61 起步,Sprint 1.5 评估,必要时换 jieba |
| Temporal regex 漏掉长尾 | 低 | Sprint 1 先覆盖 80% 中文用例 |
| 行为闭环数据不足(Sprint 0.5 才 0 行)| 低 | Sprint 1-3 跑 30+ 天;Sprint 4 评测时数据就位 |

**整体回滚**:Sprint 1 涉及文件 ≤ 10 个,1-2 个 commit revert 即可。

---

## 后续 Sprint 占位

| Sprint | 主题 | 启动条件 |
|---|---|---|
| Sprint 2 | 预测性召回(`qwen3.5:4b-no-think`,2026-06-10 决策 8 修订)| Sprint 1 全绿 + RRF 调优有 ground truth |
| Sprint 3 | 可解释包装 + reflect API(决策 7) | Sprint 2 全绿 |
| Sprint 4 | 评测框架 + 排序权重增强(50/450 split) | Sprint 3 全绿 + 30+ 天 recall_outcome 数据(≥ 100 条) |

---

*对应 SPEC: `phase3/v6/SPEC.md` v2.0 §3 Sprint 1 + 决策 3/5/6*
