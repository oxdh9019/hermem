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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests

# ── Paths ────────────────────────────────────────────────────────────────────
# Hardcoded — avoids import issues with phase3 impl path
HERMES_HOME = Path.home() / ".hermes"
NPY_PATH = HERMES_HOME / "memory" / "hermem_vectors.npy"
META_PATH = HERMES_HOME / "memory" / "hermem_meta.json"
DB_PATH = HERMES_HOME / "memory" / "hermem.db"
LOCK_FILE = HERMES_HOME / "memory" / ".vector_lock"

BATCH_SIZE = 50  # rows per DB UPDATE
CONCURRENCY = 15  # parallel Ollama calls
OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "bge-m3:latest"  # same as Hermem's utils.py
EMBED_DIM = 1024  # bge-m3 output dim (matches existing npy)

# ── Helpers ───────────────────────────────────────────────────────────────────

import fcntl


class FileLock:
    """进程间文件锁（fcntl.flock），支持 with 语句。"""

    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "w")
        deadline = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > deadline:
                    self._fd.close()
                    raise RuntimeError(f"Could not acquire lock {self.path}") from None
                time.sleep(0.05)

    def __exit__(self, *args):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


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
    # 临时表替代 f-string CASE WHEN（防止 SQL 注入）
    c.execute("CREATE TEMP TABLE IF NOT EXISTS _idx_map (chunk_id INTEGER, new_idx INTEGER)")
    c.execute("DELETE FROM _idx_map")
    c.executemany("INSERT INTO _idx_map VALUES (?, ?)", list(zip(ids, new_indices, strict=False)))
    c.execute("""
        UPDATE chunks
        SET vec_index = (SELECT new_idx FROM _idx_map WHERE chunk_id = chunks.id)
        WHERE id IN (SELECT chunk_id FROM _idx_map)
    """)
    c.execute("DELETE FROM _idx_map")
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
    c.execute(
        """
        SELECT chunk_id, content, vec_index
        FROM chunks
        WHERE vec_index >= ?
        ORDER BY vec_index
    """,
        (next_idx,),
    )
    oob_chunks = c.fetchall()
    conn.close()
    return null_chunks + oob_chunks


def get_orphan_chunks_v2() -> list[tuple]:
    """Return [(id, content), ...] for all chunks needing re-vectorization."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    meta = load_meta()
    next_idx = meta.get("next_index", 706)

    c.execute(
        """
        SELECT id, content
        FROM chunks
        WHERE vec_index IS NULL
           OR vec_index = -1
           OR vec_index >= ?
        ORDER BY vec_index NULLS FIRST, id
    """,
        (next_idx,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────


def check_drift(meta: dict, current_npy_rows: int) -> bool:
    """
    Preflight: compare meta next_index vs actual npy rows.
    Returns True if drift detected (mismatch).
    Prints diagnostic info.
    """
    next_idx = meta.get("next_index", 706)
    drift = next_idx != current_npy_rows
    print(f"  npy rows : {current_npy_rows}")
    print(f"  next_idx : {next_idx}")
    print(f"  drift?   : {'⚠ YES — next_index ahead of npy rows' if drift else 'none'}")
    return drift


def main():
    t0 = time.time()

    print("=" * 60)
    print("Hermem Vector Backfill Script")
    print("=" * 60)

    # ── Preflight: quick drift check ─────────────────────────────────────────
    meta = load_meta()
    current_npy_rows = get_current_npy_shape()[0]
    next_idx = meta.get("next_index", 706)

    print("\n[PREFLIGHT] Checking meta vs npy consistency...")
    drift = check_drift(meta, current_npy_rows)
    if drift:
        print("\n  ⚠ DRIFT DETECTED:")
        print(f"     meta.next_index={next_idx} but npy has only {current_npy_rows} rows.")
        print(
            f"     Gap of {next_idx - current_npy_rows} rows. Backfill may write to wrong positions."
        )
        print(f"     Recommend: fix meta.next_index = {current_npy_rows} before proceeding.")
        print(f"\n     To auto-fix: set next_index = {current_npy_rows} and re-run.\n")

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
    c.execute(
        """
        SELECT chunk_type, COUNT(*)
        FROM chunks
        WHERE vec_index IS NULL
           OR vec_index = -1
           OR vec_index >= ?
        GROUP BY chunk_type
    """,
        (next_idx,),
    )
    for r in c.fetchall():
        print(f"  {r[0]}: {r[1]}")
    conn.close()

    # ── Phase 1: Generate embeddings ─────────────────────────────────────────
    print(f"\n[1/3] Generating embeddings ({CONCURRENCY} workers)...")
    texts = [row[1] for row in orphans]
    ids = [row[0] for row in orphans]

    embeddings: dict[int, np.ndarray] = {}

    def genEmbedding(text: str, idx: int):
        try:
            vec = embed_texts([text])[0]
            return idx, vec
        except Exception as e:
            print(f"  [WARN] embedding error for {idx}: {e}")
            return idx, np.zeros(EMBED_DIM, dtype=np.float32)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = {ex.submit(genEmbedding, t, i): i for i, t in enumerate(texts)}
        done = 0
        for fut in as_completed(futures):
            idx, vec = fut.result()
            embeddings[idx] = vec
            done += 1
            if done % 100 == 0 or done == total:
                print(f"  ... {done}/{total}")

    print(f"  Done in {time.time() - t0:.1f}s")

    # ── Phase 2: Append to npy ───────────────────────────────────────────────
    print(f"\n[2/3] Appending {total} vectors to npy...")

    # ── Lock: verify npy hasn't changed since we read it ──────────────────────
    print("  [LOCK] Acquiring file lock to verify npy state...")
    with FileLock(LOCK_FILE, timeout=10.0):
        npy_rows_now = get_current_npy_shape()[0]
        if npy_rows_now != current_npy_rows:
            raise RuntimeError(
                f"NPY CHANGED while generating embeddings! Was {current_npy_rows}, now {npy_rows_now}. "
                f"Another process wrote to npy. Aborting to avoid off-by-one drift."
            )
        print(f"  [LOCK] npy rows unchanged ({npy_rows_now}), safe to write.")

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

    # ── Lock: verify orphans still need backfill ──────────────────────────────
    print("  [LOCK] Re-checking orphan status before DB write...")
    with FileLock(LOCK_FILE, timeout=10.0):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM chunks WHERE vec_index IS NULL OR vec_index = -1 OR vec_index >= ?",
            (next_idx,),
        )
        stale_count = c.fetchone()[0]
        conn.close()
        if stale_count != total:
            raise RuntimeError(
                f"Orphan count changed while generating! Expected {total}, now {stale_count}. "
                f"Another process may have backfilled. DB update aborted to prevent double-assign."
            )
        print(f"  [LOCK] orphan count unchanged ({stale_count}), safe to update DB.")

        new_indices = list(range(next_idx, next_idx + total))
        ids_ordered = [ids[i] for i in range(total)]

        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            batch_ids = ids_ordered[batch_start:batch_end]
            batch_indices = new_indices[batch_start:batch_end]
            update_db_batch(batch_ids, batch_indices)
            print(f"  batch {batch_start}-{batch_end} ({len(batch_ids)} rows)")

        # ── Update meta (still under lock to keep meta + npy + DB in sync) ────
        meta["next_index"] = next_idx + total
        save_meta(meta)
        print(f"\n  [LOCK] Meta updated: next_index = {meta['next_index']}")

    # ── Verify ──────────────────────────────────────────────────────────────
    print("\n[VERIFY] Checking consistency...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM chunks WHERE vec_index IS NULL OR vec_index = -1")
    null_count = c.fetchone()[0]
    print(f"  NULL/-1 vec_index remaining: {null_count}")

    c.execute("SELECT COUNT(*) FROM chunks WHERE vec_index >= ?", (next_idx + total,))
    oob_still = c.fetchone()[0]
    print(f"  vec_index >= {next_idx + total} (stale OOB): {oob_still}")

    c.execute(f"""
        SELECT COUNT(*) FROM chunks
        WHERE vec_index >= 0 AND vec_index < {next_idx + total}
    """)
    valid_count = c.fetchone()[0]
    print(f"  vec_index in valid range [0, {next_idx + total - 1}]: {valid_count}")

    print(f"\n  npy rows : {new_npy_rows}")
    print(f"  valid DB : {valid_count}")
    print(f"  match?    : {'✓ YES' if valid_count == new_npy_rows else '✗ MISMATCH'}")

    conn.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s ({total / elapsed:.1f} vectors/sec)")
    print("=" * 60)


if __name__ == "__main__":
    main()
