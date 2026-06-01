#!/usr/bin/env bash
# backup_bridge.sh — snapshot the current Hermem bridge before any hermes-agent upgrade.
#
# Why: the bridge lives inside the hermes-agent checkout at
#   ~/.hermes/hermes-agent/plugins/memory/hermem/
# Any `pip install --upgrade hermes-agent` or `git pull origin main` inside that
# checkout will OVERWRITE our local fork (4 commits: P0-4 path resolution,
# P2-14 conflict resolver wiring, plus the V4.5 work).
#
# This script copies the current bridge into a timestamped /tmp directory so
# you have a known-good fallback. Re-runnable; old snapshots are kept.
#
# Usage:
#   bash phase3/scripts/backup_bridge.sh           # backup to /tmp/hermem-bridge-<date>
#   bash phase3/scripts/backup_bridge.sh --restore /tmp/hermem-bridge-2026-06-01
#   bash phase3/scripts/backup_bridge.sh --list
#
# Restore semantics: --restore copies the snapshot BACK over the bridge dir.
# It does NOT touch git — you are responsible for committing/restoring the
# hermes-agent worktree afterwards.

set -euo pipefail

BRIDGE_SRC="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/plugins/memory/hermem"
SNAPSHOT_ROOT="/tmp"
PREFIX="hermem-bridge"

# ── args ──────────────────────────────────────────────────────────────────────

action="backup"
restore_path=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restore)
            action="restore"
            restore_path="$2"
            shift 2
            ;;
        --list)
            action="list"
            shift
            ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

# ── list ──────────────────────────────────────────────────────────────────────

if [[ "$action" == "list" ]]; then
    echo "Existing bridge snapshots under $SNAPSHOT_ROOT:"
    ls -1d "$SNAPSHOT_ROOT"/${PREFIX}-* 2>/dev/null | sort || echo "  (none)"
    exit 0
fi

# ── restore ───────────────────────────────────────────────────────────────────

if [[ "$action" == "restore" ]]; then
    if [[ -z "$restore_path" || ! -d "$restore_path" ]]; then
        echo "ERROR: --restore requires an existing snapshot directory" >&2
        echo "  example: --restore /tmp/hermem-bridge-2026-06-01" >&2
        exit 1
    fi
    if [[ ! -f "$restore_path/__init__.py" ]]; then
        echo "ERROR: $restore_path does not look like a hermem bridge snapshot (no __init__.py)" >&2
        exit 1
    fi
    echo "Restoring bridge from $restore_path → $BRIDGE_SRC"
    rm -rf "$BRIDGE_SRC"
    cp -R "$restore_path" "$BRIDGE_SRC"
    echo "Done. Verify with: bash $(dirname "$0")/bridge_smoke.py"
    exit 0
fi

# ── backup ────────────────────────────────────────────────────────────────────

if [[ ! -d "$BRIDGE_SRC" ]]; then
    echo "ERROR: bridge not found at $BRIDGE_SRC" >&2
    echo "  (is hermes-agent checked out under ~/.hermes?)" >&2
    exit 1
fi

date_stamp="$(date +%Y-%m-%d)"
dest="$SNAPSHOT_ROOT/${PREFIX}-${date_stamp}"

# If today's snapshot already exists, suffix with HHMM
if [[ -d "$dest" ]]; then
    dest="${dest}-$(date +%H%M)"
fi

echo "Backing up bridge:"
echo "  from: $BRIDGE_SRC"
echo "  to:   $dest"
mkdir -p "$dest"
cp -R "$BRIDGE_SRC"/. "$dest"/

# Write a small manifest so the user knows what this snapshot is
cat > "$dest/SNAPSHOT.txt" <<EOF
Hermem bridge snapshot
======================
Date:           $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Source:         $BRIDGE_SRC
Bridge commit:  $(git -C "$BRIDGE_SRC/../.." rev-parse HEAD 2>/dev/null || echo "(not a git repo)")
Files included: $(ls -1 "$dest" | wc -l | tr -d ' ')

To restore:
    bash backup_bridge.sh --restore $dest
EOF

echo
echo "Snapshot manifest:"
cat "$dest/SNAPSHOT.txt"
echo
echo "Tip: list all snapshots: bash $(dirname "$0")/backup_bridge.sh --list"
