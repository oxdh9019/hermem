#!/usr/bin/env python3
"""
Hermem 批量回填脚本
一次性为所有历史会话生成摘要 + 语义索引

用法: python batch_backfill.py [--dry-run]

依赖: 需要 ollama 包和 Hermem impl 模块
建议用 hermes-agent venv 运行:
  /Users/oliver/.hermes/hermes-agent/venv/bin/python3 batch_backfill.py
"""

import glob
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加 impl 到路径（需要在 "impl" 父目录才能用相对导入）
sys.path.insert(0, str(Path(__file__).parent))

from database import insert_chunk
from embedding import get_embedding_cached
from vectorstore import append_vectors

HERMES_SESSIONS = Path.home() / ".hermes" / "sessions"
MEMORY_DB = Path.home() / ".hermes" / "memory" / "hermem.db"

# Ollama 模型配置
OLLAMA_API = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL = "minimax-cn/MiniMax-M2.7"


def load_sessions():
    """加载所有非 cron 的会话文件"""
    files = sorted(glob.glob(str(HERMES_SESSIONS / "session_*.json")))
    sessions = []
    for f in files:
        if "cron" in f:
            continue
        with open(f) as fp:
            data = json.load(fp)
        sid = data.get("session_id", Path(f).stem)
        # 解析时间戳
        ts = data.get("started_at", 0)
        dt = datetime.fromtimestamp(ts) if ts else None
        sessions.append(
            {
                "path": f,
                "session_id": sid,
                "started_at": dt,
                "data": data,
            }
        )
    return sessions


def extract_text(data):
    """从 session JSON 中提取用户+助手的纯文本内容"""
    messages = data.get("messages", [])
    parts = []
    for m in messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
        if content:
            parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)


def summarize_session(text: str, session_id: str) -> str:
    """调用 Ollama 生成会话摘要"""
    import urllib.error
    import urllib.request

    prompt = f"""请为以下对话会话生成一段简洁的摘要（200字以内），包含：
1. 主要讨论主题
2. 关键结论或决定
3. 涉及的技术概念

对话内容：
{text[:8000]}

请用中文输出摘要。"""

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.3,
    }

    try:
        req = urllib.request.Request(
            OLLAMA_API,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"    [WARN] 摘要生成失败: {e}")
        # fallback: 取前200字
        return text[:200] + "..."


def extract_concepts(summary: str) -> list[str]:
    """从摘要中提取概念标签"""
    # 简单规则：技术词汇匹配
    keywords = [
        "量子",
        "QKD",
        "AES",
        "RSA",
        "PQC",
        "量子计算",
        "量子通讯",
        "加密",
        "密码学",
        "密钥",
        "贝尔不等式",
        "BB84",
        "StoryAgent",
        "Story",
        "故事",
        "小说",
        "Weibo",
        "微博",
        "监控",
        "爬虫",
        "Claude",
        "Code",
        "编程",
        "开发",
        "飞书",
        "WeChat",
        "微信",
        "Telegram",
        "Hermes",
        "Agent",
        "AI",
        "MiniMax",
        "Ollama",
        "LLM",
        "Embedding",
        " comic",
        "连环画",
        "分镜",
        "定妆照",
        "SEO",
        "网站",
        "博客",
        "Apple",
        "macOS",
        "iPhone",
        "cron",
        "定时",
        "自动化",
    ]
    found = [k for k in keywords if k in summary]
    return found[:5]  # 最多5个标签


def is_already_indexed(session_id: str) -> bool:
    """检查此 session 是否已被索引（查 Hermem.db）"""
    import sqlite3

    if not MEMORY_DB.exists():
        return False
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cur = conn.execute("SELECT 1 FROM chunks WHERE session_id = ? LIMIT 1", (session_id,))
        result = cur.fetchone()
        conn.close()
        return result is not None
    except Exception:
        return False


def index_session(session: dict, dry_run: bool = False) -> bool:
    """索引单条会话"""
    sid = session["session_id"]
    dt = session["started_at"]

    if is_already_indexed(sid):
        print(f"  ⏭️  跳过（已索引）: {sid}")
        return False

    print(f"  → 处理: {sid} ({dt.strftime('%Y-%m-%d %H:%M') if dt else '?'})")

    if dry_run:
        return True

    # 提取文本
    raw_text = extract_text(session["data"])

    # 生成摘要
    print("    生成摘要中...")
    summary = summarize_session(raw_text, sid)

    # 提取概念
    concepts = extract_concepts(summary)

    # 生成 embedding
    print("    生成 embedding 中...")
    emb, src = get_embedding_cached(summary)

    # 写入数据库 + 向量库
    vec_indices = append_vectors([emb])
    vec_idx = vec_indices[0]

    chunk_id = insert_chunk(
        session_id=sid,
        content=summary,
        chunk_type="session_summary",
        concepts=concepts,
        source_file=session["path"],
        vec_index=vec_idx,
    )

    print(f"    ✅ 完成: chunk_id={chunk_id} vec_idx={vec_idx} 标签={concepts}")
    return True


def main():
    dry_run = "--dry-run" in sys.argv

    sessions = load_sessions()
    print("=== Hermem 批量回填 ===")
    print(f"  发现 {len(sessions)} 条会话")
    print(f"  模式: {'仅预览' if dry_run else '实际写入'}")
    print()

    # 按时间排序（旧到新）
    sessions.sort(key=lambda s: s["started_at"] or 0)

    indexed = 0
    skipped = 0

    for session in sessions:
        if index_session(session, dry_run=dry_run):
            indexed += 1
        else:
            skipped += 1
        time.sleep(0.1)  # 避免 Ollama 过载

    print()
    print("=== 完成 ===")
    print(f"  新增索引: {indexed} 条")
    print(f"  跳过: {skipped} 条")

    if not dry_run:
        # 最终统计
        from database import get_chunk_count
        from vectorstore import get_stats

        print(f"  Hermem 当前: {get_chunk_count()} 条记忆, 向量 {get_stats()['total_vectors']} 条")


if __name__ == "__main__":
    main()
