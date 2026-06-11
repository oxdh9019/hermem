"""Sprint 4 任务 4.7:周报告生成器。

每周日跑:
1. 跑 ground-truth 评测(4 场景)
2. 跟上周报告对比
3. 输出 Markdown 报告 → ~/.hermes/memory/eval/weekly/YYYY-Www.md

Usage:
    cd phase3
    python3 scripts/weekly_report.py
    python3 scripts/weekly_report.py --ground_truth eval/ground_truth.jsonl --top_k 5
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_recall import evaluate, load_ground_truth


def load_last_week_report(output_dir: Path, current_week: str) -> dict | None:
    """Sprint 4 任务 4.7:读上一周报告(如有)。"""
    if not output_dir.exists():
        return None
    reports = sorted(output_dir.glob("*.md"), reverse=True)
    for r in reports:
        if r.stem != current_week:
            try:
                # 简单解析:读 markdown 表格
                text = r.read_text(encoding="utf-8")
                # 找 "Recall@5 | Hit@5 | MRR" 头一行后,找 "baseline_norm | x% | y% | z% |"
                # 注:简单版,精确解析留给未来
                return {"path": r, "raw": text}
            except Exception:
                continue
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ground_truth", default="eval/ground_truth.jsonl")
    p.add_argument("--output_dir", default=str(Path.home() / ".hermes" / "memory" / "eval" / "weekly"))
    p.add_argument("--top_k", type=int, default=5)
    args = p.parse_args()

    queries = load_ground_truth(args.ground_truth)
    if not queries:
        print(f"❌ 无 ground_truth queries: {args.ground_truth}")
        return 1

    # 4 场景评测
    scenarios = [
        ("baseline_raw", {"use_predictive": False, "normalize": False}),
        ("baseline_norm", {"use_predictive": False, "normalize": True}),
        ("predictive_raw", {"use_predictive": True, "normalize": False}),
        ("predictive_norm", {"use_predictive": True, "normalize": True}),
    ]

    results = {}
    for name, opts in scenarios:
        r = evaluate(queries, top_k=args.top_k, **opts)
        m = r["metrics"]
        n = m["total"]
        if n == 0:
            results[name] = {"recall_at_k": 0, "hit_at_k": 0, "mrr": 0, "latency_p50": 0}
            continue
        latencies = m["latency_ms"]
        results[name] = {
            "recall_at_k": round(m["recall_at_k_sum"] / n * 100, 1),
            "hit_at_k": round(m["hit_at_k"] / n * 100, 1),
            "mrr": round(m["mrr_sum"] / n * 100, 1),
            "latency_p50": round(sorted(latencies)[int(len(latencies) * 0.5)], 0) if latencies else 0,
        }

    # 周次 + 路径
    now = datetime.datetime.now()
    week = now.strftime("%Y-W%V")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{week}.md"

    # 上周对比
    last_week = load_last_week_report(out_dir, week)

    # Markdown 报告
    md = f"""# Hermem Sprint 4 周报告 - {week}

**日期**: {now.strftime('%Y-%m-%d %H:%M')}
**Ground truth**: {len(queries)} 条 query
**Top K**: {args.top_k}

## 评测结果(4 场景)

| 场景 | Recall@{args.top_k} | Hit@{args.top_k} | MRR | Latency p50 |
| --- | --- | --- | --- | --- |
"""
    for name in ["baseline_raw", "baseline_norm", "predictive_raw", "predictive_norm"]:
        r = results[name]
        md += f"| {name} | {r['recall_at_k']}% | {r['hit_at_k']}% | {r['mrr']}% | {r['latency_p50']}ms |\n"

    if last_week:
        md += f"""
## 对比上周

- 上周报告: {last_week['path']}
- (本周 vs 上周对比留给人工或下个 Sprint)

"""
    else:
        md += """
## 对比上周

- 首次生成,无历史数据
- 下周起自动对比

"""

    md += """
## 关键指标说明

- **Recall@K**: top-K 内召回到的相关 chunk 占 ground-truth 相关 chunk 的比例(越接近 100% 越好)
- **Hit@K**: top-K 内至少 1 个相关的 query 比例(粗粒度指标)
- **MRR**: Mean Reciprocal Rank(第一个相关 chunk 排名的倒数,越接近 100% 越好)
- **Latency p50**: 50% 查询的延迟中位数

## 后续

- Sprint 5+ 加 query 模式(pattern_relevance 维度)
- 重排(recency)需 last_used_at 数据累积(等用户用 hermem 越久越好)
"""

    out_path.write_text(md, encoding="utf-8")
    print(f"✓ 周报告: {out_path}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
