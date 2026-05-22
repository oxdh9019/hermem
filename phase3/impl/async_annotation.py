#!/usr/bin/env python3
"""
Hermem Phase 3 - Error Annotation 异步队列
将 annotation 任务放入后台队列，不阻塞 process_session() 主流程。

使用方式：
    from .async_annotation import enqueue_annotation

    # 在 process_session() 中，annotation 不再同步调用
    # 改为 enqueue_annotation(session_id, session_summary, l1_facts)
    # 主流程立即返回，worker 在后台异步写入 L0 的 error_annotation 字段

    enqueue_annotation(session_id, session_summary, l1_facts)

应用启动时调用一次：
    from .async_annotation import start_worker
    start_worker()

应用退出时调用：
    from .async_annotation import stop_worker
    stop_worker()
"""

import json
import queue
import threading
from pathlib import Path
from typing import Optional

# ── 全局队列和工作线程 ───────────────────────────────────────────────────

_annotation_queue: queue.Queue = queue.Queue()
_worker_thread: threading.Thread | None = None
_shutdown_flag = False


def _worker():
    """后台工作线程：不断从队列取任务并执行 annotation"""
    # 延迟导入避免循环依赖
    from .config import ERROR_ANNOTATION_MODEL
    from .l0_store import annotate_l0_after_l1_v2 as _annotate

    while not _shutdown_flag:
        try:
            item = _annotation_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        # 支持三种格式：
        # 2-tuple（轻量级）：(session_id, session_summary)
        # 3-tuple（标准）：(session_id, session_summary, l1_facts)
        # 4-tuple（V4.5）：(session_id, session_summary, l1_facts, active_disposition_ids)
        if len(item) == 2:
            session_id, session_summary = item
            l1_facts = []
            active_disposition_ids = []
        elif len(item) == 3:
            session_id, session_summary, l1_facts = item
            active_disposition_ids = []
        else:
            session_id, session_summary, l1_facts, active_disposition_ids = item

        try:
            # 幂等检查：优先查 L0 文件，再查 lightweight cache
            from .config import L0_DIR as _L0_DIR

            l0_path = Path(_L0_DIR) / f"{session_id}.json"
            if l0_path.exists():
                with open(l0_path) as f:
                    data = json.load(f)
                if "error_annotation" in data:
                    continue  # 幂等跳过，finally 会调用 task_done()

            annotation = _annotate(
                session_id=session_id,
                session_summary=session_summary,
                l1_facts=l1_facts,
                annotation_model=ERROR_ANNOTATION_MODEL,
            )
            if annotation is None:
                print(f"[AsyncAnnotation] 生成失败（见上方日志）: {session_id}")
            elif annotation.get("prediction_errors"):
                # V4.3 B1: 联动更新 disposition error_count
                from .disposition_updater import update_dispositions_from_errors

                updated = update_dispositions_from_errors(session_id, annotation)
                if updated > 0:
                    print(f"[AsyncAnnotation] B1 更新了 {updated} 条 disposition error_count")
            else:
                # V4.5: 无 prediction_errors → 累加 success_count
                # 优先用精确 ID 匹配，fallback 到 session 匹配（V4.3 旧行为）
                from .disposition_updater import (
                    increment_success_by_ids,
                    increment_success_count,
                )

                # 从 queue item 提取 active_disposition_ids（第 4 个元素，新格式）
                active_ids = []
                if len(item) >= 4:
                    active_ids = item[3] or []
                if active_ids:
                    incremented = increment_success_by_ids(active_ids, session_id)
                    if incremented > 0:
                        print(
                            f"[AsyncAnnotation] V4.5 精确更新 {incremented} 条 disposition success_count（IDs）"
                        )
                else:
                    incremented = increment_success_count(session_id)
                    if incremented > 0:
                        print(
                            f"[AsyncAnnotation] V4.5 回退累加 {incremented} 条 disposition success_count（session）"
                        )
        except Exception as e:
            print(f"[AsyncAnnotation] 异常: {session_id} - {e}")
        finally:
            _annotation_queue.task_done()


def start_worker():
    """启动后台工作线程（在应用初始化时调用一次）"""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker, daemon=True)
        _worker_thread.start()
        print("[AsyncAnnotation] 后台工作线程已启动")


def stop_worker(wait: bool = True):
    """停止工作线程，wait=True 会等待所有未完成任务完成（最多30秒）"""
    global _shutdown_flag
    _shutdown_flag = True
    if wait:
        _annotation_queue.join()
        if _worker_thread and _worker_thread.is_alive():
            _worker_thread.join(timeout=5)
    print("[AsyncAnnotation] 后台工作线程已停止")


def drain_queue(n_workers: int = 4, timeout: int = 300):
    """
    多线程并行 drain annotation 队列（用于 backfill）。
    启动 N 个 worker 线程并行处理队列所有任务，最多等 timeout 秒。
    不依赖 Hermem 主进程的 context。
    """
    import concurrent.futures

    _shutdown_flag = False  # reset for drain

    def _drain_worker(wid: int):
        from .config import ERROR_ANNOTATION_MODEL
        from .l0_store import annotate_l0_after_l1_v2 as _annotate

        processed = 0
        while not _shutdown_flag:
            try:
                item = _annotation_queue.get(timeout=0.5)
            except queue.Empty:
                break
            if len(item) == 2:
                session_id, session_summary = item
                l1_facts = []
                active_disposition_ids = []
            elif len(item) == 3:
                session_id, session_summary, l1_facts = item
                active_disposition_ids = []
            else:
                session_id, session_summary, l1_facts, active_disposition_ids = item
            try:
                from .config import L0_DIR as _L0_DIR

                l0_path = Path(_L0_DIR) / f"{session_id}.json"
                if l0_path.exists():
                    with open(l0_path) as f:
                        data = json.load(f)
                    if "error_annotation" in data:
                        _annotation_queue.task_done()
                        continue
                annotation = _annotate(
                    session_id=session_id,
                    session_summary=session_summary,
                    l1_facts=l1_facts,
                    annotation_model=ERROR_ANNOTATION_MODEL,
                )
                if annotation and annotation.get("prediction_errors"):
                    from .disposition_updater import update_dispositions_from_errors

                    update_dispositions_from_errors(session_id, annotation)
                elif annotation:
                    # V4.5: 优先用精确 ID 匹配，fallback 到 session 匹配
                    from .disposition_updater import (
                        increment_success_by_ids,
                        increment_success_count,
                    )

                    if active_disposition_ids:
                        updated = increment_success_by_ids(active_disposition_ids, session_id)
                        if updated > 0:
                            print(
                                f"[Drain w{wid}] V4.5 精确更新 {updated} 条 disposition success_count"
                            )
                    else:
                        increment_success_count(session_id)
            except Exception as e:
                print(f"[Drain w{wid}] {session_id}: {e}")
            finally:
                _annotation_queue.task_done()
                processed += 1
        print(f"[Drain w{wid}] Done, processed {processed} tasks")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(_drain_worker, i) for i in range(n_workers)]
        concurrent.futures.wait(futures, timeout=timeout)
    print("[Drain] Queue drained.")


def enqueue_annotation_lightweight(
    session_id: str,
    session_summary: str,
) -> int:
    """
    轻量级入队接口（用于 Hermes Agent 实时流程）。
    仅需 session_id + 对话摘要文本，不依赖 L0 文件。
    queue item 为 2-tuple: (session_id, session_summary)
    """
    if not session_id or not session_summary:
        return _annotation_queue.qsize()
    _annotation_queue.put((session_id, session_summary))
    return _annotation_queue.qsize()


# ── 兼容旧接口（3-tuple: session_id, session_summary, l1_facts）─────────


def enqueue_annotation(
    session_id: str,
    session_summary: str,
    l1_facts: list[dict],
    active_disposition_ids: list[str] | None = None,
) -> int:
    """
    标准入队接口（用于 process_session 批量流程）。
    queue item 为 4-tuple: (session_id, session_summary, l1_facts, active_disposition_ids)

    active_disposition_ids: V4.5 新增。Turn N 检索时激活的 disposition ID 列表，
    用于 annotation 完成后精确累加 success_count，避免 session 级别误增。
    """
    if not session_id:
        return _annotation_queue.qsize()
    _annotation_queue.put((session_id, session_summary, l1_facts, active_disposition_ids or []))
    return _annotation_queue.qsize()


def get_queue_depth() -> int:
    """返回当前队列深度（未处理任务数）"""
    return _annotation_queue.qsize()
