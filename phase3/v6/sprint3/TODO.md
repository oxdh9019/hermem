# Hermem V6 Sprint 3 TODO:可解释包装 + reflect API

**版本**: v2.0
**日期**: 2026-06-10
**状态**: Sprint 0+0.5+1+2 全部 ✅ 完成(24 任务),启动 Sprint 3
**依据**: `phase3/v6/SPEC.md` v2.0 §3 Sprint 3 + 决策 7/8
**主题**: 模板优先 + `explain_chunk()`(轻量默认 + 4b 增强 opt-in)+ `hermem_reflect()` API(4 路召回 + 1 次 LLM 综合 + 可选写 L4)

> **范围声明**:本 TODO 覆盖 Sprint 3 全部 6 任务。Sprint 4(评测框架)启动时另立 `sprint4/TODO.md`,不在本文档展开。

---

## Step 0:现状核查(改代码前必做)

- [x] `grep -rn "hermem_search_predictive" plugins/memory/hermem/__init__.py` —— 6 个 schema 已注册(Sprint 2 新增 `hermem_search_predictive`);Sprint 3 需加 2 个新 schema(`explain_chunk` + `hermem_reflect`)
- [x] `grep -n "def _v5_active_retrieval" plugins/memory/hermem/__init__.py` —— **1682**;Sprint 3 任务 3.4 需改它调 `explain_chunk()`
- [x] `grep -rn "l4_reflection" phase3/v5.5/impl/` —— 已有 `synthesize_reflection(errors: list[dict]) -> str | None` 接口,接受 `prediction_errors`;**Sprint 3 任务 3.5 需扩 signature 接受 `source="reflect_immediate"` 标签**(或新加 `synthesize_reflection_immediate()` 函数)
- [x] `grep -rn "4 路召回\|4-way\|temporal.*vec.*bm25.*rrf" phase3/v6/SPEC.md` —— Sprint 3 任务 3.5 spec 写"4 路(temporal + vec + bm25 + rrf)",实际 Sprint 1 已合并为"vec + BM25(BM25 通道含 temporal 过滤)";**Sprint 3 任务 3.5 复用 `search_with_tier(query, time_range=...)` 一调即得 4 路**,无需新接口
- [x] `grep -rn "模板\|template" phase3/impl/` —— 无现成 explain template 模块;**Sprint 3 任务 3.1 全新创建** `phase3/impl/explain_templates.py`
- [x] `grep -n "def search_with_tier\|def search_predictive" phase3/impl/` —— **已就位**(Sprint 1/2),Sprint 3 任务 3.5 直接复用
- [x] LLM 调用规范(决策 8):`qwen3.5:4b-no-think` 一律;3s hard timeout 复用 Sprint 2 经验;ndjson 解析复用 `predictor.py` 的兼容 2b/4b 双模式(直接 `import from impl.predictor import call_predictor_llm, _parse_llm_output`)

**结论**:Sprint 3 是**新增模块 + 桥层集成**的混合工作:
- 新 impl:`phase3/impl/explain.py`(explain_chunk 模板 + 4b 增强)+ `phase3/impl/reflect.py`(reflect API)
- 改 impl:`phase3/v5.5/impl/l4_reflection.py`(扩 signature 或加 reflect_immediate 分支)
- 改桥层:`plugins/memory/hermem/__init__.py`(_v5_active_retrieval 改调 explain_chunk + 加 2 个 schema + handle_tool_call 分支)

---

## Sprint 3 任务总览

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|---|
| **3.1** | P0 | 4-6 个固定过渡句模板(中文优先,与 V5 `[自动回忆]` 标签融合) | `phase3/impl/explain_templates.py`(新) | 2h |
| **3.2** | P0 | `explain_chunk()` 轻量路径(模板默认) | `phase3/impl/explain.py`(新) | 2h |
| **3.3** | P0 | `explain_chunk()` 增强路径(4b opt-in + 3s 监控,复用 Sprint 2 ndjson 解析) | `phase3/impl/explain.py` | 2h |
| **3.4** | P0 | V6 inject 路径调 `explain_chunk()`,失败降级到 V5 格式 | `plugins/memory/hermem/__init__.py` + `phase3/impl/explain.py` | 1h |
| **3.5** | P0 | `hermem_reflect()` API(决策 7:4 路召回 + 1 次 LLM 综合 + 可选写 L4 标 `source=reflect_immediate`) | `phase3/impl/reflect.py`(新)+ `phase3/v5.5/impl/l4_reflection.py`(扩 signature) | 1 天 |
| **3.6** | P0 | 单元测试 ≥ 18 个(模板轮转 + LLM opt-in + reflect 边界) | `phase3/v6/tests/test_sprint3_explain.py`(新)+ `test_sprint3_reflect.py`(新) | 半天 |

**总预估**:1.5-2 天(SPEC 估),按 Sprint 2 实际 3h 节奏,可能 1 天内

---

## Sprint 3 任务详述

### 任务 3.1 — 固定过渡句模板(中文优先)

**目标**:设计 4-6 个自然语言过渡句,把"chunk 命中"转化为"用户能读懂的话"。中文优先(因 V5 标签 `[自动回忆]` 也是中文),英文 fallback。

**涉及文件**:`phase3/impl/explain_templates.py`(新)

**代码骨架**:
```python
"""Hermem V6 Sprint 3 - 解释模板库。

设计原则(V6 SPEC §3 模块 3):
- 过渡句不掩盖相似度(footer 可选 `[内部召回 · 相似度 0.91]`)
- 过渡句不臆造内容(不能"为了过渡"添加 chunk 没有的细节)
- 失败时降级到 V5,不阻断流程
- 中文优先(本地用户),英文 fallback
- 4-6 个模板轮转:不同 turn 用不同句式,避免机械感
"""

import hashlib
import random
from typing import Optional


# ── 模板定义(6 句,中英混合)─────────────────────────────────────
TEMPLATES = [
    "看到您提到 {trigger},我想起 {chunk_excerpt}({relevance_hint})。需要我展开讲吗?",
    "关于 {trigger},之前有类似记录:{chunk_excerpt}({relevance_hint})。",
    "{trigger} 让我想到:{chunk_excerpt}({relevance_hint})。",
    "这让我回忆起:{chunk_excerpt}({relevance_hint})。",
    "Earlier we discussed: {chunk_excerpt_en}({relevance_hint}).",
    "FYI 相关历史:{chunk_excerpt}({relevance_hint})。",
]

RELEVANCE_HINTS = {
    # similarity 0-1 → 3 档 hint 文案
    (0.0, 0.4): "低置信",
    (0.4, 0.7): "中置信",
    (0.7, 1.01): "高置信",
}


def relevance_hint(similarity: float) -> str:
    """similarity → 3 档 hint 文案。"""
    for (lo, hi), hint in RELEVANCE_HINTS.items():
        if lo <= similarity < hi:
            return hint
    return "未知置信"


def select_template(seed: str) -> str:
    """基于 seed(turn id 或 chunk id)选模板 — 同一 chunk 同一 turn 同一模板,不抖动。"""
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(TEMPLATES)
    return TEMPLATES[idx]


def render_explanation(
    chunk_content: str,
    trigger: str,
    similarity: float,
    seed: str = "default",
) -> str:
    """轻量路径主函数:模板渲染一句话解释。

    Args:
        chunk_content: 命中的 chunk 内容(取前 80 字)
        trigger: 触发本次召回的用户 query 关键词
        similarity: 0-1 相似度分数
        seed: 决定用哪个模板(turn id 或 chunk id)

    Returns:
        一句话解释(中文/英文)
    """
    template = select_template(seed)
    excerpt = chunk_content[:80].rstrip()
    if excerpt and not excerpt.endswith((".", "。", "!", "?")):
        excerpt += "..."
    hint = relevance_hint(similarity)
    return template.format(
        trigger=trigger[:20],
        chunk_excerpt=excerpt,
        chunk_excerpt_en=excerpt,  # 英文模板用同一字段
        relevance_hint=hint,
    )
```

**关键设计**:
- **6 句轮转**避免每 turn 都同一模板(机械感)
- **`select_template(seed)` 用 md5 哈希**保证同一 chunk 同一 turn 同一模板(不抖动)
- **3 档 relevance_hint** 把浮点相似度翻译成"低/中/高"自然语言
- **不臆造内容**:`chunk_excerpt` 严格用 chunk 原文前 80 字,不加任何修饰

**验证**:
```bash
cd phase3
python3 -c "
from impl.explain_templates import render_explanation, select_template
# 同一 seed 同一模板
print(render_explanation('上周的 cron 任务失败了', 'cron', 0.85, seed='turn_42'))
print(render_explanation('上周的 cron 任务失败了', 'cron', 0.85, seed='turn_42'))  # 同一句
# 不同 seed 不同模板
print(render_explanation('上周的 cron 任务失败了', 'cron', 0.85, seed='turn_99'))
"
# 期望:turn_42 两次完全相同,turn_99 不同;3 个不同模板变体
```

**风险**:
- 模板对**多语言混排**(中英模板混用)鲁棒性:6 句里 2 句英文,可能在中文 context 里突兀
  - 缓解:加 `language` 参数,Sprint 3 接受 V1.0 简化为"随机轮转";Sprint 4 评估时按用户语言分流

---

### 任务 3.2 — `explain_chunk()` 轻量路径(模板默认)

**目标**:暴露 `explain_chunk(chunk, current_query, similarity, seed) -> str` 接口,默认走模板路径(零 LLM 延迟)。

**涉及文件**:`phase3/impl/explain.py`(新)

**代码骨架**:
```python
"""Hermem V6 Sprint 3 - 解释层入口。

explain_chunk(chunk, current_query, similarity, *, use_llm=False, seed="") -> str

轻量路径(默认):模板轮转,零 LLM 延迟。
增强路径(use_llm=True):qwen3.5:4b-no-think 生成,3s hard timeout,失败降级。
"""

import logging
from .explain_templates import render_explanation

logger = logging.getLogger(__name__)


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
    from .predictor import call_predictor_llm, _parse_llm_output  # 复用 Sprint 2 ndjson 解析

    try:
        prompt = EXPLANATION_PROMPT.format(
            chunk_content=chunk_content[:300],
            current_query=current_query[:200],
        )
        raw = call_predictor_llm(prompt, timeout=3.0)  # 决策 8:4b + 3s(同 Sprint 2)
        explanation = raw.strip()
        if explanation and len(explanation) <= 300:
            return explanation
        # 输出超长,丢弃
        logger.warning(f"explain_chunk LLM output too long: {len(explanation)} chars")
    except Exception as e:
        logger.warning(f"explain_chunk LLM failed: {e}; falling back to V5 format")

    # 兜底:V5 格式
    return f"[自动回忆 - 相似度 {similarity:.2f}]\n{chunk_content[:120]}"


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
```

**关键设计**:
- **轻量路径零延迟**:默认 `use_llm=False`,直接模板渲染
- **增强路径复用 Sprint 2 基础设施**:`from .predictor import call_predictor_llm` 直接用
- **降级双保险**:LLM 失败 → V5 格式;LLM 输出超长 → V5 格式
- **`effective_seed = seed or f"chunk_{chunk_id}"`**:默认按 chunk_id 选模板(同一 chunk 同一模板)

**验证**:
```bash
cd phase3
python3 -c "
from impl.explain import explain_chunk
# 轻量路径(默认)
out = explain_chunk(
    chunk={'id': 1, 'content': '上周 cron 任务失败的根因是 launchd 路径配置问题'},
    current_query='为什么 cron 又失败了?',
    similarity=0.85,
    seed='turn_42',
)
print(f'轻量: {out}')
# 应包含"看到您提到""launchd 路径"等模板词

# 增强路径(LLM,需 Ollama 4b 在跑)
out = explain_chunk(
    chunk={'id': 1, 'content': '上周 cron 任务失败的根因是 launchd 路径配置问题'},
    current_query='为什么 cron 又失败了?',
    similarity=0.85,
    use_llm=True,
)
print(f'增强: {out[:100]}')
"
```

**风险**:
- 增强路径调用 LLM(每次解释多 1 次 LLM call),**单 chunk 解释成本**:warm 380ms / cold 1.7-2s
  - 缓解:`use_llm=True` 仅 opt-in(默认 False);Sprint 4 评估时考虑 batch

---

### 任务 3.3 — `explain_chunk()` 增强路径(4b opt-in + 3s 监控)

**目标**:完整实现增强路径(含超时、降级、4 个指标埋点)。`use_llm=True` 走 4b,3s hard timeout,失败降级。

**涉及文件**:`phase3/impl/explain.py`(续任务 3.2)

**实现**:**已在任务 3.2 的 `_explain_chunk_llm` 中完整实现**(包括 3s timeout 复用 `call_predictor_llm(prompt, timeout=3.0)`、V5 格式降级、长度校验)。**任务 3.3 主要是加指标埋点**:

**代码补充**(追加到 `explain.py` 末尾):
```python
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
        "llm": _explain_metrics["llm"],
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
```

**修改 `_explain_chunk_llm`** 加埋点:
```python
def _explain_chunk_llm(chunk_content, current_query, similarity, chunk_id):
    from .predictor import call_predictor_llm
    import time
    _explain_metrics["explain_total"] += 1
    t0 = time.time()
    try:
        raw = call_predictor_llm(EXPLANATION_PROMPT.format(...), timeout=3.0)
        latency = (time.time() - t0) * 1000
        _explain_metrics["explain_latency_ms"].append(latency)
        _explain_metrics["explain_llm"] += 1
        # ... 同 3.2 ...
    except requests.Timeout:
        _explain_metrics["explain_llm_timeout"] += 1
        # 降级
    except Exception:
        pass
    _explain_metrics["explain_llm_fallback"] += 1
    return v5_format_fallback(...)
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.explain import explain_chunk, get_explain_metrics, reset_explain_metrics
reset_explain_metrics()
# 5 次增强路径
for i in range(5):
    explain_chunk({'id': i, 'content': 'test'}, 'q', 0.85, use_llm=True)
m = get_explain_metrics()
print(f'5 次后: {m}')
# 期望:llm=5, llm_fallback=0(全成功)或 llm_fallback=2(部分失败)
"
```

**风险**:
- 增强路径每 chunk 1 次 LLM → 高频注入场景成本爆炸
  - 缓解:**Sprint 3 默认 `use_llm=False`**;opt-in;Sprint 4 评估时考虑按相似度阈值自动 opt-in(只解释高置信命中)

---

### 任务 3.4 — V6 inject 路径调 `explain_chunk()`,失败降级

**目标**:把 `hermes hermem` 的 V5 主动注入路径(`_v5_active_retrieval`)+ V6 Sprint 1 的 `_v5_inject_chunk` 改用 `explain_chunk()` 输出,而非直接拼 chunk 内容。

**涉及文件**:`plugins/memory/hermem/__init__.py` + `phase3/impl/explain.py`

**Step 0 grep 关键**:
- `_v5_active_retrieval` 在 `__init__.py:1682` — 主动注入入口
- `_v5_inject_chunk` 在 `__init__.py:1860` — 实际 chunk 注入方法

**修改 `_v5_inject_chunk`**:
```python
def _v5_inject_chunk(self, chunk: dict) -> None:
    """V5 主动注入,V6 Sprint 3 改用 explain_chunk() 输出(模板默认)。"""
    impl = _impl_cache
    if "explain" not in impl:
        # Sprint 3 模块未就位,降级到 V5 原格式
        self._v5_inject_chunk_legacy(chunk)
        return

    explain = impl["explain"]
    similarity = chunk.get("similarity", 0.0) or 0.0
    seed = f"turn_{self._v5_get_frequency()}_{chunk.get('id', '')}"
    try:
        rendered = explain.explain_chunk(
            chunk=chunk,
            current_query=self._last_turn_user_message,
            similarity=similarity,
            use_llm=False,  # Sprint 3 默认走模板,opt-in 由桥层配置控制
            seed=seed,
        )
        # 注入到 system prompt 或下一轮 context
        self._v5_inject_to_context(rendered, chunk_id=chunk.get("id"))
    except Exception as e:
        logger.error(f"[Hermem] explain_chunk 失败: {e}; falling back to V5 format")
        self._v5_inject_chunk_legacy(chunk)
```

**`_ensure_impl` 加 explain 模块缓存**:
```python
# V6 Sprint 3: 解释层(explain 模块)
try:
    from impl import explain
    _impl_cache["explain"] = explain
except ImportError:
    # Sprint 3 之前 / fallback:跳过解释层,直接 V5 格式
    pass
```

**验证**:
```bash
cd ~/.hermes/hermes-agent
python3 -c "
import sys
sys.path.insert(0, 'plugins/memory/hermem')
from __init__ import _ensure_impl, _impl_cache
_ensure_impl()
print(f'explain loaded: {\"explain\" in _impl_cache}')
# 端到端:_v5_active_retrieval 调 _v5_inject_chunk 走 explain_chunk
# (这里只验证 _impl_cache;实际注入需 _ensure_impl + _v5_active_retrieval 一起跑)
"
```

**风险**:
- `_v5_inject_chunk` 是 V5 核心注入方法,Sprint 1.5 桥层修复刚改过它(medium_tracker);**改动需验证 medium_tracker 信号 4 不被破坏**
- 解释层失败 → 必须降级到 V5 格式(`[自动回忆 - 相似度 X.XX]\n{content}`),**不阻断注入主流程**

---

### 任务 3.5 — `hermem_reflect()` API(决策 7)

**目标**:实现 `hermem_reflect(query, *, write_l4=False) -> dict`,流程:4 路召回(temporal + vec + bm25 + rrf)→ top-k 拼 context → 4b 综合 → 返回答案 + 可选写 L4(标 `source=reflect_immediate`)。

**涉及文件**:`phase3/impl/reflect.py`(新)+ `phase3/v5.5/impl/l4_reflection.py`(扩 signature)

**Step 0 验证**(关键发现):
- v5.5 `synthesize_reflection(errors: list[dict])` 只接受 prediction_errors,**不支持 reflect 路径**(输入数据源不同)
- **需要 2 个新东西**:
  1. `phase3/v5.5/impl/l4_reflection.py` 加 `synthesize_reflection_immediate(query, answer, source_chunks) -> str | None` 函数
  2. `phase3/impl/reflect.py` 调它,标 `source="reflect_immediate"` 写 L4

**`phase3/v5.5/impl/l4_reflection.py` 扩**:
```python
def synthesize_reflection_immediate(
    query: str,
    answer: str,
    source_chunks: list[dict],
) -> str | None:
    """V6 Sprint 3: 即时反射(Sprint 3 任务 3.5)。

    与 synthesize_reflection(prediction_errors) 不同:
    - 输入是 query + 4b 答案 + 召回 chunks(不是 prediction_errors)
    - 产出标 source="reflect_immediate" 写 L4
    - 不要求 3+ errors 阈值(单次即时反射)
    """
    # 复用 prompt 模板(改 sources)
    prompt = REFLECT_IMMEDIATE_PROMPT.format(
        query=query[:200],
        answer=answer[:500],
        sources="\n".join(f"- {c.get('content', '')[:100]}" for c in source_chunks[:5]),
    )
    # 调 LLM 4b(决策 8)
    from impl.predictor import call_predictor_llm
    try:
        return call_predictor_llm(prompt, timeout=3.0).strip() or None
    except Exception as e:
        logger.warning(f"synthesize_reflection_immediate failed: {e}")
        return None


def write_reflection_immediate(text: str, session_id: str) -> bool:
    """把即时反射写入 l4_reflections 表(标 source=reflect_immediate)。"""
    from .database import get_db
    import time
    expires_at = time.time() + L4_REFLECTION_TTL_DAYS * 86400
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO l4_reflections
                (text, source, session_id, created_at, expires_at)
            VALUES (?, 'reflect_immediate', ?, ?, ?)
            """,
            (text, session_id, time.time(), expires_at),
        )
        return cursor.rowcount > 0
```

**`phase3/impl/reflect.py`(新)**:
```python
"""Hermem V6 Sprint 3 - 反射 API(hermem_reflect)。"""

import logging
from .vector_search import search_with_tier
from .predictor import call_predictor_llm

logger = logging.getLogger(__name__)


def hermem_reflect(
    query: str,
    *,
    top_k: int = 5,
    write_l4: bool = False,
    session_id: str = "",
) -> dict:
    """基于历史记忆反思回答用户问题。

    流程:
    1. 4 路召回(temporal + vec + bm25 → RRF;1 次 search_with_tier 调用)
    2. top_k chunks 拼 context
    3. 4b 综合(query + context → answer)
    4. 可选:把答案 + query 写 L4(标 source=reflect_immediate)

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
        }

    # 2. 拼 context
    context = "\n\n".join(
        f"[{c.get('id', '?')}] {c.get('content', '')[:200]}"
        for c in sources[:top_k]
    )

    # 3. 4b 综合
    prompt = REFLECT_ANSWER_PROMPT.format(query=query[:200], context=context[:1500])
    try:
        answer = call_predictor_llm(prompt, timeout=3.0).strip()
    except Exception as e:
        logger.warning(f"hermem_reflect LLM failed: {e}")
        return {
            "answer": f"反思失败(LLM 错误): {e}",
            "sources": sources,
            "l4_written": False,
            "l4_text": None,
        }

    # 4. 可选写 L4
    l4_written = False
    l4_text = None
    if write_l4 and session_id:
        from v5.5.impl.l4_reflection import (
            synthesize_reflection_immediate,
            write_reflection_immediate,
        )
        l4_text = synthesize_reflection_immediate(query, answer, sources)
        if l4_text:
            l4_written = write_reflection_immediate(l4_text, session_id)

    return {
        "answer": answer,
        "sources": sources,
        "l4_written": l4_written,
        "l4_text": l4_text,
    }


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
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.reflect import hermem_reflect
result = hermem_reflect('hermem V6 进度', top_k=3, write_l4=False)
print(f'answer: {result[\"answer\"][:100]}')
print(f'sources: {len(result[\"sources\"])} chunks')
print(f'l4_written: {result[\"l4_written\"]}')
"
# 期望:answer 是一段综合(可能含 [chunk_id] 引用),sources 3 个
```

**风险**:
- L4 写表涉及 schema 变更风险(已用 `INSERT INTO l4_reflections` 假设列存在)
  - Step 0 已 grep `l4_reflection.py` 看到 `l4_reflections` 表使用,假设 schema 稳定
  - **Sprint 3 任务 3.5 开始前先 PRAGMA table_info(l4_reflections) 确认列名**

---

### 任务 3.6 — 单元测试(≥ 18 个)

**目标**:覆盖 3.1-3.5 全链路,包括:模板轮转、轻量/增强路径、桥层集成、reflect 边界。

**涉及文件**:`phase3/v6/tests/test_sprint3_explain.py`(新)+ `test_sprint3_reflect.py`(新)

**`test_sprint3_explain.py` 12 个测试**:
1. `test_render_explanation_basic`:模板渲染基本流程
2. `test_select_template_deterministic`:同 seed 同一模板
3. `test_select_template_different_seeds`:不同 seed 不同模板(6 句分布)
4. `test_relevance_hint_three_buckets`:3 档 hint 正确
5. `test_explain_chunk_template_path`:默认 `use_llm=False` 走模板
6. `test_explain_chunk_llm_path`:mock LLM,验证增强路径
7. `test_explain_chunk_llm_timeout_fallback`:LLM 超时 → V5 格式
8. `test_explain_chunk_llm_long_output_fallback`:LLM 输出 > 300 字 → V5 格式
9. `test_explain_chunk_llm_exception_fallback`:LLM 抛异常 → V5 格式
10. `test_explain_metrics_track_paths`:5 次后指标计数正确
11. `test_explain_metrics_p95_latency`:latency p95 计算
12. `test_explain_chunk_no_content_fallback`:chunk 无 content 字段

**`test_sprint3_reflect.py` 8 个测试**:
1. `test_reflect_no_sources_returns_message`:无召回时返回提示
2. `test_reflect_basic_flow_with_mocked_llm`:mock LLM 综合答案
3. `test_reflect_llm_timeout_returns_error`:LLM 超时返回错误信息
4. `test_reflect_write_l4_disabled_by_default`:`write_l4=False` 不写
5. `test_reflect_write_l4_success`:mock synthesize_reflection_immediate + write_reflection_immediate → l4_written=True
6. `test_reflect_write_l4_synthesis_failure`:synthesize 失败 → l4_written=False
7. `test_reflect_sources_capped_at_top_k`:召回 > top_k 时只取 top_k
8. `test_reflect_answer_includes_chunk_id_citation`:答案含 [chunk_id] 引用

**总**:12 + 8 = **20 个**(≥ 18 标准)

**验证**:
```bash
cd phase3
python3 -m pytest v6/tests/test_sprint3_explain.py v6/tests/test_sprint3_reflect.py -v
# 期望:20 passed in <60s
```

---

## Sprint 3 验收总表

| 标准 | 验证方式 |
|---|---|
| 90% 注入走模板路径(无 LLM 延迟) | `test_explain_chunk_template_path` + 实证 V5 inject 路径默认 use_llm=False |
| LLM 路径 95% 调用 < 3s | 跑 20 次 4b 增强路径(任务 3.3 准备 4 指标) |
| LLM 失败时主流程不破 | `test_explain_chunk_llm_*_fallback` 3 个测试 + 桥层 e2e |
| reflect API 综合答案含引用 | `test_reflect_answer_includes_chunk_id_citation` |
| reflect 写 L4 标 source=reflect_immediate | `test_reflect_write_l4_success` + SQLite verify |
| 20/20 单元测试通过 | `pytest v6/tests/test_sprint3_*.py` |
| 现有 232/232 + 76/76 v6 + 18/18 v5.5 pytest 仍全过 | `pytest phase3/tests/ phase3/v6/tests/ phase3/v5.5/tests/` |
| `hermes hermem health` 基本 HEALTHY | `hermes hermem health`(drift 沿用 §6 处置) |

---

## 范围声明(再次强调)

> 本 TODO 只覆盖 Sprint 3。Sprint 4(评测框架:50 条 ground-truth + 排序权重增强)的实现步骤待 Sprint 3 完成后另立 `phase3/v6/sprint4/TODO.md`,不在本文档展开。

---

## 风险登记

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 增强路径每 chunk 1 次 LLM,高频场景成本爆炸 | 中 | Sprint 3 默认 use_llm=False;opt-in;Sprint 4 评估时按相似度阈值自动 opt-in |
| 模板 6 句含 2 句英文,中文 context 突兀 | 低 | Sprint 3 接受;Sprint 4 评估时按用户语言分流 |
| L4 表 schema 假设(`INSERT INTO l4_reflections`):列名/类型未现场验证 | 中 | 任务 3.5 起步前先 `PRAGMA table_info(l4_reflections)` 确认;**Step 0 必做** |
| L4 reflection 立即 TTL(14 天)到期 | 低 | 沿用 v5.5 L4 TTL;Sprint 4 eval 评估 |
| reflect 答案可能"编造",不真正"综合" | 中 | prompt 强指令"引用 [chunk_id] + 不知道就说不知道";Sprint 4 50 条 ground-truth 验证 |
| 桥层 `_v5_inject_chunk` 改动影响 V5 medium_tracker 信号 4 | 中 | 任务 3.4 改动保持旧方法 `_v5_inject_chunk_legacy` 作降级;改前跑 232/232 + 76/76 测试 |
| 跨仓库桥层改动需 PR 流程 | 中(沿用 Sprint 2 偏差 5) | 桥层 commit 本地 + PR 流程;Sprint 3 impl 推进不阻塞 |
| 决策 8 一律 4b;reflect 用 4b | 低 | 决策表已落,执行时严格遵循 |

---

*对应文件: `phase3/v6/SPEC.md` v2.0 §3 Sprint 3 + 决策 7/8*

*Sprint 3 启动就绪。等 Oliver "可以开始 Sprint 3" 后开干。*
