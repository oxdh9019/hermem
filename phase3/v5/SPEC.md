# Hermem V5 规划：让历史记忆在对话中得到体现

**版本**: v1.1
**日期**: 2026-05-27
**状态**: 已实现 v5.1（2026-05-27 commit 5d2405b；阈值 2026-06-01 对齐到 0.70/0.50）
**依据**: Oliver 评审意见（V5 规划评估与建议，2026-05-27）

---

## V5 核心目标

**让历史记忆在对话中得到体现。**

当前 Hermem 的能力：
- session 启动时注入上下文预热 ✅
- 按需调用 hermem_search 召回 ✅
- 对话过程中**被动**——不主动介入 ❌

V5 要解决：Hermem 在对话过程中主动检索、自动判断、必要时注入，让历史经验不被遗漏。

---

## 架构现状与缺口分析

### 现有架构（V4.5）

```
用户消息 → Agent 处理（无 Hermem 介入）→ 响应
```

Hermem 只在 session warmup 时注入一次，对话过程中完全被动。

### V5 目标架构

```
用户消息
    ↓
Hermem 主动检索（embedding 匹配）
    ↓
分层阈值判断（≥0.70 直接注入，0.50-0.70 缓存记录）
    ↓
相关 chunk 追加到上下文
    ↓
Agent 处理（含历史上下文）→ 响应
```

### 当前缺口

| 缺口 | 说明 |
|------|------|
| 无对话中主动检索 | 每条消息后没有自动触发 Hermem 召回 |
| 无 embedding 匹配 | hermem_search 是文本匹配，不是向量相似度 |
| 无分层阈值注入 | 没有机制区分高置信/中置信/低置信 |
| 无防重复机制 | 同一 chunk 可能被反复注入 |

---

## 技术方案

### 方案选择：基于 bge 的向量检索 + 分层阈值触发注入

### 依赖已满足

| 组件 | 状态 | 说明 |
|------|------|------|
| bge  embedding 模型 | ✅ 已安装 | BAAI/bge-small-zh 或 bge-m3 |
| 向量检索（NumPy） | ✅ 已有 | Phase 2 的 vectorstore.py |
| 阈值判断 | ❌ 缺失 | 需要新增逻辑 |
| 对话中触发机制 | ❌ 缺失 | 需要 Hermes Agent 层改造 |
| 注入缓存 | ❌ 缺失 | 需要防止重复注入 |
| 向量索引优化 | ❌ 缺失 | 建议用 FAISS 或 sqlite-vss |

---

## 分层阈值策略

### 阈值设计

| 层级 | 相似度范围 | 动作 | 说明 |
|------|------------|------|------|
| 高置信 | ≥ 0.70 | 直接注入 | 高度相关，直接追加到上下文（bge-m3 实测分布调整，原 0.85 不可达） |
| 中置信 | 0.50 ≤ x < 0.70 | 缓存记录 | 暂不注入，记录到日志；后续消息相似度提升则注入 |
| 低置信 | < 0.50 | 忽略 | 不注入 |

### 配置化

```python
# config.py
ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.70       # 高置信注入阈值（bge-m3 实测分布调整）
ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.50      # 中置信阈值（原 0.65 偏高，截断边缘候选）
ACTIVE_RETRIEVAL_ENABLED = True              # 可开关
ACTIVE_RETRIEVAL_TOP_K = 3                   # 每次最多注入 3 条
ACTIVE_RETRIEVAL_FREQUENCY = 3               # 每 N 条消息触发一次（0=禁用）
```

---

## 注入格式设计

### 格式规范

```
[自动回忆 - 相似度 0.91]
以下是从历史记忆中检索到的相关内容（可能相关，仅供参考）：
- {chunk_content}

---

[自动回忆 - 相似度 0.87]
以下是从历史记忆中检索到的相关内容（可能相关）：
- {chunk_content_1}
- {chunk_content_2}
```

### 设计原则

- `[自动回忆]` 标签清晰表明来源是自动检索
- 相似度分数帮助 Agent 判断可信度（高分数可更重视）
- 添加"仅供参考"的提示，避免 Agent 过度依赖自动注入的内容
- 注入内容前后加换行，与主对话自然分隔
- 一次注入多条 chunk 时，汇总为一个块，避免打断对话流

---

## 向量检索接口设计

```python
def hermem_search_vector(
    query_embedding: np.ndarray,  # bge 生成的 1024 维向量
    top_k: int = 5,
    threshold: float = 0.50  # 中置信阈值，低于此直接跳过
) -> list[dict]:
    """
    向量检索，返回相似度 ≥ threshold 的 chunk，按相似度降序。
    返回: [{"chunk_id", "content", "session_id", "similarity", "chunk_type"}, ...]
    """
```

### 向量索引优化（Step 2 增强）

推荐使用 FAISS 或 sqlite-vss 扩展，避免全量 O(N) 计算。
- 当前 1613 个 chunk，全量计算 < 5ms，可先用 NumPy 实现
- 规模超过 10k 时考虑切换到 FAISS

---

## 触发逻辑设计

```
每条用户消息
    ↓
用 bge 生成 query_embedding（30-80ms CPU）
    ↓
hermem_search_vector(query_emb, top_k=3, threshold=0.50)
    ↓
遍历结果，按阈值分层：
    if similarity ≥ 0.70 and chunk_id not in injected:
        注入（加 [自动回忆] 前缀）
        记录 injected_chunk_ids
    elif 0.50 ≤ similarity < 0.70:
        记录到 medium_confidence_log（用于后续分析）
    ↓
Agent 继续处理
```

---

## 集成点设计

### 推荐集成位置：HermemMemoryProvider.sync_turn()

在现有的 correction detection 之后、retrieve() 之前，加入主动检索逻辑。
这样可以利用已有的会话上下文和消息缓冲，且不破坏原有流程。

```python
def sync_turn(self, user_message, assistant_response, turn_context):
    # 1. 主动检索相关记忆
    if self._should_auto_retrieve(user_message):
        related_chunks = self._auto_retrieve(user_message, top_k=3, threshold=0.50)
        for chunk in related_chunks:
            if chunk['similarity'] >= 0.70 and chunk['chunk_id'] not in self._injected_chunk_ids:
                self._inject_retrieved_chunks([chunk])
                self._injected_chunk_ids.add(chunk['chunk_id'])
            # else: 记录到 medium_confidence_log

    # 2. 原有的 correction detection 逻辑
    # 3. 原有的 retrieval 逻辑
    # ...
```

### 防重复注入机制

```python
# 会话级
_injected_chunk_ids: set = set()  # 同一会话中每个 chunk 只注入一次

# 时间级（可选，中置信缓存用）
_medium_confidence_log: list = []  # 中置信记录，用于后续分析
```

---

## 实施步骤

### Step 1：批量预计算现有 chunk 的 embedding（独立工作）

**目标**：为现有 1613 个 chunk 生成并存储 embedding。

**产出**：
- `hermem_embeddings.npy` 包含所有 chunk 的向量
- 更新 `chunks` 表，增加 `embedding_index` 字段
- 新增 chunk 时增量计算

**实现**：
```python
# scripts/batch_compute_embeddings.py
from sentence_transformers import SentenceTransformer
import numpy as np, sqlite3, json

model = SentenceTransformer('BAAI/bge-small-zh')  # 或 bge-m3

# 读取所有 chunk content
# 批量生成 embedding（batch_size=32）
# 追加写入 hermem_embeddings.npy
# 记录每个 chunk 对应的 embedding 索引
```

**验证**：
```bash
python3 scripts/batch_compute_embeddings.py
# 检查：hermem_embeddings.npy 形状为 (1613, 1024)
# 检查：数据库 chunks.embedding_index 已更新
```

**注意**：这是一次性工作，完成后可立即验证效果，不影响现有功能。

---

### Step 2：新增 hermem_search_vector 接口

**目标**：提供基于 embedding 相似度的检索能力，支持分层阈值。

**产出**：`impl/vector_search.py`

**实现**：
```python
def hermem_search_vector(query_emb: np.ndarray, top_k: int = 5, threshold: float = 0.50) -> list[dict]:
    # 1. 加载 hermem_embeddings.npy
    # 2. 计算余弦相似度（NumPy 向量化）
    # 3. 按相似度排序，返回 top_k
    # 4. 只返回 ≥ threshold 的结果
```

**验证**：
```python
# 测试
emb = model.encode("上次我们讨论的架构设计")
results = hermem_search_vector(emb, threshold=0.50)
assert len(results) <= 5
assert all(r['similarity'] >= 0.50 for r in results)
```

---

### Step 3：Hermes Agent 层集成

**目标**：在消息处理循环中加入主动检索逻辑。

**改造位置**：HermemMemoryProvider.sync_turn()（待 Oliver 确认文件路径）

**实现**：见上方"集成点设计"章节。

---

### Step 4：缓存与去重机制

**目标**：防止同一 chunk 在短时间窗口内重复注入。

**实现**：
- 会话级 `_injected_chunk_ids` set，同一 chunk 在同一会话中最多注入一次
- 中置信日志用于后续分析（可选）

---

## 性能与成本评估

### 每次检索开销（基于 bge-small-zh）

| 操作 | 耗时 | 说明 |
|------|------|------|
| bge embedding | 30-80ms（CPU） | 远低于 qwen 推理 |
| 向量相似度计算（1613 vectors） | < 5ms | NumPy 向量化 |
| **总计** | **< 100ms** | 用户无感知 |

### Token 成本

- 仅在相似度 ≥ 0.70 时注入，每次注入约 100-300 tokens
- 受触发频率控制，不是每条消息都注入
- 实际对话中，高相似度触发属于低频事件

---

## 验收标准

1. **功能验证**：用户说一个话题，Hermem 能找到相关的历史讨论并注入上下文
2. **阈值有效性**：相似度 < 0.50 的 chunk 不注入，0.50-0.70 进入日志，≥0.70 直接注入
3. **防重复**：同一 chunk 在同一会话中不重复注入
4. **性能验证**：每次检索 < 100ms，不影响响应时间
5. **无破坏性**：V5 完成后，现有 session warmup 和 hermem_search 功能不受影响
6. **配置化**：阈值可通过 config.py 调整，无需修改代码

---

## 与 V4.5 的关系

V5 不改变 V4.5 的核心架构（disposition、error-activated retrieval、intent classification），而是在其基础上增加"对话中主动检索"的能力。

V5 可以理解为 Hermem 的**前台能力增强**——让已有的记忆资产在对话中得到主动调用，而不是被动等待用户查询。

---

*v1.1 已实施 (impl repo commit 5d2405b)；2026-06-01 阈值统一为 0.70/0.50 (P1-6)。*
