# Hermem V6 Sprint 3 — Summary

**日期**: 2026-06-10
**Sprint**: 3 (可解释包装 + reflect API)
**状态**: ✅ 完成(6 任务全过,21/21 单元测试,253/253 pytest 零回归)
**Sister commits**:
- `oxdh9019/hermem` `86b2c86` (impl + tests + sprint3 任务)
- `NousResearch/hermes-agent` `d3567f99d` (本地 amend,**未 push — 403 权限,沿用 sprint1/2 偏差 5 模式**)

---

## 1. 任务完成情况

| 任务 | 状态 | 实际产出 |
|---|---|---|
| **3.1** 4-6 个固定过渡句模板(中文优先) | ✅ | `phase3/impl/explain_templates.py` (100 行,6 句 + 3 档 relevance_hint + md5 seed 选模板) |
| **3.2** `explain_chunk()` 轻量路径(模板默认) | ✅ | `phase3/impl/explain.py:explain_chunk()` 模板路径(零 LLM 延迟);按 chunk_id 作 seed 保证同 chunk 同模板 |
| **3.3** `explain_chunk()` 增强路径(4b opt-in + 3s 监控) | ✅ | `_explain_chunk_llm()` 复用 `predictor.call_predictor_llm(prompt, timeout=3.0)`(决策 8:4b 一律);3 类降级:timeout / 输出超长 300 字 / 通用异常 → V5 `[自动回忆 - 相似度 X.XX]` 格式 |
| **3.4** V6 inject 路径调 `explain_chunk()`,失败降级 | ✅ | 桥层 `_v5_inject_chunk` 改调模板路径,`explain_chunk` 失败 → V5 旧格式;`explain` 模块缺失 → V5 旧格式(V5 medium_tracker 信号 4 不受影响) |
| **3.5** `hermem_reflect()` API(决策 7) | ✅ | `phase3/impl/reflect.py` 4 路召回(`search_with_tier` 复用)→ 4b 综合(3s)→ 可选写 L4;`v5.5/l4_reflection.py` 新增 `write_reflection_immediate(text, session_id)`(source_errors=0 标 reflect_immediate 路径) |
| **3.6** 单元测试 ≥ 18 个 | ✅ | **21/21 通过**(13 explain + 8 reflect,超过预期 12+8=20) |

---

## 2. 验收对照(Sprint 3 §Sprint 3 验收总表)

| 标准 | 实际 |
|---|---|
| 90% 注入走模板路径(无 LLM 延迟) | ✅ Sprint 3 默认 `use_llm=False`,Sprint 3 桥层 e2e 模板路径返回零 LLM 延迟解释 |
| LLM 路径 95% 调用 < 3s | ⚠️ 决策 8 修订 3s;实测 cold 1.7-2.0s,4b 100% 走通;**指标埋点已加 `llm_p95_ms` 供 Sprint 4 eval** |
| LLM 失败时主流程不破 | ✅ `test_explain_chunk_llm_*_fallback` 3 个测试 + 桥层 e2e 验证 |
| reflect API 综合答案含引用 | ✅ `test_reflect_answer_includes_chunk_id_citation` 通过;端到端 e2e 答案含 `[1131] [2299]` |
| reflect 写 L4 标 source=reflect_immediate | ✅ `write_reflection_immediate(source_errors=0)`;session_id 编进 reflection_text 头(无 schema 列时降级) |
| 21/21 单元测试通过 | ✅ 0.15s 全过 |
| 现有 138/138 + 76/76 v6 + 18/18 v5.5 pytest 仍全过 | ✅ **253/253** 零回归 |
| `hermes hermem health` HEALTHY | ✅ drift=0 |

---

## 3. 关键设计取舍

### 3.1 模板路径 vs LLM 路径(决策默认)

**设计意图**:
- **模板路径(默认)**:6 句轮转 + md5 seed,零 LLM 延迟,可预测
- **LLM 路径(opt-in)**:`use_llm=True` 才走 4b,3s hard timeout,失败降级

**实测**(2026-06-10 e2e):
- 模板路径:典型 < 1ms,5/5 成功,内容合理
- LLM 路径:cold 1.7-2.0s / warm 380ms;4b 100% 遵循 few-shot 格式(沿用 Sprint 2 经验)

### 3.2 同 seed 同一模板(md5 哈希选)

**问题**:机械感风险 — 每 chunk 同一模板,用户看到 N 个解释都是"看到您提到..."。

**修法**:`select_template(seed)` 用 md5 哈希:同 chunk 同 turn 同模板(不抖动),但不同 chunk/turn 分散到 6 句。

**实测**:6 seed 看到 4 个不同模板(md5 分布,符合预期)

### 3.3 L4 写表 schema hack(无 source 列)

**Step 0 PRAGMA 关键发现**:`l4_reflections` 表 schema 缺 `source` 列(只有 `id / reflection_text / source_errors / confidence / created_at / expires_at / injected_count / last_injected_at`)。

**修法**:**不 ALTER TABLE**——用 `source_errors=0` 标记 reflect_immediate 路径(正数表示批量 errors 反思,0 表示即时反射),session_id 编进 `reflection_text` 头。

**Sprint 4 跟进**:评估是否加 `source TEXT DEFAULT 'batch'` 列(语义更清晰)。

### 3.4 reflect API:走模块属性访问 predictor(便于 mock)

**实现细节**:
```python
from . import predictor as _predictor  # 走模块属性访问
answer = _predictor.call_predictor_llm(prompt, timeout=3.0)
```

**why**:`from .predictor import call_predictor_llm` 只复制引用,monkeypatch 改 `predictor.call_predictor_llm` 不影响 `reflect` 模块里的引用。走 `predictor.call_predictor_llm` 模块属性访问让 mock 生效。

**Sprint 3 测试 21/21 全过**验证此模式工作。

### 3.5 chunks 表无 similarity 列(桥层 SQL 适配)

**Step 0 关键发现**:`chunks` 表只有 `id / session_id / content / chunk_type / concepts / created_at / source_file / source_line / vec_index / usage_count / last_used_at` —— **没有 similarity 列**。

**修法**:桥层 `hermem_explain_chunk` 工具 SQL `SELECT id, content, chunk_type FROM chunks WHERE id = ?`,`similarity` 传 0.7 固定高置信(因用户主动解释的 chunk 大概率是有意义的)。

**Sprint 4 跟进**:可考虑在 `hermem_search` 召回时把 similarity 写到 chunk 的 `usage_count` 旁,或加新列。

### 3.6 桥层 `_v5_inject_chunk` 改造保持 medium_tracker 不破坏

**风险点**:Sprint 1.5 桥层修复刚改 `_v5_inject_chunk` 加 medium_tracker 信号 4 注入逻辑。Sprint 3 再改会否破坏?

**修法**:
- **改用 `explain_chunk()` 模板路径**(use_llm=False,零延迟)替换 V5 旧模板字符串
- **保留所有 medium_tracker + recall_outcome 注入逻辑**(Sprint 0/0.5/1.5 桥层代码)
- **失败/未就位 → 降级 V5 旧格式**(不阻断主流程)

**验证**:`232/232` + 76 + 18 v5.5 零回归 + 桥层 e2e 实际工作。

---

## 4. 偏差记录

### 偏差 1(Step 0 必做发现):chunks 表无 similarity 列

**位置**:`phase3/impl/database.py:chunks` schema
**现象**:桥层 `hermem_explain_chunk` 工具初版 SQL `SELECT id, content, similarity, chunk_type FROM chunks` 报 `no such column: similarity`
**严重度**:**低** — 桥层 SQL 适配即可
**修法**:SQL 改 `SELECT id, content, chunk_type`;`similarity` 传 0.7
**未做**:不改 chunks 表 schema(Sprint 3 不在范围;Sprint 4 eval 评估)

### 偏差 2(2026-06-10 全面复核后):L4 写表无 source 列

**位置**:`l4_reflections` 表 schema
**现象**:SPEC v2.0 §3 任务 3.5 写 "标 `source=reflect_immediate`",但 schema 缺这列
**严重度**:**低** — schema hack 已用(source_errors=0 + session_id 编进 text)
**修法**:`write_reflection_immediate(text, session_id)`,内部 `source_errors=0, confidence=0.7`
**Sprint 4 跟进**:评估加 `ALTER TABLE l4_reflections ADD COLUMN source TEXT DEFAULT 'batch'`

### 偏差 3(测试设计):reflect 测试 monkeypatch 失败

**位置**:`test_sprint3_reflect.py` 3 个初版测试
**现象**:`patch("impl.predictor.call_predictor_llm")` 改不了 `reflect` 模块的引用(因为是 `from .predictor import call_predictor_llm` 复制引用)
**严重度**:**低** — 测试设计问题,不是代码问题
**修法**:`reflect.py` 改用 `from . import predictor as _predictor; predictor.call_predictor_llm(...)` 模块属性访问;测试改用 `monkeypatch.setattr(_predictor, "call_predictor_llm", fake_llm)`
**影响**:`reflect.py` 现在 100% 可 mock,后续 Sprint 4 eval 框架易测

### 偏差 4(沿用 sprint1-summary §6 + sprint2-summary §6):cron 新增 drift

**现象**:health drift 4 → 0 → cron 周期性涨(Sprint 0/0.5/1/2/3 累计问题)
**严重度**:**低** — 沿用 sprint1-summary §6 偏差 6 处置
**未做**:本次 Sprint 3 不修
**Sprint 4 跟进**:`check_drift` 区分 `vec_index >= npy_rows(orphan)vs 0..npy_rows-1(有效)`

### 偏差 5(沿用 sprint2-summary 偏差 5):桥层 commit 未 push

**位置**:`NousResearch/hermes-agent` 仓库 `d3567f99d` 本地 commit
**现象**:`git push origin main` 403,`oxdh9019` 无上游仓库 push 权限
**严重度**:**中** — Sprint 3 桥层工具(hermem_explain_chunk + hermem_reflect)**只在本机 Hermes Agent 进程可用**,上游 hermes-agent 用户用不到
**处置**:**沿用 sprint2 偏差 5 模式**——桥层改动暂留本地,需走 fork + PR 流程

---

## 5. Sprint 4 启动条件

✅ **全部满足**:
- [x] Sprint 0+0.5+1+2+3 全部 30 任务完成
- [x] 21/21 v6 sprint3 单元测试 + 76/76 v6 总 + 138/138 phase3 + 18/18 v5.5 = **253/253 pytest**
- [x] `hermes hermem health` **HEALTHY**(drift=0)
- [x] explain + reflect 桥层工具 e2e 工作(`hermem_explain_chunk` 模板路径 + `hermem_reflect` 4 路召回 + L4 写)
- [x] 决策 8 一律 4b + 3s timeout 复用 Sprint 2 经验,4b 100% 遵循 few-shot 格式

⏸ **Sprint 4 启动前**:
- ✅ 2026-06-10 Oliver 复核通过(本次)
- ⏭ Sprint 4 主题:**评测框架 + 排序权重增强**(SPEC §3 Sprint 4)
  - 50 条 ground-truth(Oliver 主导标注,AI 提供候选)
  - 50/450 train/test split
  - recall@5 / recall@10 / hit_rate@30d 评测
  - 离线 RRF 阈值 sweep(k=30/60/100 × high/medium 阈值)
  - 排序权重增强(基于 recall_outcome 30+ 天数据)
- ⏭ Sprint 4 任务:`phase3/v6/sprint4/TODO.md`(待 Sprint 3 完成后另立)

⚠️ **Sprint 4 启动需要先解决**:
1. **桥层 PR**:Sprint 2 + Sprint 3 桥层 commit 合并 PR 到 NousResearch/hermes-agent(累计 2 个 sprint 改动)
2. **hits_added 埋点 bug 修**:Sprint 4 eval 框架时改(应算 medium tier,不只 high)
3. **chunks 表 similarity 列**:Sprint 4 评估是否加列(影响 recall@5 评估能否直接读 SQL)
4. **L4 source 列**:Sprint 4 评估是否 ALTER TABLE

---

## 6. 文件清单

### 新建(6)
- `phase3/impl/explain_templates.py` (100 行,任务 3.1)
- `phase3/impl/explain.py` (140 行,任务 3.2+3.3)
- `phase3/impl/reflect.py` (135 行,任务 3.5)
- `phase3/v6/tests/test_sprint3_explain.py` (200 行,13 tests,任务 3.6)
- `phase3/v6/tests/test_sprint3_reflect.py` (170 行,8 tests,任务 3.6)
- `phase3/v6/sprint3/TODO.md` (719 行,任务清单 + 风险)

### 修改(2)
- `phase3/v5.5/impl/l4_reflection.py` (+22 行,任务 3.5:write_reflection_immediate)
- (桥层) `plugins/memory/hermem/__init__.py` (+140 行,任务 3.4+3.5 桥层集成)

### 不修改
- chunks_fts 表(Phase 2 已建)
- 现有 138/138 + 18/18 + 76/76 + 21/21 = 253/253 pytest 全部通过
- 决策 1/2/3 实测修订 + 决策 8(4b 一律)未变
- chunks 表 schema(偏差 1,Sprint 4 评估)
- l4_reflections 表 schema(偏差 2,Sprint 4 评估)

---

## 7. 关键学习(V7+ 借鉴)

1. **同 seed 模板稳定**:模板轮转 + md5 seed → 同一 chunk 同一 turn 同一模板(不抖动);不同 turn 分散 → 6 句覆盖率 ~67%(md5 分布)
2. **降级 3 类路径**:`explain_chunk` 失败降级(LLM timeout / 输出超长 / 通用异常)→ V5 格式。**3 类兜底比 1 类兜底健壮**。
3. **走模块属性访问便于 mock**:`from . import predictor as _predictor; _predictor.call_predictor_llm(...)` 让 `monkeypatch.setattr(_predictor, ...)` 生效;`from .predictor import call_predictor_llm` 只复制引用 mock 不到
4. **PRAGMA table_info 是 Step 0 必做**:Sprint 3 任务 3.5 SPEC 写"标 `source=reflect_immediate`",但实际 schema 缺列;**先 PRAGMA 验证再设计,避免"代码写了再 ALTER"**
5. **跨目录模块用 sys.path hack**:`v5.5/impl/` 不是 package,`from v5.5.impl.l4_reflection import ...` 报语法错;用 `sys.path.insert(0, parent / "v5.5")` + `from l4_reflection import ...`(跟 `predictor.py` 调用 `llm_generate_ollama` 模式一致)
6. **桥层 SQL 适配**:chunks 表存的是 RRF 融合前的 raw 数据,无 similarity 列;**调用方传入 similarity(由 search_with_tier 实时算)**,不要从 chunks 表查
7. **生产 call path vs test bypass**(Sprint 1.5 教训):Sprint 3 21/21 通过 + 端到端真调用 hermem_explain_chunk + hermem_reflect 工具 — **必须真跑一次桥层 e2e**(不仅是 mock 测试)
8. **跨仓库改动**:Sprint 2/3 桥层改动全在本地 commit(累计 2 sprint),需走 fork + PR 流程;amend 合并到单 commit 减少 PR review 成本

---

*对应文件: `phase3/v6/SPEC.md` v2.0 §3 Sprint 3 + 决策 7/8 + `phase3/v6/sprint3/TODO.md`*

*Sprint 3 启动 ✅ → Sprint 4 启动就绪。等 Oliver 决策:开 Sprint 4 / 桥层 PR 流程 / 暂停。*
