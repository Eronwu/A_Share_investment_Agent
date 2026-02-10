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

from src.tools.stock_monitor import AlertEngine, is_hk_symbol, notify
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
    """Build AlertEngine-compatible quotes dict from market_data + config.

    Returns: {symbol: {name, price, change_pct, chg_amt}} keyed by stock
    id/code.  The ``chg_amt`` field (absolute price change today) is carried
    through so downstream engines (e.g. PortfolioAlertEngine) can compute
    daily P&L without re-deriving it.
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
# 4. Portfolio / P&L alert engine
# ══════════════════════════════════════════

# Default thresholds (used when settings keys are absent from config)
DEFAULT_HOLDING_ALERT_PCT = 5.0   # per-stock daily change % to trigger alert
DEFAULT_PORTFOLIO_ALERT_PCT = 3.0  # portfolio-level daily change % to trigger


class PortfolioAlertEngine:
    """Generates alerts based on holding P&L.

    Two kinds of alert:
    1. **Individual holding** -- fires when a holding's daily change%
       exceeds ``holding_alert_pct`` (absolute value).
    2. **Portfolio summary** -- fires when the total portfolio daily P&L,
       expressed as a percentage of total market value, exceeds
       ``portfolio_alert_pct`` (absolute value).

    Uses the same ``cooldown_minutes`` as other alert engines, but with
    distinct cooldown keys so it does not interfere with price/big-move
    alerts.
    """

    def __init__(self, config: dict):
        self.config = config
        self.cooldowns: dict[str, float] = {}

    # ── helpers ──

    def _cooldown_sec(self) -> int:
        return self.config.get("settings", {}).get("cooldown_minutes", 10) * 60

    def _is_cooled_down(self, key: str) -> bool:
        last = self.cooldowns.get(key, 0)
        return (time.time() - last) >= self._cooldown_sec()

    def _trigger(self, key: str):
        self.cooldowns[key] = time.time()

    # ── main entry ──

    def check(self, quotes: dict, hkd_cny_rate: float | None) -> list[dict]:
        """Check portfolio-level and per-holding P&L alerts.

        Args:
            quotes: merged dict from ``merge_data`` -- must include
                    ``chg_amt`` per symbol.
            hkd_cny_rate: HKD->CNY conversion rate from market_data.json.
                          ``None`` means HK P&L cannot be converted; those
                          holdings are skipped for CNY amounts but still
                          checked for %-based alerts.

        Returns:
            list of ``{title, message}`` dicts suitable for ``notify()``.
        """
        watchlist = self.config.get("watchlist", {})
        settings = self.config.get("settings", {})
        holding_alert_pct = settings.get("holding_alert_pct", DEFAULT_HOLDING_ALERT_PCT)
        portfolio_alert_pct = settings.get("portfolio_alert_pct", DEFAULT_PORTFOLIO_ALERT_PCT)

        alerts: list[dict] = []
        total_daily_pnl = 0.0
        total_market_value = 0.0
        holdings_counted = 0

        for symbol, quote in quotes.items():
            entry = watchlist.get(symbol, {})

            # Only care about holdings with cost & shares
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

            # Daily P&L in native currency: chgAmt * shares
            daily_pnl_native = chg_amt * shares

            # Convert to CNY
            if fx is not None and fx > 0:
                daily_pnl_cny = daily_pnl_native * fx
                market_value_cny = price * shares * fx
            else:
                # Cannot convert -- skip CNY accumulation but still check %
                daily_pnl_cny = None
                market_value_cny = None

            # Accumulate for portfolio total (only if we have CNY values)
            if daily_pnl_cny is not None and market_value_cny is not None:
                total_daily_pnl += daily_pnl_cny
                total_market_value += market_value_cny
                holdings_counted += 1

            # ── Per-holding alert (%-based) ──
            if abs(change_pct) >= holding_alert_pct:
                key = f"pnl_{symbol}_holding"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    # Format P&L string
                    if daily_pnl_cny is not None:
                        pnl_str = f"{'+'if daily_pnl_cny >= 0 else ''}{daily_pnl_cny:,.0f}"
                        pnl_detail = f"，今日盈亏 {pnl_str} 元"
                    else:
                        pnl_detail = ""
                    direction = "大涨" if change_pct > 0 else "大跌"
                    sign = "+" if change_pct >= 0 else ""
                    alerts.append({
                        "title": f"{'📈' if change_pct > 0 else '📉'} 持仓{direction} {name}",
                        "message": (
                            f"{name}({symbol}) 今日 {sign}{change_pct:.1f}%"
                            f"，现价 {price:.2f}{pnl_detail}"
                        ),
                    })

        # ── Portfolio summary alert ──
        if holdings_counted > 0 and total_market_value > 0:
            portfolio_change_pct = (total_daily_pnl / total_market_value) * 100
            if abs(portfolio_change_pct) >= portfolio_alert_pct:
                key = "pnl_portfolio_total"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    sign = "+" if total_daily_pnl >= 0 else ""
                    pnl_str = f"{sign}{total_daily_pnl:,.0f}"
                    if total_daily_pnl >= 0:
                        title = f"📊 今日持仓总盈利 {pnl_str} 元"
                    else:
                        title = f"📊 今日持仓总亏损 {pnl_str} 元"
                    pct_sign = "+" if portfolio_change_pct >= 0 else ""
                    alerts.append({
                        "title": title,
                        "message": (
                            f"持仓组合 {holdings_counted} 只标的，"
                            f"今日整体 {pct_sign}{portfolio_change_pct:.2f}%，"
                            f"盈亏 {pnl_str} 元"
                        ),
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


def _stealth_line(symbol: str, change_pct: float, extra: str = "") -> str:
    """Format one stock as a service monitor line.

    Examples:
        SVC-159516: crit -10.0%
        SVC-9988: ok +5.2% (resolved)
        SVC-159326: threshold breach
    """
    # 港股去掉 HK 前缀但保留 SVC- 前缀
    svc_id = f"SVC-{symbol.upper().removeprefix('HK')}"
    if extra:
        return f"{svc_id}: {extra}"
    if change_pct <= -7:
        return f"{svc_id}: crit {change_pct:+.1f}%"
    elif change_pct <= -4:
        return f"{svc_id}: warn {change_pct:+.1f}%"
    elif change_pct >= 7:
        return f"{svc_id}: ok {change_pct:+.1f}%"
    elif change_pct >= 4:
        return f"{svc_id}: info {change_pct:+.1f}%"
    else:
        return f"{svc_id}: {change_pct:+.1f}%"


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
# 5b. Alert enrichment (stealth + filter)
# ══════════════════════════════════════════

def _enrich_alerts(
    raw_alerts: list[dict],
    quotes: dict,
    watchlist: dict,
) -> list[dict]:
    """Enrich AlertEngine results with stealth metadata & filter.

    Filtering rules (减少打扰):
    - 大涨大跌(big_move): 只通知持仓，自选股不通知
    - 触价(above/below): 所有股都通知（用户明确设定了阈值）
    """
    enriched = []
    for a in raw_alerts:
        symbol = a.get("symbol", "")
        title = a.get("title", "")
        entry = watchlist.get(symbol, {})
        q = quotes.get(symbol, {})
        change_pct = q.get("change_pct", 0)

        # Classify alert kind
        if "突破上限" in title or "跌破下限" in title:
            kind = "threshold"
        elif "大涨" in title or "大跌" in title:
            kind = "big_move"
        else:
            kind = "other"

        # Filter: big_move only for holdings
        if kind == "big_move" and entry.get("type") != "holding":
            continue

        # Build stealth line
        if kind == "threshold":
            if "上限" in title:
                stealth = _stealth_line(symbol, change_pct, "threshold breach (above)")
            else:
                stealth = _stealth_line(symbol, change_pct, "threshold breach (below)")
        else:
            stealth = _stealth_line(symbol, change_pct)

        a["_stealth"] = stealth
        a["_kind"] = kind
        a["_change_pct"] = change_pct
        enriched.append(a)

    return enriched


def _enrich_pnl_alerts(pnl_alerts: list[dict]) -> list[dict]:
    """Enrich PortfolioAlertEngine results with stealth metadata."""
    for a in pnl_alerts:
        title = a.get("title", "")
        msg = a.get("message", "")

        if "总盈利" in title or "总亏损" in title:
            # Portfolio summary
            # Extract net amount from title
            a["_kind"] = "portfolio"
            a["_stealth"] = f"net: {title.split('元')[0].split(' ')[-1]} | {msg.split('，')[1] if '，' in msg else msg}"
        else:
            # Individual holding P&L
            a["_kind"] = "pnl"
            # Parse: "XX(159516) 今日 -6.2%，现价 1.68，今日盈亏 -255 元"
            # → "SVC-159516: warn -6.2% (impact: -255)"
            symbol = ""
            change_pct = 0.0
            pnl_part = ""
            if "(" in msg and ")" in msg:
                symbol = msg.split("(")[1].split(")")[0]
            if "今日 " in msg:
                try:
                    pct_str = msg.split("今日 ")[1].split("%")[0]
                    change_pct = float(pct_str)
                except (ValueError, IndexError):
                    pass
            if "盈亏" in msg:
                pnl_part = msg.split("盈亏 ")[1].rstrip(" 元").strip() if "盈亏 " in msg else ""

            if pnl_part:
                a["_stealth"] = _stealth_line(symbol, change_pct, f"{change_pct:+.1f}% (impact: {pnl_part})")
            else:
                a["_stealth"] = _stealth_line(symbol, change_pct)
            a["_change_pct"] = change_pct

    return pnl_alerts


# ══════════════════════════════════════════
# 5c. Status line
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

    print("Stock Notifier started")
    print(f"  watchlist : {len(watchlist)} stocks ({sum(1 for s in watchlist if is_hk_symbol(s))} HK)")
    print(f"  holdings  : {num_holdings} with cost/shares (P&L tracking)")
    print(f"  big_move  : +/-{settings.get('big_move_pct', 3)}%")
    print(f"  holding   : +/-{settings.get('holding_alert_pct', DEFAULT_HOLDING_ALERT_PCT)}% (per-stock P&L)")
    print(f"  portfolio : +/-{settings.get('portfolio_alert_pct', DEFAULT_PORTFOLIO_ALERT_PCT)}% (total P&L)")
    print(f"  cooldown  : {settings.get('cooldown_minutes', 10)} min")
    print(f"  data file : {MARKET_DATA_PATH.name}")
    print(f"  Ctrl+C to stop\n")

    # ── State ──
    last_mtime = 0.0
    daily_alerts = 0
    last_alert_date = datetime.now().date()
    last_checked_count = len(watchlist)
    engine = AlertEngine(config)
    pnl_engine = PortfolioAlertEngine(config)
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
            # Reload config at day boundary (picks up watchlist changes)
            # Use engine.config assignment to preserve cooldown state
            fresh_config = read_json_safe(MONITOR_CONFIG_PATH)
            if fresh_config is not None and "settings" in fresh_config and "watchlist" in fresh_config:
                config = fresh_config
                watchlist = config.get("watchlist", {})
                settings = config.get("settings", {})
                has_hk = any(is_hk_symbol(s) for s in watchlist)
                engine.config = config
                pnl_engine.config = config

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
                pnl_engine.config = config

            market = read_json_safe(MARKET_DATA_PATH)
            if market is not None:
                quotes = merge_data(market, config)
                last_checked_count = len(quotes)

                # Keep latest data for close summary (even outside trading check)
                if quotes:
                    latest_quotes = quotes
                    latest_hkd_cny_rate = market.get("hkdCnyRate")

                if trading and quotes:
                    # 1) Price threshold + big-move alerts (only holdings)
                    all_alerts = _enrich_alerts(
                        engine.check(quotes), quotes, watchlist,
                    )

                    # 2) Portfolio / P&L alerts
                    hkd_cny_rate = market.get("hkdCnyRate")
                    pnl_alerts = pnl_engine.check(quotes, hkd_cny_rate)
                    all_alerts.extend(_enrich_pnl_alerts(pnl_alerts))

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
