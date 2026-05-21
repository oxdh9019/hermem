#!/usr/bin/env python3
"""
Daily Synthesis for Hermem V4.3
- 扫描过去 24 小时的 error annotations（L0 JSON）
- 扫描 l1_dispositions 的 recent error_count 变化
- 识别 recurring patterns，生成 active_learnings.md
- 供下一轮 system prompt 加载
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any

# ── 路径配置 ─────────────────────────────────────────────────────────
HERMES_ROOT = Path.home() / ".hermes"
MEMORY_DIR  = HERMES_ROOT / "memory"
L0_DIR      = MEMORY_DIR / "l0_raw"
DB_PATH     = MEMORY_DIR / "l0_l3.db"          # l1_dispositions 所在 DB
OUTPUT_PATH = HERMES_ROOT / "active_learnings_daily.md"

# 时间窗口（小时）
LOOKBACK_HOURS = 24


# ── 数据收集 ─────────────────────────────────────────────────────────

def get_recent_annotations(since: datetime) -> List[Dict]:
    """
    扫描 L0 JSON，提取 error_annotation 中 annotated_at >= since 的记录。
    annotated_at 位于 error_annotation 内部（不是顶层）。
    """
    recent = []
    cutoff = since.isoformat()

    for l0_file in L0_DIR.glob("*.json"):
        try:
            data = json.loads(l0_file.read_text())
        except Exception as e:
            print(f"  [skip] {l0_file.name}: {e}")
            continue

        ann = data.get("error_annotation")
        if not ann:
            continue

        ann_time = ann.get("annotated_at")
        if not ann_time or ann_time < cutoff:
            continue

        session_id = data.get("session_id", l0_file.stem)

        for err in ann.get("prediction_errors", []):
            recent.append({
                "session_id":      session_id,
                "error_type":      err.get("error_type", "other"),
                "model_prediction": err.get("model_prediction", ""),
                "actual_outcome":  err.get("actual_outcome", ""),
                "is_recurring":    err.get("is_recurring", False),   # V4 prompt 新字段
                "timestamp":       ann_time,
                "severity":        err.get("severity", "medium"),
                "confidence":       err.get("confidence", 0.5),
            })

    return recent


def get_high_error_dispositions(since: datetime) -> List[Dict]:
    """
    从 l1_dispositions 表取过去 24h 内有 last_error_at 更新的记录，
    且 error_count >= 2（高频出错）。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    since_iso = since.isoformat()
    cursor.execute("""
        SELECT id, error_type, condition_text, prediction_text,
               error_count, success_count, last_error_at
        FROM l1_dispositions
        WHERE last_error_at >= ?
          AND is_active = 1
          AND scope = 'model_error'
        ORDER BY error_count DESC
    """, (since_iso,))

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


# ── 分析 ──────────────────────────────────────────────────────────────

def cluster_by_error_type(annotations: List[Dict]) -> Dict[str, List[Dict]]:
    clusters = defaultdict(list)
    for ann in annotations:
        clusters[ann["error_type"]].append(ann)
    return clusters


def build_summary(annotations: List[Dict],
                  dispositions: List[Dict]) -> tuple[str, str]:
    """
    返回 (recurring_section, disposition_section)
    """
    clusters = cluster_by_error_type(annotations)

    # ── Section 1: recurring patterns ──────────────────────────────
    recurring_lines = []
    recurring_lines.append("## 🔁 Recurring Error Patterns (last 24h)\n")

    sorted_types = sorted(clusters.items(),
                          key=lambda x: len(x[1]), reverse=True)

    has_patterns = False
    for error_type, items in sorted_types:
        count          = len(items)
        recurring_true = sum(1 for i in items if i.get("is_recurring"))
        if count >= 2 or recurring_true > 0:
            has_patterns = True
            recurring_lines.append(
                f"**`{error_type}`** — {count}次出现，{recurring_true}次标注为recurring"
            )
            for i, item in enumerate(items[:2]):
                pred = item["model_prediction"][:120]
                sev  = item["severity"]
                recurring_lines.append(
                    f"  - [{sev}] {pred}{'…' if len(item['model_prediction']) > 120 else ''}"
                )
            recurring_lines.append("")

    if not has_patterns:
        recurring_lines.append(
            "No recurring patterns detected in the last 24h.\n"
        )

    recurring_section = "\n".join(recurring_lines)

    # ── Section 2: dispositions ────────────────────────────────────
    disp_lines = []
    disp_lines.append("## ⚠️ Dispositions Needing Attention\n")

    high_error = [d for d in dispositions if d.get("error_count", 0) >= 2]

    if high_error:
        for disp in high_error[:5]:
            ec = disp["error_count"]
            sc = disp.get("success_count", 0)
            rate = ec / (ec + sc) if (ec + sc) > 0 else 0.0
            disp_lines.append(
                f"- **`{disp['error_type'] or 'unknown'}`** — "
                f"error={ec} success={sc} "
                f"(error_rate={rate:.1%})"
            )
            ct = (disp["condition_text"] or "")[:80]
            pt = (disp["prediction_text"] or "")[:80]
            if ct:
                disp_lines.append(f"  - Condition: {ct}")
            if pt:
                disp_lines.append(f"  - Prediction: {pt}")
            disp_lines.append("")
    else:
        disp_lines.append("No high-error dispositions in the last 24h.\n")

    disposition_section = "\n".join(disp_lines)

    return recurring_section, disposition_section


def generate_rules(clusters: Dict[str, List[Dict]]) -> str:
    """从高频 pattern 生成 one-liner watch rules。"""
    lines = []
    lines.append("## 📌 Watch Rules (auto-generated)\n")

    has_rules = False
    for error_type, items in sorted(clusters.items(),
                                    key=lambda x: len(x[1]), reverse=True):
        if len(items) >= 2:
            has_rules = True
            example = items[0]["model_prediction"][:100]
            lines.append(
                f"- When processing a task that might involve "
                f"`{error_type}`, double-check your assumption before acting. "
                f"Recent pattern: \"{example}…\""
            )

    if not has_rules:
        lines.append("No rules generated; continue normal operation.\n")

    return "\n".join(lines)


# ── 主流程 ────────────────────────────────────────────────────────────

def main():
    since = datetime.now() - timedelta(hours=LOOKBACK_HOURS)
    print(f"[daily_synthesis] lookback: {since.strftime('%Y-%m-%d %H:%M')}")

    # 1. collect
    annotations  = get_recent_annotations(since)
    dispositions = get_high_error_dispositions(since)

    print(f"  annotations: {len(annotations)} prediction errors")
    print(f"  dispositions: {len(dispositions)} updated records")

    if not annotations and not dispositions:
        print("  Nothing to synthesize, skipping.")
        return

    # 2. build sections
    recurring_sec,  disposition_sec = build_summary(annotations, dispositions)
    rules_sec                    = generate_rules(cluster_by_error_type(annotations))

    # 3. assemble
    today = datetime.now().strftime("%Y-%m-%d")
    output = f"""# Active Learnings — {today}

> Auto-generated by Hermem daily synthesis · {LOOKBACK_HOURS}h lookback

---

{recurring_sec}

---

{disposition_sec}

---

{rules_sec}

---

*This file is regenerated daily. Last run: {datetime.now().isoformat()}*
"""

    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(f"  Written → {OUTPUT_PATH}")

    # 4. quick summary to stdout
    total_errors = len(annotations)
    unique_types = len(set(a["error_type"] for a in annotations))
    print(f"  Summary: {total_errors} errors, {unique_types} types, "
          f"{len(dispositions)} disposition updates")


if __name__ == "__main__":
    main()
