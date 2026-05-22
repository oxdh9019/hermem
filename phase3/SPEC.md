# Hermem Phase 3: L0-L3 分层记忆系统

**项目**: Hermem - Hermes 记忆增强系统
**阶段**: Phase 3
**版本**: v1.0
**日期**: 2026-05-15
**状态**: 待 Oliver 确认后实施

---

## 1. 背景与动机

### 1.1 现有系统的问题

Phase 1（会话摘要）和 Phase 2（语义搜索）解决了"记忆沉淀"和"检索"的基本问题，但仍存在三个根本性缺陷：

| 缺陷 | 表现 |
|------|------|
| 无证据层 | 只有压缩后的摘要，细节无法回溯 |
| 无分层聚合 | 所有记忆扁平存储，"Oliver 最近在忙什么"无法回答 |
| 检索质量不稳 | FTS5/语义搜索无法理解用户问题的真实意图类型 |

这些问题在短会话、浅对话场景下不明显，但随着 Oliver 使用时间增长，记忆量增加，检索会逐渐退化为"搜不到想要的"。

### 1.2 参考架构：TencentDB Agent Memory

本设计借鉴了 [TencentDB Agent Memory](https://github.com/Tencent/TencentDB-Agent-Memory) 的**记忆分层 + 渐进披露**理念，但做了大量简化：

- 不借鉴 Mermaid 符号系统（不适用于 Oliver 的短会话指令型场景）
- 不引入新的存储引擎（复用现有 NumPy + SQLite + Ollama）
- 不强制外部 API 依赖（Hermem 已重度使用外部 API）

核心借鉴点只有一条：**记忆的形成和召回都必须是分层的**。

---

## 2. 系统架构

### 2.1 四层数据模型

```
┌─────────────────────────────────────────────┐
│  L3-Persona   Oliver 个人画像                │  ← 每次会话启动时注入
│               user_profile.md               │
├─────────────────────────────────────────────┤
│  L2-Scene     场景聚合                       │  ← 检索时作为主骨架
│               (scene summary)                │
├─────────────────────────────────────────────┤
│  L1-Fact      原子事实                       │  ← 主要检索层
│               (types + vector)              │
├─────────────────────────────────────────────┤
│  L0-Raw       原始会话 JSON                  │  ← 按需加载，不主动注入
│               (full messages)                │
└─────────────────────────────────────────────┘
```

### 2.2 各层数据形态

| 层 | 内容 | 存储 | 人可读 | 上下文注入 |
|----|------|------|--------|-----------|
| L0-Raw | 原始 messages 数组 | `~/.hermes/memory/l0_raw/{session_id}.json` | ❌ | 按需 |
| L1-Fact | 原子事实 | SQLite + NumPy 向量 | 部分（YAML frontmatter） | 检索层 |
| L2-Scene | 场景聚合 | SQLite + Markdown | ✅ | 检索时作为主骨架 |
| L3-Persona | Oliver 画像 | `~/.hermes/memory/user_profile.md` | ✅ | 每次会话启动 |

---

## 3. 各层详细设计

### 3.1 L0-Raw：原始会话存档

#### 3.1.1 数据结构

```json
{
  "session_id": "2026-05-15_083900",
  "l0_ref": "l0_20260515_083900",
  "start": "2026-05-15T08:39:00+08:00",
  "end": "2026-05-15T08:52:00+08:00",
  "compressed": false,
  "messages": [
    {
      "role": "user",
      "content": "检查一下 openclaw 和 qclaw 的安装情况",
      "ts": "2026-05-15T08:39:05+08:00"
    },
    {
      "role": "assistant",
      "content": "...",
      "ts": "2026-05-15T08:39:10+08:00",
      "tool_calls": [...]
    }
  ]
}
```

#### 3.1.2 存储策略

- **位置**: `~/.hermes/memory/l0_raw/`
- **配额**: 500MB（可配置），超出后删除最旧的会话
- **压缩**: tool_calls 大输出使用 gzip 压缩（`compressed: true`）
- **异步写入**: L0 保存为独立步骤，不阻塞 session-summary 生成
- **失败重试**: 写入失败进入重试队列，下次会话触发时重试

#### 3.1.3 保留与清理规则

```python
QUOTA_BYTES = 500 * 1024 * 1024  # 500MB

def enforce_l0_quota():
    """超出配额时，删除最旧的 L0 文件直到低于配额"""
    total_size = sum(f.stat().st_size for f in L0_DIR.glob("*.json"))
    if total_size > QUOTA_BYTES:
        # 按修改时间排序，删除最旧的直到低于 80%配额
        oldest = sorted(L0_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in oldest:
            if total_size < QUOTA_BYTES * 0.8:
                break
            total_size -= f.stat().st_size
            f.unlink()
```

#### 3.1.4 按需加载 L0

```python
def load_l0_detail(l0_ref: str, context_hint: str = None) -> str:
    """
    按需读取 L0 原始会话。
    context_hint 用于过滤相关 messages（而不是返回全部）。
    """
    l0_path = L0_DIR / f"{l0_ref}.json"
    if not l0_path.exists():
        return "（原始会话已过期/不存在）"

    with open(l0_path) as f:
        l0 = json.load(f)

    if not context_hint:
        return json.dumps(l0, ensure_ascii=False)

    # 简单过滤：保留包含关键词的 messages
    relevant = [m for m in l0["messages"] if context_hint.lower() in m.get("content", "").lower()]
    return json.dumps({**l0, "messages": relevant}, ensure_ascii=False, indent=2)
```

---

### 3.2 L1-Fact：原子事实提取

#### 3.2.1 数据结构

```json
{
  "id": "fact_001",
  "l0_ref": "l0_20260515_083900",
  "types": ["decision", "method"],
  "type_confidence": 0.85,
  "fallback_type": "other",
  "content": "Oliver 决定使用 NumPy + SQLite 混合存储作为 Hermem Phase 2 的向量检索方案，因为不需要引入新的外部依赖",
  "tags": ["hermem", "storage", "sqlite", "numpy"],
  "value": "high",
  "chunk_vector": [0.123, -0.456, ...],
  "created_at": "2026-05-15T09:00:00+08:00",
  "status": "active",
  "l0_ref": "l0_20260515_083900"
}
```

#### 3.2.2 fact_type 体系（多标签）

```python
FACT_TYPES = {
    "decision":    "技术决策或结论",
    "bug-fix":     "遇到 BUG 并解决了",
    "preference":  "Oliver 的偏好或习惯",
    "method":      "有效的解决方法或工作流",
    "todo":        "未完成的事项",
    "unresolved": "悬而未决的问题",
    "other":       "不归属以上类型"
}
```

**关键规则**：
- `types` 字段为数组，允许 1-3 个类型（解决单标签信息丢失问题）
- `type_confidence` 表示 LLM 提取置信度，低于 0.6 启用 `fallback_type`
- `status` 字段：`active` | `archived`（归档后不参与检索）

#### 3.2.3 提取 Prompt（含 Few-Shot）

```python
L1_EXTRACT_PROMPT = """你是一个记忆分析器。从以下会话摘要中提取原子事实。

会话摘要：
{SESSION_SUMMARY}

每条事实需要包含：
- types: 类型数组，允许 1-3 个，取自 [decision, bug-fix, preference, method, todo, unresolved]
- content: 事实内容，用中文写，一条完整的陈述句，不超过 80 词
- tags: 标签，2-5 个英文或中文标签，代表主题
- value: 长期价值，high | medium | low（只提取 medium 和 high）

只提取真正有价值的事实，不要流水账。不要编造信息。

输出 JSON 格式：
{{"facts": [{{"types": ["decision"], "content": "...", "tags": ["sqlite", "chinese-search"], "value": "high"}}]}}

示例：

输入摘要（示例）：
"讨论了 SQLite FTS5 中文分词问题。Oliver 尝试了 unicode61、trigram、porter 等 tokenizers，全部失败。最终决定用 Python 2-gram 滑动窗口提取关键词，绕过 SQLite 侧的分词限制。"

输出：
{{"facts": [{{"types": ["decision", "method"], "content": "Oliver 决定使用 Python 2-gram 滑动窗口解决 SQLite FTS5 中文分词问题，放弃 SQLite 侧 tokenizer 方案", "tags": ["sqlite", "chinese-search", "python"], "value": "high"}}]}}"""
```

#### 3.2.4 向量生成（批量）

```python
def embed_l1_batch(facts: list[dict]) -> list[list[float]]:
    """
    批量生成 embedding，减少 HTTP 开销。
    使用 Ollama bge-m3:latest。
    """
    texts = [f["content"] for f in facts]
    response = requests.post(
        f"{OLLAMA_URL}/v1/embeddings",
        json={
            "model": "bge-m3:latest",
            "input": texts  # 批量输入
        },
        timeout=60
    )
    return [item["embedding"] for item in response.json()["data"]]
```

#### 3.2.5 SQLite 表结构

```sql
CREATE TABLE l1_facts (
    id              TEXT PRIMARY KEY,     -- "fact_xxx"
    l0_ref          TEXT NOT NULL,       -- 指向原始会话
    types           TEXT NOT NULL,       -- JSON 数组: ["decision", "method"]
    type_confidence REAL DEFAULT 1.0,
    fallback_type   TEXT DEFAULT 'other',
    content         TEXT NOT NULL,
    tags            TEXT NOT NULL,       -- JSON 数组
    value           TEXT NOT NULL,       -- high | medium | low
    chunk_vector    BLOB NOT NULL,       -- float32 向量（Ollama bge-m3: 1024 dim）
    created_at      TEXT NOT NULL,       -- ISO 8601
    status          TEXT DEFAULT 'active' -- active | archived
);

CREATE INDEX idx_l1_status ON l1_facts(status);
CREATE INDEX idx_l1_l0 ON l1_facts(l0_ref);
```

---

### 3.3 L2-Scene：场景聚合

#### 3.3.1 数据结构

```json
{
  "id": "scene_001",
  "scene_type": "ongoing-project",
  "topic": "sqlite-chinese-search",
  "summary": "Oliver 用多个会话研究 SQLite 中文 FTS5 搜索。尝试了 unicode61、trigram、porter 等方案全部失败。最终采用 Python 2-gram 方案。偏好：倾向于 Python 侧解决而非数据库侧。",
  "scene_embedding": [0.111, -0.222, ...],
  "l1_refs": ["fact_001", "fact_007", "fact_012"],
  "occurrence_count": 3,
  "first_seen": "2026-05-10",
  "last_seen": "2026-05-15",
  "status": "active"  // active | dormant | resolved | abandoned
}
```

#### 3.3.2 scene_type 定义

| scene_type | 含义 |
|------------|------|
| `ongoing-project` | 持续进行的项目/研究 |
| `repeated-problem` | 反复出现的问题 |
| `emerging-interest` | 新出现的兴趣点 |
| `resolved` | 已解决的问题（可归档） |

#### 3.3.3 聚合逻辑

**触发时机**：
1. **即时**：每次 L1 提取后，检查是否有可聚合的现有 scene
2. **定期**：每天定时扫描所有 active scenes

**聚合算法（embedding 相似度）**：

```python
SIMILARITY_THRESHOLD_JOIN = 0.75   # 新 L1 归入现有 scene
SIMILARITY_THRESHOLD_MERGE = 0.85  # 两个 scene 合并

def try_aggregate_l2(new_l1_facts: list[dict], new_scene_emb: list[float]):
    """
    尝试将新的 L1 聚合到现有 scene 或新建 scene。
    """
    # Step 1: 查询所有 active scenes 的 embedding
    scenes = db.query("SELECT * FROM l2_scenes WHERE status = 'active'")

    # Step 2: 计算与每个 scene 的余弦相似度
    best_match = None
    best_sim = 0
    for scene in scenes:
        sim = cosine_sim(new_scene_emb, scene["scene_embedding"])
        if sim > SIMILARITY_THRESHOLD_JOIN and sim > best_sim:
            best_match = scene
            best_sim = sim

    if best_match:
        # 归入现有 scene
        add_l1_to_scene(best_match["id"], [f["id"] for f in new_l1_facts])
        update_scene_last_seen(best_match["id"])
    else:
        # 新建 scene（需要至少 2 条 L1，或 value=high 的单条）
        if len(new_l1_facts) >= 2 or any(f["value"] == "high" for f in new_l1_facts):
            create_new_scene(new_l1_facts, new_scene_emb)
```

**Scene summary 增量更新**：

```python
def regenerate_scene_summary(scene_id: str):
    """当 scene 新增 L1 时，重新生成 summary"""
    l1s = db.query("SELECT content FROM l1_facts WHERE id IN (?)", [scene["l1_refs"]])
    prompt = f"基于以下事实提炼一个场景总结（100词以内）：\n" + "\n".join([f"- {l['content']}" for l in l1s])
    new_summary = call_llm(prompt)  # 使用 Ollama 或 MiniMax
    db.execute("UPDATE l2_scenes SET summary = ? WHERE id = ?", [new_summary, scene_id])
```

#### 3.3.4 Scene 生命周期

```python
SCENE_DORMANT_DAYS = 60  # 60 天无新 L1 关联 → dormant

def check_scene_dormancy():
    """每日定时任务：将超期 active scene 标记为 dormant"""
    cutoff = (datetime.now() - timedelta(days=SCENE_DORMANT_DAYS)).isoformat()
    db.execute("""
        UPDATE l2_scenes
        SET status = 'dormant'
        WHERE status = 'active' AND last_seen < ?
    """, [cutoff])
```

#### 3.3.5 SQLite 表结构

```sql
CREATE TABLE l2_scenes (
    id               TEXT PRIMARY KEY,   -- "scene_xxx"
    scene_type       TEXT NOT NULL,      -- ongoing-project | repeated-problem | emerging-interest
    topic            TEXT NOT NULL,      -- 主要 topic（来自 tags 或 LLM 提取）
    summary          TEXT NOT NULL,      -- LLM 生成的场景总结
    scene_embedding  BLOB NOT NULL,      -- float32 向量
    l1_refs          TEXT NOT NULL,      -- JSON 数组: ["fact_001", "fact_007"]
    occurrence_count INTEGER DEFAULT 1,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    status           TEXT DEFAULT 'active'  -- active | dormant | resolved | abandoned
);

CREATE INDEX idx_l2_status ON l2_scenes(status);
CREATE INDEX idx_l2_last_seen ON l2_scenes(last_seen);
```

---

### 3.4 L3-Persona：人格提炼

#### 3.4.1 架构调整：Staging Area

```
L1 自动提取的 preference
        ↓
    进入 staging area（待确认列表）
        ↓
累计 5 条后，通过 cron 推送确认消息给 Oliver
        ↓
Oliver 确认后才写入 user_profile.md
```

**staging area 数据结构**：

```sql
CREATE TABLE l3_staging (
    id          TEXT PRIMARY KEY,
    fact_id     TEXT NOT NULL,          -- 来源 L1 fact
    content     TEXT NOT NULL,          -- preference 内容
    source      TEXT NOT NULL,          -- 来源 session
    created_at  TEXT NOT NULL,
    confirmed   INTEGER DEFAULT 0       -- 0=pending, 1=confirmed, -1=rejected
);
```

#### 3.4.2 用户画像更新流程

```python
def process_l3_staging():
    """每日定时任务：处理 staging area"""
    pending = db.query("SELECT * FROM l3_staging WHERE confirmed = 0 ORDER BY created_at")

    if len(pending) >= 5:
        # 生成确认消息推送给 Oliver
        msg = "以下是从最近会话中提取的偏好，请确认哪些想保留到个人画像：\n\n"
        for i, p in enumerate(pending[:5], 1):
            msg += f"{i}. {p['content']} (来源: {p['source']})\n"
        msg += "\n回复编号确认，回复「跳过」忽略本次"
        send_to_oliver(msg)  # 通过 weixin 或 feishu 推送

    # 处理 Oliver 的回复（通过消息路由）
    # confirmed → 写入 user_profile.md
    # rejected → 标记 confirmed = -1
```

#### 3.4.3 user_profile.md 格式

```markdown
# Oliver 个人画像

## 核心偏好（Confirmed）
- Oliver 偏好简洁，不喜欢冗长的解释
- Oliver 倾向于直接告诉 AI 错了，不需要铺垫安慰
- Oliver 使用微信沟通时，图片优于文字

## 工作流
- Oliver 的 vibe-coding 工作流：新项目先加载 vibe-coding-protocol skill
- Oliver 遇到 bug 时倾向于先确认复现路径

## 约束
- 禁止在 cron job 中调用 xhs login 或 --cookie-source
- 写含 API key 的配置文件必须用 terminal + python3，不能用 write_file

## 待确认偏好（Staging）
（此处为未确认的偏好，仅 Oliver 可见）
```

---

## 4. 检索流程

### 4.1 检索原则

**核心原则：永远不做硬过滤。只做后处理重排。**

### 4.2 检索流程详解

```
Oliver 问: "上次怎么解决 SQLite 中文搜索问题的"

Step 1: 纯语义搜索（L1 向量检索，top_k=20）
  ↓
Step 2: 关联到 L2 scene（按 scene_embedding 相似度）
  ↓
Step 3: 后处理重排（可选，按用户问题暗示的类型 boost）
  ↓
Step 4: 组装 L2.summary + L1.content 返回
  ↓
Step 5: Oliver 追问细节 → 按需加载 L0
```

### 4.3 实施代码

```python
def retrieve(query: str, preferred_types: list[str] = None, top_k: int = 5) -> dict:
    """
    检索入口。
    preferred_types: 用户问题暗示的类型，用于后处理 boost（不是过滤）
    """
    # Step 1: 纯语义搜索 top_k=20（不做任何类型过滤）
    query_emb = get_query_embedding(query)
    l1_results = vector_search_l1(query_emb, top_k=20)

    # Step 2: 关联到 L2 scenes
    l2_scenes = associate_scenes(l1_results)

    # Step 3: 后处理 boost（不是过滤！）
    if preferred_types:
        scored = []
        for r in l1_results:
            boost = 1.5 if any(t in r["types"] for t in preferred_types) else 1.0
            scored.append((r, r["_similarity"] * boost))
        scored.sort(key=lambda x: x[1], reverse=True)
        l1_results = [r for r, _ in scored[:top_k]]
    else:
        l1_results = l1_results[:top_k]

    # Step 4: 组装返回
    return {
        "scenes": l2_scenes,
        "facts": l1_results,
        "query": query,
        "has_l0_detail": any(r["l0_ref"] for r in l1_results)
    }


def vector_search_l1(query_emb: list[float], top_k: int) -> list[dict]:
    """SQLite + NumPy 实现余弦相似度搜索"""
    import numpy as np
    conn = sqlite3.connect(EMBEDDINGS_DB)
    rows = conn.execute(
        "SELECT id, l0_ref, types, content, tags, value, chunk_vector FROM l1_facts WHERE status = 'active'"
    ).fetchall()
    conn.close()

    q_vec = np.array(query_emb, dtype=np.float32)
    results = []
    for row in rows:
        emb = np.frombuffer(row[6], dtype=np.float32)
        sim = float(np.dot(q_vec, emb) / (np.linalg.norm(q_vec) * np.linalg.norm(emb) + 1e-8))
        results.append({
            "id": row[0], "l0_ref": row[1], "types": json.loads(row[2]),
            "content": row[3], "tags": json.loads(row[4]),
            "value": row[5], "_similarity": sim
        })

    results.sort(key=lambda x: x["_similarity"], reverse=True)
    return results[:top_k]


def associate_scenes(l1_results: list[dict]) -> list[dict]:
    """将 L1 结果关联到 L2 scenes（按 scene_embedding 相似度）"""
    if not l1_results:
        return []

    scene_ids = set()
    for r in l1_results:
        # 获取该 L1 的 tags，在 active scenes 中找相似
        tags = r["tags"]
        similar = db.query("""
            SELECT * FROM l2_scenes
            WHERE status IN ('active', 'dormant')
            AND scene_similarity(tags, ?) > 0.6
        """, [json.dumps(tags)])
        for s in similar:
            scene_ids.add(s["id"])

    return db.query("SELECT * FROM l2_scenes WHERE id IN (?)", [list(scene_ids)])
```

---

## 5. 实施优先级

| 优先级 | 任务 | 工作量 | 理由 |
|--------|------|--------|------|
| **P0** | 检索层去掉硬编码 fact_type，改为语义+后处理 boost | 0.5 人日 | 不改则检索召回率 <30% |
| **P0** | L1 fact_type 改为多标签数组 + 置信度 | 0.5 人日 | 单标签分类质量不可控 |
| **P0** | L2 聚合改用 embedding 相似度（替代 tag 匹配） | 1 人日 | tag 匹配几乎不会命中 |
| **P1** | 实现 scene 动态合并（定时任务） | 0.5 人日 | 防止重复 scene 累积 |
| **P1** | L1 fact_type 人工校验工具（`hermes review --type`） | 1 人日 | 质量保证机制 |
| **P1** | L3 staging area + 确认机制 | 1 人日 | 防止 preference 错误固化 |
| **P2** | L0 配额与自动清理 + `hermes archive` 导出 | 1 人日 | 长期运维必要 |
| **P2** | 模拟测试：用 5 个真实会话验证 L1 提取召回率 | 0.5 人日 | 实施前必须验证 |
| **P2** | 监控面板（scene 分布、检索命中率） | 1 人日 | 可后续迭代 |

**总计 P0: 2 人日 | P1: 2.5 人日 | P2: 2.5 人日**

---

## 6. 触发时机总览

| 操作 | 触发时机 | 执行内容 |
|------|----------|----------|
| L0 保存 | 会话结束（独立步骤） | 写入 JSON，支持 gzip 压缩 |
| L1 提取 | 会话结束（session-summary 后） | LLM 提取 + 批量 embedding |
| L2 聚合 | L1 提取后（即时）+ 每日定时 | embedding 相似度匹配/聚合 |
| L3 更新 | 每日定时 | staging area 检查 + 推送确认 |
| scene 清理 | 每日定时 | dormancy 检查 + 自动归档 |

---

## 7. 数据库结构汇总

```sql
-- L1 原子事实表
CREATE TABLE l1_facts (
    id, l0_ref, types, type_confidence, fallback_type,
    content, tags, value, chunk_vector, created_at, status
);

-- L2 场景聚合表
CREATE TABLE l2_scenes (
    id, scene_type, topic, summary, scene_embedding,
    l1_refs, occurrence_count, first_seen, last_seen, status
);

-- L3 staging area
CREATE TABLE l3_staging (
    id, fact_id, content, source, created_at, confirmed
);
```

**数据库文件**: `~/.hermes/memory/l0_l3.db`

---

## 8. 与 Phase 1/2 的关系

```
Phase 1: session-summary 生成 Markdown 摘要（文件系统）
Phase 2: 摘要自动向量索引 → 语义搜索召回
         ↓
Phase 3: 在 Phase 2 基础上建立 L0-L3 分层
         - L1 替换现有的扁平 embedding（保持向量搜索能力）
         - L2 新增场景聚合层
         - L3 复用并强化 user_profile.md
         - L0 新增原始会话存档（Phase 1/2 都没有）
```

---

## 9. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| L1 提取质量不稳定 | 中 | 高 | Few-shot prompt；人工抽检；持续迭代 |
| L2 错误聚合（scene 碎片化） | 高 | 中 | 模拟测试验证阈值；定期 merge 任务 |
| scene 数量失控 | 低 | 中 | active scenes 上限 200；超限自动归档低频 scene |
| L3 preference 误判导致 profile 膨胀 | 中 | 低 | staging 确认机制兜底 |
| Ollama 服务崩溃 | 低 | 中 | 降级到 MiniMax API |
| L0 存储膨胀 | 中 | 低 | 500MB 配额 + 自动清理 |

---

## 10. 完成标准

### 10.1 硬性验收指标（门禁条件）

**指标 1: L1 类型准确率 ≥ 80%**
- 验证方法：人工抽检 50 条 L1 facts
- 判定标准：`types` 数组中包含正确主类型（允许多标签重叠）
- 通过条件：50 条中至少 40 条主类型正确
- 触发时点：Step 2 模拟测试，未通过不得进入编码阶段

**指标 2: 模糊查询召回率 ≥ 85%**
- 验证方法：用 10 个真实历史问题测试（不指定类型，检查 top-5 结果）
- 判定标准：每条问题 top-5 中至少 1 条包含正确答案
- 通过条件：10 条问题中至少 9 条满足
- 触发时点：Step 2 模拟测试，未通过不得进入编码阶段

### 10.2 功能验收标准

1. **L0 存档可查**: 任何 L1 fact 都能追溯到对应的 L0 原始会话
2. **L2 聚合有效**: 相同 topic 的 3 次讨论能自动聚合为 1 个 scene
3. **L3 确认机制**: staging area 满 5 条时正确推送确认消息
4. **L0 追溯成功**: 每个 L1 可通过 `l0_ref` 加载原始会话细节

---

## 11. 参考资料

- TencentDB Agent Memory L0-L3 分层设计: https://github.com/Tencent/TencentDB-Agent-Memory
- Hermem Phase 1: `~/.hermes/projects/hermem/phase1/SPEC.md`
- Hermem Phase 2: `~/.hermes/projects/hermem/phase2/SPEC.md`
- sqlite-vec (向量索引参考): https://github.com/asg017/sqlite-vec
