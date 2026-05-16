# Hermem Phase 2 — 实施记录

**版本**: v2.0 (NumPy + SQLite 混合方案)
**日期**: 2026-05-01
**状态**: ✅ 已完成

---

## 已完成 Steps

### Step 1 — 方案评估 ✅
- 评估 SQLite vec0：macOS 3.53.0 无此模块
- 评估 NumPy：零外部依赖，0.8ms 搜索，400MB 上限安全
- 决策：采用 NumPy + SQLite FTS5 混合

### Step 2 — 核心数据模型 ✅
- `impl/database.py`：SQLite 表（chunks + fts5）、CRUD 操作
- `impl/embedding.py`：Ollama bge-m3 1024-dim + SHA256 pickle 缓存
- `impl/vectorstore.py`：NumPy `.npy` 文件、`shutil.copy2` 原子写入
- `impl/retrieval.py`：语义搜索 + FTS5 + RRF 融合（k=60，权重 0.65/0.35）

### Step 3 — CLI 工具 ✅
- `impl/commands.py`：init / search / health / stats / import
- 验证：`hermem health` → Ollama healthy, 5.9ms 延迟

### Step 4 — 历史数据迁移 ✅
- `impl/migrate.py`：Markdown parser + paragraph split + dry-run 模式
- 已迁移：`~/.hermes/memory/sessions/2026-04-28.md`（277行 → 4 chunks）

### Step 5 — 插件集成 ✅
- `plugins/memory/hermem/__init__.py`（13120 bytes）
- `plugins/memory/hermem/plugin.yaml`（134 bytes）
- 懒加载模式：`_ensure_impl_path()` 避免循环导入
- 5-parent 路径解析：`Path(__file__).resolve().parent * 5 + resolve()`

### Step 6 — 工具函数 ✅
- `HermemMemoryProvider.hermem_search`：混合/语义/关键词三种模式
- `HermemMemoryProvider.hermem_add`：写入 + 自动 embed
- `HermemMemoryProvider.hermem_forget`：语义匹配删除（SQLite 级）
- `HermemMemoryProvider.hermem_stats`：统计 + Ollama 健康状态

### Step 7 — 预取机制 ✅
- `prefetch(query)`：返回 `<hermem-context>` 块，top-3 召回
- **当前状态**：已实现但未激活自动预取（config `memory.provider: hermem`，无 auto-prefetch）
- 工具模式：Hermes 在需要时显式调用 `hermem_search`

### Step 8 — 端到端验证 ✅
- 5/5 语义召回测试全部通过
- 12 条记忆（含历史迁移 4 条）
- 13 个向量，1024 维，52KB

### Step 9 — 插件激活 ✅
- `memory.provider: hermem`（2026-05-01 激活）
- auto-prefetch：**未开启**（当前 12 条记忆量级下不必要）

---

## 已知限制

| 限制 | 说明 | 影响 |
|------|------|------|
| NumPy 孤儿向量 | `hermem_forget` 删除 SQLite 行，但 `.npy` 文件中向量仍是孤儿 | 磁盘浪费极小（<1KB/孤儿），可忽略 |
| 并发写冲突 | 多进程同时 `append_vectors` 可能覆盖 | 个人使用场景不会发生 |
| 数据规模上限 | ~400MB（100K 向量 @ 1024-dim F16） | 远超个人记忆需求 |

---

## 待建立的心智

- [ ] **召回质量评估**：12 条时噪声较多，待 50+ 条后重新评估 precision/recall
- [ ] **预取阈值**：何时开启 auto-prefetch（建议 50+ 条记忆）
- [ ] **概念标签体系**：SPEC.md v3.0 提到 concept_tags，当前未实现自动打标

---

## 归档文件

- `TODO.v1.minimax.md`：原 MiniMax API 方案（已废弃）
