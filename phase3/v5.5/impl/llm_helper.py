#!/usr/bin/env python3
"""
Hermem V5.5 - LLM 调用统一入口，支持 primary + fallback 自动降级。

Primary + Fallback 模型名从 `impl.config` 统一读取（LLM_PRIMARY_MODEL /
LLM_FALLBACK_MODEL），不要在本文件硬编码，方便集中调整。

Usage:
    from impl.llm_helper import call_llm_with_fallback
    result = call_llm_with_fallback(prompt, max_tokens=300)
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 延迟导入 llm_generate + 模型名 ─────────────────────────────────────────


def _get_llm_generate():
    """延迟导入，避免循环依赖。"""
    # v5.5/impl/ → v5.5/ → phase3/ → phase3/impl/
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.utils import llm_generate

    return llm_generate


def _get_model_names():
    """从 impl.config 读取 primary/fallback 模型名。"""
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.config import LLM_PRIMARY_MODEL, LLM_FALLBACK_MODEL

    return LLM_PRIMARY_MODEL, LLM_FALLBACK_MODEL


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
    primary, fallback = _get_model_names()

    # Primary: MiniMax-M2.7（自动路由）
    try:
        result = llm_generate(
            prompt,
            model=primary,
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.info("[LLM] Primary (%s) 调用失败: %s，尝试 fallback...", primary, e)

    # Fallback: qwen2.5:3b（本地 Ollama）
    try:
        result = llm_generate(
            prompt,
            model=fallback,
            max_tokens=max_tokens,
        )
        if result and result.strip():
            logger.info("[LLM] Fallback (%s) 成功", fallback)
            return result.strip()
    except Exception as e2:
        logger.warning("[LLM] Fallback (%s) 也失败: %s，跳过", fallback, e2)
        return None

    return None


def call_llm_primary(prompt: str, max_tokens: int = 300) -> str | None:
    """仅使用 primary LLM，不降级。失败返回 None。"""
    try:
        llm_generate = _get_llm_generate()
        primary, _ = _get_model_names()
        result = llm_generate(
            prompt,
            model=primary,
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
        _, fallback = _get_model_names()
        result = llm_generate(
            prompt,
            model=fallback,
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.debug("[LLM] Fallback call failed: %s", e)
        return None
    return None
