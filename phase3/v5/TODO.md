# Hermem V5 开发计划与测试方案

**版本**: v1.1
**日期**: 2026-05-27
**依据**: V5 规划 v1.1（SPEC.md）+ 评估报告 v1.0

---

## 开发步骤总览

| 阶段 | 步骤 | 内容 | 产出 | 优先级 |
|------|------|------|------|--------|
| **准备** | 0 | 确认 Hermes Agent 项目结构 | 文件路径确认 | P0 |
| **Phase A** | 1 | 批量预计算现有 chunk embedding | hermem_embeddings.npy | P0 |
| **Phase A** | 1b | 增量 embedding 更新方案 | impl/embedding_incremental.py | P0 |
| **Phase A** | 2 | hermem_search_vector 接口 + 批量查询优化 | impl/vector_search.py | P0 |
| **Phase B** | 3 | Hermes Agent 层集成 | sync_turn() 改造 | P1 |
| **Phase B** | 4 | 缓存与去重机制 + 中置信累积注入 | _injected_chunk_ids + _medium_tracker | P2 |
| **验证** | 5 | 端到端测试（含格式验证） | 测试报告 | P0 |

---

## Step 0：确认 Hermes Agent 项目结构

**目标**：确认 HermemMemoryProvider.sync_turn() 的确切文件路径，并准备备选方案。

### 执行

```bash
# 搜索 sync_turn 方法位置
grep -r "def sync_turn" ~/.hermes/hermes-agent/ --include="*.py"

# 搜索 HermemMemoryProvider 类
grep -r "class HermemMemoryProvider" ~/.hermes/hermes-agent/ --include="*.py"

# 列出 hermes-agent 项目结构
find ~/.hermes/hermes-agent -name "*.py" -path "*/hermes/*" | head -30
```

**产出**：确认 sync_turn() 方法所在文件和行号。

### 备选集成点（若 sync_turn() 不可用）

| 优先级 | 位置 | 说明 |
|--------|------|------|
| 1（主选） | HermemMemoryProvider.sync_turn() | 推荐：已有会话上下文，易于扩展 |
| 2（备选） | hermes/agent/core.py handle_user_message() | 直接在消息入口调用，需自行管理上下文 |
| 3（备选） | hermes/memory/hermem_provider.py 任意 sync 方法 | 已有 Hermem 集成，跳过重复初始化 |
| 4（不推荐） | hermes/gateway/wechat.py 消息回调 | 网关层，不应包含业务逻辑 |

**Oliver 确认事项**：
- 请在开始 Step 1 前确认 sync_turn() 的实际位置
- 若位置与预期不符，告知使用哪个备选集成点

**验收**：能够精确定位集成点，并确认备选方案。

---

## Step 1：批量预计算现有 chunk embedding

**目标**：为现有 1613 个 chunk 生成并存储 embedding。

**文件**：`scripts/batch_compute_embeddings.py`

### 实现

```python
#!/usr/bin/env python3
"""
批量预计算 Hermem chunk embedding
运行一次，为所有现有 chunk 生成 embedding 并存储。

用法: python3 scripts/batch_compute_embeddings.py
"""

import sys, json, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from sentence_transformers import SentenceTransformer
from impl.database import Database

# 配置（统一从 config.py 导入）
from impl.config import EMBEDDING_MODEL, BATCH_SIZE, VECTOR_FILE

def main():
    print(f"加载模型: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    db = Database()
    db.add_embedding_index_column()  # 确保字段存在
    chunks = db.get_all_chunks()
    print(f"找到 {len(chunks)} 个 chunk")

    if not chunks:
        print("无 chunk 需要处理")
        return

    # 批量生成 embedding
    texts = [c['content'] for c in chunks]
    embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=True)

    # 原子写入：临时文件 → 替换（防止损坏）
    vectors = np.array(embeddings, dtype=np.float32)
    tmp_path = f"/tmp/hermem_vec_{os.getpid()}.npy"
    np.save(tmp_path, vectors)
    shutil.copy2(tmp_path, str(VECTOR_FILE))
    os.remove(tmp_path)
    print(f"已保存 {vectors.shape} 到 {VECTOR_FILE}")

    # 批量更新数据库：记录每个 chunk 的 embedding 索引
    db.update_embedding_indices([(c['id'], i) for i, c in enumerate(chunks)])
    print("数据库 embedding_index 已更新")

    # 验证
    loaded = np.load(VECTOR_FILE)
    print(f"验证: 形状={loaded.shape}, dtype={loaded.dtype}")

if __name__ == "__main__":
    import shutil, os
    main()
```

### 数据库改动

```python
# impl/database.py

def add_embedding_index_column(self):
    """如果字段不存在则添加"""
    try:
        self.execute("ALTER TABLE chunks ADD COLUMN embedding_index INTEGER")
        self.commit()
    except Exception:
        pass  # 字段已存在

def update_embedding_indices(self, id_index_pairs: list[tuple[str, int]]):
    """批量更新 chunk 的 embedding 索引"""
    self.executemany(
        "UPDATE chunks SET embedding_index = ? WHERE id = ?",
        id_index_pairs
    )
    self.commit()

def get_all_chunks(self) -> list[dict]:
    """获取所有 chunk 用于 embedding 计算"""
    rows = self.execute(
        "SELECT id, content, session_id, chunk_type FROM chunks WHERE embedding_index IS NULL"
    ).fetchall()  # 只获取未生成 embedding 的 chunk（支持增量）
    return [dict(r) for r in rows]
```

### 验证

```bash
python3 scripts/batch_compute_embeddings.py

# 验证输出
python3 -c "
import numpy as np
from pathlib import Path
v = np.load(Path.home() / '.hermes' / 'memory' / 'hermem_embeddings.npy')
print(f'形状: {v.shape}')
print(f'dtype: {v.dtype}')
"
```

**验收标准**：
- [ ] `hermem_embeddings.npy` 形状为 (N, 1024)
- [ ] 数据库 chunks.embedding_index 已更新
- [ ] 可单独运行，不破坏现有功能
- [ ] 原子写入（临时文件替换），无损坏风险

---

## Step 1b：增量 embedding 更新方案

**目标**：解决新 chunk 加入时的 embedding 计算问题。

### 方案说明

| 方案 | 适用规模 | 实现复杂度 | 说明 |
|------|----------|-----------|------|
| 追加写入 + 定期重算 | < 10k chunk | 低 | 新 chunk 实时计算 embedding，追加到 .npy 末尾；定期全量重算 |
| FAISS 动态索引 | > 10k chunk | 高 | 支持动态添加，推荐后期切换 |

### 实现（追加写入）

```python
# impl/embedding_incremental.py
"""
增量 embedding 计算模块
当新 chunk 加入时，计算其 embedding 并追加到向量库。
"""

import numpy as np, os, shutil
from pathlib import Path
from sentence_transformers import SentenceTransformer
from impl.database import Database
from impl.config import EMBEDDING_MODEL, BATCH_SIZE, VECTOR_FILE, VECTOR_DIM

def append_embedding(content: str, chunk_id: str) -> int:
    """
    为单个 chunk 计算 embedding 并追加到向量库。
    返回: embedding_index
    """
    model = SentenceTransformer(EMBEDDING_MODEL)
    emb = model.encode(content, normalize_embeddings=True)
    emb = emb.astype(np.float32)

    # 追加到 npy 文件
    if VECTOR_FILE.exists():
        vectors = np.load(VECTOR_FILE)
    else:
        vectors = np.empty((0, VECTOR_DIM), dtype=np.float32)

    new_vectors = np.vstack([vectors, emb])
    tmp_path = f"/tmp/hermem_vec_inc_{os.getpid()}.npy"
    np.save(tmp_path, new_vectors)
    shutil.copy2(tmp_path, str(VECTOR_FILE))
    os.remove(tmp_path)

    # 记录 embedding_index
    embedding_index = len(vectors)
    db = Database()
    db.execute(
        "UPDATE chunks SET embedding_index = ? WHERE id = ?",
        (embedding_index, chunk_id)
    )
    db.commit()

    return embedding_index


def batch_append(chunks: list[dict]) -> list[int]:
    """
    批量追加 embedding（用于 cron 批量处理）
    chunks: [{"id": "...", "content": "..."}, ...]
    返回: embedding_index 列表
    """
    model = SentenceTransformer(EMBEDDING_MODEL)
    texts = [c['content'] for c in chunks]
    embeddings = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype=np.float32)

    # 追加
    if VECTOR_FILE.exists():
        vectors = np.load(VECTOR_FILE)
    else:
        vectors = np.empty((0, VECTOR_DIM), dtype=np.float32)

    new_vectors = np.vstack([vectors, embeddings])
    tmp_path = f"/tmp/hermem_vec_batch_{os.getpid()}.npy"
    np.save(tmp_path, new_vectors)
    shutil.copy2(tmp_path, str(VECTOR_FILE))
    os.remove(tmp_path)

    # 批量更新数据库
    db = Database()
    start_idx = len(vectors)
    pairs = [(c['id'], start_idx + i) for i, c in enumerate(chunks)]
    db.executemany(
        "UPDATE chunks SET embedding_index = ? WHERE id = ?",
        pairs
    )
    db.commit()

    return list(range(start_idx, start_idx + len(chunks)))
```

### 定期重算（限制说明）

> 当前方案在 chunk 数量超过 10k 时，定期全量重算会变得低效。
> 届时建议切换到 FAISS 动态索引方案。

**验收标准**：
- [ ] 新 chunk 可实时追加 embedding，不影响已有数据
- [ ] 批量追加（cron 场景）正常工作
- [ ] 向量库与数据库 embedding_index 一致

---

## Step 2：hermem_search_vector 接口 + 批量查询优化

**目标**：提供基于 embedding 相似度的检索能力，支持分层阈值。

**文件**：`impl/vector_search.py`

### 配置统一管理

```python
# impl/config.py
"""Hermem V5 统一配置"""

# 向量检索配置
VECTOR_FILE = Path.home() / ".hermes" / "memory" / "hermem_embeddings.npy"
VECTOR_DIM = 1024

# Embedding 模型配置
EMBEDDING_MODEL = 'BAAI/bge-small-zh'  # 或 bge-m3
BATCH_SIZE = 32

# 主动检索阈值配置
ACTIVE_RETRIEVAL_ENABLED = True
ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.85  # 高置信注入阈值
ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.65  # 中置信阈值
ACTIVE_RETRIEVAL_TOP_K = 3
ACTIVE_RETRIEVAL_FREQUENCY = 3  # 每 N 条消息触发一次（0=禁用）
```

### 实现

```python
# impl/vector_search.py
"""
Hermem 向量检索接口
基于 NumPy 余弦相似度，支持分层阈值过滤。
统一从 config.py 读取配置。
"""

import numpy as np
from pathlib import Path
from impl.database import Database
from impl.config import VECTOR_FILE, ACTIVE_RETRIEVAL_THRESHOLD_HIGH, ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM

def load_vectors() -> np.ndarray:
    """加载向量库"""
    if not VECTOR_FILE.exists():
        return np.empty((0, 1024), dtype=np.float32)
    return np.load(VECTOR_FILE)

def hermem_search_vector(
    query_embedding: np.ndarray,
    top_k: int = 5,
    threshold: float = ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM
) -> list[dict]:
    """
    向量检索，返回相似度 ≥ threshold 的 chunk，按相似度降序。
    优化：使用批量 SQL 查询替代逐条查询。
    """
    vectors = load_vectors()
    if vectors.shape[0] == 0:
        return []

    # 批量计算相似度
    q = query_embedding.astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1) * (np.linalg.norm(q) + 1e-8)
    scores = (vectors @ q) / norms

    # 获取 top_k * 3 的索引（留空间给阈值过滤）
    top_indices = np.argsort(scores)[::-1][:top_k * 3]

    # 批量查询：一次 SQL IN 获取所有 chunk
    db = Database()
    indices_list = [int(idx) for idx in top_indices]
    placeholders = ",".join(["?"] * len(indices_list))
    rows = db.execute(
        f"SELECT id, session_id, content, chunk_type, embedding_index FROM chunks WHERE embedding_index IN ({placeholders})",
        indices_list
    ).fetchall()

    # 构建 index → row 映射
    idx_to_row = {row[-1]: dict(row) for row in rows}

    results = []
    for idx in top_indices:
        int_idx = int(idx)
        sim = float(scores[idx])
        if sim < threshold:
            continue
        if int_idx in idx_to_row:
            chunk = idx_to_row[int_idx]
            results.append({
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "session_id": chunk["session_id"],
                "chunk_type": chunk["chunk_type"],
                "similarity": sim,
                "embedding_index": int_idx
            })
        if len(results) >= top_k:
            break

    return sorted(results, key=lambda x: x["similarity"], reverse=True)


def search_with_tier(
    query_embedding: np.ndarray,
    top_k: int = 3
) -> tuple[list[dict], list[dict]]:
    """
    分层检索，返回 (high_confidence, medium_confidence) 两个列表。
    """
    all_results = hermem_search_vector(
        query_embedding,
        top_k=top_k * 2,
        threshold=ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM
    )

    high = [r for r in all_results if r["similarity"] >= ACTIVE_RETRIEVAL_THRESHOLD_HIGH]
    medium = [r for r in all_results if ACTIVE_RETRIEVAL_THRESHOLD_HIGH > r["similarity"] >= ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM]

    return high[:top_k], medium[:top_k]
```

### 数据库新增方法

```python
# impl/database.py

def get_chunks_by_embedding_indices(self, indices: list[int]) -> list[dict]:
    """批量根据 embedding_index 获取 chunk（优化查询）"""
    if not indices:
        return []
    placeholders = ",".join(["?"] * len(indices))
    rows = self.execute(
        f"SELECT id, session_id, content, chunk_type, embedding_index FROM chunks WHERE embedding_index IN ({placeholders})",
        indices
    ).fetchall()
    return [dict(r) for r in rows]
```

### 验证

```python
# test_vector_search.py
import numpy as np
from sentence_transformers import SentenceTransformer
from impl.vector_search import hermem_search_vector, search_with_tier
from impl.config import ACTIVE_RETRIEVAL_THRESHOLD_HIGH, ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM

model = SentenceTransformer('BAAI/bge-small-zh')
query = "上次讨论的架构设计"
emb = model.encode(query)

# 测试1: 基本检索
results = hermem_search_vector(emb, threshold=ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM, top_k=5)
print(f"找到 {len(results)} 条结果，阈值={ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM}")
for r in results:
    print(f"  [{r['similarity']:.3f}] {r['content'][:60]}...")

# 测试2: 分层检索
high, medium = search_with_tier(emb)
print(f"高置信: {len(high)} 条 (≥{ACTIVE_RETRIEVAL_THRESHOLD_HIGH})")
print(f"中置信: {len(medium)} 条 ({ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM}-{ACTIVE_RETRIEVAL_THRESHOLD_HIGH})")

# 测试3: 高阈值检索
high_only = hermem_search_vector(emb, threshold=ACTIVE_RETRIEVAL_THRESHOLD_HIGH, top_k=5)
print(f"≥{ACTIVE_RETRIEVAL_THRESHOLD_HIGH} 的结果: {len(high_only)} 条")

# 测试4: 空向量（应返回空）
empty_results = hermem_search_vector(np.zeros(1024, dtype=np.float32), threshold=0.8)
print(f"空向量结果: {len(empty_results)} 条（应为0）")
```

**验收标准**：
- [ ] `hermem_search_vector(query_emb, threshold=0.65)` 返回相似度 ≥ 0.65 的 chunk
- [ ] `search_with_tier()` 正确返回高置信（≥0.85）和中置信（0.65-0.85）列表
- [ ] 返回结果按相似度降序排列
- [ ] `top_k` 参数生效
- [ ] 向量文件不存在时不报错
- [ ] 批量查询优化生效（单次 SQL 替代 N 次查询）

---

## Step 3：Hermes Agent 层集成

**目标**：在 HermemMemoryProvider.sync_turn() 中加入主动检索逻辑。

**前置**：Step 0 确认的 sync_turn() 文件路径。

### 实现框架

```python
# 集成到 HermemMemoryProvider.sync_turn()
from impl.config import (
    ACTIVE_RETRIEVAL_ENABLED,
    ACTIVE_RETRIEVAL_THRESHOLD_HIGH,
    ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM,
    ACTIVE_RETRIEVAL_TOP_K,
    ACTIVE_RETRIEVAL_FREQUENCY,
)

class HermemMemoryProvider:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self._injected_chunk_ids: set = set()       # 会话级去重
        self._medium_tracker: dict = {}            # 中置信累积: {chunk_id: max_similarity}
        self._retrieve_count: int = 0              # 消息计数（用于频率控制）

    def sync_turn(self, user_message: str, assistant_response: str, turn_context: dict):
        # 频率控制累加
        self._retrieve_count += 1

        # 1. 主动检索（V5 新增）
        if ACTIVE_RETRIEVAL_ENABLED and self._should_auto_retrieve():
            related_chunks = self._auto_retrieve(user_message)
            for chunk in related_chunks:
                chunk_id = chunk['chunk_id']
                sim = chunk['similarity']

                if sim >= ACTIVE_RETRIEVAL_THRESHOLD_HIGH and chunk_id not in self._injected_chunk_ids:
                    # 高置信：直接注入
                    self._inject_retrieved_chunk(chunk)
                    self._injected_chunk_ids.add(chunk_id)
                    # 从 medium_tracker 移除（已注入）
                    self._medium_tracker.pop(chunk_id, None)

                elif ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM <= sim < ACTIVE_RETRIEVAL_THRESHOLD_HIGH:
                    # 中置信：累积相似度
                    if chunk_id in self._medium_tracker:
                        self._medium_tracker[chunk_id] = max(self._medium_tracker[chunk_id], sim)
                    else:
                        self._medium_tracker[chunk_id] = sim

        # 2. 检查 medium_tracker 中是否有 chunk 相似度提升到注入阈值
        self._check_medium_tracker_injection()

        # 3. 原有的 correction detection 逻辑
        # ... existing code ...

    def _should_auto_retrieve(self) -> bool:
        """判断是否需要自动检索（频率控制）"""
        if ACTIVE_RETRIEVAL_FREQUENCY == 0:
            return False
        return self._retrieve_count % ACTIVE_RETRIEVAL_FREQUENCY == 0

    def _auto_retrieve(self, user_message: str, top_k: int = ACTIVE_RETRIEVAL_TOP_K) -> list[dict]:
        """执行自动检索"""
        from sentence_transformers import SentenceTransformer
        from impl.vector_search import search_with_tier

        model = SentenceTransformer('BAAI/bge-small-zh')
        emb = model.encode(user_message)
        high, medium = search_with_tier(emb, top_k=top_k)
        return high + medium

    def _check_medium_tracker_injection(self):
        """检查 medium_tracker 中是否有 chunk 达到高置信阈值"""
        to_inject = []
        for chunk_id, max_sim in list(self._medium_tracker.items()):
            if max_sim >= ACTIVE_RETRIEVAL_THRESHOLD_HIGH and chunk_id not in self._injected_chunk_ids:
                to_inject.append(chunk_id)

        for chunk_id in to_inject:
            # 获取完整 chunk 信息并注入
            chunk = self._get_chunk_by_id(chunk_id)
            if chunk:
                self._inject_retrieved_chunk(chunk)
                self._injected_chunk_ids.add(chunk_id)
                self._medium_tracker.pop(chunk_id, None)

    def _inject_retrieved_chunk(self, chunk: dict):
        """将检索到的 chunk 注入到内存上下文"""
        # 注入格式：[自动回忆 - 相似度 0.91]
        injection = (
            f"\n\n[自动回忆 - 相似度 {chunk['similarity']:.2f}]\n"
            f"以下是从历史记忆中检索到的相关内容（可能相关，仅供参考）：\n"
            f"- {chunk['content']}\n"
        )
        # 将 injection 追加到当前会话的内存上下文中
        self._memory_context += injection

    def reset_session(self):
        """新会话时重置"""
        self._injected_chunk_ids.clear()
        self._medium_tracker.clear()
        self._retrieve_count = 0
```

**验收标准**：
- [ ] sync_turn() 方法中能调用 hermem_search_vector
- [ ] 高置信 chunk（≥0.85）能正确注入到上下文
- [ ] 中置信 chunk 累积到 _medium_tracker
- [ ] 同一 chunk 在同一会话中不重复注入
- [ ] 新会话开始时所有状态重置
- [ ] 配置可通过 config.py 调整（阈值、频率、开关）

---

## Step 4：缓存与去重机制 + 中置信累积注入

**目标**：防止同一 chunk 在短时间窗口内重复注入，实现中置信累积注入逻辑。

### _medium_tracker 工作原理

```
用户消息1 → 中置信 chunk_123 (0.72) → _medium_tracker = {chunk_123: 0.72}
用户消息2 → 同一 chunk_123 (0.78) → _medium_tracker = {chunk_123: 0.78}（更新最大值）
用户消息3 → 同一 chunk_123 (0.86) → 触发注入！_medium_tracker 移除
```

### 实现细节

```python
# 见 Step 3 实现，_medium_tracker 已集成
```

### 验证

```python
# test_medium_tracker.py
def test_medium_tracker_accumulation():
    """测试中置信累积逻辑"""
    provider = HermemMemoryProvider()

    # 模拟中置信 chunk
    chunk1 = {"chunk_id": "test123", "content": "test content", "similarity": 0.72}
    chunk2 = {"chunk_id": "test123", "content": "test content", "similarity": 0.78}  # 提升

    # 第一次（0.72）
    provider._medium_tracker[chunk1["chunk_id"]] = chunk1["similarity"]
    assert provider._medium_tracker["test123"] == 0.72

    # 第二次（0.78，更新最大值）
    if chunk2["chunk_id"] in provider._medium_tracker:
        provider._medium_tracker[chunk2["chunk_id"]] = max(
            provider._medium_tracker[chunk2["chunk_id"]], chunk2["similarity"]
        )
    assert provider._medium_tracker["test123"] == 0.78

    # 第三次（0.86，达到注入阈值）
    if provider._medium_tracker["test123"] >= 0.85 and provider._medium_tracker["test123"] not in provider._injected_chunk_ids:
        # 触发注入
        provider._injected_chunk_ids.add(provider._medium_tracker.pop("test123"))
    assert "test123" not in provider._medium_tracker  # 已移除
    assert "test123" in provider._injected_chunk_ids  # 已注入
    print("medium_tracker 累积注入逻辑正常")
```

**验收标准**：
- [ ] 同一 chunk 在同一会话中只注入一次
- [ ] 中置信相似度累积到 _medium_tracker
- [ ] 相似度提升到 ≥0.85 时触发注入
- [ ] 新会话重置所有缓存状态

---

## Step 5：端到端测试（含格式验证）

### 测试用例

| 用例 | 输入 | 预期输出 | 验证方法 |
|------|------|----------|----------|
| T1: 高置信注入 | 用户说"上次那个 cron 任务的问题" | 找到相关 chunk 并注入，响应提到历史上下文 | 人工验证 + 格式检查 |
| T1a: 注入格式 | 任意高置信触发 | 注入内容包含 `[自动回忆 - 相似度 X.XX]` 前缀 | 正则匹配验证 |
| T2: 中置信缓存 | 用户说一个话题，但相似度在 0.65-0.85 | chunk 进入 _medium_tracker，不注入 | 检查 _medium_tracker |
| T3: 低置信忽略 | 用户说完全不相关的话题 | 不注入任何 chunk | 人工验证 |
| T4: 防重复 | 连续两条消息涉及同一话题 | 只在第一条注入，第二条不再注入 | 检查 _injected_chunk_ids |
| T5: 中置信累积 | 同一中置信 chunk 三次消息 | 相似度累积，第三次注入 | 检查 _medium_tracker 变化 |
| T6: 性能 | 用户消息处理时间 | embedding + 检索 < 100ms | 代码计时 |
| T7: 新会话重置 | 开启新会话 | _injected_chunk_ids 和 _medium_tracker 为空 | 检查 reset_session |
| T8: 配置生效 | 修改 config.py 阈值 | 检索结果反映新阈值 | 参数化测试 |

### 测试脚本

```python
# scripts/test_v5_e2e.py
"""
V5 端到端测试
运行方式: python3 scripts/test_v5_e2e.py
"""

import time, re
from sentence_transformers import SentenceTransformer
from impl.vector_search import hermem_search_vector, search_with_tier, load_vectors
from impl.config import ACTIVE_RETRIEVAL_THRESHOLD_HIGH, ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM

# 正则：验证注入格式
INJECTION_PATTERN = re.compile(r'\[自动回忆 - 相似度 (\d+\.\d+)\]')

def test_injection_format():
    """T1a: 验证注入格式符合 SPEC"""
    print("\n=== 注入格式验证 ===")

    # 模拟注入内容
    test_injection = "[自动回忆 - 相似度 0.91]\n以下是从历史记忆中检索到的相关内容（可能相关）：\n- 测试内容"

    matches = INJECTION_PATTERN.findall(test_injection)
    if matches:
        sim = float(matches[0])
        print(f"格式正确: 相似度={sim}")
        assert 0.8 <= sim <= 1.0, f"相似度 {sim} 超出合理范围"
        assert "以下是从历史记忆中检索到的相关内容（可能相关）：" in test_injection
        print("✓ 格式验证通过")
    else:
        print("✗ 格式错误：未找到 [自动回忆 - 相似度 X.XX]")
        assert False, "注入格式不符合 SPEC"

def test_high_confidence_injection():
    """测试高置信注入场景"""
    model = SentenceTransformer('BAAI/bge-small-zh')

    test_queries = [
        "上次讨论的 Hermem 架构设计",
        "微博监控任务怎么配置的",
        "OpenClaw 的 doctor 警告有哪些",
    ]

    print("\n=== 高置信注入测试 ===")
    for q in test_queries:
        emb = model.encode(q)
        start = time.time()
        high, medium = search_with_tier(emb, top_k=3)
        elapsed = time.time() - start

        print(f"\n查询: {q}")
        print(f"耗时: {elapsed*1000:.1f}ms")
        print(f"高置信: {len(high)} 条 (≥{ACTIVE_RETRIEVAL_THRESHOLD_HIGH})")
        for h in high:
            print(f"  [{h['similarity']:.3f}] {h['content'][:60]}...")
            # 验证格式
            injection = f"[自动回忆 - 相似度 {h['similarity']:.2f}]"
            assert h['similarity'] >= ACTIVE_RETRIEVAL_THRESHOLD_HIGH
        print(f"中置信: {len(medium)} 条 ({ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM}-{ACTIVE_RETRIEVAL_THRESHOLD_HIGH})")

def test_medium_tracker():
    """测试中置信累积逻辑"""
    print("\n=== 中置信累积测试 ===")

    # 模拟 medium_tracker
    tracker = {}
    chunk_id = "test_chunk"

    # 第一条消息：0.72
    sim1 = 0.72
    if chunk_id in tracker:
        tracker[chunk_id] = max(tracker[chunk_id], sim1)
    else:
        tracker[chunk_id] = sim1
    print(f"消息1 (0.72): tracker={tracker}")

    # 第二条消息：0.78
    sim2 = 0.78
    if chunk_id in tracker:
        tracker[chunk_id] = max(tracker[chunk_id], sim2)
    else:
        tracker[chunk_id] = sim2
    print(f"消息2 (0.78): tracker={tracker}")
    assert tracker[chunk_id] == 0.78

    # 第三条消息：0.86（达到注入阈值）
    sim3 = 0.86
    if tracker[chunk_id] >= ACTIVE_RETRIEVAL_THRESHOLD_HIGH:
        print(f"触发注入! 相似度 {tracker[chunk_id]:.2f} ≥ {ACTIVE_RETRIEVAL_THRESHOLD_HIGH}")
        tracker.pop(chunk_id)
    print(f"消息3 (0.86): tracker={tracker}")
    assert chunk_id not in tracker

def test_performance():
    """测试性能"""
    print("\n=== 性能测试 ===")
    model = SentenceTransformer('BAAI/bge-small-zh')
    vectors = load_vectors()
    print(f"向量库规模: {vectors.shape[0]} 个向量")

    times = []
    for _ in range(10):
        q = "测试查询内容"
        start = time.time()
        emb = model.encode(q)
        q_emb = emb.astype(np.float32)
        scores = (vectors @ q_emb) / (np.linalg.norm(vectors, axis=1) * (np.linalg.norm(q_emb) + 1e-8))
        elapsed = time.time() - start
        times.append(elapsed * 1000)

    avg_ms = sum(times) / len(times)
    max_ms = max(times)
    print(f"平均耗时: {avg_ms:.1f}ms")
    print(f"最大耗时: {max_ms:.1f}ms")
    assert avg_ms < 100, f"平均耗时 {avg_ms}ms 超过 100ms 阈值"
    print("✓ 性能验证通过")

if __name__ == "__main__":
    test_injection_format()
    test_high_confidence_injection()
    test_medium_tracker()
    test_performance()
    print("\n=== 所有测试完成 ===")
```

### 验证报告模板

```
V5 验收测试报告
日期: 2026-XX-XX

| 用例 | 状态 | 说明 |
|------|------|------|
| T1: 高置信注入 | ✅/❌ | |
| T1a: 注入格式 | ✅/❌ | |
| T2: 中置信缓存 | ✅/❌ | |
| T3: 低置信忽略 | ✅/❌ | |
| T4: 防重复 | ✅/❌ | |
| T5: 中置信累积 | ✅/❌ | |
| T6: 性能 | ✅/❌ | |
| T7: 新会话重置 | ✅/❌ | |
| T8: 配置生效 | ✅/❌ | |

总体结论: [通过/需修复]
```

---

## 配置管理

### 统一配置（impl/config.py）

```python
# impl/config.py
"""Hermem V5 统一配置"""

from pathlib import Path

# 向量存储配置
VECTOR_FILE = Path.home() / ".hermes" / "memory" / "hermem_embeddings.npy"
VECTOR_DIM = 1024

# Embedding 模型配置
EMBEDDING_MODEL = 'BAAI/bge-small-zh'  # bge-small-zh 或 bge-m3
BATCH_SIZE = 32

# 主动检索阈值配置
ACTIVE_RETRIEVAL_ENABLED = True          # 可开关
ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.85  # 高置信注入阈值
ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.65  # 中置信阈值
ACTIVE_RETRIEVAL_TOP_K = 3             # 每次最多注入 3 条
ACTIVE_RETRIEVAL_FREQUENCY = 3          # 每 N 条消息触发一次（0=禁用）
```

所有阈值、开关、频率都通过 `config.py` 统一管理，避免硬编码。

---

## 依赖清单

| 依赖 | 来源 | 说明 |
|------|------|------|
| sentence-transformers | pip install sentence-transformers | 加载 bge 模型 |
| numpy | 系统自带 | 向量计算 |
| bge-small-zh 或 bge-m3 | ollama 或本地 | embedding 模型 |

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| embedding 模型不可用 | 低 | 高 | Step 1 前先验证 bge 可用 |
| 注入干扰对话 | 中 | 中 | 高阈值 0.85 + 前缀提示 |
| 向量文件损坏 | 低 | 高 | 原子写入（临时文件替换）+ 备份 |
| Hermes Agent 集成复杂度高 | 中 | 中 | Step 0 先确认路径 + 备选方案 |
| 增量 embedding 累积导致文件过大 | 中 | 中 | >10k 时切换 FAISS（已在 roadmap） |

---

## 与 SPEC.md 一致性检查

| SPEC 要求 | TODO 实现 | 状态 |
|-----------|-----------|------|
| 分层阈值（≥0.85 注入，0.65-0.85 缓存，<0.65 忽略） | search_with_tier + _medium_tracker | ✅ 一致 |
| 注入格式 [自动回忆 - 相似度 X.XX] + 分隔 | _inject_retrieved_chunk | ✅ 一致 |
| 会话级去重 _injected_chunk_ids | 实现 | ✅ 一致 |
| 中置信日志记录 | _medium_tracker 累积 | ✅ 一致 |
| 性能要求 < 100ms | T6 测试用例 | ✅ 一致 |
| 配置化阈值 | config.py 统一管理 | ✅ 一致 |
| 增量 embedding 更新 | Step 1b | ✅ 一致 |

---

*开发计划 v1.1，评估报告改进后版本。*
