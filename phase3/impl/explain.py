"""Hermem V6 Sprint 3 - 解释层入口。

explain_chunk(chunk, current_query, similarity, *, use_llm=False, seed="") -> str

轻量路径(默认):模板轮转,零 LLM 延迟。
增强路径(use_llm=True):qwen3.5:4b-no-think 生成,3s hard timeout,失败降级。
"""

import logging
import time

import requests

from .explain_templates import render_explanation

logger = logging.getLogger(__name__)


# ── 增强路径 prompt(决策 8:4b 一律) ──────────────────────────────
EXPLANATION_PROMPT = """你是 Hermem 记忆助手。基于以下信息,生成一句话(≤ 80 字)解释为什么这条记忆被召回。

## 用户当前问题
{current_query}

## 命中的记忆
{chunk_content}

## 要求
1. 输出一句话(中文)
2. 解释**关联**而非重复记忆内容
3. 不添加记忆没有的细节
4. 不要 markdown 格式
"""


def explain_chunk(
    chunk: dict,
    current_query: str,
    similarity: float,
    *,
    use_llm: bool = False,
    seed: str = "",
) -> str:
    """解释单条 chunk 被召回的原因。

    Args:
        chunk: 命中的 chunk dict(id + content + similarity)
        current_query: 用户当前问题
        similarity: 0-1 相似度分数
        use_llm: True 走 4b 增强路径(决策 8 修订);False 走模板默认
        seed: 决定模板选择(seed="" 时用 chunk_id)

    Returns:
        解释文本(中文/英文),失败降级到 V5 `[自动回忆 - 相似度 X.XX]` 格式
    """
    content = chunk.get("content", "")
    chunk_id = chunk.get("id") or chunk.get("chunk_id", "unknown")
    effective_seed = seed or f"chunk_{chunk_id}"

    # 1. 轻量路径(默认)
    if not use_llm:
        _explain_metrics["explain_total"] += 1
        _explain_metrics["explain_template"] += 1
        return render_explanation(
            chunk_content=content,
            trigger=current_query,
            similarity=similarity,
            seed=effective_seed,
        )

    # 2. 增强路径(LLM)
    return _explain_chunk_llm(content, current_query, similarity, chunk_id)


def _explain_chunk_llm(
    chunk_content: str,
    current_query: str,
    similarity: float,
    chunk_id: str,
) -> str:
    """增强路径:4b 生成。失败降级到 V5 格式。"""
    from .predictor import call_predictor_llm  # 复用 Sprint 2 ndjson 解析

    _explain_metrics["explain_total"] += 1
    t0 = time.time()
    try:
        prompt = EXPLANATION_PROMPT.format(
            chunk_content=chunk_content[:300],
            current_query=current_query[:200],
        )
        raw = call_predictor_llm(prompt, timeout=3.0)  # 决策 8:4b + 3s
        latency = (time.time() - t0) * 1000
        _explain_metrics["explain_latency_ms"].append(latency)

        explanation = raw.strip()
        if explanation and len(explanation) <= 300:
            _explain_metrics["explain_llm"] += 1
            return explanation
        # 输出超长,丢弃走 V5 格式
        logger.warning(f"explain_chunk LLM output too long: {len(explanation)} chars")
    except requests.Timeout:
        latency = (time.time() - t0) * 1000
        _explain_metrics["explain_latency_ms"].append(latency)
        _explain_metrics["explain_llm_timeout"] += 1
        logger.warning("explain_chunk LLM timed out (>3s); falling back to V5 format")
    except Exception as e:
        logger.warning(f"explain_chunk LLM failed: {e}; falling back to V5 format")

    # 兜底:V5 格式
    _explain_metrics["explain_llm_fallback"] += 1
    return f"[自动回忆 - 相似度 {similarity:.2f}]\n{chunk_content[:120]}"


# ── 指标埋点(供 Sprint 4 eval 用)───────────────────────────────────────
_explain_metrics = {
    "explain_total": 0,
    "explain_template": 0,        # 走模板路径
    "explain_llm": 0,             # 走 4b 增强路径
    "explain_llm_timeout": 0,
    "explain_llm_fallback": 0,    # 降级到 V5
    "explain_latency_ms": [],     # 增强路径 LLM 耗时
}


def get_explain_metrics() -> dict:
    """返回当前解释层指标快照(供 health CLI / Sprint 4 eval 读)。"""
    latencies = _explain_metrics["explain_latency_ms"]
    return {
        "total": _explain_metrics["explain_total"],
        "template": _explain_metrics["explain_template"],
        "llm": _explain_metrics["explain_llm"],
        "llm_timeout": _explain_metrics["explain_llm_timeout"],
        "llm_fallback": _explain_metrics["explain_llm_fallback"],
        "llm_p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
    }


def reset_explain_metrics() -> None:
    """重置指标(测试用)。"""
    for k in _explain_metrics:
        if isinstance(_explain_metrics[k], list):
            _explain_metrics[k].clear()
        else:
            _explain_metrics[k] = 0
