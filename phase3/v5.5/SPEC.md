# Hermem V5.5 开发计划与测试方案

**版本**: v1.0
**日期**: 2026-05-27
**状态**: 规划中
**依据**: Hermem V5.5 版本规划（Oliver 评审）

---

## 目标

从"反应式修正"走向"主动反思与记忆冲突管理"，实现三个核心升级：

1. **L4 反思层** — 每日 Cron 主动归纳用户交互模式，调整宏观策略
2. **记忆冲突协商** — 自动检测并协商记忆矛盾，消除脏数据
3. **生物学启发的主动遗忘** — 睡眠巩固 + 主动降级，控制上下文噪声

---

## 现状评估

### V5 Phase A/B 已完成（截至 V5.1）

| 组件 | 状态 | 说明 |
|------|------|------|
| Phase A 向量检索 | ✅ | 1711 向量，1645 chunk，无 drift |
| Phase B 中置信注入 | ✅ | `_medium_tracker` 累积逻辑已实现 |
| `hermes memory health` CLI | ✅ | drift 检测 + 嵌入模型检查 |
| `hermes memory rebuild` CLI | ✅ | 幂等修复 |
| V5.1 tag → github | ✅ | 2026-05-27 |

### 数据库现状

```
~/.hermes/memory/hermem.db
├── chunks          # L1 主表（~1645 条）
├── dispositions    # L2（条件化 dispositions）
├── prediction_errors  # 预测误差记录
├── user_profile     # L3 用户画像（.md 文件）
└── pending_conflicts  # ❌ 不存在，需新建
```

---

## 一、L4 反思层（Meta-Cognition）

### 1.1 目标

新增每日 Cron 任务（02:00），读取前一天 `prediction_errors` 和 `correction detection` 记录，LLM 归纳生成元记忆描述，写入 `l4_reflections` 表，作为 system prompt 可选注入内容。

### 1.2 架构设计

```
02:00 Cron Job (cron_reflection.py)
    ↓
读取昨天 prediction_errors + correction_detection 记录
    ↓
LLM 归纳 → 元记忆描述
    ↓
写入 l4_reflections 表（chunk_type='l4_reflection'）
    ↓
下次会话 warmup 可选注入 → Agent 调整宏观策略
```

### 1.3 数据库改动

```sql
-- 新建 l4_reflections 表
CREATE TABLE l4_reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reflection_text TEXT NOT NULL,       -- LLM 归纳的元记忆
    source_errors   INTEGER DEFAULT 0,   -- 来源错误数
    confidence      REAL DEFAULT 0.5,    -- 本条 reflection 置信度
    created_at      REAL DEFAULT (julianday('now')),
    expires_at      REAL,                -- TTL：14 天后自动失效
    injected_count  INTEGER DEFAULT 0,    -- 被注入会话数
    last_injected_at REAL                -- 上次注入时间
);

-- 给 prediction_errors 加日期索引（优化 Cron 查询）
CREATE INDEX IF NOT EXISTS idx_pe_created ON prediction_errors(created_at);
```

### 1.4 新增文件

| 文件 | 职责 |
|------|------|
| `impl/l4_reflection.py` | L4 反思层核心逻辑：读取昨日 errors → LLM 归纳 → 写入 l4_reflections |
| `cron/cron_reflection.py` | 每日 02:00 Cron Job 入口 |
| `tests/test_l4_reflection.py` | L4 单元测试 |

### 1.5 cron_reflection.py 实现框架

```python
#!/usr/bin/env python3
"""
L4 反思层 Cron Job
运行时间: 每天 02:00
读取昨天的 prediction_errors，用 LLM 归纳元记忆并存储。
"""

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from impl.database import Database
from impl.config import LLM_MODEL, LLM_PROVIDER

def get_yesterday_errors(db: Database) -> list[dict]:
    """读取昨天的 prediction_errors"""
    rows = db.execute("""
        SELECT id, context, error_type, surprise_level, created_at
        FROM prediction_errors
        WHERE created_at >= julianday('now', '-1 day')
          AND created_at < julianday('now')
        ORDER BY surprise_level DESC
        LIMIT 50
    """).fetchall()
    return [dict(r) for r in rows]

def synthesize_reflection(errors: list[dict]) -> str:
    """用 LLM 从错误记录归纳元记忆"""
    if not errors:
        return None

    # 构造 prompt
    error_summary = "\n".join([
        f"- [{e['surprise_level']:.2f}] {e['error_type']}: {e['context'][:100]}"
        for e in errors
    ])
    prompt = f"""你是一个记忆分析专家。从以下预测错误记录中归纳出用户交互模式的元记忆描述。

要求：
- 用中文
- 50-150 字
- 直接描述，不要"根据分析"这类废话开头
- 重点：用户的偏好、习惯、期望（不是描述错误本身）

错误记录：
{error_summary}

元记忆："""

    # 调用 LLM（MiniMax-M2.7）
    from llm_client import call_llm
    response = call_llm(prompt, model=LLM_MODEL, provider=LLM_PROVIDER)
    return response.strip()

def main():
    db = Database()
    errors = get_yesterday_errors(db)

    if len(errors) < 3:
        print(f"昨天错误记录 {len(errors)} 条，少于 3 条，跳过反思")
        return

    reflection_text = synthesize_reflection(errors)
    if not reflection_text:
        print("LLM 归纳失败，跳过")
        return

    # 写入 l4_reflections，TTL=14 天
    db.execute("""
        INSERT INTO l4_reflections (reflection_text, source_errors, confidence, expires_at)
        VALUES (?, ?, ?, julianday('now', '+14 day'))
    """, (reflection_text, len(errors), min(len(errors) / 50, 1.0)))
    db.commit()
    print(f"L4 反思已写入: {reflection_text[:80]}...")

if __name__ == "__main__":
    main()
```

### 1.6 HermemMemoryProvider 注入接入

```python
# impl/provider.py HermemMemoryProvider 新增方法

def get_l4_reflections(self, max_count: int = 3) -> list[dict]:
    """获取活跃的 L4 reflection，供 warmup 注入"""
    rows = self.db.execute("""
        SELECT reflection_text, confidence, created_at
        FROM l4_reflections
        WHERE expires_at IS NULL OR expires_at > julianday('now')
        ORDER BY confidence DESC, created_at DESC
        LIMIT ?
    """, (max_count,)).fetchall()
    return [dict(r) for r in rows]

def mark_reflection_injected(self, reflection_id: int):
    """标记 reflection 已注入，更新计数"""
    self.db.execute("""
        UPDATE l4_reflections
        SET injected_count = injected_count + 1,
            last_injected_at = julianday('now')
        WHERE id = ?
    """, (reflection_id,))
    self.db.commit()
```

注入格式（system prompt 或 warmup）：
```
[系统元认知 - 置信度 0.82]
近期交互模式：用户在讨论代码实现时，更倾向直接看示例代码而非长篇解释。
建议调整：提供简短结论 + 可运行代码示例，而非理论分析。
---
```

### 1.7 验收标准

| 用例 | 验收条件 |
|------|---------|
| L4.1 | cron_reflection.py 独立运行不报错 |
| L4.2 | l4_reflections 表正确写入，TTL=14 天 |
| L4.3 | 少于 3 条 error 时跳过（不写入） |
| L4.4 | get_l4_reflections() 返回按 confidence 降序 |
| L4.5 | 新增 reflection 不影响现有 V5 Phase A/B 功能 |

---

## 二、记忆冲突协商机制

### 2.1 目标

在 L1 提取新事实时，与 L3 用户画像（user_profile.md）以及现有高置信 disposition 进行语义冲突检测。当相似度 > 0.75 且信息矛盾，生成 `pending_conflicts` 记录，下一轮对话主动询问用户。

### 2.2 架构设计

```
L1 新事实提取
    ↓
冲突检测（embedding 相似度 > 0.75 + 语义矛盾）
    ↓
生成 pending_conflicts 记录（不自动覆盖）
    ↓
下一轮对话主动询问用户
    ↓
用户确认 → 更新 disposition / 用户画像
用户否认 → 标记冲突为"已澄清"，不更新
```

### 2.3 数据库改动

```sql
-- 新建 pending_conflicts 表
CREATE TABLE pending_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    new_fact_text    TEXT NOT NULL,      -- 新提取的事实
    existing_fact_text TEXT NOT NULL,    -- 已有事实（冲突对象）
    similarity       REAL NOT NULL,      -- embedding 相似度
    conflict_type   TEXT NOT NULL,      -- 'disposition' | 'user_profile'
    existing_id     TEXT NOT NULL,       -- 冲突对象 ID（disposition id 或 profile 条目）
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'resolved_new' | 'resolved_existing' | 'dismissed'
    resolution_note TEXT,
    created_at      REAL DEFAULT (julianday('now')),
    resolved_at     REAL
);
```

### 2.4 新增文件

| 文件 | 职责 |
|------|------|
| `impl/conflict_resolver.py` | 冲突检测核心逻辑：加载已有画像 embedding → 新事实 embedding → 相似度计算 → 矛盾检测 |
| `tests/test_conflict_resolver.py` | 冲突协商单元测试 |

### 2.5 conflict_resolver.py 实现框架

```python
# impl/conflict_resolver.py
"""
记忆冲突检测与协商模块
当 L1 提取新事实时，检测是否与已有高置信 disposition 矛盾。
"""

import numpy as np
from pathlib import Path
from impl.database import Database
from impl.config import (
    VECTOR_FILE, VECTOR_DIM,
    CONFLICT_SIMILARITY_THRESHOLD,  # 0.75
)
from sentence_transformers import SentenceTransformer

def detect_conflicts(new_fact_text: str, min_similarity: float = CONFLICT_SIMILARITY_THRESHOLD) -> list[dict]:
    """
    检测新事实是否与已有 disposition/user_profile 冲突。
    返回: [{existing_fact, similarity, conflict_type, existing_id}, ...]
    """
    model = SentenceTransformer('BAAI/bge-small-zh')
    new_emb = model.encode(new_fact_text, normalize_embeddings=True).astype(np.float32)

    # 1. 加载已有高置信 disposition embedding
    db = Database()
    dispositions = db.execute("""
        SELECT id, condition_text, prediction_text, confidence
        FROM dispositions
        WHERE confidence >= 0.7
    """).fetchall()

    # 2. 加载 user_profile.md 已有条目 embedding
    profile_path = Path.home() / ".hermes" / "memory" / "user_profile.md"
    profile_entries = []
    if profile_path.exists():
        lines = profile_path.read_text().split("\n---\n")
        profile_entries = [{"id": f"profile_{i}", "text": e.strip()}
                          for i, e in enumerate(lines) if e.strip()]

    # 3. 构建候选 embedding 集合
    candidates = []
    for d in dispositions:
        candidates.append({
            "id": d["id"],
            "text": f"{d['condition_text']} {d['prediction_text']}",
            "type": "disposition",
            "confidence": d["confidence"]
        })
    for p in profile_entries:
        candidates.append({
            "id": p["id"],
            "text": p["text"],
            "type": "user_profile",
            "confidence": 0.8  # profile 默认置信度
        })

    if not candidates:
        return []

    # 4. 批量计算相似度
    vectors = np.load(VECTOR_FILE)
    # 取对应 embedding_index 的向量
    # ...（简化版：逐条计算，因为 candidate 数量通常不大）

    conflicts = []
    for c in candidates:
        c_emb = model.encode(c["text"], normalize_embeddings=True).astype(np.float32)
        sim = float(np.dot(new_emb, c_emb))  # 已 normalize，直接 dot

        if sim > min_similarity:
            # 5. 语义矛盾检测（简单规则 + LLM 判断）
            if _is_contradictory(new_fact_text, c["text"]):
                conflicts.append({
                    "new_fact_text": new_fact_text,
                    "existing_fact_text": c["text"],
                    "similarity": sim,
                    "conflict_type": c["type"],
                    "existing_id": c["id"],
                })

    return conflicts

def _is_contradictory(text_a: str, text_b: str) -> bool:
    """
    简单矛盾检测：检测"A 喜欢 X" vs "A 讨厌 X" 类模式。
    复杂情况委托 LLM。
    """
    # 否定词检测
    negations = ["不", "没", "无", "不是", "讨厌", "反感", "拒绝", "避免"]
    positive  = ["喜欢", "爱", "倾向", "偏好", "愿意", "接受"]

    has_neg_a = any(w in text_a for w in negations)
    has_pos_a = any(w in text_a for w in positive)
    has_neg_b = any(w in text_b for w in negations)
    has_pos_b = any(w in text_b for w in positive)

    # 矛盾模式：(喜欢 vs 讨厌) 或 (倾向 vs 避免)
    if has_pos_a and has_neg_b:
        return True
    if has_neg_a and has_pos_b:
        return True
    if has_pos_a and has_pos_b and text_a != text_b:
        return False  # 暂时搁置，用 LLM 判断
    if has_neg_a and has_neg_b and text_a != text_b:
        return False

    # 超过 5 个词时，用 LLM 判断
    if len(text_a.split()) > 5 or len(text_b.split()) > 5:
        return _llm_contradiction_check(text_a, text_b)

    return False

def _llm_contradiction_check(text_a: str, text_b: str) -> bool:
    """LLM 语义矛盾判断（高成本，仅复杂场景用）"""
    prompt = f"""判断以下两条陈述是否语义矛盾（是/否）：

A: {text_a}
B: {text_b}

回答格式：仅回答"是"或"否"
"""
    from llm_client import call_llm
    response = call_llm(prompt, model="MiniMax-M2.7", provider="minimax-cn")
    return "是" in response[:2]

def create_pending_conflict(conflict: dict, db: Database):
    """将冲突写入 pending_conflicts 表"""
    db.execute("""
        INSERT INTO pending_conflicts
        (new_fact_text, existing_fact_text, similarity, conflict_type, existing_id)
        VALUES (?, ?, ?, ?, ?)
    """, (
        conflict["new_fact_text"],
        conflict["existing_fact_text"],
        conflict["similarity"],
        conflict["conflict_type"],
        conflict["existing_id"]
    ))
    db.commit()

def get_pending_conflicts(db: Database) -> list[dict]:
    """获取所有待处理冲突"""
    rows = db.execute("""
        SELECT * FROM pending_conflicts WHERE status = 'pending' ORDER BY similarity DESC
    """).fetchall()
    return [dict(r) for r in rows]

def resolve_conflict(db: Database, conflict_id: int, resolution: str, note: str = None):
    """
    解决冲突：resolution = 'resolved_new' | 'resolved_existing' | 'dismissed'
    """
    db.execute("""
        UPDATE pending_conflicts
        SET status = ?, resolution_note = ?, resolved_at = julianday('now')
        WHERE id = ?
    """, (resolution, note, conflict_id))
    db.commit()
```

### 2.6 协商交互流程（provider.py 接入）

```python
# HermemMemoryProvider._detect_and_raise_conflicts() 新增

def _detect_and_raise_conflicts(self, new_fact_text: str):
    """检测到冲突时，返回询问消息或 None"""
    conflicts = detect_conflicts(new_fact_text)
    if not conflicts:
        return None

    for conflict in conflicts:
        create_pending_conflict(conflict, self.db)

    # 生成用户询问（取相似度最高的）
    top = conflicts[0]
    return (
        f"我注意到您之前提到「{top['existing_fact_text'][:50]}」，"
        f"现在又提到「{top['new_fact_text'][:50]}」。"
        f"这两者似乎有些出入——我应该以哪个为准？"
    )
```

### 2.7 验收标准

| 用例 | 验收条件 |
|------|---------|
| CR.1 | `detect_conflicts()` 对"喜欢A"vs"讨厌A"返回冲突 |
| CR.2 | 相似度 < 0.75 不触发冲突 |
| CR.3 | pending_conflicts 表正确写入 |
| CR.4 | 协商消息正确生成 |
| CR.5 | 解决后 status 正确更新 |

---

## 三、生物学启发的主动遗忘

### 3.1 目标

替代固定半衰期衰减，实现：

- **睡眠巩固**：每周日凌晨，将高频召回（usage_count > 5）且 last_used_at 在 7 天内的 L1 fact 提升为 L3 用户画像条目
- **主动降级**：连续 30 天未被召回的 L2 场景聚类，标记为 `archived`，移出活跃检索集合

### 3.2 架构设计

```
每周日 Cron Job (02:00 稍后，~02:30)
    ↓
Sleep Consolidation:
  - 查询 usage_count > 5 AND last_used_at >= 7天前 的 L1 facts
  - LLM 归纳 → 写入 user_profile.md (L3)
    ↓
Active Demotion:
  - 查询 last_used_at < 30天前 的 L2 dispositions/clusters
  - 标记为 archived，移出 active 检索集合
    ↓
记录本周 consolidation 统计
```

### 3.3 数据库改动

```sql
-- 给 chunks 加 usage 字段（如果不存在）
ALTER TABLE chunks ADD COLUMN usage_count INTEGER DEFAULT 0;
ALTER TABLE chunks ADD COLUMN last_used_at REAL;

-- 给 dispositions 加 archived 字段
ALTER TABLE dispositions ADD COLUMN archived INTEGER DEFAULT 0;

-- 给场景聚类（如有）加 last_used 索引
CREATE INDEX IF NOT EXISTS idx_dispositions_last_used ON dispositions(last_used_at);
CREATE INDEX IF NOT EXISTS idx_chunks_usage ON chunks(usage_count, last_used_at);
```

### 3.4 新增文件

| 文件 | 职责 |
|------|------|
| `impl/active_forgetting.py` | 主动遗忘核心逻辑：sleep consolidation + active demotion |
| `cron/cron_active_forgetting.py` | 每周日 Cron Job 入口 |
| `tests/test_active_forgetting.py` | 主动遗忘单元测试 |

### 3.5 active_forgetting.py 实现框架

```python
# impl/active_forgetting.py
"""
生物学启发的主动遗忘模块
- Sleep Consolidation: 高频 L1 facts → 提升为 L3 user_profile
- Active Demotion: 长期未召回的 L2 → 归档
"""

from pathlib import Path
from impl.database import Database
from impl.config import SLEEP_CONSOLIDATION_USAGE_THRESHOLD, SLEEP_CONSOLIDATION_DAYS

def sleep_consolidation(db: Database):
    """
    睡眠巩固：每周日凌晨调用
    将 usage_count > 5 且 last_used_at 在 7 天内的 L1 facts 提升为 L3 user_profile 条目
    """
    # 1. 查询高频 L1 facts
    rows = db.execute("""
        SELECT id, content, usage_count, last_used_at
        FROM chunks
        WHERE chunk_type = 'l1_fact'
          AND usage_count > ?
          AND last_used_at >= julianday('now', '-{} days')
        ORDER BY usage_count DESC
        LIMIT 20
    """, (SLEEP_CONSOLIDATION_USAGE_THRESHOLD, SLEEP_CONSOLIDATION_DAYS)).fetchall()

    if not rows:
        print("无符合条件的事实进行睡眠巩固")
        return {"consolidated": 0}

    # 2. LLM 归纳为 user_profile 条目
    facts_text = "\n".join([f"- {r['content']}" for r in rows])
    prompt = f"""以下是从用户对话中提取的高频事实，将它们归纳为 1-2 条简洁的用户画像陈述。

要求：
- 用中文，30-80 字
- 直接描述用户偏好/习惯，不说"用户"二字
- 合并相似主题的事实

高频事实：
{facts_text}

用户画像："""

    from llm_client import call_llm
    profile_text = call_llm(prompt, model="MiniMax-M2.7", provider="minimax-cn").strip()

    # 3. 追加到 user_profile.md
    profile_path = Path.home() / ".hermes" / "memory" / "user_profile.md"
    with open(profile_path, "a", encoding="utf-8") as f:
        f.write(f"\n---\n{profile_text}\n")

    # 4. 标记这些 chunks 为已提升（不再重复提升）
    chunk_ids = [r['id'] for r in rows]
    placeholders = ",".join(["?"] * len(chunk_ids))
    db.execute(f"UPDATE chunks SET chunk_type='l1_promoted' WHERE id IN ({placeholders})", chunk_ids)
    db.commit()

    return {"consolidated": len(rows), "profile_text": profile_text[:80]}

def active_demotion(db: Database):
    """
    主动降级：归档 30 天未召回的 L2 dispositions/clusters
    """
    # 1. 查找 30 天未召回的 active dispositions
    rows = db.execute("""
        SELECT id, condition_text, prediction_text
        FROM dispositions
        WHERE archived = 0
          AND (last_used_at IS NULL OR last_used_at < julianday('now', '-30 days'))
    """).fetchall()

    if not rows:
        print("无需要归档的 dispositions")
        return {"demoted": 0}

    # 2. 标记为 archived
    ids = [r['id'] for r in rows]
    placeholders = ",".join(["?"] * len(ids))
    db.execute(f"UPDATE dispositions SET archived = 1 WHERE id IN ({placeholders})", ids)
    db.commit()

    return {"demoted": len(ids), "ids": ids}

def get_active_dispositions(db: Database) -> list[dict]:
    """获取活跃（非归档）dispositions（检索时过滤 archived=0）"""
    rows = db.execute("""
        SELECT * FROM dispositions WHERE archived = 0
    """).fetchall()
    return [dict(r) for r in rows]
```

### 3.6 cron 配置

```python
# cron/cron_active_forgetting.py 同 cron_reflection.py 框架
# schedule: "30 2 * * 0"  # 每周日 02:30
```

### 3.7 验收标准

| 用例 | 验收条件 |
|------|---------|
| AF.1 | sleep_consolidation() 正确写入 user_profile.md |
| AF.2 | usage_count ≤ 5 或 last_used_at > 7天 不触发巩固 |
| AF.3 | 已提升 facts 标记为 l1_promoted（不重复提升） |
| AF.4 | active_demotion() 正确标记 archived=1 |
| AF.5 | get_active_dispositions() 过滤 archived=0 |
| AF.6 | 独立运行不报错，不破坏现有数据 |

---

## 四、开发步骤与优先级

### 阶段排序

```
P0（基础设施）
├─ Step 0: 数据库迁移（l4_reflections, pending_conflicts, usage_count, archived）
├─ Step 1: impl/l4_reflection.py（L4 核心）
├─ Step 2: impl/conflict_resolver.py（冲突检测）
└─ Step 3: impl/active_forgetting.py（主动遗忘核心）

P1（集成与 Cron）
├─ Step 4: HermemMemoryProvider 接入三个新模块
├─ Step 5: cron_reflection.py（每日 02:00）
└─ Step 6: cron_active_forgetting.py（每周日 02:30）

P2（测试与验收）
├─ Step 7: 单元测试（l4, conflict, active_forgetting）
└─ Step 8: 端到端测试 + 验收报告
```

### 实施顺序与 trade-off

| 步骤 | 优点 | 风险 |
|------|------|------|
| 先 L4 再冲突再遗忘 | 逐步验证，每步可独立测试 | 三个模块耦合到 provider |
| 先冲突再 L4 再遗忘 | 冲突最影响"被理解感" | L4 的 error 输入依赖冲突检测质量 |
| 先遗忘再冲突再 L4 | 减少噪声，最干净 | 用户感知最弱，优先级最低 |

**推荐顺序**：L4 → 冲突 → 遗忘（按对用户感知的影响排序）

---

## 五、测试计划

### 5.1 单元测试（独立可运行）

```
tests/
├── test_l4_reflection.py
│   ├── test_get_yesterday_errors()         # 正确筛选昨天数据
│   ├── test_synthesize_reflection()        # LLM 归纳格式
│   ├── test_write_to_l4_reflections()      # 表写入 + TTL
│   ├── test_skip_when_insufficient_errors() # < 3 条跳过
│   └── test_get_l4_reflections_ordered()   # 按 confidence 降序
│
├── test_conflict_resolver.py
│   ├── test_like_vs_hate_conflict()        # "喜欢A" vs "讨厌A" = 冲突
│   ├── test_no_conflict_below_threshold()   # sim < 0.75 不触发
│   ├── test_pending_conflicts_table_write() # 表写入正确
│   ├── test_generate_negotiation_message() # 协商消息格式
│   ├── test_resolve_conflict_status()      # 解决后 status 更新
│   └── test_llm_contradiction_fallback()   # 长文本 LLM 判断
│
└── test_active_forgetting.py
    ├── test_sleep_consolidation_threshold() # usage > 5 才触发
    ├── test_sleep_consolidation_7day()      # last_used_at < 7天
    ├── test_promoted_marked_l1_promoted()  # 提升后标记
    ├── test_active_demotion_30day()         # 30天未召回才降级
    ├── test_get_active_dispositions_filter() # archived=0 过滤
    └── test_profile_md_append()             # user_profile.md 正确追加
```

### 5.2 集成测试

```
tests/test_v5_5_e2e.py
├── E2E.1: L4 完整流程
│   "昨天 5 条 error → cron → l4_reflections 写入 → get_l4_reflections 返回"
│
├── E2E.2: 冲突检测完整流程
│   "新事实 → detect_conflicts → pending_conflicts 写入 → 协商消息生成"
│
├── E2E.3: 遗忘完整流程
│   "sleep_consolidation → user_profile.md 更新 + chunks 标记"
│   "active_demotion → dispositions archived"
│
└── E2E.4: 三模块独立运行无交叉影响
```

### 5.3 端到端测试脚本

```python
# scripts/test_v5_5_e2e.py
"""
V5.5 端到端测试
运行方式: python3 scripts/test_v5_5_e2e.py
"""

import time, re
from impl.l4_reflection import get_yesterday_errors, synthesize_reflection
from impl.conflict_resolver import detect_conflicts, get_pending_conflicts
from impl.active_forgetting import sleep_consolidation, active_demotion
from impl.database import Database

def test_l4_flow():
    print("\n=== E2E.1: L4 完整流程 ===")
    db = Database()

    # 模拟插入昨日 errors（用于测试）
    yesterday = time.time() - 86400
    test_errors = [
        {"context": "用户说想要直接看代码不要解释", "error_type": "explanation_rejected", "surprise_level": 0.8},
        {"context": "用户提供了一个实现方案", "error_type": "direct_solution", "surprise_level": 0.7},
        {"context": "用户否定了详细分析", "error_type": "analysis_rejected", "surprise_level": 0.6},
    ]
    for e in test_errors:
        db.execute("""
            INSERT INTO prediction_errors (context, error_type, surprise_level, created_at)
            VALUES (?, ?, ?, ?)
        """, (e["context"], e["error_type"], e["surprise_level"], yesterday - 100))
    db.commit()

    errors = get_yesterday_errors(db)
    print(f"读取到 {len(errors)} 条昨日 error")

    if len(errors) >= 3:
        reflection = synthesize_reflection(errors)
        print(f"L4 反思: {reflection[:80] if reflection else 'None'}...")

    print("✓ L4 流程完成")

def test_conflict_flow():
    print("\n=== E2E.2: 冲突检测完整流程 ===")

    # 测试"喜欢日料" vs "讨厌日料"冲突
    conflicts = detect_conflicts("我最近很喜欢吃生鱼片")
    print(f"检测到 {len(conflicts)} 个冲突")
    for c in conflicts:
        print(f"  [{c['similarity']:.3f}] 冲突类型: {c['conflict_type']}")
        print(f"  新: {c['new_fact_text'][:50]}")
        print(f"  已存在: {c['existing_fact_text'][:50]}")

    print("✓ 冲突检测流程完成")

def test_forgetting_flow():
    print("\n=== E2E.3: 主动遗忘完整流程 ===")
    db = Database()

    result = sleep_consolidation(db)
    print(f"睡眠巩固: {result}")

    result = active_demotion(db)
    print(f"主动降级: {result}")

    print("✓ 主动遗忘流程完成")

if __name__ == "__main__":
    test_l4_flow()
    test_conflict_flow()
    test_forgetting_flow()
    print("\n=== V5.5 E2E 测试完成 ===")
```

---

## 六、配置项（impl/config.py 新增）

```python
# ===========================
# L4 反思层配置
# ===========================
LLM_MODEL = "MiniMax-M2.7"
LLM_PROVIDER = "minimax-cn"
L4_MIN_ERRORS_FOR_REFLECTION = 3  # 最少需要 3 条 error 才触发反思
L4_REFLECTION_TTL_DAYS = 14        # 反思记录 14 天后自动失效

# ===========================
# 冲突检测配置
# ===========================
CONFLICT_SIMILARITY_THRESHOLD = 0.75  # 相似度 > 0.75 才检测矛盾
CONFLICT_DISPOSITION_MIN_CONF = 0.7   # 只检测置信度 >= 0.7 的 disposition

# ===========================
# 主动遗忘配置
# ===========================
SLEEP_CONSOLIDATION_USAGE_THRESHOLD = 5  # usage_count > 5 才考虑巩固
SLEEP_CONSOLIDATION_DAYS = 7             # last_used_at 在 7 天内
ACTIVE_DEMOTION_DAYS = 30                # 30 天未召回则降级归档
MAX_CONSOLIDATION_PER_WEEK = 20          # 每周最多巩固 20 条
```

---

## 七、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 归纳质量差 | 中 | 中 | 限制 reflection 字数 50-150，减少生成自由度 |
| 误触发冲突协商 | 中 | 中 | 双保险：sim > 0.75 AND 语义矛盾 才触发 |
| user_profile.md 无限膨胀 | 低 | 低 | 每周最多 20 条 + LLM 合并 |
| archived 标记丢失 | 低 | 中 | 检索时加 WHERE archived=0 过滤 |
| Cron 失败未通知 | 中 | 中 | 继承 cron job 的标准错误交付机制 |
| L4 reflection 干扰对话 | 低 | 中 | 提供 injection 开关（默认关闭，按需开启） |

---

## 八、与已有 SPEC 的一致性检查

| SPEC 要求 | 对应实现 | 一致性 |
|-----------|---------|--------|
| L4 反思层：每日 02:00 cron | cron_reflection.py + l4_reflections 表 | ✅ |
| L4 输出：元记忆描述 | reflection_text 字段 | ✅ |
| L4 注入：system prompt 可选 | get_l4_reflections() + warmup 注入 | ✅ |
| 冲突检测：sim > 0.75 + 语义矛盾 | CONFLICT_SIMILARITY_THRESHOLD=0.75 + _is_contradictory() | ✅ |
| 冲突不覆盖：pending_conflicts | pending_conflicts 表 | ✅ |
| 协商询问格式 | `_detect_and_raise_conflicts()` 返回消息 | ✅ |
| 睡眠巩固：usage > 5 + 7天内 → L3 | sleep_consolidation() + user_profile.md | ✅ |
| 主动降级：30天未召回 → archived | active_demotion() + archived=1 | ✅ |
| 配置化 | 全部阈值/开关在 config.py | ✅ |

---

*规划版本 v1.0，等待 Oliver 评审后进入实施。*
