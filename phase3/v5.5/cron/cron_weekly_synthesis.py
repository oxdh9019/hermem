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
IMPL_DIR = SCRIPT_DIR.parent / "impl"  # v5.5/impl/
sys.path.insert(0, str(IMPL_DIR))  # → v5.5/impl/（含 llm_helper, active_forgetting）
sys.path.insert(0, str(IMPL_DIR.parent))  # → v5.5/（兼容 phase3/impl 等）
# WORKDIR already set to phase3 by Hermes cron runner


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
    """综合归纳：errors → 元记忆；facts → 用户画像；demotion → 归档

    注意：sleep consolidation 和 active demotion 已迁移到 active_forgetting.py。
    本函数仅处理 L4 reflection（Part 1），其他两部分由 active_forgetting.run_consolidation() 接管。
    """
    from impl.llm_helper import call_llm_with_fallback

    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"

    result = {}

    # ── Part 1: L4 反思（元记忆）─────────────────────────────────────────────
    errors, _ = get_weekly_data()

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
            import sqlite3

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

    return result


def cleanup_expired_l4() -> int:
    """删除已过期的 l4_reflections 记录。返回删除数量。"""
    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    import sqlite3

    conn = sqlite3.connect(str(HERMEM_DB))
    try:
        cur = conn.execute(
            "DELETE FROM l4_reflections WHERE expires_at IS NOT NULL AND expires_at < julianday('now')"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main():
    # 先清理过期 L4 reflections
    cleaned = cleanup_expired_l4()
    print(f"[V5.5 Weekly Synthesis] 清理了 {cleaned} 条过期 L4 reflections")

    print("[V5.5 Weekly Synthesis] 开始执行...")

    # Part 1: L4 reflection
    result = synthesize_weekly()
    print(f"L4 reflection 结果: {result}")

    # Part 2 & 3: sleep consolidation + active demotion（由 active_forgetting 模块处理）
    try:
        from impl.active_forgetting import run_consolidation

        consolidation = run_consolidation()
        print(f"Sleep consolidation: {consolidation['sleep']}")
        print(f"Active demotion: {consolidation['demotion']}")
        result.update(consolidation)
    except Exception as e:
        print(f"[V5.5 Weekly Synthesis] active_forgetting 调用失败: {e}")

    print(f"综合归纳最终结果: {result}")
    return result


if __name__ == "__main__":
    main()
