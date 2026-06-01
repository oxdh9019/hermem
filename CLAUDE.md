# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hermem is a lightweight memory enhancement system for Hermes Agent, providing L0–L3 hierarchical memory with Predictive Coding (V4) and Active Retrieval (V5).

- **Current versions**: V5.1 (2026-05-27) with V5.5 (2026-05-28) in progress
- **Active implementation**: `phase3/impl/` — all V1–V5 code lives here
- **Plugin wrapper**: `plugins/memory/hermem/` — symlinked to `phase3/impl/`
- **Requirements**: Ollama (bge-m3:latest) + MiniMax API key + SQLite

## Architecture

```
hermem/
├── phase3/impl/           # ← All active implementation
│   ├── database.py        # hermem.db + l0_l3.db (WAL mode, thread-safe)
│   ├── vectorstore.py      # NumPy vector store (double-lock: threading.Lock + fcntl.flock)
│   ├── vector_search.py   # bge-m3 cosine similarity + tiered thresholds (HIGH≥0.70, MEDIUM≥0.50)
│   ├── embedding.py       # Ollama bge-m3 embeddings, SQLite cached
│   ├── intent_classifier.py # 13-intent classification
│   ├── disposition_updater.py # disposition (condition, prediction, error_count) update logic
│   ├── config.py          # All constants — thresholds, models, paths
│   ├── l0_store.py        # Raw session archive (JSON, ~/.hermes/memory/l0_raw/)
│   ├── l1_extract.py      # Atomic fact extraction
│   ├── l2_aggregate.py    # Scene clustering (SIM_THRESHOLD_JOIN=0.75, MERGE=0.85)
│   ├── l3_staging.py      # Pending confirmation staging area
│   ├── usage_tracker.py   # Async usage_count/last_used_at on retrieve() calls
│   └── db_init.py         # Database schema initialization
├── phase3/scripts/        # Cron-called operational scripts
│   ├── batch_compute_embeddings.py   # Precompute all chunk vectors
│   ├── test_v5_e2e.py     # End-to-end test (7/8 passing)
│   ├── watchdog_vectorstore.py --fix  # Drift detection + repair
│   ├── fix_drift_and_fill_embeddings.py
│   ├── journal.py         # Daily 02:00 — read L0, write patterns/errors
│   └── daily_synthesis.py # Daily 06:00 — compress learnings into active memory
├── phase3/v5.5/          # V5.5: Meta-cognition, conflict, forgetting
│   ├── impl/
│   │   ├── llm_helper.py  # MiniMax-M2.7 primary + qwen2.5:3b fallback
│   │   ├── l4_reflection.py # L4 synthesis from prediction_errors, 14-day TTL
│   │   ├── conflict_resolver.py # detect_conflicts + resolve_conflict_with_action
│   │   └── active_forgetting.py # sleep consolidation + active demotion
│   ├── cron/
│   │   └── cron_weekly_synthesis.py # Combined weekly job (L4 + consolidation + demotion)
│   └── migrate_v55.py    # DB migration for l4_reflections, pending_conflicts, usage columns
└── plugins/memory/hermem/ # Hermes plugin wrapper (symlink → phase3/impl/)
    └── templates/__init__.py # Plugin entry (HermemMemoryProvider)
```

## Key Data

| File/Table | Purpose |
|------------|---------|
| `hermem.db` | chunks, embedding_cache, l4_reflections, pending_conflicts |
| `l0_l3.db` | l1_dispositions, l2_scenes, l3_staging |
| `hermem_embeddings.npy` + `.meta.json` | Vector store (1711 vectors, 1645 chunks) |
| `user_profile.md` | L3 confirmed preferences |

## Commands

### Tests
```bash
# All tests (pythonpath = phase3 configured in pyproject.toml)
pytest

# Single test file
pytest phase3/tests/test_phase2c_pending_recall.py

# Single test
pytest phase3/tests/test_v5_5_unit.py::test_l4_reflection_synthesis
```

### Linting
```bash
ruff check phase3/impl/
```

### Database & Vector Store
```bash
# Initialize both databases
python3 phase3/impl/db_init.py

# Precompute all chunk vectors (~5-10 min, 1637 chunks)
python3 phase3/scripts/batch_compute_embeddings.py

# Fix drift and fill missing embeddings
python3 phase3/scripts/fix_drift_and_fill_embeddings.py

# Drift detection only (no fix)
python3 phase3/scripts/watchdog_vectorstore.py
```

### E2E & Health
```bash
# Run e2e tests
python3 phase3/scripts/test_v5_e2e.py

# CLI health check (V5 config, ollama daemon, vector drift, chunk count)
hermes memory health

# CLI rebuild (idempotent repair)
hermes memory rebuild
```

### Cron
```bash
# Daily journal (02:00) + synthesis (06:00)
python3 phase3/scripts/journal.py && python3 phase3/scripts/daily_synthesis.py

# Weekly synthesis (Sunday 02:30) — L4 + consolidation + demotion
hermes cron create "30 2 * * 0" --name "Weekly Memory Synthesis" \
  --script "phase3/v5.5/cron/cron_weekly_synthesis.py"
```

## Configuration

All tuning constants live in `phase3/impl/config.py`:
- `ACTIVE_RETRIEVAL_HIGH_THRESHOLD = 0.70`
- `ACTIVE_RETRIEVAL_MEDIUM_THRESHOLD = 0.50`
- `ACTIVE_RETRIEVAL_TOP_K = 3`
- `ACTIVE_RETRIEVAL_FREQUENCY = 3` (every N turns)
- `DISPOSITION_HALF_LIFE_DAYS = 7`

Ollama URL: `OLLAMA_URL` env var (default `http://localhost:11434/v1`)

## Important Notes

1. **Plugin symlink**: `plugins/memory/hermem/impl` must point to `phase3/impl/` — if broken, `ModuleNotFoundError: No module named 'impl'` occurs
2. **Python path**: Project uses `sys.path.insert` + chdir pattern — `E402` (module-level import not at top) is intentionally ignored in ruff config
3. **Re-export pattern**: `impl/__init__.py` uses `F401` (imported but unused) intentionally — configured in per-file-ignores
4. **WAL mode**: Both databases use `PRAGMA journal_mode=WAL` for concurrency
5. **V5 active retrieval**: Triggers every `FREQUENCY` turns (default 3), not on every message
