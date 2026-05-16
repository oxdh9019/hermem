# Hermem

Hermes lightweight memory enhancement system — session summarization, concept tagging, and semantic recall for AI assistants.

## Overview

Hermem extends Hermes Agent with a layered memory architecture:

- **Phase 1** — Session auto-summarization + session warmup on startup
- **Phase 2** — Concept tagging + semantic search (beyond keyword matching)
- **Phase 3** — L0–L3 hierarchical memory with fact extraction, scene aggregation, and user profile staging

## Architecture

```
Session ends
    ↓
L0: Raw session JSON archived (~500MB quota)
    ↓ extract (LLM)
L1: Atomic facts (type/content/tags/value + vector)
    ↓ aggregate (embedding similarity ≥ 0.75)
L2: Scene clusters (topic groups with occurrence counts)
    ↓ stage (preference-type facts)
L3: Staging area → User profile confirmation
    ↓ confirm
user_profile.md (persistent confirmed preferences)
```

## Requirements

- Python 3.10+
- Ollama (local LLM + embedding endpoint at `localhost:11434`)
- SQLite 3 with FTS5 enabled (built into Python stdlib)

## Setup

```bash
# Clone
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# Initialize database
python phase3/impl/db_init.py

# Run daily cron (optional)
python phase3/cron_daily.py
```

## Usage

```bash
# Summarize a session
python -m phase3.impl.l0_store /path/to/session.json

# Extract facts from summaries
python -m phase3.impl.batch_extract

# Search memory
python -m impl.retrieval "上次处理数据库问题的方法"
```

## Project Structure

```
hermem/
├── PROJECT.md              # Project overview and phase plan
├── phase1/                 # Session summarization + warmup
│   ├── SPEC.md
│   └── REVIEW.md
├── phase2/                 # Concept tagging + semantic search
│   ├── SPEC.md
│   └── REVIEW.md
├── phase3/                 # L0–L3 hierarchical memory
│   ├── SPEC.md
│   ├── TODO.md
│   ├── cron_daily.py       # Daily processing script
│   └── impl/
│       ├── db_init.py
│       ├── l0_store.py
│       ├── l0_load.py
│       ├── l1_extract.py
│       ├── l1_search.py
│       ├── l2_aggregate.py
│       └── l3_staging.py
└── impl/                   # Legacy Phase 1/2 implementation
    ├── database.py
    ├── embedding.py
    ├── vectorstore.py
    ├── retrieval.py
    └── batch_backfill.py
```

## Design Principles

- **Minimal dependencies**: Pure Python + SQLite FTS5, no heavy runtimes
- **Plain text storage**: All memories in readable Markdown, auditable and editable
- **Progressive disclosure**: Load only relevant memory to avoid context overflow
- **Skill-only delivery**: No core Hermes code modifications required

## License

MIT