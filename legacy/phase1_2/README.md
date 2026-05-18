# Phase 1/2 Legacy Code

These files are from Hermem's early development (Phase 1 & Phase 2).
They are **no longer used** — Phase 3 / V4 uses `phase3/impl/` instead.

**Why kept here:** For reference and potential future archaeology.

## Files

| File | Description | Status |
|------|-------------|--------|
| `database.py` | Phase 2 DB wrapper (SQLite) | Replaced by `phase3/impl/l0_store.py` + `phase3/impl/db_init.py` |
| `embedding.py` | Ollama embedding wrapper | Replaced by `phase3/impl/utils.py` |
| `vectorstore.py` | Phase 2 vector storage | Not used in Phase 3 |
| `retrieval.py` | Phase 2 semantic search | Replaced by `phase3/impl/l1_search.py` |
| `commands.py` | CLI commands (Phase 2) | Not used |
| `migrate.py` | Migration utilities | Not used |
| `batch_backfill.py` | Phase 2 batch backfill | Not used |
| `__init__.py` | Module exports | N/A |

## Active Code Location

```
phase3/impl/
├── l0_store.py       ← L0 archival (was database.py + save_l0)
├── l1_extract.py      ← L1 extraction
├── l1_search.py       ← Semantic search
├── l2_aggregate.py    ← Scene aggregation
├── l3_staging.py      ← Preference staging
├── async_annotation.py ← V4.1 error annotation (new)
├── config.py          ← Shared config
├── utils.py           ← Ollama client + embeddings
└── db_init.py         ← Schema init
```
