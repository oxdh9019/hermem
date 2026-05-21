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
  ~/.hermes/projects/hermem/impl/vectorstore.py
"""

import argparse
import json
import sys
from pathlib import Path

# 添加 impl 路径（scripts/ → hermem/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from impl.vectorstore import check_drift


def main():
    parser = argparse.ArgumentParser(description="Hermem vectorstore drift watchdog")
    parser.add_argument("--fix", action="store_true",
                        help="自动修复（truncate npy to meta.next_index）")
    parser.add_argument("--log", default="",
                        help="日志文件路径（追加写入）")
    args = parser.parse_args()

    result = check_drift()

    # 构造日志行
    import datetime
    ts = datetime.datetime.now().isoformat()
    log_line = f"{ts}  {result['message']}"

    # 追加写日志
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
            return auto_fix(result)
        else:
            print("提示：加 --fix 可自动修复 drift（truncate npy）")
            print("      加 --log /path/to/log.txt 可写入日志文件")
            return 2


def auto_fix(result: dict):
    """自动修复：truncate npy 到 meta.next_index 行。"""
    import numpy as np
    from impl.vectorstore import META_PATH, VEC_PATH, _write_meta, _invalidate_cache

    if result["drift"] <= 0:
        print("drift <= 0，无需修复")
        return 0

    target_rows = result["meta_next"] - result["drift"]   # = npy_rows
    print(f"自动修复：将 npy truncate 到 {target_rows} 行（移除 {result['drift']} 行孤儿）")

    try:
        # 加载当前 npy
        vecs = np.load(str(VEC_PATH))
        truncated = vecs[:target_rows]
        np.save(str(VEC_PATH), truncated)

        # 使缓存失效
        _invalidate_cache()

        # 验证
        from impl.vectorstore import check_drift
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
