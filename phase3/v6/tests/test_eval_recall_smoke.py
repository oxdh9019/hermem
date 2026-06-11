import sys
sys.path.insert(0, '/Users/oliver/.hermes/projects/hermem/phase3')
from scripts.eval_recall import load_ground_truth, normalize_query

GT_PATH = '/Users/oliver/.hermes/projects/hermem/phase3/eval/ground_truth.jsonl'

def test_load_ground_truth():
    queries = load_ground_truth(GT_PATH)
    assert len(queries) >= 1
    assert 'query' in queries[0]
    assert 'relevant_chunk_ids' in queries[0]

def test_normalize_query_strips_questions():
    assert normalize_query('ds2api 工具怎么用？') == 'ds2api 工具'
    assert normalize_query('连环画三视图生成最佳实践是什么？') == '连环画三视图生成最佳实践'
    assert normalize_query('Hermem V5 核心方案是什么？') == 'Hermem V5 核心方案'
    # 不该破坏正常 query
    assert normalize_query('ds2api') == 'ds2api'
