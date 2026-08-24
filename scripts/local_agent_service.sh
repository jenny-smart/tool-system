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
set -u
cd "$PROJECT_DIR"
export HOME="$HOME"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_DIR"
export TOOLS_APP_SECRETS_FILE="$HOME/lemon/.streamlit/secrets.toml"

PID_FILE="$PID_FILE"
LOG_FILE="$LOG_DIR/local_agent.launchd.out.log"
ERR_FILE="$LOG_DIR/local_agent.launchd.err.log"
child_pid=""
stopping=0

echo \$\$ > "\$PID_FILE"

cleanup() {
  stopping=1
  if [[ -n "\${child_pid:-}" ]] && kill -0 "\$child_pid" 2>/dev/null; then
    kill "\$child_pid" 2>/dev/null || true
    wait "\$child_pid" 2>/dev/null || true
  fi
  rm -f "\$PID_FILE"
}
trap cleanup EXIT INT TERM HUP

while (( ! stopping )); do
  /Library/Developer/CommandLineTools/usr/bin/python3 -m tools.local_agent --poll-seconds 2 >> "\$LOG_FILE" 2>> "\$ERR_FILE" &
  child_pid=\$!
  exit_code=0
  wait "\$child_pid" || exit_code=\$?
  child_pid=""

  (( stopping )) && break
  printf '[%s] Local Agent exited with code %s; restarting in 5 seconds.\n' "\$(date '+%Y-%m-%d %H:%M:%S')" "\$exit_code" >> "\$ERR_FILE"
  sleep 5
 done
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
  # 從使用者 shell 啟動常駐 supervisor；保留 Documents 存取權限，Agent 異常退出時自動重啟。
  nohup "$LAUNCHER" </dev/null >/dev/null 2>&1 &!
  sleep 1
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "started in background: pid $(cat "$PID_FILE")"
  else
    echo "Local Agent 啟動失敗；請執行 $0 logs" >&2
    exit 1
  fi
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
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
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
