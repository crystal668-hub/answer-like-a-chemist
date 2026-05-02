#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${MINERU_RUN_DIR:-$ROOT_DIR/.run/mineru}"
LOG_FILE="${MINERU_LOG_FILE:-$RUN_DIR/mineru-api.log}"
PID_FILE="${MINERU_PID_FILE:-$RUN_DIR/mineru-api.pid}"
PLIST_FILE="${MINERU_PLIST_FILE:-$RUN_DIR/local.openclaw.mineru-api.plist}"
LAUNCH_LABEL="${MINERU_LAUNCH_LABEL:-local.openclaw.mineru-api}"
VENV_BIN="$ROOT_DIR/.venv/bin"
MINERU_HOST="${MINERU_HOST:-127.0.0.1}"
MINERU_PORT="${MINERU_PORT:-8000}"
MINERU_API_URL="${MINERU_API_URL:-http://$MINERU_HOST:$MINERU_PORT}"
MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-local}"
MINERU_DOWNLOAD_SOURCE="${MINERU_DOWNLOAD_SOURCE:-modelscope}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

usage() {
  cat <<'EOF'
Usage:
  scripts/mineru_service.sh install
  scripts/mineru_service.sh download-models
  scripts/mineru_service.sh up
  scripts/mineru_service.sh down
  scripts/mineru_service.sh restart
  scripts/mineru_service.sh ps
  scripts/mineru_service.sh logs
  scripts/mineru_service.sh health

Commands:
  install          Install native MinerU CLI/runtime with uv.
  download-models  Pre-download MinerU models into the local user cache.
  up               Start long-lived native mineru-api on 127.0.0.1:8000.
  down             Stop the native mineru-api process started by this script.
  restart          Restart the native mineru-api process.
  ps               Show process status for the native mineru-api process.
  logs             Tail the native mineru-api log.
  health           Run the MinerU API HTTP health check.

Environment overrides:
  MINERU_HOST      Host to bind; default 127.0.0.1.
  MINERU_PORT      Port to bind; default 8000.
  MINERU_API_URL   Health URL base; default http://$MINERU_HOST:$MINERU_PORT.
  MINERU_MODEL_SOURCE     Runtime model source; default local.
  MINERU_DOWNLOAD_SOURCE  Download source; default modelscope.
  HF_ENDPOINT             Hugging Face mirror URL; default https://hf-mirror.com.
  MINERU_RUN_DIR   Runtime dir for pid/log files; default workspace/.run/mineru.
EOF
}

find_cmd() {
  local name="$1"
  if [[ -x "$VENV_BIN/$name" ]]; then
    echo "$VENV_BIN/$name"
    return 0
  fi
  command -v "$name" 2>/dev/null
}

require_cmd() {
  if ! find_cmd "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

current_pid() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' <"$PID_FILE"
  fi
}

launch_domain() {
  echo "gui/$(id -u)"
}

launch_service() {
  echo "$(launch_domain)/$LAUNCH_LABEL"
}

launchctl_available() {
  [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1
}

launchctl_print() {
  launchctl print "$(launch_service)" >/dev/null 2>&1
}

launchctl_pid() {
  launchctl print "$(launch_service)" 2>/dev/null | awk -F'= ' '/^[[:space:]]*"?pid"? = / {print $2; exit}'
}

write_launch_plist() {
  local mineru_api_cmd="$1"
  cat >"$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LAUNCH_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$mineru_api_cmd</string>
    <string>--host</string>
    <string>$MINERU_HOST</string>
    <string>--port</string>
    <string>$MINERU_PORT</string>
    <string>--enable-vlm-preload</string>
    <string>true</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MINERU_MODEL_SOURCE</key>
    <string>$MINERU_MODEL_SOURCE</string>
    <key>HF_ENDPOINT</key>
    <string>$HF_ENDPOINT</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$ROOT_DIR</string>
  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$LOG_FILE</string>
</dict>
</plist>
EOF
}

wait_for_launch_pid() {
  local attempts="${1:-20}"
  local i
  local pid
  for ((i = 1; i <= attempts; i += 1)); do
    pid="$(launchctl_pid || true)"
    if pid_running "$pid"; then
      echo "$pid" >"$PID_FILE"
      return 0
    fi
    sleep 1
  done
  return 1
}

install() {
  local uv_cmd
  uv_cmd="$(find_cmd uv)"
  if [[ -z "$uv_cmd" ]]; then
    echo "Missing required command: uv" >&2
    exit 1
  fi
  "$uv_cmd" pip install -U "mineru[all]"
}

download_models() {
  local download_cmd
  download_cmd="$(find_cmd mineru-models-download)"
  if [[ -z "$download_cmd" ]]; then
    echo "Missing required command: mineru-models-download" >&2
    exit 1
  fi
  MINERU_MODEL_SOURCE="$MINERU_DOWNLOAD_SOURCE" HF_ENDPOINT="$HF_ENDPOINT" \
    "$download_cmd" --source "$MINERU_DOWNLOAD_SOURCE" --model_type all
}

up() {
  local mineru_api_cmd
  mineru_api_cmd="$(find_cmd mineru-api)"
  if [[ -z "$mineru_api_cmd" ]]; then
    echo "Missing required command: mineru-api" >&2
    exit 1
  fi
  mkdir -p "$RUN_DIR"

  local pid
  pid="$(current_pid || true)"
  if pid_running "$pid"; then
    echo "mineru-api already running with pid $pid"
    return 0
  fi

  if launchctl_available; then
    write_launch_plist "$mineru_api_cmd"
    if launchctl_print; then
      launchctl bootout "$(launch_service)" >/dev/null 2>&1 || true
    fi
    launchctl bootstrap "$(launch_domain)" "$PLIST_FILE"
    launchctl kickstart -k "$(launch_service)" >/dev/null 2>&1 || true
    if ! wait_for_launch_pid 20; then
      echo "mineru-api failed to start under launchctl; see logs: $LOG_FILE" >&2
      return 1
    fi
    pid="$(current_pid || true)"
  else
    (
      export MINERU_MODEL_SOURCE HF_ENDPOINT
      exec nohup "$mineru_api_cmd" \
        --host "$MINERU_HOST" \
        --port "$MINERU_PORT" \
        --enable-vlm-preload true
    ) >"$LOG_FILE" 2>&1 &
    pid="$!"
    echo "$pid" >"$PID_FILE"
    sleep 1
    if ! pid_running "$pid"; then
      echo "mineru-api failed to start; see logs: $LOG_FILE" >&2
      return 1
    fi
  fi
  echo "mineru-api started with pid $pid"
  echo "MINERU_API_URL=$MINERU_API_URL"
  echo "logs: $LOG_FILE"
}

down() {
  local pid
  pid="$(current_pid || true)"
  if launchctl_available && launchctl_print; then
    launchctl bootout "$(launch_service)" >/dev/null 2>&1 || true
    rm -f "$PID_FILE"
    echo "mineru-api stopped"
    return 0
  fi
  if ! pid_running "$pid"; then
    rm -f "$PID_FILE"
    echo "mineru-api is not running"
    return 0
  fi

  kill "$pid"
  rm -f "$PID_FILE"
  echo "mineru-api stopped"
}

restart() {
  down
  up
}

ps_status() {
  local pid
  if launchctl_available && launchctl_print; then
    pid="$(launchctl_pid || true)"
    if pid_running "$pid"; then
      echo "$pid" >"$PID_FILE"
      echo "mineru-api running with pid $pid"
      return 0
    fi
  fi
  pid="$(current_pid || true)"
  if pid_running "$pid"; then
    echo "mineru-api running with pid $pid"
    return 0
  fi
  echo "mineru-api is not running"
  return 0
}

logs() {
  mkdir -p "$RUN_DIR"
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

health() {
  require_cmd curl
  if curl -fsS "$MINERU_API_URL/health" >/dev/null; then
    echo "mineru-api is healthy: $MINERU_API_URL/health"
    return 0
  fi
  echo "mineru-api did not respond: $MINERU_API_URL/health" >&2
  return 1
}

main() {
  local command="${1:-}"
  shift || true

  case "$command" in
    install)
      install "$@"
      ;;
    download-models)
      download_models "$@"
      ;;
    up)
      up "$@"
      ;;
    down)
      down "$@"
      ;;
    restart)
      restart "$@"
      ;;
    ps)
      ps_status "$@"
      ;;
    logs)
      logs "$@"
      ;;
    health)
      health "$@"
      ;;
    ""|-h|--help|help)
      usage
      ;;
    *)
      echo "Unknown command: $command" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
