"""Sprint 4 任务 4.8:CI 回归。pre-commit 跑 baseline eval,差过阈值则 exit 1。

阈值:基线 baseline+norm Recall@5 >= 60.0%(修后 66.2% 给 5% 余量)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_recall import evaluate, load_ground_truth

THRESHOLDS = {
    "baseline_norm": {
        "recall_at_k_min": 60.0,
        "hit_at_k_min": 60.0,
        "mrr_min": 50.0,
    },
}


def main():
    gt_path = "/Users/oliver/.hermes/projects/hermem/phase3/eval/ground_truth.jsonl"
    if not Path(gt_path).exists():
        print(f"⚠️ ground_truth not found: {gt_path}; skip CI")
        return 0

    queries = load_ground_truth(gt_path)
    if not queries:
        print("⚠️ no ground_truth queries; skip CI")
        return 0

    # 跑 baseline+norm 评测
    r = evaluate(queries, top_k=5, use_predictive=False, normalize=True)
    m = r["metrics"]
    n = m["total"]
    actual = {
        "recall_at_k": round(m["recall_at_k_sum"] / n * 100, 1),
        "hit_at_k": round(m["hit_at_k"] / n * 100, 1),
        "mrr": round(m["mrr_sum"] / n * 100, 1),
    }
    latencies = m["latency_ms"]
    p50 = round(sorted(latencies)[int(len(latencies) * 0.5)], 0) if latencies else 0

    print("=" * 50)
    print("Sprint 4 CI Eval (baseline+norm, top_k=5)")
    print("=" * 50)
    print(f"  Recall@5: {actual['recall_at_k']}%")
    print(f"  Hit@5:    {actual['hit_at_k']}%")
    print(f"  MRR:      {actual['mrr']}%")
    print(f"  Latency p50: {p50}ms")
    print()

    fails = []
    for actual_key, v_min in THRESHOLDS["baseline_norm"].items():
        # actual_key like "recall_at_k_min" → strip "_min" → "recall_at_k"
        k = actual_key.replace("_min", "")
        if actual.get(k, 0) < v_min:
            fails.append(f"{k}={actual.get(k, 0)} < {v_min}")

    if fails:
        print(f"❌ CI FAIL: {fails}")
        return 1
    print("✅ CI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
