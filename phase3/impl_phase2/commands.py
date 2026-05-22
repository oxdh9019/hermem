#!/usr/bin/env python3
"""Hermem Phase 2 CLI 工具。

用法:
    python -m impl.commands search "查询内容"
    python -m impl.commands stats
    python -m impl.commands health
    python -m impl.commands import <session_file.md>
    python -m impl.commands init
"""

import argparse
import json
import sys
from pathlib import Path

# 确保 impl 模块在路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

from impl.database import get_chunk_count, init_db, insert_chunk
from impl.embedding import get_embedding_cached, is_ollama_healthy, test_embedding
from impl.retrieval import hybrid_search, keyword_search, semantic_search
from impl.vectorstore import append_vectors, get_stats, init_vectorstore


def cmd_search(args):
    """语义搜索命令。"""
    # 确保已初始化
    init_db()
    init_vectorstore()

    mode = args.mode or "hybrid"
    top_k = args.top_k or 5

    results = (
        hybrid_search(args.query, top_k=top_k)
        if mode == "hybrid"
        else semantic_search(args.query, top_k=top_k)
        if mode == "semantic"
        else keyword_search(args.query, top_k=top_k)
    )

    if not results:
        print(f"未找到与「{args.query}」相关的记忆。")
        return

    print(f"找到 {len(results)} 条相关记忆:\n")
    for i, r in enumerate(results, 1):
        r = dict(r) if not isinstance(r, dict) else r
        concepts = r.get("concepts", "")
        try:
            concepts = ", ".join(json.loads(concepts))
        except Exception:
            concepts = concepts or ""
        print(f"  [{i}] {r['session_id']} ({r['chunk_type']})")
        print(f"      {r['content']}")
        if concepts:
            print(f"      标签: {concepts}")
        print()


def cmd_stats(args):
    """统计信息命令。"""
    db_count = get_chunk_count()
    vec_stats = get_stats()

    print("=== Hermem 统计 ===")
    print(f"  记忆片段: {db_count} 条")
    print(f"  向量总数: {vec_stats['total_vectors']}")
    print(f"  向量维度: {vec_stats['dim']}")
    print(f"  向量形状: {vec_stats['shape']}")
    print(f"  占用空间: {vec_stats['memory_bytes'] / 1024:.1f} KB")


def cmd_health(args):
    """健康检查命令。"""
    print("=== Hermem 健康检查 ===\n")

    # 1. Ollama
    print("  [1/3] Ollama + bge-m3")
    health = is_ollama_healthy()
    if health["healthy"]:
        print(f"      ✅ 服务正常 | 延迟: {health['latency_ms']}ms")
    else:
        print(f"      ❌ 问题: {health['error']}")
        return

    emb = test_embedding()
    if emb["success"]:
        print(
            f"      ✅ Embedding 正常 | dim={emb['dim']} | {emb['latency_ms']}ms | 来源: {emb['source']}"
        )
    else:
        print(f"      ❌ Embedding 失败: {emb.get('error')}")

    # 2. 数据库
    print("\n  [2/3] SQLite")
    try:
        db_count = get_chunk_count()
        print(f"      ✅ 数据库正常 | {db_count} 条记忆")
    except Exception as e:
        print(f"      ❌ 数据库错误: {e}")

    # 3. 向量库
    print("\n  [3/3] NumPy 向量库")
    try:
        stats = get_stats()
        print(f"      ✅ 向量库正常 | {stats['total_vectors']} 条 | shape={stats['shape']}")
    except Exception as e:
        print(f"      ❌ 向量库错误: {e}")


def cmd_import(args):
    """导入单条记忆（支持 .md 文件或直接文本）。"""
    init_db()
    init_vectorstore()

    file_path = Path(args.file)
    if file_path.exists():
        content = file_path.read_text(encoding="utf-8")
        # 尝试解析 frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2].strip()
                # 简单解析 tags
                for line in frontmatter.splitlines():
                    if line.startswith("tags:"):
                        tags_str = line.split("tags:", 1)[1].strip().strip('[]"').replace("'", "")
                        [t.strip() for t in tags_str.replace('"', "").split(",") if t.strip()]
                content = body
        session_id = file_path.stem
    else:
        content = args.file
        session_id = "cli_import"

    chunk_type = args.type or "session_summary"
    concepts = args.concepts or []

    # 生成 embedding
    emb, src = get_embedding_cached(content)
    indices = append_vectors([emb])
    cid = insert_chunk(
        session_id=session_id,
        content=content,
        chunk_type=chunk_type,
        concepts=concepts,
        source_file=str(file_path) if file_path.exists() else None,
        vec_index=indices[0],
    )
    print(f"✅ 已导入 chunk_id={cid} vec_index={indices[0]}")
    print(f"   session_id={session_id} type={chunk_type}")
    print(f"   content: {content[:80]}...")


def cmd_init(args):
    """初始化数据库和向量库。"""
    init_db()
    result = init_vectorstore()
    print("✅ Hermem Phase 2 初始化完成")
    print("   数据库: ~/.hermes/memory/hermem.db")
    print("   向量库: ~/.hermes/memory/hermem_vectors.npy")
    print(f"   向量: {result['total_vectors']} 条, dim={result['dim']}")


def main():
    parser = argparse.ArgumentParser(
        description="Hermem Phase 2 - 语义记忆管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # search
    p_search = sub.add_parser("search", help="语义搜索记忆")
    p_search.add_argument("query", help="查询文本")
    p_search.add_argument(
        "--mode",
        choices=["semantic", "keyword", "hybrid"],
        default="hybrid",
        help="搜索模式",
    )
    p_search.add_argument("--top-k", type=int, default=5, help="返回条数")

    # stats
    sub.add_parser("stats", help="显示统计信息")

    # health
    sub.add_parser("health", help="健康检查")

    # import
    p_import = sub.add_parser("import", help="导入记忆")
    p_import.add_argument("file", help=".md 文件路径或直接文本内容")
    p_import.add_argument("--type", default="session_summary", help="chunk 类型")
    p_import.add_argument("--concepts", nargs="*", default=[], help="概念标签")

    # init
    sub.add_parser("init", help="初始化数据库和向量库")

    args = parser.parse_args()

    cmd_map = {
        "search": cmd_search,
        "stats": cmd_stats,
        "health": cmd_health,
        "import": cmd_import,
        "init": cmd_init,
    }
    cmd_map[args.cmd](args)


if __name__ == "__main__":
    main()
