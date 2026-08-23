#!/bin/zsh
set -euo pipefail

LABEL="com.lemonclean.tools.local-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_PLIST="$PROJECT_DIR/services/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$UID"
LOG_DIR="$HOME/Library/Logs/LemonToolsAgent"
OLD_RUNTIME_DIR="$HOME/Library/Application Support/LemonToolsAgent/tool-system"

require_project() {
  if [[ ! -f "$PROJECT_DIR/tools/local_agent.py" ]]; then
    echo "找不到正式 Local Agent：$PROJECT_DIR/tools/local_agent.py" >&2
    exit 1
  fi
}

install_service() {
  require_project
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  sleep 1

  cp "$SOURCE_PLIST" "$TARGET_PLIST"
  plutil -lint "$TARGET_PLIST" >/dev/null

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
  echo "installed: $LABEL"
  echo "project: $PROJECT_DIR"
}

case "${1:-status}" in
  install)
    install_service
    ;;
  restart)
    require_project
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl kickstart -k "$DOMAIN/$LABEL"
      echo "restarted: $LABEL"
    else
      install_service
    fi
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    rm -f "$TARGET_PLIST"
    echo "uninstalled: $LABEL"
    ;;
  cleanup-old-runtime)
    if [[ -d "$OLD_RUNTIME_DIR" ]]; then
      rm -rf "$OLD_RUNTIME_DIR"
      echo "removed: $OLD_RUNTIME_DIR"
    else
      echo "old runtime not found: $OLD_RUNTIME_DIR"
    fi
    ;;
  status)
    if status_output="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)"; then
      state_line="$(printf '%s\n' "$status_output" | grep -m 1 'state = ' || true)"
      pid_line="$(printf '%s\n' "$status_output" | grep -m 1 'pid = ' || true)"
      exit_line="$(printf '%s\n' "$status_output" | grep -m 1 'last exit code = ' || true)"
      workdir_line="$(printf '%s\n' "$status_output" | grep -m 1 'working directory = ' || true)"
      [[ -n "$state_line" ]] || state_line="state = unknown"
      [[ -n "$pid_line" ]] || pid_line="pid = unavailable"
      [[ -n "$exit_line" ]] || exit_line="last exit code = 0 (running)"
      [[ -n "$workdir_line" ]] || workdir_line="working directory = unavailable"
      printf '%s\n%s\n%s\n%s\n' "$state_line" "$pid_line" "$exit_line" "$workdir_line"
    else
      echo "offline: $LABEL"
      exit 1
    fi
    ;;
  logs)
    echo "stdout: $LOG_DIR/local_agent.launchd.out.log"
    [[ ! -f "$LOG_DIR/local_agent.launchd.out.log" ]] || tail -n 100 "$LOG_DIR/local_agent.launchd.out.log"
    echo "stderr: $LOG_DIR/local_agent.launchd.err.log"
    [[ ! -f "$LOG_DIR/local_agent.launchd.err.log" ]] || tail -n 100 "$LOG_DIR/local_agent.launchd.err.log"
    ;;
  *)
    echo "usage: $0 {install|restart|status|logs|uninstall|cleanup-old-runtime}" >&2
    exit 2
    ;;
esac
