"""Hermem V6 Sprint 4 任务 4.3:召回评测。

跑 ground-truth.jsonl 里的 query,调 search_with_tier / search_predictive,
计算 recall@5 / recall@10 / MRR / hit_rate。

Usage:
    cd phase3
    python3 scripts/eval_recall.py --ground_truth eval/ground_truth.jsonl --top_k 5
    python3 scripts/eval_recall.py --ground_truth eval/ground_truth.jsonl --predictive --top_k 5
    python3 scripts/eval_recall.py --ground_truth eval/ground_truth.jsonl --normalize_query --top_k 5
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # phase3 路径

from impl.vector_search import search_with_tier


def normalize_query(q: str) -> str:
    """去掉问号和问句尾词,避免 BM25 重排(决策 1:问句后缀影响 FTS5 召回)。

    例子: 'ds2api 工具怎么用？' -> 'ds2api 工具'
          '连环画三视图生成最佳实践是什么？' -> '连环画三视图生成最佳实践'
    """
    q = q.replace('?', '').replace('？', '').strip()
    for suffix in ['是什么', '什么', '怎么用', '如何', '哪些', '哪种']:
        if q.endswith(suffix):
            q = q[:-len(suffix)]
    return q.strip()


def load_ground_truth(path: str) -> list[dict]:
    """读 ground_truth.jsonl,过滤空 relevant 列表的(标 0 相关的 query)。"""
    queries = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d.get("relevant_chunk_ids"):  # 跳过空列表(标 0 相关)
                queries.append(d)
    return queries


def evaluate(
    queries: list[dict],
    top_k: int = 5,
    use_predictive: bool = False,
    normalize: bool = False,
) -> dict:
    """对每条 query 调召回,计算指标。"""
    metrics = {
        "total": 0,
        "recall_at_k_sum": 0.0,        # recall@k 求和
        "recall_at_2k_sum": 0.0,       # recall@2k 求和(更宽松)
        "mrr_sum": 0.0,                # MRR 求和
        "hit_at_k": 0,                  # top-k 内至少 1 个相关
        "hit_at_2k": 0,                # top-2k 内至少 1 个
        "latency_ms": [],              # 每条 latency
    }

    per_query_results = []

    for q in queries:
        qid = q["query_id"]
        qtext = q["query"]
        if normalize:
            qtext = normalize_query(qtext)
        relevant = set(q["relevant_chunk_ids"])

        t0 = time.time()
        if use_predictive:
            from impl.predictor import search_predictive
            high, medium = search_predictive(user_query=qtext, top_k=top_k)
            # 收集所有 unique chunk_id(去重)
            retrieved_ids = []
            for c in high + medium:
                cid = c.get("id") or c.get("chunk_id")
                if cid is not None and cid not in retrieved_ids:
                    retrieved_ids.append(cid)
        else:
            high, medium = search_with_tier(query=qtext, top_k=top_k)
            retrieved_ids = []
            for c in high + medium:
                cid = c.get("id") or c.get("chunk_id")
                if cid is not None and cid not in retrieved_ids:
                    retrieved_ids.append(cid)
        latency_ms = (time.time() - t0) * 1000
        metrics["latency_ms"].append(latency_ms)

        # top-k hit
        top_k_ids = retrieved_ids[:top_k]
        top_2k_ids = retrieved_ids[:top_k * 2]
        hit_in_topk = bool(set(top_k_ids) & relevant)
        hit_in_2k = bool(set(top_2k_ids) & relevant)
        if hit_in_topk:
            metrics["hit_at_k"] += 1
        if hit_in_2k:
            metrics["hit_at_2k"] += 1

        # recall@k = |retrieved ∩ relevant| / |relevant|
        recall_at_k = len(set(top_k_ids) & relevant) / len(relevant) if relevant else 0
        recall_at_2k = len(set(top_2k_ids) & relevant) / len(relevant) if relevant else 0
        metrics["recall_at_k_sum"] += recall_at_k
        metrics["recall_at_2k_sum"] += recall_at_2k

        # MRR = 1 / rank_of_first_relevant(在 retrieved_ids 列表里)
        mrr = 0.0
        for rank, cid in enumerate(retrieved_ids, 1):
            if cid in relevant:
                mrr = 1.0 / rank
                break
        metrics["mrr_sum"] += mrr

        metrics["total"] += 1

        per_query_results.append({
            "query_id": qid,
            "query": qtext[:40],
            "relevant": sorted(relevant),
            "retrieved_top_k": top_k_ids,
            "hit_top_k": hit_in_topk,
            "recall_at_k": round(recall_at_k, 2),
            "mrr": round(mrr, 2),
            "latency_ms": round(latency_ms, 0),
        })

    return {
        "metrics": metrics,
        "per_query": per_query_results,
    }


def print_report(
    queries: list[dict],
    result: dict,
    top_k: int,
    use_predictive: bool,
    ground_truth_path: str,
):
    m = result["metrics"]
    n = m["total"]
    if n == 0:
        print("❌ 无 ground-truth queries(可能 ground_truth.jsonl 空或全标 0 相关)")
        return

    latencies = m["latency_ms"]
    p50 = sorted(latencies)[int(len(latencies) * 0.5)] if latencies else 0
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0
    avg = sum(latencies) / len(latencies) if latencies else 0

    print("=" * 60)
    print(f"📊 Hermem V6 Sprint 4 — 召回评测报告")
    print("=" * 60)
    print(f"ground_truth: {ground_truth_path}")
    print(f"模式: {'predictive (4b)' if use_predictive else 'baseline (search_with_tier)'}")
    print(f"top_k: {top_k}")
    print(f"queries: {n} (跳过 {len(queries) - n} 条标 0 相关的)")
    print()
    print(f"  Recall@{top_k}    : {m['recall_at_k_sum'] / n * 100:5.1f}%  (avg)")
    print(f"  Recall@{top_k*2}  : {m['recall_at_2k_sum'] / n * 100:5.1f}%  (宽松)")
    print(f"  Hit@{top_k}      : {m['hit_at_k'] / n * 100:5.1f}%  ({m['hit_at_k']}/{n} 至少 1 个相关)")
    print(f"  Hit@{top_k*2}    : {m['hit_at_2k'] / n * 100:5.1f}%  ({m['hit_at_2k']}/{n})")
    print(f"  MRR            : {m['mrr_sum'] / n * 100:5.1f}%  (avg)")
    print()
    print(f"  Latency p50    : {p50:5.0f}ms")
    print(f"  Latency p95    : {p95:5.0f}ms")
    print(f"  Latency avg    : {avg:5.0f}ms")
    print()
    print("=" * 60)
    print("逐 query 结果(召回失败优先显示):")
    sorted_results = sorted(
        result["per_query"],
        key=lambda r: (r["hit_top_k"], -r["recall_at_k"], r["latency_ms"]),
    )
    for r in sorted_results[:10]:
        mark = "✅" if r["hit_top_k"] else "❌"
        print(f"  {mark} {r['query_id']}: recall@K={r['recall_at_k']:.2f} mrr={r['mrr']:.2f} "
              f"latency={r['latency_ms']:.0f}ms")
        print(f"      query: {r['query']}")
        print(f"      relevant: {r['relevant']}")
        print(f"      retrieved_top_{top_k}: {r['retrieved_top_k']}")
    if len(sorted_results) > 10:
        print(f"  ... +{len(sorted_results) - 10} more (run with --verbose to see all)")

    # 召回失败诊断
    misses = [r for r in result["per_query"] if not r["hit_top_k"]]
    if misses:
        print()
        print("=" * 60)
        print(f"🔍 召回失败诊断({len(misses)} 条):")
        for r in misses:
            print(f"  ❌ {r['query_id']}: {r['query']}")
            print(f"      relevant: {r['relevant']} (应召回)")
            print(f"      retrieved: {r['retrieved_top_k']} (实际召回,无交集)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ground_truth", default="eval/ground_truth.jsonl")
    p.add_argument("--top_k", type=int, default=5)
    p.add_argument("--predictive", action="store_true",
                   help="用 hermem_search_predictive(4b LLM) 而非 baseline search_with_tier")
    p.add_argument("--normalize_query", action="store_true",
                   help="预处理 query(去问号 + 问句尾词);Sprint 4 决策 1:BM25 对问句词敏感")
    p.add_argument("--output", default=None,
                   help="可选:把详细 per-query 结果写到这个 JSONL")
    args = p.parse_args()

    queries = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(queries)} queries from {args.ground_truth}")

    result = evaluate(queries, top_k=args.top_k, use_predictive=args.predictive,
                     normalize=args.normalize_query)
    print_report(queries, result, args.top_k, args.predictive, args.ground_truth)

    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "ground_truth": args.ground_truth,
                "top_k": args.top_k,
                "predictive": args.predictive,
                "queries_count": len(queries),
                "per_query": result["per_query"],
                "summary": {
                    "recall_at_k": result["metrics"]["recall_at_k_sum"] / len(queries),
                    "hit_at_k": result["metrics"]["hit_at_k"] / len(queries),
                    "mrr": result["metrics"]["mrr_sum"] / len(queries),
                    "latency_p50": sorted(result["metrics"]["latency_ms"])[int(len(result["metrics"]["latency_ms"]) * 0.5)],
                    "latency_p95": sorted(result["metrics"]["latency_ms"])[int(len(result["metrics"]["latency_ms"]) * 0.95)],
                },
            }, f, ensure_ascii=False, indent=2)
        print(f"\n📁 详细结果: {args.output}")


if __name__ == "__main__":
    main()
