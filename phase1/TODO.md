# Phase 1 TODO

## 实施清单

- [x] Step 0: 创建目录结构
- [x] Step 1: 编写 SPEC.md
- [x] Step 2: 实现 `session-summary` skill → `~/.hermes/skills/hermem/session-summary/SKILL.md`
- [x] Step 3: 实现 `memory-warmup` skill → `~/.hermes/skills/hermem/memory-warmup/SKILL.md`
- [x] Step 4: 实现 `memory-tools` skill → `~/.hermes/skills/hermem/memory-tools/SKILL.md`
- [x] Step 5: 创建 Hermem 主 skill → `~/.hermes/skills/hermem/SKILL.md`
- [ ] Step 6: 创建初始化记忆文件（user_profile.md 示例）
- [ ] Step 7: 重启 Hermes Gateway 使 skill 生效
- [ ] Step 8: 告知 Oliver 测试指令

## 手动测试指令（Step 8）

### 测试 1: 记忆召回
```
Oliver: "上次我们聊了什么"
预期: Hermes 召回近期 session 摘要（如果有）
```

### 测试 2: 记住新内容
```
Oliver: "记住 Hermem 是 Hermes 的记忆增强系统"
预期: 内容写入 ~/.hermes/memory/user_profile.md
```

### 测试 3: 记住偏好
```
Oliver: "记住我偏好简洁的回复，不要过多 emoji"
预期: 偏好写入 user_profile.md 的 [偏好] section
```

### 测试 4: 搜索记忆
```
Oliver: "我们之前讨论过 Hermem 吗"
预期: 搜索 sessions/ 目录，返回相关结果
```

### 测试 5: 会话摘要
```
Oliver: "总结本次对话"
预期: 生成摘要存入 ~/.hermes/memory/sessions/YYYY-MM-DD.md
```

### 测试 6: 查看记忆状态
```
Oliver: "查看记忆状态"
预期: 显示 ~/.hermes/memory/ 目录概览
```

## 已知限制（Phase 1）

1. **memory-warmup 不自动触发**：需要 Oliver 主动说"/start" 或 "上次我们聊了什么"来加载预热
2. **session-summary 需要手动调用**：需要 Oliver 说"总结本次对话"，没有自动检测会话时长
3. **Gateway 需要重启**：新增 skill 后必须重启才能生效

这些限制是 Phase 1 的设计选择，Phase 2/3 会逐步改进（自动预热、自动摘要触发）。
