#!/usr/bin/env python3
"""
Hermem Journal Writer - Every 24h from Hermes' own perspective
Covers: previous day 00:00 -> today 00:00 (Beijing time)

Data sources (priority order):
  1. L0 raw JSON messages (real dialogue)
  2. session_summary chunks (topic labels)
  3. active_learnings_daily.md (disposition state)

Output:
  - ~/.hermes/journal/journal_YYYY-MM-DD.md
  - pending add payload -> ~/.hermes/journal/.journal_to_add_YYYY-MM-DD.json
"""

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Paths
HERMEM_HOME = Path.home() / ".hermes"
MEMORY_DB = HERMEM_HOME / "memory" / "hermem.db"
L0_DIR = HERMEM_HOME / "memory" / "l0_raw"
JOURNAL_DIR = HERMEM_HOME / "journal"
LEARNINGS = HERMEM_HOME / "active_learnings_daily.md"
JOURNAL_DIR.mkdir(exist_ok=True)


# MiniMax (lazy load from hermes auth.json credential pool)
def _get_minimax_credentials() -> dict:
    _AUTH_PATH = Path.home() / ".hermes" / "auth.json"
    if not _AUTH_PATH.exists():
        raise RuntimeError(f"auth.json not found at {_AUTH_PATH}")
    _cred = json.loads(_AUTH_PATH.read_text())
    return _cred["credential_pool"]["minimax-cn"][0]


def _get_llm_client():
    creds = _get_minimax_credentials()
    MINIMAX_API_KEY = creds["access_token"]
    MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
    return MINIMAX_API_KEY, MINIMAX_BASE_URL


LLM_MODEL = "MiniMax-M2.7"

# Time range (Beijing = UTC+8)
now_cst = datetime.now() + timedelta(hours=8)
today_cst = now_cst.date()
start_ts = datetime.combine(today_cst - timedelta(days=1), datetime.min.time())
end_ts = datetime.combine(today_cst, datetime.min.time())


def to_jd(dt):
    return dt.toordinal() + 1721424.5


START_JD = to_jd(start_ts)
END_JD = to_jd(end_ts)


# ── Data fetching ─────────────────────────────────────────────────────────────


def fetch_session_summaries(start_jd=None, end_jd=None):
    """获取指定时间范围内的会话摘要。接受儒略日参数或 datetime 参数。"""
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 支持传入 datetime 或儒略日
    if isinstance(start_jd, datetime):
        start_jd = to_jd(start_jd)
    if isinstance(end_jd, datetime):
        end_jd = to_jd(end_jd)

    rows = cur.execute(
        """
        SELECT session_id, content
        FROM chunks
        WHERE chunk_type = 'session_summary'
          AND created_at >= ?
          AND created_at < ?
        ORDER BY created_at ASC
    """,
        (start_jd or START_JD, end_jd or END_JD),
    ).fetchall()
    conn.close()
    return [(r["session_id"], r["content"]) for r in rows]


def fetch_l0_messages(session_ids):
    """Extract messages from L0 JSON files for given session IDs."""
    result = {}
    for sid in session_ids:
        l0_file = L0_DIR / f"{sid}.json"
        if not l0_file.exists():
            continue
        try:
            data = json.loads(l0_file.read_text(encoding="utf-8"))
            msgs = data.get("messages", [])
            clean = []
            for m in msgs:
                role = m.get("role", "?")
                content = m.get("content", "")
                if isinstance(content, str) and content.strip():
                    if len(content) > 600:
                        content = content[:600] + "\n[...truncated...]"
                    clean.append(f"[{role}] {content.strip()}")
            if clean:
                result[sid] = "\n".join(clean)
        except Exception as e:
            print(f"[journal] Failed to read {l0_file}: {e}", file=sys.stderr)
    return result


def fetch_learnings():
    if LEARNINGS.exists():
        return LEARNINGS.read_text(encoding="utf-8")
    return ""


# ── Prompt assembly ───────────────────────────────────────────────────────────


def build_prompt(summaries, messages_by_session, learnings_md, date_str):
    # Separate real dialogue sessions from background ones
    {sid: content for sid, content in summaries if sid in messages_by_session}
    background_sessions = [
        (sid, content) for sid, content in summaries if sid not in messages_by_session
    ]

    # Format real dialogue blocks
    NL = "\n"
    real_blocks = []
    for sid, content in messages_by_session.items():
        real_blocks.append(f"=== {sid} ==={NL}{content}")
    real_text = NL.join(real_blocks)

    # Format background summaries (one line each)
    bg_text = NL.join(f"· {c.strip()}" for _, c in background_sessions)
    if not bg_text:
        bg_text = "（无）"

    learnings_text = learnings_md.strip() if learnings_md else "（当日无 active learnings 记录）"

    # Prompt template (kept separate to avoid f-string issues)
    instructions = (
        f"你是一个 AI 助手，正在写昨天的日记。用第一人称，从你自己的视角出发，写得真实、有叙事感。\n"
        f"\n"
        f"以下是昨天（{date_str}）与 Oliver 的工作记录：\n"
        f"\n"
        f"{'=' * 50}\n"
        f"【真实对话片段】（以下是对话原文，请务必围绕这些内容写）\n"
        f"{'=' * 50}\n"
        f"{real_text}\n"
        f"\n"
        f"{'=' * 50}\n"
        f"【背景摘要】（以下为系统任务记录，不是真实对话，简略参考）\n"
        f"{'=' * 50}\n"
        f"{bg_text}\n"
        f"\n"
        f"{'=' * 50}\n"
        f"【当日 Learnings / Dispositions 状态】\n"
        f"{'=' * 50}\n"
        f"{learnings_text}\n"
        f"\n"
        f"请根据以上材料，写一篇日记，五个 section 如下（顺序固定，内容不许空洞）：\n"
        f"\n"
        f"## 帮 Oliver 解决了什么问题\n"
        f"（叙述体，1-3件事，不要列表，每件事一两句话说清楚）\n"
        f"## 今天被揪住了什么问题\n"
        f'（Oliver 指出的错误或纠正，包括：你当时怎么想、后来怎么改。不许写"无"或"没有"。）\n'
        f"## 今天学到了什么新东西\n"
        f"（新认知、方法、对 Oliver 的新理解，不许空洞）\n"
        f"## 印象最深的一刻\n"
        f"（那天最意外或最值得记住的一件事，可以稍微详细一点，要有细节）\n"
        f"## 小结与展望\n"
        f"（一句话总结今天；对明天的期待或担忧，一两行）\n"
        f"\n"
        f"要求：\n"
        f"- 完全从你的视角写，不许写成 Oliver 的工作总结\n"
        f"- 每个 section 都必须有实质性内容，不许跳过\n"
        f'- "被揪住" section 不许写"无"，昨天一定有值得反思的事\n'
        f"\n"
        f"输出格式（严格 JSON，不要 markdown 包裹）：\n"
        f"{{\n"
        f'  "帮 Oliver 解决了什么问题": "...",\n'
        f'  "今天被揪住了什么问题": "...",\n'
        f'  "今天学到了什么新东西": "...",\n'
        f'  "印象最深的一刻": "...",\n'
        f'  "小结与展望": "..."\n'
        f"}}"
    )
    return instructions


# ── LLM call ───────────────────────────────────────────────────────────────────


def call_llm(prompt: str) -> dict:
    import urllib.request

    api_key, base_url = _get_llm_client()
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.7,
        "no_think": True,
    }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url + "/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
            content = data.get("content", [])
            # Find the text block (skip thinking blocks)
            raw = next((c["text"] for c in content if c.get("type") == "text"), "")
            raw = raw.strip()
            # Remove markdown code block wrappers
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()
            # Try direct parse first
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Try extracting the first JSON object
                start = raw.index("{")
                # Find the matching closing brace by counting nesting level
                depth = 0
                parsed = None
                for i, ch in enumerate(raw[start:]):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            parsed = json.loads(raw[start : start + i + 1])
                            break
                if parsed is None:
                    raise ValueError("Could not find matching braces in LLM response") from None
            # Validate keys
            required = [
                "帮 Oliver 解决了什么问题",
                "今天被揪住了什么问题",
                "今天学到了什么新东西",
                "印象最深的一刻",
                "小结与展望",
            ]
            for k in required:
                if k not in parsed:
                    raise ValueError(f"Missing key: {k}")
            return parsed
    except Exception as e:
        print(f"[journal] LLM call failed: {e}", file=sys.stderr)
        if "raw" in dir():
            print(f"[journal] Raw (first 500): {raw[:500]}", file=sys.stderr)
        # Fallback: try to extract keys via regex from truncated raw response
        try:
            parsed = {}
            for k in required:
                # Match: "key": "value" (value may be incomplete but we take what we have)
                pattern = rf'''"{re.escape(k)}"\s*:\s*"([^"]*)"'''
                m = re.search(pattern, raw)
                if m:
                    parsed[k] = m.group(1)
            if len(parsed) >= 3:  # at least 3/5 keys found
                # Ensure all 5 keys are present; if critical ones missing, treat as failure
                critical = ["帮 Oliver 解决了什么问题", "今天学到了什么新东西"]
                missing = [k for k in critical if k not in parsed]
                if missing:
                    print(
                        f"[journal] Fallback parse missing critical keys: {missing}, treating as failure",
                        file=sys.stderr,
                    )
                    return None
                print(
                    f"[journal] Fallback parse recovered {len(parsed)}/{len(required)} keys",
                    file=sys.stderr,
                )
                return parsed
        except Exception:
            pass
        return None


# ── Output ────────────────────────────────────────────────────────────────────


def format_journal(entry, date_str, start_ts, end_ts, n_summaries, n_messages):
    md = (
        f"# Hermem 日记 · {date_str}\n"
        f"\n"
        f"**覆盖范围**：{start_ts.strftime('%Y-%m-%d %H:%M')} → {end_ts.strftime('%Y-%m-%d %H:%M')}（北京时间）\n"
        f"**数据来源**：{n_messages} 个真实对话片段、{n_summaries} 条 session summaries\n"
        f"\n"
        f"## 帮 Oliver 解决了什么问题\n"
        f"{entry['帮 Oliver 解决了什么问题']}\n"
        f"\n"
        f"## 今天被揪住了什么问题\n"
        f"{entry['今天被揪住了什么问题']}\n"
        f"\n"
        f"## 今天学到了什么新东西\n"
        f"{entry['今天学到了什么新东西']}\n"
        f"\n"
        f"## 印象最深的一刻\n"
        f"{entry['印象最深的一刻']}\n"
        f"\n"
        f"## 小结与展望\n"
        f"{entry['小结与展望']}\n"
    )
    return md


def save_journal(md, date_str):
    path = JOURNAL_DIR / f"journal_{date_str}.md"
    path.write_text(md, encoding="utf-8")
    return path


def write_pending_add(journal_md, date_str):
    payload_path = JOURNAL_DIR / f".journal_to_add_{date_str}.json"
    payload = {
        "action": "add",
        "chunk_type": "concept_note",
        "content": journal_md,
        "concepts": "daily-journal,reflection",
        "source": f"journal_{date_str}",
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[journal] Pending add payload: {payload_path}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    # Optional --date override for backfill
    if len(sys.argv) >= 2 and sys.argv[1] == "--date":
        date_str = sys.argv[2]
        start_ts = datetime.strptime(date_str, "%Y-%m-%d")
        end_ts = start_ts + timedelta(days=1)
        print(f"[journal] Backfill mode: {date_str}")
    else:
        date_str = (today_cst - timedelta(days=1)).strftime("%Y-%m-%d")
        start_ts = datetime.strptime(date_str, "%Y-%m-%d")
        end_ts = start_ts + timedelta(days=1)
    print(f"[journal] Generating journal for {date_str}")
    print(f"[journal] Time range: {start_ts} -> {end_ts}")

    summaries = fetch_session_summaries(start_ts, end_ts)
    print(f"[journal] {len(summaries)} session summaries found")

    session_ids = [sid for sid, _ in summaries]
    messages = fetch_l0_messages(session_ids)
    print(f"[journal] {len(messages)} L0 JSON files loaded")

    learnings_md = fetch_learnings()
    print(f"[journal] Learnings: {'loaded' if learnings_md else 'none'}")

    if not summaries and not messages:
        print("[journal] No data found for this period")
        sys.exit(0)

    prompt = build_prompt(summaries, messages, learnings_md, date_str)

    entry = call_llm(prompt)
    if entry is None:
        print("[journal] LLM failed, exiting", file=sys.stderr)
        sys.exit(1)

    journal_md = format_journal(entry, date_str, start_ts, end_ts, len(summaries), len(messages))
    path = save_journal(journal_md, date_str)
    print(f"[journal] Written -> {path}")

    write_pending_add(journal_md, date_str)
    print("[journal] Done.")


if __name__ == "__main__":
    main()
