#!/bin/zsh
set -euo pipefail

LABEL="com.lemonclean.tools.local-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/LemonToolsAgent"
PID_FILE="$HOME/Library/Application Support/LemonToolsAgent/local-agent.pid"
LAUNCHER="$HOME/Library/Application Support/LemonToolsAgent/start-local-agent.sh"
LOGIN_COMMAND="$HOME/Library/Application Support/LemonToolsAgent/login-agent.command"
WATCHDOG="$HOME/Library/Application Support/LemonToolsAgent/local-agent-watchdog.sh"
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

# 只允許一個 supervisor：已有另一個在跑就直接結束，避免重複啟動多個 Agent 同時搶任務
if [[ -f "\$PID_FILE" ]]; then
  other="\$(cat "\$PID_FILE" 2>/dev/null)"
  if [[ -n "\$other" && "\$other" != "\$\$" ]] && kill -0 "\$other" 2>/dev/null; then
    exit 0
  fi
fi
echo \$\$ > "\$PID_FILE"

cleanup() {
  stopping=1
  if [[ -n "\${child_pid:-}" ]] && kill -0 "\$child_pid" 2>/dev/null; then
    kill "\$child_pid" 2>/dev/null || true
    wait "\$child_pid" 2>/dev/null || true
  fi
  # 只刪自己的 PID 檔，不要讓別的 supervisor 結束時把正在跑的那個標成離線
  [[ "\$(cat "\$PID_FILE" 2>/dev/null)" == "\$\$" ]] && rm -f "\$PID_FILE"
}
trap cleanup EXIT INT TERM HUP

while (( ! stopping )); do
  # 上一個 Agent 若在執行中被 Ctrl+C、restart 或 crash 中止，先收尾其 running 任務。
  /Library/Developer/CommandLineTools/usr/bin/python3 -m tools.local_agent_recovery >> "\$LOG_FILE" 2>> "\$ERR_FILE" || true
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

  cat > "$WATCHDOG" <<EOF
#!/bin/zsh
PID_FILE="$PID_FILE"
LOGIN_COMMAND="$LOGIN_COMMAND"
if [[ -f "\$PID_FILE" ]] && kill -0 "\$(cat "\$PID_FILE")" 2>/dev/null; then
  exit 0
fi
# PID 檔不見但 supervisor 其實還在跑：不要再開一個
if /usr/bin/pgrep -f "start-local-agent.sh" >/dev/null; then
  exit 0
fi
rm -f "\$PID_FILE"
/usr/bin/open -g -a Terminal "\$LOGIN_COMMAND"
EOF
  chmod 700 "$WATCHDOG"
}

start_agent() {
  require_project
  write_launcher
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "already running: pid $(cat "$PID_FILE")"
    return
  fi
  if pgrep -f "start-local-agent.sh" >/dev/null; then
    echo "already running: $(pgrep -f "start-local-agent.sh" | tr '\n' ' ')（PID 檔遺失；要重整請執行 restart）"
    return
  fi
  rm -f "$PID_FILE"
  # 從使用者 shell 啟動常駐 supervisor；保留 Documents 存取權限，Agent 異常退出時自動重啟。
  nohup "$LAUNCHER" </dev/null >/dev/null 2>&1 &!
  sleep 1
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "started in background: pid $(cat "$PID_FILE")"
  else
    echo "Local Agent 啟動失敗；請執行 $SCRIPT_DIR/local_agent_service.sh logs" >&2
    exit 1
  fi
}

install_service() {
  require_project
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "${PID_FILE:h}"
  write_launcher
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  # Terminal retains the user's Documents access; launchd must not run Python directly.
  cat > "$LOGIN_COMMAND" <<EOF
#!/bin/zsh
exec ${(q)PROJECT_DIR}/scripts/local_agent_service.sh start
EOF
  chmod 700 "$LOGIN_COMMAND"
  /usr/bin/python3 - "$TARGET_PLIST" "$LABEL" "$WATCHDOG" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "wb") as output:
    plistlib.dump({
        "Label": sys.argv[2],
        "ProgramArguments": [sys.argv[3]],
        "RunAtLoad": True,
        "StartInterval": 30,
        "LimitLoadToSessionType": "Aqua",
    }, output)
PY
  plutil -lint "$TARGET_PLIST" >/dev/null
  launchctl bootstrap "gui/$UID" "$TARGET_PLIST"
  start_agent
  echo "installed: $LABEL"
  echo "project: $PROJECT_DIR"
}

stop_agent() {
  # 所有 supervisor 及 Agent 一起停（含 PID 檔沒記到、重複啟動的），避免重啟後舊版還在接任務
  pkill -f "start-local-agent.sh" 2>/dev/null || true
  pkill -f "tools.local_agent --poll-seconds" 2>/dev/null || true
  for _ in {1..50}; do
    pgrep -f "start-local-agent.sh|tools.local_agent --poll-seconds" >/dev/null || break
    sleep 0.2
  done
  pkill -9 -f "start-local-agent.sh" 2>/dev/null || true
  pkill -9 -f "tools.local_agent --poll-seconds" 2>/dev/null || true
  rm -f "$PID_FILE"
}

case "${1:-status}" in
  install) install_service ;;
  start) start_agent ;;
  restart) stop_agent; sleep 1; start_agent ;;
  uninstall)
    stop_agent
    launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
    rm -f "$TARGET_PLIST" "$LAUNCHER" "$LOGIN_COMMAND" "$WATCHDOG" "$PID_FILE"
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
