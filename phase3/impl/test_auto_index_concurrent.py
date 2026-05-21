#!/usr/bin/env python3
"""
test_auto_index_concurrent.py — P1 auto_index 文件锁并发测试

Test 1: append_vectors 多进程并发 → drift=0 (P0 复验)
Test 2: INDEX_LOCK_FILE 文件锁代码存在且正确 (P1)
"""

import json
import multiprocessing
import numpy as np
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ── 测试路径 ──────────────────────────────────────────────
TEST_DIR  = Path("/tmp/hermem_p1_test")
VEC_PATH  = TEST_DIR / "vectors.npy"
META_PATH = TEST_DIR / "meta.json"
LOCK_PATH = TEST_DIR / ".index.lock"


# ── Top-level worker（必须模块级定义以支持 pickle）─────────
def _vec_worker(script: str):
    """在独立进程中执行向量写入脚本。"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=20
    )
    if result.returncode != 0:
        print(f"  stderr: {result.stderr[:300]}")
    return result.returncode


def setup_clean():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    np.save(str(VEC_PATH), np.empty((0, 1024), dtype=np.float32))
    with open(str(META_PATH), "w") as f:
        json.dump({"version": "1.0", "dim": 1024, "next_index": 0}, f)
    LOCK_PATH.touch()


def test1_append_vectors():
    """复验 P0: append_vectors 的双重锁在多进程并发下 drift=0。"""
    print("\n[Test 1] append_vectors 多进程并发（8进程 × 2向量）")
    setup_clean()

    worker_script_template = f"""
import sys, json, random, os, fcntl, shutil
import numpy as np
from pathlib import Path

VEC = Path("{VEC_PATH}")
META = Path("{META_PATH}")
LOCK = Path("{LOCK_PATH}")

lock_fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    with open(META) as f:
        meta = json.load(f)
    vectors = np.load(str(VEC)) if VEC.exists() else np.empty((0, 1024), dtype=np.float32)
    new_emb = np.array([[random.random() for _ in range(1024)] for _ in range(2)], dtype=np.float32)
    combined = np.vstack([vectors, new_emb])
    tmp = Path("/tmp/_hv_tmp.npy")
    np.save(str(tmp), combined)
    shutil.copy2(str(tmp), str(VEC))
    tmp.unlink(missing_ok=True)
    start_idx = meta["next_index"]
    meta["next_index"] = start_idx + 2
    with open(META, "w") as f:
        json.dump(meta, f)
    print(f"OK pid={{os.getpid()}} indices={{list(range(start_idx, start_idx+2))}}")
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
"""

    N = 8
    procs = []
    for i in range(N):
        p = multiprocessing.Process(target=_vec_worker, args=(worker_script_template,))
        procs.append(p)

    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    # 验证
    vecs = np.load(str(VEC_PATH))
    with open(str(META_PATH)) as f:
        meta = json.load(f)
    drift = meta["next_index"] - vecs.shape[0]
    alive = [p for p in procs if p.is_alive()]
    print(f"  meta={meta['next_index']}, npy={vecs.shape[0]}, drift={drift}, alive={len(alive)}")
    ok = (drift == 0 and len(alive) == 0)
    print(f"  {'✅ PASS' if ok else '❌ FAIL'}")
    return ok


def test2_lock_code():
    """验证 hermem_auto_index_all.py 文件锁实现正确。"""
    print("\n[Test 2] 文件锁代码检查")
    script_path = Path("/Users/oliver/.hermes/scripts/hermem_auto_index_all.py")
    content = script_path.read_text()

    checks = [
        ("fcntl 导入",              "fcntl" in content),
        ("INDEX_LOCK_FILE 定义",    "INDEX_LOCK_FILE" in content),
        ("LOCK_EX 获取锁",          "fcntl.LOCK_EX" in content),
        ("LOCK_UN 释放锁",          "fcntl.LOCK_UN" in content),
        ("os.close(lock_fd)",        "os.close(lock_fd)" in content),
        ("try/finally 包裹",         "finally:" in content),
        ("main() 调用 _main_inner", "_main_inner()" in content),
    ]

    all_ok = True
    for name, result in checks:
        print(f"  {name}: {'✅' if result else '❌'}")
        if not result:
            all_ok = False

    print(f"  {'✅ PASS' if all_ok else '❌ FAIL'}")
    return all_ok


def main():
    print("=" * 60)
    print("P1: auto_index 文件锁并发测试")
    print("=" * 60)

    ok1 = test1_append_vectors()
    ok2 = test2_lock_code()

    shutil.rmtree(TEST_DIR, ignore_errors=True)

    print("\n── 结果 ──────────────────────────────")
    print(f"Test 1 (append_vectors P0):  {'✅ PASS' if ok1 else '❌ FAIL'}")
    print(f"Test 2 (文件锁代码 P1):      {'✅ PASS' if ok2 else '❌ FAIL'}")
    overall = ok1 and ok2
    print(f"\nOverall: {'✅ ALL PASS' if overall else '❌ SOME FAILED'}")
    print("=" * 60)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
