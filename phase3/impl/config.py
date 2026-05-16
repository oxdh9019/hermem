#!/usr/bin/env python3
"""
Hermem Phase 3 - 共享配置与工具
所有 impl 模块共享的常量、数据库路径、Ollama 客户端
"""
import os
from pathlib import Path

# ── 路径 ────────────────────────────────────────────────────
HERMES_HOME = Path.home() / ".hermes"
MEMORY_DIR   = HERMES_HOME / "memory"
L0_DIR       = MEMORY_DIR / "l0_raw"
DB_PATH      = MEMORY_DIR / "l0_l3.db"
PROFILE_PATH = HERMES_HOME / "memory" / "user_profile.md"

# ── Ollama ─────────────────────────────────────────────────
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
EMBED_MODEL  = "bge-m3:latest"       # 向量模型
LLM_MODEL    = "qwen2.5:3b"   # LLM 模型（提取用，可选 qwen3.5:9b-q4_K_M）

# ── L2 聚合同值 ────────────────────────────────────────────
SIM_THRESHOLD_JOIN  = 0.75   # 新 L1 归入现有 scene
SIM_THRESHOLD_MERGE = 0.85   # 两个 scene 合并
SCENE_DORMANT_DAYS  = 60

# ── L3 Staging 阈值 ─────────────────────────────────────────
STAGING_CONFIRM_THRESHOLD = 5   # 满 5 条推一次确认

# ── L1 提取 Prompt ─────────────────────────────────────────
L1_EXTRACT_PROMPT = """你是一个记忆分析器。从以下会话摘要中提取所有有价值的原子事实。

会话摘要：
{SESSION_SUMMARY}

每条事实需要包含：
- types: 类型数组，允许 1-3 个，取自 [decision, bug-fix, preference, method, todo, unresolved]
- content: 事实内容，用中文写，一条完整的陈述句，30-80 词
- tags: 标签，2-5 个英文或中文标签，代表主题
- value: 长期价值，high | medium | low（只提取 medium 和 high）

要求：
- 尽量多提取有价值的 fact，覆盖摘要中的每个关键事项
- 不要遗漏：决策、方法、偏好、问题、已完成事项
- 不要编造信息，不要添加摘要中没有的内容

输出 JSON 格式（数组）：
{{"facts": [{{"types": ["decision"], "content": "...", "tags": ["sqlite"], "value": "high"}}]}}

示例：

输入摘要：讨论了 SQLite FTS5 中文分词问题。用户尝试了 unicode61、trigram、porter 等 tokenizers，全部失败。最终决定用 Python 2-gram 滑动窗口提取关键词，绕过 SQLite 侧的分词限制。

输出：
{{"facts": [
  {{"types": ["bug-fix", "unresolved"], "content": "用户尝试了 SQLite 的 unicode61、trigram、porter 等分词器，全部失败，中文搜索无法正常工作", "tags": ["sqlite", "chinese-search", "分词"], "value": "high"}},
  {{"types": ["decision", "method"], "content": "用户决定使用 Python 2-gram 滑动窗口提取关键词来绕过 SQLite 的分词限制", "tags": ["sqlite", "chinese-search", "python"], "value": "high"}}
]}}"""
