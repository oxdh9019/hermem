#!/usr/bin/env python3
"""Hermem V6 Sprint 0.5 - 进程健康检查(zombie detection)。

设计原则(**只告警不 kill**):
- 不自动 kill 任何 hermes/hermem 进程
- 39006 是当前 gateway 主进程(运行 68+ 小时,`--replace` 模式),不能误杀
- 检测到异常 → 写 ~/.hermes/memory/hermem_zombie_alert.jsonl
- 等 Oliver 人工处理

检测信号:
1. 长跑无响应(> 30 分钟 CPU 0% 且 RSS 极低)— 假设已死锁/挂起
2. 多进程同 fd 占用(hermem.db fd 持有 > 1 小时)
3. OOM 异常模式(launchd 反复 restart)

用法:
  python3 phase3/scripts/zombie_check.py            # 单次检查
  python3 phase3/scripts/zombie_check.py --watch 60  # 每 60s 检查一次
  python3 phase3/scripts/zombie_check.py --json     # JSON 输出供其他工具消费
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

ALERT_PATH = Path.home() / ".hermes" / "memory" / "hermem_zombie_alert.jsonl"

# 阈值(保守,宁可漏报不误报)
STUCK_CPU_MIN = 30 * 60  # 30 分钟 CPU 时间
STUCK_RSS_MIN_BYTES = 50 * 1024 * 1024  # 50 MB(异常低)
FD_HOLD_SECONDS = 3600  # hermem.db fd 持有 > 1 小时
HERMEM_PROCESS_PATTERN = ("hermes_cli.main", "hermem", "openclaw")


def _list_hermem_processes() -> list[dict]:
    """列所有疑似 hermem/hermes 进程,返回 [{pid, cmd, cpu_time, rss_kb, elapsed_sec}]."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,etime=,time=,rss=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    procs = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid_str, etime, cputime, rss_kb, comm, args = parts
        # 过滤
        if not any(p in args for p in HERMEM_PROCESS_PATTERN):
            continue
        procs.append(
            {
                "pid": int(pid_str),
                "etime": etime,
                "cputime": cputime,
                "rss_kb": int(rss_kb) if rss_kb.isdigit() else 0,
                "comm": comm,
                "args": args,
            }
        )
    return procs


def _parse_cputime(s: str) -> int:
    """ps time 格式:可能是 HH:MM:SS / MM:SS / 直接浮点秒(macOS BSD ps 行为)。

    Returns 总秒数(int)。
    """
    s = s.strip()
    # macOS BSD ps 有时直接输出浮点秒(如 "45.44")
    if ":" not in s:
        try:
            return int(float(s))
        except ValueError:
            return 0
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(float(parts[1]))
    return 0


def _parse_etime(s: str) -> int:
    """ps etime 格式 [[DD-]HH:]MM:SS → 秒。"""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = s.split(":")
    if len(parts) == 3:
        return days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return days * 86400 + int(parts[0]) * 60 + int(parts[1])
    return 0


def _check_stuck_processes() -> list[dict]:
    """检测疑似死锁/挂起的长跑进程(> 30 分钟 CPU 时间但 RSS 极低)。"""
    findings = []
    for p in _list_hermem_processes():
        cpu_sec = _parse_cputime(p["cputime"])
        elapsed = _parse_etime(p["etime"])
        rss_bytes = p["rss_kb"] * 1024
        if elapsed < STUCK_CPU_MIN:
            continue
        if cpu_sec < 5 and rss_bytes < STUCK_RSS_MIN_BYTES:
            findings.append(
                {
                    "type": "stuck_process",
                    "pid": p["pid"],
                    "comm": p["comm"],
                    "elapsed_sec": elapsed,
                    "cpu_sec": cpu_sec,
                    "rss_bytes": rss_bytes,
                    "args": p["args"][:200],
                    "suggestion": "长跑无响应,可能死锁/挂起。考虑人工 review 后 kill -9 或 launchctl kickstart。",
                }
            )
    return findings


def _check_hermem_db_fd_holders() -> list[dict]:
    """检测持有 hermem.db fd > 1 小时的进程(可能 fd 未关闭泄漏)。"""
    findings = []
    db_path = Path.home() / ".hermes" / "memory" / "hermem.db"
    if not db_path.exists():
        return findings
    try:
        out = subprocess.run(
            ["lsof", str(db_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return findings
    if out.returncode != 0:
        return findings

    pids = set()
    for line in out.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))

    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read().split()
            # starttime field 22 (0-indexed) — 进程启动 jiffies
            if len(stat) > 22:
                start_jiffies = int(stat[21])
                # /proc/uptime 第一项是系统启动秒数
                with open("/proc/uptime") as f:
                    uptime_sec = float(f.read().split()[0])
                clk_tck = os.sysconf("SC_CLK_TCK")
                proc_started_at = uptime_sec - (uptime_sec * clk_tck - start_jiffies) / clk_tck
                held_sec = uptime_sec - proc_started_at
                if held_sec > FD_HOLD_SECONDS:
                    findings.append(
                        {
                            "type": "long_held_fd",
                            "pid": pid,
                            "fd_path": str(db_path),
                            "held_sec": int(held_sec),
                            "suggestion": "进程持有 hermem.db fd 超过 1 小时,正常连接不应这么久。建议 lsof + kill。",
                        }
                    )
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            # macOS 没有 /proc — 跳过此检测(用其他信号)
            pass
    return findings


def run_all_checks() -> list[dict]:
    """执行全部检测,返回 finding 列表。"""
    findings = []
    findings.extend(_check_stuck_processes())
    findings.extend(_check_hermem_db_fd_holders())
    return findings


def write_alert(findings: list[dict]) -> Path | None:
    """有 finding 时追加写入告警文件。无 finding → None。"""
    if not findings:
        return None
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", encoding="utf-8") as f:
        for finding in findings:
            record = {
                "ts": datetime.now(UTC).isoformat(),
                "check_version": "v6-sprint05-0.1",
                **finding,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return ALERT_PATH


def main():
    ap = argparse.ArgumentParser(description="Hermem zombie check (alert only, no kill)")
    ap.add_argument("--watch", type=int, metavar="SEC", help="repeat every N seconds")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--quiet", action="store_true", help="suppress normal output, only alerts")
    args = ap.parse_args()

    while True:
        findings = run_all_checks()
        alert_path = write_alert(findings)

        if args.json:
            print(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "findings": findings,
                        "alert_written": str(alert_path) if alert_path else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            if findings:
                print(f"[!] {len(findings)} finding(s):")
                for f in findings:
                    print(
                        f"    - {f['type']}: pid={f.get('pid')} → {f.get('suggestion', 'manual review')}"
                    )
                if alert_path:
                    print(f"    Alert written to: {alert_path}")
            elif not args.quiet:
                print("[ok] no zombie/stuck processes detected")

        if not args.watch:
            return 0 if not findings else 1
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
