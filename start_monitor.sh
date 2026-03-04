#!/usr/bin/env bash
# 盯盘系统启停脚本 — 全后台运行，关掉终端也不影响
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
POLLER_PID="$DIR/.poller.pid"
NOTIFIER_PID="$DIR/.notifier.pid"
L2_DAEMON_PID="$DIR/.l2_daemon.pid"
WEB_PID="$DIR/.web.pid"
POLLER_LOG="$DIR/logs/poller.log"
NOTIFIER_LOG="$DIR/logs/notifier.log"
L2_DAEMON_LOG="$DIR/logs/l2_daemon_out.log"
WEB_LOG="$DIR/logs/web.log"

mkdir -p "$DIR/logs"

# 按日期生成日志文件名
TODAY=$(date +%Y-%m-%d)
POLLER_LOG="$DIR/logs/poller-$TODAY.log"
NOTIFIER_LOG="$DIR/logs/notifier-$TODAY.log"
L2_DAEMON_LOG="$DIR/logs/l2_daemon-$TODAY.log"
WEB_LOG="$DIR/logs/web-$TODAY.log"

_check_terminal_notifier() {
  if ! command -v terminal-notifier &> /dev/null; then
    echo "ERROR: terminal-notifier 未安装"
    echo ""
    echo "L1/L2 告警弹窗功能需要 terminal-notifier，请安装:"
    echo ""
    echo "  brew install terminal-notifier"
    echo ""
    exit 1
  fi
}

_read_pid() { [ -f "$1" ] && cat "$1" || echo ""; }

_is_running() {
  local pid=$(_read_pid "$1")
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

_stop_one() {
  local pidfile=$1 label=$2 pattern=$3
  local pid=$(_read_pid "$pidfile")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    # 杀进程组（poetry → python 子进程一起杀）
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
    echo "$label 已停止 (pid=$pid)"
  fi
  rm -f "$pidfile"
  # 兜底：用命令模式清理可能的孤儿进程
  if [ -n "$pattern" ]; then
    pkill -f "$pattern" 2>/dev/null || true
  fi
}

_ensure_no_orphan() {
  # 启动前兜底：若 PID 文件不存在/失效但进程实际还在，先杀掉
  local pidfile=$1 pattern=$2
  if ! _is_running "$pidfile" && pgrep -f "$pattern" > /dev/null 2>&1; then
    echo "  检测到孤儿进程 ($pattern)，清理中..."
    pkill -f "$pattern" 2>/dev/null || true
    sleep 0.5
  fi
}

do_start() {
  # 检查 terminal-notifier 依赖
  _check_terminal_notifier

  # Poller
  _ensure_no_orphan "$POLLER_PID" "market_data_poller.py"
  if _is_running "$POLLER_PID"; then
    echo "Poller 已在运行 (pid=$(_read_pid "$POLLER_PID"))，跳过"
  else
    cd "$DIR"
    nohup poetry run python src/tools/market_data_poller.py >> "$POLLER_LOG" 2>&1 &
    echo $! > "$POLLER_PID"
    echo "Poller  启动  pid=$!  日志=logs/poller-$TODAY.log"
  fi

  # Notifier (等 poller 先写一次数据)
  _ensure_no_orphan "$NOTIFIER_PID" "stock_notifier.py"
  if _is_running "$NOTIFIER_PID"; then
    echo "Notifier 已在运行 (pid=$(_read_pid "$NOTIFIER_PID"))，跳过"
  else
    cd "$DIR"
    sleep 2  # 等 poller 首次写入 market_data.json
    nohup poetry run python src/tools/stock_notifier.py >> "$NOTIFIER_LOG" 2>&1 &
    echo $! > "$NOTIFIER_PID"
    echo "Notifier 启动  pid=$!  日志=logs/notifier-$TODAY.log"
  fi

  # L2 Strategy Daemon (optional, needs Futu OpenD)
  _ensure_no_orphan "$L2_DAEMON_PID" "l2_strategy_daemon.py"
  if _is_running "$L2_DAEMON_PID"; then
    echo "L2 Daemon 已在运行 (pid=$(_read_pid "$L2_DAEMON_PID"))，跳过"
  else
    cd "$DIR"
    nohup poetry run python src/tools/l2_strategy_daemon.py >> "$L2_DAEMON_LOG" 2>&1 &
    echo $! > "$L2_DAEMON_PID"
    echo "L2 Daemon 启动  pid=$!  日志=logs/l2_daemon-$TODAY.log"
  fi

  # Web
  _ensure_no_orphan "$WEB_PID" "next dev"
  if _is_running "$WEB_PID"; then
    echo "Web    已在运行 (pid=$(_read_pid "$WEB_PID"))，跳过"
  else
    cd "$DIR/web"
    nohup npm run dev >> "$WEB_LOG" 2>&1 &
    echo $! > "$WEB_PID"
    echo "Web    启动  pid=$!  日志=logs/web-$TODAY.log"
  fi

  echo ""
  echo "全部后台运行中，可关闭终端。"
  echo "  查看状态: ./start_monitor.sh status"
  echo "  查看日志: tail -f logs/poller-$TODAY.log logs/notifier-$TODAY.log logs/l2_daemon-$TODAY.log logs/web-$TODAY.log"
  echo "  停止服务: ./start_monitor.sh stop"
}

do_stop() {
  _stop_one "$NOTIFIER_PID" "Notifier" "stock_notifier.py"
  _stop_one "$L2_DAEMON_PID" "L2 Daemon" "l2_strategy_daemon.py"
  _stop_one "$POLLER_PID" "Poller" "market_data_poller.py"
  _stop_one "$WEB_PID" "Web" "next-router-worker\|next dev"
}

do_status() {
  if _is_running "$POLLER_PID"; then
    echo "Poller  运行中  pid=$(_read_pid "$POLLER_PID")"
  else
    echo "Poller  未运行"
    rm -f "$POLLER_PID"
  fi

  if _is_running "$NOTIFIER_PID"; then
    echo "Notifier 运行中  pid=$(_read_pid "$NOTIFIER_PID")"
  else
    echo "Notifier 未运行"
    rm -f "$NOTIFIER_PID"
  fi

  if _is_running "$L2_DAEMON_PID"; then
    echo "L2 Daemon 运行中  pid=$(_read_pid "$L2_DAEMON_PID")"
  else
    echo "L2 Daemon 未运行"
    rm -f "$L2_DAEMON_PID"
  fi

  if _is_running "$WEB_PID"; then
    echo "Web      运行中  pid=$(_read_pid "$WEB_PID")"
  else
    echo "Web      未运行"
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
