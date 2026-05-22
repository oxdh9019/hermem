#!/usr/bin/env python3
"""
Per-turn Judgment Model Comparison: qwen2.5:3b vs qwen3.5:2b

Evaluates whether qwen3.5:2b (newer, smaller, faster) can replace qwen2.5:3b
for the per-turn judgment task in Hermem V4.4.

Task: For each conversation turn, output:
  new_fact_to_l1: bool
  needs_recall: bool
  recall_keywords: list[str]
  intent_hint: str  # optional, from V4.4 Phase1 spec

Usage:
  python3 eval/per_turn_judgment_eval.py [--models qwen2.5:3b,qwen3.5:2b]
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TEST_SET_PATH = (
    Path.home() / ".hermes" / "projects" / "hermem" / "eval" / "per_turn_judgment_testset.json"
)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

FEW_SHOT_PROMPT = """你是一个轻量级的对话记忆判断模型。对每条对话，判断是否需要记忆动作。

判断标准：
- new_fact_to_l1: 这条对话是否包含值得写入长期记忆的事实？（用户偏好、项目状态、决策结论等）
- needs_recall: 当前对话是否需要召回历史记忆来回复？
- recall_keywords: 如果需要召回，列出3-5个关键词/短语（用于语义检索）

输出格式（仅输出JSON，不要其他内容）：
{
  "new_fact_to_l1": true或false,
  "needs_recall": true或false,
  "recall_keywords": ["关键词1", "关键词2"],
  "intent_hint": "这条对话的意图类型"
}

示例：

对话：用户说"我明天下午3点有个会议"
{
  "new_fact_to_l1": true,
  "needs_recall": false,
  "recall_keywords": [],
  "intent_hint": "日程安排"
}

对话：用户说"帮我查一下hermes项目的结构"
{
  "new_fact_to_l1": false,
  "needs_recall": true,
  "recall_keywords": ["hermes项目结构", "文件列表", "代码组织"],
  "intent_hint": "信息查询"
}

对话：用户说"谢谢"
{
  "new_fact_to_l1": false,
  "needs_recall": false,
  "recall_keywords": [],
  "intent_hint": "礼貌性回复"
}

现在判断：

"""


def call_ollama(model: str, prompt: str, timeout: int = 30) -> tuple[str, float]:
    """Call Ollama API, returns (response_text, latency_seconds)"""
    import requests

    start = time.time()

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 256,
        },
    }

    resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=timeout)
    latency = time.time() - start

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama call failed: {resp.status_code} {resp.text}")

    data = resp.json()
    text = data.get("response", "").strip()

    return text, latency


def parse_judgment_output(raw: str) -> dict | None:
    """Parse JSON from model output"""
    # Try direct JSON parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding JSON by braces
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass

    return None


def evaluate_model(model: str, test_turns: list) -> dict:
    """Run evaluation for a single model on all test turns"""
    results = []

    for i, (_date, role, content, expected_type) in enumerate(test_turns, 1):
        full_prompt = (
            FEW_SHOT_PROMPT + f'\n对话：{"用户" if role == "user" else "助手"}说"{content[:150]}"\n'
        )

        try:
            raw, latency = call_ollama(model, full_prompt)
            parsed = parse_judgment_output(raw)

            results.append(
                {
                    "turn_id": i,
                    "role": role,
                    "content_preview": content[:80],
                    "expected_type": expected_type,
                    "raw_output": raw[:200],
                    "parsed": parsed,
                    "parse_success": parsed is not None,
                    "latency_ms": round(latency * 1000, 1),
                    "error": None,
                }
            )
        except Exception as e:
            results.append(
                {
                    "turn_id": i,
                    "role": role,
                    "content_preview": content[:80],
                    "expected_type": expected_type,
                    "raw_output": "",
                    "parsed": None,
                    "parse_success": False,
                    "latency_ms": -1,
                    "error": str(e),
                }
            )

    return {
        "model": model,
        "total": len(results),
        "parse_success_count": sum(1 for r in results if r["parse_success"]),
        "avg_latency_ms": round(
            sum(r["latency_ms"] for r in results if r["latency_ms"] > 0)
            / max(1, sum(1 for r in results if r["latency_ms"] > 0)),
            1,
        ),
        "turns": results,
    }


def main():
    models = ["qwen2.5:3b", "qwen3.5:2b"]

    # Check if models are available
    resp = requests.get(f"{OLLAMA_HOST}/api/tags")
    available = resp.json().get("models", [])
    available_names = [m["name"] for m in available]

    print("Available models:", available_names)

    # Filter to only models that are available
    models_to_test = [m for m in models if m in available_names]
    missing = [m for m in models if m not in available_names]
    if missing:
        print(f"⚠️  Skipping (not yet downloaded): {missing}")

    if not models_to_test:
        print("No target models available yet.")
        sys.exit(1)

    # Load test set
    with open(TEST_SET_PATH) as f:
        test_turns = json.load(f)

    print(f"\nEvaluating {len(test_turns)} turns across {len(models_to_test)} models\n")

    all_results = {}
    for model in models_to_test:
        print(f"Testing {model}...")
        all_results[model] = evaluate_model(model, test_turns)

    # Print comparison table
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    for model, res in all_results.items():
        success_rate = res["parse_success_count"] / res["total"] * 100
        print(f"\n{model}")
        print(
            f"  Parse success rate: {res['parse_success_count']}/{res['total']} ({success_rate:.0f}%)"
        )
        print(f"  Avg latency: {res['avg_latency_ms']}ms")

    print("\n" + "=" * 70)
    print("PER-TURN DETAIL")
    print("=" * 70)

    for i in range(len(test_turns)):
        print(f"\n--- Turn {i + 1} ---")
        print(f"Role: {test_turns[i][1].upper()} | Expected: {test_turns[i][3]}")
        print(f"Content: {test_turns[i][2][:80]}...")

        for model, res in all_results.items():
            r = res["turns"][i]
            status = "✅" if r["parse_success"] else "❌"
            lat = r["latency_ms"] if r["latency_ms"] > 0 else "ERR"
            print(f"  {model}: {status} | latency: {lat}ms")
            if r["parsed"]:
                print(f"    new_fact_to_l1: {r['parsed'].get('new_fact_to_l1')}")
                print(f"    needs_recall: {r['parsed'].get('needs_recall')}")
                print(f"    recall_keywords: {r['parsed'].get('recall_keywords', [])[:3]}")
                print(f"    intent_hint: {r['parsed'].get('intent_hint', 'N/A')}")
            elif r["error"]:
                print(f"    Error: {r['error'][:80]}")
            else:
                print(f"    Raw: {r['raw_output'][:100]}")

    # Save results
    output_path = TEST_SET_PATH.replace(".json", "_results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
