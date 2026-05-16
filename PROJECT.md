# Hermem: Hermes 轻量记忆增强系统

## 项目概述

**项目名称**: Hermem (Hermes Memory Enhancement)
**创建时间**: 2026-04-28
**项目状态**: 规划中

### 背景与动机

当前 Hermes 的记忆系统主要依赖手动触发（Oliver 说"记住"），缺乏会话间的自动连续性。对比 claude-mem (68k stars) 等成熟方案，核心差距在于：

1. **无自动摘要**：会话结束即消散，没有自动沉淀
2. **无启动预热**：新会话不自动加载历史上下文
3. **无概念召回**：session_search 只能做字面关键词匹配

### 项目目标

为 Hermes 构建轻量记忆增强系统，在不引入重型依赖（Chroma、Milvus、Bun）的前提下，实现：
- 会话自动摘要沉淀
- 新会话上下文预热
- 概念化语义召回

### 设计原则

- **最小化依赖**：纯 Python + 现有 SQLite FTS5，不引入新运行时
- **纯 Skill 实现**：不修改 Hermes 核心代码，以 skill 形式交付
- **渐进披露**：用多少记忆取多少，避免 context overflow
- **可审计**：所有记忆以明文 Markdown 存储，可读可改可删

---

## 项目结构

```
~/.hermes/projects/hermem/
├── PROJECT.md          # 本文档：项目规划
├── phase1/             # Phase 1 实施文档
│   ├── SPEC.md         # 阶段规范
│   ├── TODO.md         # 待办清单
│   └── REVIEW.md       # 阶段完成检查
├── phase2/             # Phase 2 实施文档
│   ├── SPEC.md
│   ├── TODO.md
│   └── REVIEW.md
└── phase3/             # Phase 3 实施文档
    ├── SPEC.md
    ├── TODO.md
    └── REVIEW.md
```

---

## 阶段总览

| 阶段 | 核心功能 | 目标 | 难度 | 依赖 |
|------|----------|------|------|------|
| **Phase 1** | 会话自动摘要 + 启动预热 | 会话连续感 | 低 | 无 |
| **Phase 2** | 概念标签 + 语义召回 | 从关键词到语义 | 中 | 无 |
| **Phase 3** | L0-L3 分层记忆系统 | 原子事实层 + 场景聚合 + Profile 更新 | 中高 | Phase 1, 2 | ✅ 完成 |

---

## Phase 1: 会话自动摘要 + 启动预热

### 阶段目标

**实施目标**：
- 实现会话结束时的自动摘要生成，无需 Oliver 手动触发
- 实现新会话启动时的上下文预热，自动加载近期记忆
- 记忆以明文 Markdown 存储在 `~/.hermes/memory/` 目录

**测试目标**：
- 启动一个新的 Hermes 会话，验证是否自动加载了近期会话摘要
- 结束一个超过 30 分钟的会话，验证是否自动生成了摘要并存入 `~/.hermes/memory/sessions/`
- Oliver 问"上次我们聊了什么"，验证 Hermes 能准确召回近期话题

### 核心设计

#### 目录结构

```
~/.hermes/memory/
├── user_profile.md       # Oliver 的持久偏好和事实（手动 + 自动更新）
├── sessions/
│   ├── 2026-04-28.md     # 当日所有会话摘要
│   └── 2026-04-27.md
├── recent.md             # 最近 N 条最重要的记忆碎片（自动维护）
└── concepts/             # 概念标签索引（Phase 2）
    └── index.md
```

#### 记忆文件格式

```markdown
---
date: 2026-04-28
session_id: abc123
duration: 45min
concepts: []
---

## Session Summary

**时间**: 2026-04-28 14:00-14:45
**主题**: Hermes 记忆系统调研

### 完成事项
- 调研了 claude-mem、OpenViking、nanoclaw 等项目
- 分析了 OpenClaw 的纯文本记忆模式
- 确定了 Hermem 三阶段方案

### 待办/未完成
- [ ] 等待 Oliver 确认 Phase 1 实施
- [ ] 设计会话摘要生成 prompt

### 下次继续点
从 Phase 1 的 SPEC.md 开始实施
```

#### 组件清单

1. **`session-summary` skill**
   - 触发条件：会话结束时（手动调用或自动检测）
   - 功能：分析对话历史 → 生成结构化摘要 → 追加到当日 session 文件
   - 存放位置：`~/.hermes/skills/hermem/session-summary/`

2. **`memory-inject` hook**
   - 触发时机：新会话开始时
   - 功能：读取当日 + 前一日 session 摘要 → 写入 system prompt
   - 注入位置：在 AIAgent 的 system prompt builder 中添加

3. **Oliver 指令接口**
   - `记住 X` → 写入 `user_profile.md`
   - `我们上次讨论过X吗` → 搜索记忆
   - `上次会话做了什么` → 召回摘要

#### 实施步骤

- [ ] Step 1: 创建 `~/.hermes/memory/` 目录结构
- [ ] Step 2: 编写 `session-summary` skill 的 SPEC.md
- [ ] Step 3: 实现会话摘要生成逻辑
- [ ] Step 4: 实现 `memory-inject` 预热逻辑
- [ ] Step 5: 实现 Oliver 的 `记住/召回` 指令工具
- [ ] Step 6: 编写 Phase 1 测试用例
- [ ] Step 7: 与 Oliver 进行 Phase 1 验收测试
- [ ] Step 8: Phase 1 完成检查，写入 REVIEW.md

### 完成标准

1. 新会话启动时，Hermes 能自动说出"上次我们聊了……"
2. 超过 30 分钟的会话结束后，摘要自动沉淀到 `~/.hermes/memory/sessions/`
3. Oliver 问"我们上次讨论了什么"能准确召回
4. 所有记忆文件以明文 Markdown 存储，可手动查看和修改

---

## Phase 2: 概念标签 + 语义召回

### 阶段目标

**实施目标**：
- 为每条记忆条目添加概念标签（decision、bug-fix、preference、architecture 等）
- 检索时支持按概念过滤，不仅仅是字面关键词匹配
- 自动从会话中提取概念标签，无需 Oliver 手动标注

**测试目标**：
- Oliver 问"上次处理 SQLite 问题的方法是什么"，即使没有提到"SQLite"字样，也能召回相关记忆
- Oliver 说"记住 X"时，Hermes 自动提取 X 的概念标签
- 概念标签准确率达到 80% 以上（通过抽样人工检查验证）

### 核心设计

#### 概念标签体系

```
概念类别（预定义）:
- preference       # Oliver 的偏好和习惯
- decision         # 做出的重要技术决策
- bug-fix          # BUG 和解决方案
- architecture     # 系统架构相关
- project          # 具体项目（StoryAgent、微博监控等）
- tool-usage       # 工具使用模式
- learning         # 学到的知识/方法论
- todo             # 待办事项
- unresolved       # 未解决问题
```

#### 增强的检索流程

```
Oliver 问: "上次处理数据库问题的方法"

Step 1: 提取问句概念 → [database, problem-solving]
Step 2: 用概念过滤候选记忆 → 排除不包含 [database] 标签的条目
Step 3: FTS5 关键词确认 → 在过滤结果中搜索"处理"、"方法"
Step 4: 召回 + 展示 → 返回最相关的记忆片段
```

#### 实施步骤

- [ ] Step 1: 设计概念标签体系，写入 SPEC.md
- [ ] Step 2: 修改 session-summary 生成逻辑，自动提取概念标签
- [ ] Step 3: 实现概念过滤检索函数
- [ ] Step 4: 实现 `记住 X` 的自动标签提取
- [ ] Step 5: 编写 Phase 2 测试用例（概念召回准确率）
- [ ] Step 6: 与 Oliver 进行 Phase 2 验收测试
- [ ] Step 7: Phase 2 完成检查，写入 REVIEW.md

### 完成标准

1. 会话摘要中自动包含概念标签（无需手动标注）
2. "上次讨论过 X 吗"类型的问句能准确召回，即使字面不匹配
3. Oliver 手动 `记住 X` 时自动提取标签
4. 概念召回准确率 80% 以上（Oliver 抽样验证）

---

## Phase 3: L0-L3 分层记忆系统

### 阶段目标

**实施目标**：
- L0 原始会话存档（JSON，500MB 配额管理）
- L1 原子事实提取（LLM 提取 types/content/tags/value）
- L2 场景聚合（embedding 相似度聚合同 topic facts）
- L3 staging area + user_profile.md 更新（preference 确认机制）

**测试目标**：
- L1 类型准确率 ≥ 80%（人工抽检）
- 模糊查询召回率 ≥ 85%
- L2 相同 topic 自动合并
- L0 可追溯到原始会话

### 核心设计

#### 四层数据模型

```
L0 原始会话 (JSON, ~/.hermes/memory/l0_raw/)
    ↓ extract (Ollama LLM)
L1 原子事实 (SQLite l0_l3.db, 向量 BLOB)
    ↓ aggregate (embedding 相似度 0.75)
L2 场景聚合 (scene, occurrence_count)
    ↓ stage (type=preference)
L3 Staging Area (pending confirmation)
    ↓ confirm
user_profile.md (confirmed preferences)
```

### 实施状态

✅ 全部完成（2026-05-16）
- `phase3/impl/`: 完整实现（db_init, l0_store, l0_load, l1_extract, l1_search, l2_aggregate, l3_staging）
- `phase3/cron_daily.py`: 定时处理脚本（每天 6:00 和 18:00）
- cron job: `a70a7eb3bf8d`
- 当前数据: 316 L1 facts, 41 L2 scenes, 0 L3 staging（等待新会话 preference facts）
- 配合 `git status` 确认当前状态
```

### 实施步骤

已全部完成，见 `phase3/TODO.md`（Step 0-6 + Step 4b 维护任务）

---

## 项目风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Phase 1 摘要质量不高 | 记忆无效 | 提供明确摘要模板；让 Oliver 手动优化；持续迭代 |
| Hermem 与现有 session_search 冲突 | 功能重叠 | Phase 1 不动 session_search；Phase 2 增强而非替换 |
| Oliver 记忆隐私顾虑 | 用户信任 | 所有记忆明文存储；提供 `forget` 指令删除特定记忆 |
| Phase 3 推荐扰民 | 用户体验 | 已通过 preference 确认机制替代主动推荐，避免强制推送 |

---

## 更新日志

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-04-28 | v0.1 | 初始化项目文档，建立三阶段规划 |
| 2026-05-16 | v1.0 | Phase 3 全部完成（L0-L3 分层记忆系统 + 定时 cron） |
