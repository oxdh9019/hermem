# Phase 1 规范：会话自动摘要 + 启动预热

## 阶段目标

**实施目标**：
- 实现会话结束时的自动摘要生成，无需 Oliver 手动触发
- 实现新会话启动时的上下文预热，自动加载近期记忆
- 记忆以明文 Markdown 存储在 `~/.hermes/memory/` 目录

**测试目标**：
1. 启动一个新的 Hermes 会话，验证是否自动加载了近期会话摘要
2. 结束一个超过 30 分钟的会话，验证是否自动生成了摘要并存入 `~/.hermes/memory/sessions/`
3. Oliver 问"上次我们聊了什么"，验证 Hermes 能准确召回近期话题

## 目录结构

```
~/.hermes/memory/
├── user_profile.md       # Oliver 的持久偏好和事实
├── sessions/
│   └── YYYY-MM-DD.md    # 当日所有会话摘要
└── recent.md            # 最近重要记忆碎片（自动维护）

~/.hermes/skills/hermem/
├── session-summary/     # 会话摘要生成
│   └── SKILL.md
├── memory-warmup/       # 启动预热
│   └── SKILL.md
└── memory-tools/         # 记住/召回指令工具
    └── SKILL.md
```

## 组件规格

### 组件 1: `session-summary` skill

**触发方式**：Oliver 说"结束会话并总结"或"总结本次对话"

**输入**：当前会话的对话历史

**输出**：结构化摘要，追加到 `~/.hermes/memory/sessions/YYYY-MM-DD.md`

**摘要格式**：
```markdown
## [HH:MM-HH:MM] Session Summary

**主题**: <从对话中提取的核心主题>
**完成事项**:
- <事项1>
- <事项2>

**待办/未完成**:
- <待办1>

**关键决策**:
- <决策1>

**下次继续点**: <如果对话未完成，记录继续点>
```

**摘要生成 prompt**：
```
你是一个会话摘要生成器。请分析以下对话历史，生成结构化摘要。

要求：
- 提取核心主题（一句话）
- 列出完成事项（用 bullet point）
- 列出待办/未完成事项
- 记录关键决策
- 如果对话未完成，记录下次继续点
- 保持简洁，总字数不超过 300 字

对话历史：
<DIALOGUE_HISTORY>
```

### 组件 2: `memory-warmup` 

**触发方式**：在 AIAgent 的 system prompt 中注入

**注入时机**：每次新会话开始时

**注入内容**：读取 `~/.hermes/memory/sessions/` 下当日 + 前一日的文件，提取摘要片段，注入到 system prompt 开头

**注入格式**：
```
## 近期会话摘要

### 今日 (YYYY-MM-DD)
<当日 session 摘要>

### 昨日 (YYYY-MM-DD)
<昨日 session 摘要>
```

**注入位置**：在用户 profile 之后、工具列表之前

### 组件 3: `memory-tools` skill

**记住指令**：
- Oliver 说"记住 X" → 追加到 `~/.hermes/memory/user_profile.md`
- Oliver 说"记住我是 | 偏好 | 习惯" → 提取并更新 user_profile.md

**召回指令**：
- Oliver 问"上次我们聊了什么" → 召回当日 + 前一日 session 摘要
- Oliver 问"我们之前讨论过 X 吗" → 搜索 sessions 目录

### 触发检测逻辑

**会话时长检测**（在 session-summary 中实现）：
- 读取会话开始时间（从 session metadata 或第一轮对话时间戳）
- 计算会话时长
- 如果 > 30 分钟，提示 Oliver 是否需要生成摘要

## 实施步骤

- [x] Step 0: 创建目录结构
- [ ] Step 1: 编写 SPEC.md（本文档）
- [ ] Step 2: 实现 `session-summary` skill
- [ ] Step 3: 实现 `memory-warmup` skill
- [ ] Step 4: 实现 `memory-tools` skill
- [ ] Step 5: 编写 Phase 1 测试用例
- [ ] Step 6: 与 Oliver 进行 Phase 1 验收测试
- [ ] Step 7: Phase 1 完成检查，写入 REVIEW.md

## 完成标准

1. Oliver 说"总结本次对话"能生成结构化摘要并存入当日 session 文件
2. 新会话启动时，Hermes 能自动说出"上次/昨天我们聊了……"
3. Oliver 问"上次我们讨论了什么"能准确召回
4. 所有记忆文件以明文 Markdown 存储

## 风险与应对

| 风险 | 应对 |
|------|------|
| 摘要质量不高 | 提供明确模板；允许 Oliver 手动修改 |
| 注入导致 context 溢出 | 限制预热摘要总长度 ≤ 1500 tokens |
| 自动触发时机难判断 | 不做自动触发，由 Oliver 显式触发或手动总结 |
