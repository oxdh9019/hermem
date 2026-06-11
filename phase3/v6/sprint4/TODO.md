# Hermem V6 Sprint 4 TODO(续):排序权重 + 周期报告 + CI

**版本**: v2.0
**日期**: 2026-06-12
**状态**: Sprint 4 任务 4.1-4.4 ✅ 完成(评测框架 + 修 P0 灾难),启动 4.5-4.8
**依据**: `phase3/v6/SPEC.md` v2.0 §3 Sprint 4 + 决策 1(normalize)+ 决策 8(4b)

> **范围声明**:本 TODO 覆盖 Sprint 4 剩余 4 任务(4.5-4.8)。**任务 4.4 disposition.conf 自动化**因需 30 天观察期,Sprint 4 不做(Sprint 5+ 续)。

---

## Step 0:现状核查(改代码前必做)

- [x] `sqlite3 .schema l4_reflections` —— 表存在(8 列),**0 条记录**;沿用 sprint3 write_reflection_immediate
- [x] `SELECT * FROM recall_outcome` —— 0 条(Sprint 0.5 hook 落,生产没真实数据)
- [x] `SELECT id, concepts FROM chunks` —— 已存在概念字段(JSON 数组),**数据丰富**
- [x] `grep DISPOSITION config.py` —— 现有 disposition 体系已落地:
  - `DISPOSITION_HALF_LIFE_DAYS = 7`
  - `DISPOSITION_MIN_COUNT = 2` / `DISPOSITION_MAX_FACTOR = 2.0` / `DISPOSITION_BASE_WEIGHT = 1.0`
  - **概念权重机制已存在,Sprint 4 任务 4.5 只需"启用 + 接入 search_with_tier"**
- [x] `grep concept_weight vector_search.py` —— **当前未使用**(沿用 v5.5)
- [x] `find ~/.hermes/memory/eval/` —— 目录不存在,任务 4.7 创建

**结论**:
- **任务 4.5(概念权重)**:新增 `phase3/impl/concept_weight.py`,计算每个 chunk 的 concept_weight(0-1 半衰期 7 天),在 search_with_tier 返回时应用
- **任务 4.6(加权公式)**:在 search_with_tier 召回后,**重排**时乘 `cosine × recency × concept_weight × pattern_relevance`
- **任务 4.7(周报告)**:写 `phase3/scripts/weekly_report.py` + cron 每周日跑
- **任务 4.8(CI 回归)**:写 `.git/hooks/pre-commit`(或 `.githooks/pre-commit`)+ `phase3/scripts/ci_eval.py` 跑 baseline eval

---

## Sprint 4 任务总览(续)

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|---|
| **4.5** | P0 | `concept_weight.py` — 每个 chunk 的 0-1 权重(half_life 7 天) | `phase3/impl/concept_weight.py`(新) | 1h |
| **4.6** | P0 | `search_with_tier` 重排:score = cosine × recency × concept_weight × pattern_relevance | `phase3/impl/vector_search.py` + `impl/reranker.py`(新) | 1.5h |
| **4.7** | P0 | `weekly_report.py` — 每周日统计 recall/hit/MRR 趋势 | `phase3/scripts/weekly_report.py`(新)+ cron 提示 | 1h |
| **4.8** | P0 | CI 回归:pre-commit 跑 baseline eval,差过 5% 则 fail | `.githooks/pre-commit`(新)+ `phase3/scripts/ci_eval.py`(新) | 1h |
| **4.4** | 推迟 | disposition.conf 自动化(30 天观察) | 不做 | — |

**总预估**:半天到 1 天(4 任务,任务 4.5-4.6 紧密耦合)

---

## Sprint 4 任务详述

### 任务 4.5 — concept_weight 计算

**目标**:为每个 chunk 计算 concept_weight(0-1,半衰期 7 天),反映"用户最近在关心什么"。

**涉及文件**:`phase3/impl/concept_weight.py`(新)

**代码骨架**:
```python
"""Hermem V6 Sprint 4 任务 4.5:概念权重。

每个 chunk 有 0-1 的 concept_weight,反映"用户最近在关心这个概念"。
Sprint 0.5 落地的 disposition 体系已存在 (DISPOSITION_HALF_LIFE_DAYS=7),
本模块复用其常量,封装可独立测试的函数。
"""

import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import DISPOSITION_HALF_LIFE_DAYS, DISPOSITION_BASE_WEIGHT, DISPOSITION_MAX_FACTOR

# ── 衰减函数 ────────────────────────────────────────────

def decayed_weight(
    last_used_at: float | None,  # julianday 时间戳
    base_weight: float = DISPOSITION_BASE_WEIGHT,
    max_factor: float = DISPOSITION_MAX_FACTOR,
    half_life_days: float = DISPOSITION_HALF_LIFE_DAYS,
    now: float | None = None,
) -> float:
    """Sprint 4 任务 4.5:概念权重半衰期衰减。

    公式: weight = base + (max - base) * 0.5 ^ ((now - last_used) / half_life)
    - last_used 越近 → weight 越接近 max(最近被关心)
    - last_used 越远 → weight 越接近 base(中性 1.0)
    - 没 last_used → 1.0(中性,不加权)

    Args:
        last_used_at: julianday 时间戳(None = 1.0)
        base_weight: 中性起点(默认 1.0)
        max_factor: 最高增强(默认 2.0)
        half_life_days: 半衰期(默认 7)
        now: 当前 julianday(测试用)
    """
    if last_used_at is None:
        return base_weight
    if now is None:
        now = time.time() / 86400  # unix → julianday(近似)
    elapsed_days = max(0.0, now - last_used_at)
    decay = 0.5 ** (elapsed_days / half_life_days)
    return base_weight + (max_factor - base_weight) * decay
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.concept_weight import decayed_weight
now = 100.0
# 1. 最近用 → 高权重
print('1 天前:', decayed_weight(now - 1, now=now))   # 接近 2.0
print('7 天前:', decayed_weight(now - 7, now=now))   # 接近 1.5
print('14 天前:', decayed_weight(now - 14, now=now))  # 接近 1.25
print('None:', decayed_weight(None, now=now))          # 1.0
"
```

**风险**:
- `last_used_at` 字段在 chunks 表存在但可能大量为 NULL(从未被 recall) → 1.0 不加权,合理
- `now` 转换精度:unix 秒 / 86400 ≈ julianday(精确度足够,7 天半衰期无需毫秒级)

---

### 任务 4.6 — search_with_tier 重排

**目标**:在 search_with_tier RRF 融合后,应用加权公式 `score = cosine × recency × concept_weight × pattern_relevance` 重排,提高"用户最近关心" 的 chunk 排名。

**涉及文件**:`phase3/impl/vector_search.py` + `phase3/impl/reranker.py`(新)

**代码骨架**(`reranker.py`):
```python
"""Sprint 4 任务 4.6:重排器。"""

from .concept_weight import decayed_weight


def rerank(
    chunks: list[dict],
    top_k: int = 3,
    half_life_days: float = 7,
) -> list[dict]:
    """Sprint 4 任务 4.6 重排:score = cosine × recency × concept_weight × pattern_relevance。

    Args:
        chunks: 已 RRF 融合的 chunk 列表(每条含 rrf_score, sim, last_used_at, pattern_relevance)
        top_k: 返回条数
        half_life_days: concept_weight 半衰期

    Returns:
        重排后的 top_k chunks(每条加 final_score 字段)
    """
    now = time.time() / 86400
    for c in chunks:
        cosine = c.get("rrf_score", 0)  # RRF 融合后是 0-1 区间(BM25+vec 1/(60+rank) 加和)
        recency = 1.0  # 暂全部 1.0(后续任务加"上次注入时间"维度)
        cw = decayed_weight(c.get("last_used_at"), half_life_days=half_life_days, now=now)
        pr = c.get("pattern_relevance", 1.0)  # 暂 1.0(后续任务加)
        c["final_score"] = cosine * recency * cw * pr
    return sorted(chunks, key=lambda x: x["final_score"], reverse=True)[:top_k]
```

**集成到 `search_with_tier`**:
```python
# search_with_tier 末尾:在 RRF 融合 + 阈值切分前,重排
from .reranker import rerank
all_chunks = high + medium
all_chunks = rerank(all_chunks, top_k=len(all_chunks), half_life_days=7)
# 然后按 HIGH/MEDIUM 阈值切分
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.reranker import rerank
import time
now = time.time() / 86400
chunks = [
    {'id': 1, 'rrf_score': 0.5, 'last_used_at': now - 1},   # 高 cosine + 高 recency
    {'id': 2, 'rrf_score': 0.6, 'last_used_at': None},       # 更高 cosine + 中性 cw
    {'id': 3, 'rrf_score': 0.4, 'last_used_at': now - 0.1}, # 中 cosine + 极高 recency
]
result = rerank(chunks, top_k=3)
for r in result:
    print(f'#{r[\"id\"]}: cosine={r[\"rrf_score\"]}, final={r[\"final_score\"]:.3f}')
# 期望:#3 > #1 > #2(因为 recency 权重大)
"
```

**风险**:
- **pattern_relevance 暂 1.0**:实际应该有"query 模式 × chunk 模式"匹配度,但 Sprint 4 不做(需分析 query 模式);后续 Sprint 5+
- **recency 暂 1.0**:所有 chunk 同等;**Sprint 4 简化版只动 concept_weight**

---

### 任务 4.7 — 周报告

**目标**:每周日自动跑 `weekly_report.py`,生成 `~/.hermes/memory/eval/weekly/YYYY-Www.md`,含本周 vs 上周对比。

**涉及文件**:`phase3/scripts/weekly_report.py`(新)

**代码骨架**:
```python
"""Sprint 4 任务 4.7:周报告生成器。

每周日跑:
1. 跑 ground-truth 评测
2. 跟上周报告对比
3. 输出 Markdown 报告 → ~/.hermes/memory/eval/weekly/YYYY-Www.md
4. 推送到微信(可选,本期不做)
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_recall import evaluate, load_ground_truth


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ground_truth", default="eval/ground_truth.jsonl")
    p.add_argument("--output_dir", default=str(Path.home() / ".hermes/memory/eval/weekly"))
    p.add_argument("--top_k", type=int, default=5)
    args = p.parse_args()

    queries = load_ground_truth(args.ground_truth)

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
        results[name] = {
            "recall_at_k": round(m["recall_at_k_sum"] / n * 100, 1),
            "hit_at_k": round(m["hit_at_k"] / n * 100, 1),
            "mrr": round(m["mrr_sum"] / n * 100, 1),
            "latency_p50": round(sorted(m["latency_ms"])[int(len(m["latency_ms"]) * 0.5)], 0) if m["latency_ms"] else 0,
        }

    # 周次
    now = datetime.datetime.now()
    week = now.strftime("%Y-W%V")
    out_path = Path(args.output_dir) / f"{week}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Markdown 报告
    md = f"""# Hermem Sprint 4 周报告 - {week}

**日期**: {now.strftime('%Y-%m-%d %H:%M')}
**Ground truth**: {len(queries)} 条

## 评测结果(4 场景)

| 场景 | Recall@{args.top_k} | Hit@{args.top_k} | MRR | Latency p50 |
| --- | --- | --- | --- | --- |
"""
    for name in ["baseline_raw", "baseline_norm", "predictive_raw", "predictive_norm"]:
        r = results[name]
        md += f"| {name} | {r['recall_at_k']}% | {r['hit_at_k']}% | {r['mrr']}% | {r['latency_p50']}ms |\n"

    out_path.write_text(md, encoding="utf-8")
    print(f"✓ 周报告: {out_path}")
```

**使用**:
```bash
cd phase3
python3 scripts/weekly_report.py
# 输出: ~/.hermes/memory/eval/weekly/2026-W24.md
```

**cron**(可选,后续配置):
```bash
# 每周日 02:30 跑(跟 L4 reflection 同时间)
30 2 * * 0 cd /Users/oliver/.hermes/projects/hermem/phase3 && python3 scripts/weekly_report.py >> /tmp/weekly_report.log 2>&1
```

**风险**:
- **数据无变化时趋势不显著**:本周 vs 上周若 recall 都 66%,差 < 1% → 报告"持平"
- **ground-truth 不变**:评测稳定,可对比

---

### 任务 4.8 — CI 回归

**目标**:pre-commit 跑 baseline eval,如果关键指标差过 5% 则 fail。

**涉及文件**:`.githooks/pre-commit`(新)+ `phase3/scripts/ci_eval.py`(新)

**代码骨架**(`ci_eval.py`):
```python
"""Sprint 4 任务 4.8:CI 回归。pre-commit 跑 baseline eval,差过 5% 则 exit 1。

阈值:基线 baseline+norm Recall@5 >= 60.0%(修后 66.2% 给 5% 余量)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.eval_recall import evaluate, load_ground_truth

THRESHOLDS = {
    "baseline_norm": {"recall_at_k_min": 60.0, "hit_at_k_min": 60.0, "mrr_min": 50.0},
}


def main():
    gt_path = "/Users/oliver/.hermes/projects/hermem/phase3/eval/ground_truth.jsonl"
    queries = load_ground_truth(gt_path)
    if not queries:
        print("⚠️ no ground_truth, skip CI")
        return 0

    r = evaluate(queries, top_k=5, use_predictive=False, normalize=True)
    m = r["metrics"]
    n = m["total"]
    actual = {
        "recall_at_k": round(m["recall_at_k_sum"] / n * 100, 1),
        "hit_at_k": round(m["hit_at_k"] / n * 100, 1),
        "mrr": round(m["mrr_sum"] / n * 100, 1),
    }

    print(json.dumps(actual, ensure_ascii=False, indent=2))

    # 检查阈值
    fails = []
    for k, v_min in THRESHOLDS["baseline_norm"].items():
        if actual.get(k, 0) < v_min:
            fails.append(f"{k}={actual.get(k, 0)} < {v_min}")

    if fails:
        print(f"❌ CI FAIL: {fails}")
        return 1
    print("✅ CI PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**`pre-commit` hook**:
```bash
#!/bin/bash
# .githooks/pre-commit
cd /Users/oliver/.hermes/projects/hermem
python3 phase3/scripts/ci_eval.py
exit $?
```

**配置**:
```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
```

**风险**:
- **每次 commit 跑 4 秒**:`ci_eval.py` 跑 baseline 20 query ≈ 4 秒,可接受
- **如果阈值误设**:可能误拒 commit;`THRESHOLDS` 留余量(60% / 60% / 50%)

---

## Sprint 4 验收总表

| 标准 | 验证 |
|---|---|
| concept_weight 计算正确(半衰期) | `test_concept_weight_decay` 5 个测试 |
| search_with_tier 重排生效 | `test_search_with_tier_reranks_by_concept_weight` |
| 修后 baseline+norm Recall@5 仍 ≥ 60%(重排不破) | 跑 eval_recall.py 验证 |
| 周报告生成 | `weekly_report.py` 输出 `.md` |
| CI pre-commit 跑成功 | 手动 `git commit` 测试 |

---

## 风险登记

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 重排后 baseline 反而下降(权重公式副作用) | 中 | 7 个测试 + 跑 ground-truth 验证不退化 |
| cron 配置可能污染(任务 4.7 选做) | 低 | Sprint 4 任务 4.7 只生成报告, cron 留给 Sprint 5+ |
| CI 阈值 5% 余量可能不够 | 低 | Sprint 4 跑通后实测再调 |
| last_used_at 数据稀疏(0 行 recall_outcome) | 中 | decayed_weight(None) 返 1.0,合理降级 |

---

*依据 SPEC v2.0 §3 Sprint 4 任务 4.5-4.8 + 决策 1-7 + 决策 8(4b 一律)*

*Sprint 4 全部 4 任务 4.5-4.8 启动。等 Oliver 评审 TODO 后开干。*
