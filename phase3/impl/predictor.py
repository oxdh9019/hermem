"""Hermem V6 - 预测性召回(Sprint 2)。

L3 画像 → qwen3.5:4b-no-think 生成 2-3 预测查询词 → search_with_tier 多路
→ query-level RRF 融合 → 失败降级到显式。

设计要点(决策 1/2/3):
- LLM hard timeout 3s(决策 1 修订:2b 实测 1.5-5.5s 不稳定,2s 撞 p95 边界,3s 给 50% 余量)
- query-level RRF k=30(决策 2,显式优先,top 命中权重差距大)
- 不读近 3 轮对话(决策 3,MemoryProvider 接口无 recent_turns,降级为只用 L3 画像)

LLM 模型:qwen3.5:4b-no-think(2026-06-10 全面复核规范:调用本地 LLM 一律 4b)
- 决策 B 修订(2026-06-10):原 SPEC v2.0 写 2b;实测 2b 1.5-5.5s 不稳定 + 格式遵循 0%;
  切 4b warm 380ms + cold 1.7-2.0s + 100% 遵循 few-shot 格式
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

from .vector_search import search_with_tier

logger = logging.getLogger(__name__)

# ── 配置(Sprint 2 决策 B 复核修订:4b,实测 cold 1.7-2.0s p95,
# 2s 撞边界 → 0% 成功率;改 3s 给 p95 + 50% 余量)──
LLM_TIMEOUT_S = 3.0
LLM_MODEL = "qwen3.5:4b-no-think"  # 2026-06-10 全面复核规范:调用本地 LLM 一律 4b;决策 B 修订 2b→4b(2b 1.5-5.5s 不稳定 + 格式差)
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"  # 原生 API,不走 /v1

# ── 指标埋点(供 Sprint 4 eval 用)───────────────────────────────────────
_metrics = {
    "predictor_latency_ms": [],   # 实际 LLM 耗时列表
    "predictor_timeout_count": 0,
    "predictor_empty_count": 0,
    "predictor_hits_added": 0,    # 预测词带来的新 chunk 数量(去重前 - 去重后)
}


def get_predictor_metrics() -> dict:
    """返回当前预测器指标快照(供 health CLI / Sprint 4 eval 读)。"""
    latencies = _metrics["predictor_latency_ms"]
    p95 = (
        sorted(latencies)[int(len(latencies) * 0.95)]
        if latencies
        else 0
    )
    return {
        "latency_count": len(latencies),
        "latency_p95_ms": p95,
        "latency_avg_ms": (sum(latencies) / len(latencies)) if latencies else 0,
        "timeout_count": _metrics["predictor_timeout_count"],
        "empty_count": _metrics["predictor_empty_count"],
        "hits_added": _metrics["predictor_hits_added"],
    }


def reset_predictor_metrics() -> None:
    """重置指标(测试用)。"""
    _metrics["predictor_latency_ms"].clear()
    _metrics["predictor_timeout_count"] = 0
    _metrics["predictor_empty_count"] = 0
    _metrics["predictor_hits_added"] = 0


# ── L3 画像读取(决策 3:只用画像,不用对话历史)─────────────────────
USER_PROFILE_PATHS = [
    Path.home() / ".hermes" / "memory" / "user_profile.md",
    Path.home() / ".hermes" / "memory" / "user_profile_auto.md",
]


def read_user_profile(max_chars: int = 1500) -> str:
    """读取 L3 画像(user_profile.md + user_profile_auto.md 拼接),截断到 max_chars。

    缺失文件:返回空字符串(预测器在空画像下仍可工作,只是质量降低)。
    """
    parts = []
    for path in USER_PROFILE_PATHS:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {path.name}\n{content}")
            except OSError as e:
                logger.warning(f"读取 {path} 失败: {e}")
    merged = "\n\n".join(parts)
    return merged[:max_chars]


# ── 任务 2.1:预测 prompt 工程(决策 A 修订:few-shot examples)─────────
# few-shot 是必要的:qwen3.5:4b-no-think 不擅长严格格式(无 examples 会生成长文;有 examples 100% 遵循)
# 2026-06-10 全面复核:之前写"2b 不擅长"已过时,4b 同样需要 few-shot,但 4b 实际表现远优于 2b
PREDICTIVE_PROMPT = """你是 Hermem 记忆助手的查询预测器。基于用户画像和当前问题,生成 2-3 个用户**接下来可能想问**的查询词。

## 示例(严格按此格式)

示例 1:
当前问题: hermem V6 进度如何?
输出:
V6 Sprint 2 实现细节
Sprint 2 风险点
下一步计划

示例 2:
当前问题: 今天天气不错,适合跑步
输出:
周末跑步地点
跑步装备推荐
上次跑步记录

## 用户画像(L3)
{user_profile}

## 用户当前问题
{user_query}

## 要求
1. 输出 2-3 个查询词,每行一个,5-15 字
2. 重点预测:**用户接下来需要的信息**而非字面同义改写
3. 避免重复用户已问过的字面问题
4. 只输出查询词,不要其他解释

## 输出格式(严格遵守)
query1
query2
query3
"""


def build_predictive_prompt(
    user_profile: str,
    user_query: str,
) -> str:
    """Build prompt for qwen3.5:4b-no-think predictive query generation.

    Sprint 2 决策 3:不传 recent_turns;只基于 L3 画像 + 当前问题。
    2026-06-10 全面复核:模型名 2b → 4b(决策 B 修订,见 sprint2-summary §3.1)。
    """
    return PREDICTIVE_PROMPT.format(
        user_profile=user_profile or "(无画像)",
        user_query=user_query[:300],
    )


# ── 任务 2.2:qwen3.5:4b-no-think 调用封装(3s hard timeout)────────
def call_predictor_llm(prompt: str, timeout: float = LLM_TIMEOUT_S) -> str:
    """Call qwen3.5:4b-no-think for predictive query generation.

    3s hard timeout(决策 1 修订 + 复核修订:2b 1.5-5.5s 不可行,2s 撞 p95 边界 → 0% 成功,
    4b cold 1.7-2.0s + 50% 余量 = 3s)。
    Exceeds → raise requests.Timeout。

    解析 ndjson:stream=False 时 Ollama 仍返回多行 JSON(每 token 一行),
    content 分散在所有 done=False 行;累积拼接后返回最后内容。
    (4b 模式:done=true 行 message.content 是完整内容,优先取)

    显式 think=False(4b 验证需要显式禁用,即使 tag 标了 -no-think)。
    """
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,  # 显式禁用 thinking content
        "options": {
            "temperature": 0.3,
            "num_predict": 60,  # 3 查询词 × ~20 tokens 缓冲
        },
    }
    resp = requests.post(
        OLLAMA_CHAT_URL,
        json=payload,
        timeout=timeout,  # 1.5s hard limit
    )
    resp.raise_for_status()
    # 解析 ndjson:
    # - 2b 模型:content 分散在 done=false 的每行(token-level streaming)
    # - 4b 模型:done=true 行有完整 content(单次返回)
    # 策略:优先取 done=true 的 content;若为空则累积 done=false 的
    import json as _json
    final_content = ""
    streamed_content = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            d = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if d.get("done") and d.get("message", {}).get("content"):
            # 4b 模式:done=true 行的 content 是完整内容
            final_content = d["message"]["content"]
        elif not d.get("done") and d.get("message", {}).get("content"):
            # 2b 模式:累积每 token
            streamed_content.append(d["message"]["content"])
    # 优先 final_content(4b);若空,fallback 到累积(2b)
    return final_content or "".join(streamed_content).strip()


# ── 任务 2.3:解析 + 主函数 ────────────────────────────────────────────
def _parse_llm_output(raw: str, max_queries: int = 3) -> list[str]:
    """Parse LLM output: 2-3 query words, one per line.

    Robust against:
    - Empty output (return [])
    - Numbered list ('1. xxx' → 'xxx')
    - Bullets / quotes ('- xxx' / '"xxx"' → 'xxx')
    - Lines too long (likely malformed, skip)
    """
    queries = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去编号 / 项目符号 / 引号
        line = re.sub(r'^[\d\.\-\*\u2022"]+\s*', "", line)
        line = line.strip("\"'\u3001\u3002")
        if 2 <= len(line) <= 30:
            queries.append(line)
        if len(queries) >= max_queries:
            break
    return queries


def generate_predictive_queries(
    user_profile: str,
    user_query: str,
) -> list[str]:
    """Generate 2-3 predictive queries using qwen3.5:4b-no-think.

    Returns empty list on any failure (timeout, parse error, etc.).
    Caller is responsible for fallback to explicit-only search.
    2026-06-10 全面复核:模型名 2b → 4b(决策 B 修订)。
    """
    t0 = time.time()
    try:
        prompt = build_predictive_prompt(user_profile, user_query)
        raw = call_predictor_llm(prompt, timeout=LLM_TIMEOUT_S)
        latency_ms = (time.time() - t0) * 1000
        _metrics["predictor_latency_ms"].append(latency_ms)

        queries = _parse_llm_output(raw)
        if not queries:
            logger.warning(f"Predictor returned no queries: {raw[:200]}")
            _metrics["predictor_empty_count"] += 1
        return queries
    except requests.Timeout:
        latency_ms = (time.time() - t0) * 1000
        _metrics["predictor_latency_ms"].append(latency_ms)
        _metrics["predictor_timeout_count"] += 1
        logger.warning(f"Predictor LLM timed out (>{LLM_TIMEOUT_S:.1f}s); returning []")
        return []
    except Exception as e:
        logger.warning(f"Predictor failed: {type(e).__name__}: {e}")
        return []


# ── 任务 2.4 + 2.5:search_predictive 整合 + RRF 融合 + 降级 ──────────
def _rrf_fuse(rank_lists: list[list[dict]], k: int = 30) -> list[dict]:
    """Reciprocal Rank Fusion across multiple rank lists.

    决策 2:k=30 让 top 命中比次命中权重差距更大(显式优先)。
    """
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}
    for rank_list in rank_lists:
        for rank, chunk in enumerate(rank_list):
            cid = chunk.get("id") or chunk.get("chunk_id")
            if cid is None:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            meta[cid] = chunk
    sorted_cids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [meta[cid] for cid in sorted_cids]


def search_predictive(
    user_query: str,
    user_profile: str = "",
    top_k: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Predictive search: explicit + predicted queries, RRF-fused.

    Args:
        user_query: 当前用户查询
        user_profile: L3 画像(空字符串 → 自动 read_user_profile())
        top_k: 每层返回条数上限

    Returns:
        (high, medium) 同 search_with_tier shape

    失败/超时空降级:仅返回显式 search_with_tier 结果。
    """
    try:
        # 自动读画像(如未传)
        if not user_profile:
            user_profile = read_user_profile()

        # 1. 显式检索(必须有)
        explicit_high, explicit_medium = search_with_tier(query=user_query, top_k=top_k)

        # 2. 预测生成(可能空)
        predicted_queries = generate_predictive_queries(user_profile, user_query)
        if not predicted_queries:
            logger.debug("No predicted queries; returning explicit-only")
            return explicit_high, explicit_medium

        # 3. 每个预测词单独检索
        predicted_high, predicted_medium = [], []
        for pq in predicted_queries:
            h, m = search_with_tier(query=pq, top_k=top_k)
            predicted_high.extend(h)
            predicted_medium.extend(m)

        # 4. RRF 融合(query-level k=30,显式优先)
        fused_high = _rrf_fuse([explicit_high, predicted_high], k=30)
        fused_medium = _rrf_fuse([explicit_medium, predicted_medium], k=30)

        # 埋点:预测词带来的新 chunk 数量
        explicit_high_ids = {c.get("id") or c.get("chunk_id") for c in explicit_high}
        new_high_ids = {c.get("id") or c.get("chunk_id") for c in predicted_high} - explicit_high_ids
        _metrics["predictor_hits_added"] += len(new_high_ids)

        return fused_high[:top_k], fused_medium[:top_k]
    except Exception as e:
        logger.error(f"search_predictive catastrophic failure: {e}; falling back to explicit-only")
        # 兜底:不重复调 search_with_tier(它可能就是异常源);返回空
        return [], []
