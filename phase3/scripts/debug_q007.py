"""诊断 q007 召回失败原因 — 单独文件,避免 bash 转义问题。"""
import sqlite3
import numpy as np
from impl.embedding import get_embedding_cached

con = sqlite3.connect('/Users/oliver/.hermes/memory/hermem.db')
npy = np.load('/Users/oliver/.hermes/memory/hermem_vectors.npy')

qvec_list, _ = get_embedding_cached('连环画三视图生成最佳实践')
qvec = np.array(qvec_list)
print(f"qvec shape: {qvec.shape}")

def sim(cid):
    vec_idx = con.execute("SELECT vec_index FROM chunks WHERE id = ?", (cid,)).fetchone()[0]
    v = npy[vec_idx]
    return float(np.dot(qvec, v) / (np.linalg.norm(qvec) * np.linalg.norm(v)))

# relevant vs retrieved top-5
print("\n=== sim vs query '连环画三视图生成最佳实践' ===")
for cid, label in [(16, 'RELEVANT'), (17, 'retrieved top-1'),
                    (422, 'retrieved top-2'), (424, 'retrieved top-3'),
                    (421, 'retrieved top-4'), (423, 'retrieved top-5')]:
    s = sim(cid)
    r = con.execute("SELECT content FROM chunks WHERE id = ?", (cid,)).fetchone()
    excerpt = r[0][:50] if r else "N/A"
    print(f"  #{cid:4d} {label:18s}: sim={s:.4f}  | {excerpt}")

# 跟 search_with_tier 实际召回路径对比
print("\n=== search_with_tier 实际召回(查 RRF score) ===")
from impl.vector_search import search_with_tier
high, medium = search_with_tier(query='连环画三视图生成最佳实践', top_k=5)
for tier_name, items in [('high', high), ('medium', medium)]:
    for c in items:
        cid = c.get('id') or c.get('chunk_id')
        rrf = c.get('rrf_score', 0)
        # 看 vector 和 BM25 各自的 rank
        print(f"  [{tier_name}] #{cid} rrf={rrf:.4f}")
