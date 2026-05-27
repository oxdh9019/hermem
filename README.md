# Hermem

Hermes lightweight memory enhancement system — L0–L3 hierarchical memory with Predictive Coding (V4).

**V5.1 is live** (2026-05-27). 1645 chunks embedded with bge-m3, tiered thresholds (high≥0.70/medium≥0.65), session dedup, health + rebuild CLI.

---

## Version History

| Version | Name | Description |
|---------|------|-------------|
| V1–V3 | Phase 1–3 | L0→L1→L2→L3 pipeline, semantic search — design docs in `phase1/`/`phase2/`/`phase3/` |
| **V4** | **Predictive Memory** | Memory as generative model, not stored text |
| **V4.1** | **Error Annotation** | Predict what should happen; tag prediction errors when they don't |
| **V4.2** | **Conditioned Dispositions** | (condition, prediction, error_history) tuples replacing flat facts |
| **V4.3** | **Error-Activated Retrieval** | Beta — error signal closes the learning loop |
| **V4.4** | **Concurrency Fixes** | Vectorstore double-lock, auto_index file lock, watchdog drift monitor |
| **V4.5** | **Disposition-Aware Rerank** | Boost L1 facts via disposition context — error_count drives retrieval ranking |
| **V5** | **Active Retrieval** | bge-m3 vector search in-conversation — automatic memory injection during chat |
| **V5.1** | **Engineering Fixes** | drift=91 fixed, `hermes memory health` + `rebuild` CLI, embedding automation audit (no gaps found) |

---

## How Hermem Works

```
Session ends
    ↓
L0: Raw transcript archived (JSON)
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

**Current data: 1711 vectors (1645 chunks), 22 dispositions, 80 L2 scenes** (as of 2026-05-27).

---

## Directory Structure

```
hermem/
│
├── README.md                     # English version
├── README_zh.md                # 中文版
├── QUICKSTART.md                # 5-minute install guide
├── TROUBLESHOOTING.md         # Common issues + fixes
├── install.sh                   # Auto-configure plugin directory
├── requirements.txt             # Minimal dependencies
│
├── templates/
│   └── __init__.py            # Hermes plugin entry (friendly error messages)
│
├── phase1/                      # Phase 1 design docs
├── phase2/                     # Phase 2 design docs
│
├── phase3/                     # Phase 3 design + all V1–V5 implementation
│   ├── impl/                  # ← All active implementation
│   ├── scripts/               # Operational scripts (cron-called)
│   └── eval/                  # Evaluation scripts
│
└── plugins/memory/hermem/     # Hermes gateway plugin wrapper
```

---

## V5 — Active Retrieval (2026-05-27)

V5 brings **in-conversation memory retrieval** — Hermem proactively searches semantic memory and auto-injects relevant past context without waiting for the user to ask.

**How it works:**
```
User message
    ↓
Every N turns (frequency=3): vector search
    ↓
Tiered threshold:
  high (≥0.70): inject immediately, format [自动回忆 - 相似度 X.XX]
  medium (0.65–0.70): cache, promote if seen again
  low (<0.65): ignore
    ↓
Session dedup: same chunk injected at most once
```

**Thresholds (tuned 2026-05-27):**
- HIGH: 0.70 (实测最高 0.77, 0.85 无法命中 → 0.70)
- MEDIUM: 0.65
- TOP_K: 3 per turn
- FREQUENCY: every 3 turns

**Key components:**
- `impl/vector_search.py`: bge-m3 cosine similarity + `search_with_tier()`
- `impl/embedding.py`: Ollama bge-m3 embeddings, SQLite cached
- `impl/config.py`: all `ACTIVE_RETRIEVAL_*` flags tunable
- `phase3/scripts/batch_compute_embeddings.py`: precompute all chunk vectors
- `phase3/scripts/test_v5_e2e.py`: 7/8 tests passing
- `plugins/memory/hermem/cli.py`: `hermes memory health` + `hermes memory rebuild`

**Phase B pending:** Medium-confidence accumulation trigger

---

## V4 — Predictive Memory

V4 rethinks memory as a **generative model** rather than stored text. Hermem predicts what the user needs, then activates only when the prediction is violated — the error signal drives learning.

### V4.1 — Error Annotation

After each session, annotate with falsifiable predictions that were violated:
- `prediction_errors[]`: violated predictions
- `surprise_level`: how unexpected this session was
- `confidence`: per-error certainty (0–1)
- `overall_quality_score`: session-level prediction quality (0–1)

### V4.2 — Conditioned Dispositions

`(condition, prediction, confidence, error_history)` replacing flat L1 facts:
- `condition_text`: when does this pattern activate?
- `prediction_text`: what does the user expect?
- `error_count` / `success_count`: tracks prediction accuracy over time
- `disposition_decay`: time × frequency joint decay (7-day half-life)

### V4.3 — Error-Activated Retrieval

Completes the error-driven learning loop. **End-to-end annotation pipeline verified (2026-05-22).**

**13 Intent Classes:**

| Intent | Description | Action |
|--------|-------------|--------|
| 学习/Study | Wants to learn a concept | Trigger recall |
| 执行/Execute | Clear task instruction | Execute directly |
| 修正/Correct | Corrects Hermem | Update disposition |
| 结束/Close | Phase completion | Update summary |
| 反馈/Feedback | Provides opinion/evaluation | Trigger lightweight annotation |
| 确认/Confirm | Confirms/approves | Route to execution |
| 建议/Suggest | Proposes a suggestion | Record as preference |
| 记忆/Memory | Stores/retrieves memory | Call Hermem |
| 修改/Modify | Modifies/edits content | Execute modification |
| 停止/Stop | Stops current operation | Interrupt flow |
| 提问/Ask | Asks a question | Answer directly |
| 咨询/Consult | Seeks advice | Generate suggestion |
| 评估/Evaluate | Judges/evaluates | Provide analysis |

**8 Trigger Conditions:**

| Trigger | Type | Status |
|---------|------|--------|
| A1 User explicit negation | strong | ✅ |
| A2 User partial correction | strong | ✅ |
| B1 Agent self-correction | strong | ✅ |
| B2 Agent expresses uncertainty | medium | ✅ |
| B3 Agent gives up | strong | ✅ |
| C1 LLM error | — | ⚠️ Awaiting gateway integration |
| C2 Tool error | — | ⚠️ Awaiting gateway integration |
| C3 Session-end fallback | — | ✅ Active |

**Daily Loop:**
- 02:00 — Self-Journal: reads all L0 sessions, writes patterns/errors/solutions
- 06:00 — Synthesis: compresses learnings into active memory

**Completed:** B1, B2, B4, B5, B6, B8, B9, C3
**Pending:** B3 (dynamic threshold), C1/C2 (gateway hooks)

### V4.5 — Disposition-Aware Rerank (2026-05-22)

`disposition_aware_rerank()` boosts L1 facts sharing context with top dispositions — dispositions don't just accumulate error_count, they actively rerank what Hermem retrieves.

**Boost paths:**
1. `l0_ref` exact match — disposition and fact from the same session
2. Condition keyword → fact content overlap ≥ 2 hits (UUID-format disposition fallback)

### V4.4 — Concurrency Fixes (2026-05-21)

| Phase | Feature | Status |
|-------|---------|--------|
| P0 | `append_vectors()` double-lock: `threading.Lock` + `fcntl.flock` | ✅ |
| P1 | `hermem_auto_index_all.py` file lock | ✅ |
| P2 | `watchdog_vectorstore.py`: drift detection + `--fix` | ✅ |

---

## Quick Start

### Hermem Users (5-minute install)

```bash
# 1. Clone Hermem
git clone https://github.com/oxdh9019/hermem.git ~/hermem

# 2. Run installer (auto-configures plugin directory + impl symlink)
cd ~/hermem && ./install.sh

# 3. Initialize vector store (first time only, ~5 min)
python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py

# 4. Configure Hermes to use Hermem
# Add to ~/.hermes/config.yaml:
#   memory:
#     provider: hermem

# 5. Restart Hermes
hermes restart
```

Full guide: [QUICKSTART.md](QUICKSTART.md) · Troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

### Developers (self-host)

```bash
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# Initialize L1/L2/L3 tables
python3 phase3/impl/db_init.py

# Run daily pipeline (journal 02:00 + synthesis 06:00)
python3 phase3/cron_daily.py
```

---

## Requirements

- Ollama (`localhost:11434`) — bge-m3 for embeddings
- MiniMax API key (`MINIMAX_CN_API_KEY` in `~/.hermes/.env`) — for error annotation + LLM calls
- SQLite 3 (Python stdlib)

---

## Changelog

### 2026-05-27 — V5.1 Engineering Fixes

- **drift=91 fixed**: meta and npy fully aligned (1711 vectors, 1645 chunks, 0 orphans)
- **`hermes memory health`**: CLI check for embedding model, vector drift, chunk count, V5 config, ollama daemon
- **`hermes memory rebuild`**: Idempotent CLI to repair drift and fill missing embeddings
- **Embedding automation audit**: All `insert_chunk` call sites verified — no gaps found, no new embedding automation needed

### 2026-05-27 — V5 Active Retrieval + Public Beta

- **Phase A complete**: bge-m3 vector search + tiered thresholds + injection + session dedup
- HIGH threshold: 0.85 → 0.70 (实测最高 0.77)
- **Public beta release kit**: `install.sh` + `QUICKSTART.md` + `TROUBLESHOOTING.md` + `requirements.txt` + `templates/__init__.py`

### 2026-05-23 — V4.5 Patch (15 Fixes)

### 2026-05-22 — V4.3.1 Patch

---

## Outstanding Issues

| Issue | Notes | Revisit After |
|-------|-------|---------------|
| **B3 is_recurring_cross_session** | Dynamic threshold not implemented. V4.4 Plan B satisfaction check bypasses it but doesn't close the path. Currently relying on hardcoded threshold. | More satisfaction check data |
| **V4.5 keyword threshold tuning** | `MIN_HITS=2` is conservative. After 1-2 weeks of boost log data, tighten to `max(2, ceil(n_keywords*0.4))`. | Boost log analysis |

---

## Feature Status

| Feature | Status |
|---------|--------|
| Phase 1/2 skill layer | ✅ |
| Phase 3 plugin | ✅ HermemMemoryProvider registered in Hermes config |
| V4.1 Error Annotation | ✅ MiniMax-M2.7 async queue |
| V4.2 Conditioned Dispositions | ✅ l1_dispositions table + extract/vector_search/three-tier detection |
| V4.3 Error-Activated Retrieval | ✅ Beta — B1/B2/B4/B5/B6/B8/B9/C3 complete |
| V4.4 Concurrency Fixes | ✅ P0/P1/P2 complete |
| **V5 Active Retrieval** | ✅ Phase A — vector search, injection, dedup done. `hermes memory health` + `rebuild` CLI. Phase B pending. |
| Intent Classifier | ✅ 13 intents + 2-layer architecture |
| Daily Journal + Synthesis Loop | ✅ Cron 02:00 / 06:00 |
| C1/C2 gateway hooks | ⚠️ C3 (session-end) active. C1/C2 defined but awaiting Hermes gateway integration. Non-blocking for V5 active retrieval. |
| Unit tests | ⚠️ 116 passed, 3 failed (intent_classifier trigger edge cases — pre-existing, unrelated to V5). Not blocking for beta. |
| CI/CD | ❌ None |

---

## Design Principles

- **Minimal dependencies**: Pure Python + SQLite, no heavy runtimes
- **Plain text storage**: All memories in readable Markdown, auditable and editable
- **Progressive disclosure**: Load only relevant memory to avoid context overflow
- **Self-auditing**: git log, journal, annotations all public

## License

MIT
