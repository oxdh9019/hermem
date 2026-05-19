#!/usr/bin/env python3
"""
OpenClaw 会话导入器 → Hermem V4.2 Disposition 评估

功能：
1. 扫描 ~/.openclaw/sessions_archive/{agent}/ 下的所有 .jsonl 会话文件
2. 解析 NDJSON，还原对话文本（过滤 compaction/system 噪声）
3. 识别 Oliver 的真实指令（user role 中以 "Oliver:" 开头的消息）
4. 对每个会话生成摘要并提取 disposition
5. 结果写入 l1_dispositions 表

用法：
    python3 openclaw_import.py                    # 全量导入（所有 agent）
    python3 openclaw_import.py --agent main       # 只导入 main agent
    python3 openclaw_import.py --agent main --dry  # dry run（不写 DB）
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 项目路径 ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "phase3"))

from impl.l1_extract import extract_dispositions
from impl.utils import get_embedding, serialize_vec
from impl.config import DB_PATH

# ── 常量 ─────────────────────────────────────────────────────────────────────
OPENCLAW_ARCHIVE = Path.home() / ".openclaw" / "sessions_archive"
AGENTS = ["main", "writer", "tech", "research", "editor", "ark", "others", "default"]

# 排除的 agent（不处理）
EXCLUDE_AGENTS = {"default", "volcengine-plan-ark-code-latest"}

# ── 解析器 ───────────────────────────────────────────────────────────────────

def extract_text_from_content(content) -> str:
    """从 message.content 列表中提取纯文本。"""
    if isinstance(content, list):
        return " ".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return str(content) if content else ""


def parse_session_file(path: Path) -> Optional[dict]:
    """
    解析一个 .jsonl session 文件。
    返回 dict: {
        'session_id': str,
        'agent': str,
        'started_at': str (ISO),
        'messages': [(role, text, timestamp), ...],
        'oliver_messages': [(role, text, timestamp), ...],  # Oliver 发的消息
    }
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        return None

    # session 元数据
    session_event = next((e for e in events if e.get("type") == "session"), None)
    if not session_event:
        return None

    session_id = session_event.get("id", path.stem)
    timestamp = session_event.get("timestamp", "")

    messages = []
    oliver_messages = []

    for evt in events:
        if evt.get("type") != "message":
            continue

        msg = evt.get("message", {})
        role = msg.get("role", "unknown")
        content = extract_text_from_content(msg.get("content", []))
        evt_timestamp = msg.get("timestamp", evt.get("timestamp", ""))

        if not content or not content.strip():
            continue

        # 过滤系统噪音
        if role == "system":
            # 跳过 OpenClaw 内部系统消息（compaction 等）
            if re.match(r"\[.*?\] Compacted", content.strip()):
                continue
            if content.startswith("OpenClaw runtime context"):
                continue
            if content.startswith("[Internal task completion"):
                continue
            if "Subagent Context" in content:
                # 这是 subagent 的启动上下文，不是给 Oliver 的消息，过滤
                continue
            # 保留其他 system 消息（如角色设定等）
            messages.append((role, content, evt_timestamp))
            continue

        # user 消息：需要识别是 Oliver 本人还是 subagent
        if role == "user":
            is_oliver = False

            # 排除：subagent 任务完成报告
            if re.search(r"^source:\s*subagent\n", content.strip()):
                continue
            if "[Internal task completion" in content:
                continue
            if content.strip().startswith("source:") and "session_key:" in content:
                continue

            # 方法1：content 中包含 "Oliver:" 前缀（Feishu 消息）
            if re.search(r"\n?(?:\[message_id:[^\]]*\] )?Oliver:", content):
                is_oliver = True
            # 方法2：content 中以 "[Wed 2026-04-01 12:41 GMT+8]" 时间戳开头，且无 "Subagent Context"
            if re.match(r"\[[A-Z][a-z]{2} \d{4}-\d{2}-\d{2}", content):
                if "Subagent Context" not in content:
                    is_oliver = True
            # 方法3：sender metadata 中包含 Oliver 的邮箱或 ID
            if re.search(r'"sender":\s*"oxdh99@gmail\.com"', content):
                is_oliver = True
            if re.search(r'"sender_id":\s*"oxdh99@gmail\.com"', content):
                is_oliver = True
            if re.search(r'"sender_id":\s*"ou_711078041d95c6e79bdc67eb19e5d812"', content):
                is_oliver = True
            if re.search(r'"label":\s*"Oliver', content):
                is_oliver = True
            # 方法4：content 直接是 Oliver 的指令（无特殊前缀）
            if is_oliver:
                # 去掉 JSON metadata wrapper，还原真实消息
                cleaned = clean_oliver_message(content)
                if cleaned.strip():
                    oliver_messages.append((role, cleaned, evt_timestamp))
                    messages.append((role, cleaned, evt_timestamp))
            else:
                # subagent 之间的通信，保留但不计入 Oliver 消息
                messages.append((role, content[:500], evt_timestamp))
            continue

        # assistant 消息
        if role == "assistant":
            messages.append((role, content, evt_timestamp))
            continue

        # 其他角色（如 tool）
        messages.append((role, content[:500], evt_timestamp))

    return {
        "session_id": session_id,
        "agent": path.parent.name,
        "started_at": timestamp,
        "messages": messages,
        "oliver_messages": oliver_messages,
    }


def clean_oliver_message(content: str) -> str:
    """
    清理 Oliver 消息中的 JSON metadata wrapper，
    提取真实消息内容。
    """
    # 去掉开头的 JSON metadata 块（Conversation info ... Sender ...）
    # 模式：开头有 [xxx] Oliver: 或 [message_id: ...] Oliver:
    # 先找 "Oliver:" 的位置
    match = re.search(r"\n?(?:\[message_id:[^\]]*\] )?Oliver:", content)
    if match:
        return content[match.end():].strip()

    # email/oxdh99 格式：Conversation info (untrusted metadata) {...} Sender (untrusted metadata) {...}
    # 结构：4个 fence，第四个 ``` 之后才是正文
    # fences[0]=第一个开, [1]=第一个闭, [2]=第二个开, [3]=第二个闭 → 正文在 [3]+3 之后
    fences = [m.start() for m in re.finditer(r"```", content)]
    if len(fences) >= 4:
        return content[fences[3] + 3:].strip()

    # 去掉 [Thu 2026-04-02 18:19 GMT+8] OpenClaw runtime context ... 部分
    if "OpenClaw runtime context" in content:
        # 找 [Internal task completion] 之后的内容
        m = re.search(r"\[Internal task completion[^\]]*\](.*)", content, re.DOTALL)
        if m:
            return m.group(1).strip()

    return content.strip()


def build_conversation_text(session_data: dict, max_len: int = 4000) -> str:
    """
    将 messages 列表还原为对话格式字符串。
    """
    parts = []
    for role, text, _ in session_data["messages"]:
        prefix = "Oliver" if role == "user" else "Assistant"
        # 截断过长的消息
        if len(text) > 1500:
            text = text[:1500] + "[截断]"
        parts.append(f"[{prefix}] {text}")

    text = "\n".join(parts)
    if len(text) > max_len:
        text = text[:max_len] + f"\n[...截断，共 {len(session_data['messages'])} 条消息...]"
    return text


def build_summary(conversation: str, oliver_messages: list = None) -> str:
    """
    从对话生成 session summary。

    重点：识别 Oliver 的行为模式、偏好、决策方式，
    而非只总结任务内容。
    """
    from impl.utils import llm_generate

    # 如果有 Oliver 原始消息，单独拼接这部分（行为偏好最重要）
    oliver_text = ""
    if oliver_messages:
        oliver_text = "\n".join(
            f"- {t[:300]}" for _, t, _ in oliver_messages
        )

    prompt = f"""从以下对话中提取 Oliver 的行为模式。不是任务内容，而是他如何提要求、如何做决策的风格特征。

**格式要求**：输出一行 Oliver 的行为特征描述（30-60字），直接描述行为，不描述任务。

**示例**：
- 输入："Oliver说不行，必须用writer agent" → 输出："Oliver 禁止使用 subagent，坚持指定特定 agent 执行任务"
- 输入："Oliver多次催促进度，说'继续'" → 输出："Oliver 用简短指令（继续）催促，不给额外解释"
- 输入："Oliver要求先确认文件位置再开始" → 输出："Oliver 要求确认环境和资源就绪后再执行"

---
Oliver 原始消息：
{oliver_text or '(无)'}

对话摘要：
{conversation[:3000]}"""
    try:
        return llm_generate(prompt, temperature=0.3, max_tokens=500)
    except Exception as e:
        return f"[摘要生成失败: {e}]"


# ── 数据库操作 ───────────────────────────────────────────────────────────────

def disposition_exists(conn: sqlite3.Connection, session_id: str, agent: str) -> bool:
    """检查该 session 是否已有 disposition。"""
    row = conn.execute(
        "SELECT 1 FROM l1_dispositions WHERE l0_ref = ? AND source_agent = ? LIMIT 1",
        (session_id, agent)
    ).fetchone()
    return row is not None


def save_dispositions(
    session_id: str,
    agent: str,
    dispositions: list[dict],
    conn: sqlite3.Connection
) -> int:
    """将 disposition 列表写入 l1_dispositions 表。返回写入条数。"""
    saved = 0
    now = datetime.now().isoformat()
    for d in dispositions:
        try:
            cond_emb = get_embedding(d["condition"])
            pred_emb = get_embedding(d["prediction"])
        except Exception as e:
            print(f"    [!] embedding 失败: {e}")
            continue

        disp_id = f"disp_oc_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(session_id) % 100000:05d}_{saved}"
        conn.execute("""
            INSERT INTO l1_dispositions
            (id, l0_ref, condition_text, prediction_text,
             condition_embedding, prediction_embedding,
             confidence, source_agent, source_session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            disp_id,
            session_id,
            d["condition"],
            d["prediction"],
            serialize_vec(cond_emb.tolist()),
            serialize_vec(pred_emb.tolist()),
            d.get("confidence", 1.0),
            agent,
            session_id,
            now,
        ))
        saved += 1
    return saved


# ── 主流程 ───────────────────────────────────────────────────────────────────

def process_agent(agent: str, dry_run: bool = False, batch_size: int = 5) -> dict:
    """处理单个 agent 的所有 session。返回统计。"""
    agent_dir = OPENCLAW_ARCHIVE / agent
    if not agent_dir.exists():
        return {"agent": agent, "skipped": 0, "processed": 0, "saved": 0, "errors": 0}

    session_files = sorted(
        agent_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_size,
        reverse=True
    )

    # 过滤掉 reset/deleted 等备份文件
    session_files = [
        f for f in session_files
        if not any(x in f.name for x in [".reset.", ".deleted.", ".bak."])
    ]

    stats = {"agent": agent, "total": len(session_files),
             "skipped": 0, "processed": 0, "saved": 0, "errors": 0}

    if dry_run:
        print(f"[{agent}] Dry run: 发现 {len(session_files)} 个会话")
        for f in session_files[:10]:
            print(f"  {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        return stats

    conn = sqlite3.connect(DB_PATH)

    # 取需要处理的 session（排除已处理的）
    pending = [f for f in session_files if not disposition_exists(conn, f.stem, agent)]
    stats["skipped"] = len(session_files) - len(pending)

    print(f"[{agent}] 总 {len(session_files)} 个会话，"
          f"{stats['skipped']} 个已处理，{len(pending)} 个待处理（本次最多 {batch_size}）")

    pending = pending[:batch_size]

    for f in pending:
        print(f"  → {f.name} ({f.stat().st_size / 1024:.1f} KB)", end=" ", flush=True)

        try:
            session_data = parse_session_file(f)
        except Exception as e:
            print(f"[解析错误: {e}]")
            stats["errors"] += 1
            continue

        if not session_data:
            print("[空会话]")
            stats["errors"] += 1
            continue

        if len(session_data["oliver_messages"]) < 2:
            print(f"[Oliver消息不足: {len(session_data['oliver_messages'])} 条，跳过]")
            stats["skipped"] += 1
            continue

        try:
            conversation = build_conversation_text(session_data)

            # Oliver 消息 >= 15 条时，直接用原始消息（不经过摘要）
            # 原因：摘要会丢失行为特异性，大段任务描述混入导致 LLM 无法聚焦
            if len(session_data["oliver_messages"]) >= 15:
                summary = "\n".join(
                    f"- {t[:300]}" for _, t, _ in session_data["oliver_messages"]
                )
            else:
                summary = build_summary(conversation, session_data["oliver_messages"])

            dispositions = extract_dispositions(summary, None)
        except Exception as e:
            print(f"[处理错误: {e}]")
            stats["errors"] += 1
            continue

        if not dispositions:
            print(f"[无 disposition，Oliver消息{len(session_data['oliver_messages'])}条]")
            stats["processed"] += 1
            continue

        saved = save_dispositions(f.stem, agent, dispositions, conn)
        conn.commit()
        print(f"✓ {saved} 条 / Oliver消息{len(session_data['oliver_messages'])}条")

        stats["processed"] += 1
        stats["saved"] += saved

    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 会话 → Hermem V4.2 Disposition")
    parser.add_argument("--agent", "-a", choices=AGENTS, default=None,
                        help="只处理指定 agent（默认全部）")
    parser.add_argument("--dry", action="store_true",
                        help="Dry run，不写 DB，只扫描文件")
    parser.add_argument("--batch", "-b", type=int, default=5,
                        help="每次处理的 session 数量（默认 5）")
    args = parser.parse_args()

    agents = [args.agent] if args.agent else AGENTS
    agents = [a for a in agents if a not in EXCLUDE_AGENTS]

    print(f"=== OpenClaw → Hermem V4.2 Import ===")
    print(f"Agent: {agents if args.agent else '全部'}")
    print(f"Dry: {args.dry}")
    print(f"Batch: {args.batch}/agent")
    print()

    total_saved = 0
    total_processed = 0
    total_errors = 0

    for agent in agents:
        stats = process_agent(agent, dry_run=args.dry, batch_size=args.batch)
        total_saved += stats["saved"]
        total_processed += stats["processed"]
        total_errors += stats["errors"]

    print()
    print(f"=== 完成 ===")
    print(f"处理: {total_processed} 个会话")
    print(f"写入: {total_saved} 条 dispositions")
    print(f"错误: {total_errors}")

    if not args.dry:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM l1_dispositions").fetchone()[0]
        print(f"l1_dispositions 表共 {total} 条")
        conn.close()


if __name__ == "__main__":
    main()
