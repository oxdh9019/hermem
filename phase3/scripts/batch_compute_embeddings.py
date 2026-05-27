#!/usr/bin/env python3
"""
Hermem V5 Step 1: 批量预计算现有 chunk embedding

为现有 1630 个 chunk 生成并存储 embedding，建立 vec_index 映射。

用法: python3 scripts/batch_compute_embeddings.py
"""

import json
import os
import shutil
import sys
from pathlib import Path

script_path = Path(__file__).resolve()
sys.path.insert(0, str(script_path.parent.parent))  # scripts/ → phase3/

import numpy as np
from impl import config
from impl.database import (
    close_conn,
    get_all_chunks,
    get_chunk_count,
    init_db,
    insert_chunk,
)


def main():
    print("=== Hermem V5 Step 1: 批量预计算 embedding ===")
    print(f"Embedding 模型: {config.EMBEDDING_MODEL}")

    init_db()
    chunks = get_all_chunks()
    print(f"找到 {len(chunks)} 个 chunk")

    if not chunks:
        print("无 chunk 需要处理")
        return

    # 去重：过滤掉已有 vec_index 的 chunk
    chunks_to_process = [c for c in chunks if c.get("vec_index") is None]
    already_done = len(chunks) - len(chunks_to_process)
    print(f"已有 vec_index: {already_done}，需处理: {len(chunks_to_process)}")

    if not chunks_to_process:
        print("所有 chunk 已生成 embedding")
        return

    # 加载模型
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("ERROR: sentence-transformers 未安装")
        print("运行: pip install sentence-transformers")
        return

    model = SentenceTransformer(config.EMBEDDING_MODEL)

    # 批量生成 embedding
    texts = [c["content"] for c in chunks_to_process]
    print(f"正在生成 {len(texts)} 个 embedding（batch_size={config.BATCH_SIZE}）...")
    embeddings = model.encode(
        texts,
        batch_size=config.BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    vectors = np.array(embeddings, dtype=np.float32)
    print(f"Embedding 形状: {vectors.shape}")

    # 追加到现有向量库
    from impl.vectorstore import append_vectors, get_stats

    existing = get_stats()
    print(f"现有向量库: {existing['total_vectors']} 个向量")

    indices = append_vectors(embeddings.tolist())
    print(f"追加 {len(indices)} 个向量，索引范围: {indices[0]}-{indices[-1]}")

    # 更新数据库 chunks.vec_index
    from impl.database import get_db

    with get_db() as conn:
        for i, chunk in enumerate(chunks_to_process):
            vec_idx = indices[i]
            conn.execute(
                "UPDATE chunks SET vec_index = ? WHERE id = ?",
                (vec_idx, chunk["id"]),
            )
        conn.commit()

    print("数据库 vec_index 已更新")

    # 验证
    from impl.vectorstore import get_stats as vs_stats

    stats = vs_stats()
    print("\n=== 验证 ===")
    print(
        f"向量库总量: {stats['total_vectors']}（应为 {existing['total_vectors']} + {len(indices)}）"
    )
    print(f"向量形状: {stats['shape']}")

    total = get_chunk_count()
    with get_db() as conn:
        mapped = conn.execute("SELECT COUNT(*) FROM chunks WHERE vec_index IS NOT NULL").fetchone()[
            0
        ]
    print(f"chunks 表总数: {total}，已有 vec_index: {mapped}")

    if stats["total_vectors"] == existing["total_vectors"] + len(indices):
        print("Step 1 完成")
    else:
        print("WARNING: 数量不匹配，请检查")


if __name__ == "__main__":
    main()
