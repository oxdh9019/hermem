# Hermem V6 Sprint 2 TODO:预测性召回

**版本**: v2.0
**日期**: 2026-06-10
**状态**: Sprint 0+0.5+1 全部 ✅ 完成,启动 Sprint 2
**依据**: `phase3/v6/SPEC.md` v2.0 §3 Sprint 2 + 决策 1/3/5/6/7
**主题**: 基于 L3 画像 + 近 3 轮对话上下文,用 `qwen3.5:2b-no-think` 生成 2-3 个预测查询词,合并到 `search_with_tier` 召回管线

> **范围声明**:本 TODO 覆盖 Sprint 2 全部 7 任务。Sprint 3+ 启动时另立 `sprint3/TODO.md`,不在本文档展开。

---

## Step 0:现状核查(写代码前必做)

- [x] `grep -rn "qwen3.5" phase3/impl/config.py` —— `LLM_MODEL = "qwen3.5:4b-no-think"`(通用 pipeline);`LLM_PRIMARY_MODEL = "MiniMax-M2.7"`(API 路由);`LLM_FALLBACK_MODEL = "qwen2.5:3b"`。`qwen3.5:2b-no-think` 尚未配置为路由项。
- [x] `ollama show qwen3.5:2b-no-think` —— 模型已就绪,`thinking` capability ✓,Q8_0 量化
- [x] `grep -rn "llm_generate_ollama" phase3/impl/utils.py` —— 已有 `llm_generate_ollama(prompt, model="qwen3.5:4b-no-think", max_tokens=50, timeout=600s)`,换 `model` 参数即可用 2b-no-think
- [x] `ls ~/.hermes/memory/user_profile*.md` —— `user_profile.md`(手写,1.3KB) + `user_profile_auto.md`(V5.5 自动生成,0.8KB)均存在
- [x] `grep -rn "hermem_search" plugins/memory/hermem/__init__.py` —— 已有 `HERMEM_SEARCH_SCHEMA` 工具;需新增 `HERMEM_SEARCH_PREDICTIVE_SCHEMA`
- [x] `grep -n "def _read_user_profile\|def _get_recent_turns" plugins/memory/hermem/__init__.py` —— **均不存在**;只有 `self._last_turn_user_message`(1 轮,不是 3 轮)。Sprint 2 决策:**只读 L3 画像,不读对话历史**(方案 A,实用主义;Sprint 3 再评估)
- [x] HermesAgent `MemoryProvider` 接口无 `recent_turns` API;近 3 轮对话需 V5 active retrieval 那种自维护 deque,**Sprint 2 跳过**
- [x] `ollama show qwen3.5:2b-no-think` + 5 次延迟实证:warmup p95 ≈ 1.5s,cold start 5s,格式遵循差(返回长 markdown 而非查询词)。**修订决策**:改用 `qwen3.5:4b-no-think`(决策 B),实测 warm 300-500ms,100% 遵循 few-shot 格式
- [x] `grep -rn "search_with_tier" phase3/impl/vector_search.py` —— Sprint 1 已就位,Sprint 2 预测走 `query` 参数传多个预测词
- [x] `grep -rn "predict" phase3/impl/` —— 只有 `prediction_errors`(V4 反思机制),无"predictive recall"代码,符合"全新模块"判定
- [x] LLM 调用模式: `no_think=True` 已在 `utils.py:238` 测过 → `qwen3.5:2b-no-think` 命名规范表示已禁用 think,无需额外参数

**结论**:Sprint 2 是**全新模块**(无既有实现可改),LLM 工具齐全,无需新建基础设施。**唯一改动路径**:`phase3/impl/predictor.py`(新)+ `plugins/memory/hermem/__init__.py` 加 tool + `phase3/impl/llm_router.py`(如需)或直接用 `llm_generate_ollama`。

---

## Sprint 2 任务总览(决策修订:qwen3.5:4b,2s timeout,few-shot examples)

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 | 状态 |
|---|---|---|---|---|---|
| **2.1** | P0 | 预测 prompt 工程(few-shot 强指令) | `phase3/impl/predictor.py`(新) | 半天 | ✅ |
| **2.2** | P0 | `qwen3.5:4b-no-think` 调用封装(2s hard timeout,ndjson 解析 4b 模式) | `phase3/impl/predictor.py` | 2h | ✅ |
| **2.3** | P0 | `generate_predictive_queries(user_profile, user_query) -> list[str]` 主函数 | `phase3/impl/predictor.py` | 2h | ✅ |
| **2.4** | P0 | `search_predictive()` 整合:多预测词并发 → `search_with_tier` → RRF k=30 融合 | `phase3/impl/predictor.py` | 半天 | ✅ |
| **2.5** | P0 | 失败/超时空降级:catastrophic → ([], []);predictor timeout → 显式;4 指标埋点 | `phase3/impl/predictor.py` | 1h | ✅ |
| **2.6** | P0 | 桥层加 `HERMEM_SEARCH_PREDICTIVE_SCHEMA` + `handle_tool_call` 分支 + `_impl_cache["predictor"]` | `plugins/memory/hermem/__init__.py`(hermes-agent 仓库) | 2h | ✅ |
| **2.7** | P0 | 18 个单元测试(prompt 2 + LLM 3 + 解析 4 + 主函数 2 + 整合 3 + 降级 2 + 桥层 2) | `phase3/v6/tests/test_sprint2_predictor.py`(新) | 半天 | ✅ |

**实际耗时**:~3h(2026-06-10 一次性执行,无返工;主要耗时在 ndjson 解析 bug 定位 + 4b 决策切换)

**总预估**:原 3-4 天 → 实际 3h(Sprint 2 决策 1+2+3 提前拍板省了大量反复,Step 0 现状核查省了 1-2h 写无用代码)

---

## Sprint 2 任务详述

### 任务 2.1 — 预测 prompt 工程

**目标**:设计一个 prompt,让 `qwen3.5:2b-no-think` 根据 L3 画像 + 近 3 轮对话,生成 2-3 个预测性查询词。

**涉及文件**:`phase3/impl/predictor.py`(新)

**代码骨架**:
```python
# phase3/impl/predictor.py

PREDICTIVE_PROMPT = """你是 Hermem 记忆助手的查询预测器。基于用户画像和最近对话,生成 2-3 个用户**接下来可能想问**的查询词。

## 用户画像(L3)
{user_profile}

## 最近 3 轮对话
{recent_turns}

## 用户当前问题
{user_query}

## 要求
1. 输出 2-3 个查询词,每行一个,简洁(5-15 字)
2. 重点预测:**用户接下来需要的信息**而非字面同义改写
3. 避免重复用户已问过的字面问题
4. 失败兜底:如果无法预测,只输出空行

## 输出格式(严格遵守)
query1
query2
query3
"""
```

**实现**:
```python
def build_predictive_prompt(
    user_profile: str,
    recent_turns: list[dict],  # [{"role": "user"|"assistant", "content": str}, ...]
    user_query: str,
) -> str:
    """Build prompt for qwen3.5:2b-no-think predictive query generation."""
    turns_text = "\n".join(
        f"[{t['role']}] {t['content'][:200]}"  # 截断,避免 prompt 过长
        for t in recent_turns[-3:]
    )
    return PREDICTIVE_PROMPT.format(
        user_profile=user_profile[:1500],  # 画像截断 1500 字
        recent_turns=turns_text,
        user_query=user_query[:300],
    )
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.predictor import build_predictive_prompt
p = build_predictive_prompt(
    user_profile='用户: Oliver, 偏好简洁直接',
    recent_turns=[
        {'role': 'user', 'content': '检查 hermem 进度'},
        {'role': 'assistant', 'content': 'Sprint 1 完成, 6 commits 已 push'},
    ],
    user_query='下一步做什么?',
)
print(p)
"
# 期望:看到 PREDICTIVE_PROMPT 模板 + 三段填充内容
```

**风险**:
- L3 画像可能为空(用户没填)→ fallback 用"通用用户"
- 近 3 轮对话为空(会话刚启动)→ fallback 用"无上下文"
- prompt 长度 > Ollama context 2k → 截断已加,验证实际长度

---

### 任务 2.2 — `qwen3.5:2b-no-think` LLM 调用封装(200ms hard timeout)

**目标**:封装一个**严格 200ms timeout** 的 Ollama 调用,超时立即抛 `TimeoutError`,由 2.5 兜底。

**涉及文件**:`phase3/impl/predictor.py`

**代码骨架**:
```python
import requests

LLM_TIMEOUT_S = 0.25  # 250ms hard limit (决策 1:接受 200ms 太严,给 cold-start 一些余量;Sprint 4 跑 50 条 ground-truth 后再调)
LLM_MODEL = "qwen3.5:2b-no-think"

def call_predictor_llm(prompt: str, timeout: float = LLM_TIMEOUT_S) -> str:
    """Call qwen3.5:2b-no-think for predictive query generation.

    Hard 200ms timeout per SPEC. Exceeds → raise TimeoutError.
    """
    base_url = "http://localhost:11434"  # 不走 /v1,原生 /api/chat
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 100},  # 预测词短,50 tokens 够
    }
    resp = requests.post(
        f"{base_url}/api/chat",
        json=payload,
        timeout=timeout,  # 200ms hard limit
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "").strip()
```

**关键**:**用 requests 而非 ollama SDK** —— ollama SDK 的 timeout 参数在 Sprint 1.5 P1 修复时已发现是装饰品(实际是 httpx.Client(timeout=None) 无限等),`requests.post(timeout=0.2)` 是 hard limit。

**验证**:
```bash
cd phase3
python3 -c "
import time
from impl.predictor import call_predictor_llm, build_predictive_prompt
p = build_predictive_prompt('用户: Oliver', [{'role':'user','content':'测试'}], '测一下')
t0 = time.time()
try:
    out = call_predictor_llm(p)
    print(f'OK in {time.time()-t0:.3f}s: {out[:100]}')
except Exception as e:
    print(f'FAIL in {time.time()-t0:.3f}s: {type(e).__name__}: {e}')
"
# 期望:200ms 内返回,或 TimeoutError(若冷启动超 200ms)
```

**风险**:
- 首次冷启动(qwen3.5:2b-no-think 加载)可能 > 200ms → **接受**:冷启动延迟由调用方决定(2.5 兜底返回显式结果)
- 200ms 阈值可能太严 → Sprint 2 跑通后实测,必要的话 Sprint 3 调整

---

### 任务 2.3 — `generate_predictive_queries` 主函数

**目标**:封装 2.1 + 2.2,提供简洁接口给 2.4 用。

**涉及文件**:`phase3/impl/predictor.py`

**代码骨架**:
```python
import logging
import re

logger = logging.getLogger(__name__)

def _parse_llm_output(raw: str, max_queries: int = 3) -> list[str]:
    """Parse LLM output: 2-3 query words, one per line.

    Robust against:
    - Empty output (return [])
    - Numbered list ('1. xxx' → 'xxx')
    - Quotes / bullets ('- xxx' → 'xxx')
    - Lines > 30 chars (likely malformed, skip)
    """
    queries = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去编号/项目符号/引号
        line = re.sub(r"^[\d\.\-\*\u2022\"]+\s*", "", line)
        line = line.strip("\"'\u3001\u3002")
        if 2 <= len(line) <= 30:
            queries.append(line)
        if len(queries) >= max_queries:
            break
    return queries

def generate_predictive_queries(
    user_profile: str,
    recent_turns: list[dict],
    user_query: str,
) -> list[str]:
    """Generate 2-3 predictive queries using qwen3.5:2b-no-think.

    Returns empty list on any failure (timeout, parse error, etc.).
    Caller is responsible for fallback to explicit-only search.
    """
    try:
        prompt = build_predictive_prompt(user_profile, recent_turns, user_query)
        raw = call_predictor_llm(prompt)
        queries = _parse_llm_output(raw)
        if not queries:
            logger.warning(f"Predictor returned no queries: {raw[:200]}")
        return queries
    except requests.Timeout:
        logger.warning("Predictor LLM timed out (>200ms); returning []")
        return []
    except Exception as e:
        logger.warning(f"Predictor failed: {type(e).__name__}: {e}")
        return []
```

**验证**:
```bash
cd phase3
python3 -c "
from impl.predictor import generate_predictive_queries
qs = generate_predictive_queries(
    user_profile='用户: Oliver, 偏好简洁',
    recent_turns=[{'role':'user','content':'检查 hermem 进度'}],
    user_query='下一步做什么?',
)
print(f'Got {len(qs)} queries: {qs}')
"
# 期望:0-3 个查询词,典型 2-3 个
```

**风险**:
- LLM 输出格式不稳定 → `_parse_llm_output` 容错
- LLM 生成重复/同义查询 → 2.4 RRF 融合会去重

---

### 任务 2.4 — `search_predictive()` 整合:多预测词 → `search_with_tier` → RRF 融合

**目标**:把"显式 + 预测"两路召回合并,用 `search_with_tier` 的 RRF 机制去重排序。

**涉及文件**:`phase3/impl/predictor.py`

**代码骨架**:
```python
from .vector_search import search_with_tier

def search_predictive(
    user_query: str,
    user_profile: str,
    recent_turns: list[dict],
    top_k: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Predictive search: explicit + predicted queries, RRF-fused.

    Returns (high, medium) tier chunks, same shape as search_with_tier.
    Falls back to explicit-only if predictor fails/returns empty.
    """
    # 1. 显式检索(必须有)
    explicit_high, explicit_medium = search_with_tier(query=user_query, top_k=top_k)

    # 2. 预测生成(可能空)
    predicted_queries = generate_predictive_queries(user_profile, recent_turns, user_query)
    if not predicted_queries:
        logger.debug("No predicted queries; returning explicit-only")
        return explicit_high, explicit_medium

    # 3. 每个预测词单独检索
    predicted_high, predicted_medium = [], []
    for pq in predicted_queries:
        h, m = search_with_tier(query=pq, top_k=top_k)
        predicted_high.extend(h)
        predicted_medium.extend(m)

    # 4. RRF 融合(用 search_with_tier 内部的 RRF 公式,这里手工合并)
    # 注:因为 search_with_tier 已对单 query 做 RRF(vec+BM25),
    #    我们这里做的"显式 vs 预测"是 query-level 融合,用 RRF 再合并一次
    # 决策 2:query-level RRF k=30(显式优先,top 命中比次命中权重差距更大)
    fused_high = _rrf_fuse([explicit_high, predicted_high], k=30)
    fused_medium = _rrf_fuse([explicit_medium, predicted_medium], k=30)
    return fused_high[:top_k], fused_medium[:top_k]


def _rrf_fuse(rank_lists: list[list[dict]], k: int = 30) -> list[dict]:
    """Reciprocal Rank Fusion across multiple rank lists.

    Each input list is sorted by relevance (best first).
    Output: single list sorted by RRF score, deduplicated by chunk id.
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
    # 按 RRF 分数降序
    sorted_cids = sorted(scores, key=scores.get, reverse=True)
    return [meta[cid] for cid in sorted_cids]
```

**关键设计**:
- **二级 RRF**:第一级是 `search_with_tier` 内部(vec+BM25),第二级是这里(query-level:显式 vs 预测)
- **二级 RRF 的 k 用 60**:和第一级保持一致,避免双 RRF 的权重不一致
- **dedup by chunk id**:同一 chunk 出现在显式 + 预测中,只保留 RRF 分数最高的(自动)

**验证**:
```bash
cd phase3
python3 -c "
from impl.predictor import search_predictive
high, medium = search_predictive(
    user_query='检查 hermem 进度',
    user_profile='用户: Oliver',
    recent_turns=[],
    top_k=3,
)
print(f'high: {len(high)} chunks, medium: {len(medium)} chunks')
for c in high[:3]:
    print(f'  - {c.get(\"content\", \"\")[:60]}')
"
# 期望:high/medium 都有内容(显式+预测),有 chunk_id 去重
```

**风险**:
- 显式和预测完全重叠(LLM 生成字面同义)→ RRF 自动 dedup,无副作用
- 预测词过偏 → 可能降级(显式仍返回,只是 medium 可能被低分)

---

### 任务 2.5 — 失败/超时空降级

**目标**:`search_predictive` 已在 2.4 内部 try/except,但需要在日志和指标上明显,便于 Sprint 4 评测。

**涉及文件**:`phase3/impl/predictor.py`

**代码补充**:
```python
# 已有 try/except 在 generate_predictive_queries 里
# search_predictive 自身 try/except 加在 2.4 整段外层
def search_predictive(...) -> tuple[list, list]:
    try:
        # ... 2.4 全部逻辑 ...
        return fused_high[:top_k], fused_medium[:top_k]
    except Exception as e:
        logger.error(f"search_predictive catastrophic failure: {e}; falling back to explicit-only")
        # 兜底:仅显式检索
        return search_with_tier(query=user_query, top_k=top_k)
```

**指标埋点**(为 Sprint 4 eval 准备):
- `predictor_latency_ms`: 实际 LLM 调用耗时
- `predictor_timeout_count`: 超时累计
- `predictor_empty_count`: 返回 0 查询词累计
- `predictor_hits_added`: 预测词带来的新 chunk 数量(去重前 vs 去重后)

**验证**:
```bash
cd phase3
# 1. 模拟 LLM 失败(改 timeout 到 1ms)
python3 -c "
import impl.predictor
impl.predictor.LLM_TIMEOUT_S = 0.001  # 1ms, 必超时
from impl.predictor import search_predictive
high, medium = search_predictive(
    user_query='test',
    user_profile='test',
    recent_turns=[],
    top_k=3,
)
print(f'fallback OK: high={len(high)} medium={len(medium)}')
"
# 期望:不抛异常,返回显式结果

# 2. 模拟 LLM 返回 0 查询词(改 prompt 让 LLM 输出空)
# 跳过,留给单元测试覆盖
```

**风险**:
- 日志可能刷屏(每次失败都 warn)→ Sprint 4 评估时考虑加 rate limit
- 指标目前只 log,没存表 → Sprint 4 eval 框架时再写 `recall_outcome` 表

---

### 任务 2.6 — 桥层加 `HERMEM_SEARCH_PREDICTIVE_SCHEMA` + `handle_tool_call` 分支

**目标**:让 LLM 代理能通过 `hermem_search_predictive` 工具调用 Sprint 2 的预测检索。

**涉及文件**:`plugins/memory/hermem/__init__.py`

**代码骨架**:
```python
# 1. 在 HERMEM_*_SCHEMA 列表(line 101-180)加:
HERMEM_SEARCH_PREDICTIVE_SCHEMA = {
    "name": "hermem_search_predictive",
    "description": "Predictive semantic search: combines explicit query with LLM-generated predicted queries, fused via RRF. Use when user_query suggests a follow-up need (e.g. 'next step', 'how to'). Slower (200ms LLM call) but returns more relevant context.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Current user query"},
            "top_k": {"type": "integer", "description": "Max results (default 3)"},
        },
        "required": ["query"],
    },
}

# 2. 在 get_tool_schemas() 加:
def get_tool_schemas(self) -> list[dict]:
    return [
        HERMEM_SEARCH_SCHEMA,
        HERMEM_ADD_SCHEMA,
        HERMEM_FORGET_SCHEMA,
        HERMEM_STATS_SCHEMA,
        HERMEM_SEARCH_PREDICTIVE_SCHEMA,  # 新增
    ]

# 3. 在 handle_tool_call() 加分支:
def handle_tool_call(self, tool_name: str, args: dict) -> str:
    if tool_name == "hermem_search_predictive":
        from impl.predictor import search_predictive
        # 读 L3 画像 + 近 3 轮对话(从 session context 拿)
        user_profile = self._read_user_profile()
        recent_turns = self._get_recent_turns(n=3)
        high, medium = search_predictive(
            user_query=args["query"],
            user_profile=user_profile,
            recent_turns=recent_turns,
            top_k=args.get("top_k", 3),
        )
        return json.dumps({
            "high_confidence": high,
            "medium_confidence": medium,
            "predictor_used": True,
        }, ensure_ascii=False)
    # ... 其他分支 ...
```

**关键**:
- `_read_user_profile()` 和 `_get_recent_turns(n=3)` 是 V5/V5.5 已有的工具函数(查 `__init__.py` 是否已有)
- 如已有,直接调用;如无,本任务补上

**验证**:
```bash
cd plugins/memory/hermem
python3 -c "
import sys; sys.path.insert(0, '../../phase3')
from __init__ import HERMEM_SEARCH_PREDICTIVE_SCHEMA
import json
print(json.dumps(HERMEM_SEARCH_PREDICTIVE_SCHEMA, ensure_ascii=False, indent=2))
"
# 期望:看到完整 schema 定义

# 端到端 smoke:实际 tool call
python3 -c "
import sys; sys.path.insert(0, '../../phase3')
import json
from __init__ import handle_tool_call
result = handle_tool_call('hermem_search_predictive', {'query': '检查进度', 'top_k': 3})
print(json.loads(result))
"
# 期望:JSON 包含 high/medium chunk 列表
```

**风险**:
- `handle_tool_call` 已有大量分支,新增分支必须**不破坏**已有 4 个工具(搜索/添加/遗忘/统计)
- `_read_user_profile` / `_get_recent_turns` 可能不存在 → Step 0 提前 grep 确认

---

### 任务 2.7 — 单元测试(≥ 18 个)

**目标**:覆盖 2.1-2.6 全链路,包括:prompt 工程、LLM 调用、解析、整合、失败降级、桥层 e2e。

**涉及文件**:`phase3/v6/tests/test_sprint2_predictor.py`(新)

**测试用例(按 Sprint 1 标准粒度)**:
1. **prompt 工程**(2 个)
   - `test_build_predictive_prompt_with_full_context`:画像 + 3 轮对话 + query → 完整 prompt
   - `test_build_predictive_prompt_truncates_long_profile`:画像 > 1500 字 → 截断

2. **LLM 调用**(3 个)
   - `test_call_predictor_llm_returns_text`:正常调用 → 返回 LLM 文本
   - `test_call_predictor_llm_raises_on_timeout`:用 1ms timeout → TimeoutError
   - `test_call_predictor_llm_uses_correct_model`:验证 payload 的 model 字段是 `qwen3.5:2b-no-think`

3. **解析容错**(4 个)
   - `test_parse_llm_output_normal_lines`:`query1\nquery2\nquery3` → 3 个
   - `test_parse_llm_output_numbered`:`1. xxx\n2. yyy` → 2 个(去编号)
   - `test_parse_llm_output_with_bullets`: `- xxx\n- yyy` → 2 个
   - `test_parse_llm_output_empty_returns_empty`:`""` 或 `"\n\n"` → []

4. **主函数**(2 个)
   - `test_generate_predictive_queries_returns_2_to_3`:正常 → 2-3 个
   - `test_generate_predictive_queries_falls_back_on_timeout`:LLM 超时 → []

5. **整合**(3 个)
   - `test_search_predictive_returns_explicit_when_predictor_empty`:预测失败 → 仅显式
   - `test_search_predictive_fuses_explicit_and_predicted`:预测有 → 融合(显式 + 预测都有 chunk)
   - `test_search_predictive_dedupes_overlapping_chunks`:显式+预测都命中同一 chunk → 只 1 个(分数相加)

6. **失败降级**(2 个)
   - `test_search_predictive_handles_predictor_timeout`:1ms timeout → 兜底
   - `test_search_predictive_handles_search_failure`:显式搜索抛异常 → 兜底(返回 [])

7. **桥层 e2e**(2 个)
   - `test_hermem_search_predictive_schema_registered`:schema 在 `get_tool_schemas()` 返回
   - `test_hermem_search_predictive_tool_call_e2e`:实际调 `handle_tool_call("hermem_search_predictive", {...})` → 拿到 JSON 结果

**总**:2+3+4+2+3+2+2 = **18 个**,符合 Sprint 1.5 后 ≥ 18 标准

**验证**:
```bash
cd phase3
python3 -m pytest v6/tests/test_sprint2_predictor.py -v
# 期望:18 passed in <60s
```

**风险**:
- 桥层 e2e 测试可能因 `_read_user_profile` 等辅助函数找不到而失败 → 测试时 mock
- LLM 调用测试需要 Ollama 跑 → CI 跑测试时跳过(用 `@pytest.mark.skipif` 检测 Ollama)

---

## Sprint 2 验收总表

| 标准 | 验证方式 |
|---|---|
| `generate_predictive_queries()` 返回 2-3 个查询词(LLM 正常) | `test_generate_predictive_queries_returns_2_to_3` |
| LLM 调用 < 200ms 95% 命中 | 跑 20 次统计(任务 2.5 准备里 4 个指标) |
| 失败/超时空降级到显式 | `test_search_predictive_handles_predictor_timeout` |
| 显式 + 预测 RRF 融合 | `test_search_predictive_fuses_explicit_and_predicted` |
| 桥层 tool 正常注册 + e2e | `test_hermem_search_predictive_tool_call_e2e` |
| 18/18 单元测试通过 | `pytest v6/tests/test_sprint2_predictor.py` |
| 现有 138/138 phase3/tests/ 仍全过 | `pytest phase3/tests/` |
| 现有 58/58 v6/tests/(Sprint 1)仍全过 | `pytest phase3/v6/tests/` |
| `hermes hermem health` HEALTHY | `hermes hermem health` |

---

## 范围声明(再次强调)

> 本 TODO 只覆盖 Sprint 2。Sprint 3+ 的实现步骤待 Sprint 2 完成后另立 `phase3/v6/sprint3/TODO.md`,不在本文档展开。

---

## 风险登记

| 风险 | 严重度 | 缓解 |
|---|---|---|
| qwen3.5:2b-no-think 冷启动 > 200ms | 中 | 接受;2.5 兜底;Sprint 2 跑通后实测 |
| LLM 输出格式不稳定 | 中 | `_parse_llm_output` 容错,空输出 log warn |
| 显式 + 预测召回不增反降(预测词偏) | 中 | Sprint 4 50 条 ground-truth 评估;不达标则 2.1 prompt 改 |
| 桥层 `_read_user_profile` / `_get_recent_turns` 不存在 | 中 | Step 0 已 grep;如有缺,2.6 补辅助函数 |
| 测试需 Ollama 运行 | 低 | `@pytest.mark.skipif(not ollama_available())` |
| 200ms 阈值可能太严(Sprint 2 跑完才知道) | 低 | Sprint 3 评估调整;默认模板路径(Sprint 3)不需要 LLM |

---

*对应文件: `phase3/v6/SPEC.md` v2.0 §3 Sprint 2 + 决策 1/3/5/6/7*

*Sprint 2 启动就绪。等 Sprint 2 完成后写 sprint2-summary.md + 启动 Sprint 3。*
