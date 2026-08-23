#!/bin/zsh
set -euo pipefail

LABEL="com.lemonclean.tools.local-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/LemonToolsAgent"
PID_FILE="$HOME/Library/Application Support/LemonToolsAgent/local-agent.pid"
LAUNCHER="$HOME/Library/Application Support/LemonToolsAgent/start-local-agent.sh"
OLD_RUNTIME_DIR="$HOME/Library/Application Support/LemonToolsAgent/tool-system"

require_project() {
  [[ -f "$PROJECT_DIR/tools/local_agent.py" ]] || { echo "找不到正式 Local Agent：$PROJECT_DIR/tools/local_agent.py" >&2; exit 1; }
}

write_launcher() {
  mkdir -p "${PID_FILE:h}" "$LOG_DIR"
  cat > "$LAUNCHER" <<EOF
#!/bin/zsh
set -e
cd "$PROJECT_DIR"
export HOME="$HOME"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_DIR"
export TOOLS_APP_SECRETS_FILE="$HOME/lemon/.streamlit/secrets.toml"
echo \$\$ > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT INT TERM
exec /Library/Developer/CommandLineTools/usr/bin/python3 -m tools.local_agent --poll-seconds 2 >> "$LOG_DIR/local_agent.launchd.out.log" 2>> "$LOG_DIR/local_agent.launchd.err.log"
EOF
  chmod 700 "$LAUNCHER"
}

start_agent() {
  require_project
  write_launcher
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "already running: pid $(cat "$PID_FILE")"
    return
  fi
  rm -f "$PID_FILE"
  /usr/bin/osascript -e 'tell application "Terminal"' -e "do script quoted form of \"$LAUNCHER\"" -e 'activate' -e 'end tell' >/dev/null
  echo "started in user Terminal session"
}

install_service() {
  require_project
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "${PID_FILE:h}"
  write_launcher
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  cat > "$TARGET_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>/usr/bin/true</string></array>
<key>RunAtLoad</key><false/>
</dict></plist>
EOF
  plutil -lint "$TARGET_PLIST" >/dev/null
  start_agent
  echo "installed: $LABEL"
  echo "project: $PROJECT_DIR"
}

stop_agent() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
}

case "${1:-status}" in
  install) install_service ;;
  start) start_agent ;;
  restart) stop_agent; sleep 1; start_agent ;;
  uninstall)
    stop_agent
    launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
    rm -f "$TARGET_PLIST" "$LAUNCHER" "$PID_FILE"
    echo "uninstalled: $LABEL"
    ;;
  cleanup-old-runtime)
    if [[ -d "$OLD_RUNTIME_DIR" ]]; then rm -rf "$OLD_RUNTIME_DIR"; echo "removed: $OLD_RUNTIME_DIR"; else echo "old runtime not found: $OLD_RUNTIME_DIR"; fi
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "state = running"
      echo "pid = $(cat "$PID_FILE")"
      echo "working directory = $PROJECT_DIR"
    else
      echo "state = offline"
      echo "pid = unavailable"
      echo "working directory = $PROJECT_DIR"
      exit 1
    fi
    ;;
  logs)
    echo "stdout: $LOG_DIR/local_agent.launchd.out.log"
    [[ ! -f "$LOG_DIR/local_agent.launchd.out.log" ]] || tail -n 100 "$LOG_DIR/local_agent.launchd.out.log"
    echo "stderr: $LOG_DIR/local_agent.launchd.err.log"
    [[ ! -f "$LOG_DIR/local_agent.launchd.err.log" ]] || tail -n 100 "$LOG_DIR/local_agent.launchd.err.log"
    ;;
  *) echo "usage: $0 {install|start|restart|status|logs|uninstall|cleanup-old-runtime}" >&2; exit 2 ;;
esac
