# Hermem

Hermes lightweight memory enhancement system — L0–L3 hierarchical memory for AI assistants.

> **Project status (2026-05-16): Phase 3 is the active deliverable. Phase 1/2 are skill-layer designs with no plugin implementation. See Caveats below.**

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

Current data: **316 L1 facts, 41 L2 scenes, 0 L3** (as of 2026-05-16).

## Requirements

- Python 3.10+
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
| Phase 3 `plugins/memory/hermem/` | ❌ Same — plugin not registered; Hermem works via skill + cron, not as a memory provider |
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