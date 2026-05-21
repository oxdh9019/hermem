#!/usr/bin/env python3
"""
Hermem Phase 3 - Error Annotation 验证脚本

运行方式：
    python3 ~/.hermes/projects/hermem/phase3/impl/verify_annotation.py

验证维度：
1. 覆盖率：L0 文件中 error_annotation 的比例
2. surprise_level 分布：high/medium/low 的占比
3. error_type 分布：各类型的数量
4. L1 facts 密度对比：高惊讶 vs 低惊讶 session 的 facts 密度差异

预期结果：
- 覆盖率 >= 80%
- surprise_level 分布接近：high~10%, medium~30%, low~60%
- 高惊讶 session 的 L1 facts 密度应显著高于低惊讶 session
"""

import json
import pathlib
import statistics
from collections import Counter, defaultdict


L0_DIR = pathlib.Path.home() / ".hermes" / "memory" / "l0_raw"
DB_PATH = pathlib.Path.home() / ".hermes" / "memory" / "l0_l3.db"


def load_l0_annotations(limit: int = 200) -> list[dict]:
    """加载最近 N 个 L0 文件的 error_annotation"""
    l0_files = sorted(L0_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    l0_files = l0_files[:limit]

    results = []
    for f in l0_files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        ea = data.get("error_annotation")
        if not ea:
            continue

        results.append({
            "session_id": data.get("session_id", f.stem),
            "error_annotation": ea,
            "l0_size_kb": f.stat().st_size // 1024,
            "messages_count": len(data.get("messages", [])),
        })

    return results


def load_l1_facts_per_session() -> dict[str, int]:
    """从 DB 加载每个 session 的 L1 facts 数量"""
    import sqlite3
    counts = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        for (sid, cnt) in conn.execute("SELECT l0_ref, COUNT(*) FROM l1_facts GROUP BY l0_ref"):
            # l0_ref 格式是 "l0_{session_id}"
            session_id = sid.replace("l0_", "") if sid.startswith("l0_") else sid
            counts[session_id] = cnt
    except Exception as e:
        print(f"[DB] 无法读取 l1_facts: {e}")
    return counts


def main():
    print("=" * 60)
    print("Hermem Error Annotation 验证报告")
    print("=" * 60)

    # 1. 基础统计
    all_l0 = list(L0_DIR.glob("*.json"))
    total_sessions = len(all_l0)
    print(f"\n[1] L0 文件总数: {total_sessions}")

    annotations = load_l0_annotations(limit=min(total_sessions, 200))
    annotated = len(annotations)

    print(f"[2] 有 error_annotation 的 session（最近200个）: {annotated}")
    print(f"    覆盖率: {annotated/200:.0%}" if total_sessions >= 200 else "")

    if not annotations:
        print("\n⚠️  尚无 error_annotation 数据，等待异步队列消费后重新运行")
        return

    # 2. surprise_level 分布
    surprise_levels = Counter(a["error_annotation"].get("surprise_level", "unknown") for a in annotations)
    total_annotated = sum(surprise_levels.values())
    print(f"\n[3] surprise_level 分布（共 {total_annotated} 个）:")
    for level in ["high", "medium", "low"]:
        cnt = surprise_levels.get(level, 0)
        pct = cnt / total_annotated * 100 if total_annotated else 0
        bar = "█" * int(pct / 5)
        print(f"    {level:8s}: {cnt:3d} ({pct:5.1f}%) {bar}")

    # 3. error_type 分布
    error_types = Counter()
    severity_counts = Counter()
    for a in annotations:
        for err in a["error_annotation"].get("prediction_errors", []):
            error_types[err.get("error_type", "unknown")] += 1
            severity = err.get("severity", "unknown")
            severity_counts[severity] += 1

    print(f"\n[4] error_type 分布（共 {sum(error_types.values())} 个预测误差）:")
    for et, cnt in error_types.most_common():
        print(f"    {et}: {cnt}")

    print(f"\n[5] severity 分布:")
    for sev in ["high", "medium", "low"]:
        cnt = severity_counts.get(sev, 0)
        total_errs = sum(severity_counts.values())
        pct = cnt / total_errs * 100 if total_errs else 0
        print(f"    {sev}: {cnt} ({pct:.1f}%)")

    # 4. L1 facts 密度对比
    print(f"\n[6] L1 Facts 密度对比（高惊讶 vs 低惊讶）:")
    facts_counts = load_l1_facts_per_session()

    high_sessions_msgs = []
    low_sessions_msgs = []

    for a in annotations:
        sid = a["session_id"]
        msgs = a.get("messages_count", 0)
        if msgs == 0:
            continue
        level = a["error_annotation"].get("surprise_level", "low")
        facts = facts_counts.get(sid, 0)
        density = facts / msgs if msgs > 0 else 0

        if level == "high":
            high_sessions_msgs.append((facts, msgs, density))
        elif level == "low":
            low_sessions_msgs.append((facts, msgs, density))

    if high_sessions_msgs and low_sessions_msgs:
        high_densities = [d for _, _, d in high_sessions_msgs]
        low_densities = [d for _, _, d in low_sessions_msgs]

        print(f"    高惊讶 session 数: {len(high_sessions_msgs)}")
        print(f"    低惊讶 session 数: {len(low_sessions_msgs)}")
        print(f"    高惊讶 平均facts/msg: {statistics.mean(high_densities):.3f}")
        print(f"    低惊讶 平均facts/msg: {statistics.mean(low_densities):.3f}")

        if len(high_densities) >= 5 and len(low_densities) >= 5:
            try:
                # Welch's t-test（不假设方差齐性）
                import math
                n1, n2 = len(high_densities), len(low_densities)
                mean1, mean2 = statistics.mean(high_densities), statistics.mean(low_densities)
                var1 = statistics.variance(high_densities) if n1 > 1 else 0
                var2 = statistics.variance(low_densities) if n2 > 1 else 0
                if var1 + var2 > 0:
                    se = math.sqrt(var1/n1 + var2/n2)
                    t_stat = (mean1 - mean2) / se if se > 0 else 0
                    print(f"    差异 t-statistic: {t_stat:.3f}")
                    print(f"    判断: {'✅ 高惊讶密度显著更高（signal有效）' if mean1 > mean2 and t_stat > 1.5 else '⚠️  差异不显著，需更多数据'}")
            except Exception as e:
                print(f"    统计检验跳过: {e}")
    else:
        print(f"    数据不足：high={len(high_sessions_msgs)}, low={len(low_sessions_msgs)} （各需≥5）")

    # 5. meta_prediction 抽样
    print(f"\n[7] meta_prediction 抽样（最近5条 high surprise）:")
    for a in annotations[:30]:
        if a["error_annotation"].get("surprise_level") == "high":
            meta = a["error_annotation"].get("meta_prediction", "")
            if meta:
                print(f"    [{a['session_id']}] {meta[:80]}")
                break

    print("\n" + "=" * 60)
    print("验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()