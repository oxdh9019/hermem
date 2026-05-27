#!/usr/bin/env python3
"""
Hermem Phase 3 - 共享配置与工具
所有 impl 模块共享的常量、数据库路径、Ollama 客户端
"""

import os
from pathlib import Path

# ── 路径 ────────────────────────────────────────────────────
HERMES_HOME = Path.home() / ".hermes"
MEMORY_DIR = HERMES_HOME / "memory"
L0_DIR = MEMORY_DIR / "l0_raw"
DB_PATH = MEMORY_DIR / "l0_l3.db"
PROFILE_PATH = HERMES_HOME / "memory" / "user_profile.md"

# ── Ollama ─────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
EMBED_MODEL = "bge-m3:latest"  # 向量模型
LLM_MODEL = "qwen3.5:4b-no-think"  # Ollama LLM 统一模型

# ── L2 聚合同值 ────────────────────────────────────────────
SIM_THRESHOLD_JOIN = 0.75  # 新 L1 归入现有 scene
SIM_THRESHOLD_MERGE = 0.85  # 两个 scene 合并
SCENE_DORMANT_DAYS = 60

# ── B6: Disposition 衰减机制 ───────────────────────────────
DISPOSITION_HALF_LIFE_DAYS = 7  # 半衰期 7 天
DISPOSITION_MIN_COUNT = 2  # 至少 2 次错误才增强
DISPOSITION_MAX_FACTOR = 2.0  # 最高增强到 2 倍
DISPOSITION_BASE_WEIGHT = 1.0  # 中性起点
# B8: ranking cap — error_count 超过此值时封顶，不再线性增长
DISPOSITION_MAX_ERROR_COUNT = 5  # error_count 超过 5 则封顶（防止单个 disposition 垄断）

# ── L3 Staging 阈值 ─────────────────────────────────────────
STAGING_CONFIRM_THRESHOLD = 5  # 满 5 条推一次确认

# ── Error Annotation ─────────────────────────────────────────
ERROR_ANNOTATION_MODEL = "MiniMax-M2.7"  # 可切换到 qwen3.5:9b-q4_K_M 做对比实验

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

**示例1：design_decision_error（正例）**
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

**示例2：factual_misunderstanding（正例）**
对话片段：
助手：已为你创建了微博监控任务，每4小时执行。
用户：我没有要求你创建任务，我只是问会不会占用配额。
输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "用户同意了创建监控任务",
      "actual_outcome": "用户只是询问配额，未同意创建",
      "error_type": "factual_misunderstanding",
      "severity": "high",
      "confidence": 1.0
    }}
  ],
  "surprise_level": "high",
  "meta_prediction": "将询问误认为授权",
  "overall_quality_score": 0.2
}}

**示例3：preference_misjudgment（正例）**
对话片段：
助手：我觉得用方案B更好，结构更清晰。
用户：其实我更在意执行速度，结构清晰度是次要的。
输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "用户优先考虑结构清晰度",
      "actual_outcome": "用户实际优先考虑执行速度",
      "error_type": "preference_misjudgment",
      "severity": "medium",
      "confidence": 0.9
    }}
  ],
  "surprise_level": "medium",
  "meta_prediction": "错误估计了用户的优先级",
  "overall_quality_score": 0.4
}}

**示例4：timing_misprediction（正例）**
对话片段：
助手：等配置完成后再启动定时任务。
用户：先启动，再慢慢调配置也行。
输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "配置必须在启动前完成",
      "actual_outcome": "用户接受先启动后配置",
      "error_type": "timing_misprediction",
      "severity": "low",
      "confidence": 0.85
    }}
  ],
  "surprise_level": "low",
  "meta_prediction": "过度保守的时序假设",
  "overall_quality_score": 0.6
}}

**示例5：topic_shift_unexpected（正例）**
对话片段：
助手：关于 Hermem V4.2 的 Disposition 更新，我已经提交了 PR。
用户：对了，之前说的 B9 现在到什么程度了？
输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "对话会继续在 Hermem PR 话题上",
      "actual_outcome": "用户切换到 B9 进度话题",
      "error_type": "topic_shift_unexpected",
      "severity": "medium",
      "confidence": 0.9
    }}
  ],
  "surprise_level": "medium",
  "meta_prediction": "未预判到话题跳转",
  "overall_quality_score": 0.5
}}

**示例6：反例——措辞不同但本质一致（不要记录）**
对话片段：
助手：方案A更简洁。
用户：我觉得方案B其实也很好。
输出：
{{
  "prediction_errors": [],
  "surprise_level": "low",
  "meta_prediction": "用户未否定方案A，两者都是正面评价",
  "overall_quality_score": 0.8
}}
（解释：用户说"B也很好"不等于"A不好"，这不是矛盾，不记录为误差。）

**示例7：反例——推断过度（不要记录）**
对话片段：
助手：已为你开启预取功能。
用户：预取是什么意思？
输出：
{{
  "prediction_errors": [],
  "surprise_level": "low",
  "meta_prediction": "用户只是询问概念，未表示不满",
  "overall_quality_score": 0.75
}}
（解释：用户询问不等于不满，推断"用户对开启不满"超出原文证据。）

**示例8：边界 case——低 confidence（可记录但不建议）**
对话片段：
助手：看起来你对这个方案比较犹豫。
用户：也还好，就是在想有没有更好的替代方案。
输出：
{{
  "prediction_errors": [
    {{
      "model_prediction": "用户对当前方案持保留态度",
      "actual_outcome": "用户实际上在主动寻求优化",
      "error_type": "preference_misjudgment",
      "severity": "low",
      "confidence": 0.65
    }}
  ],
  "surprise_level": "low",
  "meta_prediction": "轻微误判用户态度",
  "overall_quality_score": 0.7
}}
（confidence=0.65 低于0.6阈值，本应省略，此处示范边界 case。）

### 重要规则
- **自检**：每条 prediction_errors 必须满足「model_prediction 与 actual_outcome 构成真实矛盾」才能输出
- **措辞不同 ≠ 矛盾**：用户用不同方式表达相同意思是正常的，不记录
- **推断过度 → 不记录**：从用户行为推断出对话中未明确表达的结论，需要直接证据
- **低 confidence（<0.6）→ 不输出**：无法确定的情况不要制造记录
- **无 error → 输出空数组**：对话顺利时输出空 prediction_errors，这是正常结果
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


# ── V4.5: Conditioned Dispositions ─────────────────────────
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

# ── V5: Active Retrieval（对话中主动检索）────────────────────────
# 向量检索开关与阈值
ACTIVE_RETRIEVAL_ENABLED = True
ACTIVE_RETRIEVAL_THRESHOLD_HIGH = 0.85  # 高置信：直接注入
ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = 0.65  # 中置信：缓存记录
ACTIVE_RETRIEVAL_TOP_K = 3  # 每次最多注入 3 条
ACTIVE_RETRIEVAL_FREQUENCY = 3  # 每 N 条消息触发一次（0=禁用）
EMBEDDING_MODEL = "bge-m3:latest"  # 向量模型（复用现有 EMBED_MODEL）
BATCH_SIZE = 32  # 批量 embedding 尺寸
