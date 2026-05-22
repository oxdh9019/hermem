#!/Users/oliver/.hermes/hermes-agent/venv/bin/python3
"""
Hermem Phase 3 - Annotation Backfill

一次性批量补 annotation：
- 扫描所有 L0 文件，筛选出没有真实 annotation 的 session
- 对每个 session 生成 session_summary
- 入队 annotation（异步队列）
- 等待队列清空

用法：
    python3 scripts/backfill_annotations.py [--limit N]
"""
import sys, json, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "phase3"))

from impl.async_annotation import start_worker, drain_queue, enqueue_annotation

L0_DIR = pathlib.Path.home() / ".hermes" / "memory" / "l0_raw"


def build_summary(d: dict) -> str:
    """从 L0 JSON 构建简洁的 session_summary。"""
    msgs = d.get("messages", [])
    if not msgs:
        return ""
    user_msgs = [m for m in msgs if m.get("role") in ("user", "human")]
    assistant_msgs = [m for m in msgs if m.get("role") in ("assistant", "bot")]
    # Collect topics/content
    parts = []
    for m in msgs:
        content = m.get("content", "")
        if isinstance(content, str) and len(content) > 20:
            parts.append(content[:200])
    combined = "\n".join(parts[:10])
    if len(combined) > 2000:
        combined = combined[:2000] + "[截断]"
    return combined


def main():
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    l0_files = sorted(L0_DIR.glob("*.json"), key=lambda f: -f.stat().st_mtime)
    to_process = []
    for f in l0_files:
        if limit and len(to_process) >= limit:
            break
        d = json.load(open(f))
        ea = d.get("error_annotation")
        if ea and isinstance(ea, dict):
            continue  # Already has real annotation
        msgs = d.get("messages", [])
        total_chars = sum(len(m.get("content", "") or "") for m in msgs)
        if len(msgs) >= 2 or total_chars > 300:
            to_process.append((f.stem, d))

    print(f"[Backfill] Found {len(to_process)} sessions to annotate")
    if not to_process:
        print("Nothing to do.")
        return

    enqueued = 0
    for session_id, d in to_process:
        summary = build_summary(d)
        if not summary:
            continue
        enqueue_annotation(session_id, summary, [])
        enqueued += 1
        if enqueued % 20 == 0:
            print(f"  Enqueued {enqueued}/{len(to_process)}...")

    print(f"\n[Backfill] Enqueued {enqueued} annotation tasks, draining with 4 workers...")
    drain_queue(n_workers=4, timeout=600)
    print(f"[Backfill] Done. {enqueued} sessions annotated.")


if __name__ == "__main__":
    main()
