# Hermem V4.5 修复建议

> **目标**: 闭合 error_count → behavior 闭环，使核心管线可在干净 clone 上端到端运行
> **原则**: 每个修复提供可直接使用的代码，标注文件和行号

---

## P0 — 闭合闭环（必须先做）

### Fix 1: Schema 完整化 — `db_init.py`

**问题**: `l1_dispositions` 表只有 17 列，代码引用了 21 列。干净 clone 后 4 条关键 SQL 全部崩溃。

**文件**: `phase3/impl/db_init.py` 第 70-88 行

**替换为**:

```python
_conn.execute("""
    CREATE TABLE IF NOT EXISTS l1_dispositions (
        id                   TEXT PRIMARY KEY,
        l0_ref               TEXT,
        condition_text       TEXT NOT NULL,
        prediction_text      TEXT NOT NULL,
        condition_embedding  BLOB,
        prediction_embedding  BLOB,
        error_type           TEXT,
        keywords             TEXT,
        source_session_id    TEXT,
        source_agent         TEXT,
        confidence           REAL DEFAULT 1.0,
        error_count          INTEGER DEFAULT 0,
        success_count        INTEGER DEFAULT 0,
        last_error_at        TEXT,
        created_at           TEXT NOT NULL,
        last_used_at         TEXT,
        usage_count          INTEGER DEFAULT 0,
        is_active            INTEGER DEFAULT 1,
        scope                TEXT DEFAULT 'model_error',
        weight               REAL DEFAULT 1.0,
        intent               TEXT
    )
""")
```

**变更**:
- 新增 `source_agent TEXT`（`openclaw_import.py` INSERT 引用）
- 新增 `scope TEXT DEFAULT 'model_error'`（`l1_search.py` / `disposition_updater.py` WHERE 引用，DEFAULT 确保旧数据也可见）
- 新增 `weight REAL DEFAULT 1.0`（`l1_search.py` SELECT / `disposition_updater.py` UPDATE 引用）
- 新增 `intent TEXT`（`l1_search.py` SELECT/WHERE 引用）

---

### Fix 1b: 现有 DB 迁移脚本

**新建文件**: `phase3/scripts/migrate_add_disposition_columns.py`

```python
#!/usr/bin/env python3
"""
一次性迁移：为旧版 l1_dispositions 添加 V4.3+ 所需的列。

安全：ALTER TABLE ADD COLUMN 对已存在的列会报错，用 PRAGMA table_info 检查。
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory" / "l0_l3.db"

COLUMNS_TO_ADD = [
    ("source_agent", "TEXT",              None),
    ("scope",        "TEXT",              "'model_error'"),
    ("weight",       "REAL",              "1.0"),
    ("intent",       "TEXT",              None),
    # error_type/keywords/source_session_id 三个 V4.2 列，防旧 DB 缺失
    ("error_type",           "TEXT",      None),
    ("keywords",             "TEXT",      None),
    ("source_session_id",    "TEXT",      None),
]

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(l1_dispositions)")
    existing = {r[1] for r in cursor.fetchall()}

    added = []
    for col_name, col_type, default_val in COLUMNS_TO_ADD:
        if col_name not in existing:
            default_clause = f" DEFAULT {default_val}" if default_val else ""
            sql = f"ALTER TABLE l1_dispositions ADD COLUMN {col_name} {col_type}{default_clause}"
            cursor.execute(sql)
            added.append(col_name)
            print(f"  + {col_name} ({col_type}{default_clause})")

    if added:
        conn.commit()
        print(f"\n✓ 已添加 {len(added)} 列: {', '.join(added)}")
    else:
        print("\n✓ 所有列已存在，无需迁移")

    conn.close()

if __name__ == "__main__":
    migrate()
```

---

### Fix 1c: 删除冗余 ALTER TABLE — `generate_dispositions_from_annotations.py`

**文件**: `phase3/scripts/generate_dispositions_from_annotations.py` 第 131-141 行

**删除整个 `ensure columns` 块**（Schema 已在 db_init.py 中定义，迁移脚本处理旧 DB）:

```python
# 删除以下代码（第 131-141 行）:
    # 确保 scope 和 keywords 列存在
    cursor.execute("PRAGMA table_info(l1_dispositions)")
    existing_cols = {r[1] for r in cursor.fetchall()}
    for col, coltype in [("scope", "TEXT"), ("keywords", "TEXT")]:
        if col not in existing_cols:
            default = "'model_error'" if col == "scope" else "NULL"
            cursor.execute(f"ALTER TABLE l1_dispositions ADD COLUMN {col} {coltype}")
            print(f"添加列: {col}")
```

---

### Fix 2: JSON fallback 结果被丢弃 — `process_turn_judgments.py`

**问题**: 第 103-106 行，regex fallback 成功解析的 `data` 被无条件 `return []` 覆盖。

**文件**: `phase3/scripts/process_turn_judgments.py` 第 100-106 行

**当前代码**:
```python
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]+\]', text)
        if match:
            try:
                data = json.loads(match.group())
            except Exception:
                return []
        return []          # ← BUG: 无论 fallback 是否成功都执行
```

**修复为**:
```python
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]+\]', text)
        if match:
            try:
                data = json.loads(match.group())
            except Exception:
                return []
        else:
            return []      # ← 仅在 regex 也没匹配时返回空
```

---

### Fix 3: v4_2_migrate 补全列 + 日期格式 — `v4_2_migrate.py`

**问题 A**: INSERT 缺少 `scope`/`is_active`/`error_type`/`source_agent`，迁移数据对 V4.3+ 查询不可见。

**问题 B**: 日期格式 `%Y%m%H%M%S` 缺少 `%d`（日）。

**文件**: `phase3/v4_2_migrate.py` 第 82-99 行

**当前代码**:
```python
        disp_id = f"disp_{datetime.now().strftime('%Y%m%H%M%S')}_{hash(session_id) % 100000:05d}_{saved}"
        conn.execute("""
            INSERT INTO l1_dispositions
            (id, l0_ref, condition_text, prediction_text,
             condition_embedding, prediction_embedding,
             confidence, source_session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            disp_id,
            session_id,
            ...
        ))
```

**修复为**:
```python
        disp_id = f"disp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(session_id) % 100000:05d}_{saved}"
        conn.execute("""
            INSERT INTO l1_dispositions
            (id, l0_ref, condition_text, prediction_text,
             condition_embedding, prediction_embedding,
             confidence, source_session_id, source_agent,
             error_type, scope, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            disp_id,
            f"l0_{session_id}",          # ← 统一 l0_ref 格式
            d["condition"],
            d["prediction"],
            serialize_vec(cond_emb.tolist()),
            serialize_vec(pred_emb.tolist()),
            d.get("confidence", 1.0),
            session_id,
            "v4_2_migrate",               # ← source_agent
            "other",                       # ← error_type 默认
            "user_behavior",               # ← scope (迁移的是用户行为)
            1,                             # ← is_active
            now,
        ))
```

**变更说明**:
- `%Y%m%H%M%S` → `%Y%m%d%H%M%S`（补 `%d`）
- `l0_ref` 统一为 `l0_{session_id}` 格式（与 `l1_extract.py` 一致）
- 新增 `source_agent='v4_2_migrate'`、`error_type='other'`、`scope='user_behavior'`、`is_active=1`

---

### Fix 4: weight 自动计算触发 — `cron_daily.py`

**问题**: `update_disposition_weights()` 存在但无自动调用，`weight` 列永远为 NULL。

**文件**: `phase3/cron_daily.py` 在 L1 提取流程之后（约第 115 行后）添加:

```python
    # V4.5: 重新计算所有 disposition 权重
    from impl.disposition_updater import update_disposition_weights
    weight_result = update_disposition_weights()
    if weight_result.get("updated", 0) > 0:
        print(f"  [B6] 更新了 {weight_result['updated']} 条 disposition 权重")
```

**位置**: 在 `store_l1_batch` 调用之后、L2 聚合之前。

---

## P1 — 提升闭环质量

### Fix 5: ALTER TABLE 添加 DEFAULT — `generate_dispositions_from_annotations.py`

**问题**: 第 138 行计算了 `default` 但未使用，已有行 `scope=NULL`。

**文件**: `phase3/scripts/generate_dispositions_from_annotations.py` 第 138-139 行

**如果保留 ALTER TABLE**（不删），修复为:
```python
    for col, coltype, default in [("scope", "TEXT", "'model_error'"), ("keywords", "TEXT", "NULL")]:
        if col not in existing_cols:
            cursor.execute(
                f"ALTER TABLE l1_dispositions ADD COLUMN {col} {coltype} DEFAULT {default}"
            )
            print(f"添加列: {col} DEFAULT {default}")
```

> 注意：如果已执行 Fix 1c（删除此 ALTER TABLE 块），则此修复不再需要。

---

### Fix 6: 启用精确 success 匹配 — `async_annotation.py`

**问题**: worker 使用粗粒度 `increment_success_count(session_id)`，可能误增无关 disposition。精确版本 `increment_success_by_ids` 已存在但从未被调用。

**文件**: `phase3/impl/async_annotation.py` 第 81-84 行

**当前代码**:
```python
            else:
                # V4.3 B1: 无 prediction_errors → 累加 success_count
                from .disposition_updater import increment_success_count
                incremented = increment_success_count(session_id)
```

**修复为**:
```python
            else:
                # V4.5: 无 prediction_errors → 累加 success_count
                # 优先用精确 ID 匹配，fallback 到 session 匹配
                from .disposition_updater import increment_success_count, increment_success_by_ids
                active_disp_ids = annotation.get("active_disposition_ids", [])
                if active_disp_ids:
                    incremented = increment_success_by_ids(active_disp_ids, session_id)
                else:
                    incremented = increment_success_count(session_id)
```

**配套修改**: `l0_store.py` 的 `annotate_l0_after_l1_v2` 需要在返回的 annotation 中包含当前激活的 disposition ID 列表:

```python
# 在 l0_store.py annotate_l0_after_l1_v2 的返回值中添加:
annotation["active_disposition_ids"] = []  # TODO: 从检索结果中获取
```

> **过渡方案**: 如果获取 active_disposition_ids 较复杂，可先保持 `increment_success_count` 不变，作为 P2 优化项。

---

### Fix 7: l0_ref 格式统一 — `openclaw_import.py`

**问题**: `openclaw_import.py` 存储裸 `session_id` 作为 `l0_ref`，与 `l1_extract.py` 的 `l0_{session_id}` 格式不一致，导致 `disposition_aware_rerank` Path 1 永远不匹配。

**文件**: `phase3/openclaw_import.py` 第 300 行

**当前代码**:
```python
            session_id,      # l0_ref
```

**修复为**:
```python
            f"l0_{session_id}",  # l0_ref — 统一格式
```

---

### Fix 8: Boost 日志线程池化 — `l1_search.py`

**问题**: 每次 boost 事件创建新 daemon 线程，高频查询下线程数不可控。

**文件**: `phase3/impl/l1_search.py` 第 154-195 行

**替换 `_write_boost_log` 和 `_async_append` 为**:

```python
# 模块级别（约第 15 行）
_boost_queue: queue.Queue = queue.Queue(maxsize=1000)
_boost_thread: threading.Thread | None = None

def _ensure_boost_writer():
    """启动单个后台写线程"""
    global _boost_thread
    if _boost_thread is None or not _boost_thread.is_alive():
        _boost_thread = threading.Thread(target=_boost_writer_loop, daemon=True)
        _boost_thread.start()

def _boost_writer_loop():
    """单线程消费 boost 日志队列"""
    while True:
        try:
            path, line = _boost_queue.get(timeout=5.0)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except queue.Empty:
            continue
        except Exception:
            pass

def _write_boost_log(query, dispositions, boost_entries):
    """异步写入 boost 日志（队列模式，不阻塞返回）"""
    try:
        path = _get_boost_log_path()
        entry = {
            "timestamp": datetime.now().isoformat(),
            "query": query[:80],
            "disposition_count": len(dispositions),
            "boosted_facts": boost_entries,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        _ensure_boost_writer()
        _boost_queue.put_nowait((path, line))  # 满则丢弃，不阻塞
    except Exception:
        pass  # 日志失败不影响主流程
```

**变更**:
- 单线程消费，最多 1 个 daemon 线程
- `maxsize=1000` 防止 OOM
- `put_nowait` 满则丢弃，不阻塞检索

---

### Fix 9: daily_synthesis SQL 补全过滤 — `daily_synthesis.py`

**问题**: 函数名和文档声称 `error_count >= 2`，但 SQL 未过滤，Python 侧再过滤导致过度取数。

**文件**: `phase3/scripts/daily_synthesis.py` 第 80-87 行

**当前 SQL**:
```sql
WHERE last_error_at >= ?
  AND is_active = 1
  AND scope = 'model_error'
```

**修复为**:
```sql
WHERE last_error_at >= ?
  AND is_active = 1
  AND scope = 'model_error'
  AND error_count >= 2
```

同时删除 Python 侧的冗余过滤（第 145 行 `high_error = [d for d in dispositions if d.get("error_count", 0) >= 2]`），改用 `dispositions` 直接传入下游。

---

### Fix 10: 去重缓存扩大 — `process_turn_judgments.py`

**问题**: `FactCache(max_size=5)` 过小，几乎无去重效果。

**文件**: `phase3/scripts/process_turn_judgments.py` 第 62 行和第 172 行

```python
# 当前
FactCache(max_size=5)

# 修复
FactCache(max_size=500)
```

---

## P2 — 债务清理

### Fix 11: SQL 参数化 — `backfill_vectors.py`

**文件**: `phase3/scripts/backfill_vectors.py` 第 86-91 行

**当前代码**:
```python
cases = " ".join(f"WHEN id = {cid} THEN {idx}" for cid, idx in zip(ids, new_indices))
sql = f"""
    UPDATE chunks
    SET vec_index = CASE {cases} END
    WHERE id IN ({','.join('?'*len(ids))})
"""
c.execute(sql, ids)
```

**修复为**:
```python
# 使用临时表替代 f-string CASE WHEN
c.execute("CREATE TEMP TABLE IF NOT EXISTS _idx_map (chunk_id INTEGER, new_idx INTEGER)")
c.execute("DELETE FROM _idx_map")
c.executemany("INSERT INTO _idx_map VALUES (?, ?)", zip(ids, new_indices))
c.execute("""
    UPDATE chunks
    SET vec_index = (SELECT new_idx FROM _idx_map WHERE chunk_id = chunks.id)
    WHERE id IN (SELECT chunk_id FROM _idx_map)
""")
c.execute("DELETE FROM _idx_map")
```

同样修复第 269 行:
```python
# 当前
c.execute(f"SELECT COUNT(*) FROM chunks WHERE vec_index >= {next_idx + total}")
# 修复
c.execute("SELECT COUNT(*) FROM chunks WHERE vec_index >= ?", (next_idx + total,))
```

---

### Fix 12: 锁机制修复 — `backfill_vectors.py`

**文件**: `phase3/scripts/backfill_vectors.py` 第 38-49 行

**当前代码**（完全失效）:
```python
def acquire_lock(path: Path, timeout: float = 5.0) -> threading.Lock:
    lock = threading.Lock()
    start = time.time()
    while (time.time() - start) < timeout:
        try:
            open(path, "x").close()
            path.unlink()      # ← 立即删除，锁从未持有
            return lock        # ← 返回未加锁的 threading.Lock
        except FileExistsError:
            time.sleep(0.05)
    raise RuntimeError(f"Could not acquire lock {path}")
```

**修复为**:
```python
import fcntl

class FileLock:
    """进程间文件锁（fcntl.flock），支持 with 语句。"""
    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "w")
        deadline = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (IOError, OSError):
                if time.time() > deadline:
                    raise RuntimeError(f"Could not acquire lock {self.path}")
                time.sleep(0.05)

    def __exit__(self, *args):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
```

**使用方式**:
```python
# 在 main() 中
with FileLock(HERMEM_DIR / ".backfill.lock"):
    # ... 执行 backfill 操作
```

---

### Fix 13: 原子写入 — `vectorstore.py`

**文件**: `phase3/impl/vectorstore.py` 第 163-165 行

**当前代码**:
```python
tmp_file = HERMEM_DIR / ".vector_write_tmp.npy"
np.save(str(tmp_file), combined)
shutil.copy2(str(tmp_file), str(VEC_PATH))   # ← 非原子
tmp_file.unlink(missing_ok=True)
```

**修复为**:
```python
tmp_file = HERMEM_DIR / ".vector_write_tmp.npy"
np.save(str(tmp_file), combined)
# os.rename 在同一文件系统上是原子操作
os.replace(str(tmp_file), str(VEC_PATH))
```

**注意**: `os.replace` 在同文件系统上等价于 `os.rename`（原子），跨文件系统则执行 copy+delete。两者都比 `shutil.copy2` 更安全。

---

### Fix 14: 场景嵌入更新 — `l2_aggregate.py`

**问题**: L1 事实加入场景后 `scene_embedding` 不更新，导致后续相似度匹配逐渐偏移。

**文件**: `phase3/impl/l2_aggregate.py` 第 65-74 行

**当前代码**:
```python
    if best_match:
        existing_refs = json.loads(best_match[5])
        existing_refs.extend(new_l1_ids)
        occ = best_match[6] + 1
        conn.execute("""
            UPDATE l2_scenes
            SET l1_refs = ?, occurrence_count = ?, last_seen = ?
            WHERE id = ?
        """, (json.dumps(existing_refs), occ, now, best_match[0]))
```

**修复为**:
```python
    if best_match:
        existing_refs = json.loads(best_match[5])
        existing_refs.extend(new_l1_ids)
        occ = best_match[6] + 1

        # 重新计算场景嵌入：取所有 L1 事实嵌入的加权平均
        from .utils import deserialize_vec, serialize_vec
        all_fact_vecs = []
        for ref_id in existing_refs:
            row = conn.execute(
                "SELECT chunk_vector FROM l1_facts WHERE id = ? AND status = 'active'",
                (ref_id,)
            ).fetchone()
            if row and row[0]:
                all_fact_vecs.append(deserialize_vec(row[0]))

        if all_fact_vecs:
            import numpy as np
            new_scene_emb = np.mean(all_fact_vecs, axis=0)
            conn.execute("""
                UPDATE l2_scenes
                SET l1_refs = ?, occurrence_count = ?, last_seen = ?, scene_embedding = ?
                WHERE id = ?
            """, (json.dumps(existing_refs), occ, now,
                  serialize_vec(new_scene_emb.tolist()), best_match[0]))
        else:
            conn.execute("""
                UPDATE l2_scenes
                SET l1_refs = ?, occurrence_count = ?, last_seen = ?
                WHERE id = ?
            """, (json.dumps(existing_refs), occ, now, best_match[0]))
```

---

### Fix 15: journal `--date` 修复 — `journal.py`

**问题**: 模块级 `START_JD`/`END_JD` 使用默认日期，`--date` 参数不影响 SQL 查询。

**文件**: `phase3/scripts/journal.py`

**修改 `fetch_session_summaries` 接受参数**:

```python
# 将第 60 行的函数签名改为:
def fetch_session_summaries(start_jd=None, end_jd=None):
    """获取指定时间范围内的会话摘要"""
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT session_id, content
        FROM chunks
        WHERE chunk_type = 'session_summary'
          AND created_at >= ?
          AND created_at < ?
        ORDER BY created_at ASC
    """, (start_jd or START_JD, end_jd or END_JD)).fetchall()
    conn.close()
    return [(r["session_id"], r["content"]) for r in rows]
```

**修改 `main()` 中调用**:

```python
def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--date":
        date_str = sys.argv[2]
        start_ts = datetime.strptime(date_str, "%Y-%m-%d")
        end_ts   = start_ts + timedelta(days=1)
        print(f"[journal] Backfill mode: {date_str}")
    else:
        date_str = (today_cst - timedelta(days=1)).strftime("%Y-%m-%d")
        start_ts = datetime.strptime(date_str, "%Y-%m-%d")
        end_ts   = start_ts + timedelta(days=1)

    # 使用命令行日期计算 JD
    start_jd = to_jd(start_ts)
    end_jd   = to_jd(end_ts)

    summaries = fetch_session_summaries(start_jd, end_jd)  # ← 传入参数
```

---

## 修复优先级总览

| 优先级 | Fix # | 描述 | 预计时间 | 阻塞闭环？ |
|--------|-------|------|---------|-----------|
| **P0** | 1 | Schema 完整化 + 迁移脚本 + 删冗余 ALTER | 30min | ✅ |
| **P0** | 2 | JSON fallback 结果丢弃 | 5min | ✅ |
| **P0** | 3 | v4_2_migrate 补列 + 日期格式 | 15min | ✅ |
| **P0** | 4 | weight 自动计算触发 | 10min | ✅ |
| P1 | 5 | ALTER TABLE DEFAULT | 5min | ❌ |
| P1 | 6 | 精确 success 匹配 | 30min | ❌ |
| P1 | 7 | l0_ref 格式统一 | 5min | ❌ |
| P1 | 8 | Boost 日志线程池化 | 20min | ❌ |
| P1 | 9 | daily_synthesis SQL 过滤 | 5min | ❌ |
| P1 | 10 | 去重缓存扩大 | 2min | ❌ |
| P2 | 11 | SQL 参数化 | 15min | ❌ |
| P2 | 12 | 锁机制修复 | 20min | ❌ |
| P2 | 13 | 原子写入 | 5min | ❌ |
| P2 | 14 | 场景嵌入更新 | 15min | ❌ |
| P2 | 15 | journal --date 修复 | 10min | ❌ |

**P0 合计约 1 小时**，完成后闭环可端到端运行。
**P1 合计约 1 小时**，完成后闭环质量显著提升。
**P2 合计约 1 小时**，完成后技术债大幅减少。

**建议执行顺序**: Fix 1 → 1b → 1c → 2 → 3 → 4，验证闭环可运行后再做 P1/P2。
