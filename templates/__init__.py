"""Hermem - Hermes 轻量记忆增强系统

Plugin wrapper that exposes Hermem's Phase 2 semantic recall engine
as a MemoryProvider plugin for Hermes Agent.

Implementation lives at: ~/.hermes/projects/hermem/impl/
"""

from __future__ import annotations

import json
import logging
import threading
import re
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ── Lazy imports from Hermem impl (deferred until initialize()) ─────────────
_impl_cache: Dict[str, Any] = {}
_impl_init_done = False


def _ensure_impl():
    """Lazily import and initialize the Hermem impl modules."""
    global _impl_init_done
    if _impl_init_done:
        return
    import sys

    impl_path = os.path.expanduser("~/.hermes/projects/hermem/phase3")

    # Try plugin-local impl first (submodule/symlink layout)
    plugin_impl = os.path.join(os.path.dirname(__file__), "impl")
    if os.path.isdir(plugin_impl):
        if plugin_impl not in sys.path:
            sys.path.insert(0, plugin_impl)
    elif impl_path not in sys.path:
        sys.path.insert(0, impl_path)

    try:
        from impl import database, embedding, vectorstore, retrieval
        from impl.database import init_db
        from impl.vectorstore import init_vectorstore

        # Ensure DB and vectorstore are ready
        database.init_db()
        vectorstore.init_vectorstore()

        _impl_cache["database"] = database
        _impl_cache["embedding"] = embedding
        _impl_cache["vectorstore"] = vectorstore
        _impl_cache["retrieval"] = retrieval
        _impl_init_done = True
    except ImportError:
        _print_setup_guide()
        raise


def _print_setup_guide():
    """Print friendly setup guide when impl import fails."""
    import sys
    import textwrap
    sep = "=" * 62
    guide = textwrap.dedent("""
    {sep}
    Hermem 插件无法加载实现模块。
    请按以下步骤操作:

    1. 克隆 Hermem 仓库（如果没有）:
       git clone https://github.com/oxdh9019/hermem.git ~/hermem

    2. 创建 impl 软链接:
       cd ~/.hermes/plugins/memory/hermem
       ln -sf ~/hermem/phase3/impl impl

    3. 确认软链接生效:
       ls -la impl  # 应显示 -> ~/hermem/phase3/impl

    4. 初始化向量库:
       python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py

    详细指南: ~/.hermes/plugins/memory/hermem/QUICKSTART.md
    {sep}
    """.format(sep=sep)).strip()
    print(guide, file=sys.stderr)


def _get_impl():
    _ensure_impl()
    return _impl_cache


# ── Tool schemas ────────────────────────────────────────────────────────────

HERMEM_SEARCH_SCHEMA = {
    "name": "hermem_search",
    "description": (
        "Search Hermem long-term memory using semantic + keyword hybrid recall.  "
        "Use this when the user asks about something discussed in past sessions, "
        "a previous project, a known preference, or any topic requiring memory of prior conversations.  "
        "Returns top matching memory chunks with relevance scores.\n\n"
        "Args:\n"
        "  query (required): Search query text — be specific and use the user's own words.\n"
        "  mode: 'semantic' | 'keyword' | 'hybrid' (default: 'hybrid').\n"
        "  top_k: Max results to return (default: 5)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text."},
            "mode": {
                "type": "string",
                "enum": ["semantic", "keyword", "hybrid"],
                "description": "Search mode (default: hybrid).",
            },
            "top_k": {"type": "integer", "description": "Max results (default: 5)."},
        },
        "required": ["query"],
    },
}

HERMEM_ADD_SCHEMA = {
    "name": "hermem_add",
    "description": (
        "Add a fact or note to Hermem long-term memory.  "
        "Use when the user explicitly says 'remember this', shares a preference, "
        "makes a decision, or provides information worth preserving across sessions.  "
        "The content is embedded and stored in Hermem's semantic recall index.\n\n"
        "Args:\n"
        "  content (required): The fact or note to remember. Use the user's exact words.\n"
        "  concepts (optional): Comma-separated concept tags for filtering (e.g. 'preference,project').\n"
        "  chunk_type: 'concept_note' | 'decision' | 'preference' | 'fact' (default: 'fact')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Content to store in memory."},
            "concepts": {"type": "string", "description": "Comma-separated concept tags."},
            "chunk_type": {
                "type": "string",
                "enum": ["concept_note", "decision", "preference", "fact"],
                "description": "Type of memory chunk (default: 'fact').",
            },
        },
        "required": ["content"],
    },
}

HERMEM_FORGET_SCHEMA = {
    "name": "hermem_forget",
    "description": (
        "Remove a memory chunk from Hermem by semantic similarity.  "
        "Use when the user says 'forget this' or wants to delete a specific memory.  "
        "Finds the most similar chunk to the query and deletes it.\n\n"
        "Args:\n"
        "  query (required): Query to find the chunk to delete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Query to find the chunk to delete."},
        },
        "required": ["query"],
    },
}

HERMEM_STATS_SCHEMA = {
    "name": "hermem_stats",
    "description": "Show Hermem memory statistics: total chunks, embedding health, Ollama status.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ── HermemMemoryProvider ─────────────────────────────────────────────────────

# ── Phase2c constants ──────────────────────────────────────────────────────────
MAX_PENDING_RECALL_KEYWORDS = 10   # max keywords in _pending_recall queue
RECOLLECT_TIMEOUT_PER_KEYWORD = 2.0  # seconds per keyword retrieval timeout
RECOLLECT_STALENESS_TURNS = 3       # keywords older than this many turns are stale


class HermemMemoryProvider(MemoryProvider):
    """Hermem Phase 2 — semantic recall memory provider plugin."""

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._chunk_count: Optional[int] = None
        self._active_dispositions: list = []  # V4.2: session-level active dispositions
        # V4.3 B2b: session 缓冲区（用于轻量级 annotation）
        self._current_session_id: Optional[str] = None
        self._session_messages: list = []      # [{"role": ..., "content": ..., "timestamp": ...}]
        self._last_annotation_trigger: Optional[object] = None
        # V4.3 方案 A: 追踪上一轮激活的 disposition ID，用于无 correction 时的 success 增量
        self._last_activated_disposition_ids: list[str] = []
        # V4.4: 缓存上一轮激活的 disposition 完整对象 + 上一轮用户消息
        self._last_activated_dispositions: list[dict] = []
        self._last_turn_user_message: str = ""
        self._last_turn_topic_keywords: set[str] = set()  # 上一轮关键词集合（用于话题延续判断）
        # V4.4 Phase2c: pending recall keywords queue (thread-safe)
        self._pending_recall_lock = threading.Lock()
        self._pending_recall_keywords: list[tuple[str, float]] = []  # [(keyword, queued_at_turn), ...]
        self._pending_recall_turn_counter = 0  # track turn counter for staleness
        # V5: Active retrieval state
        self._v5_injected_chunk_ids: set = set()  # 会话级去重
        self._v5_medium_tracker: dict = {}  # 中置信累积: {chunk_id: max_similarity}
        self._v5_retrieve_count: int = 0  # 消息计数（频率控制）

    @property
    def name(self) -> str:
        return "hermem"

    # ── Availability ─────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if Hermem is ready: Ollama + bge-m3 must be available."""
        try:
            _ensure_impl()
            impl = _impl_cache
            health = impl["embedding"].is_ollama_healthy()
            return bool(health.get("healthy") and health.get("model_installed"))
        except Exception as e:
            logger.debug("Hermem is_available() failed: %s", e)
            return False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    # ── V4.3 方案 B: Feedback Consumer ─────────────────────────────────────────

    def _process_feedback_queue(self) -> None:
        """Poll pending_feedback table and directly annotate each skill submission.

        V4.3 方案 B fix: 不再走 enqueue_annotation_lightweight -> queue -> worker -> L0文件路径。
        改为直接在 consumer 线程里调 LLM annotation，避免 L0 文件依赖。
        反馈文本本身就是 annotation 内容，用户需在 /hermem-feedback 里自包含上下文。
        """
        import time as _time, sqlite3 as _sqlite3, re as _re, json as _json
        from concurrent.futures import ThreadPoolExecutor as _TPE

        db_path = Path.home() / ".hermes" / "memory" / "l0_l3.db"

        # 延迟导入 phase3 impl
        import sys as _sys
        p3_path = str(Path.home() / ".hermes" / "projects" / "hermem" / "phase3")
        if p3_path not in _sys.path:
            _sys.path.insert(0, p3_path)
        from impl.config import ERROR_ANNOTATION_PROMPT
        from impl.utils import llm_generate
        from impl.disposition_updater import update_dispositions_from_errors

        _tpe = _TPE(max_workers=4)

        def _annotate_one(feedback_id: int, feedback_text: str) -> None:
            """线程池任务：直接调 LLM annotation，不依赖 L0 文件。
            注意：qwen3.5:4b-no-think 在长 prompt 下 /api/chat 超时，
            改用 MiniMax-M2.7（与 ERROR_ANNOTATION_MODEL 一致）。
            """
            session_id = f"feedback-{int(_time.time())}-{feedback_id}"
            summary = f"[/hermem-feedback skill]\n{feedback_text}"

            try:
                prompt = ERROR_ANNOTATION_PROMPT.format(
                    SESSION_SUMMARY=summary,
                    L1_FACTS="（无 — 用户反馈场景，无历史 L1 facts）",
                )
                raw = llm_generate(
                    prompt,
                    model="MiniMax-M2.7",
                    temperature=0.2,
                    max_tokens=1024,
                )
                text = raw.strip()
                # 去掉 markdown code fence
                if text.startswith("```"):
                    parts = text.split("```", 2)
                    text = parts[1] if len(parts) >= 2 else parts[0]
                    text = text.lstrip("\n\r")
                text = text.strip().strip("`")
                # 提 JSON 对象
                m = _re.search(r'\{[\s\S]*\}', text)
                if not m:
                    logger.debug("[Hermem] Feedback %d: no JSON found", feedback_id)
                    return
                annotation = _json.loads(m.group())
                if not annotation.get("prediction_errors"):
                    return
                updated = update_dispositions_from_errors(session_id, annotation)
                logger.info(
                    "[Hermem] Feedback %d annotated: errors=%d, dispositions_updated=%d",
                    feedback_id, len(annotation["prediction_errors"]), updated,
                )
            except Exception as e:
                logger.debug("[Hermem] Feedback %d annotation error: %s", feedback_id, e)
                return  # 幂等：失败不重试，避免卡死线程池
            finally:
                # 标记 processed
                try:
                    conn2 = _sqlite3.connect(db_path)
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE pending_feedback SET processed=1 WHERE id=?",
                        (feedback_id,),
                    )
                    conn2.commit()
                    conn2.close()
                except Exception:
                    pass

        while True:
            conn = None
            try:
                conn = _sqlite3.connect(db_path, timeout=5.0)
                conn.row_factory = _sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, text FROM pending_feedback "
                    "WHERE processed=0 AND source='skill' LIMIT 4"
                )
                rows = cur.fetchall()
                conn.close()
                conn = None
                if not rows:
                    _time.sleep(2)
                    continue
                for row in rows:
                    _tpe.submit(_annotate_one, row["id"], row["text"])
                _time.sleep(1)
            except Exception as e:
                logger.debug("[Hermem] Feedback consumer poll error: %s", e)
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                _time.sleep(5)

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize Hermem: init DB + vectorstore, warm up Ollama."""
        try:
            _ensure_impl()
            impl = _impl_cache
            # Prime the Ollama health check in background (don't fail init)
            threading.Thread(
                target=impl["embedding"].is_ollama_healthy,
                daemon=True,
                name="hermem-health-check",
            ).start()
            # V4.3 方案 B: start feedback consumer (one-shot per process)
            if not getattr(self, "_feedback_consumer_started", False):
                self._feedback_consumer_started = True
                t = threading.Thread(
                    target=self._process_feedback_queue,
                    daemon=True,
                    name="hermem-feedback-consumer",
                )
                t.start()
                logger.info("Hermem feedback consumer started")
            # V4.3: Start the annotation worker so enqueued tasks are actually drained
            _ensure_impl()
            from impl.async_annotation import start_worker as _start_worker
            _start_worker()
            logger.info("HermemMemoryProvider initialized (session=%s)", session_id)
        except Exception as e:
            logger.warning("HermemMemoryProvider init failed: %s", e)

    def shutdown(self) -> None:
        """Join any in-flight prefetch thread and clear pending state."""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        # V4.4 Phase2c: clear pending recall keywords on shutdown
        with self._pending_recall_lock:
            self._pending_recall_keywords.clear()

    # ── System prompt ─────────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        try:
            impl = _impl_cache
            count = impl["database"].get_chunk_count()
            self._chunk_count = count
        except Exception:
            count = 0
        if count == 0:
            return (
                "# Hermem Memory\n"
                "Active. Empty memory index — use hermem_add to store facts the user expects you to remember. "
                "Use hermem_search to recall past conversations, preferences, or decisions. "
                "Use hermem_stats to check memory health."
            )
        return (
            f"# Hermem Memory\n"
            f"Active. {count} chunks indexed with semantic recall. "
            f"Use hermem_search to find relevant past context. "
            f"Use hermem_add to store new facts. "
            f"Use hermem_stats to check health."
        )

    # ── Prefetch (background recall) ────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached prefetch result from the background thread."""
        if not query:
            return ""
        # If background thread is still running, return empty (don't block)
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=0.5)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        return result

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Fire background recall for the next turn's prefetch() call."""
        if not query:
            return

        # Cancel any in-flight prefetch from the previous turn
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=1.0)

        # Increment turn counter for Phase2c staleness tracking
        with self._pending_recall_lock:
            self._pending_recall_turn_counter += 1

        def _run():
            try:
                impl = _impl_cache
                results = impl["retrieval"].search(query, mode="hybrid", top_k=3)
                lines = []
                for r in results:
                    row = dict(r) if hasattr(r, 'keys') else r
                    content = row.get("content", "")
                    chunk_type = row.get("chunk_type", "fact")
                    concepts_raw = row.get("concepts", "")
                    concepts = ""
                    if concepts_raw:
                        try:
                            tags = json.loads(concepts_raw)
                            if tags:
                                concepts = " [" + ",".join(tags) + "]"
                        except Exception:
                            pass
                    lines.append(f"- [{chunk_type}]{concepts} {content}")

                # ── V4.4 Phase2c: drain pending recall keywords ──────────────────
                with self._pending_recall_lock:
                    current_turn = self._pending_recall_turn_counter
                    stale_threshold = current_turn - RECOLLECT_STALENESS_TURNS
                    # Filter: keep only non-stale keywords, unpack queued_at_turn
                    valid_keywords = [
                        kw for kw, queued_at in self._pending_recall_keywords
                        if queued_at >= stale_threshold
                    ]
                    self._pending_recall_keywords.clear()

                # Retrieve for each pending keyword (with per-keyword timeout)
                recall_lines = []
                for kw in valid_keywords[:MAX_PENDING_RECALL_KEYWORDS]:
                    kw_results = []
                    kw_done = threading.Event()
                    def _search_kw():
                        nonlocal kw_results
                        try:
                            kw_results = impl["retrieval"].search(kw, mode="hybrid", top_k=2)
                        except Exception:
                            pass
                        finally:
                            kw_done.set()
                    t = threading.Thread(target=_search_kw, daemon=True)
                    t.start()
                    kw_done.wait(timeout=RECOLLECT_TIMEOUT_PER_KEYWORD)
                    for r in kw_results:
                        row = dict(r) if hasattr(r, 'keys') else r
                        content = row.get("content", "")
                        chunk_type = row.get("chunk_type", "fact")
                        recall_lines.append(f"  → [{chunk_type}] {content}")
                if recall_lines:
                    lines.append(f"[v4-recall] {'; '.join(valid_keywords[:5])}")
                    lines.extend(recall_lines)
                # ── end V4.4 Phase2c ───────────────────────────────────────────

                # Phase 3 dispositions (V4.2 — condition→prediction behavior patterns)
                # V4.4: 同时缓存这些 disposition，供下一轮 satisfaction check 使用
                retrieved_dispositions = []
                try:
                    import sys as _sys
                    p3_path = os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
                    if p3_path not in _sys.path:
                        _sys.path.insert(0, p3_path)
                    from impl.l1_search import retrieve as p3_retrieve
                    p3_result = p3_retrieve(query, disposition_k=2)
                    retrieved_dispositions = p3_result.get("dispositions", [])
                    for d in retrieved_dispositions:
                        conf = d.get("confidence", 1.0)
                        err = d.get("error_count", 0)
                        # 低置信度 disposition 标注不确定性
                        uncertain_tag = "⚠️不确定" if conf < 0.7 else ""
                        lines.append(
                            f"- [disposition{uncertain_tag}] IF: {d['condition'][:45]}... "
                            f"THEN: {d['prediction'][:45]}... (conf={conf:.1f}, err={err})"
                        )
                except Exception:
                    pass  # Phase 3 not available or no dispositions — skip silently

                # V4.4: 缓存 prefetch 召回的 disposition，供下一轮 satisfaction check
                if retrieved_dispositions:
                    with self._prefetch_lock:
                        self._last_activated_dispositions = list(retrieved_dispositions)
                        # V4.5: 同步提取 ID，供 satisfaction check 后的 success 增量用
                        self._last_activated_disposition_ids = [
                            d.get("id") or d.get("disposition_id")
                            for d in retrieved_dispositions
                            if d.get("id") or d.get("disposition_id")
                        ]
                        logger.debug(
                            "[Hermem V4.4] prefetch cached %d dispositions for satisfaction check",
                            len(retrieved_dispositions),
                        )

                # V4.2 Step2: inject session-level active dispositions (from correction recall)
                active_dispositions = getattr(self, "_active_dispositions", [])
                for d in active_dispositions:
                    conf = d.get("confidence", 1.0)
                    uncertain_tag = "⚠️不确定" if conf < 0.7 else ""
                    lines.append(
                        f"- [active_disposition{uncertain_tag}] IF: {d['condition'][:45]}... "
                        f"THEN: {d['prediction'][:45]}... (conf={conf:.1f})"
                    )

                with self._prefetch_lock:
                    if lines:
                        self._prefetch_result = "<hermem-context>\n" + "\n".join(lines) + "\n</hermem-context>"
            except Exception as e:
                logger.debug("Hermem prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="hermem-prefetch"
        )
        self._prefetch_thread.start()

    # ── Correction detection tiers ─────────────────────────────────────────────

    @staticmethod
    def _correction_tier(user_content: str, assistant_content: str = "") -> str:
        """
        Eight-trigger correction/error detection:
        - 'strong': unambiguous correction OR agent self-signaled error — annotate + recall
        - 'medium': ambiguous or implicit correction — LLM judgment needed
        - 'weak': possible correction but low confidence — log only
        - 'none': no signal

        Triggers (L1, 8 conditions):
          A1. User explicit negation: "不对","不是","错了"...
          A2. User partial correction: "对，但是…","不完全对"...
          B1. Agent self-corrects: "让我修正","等等，我重新说"...
          B2. Agent expresses uncertainty: "不确定","可能不对"...
          B3. Agent gives up: "我做不到","无法完成"...
          C1. System LLM error  (via on_llm_error hook)
          C2. System tool error (via on_tool_error hook)
          C3. Session end with no corrections (via on_session_end)
        """
        import re

        # ── A1: User explicit negation (strong) ────────────────────────────
        strong_signals = (
            "不对", "不是", "错了", "重新", "再来一遍",
            "不对不对", "错了错了", "不是这个", "重新来",
            "你搞错了", "你想错了", "我说的是", "我是说",
        )
        if any(sig in user_content for sig in strong_signals):
            return "strong"

        # ── A2: User partial correction (strong) ───────────────────────────
        partial_signals = ("对，但是", "对，不过", "不完全对", "部分对",
                           "基本上对", "大致对", "你说的对，但是")
        if any(sig in user_content for sig in partial_signals):
            return "strong"

        # ── B1: Agent self-corrects (strong) ────────────────────────────────
        if assistant_content:
            b1_signals = ("让我修正", "等等我重新", "抱歉我理解错了",
                          "我重新说", "让我重新理解", "我之前的理解有误")
            if any(sig in assistant_content for sig in b1_signals):
                return "strong"

            # ── B2: Agent expresses uncertainty (medium) ─────────────────
            b2_signals = ("不确定", "可能不对", "我不确定", "可能有问题",
                          "也许我错了", "需要确认一下")
            if any(sig in assistant_content for sig in b2_signals):
                return "medium"

            # ── B3: Agent gives up (strong) ────────────────────────────────
            b3_signals = ("我做不到", "我无法完成", "我没法做",
                          "这个我做不了", "超出我的能力", "暂时无法完成")
            if any(sig in assistant_content for sig in b3_signals):
                return "strong"

        # ── Medium: more contextual correction language ────────────────────
        medium_signals = (
            "等等", "停", "等一下",
            "刚才说的", "之前说的",
            "我想表达的是", "我的意思是",
            "不是那样", "不是这样",
            "你理解错了", "理解错了",
            "方向不对", "思路不对",
        )
        if any(sig in user_content for sig in medium_signals):
            return "medium"

        # ── Weak: negation without question — possible implicit correction ──
        negation_patterns = re.findall(
            r"(不(?:是|对|好|行|要|该)|没(?:有)?|别|不要|不想|不应)", user_content
        )
        question_words = re.findall(r"(吗|呢|怎么|为什么)", user_content)
        if negation_patterns and not question_words:
            return "weak"

        return "none"

    def _recall_dispositions_for_correction(self, user_content: str) -> None:
        """Phase 3 dispositions recall for correction content."""
        import sys as _sys, os as _os
        try:
            p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.l1_search import retrieve as p3_retrieve
            result = p3_retrieve(user_content, disposition_k=3)
            dispositions = result.get("dispositions", [])
            if dispositions:
                self._active_dispositions = dispositions
                # V4.5: 同步提取 ID，供 satisfaction check 后的 success 增量用
                self._last_activated_disposition_ids = [
                    d.get("id") or d.get("disposition_id")
                    for d in dispositions
                    if d.get("id") or d.get("disposition_id")
                ]
                logger.info(
                    "Hermem V4.2: correction detected (tier=strong), activated %d dispositions",
                    len(dispositions),
                )
        except Exception as e:
            logger.debug("Hermem V4.2: disposition recall failed: %s", e)

    # ── V4.4: Disposition Satisfaction Check ─────────────────────────────────

    NEGATION_PATTERNS = (
        r"不对", r"不是", r"错了", r"不正确", r"错误", r"不对吧",
        r"你错了", r"重新来", r"等等", r"回退", r"重新做", r"重新生成",
        r"不完全对", r"部分对", r"对但是", r"对不过",
    )
    TOPIC_OVERLAP_THRESHOLD = 0.25  # Jaccard 阈值，越低越宽松

    def _extract_topic_keywords(self, text: str) -> set[str]:
        """从文本中提取关键词（中文按字符/bigram，英文分词）。用于话题延续判断。"""
        import re as _re
        chinese_chars = _re.findall(r"[\u4e00-\u9fff]+", text)
        chinese_words = set()
        for chunk in chinese_chars:
            for i in range(len(chunk)):
                chinese_words.add(chunk[i].lower())
            for i in range(len(chunk) - 1):
                chinese_words.add(chunk[i:i+2].lower())
        english_words = set(w.lower() for w in _re.findall(r"[a-zA-Z]+", text))
        stopwords = {
            "的", "了", "是", "在", "我", "你", "他", "她", "它", "这", "那",
            "有", "说", "也", "不", "就", "都", "啊", "呢", "吧", "吗", "哦",
            "和", "与", "或", "但", "而", "着", "过", "被", "把", "给", "让",
            "the", "a", "an", "and", "or", "but", "to", "for", "of", "with",
            "is", "are", "was", "were", "be", "been", "i", "you", "he", "she",
            "it", "we", "they", "what", "which", "that", "this",
        }
        all_words = chinese_words | english_words
        return {w for w in all_words if w not in stopwords and len(w) > 1}

    def _evaluate_last_turn_satisfaction(self, current_user_message: str) -> None:
        """
        V4.4 验证段：在收到新用户消息时，判断上一轮激活的 disposition 预测是否被满足。

        规则（优先级递减）：
          1. 当前消息含明确否定词 → 不更新（correction tier 会处理 error++)
          2. 当前消息与上一轮话题延续（关键词 Jaccard >= 阈值）→ success++
          3. 其他（话题切换或无法判断）→ 不更新

        这个方法在 sync_turn() 开头调用，先于 correction tier 处理。
        """
        import re as _re

        last_dispositions = getattr(self, "_last_activated_dispositions", [])
        if not last_dispositions:
            return

        last_user_msg = getattr(self, "_last_turn_user_message", "") or ""

        # ── 1. 否定词检测 ──
        # 如果当前消息包含否定词，说明用户在纠正上一轮的 prediction，
        # 视为"未满足"——但 error_count 已在 correction tier 处理了，这里直接清缓存退出
        if any(_re.search(pat, current_user_message) for pat in self.NEGATION_PATTERNS):
            logger.debug(
                "[Hermem V4.4] satisfaction: negation detected — skip success, clear cache"
            )
            self._last_activated_dispositions = []
            self._last_turn_user_message = ""
            self._last_turn_topic_keywords = set()
            return

        # ── 2. 话题延续判断 ──
        if not last_user_msg:
            # 没有上一轮用户消息记录，无法判断
            self._last_activated_dispositions = []
            self._last_turn_user_message = ""
            self._last_turn_topic_keywords = set()
            return

        # 提取关键词并计算 Jaccard 重叠
        prev_kw = self._extract_topic_keywords(last_user_msg)
        curr_kw = self._extract_topic_keywords(current_user_message)

        if not prev_kw or not curr_kw:
            overlap = 0.0
        else:
            overlap = len(prev_kw & curr_kw) / len(prev_kw | curr_kw)

        if overlap < self.TOPIC_OVERLAP_THRESHOLD:
            # 话题切换，不更新
            logger.debug(
                "[Hermem V4.4] satisfaction: topic shift (overlap=%.2f < %.2f) — skip",
                overlap, self.TOPIC_OVERLAP_THRESHOLD,
            )
            self._last_activated_dispositions = []
            self._last_turn_user_message = ""
            self._last_turn_topic_keywords = set()
            return

        # ── 3. 预测被满足：累加 success_count ──
        disposition_ids = [d.get("id") for d in last_dispositions if d.get("id")]
        if not disposition_ids:
            return

        try:
            import sys as _sys, os as _os
            p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.disposition_updater import increment_success_by_ids
            updated = increment_success_by_ids(
                disposition_ids,
                self._current_session_id or "unknown",
            )
            logger.info(
                "[Hermem V4.4] satisfaction: success_count += 1 (dispositions=%d, updated=%d, overlap=%.2f)",
                len(disposition_ids), updated, overlap,
            )
        except Exception as e:
            logger.debug("[Hermem V4.4] increment_success_by_ids failed: %s", e)
        finally:
            # 无论如何，清除缓存避免重复更新
            self._last_activated_dispositions = []
            self._last_turn_user_message = ""
            self._last_turn_topic_keywords = set()

    # ── V4.3 B2b: 轻量级 annotation ─────────────────────────────────────

    def _build_recent_summary(self, max_messages: int = 6) -> str:
        """从 session 缓冲区提取最近 N 轮对话，生成简洁摘要供 annotation 使用。"""
        if not self._session_messages:
            return ""
        recent = self._session_messages[-max_messages:]
        lines = []
        for msg in recent:
            role = msg["role"].capitalize()
            content = msg["content"]
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role}: {content}")
        return "（以下是最近的对话片段，用于快速预测误差分析）\n" + "\n".join(lines)

    def _trigger_lightweight_annotation(
        self,
        trigger_type: str = "correction",
        extra: str = "",
    ) -> None:
        """在 strong-tier correction 发生时，触发轻量级 annotation 入队。

        Args:
            trigger_type: 触发类型标识 — "correction" | "llm_error" | "tool_error" | "session_end_no_correction"
            extra: 附加上下文，会拼接到 session_summary 头部
        """
        import datetime as _dt

        if not self._current_session_id:
            return
        # 防抖：距离上次触发不足 10 秒则跳过
        if (self._last_annotation_trigger is not None and
                (_dt.datetime.now() - self._last_annotation_trigger).total_seconds() < 10):
            return
        self._last_annotation_trigger = _dt.datetime.now()

        summary = self._build_recent_summary(max_messages=6)
        if not summary:
            return

        # 头部加上触发类型和 extra 上下文（供 annotation prompt 使用）
        if trigger_type != "correction" or extra:
            header = f"[触发类型: {trigger_type}]"
            if extra:
                header += f" {extra}"
            summary = f"{header}\n\n{summary}"

        try:
            import sys as _sys, os as _os
            from datetime import datetime as _dt
            p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.l0_store import save_l0_raw, annotate_l0_after_l1_v2
            from impl.disposition_updater import update_dispositions_from_errors
            from impl.config import ERROR_ANNOTATION_MODEL

            # ── B4 fix: 实时写 L0 JSON ──────────────────────────────
            # L0 JSON 原为 batch 产物，实时 correction 路径需要实时写入。
            # 否则 worker 读不到 L0 文件导致 annotation 失败。
            if self._session_messages:
                now = _dt.now()
                session_start = self._session_messages[0].get("timestamp") or now.isoformat()
                save_l0_raw(
                    session_id=self._current_session_id,
                    messages=self._session_messages,
                    start=session_start,
                    end=now.isoformat(),
                )
            # ── end B4 fix ──────────────────────────────────────────

            # V4.3 B2: 同步调用 annotation（方案A）
            # 替换原异步 enqueue_annotation()，直接同步执行 annotation +
            # disposition 更新，同一轮内立即生效。
            annotation = annotate_l0_after_l1_v2(
                session_id=self._current_session_id,
                session_summary=summary,
                l1_facts=None,
                force=False,
                annotation_model=ERROR_ANNOTATION_MODEL,
            )
            if annotation and annotation.get("prediction_errors"):
                update_dispositions_from_errors(self._current_session_id, annotation)
                logger.info(
                    "[Hermem] V4.3 B2: disposition updated (errors=%d, surprise=%s)",
                    len(annotation.get("prediction_errors", [])),
                    annotation.get("surprise_level"),
                )
            else:
                logger.debug("[Hermem] V4.3 B2: no prediction errors in annotation")
        except Exception as e:
            logger.debug("[Hermem] V4.3 B2: sync annotation failed: %s", e)

    # ── Turn sync ────────────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """
        V4.2 Error-Activated Retrieval:
        - Tier 1 (strong): immediate recall
        - Tier 2 (medium): LLM-judged, then recall
        - Tier 3 (weak): log only for pattern learning
        - Decay: clear after 2 turns of no correction signal

        V4.3 B2b: Session buffer management + lightweight annotation enqueue

        V4.4: Verification loop — 在处理当轮之前，先判断上一轮 disposition 是否被满足
        """
        # ── V4.4: 验证段 — 先于所有其他逻辑判断上一轮 satisfaction ─────────────
        self._evaluate_last_turn_satisfaction(user_content)
        # 缓存当前轮用户消息（供下一轮判断话题延续）
        self._last_turn_user_message = user_content
        self._last_turn_topic_keywords = self._extract_topic_keywords(user_content)
        # ───────────────────────────────────────────────────────────────────────

        # ── V4.3 B2b: 管理 session 缓冲区 ──────────────────────────────
        import datetime as _dt
        if not session_id:
            session_id = "weixin_" + str(hash(user_content + assistant_content))[:16]
        if self._current_session_id != session_id:
            # 新 session，重置缓冲区
            self._current_session_id = session_id
            self._session_messages = []
            self._last_annotation_trigger = None
            # V5: 重置 active retrieval 状态
            self._v5_injected_chunk_ids = set()
            self._v5_medium_tracker = {}
            self._v5_retrieve_count = 0
        now_iso = _dt.datetime.now().isoformat()
        self._session_messages.append({"role": "user", "content": user_content, "timestamp": now_iso})
        self._session_messages.append({"role": "assistant", "content": assistant_content, "timestamp": now_iso})
        # ───────────────────────────────────────────────────────────────

        logger.info("Hermem sync_turn called [tier=%s]", self._correction_tier(user_content, assistant_content))
        tier = self._correction_tier(user_content, assistant_content)

        if tier == "strong":
            # V4.3 B2: 先同步更新 disposition，再读取（同一轮内立即生效）
            self._trigger_lightweight_annotation(trigger_type="correction")
            self._recall_dispositions_for_correction(user_content)
            # V4.4: 缓存本轮激活的完整 disposition 列表，供下一轮 satisfaction 判断
            self._last_activated_dispositions = list(self._active_dispositions)

        elif tier == "medium":
            # Defer to LLM judgment (async in background to avoid blocking sync)
            def _medium_check():
                try:
                    import numpy as _np
                    impl = _impl_cache
                    emb_raw = impl["embedding"].get_embedding_cached(user_content)
                    if not emb_raw or not emb_raw[0]:
                        return
                    emb = _np.array(emb_raw[0], dtype=np.float64)
                    ref_raw = impl["embedding"].get_embedding_cached("你说的不对，刚才的意思不是这样")
                    if not ref_raw or not ref_raw[0]:
                        return
                    ref = _np.array(ref_raw[0], dtype=np.float64)
                    sim = float(
                        _np.dot(emb, ref)
                        / (_np.linalg.norm(emb) * _np.linalg.norm(ref) + 1e-8)
                    )
                    if sim > 0.75:
                        self._recall_dispositions_for_correction(user_content)
                        # V4.4: 缓存确认的 disposition 列表
                        self._last_activated_dispositions = list(self._active_dispositions)
                        logger.debug("Hermem V4.2: medium-tier correction confirmed (sim=%.3f)", sim)
                except Exception as e:
                    logger.debug("Hermem V4.2: medium-tier check failed: %s", e)

            threading.Thread(target=_medium_check, daemon=True, name="hermem-medium-check").start()

        # ── 方案 A: LLM 隐式纠正检测 ─────────────────────────────
        # 规则未捕获的隐式纠正（weak/none tier），用 qwen3.5:2b 做二分类。
        # 包含最近一次 AI 回复作为上下文，提升判断准确率。
        if tier in ("weak", "none"):
            recent_assistant = ""
            for msg in reversed(self._session_messages):
                if msg.get("role") == "assistant":
                    recent_assistant = msg.get("content", "")[:200]
                    break

            def _implicit_correction_check(
                _uc: str, _ac: str, _sid: str, _msgs: list
            ):
                try:
                    import sys as _sys, os as _os
                    p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
                    if p3_path not in _sys.path:
                        _sys.path.insert(0, p3_path)
                    from impl.utils import llm_generate_ollama

                    prompt = (
                        "判断下面这段对话中，用户是否在否定、纠正或质疑 AI 助手刚才说的话？\n"
                        "只回答 YES 或 NO，不要解释。\n\n"
                        f"AI 助手刚才说：{_ac}\n\n"
                        f"用户现在说：{_uc}"
                    )
                    answer = llm_generate_ollama(
                        prompt,
                        model="qwen3.5:4b-no-think",
                    ).strip().upper()
                    if "YES" in answer:
                        logger.info(
                            "[Hermem] 方案A: LLM detected implicit correction "
                            "(tier=%s, answer=%s). Triggering annotation.",
                            tier, answer,
                        )
                        # 升级为 strong，触发轻量 annotation
                        self._trigger_lightweight_annotation(
                            trigger_type="implicit_correction",
                            extra=f"[LLM判断tier={tier}]",
                        )
                except Exception as _e:
                    logger.debug("[Hermem] 方案A: implicit correction check failed: %s", _e)

            threading.Thread(
                target=_implicit_correction_check,
                args=(user_content, recent_assistant,
                      self._current_session_id, self._session_messages),
                daemon=True,
                name="hermem-implicit-correction",
            ).start()
        # ── end 方案 A ─────────────────────────────────────────

        elif tier == "weak":
            logger.debug("Hermem V4.2: weak-tier signal detected — logging: %s", user_content[:50])

        # V4.3 方案 A（旧逻辑，已被 V4.4 satisfaction check 替代，保留作为 fallback）：
        # 当 tier=="none" 且有上一轮激活的 disposition 时，追加 success
        # 注意：这里用 _last_activated_disposition_ids（旧代码遗留），新路径用 _last_activated_dispositions
        if tier == "none" and self._last_activated_disposition_ids:
            try:
                import sys as _sys, os as _os
                p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
                if p3_path not in _sys.path:
                    _sys.path.insert(0, p3_path)
                from impl.disposition_updater import increment_success_by_ids
                updated = increment_success_by_ids(
                    self._last_activated_disposition_ids,
                    self._current_session_id or "unknown",
                )
                if updated > 0:
                    logger.info(
                        "[Hermem] V4.3 方案 A fallback: success_count += 1 (dispositions=%d, updated=%d)",
                        len(self._last_activated_disposition_ids),
                        updated,
                    )
            except Exception as e:
                logger.debug("[Hermem] V4.3 方案 A fallback: %s", e)
            finally:
                self._last_activated_disposition_ids = []

        # Decay
        if tier == "none" and self._active_dispositions:
            self._active_dispositions_cold = True

        if getattr(self, "_active_dispositions_cold", False) and tier == "none":
            self._active_dispositions = []
            self._active_dispositions_cold = False

        # ── V4.4 Phase1: Per-turn Judgment ─────────────────────────────────
        self._v4_turn_counter = getattr(self, "_v4_turn_counter", 0) + 1
        if self._should_sample_judgment(user_content, self._v4_turn_counter):
            threading.Thread(
                target=self._trigger_turn_judgment,
                args=(user_content, assistant_content, self._v4_turn_counter),
                daemon=True,
                name="hermem-turn-judgment",
            ).start()
        # ── end V4.4 Phase1 ───────────────────────────────────────────────

        # ── V5: Active Retrieval ───────────────────────────────────────────
        self._v5_retrieve_count += 1
        self._v5_active_retrieval(user_content)
        # ── end V5 ───────────────────────────────────────────────────────

    # ── V4.4 Phase1: Per-turn Judgment ────────────────────────────────────────

    V4_JUDGMENT_FEW_SHOT = """你是一个轻量级的对话记忆判断模型。对每条对话，判断是否需要记忆动作。

判断标准：
- new_fact_to_l1: 这条对话是否包含值得写入长期记忆的事实？（用户偏好、项目状态、决策结论等）
- needs_recall: 当前对话是否需要召回历史记忆来回复？
- recall_keywords: 如果需要召回，列出3-5个关键词/短语（用于语义检索）

输出格式（严格JSON，不要任何其他内容）：
{
  "new_fact_to_l1": true或false,
  "needs_recall": true或false,
  "recall_keywords": ["关键词1", "关键词2"]
}

示例1（需要recall）：
对话：用户说"帮我查一下hermes agent的项目结构和定时任务"
{
  "new_fact_to_l1": false,
  "needs_recall": true,
  "recall_keywords": ["hermes项目结构", "文件列表", "定时任务", "cron"]
}

示例2（包含新事实）：
对话：用户说"简直太有意思了，一个以超越 Claude Code 为目的的自主管理 agent，出自于 MiniMax"
{
  "new_fact_to_l1": true,
  "needs_recall": false,
  "recall_keywords": []
}

示例3（无新事实，无recall需求）：
对话：用户说"能先跑一次昨天24小时的内容吗？"
{
  "new_fact_to_l1": false,
  "needs_recall": true,
  "recall_keywords": ["昨天24小时", "内容摘要", "定时任务"]
}

示例4（opinion，需要recall历史opinion）：
对话：用户说"使用相同的大模型，感觉hermes的编程能力不如claude code。你怎么看待这个评价？"
{
  "new_fact_to_l1": false,
  "needs_recall": true,
  "recall_keywords": ["hermes编程能力", "claude code对比", "用户评价"]
}

现在判断：
"""

    def _should_sample_judgment(self, user_content: str, turn_counter: int) -> bool:
        """V4.4 Phase1 采样策略：每3轮一次 或 用户消息>200字触发。"""
        # 每3轮强制采样
        if turn_counter % 3 == 0:
            return True
        # 长消息强制采样（可能包含重要事实）
        if len(user_content) > 200:
            return True
        return False

    def _trigger_turn_judgment(self, user_content: str, assistant_content: str, turn_counter: int) -> None:
        """V4.4 Phase1: 调用 LLM 生成 per-turn judgment，写入 turn_judgments.jsonl。"""
        import datetime as _dt, json as _json, re as _re, pathlib

        # 组合对话文本（取最新一轮）
        dialogue = f"用户说：{user_content}"
        if assistant_content:
            dialogue += f"\n助手说：{assistant_content[:300]}"

        prompt = self.V4_JUDGMENT_FEW_SHOT + dialogue + "\n"

        try:
            # 使用 qwen3.5:4b-no-think 通过原生端点（OpenAI兼容端点对该模型返回空）
            import sys as _sys, os as _os
            p3_path = _os.path.expanduser("~/.hermes/projects/hermem-github/phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.utils import llm_generate_ollama as _llm_generate
            resp_text = _llm_generate(prompt, model="qwen3.5:4b-no-think", temperature=0.3, max_tokens=256)
            # 去掉可能的 markdown 包裹
            resp_text = resp_text.strip()
            if resp_text.startswith("```"):
                resp_text = _re.sub(r"^```json?\s*", "", resp_text)
            if resp_text.endswith("```"):
                resp_text = resp_text[:-3].strip()

            result = _json.loads(resp_text)
            # 确保字段非空
            if not isinstance(result.get("new_fact_to_l1"), bool):
                result["new_fact_to_l1"] = False
            if not isinstance(result.get("needs_recall"), bool):
                result["needs_recall"] = False
            if not isinstance(result.get("recall_keywords"), list):
                result["recall_keywords"] = []

            # ── V4.4 Phase2c: enqueue recall keywords ─────────────────────────
            if result.get("needs_recall") and result.get("recall_keywords"):
                keywords = result["recall_keywords"][:MAX_PENDING_RECALL_KEYWORDS]
                with self._pending_recall_lock:
                    queued_turn = self._pending_recall_turn_counter
                    for kw in keywords:
                        if not any(k == kw and q == queued_turn for k, q in self._pending_recall_keywords):
                            self._pending_recall_keywords.append((kw, queued_turn))
                logger.info(
                    "[Hermem V4.4] Phase2c enqueued %d recall keywords (kw=%s)",
                    len(keywords), keywords[:3],
                )
            # ── end V4.4 Phase2c ───────────────────────────────────────────

            # Write to turn_judgments.jsonl
            journal_dir = pathlib.Path.home() / ".hermes" / "memory"
            journal_dir.mkdir(exist_ok=True)
            journal_path = journal_dir / "turn_judgments.jsonl"
            entry = {
                "timestamp": _dt.datetime.now().isoformat(),
                "session_id": self._current_session_id or "unknown",
                "turn_counter": turn_counter,
                "user_content_preview": user_content[:100],
                "judgment": result,
            }
            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info(
                "[Hermem V4.4] judgment: new_fact=%s recall=%s kw=%s",
                result["new_fact_to_l1"],
                result["needs_recall"],
                result.get("recall_keywords", [])[:3],
            )

        except Exception as e:
            logger.debug("[Hermem V4.4] judgment failed: %s", e)

    # ── V4.3 C1/C2: System error hooks — trigger annotation directly ──────────

    def on_llm_error(self, error_message: str = "", *, session_id: str = "") -> None:
        """LLM call failed or timed out — trigger annotation (C1)."""
        import datetime as _dt
        if not self._current_session_id and not session_id:
            return
        sid = session_id or self._current_session_id
        self._current_session_id = sid
        if not self._session_messages:
            return
        try:
            self._trigger_lightweight_annotation(
                trigger_type="llm_error",
                extra=f"LLM错误信息: {error_message[:200]}" if error_message else "LLM调用失败/超时",
            )
            logger.warning("[Hermem] V4.3 C1: llm_error triggered annotation (msg=%s)", error_message[:80])
        except Exception as e:
            logger.debug("[Hermem] on_llm_error failed: %s", e)

    def on_tool_error(self, tool_name: str = "", error_message: str = "", *, session_id: str = "") -> None:
        """Tool execution failed — trigger annotation (C2)."""
        if not self._current_session_id and not session_id:
            return
        sid = session_id or self._current_session_id
        self._current_session_id = sid
        if not self._session_messages:
            return
        try:
            self._trigger_lightweight_annotation(
                trigger_type="tool_error",
                extra=f"工具'{tool_name}'错误: {error_message[:200]}" if tool_name else f"工具错误: {error_message[:200]}",
            )
            logger.warning("[Hermem] V4.3 C2: tool_error triggered annotation (tool=%s, msg=%s)", tool_name, error_message[:80])
        except Exception as e:
            logger.debug("[Hermem] on_tool_error failed: %s", e)

    # ── C3: Session end annotation ────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Auto-extract a session summary + C3: annotate if no corrections were triggered."""
        import datetime as _dt

        # ── C3: session-end annotation if no corrections fired in this session ──
        # Only annotate sessions with enough content (>=3 messages) that had
        # zero correction triggers — these are "silent low-quality" candidates.
        try:
            if self._session_messages and len(self._session_messages) >= 6:
                # Check if any correction was triggered in this session
                had_correction = any(
                    self._correction_tier(m.get("content", ""), "")
                    in ("strong", "medium", "weak")
                    for m in self._session_messages
                    if m.get("role") == "user"
                )
                if not had_correction:
                    self._trigger_lightweight_annotation(
                        trigger_type="session_end_no_correction",
                        extra="会话结束前无任何correction触发 — 采样标注",
                    )
                    logger.info("[Hermem] V4.3 C3: session-end annotation fired (no correction detected)")
        except Exception as e:
            logger.debug("[Hermem] V4.3 C3: session-end annotation failed: %s", e)
        # ── end C3 ────────────────────────────────────────────────────────────
        try:
            impl = _impl_cache
            # Extract plain text from messages
            text_parts = []
            for m in messages:
                role = m.get("role", "unknown")
                content = m.get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text_parts.append(f"{role}: {c['text']}")
                elif isinstance(content, str):
                    text_parts.append(f"{role}: {content}")
            full_text = "\n".join(text_parts)
            if len(full_text) < 100:
                return

            # Generate summary via MiniMax API
            summary_text, concepts = _generate_summary_via_minimax(full_text)
            if not summary_text:
                return

            # Embed and store
            emb, _ = impl["embedding"].get_embedding_cached(summary_text)
            chunk_id = impl["database"].insert_chunk(
                session_id="session_end",
                content=summary_text,
                chunk_type="session_summary",
                concepts=concepts,
                source_file=None,
                source_line=0,
                vec_index=None,
            )
            impl["vectorstore"].append_vectors([emb])
            logger.debug("Hermem session_summary stored (chunk_id=%d)", chunk_id)
        except Exception as e:
            logger.debug("Hermem on_session_end failed: %s", e)
        finally:
            # V4.4 Phase2c: always clear pending recall keywords on session end
            with self._pending_recall_lock:
                self._pending_recall_keywords.clear()
            # Also reset session buffer
            self._session_messages = []
            self._current_session_id = None

    # ── Tools ────────────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [HERMEM_SEARCH_SCHEMA, HERMEM_ADD_SCHEMA, HERMEM_FORGET_SCHEMA, HERMEM_STATS_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        impl = _impl_cache

        if tool_name == "hermem_search":
            query = args.get("query", "")
            mode = args.get("mode", "hybrid")
            top_k = int(args.get("top_k", 5))
            if not query:
                return tool_error("hermem_search requires a non-empty 'query' argument.")
            try:
                results = impl["retrieval"].search(query, mode=mode, top_k=top_k)
                if not results:
                    return json.dumps({"chunks": [], "message": "No matching memories found."})
                chunks = []
                for r in results:
                    # sqlite3.Row doesn't have .get() — convert to dict
                    row = dict(r) if hasattr(r, 'keys') else r
                    chunks.append({
                        "id": row["id"],
                        "content": row["content"],
                        "chunk_type": row.get("chunk_type", ""),
                        "concepts": _parse_concepts(row.get("concepts", "")),
                        "session_id": row.get("session_id", ""),
                    })
                return json.dumps({"chunks": chunks}, ensure_ascii=False)
            except Exception as e:
                return tool_error(f"hermem_search failed: {e}")

        elif tool_name == "hermem_add":
            content = args.get("content", "")
            if not content:
                return tool_error("hermem_add requires a non-empty 'content' argument.")
            concepts_str = args.get("concepts", "")
            concepts = [c.strip() for c in concepts_str.split(",") if c.strip()] if concepts_str else []
            chunk_type = args.get("chunk_type", "fact")

            try:
                emb, _ = impl["embedding"].get_embedding_cached(content)
                chunk_id = impl["database"].insert_chunk(
                    session_id="tool_call",
                    content=content,
                    chunk_type=chunk_type,
                    concepts=concepts,
                    source_file=None,
                    source_line=0,
                    vec_index=None,
                )
                impl["vectorstore"].append_vectors([emb])
                return json.dumps({"chunk_id": chunk_id, "message": "Memory stored."}, ensure_ascii=False)
            except Exception as e:
                return tool_error(f"hermem_add failed: {e}")

        elif tool_name == "hermem_forget":
            query = args.get("query", "")
            if not query:
                return tool_error("hermem_forget requires a non-empty 'query' argument.")
            try:
                results = impl["retrieval"].search(query, mode="hybrid", top_k=1)
                if not results:
                    return tool_error("No matching memory found to delete.")
                to_delete = results[0]
                impl["database"].delete_chunk(to_delete["id"])
                return json.dumps({"deleted_id": to_delete["id"], "content": to_delete["content"][:80]}, ensure_ascii=False)
            except Exception as e:
                return tool_error(f"hermem_forget failed: {e}")

        elif tool_name == "hermem_stats":
            try:
                count = impl["database"].get_chunk_count()
                health = impl["embedding"].is_ollama_healthy()
                vec_stats = impl["vectorstore"].get_stats()
                emb_cache = impl["database"].get_cache_stats()
                return json.dumps({
                    "total_chunks": count,
                    "ollama_healthy": health.get("healthy", False),
                    "model_installed": health.get("model_installed", False),
                    "ollama_latency_ms": health.get("latency_ms"),
                    "vector_count": vec_stats.get("total_vectors", 0),
                    "embedding_cache_entries": emb_cache.get("total_entries", 0),
                }, ensure_ascii=False)
            except Exception as e:
                return tool_error(f"hermem_stats failed: {e}")

        return tool_error(f"Unknown tool: {tool_name}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_concepts(concepts_raw: str) -> List[str]:
    if not concepts_raw:
        return []
    try:
        tags = json.loads(concepts_raw)
        return tags if isinstance(tags, list) else []
    except Exception:
        return []


def _generate_summary_via_minimax(text: str, api_key: Optional[str] = None) -> tuple[str, list]:
    """Generate session summary using MiniMax API."""
    if api_key is None:
        env_path = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k in ("MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"):
                        api_key = v.strip()
                        break

    if not api_key:
        return "", []

    try:
        import requests
        url = "https://api.minimaxi.com/anthropic/v1/messages"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "MiniMax-M2.7",
            "max_tokens": 800,
            "messages": [{"role": "user", "content": (
                f"请为以下对话生成50字以内的中文摘要，并提取3-5个概念标签（格式：标签1,标签2,标签3...）。\n\n"
                f"摘要：\n概念标签：\n\n对话内容：\n{text[:4000]}"
            )}],
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            return "", []
        result = resp.json()
        summary_text, concepts = None, []
        for block in result.get("content", []):
            if block.get("type") == "text":
                t = re.sub(r'\*\*', '', block.get("text", ""))
                if "概念标签：" in t:
                    parts = t.split("概念标签：")
                    summary_part = parts[0]
                    if "摘要：" in summary_part:
                        summary_text = summary_part.split("摘要：")[-1].strip()
                    concepts = [c.strip() for c in re.split(r'[,，]', parts[-1]) if c.strip()]
                elif "摘要：" in t and not summary_text:
                    summary_text = t.split("摘要：")[-1].strip()
        # Fallback: parse from thinking block if text block is empty
        if not summary_text:
            for block in result.get("content", []):
                if block.get("type") == "thinking":
                    m = re.search(r'摘要[（(]50字以内[）)][：:]\s*["""]([^"""]+)["""]', block.get("thinking", ""))
                    if m:
                        summary_text = m.group(1).strip()
        return summary_text or "", concepts
    except Exception:
        return "", []


# ── V4.3 方案 B: /feedback 命令 ──────────────────────────────────────────────

def _feedback_handler(raw_args: str) -> str | None:
    """Handle ``/feedback <text>``.

    Immediately enqueues a lightweight annotation for the current session,
    bypassing the 12-hour cron cycle. Works even when the gateway's live
    HermemMemoryProvider is in a different process — reads the current
    session state directly from the provider instance stored in sys.modules.
    """
    feedback = (raw_args or "").strip()
    if not feedback:
        return "Usage: /feedback <your correction or feedback>"

    try:
        # Access the live provider instance (loaded by the gateway)
        import sys as _sys
        hermem_mod = _sys.modules.get("plugins.memory.hermem")
        if not hermem_mod:
            return "[Hermem] /feedback unavailable: module not loaded"

        provider = getattr(hermem_mod, "_hermem_active_provider", None)
        if not provider:
            return "[Hermem] /feedback unavailable: no active session"

        session_id = provider._current_session_id or "manual-feedback"
        # Build a useful summary from the current session context
        recent = provider._session_messages[-6:] if provider._session_messages else []
        ctx_lines = []
        for msg in recent:
            role = msg.get("role", "?").capitalize()
            content = msg.get("content", "")[:200]
            ctx_lines.append(f"{role}: {content}")
        session_ctx = "\n".join(ctx_lines)

        summary = (
            f"[Manual /feedback]\n"
            f"User feedback: {feedback}\n"
            f"---\n"
            f"Recent session context:\n{session_ctx}"
        )

        # Enqueue via async_annotation (shared SQLite queue)
        import sys
        p3_path = str(Path.home() / ".hermes" / "projects" / "hermem" / "phase3")
        if p3_path not in sys.path:
            sys.path.insert(0, p3_path)
        from impl.async_annotation import enqueue_annotation_lightweight

        qsize = enqueue_annotation_lightweight(session_id, summary)

        return (
            f"[Hermem] Feedback queued (session={session_id}, queue depth={qsize})\n"
            f"Annotation will run on next worker drain."
        )

    except Exception as e:
        return f"[Hermem] /feedback error: {e}"


    # ── V5: Active Retrieval ───────────────────────────────────────────────

    def _v5_active_retrieval(self, user_message: str) -> None:
        """V5 主动检索：在对话过程中自动检索相关历史记忆。

        分层策略：
        - 高置信（≥0.85）：直接注入上下文
        - 中置信（0.65-0.85）：累积到 _v5_medium_tracker，相似度提升后注入
        - 低置信（<0.65）：忽略
        """
        import numpy as _np
        p3_path = str(Path.home() / ".hermes" / "projects" / "hermem" / "phase3")

        try:
            # 频率控制
            frequency = self._v5_get_frequency()
            if frequency > 0 and self._v5_retrieve_count % frequency != 0:
                return

            # 确保 phase3 路径可用
            import sys as _sys
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)

            from impl.config import (
                ACTIVE_RETRIEVAL_ENABLED,
                ACTIVE_RETRIEVAL_THRESHOLD_HIGH,
                ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM,
                ACTIVE_RETRIEVAL_TOP_K,
            )

            if not ACTIVE_RETRIEVAL_ENABLED:
                return

            from impl.embedding import get_embedding_cached
            from impl.vector_search import search_with_tier

            # 生成 query embedding
            emb_raw, _ = get_embedding_cached(user_message)
            q_emb = _np.array(emb_raw, dtype=_np.float32)

            # 分层检索
            high, medium = search_with_tier(q_emb, top_k=ACTIVE_RETRIEVAL_TOP_K)

            # 处理高置信
            for chunk in high:
                cid = chunk["chunk_id"]
                sim = chunk["similarity"]
                if cid not in self._v5_injected_chunk_ids:
                    self._v5_inject_chunk(chunk)
                    self._v5_injected_chunk_ids.add(cid)
                    self._v5_medium_tracker.pop(cid, None)
                    logger.info(
                        "[Hermem V5] 注入高置信 chunk [%s] sim=%.3f: %s",
                        cid, sim, chunk["content"][:40],
                    )

            # 处理中置信（累积）
            for chunk in medium:
                cid = chunk["chunk_id"]
                sim = chunk["similarity"]
                if cid in self._v5_medium_tracker:
                    self._v5_medium_tracker[cid] = max(self._v5_medium_tracker[cid], sim)
                else:
                    self._v5_medium_tracker[cid] = sim

            # 检查 medium_tracker 是否有达到注入阈值的
            self._v5_check_medium_injection()

        except Exception as e:
            logger.debug("[Hermem V5] active retrieval failed: %s", e)

    def _v5_get_frequency(self) -> int:
        """获取检索频率（每 N 条消息触发一次）。"""
        try:
            import sys as _sys
            p3_path = str(Path.home() / ".hermes" / "projects" / "hermem" / "phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.config import ACTIVE_RETRIEVAL_FREQUENCY
            return ACTIVE_RETRIEVAL_FREQUENCY
        except Exception:
            return 3  # 默认每 3 条消息触发

    def _v5_check_medium_injection(self) -> None:
        """检查 medium_tracker 中是否有 chunk 达到高置信阈值。"""
        try:
            import sys as _sys
            p3_path = str(Path.home() / ".hermes" / "projects" / "hermem" / "phase3")
            if p3_path not in _sys.path:
                _sys.path.insert(0, p3_path)
            from impl.config import ACTIVE_RETRIEVAL_THRESHOLD_HIGH
        except Exception:
            threshold = 0.85
        else:
            threshold = ACTIVE_RETRIEVAL_THRESHOLD_HIGH

        to_inject = [
            (cid, max_sim)
            for cid, max_sim in self._v5_medium_tracker.items()
            if max_sim >= threshold and cid not in self._v5_injected_chunk_ids
        ]

        for cid, max_sim in to_inject:
            self._v5_injected_chunk_ids.add(cid)
            self._v5_medium_tracker.pop(cid, None)
            logger.info(
                "[Hermem V5] 中置信累积触发注入 [%s] sim=%.3f",
                cid, max_sim,
            )

    def _v5_inject_chunk(self, chunk: dict) -> None:
        """将检索到的 chunk 直接注入到 prefetch result。"""
        sim = chunk.get("similarity", 0.0)
        injection = (
            f"\n\n[自动回忆 - 相似度 {sim:.2f}]\n"
            f"以下是从历史记忆中检索到的相关内容（可能相关，仅供参考）：\n"
            f"- {chunk['content']}\n"
        )
        with self._prefetch_lock:
            current = self._prefetch_result
            if current:
                self._prefetch_result = current + injection
            else:
                self._prefetch_result = injection
        logger.debug("[Hermem V5] injected: sim=%.2f, content=%s",
                    sim, chunk["content"][:40])


def register(ctx) -> None:
    """Register Hermem as a memory provider and expose /feedback slash command."""
    ctx.register_memory_provider(HermemMemoryProvider())
    ctx.register_command(
        name="feedback",
        handler=_feedback_handler,
        description="Submit real-time correction/feedback to Hermem's annotation pipeline",
        args_hint="<your feedback text>",
    )
    # Also store a reference so cli.py can reach the live instance
    import sys as _sysmod
    _mod = _sysmod.modules.get("plugins.memory.hermem")
    if _mod is not None:
        _mod._hermem_active_provider = HermemMemoryProvider()  # type: ignore[attr-defined]
