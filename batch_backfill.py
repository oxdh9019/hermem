#!/usr/bin/env python3
"""
Hermem 批量回填脚本
一次性为所有历史会话生成摘要 + 语义索引

用法:
  cd .../hermem
  ../hermes-agent/venv/bin/python3 batch_backfill.py [--dry-run]

依赖: Hermem impl 模块直接通过 Hermem 项目路径导入
"""
import sys, json, glob, os, time, sqlite3
from datetime import datetime
from pathlib import Path

HERMEM_DIR = Path(__file__).resolve().parent
# impl/ 内部用 "from . import database" 等相对导入，
# 必须把 hermem 的父目录加入路径，让 Python 把 "impl" 当作包来导入
sys.path.insert(0, str(HERMEM_DIR.parent))

# 用绝对导入加载 impl 模块（Python 会正确处理 impl/ 内部的相对导入）
import impl.database as db_mod
import impl.embedding as emb_mod
import impl.vectorstore as vs_mod

HERMES_SESSIONS = Path.home() / ".hermes" / "sessions"
MEMORY_DB = Path.home() / ".hermes" / "memory" / "hermem.db"

# MiniMax API（从 ~/.hermes/.env 读取）
_minimax_key = None
_minimax_base = "https://api.minimaxi.com/anthropic"


def _get_minimax_config():
    global _minimax_key
    if _minimax_key is None:
        env_file = Path.home() / ".hermes" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.strip().startswith("MINIMAX_CN_API_KEY"):
                    _minimax_key = line.split("=", 1)[1].strip()
        if not _minimax_key:
            raise RuntimeError("未找到 MINIMAX_CN_API_KEY")
    return _minimax_key


OLLAMA_API = _get_minimax_config()  # not Ollama, just placeholder
MINIMAX_API = _minimax_base + "/v1/messages"
MINIMAX_MODEL = "MiniMax-M2.7"


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
        ts = data.get("started_at", 0)
        dt = datetime.fromtimestamp(ts) if ts else None
        sessions.append({
            "path": f,
            "session_id": sid,
            "started_at": dt,
            "data": data,
        })
    return sessions


def extract_text(data):
    """从 session JSON 中提取用户+助手的纯文本"""
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


def summarize_session(text: str) -> str:
    """调用 MiniMax API 生成会话摘要"""
    import urllib.request, urllib.error

    prompt = (
        "请为以下对话会话生成一段简洁的摘要（200字以内），包含：\n"
        "1. 主要讨论主题\n"
        "2. 关键结论或决定\n"
        "3. 涉及的技术概念\n\n"
        f"对话内容：\n{text[:8000]}\n\n"
        "请用中文输出摘要。"
    )

    payload = {
        "model": MINIMAX_MODEL,
        "max_tokens": 300,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }

    key = _get_minimax_config()
    try:
        req = urllib.request.Request(
            MINIMAX_API,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            # MiniMax Anthropic 格式: content = [{"type":"thinking",...}, {"type":"text","text":"..."}]
            for block in result.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip()
            return str(result)[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"    [WARN] MiniMax HTTP {e.code}: {body[:200]}")
        return text[:200] + "..."
    except Exception as e:
        print(f"    [WARN] 摘要生成失败: {e}")
        return text[:200] + "..."


def extract_concepts(summary: str) -> list[str]:
    """从摘要中提取概念标签"""
    keywords = [
        "量子", "QKD", "AES", "RSA", "PQC", "量子计算", "量子通讯",
        "加密", "密码学", "密钥", "BB84",
        "StoryAgent", "Story", "故事", "小说",
        "Weibo", "微博", "监控",
        "Claude", "Code", "编程", "开发",
        "飞书", "WeChat", "微信",
        "Hermes", "Agent", "AI",
        "MiniMax", "Ollama", "LLM", "Embedding",
        "comic", "连环画", "分镜", "定妆照",
        "SEO", "网站",
        "Apple", "macOS", "iPhone",
        "cron", "自动化",
        "财务", "审计", "创业", "机遇",
    ]
    found = [k for k in keywords if k in summary]
    return found[:5]


def is_already_indexed(session_id: str) -> bool:
    """检查此 session 是否已在 Hermem.db 中"""
    if not MEMORY_DB.exists():
        return False
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cur = conn.execute(
            "SELECT 1 FROM chunks WHERE session_id = ? LIMIT 1",
            (session_id,)
        )
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

    date_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "?"
    print(f"  → 处理: {sid} ({date_str})")

    if dry_run:
        return True

    raw_text = extract_text(session["data"])

    print(f"    生成摘要...")
    summary = summarize_session(raw_text)
    concepts = extract_concepts(summary)

    print(f"    生成 embedding...")
    emb, src = emb_mod.get_embedding_cached(summary)
    vec_indices = vs_mod.append_vectors([emb])

    chunk_id = db_mod.insert_chunk(
        session_id=sid,
        content=summary,
        chunk_type="session_summary",
        concepts=concepts,
        source_file=session["path"],
        vec_index=vec_indices[0],
    )

    print(f"    ✅ chunk_id={chunk_id} vec={vec_indices[0]} 标签={concepts}")
    return True


def main():
    dry_run = "--dry-run" in sys.argv

    # 初始化 Hermem 组件
    db_mod.init_db()
    vs_mod.init_vectorstore()

    sessions = load_sessions()
    sessions.sort(key=lambda s: s["started_at"] or 0)

    print(f"=== Hermem 批量回填 ===")
    print(f"  发现 {len(sessions)} 条会话")
    print(f"  模式: {'仅预览' if dry_run else '实际写入'}")
    print()

    indexed = 0
    skipped = 0

    for session in sessions:
        try:
            if index_session(session, dry_run=dry_run):
                indexed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"    ❌ 错误: {e}")
            skipped += 1
        time.sleep(0.15)  # 避免 Ollama 过载

    print()
    print(f"=== 完成 ===")
    print(f"  新增索引: {indexed} 条")
    print(f"  跳过: {skipped} 条")

    if not dry_run:
        print(f"  Hermem 当前: {db_mod.get_chunk_count()} 条记忆, "
              f"向量 {vs_mod.get_stats()['total_vectors']} 条")


if __name__ == "__main__":
    main()
