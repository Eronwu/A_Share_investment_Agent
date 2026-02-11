#!/usr/bin/env python3
"""
Stock Notifier -- mtime-driven notification daemon

Watches market_data.json (written by poller) for changes, merges with
monitor_config.json (user thresholds), and fires macOS notifications
when price/big-move alerts trigger.

Does NOT fetch any market data -- purely a consumer of poller output.

Usage:
    poetry run python src/tools/stock_notifier.py
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

from src.tools.stock_monitor import is_hk_symbol, notify
from src.utils.logging_config import setup_logger

logger = setup_logger("stock_notifier")

# ── Data file paths ──
MARKET_DATA_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"
MONITOR_CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"

# ── Poll intervals ──
TRADING_CHECK_SEC = 3      # mtime check interval during trading hours
NON_TRADING_CHECK_SEC = 60  # mtime check interval outside trading hours


# ══════════════════════════════════════════
# 1. Trading hours (extended for HK)
# ══════════════════════════════════════════

def is_any_market_open(has_hk: bool = False) -> bool:
    """Check if any watched market is currently in trading hours.

    A-shares: Mon-Fri 09:15-11:30, 13:00-15:00
    HK:       Mon-Fri 09:15-12:00, 13:00-16:00 (when has_hk=True)

    Uses 09:15 instead of 09:30 to catch pre-open auction moves.
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False

    t = now.hour * 100 + now.minute

    # A-share session
    if (915 <= t <= 1130) or (1300 <= t <= 1500):
        return True

    # HK extended session
    if has_hk:
        if (915 <= t <= 1200) or (1300 <= t <= 1600):
            return True

    return False


# ══════════════════════════════════════════
# 2. File reading helpers
# ══════════════════════════════════════════

def read_json_safe(path: Path) -> dict | None:
    """Read a JSON file, returning None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in {path.name}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to read {path.name}: {e}")
        return None


def get_mtime(path: Path) -> float:
    """Return file mtime, or 0 if file does not exist."""
    try:
        return path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return 0.0


# ══════════════════════════════════════════
# 3. Data merging
# ══════════════════════════════════════════

def merge_data(market: dict, config: dict) -> dict:
    """Build quotes dict from market_data + config.

    Returns: {symbol: {name, price, change_pct, chg_amt}} keyed by stock
    id/code.  The ``chg_amt`` field (absolute price change today) is carried
    through so DeltaAlertEngine can compute daily P&L.
    """
    watchlist = config.get("watchlist", {})
    services = market.get("services", [])

    quotes = {}
    for svc in services:
        sid = svc.get("id", "")
        if not sid:
            continue
        price = svc.get("price", 0)
        # "change" in market_data.json is the percentage (涨跌幅%)
        change_pct = svc.get("change", 0)
        # "chgAmt" is absolute price change (涨跌额)
        chg_amt = svc.get("chgAmt", 0)
        name = svc.get("name", sid)

        # Only alert on stocks that exist in the watchlist
        if sid not in watchlist:
            continue

        # Skip hidden stocks
        if watchlist[sid].get("hidden", False):
            continue

        quotes[sid] = {
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "chg_amt": chg_amt,
        }

    return quotes


# ══════════════════════════════════════════
# 4. Delta-based alert engine (变化驱动)
# ══════════════════════════════════════════
#
# 核心逻辑：以价格变化驱动通知，不重复提醒同一状态。
#
#   首次触发: |日涨跌幅| >= trigger_pct  →  通知，记住当前价格
#   再次触发: |当前价 - 上次通知价| / 上次通知价 * 100 >= delta_pct
#   触价同理: 首次突破通知，之后价格变化 >= delta_pct 才再通知
#
# 例: 掌阅涨停 +10% → 通知1次，记住 31.09。
#     价格不变 → 不再通知。回落到 29.85 (-4%) → 再通知。
#
# Settings (均可通过 monitor_config.json 覆盖):
#   trigger_pct  : 首次触发阈值，|日涨跌幅| 超过此值才通知 (default 5%)
#   delta_pct    : 再次触发阈值，距上次通知价变化超过此值才通知 (default 4%)
#   portfolio_delta_pct : 组合 P&L 变化阈值 (default 2%)

DEFAULT_TRIGGER_PCT = 5.0         # 首次触发: |日涨跌幅| >= 5%
DEFAULT_DELTA_PCT = 4.0           # 再次触发: 距上次通知价变化 >= 4%
DEFAULT_PORTFOLIO_DELTA_PCT = 2.0  # 组合: P&L 变化 >= 2%


class DeltaAlertEngine:
    """变化驱动的告警引擎。

    替代 AlertEngine + PortfolioAlertEngine，合并同一只股票的
    big_move / P&L / threshold 为一条通知，避免重复。

    每只股票追踪 ``last_notified_price``，只在价格发生显著变化时
    才再次通知。涨停/跌停不会反复弹窗。
    """

    def __init__(self, config: dict):
        self.config = config
        # {symbol: {"price": float, "change_pct": float}}
        self._notified: dict[str, dict] = {}
        # portfolio: last notified total daily P&L
        self._last_portfolio_pnl: float | None = None

    def reset(self):
        """Midnight reset — clear all tracking state."""
        self._notified.clear()
        self._last_portfolio_pnl = None

    def check(
        self,
        quotes: dict,
        hkd_cny_rate: float | None,
    ) -> list[dict]:
        """Run all alert checks. Returns list of {title, message, symbol, _kind, _stealth, _change_pct}."""
        config = self.config
        watchlist = config.get("watchlist", {})
        settings = config.get("settings", {})

        trigger_pct = settings.get("trigger_pct", DEFAULT_TRIGGER_PCT)
        delta_pct = settings.get("delta_pct", DEFAULT_DELTA_PCT)
        portfolio_delta_pct = settings.get("portfolio_delta_pct", DEFAULT_PORTFOLIO_DELTA_PCT)

        alerts: list[dict] = []
        total_daily_pnl = 0.0
        total_market_value = 0.0
        holdings_counted = 0

        for symbol, quote in quotes.items():
            entry = watchlist.get(symbol, {})
            price = quote.get("price", 0)
            change_pct = quote.get("change_pct", 0)
            chg_amt = quote.get("chg_amt", 0)
            name = quote.get("name", symbol)

            if price <= 0:
                continue

            is_holding = entry.get("type") == "holding"
            cost = entry.get("cost")
            shares = entry.get("shares")
            has_position = is_holding and cost and shares and cost > 0 and shares > 0

            # ── Accumulate portfolio P&L ──
            if has_position:
                is_hk = is_hk_symbol(symbol)
                fx = hkd_cny_rate if is_hk else 1.0
                if fx is not None and fx > 0:
                    total_daily_pnl += chg_amt * shares * fx
                    total_market_value += price * shares * fx
                    holdings_counted += 1

            # ── Should we notify for this stock? ──
            reasons: list[str] = []
            prev = self._notified.get(symbol)

            # Check threshold breach (above/below) — all stocks
            above = entry.get("above")
            below = entry.get("below")
            if above is not None and price >= above:
                reasons.append("threshold")
            if below is not None and price <= below:
                reasons.append("threshold")

            if prev is None:
                # ── First notification: need |daily change%| >= trigger_pct ──
                if is_holding and abs(change_pct) >= trigger_pct:
                    reasons.append("big_move")
                if not reasons:
                    continue
            else:
                # ── Re-trigger: need price delta >= delta_pct from last notified ──
                prev_price = prev["price"]
                if prev_price > 0:
                    delta = abs(price - prev_price) / prev_price * 100
                    if delta >= delta_pct:
                        reasons.append("big_move")
                if not reasons:
                    continue

            # ── Record and build alert ──
            self._notified[symbol] = {"price": price, "change_pct": change_pct}
            kind = "threshold" if "threshold" in reasons else "big_move"

            # Ultra-concise: name change% price
            sign = "+" if change_pct >= 0 else ""
            title = f"{name} {sign}{change_pct:.1f}%"
            message = f"{price:.2f}"
            if "threshold" in reasons:
                message += " !"

            # Stealth line (no monetary values either)
            stealth_extra = f"{change_pct:+.1f}%"
            if "threshold" in reasons:
                stealth_extra += " threshold"

            alerts.append({
                "symbol": symbol,
                "title": title,
                "message": message,
                "_kind": kind,
                "_change_pct": change_pct,
                "_stealth": _stealth_line(name, change_pct, stealth_extra),
            })

        # ── Portfolio summary ──
        if holdings_counted > 0 and total_market_value > 0:
            portfolio_pct = (total_daily_pnl / total_market_value) * 100

            should_notify = False
            if self._last_portfolio_pnl is None:
                # First time: only if significant
                if abs(portfolio_pct) >= DEFAULT_TRIGGER_PCT:
                    should_notify = True
            else:
                # Delta from last notification
                pnl_delta = abs(total_daily_pnl - self._last_portfolio_pnl)
                pnl_delta_pct = (pnl_delta / total_market_value) * 100 if total_market_value > 0 else 0
                if pnl_delta_pct >= portfolio_delta_pct:
                    should_notify = True

            if should_notify:
                self._last_portfolio_pnl = total_daily_pnl
                pct_sign = "+" if portfolio_pct >= 0 else ""
                alerts.append({
                    "symbol": "",
                    "title": f"组合 {pct_sign}{portfolio_pct:.1f}%",
                    "message": f"{holdings_counted} stocks",
                    "_kind": "portfolio",
                    "_change_pct": portfolio_pct,
                    "_stealth": f"portfolio {pct_sign}{portfolio_pct:.1f}%",
                })

        return alerts


# ══════════════════════════════════════════
# 5. Stealth notification layer
# ══════════════════════════════════════════
#
# 伪装成 CI/监控系统通知，同事路过看到也只觉得是工作消息。
# 多条告警合并为 1 条弹窗，减少打扰。
#
# 对照表 (只有你自己知道):
#   SVC-159516  = 半导设备ETF     crit = 大跌    ok = 大涨
#   SVC-HK09988 = 阿里巴巴        threshold = 触价
#   blocked     = 跌               resolved = 涨
#   net: -1,765 = 今日盈亏        coverage = 涨跌幅
#

# 标题轮换，避免每次都一样（更自然）
_STEALTH_TITLES_ALERT = [
    "CI Pipeline Alert",
    "Deploy Monitor",
    "SRE Notification",
    "Build Status",
]

_STEALTH_TITLES_SUMMARY = [
    "Daily Standup Notes",
    "Sprint Report",
    "Weekly Metrics",
    "Team Dashboard",
]


def _stealth_title(is_summary: bool = False) -> str:
    """Pick a stealth title based on current minute for consistency within a cycle."""
    titles = _STEALTH_TITLES_SUMMARY if is_summary else _STEALTH_TITLES_ALERT
    idx = datetime.now().minute % len(titles)
    return titles[idx]


def _stealth_line(name: str, change_pct: float, extra: str = "") -> str:
    """Format one stock as a concise monitor line using stock name."""
    if extra:
        return f"{name}: {extra}"
    if change_pct <= -7:
        return f"{name}: crit {change_pct:+.1f}%"
    elif change_pct <= -4:
        return f"{name}: warn {change_pct:+.1f}%"
    elif change_pct >= 7:
        return f"{name}: ok {change_pct:+.1f}%"
    elif change_pct >= 4:
        return f"{name}: info {change_pct:+.1f}%"
    else:
        return f"{name}: {change_pct:+.1f}%"


def stealth_dispatch(alerts: list[dict], *, sound: str = ""):
    """Batch alerts into 1~2 stealth notifications.

    Rules:
    - 0 alerts: do nothing
    - 1~3 alerts: 1 notification, each alert a line
    - 4+ alerts: 1 notification, top 3 + "and N more"
    - Portfolio summary is always a separate notification
    - No sound by default (discreet); sound="default" for critical only
    """
    if not alerts:
        return 0

    # Separate portfolio-level from per-stock alerts
    portfolio_alerts = [a for a in alerts if a.get("_kind") == "portfolio"]
    stock_alerts = [a for a in alerts if a.get("_kind") != "portfolio"]
    sent = 0

    # ── Per-stock alerts → 1 notification ──
    if stock_alerts:
        # Sort by severity: threshold > pnl > big_move
        priority = {"threshold": 0, "pnl": 1, "big_move": 2}
        stock_alerts.sort(key=lambda a: priority.get(a.get("_kind", ""), 9))

        lines = []
        for a in stock_alerts[:3]:
            lines.append(a.get("_stealth", a["message"]))
        if len(stock_alerts) > 3:
            lines.append(f"... and {len(stock_alerts) - 3} more")

        # Critical if any threshold breach or change > 8%
        has_critical = any(
            a.get("_kind") == "threshold" or abs(a.get("_change_pct", 0)) >= 8
            for a in stock_alerts
        )

        notify(
            _stealth_title(is_summary=False),
            " | ".join(lines),
            sound="default" if has_critical else sound,
        )
        sent += 1

    # ── Portfolio summary → separate notification (no sound) ──
    if portfolio_alerts:
        a = portfolio_alerts[0]
        notify(
            _stealth_title(is_summary=True),
            a.get("_stealth", a["message"]),
            sound="",
        )
        sent += 1

    return sent


def stealth_dispatch_open_close(alerts: list[dict]):
    """Dispatch open/close notifications in stealth mode."""
    for a in alerts:
        title = a.get("title", "")
        if "开盘" in title:
            notify("Sprint Started", a.get("_stealth", f"Standup in 5 min — {a['message']}"), sound="")
        elif "收盘" in title:
            notify("Daily Report", a.get("_stealth", a["message"]), sound="")
        else:
            notify(_stealth_title(), a.get("_stealth", a["message"]), sound="")


# ══════════════════════════════════════════
# 5b. Status line
# ══════════════════════════════════════════

def print_status(checked: int, alert_count: int, next_sec: int, trading: bool):
    """Print a compact single-line status, overwriting the previous one."""
    now = datetime.now().strftime("%H:%M:%S")
    if trading:
        line = f"[{now}] checked {checked} stocks | alerts today: {alert_count} | next: {next_sec}s"
    else:
        line = f"[{now}] non-trading hours | alerts today: {alert_count} | next check: {next_sec}s"
    # \r + \033[K = overwrite current line
    print(f"\r\033[K{line}", end="", flush=True)


# ══════════════════════════════════════════
# 5b. Market open/close notifications
# ══════════════════════════════════════════

def _count_watchlist(watchlist: dict) -> tuple[int, int]:
    """Count holdings and watching stocks (non-hidden).

    Returns (num_holdings, num_watching).
    """
    holdings = 0
    watching = 0
    for entry in watchlist.values():
        if entry.get("hidden", False):
            continue
        if entry.get("type") == "holding" and entry.get("cost") and entry.get("shares"):
            holdings += 1
        else:
            watching += 1
    return holdings, watching


def _build_close_summary(
    quotes: dict,
    config: dict,
    hkd_cny_rate: float | None,
) -> str:
    """Build a multi-line market close summary message.

    Computes:
    - Total portfolio daily P&L (CNY, %)
    - Top 3 gainers and top 3 losers among holdings
    - Stocks that hit above/below thresholds during the session
    """
    watchlist = config.get("watchlist", {})

    # ── Collect holding data ──
    holdings_data: list[dict] = []
    total_daily_pnl = 0.0
    total_market_value = 0.0

    for symbol, quote in quotes.items():
        entry = watchlist.get(symbol, {})
        if entry.get("type") != "holding":
            continue
        cost = entry.get("cost")
        shares = entry.get("shares")
        if not cost or not shares or cost <= 0 or shares <= 0:
            continue

        price = quote.get("price", 0)
        change_pct = quote.get("change_pct", 0)
        chg_amt = quote.get("chg_amt", 0)
        name = quote.get("name", symbol)

        if price <= 0:
            continue

        is_hk = is_hk_symbol(symbol)
        fx = hkd_cny_rate if is_hk else 1.0

        daily_pnl_native = chg_amt * shares
        if fx is not None and fx > 0:
            daily_pnl_cny = daily_pnl_native * fx
            market_value_cny = price * shares * fx
        else:
            daily_pnl_cny = daily_pnl_native  # fallback: treat as 1:1
            market_value_cny = price * shares

        total_daily_pnl += daily_pnl_cny
        total_market_value += market_value_cny

        holdings_data.append({
            "symbol": symbol,
            "name": name,
            "change_pct": change_pct,
            "daily_pnl_cny": daily_pnl_cny,
        })

    # ── Threshold hits ──
    threshold_hits: list[str] = []
    for symbol, quote in quotes.items():
        entry = watchlist.get(symbol, {})
        price = quote.get("price", 0)
        name = quote.get("name", symbol)
        if price <= 0:
            continue
        above = entry.get("above")
        below = entry.get("below")
        if above and price >= above:
            threshold_hits.append(f"  {name} 突破上限 {above}（现价 {price:.2f}）")
        if below and price <= below:
            threshold_hits.append(f"  {name} 跌破下限 {below}（现价 {price:.2f}）")

    # ── Build message lines ──
    lines: list[str] = []

    if holdings_data and total_market_value > 0:
        portfolio_pct = (total_daily_pnl / total_market_value) * 100
        sign = "+" if total_daily_pnl >= 0 else ""
        pct_sign = "+" if portfolio_pct >= 0 else ""
        lines.append(f"持仓盈亏: {sign}{total_daily_pnl:,.0f}元 ({pct_sign}{portfolio_pct:.2f}%)")
    else:
        lines.append("持仓盈亏: 无持仓数据")

    # Top gainers / losers (sorted by change_pct)
    if holdings_data:
        sorted_by_change = sorted(holdings_data, key=lambda x: x["change_pct"], reverse=True)

        gainers = [h for h in sorted_by_change if h["change_pct"] > 0][:3]
        losers = [h for h in reversed(sorted_by_change) if h["change_pct"] < 0][:3]

        if gainers:
            parts = [f"{h['name']}({h['change_pct']:+.1f}%)" for h in gainers]
            lines.append(f"领涨: {', '.join(parts)}")
        if losers:
            parts = [f"{h['name']}({h['change_pct']:+.1f}%)" for h in losers]
            lines.append(f"领跌: {', '.join(parts)}")

    if threshold_hits:
        lines.append("触发阈值:")
        lines.extend(threshold_hits)

    return "\n".join(lines)


def check_market_open_close(
    *,
    sent_open: bool,
    sent_close: bool,
    config: dict,
    quotes: dict | None,
    hkd_cny_rate: float | None,
) -> tuple[bool, bool, list[dict]]:
    """Check if market open/close notifications should be sent.

    Only fires for A-share market (9:30 open, 15:01 close).
    Returns updated (sent_open, sent_close, alerts_to_send).
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return sent_open, sent_close, []

    t = now.hour * 100 + now.minute
    watchlist = config.get("watchlist", {})
    alerts: list[dict] = []

    # ── Market open notification: 09:30 ~ 09:45 window ──
    if not sent_open and 930 <= t <= 945:
        num_holdings, num_watching = _count_watchlist(watchlist)
        alerts.append({
            "title": "开盘",
            "message": f"A股开盘 | 关注: {num_holdings}只持仓, {num_watching}只自选",
            "_stealth": f"Tracking {num_holdings} prod + {num_watching} staging services",
        })
        sent_open = True

    # ── Market close notification: 15:01 ~ 15:15 window ──
    if not sent_close and 1501 <= t <= 1515 and quotes:
        summary = _build_close_summary(quotes, config, hkd_cny_rate)
        alerts.append({
            "title": "收盘",
            "message": summary,
            "_stealth": summary,  # close summary is already compact enough
        })
        sent_close = True

    return sent_open, sent_close, alerts


# ══════════════════════════════════════════
# 6. Main loop
# ══════════════════════════════════════════

def run():
    """Main notification daemon loop."""
    # ── Graceful shutdown ──
    running = True

    def _handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Startup banner ──
    config = read_json_safe(MONITOR_CONFIG_PATH)
    if config is None:
        print(f"ERROR: cannot read {MONITOR_CONFIG_PATH}")
        sys.exit(1)

    settings = config.get("settings", {})
    watchlist = config.get("watchlist", {})
    has_hk = any(is_hk_symbol(s) for s in watchlist)
    num_holdings = sum(
        1 for e in watchlist.values()
        if e.get("type") == "holding" and e.get("cost") and e.get("shares")
    )

    print("Stock Notifier started (delta mode)")
    print(f"  watchlist : {len(watchlist)} stocks ({sum(1 for s in watchlist if is_hk_symbol(s))} HK)")
    print(f"  holdings  : {num_holdings} with cost/shares (P&L tracking)")
    print(f"  trigger   : +/-{settings.get('trigger_pct', DEFAULT_TRIGGER_PCT)}% (首次触发)")
    print(f"  delta     : +/-{settings.get('delta_pct', DEFAULT_DELTA_PCT)}% (再次触发需价格变化)")
    print(f"  portfolio : +/-{settings.get('portfolio_delta_pct', DEFAULT_PORTFOLIO_DELTA_PCT)}% (组合变化)")
    print(f"  data file : {MARKET_DATA_PATH.name}")
    print(f"  Ctrl+C to stop\n")

    # ── State ──
    last_mtime = 0.0
    daily_alerts = 0
    last_alert_date = datetime.now().date()
    last_checked_count = len(watchlist)
    engine = DeltaAlertEngine(config)
    sent_open_today = False
    sent_close_today = False
    latest_quotes: dict | None = None       # last merged quotes (for close summary)
    latest_hkd_cny_rate: float | None = None

    while running:
        # Reset daily counter at midnight
        today = datetime.now().date()
        if today != last_alert_date:
            daily_alerts = 0
            sent_open_today = False
            sent_close_today = False
            latest_quotes = None
            latest_hkd_cny_rate = None
            last_alert_date = today
            engine.reset()  # clear delta tracking for new day
            # Reload config at day boundary
            fresh_config = read_json_safe(MONITOR_CONFIG_PATH)
            if fresh_config is not None and "settings" in fresh_config and "watchlist" in fresh_config:
                config = fresh_config
                watchlist = config.get("watchlist", {})
                settings = config.get("settings", {})
                has_hk = any(is_hk_symbol(s) for s in watchlist)
                engine.config = config

        trading = is_any_market_open(has_hk)
        check_interval = TRADING_CHECK_SEC if trading else NON_TRADING_CHECK_SEC

        # Check mtime
        current_mtime = get_mtime(MARKET_DATA_PATH)

        if current_mtime > 0 and current_mtime != last_mtime:
            last_mtime = current_mtime

            # Reload config each time (cheap, picks up threshold changes)
            fresh_config = read_json_safe(MONITOR_CONFIG_PATH)
            if fresh_config is not None and "settings" in fresh_config and "watchlist" in fresh_config:
                config = fresh_config
                watchlist = config.get("watchlist", {})
                settings = config.get("settings", {})
                has_hk = any(is_hk_symbol(s) for s in watchlist)
                engine.config = config

            market = read_json_safe(MARKET_DATA_PATH)
            if market is not None:
                quotes = merge_data(market, config)
                last_checked_count = len(quotes)

                # Keep latest data for close summary (even outside trading check)
                if quotes:
                    latest_quotes = quotes
                    latest_hkd_cny_rate = market.get("hkdCnyRate")

                if trading and quotes:
                    hkd_cny_rate = market.get("hkdCnyRate")
                    all_alerts = engine.check(quotes, hkd_cny_rate)

                    if all_alerts:
                        print()
                        sent = stealth_dispatch(all_alerts)
                        daily_alerts += sent
                        for a in all_alerts:
                            logger.info(f"Alert: {a['title']} - {a['message']}")

                print_status(last_checked_count, daily_alerts, check_interval, trading)
            else:
                print_status(last_checked_count, daily_alerts, check_interval, trading)
        else:
            # No new data -- reuse last checked count for consistent display
            print_status(last_checked_count, daily_alerts, check_interval, trading)

        # ── Market open/close notifications (time-based, independent of mtime) ──
        sent_open_today, sent_close_today, oc_alerts = check_market_open_close(
            sent_open=sent_open_today,
            sent_close=sent_close_today,
            config=config,
            quotes=latest_quotes,
            hkd_cny_rate=latest_hkd_cny_rate,
        )
        if oc_alerts:
            print()
            stealth_dispatch_open_close(oc_alerts)
            daily_alerts += len(oc_alerts)
            for a in oc_alerts:
                logger.info(f"Alert: {a['title']} - {a['message']}")

        # Sleep in small increments for responsive shutdown
        slept = 0.0
        while slept < check_interval and running:
            time.sleep(min(0.5, check_interval - slept))
            slept += 0.5

    # ── Shutdown ──
    print(f"\n\nNotifier stopped. Total alerts today: {daily_alerts}")


if __name__ == "__main__":
    run()
