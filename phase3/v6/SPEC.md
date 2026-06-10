# Hermem V6：从"机械主动检索"走向"情境感知 + 行为闭环"

**版本**: v2.0
**日期**: 2026-06-08
**状态**: Sprint 0 立项
**依据**:
- `archive/v1.0-v1.3-SPEC.md`(V6 草案四版迭代,oGMemory / MemPalace / Memory Box / Hindsight 四源借鉴)
- `archive/v1.0-v1.3-TODO.md`(Sprint 0-5 任务表累积)
- V5.5 v1.0 收口完成(2026-06-01 阈值调整 + `commit 037cfe3` embedding timeout 修复)
- 6 项已拍板决策(见 §1)

---

## 0. V6 目标

V5 已解决"对话过程中能不能主动找到相关历史记忆",V5.5 补了 L4 反思/冲突/遗忘。V6 解决 **4 个未达成的方向**:

1. **按需触发** —— 不再"每 N 回合机械触发",而是"需要时才触发"
2. **预测性召回** —— 不只匹配字面问题,还预判用户**接下来需要什么**
3. **可解释包装** —— 召回内容用自然语言过渡句,不再裸露 `[自动回忆 - 相似度 0.91]`
4. **行为闭环**(V5 缺失) —— 记录 recall 后用户**用没用/忽略/反驳**,反哺排序权重
5. **可评测性** —— 从"人工 review"升级到"看指标",给 V7+ 留数据基础

**v1.0-v1.3 vs v2.0 的关键差异**:
- 7 个待拍板决策**全部拍板**(§1)
- "行为闭环"从 Sprint 5 候选**提前到 Sprint 0 P0**(§2.5 论证)
- 总预估从 6-9 天修正为 **13-18 天**(v1.x 严重低估)
- 5 个 Sprint 调整为 6 个,**Sprint 0.5 专门做基础设施 bug 收口**
- 旧版决策债务归零,新版本不再以"追加章节"方式扩张

---

## 1. 已拍板决策(7 条)

| # | 决策 | 选项 | 理由 |
|---|---|---|---|
| **1** | V6 路径 | **`phase3/v6/`** | 与 V5.5 对齐,自包含 |
| **2** | 0.85 fallback 改造 | **❌ 整项作废** | `phase3/impl/templates/` 不存在;`vector_search.py` 已用 config 常量 |
| **3** | Sprint 1 anchor 词典 | **手动维护,5 个固定词** | 瘦身后:`"上次" / "之前那个" / "你还记得" / "接着说" / "之前提到"`。时间类词走 Temporal 通道 |
| **4** | Sprint 5 启动时机 | **拆解,核心(recall_outcome)提前到 Sprint 0.5** | 数据先于算法;不先有 recall_outcome,RRF 调优无 ground truth |
| **5** | hybrid 融合公式 | **RRF 融合**(`rrf = 1/(60+rank_vec) + 1/(60+rank_bm25)`) | rank-based,无需通道分数校准;Hindsight 论文已论证 |
| **6** | Temporal 通道时间词解析 | **手写 regex(5-7 条)** | dateparser 200KB 依赖不值;regex 覆盖中文"上周/上个月/YYYY-MM/Q1 2026"已够 |
| **7** | reflect vs L4 边界 | **reflect = 即时(用户调时)**,**L4 = 批处理(launchd 周日 02:30)** | 互不替代;reflect 写 L4 标 `source=reflect_immediate` |

**v1.0-v1.3 草案归档至** `phase3/v6/archive/`,仅作决策上下文参考。本 v2.0 是唯一进行中版本,不再以"v2.1 追加"方式扩张(任何新增 → v3.0 走完整融合流程)。

---

## 2. 架构现状与目标

### 2.1 V5 + V5.5 现状(已实现)

```
用户消息(每 N 回合=3)
    ↓
bge-m3 主动检索(固定频率,ACTIVE_RETRIEVAL_FREQUENCY=3)
    ↓
search_with_tier → 高置信(≥0.70) 注入 / 中置信(0.50–0.70) 累积
    ↓
裸露输出:[自动回忆 - 相似度 0.91] + chunk 内容
    ↓
Agent 处理 → 响应
```

| 能力 | 状态 |
|---|---|
| 主动检索(频率触发) | ✅ V5 |
| 分层阈值 HIGH=0.70 / MEDIUM=0.50 | ✅ `config.py:327`(V5.5 落地) |
| 注入防重复 | ✅ `_injected_chunk_ids` |
| L4 反思 / 冲突消解 / 主动遗忘 | ✅ V5.5 |
| Weekly consolidation | ✅ launchd 周日 02:30(`com.hermes.weekly-memory-synthesis`,退出码 0) |
| Embedding timeout 真生效 | ✅ `commit 037cfe3`(httpx.Timeout(30.0)) |
| 156/156 pytest 全过 | ✅ V5.5 收口 |
| **按需触发** | ❌ V6 Sprint 1 |
| **预测性召回** | ❌ V6 Sprint 2 |
| **可解释包装** | ❌ V6 Sprint 3 |
| **行为闭环 recall_outcome** | ❌ V6 Sprint 0.5 |
| **评测框架** | ❌ V6 Sprint 4 |
| **Temporal 检索通道** | ❌ V6 Sprint 1 任务 1.5 |
| **RRF 融合** | ❌ V6 Sprint 1 任务 1.1 增强 |
| **`hermem_reflect()` API** | ❌ V6 Sprint 3 任务 3.5 |

### 2.2 V6 目标架构(6 Sprint 累计后)

```
用户消息
    ↓
[_v6_should_trigger()] ← 意图置信度 + anchor 5 词 + Temporal 时间词 + 中置信累积
    ↓ 触发
1. Temporal 过滤(Sprint 1.5)→ 先按时间区间缩候选集
2. 显式检索(vec + BM25 → RRF 融合)→ Sprint 1.1
3. 预测性召回(L3 画像 + 对话上下文, qwen3.5:2b 生成 2-3 预测词)→ Sprint 2
4. recall_outcome hook(记录 chunk_id + 触发原因)→ Sprint 0.5
    ↓
[explain_chunk()] ← 模板默认,LLM 增强 opt-in → Sprint 3
    ↓
[reflect 可选] ← 用户显式调 hermem_reflect(),走 4 路召回 + 一次 LLM 综合 → Sprint 3
    ↓
注入(带过渡句)
    ↓
[behavior_log] ← recall 后 3 轮内识别"用了/忽略/反驳" → Sprint 0.5
    ↓
后续 recall 排序权重(usage_count + recall_outcome)→ Sprint 4
```

### 2.3 5 大能力模块

#### 模块 1:按需触发(Dynamic Injection)— Sprint 1

**信号源**(任一触发即检索):
- 意图分类置信度 < 0.7(`INTENT_CONFIDENCE_THRESHOLD = 0.7`)
- 同一 chunk 在最近 3 轮内被累积到中置信但未注入(暗示话题持续)
- 用户消息命中 5 词 anchor 词典(`上次/之前那个/你还记得/接着说/之前提到`)
- 用户消息命中 Temporal regex(走 Temporal 通道,**不**走 anchor)
- 任务进入新阶段(当前 disposition 的 `error_count > 0` 累积时)

**降级**:
- LLM 置信度信号获取成本高 → 降级为 "anchor + Temporal + 中置信累积" 三件套
- 固定频率(每 3 回合)保留作为最后兜底,防意图分类失败时无检索

**新增检索能力**(Sprint 1 合并):
- **RRF 融合**(决策 5):`rrf = 1/(60+rank_vec) + 1/(60+rank_bm25)`,未出现的 doc 分数为 0。`search_with_tier` 重构
- **Temporal 通道**(Sprint 1.5):`hermem_search(query, time_range=None)`,SQLite 层 `chunks.created_at BETWEEN ?` 硬过滤
- **anchor 词典**:5 词固定表,Sprint 1 启动时一次性写死

#### 模块 2:预测性召回(Predictive Recall)— Sprint 2

**接口**:`hermem_search_predictive(context_window, user_query) -> list[chunk]`,与 `hermem_search` 平行

**流程**:
1. 读 L3 `user_profile.md` + 当前对话前 3 轮
2. `qwen3.5:2b-no-think` 生成 2-3 预测性查询词(本地,latency ≤ 200ms)
3. 对每条查询词走显式检索(threshold=MEDIUM)
4. 与显式检索结果合并去重(用 RRF 分数)
5. 失败/超时空降级:仅返回显式结果

**示例**:用户说"帮我安排明天的行程"
- 显式:检索"行程安排"
- 预测:检索"常去的地点"(L3 画像) + "历史抱怨过的拥堵路线"(disposition) + "用户通常午休时间"(pattern)

#### 模块 3:可解释包装(Explainable Wrapper)— Sprint 3

**`explain_chunk(chunk, current_query) -> str`**:
- **轻量路径**(默认):4-6 个固定句式模板轮转
- **增强路径**(opt-in):`qwen3.5:2b-no-think` 生成,latency ≤ 200ms
- 失败降级到 V5 格式(`[自动回忆 - 相似度 0.91]`)

**对比**:

| 旧(V5) | 新(V6) |
|---|---|
| `[自动回忆 - 相似度 0.91]\n- 上次 cron 任务失败的根因是 ...` | `看到您提到 cron 任务,我想起上周一次类似失败的排查记录(根因是 launchd 路径配置问题)。需要我展开讲吗?` |

**设计原则**:
- 过渡句不掩盖相似度(footer 可选 `[内部召回 · 相似度 0.91]`)
- 过渡句不臆造内容(不能"为了过渡"添加 chunk 没有的细节)
- 失败时降级到 V5,不阻断流程

**Sprint 3 合并实现**:
- **任务 3.5** `hermem_reflect()` 按需 API(决策 7):4 路召回(temporal + vec + bm25 + rrf)→ top-k 拼 context → 调一次 `qwen3.5:2b-no-think` → 返回答案 + 可选写 L4(标 `source=reflect_immediate`)
- 与 explain_chunk 合并实现,共享 LLM 调用路径

#### 模块 4:行为闭环(Behavior Loop)— Sprint 0.5(提前) + Sprint 4 增强

**问题**:Hermem 长期缺"行为数据源"。v1.2 Memory Box 借鉴分析明确指出这是"补 Hermem 最大盲点"。

**Sprint 0.5 新增基础设施**:
- 新表 `recall_outcome(recall_id, session_id, chunk_id, follow_up_type, follow_up_turn_count, created_at)`
- 数据源:V5 active retrieval 注入点 hook(Sprint 0.5 接入)
- `follow_up_type` 识别:同 chunk_id 3 轮内被用户引用 → `used`;未引用 + 话题切换 → `ignored`;明确否定("不是这个" / "不相关")→ `rejected`
- 写入失败不阻断主流程(try/except 包住)

**Sprint 4 增强**:
- `recall_outcome` 反哺 `disposition.conf` 自动更新(替代硬编码 0.8)
- `concept_tag` 加"关注度"维度(0-1 范围),recall 行为自动调,半衰期 7 天
- 加权公式:`score = cosine_sim × recency × concept_weight × pattern_relevance`

**为什么提前到 Sprint 0.5(关键论证)**:
- 不先有 `recall_outcome` 数据 → Sprint 4 评测无 ground truth,RRF 调优无反馈信号
- v1.0-v1.3 把它放 Sprint 5 = "Sprint 4 跑评测时发现:没有 recall 后行为数据,评测只能查 chunk 表面相似度"
- **数据先于算法**:Sprint 0.5 落地 recall_outcome → Sprint 1-3 跑 30+ 天 → Sprint 4 评测有真实数据

#### 模块 5:可评测性(Eval Framework)— Sprint 4

**6 个最小指标**(从 Sprint 0 起就持续观测):

| 指标 | 定义 | 数据源 | Sprint 0 即有值? |
|---|---|---|---|
| `total_chunks` | chunks 表总行数 | `chunks` | ✅ |
| `embedding_coverage` | 有 vec_index 的 chunk 占比 | `chunks.vec_index IS NOT NULL` | ✅ |
| `hit_rate_30d` | 召回过 1+ 次的 chunk 占比(30 天窗口) | `usage_count > 0` | ✅ |
| `avg_inject_token_7d` | 单次注入平均 token 数 | `hermem_inject_log.jsonl` | ⚠️ 需 Sprint 0.4 落地 |
| `dedup_rate_7d` | 抽取后被判定为重复/合并的占比 | V5.5 disposition outcome | ⚠️ 需 V5.5 加 outcome 字段 |
| `ollama_latency_ms` | Ollama 端点往返延迟 | `ollama.ps()` | ✅ |

**CLI**:`hermes memory stats`(Sprint 0.3 创建,目前**不存在**)

**Sprint 4 评测框架**:
- 50/450 held-out split(借鉴 MemPalace `lme_split_50_450.json` seed=42,50q 反复调,450q 只跑一次,防 overfit)
- 离线 `hermes memory eval --against labels.jsonl`,跑 recall@5 / MRR
- **独立复现,不抄数字**(避免 Hindsight 论文那种"自评自 SOTA"陷阱)
- 标注集由 Oliver 主导,AI 提供候选标注 + 人工 review
- 自动回归:每次 merge 前跑 eval,差过阈值则 fail CI

---

## 3. 6 个 Sprint 拆分

### Sprint 0:可观测性奠基(5-7h)

**目标**:补可观测基础,确保 V5 主路径不破

| 任务 | 内容 | 预估 |
|---|---|---|
| **0.1** | 同步 `Hermem-V5-TODO.md` 文档阈值(0.85/0.65 → 0.70/0.50),7 处 | 15 min |
| **0.2** | 创建 `hermes memory stats` CLI 子命令(6 指标) | 2-3 h |
| **0.3** | V5 active retrieval 注入路径加 `avg_inject_token` 日志 → `~/.hermes/memory/hermem_inject_log.jsonl` | 1 h |
| **0.4** | 单元测试:stats CLI 各指标计算正确 + JSON 输出 + 失败降级 | 1-2 h |
| **0.5** | `SIM_THRESHOLD_MERGE = 0.85` 加 daily counter(先观测再修) | 1 h |

**Sprint 0 验收**:
- `hermes memory stats` 6 指标可输出(部分可 null,带降级提示)
- `hermes memory health` 仍 HEALTHY
- 156/156 pytest 全过
- `grep -r "0\.85" Hermem-V5-TODO.md` 仅在"V5.0 旧值"语境

**❌ 任务作废说明**:
- v1.0 任务 0.1(0.85 fallback 改造)整项作废:文件不存在 + 主路径已修
- v1.1 任务 0.7(Ollama timeout 修复)整项作废:`commit 037cfe3` 已落地
- v1.1 任务 0.8(V4.5 uncommitted commit)整项作废:实际是 hermes-agent 仓库的 bridge 改动,不是 hermem impl,不需要 commit 到 hermem
- v1.1 任务 0.6(僵尸 hermes 进程清理)改写到 Sprint 0.5

### Sprint 0.5:行为数据基础设施(1-1.5 天)

**目标**:recall_outcome 表 + behavior_log hook,给 Sprint 4 评测和 RRF 调优铺路

| 任务 | 内容 | 预估 |
|---|---|---|
| **0.5.1** | 新表 `recall_outcome` schema 迁移(参考 `v5.5/migrate_v55.py` 模式) | 2 h |
| **0.5.2** | V5 active retrieval 注入点 hook 写 `recall_outcome`(chunk_id, similarity, tier, anchor_source) | 1 h |
| **0.5.3** | 3 轮内 follow-up 识别:`used` / `ignored` / `rejected` 后台异步检测 | 半天 |
| **0.5.4** | 写入失败不阻断主流程(try/except + 错误日志) | 1 h |
| **0.5.5** | 单元测试:行为数据采集正确 + 降级路径 | 2-3 h |

**⚠️ 0.5 特殊说明 — 进程健康检查**(原 v1.1 任务 0.6 改写):
- 启动时检测"长跑卡死"的 hermes gateway 实例(> 30 分钟无响应)
- **不**自动 kill(PID 39006 = 当前 gateway 主进程,不能误杀)
- 检测到异常 → 告警(写 `~/.hermes/memory/hermem_zombie_alert.jsonl`)→ 提示 Oliver 人工处理
- 避免误杀的优先级 > 自动化清理

**Sprint 0.5 验收**:
- 新表 `recall_outcome` 可写入 + 可查询
- 30 天后有 ≥ 100 条真实 recall_outcome 数据
- 进程异常告警可触发
- 156/156 pytest 仍全过

### Sprint 1:按需触发 + 检索管线升级(2-3 天)

| 任务 | 内容 | 预估 |
|---|---|---|
| **1.1** | `intent_classifier` 路径加 `confidence` 字段返回 | 半天 |
| **1.2** | `_v6_should_trigger()` 综合 4 信号(意图置信 + anchor 5 词 + 中置信累积 + 新阶段) | 半天 |
| **1.3** | `search_with_tier` 重构:加 BM25 通道,RRF 融合替换硬阈值切分(决策 5) | 半天 |
| **1.4** | `_v5_active_retrieval()` 触发逻辑改为调 `_v6_should_trigger()`,固定频率保留兜底 | 1 h |
| **1.5** | Temporal 通道:`hermem_search(query, time_range=None)` + SQLite `BETWEEN` 过滤 + 5-7 条中文 regex(决策 6) | 半天 |
| **1.6** | anchor 词典 5 词写死,集成到 trigger | 30 min |
| **1.7** | 单元测试:4 信号触发 + RRF 排序 + Temporal 过滤 | 1 天 |

**Sprint 1 验收**:
- 触发频率从"每 3 回合"降为"按需"(目标:平均 ≤ 1.5 回合/触发)
- 50 条 ground-truth 上 recall@5 对比 V5 baseline 提升 ≥ 10%
- Temporal 查询"上周 X"准确过滤时间区间

### Sprint 2:预测性召回(1.5-2 天)

| 任务 | 内容 | 预估 |
|---|---|---|
| **2.1** | `hermem_search_predictive()` 接口实现(与 `hermem_search` 平行) | 半天 |
| **2.2** | `qwen3.5:2b-no-think` 预测查询词生成 prompt + 200ms timeout | 半天 |
| **2.3** | 预测结果与显式结果合并去重(RRF 分数) | 半天 |
| **2.4** | 失败/超时空降级:仅返回显式结果 | 1 h |
| **2.5** | 单元测试:预测质量 + 失败降级 + latency 监控 | 半天 |

**Sprint 2 验收**:
- 平均增加召回 hit 数量 ≥ 15%
- LLM 路径 95% 调用 < 200ms
- LLM 失败时主流程不破

### Sprint 3:可解释包装 + reflect API(1.5-2 天)

| 任务 | 内容 | 预估 |
|---|---|---|
| **3.1** | 4-6 个固定过渡句模板(中文优先,与 V5 `[自动回忆]` 标签融合) | 2 h |
| **3.2** | `explain_chunk()` 轻量路径(模板默认) | 2 h |
| **3.3** | `explain_chunk()` 增强路径(`qwen3.5:2b-no-think` opt-in + 200ms 监控) | 2 h |
| **3.4** | V6 inject 路径调用 `explain_chunk()`,失败降级到 V5 格式 | 1 h |
| **3.5** | `hermem_reflect()` API(决策 7):4 路召回 + 一次 LLM 综合 + 可选写 L4(标 `source=reflect_immediate`) | 1 天 |
| **3.6** | 单元测试:模板轮转 + LLM opt-in + reflect 写 L4 边界 | 半天 |

**Sprint 3 验收**:
- 90% 注入走模板路径(无 LLM 延迟)
- 10% opt-in 走 LLM 路径,95% < 200ms
- reflect API 可独立调用,失败降级到 `hermem_search` 返回 chunks

### Sprint 4:评测框架 + 排序权重增强(2-3 天)

| 任务 | 内容 | 预估 |
|---|---|---|
| **4.1** | 50/450 held-out split(借鉴 MemPalace `lme_split_50_450.json` seed=42) | 半天 |
| **4.2** | 30-50 条历史 session 人工标注(Oliver 主导,AI 提供候选) | 1-2 天(Oliver 时间) |
| **4.3** | `hermes memory eval --against labels.jsonl`,跑 recall@5 / MRR | 半天 |
| **4.4** | `disposition.conf` 自动更新(从硬编码 0.8 → recall_outcome 驱动) | 半天 |
| **4.5** | `concept_tag` 加"关注度"维度(0-1,半衰期 7 天) | 1 天 |
| **4.6** | 加权公式:`score = cosine × recency × concept_weight × pattern_relevance` | 半天 |
| **4.7** | 周报告:`~/.hermes/memory/eval/weekly/` 跟踪趋势 | 1 h |
| **4.8** | CI 回归:每次 merge 前跑 eval,差过阈值则 fail | 半天 |

**Sprint 4 验收**:
- 50 条 ground-truth 上 recall@5 报告可复现
- disposition.conf 自动更新(30 天观察期)
- concept_weight 加权使排序反映"用户最近在关心什么"
- CI 自动回归

### 总预估(6 Sprint)

| Sprint | 主题 | 预估 |
|---|---|---|
| Sprint 0 | 可观测性奠基 | 5-7 h |
| Sprint 0.5 | 行为数据基础设施 | 1-1.5 天 |
| Sprint 1 | 按需触发 + 检索管线 | 2-3 天 |
| Sprint 2 | 预测性召回 | 1.5-2 天 |
| Sprint 3 | 可解释包装 + reflect | 1.5-2 天 |
| Sprint 4 | 评测框架 + 排序权重 | 2-3 天 |
| **合计** | | **8.5-12.5 天**(Oliver 时间)+ **1-2 天** 标注 |

**vs v1.0-v1.3 预估 6-9 天**:**修正后 +50-100%**。原估算忽略了 156/156 pytest 跑测时间、CI 集成、50/450 split 验证、调参 sweep 等隐性成本。

---

## 4. 验收标准(Sprint 0)

1. `grep -r "0\.85" Hermem-V5-TODO.md` 仅在"V5.0 旧值"语境
2. `hermes memory stats` 可执行,输出 6 指标,无报错
3. `total_chunks` / `embedding_coverage` / `hit_rate_30d` 基于现有数据可立即算出
4. `avg_inject_token_7d` 需要至少 1 次主动注入事件后才能有值
5. `hermes memory health` 仍 HEALTHY,V5 active retrieval 不被破坏
6. 156/156 pytest 全过
7. Sprint 0 完成后写 `phase3/v6/eval/sprint0-summary.md`

---

## 5. 风险与权衡

| 风险 | 严重度 | 缓解 |
|---|---|---|
| `qwen3.5:2b-no-think` 延迟不稳(> 200ms) | 中 | Sprint 2/3 默认走模板/显式路径,LLM 路径 opt-in;超时降级 |
| 评测标注集构建成本 | 中 | Sprint 4 任务 4.2 由 Oliver 主导,AI 提供候选标注 + 人工 review |
| LLM logit 不可用(OpenAI/Claude 风格 API 多数不暴露) | 高 | 按需触发主信号降级为"意图置信度 + anchor 关键词 + 中置信累积"三件套,不强依赖 logit |
| Temporal regex 漏掉长尾时间词 | 低 | 后续可加 dateparser;Sprint 1 先覆盖 80% 中文用例 |
| 进程异常告警误报 | 中 | 检测阈值保守(> 30 分钟无响应),先告警不 kill,等 Oliver 人工确认 |
| 排序公式调参 overfit | 高 | 50/450 split,50q 反复调,450q 只跑一次;评测透明披露(每次 eval 记录 dev 调过什么) |
| `recall_outcome` 30 天数据不足 | 中 | Sprint 0.5 提前 30 天开始采集,Sprint 4 评测有数据用;若仍不足,Sprint 4 任务 4.4-4.6 推迟到 Sprint 5 |

### 明确不做的事(划出去)

- **ContextFS 关系层 / 记忆图谱**:oGMemory §3 评估里写"low ROI",V6 不做。**V7+ backlog**
- **YAML Schema 驱动记忆类型**:oGMemory §4 评估里写"中 ROI,等 V6 稳了再动",V6 不做
- **多用户/多 Agent/多空间隔离增强**:CLAUDE.md 显示已有 `namespace + person_id` 基础,V6 不动
- **Hindsight 四网络(World/Experience/Opinion/Observation)**:Hermem 是单用户个人 memory,不是 belief 系统
- **Hindsight CARA personality profile**:单 agent 服务单用户,无多 persona 需求
- **Hindsight entity graph 通道**:单跳 BM25+vec 已覆盖 80% 召回,entity graph 是 long-tail 优化
- **Hindsight opinion reinforcement 公式**:usage_count 已是"被用过几次"代理指标
- **Hindsight LLM 抽 narrative full text**:存储膨胀真实成本
- **MemPalace verbatim 哲学 / Palace 命名 / 29 个 MCP tools**:Hermem <10 个 tools,不扩
- **MemPalace LLM rerank 100% 路径**:违反"本地/无 API key"定位
- **MemPalace `develop` 当默认分支**:行业反模式

---

## 6. 与 V5 / V5.5 的关系

- **V5**(已实现):主动检索 + 分层阈值 + 防重复
- **V5.5**(已实现):L4 反思 + 冲突消解 + 主动遗忘 + Weekly consolidation
- **V6**(本计划):按需触发 + Temporal + RRF 融合 + 预测性召回 + 可解释包装 + 行为闭环 + 评测框架

V6 **不改** V5/V5.5 现有接口,**叠加**而非**替换**:
- Sprint 0 完成后 `_v5_*` 路径仍有效
- Sprint 1 起 `_v6_should_trigger()` 决定走 `_v5_*` 或 `_v6_*`
- Sprint 1.3 改 `search_with_tier` 内部 = RRF 融合(对外接口不变)
- Sprint 0.5 新增 `recall_outcome` 表不影响任何现有表

---

## 7. 文件结构

```
phase3/v6/
├── SPEC.md                  # 本文档(v2.0,唯一进行中版本)
├── TODO.md                  # Sprint 0 + 0.5 任务表
├── sprint1/TODO.md          # Sprint 0 完成后新建
├── sprint2/TODO.md          # Sprint 1 完成后新建
├── sprint3-TODO.md          # Sprint 2 完成后新建
├── sprint4-TODO.md          # Sprint 3 完成后新建
├── eval/
│   ├── sprint0-summary.md   # Sprint 0 完成后追加
│   ├── sprint05-summary.md  # Sprint 0.5 完成后追加
│   ├── sprint1-summary.md   # Sprint 1 完成后追加
│   └── ...
├── tests/                   # Sprint 0+ 单元测试
└── archive/                 # v1.0-v1.3 草案(决策上下文)
    ├── v1.0-v1.3-SPEC.md
    └── v1.0-v1.3-TODO.md
```

**v2.x 扩张规则**:任何 v2.0 后续增补必须 v3.0 走完整融合流程(v2.x 不再追加章节,防止决策债务累积)。

---

*v2.0 已拍板;Sprint 0 启动前置条件:本 SPEC + TODO 通过 Oliver 评审 + "可以开始" 确认。*
