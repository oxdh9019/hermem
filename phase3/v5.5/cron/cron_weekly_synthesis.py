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
# 路径冲突解决：
#   v5.5/impl/ 有 __init__.py（让 v5.5 模块成为 `impl` namespace package），
#   而 phase3/impl/ 也有 __init__.py（真正的 impl 包）。两者同时加进 sys.path
#   会让 Python 选 v5.5/impl 为 `impl` 包，屏蔽 phase3/impl，导致
#   active_forgetting 内部 `from impl.utils import ...` 失败
#   （错误：No module named 'impl.utils'）。
#
# 解决：只把 phase3/impl/ 加为 `impl` 命名空间；v5.5/impl/ 下的 4 个模块
# 改用 importlib 显式 import 到独立名字（v55_l4_reflection 等），避免命名冲突。
SCRIPT_DIR = Path(__file__).parent
IMPL_DIR = SCRIPT_DIR.parent / "impl"  # v5.5/impl/
PHASE3_DIR = SCRIPT_DIR.parent.parent  # phase3/（含 impl/）
sys.path.insert(0, str(PHASE3_DIR))   # → phase3/（让 `impl` 解析为 phase3/impl/）
# WORKDIR already set to phase3 by Hermes cron runner

# 显式 import v5.5 模块到独立名字（避开 `impl` namespace 冲突）
import importlib.util as _importlib_util
for _mod_name in ("llm_helper", "l4_reflection", "conflict_resolver", "active_forgetting"):
    _spec = _importlib_util.spec_from_file_location(
        f"v55_{_mod_name}",
        str(IMPL_DIR / f"{_mod_name}.py"),
    )
    if _spec is None or _spec.loader is None:
        raise ImportError(f"无法加载 v5.5 模块: {_mod_name}")
    _mod = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    globals()[f"v55_{_mod_name}"] = _mod

# 别名（保持向后兼容原有 `from impl.X import Y` 风格的内部引用）
sys.modules.setdefault("impl.llm_helper", v55_llm_helper)
sys.modules.setdefault("impl.l4_reflection", v55_l4_reflection)
sys.modules.setdefault("impl.conflict_resolver", v55_conflict_resolver)
sys.modules.setdefault("impl.active_forgetting", v55_active_forgetting)


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


def refresh_active_l4_ttls(extension_days: int = 14) -> int:
    """P2-12: 续期活跃 L4 reflections。

    当 cron 持续运行（即系统未停摆）时，每次合成把未过期 reflections 的
    expires_at 顺延 extension_days 天。这反映"用户行为模式仍稳定"的信号。
    副作用：cron 停跑超过 14 天后，未续期的 reflections 自动过期清理。

    Returns:
        续期数量
    """
    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    import sqlite3

    conn = sqlite3.connect(str(HERMEM_DB))
    try:
        cur = conn.execute(
            "UPDATE l4_reflections "
            "SET expires_at = julianday('now', '+' || CAST(? AS TEXT) || ' days') "
            "WHERE expires_at IS NULL OR expires_at >= julianday('now')",
            (extension_days,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main():
    # 先清理过期 L4 reflections
    cleaned = cleanup_expired_l4()
    print(f"[V5.5 Weekly Synthesis] 清理了 {cleaned} 条过期 L4 reflections")

    # P2-12: 续期活跃 reflections（即使没新数据也保留旧的）
    refreshed = refresh_active_l4_ttls()
    print(f"[V5.5 Weekly Synthesis] 续期了 {refreshed} 条活跃 L4 reflections")

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
