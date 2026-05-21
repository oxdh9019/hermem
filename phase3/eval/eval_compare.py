#!/usr/bin/env python3
"""Per-turn Judgment Model Comparison
Models: qwen2.5:3b (baseline) vs qwen3.5:4b-no-think (proxy for 2b)

Key fix: qwen3.5 requires "think": False at payload top-level (not in options).
"""
import requests, json, time

OLLAMA = "http://localhost:11434"

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

def parse_json(raw: str) -> dict | None:
    import re
    # Try direct
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    # Try code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try find between braces
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    return None

def call_ollama(model: str, prompt: str, timeout: int = 30) -> tuple[str, float]:
    """Returns (response_text, latency_seconds)"""
    t0 = time.time()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 256}
    }
    # qwen3.5 requires think: False at top level
    if "qwen3.5" in model:
        payload["think"] = False

    resp = requests.post(f"{OLLAMA}/api/generate", json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text}")
    lat = time.time() - t0
    return resp.json().get("response", "").strip(), lat

MODELS = ["qwen2.5:3b", "qwen3.5:2b", "qwen3.5:4b"]

with open("/Users/oliver/.hermes/projects/hermem/eval/per_turn_judgment_testset.json") as f:
    turns = json.load(f)

print(f"Test set: {len(turns)} turns\n")

all_results = {}
for model in MODELS:
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"{'='*60}")

    model_results = []
    for i, (date, role, content, etype) in enumerate(turns, 1):
        role_label = "用户" if role == "user" else "助手"
        prompt = PROMPT_TPL.format(content=f'{role_label}说"{content[:150]}"')

        try:
            raw, lat = call_ollama(model, prompt)
            parsed = parse_json(raw)
            success = parsed is not None
        except Exception as e:
            raw, lat = "", 0
            parsed, success = None, False

        r = {
            "turn": i, "role": role, "etype": etype,
            "latency_ms": round(lat * 1000, 1),
            "success": success,
            "parsed": parsed,
        }
        model_results.append(r)

        status = "✅" if success else "❌"
        print(f"[{i:2d}] {status} | {lat:.2f}s | {content[:40]}...")
        if parsed:
            print(f"       → new_fact:{parsed.get('new_fact_to_l1')} recall:{parsed.get('needs_recall')} intent:{parsed.get('intent_hint','')}")

    successes = [r for r in model_results if r["success"]]
    avg_lat = sum(r["latency_ms"] for r in model_results) / len(model_results)

    print(f"\nSUMMARY: {len(successes)}/{len(model_results)} parsed | avg {avg_lat:.0f}ms\n")
    all_results[model] = {
        "total": len(model_results),
        "parse_success": len(successes),
        "avg_latency_ms": round(avg_lat, 1),
        "turns": model_results
    }

# Save comparison
output_path = "/Users/oliver/.hermes/projects/hermem/eval/model_comparison.json"
with open(output_path, "w") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print("FINAL COMPARISON")
print(f"{'='*60}")
print(f"{'Model':<20} {'Parse':>8} {'Avg Latency':>12}")
print("-" * 45)
for model, res in all_results.items():
    print(f"{model:<20} {res['parse_success']}/{res['total']:>4}      {res['avg_latency_ms']:>8.0f}ms")
print(f"\nResults saved to {output_path}")
