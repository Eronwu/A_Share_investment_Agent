#!/usr/bin/env python3
"""
L2 Strategy Daemon — 独立进程，3s 轮询检测 L2 策略信号

读取 monitor_config.json（持仓列表）+ l2_strategy_config.json（策略参数），
通过 Futu OpenD 获取实时 L2 数据，检测信号后写入 l2_strategy_signals.json。

Notifier 消费信号文件，转换为 macOS 通知 + alert_events.json。

设计原则:
  - OpenD 不可用时静默退出（Futu 可选）
  - 信号原子写入（tmp → rename）
  - 交易时段 3s，非交易时段 60s

Usage:
    poetry run python src/tools/l2_strategy_daemon.py
"""

import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.l2_strategy_engine import L2StrategyEngine
from src.tools.stock_notifier import is_any_market_open, read_json_safe
from src.tools.stock_monitor import is_hk_symbol
from src.utils.logging_config import setup_logger

logger = setup_logger("l2_daemon")

# ── File paths ──
MONITOR_CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
L2_CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "l2_strategy_config.json"
L2_SIGNALS_PATH = PROJECT_ROOT / "src" / "data" / "l2_strategy_signals.json"

# ── Poll intervals ──
TRADING_POLL_SEC = 3
NON_TRADING_POLL_SEC = 60

MAX_SIGNALS = 200


def load_configs() -> tuple[dict, dict]:
    """Load monitor_config and l2_strategy_config"""
    monitor = read_json_safe(MONITOR_CONFIG_PATH)
    if monitor is None:
        logger.error(f"Cannot read {MONITOR_CONFIG_PATH}")
        sys.exit(1)

    l2_config = read_json_safe(L2_CONFIG_PATH)
    if l2_config is None:
        logger.warning(f"Cannot read {L2_CONFIG_PATH}, using defaults")
        l2_config = {"enabled": True, "strategies": {}, "max_signals": MAX_SIGNALS}

    return monitor, l2_config


def write_signals(new_signals: list[dict]):
    """原子写入信号到 l2_strategy_signals.json"""
    if not new_signals:
        return

    # 读已有信号
    existing = []
    try:
        if L2_SIGNALS_PATH.exists():
            with open(L2_SIGNALS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("signals", [])
    except Exception:
        existing = []

    # 追加新信号
    existing.extend(new_signals)

    # 保留最近 N 条
    max_signals = MAX_SIGNALS
    existing = existing[-max_signals:]

    ts = int(time.time() * 1000)

    # 原子写入
    tmp = L2_SIGNALS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "signals": existing,
            "lastUpdated": ts,
        }, f, ensure_ascii=False, indent=2)
    tmp.replace(L2_SIGNALS_PATH)


def run():
    """Main daemon loop"""
    running = True

    def _handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Load configs ──
    monitor_config, l2_config = load_configs()

    if not l2_config.get("enabled", True):
        print("L2 strategy engine is disabled in config. Exiting.")
        return

    watchlist = monitor_config.get("watchlist", {})
    has_hk = any(is_hk_symbol(s) for s in watchlist)
    hk_holdings = [
        code for code, info in watchlist.items()
        if code.startswith("HK")
        and info.get("type") == "holding"
        and not info.get("hidden", False)
    ]

    if not hk_holdings:
        print("No HK holdings found in watchlist. L2 daemon has nothing to monitor.")
        return

    # ── Initialize engine ──
    engine = L2StrategyEngine(l2_config, watchlist)

    # ── Startup banner ──
    print("L2 Strategy Daemon started")
    print(f"  HK holdings : {len(hk_holdings)} stocks")
    strategies = l2_config.get("strategies", {})
    enabled = [k for k, v in strategies.items() if v.get("enabled", True)]
    print(f"  strategies  : {', '.join(enabled)}")
    print(f"  signals file: {L2_SIGNALS_PATH.name}")
    print(f"  Ctrl+C to stop\n")

    # ── State ──
    total_signals = 0
    last_date = datetime.now().date()

    while running:
        # Daily reset
        today = datetime.now().date()
        if today != last_date:
            last_date = today
            total_signals = 0
            engine.reset_daily()
            # Reload config
            monitor_config, l2_config = load_configs()
            watchlist = monitor_config.get("watchlist", {})
            engine = L2StrategyEngine(l2_config, watchlist)
            logger.info("Daily reset complete, config reloaded")

        trading = is_any_market_open(has_hk)
        poll_interval = TRADING_POLL_SEC if trading else NON_TRADING_POLL_SEC

        if trading:
            signals = engine.poll_once()
            if signals:
                write_signals(signals)
                total_signals += len(signals)
                for s in signals:
                    logger.info(f"Signal: [{s['strategy']}] {s['display']}")

            # Status line
            now = datetime.now().strftime("%H:%M:%S")
            print(
                f"\r\033[K[{now}] L2 daemon | signals: {total_signals} | next: {poll_interval}s",
                end="", flush=True,
            )
        else:
            now = datetime.now().strftime("%H:%M:%S")
            print(
                f"\r\033[K[{now}] L2 daemon | non-trading | signals today: {total_signals} | next: {poll_interval}s",
                end="", flush=True,
            )

        # Sleep in small increments for responsive shutdown
        slept = 0.0
        while slept < poll_interval and running:
            time.sleep(min(0.5, poll_interval - slept))
            slept += 0.5

    # ── Shutdown ──
    engine.close()
    print(f"\n\nL2 Strategy Daemon stopped. Total signals today: {total_signals}")


if __name__ == "__main__":
    run()
