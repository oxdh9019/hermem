#!/usr/bin/env python3
"""Eval qwen3.5:4b with enough tokens for thinking + response."""
import requests, json, time
from pathlib import Path

OLLAMA = "http://localhost:11434"
MODEL = "qwen3.5:4b"
# qwen3.5 with thinking mode needs ~400 tokens just for thinking
# Give 800 total to be safe
NUM_PREDICT = 800

PROMPT_TPL = """你是一个轻量级的对话记忆判断模型。对每条对话，判断是否需要记忆动作。

判断标准：
- new_fact_to_l1: 这条对话是否包含值得写入长期记忆的事实？
- needs_recall: 当前对话是否需要召回历史记忆来回复？
- recall_keywords: 如果需要召回，列出3-5个关键词

输出格式（仅输出JSON）：
{{
  "new_fact_to_l1": true或false,
  "needs_recall": true或false,
  "recall_keywords": ["关键词1"],
  "intent_hint": "意图类型"
}}

现在判断：
对话：{content}

"""

def parse_output(raw: str) -> dict | None:
    """Extract JSON from model output (may be wrapped in markdown)."""
    # Try direct parse
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    # Try markdown code block
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding JSON between braces
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    return None

EVAL_DIR = Path(__file__).resolve().parent.parent  # phase3/eval/ -> phase3/
TEST_SET_PATH = EVAL_DIR / "per_turn_judgment_testset.json"
RESULTS_PATH = EVAL_DIR / "qwen35_4b_results.json"

with open(TEST_SET_PATH) as f:
    turns = json.load(f)

results = []
for i, (date, role, content, etype) in enumerate(turns, 1):
    role_label = "用户" if role == "user" else "助手"
    prompt = PROMPT_TPL.format(content=f'{role_label}说"{content[:150]}"')

    t0 = time.time()
    resp = requests.post(
        f"{OLLAMA}/api/generate",
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": NUM_PREDICT,
            }
        },
        timeout=120
    )
    lat = time.time() - t0

    raw = resp.json().get("response", "")
    parsed = parse_output(raw)
    success = parsed is not None

    # Check for thinking mode artifacts in response
    has_thinking = "thinking" in raw.lower() or "thinking process" in raw.lower()

    results.append({
        "turn": i, "role": role, "etype": etype,
        "latency_ms": round(lat*1000, 1),
        "success": success,
        "parsed": parsed,
        "has_thinking": has_thinking,
        "raw_preview": raw[:100] if not success else None
    })

    status = "✅" if success else "❌"
    think = " [THINKING]" if has_thinking else ""
    print(f"[{i:2d}] {status} | {lat:.1f}s{think} | {content[:50]}...")

print(f"\n{'='*60}")
print(f"Model: {MODEL} (num_predict={NUM_PREDICT})")
print(f"Parse success: {sum(1 for r in results if r['success'])}/{len(results)}")
print(f"Avg latency: {sum(r['latency_ms'] for r in results)/len(results):.0f}ms")
print(f"With thinking: {sum(1 for r in results if r['has_thinking'])} turns")

print(f"\nPer-turn:")
for r in results:
    p = r["parsed"]
    if p:
        print(f"  [{r['turn']:2d}] {r['role'].upper()} | new_fact:{p.get('new_fact_to_l1')} | recall:{p.get('needs_recall')} | kw:{p.get('recall_keywords',[])[:2]} | intent:{p.get('intent_hint','')}")
    else:
        print(f"  [{r['turn']:2d}] PARSE FAIL | raw: {r['raw_preview'][:80]}")

output = {
    "model": MODEL,
    "num_predict": NUM_PREDICT,
    "total": len(results),
    "parse_success": sum(1 for r in results if r["success"]),
    "avg_latency_ms": round(sum(r["latency_ms"] for r in results)/len(results), 1),
    "thinking_mode_count": sum(1 for r in results if r["has_thinking"]),
    "turns": results
}
with open("/Users/oliver/.hermes/projects/hermem/eval/qwen35_4b_results.json", "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\nSaved to qwen35_4b_results.json")
