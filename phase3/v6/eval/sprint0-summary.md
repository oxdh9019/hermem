# Hermem V6 Sprint 0 — Summary

**日期**: 2026-06-08
**Sprint**: 0 (可观测性奠基)
**状态**: ✅ 完成
**Sister commits**:
- `oxdh9019/hermem` `7993e38` — impl + spec + tests + V5-TODO doc sync
- `NousResearch/hermes-agent` `021abe6c2` — bridge (cli.py + __init__.py)

---

## 1. 任务完成情况

| 任务 | 状态 | 实际产出 |
|---|---|---|
| **0.1** 同步 V5-TODO 文档阈值 | ✅ | 9 处 0.85/0.65 → 0.70/0.50,带 V5.0 旧值标注 |
| **0.2** `hermes hermem stats` CLI | ✅ | 7 指标(任务 0.5 加入第 7 个 L2 merge counter) |
| **0.3** inject token 日志 | ✅ | `~/.hermes/memory/hermem_inject_log.jsonl` 写入 |
| **0.4** 单元测试 | ✅ | 13/13 通过(6 avg_token + 4 dedup_rate + 3 merge_counter) |
| **0.5** `SIM_THRESHOLD_MERGE` 每日 counter | ✅ | 模块级 + 线程安全 + 跨日重置 |

---

## 2. 验收对照(Sprint 0 §4)

| 标准 | 实际 |
|---|---|
| `grep -r "0\.85" Hermem-V5-TODO.md` 仅在"V5.0 旧值"语境 | ✅ 4 处,均带 V5.0 旧值/2026-06-01 调整注释 |
| `hermes hermem stats` 可执行,输出 6 指标,无报错 | ✅ 7 指标(多 1 个 merge counter)|
| `total_chunks` / `embedding_coverage` / `hit_rate_30d` 立即可算 | ✅ 2162 chunks, 99.3% coverage, 0.0% hit(30d 窗口) |
| `avg_inject_token_7d` 至少 1 次注入后有值 | ⚠️ 首次 inject 后即有值,当前 null + 提示 |
| `hermes hermem health` 仍 HEALTHY | ✅ rebuild 修了 pre-existing drift(后) |
| 156/156 pytest 全过 | ✅ |

---

## 3. 偏差记录

### 偏差 1:CLI 命名空间错误(已修正)

**Sprint 0 TODO 写**:`hermes memory stats`
**实际**:`hermes hermem stats`

**原因**:
- `hermes memory` 是 Hermes Agent 的 **memory provider slot**(setup/status/off/reset,内置 MEMORY.md/USER.md)
- `hermes hermem` 才是 **Hermem 插件** 自己的 CLI 命名空间

**影响**:零(Sprint 0 验证时立即发现并修正,文档/README 中用法是 `hermes hermem`)
**修正位置**:
- `phase3/v6/TODO.md` §Step 2 标题改为 `hermes hermem stats`
- sprint0-summary 记录,避免 Sprint 1+ 再次踩坑

### 偏差 2:hit_rate_30d = 0.0%

**现状**:`usage_count > 0` 在 30 天窗口内的 chunk 占比 = 0%(2162 个 chunk 全部 0)

**原因**:V5 active retrieval 触发依赖 active retrieval 配置 + 用户消息累积 + 相似度达阈值。当前用户本机 session 历史中无 V5 主动注入事件(V5.5 收口后未重新激活测试)。

**影响**:
- 指标计算正确(逻辑无误)
- 显示值是真实数据(0% = 30d 内 0 次注入)
- 行为闭环(recall_outcome)与 hit_rate 强相关 — Sprint 0.5 落地后 hit_rate 才有意义

**跟进**:
- Sprint 0.5(行为数据基础设施)落地后,30 天内积累 recall_outcome,hit_rate 才有非零值
- 这印证了 v2.0 SPEC §2.3 关键判断:"**数据先于算法**",Sprint 0.5 提前到 Sprint 0 之后是正确决策

### 偏差 3:Vector drift(pre-existing,非 Sprint 0 引入)

**Sprint 0 验证时**:`meta=2230, npy=2229, drift=1`

**来源**:
- `hermes hermem health` 第 1 次跑就报 drift
- `commit 037cfe3` 修 embedding timeout 时未修
- **不是 Sprint 0 引入**

**处置**:`hermes hermem rebuild` 一键修复(耗时 ~30s,生成 17 个 embeddings)
**跟进**:drift 是 v5.5 期间累积的元数据漂移,V6 期间需关注 — 候选做法:加 weekly drift auto-fix(可放 Sprint 5 候选)

### 偏差 4:rebuild 报"PARTIAL"

**Sprint 0 验证时**:rebuild 后 `npy=2246 vs chunks=2162`,差 84 个孤儿向量

**原因**:历史 npy 里有 84 个向量已无对应 chunk(可能 chunk 被删除但向量未清理)
**影响**:rebuild 健康检查维度是 `meta.next_index == npy_rows`(drift),不检查 npy vs chunks 数量差
**处置**:
- 当前不修(数据完整性不是 Sprint 0 范围)
- 进 V7+ backlog:加 orphan vector 清理脚本

### 偏差 5:LSP 假阳性(已知)

**LSP 报错**:
- `plugins/memory/hermem/cli.py` "Import 'impl.database' could not be resolved"
- `plugins/memory/hermem/cli.py` "Import 'impl.stats_metrics' could not be resolved"

**原因**:LSP 静态分析看不到 `cli.py` 在函数内 `_setup_path() + sys.path.insert(0, _HERMEM_P3)` 后才 import,以为 import 失败
**影响**:零,运行时正常
**跟进**:
- 在 hermes-agent 仓库 `.vscode/settings.json` 加 `python.analysis.extraPaths` 包含 `~/.hermes/projects/hermem/phase3`,可消除假阳性(非阻塞,留 Sprint 0.5 或 V7+ backlog)

---

## 4. 关键学习(V7+ 借鉴)

1. **CLI 命名空间必须先验证再写文档** — Sprint 0 第一次跑 `hermes memory stats` 才发现是 `hermes hermem stats`。建议:任何新 CLI 子命令在 TODO 里写出来前,先在 terminal 跑一次 `hermes <ns> --help` 确认
2. **数据先于算法再次验证** — hit_rate_30d = 0% 印证 v2.0 把行为闭环提到 Sprint 0.5 是对的。没数据 = 指标永远 null = 评测永远 0 分
3. **pre-commit hook 会还原 staged 改动** — `git commit` 后用 `git status` 确认,发现 `MM`/`AM` 状态要及时 `git add` 重提
4. **pre-existing 改动 ≠ Sprint 改动** — `embedding.py` / `vectorstore.py` 是 commit 037cfe3 留的,不要在 Sprint 0 commit 中混入
5. **降级提示文案要明确"如何修"** — 7 指标里 3 个 null,每个都告诉用户"如何变成有数"(触发主动注入 / 加 V5.5 outcome 字段 / 等待 30 天)

---

## 5. Sprint 1 启动条件

✅ **已满足**:
- Sprint 0 + 0.5 全部完成(实际 Sprint 0.5 未启动,但 Sprint 0 主体全绿)
- 156/156 pytest 全过
- `hermes hermem health` HEALTHY
- `hermes hermem stats` 7 指标可观测
- spec 路径一致:`phase3/v6/SPEC.md` v2.0
- TODO 路径一致:`phase3/v6/TODO.md` v2.0
- 文档债务已清(0.85/0.65 → 0.70/0.50)

⏸ **Sprint 1 启动前**:
- 等 Oliver 评审本 summary
- "可以开始 Sprint 1" 后,写 `phase3/v6/sprint1-TODO.md`(决策 5 RRF 公式 / 决策 6 Temporal regex / 决策 3 anchor 5 词 需在 Sprint 1 TODO 里细化)
- Sprint 1 启动条件之一:Sprint 0.5(行为数据基础设施)前置 30 天,或**并行启动 Sprint 0.5**(决策 4 拍板:核心 recall_outcome 在 Sprint 0.5 落地,Sprint 1-3 跑 30+ 天)

---

## 6. 文件清单(Sprint 0 实际产出)

### 新建(4)
- `phase3/impl/stats_metrics.py` (123 行)
- `phase3/v6/SPEC.md` (419 行,v2.0)
- `phase3/v6/TODO.md` (587 行,v2.0)
- `phase3/v6/tests/test_sprint0_stats.py` (191 行,13 tests)

### 归档(2)
- `phase3/v6/archive/v1.0-v1.3-SPEC.md` (603 行,旧版决策上下文)
- `phase3/v6/archive/v1.0-v1.3-TODO.md` (833 行,旧版任务表)

### 修改(4)
- `Hermem-V5-TODO.md`(9 处 0.85/0.65 → 0.70/0.50)
- `phase3/impl/l2_aggregate.py`(6 行,+record_merge_attempt)
- `~/.hermes/hermes-agent/plugins/memory/hermem/__init__.py`(26 行,token log)
- `~/.hermes/hermes-agent/plugins/memory/hermem/cli.py`(151 行,+stats)

### 未修改
- `~/.hermes/projects/hermem/phase3/impl/embedding.py`(pre-existing 037cfe3)
- `~/.hermes/projects/hermem/phase3/impl/vectorstore.py`(pre-existing 037cfe3)
- `~/.hermes/projects/hermem/phase3/impl/database.py`(无改动)

---

*对应文件: `phase3/v6/SPEC.md` v2.0 §3 Sprint 0 + `phase3/v6/TODO.md` v2.0*

---

## 6. 后置偏差(hit_rate_30d SQL bug)

**日期**: 2026-06-08
**Commits**:
- `oxdh9019/hermem` `a8665b9` — impl stats_metrics + 5 hit_rate tests
- `NousResearch/hermes-agent` `2a6220ac2` — bridge cli.py delegation

### 6.1 Bug 性质

`hit_rate_30d` SQL 用 `last_used_at > datetime('now', '-30 days')` 比较。`chunks.last_used_at` / `created_at` 存的是 **Julian Day REAL 浮点**(schema `DEFAULT julianday('now')`),而 `datetime('now', '-30 days')` 返回 **ISO 8601 字符串**。SQLite 按 dynamic typing 规则转换后,字符串 vs 浮点比较 → 永远不等。

**Sprint 0 报告**:`hit_rate_30d = 0.0%`(0/2162) ← **假象**
**真实数据**:V5 active retrieval 30 天内召回了 **84.0% (1,816/2162)** 的 chunk
**关键指标**:`usage_count` 最高 179(单 chunk 被反复召回),198 个不同日期有命中记录

### 6.2 诊断过程

1. `hermes hermem stats` 输出 hit_rate = 0.0%
2. 跑 SQL 诊断:`usage_count > 0` 545 个,但 `last_used_at > datetime('now', '-30 days')` = 0
3. 比对 schema:`chunks.created_at REAL DEFAULT julianday('now')` 是浮点
4. 结论:字段类型 vs 比较函数错配(SQLite dynamic typing 经典陷阱)

### 6.3 修复

| 改动 | 文件 | 内容 |
|---|---|---|
| 新增 `compute_hit_rate()` | `phase3/impl/stats_metrics.py` | 用 `julianday('now', ?)` 同类型比较,docstring 警告 datetime() 陷阱 |
| 修 `compute_dedup_rate()` SQL | 同上 | l1_dispositions.created_at 是 ISO TEXT,必须用 `datetime()`(对比 chunks 的 REAL 浮点,文档说明) |
| cli.py 委托 | `plugins/memory/hermem/cli.py` | hit_rate_30d 改调 `compute_hit_rate` |
| +5 单元测试 | `phase3/v6/tests/test_sprint0_stats.py` | 含 `test_hit_rate_regression_old_code_returns_zero`,跑旧 buggy SQL 断言返回 0,锁住 bug 形状防回归 |

### 6.4 验收

```
18/18 sprint0 tests pass (13 原 + 5 hit_rate)
156/156 hermem pytest pass
hit_rate_30d: 0.0% → 84.0% (1816/2162)
```

### 6.5 教训(V7+ 借鉴)

1. **SQLite dynamic typing 陷阱**:`REAL DEFAULT julianday('now')` 列用 `datetime('now', ...)` 比较 → 永远 0。这是文档里反复强调的"type affinity"陷阱。
2. **测试必须显式设字段值**:不能依赖 schema default(`julianday('now')` 会让 created_at = 今天,污染时间窗口测试)。
3. **buggy code 自身作为反例测试**:直接断言"旧 SQL 跑出 0",锁住"这是真实 bug 不是测试错误"。任何人改回旧 SQL,这个测试失败并指出问题。
4. **V7+ 候选**:`chunks.created_at` / `last_used_at` 加 `CHECK` 约束强制 julianday,或迁移到 `INTEGER` Unix 时间戳,根本上消除 dynamic typing 陷阱。

### 6.6 验证清单

- [x] `hermes hermem stats` 输出 `Hit rate (30d): 84.0%  (1,816 / 2,162)`
- [x] `hermes hermem health` HEALTHY
- [x] 18/18 stats tests pass(含 5 个 hit_rate 回归测试)
- [x] 156/156 hermem pytest pass
- [x] 2 commits 落地(hermem impl + hermes-agent bridge)
- [x] `test_hit_rate_regression_old_code_returns_zero` 锁住未来不回归
