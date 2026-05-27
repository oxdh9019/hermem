#!/usr/bin/env python3
"""
Hermem V5 准备脚本 0b: 修复向量库 drift + 补全缺失 embedding

Drift 原因：meta_next_index=1670 但 npy 只有 1369 行（301 个向量写入 meta 但未写入 npy）

流程：
1. 修复 drift：重置 meta next_index = 实际 npy 行数
2. 找出所有无 vec_index 或 vec_index >= npy 行数的 chunk（孤儿映射）
3. 用 Ollama bge-m3 批量生成 embedding，追加到向量库
4. 验证最终状态

用法: python3 scripts/fix_drift_and_fill_embeddings.py
"""

import json
import sys
from pathlib import Path

script_path = Path(__file__).resolve()
sys.path.insert(0, str(script_path.parent.parent))  # scripts/ → phase3/

import numpy as np
from impl import config
from impl.database import get_db
from impl.embedding import get_embedding_cached
from impl.vectorstore import (
    _load_meta,
    _load_vectors,
    _write_meta,
    append_vectors,
    get_stats,
)


def fix_meta_drift():
    """修复 meta 与 npy 不一致问题。"""
    meta = _load_meta()
    vecs = _load_vectors()
    actual_rows = vecs.shape[0]

    if meta["next_index"] == actual_rows:
        print(f"无 drift，next_index={actual_rows}")
        return actual_rows

    print(f"DRIFT 检测: meta next_index={meta['next_index']}, npy rows={actual_rows}")
    print(f"修复: 重置 meta.next_index = {actual_rows}")

    meta["next_index"] = actual_rows
    _write_meta()
    return actual_rows


def find_orphan_and_unmapped():
    """找出所有需要重新映射的 chunk。"""
    vecs = _load_vectors()
    max_valid_index = vecs.shape[0] - 1

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, vec_index, content FROM chunks WHERE vec_index IS NOT NULL"
        ).fetchall()

        orphan_ids = []
        for (chunk_id, vec_index, _content) in rows:
            if vec_index > max_valid_index:
                orphan_ids.append(chunk_id)

        unmapped_rows = conn.execute(
            "SELECT id, content FROM chunks WHERE vec_index IS NULL"
        ).fetchall()
        unmapped_ids = [r[0] for r in unmapped_rows]

    print(f"孤儿映射（vec_index > {max_valid_index}）: {len(orphan_ids)} 个")
    print(f"未映射（vec_index IS NULL）: {len(unmapped_ids)} 个")
    print(f"共 {len(orphan_ids) + len(unmapped_ids)} 个 chunk 需要处理")

    return orphan_ids + unmapped_ids


def batch_fill_embeddings(chunk_ids: list[int]):
    """用 Ollama bge-m3 批量为指定 chunk 生成 embedding。"""
    if not chunk_ids:
        print("无 chunk 需要生成 embedding")
        return

    with get_db() as conn:
        placeholders = ",".join(["?"] * len(chunk_ids))
        rows = conn.execute(
            f"SELECT id, content FROM chunks WHERE id IN ({placeholders})",
            chunk_ids,
        ).fetchall()

    chunks = [(r[0], r[1]) for r in rows]
    print(f"正在用 Ollama bge-m3 生成 {len(chunks)} 个 embedding（通过 SQLite 缓存）...")

    embeddings = []
    for i, (_chunk_id, content) in enumerate(chunks):
        if i % 10 == 0:
            print(f"  处理进度: {i}/{len(chunks)}")
        emb, src = get_embedding_cached(content[:512])
        embeddings.append(emb)

    print("生成完成，开始追加到向量库...")

    # 追加到向量库
    indices = append_vectors(embeddings)
    print(f"追加 {len(indices)} 个向量，索引: {indices[0]}-{indices[-1]}")

    # 更新数据库
    with get_db() as conn:
        for i, chunk_id in enumerate(chunk_ids):
            conn.execute(
                "UPDATE chunks SET vec_index = ? WHERE id = ?",
                (indices[i], chunk_id),
            )
        conn.commit()

    print(f"已更新 {len(chunk_ids)} 个 chunk 的 vec_index")
    return indices


def main():
    print("=== Hermem V5 准备: 修复 drift + 补全 embedding ===\n")

    # Step 1: 修复 drift
    actual_rows = fix_meta_drift()
    vecs = _load_vectors()
    print(f"向量库实际行数: {vecs.shape[0]}\n")

    # Step 2: 找出需要处理的 chunk
    orphan_and_unmapped = find_orphan_and_unmapped()

    if orphan_and_unmapped:
        print("\n开始批量处理...")
        batch_fill_embeddings(orphan_and_unmapped)
    else:
        print("\n所有 chunk 已有有效 vec_index")

    # Step 4: 最终验证
    print("\n=== 最终验证 ===")
    from impl.vectorstore import check_drift

    drift = check_drift()
    print(f"drift check: {drift['message']}")

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        mapped = conn.execute("SELECT COUNT(*) FROM chunks WHERE vec_index IS NOT NULL").fetchone()[
            0
        ]
        orphan = conn.execute(
            f"SELECT COUNT(*) FROM chunks WHERE vec_index IS NOT NULL AND vec_index >= {actual_rows}"
        ).fetchone()[0]
        print(f"chunks 总数: {total}, 已映射: {mapped}, 孤儿: {orphan}")

    stats = get_stats()
    print(f"向量库: {stats['total_vectors']} 个向量，形状 {stats['shape']}")

    if drift["ok"] and orphan == 0:
        print("\n准备完成，数据一致")


if __name__ == "__main__":
    main()
