#!/usr/bin/env bash
# Hermem V5.5 - Weekly Memory Synthesis launchd installer
# 注册 com.hermes.weekly-memory-synthesis 到 ~/Library/LaunchAgents
# 计划：每周日 02:30 执行 cron_weekly_synthesis.py
#
# Usage:
#   bash phase3/v5.5/cron/install_weekly_cron.sh         # 安装 + 加载
#   bash phase3/v5.5/cron/install_weekly_cron.sh uninstall
#   bash phase3/v5.5/cron/install_weekly_cron.sh run      # 手动跑一次

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.hermes.weekly-memory-synthesis.plist"
PLIST_NAME="com.hermes.weekly-memory-synthesis.plist"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS/$PLIST_NAME"
LOG_DIR="$HOME/.hermes/logs"
HERMEM_HOME="${HERMES_HOME:-$HOME/.hermes}/projects/hermem/phase3"
WRAPPER="$SCRIPT_DIR/run_weekly_synthesis.sh"

label="com.hermes.weekly-memory-synthesis"

ensure_wrapper_executable() {
    chmod +x "$WRAPPER"
}

case "${1:-install}" in
    run)
        ensure_wrapper_executable
        "$WRAPPER"
        ;;
    uninstall)
        if launchctl list 2>/dev/null | grep -q "$label"; then
            launchctl unload "$PLIST_DEST" 2>/dev/null || true
        fi
        rm -f "$PLIST_DEST"
        echo "✓ 已卸载 $label"
        ;;
    install|"")
        # 前置检查
        if [[ ! -f "$HERMEM_HOME/v5.5/cron/cron_weekly_synthesis.py" ]]; then
            echo "✗ 未找到 $HERMEM_HOME/v5.5/cron/cron_weekly_synthesis.py" >&2
            echo "  请确认 Hermem impl 已 clone 到 $HERMEM_HOME" >&2
            exit 1
        fi
        ensure_wrapper_executable
        mkdir -p "$LOG_DIR" "$LAUNCH_AGENTS"

        # 占位符替换
        sed -e "s|__HERMEM_HOME__|$HERMEM_HOME|g" \
            -e "s|__LOG_DIR__|$LOG_DIR|g" \
            "$PLIST_SRC" > "$PLIST_DEST"

        # 加载（先卸载避免重复）
        if launchctl list 2>/dev/null | grep -q "$label"; then
            launchctl unload "$PLIST_DEST" 2>/dev/null || true
        fi
        launchctl load "$PLIST_DEST"

        echo "✓ 已安装 $label → 每周日 02:30"
        echo "  日志: $LOG_DIR/hermem-weekly-synthesis.log"
        echo "  错误: $LOG_DIR/hermem-weekly-synthesis.err.log"
        echo ""
        echo "手动测试:"
        echo "  bash $0 run                     # 直接跑一次（同步）"
        echo "  launchctl start $label          # 触发 launchd 执行"
        echo "  launchctl list | grep hermem    # 查看状态"
        echo "  tail -f $LOG_DIR/hermem-weekly-synthesis.log"
        ;;
    *)
        echo "Usage: $0 [install|uninstall|run]" >&2
        exit 1
        ;;
esac
