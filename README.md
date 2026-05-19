# Hermem

Hermes lightweight memory enhancement system — L0–L3 hierarchical memory with Predictive Coding (V4).

## Version History

| Version | Name | Description |
|---------|------|-------------|
| V1–V3 | Phase 1–3 | L0→L1→L2→L3 pipeline, semantic search |
| **V4** | **Predictive Memory** | Phase 4 — memory as generative model, not stored text |
| **V4.1** | **Error Annotation** | Predict what should happen; tag prediction errors when they don't |

> **V4.3 (Error-Activated Retrieval, beta) is active.** Tag: `v4.3.0-beta`
> Run `python phase3/impl/verify_annotation.py` to check signal quality after a few days of data accumulation.

## What Hermem Actually Does

```
Daily cron (6:00 + 18:00)
    ↓
Scan new sessions from Hermes state.db
    ↓ extract (Ollama LLM, qwen2.5:3b)
L1: Atomic facts → SQLite l0_l3.db (vector BLOB)
    ↓ aggregate (embedding similarity ≥ 0.75)
L2: Scene clusters (topic groups with occurrence counts)
    ↓ stage (preference-type facts)
L3: Staging → user_profile.md confirmation
```

Current data: **~1145 L1 chunks, 22 L1 dispositions (6 model_error + 16 user_behavior), ~30 L2 scenes** (as of 2026-05-20).

## Phase 4 — Predictive Memory (V4)

V4 rethinks memory as a **generative model** rather than stored text. Instead of retrieving facts and hoping they are relevant, Hermem predicts what the user needs based on context, then activates only when the prediction is violated — the error signal is what drives learning.

```
Context → Predict what should happen → Compare to what actually happens
                                                    ↓
                                          Error signal → Update model
```

### V4.1 — Error Annotation (active)

After each session, annotate L0 with prediction errors the assistant made:
- `prediction_errors[]`: falsifiable predictions that were violated
- `surprise_level`: how unexpected this session was
- `confidence`: per-error certainty (0–1)
- `overall_quality_score`: session-level prediction quality (0–1)

Annotation runs **asynchronously** (background queue, does not block session processing).
Run `python phase3/impl/verify_annotation.py` to audit signal quality.

### V4.2 — Conditioned Dispositions (active)

Replace propositional L1 facts with `(condition, prediction, confidence, error_history)`. After V4.1 annotates a session with prediction errors, V4.2 stores the corrected behavior pattern as a conditioned disposition:

- `condition_text`: when does this pattern activate?
- `prediction_text`: what does the user expect?
- `condition_embedding`: semantic index for retrieval
- `confidence`: initial confidence from extraction evidence

Dispositions are extracted by `extract_dispositions()` from session summaries (LLM, confidence ≥ 0.6 required). Retrieved in parallel with L1 facts via `vector_search_dispositions()` — ranked by cosine similarity against the current query's condition embedding.

In HermemMemoryProvider, `sync_turn()` detects corrections via three-tier detection (strong keyword / medium cosine / weak negation heuristic) and activates matching dispositions for the next turn. Active dispositions decay after 2 turns of no correction signal.

Run `python phase3/v4_2_migrate.py` to expand the disposition dataset from new sessions.

### V4.3 — Error-Activated Retrieval (beta — `v4.3.0-beta`)

V4.3 在 V4.2 的基础上完成了误差驱动的学习闭环。

**已完成：**

- **B1** — `update_dispositions_from_errors()` 按 error_type 匹配 disposition 并递增 error_count
- **B4** — 同步 annotation 路径（`sync_turn()` → `annotate_l0_after_l1_v2()` → `update_dispositions_from_errors()`，同一轮内立即生效）
- **B5** — scope 过滤：disposition 表加 `scope` 列（`model_error` vs `user_behavior`），检索时隔离
- **B6** — Disposition 衰减机制：时间半衰期（7天）× 频次衰减，高频 disposition 权重更高
- **B8** — 三维权召回 ranking：`score = sim × f_time × min(error_count, 5)`
- **B9** — few-shot 示例：8个示例覆盖全 5 种 error_type + 反例 + 边界 case

**待完成（pending success_count 积累）：**

- **B3** — 动态 threshold：需要 success_count > 0 才能建立 error_count/success_count 分布
- **B7** — 多 error_type 权重：依赖 B3 动态 threshold 输出

**待完成（P2 阶段）：**

- **B10** — 跨 session 误差模式
- **B11** — token 成本监控

## Requirements

- Ollama (`localhost:11434`) — bge-m3 for embeddings, qwen2.5:3b for extraction
- SQLite 3 (built into Python stdlib)

## Quick Start

```bash
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# Initialize L1/L2/L3 tables
python phase3/impl/db_init.py

# Run daily processing (optional — sets up cron at 6:00 and 18:00)
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
│   ├── cron_daily.py       # Daily L0→L1→L2→L3 pipeline
│   └── impl/
│       ├── db_init.py      # Schema: l1_facts, l2_scenes, l3_staging
│       ├── l0_store.py     # L0 raw session archival + 500MB GC
│       ├── l1_extract.py   # LLM fact extraction (type/content/tags/value)
│       ├── l1_search.py    # Semantic vector search + boost post-processing
│       ├── l2_aggregate.py # Embedding similarity scene clustering
│       └── l3_staging.py   # Preference staging → user_profile.md
└── impl/                   # Legacy Phase 1/2 code (not integrated)
    ├── database.py
    ├── embedding.py
    ├── vectorstore.py
    ├── retrieval.py
    └── batch_backfill.py
```

## Phase 3 Key Design Decisions

- **No Hard Filter**: L1 search does NO fact-type filtering — only boost post-processing (`preferred_types × 1.5`)
- **Small models first**: qwen2.5:3b for extraction (~30s), not qwen3.5:9b (~90s+, times out on M4)
- **Ollama-only**: No external API calls for inference; MiniMax only used when Hermes itself calls it
- **Skill-only delivery**: No core Hermes code modification required

## Caveats

| Issue | Status |
|-------|--------|
| Phase 1/2 skill layer | ✅ `skills/hermem/` (session-summary, memory-warmup, memory-tools) — real, loaded in Hermes |
| Phase 1/2 plugin layer (`plugins/memory/hermem/`) | ❌ Empty — `memory.provider: hermem` in config points to non-existent plugin |
| Phase 3 `plugins/memory/hermem/` | ✅ Implemented — HermemMemoryProvider with Hermem Phase 2 backend, registered in Hermes config as `memory.provider: hermem` |
| V4.1 Error Annotation | ✅ Implemented — async queue + V3 prompt, awaiting data accumulation |
| V4.2 Conditioned Dispositions | ✅ Implemented — l1_dispositions table, extract_dispositions(), vector_search_dispositions(), three-tier correction detection in HermemMemoryProvider |
| V4.3 Error-Activated Retrieval | ✅ Beta (`v4.3.0-beta`) — B1/B4/B5/B6/B8/B9 complete, B3/B7 pending success_count |
| Unit tests | ❌ None — smoke-test only |
| CI/CD | ❌ None |
| Community stars | 0 — personal project, no external users |

**If you want to integrate Hermem as a real memory provider plugin**: the `impl/` code needs to be wrapped in a proper `plugins/memory/hermem/__init__.py` that exposes the `MemoryProvider` ABC. The skill layer is independent of that.

## Design Principles

- **Minimal dependencies**: Pure Python + SQLite, no heavy runtimes
- **Plain text storage**: All memories in readable Markdown, auditable and editable
- **Progressive disclosure**: Load only relevant memory to avoid context overflow
- **Skill-only delivery**: No core Hermes code modification required

## License

MIT