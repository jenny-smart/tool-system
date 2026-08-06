#!/bin/zsh
set -euo pipefail

LABEL="com.lemonclean.tools.local-agent"
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
SOURCE_PLIST="$PROJECT_DIR/services/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$UID"
RUNTIME_ROOT="$HOME/Library/Application Support/LemonToolsAgent"
RUNTIME_DIR="$RUNTIME_ROOT/tool-system"
LOG_DIR="$HOME/Library/Logs/LemonToolsAgent"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_DIR" "$LOG_DIR"
    rsync -a --delete "$PROJECT_DIR/tools/" "$RUNTIME_DIR/tools/"
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    cp "$SOURCE_PLIST" "$TARGET_PLIST"
    plutil -lint "$TARGET_PLIST" >/dev/null
    launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"
    launchctl enable "$DOMAIN/$LABEL"
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "installed: $LABEL"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    rm -f "$TARGET_PLIST"
    if [[ "$RUNTIME_ROOT" == "$HOME/Library/Application Support/LemonToolsAgent" ]]; then
      rm -rf "$RUNTIME_ROOT"
    fi
    echo "uninstalled: $LABEL"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/tmp/local_agent_launchd_status.txt 2>/dev/null; then
      rg -n "state =|pid =|last exit code =|program =|path =" /tmp/local_agent_launchd_status.txt || true
    else
      echo "offline: $LABEL"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}" >&2
    exit 2
    ;;
esac
