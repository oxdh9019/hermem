 Hermem 当前实现架构
    
    记忆系统:4 层记忆 + 信号采集 + 结构化加权排序
    
    
    L0 raw  →  L1 facts  →  L2 scenes  →  L3 staged  →  L4 reflections (V5.5)
       ↓         ↓             ↓             ↓
      原文     原子事实       场景聚类      待确认
    
    
    评估信号采集(已有):
    - L1 disposition:condition → prediction → error_count,conf 0.8(来自 session_summary 摘要里那条 disposition)
    - usage_tracker:usage_count / last_used_at,recall 时异步更新
    - recency:时间衰减(DISPOSITION_HALF_LIFE_DAYS = 7)
    - pattern_relevance:概念标签匹配(V5 active retrieval,每 3 turn 触发)
    
    排序公式(隐式,但代码里能拼出来):
    
    score = recency_weight × pattern_relevance × conf × emotional_signal
    
    
    结构上不是"评估系统",是"recall 时多信号加权排序"。每次 recall 都是一次"评估 + 推送"的合一,没有独立的"该不该打扰用户"层(因为 Hermem 是被动 recall,不是主动 push)。
    
    
    
    4 个选项对 Hermem 的可借鉴度
    
    A. 多臂老虎机 / RL  ← 不适用
    
    不适用原因: 你的 Hermem 是被动 recall(用户问什么召回什么),不是主动推(系统决定推什么)。多臂老虎机的前提是"系统在多个动作里选",Hermem 没有这个选择空间。
    
    可借鉴的极小部分: usage_count 其实就是 bandit 里的 reward 隐喻。但没有显式 Q 表 + 没有探索/利用权衡 = 实质上不是 bandit,是 count-based re-ranking。
    
    建议: 不动。
    
    
    
    B. 情感加权检索  ← 已经在做,但没标签化
    
    recency × emotional_intensity × pattern_relevance 里的 emotional_intensity 在 Hermem 里没有显式字段。但 l1_dispositions 的 error_count 隐式承载了"这条记忆被验证过/推翻过"的强度信号。
    
    对照表:
    
    | B 选项维度               | Hermem 当前实现                          | 缺口            |
    |--------------------------|------------------------------------------|-----------------|
    | recency                  | ✅ DISPOSITION_HALF_LIFE_DAYS=7 衰减     | OK              |
    | emotional_intensity      | ⚠️ 隐式在 error_count + disposition.conf | 缺显式情感标签  |
    | pattern_relevance        | ✅ 概念标签匹配                          | OK              |
    | "我知道这重要但我不想做" | ❌ 无                                    | 缺拖延/回避信号 |
    
    可借鉴的具体改动:
    - 在 L1 提取时加 emotional_valence(-1/0/+1) + arousal(0/1)字段
    - recall 加权时引入 emotional_weight = abs(valence) × arousal
    - 加 dismissed_count(被 LLM 跳过的次数,跟 user 主动封存不是一回事)
    
    对应 B 缺点的"拖延症场景": dismissed_count 上升 → 降低权重,这就是"我知道这重要但用户回避"的隐式信号。
    
    
    
    C. 目标图 + 满意度追踪  ← 不适用,over-spec
    
    不适用原因:
    - Hermem 是单用户、被动 recall 系统,Oliver 不会主动声明"我的目标是 X"
    - 目标图需要持续维护(status / importance / last_touched / satisfaction),每次 recall 都要更新图谱状态,overhead 大
    - 你已经有 disposition(condition/prediction/error_count),这是比"goal"更细粒度的"行为规则",再叠一层 goal 概念是冗余
    
    可借鉴的极小部分: last_touched_at(你已经有了,usage_tracker 里的 last_used_at)就是 "last_touched" 的弱化版。不需要单独造 goal graph。
    
    建议: 不动。
    
    
    
    D. 价值向量 V(t)  ← 方向对,但实现错位
    
    核心矛盾:
    - D 假设 V(t) 是全局状态(工作/家庭/健康/学习/社交/...)
    - Hermem 实际是单次 recall 的局部状态(用户当下问的是什么)
    - 在"该返回什么"这个决策点上,V(t) 是输入,不是引擎
    
    可借鉴的部分:
    
    不是"V(t) 取代 recall 排序",而是"V(t) 作为 recall 的额外加权维度":
    
    python
    当前 hermem 排序(假设)
    score = recency × pattern_relevance × conf
    
    加 V(t) 之后
    score = recency × pattern_relevance × conf × value_alignment(memory, V(t))
    
    
    value_alignment 怎么算?Oliver 已经在用的"concept_tags 命中" 就是 value_alignment 的近似物(用户关注什么概念,记忆跟哪些概念相关)。
    
    可借鉴的具体改动:
    - 不需要新造 V(t) 状态机
    - 把"概念标签命中数"显式化,作为排序的一个独立因子
    - 这等价于 D 的"维度 i 关切度"的离散版
    
    
    
    综合建议(分优先级)
    
    | 优先级 | 改动                                         | 来源              | 理由                                                           |
    |--------|----------------------------------------------|-------------------|----------------------------------------------------------------|
    | P0     | L1 提取加 emotional_valence / arousal 字段   | B                 | Hermem 唯一真实缺的能力,加 1 个 prompt 指令 + 1 个加权因子即可 |
    | P1     | recall 加 dismissed_count(被 LLM 跳过的次数) | B(补"拖延症"缺口) | 隐式信号,不打扰 Oliver,自然沉淀                                |
    | P2     | 概念标签命中数显式化成排序因子               | D(轻量化)         | 跟现有 pattern_relevance 协同,不引入 V(t) 全局状态机           |
    | 不做   | 显式 V(t) 状态机                             | D                 | 单用户被动 recall 场景 over-spec                               |
    | 不做   | 显式 goal graph                              | C                 | disposition 已经覆盖,且 goal 概念粒度太粗                      |
    | 不做   | bandit/RL 框架                               | A                 | 场景不匹配(被动 recall,不是主动 push)                          |
    
    一句话总结:
    
    Hermem 当前是"被动 recall + 多信号加权",不是"主动 push + 评估系统"。所以 A/C/D 都不直接适用,只有 B 的"情感权重"是真实缺口——加 emotional_valence / arousal 字段是性价比最高的借鉴路径。
    
    
     继续。在我们刚定的优先级上往前推:
    
    P0:emotional_valence + arousal 字段
    
    具体实现路径(具体到行号):
    
    1. L1 提取 prompt 加指令
    
    phase3/impl/l1_extract.py 里 LLM 提取 facts 的 prompt,加输出字段:
    json
    {
      "content": "...",
      "types": [...],
      "emotional_valence": -1 | 0 | 1,   // 负面/中性/正面
      "arousal": 0.0 | 1.0,                // 平静/激烈
      "concept_tags": [...]
    }
    
    
    LLM 已经在按这个 schema 输出大部分字段,加两个键不会破坏现有 parser。
    
    2. L1 schema 加列
    
    phase3/impl/database.py 的 l1_facts 表(或对应表)加两列:
    sql
    ALTER TABLE l1_facts ADD COLUMN emotional_valence INTEGER DEFAULT 0;
    ALTER TABLE l1_facts ADD COLUMN arousal REAL DEFAULT 0.0;
    
    
    3. recall 加权公式扩一个因子
    
    phase3/impl/vector_search.py(或 vectorstore.py)当前排序里加:
    python
    emotional_weight = abs(valence) × arousal   # 0~1 范围
    score = cosine_sim × recency × pattern_relevance × (1 + α × emotional_weight)
    
    
    α 从 0.1 起调,这个因子在排序里只是加权不主导,保证不影响现有召回质量。
    
    
    
    P1:dismissed_count 字段
    
    这个更微妙,要分清两个信号:
    
    | 信号            | 含义                                 | 实现位置              |
    |-----------------|--------------------------------------|-----------------------|
    | usage_count     | "这条记忆被 recall 过几次"           | 已有,usage_tracker.py |
    | dismissed_count | "被 recall 出来后 LLM 没用,直接无视" | 没有,新增             |
    
    难点: 怎么知道 LLM 没用?
    
    Hermem 的工作流:
    
    recall → top-k chunks  →  LLM 拿这堆 chunks 写答案
    
    
    LLM 用了哪几条、没用哪几条,Hermem 看不到(LLM 是黑盒)。要捕获 dismissed_count 有两条路:
    
    路 1: prompt 后自检(轻量)
    在 recall 后给 LLM 一个 follow-up:"你刚才的回答里引用了哪几条 memory_id?",LLM 回个 list。Hermem 知道 total - cited = dismissed。
    - 优点:简单,prompt 加一行
    - 缺点:多一次 LLM 调用,token 成本 ×2
    
    路 2: 改用 tool calling(准确)
    把 recall chunks 变成 LLM 能"调用"的 tool,LLM 必须显式声明"用了哪几条"。
    - 优点:100% 准确
    - 缺点:改 recall API 接口,破坏现有 hermem_search 工具 schema,所有调用方都要改
    
    我建议路 1,但只对 top-3 的 chunk 做 follow-up(不是全量),把成本控制在 +30% 而不是 ×2。
    
    
    
    这两个改动的耦合关系:
    
    P0 和 P1 是独立的,可以分开做:
    - P0 加 2 个字段 + 1 个加权因子 → 1 个文件改 3 处
    - P1 加 1 个字段 + 1 个 follow-up 调用 → 2 个文件改
    
    今天要不要把 P0 先做了? P0 改完会让"情感重量"这个维度真正落到排序里,后续观察 Oliver 主动反馈(主动用 hermem_search 时的反应)就能调 α。
    
    具体行动(等你说"开始"):
    1. 看 phase3/impl/l1_extract.py 当前 prompt 全文
    2. 看 phase3/impl/database.py 当前 l1_facts schema
    3. 看 phase3/impl/vector_search.py 当前排序代码
    4. 改 3 处:prompt + schema + 排序
