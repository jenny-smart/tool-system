#!/bin/zsh
set -euo pipefail

LABEL="com.lemonclean.tools.local-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_PLIST="$PROJECT_DIR/services/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$UID"
RUNTIME_ROOT="$HOME/Library/Application Support/LemonToolsAgent"
RUNTIME_DIR="$RUNTIME_ROOT/tool-system"
LOG_DIR="$HOME/Library/Logs/LemonToolsAgent"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_DIR" "$LOG_DIR"
    rm -rf "$RUNTIME_DIR/tools"
    mkdir -p "$RUNTIME_DIR/tools"
    cp -R "$PROJECT_DIR/tools/." "$RUNTIME_DIR/tools/"
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
    ;;
  restart)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl kickstart -k "$DOMAIN/$LABEL"
      echo "restarted: $LABEL"
    elif [[ -f "$TARGET_PLIST" ]]; then
      launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"
      launchctl enable "$DOMAIN/$LABEL"
      launchctl kickstart -k "$DOMAIN/$LABEL"
      echo "restarted: $LABEL"
    else
      "$0" install
    fi
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
    if status_output="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)"; then
      state_line="$(printf '%s\n' "$status_output" | grep -m 1 'state = ' || true)"
      pid_line="$(printf '%s\n' "$status_output" | grep -m 1 'pid = ' || true)"
      exit_line="$(printf '%s\n' "$status_output" | grep -m 1 'last exit code = ' || true)"
      [[ -n "$state_line" ]] || state_line="state = unknown"
      [[ -n "$pid_line" ]] || pid_line="pid = unavailable"
      [[ -n "$exit_line" ]] || exit_line="last exit code = 0 (running)"
      printf '%s\n%s\n%s\n' "$state_line" "$pid_line" "$exit_line"
    else
      echo "offline: $LABEL"
      exit 1
    fi
    ;;
  logs)
    echo "stdout: $LOG_DIR/local-agent.out.log"
    [[ ! -f "$LOG_DIR/local-agent.out.log" ]] || tail -n 100 "$LOG_DIR/local-agent.out.log"
    echo "stderr: $LOG_DIR/local-agent.err.log"
    [[ ! -f "$LOG_DIR/local-agent.err.log" ]] || tail -n 100 "$LOG_DIR/local-agent.err.log"
    ;;
  *)
    echo "usage: $0 {install|restart|status|logs|uninstall}" >&2
    exit 2
    ;;
esac
