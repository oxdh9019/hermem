# Hermem

Hermes 轻量记忆增强系统 — L0–L3 分层记忆 + 预测编码（V4）。

**V5.1 已上线**（2026-05-27）。1645 个 chunk 已生成 bge-m3 向量，分层阈值（高≥0.70 / 中≥0.65），会话级去重，健康检查 + rebuild CLI。

---

## 版本历史

| 版本 | 名称 | 说明 |
|------|------|------|
| V1–V3 | Phase 1–3 | L0→L1→L2→L3 流程，语义搜索。设计文档见 `phase1/`/`phase2/`/`phase3/` |
| **V4** | **预测记忆** | 记忆即生成模型，而非存储文本 |
| **V4.1** | **误差标注** | 预测应发生的事；标注未被验证的预测 |
| **V4.2** | **条件Disposition** | (condition, prediction, error_history) 元组替代平面事实 |
| **V4.3** | **误差激活检索** | Beta — 误差信号关闭学习回路 |
| **V4.4** | **并发修复** | 向量库双锁、自动索引文件锁、watchdog drift 监控 |
| **V4.5** | **Disposition感知重排** | 通过 disposition 上下文提升 L1 事实，error_count 驱动检索排名 |
| **V5** | **主动检索** | 对话中 bge-m3 向量搜索 — 自动判断并注入相关历史记忆 |
| **V5.1** | **工程修复** | drift=91 修复，`hermes memory health` + `rebuild` CLI，embedding 自动化审计（无遗漏） |

---

## Hermem 工作原理

```
会话结束
    ↓
L0: 原始对话存档（JSON）
    ↓
L1: 原子事实提取（MiniMax-M2.7）
    ↓ 聚合（embedding 相似度 ≥ 0.75）
L2: 场景聚类
    ↓ 暂存（偏好类事实）
L3: user_profile.md 确认
    ↓
意图分类（13 种意图）→ 路由到 disposition 更新或检索
    ↓
Disposition: (condition, prediction, error_count, success_count)
    ↓ 每日综合
主动记忆 ←  learnings + social learnings 反馈到下一轮 prompt
```

**当前数据：1711 个向量（1645 个 chunk），22 个 disposition，80 个 L2 场景**（截至 2026-05-27）。

---

## 目录结构

```
hermem/
│
├── README.md                     # 英文版
├── README_zh.md                 # 中文版
├── QUICKSTART.md                # 快速安装指南
├── TROUBLESHOOTING.md           # 常见问题
├── install.sh                   # 自动配置脚本
├── requirements.txt              # 最小依赖
│
├── templates/
│   └── __init__.py             # Hermes 插件入口（含友好报错）
│
├── phase1/                      # Phase 1 设计文档
├── phase2/                      # Phase 2 设计文档
│
├── phase3/                      # Phase 3 设计 + 全部 V1–V5 实现
│   ├── impl/                   # ← 全部活跃实现
│   ├── scripts/                # 运维脚本（cron 调用）
│   └── eval/                   # 评估脚本
│
└── plugins/memory/hermem/       # Hermes gateway 插件封装
```

---

## V5 — 主动检索（2026-05-27）

V5 实现**对话中主动检索** — Hermem 在对话过程中主动搜索语义记忆，在用户未主动询问时自动注入相关内容。

**工作机制：**
```
用户消息
    ↓
每 N 轮（N=3）触发向量搜索
    ↓
分层阈值判断：
  高置信（≥0.70）：直接注入，格式 [自动回忆 - 相似度 X.XX]
  中置信（0.65–0.70）：缓存，再次出现时提升
  低置信（<0.65）：忽略
    ↓
会话级去重：同一 chunk 最多注入一次
```

**阈值配置（2026-05-27 调优）：**
- HIGH: 0.70（实测最高相似度 0.77）
- MEDIUM: 0.65
- TOP_K: 每轮最多 3 条
- FREQUENCY: 每 3 轮触发一次

**核心组件：**
- `impl/vector_search.py`：bge-m3 cosine 相似度 + `search_with_tier()`
- `impl/embedding.py`：Ollama bge-m3 embeddings，SQLite 缓存
- `impl/config.py`：`ACTIVE_RETRIEVAL_*` 全部可配置
- `phase3/scripts/batch_compute_embeddings.py`：预计算全部 chunk 向量
- `phase3/scripts/test_v5_e2e.py`：端到端测试（7/8 通过）
- `plugins/memory/hermem/cli.py`：`hermes memory health` + `hermes memory rebuild`

**Phase B 待完成：** 中置信累积触发

---

## V4 — 预测记忆

V4 将记忆重新定义为**生成模型**而非存储文本。Hermem 预测用户需要什么，仅在预测被违背时激活 — 误差信号驱动学习。

### V4.1 — 误差标注

会话结束后，标注助手作出的可被证伪的预测：
- `prediction_errors[]`：被违背的预测
- `surprise_level`：本轮意外程度
- `confidence`：每条误差的确定度（0–1）
- `overall_quality_score`：会话级预测质量（0–1）

### V4.2 — 条件Disposition

用 `(condition, prediction, confidence, error_history)` 替代平面 L1 事实：
- `condition_text`：何时激活
- `prediction_text`：用户预期
- `condition_embedding`：语义索引
- `error_count` / `success_count`：追踪预测准确度
- `disposition_decay`：时间 × 频率联合衰减（7 天半衰期）

### V4.3 — 误差激活检索

完成误差驱动的学习回路。**端到端标注 pipeline 已验证（2026-05-22）。**

**13 种意图分类：**

| 意图 | 描述 | 处置 |
|------|------|------|
| 学习 | 想学习/理解某概念 | 触发 recall |
| 执行 | 明确任务指令 | 直接执行 |
| 修正 | 纠正 Hermem 错误 | 更新 disposition |
| 结束/关闭 | 阶段性收尾 | 更新摘要 |
| 反馈 | 提供意见/评价 | 触发轻量标注 |
| 确认 | 确认/批准 | 路由到执行 |
| 建议 | 提出建议 | 记录为 preference |
| 记忆 | 存储/检索记忆 | 调用 Hermem |
| 修改 | 修改/编辑内容 | 执行修改 |
| 停止 | 停止当前操作 | 中断任务流 |
| 提问 | 提出问题 | 直接回答 |
| 咨询 | 寻求意见/建议 | 生成建议 |
| 评估 | 判断/评估 | 提供分析 |

**8 个触发条件：**

| 触发 | 类型 | 状态 |
|------|------|------|
| A1 用户明确否定 | strong | ✅ |
| A2 用户部分纠正 | strong | ✅ |
| B1 Agent 自修正 | strong | ✅ |
| B2 Agent 表达不确定 | medium | ✅ |
| B3 Agent 放弃 | strong | ✅ |
| C1 LLM 错误 | — | ⚠️ 待 gateway 集成 |
| C2 工具错误 | — | ⚠️ 待 gateway 集成 |
| C3 session 结束兜底 | — | ✅ 已生效 |

**每日循环：**
- 02:00 — 自省日记：读取当天所有 L0会话，写入模式/错误/解决方案
- 06:00 — 综合：压缩学习成果到主动记忆

**已完成：** B1, B2, B4, B5, B6, B8, B9, C3
**待完成：** B3（动态阈值），C1/C2（gateway hooks）

### V4.5 — Disposition感知重排（2026-05-22）

`disposition_aware_rerank()` 通过 disposition 上下文提升 L1 事实，使 disposition 不只是累积 error_count，而是主动重排检索结果。

**提升路径：**
1. `l0_ref` 精确匹配 — 同一会话的 disposition 和 fact（精确路径）
2. Condition 关键词 → fact 内容重叠 ≥ 2 次（OpenClaw 导入的 UUID-format disposition 兜底）

### V4.4 — 并发修复（2026-05-21）

| 阶段 | 功能 | 状态 |
|------|------|------|
| P0 | `append_vectors()` 双锁：`threading.Lock` + `fcntl.flock` | ✅ |
| P1 | `hermem_auto_index_all.py` 文件锁 | ✅ |
| P2 | `watchdog_vectorstore.py`：drift 检测 + `--fix` 自动修复 | ✅ |

---

## 快速开始

### Hermem 用户（5 分钟安装）

```bash
# 1. 克隆 Hermem
git clone https://github.com/oxdh9019/hermem.git ~/hermem

# 2. 运行安装脚本（自动配置插件目录 + 软链接）
cd ~/hermem && ./install.sh

# 3. 初始化向量库（首次仅需，约 5 分钟）
python3 ~/hermem/phase3/scripts/batch_compute_embeddings.py

# 4. 配置 Hermes 使用 Hermem
# 在 ~/.hermes/config.yaml 中添加:
#   memory:
#     provider: hermem

# 5. 重启 Hermes
hermes restart
```

详细指南：[QUICKSTART.md](QUICKSTART.md) · 问题排查：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)

### 开发者（自托管）

```bash
git clone https://github.com/oxdh9019/hermem.git
cd hermem

# 初始化 L1/L2/L3 表
python3 phase3/impl/db_init.py

# 运行每日 pipeline（journal 02:00 + synthesis 06:00）
python3 phase3/cron_daily.py
```

---

## 前置要求

- Ollama（`localhost:11434`）— bge-m3 用于 embeddings
- MiniMax API key（`MINIMAX_CN_API_KEY`，存于 `~/.hermes/.env`）— 用于误差标注和 LLM 调用
- SQLite 3（Python 标准库自带）

---

## 版本说明

### 2026-05-27 — V5.1 工程修复

- **drift=91 已修复**：meta 和 npy 完全对齐（1711 向量，1645 chunk，0 孤儿）
- **`hermes memory health`**：CLI 健康检查（embedding 模型、向量 drift、chunk 数量、V5 配置、ollama daemon）
- **`hermes memory rebuild`**：幂等 CLI，修复 drift 并补全缺失 embedding
- **Embedding 自动化审计**：所有 `insert_chunk` 调用点已验证，无遗漏，不需要新增自动化

### 2026-05-27 — V5 主动检索 + 公开 Beta

- **Phase A 完成**：bge-m3 向量搜索 + 分层阈值 + 注入格式 + 会话去重
- HIGH 阈值：0.85 → 0.70（实测最高 0.77）
- **公开 Beta 发布包**：`install.sh` + `QUICKSTART.md` + `TROUBLESHOOTING.md` + `requirements.txt` + `templates/__init__.py`

### 2026-05-23 — V4.5 Patch（15 个修复）

### 2026-05-22 — V4.3.1 Patch

---

## 已知问题

| 问题 | 说明 | 后续处理 |
|------|------|---------|
| **B3 is_recurring_cross_session** | 动态阈值未实现（V4.4 Plan B satisfaction check 绕过，但未完全关闭通路）。目前依赖硬编码阈值。 | 等 satisfaction check 数据积累后决定 |
| **V4.5 keyword threshold tuning** | `MIN_HITS=2` 保守。等 boost log 积累 1-2 周后调紧到 `max(2, ceil(n_keywords*0.4))`。 | 观察期，待数据驱动 |

---

## 功能状态

| 功能 | 状态 |
|------|------|
| Phase 1/2 skill layer | ✅ |
| Phase 3 plugin | ✅ HermemMemoryProvider 在 Hermes config 中注册 |
| V4.1 误差标注 | ✅ MiniMax-M2.7 异步队列 |
| V4.2 条件Disposition | ✅ l1_dispositions 表 + 提取/向量搜索/三层检测 |
| V4.3 误差激活检索 | ✅ Beta — B1/B2/B4/B5/B6/B8/B9/C3 完成 |
| V4.4 并发修复 | ✅ P0/P1/P2 完成 |
| **V5 主动检索** | ✅ Phase A — 向量搜索、注入、去重完成。`hermes memory health` + `rebuild` CLI。Phase B 待完成。 |
| 意图分类器 | ✅ 13 种意图 + 双层架构 |
| 每日日记 + 综合循环 | ✅ Cron 02:00 / 06:00 |
| C1/C2 gateway hooks | ⚠️ C3（session-end）运行中。C1/C2 已定义但待 gateway 集成。对 V5 主动检索无阻塞。 |
| 单元测试 | ⚠️ 116 通过，3 个失败（intent_classifier 边界 case — 历史遗留，与 V5 无关）。不阻塞 beta 发布。 |
| CI/CD | ❌ 无 |

---

## 设计原则

- **最小依赖**：纯 Python + SQLite，无重型运行时
- **明文存储**：所有记忆为可读 Markdown，可审计和编辑
- **渐进披露**：仅加载相关记忆，避免上下文溢出
- **自我审计**：git log、journal、标注全部公开

## 许可证

MIT
