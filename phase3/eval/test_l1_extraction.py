#!/usr/bin/env python3
"""验证 qwen3.5:2b 在真实 session 上的 L1 extraction 效果。"""

import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.hermes/projects/hermem-github/phase3"))

from impl.config import L1_EXTRACT_PROMPT
from impl.utils import llm_generate

SESSION_PATH = os.path.expanduser("~/.hermes/sessions/session_20260521_114059_ded70a.json")

with open(SESSION_PATH) as f:
    session = json.load(f)

messages = session.get("messages", [])

# Build a session summary (simulate what Hermes does)
# Use last N messages to make a concise summary
user_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") == "user"]
assistant_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") == "assistant"]

print(f"Session: {SESSION_PATH.split('/')[-1]}")
print(f"Total messages: {len(messages)}, User: {len(user_msgs)}, Assistant: {len(assistant_msgs)}")
print()

# Build a compact session summary (user/assistant pairs)
summary_parts = []
for idx, m in user_msgs:
    user_content = m.get("content", "")[:300].replace("\n", " ")
    # Find next assistant message
    next_assistant = next(
        (
            messages[i].get("content", "")[:200]
            for i in range(idx + 1, len(messages))
            if messages[i].get("role") == "assistant"
        ),
        "(no response)",
    )
    next_assistant = next_assistant.replace("\n", " ")[:200]
    summary_parts.append(f"User: {user_content}\nAssistant: {next_assistant}")

session_summary = "\n---\n".join(summary_parts)

# Call L1 extraction with qwen3.5:2b
print("Running L1 extraction with qwen3.5:2b...")
prompt = L1_EXTRACT_PROMPT.format(SESSION_SUMMARY=session_summary)

import time

t0 = time.time()
result = llm_generate(prompt, model="qwen3.5:2b", temperature=0.2, max_tokens=1024)
latency = time.time() - t0

print(f"Latency: {latency:.1f}s")
print()
print("Raw output:")
print(result[:500])
print()

# Parse JSON
import re

try:
    # Try code block first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result, re.DOTALL)
    if m:
        parsed = json.loads(m.group(1))
    else:
        start, end = result.find("{"), result.rfind("}") + 1
        parsed = json.loads(result[start:end])

    facts = parsed.get("facts", [])
    print(f"Extracted {len(facts)} facts:")
    for i, f in enumerate(facts, 1):
        types = f.get("types", [])
        content = f.get("content", "")[:100]
        value = f.get("value", "?")
        print(f"  [{i}] [{','.join(types)}] ({value}) {content}")
except Exception as e:
    print(f"Parse error: {e}")
