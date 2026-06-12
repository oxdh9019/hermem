# Hermem Phase 2: 语义搜索增强方案

**项目**: Hermem - Hermes 记忆增强系统
**阶段**: Phase 2
**版本**: v1.0
**日期**: 2026-05-01
**状态**: 待 Oliver 确认后实施（**注**:Phase 2 实际已完成,见 `phase2/TODO.md` 状态行 ✅ + `phase2/REVIEW.md` ✅。本 SPEC 是 V2 阶段设计文档;V3+ 后续迭代见 `phase3/SPEC.md` / V4-V6 各版本 SPEC）

---

## 1. 背景与动机

### 1.1 现有系统的局限

Hermes Agent 当前使用 **SQLite FTS5** 实现会话历史搜索，核心限制：

| 限制 | 表现 |
|------|------|
| **关键词依赖** | 必须猜对关键词才能搜到 |
| **同义词无法匹配** | "SQLite 问题"搜不到"数据库故障" |
| **字面顺序** | 短语匹配必须字面顺序一致 |

### 1.2 语义搜索的核心价值

Embedding 向量搜索可以在**意思层面**匹配，不依赖文字重合度：

```
查询: "上次处理数据库连接失败的方法"

FTS5 结果: 可能找不到（因为记忆里写的是"SQLite connection timeout"）
语义搜索: 能召回（语义相近）
```

### 1.3 设计约束

- **不引入重型依赖**：不装 ChromaDB/Pinecone/Milvus
- **复用现有架构**：SQLite + Hermes 现有工具链
- **可选叠加**：FTS5 仍然保留，语义搜索作为补充
- **API 调用成本低**：MiniMax Embedding 便宜且内网直连

---

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│  用户查询: "上次处理数据库问题的方法"                      │
└─────────────────┬───────────────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────────────┐
│  查询路由层 (hybrid_search)                              │
│  ┌──────────────────┐    ┌──────────────────────────┐  │
│  │  语义搜索分支     │    │  FTS5 关键词分支          │  │
│  │  Query → Embed  │    │  原始查询直接 FTS5        │  │
│  │  → 向量相似度    │    │  → BM25 排序             │  │
│  └────────┬─────────┘    └────────────┬─────────────┘  │
│           │                           │                 │
│           │         ┌────────────────┘                 │
│           ▼         ▼                                  │
│  ┌──────────────────────────────────────────────┐     │
│  │  结果融合层 (RRF 融合)                         │     │
│  │  语义 Top-K + FTS5 Top-K → RRF 合并排序        │     │
│  └──────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────┘
```

### 2.2 数据层

新增一张 SQLite 表，**不改动现有 hermes_state.py**：

```sql
-- 向量索引表（附加到 Hermes 现有数据库）
CREATE TABLE memory_embeddings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,          -- 关联会话 ID
    chunk_index     INTEGER NOT NULL,       -- 分块序号
    text            TEXT NOT NULL,          -- 原始文本片段
    embedding       BLOB NOT NULL,          -- float32 向量序列化
    concept_tags    TEXT,                   -- JSON: ["preference", "bug-fix"]
    created_at      REAL NOT NULL,          -- Unix timestamp
    memory_type     TEXT NOT NULL,          -- 'session_summary' | 'user_profile' | 'skill'
    source_file     TEXT,                   -- 来源文件路径（可选）
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX idx_emb_session ON memory_embeddings(session_id);
CREATE INDEX idx_emb_type   ON memory_embeddings(memory_type);
```

> **注意**：不改动 `hermes_state.py` 的 SCHEMA_SQL，向量表在 Hermem 自己的 SQLite 文件中管理。

### 2.3 存储文件布局

```
~/.hermes/
├── memory/
│   └── embeddings.db          # Hermem 向量索引（新建）
├── skills/hermem/
│   ├── SKILL.md
│   ├── memory-tools/
│   │   ├── embedding_store.py    # 写入向量
│   │   ├── hybrid_search.py      # 混合搜索
│   │   └── concept_tagger.py     # 自动标签提取
│   └── ...
```

---

## 3. 核心模块设计

### 3.1 Embedding 客户端

**文件**: `memory-tools/embedding_client.py`

```python
"""
MiniMax Embedding 客户端
使用 text-embedding-3-small（便宜、快速、中文效果好）
"""
import os
import requests
from typing import Literal

_EMBED_MODEL = "embo"

def get_embedding(text: str, model: str = _EMBED_MODEL) -> list[float]:
    """调用 MiniMax Embedding API，返回归一化向量"""
    api_key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_API_KEY")
    base_url = os.environ.get("MINIMAX_API_URL", "https://api.minimax.io")

    resp = requests.post(
        f"{base_url}/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]  # list[float]
```

> **Fallback**: 如果 MiniMax API 不可用，降级到 Ollama 本地 embedding（`nomic-embed-text`）。

### 3.2 向量存储与检索

**文件**: `memory-tools/embedding_store.py`

```python
"""
向量存储：SQLite BLOB + numpy 余弦相似度
"""
import numpy as np
import sqlite3
import struct
from pathlib import Path

EMBEDDINGS_DB = Path.home() / ".hermes" / "memory" / "embeddings.db"
EMBED_DIM = 1024  # MiniMax embo 输出维度

def _serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def _deserialize(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.frombuffer(blob, dtype=np.float32, count=n)

def store_chunk(session_id: str, text: str, embedding: list[float],
                chunk_index: int, memory_type: str, concept_tags: list[str] = None):
    """存储单个文本块及其向量"""
    import time, json
    conn = sqlite3.connect(EMBEDDINGS_DB)
    conn.execute("""
        INSERT INTO memory_embeddings
        (session_id, chunk_index, text, embedding, concept_tags, created_at, memory_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, chunk_index, text, _serialize(embedding),
          json.dumps(concept_tags or []), time.time(), memory_type))
    conn.commit()
    conn.close()

def search_semantic(query_embedding: list[float], top_k: int = 5,
                    memory_types: list[str] = None) -> list[dict]:
    """
    语义搜索：返回 top_k 个最相似记忆块
    返回格式: [{"chunk_id", "session_id", "text", "score", "concept_tags"}, ...]
    """
    import json
    conn = sqlite3.connect(EMBEDDINGS_DB)
    cursor = conn.execute(
        "SELECT id, session_id, text, embedding, concept_tags FROM memory_embeddings"
    )
    q_vec = np.array(query_embedding, dtype=np.float32)
    results = []
    for row in cursor.fetchall():
        emb = _deserialize(row[3])
        score = float(np.dot(q_vec, emb) / (np.linalg.norm(q_vec) * np.linalg.norm(emb) + 1e-8))
        results.append({
            "chunk_id": row[0],
            "session_id": row[1],
            "text": row[2],
            "score": score,
            "concept_tags": json.loads(row[4]),
        })
    conn.close()
    # 余弦相似度排序，取 top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
```

### 3.3 混合搜索

**文件**: `memory-tools/hybrid_search.py`

```python
"""
混合搜索：语义向量搜索 + FTS5 关键词搜索 → RRF 融合排序
"""
import json
from .embedding_client import get_embedding
from .embedding_store import search_semantic

def hybrid_search(query: str, top_k: int = 5, memory_types: list[str] = None):
    """
    融合语义和 FTS5 的搜索结果

    RRF (Reciprocal Rank Fusion):
    score = Σ 1/(k + rank_i)，k 通常取 60
    """
    K = 60  # RRF 常数

    # 1. 语义搜索
    query_emb = get_embedding(query)
    semantic_results = search_semantic(query_emb, top_k=top_k * 2,
                                       memory_types=memory_types)

    # 2. FTS5 搜索（调用 Hermes 现有 session_search）
    # 注意：这里复用 Hermes 内置的 FTS5，不重复造轮子
    from hermes_state import SessionDB
    db = SessionDB()
    raw_fts = db.search_messages(query=query, limit=top_k * 2)
    fts_results = [{"session_id": r["session_id"], "text": r["content"],
                    "score": 1.0, "concept_tags": []} for r in raw_fts]

    # 3. RRF 融合
    rrf_scores: dict[str, float] = {}
    for rank, item in enumerate(semantic_results):
        key = f"sem_{item['chunk_id']}"
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (K + rank + 1)
    for rank, item in enumerate(fts_results):
        key = f"fts_{item['session_id']}"
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (K + rank + 1)

    # 合并排序
    fused = []
    seen_texts = set()
    for key, score in sorted(rrf_scores.items(), key=lambda x: -x[1])[:top_k]:
        if key.startswith("sem_"):
            chunk_id = int(key.split("_")[1])
            item = next(i for i in semantic_results if i["chunk_id"] == chunk_id)
            text = item["text"]
        else:
            sid = key.split("_", 1)[1]
            item = next(i for i in fts_results if i["session_id"] == sid)
            text = item["text"]
        # 去重：相似文本去重
        if text[:50] in seen_texts:
            continue
        seen_texts.add(text[:50])
        fused.append({**item, "rrf_score": score})

    return fused
```

### 3.4 概念标签自动提取

**文件**: `memory-tools/concept_tagger.py`

```python
"""
自动从记忆中提取概念标签
预定义标签体系（Phase 2 目标）
"""
import re

CONCEPT_TAGS = {
    "preference",    # Oliver 的偏好和习惯
    "decision",      # 技术决策
    "bug-fix",       # BUG 和解决方案
    "architecture",  # 系统架构
    "project",       # 具体项目（StoryAgent、微博监控等）
    "tool-usage",   # 工具使用模式
    "learning",     # 学到的知识/方法论
    "todo",         # 待办
    "unresolved",   # 未解决问题
}

# 关键词 → 标签 映射
TAG_KEYWORDS = {
    "preference":   ["喜欢", "偏好", "不用", "不要", "总是", "从来不"],
    "decision":     ["决定", "采用", "选择", "最终方案", "结论是"],
    "bug-fix":      ["bug", "错误", "修复", "问题", "失败", "报错"],
    "architecture": ["架构", "架构设计", "系统结构", "模块"],
    "project":      ["StoryAgent", "微博监控", "Hermem", "微信"],
    "tool-usage":   ["命令", "terminal", "执行", "脚本"],
    "learning":     ["学到了", "理解了", "明白了", "方法论"],
    "todo":         ["待办", "TODO", "还没做", "下一步"],
    "unresolved":   ["没解决", "悬而未决", "待研究", "不清楚"],
}

def extract_concepts(text: str) -> list[str]:
    """基于关键词规则快速提取标签（无 LLM 调用）"""
    tags = set()
    for tag, keywords in TAG_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                tags.add(tag)
                break
    return list(tags)

def extract_concepts_with_llm(text: str) -> list[str]:
    """基于 LLM 提取更准确的标签（可选，精度更高）"""
    from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
    import asyncio

    prompt = f"""从以下记忆文本中提取概念标签。
可用标签: {', '.join(sorted(CONCEPT_TAGS))}
只返回标签列表，用逗号分隔，不要其他内容。

记忆文本:
{text[:500]}"""

    response = asyncio.run(async_call_llm(
        task="concept_tagging",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=50,
    ))
    content = extract_content_or_reasoning(response)
    if content:
        tags = [t.strip() for t in content.split(",") if t.strip() in CONCEPT_TAGS]
        return tags
    return extract_concepts(text)  # 回退到规则匹配
```

---

## 4. 实施步骤

### Step 1: 环境准备

```bash
# 1. 确保 Hermem 目录存在
mkdir -p ~/.hermes/memory
mkdir -p ~/.hermes/skills/hermem/memory-tools

# 2. 安装必要依赖（如果 Ollama 用于 fallback）
# ollama pull nomic-embed-text
```

### Step 2: 创建向量索引数据库

```bash
python3 << 'EOF'
import sqlite3, struct
from pathlib import Path

db_path = Path.home() / ".hermes" / "memory" / "embeddings.db"
db_path.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(db_path)
conn.execute("""
CREATE TABLE IF NOT EXISTS memory_embeddings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    embedding       BLOB NOT NULL,
    concept_tags    TEXT DEFAULT '[]',
    created_at      REAL NOT NULL,
    memory_type     TEXT NOT NULL,
    source_file     TEXT
);
CREATE INDEX IF NOT EXISTS idx_emb_session ON memory_embeddings(session_id);
CREATE INDEX IF NOT EXISTS idx_emb_type   ON memory_embeddings(memory_type);
""")
conn.commit()
conn.close()
print("Done: embeddings.db created")
EOF
```

### Step 3: 实现 embedding 客户端

**文件**: `~/.hermes/skills/hermem/memory-tools/embedding_client.py`

（见上方 3.1 节代码）

### Step 4: 实现向量存储与检索

**文件**: `~/.hermes/skills/hermem/memory-tools/embedding_store.py`

（见上方 3.2 节代码）

### Step 5: 实现混合搜索

**文件**: `~/.hermes/skills/hermem/memory-tools/hybrid_search.py`

（见上方 3.3 节代码）

### Step 6: 实现概念标签提取

**文件**: `~/.hermes/skills/hermem/memory-tools/concept_tagger.py`

（见上方 3.4 节代码）

### Step 7: 修改 session-summary Skill（自动 embedding）

修改 `~/.hermes/skills/hermem/session-summary/` 中的摘要生成逻辑：

```python
# 在摘要生成后、写入文件前，调用：
from memory_tools.embedding_store import store_chunk
from memory_tools.embedding_client import get_embedding
from memory_tools.concept_tagger import extract_concepts

# 对摘要文本分块（避免超长）
chunks = chunk_text(summary_text, chunk_size=300, overlap=50)
for i, chunk in enumerate(chunks):
    emb = get_embedding(chunk)
    tags = extract_concepts(chunk)
    store_chunk(
        session_id=session_id,
        text=chunk,
        embedding=emb,
        chunk_index=i,
        memory_type="session_summary",
        concept_tags=tags,
    )
```

### Step 8: 修改 memory-warmup Skill（语义预热）

修改 `~/.hermes/skills/hermem/memory-warmup/` 中的注入逻辑：

```python
# 在现有 FTS5 搜索后，增加语义搜索分支
from memory_tools.hybrid_search import hybrid_search

# Oliver 问 "上次讨论 X" 时，优先用语义搜索
semantic_results = hybrid_search(query, top_k=3)
# 将语义搜索结果注入到 system prompt
```

### Step 9: 测试验证

```bash
# 测试 1: 手动写入一条记忆，验证向量存储
python3 -c "
from memory_tools.embedding_client import get_embedding
from memory_tools.embedding_store import store_chunk, search_semantic

text = 'Oliver 喜欢用 MiniMax 模型处理中文任务'
emb = get_embedding(text)
store_chunk('test-session', text, emb, 0, 'user_profile', ['preference'])
results = search_semantic(emb, top_k=3)
print('Stored and retrieved:', results[0]['text'] if results else 'NONE')
"

# 测试 2: 混合搜索质量对比
# 问: "Oliver 用什么模型跑任务"
# 对比: FTS5 结果 vs 混合搜索结果
```

---

## 5. 与现有系统的关系

### 5.1 不改动 Hermes 核心

- **hermes_state.py**: 完全不动
- **session_search_tool.py**: 不改，向量搜索是另一条路
- **现有 FTS5**: 保留，语义搜索是增强而非替换

### 5.2 Hermem Phase 2 与 Phase 1 的关系

```
Phase 1: 会话摘要 → 写入 ~/.hermes/memory/sessions/
Phase 2: 摘要自动向量索引 → 语义搜索召回
         ↓
         两者叠加：摘要既在文件系统，又在向量索引
```

### 5.3 渐进策略

| 阶段 | 搜索方式 | 备注 |
|------|---------|------|
| 当前 | 纯 FTS5 | 关键词匹配 |
| Phase 2 | FTS5 + 语义混合 | 增强，不破坏现有 |
| 未来可选 | 纯语义（可选关闭 FTS5） | 如果语义质量足够好 |

---

## 6. 成本估算

| 项目 | 单次成本 | 估算 |
|------|---------|------|
| MiniMax Embedding | ¥0.001/千 token | 每天 10 次调用 ≈ ¥0.01 |
| 存储 | SQLite BLOB | 1 万条记忆 ≈ ~50MB |
| API 延迟 | ~200ms | 可接受 |

**总计**: 基本可忽略，MiniMax 额度足够用。

---

## 7. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| MiniMax Embedding API 不可用 | 语义搜索完全失败 | 降级到 Ollama 本地 embedding |
| 向量索引与 FTS5 结果不一致 | 用户困惑 | 明确告知"语义搜索补充"而非替代 |
| 摘要质量不高导致向量索引低质 | 记忆失效 | Phase 1 先完成，确保摘要质量 |
| 中文分块效果差 | 召回率低 | 优先按段落切分，尊重句子边界 |

---

## 8. 完成标准

1. **语义搜索可用**: 问"上次处理 X 问题"能召回语义相近的记忆
2. **混合搜索工作**: FTS5 + 语义 RRF 融合正常
3. **概念标签提取**: 摘要自动带标签（rule-based）
4. **无破坏性变更**: 现有 FTS5 搜索完全不受影响
5. **可独立卸载**: 删除 `memory-tools/` 和 `embeddings.db` 不影响 Hermes 核心

---

## 9. 参考资料

- Mem0 架构: https://github.com/mem0ai/mem0
- StoryAgent 向量存储: `~/.hermes/knowledge/tech/storyagent-v5/src/state/vector_store.py`
- Hermes SessionDB: `~/.hermes/hermes-agent/hermes_state.py`
- RRF 融合算法: ` Reciprocal Rank Fusion` (Manmatha et al., 2021)
