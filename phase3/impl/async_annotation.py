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
_worker_thread: Optional[threading.Thread] = None
_shutdown_flag = False


def _worker():
    """后台工作线程：不断从队列取任务并执行 annotation"""
    # 延迟导入避免循环依赖
    from .l0_store import annotate_l0_after_l1_v2 as _annotate
    from .config import ERROR_ANNOTATION_MODEL

    while not _shutdown_flag:
        try:
            item = _annotation_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        session_id, session_summary, l1_facts = item

        try:
            # 幂等检查放在 dequeue 后、调用前（避免竞态窗口）
            from .config import L0_DIR
            l0_path = Path(L0_DIR) / f"{session_id}.json"
            if l0_path.exists():
                with open(l0_path) as f:
                    data = json.load(f)
                if "error_annotation" in data:
                    # 已标注，无需再处理
                    _annotation_queue.task_done()
                    continue

            annotation = _annotate(
                session_id=session_id,
                session_summary=session_summary,
                l1_facts=l1_facts,
                annotation_model=ERROR_ANNOTATION_MODEL,
            )
            if annotation is None:
                print(f"[AsyncAnnotation] 生成失败（见上方日志）: {session_id}")
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


def enqueue_annotation(
    session_id: str,
    session_summary: str,
    l1_facts: list[dict],
) -> int:
    """
    将 annotation 任务放入异步队列。

    参数：
        session_id:      会话 ID
        session_summary: 对话摘要
        l1_facts:        已提取的 L1 facts 列表

    返回：
        队列中的任务数量（便于观察积压情况）

    注意：
        - 调用后立即返回，不等待完成
        - 不对 session_id 和 session_summary 做空检查（由调用方保证）
    """
    if not session_id:
        return _annotation_queue.qsize()

    _annotation_queue.put((session_id, session_summary, l1_facts))
    return _annotation_queue.qsize()


def get_queue_depth() -> int:
    """返回当前队列深度（未处理任务数）"""
    return _annotation_queue.qsize()