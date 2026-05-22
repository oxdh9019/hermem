#!/usr/bin/env python3
"""
Hermem Phase 3 - L2 场景聚合
Step 4a: try_aggregate_l2() — 基于 embedding 相似度聚合
Step 4b: check_dormancy() + merge_duplicate_scenes()
"""

import json
import uuid
from datetime import datetime, timedelta

from .config import DB_PATH, SCENE_DORMANT_DAYS, SIM_THRESHOLD_JOIN, SIM_THRESHOLD_MERGE
from .utils import (
    cosine_sim,
    deserialize_vec,
    get_embeddings_batch,
    serialize_vec,
)


def compute_scene_embedding(l1_contents: list[str]) -> list[float]:
    """将多个 L1 content 合并后生成一个 scene embedding"""
    if not l1_contents:
        return [0.0] * 1024
    combined = " ".join(l1_contents)[:1500]
    emb = get_embeddings_batch([combined])[0]
    return emb.tolist()


def try_aggregate_l2(new_l1_facts: list[dict]):
    """
    尝试将新的 L1 facts 聚合到现有 scene 或新建 scene。

    逻辑：
    1. 计算新 L1 群的综合 embedding
    2. 与所有 active scene 的 embedding 做余弦相似度
    3. > JOIN_THRESHOLD(0.75) → 归入该 scene
    4. 无匹配但 L1 数量>=2 或有 high → 新建 scene
    """
    if not new_l1_facts:
        return

    import sqlite3

    # 计算新 L1 群的综合 embedding
    texts = [f["content"] for f in new_l1_facts]
    combined = " ".join(texts)[:1500]
    embs = get_embeddings_batch([combined])
    new_emb = embs[0]
    new_emb_bytes = serialize_vec(new_emb.tolist())

    conn = sqlite3.connect(DB_PATH)
    scenes = conn.execute("SELECT * FROM l2_scenes WHERE status = 'active'").fetchall()

    best_match = None
    best_sim = 0.0

    for scene in scenes:
        scene_emb = deserialize_vec(scene[4])  # scene_embedding BLOB
        sim = cosine_sim(new_emb, scene_emb)
        if sim > SIM_THRESHOLD_JOIN and sim > best_sim:
            best_match = scene
            best_sim = sim

    now = datetime.now().isoformat()
    new_l1_ids = [f["id"] for f in new_l1_facts]

    if best_match:
        # 归入现有 scene
        existing_refs = json.loads(best_match[5])
        existing_refs.extend(new_l1_ids)
        occ = best_match[6] + 1

        # 重新计算场景嵌入：取所有 L1 事实嵌入的加权平均
        all_fact_vecs = []
        for ref_id in existing_refs:
            row = conn.execute(
                "SELECT chunk_vector FROM l1_facts WHERE id = ? AND status = 'active'",
                (ref_id,),
            ).fetchone()
            if row and row[0]:
                all_fact_vecs.append(deserialize_vec(row[0]))

        if all_fact_vecs:
            import numpy as np

            new_scene_emb = np.mean(all_fact_vecs, axis=0)
            conn.execute(
                """
                UPDATE l2_scenes
                SET l1_refs = ?, occurrence_count = ?, last_seen = ?, scene_embedding = ?
                WHERE id = ?
            """,
                (
                    json.dumps(existing_refs),
                    occ,
                    now,
                    serialize_vec(new_scene_emb.tolist()),
                    best_match[0],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE l2_scenes
                SET l1_refs = ?, occurrence_count = ?, last_seen = ?
                WHERE id = ?
            """,
                (json.dumps(existing_refs), occ, now, best_match[0]),
            )
        print(f"  [L2] joined scene {best_match[0]} (sim={best_sim:.3f})")
    else:
        # 新建 scene
        if len(new_l1_facts) >= 2 or any(f.get("value") == "high" for f in new_l1_facts):
            topic = new_l1_facts[0]["tags"][0] if new_l1_facts[0].get("tags") else "unknown"
            fid = f"scene_{uuid.uuid4().hex[:8]}"
            summary = _regenerate_scene_summary(new_l1_facts)
            conn.execute(
                """
                INSERT INTO l2_scenes
                (id, scene_type, topic, summary, scene_embedding,
                 l1_refs, occurrence_count, first_seen, last_seen, status)
                VALUES (?, 'ongoing-project', ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
                (
                    fid,
                    topic,
                    summary,
                    new_emb_bytes,
                    json.dumps(new_l1_ids),
                    1,
                    now,
                    now,
                ),
            )
            print(f"  [L2] created scene {fid} (topic={topic})")

    conn.commit()
    conn.close()


def _regenerate_scene_summary(l1_facts: list[dict]) -> str:
    """基于 L1 facts 重新生成 scene summary"""
    if not l1_facts:
        return ""
    lines = "\n".join(f"- {f['content']}" for f in l1_facts)
    prompt = f"""基于以下事实提炼一个场景总结（80词以内，中文）：

{lines}

输出格式：直接输出总结文字，不需要额外说明。"""
    from .utils import llm_generate

    try:
        return llm_generate(prompt, temperature=0.3, max_tokens=200).strip()
    except Exception:
        return f"相关事实 {len(l1_facts)} 条"


# ── 定时维护任务 ────────────────────────────────────────────
def check_scene_dormancy():
    """每日定时：将 60 天无新 L1 的 scene 标记为 dormant"""
    import sqlite3

    cutoff = (datetime.now() - timedelta(days=SCENE_DORMANT_DAYS)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT id FROM l2_scenes WHERE status='active' AND last_seen < ?",
        [cutoff],
    )
    ids = [r[0] for r in cur.fetchall()]
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE l2_scenes SET status='dormant' WHERE id IN ({placeholders})",
            ids,
        )
        print(f"  [L2 maintenance] {len(ids)} scenes → dormant")
    conn.commit()
    conn.close()


def merge_duplicate_scenes():
    """
    每日定时：相似度 > 0.85 的 scene 合并。
    合并后将 refs 合并、重新生成 summary、删除被合并的 scene。
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    scenes = conn.execute("SELECT * FROM l2_scenes WHERE status='active'").fetchall()

    merged_ids = set()
    merge_count = 0

    for i, s1 in enumerate(scenes):
        if s1["id"] in merged_ids:
            continue
        emb1 = deserialize_vec(s1["scene_embedding"])
        for s2 in scenes[i + 1 :]:
            if s2["id"] in merged_ids:
                continue
            emb2 = deserialize_vec(s2["scene_embedding"])
            sim = cosine_sim(emb1, emb2)
            if sim > SIM_THRESHOLD_MERGE:
                # 合并 s2 into s1
                refs1 = json.loads(s1["l1_refs"])
                refs2 = json.loads(s2["l1_refs"])
                merged_refs = list(set(refs1) | set(refs2))
                # 收集 L1 contents 重新生成 summary
                placeholders = ",".join("?" * len(merged_refs))
                rows = conn.execute(
                    f"SELECT content FROM l1_facts WHERE id IN ({placeholders})",
                    merged_refs,
                ).fetchall()
                new_summary = _regenerate_scene_summary([{"content": r[0]} for r in rows])
                conn.execute(
                    """
                    UPDATE l2_scenes
                    SET l1_refs=?, summary=?, occurrence_count=?, last_seen=?
                    WHERE id=?
                """,
                    (
                        json.dumps(merged_refs),
                        new_summary,
                        len(merged_refs),
                        datetime.now().isoformat(),
                        s1["id"],
                    ),
                )
                conn.execute("DELETE FROM l2_scenes WHERE id=?", [s2["id"]])
                merged_ids.add(s2["id"])
                merge_count += 1
                print(f"  [L2 merge] {s2['id']} → {s1['id']} (sim={sim:.3f})")

    conn.commit()
    conn.close()
    print(f"  [L2 merge] total {merge_count} scenes merged")
