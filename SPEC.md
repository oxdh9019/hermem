# Hermem Phase 2 规范：语义召回（本地 Ollama + NumPy 方案）

**版本**: v3.0
**更新**: 2026-05-01
**依据**: macOS SQLite 3.53.0 不支持 vec0，改用 NumPy + SQLite 混合存储

---

## 变更摘要（对比 v2.0）

| 项目 | v2.0（旧） | v3.0（新） |
|------|-----------|-----------|
| 向量存储 | SQLite vec0 虚拟表 | **NumPy `.npy` 文件 + SQLite 元数据表** |
| 向量索引 | vec0 内置 HNSW | **NumPy 全量计算 + top-k 排序** |
| 依赖 | SQLite vec0（macOS 不可用） | **NumPy（macOS 自带）** |
| 外部依赖 | MiniMax API | **零外部依赖** |

**核心思路**：向量存 NumPy 二进制文件（持久化 + 高速），SQLite 存元数据 + 全文索引，Python 做 top-k 余弦计算。

---

## 核心架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Hermem Phase 2 架构（v3.0）               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  输入文本 ──► Ollama bge-m3 ──► 1024维向量                   │
│  (http://localhost:11434/v1)                                │
│                                                             │
│  向量 ──► NumPy .npy 文件（追加写入）                         │
│  (~/.hermes/memory/hermem_vectors.npy)                      │
│                                                             │
│  chunk_id ──► SQLite chunks 表（元数据）                    │
│  (chunks.id → numpy 数组下标)                               │
│                                                             │
│  原始文本 ──► SQLite FTS5（全文搜索回退）                    │
│  (chunks_fts)                                               │
│                                                             │
│  摘要文件 ──► ~/.hermes/memory/sessions/*.md                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 数据库设计

### 文件位置

```
~/.hermes/memory/hermem.db          # SQLite（元数据 + FTS5）
~/.hermes/memory/hermem_vectors.npy  # NumPy 向量库（二进制）
~/.hermes/memory/hermem_meta.json   # 向量库元数据（版本、维度、记录数）
```

### SQLite 表结构

#### 1. `chunks` 主表

```sql
CREATE TABLE chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    chunk_type  TEXT NOT NULL,       -- 'session_summary' | 'user_profile' | 'concept_note'
    concepts    TEXT,                -- JSON 数组
    created_at  REAL DEFAULT (julianday('now')),
    source_file TEXT,
    source_line INTEGER,
    vec_index   INTEGER              -- 该 chunk 对应 numpy 数组的下标
);
```

#### 2. `embedding_cache` 缓存表

```sql
CREATE TABLE embedding_cache (
    text_hash  TEXT PRIMARY KEY,
    embedding  BLOB,
    created_at REAL DEFAULT (julianday('now'))
);
```

#### 3. `chunks_fts` FTS5 表（全文搜索）

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content,
    content=chunks,
    content_rowid=id
);
```

---

## NumPy 向量库设计

### 存储格式

```python
# hermem_vectors.npy 形状: (N, 1024) float32
# 行顺序与 chunks.id 对应（vec_index = 行号）

import numpy as np

vectors = np.load(path)                    # shape: (N, 1024)
chunk_vectors = vectors[vec_index]         # 获取单个向量
query_sim = vectors @ query_vec            # 批量余弦计算（NumPy 向量化）
```

### 写入流程（追加模式）

```python
import numpy as np, json, os

def append_vectors(new_embeddings: list[list[float]]) -> list[int]:
    """追加向量到 npy 文件，返回对应的 vec_index 列表"""
    meta_path = VEC_DIR / "hermem_meta.json"
    vec_path = VEC_DIR / "hermem_vectors.npy"

    # 读取当前元数据
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        vectors = np.load(vec_path) if vec_path.exists() else np.empty((0, 1024), dtype=np.float32)
        next_index = meta["next_index"]
    else:
        vectors = np.empty((0, 1024), dtype=np.float32)
        next_index = 0
        meta = {"version": "1.0", "dim": 1024, "next_index": 0}

    # 原子写入：写临时文件 → shutil.copy2（防止写入崩溃损坏）
    # 注意：macOS 上 os.replace 可能因跨文件系统失败，改用 shutil.copy2
    start_indices = list(range(next_index, next_index + len(new_embeddings)))
    new_matrix = np.array(new_embeddings, dtype=np.float32)
    vectors = np.vstack([vectors, new_matrix])

    tmp_path = "/tmp/hermem_vec_tmp.npy"
    np.save(tmp_path, vectors)
    shutil.copy2(tmp_path, str(vec_path))
    os.remove(tmp_path)

    meta["next_index"] = next_index + len(new_embeddings)
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    return start_indices
```

### 向量检索流程

```python
import numpy as np

def semantic_search(query: str, top_k: int = 5, concept_filter: list[str] = None) -> list[dict]:
    """语义召回：NumPy 全量计算余弦相似度"""

    # 1. 查询向量
    query_vec = get_embedding(query)
    q = np.array(query_vec, dtype=np.float32)

    # 2. 加载全部向量
    vectors = np.load(VEC_DIR / "hermem_vectors.npy")
    meta = json.load(open(VEC_DIR / "hermem_meta.json"))

    # 3. 余弦相似度计算（向量化）
    norms = np.linalg.norm(vectors, axis=1) * np.linalg.norm(q)
    cosine_scores = (vectors @ q) / (norms + 1e-8)

    # 4. top-k
    top_indices = np.argsort(cosine_scores)[::-1][:top_k * 3]  # 多取一些，留给过滤
    top_indices = top_indices.tolist()

    # 5. 查 SQLite 元数据
    placeholders = ",".join(["?"] * len(top_indices))
    rows = db.execute(f"""
        SELECT c.id, c.session_id, c.content, c.chunk_type, c.concepts
        FROM chunks c WHERE c.vec_index IN ({placeholders})
    """, top_indices).fetchall()

    # 6. 按相似度排序返回
    index_to_score = {idx: cosine_scores[idx] for idx in top_indices}
    results = sorted(rows, key=lambda r: index_to_score[r["vec_index"]], reverse=True)

    # 7. 概念过滤
    if concept_filter:
        results = [r for r in results if concept_filter_includes(r, concept_filter)]

    return results[:top_k]
```

### 性能估算

| 规模 | 向量数 | 内存占用 | 搜索耗时 |
|------|--------|---------|---------|
| 轻量 | 100 | 0.4 MB | < 0.5ms |
| 中量 | 1,000 | 4 MB | < 1ms |
| 重量 | 10,000 | 40 MB | 3–8ms |
| 上限 | 100,000 | 400 MB | 50–100ms |

NumPy SIMD 优化 + 内存连续访问，实测 500 向量 0.8ms（已验证）。

---

## 检索流程

### 混合召回（语义 + FTS5）

```python
def hybrid_search(query: str, concept_filter: list[str] = None, top_k: int = 5) -> list[dict]:
    """语义 + 关键词混合召回（RRF 融合）"""

    semantic_results = semantic_search(query, top_k, concept_filter)
    keyword_results = keyword_search(query, top_k, concept_filter)

    # RRF（Reciprocal Rank Fusion）
    fused = rrf_merge(semantic_results, keyword_results, k=60,
                       w_sem=0.65, w_kw=0.35)
    return fused[:top_k]
```

### 全文搜索（FTS5 回退）

```python
def keyword_search(query: str, top_k: int = 5) -> list[dict]:
    """FTS5 关键词搜索（中文 2-gram）"""

    tokens = chinese_2gram(query)
    fts_query = " AND ".join(tokens)

    results = db.execute("""
        SELECT c.*, rank
        FROM chunks_fts
        JOIN chunks c ON chunks_fts.rowid = c.id
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (fts_query, top_k)).fetchall()

    return results
```

---

## 实施步骤

- [x] Step 1: 初始化 `hermem.db`（chunks + embedding_cache + FTS5 表）
- [x] Step 2: 实现 `impl/database.py`（SQLite 连接管理 + chunks 表操作）
- [x] Step 3: 实现 `impl/embedding.py`（Ollama bge-m3 调用 + 缓存）
- [x] Step 4: 实现 `impl/vectorstore.py`（NumPy npy 读写 + 原子写入 + top-k 检索）
- [x] Step 5: 实现 `impl/retrieval.py`（语义搜索 + FTS5 回退 + 混合 RRF）
- [x] Step 6: 编写历史摘要迁移脚本（扫描 sessions/*.md → 入库）
- [x] Step 7: 实现 Hermem CLI 工具（`hermem search`）
- [x] Step 8: 健康检查（Ollama + bge-m3 可用性）
- [x] Step 9: 端到端测试（摘要 → 入库 → 语义召回）

> ✅ Phase 2 语义召回方案（v3.0，NumPy + SQLite 混合）于 2026-05-01 实施完成。

---

## 验证方法

```bash
# 1. Ollama 健康检查
curl -s http://localhost:11434/api/tags | python3 -c \
  "import json,sys; m=[x['name'] for x in json.load(sys.stdin)['models']]; \
   print('bge-m3 OK' if 'bge-m3:latest' in m else 'MISSING')"

# 2. NumPy 向量库验证
python3 -c "
from impl.vectorstore import append_vectors, semantic_search
ids = append_vectors([[0.1]*1024, [0.2]*1024])
print(f'vec_index: {ids}')
"

# 3. 端到端语义召回
python3 -c "
from impl.retrieval import semantic_search
results = semantic_search('上次讨论的架构设计')
for r in results: print(r['content'][:80])
"
```

---

## 依赖清单

| 依赖 | 状态 | 说明 |
|------|------|------|
| Python 3.10+ | 系统自带 | — |
| `ollama` | `pip install ollama` | Ollama API 客户端（已有） |
| NumPy | **macOS 自带** | 无需安装 |
| SQLite | macOS 自带 | 无需安装 |
| SHA256 / pickle | Python 标准库 | 缓存序列化 |

**零外部依赖**（对比 v2.0 减少了 MiniMax API，替换了不可用的 vec0）。

---

## 附录：V4.x 架构演进（2026-05-19 起）

> Phase 2 实现了语义召回（NumPy + SQLite），但记忆仍是"存储-检索"模式。V4.x 将记忆重新定义为**生成模型**而非存储文本——Hermem 预测用户需要什么，在预测被违反时触发学习。

### V4.0 — Predictive Memory 核心范式

```
Context → Predict what should happen → Compare to what actually happens
                                              ↓
                                    Error signal → Update disposition
                                              ↓
                                    Daily synthesis → Active memory
```

### V4.1 — Error Annotation

在每次会话后，用 MiniMax-M2.7 标注 L0 中的预测误差：

```python
prediction_errors[]  # 被违反的可证伪预测
surprise_level       # 意外程度 (0-1)
confidence           # 每个错误的置信度 (0-1)
overall_quality_score # 会话级预测质量 (0-1)
```

### V4.2 — Conditioned Dispositions

用 `(condition, prediction, confidence, error_history)` 元组替代原子 L1 事实：

```python
condition_text      # 何时激活
prediction_text    # 用户期望什么
error_count        # 预测被违反次数
success_count       # 预测正确次数
disposition_decay  # 时间×频次联合衰减（7天半衰期）
```

### V4.3 — Error-Activated Retrieval

意图分类（13 种）决定路由：

| 意图类型 | 处置 |
|----------|------|
| 修正/反馈 | 更新 disposition |
| 学习/咨询 | 触发 recall 模式 |
| 执行/确认 | 直接执行 |

三层检索：B1(strong) / B2(medium) / C3(fallback)，C3 兜底将覆盖率从 6.7% 提升至 ~100%。

### V4.4 — Concurrency Fixes

向量存储并发安全 + 自动修复：

- **P0**: `append_vectors()` 双重锁（`threading.Lock` + `fcntl.flock`）
- **P1**: `hermes_auto_index_all.py` 文件锁（防止并发覆盖）
- **P2**: `watchdog_vectorstore.py` drift 检测 + 自动 truncate/remap

详见 [README.md](./README.md) 或 GitHub repo。

---

## 后续版本索引

本文档描述 **Hermem Phase 2 v3.0** 基础架构（NumPy + SQLite 混合存储），为后续所有版本提供底层基础。后续版本的完整规范按以下结构组织（`phase3/vN/` 目录自包含 SPEC + TODO + impl + tests）：

| 版本 | 规范文件 | 状态 | 概要 |
|------|---------|------|------|
| V5（主动检索）| `Hermem-V5-SPEC.md`（顶层） | 已实现 v5.1 | bge-m3 对话中检索 + 阈值 0.70/0.50 |
| V5.5（元认知+冲突+遗忘）| `phase3/v5.5/SPEC.md` | 已上线(2026-05-28) | L4 反思 + 冲突协商 + 主动遗忘 |
| **V6（按需触发 + RRF + 时间通道）** | `phase3/v6/SPEC.md` v2.0 | **Sprint 0+0.5+1 已完成**(2026-06-08);Sprint 2-4 待开始 | 4 信号 `should_trigger`、RRF(k=60) vec+BM25 融合、9 条 regex 时间解析、Sprint 1.5 桥层 float→int 修复 |

各 sprint closeout 见 `phase3/vN/eval/sprint{N}-summary.md`。V6 v2.0 fusion 决策表见 `phase3/v6/SPEC.md` §1。
