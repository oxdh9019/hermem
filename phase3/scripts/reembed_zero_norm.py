"""Sprint 4 任务 4.4 修 P0:re-embed 1506 个 norm=0 chunk。

根因:这些 chunk 写入时 Ollama bge-m3 返回 0 向量(异常或 batch 失败),
导致它们永远无法被 search_with_tier 召回(余弦相似度 0/0 = NaN)。

修法:
1. 扫 chunks 表找 norm=0 的 chunk
2. 调 Ollama bge-m3 重新 embed(content 限制 5000 字符防超长)
3. 写回 npy 对应位置(覆盖原 0 向量)
4. 记录 metadata log(谁跑了,跑了多少,失败多少)
5. 写一个 dry-run 选项(--dry_run 只看不改)

Usage:
    python3 scripts/reembed_zero_norm.py --dry_run
    python3 scripts/reembed_zero_norm.py --batch_size 50 --max_total 100
    python3 scripts/reembed_zero_norm.py
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

# Ollama bge-m3 endpoint
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "bge-m3:latest"


def find_zero_norm_chunks(con, npy):
    """扫 chunks 表,找 vec_index 在 npy 范围内但 norm=0 的 chunk。"""
    bad = []
    for row in con.execute(
        "SELECT id, vec_index, content, chunk_type FROM chunks "
        "WHERE vec_index IS NOT NULL"
    ):
        cid, vi, content, ctype = row
        if vi is None or vi < 0 or vi >= len(npy):
            continue
        v = npy[vi]
        norm = float(np.linalg.norm(v))
        if norm == 0 or np.isnan(v).any():
            bad.append((cid, vi, content, ctype, norm))
    return bad


def embed_one(text: str, timeout: float = 30.0) -> np.ndarray | None:
    """调 Ollama bge-m3 embed 单条 text,返回 1024-d numpy。失败返 None。"""
    try:
        resp = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": text[:5000]},  # bge-m3 上限 512 tokens
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        emb = data.get("embedding")
        if emb and len(emb) == 1024:
            v = np.array(emb, dtype=np.float32)
            # 验证 norm > 0
            if float(np.linalg.norm(v)) > 0:
                return v
    except Exception as e:
        print(f"  ✗ embed error: {e}")
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry_run", action="store_true", help="只看,不改 npy")
    p.add_argument("--batch_size", type=int, default=50, help="每多少条打印一次进度")
    p.add_argument("--max_total", type=int, default=None, help="最多跑多少条(限速)")
    p.add_argument("--timeout", type=float, default=30.0, help="Ollama 请求超时(秒)")
    args = p.parse_args()

    # 路径
    HERMEM_DB = "/Users/oliver/.hermes/memory/hermem.db"
    NPY_PATH = "/Users/oliver/.hermes/memory/hermem_vectors.npy"
    LOG_PATH = "/Users/oliver/.hermes/memory/hermem_reembed_log.jsonl"

    con = sqlite3.connect(HERMEM_DB)
    npy = np.load(NPY_PATH)
    print(f"npy shape: {npy.shape}")

    bad = find_zero_norm_chunks(con, npy)
    print(f"找到 {len(bad)} 个 norm=0 或 nan 的 chunk")

    if args.dry_run:
        print("\n[DRY-RUN] 不修改 npy,只列:")
        for cid, vi, content, ctype, norm in bad[:10]:
            excerpt = (content or "")[:60]
            print(f"  #{cid} (vec_index={vi}, type={ctype}): {excerpt}")
        if len(bad) > 10:
            print(f"  ... +{len(bad) - 10} more")
        return

    # 限制总条数
    if args.max_total:
        bad = bad[:args.max_total]
        print(f"限速: 只跑前 {args.max_total} 条")

    # 写 metadata log
    log_file = open(LOG_PATH, "a")
    run_id = time.strftime("%Y%m%d_%H%M%S")
    log_file.write(json.dumps({
        "event": "start", "run_id": run_id, "total": len(bad),
        "model": EMBED_MODEL, "timeout": args.timeout,
    }, ensure_ascii=False) + "\n")

    success = 0
    fail = 0
    t_start = time.time()
    for i, (cid, vi, content, ctype, _old_norm) in enumerate(bad):
        t0 = time.time()
        v_new = embed_one(content or "", timeout=args.timeout)
        elapsed = (time.time() - t0) * 1000

        if v_new is not None:
            # 写回 npy(就地修改)
            npy[vi] = v_new
            success += 1
            status = "✓"
        else:
            fail += 1
            status = "✗"

        # 进度日志
        if (i + 1) % args.batch_size == 0 or i == 0:
            total_elapsed = time.time() - t_start
            rate = (i + 1) / total_elapsed if total_elapsed > 0 else 0
            eta = (len(bad) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1:4d}/{len(bad)}] {status} chunk #{cid} ({elapsed:.0f}ms) "
                  f"success={success} fail={fail} "
                  f"rate={rate:.1f}/s ETA={eta:.0f}s")

        # 写 log
        log_file.write(json.dumps({
            "event": "embed", "run_id": run_id, "chunk_id": cid, "vec_index": vi,
            "status": "success" if v_new is not None else "fail",
            "elapsed_ms": round(elapsed, 0),
        }, ensure_ascii=False) + "\n")

    # 持久化 npy
    if success > 0:
        print(f"\n保存 npy ({success} 个新嵌入)...")
        np.save(NPY_PATH, npy)
        print(f"✓ npy saved")

    log_file.write(json.dumps({
        "event": "end", "run_id": run_id, "success": success, "fail": fail,
        "total_time_s": round(time.time() - t_start, 1),
    }, ensure_ascii=False) + "\n")
    log_file.close()

    print()
    print("=" * 50)
    print(f"完成: 成功 {success} / 失败 {fail} / 总 {len(bad)}")
    print(f"总耗时: {time.time() - t_start:.1f}s")
    print(f"平均速率: {len(bad) / (time.time() - t_start):.1f} chunks/s")
    print(f"日志: {LOG_PATH}")


if __name__ == "__main__":
    main()
