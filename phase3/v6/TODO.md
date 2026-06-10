# Hermem V6 Sprint 0 + 0.5 TODO:可观测性奠基 + 行为数据基础设施

**版本**: v2.0
**日期**: 2026-06-08
**状态**: 待评审
**依据**:
- `phase3/v6/SPEC.md` v2.0
- `archive/v1.0-v1.3-TODO.md`(Sprint 0-5 任务表累积,已决策 7 条 + 重写)
- V5.5 收口完成(2026-06-01,156/156 pytest + launchd 周日 02:30 退出码 0)

> **范围声明**:本 TODO 只覆盖 **Sprint 0**(可观测性) + **Sprint 0.5**(行为数据基础设施)。Sprint 1-4 启动时各立 `sprint{N}-TODO.md`,不在本文档展开。Sprint 1 占位见 SPEC §3。

---

## Sprint 0:可观测性奠基(5-7 h)

### 任务总览

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|---|
| **0.1** | P0 | 同步 `Hermem-V5-TODO.md` 文档阈值(0.85/0.65 → 0.70/0.50),7 处 | `Hermem-V5-TODO.md` | 15 min |
| **0.2** | P0 | 创建 `hermes memory stats` CLI 子命令(6 指标) | `~/.hermes/hermes-agent/plugins/memory/hermem/cli.py` | 2-3 h |
| **0.3** | P0 | V5 active retrieval 注入路径加 `avg_inject_token` 日志 | `~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py`(`_v5_inject_chunk`) | 1 h |
| **0.4** | P1 | 单元测试:stats CLI 各指标计算正确 | `phase3/v6/tests/test_sprint0_stats.py` | 1-2 h |
| **0.5** | P2 | `SIM_THRESHOLD_MERGE = 0.85` 加 daily counter(先观测再修) | `phase3/impl/l2_aggregate.py` + stats 输出 | 1 h |

**Sprint 0 总预估**:5-7 h(一人)

---

### Step 0:路径确认(改代码前必做)

#### 0.A 确认 cli.py 当前结构

```bash
grep -n "subs.add_parser\|subparser.add_parser" ~/.hermes/hermes-agent/plugins/memory/hermem/cli.py
```

**已知结构**(v1.0 已验证):
- `cli.py:269` `subs = subparser.add_subparsers(dest="hermem_command")`
- `cli.py:271-298` 已注册 `feedback_parser / health_parser / rebuild_parser`
- **没有 `stats_parser`** — 任务 0.2 是**创建**而非修改

#### 0.B 确认 `_v5_inject_chunk` 位置

```bash
grep -n "_v5_inject_chunk\|def _v5_inject" ~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py
```

**已知位置**:`__init__.py:1730` 定义,`:1662 / :1724` 调用。任务 0.3 改此处。

#### 0.C 确认 V5-TODO.md 0.85 字面量位置

```bash
grep -n "0\.85\|0\.65" ~/.hermes/projects/hermem/Hermem-V5-TODO.md
```

**已知 7 处**:
- 行 306-307:阈值常量定义
- 行 458-459:`hermem_search_vector` 验收标准
- 行 574:高置信 chunk 验收
- 行 624:`_medium_tracker` 测试用例
- 行 635:相似度提升验收
- 行 648:T2 中置信缓存说明
- 行 826-827:阈值常量重复

---

### Step 1:同步 V5-TODO 文档(任务 0.1)

**目标**:`Hermem-V5-TODO.md` 不再写已过期的 0.85/0.65 阈值。

| 行号 | 当前 | 改为 |
|---|---|---|
| 306 | `ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.85` | `ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.70  # 2026-06-01 V5.5 调整` |
| 307 | `ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.65` | `ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.50` |
| 458 | `hermem_search_vector(query_emb, threshold=0.65)` | `threshold=0.50` |
| 459 | `search_with_tier 正确返回高置信(≥0.85)和中置信(0.65-0.85)` | `高置信(≥0.70)和中置信(0.50-0.70)` |
| 574 | `高置信 chunk(≥0.85)` | `高置信 chunk(≥0.70)` |
| 624 | `provider._medium_tracker["test123"] >= 0.85` | `>= 0.70` |
| 635 | `相似度提升到 ≥0.85` | `≥0.70` |
| 648 | `相似度在 0.65-0.85` | `0.50-0.70` |
| 826-827 | 同 306-307 重复 | 同 306-307 |

**验收**:
- `grep -n "0\.85\|0\.65" Hermem-V5-TODO.md` 仅在"已弃用"或"V5.0 旧值"语境
- 实际 `config.py:327` 已为 0.70,文档与代码一致

---

### Step 2:创建 `hermes memory stats` CLI(任务 0.2)

**目标**:CLI 输出 6 个最小指标,对应 oGMemory §7 可观测/评测 + V6 Sprint 4 评测框架。

**CLI 设计**:

```
$ hermes memory stats
=== Hermem Memory Stats ===

基础指标
  Total chunks:           2,591
  Embedding coverage:     98.4%  (2,549 / 2,591)
  Hit rate (30d):         12.3%  (319 / 2,591)

使用指标
  Avg inject token:       187   (n=2,541 injects)
  Dedup rate (7d):        8.2%  (217 / 2,646 extractions)
  [NOTE: dedup_rate 需 V5.5 disposition outcome 字段]

基础设施
  Ollama latency:         42ms

$ hermes memory stats --json
{"total_chunks": 2591, "embedding_coverage": 0.984, ...}
```

**6 个指标的数据源**:

| 指标 | SQL / 数据源 | 复杂度 | Sprint 0 立即有值? |
|---|---|---|---|
| `total_chunks` | `SELECT COUNT(*) FROM chunks` | 1 行 | ✅ |
| `embedding_coverage` | `chunks.vec_index IS NOT NULL` 占比 | 2 行 | ✅ |
| `hit_rate_30d` | `usage_count > 0` 占比(30 天窗口) | 1 行 | ✅ |
| `avg_inject_token_7d` | 读 `~/.hermes/memory/hermem_inject_log.jsonl` 7 天窗口 | 文件读取 + parse | ⚠️ 需 0.3 落地 |
| `dedup_rate_7d` | V5.5 disposition 提取 outcome 字段(需 V5.5 配合) | 需 schema 改动 | ⚠️ 返回 null + 提示 |
| `ollama_latency_ms` | `ollama.ps()` 测一次往返 | 网络调用 | ✅ |

**实现位置**:`hermes-agent/plugins/memory/hermem/cli.py`

```python
# ── memory stats ─────────────────────────────────────────────────────────────

def _cmd_stats(args) -> int:
    """Handle `hermes memory stats`."""
    _setup_path()
    try:
        from impl.database import get_db, get_chunk_count
        from impl.vectorstore import get_stats
        from impl.embedding import is_ollama_healthy
    except Exception as e:
        print(f"[Hermem] stats failed to load modules: {e}")
        return 1

    # 1. total_chunks
    total = get_chunk_count()

    # 2. embedding_coverage
    with get_db() as conn:
        mapped = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE vec_index IS NOT NULL"
        ).fetchone()[0]

    # 3. hit_rate_30d
    with get_db() as conn:
        hit_30d = conn.execute(
            """SELECT COUNT(*) FROM chunks
               WHERE usage_count > 0
                 AND (last_used_at > datetime('now', '-30 days')
                      OR created_at > datetime('now', '-30 days'))"""
        ).fetchone()[0]

    # 4. avg_inject_token_7d
    log_path = Path.home() / ".hermes" / "memory" / "hermem_inject_log.jsonl"
    avg_token = _compute_avg_inject_token(log_path, days=7)

    # 5. dedup_rate_7d — V5.5 outcome 字段未就绪时返回 None
    dedup_rate = _compute_dedup_rate(days=7)

    # 6. ollama_latency_ms
    try:
        health = is_ollama_healthy()
        ollama_latency = health.get("latency_ms")
    except Exception:
        ollama_latency = None

    metrics = {
        "total_chunks": total,
        "embedding_coverage": round(mapped / total, 4) if total > 0 else 0.0,
        "hit_rate_30d": round(hit_30d / total, 4) if total > 0 else 0.0,
        "avg_inject_token_7d": avg_token,
        "dedup_rate_7d": dedup_rate,
        "ollama_latency_ms": ollama_latency,
    }

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        _print_stats_table(metrics, total, mapped, hit_30d)

    return 0


def register_cli(subparser) -> None:
    # ... 已有 feedback / memory health / memory rebuild ...
    # 新增:
    stats_sub = memory_parser.add_subparser(
        "stats",
        help="Show Hermem memory usage metrics (hit rate, token, dedup, etc.)",
        description="Outputs 6 minimal metrics for observability. Use --json for machine-readable output.",
    )
    stats_sub.add_argument("--json", action="store_true")
    stats_sub.set_defaults(func=lambda args: _cmd_stats(args))
```

**验收**:
- `hermes memory stats` 命令注册成功(`hermes memory --help` 显示 stats 子命令)
- 6 个指标输出,total_chunks / embedding_coverage / hit_rate_30d / ollama_latency_ms 立即有值
- `avg_inject_token_7d` 在 inject 日志未生成时返回 `null` + 提示
- `dedup_rate_7d` 在 V5.5 outcome 字段未就绪时返回 `null` + 提示
- `hermes memory stats --json` 输出合法 JSON
- 不破坏 `hermes memory health` 和 `hermes memory rebuild`

---

### Step 3:V5 inject 路径加 token 日志(任务 0.3)

**目标**:`_v5_inject_chunk()` 调用时记录 chunk token 估算到 jsonl。

**实现位置**:`hermes-agent/plugins/memory/hermem/__init__.py:_v5_inject_chunk(行 1730)`

```python
def _v5_inject_chunk(self, chunk: dict) -> None:
    """将检索到的 chunk 直接注入到 prefetch result。"""
    sim = chunk.get("similarity", 0.0)
    content = chunk['content']
    injection = (
        f"\n\n[自动回忆 - 相似度 {sim:.2f}]\n"
        f"以下是从历史记忆中检索到的相关内容(可能相关,仅供参考):\n"
        f"- {content}\n"
    )
    with self._prefetch_lock:
        current = self._prefetch_result
        if current:
            self._prefetch_result = current + injection
        else:
            self._prefetch_result = injection

    # V6 Sprint 0:记录 inject token 估算(喂给 hermes memory stats)
    try:
        from pathlib import Path
        import json
        from datetime import datetime, timezone
        log_path = Path.home() / ".hermes" / "memory" / "hermem_inject_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "chunk_id": chunk.get("chunk_id", ""),
                "session_id": chunk.get("session_id", ""),
                "similarity": sim,
                "token_est": len(content) // 4,  # 粗估
                "char_count": len(content),
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        # 日志失败不阻断主流程
        logger.debug("[Hermem V6] inject log write failed: %s", e)

    logger.debug("[Hermem V5] injected: sim=%.2f, content=%s",
                sim, content[:40])
```

**验收**:
- 主动注入触发后,`~/.hermes/memory/hermem_inject_log.jsonl` 追加 1 行
- JSON 每行含 ts / chunk_id / session_id / similarity / token_est / char_count
- 文件不存在时自动创建父目录
- 写入失败不影响 inject 主流程(try/except 包住)

---

### Step 4:单元测试(任务 0.4)

**测试文件**:`phase3/v6/tests/test_sprint0_stats.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

def test_total_chunks():
    """stats 输出包含 total_chunks 字段,且为正整数。"""
    ...

def test_embedding_coverage_calculation():
    """mapped=2549, total=2591 → coverage=0.984。"""
    ...

def test_hit_rate_30d():
    """usage_count > 0 且 last_used_at 在 30 天内的 chunk 占比。"""
    ...

def test_avg_inject_token_7d():
    """构造 7 天内 3 条日志(token_est 100/200/300),avg=200。"""
    log_path = tmp_path / "hermem_inject_log.jsonl"
    log_path.write_text("\n".join([
        json.dumps({"ts": "2026-06-05T00:00:00Z", "token_est": 100}),
        json.dumps({"ts": "2026-06-04T00:00:00Z", "token_est": 200}),
        json.dumps({"ts": "2026-06-03T00:00:00Z", "token_est": 300}),
        json.dumps({"ts": "2026-05-01T00:00:00Z", "token_est": 999}),  # 超出 7 天
    ]))
    avg = _compute_avg_inject_token(log_path, days=7)
    assert avg == 200  # (100+200+300)/3

def test_dedup_rate_returns_null_when_outcome_missing():
    """V5.5 outcome 字段未就绪时,dedup_rate 返回 None 不报错。"""
    ...

def test_json_output_valid():
    """hermes memory stats --json 输出能被 json.loads 解析。"""
    ...
```

**验收**:
- `pytest phase3/v6/tests/test_sprint0_stats.py -v` 全部通过
- 覆盖 6 个指标 + JSON 输出 + 失败降级(dedup_rate 缺失)三个边界

---

### Step 5:`SIM_THRESHOLD_MERGE = 0.85` 加 daily counter(任务 0.5)

**目标**:在 L2 scene 合并路径加触发次数计数,先观测再决定是否改值。

**涉及文件**:`phase3/impl/l2_aggregate.py`(`SIM_THRESHOLD_MERGE` 使用处)

**实现**:
```python
# phase3/impl/l2_aggregate.py
_merge_counter = {"count": 0, "date": ""}  # 模块级

def _maybe_merge_scenes(scene_a, scene_b, similarity):
    """两个 scene 相似度达 SIM_THRESHOLD_MERGE 时合并。"""
    if similarity >= config.SIM_THRESHOLD_MERGE:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _merge_counter["date"] != today:
            _merge_counter["date"] = today
            _merge_counter["count"] = 0
        _merge_counter["count"] += 1
        return _do_merge(scene_a, scene_b)
    return None

def get_merge_counter():
    """返回当日 merge 触发次数(供 stats CLI 调用)。"""
    return _merge_counter.copy()
```

**stats CLI 集成**(Step 2 `_cmd_stats` 末尾):
```python
# 7. sim_merge_counter_today(任务 0.5)
from impl.l2_aggregate import get_merge_counter
merge_counter = get_merge_counter()
metrics["sim_merge_counter_today"] = merge_counter["count"]
```

**验收**:
- 每日 `sim_merge_counter_today` 在 stats 输出可见
- 30 天后看数据决定是否调 `SIM_THRESHOLD_MERGE` 值(先观测原则)

---

### Sprint 0 验收总表

- [ ] **0.1** `Hermem-V5-TODO.md` 文档同步,0.85/0.65 字面量仅在"V5.0 旧值"语境
- [ ] **0.2** `hermes memory stats` CLI 工作正常,6 指标全部可输出(部分可 null)
- [ ] **0.3** `_v5_inject_chunk` 加 token 日志,不破坏主流程
- [ ] **0.4** 单元测试全部通过
- [ ] **0.5** `SIM_THRESHOLD_MERGE` 每日 merge 次数可观测
- [ ] `hermes memory health` 仍 HEALTHY
- [ ] 156/156 pytest 全过
- [ ] `git status` 干净
- [ ] 提交信息:`feat(V6-Sprint0): observability foundation — stats CLI + 0.85 fallback cleanup`
- [ ] `phase3/v6/eval/sprint0-summary.md` 追加

---

## Sprint 0.5:行为数据基础设施(1-1.5 天)

> **为什么 Sprint 0.5 提前**(原 v1.2 计划放 Sprint 5):
> 不先有 `recall_outcome` 数据 → Sprint 4 评测无 ground truth,RRF 调优无反馈信号。Sprint 0.5 落地 → Sprint 1-3 跑 30+ 天 → Sprint 4 评测有真实数据。

### 任务总览

| 任务 | 优先级 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|---|
| **0.5.1** | P0 | 新表 `recall_outcome` schema 迁移(参考 `v5.5/migrate_v55.py` 模式) | `phase3/impl/database.py` + `phase3/impl/migrate_v6_sprint05.py`(新) | 2 h |
| **0.5.2** | P0 | V5 active retrieval 注入点 hook 写 `recall_outcome`(chunk_id, similarity, tier, anchor_source) | `~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py` | 1 h |
| **0.5.3** | P0 | 3 轮内 follow-up 识别:`used` / `ignored` / `rejected` 后台异步检测 | `phase3/impl/recall_outcome_tracker.py`(新) | 半天 |
| **0.5.4** | P0 | 写入失败不阻断主流程(try/except + 错误日志) | 同 0.5.2 | 1 h |
| **0.5.5** | P1 | 单元测试:行为数据采集正确 + 降级路径 | `phase3/v6/tests/test_sprint05_recall_outcome.py` | 2-3 h |
| **0.5.6** | P2 | 进程异常告警(改写自 v1.1 任务 0.6)| `phase3/scripts/zombie_check.py`(新) | 1-2 h |

**Sprint 0.5 总预估**:1-1.5 天(一人)

---

### Step 6:新表 schema 迁移(任务 0.5.1)

**目标**:新增 `recall_outcome` 表,记录 recall 后用户行为。

**Schema**:
```sql
CREATE TABLE recall_outcome (
    recall_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    follow_up_type TEXT,  -- 'used' / 'ignored' / 'rejected' / NULL(待定)
    follow_up_turn_count INT,  -- 这次 recall 后用户又聊了几轮
    similarity REAL,
    tier TEXT,  -- 'high' / 'medium'
    anchor_source TEXT,  -- 'frequency' / 'anchor_keyword' / 'temporal' / 'disposition_error' / 'predictive'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    follow_up_resolved_at TIMESTAMP  -- NULL = 3 轮内未识别,需要异步检测
);

CREATE INDEX idx_recall_outcome_session ON recall_outcome(session_id);
CREATE INDEX idx_recall_outcome_chunk ON recall_outcome(chunk_id);
CREATE INDEX idx_recall_outcome_unresolved ON recall_outcome(follow_up_resolved_at) WHERE follow_up_resolved_at IS NULL;
```

**迁移文件**:`phase3/impl/migrate_v6_sprint05.py`(参考 `phase3/v5.5/migrate_v55.py` 模式)

**验收**:
- 迁移幂等可重跑
- 现有 156/156 pytest 全过(新表不影响旧测试)
- `SELECT * FROM recall_outcome LIMIT 1` 可执行(空表)

---

### Step 7:注入点 hook(任务 0.5.2)

**目标**:V5 active retrieval 触发注入时,同步写一条 `recall_outcome` 行(状态 NULL = 待定)。

**实现位置**:`hermes-agent/plugins/memory/hermem/__init__.py:_v5_inject_chunk`(行 1730,在任务 0.3 改动基础上叠加)

```python
# 在 _v5_inject_chunk 末尾、stats 日志之后追加:
# V6 Sprint 0.5:写 recall_outcome(状态 NULL,3 轮内异步检测)
try:
    import uuid
    from impl.database import get_db
    from datetime import datetime, timezone
    recall_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            """INSERT INTO recall_outcome
               (recall_id, session_id, chunk_id, similarity, tier, anchor_source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                recall_id,
                self._session_id,  # 需从 provider 上下文获取
                chunk.get("chunk_id", ""),
                sim,
                "high" if sim >= 0.70 else "medium",
                getattr(self, "_v6_last_trigger_source", "frequency"),  # Sprint 1 后填
                datetime.now(timezone.utc).isoformat(),
            )
        )
    self._v6_last_recall_id = recall_id  # 给 follow-up 检测用
except Exception as e:
    logger.debug("[Hermem V6] recall_outcome write failed: %s", e)
```

**验收**:
- 主动注入后,`recall_outcome` 表新增 1 行
- `follow_up_type` 初始为 NULL,`follow_up_resolved_at` 为 NULL
- 写入失败不阻断 inject 主流程

---

### Step 8:3 轮内 follow-up 识别(任务 0.5.3)

**目标**:recall 后 3 轮内,识别用户对注入 chunk 的反馈。

**识别规则**:
- **used**:用户在后续 3 轮内引用 chunk 内容(关键词匹配 / `hermem_search` 再次召回该 chunk_id)
- **ignored**:3 轮内未引用 + 话题切换(`_v6_should_trigger` 触发新检索)
- **rejected**:用户明确否定(`"不是这个"` / `"不相关"` / `"不对"` 等否定关键词)

**实现**:`phase3/impl/recall_outcome_tracker.py`(新文件,后台异步线程)

```python
"""recall_outcome 后台跟踪器。

每 30 秒扫一次未解析的 recall_outcome(session_id, follow_up_resolved_at IS NULL):
- 查最近 3 轮用户消息,匹配 used / ignored / rejected
- 更新 follow_up_type 和 follow_up_resolved_at

设计原则:写入失败不阻断主流程,后台异步,不阻塞对话。
"""
```

**验收**:
- 召回后用户引用 chunk → 30 秒内 follow_up_type 变为 'used'
- 召回后 3 轮无引用 + 话题切换 → 变为 'ignored'
- 30 天后有 ≥ 100 条已解析 recall_outcome 真实数据

---

### Step 9:进程异常告警(任务 0.5.6,改写自 v1.1 任务 0.6)

**⚠️ 重要修改**:v1.1 任务 0.6 写"启动时检测 + 自动 kill 僵尸 hermes 进程",**作废**自动 kill。理由:
- PID 39006 = 当前 gateway 主进程(运行 68 小时),`--replace` 模式,**不能误杀**
- 任何自动 kill 风险 > 检测价值

**新方案**:只告警不 kill

**实现**:`phase3/scripts/zombie_check.py`

```python
"""Hermes gateway 进程健康检查。

检测:
- 长跑无响应(> 30 分钟 CPU 0% + 内存不增长)
- fd 未关闭(hermem.db fd 持有 > 1 小时)
- 已知 OOM 模式

检测到异常 → 写 ~/.hermes/memory/hermem_zombie_alert.jsonl
不 kill,等 Oliver 人工处理。
"""
```

**验收**:
- 检测到异常时告警文件出现新行
- 健康时无告警
- **不**有 kill 逻辑(grep 检查)

---

### Sprint 0.5 验收总表

- [ ] **0.5.1** 新表 `recall_outcome` schema 迁移可重跑
- [ ] **0.5.2** V5 inject 触发后 `recall_outcome` 写入 1 行(状态 NULL)
- [ ] **0.5.3** 3 轮内 follow-up 识别(used/ignored/rejected)工作正常
- [ ] **0.5.4** 写入失败不阻断主流程
- [ ] **0.5.5** 单元测试全部通过
- [ ] **0.5.6** 进程异常告警可触发 + 无 kill 逻辑
- [ ] 156/156 pytest 全过(新表不影响)
- [ ] `git status` 干净
- [ ] 提交信息:`feat(V6-Sprint0.5): recall_outcome behavior loop + zombie alert`
- [ ] `phase3/v6/eval/sprint05-summary.md` 追加

---

## Sprint 0 + 0.5 合并验收

- [ ] `hermes memory stats` 输出 6 指标 + sim_merge_counter_today
- [ ] V5 active retrieval 注入后 2 个日志文件都写:`hermem_inject_log.jsonl` + `recall_outcome` 表
- [ ] 156/156 pytest 全过
- [ ] `hermes memory health` HEALTHY
- [ ] Sprint 0 summary + Sprint 0.5 summary 都已追加
- [ ] 提交信息 2 条:
  - `feat(V6-Sprint0): observability foundation — stats CLI + 0.85 fallback cleanup`
  - `feat(V6-Sprint0.5): recall_outcome behavior loop + zombie alert`

---

## 风险与回滚

| 风险 | 回滚方式 |
|---|---|
| 任务 0.2 stats CLI 注册失败 → 不影响 health/rebuild | 删除 stats_sub 注册 |
| 任务 0.3 日志写入 hang → 主流程阻断 | try/except 已包住 + async queue 可选 |
| 任务 0.5.2 recall_outcome 写入慢 → inject 慢 | try/except 已包住 + 同步写,无网络调用 |
| 任务 0.5.6 进程告警误报 | 检测阈值保守(> 30 分钟) |
| 任务 0.5.3 follow-up 误判(used vs ignored) | 先 30 天观察期,准确率不达预期则回退到仅记 created_at |

**整体回滚**:Sprint 0 + 0.5 涉及文件 ≤ 8 个,2 个 commit revert 即可全量回滚。

---

## 后续 Sprint 占位

| Sprint | 主题 | 计划文件 | 启动条件 |
|---|---|---|---|
| Sprint 1 | 按需触发 + Temporal + RRF 融合 | `phase3/v6/sprint1/TODO.md` | Sprint 0 + 0.5 全绿 |
| Sprint 2 | 预测性召回 | `phase3/v6/sprint2/TODO.md` | Sprint 1 全绿 |
| Sprint 3 | 可解释包装 + reflect API | `phase3/v6/sprint3-TODO.md` | Sprint 2 全绿 + 30 天 recall_outcome 数据 |
| Sprint 4 | 评测框架 + 排序权重增强 | `phase3/v6/sprint4-TODO.md` | Sprint 3 全绿 + 30 天 recall_outcome 数据(≥ 100 条) |

每个 Sprint 完成后追加 `phase3/v6/eval/sprint{N}-summary.md`,记录实际产出 / 偏差 / 经验。

---

*v2.0 已拍板;Sprint 0 启动前置条件:本 SPEC + TODO 通过 Oliver 评审 + "可以开始" 确认。*

*对照 v1.0-v1.3 草案的关键变化:7 决策全拍 / 5 大能力(行为闭环提前) / 6 Sprint / 总预估 8.5-12.5 天 / 旧版归档至 archive/。*
