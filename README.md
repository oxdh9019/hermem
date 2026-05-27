# Hermem

Hermes lightweight memory enhancement system — L0–L3 hierarchical memory with Predictive Coding (V4).

## Version History

| Version | Name | Description |
|---------|------|-------------|
| V1–V3 | Phase 1–3 | L0→L1→L2→L3 pipeline, semantic search — design docs in `phase1/`/`phase2/`/`phase3/` |
| **V4** | **Predictive Memory** | Phase 4 — memory as generative model, not stored text |
| **V4.1** | **Error Annotation** | Predict what should happen; tag prediction errors when they don't |
| **V4.2** | **Conditioned Dispositions** | (condition, prediction, error_history) tuples replacing flat facts |
| **V4.3** | **Error-Activated Retrieval** | Beta — error signal closes the learning loop |
| **V4.4** | **Concurrency Fixes** | Vectorstore double-lock, auto_index file lock, watchdog drift monitor |
| **V4.5** | **Disposition-Aware Rerank** | Boost L1 facts via disposition context — error_count now drives retrieval ranking |
| **V5** | **Active Retrieval** | bge-m3 vector search in-conversation — context-aware memory injection during chat |

> **V5 is active** (2026-05-27). 1637 chunks embedded with bge-m3, tiered thresholds (high≥0.70/medium≥0.65), session dedup, injection format `[自动回忆 - 相似度 X.XX]`.

---

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

**Current data: 1706 vectors (1637 chunks), 22 dispositions, 80 L2 scenes** (as of 2026-05-27).

---

## Directory Structure

```
hermem/                          # Canonical directory — single git clone of hermem-github/
│
├── README.md                    # This file
├── PROJECT.md                   # Three-phase plan overview + version changelog
│
├── phase1/                      # Phase 1 design documents only
│   ├── SPEC.md
│   └── REVIEW.md
│
├── phase2/                      # Phase 2 design documents only
│   ├── SPEC.md
│   └── REVIEW.md
│
├── phase3/                     # Phase 3 design + all V1–V4 implementation
│   ├── SPEC.md                 # Phase 3 specification
│   ├── TODO.md
│   ├── cron_daily.py           # Daily pipeline entry (journal 02:00 + synthesis 06:00)
│   │
│   ├── impl/                   # ← All active implementation (V1–V5)
│   │   ├── __init__.py
│   │   ├── config.py           # Config: model names, paths, constants
│   │   ├── utils.py            # LLM calls, embeddings, serialization
│   │   ├── db_init.py          # Schema init: l1_facts, l1_dispositions, l2_scenes, l3_staging
│   │   ├── database.py         # SQLite helpers
│   │   ├── l0_store.py         # L0 raw session archival + MiniMax routing
│   │   ├── l1_extract.py       # LLM fact extraction (type/content/tags/value)
│   │   ├── l1_search.py        # Semantic vector search + B8 activation score
│   │   ├── l2_aggregate.py    # Embedding similarity scene clustering
│   │   ├── l3_staging.py      # Preference staging → user_profile.md
│   │   ├── disposition_updater.py  # Three-tier error matching + disposition update
│   │   ├── intent_classifier.py   # 13-intent classifier (B2)
│   │   ├── vectorstore.py      # Vector storage (npy): double-lock append_vectors
│   │   ├── retrieval.py        # Semantic + keyword + hybrid search
│   │   ├── embedding.py       # Ollama bge-m3 embeddings
│   │   ├── vector_search.py    # V5: bge-m3 cosine similarity + search_with_tier()
│   │   ├── verify_annotation.py  # Annotation quality audit
│   │   ├── async_annotation.py   # Async annotation queue
│   │   ├── batch_extract.py   # Batch L1 extraction
│   │   │
│   │   ├── test_vector_concurrent.py   # P0 concurrent stress test
│   │   └── test_auto_index_concurrent.py  # P1 file lock test
│   │
│   ├── impl_phase2/            # Phase 2 vectorstore backfill layer
│   │   ├── __init__.py
│   │   ├── vectorstore.py     # Phase 2 vector operations
│   │   ├── database.py
│   │   ├── embedding.py
│   │   ├── retrieval.py
│   │   ├── commands.py         # CLI commands for backfill
│   │   ├── migrate.py          # Migration scripts
│   │   └── batch_backfill.py  # Batch backfill tool
│   │
│   ├── scripts/                # Operational scripts (cron-called)
│   │   ├── watchdog_vectorstore.py   # Drift monitor (cron: every 360m)
│   │   ├── daily_synthesis.py        # Daily synthesis (cron: 06:00)
│   │   ├── journal.py                 # Daily self-journal (cron: 02:00)
│   │   ├── process_turn_judgments.py # V4.4 per-turn judgment processor
│   │   ├── generate_dispositions_from_annotations.py  # V4.3 seed dispositions
│   │   ├── backfill_vectors.py       # Vector backfill helper
│   │   ├── rebuild_vectorstore.py     # Compact + remap rebuild tool
│   │   ├── batch_compute_embeddings.py  # V5: precompute all chunk embeddings
│   │   ├── fix_drift_and_fill_embeddings.py  # V5: drift fix + embedding backfill
│   │   └── test_v5_e2e.py             # V5: end-to-end tests (7/8 passing)
│   │
│   ├── eval/                   # Evaluation scripts
│   │   ├── eval_compare.py            # Model comparison eval
│   │   ├── eval_qwen35_4b.py          # qwen3.5:4b eval
│   │   ├── per_turn_judgment_eval.py  # Per-turn judgment quality eval
│   │   └── test_l1_extraction.py      # L1 extraction quality test
│   │
│   ├── tests/                  # Test suite
│   │   ├── unit/
│   │   │   ├── test_disposition_updater.py  # 25 cases
│   │   │   ├── test_intent_classifier.py     # 75 cases (3 pre-existing gaps)
│   │   │   └── test_l1_search.py             # 16 cases (B8 formula)
│   │   └── test_phase2c_pending_recall.py
│   │
│   ├── test_intent_coverage.py
│   ├── openclaw_import.py      # OpenClaw disposition import
│   └── v4_2_migrate.py         # V4.2 migration script
│
└── plugins/memory/hermem/       # Hermes gateway plugin wrapper
    └── __init__.py             # HermemMemoryProvider + 8 trigger conditions
```

---

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
**Model**: MiniMax-M2.7 with `x-no-think: true` header.

### V4.2 — Conditioned Dispositions

Replace propositional L1 facts with `(condition, prediction, confidence, error_history)`. After V4.1 annotates a session with prediction errors, V4.2 stores the corrected behavior pattern as a conditioned disposition:

- `condition_text`: when does this pattern activate?
- `prediction_text`: what does the user expect?
- `condition_embedding`: semantic index for retrieval
- `error_count` / `success_count`: tracks prediction accuracy over time
- `disposition_decay`: time × frequency joint decay (7-day half-life)

### V4.3 — Error-Activated Retrieval

V4.3 completes the error-driven learning loop. **End-to-end annotation pipeline verified (2026-05-22).**

**Key fix (V4.3.1):** Plan B consumer bypassed broken L0-file queue — annotation now runs via direct `llm_generate()` + `update_dispositions_from_errors()` in ThreadPoolExecutor threads. Feedback annotation JSON cascades to disposition `error_count` updates.

**Model changes (2026-05-22):**
- Per-turn judgment: switched from `llm_generate` (OpenAI endpoint, empty response for qwen3.5:4b-no-think) to `llm_generate_ollama` (native `/api/chat`)
- All Hermem plugin calls unified on `qwen3.5:4b-no-think`
- Annotation: `MiniMax-M2.7` (qwen3.5:4b-no-think times out on long ERROR_ANNOTATION_PROMPT inputs)

**8 Trigger Conditions:**

**Intent Classification (B2):** 13 intents + two-layer architecture.

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

**8 Trigger Conditions:**

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

**Daily Loop:**
- **02:00 — Self-Journal**: reads all L0 sessions of the day, writes patterns/errors/solutions to journal
- **06:00 — Synthesis**: compresses learnings into active memory for next prompt
- **Feedback**: journal output re-injects into disposition system

**Completed:** B1, B2, B4, B5, B6, B8, B9, C3

**Pending:** B3 (dynamic threshold), B7 (multi-error weights), C1/C2 (gateway hooks)

### V4.5 — Disposition-Aware Rerank (2026-05-22)

|V4.5 closes the error_count → behavior loop. `disposition_aware_rerank()` boosts L1 facts that share context with top dispositions, so dispositions don't just accumulate error_count — they actively rerank what Hermem retrieves.

**Boost paths:**
1. `l0_ref` exact match — disposition and fact from the same session (precision path)
2. Condition keyword → fact content overlap ≥ 2 hits (fallback for UUID-format dispositions from OpenClaw import)

**Code:**
```python
# Phase 3 impl/l1_search.py
disposition_aware_rerank(l1_results, dispositions, query=query, boost_factor=1.5)
```

**Boost log:** `~/.hermes/logs/hermem-boost.jsonl` (async, non-blocking). Each entry records:
- `query`, `disposition_ids`, `boosted_facts[]` with `match_method`, `old_sim`, `new_sim`
- Analyzable with: `jq 'select(.query | contains("..."))' ~/.hermes/logs/hermem-boost.jsonl`

**Schema note:** `l1_dispositions.l0_ref` (UUID) ≠ `l1_facts.l0_ref` (l0_YYYYMMDD_HHMMSS). Only dispositions created after 2026-05-18 have matching L0 refs. Path 2 (keyword fallback) covers the rest.

### V5 — Active Retrieval (2026-05-27)

V5 brings **in-conversation memory retrieval** — Hermem proactively searches semantic memory during chat and injects relevant past context without waiting for the user to ask.

**How it works:**
```
User message
    ↓
Every N turns (frequency=3): vector search
    ↓
Tiered threshold check:
  high (≥0.70): inject immediately via [自动回忆 - 相似度 X.XX]
  medium (0.65–0.70): cache, promote if seen again
  low (<0.65): ignore
    ↓
Session dedup: same chunk injected at most once per session
```

**Key components:**
- `impl/vector_search.py`: bge-m3 cosine similarity search + `search_with_tier()`
- `impl/embedding.py`: Ollama bge-m3 embeddings, cached in SQLite
- `impl/config.py`: `ACTIVE_RETRIEVAL_*` flags + thresholds (all tunable)
- `phase3/scripts/batch_compute_embeddings.py`: precompute all 1637 chunk vectors
- `phase3/scripts/test_v5_e2e.py`: 7/8 tests passing

**Thresholds (tuned 2026-05-27):**
- HIGH: 0.70 (实测最高相似度 0.77，0.85 无法命中)
- MEDIUM: 0.65
- TOP_K: 3 per turn
- FREQUENCY: every 3 turns

**Pending (Phase B):**
- Medium-confidence accumulation trigger: promote cached chunk when similarity rises above HIGH threshold
- Incremental embedding for new chunks (Step 1b)

### V4.4 — Concurrency Fixes (2026-05-21)

| Phase | Feature | Status |
|-------|---------|--------|
| P0 | `append_vectors()` double-lock: `threading.Lock` + `fcntl.flock` | ✅ |
| P1 | `hermem_auto_index_all.py` file lock around `main()` | ✅ |
| P2 | `watchdog_vectorstore.py`: drift detection + `--fix` auto-repair, cron every 360m | ✅ |

---

## Requirements

- Ollama (`localhost:11434`) — bge-m3 for embeddings
- MiniMax API key (`MINIMAX_CN_API_KEY` in `~/.hermes/.env`) — for error annotation + LLM calls
- SQLite 3 (built into Python stdlib)

## Quick Start

```bash
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# Initialize L1/L2/L3 tables
python3 phase3/impl/db_init.py

# Run daily pipeline (journal 02:00 + synthesis 06:00)
python3 phase3/cron_daily.py
```

---

## Changelog

### 2026-05-27 — V5 Active Retrieval

**`origin/main` → `a11f73f`**:

- **Phase A complete**: bge-m3 vector search + tiered thresholds + injection format + session dedup
- `impl/vector_search.py`: cosine similarity with `search_with_tier()` (high/medium/low tiers)
- `impl/embedding.py`: bge-m3 Ollama embeddings, SQLite cached
- `Hermem-V5-SPEC.md` + `phase3/v5/SPEC.md`: full specification documents
- `phase3/scripts/batch_compute_embeddings.py`: precompute all 1637 chunk embeddings (drift=0)
- `phase3/scripts/test_v5_e2e.py`: 7/8 tests passing
- HIGH threshold: 0.85 → 0.70 (实测最高 0.77，0.85 无法命中)

### 2026-05-23 — V4.5 Patch (15 Fixes)

**`origin/main` → `6e05d22`** (all pushed):

| Fix | Area | Description |
|-----|------|-------------|
| 1 | Schema | `db_init.py` CREATE TABLE 补全 source_agent/scope/weight/intent 四列 |
| 1b | Migration | `scripts/migrate_add_disposition_columns.py` 一次性迁移脚本（含 scope DEFAULT） |
| 1c | Cleanup | `generate_dispositions_from_annotations.py` 删除冗余 ALTER TABLE 块 |
| 2 | Bug | `process_turn_judgments.py` JSON regex fallback 缩进错误导致 `return []` 误执行 |
| 3 | Schema | `v4_2_migrate.py` INSERT 补四列 + 日期格式修复 + l0_ref 格式化为 `l0_{session_id}` |
| 4 | Weight | `cron_daily.py` 每次 L1 batch 后调用 `update_disposition_weights()` 触发 weight 重算 |
| 7 | l0_ref | `openclaw_import.py` l0_ref 统一为 `l0_{session_id}` 格式 |
| 8 | Perf | `l1_search.py` Boost 日志从"每次新 Thread"→ 单线程队列消费 |
| 9 | SQL | `daily_synthesis.py` SQL 加 `error_count >= 2` 过滤（Python 侧已有，SQL 补漏） |
| 10 | Cache | `process_turn_judgments.py` FactCache max_size 5→500 |
| 11 | Security | `backfill_vectors.py` f-string CASE WHEN → 临时表 + executemany 参数化 |
| 11b | Security | `backfill_vectors.py` vec_index >= 参数化 |
| 12 | Lock | `backfill_vectors.py` 失效 `acquire_lock()` → `fcntl.flock` FileLock |
| 13 | Atomicity | `vectorstore.py` `shutil.copy2` → `os.replace`（原子写入） |
| 14 | Embedding | `l2_aggregate.py` L2 scene 合并时重算 scene_embedding（加权平均） |
| 15 | Bug | `journal.py` `--date` 参数 now works（`fetch_session_summaries()` 接受 date 参数） |
| Fix 6 | Precise match | `async_annotation.py` 4-tuple 格式 + `increment_success_by_ids()` 精确 success 匹配 |

**hermes-agent** (`4f4c7c137`, local only):
- `_run()` prefetch + `_recall_dispositions_for_correction()` 补上 `_last_activated_disposition_ids` 提取，使 Fix 6 端到端生效

---

### 2026-05-22 — V4.3.1 Patch

**hermem repo** (`d63f663`, pushed to `origin/main`):
- **B1/B2/B4 fixes**: `l1_search.py` trigger keyword case-insensitivity, `_judge_similar()` fallback to `condition_embedding` similarity, `l0_store.py` GC `stat`-before-`unlink`
- **Per-turn judgment endpoint**: `llm_generate` (OpenAI `/v1/chat/completions`) → `llm_generate_ollama` (native `/api/chat`) for qwen3.5:4b-no-think compatibility
- **Model unification**: all plugin calls on `qwen3.5:4b-no-think`; qwen3.5:2b and qwen3.5:2b-no-think unloaded from Ollama (saves ~2.6GB)
- **Annotation model**: `MiniMax-M2.7` (not qwen3.5:4b — times out on long ERROR_ANNOTATION_PROMPT prompts)

**hermes-agent repo** (`d16b167bf`, local commit only):
- **Plan B consumer fix**: bypassed broken `enqueue_annotation_lightweight` → queue → worker → L0 file path; consumer now calls `llm_generate()` + `update_dispositions_from_errors()` directly in ThreadPoolExecutor threads
- **End-to-end verified**: injected feedback → annotation → disposition cascade confirmed (error_count 12→19 on `disp_hm_20260519215550_17`)
- hermes-agent remote is `NousResearch/hermes-agent` (no write access) — commit stays local

---

## Outstanding Issues

||| Issue | Notes | Revisit After |
|-------|-------|---------------|
| **B3 is_recurring_cross_session** | Dynamic threshold blocked — success_count=0 from annotation-only data. B3 bypass via satisfaction check (V4.4 Plan B) — no dynamic threshold needed yet. | More satisfaction check data |
| **V4.5 keyword threshold tuning** | `MIN_HITS=2` is conservative. After 1-2 weeks of boost log data, tighten to `max(2, ceil(n_keywords*0.4))` or add `trigger_keywords` field. | Boost log analysis |
| **Fix 6 annotation pipeline** | Precise success matching via `active_disposition_ids` — V4.5 end-to-end now verified. | Live data validation |

## Caveats

| Issue | Status |
|-------|--------|
| Phase 1/2 skill layer | ✅ `skills/hermem/` |
| Phase 3 plugin (`plugins/memory/hermem/`) | ✅ HermemMemoryProvider registered in Hermes config |
| V4.1 Error Annotation | ✅ MiniMax-M2.7 async queue |
| V4.2 Conditioned Dispositions | ✅ l1_dispositions table, extract/vector_search/three-tier detection |
| V4.3 Error-Activated Retrieval | ✅ Beta (v4.3.0-beta) — B1/B2/B4/B5/B6/B8/B9/C3 complete |
| **V4.4 Concurrency Fixes** | ✅ P0/P1/P2 complete |
| **V5 Active Retrieval** | ✅ Phase A — vector search, injection, dedup. Phase B pending (medium accumulation, incremental embedding). |
| Intent Classifier (B2) | ✅ 13 intents + 2-layer architecture |
| Daily Journal + Synthesis Loop | ✅ Cron at 02:00 / 06:00 |
| C1/C2 gateway hooks | ⚠️ Defined but not called by Hermes gateway yet |
| Unit tests | ⚠️ 116 passed, 3 failed (intent_classifier trigger gaps — pre-existing) |
| CI/CD | ❌ None |

---

## Design Principles

- **Minimal dependencies**: Pure Python + SQLite, no heavy runtimes
- **Plain text storage**: All memories in readable Markdown, auditable and editable
- **Progressive disclosure**: Load only relevant memory to avoid context overflow
- **Self-auditing**: yoyo-evolve-style "Truman Show" — git log, journal, annotations all public

## License

MIT
