#!/usr/bin/env python3
"""
Backfill orphaned vectors into hermem_vectors.npy.

Problem: concurrent writes caused np.vstack to overwrite rows,
         leaving npy with only 706 rows but meta.next_index=1032.
         688 chunks have vec_index >= 706 or NULL/-1.

Solution: re-vectorize all 688 orphans, append to npy, update DB.
"""

import json
import sqlite3
import numpy as np
import requests
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue

# ── Paths ────────────────────────────────────────────────────────────────────
# Hardcoded — avoids import issues with phase3 impl path
HERMES_HOME  = Path.home() / ".hermes"
NPY_PATH     = HERMES_HOME / "memory" / "hermem_vectors.npy"
META_PATH    = HERMES_HOME / "memory" / "hermem_meta.json"
DB_PATH      = HERMES_HOME / "memory" / "hermem.db"
LOCK_FILE    = HERMES_HOME / "memory" / ".vector_lock"

BATCH_SIZE   = 50      # rows per DB UPDATE
CONCURRENCY  = 15      # parallel Ollama calls
OLLAMA_BASE  = "http://localhost:11434"
EMBED_MODEL  = "bge-m3:latest"        # same as Hermem's utils.py
EMBED_DIM    = 1024                    # bge-m3 output dim (matches existing npy)

# ── Helpers ───────────────────────────────────────────────────────────────────

def acquire_lock(path: Path, timeout: float = 5.0) -> threading.Lock:
    """Simple file-based lock via exclusive open."""
    lock = threading.Lock()
    start = time.time()
    while (time.time() - start) < timeout:
        try:
            open(path, "x").close()
            path.unlink()
            return lock
        except FileExistsError:
            time.sleep(0.05)
    raise RuntimeError(f"Could not acquire lock {path}")


def load_meta() -> dict:
    with open(META_PATH) as f:
        return json.load(f)


def save_meta(meta: dict):
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)


def get_current_npy_shape() -> tuple:
    vecs = np.load(NPY_PATH)
    return vecs.shape


def embed_texts(texts: list[str]) -> np.ndarray:
    """Call Ollama embedding API. Falls back to /api/embeddings."""
    url = f"{OLLAMA_BASE}/api/embeddings"
    results = []
    for text in texts:
        try:
            r = requests.post(url, json={"model": EMBED_MODEL, "prompt": text}, timeout=10)
            r.raise_for_status()
            results.append(r.json()["embedding"])
        except Exception as e:
            print(f"  [WARN] embed failed for text len={len(text)}: {e}")
            results.append([0.0] * EMBED_DIM)
    return np.array(results, dtype=np.float32)


def update_db_batch(ids: list[int], new_indices: list[int]):
    """Batch update vec_index for a list of chunks (by id)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cases = " ".join(f"WHEN id = {cid} THEN {idx}" for cid, idx in zip(ids, new_indices))
    sql = f"""
        UPDATE chunks
        SET vec_index = CASE {cases} END
        WHERE id IN ({','.join('?'*len(ids))})
    """
    c.execute(sql, ids)
    conn.commit()
    conn.close()


def get_orphan_chunks() -> list[tuple]:
    """Return [(chunk_id, content, current_vec_index), ...] for all orphans."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT chunk_id, content, vec_index
        FROM chunks
        WHERE vec_index IS NULL
           OR vec_index = -1
           OR vec_index >= (
                SELECT value::int FROM kv WHERE key = 'next_index'
           )
        ORDER BY vec_index NULLS FIRST
    """)
    # vec_index >= subquery won't work well with NULLS FIRST, handle separately
    conn.close()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Get NULL / -1
    c.execute("""
        SELECT chunk_id, content, vec_index
        FROM chunks
        WHERE vec_index IS NULL OR vec_index = -1
        ORDER BY chunk_id
    """)
    null_chunks = c.fetchall()

    # Get OOB (using Python side after loading meta)
    meta = load_meta()
    next_idx = meta.get("next_index", 706)
    c.execute("""
        SELECT chunk_id, content, vec_index
        FROM chunks
        WHERE vec_index >= ?
        ORDER BY vec_index
    """, (next_idx,))
    oob_chunks = c.fetchall()
    conn.close()
    return null_chunks + oob_chunks


def get_orphan_chunks_v2() -> list[tuple]:
    """Return [(id, content), ...] for all chunks needing re-vectorization."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    meta = load_meta()
    next_idx = meta.get("next_index", 706)

    c.execute("""
        SELECT id, content
        FROM chunks
        WHERE vec_index IS NULL
           OR vec_index = -1
           OR vec_index >= ?
        ORDER BY vec_index NULLS FIRST, id
    """, (next_idx,))
    rows = c.fetchall()
    conn.close()
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()

    print("=" * 60)
    print("Hermem Vector Backfill Script")
    print("=" * 60)

    # Load current state
    meta = load_meta()
    current_npy_rows = get_current_npy_shape()[0]
    next_idx = meta.get("next_index", 706)

    print(f"Current npy rows : {current_npy_rows}")
    print(f"meta next_index  : {next_idx}")
    print(f"Will append from : {next_idx}")

    # Get orphans
    orphans = get_orphan_chunks_v2()
    total = len(orphans)
    print(f"Chunks to backfill: {total}")
    if total == 0:
        print("Nothing to do.")
        return

    # Breakdown by type
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT chunk_type, COUNT(*)
        FROM chunks
        WHERE vec_index IS NULL
           OR vec_index = -1
           OR vec_index >= ?
        GROUP BY chunk_type
    """, (next_idx,))
    for r in c.fetchall():
        print(f"  {r[0]}: {r[1]}")
    conn.close()

    # ── Phase 1: Generate embeddings ─────────────────────────────────────────
    print(f"\n[1/3] Generating embeddings ({CONCURRENCY} workers)...")
    texts = [row[1] for row in orphans]
    ids   = [row[0] for row in orphans]

    embeddings: dict[int, np.ndarray] = {}

    def genEmbedding(text: str, idx: int):
        try:
            vec = embed_texts([text])[0]
            return idx, vec
        except Exception as e:
            print(f"  [WARN] embedding error for {idx}: {e}")
            return idx, np.zeros(EMBED_DIM, dtype=np.float32)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = {ex.submit(genEmbedding, t, i): i
                   for i, t in enumerate(texts)}
        done = 0
        for fut in as_completed(futures):
            idx, vec = fut.result()
            embeddings[idx] = vec
            done += 1
            if done % 100 == 0 or done == total:
                print(f"  ... {done}/{total}")

    print(f"  Done in {time.time()-t0:.1f}s")

    # ── Phase 2: Append to npy ───────────────────────────────────────────────
    print(f"\n[2/3] Appending {total} vectors to npy...")

    # Build stacked array in order
    new_vectors = np.stack([embeddings[i] for i in range(total)], axis=0)
    print(f"  new_vectors shape: {new_vectors.shape}")

    # Append
    vecs = np.load(NPY_PATH)
    new_vecs = np.vstack([vecs, new_vectors])
    np.save(NPY_PATH, new_vecs)
    new_npy_rows = new_vecs.shape[0]
    print(f"  npy rows: {current_npy_rows} -> {new_npy_rows}")

    # ── Phase 3: Update DB ───────────────────────────────────────────────────
    print(f"\n[3/3] Updating DB vec_index in batches of {BATCH_SIZE}...")

    new_indices = list(range(next_idx, next_idx + total))
    ids_ordered = [ids[i] for i in range(total)]

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_ids    = ids_ordered[batch_start:batch_end]
        batch_indices = new_indices[batch_start:batch_end]
        update_db_batch(batch_ids, batch_indices)
        print(f"  batch {batch_start}-{batch_end} ({len(batch_ids)} rows)")

    # ── Update meta ──────────────────────────────────────────────────────────
    meta["next_index"] = next_idx + total
    save_meta(meta)
    print(f"\nMeta updated: next_index = {meta['next_index']}")

    # ── Verify ──────────────────────────────────────────────────────────────
    print("\n[VERIFY] Checking consistency...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM chunks WHERE vec_index IS NULL OR vec_index = -1")
    null_count = c.fetchone()[0]
    print(f"  NULL/-1 vec_index remaining: {null_count}")

    c.execute(f"SELECT COUNT(*) FROM chunks WHERE vec_index >= {next_idx + total}")
    oob_still = c.fetchone()[0]
    print(f"  vec_index >= {next_idx + total} (stale OOB): {oob_still}")

    c.execute(f"""
        SELECT COUNT(*) FROM chunks
        WHERE vec_index >= 0 AND vec_index < {next_idx + total}
    """)
    valid_count = c.fetchone()[0]
    print(f"  vec_index in valid range [0, {next_idx + total - 1}]: {valid_count}")

    total_chunks = new_npy_rows  # every npy row corresponds to a chunk
    print(f"\n  npy rows : {new_npy_rows}")
    print(f"  valid DB : {valid_count}")
    print(f"  match?    : {'✓ YES' if valid_count == new_npy_rows else '✗ MISMATCH'}")

    conn.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s ({total/elapsed:.1f} vectors/sec)")
    print("=" * 60)


if __name__ == "__main__":
    main()
