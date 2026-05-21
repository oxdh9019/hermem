#!/usr/bin/env python3
"""
test_vector_concurrent.py — 多进程并发写入测试

策略：
- 主进程：准备环境 + 启动 N 个子进程，每个子进程调用 append_vectors
- 子进程用 subprocess 启动（真实的多进程隔离）
- 主进程负责汇总验证

用法：
  python3 test_vector_concurrent.py
"""

import json
import multiprocessing
import os
import random
import subprocess
import sys
import time
from pathlib import Path

HERMEM   = Path.home() / ".hermes"
IMPL_PY  = Path(__file__).resolve().parent
SCRIPT   = IMPL_PY / "_concurrent_worker.py"

NPY_PATH  = HERMEM / "memory" / "hermem_vectors.npy"
META_PATH = HERMEM / "memory" / "hermem_meta.json"


def get_state():
    """读取 meta next_index 和 npy 行数（直接读文件）。"""
    with open(META_PATH) as f:
        meta = json.load(f)
    meta_next = meta["next_index"]
    vecs = __import__("numpy").load(str(NPY_PATH))
    npy_rows = vecs.shape[0]
    return meta_next, npy_rows


def worker(batch_size: int, idx: int, lock_fd: int):
    """子进程入口：调用 append_vectors。"""
    import sys
    sys.path.insert(0, str(IMPL_PY.parent))
    from impl.vectorstore import append_vectors

    embeddings = [[random.random() for _ in range(1024)] for _ in range(batch_size)]
    indices = append_vectors(embeddings)
    return idx, len(indices), indices


def run_test(num_procs: int, batch_size: int, repeats: int = 3) -> bool:
    all_ok = True
    for rep in range(repeats):
        state_before = get_state()
        meta_before, npy_before = state_before

        # 启动 N 个子进程
        procs = []
        for i in range(num_procs):
            p = multiprocessing.Process(target=worker, args=(batch_size, i, 0))
            procs.append(p)

        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)   # 每个子进程最多等 30s

        # 检查是否有子进程未退出
        alive = [p for p in procs if p.is_alive()]
        if alive:
            print(f"  rep {rep+1}: {len(alive)} processes still alive — TIMEOUT")
            for p in alive:
                p.terminate()
            all_ok = False
            continue

        # 验证结果
        state_after = get_state()
        meta_after, npy_after = state_after
        expected_new = num_procs * batch_size
        actual_meta_inc  = meta_after  - meta_before
        actual_npy_inc  = npy_after   - npy_before
        drift = meta_after - npy_after

        ok = (actual_meta_inc == expected_new and
              actual_npy_inc  == expected_new and
              drift == 0)
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False

        print(f"  rep {rep+1}: "
              f"meta +{actual_meta_inc}/{expected_new}, "
              f"npy  +{actual_npy_inc}/{expected_new}, "
              f"drift={drift} → {status}")
    return all_ok


def main():
    print("=" * 60)
    print("Vectorstore 多进程并发写入测试")
    print("=" * 60)

    state = get_state()
    print(f"\n初始: meta_next={state[0]}, npy_rows={state[1]}, drift={state[0]-state[1]}")

    # Test 1: 3 processes × 5 vectors
    print("\n[Test 1] 3 processes × 5 vectors × 3 repeats")
    ok1 = run_test(num_procs=3, batch_size=5, repeats=3)

    # Test 2: 5 processes × 3 vectors
    print("\n[Test 2] 5 processes × 3 vectors × 3 repeats")
    ok2 = run_test(num_procs=5, batch_size=3, repeats=3)

    # Test 3: 10 processes × 2 vectors
    print("\n[Test 3] 10 processes × 2 vectors × 3 repeats")
    ok3 = run_test(num_procs=10, batch_size=2, repeats=3)

    state_final = get_state()
    print(f"\n最终: meta_next={state_final[0]}, npy_rows={state_final[1]}, "
          f"drift={state_final[0]-state_final[1]}")

    overall = ok1 and ok2 and ok3 and (state_final[0] - state_final[1]) == 0
    print(f"\nOverall: {'ALL PASS ✅' if overall else 'SOME FAILED ❌'}")
    print("=" * 60)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
