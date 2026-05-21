# Hermem

Hermes lightweight memory enhancement system — L0–L3 hierarchical memory with Predictive Coding (V4).

## Version History

| Version | Name | Description |
|---------|------|-------------|
| V1–V3 | Phase 1–3 | L0→L1→L2→L3 pipeline, semantic search |
| **V4** | **Predictive Memory** | Phase 4 — memory as generative model, not stored text |
| **V4.1** | **Error Annotation** | Predict what should happen; tag prediction errors when they don't |
| **V4.2** | **Conditioned Dispositions** | (condition, prediction, error_history) tuples replacing flat facts |
| **V4.3** | **Error-Activated Retrieval** | Beta — error signal closes the learning loop |
| **V4.4** | **Concurrency Fixes** | Vectorstore double-lock, auto_index file lock, watchdog drift monitor |

> **V4.4 is active** (2026-05-21). Vectorstore now has process-safe double locking; watchdog runs every 360m with auto-fix.

## What Hermem Actually Does

```
Session ends
    ↓
L0: Raw transcript archived (JSON, 500MB GC)
    ↓
L1: Atomic facts extracted (MiniMax-M2.7)
    ↓ aggregate (embedding similarity ≥ 0.75)
L2: Scene clusters
    ↓ stage (preference-type facts)
L3: user_profile.md confirmation
    ↓
Intent Classification (13 intents) → routes to disposition update or retrieval
    ↓
Disposition: (condition, prediction, error_count, success_count)
    ↓ daily synthesis
Active Memory ← learnings + social learnings fed back to next prompt
```

Current data: **1264 vectors, 22 dispositions (6 model_error + 16 user_behavior), 80 L2 scenes** (as of 2026-05-21, compact-applied).

## Phase 4 — Predictive Memory (V4)

V4 rethinks memory as a **generative model** rather than stored text. Instead of retrieving facts and hoping they are relevant, Hermem predicts what the user needs based on context, then activates only when the prediction is violated — the error signal is what drives learning.

```
Context → Predict what should happen → Compare to what actually happens
                                                    ↓
                                          Error signal → Update disposition
                                                    ↓
                                          Daily synthesis → Active memory
```

### V4.1 — Error Annotation

After each session, annotate L0 with prediction errors the assistant made:
- `prediction_errors[]`: falsifiable predictions that were violated
- `surprise_level`: how unexpected this session was
- `confidence`: per-error certainty (0–1)
- `overall_quality_score`: session-level prediction quality (0–1)

Annotation runs **asynchronously** (background queue, does not block session processing).
**Model**: MiniMax-M2.7 with `x-no-think: true` header (migrated from qwen2.5:3b → qwen3.5:2b, 2026-05-21).

### V4.2 — Conditioned Dispositions

Replace propositional L1 facts with `(condition, prediction, confidence, error_history)`. After V4.1 annotates a session with prediction errors, V4.2 stores the corrected behavior pattern as a conditioned disposition:

- `condition_text`: when does this pattern activate?
- `prediction_text`: what does the user expect?
- `condition_embedding`: semantic index for retrieval
- `error_count` / `success_count`: tracks prediction accuracy over time
- `disposition_decay`: time × frequency joint decay (7-day half-life)

### V4.3 — Error-Activated Retrieval (beta)

V4.3 completes the error-driven learning loop.

**Architecture — Intent Classification (B2):**

独立 LLM 调用做意图分类，13 种意图 + 两层判断架构：

| 意图 | 描述 | 处置 |
|---|---|---|
| 学习 | 想学习/理解某概念 | 触发 recall 模式 |
| 执行 | 明确任务指令 | 直接执行 |
| 修正 | 纠正 Hermem 错误 | 更新 disposition |
| 结束/关闭 | 阶段性收尾 | 更新会话摘要 |
| 反馈 | 提供意见/评价 | 触发轻量标注 |
| 确认 | 确认/批准某事 | 路由到执行 |
| 建议 | 提出建议 | 记录为 preference |
| 记忆 | 存储/检索记忆 | 调用 Hermem |
| 修改 | 修改/编辑内容 | 执行修改 |
| 停止 | 停止当前操作 | 中断任务流 |
| 提问 | 提出问题 | 直接回答 |
| 咨询 | 寻求意见/建议 | 生成建议 |
| 评估 | 判断/评估某事 | 提供分析 |

**8 Trigger Conditions (Annotation Coverage):**

| 触发 | 类型 | 信号质量 |
|---|---|---|
| A1 用户明确否定 | strong | ✅ 清晰 |
| A2 用户部分纠正（"但是"、"但"） | strong | ✅ 清晰 |
| B1 Agent 自修正（"等等我修正"、"重新回答"） | strong | ✅ 清晰 |
| B2 Agent 表达不确定（"不确定"） | medium | ✅ 清晰 |
| B3 Agent 放弃（"我做不到"、"我无法"） | strong | ✅ 清晰 |
| C1 LLM 错误 | — | ⚠️ 待 gateway 集成 |
| C2 工具错误 | — | ⚠️ 待 gateway 集成 |
| C3 session 结束兜底 | — | ✅ 已生效 |

C3 兜底把 annotation 覆盖率从 6.7% 提升至接近 100%（session 结束时无条件触发）。

**Daily Synthesis + Active Memory Loop:**

参考 yoyo-evolve 的设计（"A Truman Show of a self-evolving AI coding agent"）：

- **每日 02:00 — Self-Journal**：读取当天所有 L0 session，总结学到的 pattern、犯的错误、解决的疑问，写入 journal 文件
- **每日 06:00 — Synthesis**：压缩 learnings + social learnings 进 active memory，再喂回下一轮 prompt
- **反馈循环**：journal output 再注入 disposition 系统，形成持续进化的记忆层

**Completed:**

- **B1** — `update_dispositions_from_errors()` 按 error_type 匹配 disposition 并递增 error_count
- **B2** — Intent Classifier：13 意图分类 + 两层判断架构
- **B4** — 同步 annotation 路径（`sync_turn()` → `annotate_l0_after_l1_v2()` → `update_dispositions_from_errors()`）
- **B5** — scope 过滤：disposition 表 `scope` 列（`model_error` vs `user_behavior`），检索时隔离
- **B6** — Disposition 衰减机制：时间半衰期（7天）× 频次衰减
- **B8** — 三维权召回 ranking：`score = sim × f_time × min(error_count, 5)`
- **B9** — few-shot 示例：8个示例覆盖全 5 种 error_type + 反例 + 边界 case
- **C3** — session 结束兜底 annotation，覆盖率从 6.7% 提升

**Pending:**

- **B3** — 动态 threshold：需要 success_count > 0 才能建立 error_count/success_count 分布
- **B7** — 多 error_type 权重：依赖 B3 动态 threshold 输出
- **C1/C2** — `on_llm_error()`/`on_tool_error()` 钩子：需要 gateway 支持

**P2:**

- **B10** — 跨 session 误差模式
- **B11** — token 成本监控

## V4.4 — Concurrency Fixes (2026-05-21)

**P0: Double Locking for append_vectors()**

- `threading.Lock` (process-local) + `fcntl.flock` (inter-process) dual protection
- 9 rounds multi-process concurrent stress test: drift=0, 100% pass

**P1: auto_index File Lock**

- `hermem_auto_index_all.py`: `fcntl.flock` wraps `main()`, prevents concurrent overwrites
- Script: `phase3/scripts/hermem_auto_index_all.py`

**P2: Watchdog Drift Monitor**

- `watchdog_vectorstore.py`: detects drift between `hermem_vectors.npy` and `hermem.db` chunk refs
- `--fix` flag auto-truncates orphan vectors and remaps chunk.vec_index
- Cron: every 360 minutes, auto-fix then report to home channel
- Script: `phase3/scripts/watchdog_vectorstore.py`

**New Components:**

- `phase3/impl_phase2/`: Phase 2 vectorstore layer (batch_backfill, commands, database, embedding, migrate, retrieval, vectorstore)
- `phase3/eval/`: Per-turn judgment evaluation suite (eval_compare, eval_qwen35_4b, per_turn_judgment_eval, test_l1_extraction)
- `phase3/scripts/rebuild_vectorstore.py`: Compact + remap rebuild tool for drift repair
- `phase3/scripts/journal.py`: Daily self-journal script (02:00)
- `phase3/scripts/daily_synthesis.py`: Daily synthesis script (06:00)

## Requirements

- Ollama (`localhost:11434`) — bge-m3 for embeddings
- MiniMax API key (`MINIMAX_CN_API_KEY` in `~/.hermes/.env`) — for error annotation + LLM calls
- SQLite 3 (built into Python stdlib)

## Quick Start

```bash
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# Initialize L1/L2/L3 tables
python phase3/impl/db_init.py

# Run daily pipeline (sets up cron: journal at 02:00, synthesis at 06:00)
python phase3/cron_daily.py
```

## Project Structure

```
hermem/
├── PROJECT.md              # Three-phase plan overview
├── phase1/                 # Session summarization + warmup (design only)
│   ├── SPEC.md
│   └── REVIEW.md
├── phase2/                 # Semantic search design (design only)
│   ├── SPEC.md
│   └── REVIEW.md
├── phase3/                 # ← Active deliverable
│   ├── SPEC.md
│   ├── TODO.md
│   ├── cron_daily.py       # Daily: journal(02:00) + L0→L1→L2→L3(06:00)
│   └── impl/
│       ├── db_init.py      # Schema: l1_facts, l1_dispositions, l2_scenes, l3_staging
│       ├── l0_store.py     # L0 raw session archival + annotation + MiniMax routing
│       ├── l1_extract.py  # LLM fact extraction (type/content/tags/value)
│       ├── l1_search.py    # Semantic vector search + disposition ranking
│       ├── l2_aggregate.py # Embedding similarity scene clustering
│       ├── l3_staging.py   # Preference staging → user_profile.md
│       ├── utils.py        # call_minimax() with x-no-think header
│       ├── config.py       # ERROR_ANNOTATION_MODEL = MiniMax-M2.7
│       └── verify_annotation.py  # Annotation quality audit
└── plugins/memory/hermem/ # Hermes plugin wrapper (loaded by Hermes gateway)
    └── __init__.py         # HermemMemoryProvider + 8 trigger conditions + hooks
```

## Phase 3 Key Design Decisions

- **No Hard Filter**: L1 search does NO fact-type filtering — only boost post-processing
- **LLM for extraction**: qwen2.5:3b → qwen3.5:2b (2026-05-21), qwen3.5 routes via native API + think:false
- **MiniMax for annotation**: qwen2.5:3b → MiniMax-M2.7 (2026-05-20), enables full Phase3 capabilities
- **Skill-only delivery**: No core Hermes code modification required for the skill layer
- **Auditability over performance**: git log, journal, annotations — all publicly auditable

## Outstanding Issues

| Issue | Notes | Revisit After |
|-------|-------|---------------|
| **B3 is_recurring_cross_session bypass** | BLOCKED — all error annotations map to 2-3 broad disposition buckets; no granularity to distinguish recurring vs isolated. Also: success_count=0 (all annotations flagged as errors, no success path ever reached). | V4.4 per-turn judgment provides finer-grained data |

## Caveats

| Issue | Status |
|-------|--------|
| Phase 1/2 skill layer | ✅ `skills/hermem/` — session-summary, memory-warmup, memory-tools |
| Phase 3 plugin (`plugins/memory/hermem/`) | ✅ HermemMemoryProvider registered in Hermes config |
| V4.1 Error Annotation | ✅ MiniMax-M2.7 async queue |
| V4.2 Conditioned Dispositions | ✅ l1_dispositions table, extract/vector_search/three-tier detection |
| V4.3 Error-Activated Retrieval | ✅ Beta (v4.3.0-beta) — B1/B2/B4/B5/B6/B8/B9/C3 complete |
| V4.4 Concurrency Fixes | ✅ P0/P1/P2 complete — double-lock, auto_index lock, watchdog with auto-fix |
| Intent Classifier (B2) | ✅ 13 intents + 2-layer architecture |
| Daily Journal + Synthesis Loop | ✅ Cron at 02:00 / 06:00 |
| C1/C2 gateway hooks | ⚠️ Defined but not called by Hermes gateway yet |
| Unit tests | ❌ Smoke-test only |
| CI/CD | ❌ None |

## Design Principles

- **Minimal dependencies**: Pure Python + SQLite, no heavy runtimes
- **Plain text storage**: All memories in readable Markdown, auditable and editable
- **Progressive disclosure**: Load only relevant memory to avoid context overflow
- **Self-auditing**: yoyo-evolve-style "Truman Show" — git log, journal, annotations all public

## License

MIT
