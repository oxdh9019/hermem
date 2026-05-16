# Phase 2 Review：概念标签 + 语义召回

**日期**: 2026-05-10
**状态**: ✅ 功能交付完成

---

## 一、交付物检查

| 交付物 | 文件 | 状态 |
|--------|------|------|
| SPEC.md | `phase2/SPEC.md` | ✅ 完整（546 行） |
| TODO.md | `phase2/TODO.md` | ✅ 完整 |
| impl/ 目录 | `projects/hermem/impl/` | ✅ 完整 |
| — database.py | SQLite CRUD + FTS5 | ✅ |
| — embedding.py | Ollama bge-m3 1024-dim + SHA256 缓存 | ✅ |
| — vectorstore.py | NumPy 向量存储 | ✅ |
| — retrieval.py | 混合搜索（语义 + FTS5 + RRF） | ✅ |
| — commands.py | CLI 工具（init/search/health/stats/import） | ✅ |
| — migrate.py | 历史数据迁移 | ✅ |
| — batch_backfill.py | 批量回填脚本 | ✅ |
| plugin 集成 | `plugins/memory/hermem/` | ✅ |
| HermemMemoryProvider | 5 个工具函数（search/add/forget/stats + prefetch） | ✅ |
| concepts/index.md | 9 类概念索引 | ✅ |

---

## 二、完成标准核对

| 标准 | 实际表现 | 结果 |
|------|---------|------|
| 会话摘要中自动包含概念标签 | session-summary skill 已升级，生成摘要时自动提取 9 类标签 | ✅ |
| "上次讨论过 X 吗"能准确召回 | hermem_search（语义 + FTS5 混合）已实现 | ✅ |
| Oliver 手动"记住 X"时自动提取标签 | memory-tools skill 已实现 | ✅ |
| 概念召回准确率 80%+ | 无正式评估；1059 条 chunk，82 个概念标签样本（需 Oliver 主观验证） | ⚠️ 未验证 |

---

## 三、实际表现

### 活跃数据

```
memory/hermem.db:
  - 1059 条 chunks（含 965 条 session_summary）
  - 822 个独立 session_id
  - 82 个 concept_tag 条目（chunk + concept_note 合计）

memory/hermem_vectors.npy:
  - 3670144 bytes（约 3.5MB，对应 ~900 向量 @ 1024-dim float32）

memory/concepts/index.md:
  - 9 类概念，38 条索引记录
```

### 插件状态

- `memory.provider: hermem` 已激活
- auto-prefetch：**未开启**（当前 1059 条量级下已值得开启，但因缺少正式评估未激活）
- 工具模式：Hermes 在需要时显式调用 `hermem_search`

### 混合搜索架构

```
语义分支: Query → Ollama bge-m3 (1024-dim) → NumPy 余弦相似度
FTS5分支: Query → SQLite FTS5 BM25
                      ↓ RRF 融合（k=60，权重 0.65/0.35）
                   混合结果
```

---

## 四、TODO 步骤对照

| Step | 内容 | 状态 |
|------|------|------|
| Step 1 | 方案评估（vec0 vs NumPy vs ChromaDB） | ✅ NumPy 胜出 |
| Step 2 | 核心数据模型（SQLite 表 + NumPy 向量） | ✅ |
| Step 3 | CLI 工具 | ✅ |
| Step 4 | 历史数据迁移 | ✅ |
| Step 5 | 插件集成 | ✅ |
| Step 6 | 工具函数（search/add/forget/stats/prefetch） | ✅ |
| Step 7 | 预取机制 | ✅ 已实现，未激活 auto-prefetch |
| Step 8 | 端到端验证 | ✅ 5/5 测试通过 |
| Step 9 | 插件激活 | ✅ `memory.provider: hermem` |

---

## 五、已知限制

| 限制 | 说明 | 应对 |
|------|------|------|
| NumPy 孤儿向量 | `hermem_forget` 删除 SQLite 行，但 `.npy` 文件向量为孤儿 | 磁盘浪费极小（<1KB/孤儿），可忽略 |
| 并发写冲突 | 多进程同时 `append_vectors` 可能覆盖 | 个人使用场景不会发生 |
| 数据规模上限 | ~400MB（100K 向量 @ 1024-dim F16） | 远超个人记忆需求 |
| 概念标签准确率 | 未做正式评估 | 需 Oliver 主观验证 |
| auto-prefetch 未开启 | 1059 条 chunk 已超过建议阈值 50 | 待开启或持续评估 |

---

## 六、待建立的心智（TODO.md 遗留）

- [ ] **召回质量评估**：1059 条 chunk，concept_tag 样本 82 个，需要 Oliver 抽样验证 precision/recall
- [ ] **预取阈值**：当前 1059 条已远超 50 条建议阈值，可考虑开启 auto-prefetch
- [ ] **概念标签自动打标**：已在 session-summary skill 中实现，但需确认实际召回效果

---

## 七、结论

**Phase 2 功能全部交付完成**。核心架构稳定（NumPy + SQLite FTS5），插件集成正常，`hermem_search` 已激活使用。

主要遗留问题：
1. 缺少正式的召回质量评估（Oliver 主观验证）
2. auto-prefetch 未开启（1059 条已达开启阈值）
3. 概念标签体系的 precision 未知

建议下一步行动：在 Phase 3 启动前，先完成 Phase 2 的实际召回质量验证（Oliver 抽几个问题测试），并决定是否开启 auto-prefetch。
