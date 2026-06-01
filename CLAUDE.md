# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hermem is a lightweight memory enhancement system for Hermes Agent, providing L0–L3 hierarchical memory with Predictive Coding (V4) and Active Retrieval (V5).

- **Current versions**: V5.5 v1.0 (2026-05-28, audit-clean 2026-06-01)
- **Active implementation**: `phase3/impl/` — all V1–V5 code lives here
- **V5.5 modules**: `phase3/v5.5/impl/` (L4 reflection, conflict resolver, active forgetting, llm_helper)
- **Hermes Agent plugin / bridge**: lives in a separate repo (`NousResearch/hermes-agent`, at `plugins/memory/hermem/`) — see [Bridge / Plugin Architecture](#bridge--plugin-architecture) below
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
│   │   ├── llm_helper.py  # LLM routing (reads LLM_PRIMARY_MODEL/LLM_FALLBACK_MODEL from impl.config)
│   │   ├── l4_reflection.py # L4 synthesis from prediction_errors, 14-day TTL (refreshed weekly)
│   │   ├── conflict_resolver.py # detect_conflicts + resolve_conflict_with_action
│   │   └── active_forgetting.py # sleep consolidation (→ user_profile_auto.md) + active demotion
│   ├── cron/
│   │   ├── cron_weekly_synthesis.py # L4 + consolidation + demotion + TTL refresh
│   │   ├── com.hermes.weekly-memory-synthesis.plist # launchd job (Sunday 02:30)
│   │   ├── run_weekly_synthesis.sh # wrapper invoked by launchd
│   │   └── install_weekly_cron.sh # install/uninstall/run verbs
│   ├── tests/            # V5.5-specific tests (collected by root pytest)
│   └── migrate_v55.py    # DB migration for l4_reflections, pending_conflicts, usage columns
└── plugins/memory/hermem/ # Symlinked wrapper (read-only mirror, see Bridge section below)
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
# Weekly synthesis (Sunday 02:30) — L4 + consolidation + demotion + TTL refresh
# Registered as launchd (not hermes cron) for reliability:
bash phase3/v5.5/cron/install_weekly_cron.sh install
bash phase3/v5.5/cron/install_weekly_cron.sh run      # manual trigger
launchctl list | grep hermes.weekly-memory-synthesis   # inspect
bash phase3/v5.5/cron/install_weekly_cron.sh uninstall
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

## Bridge / Plugin Architecture

Hermem is a **memory provider plugin** that plugs into Hermes Agent. The implementation in this repo (`oxdh9019/hermem`) is separate from the bridge code that actually registers the plugin with the agent.

### Where the bridge code lives

| Path | Role | Repo |
|------|------|------|
| `~/.hermes/projects/hermem/phase3/` | **Implementation** (this repo) | `oxdh9019/hermem` |
| `~/.hermes/hermes-agent/plugins/memory/hermem/` | **Bridge / plugin entry** | `NousResearch/hermes-agent` |

The bridge is a thin `HermemMemoryProvider` class that:

- Implements the `agent.memory_provider.MemoryProvider` ABC
- Discovers the impl via `_ensure_impl()` with a 3-tier fallback:
  1. `./impl/` symlink next to `__init__.py` (canonical install)
  2. `~/.hermes/projects/hermem/phase3` (the path used on this machine)
  3. `~/.hermes/projects/hermem-github/phase3` (defensive dead branch — does not exist on this box, silently no-ops)
- Exposes four tool schemas: `hermem_search`, `hermem_add`, `hermem_forget`, `hermem_stats`, plus `hermem_resolve_conflict` (added 2026-06-01)
- Runs background threads: prefetch, V4.3 feedback consumer, V5 active retrieval, V5.5 async conflict detection

### Editing the bridge

The bridge source of truth is `NousResearch/hermes-agent`. On this machine the working tree lives at `~/.hermes/hermes-agent/`, but **changes are NOT pushed upstream from this checkout** — the bridge here is a local fork. Edit, test, and commit locally; the upstream hermes-agent has its own release cadence.

When you change the bridge, the on-disk path of the impl it discovers is `~/.hermes/projects/hermem/phase3` (the `phase3` directory, not `phase3/impl`). The bridge's `_impl_cache` populates `impl.database`, `impl.vectorstore`, `impl.embedding`, `impl.retrieval`, `impl.config` keys. V5.5 modules come through a separate `_v55_import()` helper (importlib bypass — see P1-7 note above about `phase3/v5.5/impl/__init__.py`).

### Path safety (2026-06-01 fix, P0-4)

The bridge previously hardcoded `Path.home() / ".hermes" / ...` in 8 places. All of those have been replaced with module-level constants resolved through `hermes_constants.get_hermes_home()` so the bridge works correctly under profiles (`~/.hermes/profiles/<name>/`) and not just the default install.
