# Hermem V5.5 开发计划

**版本**: v1.2
**日期**: 2026-05-27
**状态**: 已实现 v1.0；P0 修复已落地（2026-06-01，commit 95858e9）
**依据**: SPEC.md v1.0 + 评估报告 v1.0
**评估综合评分**: 9.5/10（v1.1 提的 3 项扣分均已修复：usage_count/last_used_at 追踪补上；llm_helper 已带 fallback；冲突检测时机已修正）

---

## 开发阶段总览

| 阶段 | 步骤 | 内容 | 优先级 | 评估改进 |
|------|------|------|--------|---------|
| **P0 前置** | 0a | 数据库迁移脚本 | P0 | 沿用 v1.0 |
| **P0 前置** | 0b | **usage_count / last_used_at 维护逻辑** | P0 | ❌ 严重遗漏，已修正 |
| **P0 核心** | 1 | LLM fallback helper（统一本地模型降级） | P0 | 新增 |
| **P0 核心** | 2 | impl/l4_reflection.py（L4 核心） | P0 | prompt 加字数限制；LLM fallback |
| **P0 核心** | 3 | impl/conflict_resolver.py（冲突检测） | P0 | 触发时机修正：L1 持久化后而非 sync_turn |
| **P0 核心** | 4 | impl/active_forgetting.py（主动遗忘核心） | P0 | 继承 Step 0b 的维护逻辑 |
| **P1 集成** | 5 | HermemMemoryProvider 接入（正确调用顺序） | P1 | 冲突检测移至 L1 写入后 |
| **P1 Cron** | 6 | cron/cron_weekly_synthesis.py（每周综合归纳，合并 L4 + 睡眠巩固） | P1 | P1 改进：合并避免冗余 |
| **P2** | 7 | resolve_conflict_with_action（冲突解决联动） | P2 | P1 改进：增加数据清理 |
| **P2** | 8 | 单元测试 + 集成测试 | P2 | 新增 usage_count 更新覆盖 |
| **P2** | 9 | 端到端测试 + 验收报告 | P2 | 调整后约 15-16h |

---

## Step 0a：数据库迁移脚本

**目标**：创建新表 + 已有表加字段，不破坏现有数据。

**文件**：`phase3/v5.5/migrate_v55.py`

### 0a.1 实现

```python
#!/usr/bin/env python3
"""
V5.5 数据库迁移脚本
运行一次，将以下改动应用到 hermem.db：
1. 新建 l4_reflections 表
2. 新建 pending_conflicts 表
3. 给 prediction_errors 加日期索引
4. 给 chunks 加 usage_count, last_used_at 字段
5. 给 dispositions 加 archived 字段 + last_used_at 字段
6. 给新增索引加索引

用法: python3 phase3/v5.5/migrate_v55.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory" / "hermem.db"

MIGRATIONS = [
    # l4_reflections 表
    """
    CREATE TABLE IF NOT EXISTS l4_reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reflection_text TEXT NOT NULL,
        source_errors   INTEGER DEFAULT 0,
        confidence      REAL DEFAULT 0.5,
        created_at      REAL DEFAULT (julianday('now')),
        expires_at      REAL,
        injected_count  INTEGER DEFAULT 0,
        last_injected_at REAL
    );
    """,
    # pending_conflicts 表
    """
    CREATE TABLE IF NOT EXISTS pending_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        new_fact_text      TEXT NOT NULL,
        existing_fact_text TEXT NOT NULL,
        similarity        REAL NOT NULL,
        conflict_type     TEXT NOT NULL,
        existing_id      TEXT NOT NULL,
        status           TEXT DEFAULT 'pending',
        resolution_note  TEXT,
        created_at       REAL DEFAULT (julianday('now')),
        resolved_at      REAL
    );
    """,
    # prediction_errors 日期索引
    "CREATE INDEX IF NOT EXISTS idx_pe_created ON prediction_errors(created_at);",
    # chunks 加字段（防重复添加）
    "ALTER TABLE chunks ADD COLUMN usage_count INTEGER DEFAULT 0;",
    "ALTER TABLE chunks ADD COLUMN last_used_at REAL;",
    # dispositions 加字段
    "ALTER TABLE dispositions ADD COLUMN archived INTEGER DEFAULT 0;",
    "ALTER TABLE dispositions ADD COLUMN last_used_at REAL;",
    # 索引
    "CREATE INDEX IF NOT EXISTS idx_dispositions_last_used ON dispositions(last_used_at);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_usage ON chunks(usage_count, last_used_at);",
]

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    print(f"连接数据库: {DB_PATH}")

    for sql in MIGRATIONS:
        try:
            cur.execute(sql)
            # 日志提取表名
            if "CREATE TABLE" in sql:
                name = sql.split("CREATE TABLE IF NOT EXISTS ")[-1].split(" ")[0]
            elif "ALTER TABLE" in sql:
                name = sql.split("ALTER TABLE ")[-1].split(" ")[0]
            elif "CREATE INDEX" in sql:
                name = sql.split("CREATE INDEX IF NOT EXISTS ")[-1].split(" ")[0]
            else:
                name = "op"
            print(f"  ✅ {name}")
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                print(f"  ⚠️  已存在，跳过")
            else:
                raise
    conn.commit()
    conn.close()
    print("迁移完成")

if __name__ == "__main__":
    migrate()
```

### 0a.2 验收

- [ ] 独立运行无报错
- [ ] 再次运行不报 duplicate column 错误（IF NOT EXISTS 生效）
- [ ] hermem.db 新增两个表，字段正确
- [ ] 现有数据不受影响

---

## Step 0b：usage_count / last_used_at 自动维护（❌ 严重遗漏，必须 P0）

**目标**：在每次检索命中 chunk / disposition 时更新 usage_count 和 last_used_at，使主动遗忘模块能正常工作。

**核心原则**：维护操作异步批量执行，不阻塞检索响应。

### 0b.1 为什么这是 P0

评估报告指出：
> 若不先实现 usage_count 和 last_used_at 的维护，主动遗忘的两个功能都将失效（因为永远不会有满足条件的记录）。

`active_demotion()` 查询 `last_used_at < 30天前`，`sleep_consolidation()` 查询 `usage_count > 5 AND last_used_at >= 7天前`。若这两个字段从未被更新，两个函数都永远匹配不到任何记录。

### 0b.2 实现

```python
# impl/usage_tracker.py
"""
检索命中统计更新模块
在每次 retrieve() 返回结果后，异步批量更新命中 chunk 的 usage_count 和 last_used_at。
不阻塞检索流程。
"""

import sqlite3, threading
from pathlib import Path
from impl.config import DB_PATH

def update_chunk_usage_async(chunk_ids: list[int]):
    """
    异步批量更新 chunks 的 usage_count += 1 和 last_used_at = now。
    调用方式：threading.Thread(target=update_chunk_usage_async, args=(ids,)).start()
    """
    if not chunk_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = cur.execute("SELECT julianday('now')").fetchone()[0]
    # 使用 julianday('now') 保证时间可比
    try:
        cur.executemany(
            "UPDATE chunks SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            [(now, cid) for cid in chunk_ids]
        )
        conn.commit()
    finally:
        conn.close()

def update_disposition_usage_async(disposition_ids: list[int]):
    """异步批量更新 dispositions 的 last_used_at = now"""
    if not disposition_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = cur.execute("SELECT julianday('now')").fetchone()[0]
    try:
        cur.executemany(
            "UPDATE dispositions SET last_used_at = ? WHERE id = ?",
            [(now, did) for did in disposition_ids]
        )
        conn.commit()
    finally:
        conn.close()
```

### 0b.3 接入 retrieve() 流程

```python
# impl/retrieval.py retrieve() 函数改造

import threading
from impl.usage_tracker import update_chunk_usage_async, update_disposition_usage_async

def retrieve(query: str, top_k: int = 5, ...) -> list[dict]:
    results = _do_search(query, top_k, ...)  # 现有搜索逻辑

    # [新增] 异步更新命中 chunk 的使用统计
    if results:
        chunk_ids = [r["id"] for r in results if r.get("id")]
        threading.Thread(target=update_chunk_usage_async, args=(chunk_ids,), daemon=True).start()

    # disposition 命中也更新
    disp_ids = [r["disposition_id"] for r in results if r.get("disposition_id")]
    if disp_ids:
        threading.Thread(target=update_disposition_usage_async, args=(disp_ids,), daemon=True).start()

    return results
```

### 0b.4 验收

- [ ] `update_chunk_usage_async([1,2,3])` 运行后，id=1,2,3 的 chunks usage_count += 1，last_used_at = now
- [ ] `update_disposition_usage_async([1,2])` 运行后，id=1,2 的 dispositions last_used_at = now
- [ ] retrieve() 调用后，不阻塞返回结果（异步）
- [ ] 并发多次调用 retrieve()，统计更新不丢失（SQLite 并发安全）
- [ ] ❗**关键验证**：运行 3 次检索后，`SELECT usage_count FROM chunks` 确认计数 > 0

### 0b.5 批量回填历史数据（一次性）

```python
# scripts/backfill_usage_stats.py
"""一次性回填：将所有已有 chunks 的 usage_count 设为 1（假设历史已使用过）"""
import sqlite3
from pathlib import Path
from impl.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
# 所有已有 chunk 视为至少被使用过 1 次，避免新系统冷启动时 active_demotion 误判
cur.execute("UPDATE chunks SET usage_count = MAX(usage_count, 1), last_used_at = julianday('now', '-7 days') WHERE usage_count = 0")
print(f"回填了 {cur.rowcount} 条历史 chunk")
conn.commit()
conn.close()
```

---

## Step 1：LLM Fallback Helper（新增 P0）

**目标**：为 L4 和冲突检测中的 LLM 调用提供统一的本地模型降级，避免外部 API 不可用时整个 Cron 失败。

**文件**：`impl/llm_helper.py`

### 1.1 实现

```python
# impl/llm_helper.py
"""
统一 LLM 调用入口，支持 primary + fallback 自动降级。
Primary: MiniMax-M2.7 / minimax-cn（外部 API）
Fallback: qwen2.5:3b（本地 Ollama）
"""

from impl.config import LLM_MODEL, LLM_PROVIDER, LLM_FALLBACK_MODEL, LLM_FALLBACK_PROVIDER

def call_llm_with_fallback(prompt: str, max_tokens: int = 300) -> str | None:
    """
    调用 LLM，primary 失败时自动降级到本地模型。
    返回生成的文本，失败时返回 None（不抛异常）。
    """
    try:
        return _call_llm(prompt, model=LLM_MODEL, provider=LLM_PROVIDER, max_tokens=max_tokens)
    except Exception as e:
        print(f"[LLM] Primary ({LLM_MODEL}) 调用失败: {e}，尝试 fallback...")
        try:
            return _call_llm(prompt, model=LLM_FALLBACK_MODEL, provider=LLM_FALLBACK_PROVIDER, max_tokens=max_tokens)
        except Exception as e2:
            print(f"[LLM] Fallback ({LLM_FALLBACK_MODEL}) 也失败: {e2}，跳过")
            return None

def _call_llm(prompt: str, model: str, provider: str, max_tokens: int) -> str:
    """实际 LLM 调用"""
    # 现有 llm_client 保持不变
    from llm_client import call_llm
    return call_llm(prompt, model=model, provider=provider, max_tokens=max_tokens)
```

### 1.2 配置新增

```python
# impl/config.py 新增
LLM_FALLBACK_MODEL = "qwen2.5:3b"
LLM_FALLBACK_PROVIDER = "ollama"  # 本地
```

### 1.3 验收

- [ ] primary LLM 不可用时自动切换 fallback
- [ ] fallback 也失败时静默返回 None，不抛异常
- [ ] 两个模型都正常时优先使用 primary

---

## Step 2：impl/l4_reflection.py

**目标**：L4 反思层核心逻辑 + local fallback + prompt 字数限制。

**文件**：`impl/l4_reflection.py`

### 2.1 实现（改进版 vs v1.0）

| 改进点 | v1.0 | v1.1 |
|--------|------|------|
| LLM 无 fallback | ❌ | ✅ 统一用 llm_helper |
| prompt 无字数限制 | ❌ | ✅ prompt 加"不超过 150 字" |
| 注入方式单一 | 消息上下文 | 可选 system prompt（配置控制） |

### 2.2 核心代码片段

```python
# impl/l4_reflection.py

from impl.llm_helper import call_llm_with_fallback
from impl.config import L4_MIN_ERRORS_FOR_REFLECTION, L4_REFLECTION_TTL_DAYS, L4_PROMPT_MAX_CHARS

def synthesize_reflection(errors: list[dict]) -> str | None:
    """用 LLM 从错误记录归纳元记忆（v1.1：字数限制 + fallback）"""
    if not errors:
        return None

    error_summary = "\n".join([
        f"- [{e['surprise_level']:.2f}] {e['error_type']}: {e['context'][:100]}"
        for e in errors
    ])
    prompt = f"""你是一个记忆分析专家。从以下预测错误记录中归纳出用户交互模式的元记忆描述。

要求：
- 用中文
- 不超过 150 字（硬限制，超出截断）
- 直接描述，不要"根据分析"这类废话开头
- 重点：用户的偏好、习惯、期望（不是描述错误本身）

错误记录：
{error_summary}

元记忆（不超过150字）："""

    response = call_llm_with_fallback(prompt, max_tokens=200)
    if not response:
        return None

    # 硬截断
    return response.strip()[:L4_PROMPT_MAX_CHARS]
```

### 2.3 验收（新增验收项）

- [ ] v1.0 所有验收项（get_yesterday_errors 等）
- [ ] LLM 不可用时 fallback 到本地模型，正常返回
- [ ] prompt 中加字数限制后，生成文本不超过 150 字
- [ ] 两处 LLM 调用（synthesize_reflection + LLM fallback）均可独立测试

---

## Step 3：impl/conflict_resolver.py

**目标**：冲突检测与协商核心逻辑。**关键修正**：触发时机从 `sync_turn` 移至 L1 事实持久化之后。

**文件**：`impl/conflict_resolver.py`

### 3.1 触发时机修正（❌ v1.0 错误）

| 版本 | 触发位置 | 问题 |
|------|---------|------|
| v1.0 | `sync_turn()` 中基于 `user_message` | L1 事实尚未写入数据库，检测必定漏掉或检测的是旧数据 |
| **v1.1** | L1 持久化后（`extract_and_store()` 返回时） | 新事实已在 DB，可正确比对 |

### 3.2 实现（v1.1）

```python
# impl/conflict_resolver.py

def detect_conflicts_after_persist(new_fact_text: str, new_fact_id: str) -> list[dict]:
    """
    在 L1 事实持久化后调用（不是 sync_turn 中基于用户消息）。
    检测新事实是否与已有 disposition/user_profile 冲突。
    返回: [{existing_fact, similarity, conflict_type, existing_id}, ...]
    """
    # ...  embedding 计算和相似度检测逻辑同 v1.0 ...

def detect_conflicts(new_fact_text: str) -> list[dict]:
    """
    兼容接口：供 external caller（如 provider._detect_after_store）调用。
    内部调用 detect_conflicts_after_persist。
    """
    return detect_conflicts_after_persist(new_fact_text, new_fact_id=None)
```

### 3.3 LLM Fallback 触发条件修正

v1.0 任意长文本都调用 LLM，v1.1 限制为：

```python
def _is_contradictory(text_a: str, text_b: str) -> bool:
    """
    矛盾检测：
    - 简单规则（否定词检测）优先
    - LLM fallback 仅在：简单规则无法判断 AND 两句都 >= 10 词
    """
    # 简单规则
    if _simple_contradiction_rule(text_a, text_b):
        return True
    if _simple_non_contradiction_rule(text_a, text_b):
        return False

    # 仅长文本才走 LLM fallback（节省 token）
    if len(text_a.split()) >= 10 and len(text_b.split()) >= 10:
        return _llm_contradiction_check(text_a, text_b)

    return False  # 模棱两可时默认不触发冲突
```

### 3.4 resolve_conflict_with_action（新增 Step 7）

```python
# 新增到 conflict_resolver.py

def resolve_conflict_with_action(db: Database, conflict_id: int, resolution: str, note: str = None):
    """
    解决冲突并执行实际数据更新。
    resolution:
      - 'resolved_new': 删除/降级旧的 existing_fact，更新 pending_conflicts status
      - 'resolved_existing': 保留旧的，标记 pending_conflicts
      - 'dismissed': 用户否认冲突，标记 pending_conflicts
    """
    # 读取冲突记录
    row = db.execute("SELECT * FROM pending_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    if not row:
        return
    conflict = dict(row)

    if resolution == "resolved_new":
        # 删除或归档旧的 disposition / user_profile 条目
        if conflict["conflict_type"] == "disposition":
            db.execute(
                "UPDATE dispositions SET archived = 1 WHERE id = ?",
                (conflict["existing_id"],)
            )
        elif conflict["conflict_type"] == "user_profile":
            _remove_user_profile_entry(conflict["existing_id"])

    resolve_conflict(db, conflict_id, resolution, note)
```

### 3.5 验收（v1.1 新增）

- [ ] v1.0 所有验收项
- [ ] LLM fallback 仅在 >= 10 词长文本时触发
- [ ] 冲突解决后可联动删除/归档旧记录
- [ ] `detect_conflicts()` 在 L1 持久化后调用（不在 sync_turn 中基于用户消息）

---

## Step 4：impl/active_forgetting.py

**目标**：主动遗忘核心逻辑，依赖 Step 0b 的 usage_count / last_used_at 维护。

**文件**：`impl/active_forgetting.py`

### 4.1 继承 Step 0b 的使用统计

由于 Step 0b 已在 retrieve() 中埋入异步更新调用，`sleep_consolidation()` 和 `active_demotion()` 无需改造，只需确认查询条件正确。

### 4.2 主动降级增加置信度过滤（v1.1 P1 改进）

| 版本 | 降级条件 |
|------|---------|
| v1.0 | `last_used_at < 30天前`（全部） |
| **v1.1** | `last_used_at < 30天前 AND confidence < 0.6` |

### 4.3 实现

```python
def active_demotion(db: Database, min_confidence: float = 0.6):
    """
    归档 30 天未召回且置信度低的 dispositions。
    防止低频但重要的记忆（如账号密码）被误归档。
    """
    rows = db.execute("""
        SELECT id, condition_text, prediction_text, confidence
        FROM dispositions
        WHERE archived = 0
          AND confidence < ?
          AND (last_used_at IS NULL OR last_used_at < julianday('now', '-30 days'))
    """, (min_confidence,)).fetchall()

    if not rows:
        return {"demoted": 0}

    ids = [r['id'] for r in rows]
    placeholders = ",".join(["?"] * len(ids))
    db.execute(f"UPDATE dispositions SET archived = 1 WHERE id IN ({placeholders})", ids)
    db.commit()
    return {"demoted": len(ids), "ids": ids}
```

### 4.4 验收（v1.1 新增）

- [ ] v1.0 所有验收项
- [ ] `confidence >= 0.6` 的 disposition 不被归档（即使 30 天未召回）
- [ ] `usage_count > 5` 且 `last_used_at >= 7天前` 才触发睡眠巩固

---

## Step 5：HermemMemoryProvider 集成（正确调用顺序）

**目标**：在 `sync_turn()` 和 `extract_and_store()` 正确接入三个模块。

**改造文件**：`impl/provider.py`

### 5.1 接入点（v1.1 修正）

```
sync_turn(user_message, assistant_response, turn_context)
    ├─ [已有] correction detection（基于 assistant_response）
    └─ [已有] L4 reflection 注入（首次 turn）

extract_and_store(new_fact_text, ...)  ← L1 持久化路径
    └─ [新增] 持久化成功后调用 detect_conflicts_after_persist()
```

**❌ v1.0 错误**：在 `sync_turn` 中基于 `user_message` 直接调用 `detect_conflicts()`——此时新事实还在 Agent 处理中，尚未写入数据库，冲突检测会漏掉。

**✅ v1.1 正确**：在 L1 写入数据库后（`extract_and_store()` 返回时）调用 `detect_conflicts()`。

### 5.2 实现框架

```python
# impl/provider.py HermemMemoryProvider

from impl.l4_reflection import get_l4_reflections, mark_reflection_injected
from impl.conflict_resolver import (
    detect_conflicts_after_persist,
    create_pending_conflict,
    get_pending_conflicts,
)
from impl.config import L4_INJECTION_ENABLED, L4_INJECTION_TO_SYSTEM_PROMPT

def extract_and_store(self, fact_text: str, ...):
    """[已有 L1 存储逻辑] → 返回 chunk_id"""
    chunk_id = self._do_store(fact_text, ...)  # 现有逻辑

    # [新增] L1 持久化后检测冲突（不是 sync_turn 中）
    conflict_msg = self._detect_and_raise_conflicts_after_store(fact_text, chunk_id)
    if conflict_msg:
        self._pending_negotiation = conflict_msg

    return chunk_id

def sync_turn(self, user_message, assistant_response, turn_context):
    # 1. [已有] correction detection
    self._detect_and_process_corrections(assistant_response)

    # 2. [新增] L4 reflection 注入（首次 turn）
    if L4_INJECTION_ENABLED and self._first_turn:
        for ref in get_l4_reflections(self.db, max_count=3):
            if ref["id"] not in self._injected_reflection_ids:
                self._inject_l4_reflection(ref)
                mark_reflection_injected(self.db, ref["id"])
                self._injected_reflection_ids.add(ref["id"])

def _detect_and_raise_conflicts_after_store(self, new_fact_text: str, new_fact_id: str):
    """L1 持久化后调用，正确触发冲突检测"""
    conflicts = detect_conflicts_after_persist(new_fact_text, new_fact_id)
    if not conflicts:
        return None

    for c in conflicts:
        create_pending_conflict(c, self.db)

    top = conflicts[0]
    return (
        f"我注意到您之前提到「{top['existing_fact_text'][:50]}」，"
        f"现在又提到「{top['new_fact_text'][:50]}」。"
        f"这两者似乎有些出入——我应该以哪个为准？"
    )

def _inject_l4_reflection(self, reflection: dict):
    """注入 L4 反思到上下文或 system prompt"""
    injection = (
        f"[系统元认知 - 置信度 {reflection['confidence']:.2f}]\n"
        f"{reflection['reflection_text']}\n"
    )
    if L4_INJECTION_TO_SYSTEM_PROMPT:
        # 注入到 system prompt（宏观策略层）
        self._system_prompt_injection = injection
    else:
        # 注入到消息上下文（默认）
        self._memory_context += f"\n\n{injection}"
```

### 5.3 验收（v1.1 新增）

- [ ] 冲突检测在 `extract_and_store()` 返回时调用，不是在 `sync_turn` 中
- [ ] L4 reflection 注入支持两种方式（system prompt / 消息上下文）且可配置
- [ ] `resolve_conflict_with_action()` 可正确清理旧数据
- [ ] 三个模块独立可测，不破坏 V5 Phase A/B 已有功能

---

## Step 6：cron/cron_weekly_synthesis.py（合并 L4 + 睡眠巩固）

**目标**：将"每日 L4 反思"与"每周睡眠巩固"合并为一个每周日 Cron 任务，避免 LLM 归纳冗余。

### 6.1 为什么合并

评估报告指出：
> L4 反思（从 errors 归纳）与睡眠巩固（从高频 facts 归纳）都涉及 LLM 归纳，功能重叠。

合并后同时输入 error patterns + 高频 facts，生成更全面的元记忆，节省 LLM 调用次数。

### 6.2 实现

```python
#!/usr/bin/env python3
"""
V5.5 每周综合归纳 Cron Job
schedule: 每周日 02:30

同时输入：
1. 近 7 天 prediction_errors → error_patterns（用户犯错模式）
2. 高频召回的 L1 facts（usage_count > 5, 7天内）→ user_preferences

输出：
- 元记忆描述 → l4_reflections 表
- 用户画像 → user_profile.md
- 低置信 disposition 归档
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from impl.llm_helper import call_llm_with_fallback
from impl.database import Database
from impl.config import L4_REFLECTION_TTL_DAYS

def get_weekly_data(db: Database):
    """获取本周数据：errors + 高频 facts"""
    # 7 天内的 errors
    errors = db.execute("""
        SELECT context, error_type, surprise_level
        FROM prediction_errors
        WHERE created_at >= julianday('now', '-7 days')
        ORDER BY surprise_level DESC
        LIMIT 30
    """).fetchall()

    # 高频 facts（sleep consolidation 候选）
    facts = db.execute("""
        SELECT id, content, usage_count
        FROM chunks
        WHERE chunk_type = 'l1_fact'
          AND usage_count > 5
          AND last_used_at >= julianday('now', '-7 days')
        ORDER BY usage_count DESC
        LIMIT 20
    """).fetchall()

    return [dict(e) for e in errors], [dict(f) for f in facts]

def synthesize_weekly(db: Database, errors: list[dict], facts: list[dict]):
    """综合归纳：errors → 元记忆；facts → 用户画像"""
    result = {}

    # Part 1: L4 反思（元记忆）
    if len(errors) >= 3:
        error_text = "\n".join([
            f"- [{e['surprise_level']:.2f}] {e['error_type']}: {e['context'][:80]}"
            for e in errors
        ])
        prompt = f"""从以下预测错误中归纳用户交互模式的元记忆。不超过150字，直接描述。

错误记录：{error_text}

元记忆（不超过150字）："""
        reflection = call_llm_with_fallback(prompt, max_tokens=200)
        if reflection:
            db.execute("""
                INSERT INTO l4_reflections (reflection_text, source_errors, confidence, expires_at)
                VALUES (?, ?, ?, julianday('now', '+{days} days'))
            """, (reflection[:150], len(errors), min(len(errors)/50, 1.0), L4_REFLECTION_TTL_DAYS))
            result["reflection"] = reflection[:80]

    # Part 2: 睡眠巩固（用户画像）
    if facts:
        fact_text = "\n".join([f"- {f['content']}" for f in facts])
        prompt = f"""从以下高频事实归纳用户偏好。不超过80字，直接描述。

高频事实：{fact_text}

用户画像（不超过80字）："""
        profile = call_llm_with_fallback(prompt, max_tokens=150)
        if profile:
            profile_path = Path.home() / ".hermes" / "memory" / "user_profile.md"
            with open(profile_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n---\n{profile[:80]}\n")
            # 标记已提升
            ids = [f['id'] for f in facts]
            placeholders = ",".join(["?"] * len(ids))
            db.execute(
                f"UPDATE chunks SET chunk_type='l1_promoted' WHERE id IN ({placeholders})",
                ids
            )
            result["profile"] = profile[:80]

    # Part 3: 主动降级
    from impl.active_forgetting import active_demotion
    demotion_result = active_demotion(db)
    result["demotion"] = demotion_result

    db.commit()
    return result

def main():
    db = Database()
    errors, facts = get_weekly_data(db)
    print(f"本周: {len(errors)} 条 errors, {len(facts)} 条高频 facts")

    result = synthesize_weekly(db, errors, facts)
    print(f"综合归纳结果: {result}")

if __name__ == "__main__":
    main()
```

### 6.3 Cron 创建

```bash
hermes cron create "30 2 * * 0" \
  --name "Weekly Memory Synthesis" \
  --script "phase3/v5.5/cron/cron_weekly_synthesis.py" \
  --model "MiniMax-M2.7" \
  --provider "minimax-cn"
```

> 合并后，原每日 02:00 的 cron_reflection.py 取消，改为每周日 02:30 统一执行。

### 6.4 验收

- [ ] 独立运行不报错
- [ ] errors < 3 时跳过 L4 部分，继续执行睡眠巩固
- [ ] facts 为空时跳过 sleep consolidation 部分
- [ ] 输出包含 consolidation 和 demotion 统计

---

## Step 7：resolve_conflict_with_action（冲突解决联动）

**目标**：当用户选择"以新事实为准"时，自动更新或删除旧记录。

**文件**：`impl/conflict_resolver.py`（已含，见 Step 3.4）

### 7.1 provider.py 接入

```python
def _handle_conflict_resolution(self, conflict_id: int, resolution: str, note: str = None):
    """
    用户在对话中选择冲突解决方案后调用。
    resolution: 'resolved_new' | 'resolved_existing' | 'dismissed'
    """
    from impl.conflict_resolver import resolve_conflict_with_action
    resolve_conflict_with_action(self.db, conflict_id, resolution, note)
```

### 7.2 验收

- [ ] `resolved_new`：旧 disposition 标记 archived=1，旧 user_profile 条目删除
- [ ] `resolved_existing`：保留旧记录，pending_conflicts 标记 resolved
- [ ] `dismissed`：用户否认冲突，标记 dismissed，不修改任何数据

---

## Step 8：单元测试 + 集成测试

### 8.1 文件结构（新增 usage_count 相关测试）

```
phase3/v5.5/tests/
├── test_l4_reflection.py           # L4 + fallback + 字数限制
├── test_conflict_resolver.py        # 冲突 + 触发时机 + LLM fallback 限制
├── test_active_forgetting.py       # 主动遗忘 + 置信度过滤
├── test_usage_tracker.py           # ❌ 新增：usage_count/last_used_at 更新
└── test_v5_5_e2e.py               # V5.5 端到端（含 Step 0b 验证）
```

### 8.2 新增 usage_tracker 测试

```python
# tests/test_usage_tracker.py

def test_chunk_usage_increment():
    """chunk 被命中后 usage_count += 1"""
    ids = [1, 2, 3]
    before = db.execute("SELECT usage_count FROM chunks WHERE id IN (1,2,3)").fetchall()
    update_chunk_usage_async(ids)
    after = db.execute("SELECT usage_count FROM chunks WHERE id IN (1,2,3)").fetchall()
    assert all(a == b + 1 for a, b in zip(after, before))

def test_disposition_last_used_updated():
    """disposition 激活后 last_used_at 更新"""
    db.execute("UPDATE dispositions SET last_used_at = NULL WHERE id = 1")
    update_disposition_usage_async([1])
    row = db.execute("SELECT last_used_at FROM dispositions WHERE id = 1").fetchone()
    assert row[0] is not None  # 不为 NULL

def test_async_non_blocking():
    """异步更新不阻塞主线程"""
    import threading, time
    start = time.time()
    t = threading.Thread(target=update_chunk_usage_async, args=([1],))
    t.start()
    elapsed = time.time() - start
    assert elapsed < 0.1  # 应该几乎立即返回
```

### 8.3 覆盖率目标

| 模块 | 覆盖率目标 |
|------|----------|
| usage_tracker.py | 90%+（新增模块，必须高覆盖） |
| l4_reflection.py | 80%+ |
| conflict_resolver.py | 80%+ |
| active_forgetting.py | 80%+ |

---

## Step 9：端到端测试 + 验收报告

### 9.1 验收用例（含 P0 修复验证）

| 用例 | 验收条件 | 优先级 | 对应评估改进 |
|------|---------|--------|------------|
| E2E.0 | 迁移脚本重跑不报错（幂等） | P0 | — |
| E2E.0b | usage_count/last_used_at 维护正常工作 | P0 | ❌ P0 严重遗漏 |
| E2E.1 | L4 完整流程（fallback 生效） | P0 | P0 新增 |
| E2E.2 | 冲突检测在 L1 持久化后触发（非 sync_turn） | P0 | ❌ v1.0 触发时机错误 |
| E2E.3 | 遗忘：高频 fact → user_profile.md 更新 + 低置信 disposition 归档 | P0 | — |
| E2E.4 | resolve_conflict_with_action 联动更新 | P1 | P1 新增 |
| E2E.5 | 每周综合归纳（L4 + 睡眠巩固合并）正常 | P1 | P1 合并 |
| E2E.6 | 三模块独立运行无交叉影响 | P1 | — |

### 9.2 验收报告模板

```
# V5.5 验收测试报告
日期: 2026-XX-XX

## 环境
- Hermem 版本: V5.1 → V5.5
- 向量数: 1711
- Chunk 数: 1645
- 数据库: hermem.db（迁移后）

## 评估报告 P0 修复验证

| 评估问题 | 验收条件 | 状态 |
|---------|---------|------|
| P0.1 usage_count/last_used_at 维护缺失 | E2E.0b 通过 | ✅/❌ |
| P0.2 冲突检测触发时机错误 | E2E.2 通过 | ✅/❌ |
| P0.3 LLM 无本地 fallback | E2E.1 fallback 测试通过 | ✅/❌ |

## 测试结果

| 用例 | 状态 | 说明 |
|------|------|------|
| E2E.0 | ✅/❌ | |
| E2E.0b | ✅/❌ | |
| E2E.1 | ✅/❌ | |
| E2E.2 | ✅/❌ | |
| E2E.3 | ✅/❌ | |
| E2E.4 | ✅/❌ | |
| E2E.5 | ✅/❌ | |
| E2E.6 | ✅/❌ | |

## Cron Jobs

| Job | Schedule | 模型 | 状态 |
|-----|----------|------|------|
| Weekly Memory Synthesis | 30 2 * * 0 | MiniMax-M2.7 / fallback | ✅/❌ |

## 总体结论
[通过 / 需修复后通过 / 不通过]
```

---

## 文件变更清单

### 新增文件

```
phase3/v5.5/
├── SPEC.md                           # V5.5 规范
├── TODO.md                           # 本文件（v1.1）
├── migrate_v55.py                   # 数据库迁移脚本
├── impl/
│   ├── llm_helper.py               # ❌ 新增：LLM fallback 统一入口
│   ├── l4_reflection.py            # v1.1：fallback + 字数限制
│   ├── conflict_resolver.py        # v1.1：触发时机修正 + 联动更新
│   ├── active_forgetting.py        # v1.1：置信度过滤
│   └── usage_tracker.py            # ❌ 新增：usage_count/last_used_at 维护
├── retrieval.py                     # ❌ 修改：接入 usage_tracker 异步更新
├── cron/
│   └── cron_weekly_synthesis.py   # v1.1：合并 L4 + 睡眠巩固
└── tests/
    ├── test_l4_reflection.py
    ├── test_conflict_resolver.py
    ├── test_active_forgetting.py
    ├── test_usage_tracker.py       # ❌ 新增
    └── test_v5_5_e2e.py
```

### 修改文件

```
impl/
├── provider.py           # 接入三个新模块，修正冲突检测调用时机
├── config.py             # 新增 LLM_FALLBACK_* 配置项
└── retrieval.py         # 接入 usage_tracker 异步更新
```

---

## 时间估算（v1.1 更新）

| 步骤 | 复杂度 | 预计工时 | 说明 |
|------|--------|---------|------|
| Step 0a 迁移脚本 | 低 | 1h | 沿用 v1.0 |
| **Step 0b usage_count 维护** | 中 | **2-3h** | ❌ 严重遗漏修复，需改造 retrieval.py |
| Step 1 LLM fallback | 低 | 1h | 新增 helper |
| Step 2 L4 reflection | 中 | 1.5h | fallback 接入 + prompt 限制 |
| Step 3 冲突检测 | 中 | 2h | 触发时机修正 |
| Step 4 主动遗忘 | 中 | 1h | 置信度过滤（改动小） |
| Step 5 Provider 集成 | 中 | 2h | 调用顺序修正 |
| Step 6 Cron 合并 | 低 | 1h | 合并后减少一个 job |
| Step 7 冲突联动 | 低 | 1h | 新增 resolve action |
| Step 8 测试 | 中 | 2h | 新增 usage_tracker 测试 |
| Step 9 验收 | 低 | 1h | — |

**总计**：~15-16h（原 13h → 新增 Step 0b 的 2-3h）

---

## 评估报告关键改进对应

| 评估报告 P0 问题 | 对应 TODO 步骤 |
|-----------------|---------------|
| ❌ 缺少 usage_count/last_used_at 维护 | **Step 0b**（P0 前置） |
| ❌ 冲突检测触发时机错误 | **Step 3** 中修正 detect_conflicts 文档说明 + **Step 5** 修正接入点 |
| ❌ LLM 无本地 fallback | **Step 1** llm_helper.py |
| P1: 合并 L4 + 睡眠巩固 | **Step 6** cron_weekly_synthesis.py |
| P1: 冲突解决联动 | **Step 7** resolve_conflict_with_action |
| P1: 主动降级加置信度过滤 | **Step 4** active_demotion() 加 min_confidence 参数 |
| P2: L4 注入方式可配置 | **Step 5** L4_INJECTION_TO_SYSTEM_PROMPT 配置 |
| P2: user_profile.md 结构化 | 暂推后（v5.6） |

---

*v1.2 版本（v1.1 评估扣分项已全部修复）。*
*代码已提交（impl repo 95858e9）。*
*剩余：P1-5（cron 注册）、P1-8（profile dedupe）、P1-9（已 commit）、P2-*（详见合并审计报告）。*
