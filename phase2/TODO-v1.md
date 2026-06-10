# Hermem Phase 2 实施清单

**状态**: 待实施
**最后更新**: 2026-05-01

---

## 实施清单

### 前期准备

- [ ] Step 1: 确认 MiniMax Embedding API 可用（`MINIMAX_API_KEY`）
- [ ] Step 2: 确认 Ollama 可用（fallback 方案，`nomic-embed-text` 模型）
- [ ] Step 3: 确认 Hermem Phase 1 已完成或可独立实施

### 代码实现

- [ ] Step 4: 创建 `~/.hermes/memory/` 目录
- [ ] Step 5: 创建 `embeddings.db` SQLite 向量索引数据库
- [ ] Step 6: 实现 `embedding_client.py`（MiniMax API 客户端）
- [ ] Step 7: 实现 `embedding_store.py`（向量存储与检索）
- [ ] Step 8: 实现 `hybrid_search.py`（混合搜索 + RRF 融合）
- [ ] Step 9: 实现 `concept_tagger.py`（规则 + LLM 标签提取）
- [ ] Step 10: 实现 `chunk_text.py`（文本分块工具）

### 集成到 Skill

- [ ] Step 11: 修改 `session-summary` skill — 摘要生成后自动写入向量索引
- [ ] Step 12: 修改 `memory-warmup` skill — 注入语义搜索结果
- [ ] Step 13: 在 Hermem SKILL.md 中注册新工具

### 测试验证

- [ ] Step 14: 单元测试 — embedding 存储和检索
- [ ] Step 15: 单元测试 — 混合搜索 RRF 融合
- [ ] Step 16: 集成测试 — 完整语义搜索流程
- [ ] Step 17: 人工验收 — Oliver 提问测试召回质量
- [ ] Step 18: 对比测试 — FTS5 vs 混合搜索（同样查询哪个更好）

### 文档

- [ ] Step 19: 更新 PROJECT.md — Phase 2 完成记录
- [ ] Step 20: 写 REVIEW.md — 验收结果和发现

---

## 实施顺序说明

```
Phase 2 实施顺序：

1. 环境准备（Step 1-3）
   ↓
2. 核心模块（Step 4-10）
   顺序：embedding_client → embedding_store → concept_tagger → hybrid_search
   ↓
3. 集成（Step 11-13）
   修改现有 skill 自动调用新模块
   ↓
4. 测试（Step 14-18）
   先单元测试，再集成测试，最后 Oliver 验收
   ↓
5. 收尾（Step 19-20）
   更新文档
```

## 依赖关系

| 步骤 | 依赖 | 阻塞 |
|------|------|------|
| Step 6-10 | Step 5 | 否 |
| Step 11-13 | Step 6-10 | 是 |
| Step 14-18 | Step 11-13 | 是 |
| Step 19-20 | Step 14-18 | 是 |
