# Hermem Phase 3: 实施步骤

**项目**: Hermem - Hermes 记忆增强系统
**阶段**: Phase 3
**版本**: v1.0
**日期**: 2026-05-15
**状态**: ✅ 完成（2026-05-16）

> 实施完成，定时 cron 已激活（每天 6:00 和 18:00）

> **前提**: Phase 1 和 Phase 2 必须已完成并验收通过。
> 本文档为 Phase 3 的分步实施指南，每步完成后需通过验证再进入下一步。

---

## 硬性验收指标

### 指标 1: L1 类型准确率 ≥ 80%

- **验证方法**: 人工抽检 50 条 L1 facts
- **判定标准**: `types` 数组中包含正确主类型（允许多标签重叠）
- **通过条件**: 50 条中至少 40 条主类型正确

### 指标 2: 模糊查询召回率 ≥ 85%

- **验证方法**: 用 10 个真实历史问题测试（不指定类型，检查 top-5 结果）
- **判定标准**: 每条问题 top-5 中至少 1 条包含正确答案
- **通过条件**: 10 条问题中至少 9 条满足

---

## 实施步骤总览

| 步骤 | 内容 | 产出 | 风险 |
|------|------|------|------|
| **0** | 准备工作：数据库初始化、目录结构 | `~/.hermes/memory/l0_l3.db` | 低 |
| **1a** | L0 原始会话存档（写入） | `l0_raw/` 目录 | 低 |
| **1b** | L0 配额清理 + 按需加载 | `load_l0_detail()` | 低 |
| **2** | **模拟测试（P0 通过后才能进入 Step 3）** | 验证 L1 提取质量 + 检索召回率 | **P0** |
| **3a** | L1 原子事实提取 + 批量 embedding | `l1_facts` 表 | 中 |
| **3b** | L1 检索（纯语义，无类型过滤） | `vector_search_l1()` | **P0** |
| **3c** | L1 后处理 boost（替代硬过滤） | `retrieve()` 完整流程 | **P0** |
| **4a** | L2 场景聚合（embedding 相似度） | `l2_scenes` 表 | 中 |
| **4b** | L2 定时合并 + dormancy 清理 | scene 生命周期管理 | 中 |
| **5a** | L3 staging area | `l3_staging` 表 | 低 |
| **5b** | L3 确认机制 + user_profile 更新 | staging → profile 流程 | 中 |
| **6** | 端到端集成测试 + 全部指标复测 | 完整流程验证 | 中 |

---

## Step 0: 准备工作

### 0.1 创建目录结构

```bash
mkdir -p ~/.hermes/memory/l0_raw
mkdir -p ~/.hermes/projects/hermem/phase3/impl
```

### 0.2 初始化数据库

```python
# impl/db_init.py
import sqlite3
from pathlib import Path

DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
DB.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB)

# L1 原子事实表
conn.execute("""
CREATE TABLE IF NOT EXISTS l1_facts (
    id              TEXT PRIMARY KEY,
    l0_ref          TEXT NOT NULL,
    types           TEXT NOT NULL,       -- JSON数组
    type_confidence REAL DEFAULT 1.0,
    fallback_type   TEXT DEFAULT 'other',
    content         TEXT NOT NULL,
    tags            TEXT NOT NULL,       -- JSON数组
    value           TEXT NOT NULL,       -- high|medium|low
    chunk_vector    BLOB NOT NULL,       -- float32
    created_at      TEXT NOT NULL,
    status          TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_l1_status ON l1_facts(status);
CREATE INDEX IF NOT EXISTS idx_l1_l0 ON l1_facts(l0_ref);
""")

# L2 场景聚合表
conn.execute("""
CREATE TABLE IF NOT EXISTS l2_scenes (
    id               TEXT PRIMARY KEY,
    scene_type       TEXT NOT NULL,
    topic            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    scene_embedding  BLOB NOT NULL,
    l1_refs          TEXT NOT NULL,       -- JSON数组
    occurrence_count INTEGER DEFAULT 1,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    status           TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_l2_status ON l2_scenes(status);
CREATE INDEX IF NOT EXISTS idx_l2_last_seen ON l2_scenes(last_seen);
""")

# L3 staging area
conn.execute("""
CREATE TABLE IF NOT EXISTS l3_staging (
    id          TEXT PRIMARY KEY,
    fact_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    confirmed   INTEGER DEFAULT 0  -- 0=pending, 1=confirmed, -1=rejected
);
""")

conn.commit()
conn.close()
print("l0_l3.db initialized")
```

**验证**: `python3 impl/db_init.py` 无报错。

---

## Step 1a: L0 原始会话存档

### 1a.1 写入 L0 JSON

```python
# impl/l0_store.py
import json, gzip
from pathlib import Path
from datetime import datetime

L0_DIR = Path.home() / ".hermes" / "memory" / "l0_raw"
QUOTA_BYTES = 500 * 1024 * 1024  # 500MB

def save_l0_raw(session_id: str, messages: list, start: str, end: str) -> str:
    """
    保存原始会话到 L0。
    返回 l0_ref。
    如果会话超过 100 条 messages，自动压缩 tool_calls 输出。
    """
    l0_ref = f"l0_{session_id}"
    payload = {
        "session_id": session_id,
        "l0_ref": l0_ref,
        "start": start,
        "end": end,
        "compressed": False,
        "messages": messages
    }

    # 大会话压缩 tool_calls
    if len(messages) > 100:
        for m in payload["messages"]:
            if "tool_calls" in m and len(str(m["tool_calls"])) > 5000:
                m["tool_calls"] = "[compressed]"
        payload["compressed"] = True

    L0_DIR.mkdir(parents=True, exist_ok=True)
    path = L0_DIR / f"{session_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    enforce_l0_quota()  # 写入后检查配额
    return l0_ref


def enforce_l0_quota():
    """超出配额时，删除最旧的会话直到低于 80% 配额"""
    if not L0_DIR.exists():
        return
    files = sorted(L0_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    if total <= QUOTA_BYTES:
        return
    for f in files:
        if total < QUOTA_BYTES * 0.8:
            break
        total -= f.stat().st_size
        f.unlink()
```

**触发时机**: 会话结束时，与 session-summary 并行执行（异步，不阻塞）。

---

## Step 1b: L0 按需加载 + 配额管理

### 1b.1 加载 L0 细节

```python
# impl/l0_load.py
import json, gzip
from pathlib import Path

L0_DIR = Path.home() / ".hermes" / "memory" / "l0_raw"

def load_l0_detail(l0_ref: str, context_hint: str = None) -> str:
    """
    按需读取 L0。
    context_hint: 关键词过滤，只返回包含该词的 messages。
    """
    session_id = l0_ref.replace("l0_", "")
    l0_path = L0_DIR / f"{session_id}.json"
    if not l0_path.exists():
        return "（原始会话已过期/不存在）"

    with open(l0_path) as f:
        l0 = json.load(f)

    if not context_hint:
        return json.dumps(l0, ensure_ascii=False)

    relevant = [
        m for m in l0["messages"]
        if context_hint.lower() in m.get("content", "").lower()
    ]
    return json.dumps({**l0, "messages": relevant}, ensure_ascii=False, indent=2)
```

**验证**: `python3 -c "from impl.l0_load import load_l0_detail; print(load_l0_detail('l0_test'))"` 无报错。

---

## Step 2: 模拟测试（P0 门禁）

> **此步骤在编码前执行，用于验证 L1 提取质量是否满足硬指标。**
> ~~通过后才能进入 Step 3~~ ✅ P0 已通过（2026-05-16）

### 2.1 准备测试数据集

从 `~/.hermes/memory/sessions/` 选取 10 个真实会话摘要（覆盖不同主题），确保：
- 每个摘要 200-2000 字
- 涵盖 decision、bug-fix、preference、method 等多种类型
- 来自不同日期

```python
# impl/test_l1_extraction.py
import json, os
from pathlib import Path

# 准备 10 个测试摘要
SESSIONS_DIR = Path.home() / ".hermes" / "memory" / "sessions"
test_summaries = []
for f in sorted(SESSIONS_DIR.glob("2026-*.md"))[:10]:
    content = f.read_text()
    # 提取 --- 到第一个 H2 之间的内容作为摘要
    if "---" in content:
        summary = content.split("---", 2)[-1][:2000]
        test_summaries.append({"file": f.name, "summary": summary})

print(f"准备 {len(test_summaries)} 个测试摘要")
```

### 2.2 L1 提取测试

```python
def run_l1_extraction_test():
    """
    对 10 个测试摘要调用 LLM 提取 L1，检查：
    1. 每条 fact 是否有 types/content/tags/value
    2. types 是否为有效类型数组
    3. 提取数量是否合理（1-10 条/session）
    """
    # 使用 Ollama 或 MiniMax 提取
    results = []
    for item in test_summaries:
        facts = extract_l1_facts(item["summary"])
        results.append({"file": item["file"], "facts": facts})
    return results
```

### 2.3 硬指标验证

```python
def verify_hard_metrics(l1_results: list) -> dict:
    """
    硬指标验证：
    1. L1 类型准确率：抽样 50 条，检查主类型是否在数组中
       → Oliver 人工抽检：至少 40/50 正确
    2. 模糊查询召回率：用 10 个真实问题测试 top-5
       → 至少 9/10 包含正确答案
    """
    print("=== 硬指标验证 ===")
    print(f"提取 L1 facts 总数: {sum(len(r['facts']) for r in l1_results)}")
    print("请 Oliver 人工抽检 50 条，判定主类型是否正确")
    print("然后用 10 个真实历史问题测试召回率")
    return {
        "l1_accuracy": None,      # Oliver 填写：40+ = 通过
        "recall_rate": None      # Oliver 填写：9+/10 = 通过
    }
```

### 2.4 模拟检索召回率测试

```python
TEST_QUERIES = [
    "上次怎么处理 SQLite 中文搜索的",
    "Oliver 偏好什么样的回复风格",
    "Hermem Phase 2 用的什么存储方案",
    "微博监控任务的配置是什么样的",
    "StoryAgent 最近的更新是什么",
    "OpenClaw 的 doctor 警告有哪些",
    "飞书机器人的配置问题",
    "Ollama 模型用什么",
    "微信发图片的规则是什么",
    "comic-gen 项目的进展",
]

def test_recall_rate(retrieve_fn, test_queries: list[str]) -> float:
    """
    对每个查询，检查 top-5 结果是否包含正确答案（人工判定）。
    返回召回率。
    """
    correct = 0
    for q in test_queries:
        results = retrieve_fn(q, top_k=5)
        answer = input(f"Q: {q}\n结果: {results['facts'][:2]}\n有正确答案吗? (y/n): ")
        if answer.lower() == "y":
            correct += 1
    rate = correct / len(test_queries)
    print(f"召回率: {rate:.0%} ({correct}/{len(test_queries)})")
    return rate
```

### 2.5 通过条件

```
L1 类型准确率 ≥ 80%  ← Oliver 人工抽检 50 条后填写结果
模糊查询召回率 ≥ 85% ← Oliver 逐条判定后填写结果

两项全部通过 → 进入 Step 3
任一项未通过 → 调整 prompt，重新测试（Step 2）
```

---

## Step 3a: L1 原子事实提取 + 批量 embedding

### 3a.1 实现 L1 提取

```python
# impl/l1_extract.py
import json, requests, uuid, time
from datetime import datetime

OLLAMA_URL = "http://localhost:11434/v1"
OLLAMA_EMBED = "bge-m3:latest"
LLM_MODEL = "qwen3.5:9b-q4_K_M"  # 或 MiniMax

L1_EXTRACT_PROMPT = """你是一个记忆分析器。从以下会话摘要中提取原子事实。

会话摘要：
{SESSION_SUMMARY}

每条事实需要包含：
- types: 类型数组，允许 1-3 个，取自 [decision, bug-fix, preference, method, todo, unresolved]
- content: 事实内容，用中文写，一条完整的陈述句，不超过 80 词
- tags: 标签，2-5 个英文或中文标签，代表主题
- value: 长期价值，high | medium | low（只提取 medium 和 high）

只提取真正有价值的事实，不要流水账。不要编造信息。

输出 JSON 格式：
{{"facts": [{{"types": ["decision"], "content": "...", "tags": ["sqlite"], "value": "high"}}]}}

示例：

输入摘要：讨论了 SQLite FTS5 中文分词问题。Oliver 尝试了 unicode61、trigram、porter 等 tokenizers，全部失败。最终决定用 Python 2-gram 滑动窗口提取关键词，绕过 SQLite 侧的分词限制。

输出：
{{"facts": [{{"types": ["decision", "method"], "content": "Oliver 决定使用 Python 2-gram 滑动窗口解决 SQLite FTS5 中文分词问题，放弃 SQLite 侧 tokenizer 方案", "tags": ["sqlite", "chinese-search", "python"], "value": "high"}}]}}"""


def extract_l1_facts(session_summary: str) -> list[dict]:
    """调用 LLM 提取 L1 facts"""
    prompt = L1_EXTRACT_PROMPT.format(SESSION_SUMMARY=session_summary)
    resp = requests.post(
        f"{OLLAMA_URL}/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2048
        },
        timeout=60
    )
    content = resp.json()["choices"][0]["message"]["content"]
    # 解析 JSON（可能有 markdown 包裹）
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    data = json.loads(content)
    return data.get("facts", [])


def embed_l1_batch(facts: list[dict]) -> list[list[float]]:
    """批量生成 embedding"""
    texts = [f["content"] for f in facts]
    resp = requests.post(
        f"{OLLAMA_URL}/embeddings",
        json={"model": OLLAMA_EMBED, "input": texts},
        timeout=120
    )
    return [item["embedding"] for item in resp.json()["data"]]
```

### 3a.2 写入 L1 facts

```python
def store_l1_batch(facts: list[dict], embeddings: list[list[float]], l0_ref: str, db_path: str):
    """将 L1 facts 和 embedding 批量写入数据库"""
    import sqlite3, struct
    conn = sqlite3.connect(db_path)
    now = datetime.now().isoformat()
    for fact, emb in zip(facts, embeddings):
        fid = f"fact_{uuid.uuid4().hex[:8]}"
        conn.execute("""
            INSERT INTO l1_facts
            (id, l0_ref, types, type_confidence, fallback_type, content, tags, value, chunk_vector, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            fid, l0_ref,
            json.dumps(fact.get("types", ["other"])),
            fact.get("type_confidence", 1.0),
            fact.get("fallback_type", "other"),
            fact["content"],
            json.dumps(fact.get("tags", [])),
            fact.get("value", "medium"),
            struct.pack(f"{len(emb)}f", *emb),
            now
        ))
    conn.commit()
    conn.close()
```

---

## Step 3b: L1 纯语义检索（无类型过滤）

### 3b.1 向量搜索实现

```python
# impl/l1_search.py
import numpy as np, sqlite3, json
from pathlib import Path

DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
OLLAMA_URL = "http://localhost:11434/v1"

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))

def get_query_embedding(query: str) -> np.ndarray:
    import requests
    resp = requests.post(
        f"{OLLAMA_URL}/embeddings",
        json={"model": "bge-m3:latest", "input": query},
        timeout=30
    )
    return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)

def vector_search_l1(query_emb: np.ndarray, top_k: int = 20) -> list[dict]:
    """
    纯语义搜索，返回 top_k 条，不做任何类型过滤。
    """
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, l0_ref, types, content, tags, value, chunk_vector FROM l1_facts WHERE status = 'active'"
    ).fetchall()
    conn.close()

    q = query_emb
    results = []
    for row in rows:
        emb = np.frombuffer(row[6], dtype=np.float32)
        sim = cosine_sim(q, emb)
        results.append({
            "id": row[0],
            "l0_ref": row[1],
            "types": json.loads(row[2]),
            "content": row[3],
            "tags": json.loads(row[4]),
            "value": row[5],
            "_similarity": sim
        })

    results.sort(key=lambda x: x["_similarity"], reverse=True)
    return results[:top_k]
```

---

## Step 3c: L1 后处理 boost（替代硬过滤）

### 3c.1 完整 retrieve 函数

```python
def retrieve(query: str, preferred_types: list[str] = None, top_k: int = 5) -> dict:
    """
    检索入口。
    preferred_types: 用户问题暗示的类型，用于 boost（不是过滤）
    """
    # Step 1: 纯语义搜索 top_k=20
    query_emb = get_query_embedding(query)
    l1_results = vector_search_l1(query_emb, top_k=20)

    # Step 2: 关联到 L2 scenes
    l2_scenes = associate_scenes(l1_results)

    # Step 3: 后处理 boost（不是过滤！）
    if preferred_types:
        scored = []
        for r in l1_results:
            boost = 1.5 if any(t in r["types"] for t in preferred_types) else 1.0
            scored.append((r, r["_similarity"] * boost))
        scored.sort(key=lambda x: x[1], reverse=True)
        l1_results = [r for r, _ in scored[:top_k]]
    else:
        l1_results = l1_results[:top_k]

    return {
        "scenes": l2_scenes,
        "facts": l1_results,
        "query": query,
        "has_l0_detail": any(r["l0_ref"] for r in l1_results)
    }
```

---

## Step 4a: L2 场景聚合

### 4a.1 Scene 聚合逻辑

```python
# impl/l2_aggregate.py
SIMILARITY_THRESHOLD_JOIN = 0.75
SIMILARITY_THRESHOLD_MERGE = 0.85

def compute_scene_embedding(l1_facts: list[dict], ollama_url: str) -> np.ndarray:
    """用关联 L1 的 content 合成 scene embedding"""
    texts = [f["content"] for f in l1_facts]
    combined = " ".join(texts)
    import requests
    resp = requests.post(
        f"{ollama_url}/embeddings",
        json={"model": "bge-m3:latest", "input": combined[:1000]},
        timeout=30
    )
    return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)


def try_aggregate_l2(new_l1_facts: list[dict], db_path: str, ollama_url: str):
    """
    尝试将新的 L1 聚合到现有 scene 或新建 scene。
    """
    if not new_l1_facts:
        return

    new_emb = compute_scene_embedding(new_l1_facts, ollama_url)
    new_emb_bytes = new_emb.tobytes()

    conn = sqlite3.connect(db_path)
    scenes = conn.execute(
        "SELECT * FROM l2_scenes WHERE status = 'active'"
    ).fetchall()

    best_match = None
    best_sim = 0
    for scene in scenes:
        scene_emb = np.frombuffer(scene[4], dtype=np.float32)
        sim = cosine_sim(new_emb, scene_emb)
        if sim > SIMILARITY_THRESHOLD_JOIN and sim > best_sim:
            best_match = scene
            best_sim = sim

    now = datetime.now().isoformat()
    new_l1_ids = [f["id"] for f in new_l1_facts]

    if best_match:
        # 归入现有 scene
        existing_refs = json.loads(best_match[5])
        existing_refs.extend(new_l1_ids)
        occ = best_match[6] + 1
        conn.execute("""
            UPDATE l2_scenes
            SET l1_refs = ?, occurrence_count = ?, last_seen = ?
            WHERE id = ?
        """, (json.dumps(existing_refs), occ, now, best_match[0]))
    else:
        # 新建 scene（需至少 2 条 L1 或 1 条 high value）
        if len(new_l1_facts) >= 2 or any(f["value"] == "high" for f in new_l1_facts):
            topic = new_l1_facts[0]["tags"][0] if new_l1_facts[0]["tags"] else "unknown"
            summary = f"共 {len(new_l1_facts)} 条相关事实，涉及 {topic}"
            fid = f"scene_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO l2_scenes
                (id, scene_type, topic, summary, scene_embedding, l1_refs, occurrence_count, first_seen, last_seen, status)
                VALUES (?, 'ongoing-project', ?, ?, ?, ?, ?, ?, ?, 'active')
            """, (
                fid, topic, summary, new_emb_bytes,
                json.dumps(new_l1_ids), 1, now, now
            ))

    conn.commit()
    conn.close()
```

---

## Step 4b: L2 定时合并 + dormancy 清理

```python
# impl/l2_maintenance.py
from datetime import datetime, timedelta

SCENE_DORMANT_DAYS = 60

def check_scene_dormancy(db_path: str):
    """每日定时：将 60 天无新 L1 的 scene 标记为 dormant"""
    conn = sqlite3.connect(db_path)
    cutoff = (datetime.now() - timedelta(days=SCENE_DORMANT_DAYS)).isoformat()
    conn.execute("""
        UPDATE l2_scenes SET status = 'dormant'
        WHERE status = 'active' AND last_seen < ?
    """, [cutoff])
    conn.commit()
    conn.close()


def merge_duplicate_scenes(db_path: str, ollama_url: str):
    """
    每日定时：相似度 > 0.85 的 scene 合并。
    合并后重新生成 summary。
    """
    import requests
    conn = sqlite3.connect(db_path)
    scenes = conn.execute(
        "SELECT * FROM l2_scenes WHERE status = 'active'"
    ).fetchall()

    merged = set()
    for i, s1 in enumerate(scenes):
        if s1[0] in merged:
            continue
        for s2 in scenes[i+1:]:
            if s2[0] in merged:
                continue
            emb1 = np.frombuffer(s1[4], dtype=np.float32)
            emb2 = np.frombuffer(s2[4], dtype=np.float32)
            if cosine_sim(emb1, emb2) > SIMILARITY_THRESHOLD_MERGE:
                # 合并 s2 into s1
                refs1 = json.loads(s1[5])
                refs2 = json.loads(s2[5])
                refs1.extend(refs2)
                # 重新生成 summary
                l1s = conn.execute(
                    "SELECT content FROM l1_facts WHERE id IN (?)",
                    [json.dumps(refs1)]
                ).fetchall()
                combined = " ".join([l[0] for l in l1s])
                new_summary = f"[Merged scene, {len(refs1)} facts]"
                # 更新 s1
                conn.execute("""
                    UPDATE l2_scenes
                    SET l1_refs = ?, summary = ?, occurrence_count = ?
                    WHERE id = ?
                """, (json.dumps(refs1), new_summary, len(refs1), s1[0]))
                # 删除 s2
                conn.execute("DELETE FROM l2_scenes WHERE id = ?", [s2[0]])
                merged.add(s2[0])

    conn.commit()
    conn.close()
```

---

## Step 5a: L3 Staging Area

```python
# impl/l3_staging.py
import sqlite3, json
from datetime import datetime

def stage_preference(fact_id: str, content: str, source: str, db_path: str):
    """当 L1 中有 type=preference 时，存入 staging area"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR IGNORE INTO l3_staging (id, fact_id, content, source, created_at, confirmed)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (f"staging_{uuid.uuid4().hex[:8]}", fact_id, content, source, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_pending_preferences(db_path: str, limit: int = 5) -> list[dict]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, content, source FROM l3_staging WHERE confirmed = 0 ORDER BY created_at LIMIT ?"
    , [limit]).fetchall()
    conn.close()
    return [{"id": r[0], "content": r[1], "source": r[2]} for r in rows]


def confirm_preference(staging_id: str, db_path: str, profile_path: str):
    """Oliver 确认后，写入 user_profile.md"""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT content, source FROM l3_staging WHERE id = ?", [staging_id]
    ).fetchone()
    if not row:
        return
    conn.execute("UPDATE l3_staging SET confirmed = 1 WHERE id = ?", [staging_id])
    conn.commit()
    conn.close()

    # 追加到 user_profile.md
    entry = f"- {row[0]} (来源: {row[1]})\n"
    with open(profile_path, "a") as f:
        f.write(entry)
```

---

## Step 5b: L3 确认机制

```python
# impl/l3_confirm.py
def process_l3_staging(db_path: str, profile_path: str, notify_fn):
    """
    每日定时任务：staging 满 5 条时推送确认消息给 Oliver。
    notify_fn: 推送消息的函数（如飞书/微信发送函数）
    """
    pending = get_pending_preferences(db_path, limit=5)
    if len(pending) < 5:
        return

    msg = "以下是从最近会话中提取的偏好，请确认哪些想保留到个人画像：\n\n"
    for i, p in enumerate(pending, 1):
        msg += f"{i}. {p['content']} (来源: {p['source']})\n"
    msg += "\n回复编号确认，回复「跳过」忽略本次"

    notify_fn(msg)  # 发送到 Oliver

    # Oliver 回复后，由消息路由调用 confirm_preference() 处理
```

---

## Step 6: 端到端集成测试

### 6.1 完整流程测试

```python
def e2e_test(session_id: str, messages: list, start: str, end: str):
    """
    端到端测试：
    1. L0 保存
    2. L1 提取 + 写入
    3. L2 聚合
    4. L1 检索 + boost
    5. 验证结果正确性
    """
    # 1. L0
    l0_ref = save_l0_raw(session_id, messages, start, end)
    assert Path(L0_DIR / f"{session_id}.json").exists()

    # 2. 获取 session summary（复用 Phase 1）
    summary = get_session_summary(session_id)

    # 3. L1 提取
    facts = extract_l1_facts(summary)
    if not facts:
        return {"status": "no_facts_extracted"}
    embeddings = embed_l1_batch(facts)
    store_l1_batch(facts, embeddings, l0_ref, DB)

    # 4. L2 聚合
    try_aggregate_l2(facts, DB, OLLAMA_URL)

    # 5. 检索测试（用 summary 中的关键词）
    test_query = summary[:100]
    results = retrieve(test_query)

    return {
        "l0_ref": l0_ref,
        "facts_extracted": len(facts),
        "l2_scenes_count": count_scenes(DB),
        "top_result": results["facts"][0] if results["facts"] else None,
    }
```

### 6.2 全部指标复测

```python
def final_verification():
    """
    全部硬指标复测，必须全部通过才算 Phase 3 完成：
    1. L1 类型准确率 ≥ 80%（人工抽检）
    2. 模糊查询召回率 ≥ 85%（10 个问题）
    3. L2 聚合有效（相同 topic 3 次讨论 → 1 个 scene）
    4. L0 可按需加载（l0_ref 追溯成功）
    """
    print("=== Phase 3 最终验收 ===")
    print("请 Oliver 执行以下验证：")
    print("1. 人工抽检 50 条 L1 的 fact_type 准确率（≥80%）")
    print("2. 用 10 个真实历史问题测试召回率（≥85%）")
    print("3. 检查 scene 聚合是否正确（相同 topic 自动合并）")
    print("4. 测试 L0 追溯是否成功（fact → L0 原始会话）")
```

---

## 完成标准

| 指标 | 目标 | 验证方法 |
|------|------|----------|
| L1 类型准确率 | ≥ 80% | 人工抽检 50 条 |
| 模糊查询召回率 | ≥ 85% | 10 个真实问题测试 |
| L2 聚合 | 相同 topic 3 次讨论 → 1 scene | 模拟测试验证 |
| L0 追溯 | 每个 L1 可追溯到原始会话 | 抽样检查 |
| L3 staging | 满 5 条正确推送确认 | 模拟测试 |

---

## 实施顺序

```
Step 0 → Step 1a → Step 1b
                    ↓
              Step 2（门禁测试）
                    ↓（通过）
Step 3a → Step 3b → Step 3c
                    ↓
Step 4a → Step 4b
                    ↓
Step 5a → Step 5b
                    ↓
              Step 6（最终验收）
```

---

## 风险提醒

- **Step 2 未通过 → 不能进入 Step 3**：这是硬性门禁，L1 提取质量不达标后续全部白做
- **Ollama 服务稳定性**：L1 提取和 embedding 生成依赖 Ollama，提前检查 `ollama list` 确认 bge-m3:latest 就绪
- **数据库并发**：多步操作使用同一个 `l0_l3.db`，确保写入顺序正确（L0 → L1 → L2）
