#!/usr/bin/env bash
# Hermem V5.5 - Weekly Memory Synthesis wrapper
# 由 launchd (com.hermes.weekly-memory-synthesis) 调用
# 不直接通过 launchd 调 Python 的原因：plist XML 不支持 $HOME 展开，需要先 cd

set -euo pipefail

HERMEM_HOME="${HERMES_HOME:-$HOME/.hermes}/projects/hermem/phase3"
cd "$HERMEM_HOME" || { echo "✗ 无法 cd 到 $HERMEM_HOME" >&2; exit 1; }

exec /usr/bin/env python3 v5.5/cron/cron_weekly_synthesis.py
