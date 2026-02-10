#!/usr/bin/env bash
# 盯盘系统启停脚本 — 全后台运行，关掉终端也不影响
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
POLLER_PID="$DIR/.poller.pid"
WEB_PID="$DIR/.web.pid"
POLLER_LOG="$DIR/logs/poller.log"
WEB_LOG="$DIR/logs/web.log"

mkdir -p "$DIR/logs"

_read_pid() { [ -f "$1" ] && cat "$1" || echo ""; }

_is_running() {
  local pid=$(_read_pid "$1")
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

_stop_one() {
  local pidfile=$1 label=$2
  local pid=$(_read_pid "$pidfile")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "$label 已停止 (pid=$pid)"
  fi
  rm -f "$pidfile"
}

do_start() {
  # Poller
  if _is_running "$POLLER_PID"; then
    echo "Poller 已在运行 (pid=$(_read_pid "$POLLER_PID"))，跳过"
  else
    cd "$DIR"
    nohup poetry run python src/tools/market_data_poller.py >> "$POLLER_LOG" 2>&1 &
    echo $! > "$POLLER_PID"
    echo "Poller  启动  pid=$!  日志=$POLLER_LOG"
  fi

  # Web
  if _is_running "$WEB_PID"; then
    echo "Web    已在运行 (pid=$(_read_pid "$WEB_PID"))，跳过"
  else
    cd "$DIR/web"
    nohup npm run dev >> "$WEB_LOG" 2>&1 &
    echo $! > "$WEB_PID"
    echo "Web    启动  pid=$!  日志=$WEB_LOG"
  fi

  echo ""
  echo "全部后台运行中，可关闭终端。"
  echo "  查看状态: ./start_monitor.sh status"
  echo "  查看日志: tail -f logs/poller.log logs/web.log"
  echo "  停止服务: ./start_monitor.sh stop"
}

do_stop() {
  _stop_one "$POLLER_PID" "Poller"
  _stop_one "$WEB_PID" "Web"
}

do_status() {
  if _is_running "$POLLER_PID"; then
    echo "Poller  运行中  pid=$(_read_pid "$POLLER_PID")"
  else
    echo "Poller  未运行"
    rm -f "$POLLER_PID"
  fi

  if _is_running "$WEB_PID"; then
    echo "Web     运行中  pid=$(_read_pid "$WEB_PID")"
  else
    echo "Web     未运行"
    rm -f "$WEB_PID"
  fi
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

case "${1:-start}" in
  start)   do_start   ;;
  stop)    do_stop    ;;
  status)  do_status  ;;
  restart) do_restart ;;
  *)
    echo "用法: $0 {start|stop|status|restart}"
    exit 1
    ;;
esac
