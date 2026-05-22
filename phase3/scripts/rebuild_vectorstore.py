#!/usr/bin/env python3
"""
rebuild_vectorstore.py — Hermem 向量存储完整重建脚本

功能：
- 从 hermem.db 的 chunks 表重建 hermem_vectors.npy
- 当 drift 无法用 truncate 修复时（双向 drift：meta < npy），执行完整重建
- 输出 JSON 格式报告供 cron 捕获

用法：
  python3 rebuild_vectorstore.py [--verify-only] [--output PATH]

验证模式（--verify-only）：
  仅检查 chunks.vec_index 与向量矩阵的一致性，不做任何修改
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

HERMEM_DIR = Path.home() / ".hermes"
MEM_DIR = HERMEM_DIR / "memory"
VEC_PATH = MEM_DIR / "hermem_vectors.npy"
META_PATH = MEM_DIR / "hermem_meta.json"
DB_PATH = MEM_DIR / "hermem.db"


def load_meta():
    with open(META_PATH) as f:
        return json.load(f)


def load_vectors():
    return np.load(str(VEC_PATH))


def verify_only():
    """验证 chunks.vec_index 与向量矩阵的一致性。"""
    meta = load_meta()
    vecs = load_vectors()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, vec_index FROM chunks WHERE vec_index IS NOT NULL ORDER BY vec_index"
    ).fetchall()
    conn.close()

    issues = []
    vec_count = vecs.shape[0]
    max_vec_index = vec_count - 1

    for chunk_id, vec_index in rows:
        if vec_index < 0:
            issues.append(f"  chunk_id={chunk_id}: vec_index={vec_index} < 0")
        elif vec_index > max_vec_index:
            issues.append(f"  chunk_id={chunk_id}: vec_index={vec_index} > max({max_vec_index})")

    # 检查 meta.next_index 是否合理
    expected_next = len(rows)
    actual_next = meta.get("next_index", 0)

    print(f"[verify] chunks with vec_index: {len(rows)}")
    print(f"[verify] vectors shape: {vecs.shape}")
    print(f"[verify] meta.next_index: {actual_next}, expected from rows: {expected_next}")

    if issues:
        for issue in issues:
            print(f"[ISSUE] {issue}")
        print(f"[FAIL] {len(issues)} issues found")
        return False, issues
    else:
        print(f"[OK] All {len(rows)} chunks have valid vec_index")
        return True, []


def rebuild():
    """从 chunks 表重建向量矩阵（compact + remap）。

    处理逻辑：
    1. 收集所有被引用的 vec_index
    2. 压缩向量矩阵（去除孤儿向量）
    3. 重映射所有 chunk.vec_index（old → new）
    4. 更新 meta.next_index

    适用场景：
    - npy 有孤儿向量（被写入但无 chunk 引用）
    - chunk vec_index 超界（truncate 无法修复）
    """
    print("[rebuild] Starting compact rebuild...")

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, vec_index FROM chunks WHERE vec_index IS NOT NULL ORDER BY vec_index"
    ).fetchall()
    conn.close()

    if not rows:
        print("[rebuild] No chunks with vec_index")
        return True

    referenced = sorted({r[1] for r in rows})
    n_referenced = len(referenced)
    max_ref = max(referenced)
    print(f"[rebuild] Referenced indices: {n_referenced}, max={max_ref}")

    # 加载当前向量
    vecs = load_vectors()
    meta = load_meta()
    npy_rows = vecs.shape[0]
    meta_next = meta.get("next_index", 0)
    print(f"[rebuild] Current: npy={npy_rows}, meta.next_index={meta_next}")

    orphans_mid = sum(1 for i in range(max_ref + 1) if i not in set(referenced))
    orphans_tail = max(0, npy_rows - max_ref - 1)
    print(f"[rebuild] Mid-orphans (in-range, no chunk): {orphans_mid}")
    print(f"[rebuild] Tail-orphans (index >= max+1):    {orphans_tail}")

    if orphans_mid == 0 and orphans_tail == 0:
        print("[rebuild] No orphans, no rebuild needed")
        return True

    # Compact: 提取所有被引用的向量
    compact_vecs = vecs[referenced]
    new_meta_next = n_referenced
    print(f"[rebuild] Compacted: {compact_vecs.shape} → meta.next_index={new_meta_next}")

    # 构建 old→new 映射，更新 chunks 表
    old_to_new = {old: new for new, old in enumerate(referenced)}
    conn2 = sqlite3.connect(DB_PATH)
    updated = 0
    for chunk_id, old_idx in rows:
        new_idx = old_to_new[old_idx]
        if old_idx != new_idx:
            conn2.execute("UPDATE chunks SET vec_index = ? WHERE id = ?", (new_idx, chunk_id))
            updated += 1
    conn2.commit()
    conn2.close()
    print(f"[rebuild] Updated {updated} chunk vec_indices (remapped from old→new)")

    # 保存压缩后的向量
    tmp = Path("/tmp/_rebuild_vec.npy")
    np.save(str(tmp), compact_vecs)
    import shutil

    shutil.copy2(str(tmp), str(VEC_PATH))
    tmp.unlink(missing_ok=True)

    # 更新 meta
    new_meta = {**meta, "next_index": new_meta_next}
    with open(META_PATH, "w") as f:
        json.dump(new_meta, f)
    print(f"[rebuild] Wrote {compact_vecs.shape[0]} vectors, meta.next_index={new_meta_next}")

    # 验证
    ok, issues = verify_only()
    print(f"[rebuild] {'✅ OK' if ok else f'❌ FAIL ({len(issues)} issues)'}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Hermem vectorstore rebuild/verify")
    parser.add_argument("--verify-only", action="store_true", help="仅验证，不做任何修改")
    args = parser.parse_args()

    print(f"[rebuild] Vector store: {VEC_PATH}")
    print(f"[rebuild] Meta: {META_PATH}")
    print(f"[rebuild] DB: {DB_PATH}")
    print("---")

    if args.verify_only:
        ok, issues = verify_only()
        print(f"[result] {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    else:
        ok, issues = rebuild()
        print(f"[result] {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
