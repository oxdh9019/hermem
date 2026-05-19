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

# ── B6: Disposition 衰减机制 ───────────────────────────────
DISPOSITION_HALF_LIFE_DAYS = 7    # 半衰期 7 天
DISPOSITION_MIN_COUNT       = 2   # 至少 2 次错误才增强
DISPOSITION_MAX_FACTOR      = 2.0  # 最高增强到 2 倍
DISPOSITION_BASE_WEIGHT     = 1.0 # 中性起点

# ── L3 Staging 阈值 ─────────────────────────────────────────
STAGING_CONFIRM_THRESHOLD = 5   # 满 5 条推一次确认

# ── Error Annotation ─────────────────────────────────────────
ERROR_ANNOTATION_MODEL = "qwen2.5:3b"   # 可切换到 qwen3.5:9b-q4_K_M 做对比实验

ERROR_ANNOTATION_PROMPT = """你是一个严格的预测误差审计系统。请基于**对话原文中的明确内容**，逐条识别助手（Hermes）作出的**可被证伪的预测或隐含假设**，并与实际结果对比。

对话摘要：
{SESSION_SUMMARY}

助手提取的原子事实：
{L1_FACTS}

### 预测的定义（必须同时满足）
1. 它必须是对话中**可以明确表述出来的预期**（如"我认为用户会同意X"、"方案A应该在步骤B之后"）。
2. 它必须**在后续对话中被明确事实推翻**，或用户明确纠正。
3. 如果只是"我猜可能"，但没有被否定，不记录为误差。

### 误差类型（从以下枚举选择）
- `design_decision_error`：设计顺序、架构选择、实现方案被纠正
- `factual_misunderstanding`：对事实、用户状态、外部信息的错误理解
- `timing_misprediction`：对事件发生顺序或时间的预测错误
- `topic_shift_unexpected`：用户突然切换话题或引入新信息源
- `preference_misjudgment`：对用户偏好、态度的错误判断
- `other`

### 输出格式（严格JSON，不要markdown包裹）
{{
  "prediction_errors": [
    {{
      "model_prediction": "一句话描述预测，必须引用对话中的原意",
      "actual_outcome": "实际发生的事，必须引用对话中的证据",
      "error_type": "从上面列表选择",
      "severity": "high|medium|low",
      "confidence": 0.95
    }}
  ],
  "surprise_level": "high|medium|low",
  "meta_prediction": "一句话总结整体预测质量",
  "overall_quality_score": 0.85
}}

### 新增字段说明
- `confidence`（0-1）：你对这条误差判断的确信程度。
  1表示完全确定（对话中有直接矛盾证据），
  0.5表示需要推断但有间接证据，
  低于0.6不建议输出（直接省略该条）。
- `overall_quality_score`（0-1）：整场对话预测质量的自我评分。
  1表示所有预测都正确，0表示全部错误。

### 重要规则
- 不要构造内部叙事。只写对话中**明确出现**的内容。
- 如果同一对话中有多个预测误差，每条都要列出。
- 如果没有预测误差，输出空数组，但需要给出 `overall_quality_score` 和 `meta_prediction`。
- **自检步骤**：检查每条 `model_prediction` 与 `actual_outcome` 是否构成真实矛盾。如果两者本质一致（只是措辞不同），删除该条。
- 不确定时，不要编造。

### 示例（正确格式）
对话片段：
助手：我建议把 annotation 放在 L1 提取之前。
用户：不对，应该放在 L1 之后。

输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "annotation 应该在 L1 之前",
      "actual_outcome": "用户纠正为 L1 之后",
      "error_type": "design_decision_error",
      "severity": "high",
      "confidence": 1.0
    }}
  ],
  "surprise_level": "high",
  "meta_prediction": "在设计顺序上犯了错误",
  "overall_quality_score": 0.3
}}

### 错误示例（不要这样做）
{{
  "model_prediction": "助手认为 L1 之后是对的，但实际用户认为 L1 之前",
  ...
}}
"""

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

输入摘要：讨论了 SQLite FTS5 中文分词问题。Oliver 尝试了 unicode61、trigram、porter 等 tokenizers，全部失败。最终决定用 Python 2-gram 滑动窗口提取关键词，绕过 SQLite 侧的分词限制。

输出：
{{"facts": [
  {{"types": ["bug-fix", "unresolved"], "content": "Oliver 尝试了 SQLite 的 unicode61、trigram、porter 等分词器，全部失败，中文搜索无法正常工作", "tags": ["sqlite", "chinese-search", "分词"], "value": "high"}},
  {{"types": ["decision", "method"], "content": "Oliver 决定使用 Python 2-gram 滑动窗口提取关键词来绕过 SQLite 的分词限制", "tags": ["sqlite", "chinese-search", "python"], "value": "high"}}
]}}"""


# ── V4.2: Conditioned Dispositions ─────────────────────────
DISPOSITION_EXTRACT_PROMPT = """你是一个记忆倾向分析器。基于以下对话摘要，识别用户（Oliver）行为的条件-预测对。

对话摘要：
%s

已有原子事实：
%s

任务：从对话中提取所有有明确证据的条件-预测对。

格式（严格JSON数组，不要markdown包裹）：
[["condition", "...", "prediction", "...", "confidence", 0.x], ...]

示例1（有行为倾向时）：
输入摘要：Oliver说"我讨厌你每次都先说好话再提意见，直接说就行了"。助手理解了他的偏好。
输出：
[["condition", "当 Oliver 收到先夸后批的反馈时", "prediction", "他会感到不耐烦，希望助手直接提出批评", "confidence", 0.95]]

示例2（有多次行为证据时）：
输入摘要：Oliver多次说"不同意spawn subagent"，坚持直接联系writer agent。他要求所有操作必须按"创作流程手册"执行，不允许spawn新的子代理。
输出：
[["condition", "当需要联系子代理时", "prediction", "Oliver 禁止 spawn subagent，要求直接联系现有 agent", "confidence", 0.9], ["condition", "当有新章节启动时", "prediction", "Oliver 要求严格按照'创作流程手册'执行，不接受偏离", "confidence", 0.85]]

示例3（无行为倾向时）：
输入摘要：助手帮助Oliver修复了一个bug，清理了test_l0_l3.db测试库文件。
输出：
[]

规则：
- condition 以 "当 Oliver ..." 或 "当用户 ..." 开头（中文）
- prediction 用中文描述 Oliver 的预期行为或偏好
- 只输出有明确对话证据的倾向，不要过度泛化
- confidence 基于证据强度：直接引用 Oliver 原话=0.9-1.0，有明确暗示=0.7-0.85，模糊推断=0.5-0.65
- confidence < 0.6 的不要输出
- 如果没有明确的条件-预测对，输出空数组 []"""
