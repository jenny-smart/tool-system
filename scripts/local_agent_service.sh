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
PYTHON_BIN="${TOOL_LOCAL_AGENT_PYTHON:-$(command -v python3)}"

prepare_runtime() {
  mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_DIR" "$LOG_DIR"
  rsync -a --delete "$PROJECT_DIR/tools/" "$RUNTIME_DIR/tools/"
  cp "$SOURCE_PLIST" "$TARGET_PLIST"
  /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $PYTHON_BIN" "$TARGET_PLIST"
  plutil -lint "$TARGET_PLIST" >/dev/null
}

bootstrap_service() {
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  sleep 1
  bootstrap_ok=0
  for _attempt in 1 2 3; do
    if launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"; then
      bootstrap_ok=1
      break
    fi
    sleep 2
  done
  if (( bootstrap_ok == 0 )); then
    echo "failed to bootstrap: $LABEL" >&2
    exit 1
  fi
  launchctl enable "$DOMAIN/$LABEL"
  launchctl kickstart -k "$DOMAIN/$LABEL"
}

case "${1:-status}" in
  install)
    prepare_runtime
    bootstrap_service
    echo "installed: $LABEL"
    echo "python: $PYTHON_BIN"
    ;;
  restart)
    prepare_runtime
    bootstrap_service
    echo "restarted: $LABEL"
    echo "python: $PYTHON_BIN"
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
      grep -En "state =|pid =|last exit code =|program =|path =" /tmp/local_agent_launchd_status.txt || true
    else
      echo "offline: $LABEL"
      exit 1
    fi
    ;;
  logs)
    echo "stdout: $LOG_DIR/local_agent.launchd.out.log"
    echo "stderr: $LOG_DIR/local_agent.launchd.err.log"
    tail -n 80 "$LOG_DIR/local_agent.launchd.out.log" 2>/dev/null || true
    tail -n 80 "$LOG_DIR/local_agent.launchd.err.log" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 {install|restart|uninstall|status|logs}" >&2
    exit 2
    ;;
esac
