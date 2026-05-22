#!/usr/bin/env python3
"""
watchdog_vectorstore.py — 向量存储 drift 监控脚本

功能：
1. 检查 meta.next_index 与 npy 行数是否一致
2. drift > 0 时记录到日志并输出 [ALERT] 供 cron 捕获
3. drift > 阈值时提供修复建议

用法：
  python3 watchdog_vectorstore.py [--fix] [--log PATH]

依赖：
  phase3/impl/vectorstore.py
"""

import argparse
import sys
from pathlib import Path

# 添加 phase3 路径（scripts/ → phase3/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from impl.vectorstore import check_drift


def main():
    parser = argparse.ArgumentParser(description="Hermem vectorstore drift watchdog")
    parser.add_argument(
        "--fix", action="store_true", help="自动修复（truncate npy to meta.next_index）"
    )
    parser.add_argument("--log", default="", help="日志文件路径（追加写入）")
    args = parser.parse_args()

    result = check_drift()

    # 构造日志行
    import datetime

    ts = datetime.datetime.now().isoformat()
    log_line = f"{ts}  {result['message']}"

    # 追加写日志（先记录初始状态）
    if args.log:
        log_path = Path(args.log).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(log_line + "\n")

    # 标准输出（cron 捕获）
    if result["ok"]:
        print(f"[OK] {result['message']}")
        return 0
    else:
        print(f"[ALERT] {result['message']}")
        if args.fix:
            fix_ret = auto_fix(result)
            # 修复后重新写入日志（覆盖为修复后状态）
            if args.log:
                ts2 = datetime.datetime.now().isoformat()
                verify = check_drift()
                post_msg = f"{ts2}  FIXED: {verify['message']}"
                # 追加修复后记录
                with open(log_path, "a") as f:
                    f.write(post_msg + "\n")
            return fix_ret
        else:
            print("提示：加 --fix 可自动修复 drift（pad 0 / truncate 多余行）")
            print("      加 --log /path/to/log.txt 可写入日志文件")
            return 2


def auto_fix(result: dict):
    """自动修复 drift：
    - drift > 0：npy 少于 meta，pad 0 行补齐
    - drift < 0：npy 多于 meta，truncate 多的行
    """
    import numpy as np
    from impl.vectorstore import VEC_PATH, _invalidate_cache

    drift = result["drift"]
    meta_next = result["meta_next"]
    result["npy_rows"]
    target_rows = meta_next  # npy 应有的行数

    if drift == 0:
        print("drift = 0，无需修复")
        return 0

    try:
        vecs = np.load(str(VEC_PATH))
        dim = vecs.shape[1]

        if drift > 0:
            # npy 落后于 meta → pad 0 行
            print(f"自动修复：npy 落后 meta {drift} 行，pad {drift} 行零向量至 {target_rows} 行")
            padding = np.zeros((drift, dim), dtype=vecs.dtype)
            fixed = np.vstack([vecs, padding])
        else:
            # npy 领先于 meta → truncate
            print(f"自动修复：npy 领先 meta {-drift} 行，truncate 至 {target_rows} 行")
            fixed = vecs[:target_rows]

        np.save(str(VEC_PATH), fixed)

        # 使缓存失效
        _invalidate_cache()

        # 验证
        verify = check_drift()
        if verify["ok"]:
            print(f"修复成功：{verify['message']}")
            return 0
        else:
            print(f"修复后仍有问题：{verify['message']}")
            return 1
    except Exception as e:
        print(f"自动修复失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
