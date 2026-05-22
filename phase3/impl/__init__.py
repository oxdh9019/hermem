#!/usr/bin/env python3
"""
Hermem Phase 3 - 统一入口
通过 `python -m impl.run <session_id>` 调用完整流程，或直接导入各模块。
"""
import sys
from pathlib import Path

# 确保 impl 目录可导入
sys.path.insert(0, str(Path(__file__).parent))

from . import config
from .l0_store import save_l0_raw, load_l0_detail, enforce_l0_quota
from .l1_extract import extract_l1_facts, store_l1_batch
from .l1_search import vector_search_l1, retrieve
from .l2_aggregate import try_aggregate_l2, check_scene_dormancy, merge_duplicate_scenes
from .l3_staging import (
    stage_preference, get_pending_preferences,
    process_l3_staging, confirm_preference, reject_preference,
    batch_stage_from_l1,
)


def process_session(
    session_id: str,
    messages: list,
    start: str,
    end: str,
    session_summary: str,
    active_disposition_ids: list[str] | None = None,
) -> dict:
    """
    端到端处理一个会话的全部 Phase 3 流程。

    参数:
        session_id:     会话 ID
        messages:       原始 messages 数组
        start:          ISO 8601 开始时间
        end:            ISO 8601 结束时间
        session_summary: Phase 1 生成的会话摘要

    返回:
        dict，含各步骤状态和结果统计
    """
    import time  # noqa: F401 (reserved for future duration tracking)
    stats = {}

    # 1. L0 保存
    l0_ref = save_l0_raw(session_id, messages, start, end)
    stats["l0_ref"] = l0_ref

    # 2. L1 提取
    facts = extract_l1_facts(session_summary)
    stats["facts_extracted"] = len(facts)
    if not facts:
        # 无 facts 时也保存了 L0，但跳过后续阶段
        return stats

    # 3. L1 写入数据库
    fact_ids = store_l1_batch(facts, l0_ref)
    stats["fact_ids"] = fact_ids

    # 4. L2 聚合（基于写入后的完整 fact 对象）
    written_facts = [{**f, "id": fid} for f, fid in zip(facts, fact_ids)]
    try_aggregate_l2(written_facts)

    # 5. L3 staging（提取 preference）
    batch_stage_from_l1(written_facts, source=session_id)

    # 6. L3 staging 触发检查
    staging_result = process_l3_staging()
    stats["staging"] = staging_result

    # 7. Error annotation（异步，不阻塞主流程）
    #    使用 enqueue 而非同步调用，避免 LLM 延迟阻塞用户响应
    #    active_disposition_ids: V4.5 精确 success 匹配所需的上下文
    from .async_annotation import enqueue_annotation
    qsize = enqueue_annotation(
        session_id=session_id,
        session_summary=session_summary,
        l1_facts=facts,
        active_disposition_ids=active_disposition_ids,
    )
    stats["annotation_queued"] = True
    stats["annotation_queue_depth"] = qsize

    return stats


if __name__ == "__main__":
    import json
    from datetime import datetime

    # CLI: python -m impl.run <session_summary_text>
    if len(sys.argv) > 1:
        summary = sys.argv[1]
        sid = f"cli_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"Processing session {sid} with summary: {summary[:80]}...")
        result = process_session(sid, [], datetime.now().isoformat(), datetime.now().isoformat(), summary)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: python -m impl.run <session_summary_text>")
