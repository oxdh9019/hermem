#!/usr/bin/env python3
"""
Hermem V5.5 - LLM 调用统一入口，支持 primary + fallback 自动降级。

Primary: MiniMax-M2.7（外部 API，自动路由）
Fallback: qwen2.5:3b（本地 Ollama，自动路由）

Usage:
    from impl.llm_helper import call_llm_with_fallback
    result = call_llm_with_fallback(prompt, max_tokens=300)
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 延迟导入 llm_generate ─────────────────────────────────────────────────────


def _get_llm_generate():
    """延迟导入，避免循环依赖。"""
    # v5.5/impl/ → v5.5/ → phase3/ → phase3/impl/
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.utils import llm_generate

    return llm_generate


def call_llm_with_fallback(prompt: str, max_tokens: int = 300) -> str | None:
    """
    调用 LLM，primary 失败时自动降级到本地模型。

    Args:
        prompt: 输入提示词
        max_tokens: 最大生成 token 数

    Returns:
        生成的文本，失败时返回 None（不抛异常）。
    """
    llm_generate = _get_llm_generate()

    # Primary: MiniMax-M2.7（自动路由）
    try:
        result = llm_generate(
            prompt,
            model="MiniMax-M2.7",
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.info("[LLM] Primary (MiniMax-M2.7) 调用失败: %s，尝试 fallback...", e)

    # Fallback: qwen2.5:3b（本地 Ollama）
    try:
        result = llm_generate(
            prompt,
            model="qwen2.5:3b",
            max_tokens=max_tokens,
        )
        if result and result.strip():
            logger.info("[LLM] Fallback (qwen2.5:3b) 成功")
            return result.strip()
    except Exception as e2:
        logger.warning("[LLM] Fallback (qwen2.5:3b) 也失败: %s，跳过", e2)
        return None

    return None


def call_llm_primary(prompt: str, max_tokens: int = 300) -> str | None:
    """仅使用 primary LLM，不降级。失败返回 None。"""
    try:
        llm_generate = _get_llm_generate()
        result = llm_generate(
            prompt,
            model="MiniMax-M2.7",
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.debug("[LLM] Primary call failed: %s", e)
        return None
    return None


def call_llm_fallback(prompt: str, max_tokens: int = 300) -> str | None:
    """仅使用 fallback LLM（本地）。失败返回 None。"""
    try:
        llm_generate = _get_llm_generate()
        result = llm_generate(
            prompt,
            model="qwen2.5:3b",
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.debug("[LLM] Fallback call failed: %s", e)
        return None
    return None
