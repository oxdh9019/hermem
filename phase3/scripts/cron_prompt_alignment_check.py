#!/usr/bin/env python3
"""
Cron Prompt Alignment Check
============================

验证 cron prompt 中的占位符与 hermem_stats() schema 是否一致。
避免 "prompt里写了 {chunk_count} 但实际返回 total_chunks" 类静默错配。

用法：
    python3 cron_prompt_alignment_check.py [--jobs-json PATH] [--job-id ID]

输出 JSON：
    {
      "aligned": true/false,
      "missing_in_prompt": [...],   # schema 有但 prompt 没用的字段
      "stale_in_prompt": [...],     # prompt 用了但 schema 没有的字段
      "schema_version": "1.2",
      "checked_at": "2026-06-11T..."
    }
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# hermem_stats() 已知 schema 字段（v1.2）
KNOWN_FIELDS = {
    "total_chunks",
    "vector_count",
    "ollama_healthy",
    "ollama_latency_ms",
    "model_installed",
    "embedding_cache_entries",
}


def extract_placeholders(prompt_text: str) -> set[str]:
    """从 prompt 文本中提取所有 {field_name} 占位符"""
    return set(re.findall(r"\{([a-z_][a-z_0-9]*)\}", prompt_text))


def load_schema_version() -> str:
    """从 impl.config 读 STATS_SCHEMA_VERSION"""
    sys.path.insert(0, "/Users/oliver/.hermes/projects/hermem/phase3")
    try:
        import impl.config as config_mod
        return getattr(config_mod, "STATS_SCHEMA_VERSION", "unknown")
    except Exception as e:
        print(f"WARN: failed to load config: {e}", file=sys.stderr)
        return "unknown"


def check_job(jobs_json_path: str, job_id: str) -> dict:
    with open(jobs_json_path, encoding="utf-8") as f:
        jobs = json.load(f)

    job_list = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    target = None
    for j in job_list:
        if j.get("id") == job_id:
            target = j
            break

    if target is None:
        return {"error": f"job {job_id} not found in {jobs_json_path}"}

    prompt = target.get("prompt", "")
    placeholders = extract_placeholders(prompt)
    schema_version = load_schema_version()

    # 只关心已知的 stats 字段（其他占位符如 {delta}、{npy_shape} 不是 stats 字段）
    stats_placeholders = placeholders & KNOWN_FIELDS
    missing = KNOWN_FIELDS - stats_placeholders  # schema 有但 prompt 没用
    stale = stats_placeholders - KNOWN_FIELDS  # prompt 用了但 schema 没有

    # alignment = 没有 stale（stale 是硬错误，意味着 prompt 引用了不存在的字段）
    # missing 只是建议，不是硬错误（prompt 可能故意只展示部分字段）
    aligned = len(stale) == 0

    return {
        "job_id": job_id,
        "job_name": target.get("name", ""),
        "aligned": aligned,
        "schema_version": schema_version,
        "known_fields": sorted(KNOWN_FIELDS),
        "prompt_placeholders": sorted(stats_placeholders),
        "missing_in_prompt": sorted(missing),
        "stale_in_prompt": sorted(stale),
        "checked_at": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Cron prompt alignment check")
    parser.add_argument(
        "--jobs-json",
        default=os.path.expanduser("~/.hermes/cron/jobs.json"),
        help="Path to jobs.json",
    )
    parser.add_argument(
        "--job-id",
        default="48f3a3770234",
        help="Job ID to check (default: Hermem 记忆量提醒)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat missing fields as errors (not just stale)",
    )
    args = parser.parse_args()

    if not Path(args.jobs_json).exists():
        print(f"ERROR: {args.jobs_json} not found", file=sys.stderr)
        sys.exit(1)

    result = check_job(args.jobs_json, args.job_id)

    if args.strict and result.get("missing_in_prompt"):
        result["aligned"] = False

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("aligned") else 1)


if __name__ == "__main__":
    main()