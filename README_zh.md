# Hermem

> **Hermes 轻量记忆增强插件** — 给 Hermes Agent 加 L0–L3 分层记忆 + 主动检索 + 行为闭环。
>
> **V6 完整收尾**(2026-06-12,7 sprint + 3 P0 修复)。273/273 测试通过,baseline Recall@5 38.2% → 66.2% (+28%)。生产数据:2350 vectors / 2276 chunks。完整总览:[`phase3/v6/eval/v6-overview.md`](phase3/v6/eval/v6-overview.md)。

**新用户**:跳到 [§核心能力](#核心能力) 看它能做什么,或 [§快速开始](#快速开始) 5 分钟跑起来。版本演进和 changelog 见 [VERSION_HISTORY.md](VERSION_HISTORY.md)。

---

## 核心能力

Hermem 给 Hermes Agent 提供 **6 类记忆能力**,V6 全部上线:

1. **对话中主动找历史记忆** — 不等你问,Agent 自己判断"是不是该查一下过去的对话",命中就注入到 prompt。V6 用 4 信号触发(`medium_accumulated` / `anchor` / `temporal` / `intent`),默认不是每轮检索。
2. **预测你接下来要查什么** — 用 L3 画像(你的偏好/习惯)+ 近 3 轮对话上下文,本地 LLM(`qwen3.5:4b-no-think`)生成 2-3 个预测查询词,合并到检索管线。
3. **自然语言过渡句** — 注入历史记忆时不是裸 dump,而是用"上次我们在 X 时间讨论过 Y,这次聊到 Z 顺手提一下"这种模板包裹,可读性优先,LLM 增强 opt-in。
4. **行为闭环** — 每次 recall 后跟踪你"用没用/忽略/反驳"(`recall_outcome` 表),反哺排序权重。Hermem 越用越准。
5. **可评测性** — 20 条 ground-truth + 4 场景自动化评测,baseline +28% 的来源可对照 `phase3/v6/eval/` 任一 sprint summary。
6. **冲突协商 + 主动遗忘** — V5.5 元认知层:检测新旧记忆矛盾(`hermem_resolve_conflict` 工具),生物学启发的"用得多的保留、用得少的 30 天后降级"。

完整产品定位/技术细节见 [§工作原理](#工作原理) 和 [§高级主题](#高级主题);版本演进/changelog/已知问题见 [VERSION_HISTORY.md](VERSION_HISTORY.md)。

---

## 这是给谁用的

**目标用户**:单 Mac mini 上的 Hermes Agent 用户(信任边界=本机单用户)。

**适用范围**:
- ✅ 已经在用 Hermes Agent,想给对话加长期记忆
- ✅ 单机本机部署,有 Ollama(`localhost:11434`)跑 bge-m3 embeddings
- ✅ 接受 SQLite + NumPy 的轻量存储(纯 Python,无重型运行时)

**不适用**:
- ❌ 多人协作 / 多用户隔离(无 audit log、凭据轮换)
- ❌ 非 Hermes Agent 场景(本插件与 Hermes gateway 紧耦合)
- ❌ 云端部署(本机单用户设计,无分布式存储)

如果你的场景不匹配,见 [§设计原则](#设计原则) 了解背景决策。

---

## 快速开始

**5 分钟上手**(详细指南:[QUICKSTART.md](QUICKSTART.md)):

```bash
# 1. Clone Hermem
git clone https://github.com/oxdh9019/hermem.git ~/hermem

# 2. 运行安装脚本(自动配置插件目录 + 软链接)
cd ~/hermem && ./install.sh

# 3. 初始化向量库(首次仅需,约 5-10 分钟,1700+ chunks)
python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py

# 4. 配置 Hermes 使用 Hermem
# 在 ~/.hermes/config.yaml 中添加:
#   memory:
#     provider: hermem

# 5. 重启 Hermes
hermes restart
```

**验证安装**:
```bash
hermes memory health    # 检查 embedding 模型/向量 drift/chunk 数量/V5 配置
python3 ~/hermem/phase3/scripts/test_v5_e2e.py    # 8/8 端到端测试
```

**遇到问题**:[TROUBLESHOOTING.md](TROUBLESHOOTING.md) 覆盖 5 个常见场景(模块缺失、软链接断、检索不注入等)。

---

## 工作原理

```
会话结束
    ↓
L0: 原始对话存档(JSON,~/.hermes/memory/l0_raw/)
    ↓
L1: 原子事实提取(LLM)
    ↓ 聚合(embedding 相似度 ≥ 0.75)
L2: 场景聚类
    ↓ 暂存(偏好类事实)
L3: user_profile.md 确认
    ↓
Disposition: (condition, prediction, error_count, success_count)
    ↓ 每周综合(launchd 周日 02:30)
主动记忆 ← learnings 反馈到下一轮 prompt
    ↓
V6 should_trigger(4 信号)→ frequency_fallback
    ↓
search_with_tier(query) → RRF(k=60) vec(NumPy) + BM25(FTS5) → 高 / 中置信 chunk
    ↓ 可选
时间解析器(9 regex)→ 按 created_at 过滤
```

**关键路径**:V6 4 信号触发 → 检索(vec + BM25 RRF 融合)→ 可选时间过滤 → 分层阈值(高/中/低)→ inject(模板优先,LLM opt-in)→ 行为闭环(`recall_outcome` 反馈)。

**数据层**:`hermem.db`(chunks/embeddings/l4_reflections/pending_conflicts)+ `l0_l3.db`(l1_dispositions/l2_scenes/l3_staging)+ `hermem_embeddings.npy`(NumPy 向量库)+ `user_profile.md`(手动 L3 偏好)+ `user_profile_auto.md`(LLM 自动归纳,带 SHA256 去重)。

**生产数据**(2026-06-10 verify-on-disk):2350 vectors / 2276 chunks,drift 7(非 P0),`hermes memory rebuild` 可修。

---

## 高级主题

按需深入 — 不读这些也能用 Hermem。

### V6 4 信号触发

不是每轮都检索。V6 用 4 个信号判断时机(命中任一即触发):

1. `medium_accumulated` — 同一 chunk 近期轮次中置信命中 ≥ 3 次(最确定)
2. `anchor` — 显式指代关键词(`上次` / `之前那个` / `你还记得` / `接着说` / `之前提到`)
3. `temporal` — query 含时间词(`今天` / `昨天` / `上周` / `三天前` 等)
4. `intent` — 高置信意图分类(≥ 0.85)
5. `frequency_fallback` — 每 N 轮(默认 3)兜底,无视上面信号

实现:`phase3/impl/trigger.py:should_trigger()` + 桥层 `_v5_active_retrieval()` 重写。

### RRF 融合(Vec + BM25)

两条检索通道用 Reciprocal Rank Fusion(k=60)合并:

```
RRF_score(chunk) = 1/(60 + vec_rank) + 1/(60 + bm25_rank)
```

- **高置信**(RRF ≥ 0.025):双路都命中,至少一路 top-3
- **中置信**(RRF ≥ 0.01):至少一路命中 top-10

实现:`phase3/impl/vector_search.py:search_with_tier()`(向后兼容签名,支持 `time_range` 参数)。

### 预测性召回 / 可解释包装 / 行为闭环

V6 Sprint 2-4 三块新增能力,代码在 `phase3/v6/impl/`:

- `predictor.py` — `qwen3.5:4b-no-think` 调 L3 画像 + 近 3 轮上下文生成 2-3 预测查询词(`search_predictive()` 整合 RRF 二级融合)
- `explain.py` + `explain_templates.py` — 6 模板轮转 + `explain_chunk()` 4b 增强 opt-in,默认模板零 LLM 延迟
- `reflect.py` — `hermem_reflect()` 4 路召回 + 1 次 LLM 综合 + 可选写 L4

行为闭环:`recall_outcome` 表(Sprint 0.5)+ `phase3/v6/eval/` ground-truth 评测框架(Sprint 4 任务 4.5-4.8)。

### 数据模型与 Schema

```
hermem.db:
  chunks                    — memory chunks + embedding_cache
  l4_reflections            — L4 元记忆(14 天 TTL,每周自动续期)
  pending_conflicts         — 冲突协商队列
  prediction_errors         — 喂入 L4 的误差信号(已主动写入)
  chunks.usage_count        — 召回计数(异步更新)
  chunks.last_used_at       — 最近召回时间

l0_l3.db:
  l1_dispositions           — (condition, prediction, error_count) 7 天半衰期
  l2_scenes                 — 场景聚类
  l3_staging                — 待确认偏好
  l1_dispositions.archived  — 主动降级标记
```

`hermem_embeddings.npy` + `.meta.json` 是 NumPy 向量库,`user_profile.md`(手动)和 `user_profile_auto.md`(LLM 自动归纳)是两个分开的 profile 文件。

完整 schema + 迁移:`phase3/v5.5/migrate_v55.py` + `phase3/impl/db_init.py`。

---

## Hermes Agent 集成(桥层)

Hermem 是 Hermes Agent 的一个 **memory provider 插件**。本仓库是实现层(`oxdh9019/hermem`),被一个独立的 **桥接器** 消费:

| 路径 | 角色 |
|------|------|
| `~/.hermes/projects/hermem/`(本仓库) | **实现层** — `phase3/impl/` + `phase3/v5.5/impl/` + `phase3/v6/impl/` |
| `~/.hermes/hermes-agent/plugins/memory/hermem/` | **桥接器 / 插件入口** — `HermemMemoryProvider` 类、tool schemas、后台线程 |

桥接器通过 `_ensure_impl()` 三级 fallback 定位实现:
1. `__init__.py` 同级的 `./impl/` 软链接(标准安装方式)
2. `~/.hermes/projects/hermem/phase3`(本机实际路径)
3. `~/.hermes/projects/hermem-github/phase3`(防御性死分支,本机不存在,silently no-op)

**Tool schemas 暴露给 agent**:`hermem_search`、`hermem_add`、`hermem_forget`、`hermem_stats`、`hermem_resolve_conflict`(2026-06-01 新增)、`hermem_search_predictive`(Sprint 2)、`hermem_explain_chunk`(Sprint 3)、`hermem_reflect`(Sprint 3)。

桥接器的 source of truth 是 `NousResearch/hermes-agent`。本机桥接器 working tree 在 `~/.hermes/hermes-agent/`,但**本 checkout 的改动不会 push 到上游** — 仅作为本地 fork。修改桥接器时请直接编辑本机 hermes-agent 目录中的文件并在那里提交。

完整架构说明(后台线程、profile 安全、冲突解决流程、upgrade preflight checklist)见 `~/.hermes/hermes-agent/plugins/memory/hermem/AGENTS.md`。

---

## 文档地图

| 文档 | 用途 |
|------|------|
| [README.md](README.md) | 产品门户(快速开始 + 核心能力) |
| [README_zh.md](README_zh.md) | 本文件 — 中文使用说明 |
| [QUICKSTART.md](QUICKSTART.md) | 5 分钟安装详细步骤 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 常见问题排查 |
| [VERSION_HISTORY.md](VERSION_HISTORY.md) | V1–V6 演进 + sprint 进度 + changelog + 已知问题 + feature status |
| [CLAUDE.md](CLAUDE.md) | 给 Claude Code 工作时的入口文档(项目结构 + 关键数据 + 桥层细节) |
| [PROJECT.md](PROJECT.md) | 项目总体介绍(背景、动机、风险) |
| [phase3/v6/eval/v6-overview.md](phase3/v6/eval/v6-overview.md) | V6 收尾总览(7 sprint + 5 目标 + baseline +28%) |
| [phase3/v6/eval/sprint{0,05,1,2,3,4}-summary.md](phase3/v6/eval/) | 各 sprint 实际产出 / 偏差 / 经验 |
| [Hermem-V5-SPEC.md](Hermem-V5-SPEC.md) / [phase3/v5.5/SPEC.md](phase3/v5.5/SPEC.md) / [phase3/v6/SPEC.md](phase3/v6/SPEC.md) | V5 / V5.5 / V6 设计规范 |

---

## 前置要求

- **Ollama**(`localhost:11434`)— `bge-m3:latest` 用于 embeddings
- **MiniMax API key**(`MINIMAX_CN_API_KEY`,存于 `~/.hermes/.env`)— 用于误差标注和 LLM 调用
- **SQLite 3**(Python 标准库自带)
- macOS / Linux,Python 3.10+

---

## 设计原则

- **最小依赖**:纯 Python + SQLite,无重型运行时
- **明文存储**:所有记忆为可读 Markdown,可审计和编辑
- **渐进披露**:仅加载相关记忆,避免上下文溢出
- **自我审计**:git log、journal、标注全部公开

---

## 版本说明

### 2026-06-12 — V6 完整收尾 + 全面文档同步 + README 产品门户化重构 (Commits 4e69b9d + 27770d5 + 99cbf97 + 6b0c5fb + 6cef041)

V6 SPEC §0 5 目标全部达成(7 sprint + 3 P0 修复),baseline Recall@5 38.2% → 66.2% (+28%)。

**本节要点**:
- 14 项 P0 失实修正:README/CLAUDE/PROJECT/v6-overview/QUICKSTART/TROUBLESHOOTING 顶部状态、数据快照、目录结构、测试计数
- 3 项 P2 加注:phase2/SPEC、phase3/SPEC 状态行 + sprint1/TODO 3 处 156/156 pytest 基线锚定
- **README 产品门户化重构**:抽出 VERSION_HISTORY.md 独立文档(commit 6b0c5fb),README 重塑为产品门户(commit 6cef041:560 行 → 250 行,-55%)。结构:标题+核心能力 6 bullet → 适用用户 → 快速开始 → 工作原理 → 高级主题 → 文档地图。

**完整 P0/P2 列表**:见 git log `4e69b9d..6cef041` 的 5 个 commit。

更早的版本说明(2026-06-01 V5.5 审计 / 2026-05-28 V5.5 / 2026-05-27 V5.1 / 2026-05-22 V4.3.1 等)见 [VERSION_HISTORY.md §Changelog](VERSION_HISTORY.md#changelog)。

---

## 许可证

MIT
