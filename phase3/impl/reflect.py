"""Hermem V6 Sprint 3 - 反射 API(hermem_reflect)。

流程:
1. 4 路召回(temporal + vec + bm25 → RRF,1 次 search_with_tier 调用)
2. top_k chunks 拼 context
3. 4b 综合(query + context → answer)
4. 可选:把答案 + query 写 L4(标 source=reflect_immediate,v5.5 l4_reflection 扩 signature)
"""

import logging

from .predictor import call_predictor_llm
from .vector_search import search_with_tier

logger = logging.getLogger(__name__)


# ── 反射综合 prompt(决策 8:4b 一律) ──────────────────────────────
REFLECT_ANSWER_PROMPT = """基于以下历史记忆,综合回答用户问题。

## 历史记忆(context)
{context}

## 用户问题
{query}

## 要求
1. 综合多条记忆,不要简单复述
2. 引用具体来源 [chunk_id]
3. 不知道就说不知道
4. 不超过 300 字
5. 中文输出
"""

REFLECT_L4_PROMPT = """你是 Hermem 记忆分析助手。基于以下即时反射的问答对,生成一条不超过 80 字的元记忆描述。

## 用户问题
{query}

## 反射答案
{answer}

## 要求
1. 用中文
2. 描述用户的偏好、习惯、期望(不是描述问题本身)
3. 80 字以内
4. 直接描述,不要"根据分析"这类废话开头
"""


def hermem_reflect(
    query: str,
    *,
    top_k: int = 5,
    write_l4: bool = False,
    session_id: str = "",
) -> dict:
    """基于历史记忆反思回答用户问题。

    Args:
        query: 用户问题
        top_k: 召回 chunks 数(默认 5)
        write_l4: True 时把答案写入 l4_reflections(需 session_id)
        session_id: 写 L4 时的归属 session

    Returns:
        {
            "answer": str,
            "sources": list[dict],  # top-k chunks
            "l4_written": bool,      # 是否成功写 L4
            "l4_text": str | None,   # L4 文本(若写)
            "l4_id": int | None,     # L4 row id
        }
    """
    # 1. 4 路召回
    high, medium = search_with_tier(query=query, top_k=top_k)
    sources = high + medium

    if not sources:
        return {
            "answer": "没有找到相关历史记忆。",
            "sources": [],
            "l4_written": False,
            "l4_text": None,
            "l4_id": None,
        }

    # 2. 拼 context(限制长度避免 LLM 超长)
    context = "\n\n".join(
        f"[{c.get('id', '?')}] {c.get('content', '')[:200]}"
        for c in sources[:top_k]
    )

    # 3. 4b 综合
    from . import predictor as _predictor  # 走模块属性访问,便于 mock
    prompt = REFLECT_ANSWER_PROMPT.format(query=query[:200], context=context[:1500])
    try:
        answer = _predictor.call_predictor_llm(prompt, timeout=3.0).strip()  # 决策 8:4b + 3s
    except Exception as e:
        logger.warning(f"hermem_reflect LLM failed: {e}")
        return {
            "answer": f"反思失败(LLM 错误): {e}",
            "sources": sources,
            "l4_written": False,
            "l4_text": None,
            "l4_id": None,
        }

    # 4. 可选写 L4
    l4_written = False
    l4_text = None
    l4_id = None
    if write_l4 and session_id:
        # v5.5 不是 package;走 sys.path hack 直接 import l4_reflection
        import sys as _sys
        from pathlib import Path as _Path
        _v55_path = str(_Path(__file__).parent.parent / "v5.5")
        if _v55_path not in _sys.path:
            _sys.path.insert(0, _v55_path)
        try:
            from l4_reflection import write_reflection_immediate
        except ImportError:
            write_reflection_immediate = None
            logger.debug("v5.5 l4_reflection 导入失败,跳过 L4 写")

        if write_reflection_immediate is not None:
            try:
                # 用 4b 把 answer 综合为 L4 元记忆(80 字内)
                l4_text = _predictor.call_predictor_llm(
                    REFLECT_L4_PROMPT.format(query=query[:200], answer=answer[:500]),
                    timeout=3.0,
                ).strip() or None
                if l4_text:
                    l4_id = write_reflection_immediate(l4_text, session_id)
                    l4_written = l4_id is not None
            except Exception as e:
                logger.warning(f"hermem_reflect L4 write failed: {e}")

    return {
        "answer": answer,
        "sources": sources,
        "l4_written": l4_written,
        "l4_text": l4_text,
        "l4_id": l4_id,
    }
