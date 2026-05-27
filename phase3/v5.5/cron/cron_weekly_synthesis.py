#!/usr/bin/env python3
"""
Hermem V5.5 - 每周综合归纳 Cron Job

schedule: 每周日 02:30

同时输入：
1. 近 7 天 prediction_errors → error_patterns（用户犯错模式）
2. 高频召回的 L1 facts（usage_count > 5, 7天内）→ user_preferences

输出：
- 元记忆描述 → l4_reflections 表
- 用户画像 → user_profile.md
- 低置信 disposition 归档

Usage:
    python3 phase3/v5.5/cron/cron_weekly_synthesis.py
"""

import sys
from pathlib import Path

# ── 路径设置 ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
IMPL_DIR = SCRIPT_DIR.parent / "impl"
sys.path.insert(0, str(IMPL_DIR.parent))


# ── 主函数 ─────────────────────────────────────────────────────────────────────


def get_weekly_data():
    """获取本周数据：errors + 高频 facts"""
    from impl.llm_helper import call_llm_with_fallback

    # 数据库路径
    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"

    import sqlite3

    # 7 天内的 errors（hermem.db）
    conn_h = sqlite3.connect(str(HERMEM_DB))
    conn_h.row_factory = sqlite3.Row
    try:
        errors = conn_h.execute("""
            SELECT context, error_type, surprise_level
            FROM prediction_errors
            WHERE created_at >= julianday('now', '-7 days')
            ORDER BY surprise_level DESC
            LIMIT 30
        """).fetchall()
        errors = [dict(e) for e in errors]
    finally:
        conn_h.close()

    # 高频 facts（l0_l3.db）
    conn_l = sqlite3.connect(str(L0L3_DB))
    conn_l.row_factory = sqlite3.Row
    try:
        facts = conn_l.execute("""
            SELECT id, content, usage_count
            FROM l1_facts
            WHERE status = 'active'
              AND usage_count > 5
              AND last_used_at >= julianday('now', '-7 days')
            ORDER BY usage_count DESC
            LIMIT 20
        """).fetchall()
        facts = [dict(f) for f in facts]
    finally:
        conn_l.close()

    return errors, facts


def synthesize_weekly():
    """综合归纳：errors → 元记忆；facts → 用户画像；demotion → 归档"""
    from impl.llm_helper import call_llm_with_fallback

    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
    USER_PROFILE_PATH = Path.home() / ".hermes" / "memory" / "user_profile.md"

    import sqlite3

    result = {}

    # ── Part 1: L4 反思（元记忆）─────────────────────────────────────────────
    errors, facts = get_weekly_data()

    if len(errors) >= 3:
        error_text = "\n".join(
            [
                f"- [{e['surprise_level']:.2f}] {e['error_type']}: {e['context'][:80]}"
                for e in errors
            ]
        )
        prompt = f"""从以下预测错误中归纳用户交互模式的元记忆。不超过150字，直接描述。

错误记录：
{error_text}

元记忆（不超过150字）："""

        reflection = call_llm_with_fallback(prompt, max_tokens=200)
        if reflection:
            conn_h = sqlite3.connect(str(HERMEM_DB))
            conn_h.row_factory = sqlite3.Row
            try:
                confidence = min(len(errors) / 50, 1.0)
                conn_h.execute(
                    """
                    INSERT INTO l4_reflections (reflection_text, source_errors, confidence, expires_at)
                    VALUES (?, ?, ?, julianday('now', '+14 days'))
                """,
                    (reflection[:150], len(errors), confidence),
                )
                conn_h.commit()
                result["reflection"] = reflection[:80]
            finally:
                conn_h.close()
    else:
        result["reflection"] = None

    # ── Part 2: 睡眠巩固（用户画像）─────────────────────────────────────────
    if facts:
        fact_text = "\n".join([f"- {f['content']}" for f in facts])
        prompt = f"""从以下高频事实归纳用户偏好。不超过80字，直接描述。

高频事实：
{fact_text}

用户画像（不超过80字）："""

        profile = call_llm_with_fallback(prompt, max_tokens=150)
        if profile:
            profile_text = profile.strip()[:80]
            try:
                USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(USER_PROFILE_PATH, "a", encoding="utf-8") as fh:
                    fh.write(f"\n---\n{profile_text}\n")
            except Exception:
                pass

            # 标记已提升
            conn_l = sqlite3.connect(str(L0L3_DB))
            try:
                ids = [f["id"] for f in facts]
                placeholders = ",".join(["?"] * len(ids))
                conn_l.execute(
                    f"UPDATE l1_facts SET status = 'promoted' WHERE id IN ({placeholders})", ids
                )
                conn_l.commit()
            finally:
                conn_l.close()
            result["profile"] = profile_text

    # ── Part 3: 主动降级 ────────────────────────────────────────────────────
    conn_l = sqlite3.connect(str(L0L3_DB))
    conn_l.row_factory = sqlite3.Row
    try:
        rows = conn_l.execute("""
            SELECT id FROM l1_dispositions
            WHERE is_active = 1
              AND confidence < 0.6
              AND (last_used_at IS NULL OR last_used_at < julianday('now', '-30 days'))
        """).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join(["?"] * len(ids))
            conn_l.execute(
                f"UPDATE l1_dispositions SET is_active = 0 WHERE id IN ({placeholders})", ids
            )
            conn_l.commit()
            result["demotion"] = len(ids)
        else:
            result["demotion"] = 0
    finally:
        conn_l.close()

    return result


def main():
    print("[V5.5 Weekly Synthesis] 开始执行...")
    errors, facts = get_weekly_data()
    print(f"本周: {len(errors)} 条 errors, {len(facts)} 条高频 facts")

    result = synthesize_weekly()
    print(f"综合归纳结果: {result}")
    return result


if __name__ == "__main__":
    main()
