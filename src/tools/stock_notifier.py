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
from datetime import datetime, timedelta
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
ALERT_CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "alert_config.json"
L2_SIGNALS_PATH = PROJECT_ROOT / "src" / "data" / "l2_strategy_signals.json"

ARCHIVE_DIR = PROJECT_ROOT / "src" / "data" / "archive"

# ── Poll intervals ──
TRADING_CHECK_SEC = 3      # mtime check interval during trading hours
NON_TRADING_CHECK_SEC = 60  # mtime check interval outside trading hours


def _archive_and_reset(today):
    """归档昨日 market_data + l2_strategy_signals，清理 30 天前 alert_events。

    归档文件命名: archive/market_data_2026-02-12.json
    l2_strategy_signals 每 3s 覆盖，session 上下文（资金流、盘口）不入 DB，
    必须每日归档保留完整数据用于量化回测。
    """
    import shutil
    yesterday = (today - timedelta(days=1)).isoformat()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # 归档 market_data.json
    try:
        if MARKET_DATA_PATH.exists():
            dest = ARCHIVE_DIR / f"market_data_{yesterday}.json"
            if not dest.exists():
                shutil.copy2(MARKET_DATA_PATH, dest)
                logger.info(f"归档: {MARKET_DATA_PATH.name} → archive/{dest.name}")
    except Exception as e:
        logger.warning(f"归档 market_data 失败: {e}")

    # 归档 l2_strategy_signals.json（session 上下文仅存于此文件，不归档则丢失）
    try:
        if L2_SIGNALS_PATH.exists():
            dest = ARCHIVE_DIR / f"l2_strategy_signals_{yesterday}.json"
            if not dest.exists():
                shutil.copy2(L2_SIGNALS_PATH, dest)
                logger.info(f"归档: {L2_SIGNALS_PATH.name} → archive/{dest.name}")
    except Exception as e:
        logger.warning(f"归档 l2_strategy_signals 失败: {e}")

    # 归档 daily_summary.json（LLM 日报每日覆盖，不归档则丢失）
    try:
        summary_path = PROJECT_ROOT / "src" / "data" / "daily_summary.json"
        if summary_path.exists():
            dest = ARCHIVE_DIR / f"daily_summary_{yesterday}.json"
            if not dest.exists():
                shutil.copy2(summary_path, dest)
                logger.info(f"归档: {summary_path.name} → archive/{dest.name}")
    except Exception as e:
        logger.warning(f"归档 daily_summary 失败: {e}")

    # 清理 90 天前的归档文件
    try:
        cutoff_date = (today - timedelta(days=90)).isoformat()
        removed = 0
        for f in ARCHIVE_DIR.glob("*_????-??-??.json"):
            # 提取文件名中的日期: xxx_2026-02-12.json → 2026-02-12
            date_str = f.stem.rsplit("_", 1)[-1]
            if len(date_str) == 10 and date_str < cutoff_date:
                f.unlink()
                removed += 1
        if removed:
            logger.info(f"清理归档: 删除 {removed} 个 90 天前文件")
    except Exception as e:
        logger.warning(f"清理归档失败: {e}")

    # 清理 SQLite 过期数据
    conn = None
    try:
        from src.sim_trading.db import get_connection
        conn = get_connection()
        cutoff_30d = (today - timedelta(days=30)).isoformat()
        cutoff_180d = (today - timedelta(days=180)).isoformat()

        # alert_events: 30 天
        d1 = conn.execute("DELETE FROM alert_events WHERE date < ?", (cutoff_30d,)).rowcount
        # signals / price_snapshots / session_snapshots: 180 天
        d2 = conn.execute("DELETE FROM signals WHERE date < ?", (cutoff_180d,)).rowcount
        d3 = conn.execute("DELETE FROM price_snapshots WHERE date < ?", (cutoff_180d,)).rowcount
        d4 = conn.execute("DELETE FROM session_snapshots WHERE date < ?", (cutoff_180d,)).rowcount

        conn.commit()
        total = d1 + d2 + d3 + d4
        if total:
            logger.info(f"SQLite 清理: alert_events -{d1}, signals -{d2}, price_snap -{d3}, session_snap -{d4}")
    except Exception as e:
        logger.warning(f"SQLite 清理失败: {e}")
    finally:
        if conn:
            conn.close()


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
            "amount": svc.get("amount", 0),
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

DEFAULT_PORTFOLIO_DELTA_PCT = 2.0  # 组合: P&L 变化 >= 2%

# ── 分级通知策略表（配置驱动，可扩展） ──
NOTIFY_POLICIES = {
    1: {"trigger_pct": 4, "delta_pct": 3, "cooldown_min": 5,
        "big_move": True, "threshold": True, "dispatch": "sound"},
    2: {"trigger_pct": 6, "delta_pct": 5, "cooldown_min": 15,
        "big_move": True, "threshold": True, "dispatch": "silent"},
    3: {"trigger_pct": None, "delta_pct": None, "cooldown_min": 30,
        "big_move": False, "threshold": True, "dispatch": "web_only"},
    4: {"trigger_pct": None, "delta_pct": None, "cooldown_min": None,
        "big_move": False, "threshold": False, "dispatch": "none"},
}


def resolve_level(entry: dict) -> int:
    """从 watchlist entry 推导通知级别"""
    if entry.get("hidden"):
        return 4
    if entry.get("star"):
        return 1
    if entry.get("type") == "holding":
        return 2
    return 3


def get_policy(level: int, settings: dict) -> dict:
    """获取级别策略，合并用户覆盖"""
    base = dict(NOTIFY_POLICIES.get(level, NOTIFY_POLICIES[4]))
    # 用户可通过 settings 覆盖: l1_trigger_pct, l2_delta_pct, etc.
    prefix = f"l{level}_"
    for key in ("trigger_pct", "delta_pct", "cooldown_min"):
        override = settings.get(f"{prefix}{key}")
        if override is not None:
            base[key] = override
    return base


class DeltaAlertEngine:
    """变化驱动的告警引擎。

    替代 AlertEngine + PortfolioAlertEngine，合并同一只股票的
    big_move / P&L / threshold 为一条通知，避免重复。

    每只股票追踪 ``last_notified_price``，只在价格发生显著变化时
    才再次通知。涨停/跌停不会反复弹窗。
    """

    def __init__(self, config: dict):
        self.config = config
        self._alerts: dict = {}
        self._reload_alerts()
        # {symbol: {"price": float, "change_pct": float}}
        self._notified: dict[str, dict] = {}
        # portfolio: last notified total daily P&L
        self._last_portfolio_pnl: float | None = None

    def _reload_alerts(self):
        """从 alert_config.json 加载告警规则"""
        try:
            with open(ALERT_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._alerts = data.get("alerts", {})
        except Exception:
            self._alerts = {}

    def reset(self):
        """Midnight reset — clear all tracking state."""
        self._notified.clear()
        self._last_portfolio_pnl = None

    def check(
        self,
        quotes: dict,
        hkd_cny_rate: float | None,
    ) -> list[dict]:
        """Run all alert checks with tiered notification policies.

        Returns list of alert dicts, each with _level for dispatch routing.
        """
        self._reload_alerts()
        config = self.config
        watchlist = config.get("watchlist", {})
        settings = config.get("settings", {})
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

            # ── Resolve level and policy ──
            level = resolve_level(entry)
            policy = get_policy(level, settings)

            if policy["dispatch"] == "none":
                # L4: still accumulate P&L but skip notification
                pass

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

            if policy["dispatch"] == "none":
                continue

            # ── Should we notify? ──
            reasons: list[str] = []
            prev = self._notified.get(symbol)

            # Threshold check (above/below)
            threshold_hit = False
            if policy["threshold"]:
                alert_entry = self._alerts.get(symbol, {})
                above = alert_entry.get("above")
                below = alert_entry.get("below")
                if above is not None and price >= above:
                    threshold_hit = True
                if below is not None and price <= below:
                    threshold_hit = True

            trigger_pct = policy["trigger_pct"]
            delta_pct = policy["delta_pct"]

            if prev is None:
                # ── First notification ──
                if threshold_hit:
                    reasons.append("threshold")
                elif policy["big_move"] and trigger_pct and abs(change_pct) >= trigger_pct:
                    reasons.append("big_move")
                if not reasons:
                    continue
            else:
                # ── Re-trigger: price delta >= delta_pct ──
                prev_price = prev["price"]
                if prev_price > 0 and delta_pct:
                    delta = abs(price - prev_price) / prev_price * 100
                    if delta >= delta_pct:
                        if threshold_hit:
                            reasons.append("threshold")
                        elif policy["big_move"]:
                            reasons.append("big_move")
                if not reasons:
                    continue

            # ── Record and build alert ──
            self._notified[symbol] = {"price": price, "change_pct": change_pct}
            kind = "threshold" if "threshold" in reasons else "big_move"

            sign = "+" if change_pct >= 0 else ""
            title = f"{name} {sign}{change_pct:.1f}%"
            message = f"{price:.2f}"
            if "threshold" in reasons:
                message += " !"

            stealth_extra = f"{change_pct:+.1f}%"
            if "threshold" in reasons:
                stealth_extra += " threshold"

            alerts.append({
                "symbol": symbol,
                "title": title,
                "message": message,
                "_kind": kind,
                "_level": level,
                "_change_pct": change_pct,
                "_price": price,
                "_stealth": _notify_line(name, change_pct, stealth_extra),
            })

        # ── Portfolio summary ──
        if holdings_counted > 0 and total_market_value > 0:
            portfolio_pct = (total_daily_pnl / total_market_value) * 100

            should_notify = False
            if self._last_portfolio_pnl is None:
                if abs(portfolio_pct) >= get_policy(2, settings)["trigger_pct"]:
                    should_notify = True
            else:
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
                    "_level": 2,
                    "_change_pct": portfolio_pct,
                    "_stealth": f"组合 {pct_sign}{portfolio_pct:.1f}%",
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

# 通知标题（低调，不暴露用途）
_TITLES_ALERT = ["CI Pipeline Alert", "Deploy Monitor", "SRE Notification", "Build Status"]
_TITLES_SUMMARY = ["Daily Report", "Sprint Summary"]


def _notify_title(is_summary: bool = False) -> str:
    titles = _TITLES_SUMMARY if is_summary else _TITLES_ALERT
    idx = datetime.now().minute % len(titles)
    return titles[idx]


def _notify_line(name: str, change_pct: float, extra: str = "") -> str:
    """Terminal 通知行：简短中文，比 web display 更精简"""
    sign = "+" if change_pct >= 0 else ""
    if extra:
        return f"{name} {sign}{change_pct:.1f}% {extra}"
    return f"{name} {sign}{change_pct:.1f}%"


def write_alert_events(alerts: list[dict]):
    """将告警事件写入 SQLite alert_events 表，供 web 端读取展示。

    单一数据源：notifier 计算，web 只读。确保 terminal 和 web 告警一致。
    INSERT OR IGNORE 利用 UNIQUE(ts, symbol, message) 零成本去重。
    """
    if not alerts:
        return

    from src.sim_trading.db import get_connection

    ts_base = int(time.time() * 1000)
    t = datetime.now().strftime("%H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for i, a in enumerate(alerts):
        symbol = a.get("symbol", "")
        kind = a.get("_kind", "")
        change_pct = a.get("_change_pct", 0)
        name = a.get("title", "").split(" ")[0] if a.get("title") else symbol

        # 中文可读格式
        if kind == "l2_strategy":
            display = a.get("message", f"{symbol} L2 signal")
        elif kind == "threshold":
            display = f"{symbol} {name} 触价告警 {a.get('message', '')}"
        elif kind == "portfolio":
            display = f"组合盈亏 {change_pct:+.1f}%"
        else:
            direction = "涨幅" if change_pct > 0 else "跌幅"
            p = a.get("_price", 0)
            display = f"{symbol} {name} {direction} {abs(change_pct):.1f}% 现价{p:.2f}" if p else f"{symbol} {name} {direction} {abs(change_pct):.1f}%"

        message = a.get("_stealth", a.get("message", ""))
        rows.append((ts_base + i, today, t, symbol, kind, a.get("_level", 2),
                      message, display, change_pct))

    conn = None
    try:
        conn = get_connection()
        conn.executemany(
            "INSERT OR IGNORE INTO alert_events "
            "(ts, date, time, symbol, kind, level, message, display, change_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"写入 alert_events 到 SQLite 失败: {e}")
    finally:
        if conn:
            conn.close()


# ══════════════════════════════════════════
# 5a. L2 strategy signal consumption
# ══════════════════════════════════════════

PER_STOCK_DAILY_CAP = 8  # max L2 alerts per stock per day (safety net)


def check_l2_signals() -> list[dict]:
    """读取 l2_strategy_signals.json 中未处理的信号，转换为 alert 格式。

    L2 daemon 写信号 → notifier 消费 → 统一 dispatch。
    用 lastConsumed 时间戳避免重复处理。
    Per-stock daily cap: 超过 PER_STOCK_DAILY_CAP 的信号不写入 alert_events。
    """
    data = read_json_safe(L2_SIGNALS_PATH)
    if data is None:
        return []

    signals = data.get("signals", [])
    if not signals:
        return []

    # 只取本次新增的（ts > _l2_last_consumed）
    new_signals = [s for s in signals if s.get("ts", 0) > check_l2_signals._last_consumed]
    if not new_signals:
        return []

    # 更新消费位点
    check_l2_signals._last_consumed = max(s.get("ts", 0) for s in new_signals)

    # 转换为 notifier alert 格式, applying per-stock daily cap
    alerts = []
    for s in new_signals:
        strategy = s.get("strategy", "")
        display = s.get("display", "")
        message = s.get("message", "")
        code = s.get("code", "")
        should_notify = s.get("notify", False)

        # Per-stock daily cap check
        count = check_l2_signals._daily_counts.get(code, 0)
        if count >= PER_STOCK_DAILY_CAP:
            continue
        check_l2_signals._daily_counts[code] = count + 1

        alerts.append({
            "symbol": code,
            "title": f"L2 {strategy}",
            "message": display,
            "_kind": "l2_strategy",
            "_change_pct": 0,
            "_stealth": message,
            "_notify": should_notify,
        })

    return alerts


# Initialize consumption watermark and daily counters
check_l2_signals._last_consumed = 0
check_l2_signals._daily_counts = {}  # {code: count} — reset daily at 08:00


def stealth_dispatch(alerts: list[dict], *, sound: str = ""):
    """Batch alerts into 1~2 stealth notifications.

    Rules:
    - 0 alerts: do nothing
    - 1~2 alerts: 1 notification, each alert a line
    - 3+ alerts: 1 notification, top 2 + "+N more"
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
        # Sort by severity: threshold > pnl > big_move > l2_strategy
        priority = {"threshold": 0, "pnl": 1, "big_move": 2, "l2_strategy": 3}
        stock_alerts.sort(key=lambda a: priority.get(a.get("_kind", ""), 9))

        lines = []
        for a in stock_alerts[:2]:
            lines.append(a.get("_stealth", a["message"]))
        if len(stock_alerts) > 2:
            lines.append(f"+{len(stock_alerts) - 2} more")

        # Critical if any threshold breach or change > 8%
        has_critical = any(
            a.get("_kind") == "threshold" or abs(a.get("_change_pct", 0)) >= 8
            for a in stock_alerts
        )

        notify(
            _notify_title(is_summary=False),
            "\n".join(lines),
            sound="default" if has_critical else sound,
        )
        sent += 1

    # ── Portfolio summary → separate notification (no sound) ──
    if portfolio_alerts:
        a = portfolio_alerts[0]
        notify(
            _notify_title(is_summary=True),
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
            notify("Sprint Started", a.get("_stealth", a["message"]), sound="")
        elif "收盘" in title:
            notify("Daily Report", a.get("_stealth", a["message"]), sound="")
        else:
            notify(_notify_title(), a.get("_stealth", a["message"]), sound="")


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

    # ── Threshold hits (from alert_config.json) ──
    threshold_hits: list[str] = []
    try:
        with open(ALERT_CONFIG_PATH, "r", encoding="utf-8") as _af:
            _alert_data = json.load(_af).get("alerts", {})
    except Exception:
        _alert_data = {}
    for symbol, quote in quotes.items():
        price = quote.get("price", 0)
        name = quote.get("name", symbol)
        if price <= 0:
            continue
        _ae = _alert_data.get(symbol, {})
        above = _ae.get("above")
        below = _ae.get("below")
        if above is not None and price >= above:
            threshold_hits.append(f"  {name} 突破上限 {above}（现价 {price:.2f}）")
        if below is not None and price <= below:
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
            "_stealth": f"持仓{num_holdings}只 自选{num_watching}只",
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
# 6. Trade Plan Engine
# ══════════════════════════════════════════

TRADE_PLANS_PATH = PROJECT_ROOT / "src" / "data" / "trade_plans.json"


class TradePlanEngine:
    """检查交易计划条件，触发通知 + 模拟执行。

    每 tick 调用 check()，返回触发的告警列表（格式兼容 write_alert_events）。
    触发后自动更新 trade_plans.json（标记 triggered=True）并写入 trade_plan_events 表。
    """

    def __init__(self):
        self._plans: dict = {}
        self._consecutive_tracker: dict[str, dict] = {}  # {plan_id: {cond_id: {"count": N, "last_date": "YYYY-MM-DD"}}}
        self._last_mtime: float = 0.0
        self._reload_plans()

    def _reload_plans(self):
        """Load or reload plans from JSON (checks mtime for hot-reload)."""
        try:
            mtime = TRADE_PLANS_PATH.stat().st_mtime
        except FileNotFoundError:
            self._plans = {}
            return
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime
        data = read_json_safe(TRADE_PLANS_PATH)
        if data:
            self._plans = data.get("plans", {})

    def _save_plans(self):
        """Atomic write back to trade_plans.json."""
        tmp = TRADE_PLANS_PATH.with_suffix(".tmp")
        data = {"plans": self._plans}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.rename(TRADE_PLANS_PATH)
        self._last_mtime = TRADE_PLANS_PATH.stat().st_mtime

    def check(self, quotes: dict) -> list[dict]:
        """Check all active plans against current quotes.

        Returns list of alert dicts compatible with write_alert_events().
        """
        self._reload_plans()
        alerts: list[dict] = []
        dirty = False
        today = datetime.now().strftime("%Y-%m-%d")

        for plan_id, plan in self._plans.items():
            if plan.get("status") != "active":
                continue
            symbol = plan.get("symbol", "")
            q = quotes.get(symbol)
            if not q:
                continue
            price = q.get("price", 0)
            if price <= 0:
                continue
            amount = q.get("amount", 0) or q.get("_amount", 0)
            change = q.get("change_pct", 0) or q.get("change", 0)
            name = q.get("name", symbol)

            # 1. 移动止损（在固定止损之前检查）
            sl = plan.get("stop_loss")
            ts = sl.get("trailing") if sl else None
            if ts and ts.get("trail_pct") and sl and not sl.get("triggered"):
                act_price = ts.get("activation_price", 0)
                trail_pct = ts["trail_pct"]
                hw = ts.get("high_watermark") or 0
                is_active = ts.get("active", False)

                if not is_active and price >= act_price:
                    is_active = True
                    hw = price
                    logger.info(f"PLAN TS activated: {symbol} @ {price:.4f} (activation={act_price})")

                if is_active:
                    if price > hw:
                        hw = price
                    ts_stop = round(hw * (1 - trail_pct / 100), 4)
                    if price <= ts_stop:
                        drop_pct = (hw - price) / hw * 100
                        alert = self._make_alert(
                            plan_id, plan, "ts",
                            f"移动止损: 峰{hw:.2f}→{price:.2f} (-{drop_pct:.1f}%)",
                            price, name, change, event_type="ts_triggered",
                        )
                        alerts.append(alert)
                        sl["triggered"] = True
                        dirty = True
                        self._write_plan_event(plan_id, "ts_triggered", "ts",
                            f"移动止损 峰{hw:.2f} 回落{drop_pct:.1f}%", price, 0)
                        logger.warning(f"PLAN TS {plan['name']}: {symbol} @ {price:.4f}, peak={hw:.4f}")

                if is_active != ts.get("active") or hw != (ts.get("high_watermark") or 0):
                    ts["active"] = is_active
                    ts["high_watermark"] = round(hw, 4)
                    dirty = True

            # 2. 固定止损
            if sl and not sl.get("triggered") and price <= sl.get("price", 0):
                alert = self._make_alert(
                    plan_id, plan, "sl", "止损触发", price, name, change,
                    event_type="sl_triggered",
                )
                alerts.append(alert)
                sl["triggered"] = True
                dirty = True
                self._write_plan_event(plan_id, "sl_triggered", "sl", "止损触发", price, 0)
                logger.warning(f"PLAN SL {plan['name']}: {symbol} @ {price:.2f} <= {sl['price']:.2f}")

            # 3. 条件单 (统一处理 buy/sell)
            for order in plan.get("orders", []):
                if order.get("triggered"):
                    continue
                if not self._check_order_conditions(plan_id, order, price, amount, today):
                    if order.pop("_ts_dirty", False):
                        dirty = True
                    continue
                side = order.get("side", "sell")
                shares = order.get("shares", 0) or 0
                sell_pct = order.get("sell_pct", 0) or 0
                label = order.get("label", "")
                if side == "buy":
                    prefix = f"买入 {shares}股"
                    event_type = "buy_triggered"
                else:
                    prefix = f"卖出 {int(sell_pct * 100)}%"
                    event_type = "sell_triggered"
                alert = self._make_alert(
                    plan_id, plan, order["id"], f"{prefix}: {label}",
                    price, name, change, event_type=event_type, shares=shares,
                )
                alerts.append(alert)
                order["triggered"] = True
                order["triggered_at"] = datetime.now().isoformat()
                dirty = True
                self._write_plan_event(plan_id, event_type, order["id"], label, price, shares)
                logger.info(f"PLAN {side.upper()} {plan['name']}: {label} @ {price:.2f}")

        if dirty:
            self._save_plans()
        return alerts

    def _check_order_conditions(self, plan_id: str, order: dict,
                                price: float, amount: float,
                                today: str) -> bool:
        """Check if an order's conditions are met. Supports trailing orders."""
        op = order.get("op", ">=")
        target = order.get("price", 0)
        side = order.get("side", "sell")

        # ── Trailing order logic ──
        ts = order.get("trailing")
        if ts and ts.get("pct"):
            pct = ts["pct"]
            wm = ts.get("watermark") or 0
            is_active = ts.get("active", False)

            if not is_active:
                activated = (price >= target) if op == ">=" else (price <= target)
                if activated:
                    is_active = True
                    wm = price
                    logger.info(f"Order trailing activated: {plan_id}:{order['id']} @ {price:.4f}")

            if is_active:
                if side == "sell":
                    if price > wm:
                        wm = price
                    if wm > 0 and price <= wm * (1 - pct / 100):
                        ts["active"] = is_active
                        ts["watermark"] = round(wm, 4)
                        return True
                else:
                    if wm == 0 or price < wm:
                        wm = price
                    if wm > 0 and price >= wm * (1 + pct / 100):
                        ts["active"] = is_active
                        ts["watermark"] = round(wm, 4)
                        return True

            if is_active != ts.get("active") or wm != (ts.get("watermark") or 0):
                ts["active"] = is_active
                ts["watermark"] = round(wm, 4)
                order["_ts_dirty"] = True

            return False

        # ── Normal price check ──
        if op == ">=":
            if price < target:
                return False
        else:
            if price > target:
                return False

        vol_min = order.get("volume_min")
        if vol_min and amount < vol_min:
            return False

        cons_days = order.get("consecutive_days")
        if cons_days:
            key = f"{plan_id}:{order['id']}"
            if key not in self._consecutive_tracker:
                self._consecutive_tracker[key] = {"count": 0, "last_date": ""}
            tracker = self._consecutive_tracker[key]

            if (op == ">=" and price >= target) or (op == "<=" and price <= target):
                if tracker["last_date"] != today:
                    tracker["count"] += 1
                    tracker["last_date"] = today
            else:
                tracker["count"] = 0
                tracker["last_date"] = ""

            if tracker["count"] < cons_days:
                return False

        return True

    def _make_alert(self, plan_id: str, plan: dict, cond_id: str,
                    label: str, price: float, name: str, change: float,
                    event_type: str = "", shares: int = 0) -> dict:
        """Create alert dict compatible with write_alert_events + stealth_dispatch."""
        symbol = plan.get("symbol", "")
        plan_name = plan.get("name", plan_id)

        if shares > 0:
            action_text = f"买入 {shares} 股 @ {price:.2f}"
        elif event_type == "sl_triggered":
            action_text = f"止损 @ {price:.2f}"
        else:
            action_text = f"@ {price:.2f}"

        return {
            "symbol": symbol,
            "title": f"{name} {label}",
            "message": f"[PLAN] {name} {label} {action_text}",
            "display": f"📋 {plan_name} | {label} | {action_text}",
            "_kind": "trade_plan",
            "_level": 1,  # L1: 交易计划 = 需要立即行动
            "_change_pct": change,
            "_stealth": f"{symbol} {label} {action_text}",
            "_plan_id": plan_id,
            "_condition_id": cond_id,
            "_event_type": event_type,
        }

    def _write_plan_event(self, plan_id: str, event_type: str,
                          condition_id: str, label: str,
                          price: float, shares: int):
        """Write to trade_plan_events SQLite table."""
        try:
            from src.sim_trading.db import get_connection
            conn = get_connection()
            conn.execute(
                "INSERT OR IGNORE INTO trade_plan_events "
                "(ts, date, plan_id, event_type, condition_id, label, price, shares, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(time.time() * 1000),
                    datetime.now().strftime("%Y-%m-%d"),
                    plan_id, event_type, condition_id, label,
                    price, shares,
                    f"{label} @ {price:.2f}",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to write plan event: {e}")


# ══════════════════════════════════════════
# 7. Main loop
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
    l1 = get_policy(1, settings)
    print(f"  trigger   : L1 +/-{l1['trigger_pct']}% / L2 +/-{get_policy(2, settings)['trigger_pct']}% (首次触发)")
    print(f"  delta     : L1 +/-{l1['delta_pct']}% / L2 +/-{get_policy(2, settings)['delta_pct']}% (再次触发)")
    print(f"  portfolio : +/-{settings.get('portfolio_delta_pct', 2)}% (组合变化)")
    print(f"  data file : {MARKET_DATA_PATH.name}")
    print(f"  Ctrl+C to stop\n")

    # ── Ensure alert_events table exists ──
    from src.sim_trading.db import init_db
    init_db()

    # ── State ──
    last_mtime = 0.0
    daily_alerts = 0
    # 如果当前已过 08:00，说明今天的重置已经（或应该已经）执行过，
    # 不需要再次触发每日重置（避免重启进程丢失当日 delta 追踪状态）。
    _now = datetime.now()
    last_alert_date = _now.date() if _now.hour >= 8 else None
    last_checked_count = len(watchlist)
    engine = DeltaAlertEngine(config)
    plan_engine = TradePlanEngine()
    sent_open_today = False
    sent_close_today = False
    sent_summary_today = False
    latest_quotes: dict | None = None       # last merged quotes (for close summary)
    latest_hkd_cny_rate: float | None = None

    while running:
        # Reset daily at 08:00 (before market open)
        # last_alert_date=None on fresh start → always triggers reset once
        today = datetime.now().date()
        now_hour = datetime.now().hour
        if (last_alert_date is None or today != last_alert_date) and now_hour >= 8:
            daily_alerts = 0
            sent_open_today = False
            sent_close_today = False
            sent_summary_today = False
            latest_quotes = None
            latest_hkd_cny_rate = None
            last_alert_date = today
            engine.reset()  # clear delta tracking for new day
            check_l2_signals._daily_counts = {}  # reset per-stock L2 cap
            # 归档昨日数据 + 清空（新交易日重新开始）
            _archive_and_reset(today)
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

                    # ── L2 strategy signals (from daemon) ──
                    l2_alerts = check_l2_signals()
                    if l2_alerts:
                        for a in l2_alerts:
                            if "_level" not in a:
                                # _notify=True → L1（高优先级弹窗+声音），否则 L3（仅 web）
                                a["_level"] = 1 if a.get("_notify") else 3
                        l2_web_only = [a for a in l2_alerts if not a.get("_notify")]
                        l2_notify = [a for a in l2_alerts if a.get("_notify")]
                        if l2_web_only:
                            write_alert_events(l2_web_only)
                        # l2_notify 加入 all_alerts（后面统一写入+分发）
                        all_alerts.extend(l2_notify)

                    # ── Trade plan conditions ──
                    plan_alerts = plan_engine.check(quotes)
                    if plan_alerts:
                        write_alert_events(plan_alerts)
                        all_alerts.extend(plan_alerts)

                    if all_alerts:
                        print()
                        # 统一写入一次（去重在 write_alert_events 内处理）
                        write_alert_events(all_alerts)
                        # 按级别分流 macOS 通知
                        l1_alerts = [a for a in all_alerts if a.get("_level") == 1]
                        l2_alerts_dispatch = [a for a in all_alerts if a.get("_level") == 2 or a.get("_kind") == "portfolio"]
                        # L3 = web_only, 已写入 alert_events，不弹窗
                        if l1_alerts:
                            stealth_dispatch(l1_alerts, sound="default")
                        if l2_alerts_dispatch:
                            stealth_dispatch(l2_alerts_dispatch, sound="")
                        daily_alerts += len(l1_alerts) + len(l2_alerts_dispatch)
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

        # ── Daily summary generation (16:05-16:15 after HK close) ──
        if not sent_summary_today:
            now_t = datetime.now()
            hhmm = now_t.hour * 100 + now_t.minute
            if now_t.weekday() < 5 and 1605 <= hhmm <= 1615:
                sent_summary_today = True
                logger.info("Triggering daily summary generation...")
                try:
                    from src.tools.daily_summary_generator import generate_daily_summary
                    generate_daily_summary()
                    notify("Daily Report", "Signal digest ready", sound="")
                except Exception as e:
                    logger.error(f"Daily summary generation failed: {e}")

        # Sleep in small increments for responsive shutdown
        slept = 0.0
        while slept < check_interval and running:
            time.sleep(min(0.5, check_interval - slept))
            slept += 0.5

    # ── Shutdown ──
    print(f"\n\nNotifier stopped. Total alerts today: {daily_alerts}")


if __name__ == "__main__":
    run()
