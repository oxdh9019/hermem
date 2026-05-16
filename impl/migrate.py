#!/usr/bin/env python3
"""Hermem Phase 2 历史迁移脚本。

将 ~/.hermes/memory/sessions/*.md 迁移到 Phase 2 向量数据库。

用法:
    python -m impl.migrate                  # 迁移所有 sessions
    python -m impl.migrate --dry-run        # 预览，不写入
    python -m impl.migrate --sessions-dir /path/to/sessions
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from impl.database import init_db, insert_chunk, get_chunk_count
from impl.embedding import get_embedding_cached
from impl.vectorstore import init_vectorstore, append_vectors


# ── Browser Snapshot 清洗 ───────────────────────────────

def clean_browser_snapshot(text: str) -> str:
    """从浏览器快照文本中提取干净的文章正文。

    典型格式：
      - banner:
        - heading "博主名" [ref=eN] [nth=N] [level=N]:
          - text: 时间
        - article:
          - text: 实际文章内容...
          - link "全文" [ref=eN]:
            - /url: /status/...

    清洗策略：
    1. 尝试 JSON 解析（browser snapshot 是 JSON 序列化的字符串）
    2. 从 article.text 字段提取正文
    3. 如果解析失败，用正则提取 "- text:" 行
    4. 去除残留的 [ref=eN] [nth=N] 等标记
    """
    if not text:
        return text

    # 策略1：尝试当作 JSON 解析（最准确）
    # browser snapshot 通常是 {"success": true, "snapshot": "..."} 或直接是快照字符串
    if text.strip().startswith('{') or text.strip().startswith('['):
        try:
            parsed = json.loads(text)
            # 支持多种 JSON 结构
            snapshot = None
            if isinstance(parsed, dict):
                snapshot = parsed.get('snapshot', '') or parsed.get('content', '')
                if not snapshot:
                    # 可能是 {"success": true, "url": "...", "snapshot": {...}}
                    for v in parsed.values():
                        if isinstance(v, dict) and 'snapshot' in v:
                            snapshot = v['snapshot']
                            break
            elif isinstance(parsed, list):
                snapshot = ' '.join(
                    c.get('text', '') if isinstance(c, dict) else str(c)
                    for c in parsed if isinstance(c, dict)
                )

            if snapshot and isinstance(snapshot, str) and len(snapshot) > 50:
                text = snapshot
        except (json.JSONDecodeError, TypeError):
            pass

    # 策略2：从 article.text 字段提取正文
    # 格式: "    - text: 文章内容..."
    article_texts = re.findall(r'^\s+- text:\s*(.+)$', text, re.MULTILINE)
    if article_texts:
        # 取最长的几段（通常是正文）
        article_texts = [t.strip() for t in article_texts if len(t.strip()) > 30]
        if article_texts:
            # 合并所有 article.text，按长度排序取前5
            combined = '\n'.join(sorted(article_texts, key=len, reverse=True)[:5])
            if len(combined) > 50:
                text = combined

    # 策略3：去除残留的 browser snapshot 标记
    # 去除 [ref=eN] [nth=N] [level=N] 等标记
    text = re.sub(r'\s*\[ref=[^\]]+\]', '', text)
    text = re.sub(r'\s*\[nth=[^\]]+\]', '', text)
    text = re.sub(r'\s*\[level=[^\]]+\]', '', text)
    # 去除开头的列表标记 "  - "
    text = re.sub(r'^  - ', '', text, flags=re.MULTILINE)
    text = re.sub(r'^    - ', '', text, flags=re.MULTILINE)
    # 去除空的或有残留标记的行
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not re.match(r'^[a-z]+:', line)]
    text = '\n'.join(lines)

    return text.strip()


def is_likely_browser_snapshot(text: str) -> bool:
    """判断文本是否像浏览器快照（需要清洗）。"""
    if not text:
        return False
    # 特征：包含 banner:、heading \、ref=[eN]、\/url: 等浏览器快照典型标记
    score = 0
    if re.search(r'banner:|article:', text):
        score += 2
    if re.search(r'heading\s+"[^"]+"\s+\[ref=', text):
        score += 2
    if re.search(r'\[ref=e\d+\]', text):
        score += 1
    if re.search(r'\\"/url:', text):
        score += 1
    if re.search(r'^\s+-\s+(img|text|link):', text, re.MULTILINE):
        score += 1
    return score >= 3


# ── Markdown 解析 ───────────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter。返回 (frontmatter_dict, body)。"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    fm_text, body = parts[1], parts[2].strip()
    fm = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        fm[key] = val
    return fm, body


def split_into_chunks(body: str, chunk_size: int = 500) -> list[str]:
    """按段落拆分 Markdown body（双换行分隔）。

    对于 browser snapshot 内容，自动提取 article.text 字段作为正文。
    """
    # 先清洗 browser snapshot（如果检测到的话）
    if is_likely_browser_snapshot(body):
        body = clean_browser_snapshot(body)

    paragraphs = [p.strip() for p in re.split(r"\n\n+", body)]
    chunks = []
    current = ""
    for para in paragraphs:
        if not para:
            continue
        if len(current) + len(para) < chunk_size:
            current += ("\n\n" if current else "") + para
        else:
            if current:
                chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) > 20]


def extract_title_and_tags(body: str) -> tuple[str, list[str]]:
    """从 Markdown 内容中提取标题和标签。"""
    tags = set()
    title = ""

    # # 标题
    h1 = re.search(r"^# (.+)$", body, re.MULTILINE)
    if h1:
        title = h1.group(1).strip()

    # 标签从 **完成事项**:、**待办**、- [ ] 等模式
    for line in body.splitlines():
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["hermem", "phase", "项目"]):
            tags.add("hermem")
        if any(kw in line_lower for kw in ["storyagent", "story"]):
            tags.add("storyagent")
        if any(kw in line_lower for kw in ["微博", "weibo", "监控"]):
            tags.add("weibo")
        if any(kw in line_lower for kw in ["skill", "技能"]):
            tags.add("skill")
        if any(kw in line_lower for kw in ["测试", "test"]):
            tags.add("testing")

    return title, list(tags)


# ── 迁移逻辑 ───────────────────────────────────────────

def migrate_file(
    session_file: Path,
    dry_run: bool = False,
    chunk_size: int = 500,
) -> dict:
    """迁移单个 session 文件。返回统计信息。"""
    content = session_file.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    session_id = session_file.stem
    date_str = fm.get("date", session_id[:10])
    chunk_type = fm.get("type", "session_summary")

    # 提取标题和标签
    title, auto_tags = extract_title_and_tags(body)
    chunks = split_into_chunks(body, chunk_size=chunk_size)

    result = {
        "session_id": session_id,
        "date": date_str,
        "title": title,
        "total_chunks": len(chunks),
        "imported": 0,
        "skipped": 0,
        "errors": [],
    }

    if dry_run:
        print(f"  [dry-run] {session_id} → {len(chunks)} chunks")
        for i, c in enumerate(chunks):
            print(f"    chunk[{i}]: {c[:60]}...")
        return result

    for i, chunk_text in enumerate(chunks):
        try:
            # 生成 embedding
            emb, src = get_embedding_cached(chunk_text)
            indices = append_vectors([emb])

            # 入库
            cid = insert_chunk(
                session_id=session_id,
                content=chunk_text,
                chunk_type=chunk_type,
                concepts=auto_tags,
                source_file=str(session_file),
                source_line=i,
                vec_index=indices[0],
            )
            result["imported"] += 1
        except Exception as e:
            result["errors"].append(str(e))
            result["skipped"] += 1

    return result


def migrate_all(
    sessions_dir: Path = None,
    dry_run: bool = False,
    chunk_size: int = 500,
) -> list[dict]:
    """迁移所有 session 文件。"""
    if sessions_dir is None:
        sessions_dir = Path.home() / ".hermes" / "memory" / "sessions"

    if not sessions_dir.exists():
        print(f"目录不存在: {sessions_dir}")
        return []

    md_files = sorted(sessions_dir.glob("*.md"))
    if not md_files:
        print(f"没有找到 .md 文件: {sessions_dir}")
        return []

    print(f"找到 {len(md_files)} 个 session 文件\n")

    results = []
    for f in md_files:
        print(f"处理: {f.name}")
        r = migrate_file(f, dry_run=dry_run, chunk_size=chunk_size)
        results.append(r)
        if not dry_run:
            print(f"  → 导入 {r['imported']} 条" +
                  (f", 错误 {len(r['errors'])}" if r['errors'] else ""))
        else:
            print(f"  → dry-run: {r['total_chunks']} chunks")

    return results


# ── CLI ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Hermem 历史迁移工具")
    parser.add_argument("--sessions-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="预览，不写入")
    parser.add_argument("--chunk-size", type=int, default=500, help="每个 chunk 的最大字符数")
    args = parser.parse_args()

    # 初始化数据库
    init_db()
    init_vectorstore()

    before = get_chunk_count()
    print(f"迁移前 chunks: {before} 条\n")

    results = migrate_all(
        sessions_dir=args.sessions_dir,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
    )

    after = get_chunk_count()
    print(f"\n=== 迁移完成 ===")
    print(f"迁移前: {before} 条 | 新增: {after - before} 条 | 总计: {after} 条")
    total_chunks = sum(r["total_chunks"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)
    print(f"处理文件: {len(results)} | 总 chunks: {total_chunks} | 错误: {total_errors}")


if __name__ == "__main__":
    main()
