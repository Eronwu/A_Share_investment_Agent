"""
Sector rotation tracking + custom index engine.

Collects EM industry/concept board rankings, computes equal-weight custom
indices from component stocks, detects "mainline" themes via linear regression,
and manages data lifecycle (backfill + cleanup).

Usage:
    poetry run python -m src.tools.sector_index_engine                # run_daily()
    poetry run python -m src.tools.sector_index_engine --rotation     # collect rotation only
    poetry run python -m src.tools.sector_index_engine --indices      # compute indices only
    poetry run python -m src.tools.sector_index_engine --backfill ID  # backfill 30d for index
    poetry run python -m src.tools.sector_index_engine --detect       # detect mainline only
"""

import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

import re

import akshare as ak
import requests as _requests

from src.sim_trading.db import get_connection, init_db

logger = logging.getLogger("sector_engine")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_config.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load sector_config.json, return empty dict on failure."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load config %s: %s", CONFIG_PATH, exc)
        return {}


def _akshare_call(fn, label: str, max_retries: int = 3):
    """Call an akshare function with exponential backoff retry + rate limiting."""
    for attempt in range(1, max_retries + 1):
        time.sleep(1.5)  # rate limit between calls
        try:
            result = fn()
            return result
        except Exception as exc:
            wait = min(60, 2 ** attempt)
            if attempt < max_retries:
                logger.warning(
                    "%s attempt %d/%d failed: %s  (retry in %ds)",
                    label, attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "%s all %d attempts failed: %s", label, max_retries, exc,
                )
                return None


def _today_str(today=None) -> str:
    """Return date string YYYY-MM-DD."""
    if today:
        return today
    return datetime.now().strftime("%Y-%m-%d")


def _today_compact(today=None) -> str:
    """Return date string YYYYMMDD for akshare stock_zh_a_hist."""
    d = _today_str(today)
    return d.replace("-", "")


def _is_trading_day(date_str: str | None = None) -> bool:
    """Check if date is a potential A-share trading day (Mon-Fri).

    Does NOT check holidays — but data sources will return no new data
    on holidays, and the suspended-stock logic treats that as 0% change.
    """
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    return d.weekday() < 5  # 0=Mon .. 4=Fri


# ---------------------------------------------------------------------------
# Tencent Finance fallback for stock kline (when EM push2 is blocked)
# ---------------------------------------------------------------------------

def _tencent_market_prefix(code: str) -> str:
    """Return Tencent market prefix: 'sh' for 6xx/68x, 'sz' for others."""
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


# Name cache: persists for the process lifetime
_stock_name_cache: dict[str, str] = {}


def _fetch_stock_names(codes: list[str]) -> dict[str, str]:
    """Batch fetch stock names from Tencent Finance qt API.

    Returns {code: name} dict. Uses in-process cache.
    """
    result = {}
    missing = []
    for c in codes:
        if c in _stock_name_cache:
            result[c] = _stock_name_cache[c]
        else:
            missing.append(c)

    if not missing:
        return result

    # Tencent qt batch API: comma-separated "sh600096,sz000792"
    qt_codes = ",".join(f"{_tencent_market_prefix(c)}{c}" for c in missing)
    try:
        url = f"https://qt.gtimg.cn/q={qt_codes}"
        r = _requests.get(url, timeout=10)
        # Response: v_sz000792="51~盐湖股份~000792~38.20~...";
        for line in r.text.strip().split(";"):
            line = line.strip()
            if not line or "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) >= 3:
                name = parts[1]
                raw_code = parts[2]
                if name and raw_code:
                    _stock_name_cache[raw_code] = name
                    result[raw_code] = name
    except Exception as exc:
        logger.warning("Tencent qt name fetch failed: %s", exc)

    return result


def _fetch_tencent_kline(code: str, start: str, end: str) -> list[dict] | None:
    """Fetch QFQ daily kline from Tencent Finance.

    Args:
        code: stock code like '000792'
        start: 'YYYY-MM-DD'
        end: 'YYYY-MM-DD'

    Returns list of {date, close, change_pct} or None on failure.
    """
    prefix = _tencent_market_prefix(code)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={prefix}{code},day,{start},{end},250,qfq"
    )
    try:
        r = _requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        # data format: {"code":0, "data":{"sz000792":{"qfqday":[[date,open,close,high,low,vol],...]}}}
        stock_key = f"{prefix}{code}"
        kline = data.get("data", {}).get(stock_key, {})
        days = kline.get("qfqday") or kline.get("day", [])
        if not days:
            return None

        result = []
        prev_close = None
        for row in days:
            date_str = row[0]  # "YYYY-MM-DD"
            close = float(row[2])
            if prev_close and prev_close != 0:
                change_pct = (close / prev_close - 1) * 100
            else:
                # First day: try to compute from open
                open_price = float(row[1])
                change_pct = (close / open_price - 1) * 100 if open_price else 0.0
            result.append({"date": date_str, "close": close, "change_pct": round(change_pct, 4)})
            prev_close = close
        return result
    except Exception as exc:
        logger.warning("Tencent kline %s failed: %s", code, exc)
        return None


# ---------------------------------------------------------------------------
# Sina Finance fallback (when EM push2 is blocked by corporate network)
# ---------------------------------------------------------------------------

_SINA_URLS = {
    "industry": "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
    "concept": "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php",
}


def _fetch_sina_boards(category: str) -> list[tuple[str, float]]:
    """Fetch board rankings from Sina Finance.

    Returns list of (board_name, change_pct) sorted by change% desc.
    Sina data format: var XXX = {"key":"key,name,count,avg_price,change_amt,change_pct,..."}
    """
    url = _SINA_URLS.get(category)
    if not url:
        return []
    try:
        r = _requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        text = r.content.decode("gbk", errors="replace")
        m = re.search(r"=\s*(\{.*\})", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(1))
        boards = []
        for val in data.values():
            parts = val.split(",")
            if len(parts) >= 6:
                name = parts[1]
                pct = float(parts[5]) if parts[5] else 0.0
                boards.append((name, pct))
        boards.sort(key=lambda x: x[1], reverse=True)
        return boards
    except Exception as exc:
        logger.warning("Sina %s fetch failed: %s", category, exc)
        return []


# ---------------------------------------------------------------------------
# 1. collect_rotation
# ---------------------------------------------------------------------------

def collect_rotation(today=None):
    """Fetch board rankings (EM first, Sina fallback) and store in sector_rotation."""
    date_str = _today_str(today)

    if not _is_trading_day(date_str):
        logger.info("collect_rotation: %s is not a trading day, skipping", date_str)
        return

    logger.info("collect_rotation for %s", date_str)

    conn = get_connection()
    try:
        inserted = 0

        for category, ak_fn, ak_label in [
            ("industry", lambda: ak.stock_board_industry_name_em(), "EM_industry"),
            ("concept", lambda: ak.stock_board_concept_name_em(), "EM_concept"),
        ]:
            boards: list[tuple[str, float]] = []

            # Try akshare (EM push2) first
            df = _akshare_call(ak_fn, ak_label)
            if df is not None and not df.empty:
                df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
                boards = [(row["板块名称"], float(row["涨跌幅"])) for _, row in df.iterrows()]
                logger.info("%s via EM: %d boards", category, len(boards))
            else:
                # Fallback to Sina
                logger.info("%s EM failed, trying Sina fallback...", category)
                boards = _fetch_sina_boards(category)
                if boards:
                    logger.info("%s via Sina: %d boards", category, len(boards))
                else:
                    logger.warning("%s: no data from EM or Sina", category)

            for rank, (name, pct) in enumerate(boards, 1):
                conn.execute(
                    "INSERT OR IGNORE INTO sector_rotation"
                    " (date, category, board_name, change_pct, rank)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (date_str, category, name, pct, rank),
                )
                inserted += 1

        conn.commit()
        logger.info("collect_rotation done, inserted %d rows total", inserted)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. compute_custom_indices
# ---------------------------------------------------------------------------

def compute_custom_indices(today=None):
    """Compute equal-weight custom indices from sector_config.json definitions."""
    date_str = _today_str(today)

    if not _is_trading_day(date_str):
        logger.info("compute_custom_indices: %s is not a trading day, skipping", date_str)
        return

    date_compact = _today_compact(today)
    config = _load_config()
    indices = config.get("indices", {})

    if not indices:
        logger.info("No custom indices defined in config, skipping")
        return

    logger.info("compute_custom_indices for %s (%d indices)", date_str, len(indices))

    conn = get_connection()
    try:
        for index_id, index_def in indices.items():
            _compute_single_index(conn, index_id, index_def, date_str, date_compact)
        conn.commit()
    finally:
        conn.close()


def _compute_single_index(conn, index_id: str, index_def: dict,
                          date_str: str, date_compact: str):
    """Compute one custom index for a single date."""
    components = index_def.get("stocks") or index_def.get("components", [])
    if not components:
        logger.warning("index %s has no components, skipping", index_id)
        return

    changes = []
    comp_details = []

    for code in components:
        chg = None
        close = None

        # Try akshare (EM push2) first
        df = _akshare_call(
            lambda c=code: ak.stock_zh_a_hist(
                symbol=c, period="daily",
                start_date=date_compact, end_date=date_compact,
                adjust="qfq",
            ),
            f"hist_{code}_{date_compact}",
        )
        if df is not None and not df.empty:
            chg = float(df.iloc[-1]["涨跌幅"])
            close = float(df.iloc[-1]["收盘"])
        else:
            # Fallback: Tencent Finance kline
            logger.info("  %s: akshare failed, trying Tencent fallback", code)
            tk = _fetch_tencent_kline(code, date_str, date_str)
            if tk:
                chg = tk[-1]["change_pct"]
                close = tk[-1]["close"]

        if chg is not None:
            changes.append(chg)
            comp_details.append({
                "code": code, "change_pct": chg, "close": close,
            })
            logger.debug("  %s: change=%.2f%%", code, chg)
        else:
            # Suspended/no-data stocks count as 0% change
            logger.warning("  %s: no data for %s (using 0%%)", code, date_str)
            changes.append(0.0)
            comp_details.append({"code": code, "change_pct": 0.0, "close": None})

    if not changes:
        logger.warning("index %s: no component data for %s", index_id, date_str)
        return

    # Enrich with stock names
    names = _fetch_stock_names(components)
    for cd in comp_details:
        cd["name"] = names.get(cd["code"], "")

    avg_change = mean(changes)
    up_count = sum(1 for c in changes if c > 0)
    down_count = sum(1 for c in changes if c < 0)

    # Get previous day's index value (for chaining)
    row = conn.execute(
        "SELECT index_value FROM sector_daily"
        " WHERE index_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (index_id, date_str),
    ).fetchone()
    prev_value = row["index_value"] if row else 100.0

    index_value = round(prev_value * (1 + avg_change / 100), 4)

    conn.execute(
        "INSERT OR REPLACE INTO sector_daily"
        " (date, index_id, avg_change_pct, index_value, up_count, down_count, components_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            date_str, index_id,
            round(avg_change, 4), index_value,
            up_count, down_count,
            json.dumps(comp_details, ensure_ascii=False),
        ),
    )
    logger.info(
        "index %s @ %s: avg=%.2f%% value=%.4f (up=%d down=%d)",
        index_id, date_str, avg_change, index_value, up_count, down_count,
    )


# ---------------------------------------------------------------------------
# 3. backfill_index
# ---------------------------------------------------------------------------

def backfill_index(index_id: str, days: int = 30):
    """Backfill an index by fetching multi-day history for each component."""
    config = _load_config()
    indices = config.get("indices", {})
    index_def = indices.get(index_id)
    if not index_def:
        logger.error("index_id=%s not found in config", index_id)
        return

    components = index_def.get("stocks") or index_def.get("components", [])
    if not components:
        logger.error("index %s has no components", index_id)
        return

    end_date = datetime.now()
    start_date = end_date - timedelta(days=int(days * 1.6))  # extra margin for weekends
    start_compact = start_date.strftime("%Y%m%d")
    end_compact = end_date.strftime("%Y%m%d")

    logger.info(
        "backfill_index %s: %d components, %s ~ %s",
        index_id, len(components), start_compact, end_compact,
    )

    # Fetch full history for each component
    hist_by_code: dict[str, dict[str, dict]] = {}  # code -> {date_str -> {close, change_pct}}
    all_dates: set[str] = set()

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    for code in components:
        code_hist: dict[str, dict] = {}

        # Try akshare (EM push2) first
        df = _akshare_call(
            lambda c=code: ak.stock_zh_a_hist(
                symbol=c, period="daily",
                start_date=start_compact, end_date=end_compact,
                adjust="qfq",
            ),
            f"backfill_{code}",
        )
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                d = str(row["日期"])[:10]  # YYYY-MM-DD
                code_hist[d] = {
                    "close": float(row["收盘"]),
                    "change_pct": float(row["涨跌幅"]),
                }
            logger.info("backfill: %s fetched %d days via akshare", code, len(code_hist))
        else:
            # Fallback: Tencent Finance kline
            logger.info("backfill: %s akshare failed, trying Tencent fallback", code)
            tk = _fetch_tencent_kline(code, start_str, end_str)
            if tk:
                for item in tk:
                    code_hist[item["date"]] = {
                        "close": item["close"],
                        "change_pct": item["change_pct"],
                    }
                logger.info("backfill: %s fetched %d days via Tencent", code, len(code_hist))

        if not code_hist:
            logger.warning("backfill: %s returned no data from any source", code)
            continue

        for d in code_hist:
            all_dates.add(d)
        hist_by_code[code] = code_hist

    if not all_dates:
        logger.error("backfill: no data for any component")
        return

    # Process dates oldest → newest
    sorted_dates = sorted(all_dates)
    # Only keep the most recent `days` trading days
    if len(sorted_dates) > days:
        sorted_dates = sorted_dates[-days:]

    conn = get_connection()
    try:
        prev_value = 100.0

        # Check if there's an existing value before our range
        row = conn.execute(
            "SELECT index_value FROM sector_daily"
            " WHERE index_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (index_id, sorted_dates[0]),
        ).fetchone()
        if row:
            prev_value = row["index_value"]

        # Fetch stock names once for all dates
        names = _fetch_stock_names(components)

        for date_str in sorted_dates:
            changes = []
            comp_details = []

            for code in components:
                code_hist = hist_by_code.get(code, {})
                day_data = code_hist.get(date_str)
                if day_data:
                    changes.append(day_data["change_pct"])
                    comp_details.append({
                        "code": code,
                        "name": names.get(code, ""),
                        "change_pct": day_data["change_pct"],
                        "close": day_data["close"],
                    })
                else:
                    # Suspended/no-data stocks count as 0% change
                    changes.append(0.0)
                    comp_details.append({
                        "code": code, "name": names.get(code, ""),
                        "change_pct": 0.0, "close": None,
                    })

            if not changes:
                continue

            avg_change = mean(changes)
            up_count = sum(1 for c in changes if c > 0)
            down_count = sum(1 for c in changes if c < 0)
            index_value = round(prev_value * (1 + avg_change / 100), 4)

            conn.execute(
                "INSERT OR REPLACE INTO sector_daily"
                " (date, index_id, avg_change_pct, index_value,"
                "  up_count, down_count, components_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    date_str, index_id,
                    round(avg_change, 4), index_value,
                    up_count, down_count,
                    json.dumps(comp_details, ensure_ascii=False),
                ),
            )
            prev_value = index_value

        conn.commit()
        logger.info("backfill_index %s done (%d trading days)", index_id, len(sorted_dates))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. detect_mainline
# ---------------------------------------------------------------------------

def _linear_regression(ys: list[float]):
    """Manual linear regression (no numpy/scipy).

    Returns (slope, r_squared) for y values indexed 0..N-1.
    """
    n = len(ys)
    if n < 2:
        return 0.0, 0.0

    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    ss_xy = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((xs[i] - x_mean) ** 2 for i in range(n))
    ss_yy = sum((ys[i] - y_mean) ** 2 for i in range(n))

    if ss_xx == 0:
        return 0.0, 0.0

    slope = ss_xy / ss_xx

    if ss_yy == 0:
        r_squared = 1.0 if ss_xy == 0 else 0.0
    else:
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

    return slope, r_squared


def detect_mainline(today=None):
    """Detect mainline themes: sustained upward trend in custom indices."""
    date_str = _today_str(today)

    if not _is_trading_day(date_str):
        logger.info("detect_mainline: %s is not a trading day, skipping", date_str)
        return

    config = _load_config()
    indices = config.get("indices", {})
    alert_rules = config.get("alert_rules", {})

    cum_gain_threshold = alert_rules.get("cumulative_gain_pct", 8)
    slope_threshold = alert_rules.get("slope_threshold", 0.05)
    r_squared_min = alert_rules.get("r_squared_min", 0.4)
    lookback_days = alert_rules.get("lookback_days", 10)
    min_days_since_create = alert_rules.get("min_days_since_create", 3)

    if not indices:
        logger.info("No indices configured, skipping mainline detection")
        return

    logger.info(
        "detect_mainline for %s (threshold=%.1f%% slope>=%.3f R2>=%.2f lookback=%d)",
        date_str, cum_gain_threshold, slope_threshold, r_squared_min, lookback_days,
    )

    conn = get_connection()
    try:
        ts_now = int(time.time() * 1000)

        for index_id, index_def in indices.items():
            # Fix 1: skip indices not being watched
            if not index_def.get("watch", True):
                continue

            index_name = index_def.get("name", index_id)

            # Get last N days of index values
            rows = conn.execute(
                "SELECT date, index_value FROM sector_daily"
                " WHERE index_id = ? AND date <= ?"
                " ORDER BY date DESC LIMIT ?",
                (index_id, date_str, lookback_days),
            ).fetchall()

            if len(rows) < min_days_since_create:
                logger.info(
                    "index %s: only %d days (need %d), skipping",
                    index_id, len(rows), min_days_since_create,
                )
                continue

            # Reverse to chronological order (oldest first)
            rows = list(reversed(rows))
            values = [r["index_value"] for r in rows]
            baseline = values[0]

            if baseline == 0:
                continue

            cum_gain = (values[-1] / baseline - 1) * 100

            # Fix 2: regress on daily return% series, not absolute index values
            # slope > 0 means trend accelerating, ~0 means steady, <0 decelerating
            if len(values) < 3:
                slope, r_squared = 0.0, 0.0
            else:
                returns = [(values[i] / values[i - 1] - 1) * 100 for i in range(1, len(values))]
                slope, r_squared = _linear_regression(returns)

            logger.info(
                "index %s (%s): cum_gain=%.2f%% slope=%.4f R2=%.4f",
                index_id, index_name, cum_gain, slope, r_squared,
            )

            # Determine alert type
            alert_type = None
            message = None
            display = None

            # Fix 4: mainline requires R² >= r_squared_min (trend must be reliable)
            if (cum_gain >= cum_gain_threshold
                    and slope >= slope_threshold
                    and r_squared >= r_squared_min):
                alert_type = "mainline"
                message = (
                    f"{index_name}: mainline detected "
                    f"(+{cum_gain:.1f}% in {len(rows)}d, slope={slope:.3f}, R2={r_squared:.2f})"
                )
                display = (
                    f"[主线] {index_name} 累计涨幅 {cum_gain:.1f}%"
                    f"（{len(rows)}日），趋势斜率 {slope:.3f}，R2={r_squared:.2f}"
                )
            # Fix 3: approaching also requires slope > 0 (at least not declining)
            elif cum_gain >= cum_gain_threshold * 0.75 and slope > 0:
                alert_type = "approaching"
                message = (
                    f"{index_name}: approaching mainline "
                    f"(+{cum_gain:.1f}% in {len(rows)}d, slope={slope:.3f}, R2={r_squared:.2f})"
                )
                display = (
                    f"[接近主线] {index_name} 累计涨幅 {cum_gain:.1f}%"
                    f"（{len(rows)}日），趋势斜率 {slope:.3f}，R2={r_squared:.2f}"
                )

            if alert_type:
                conn.execute(
                    "INSERT OR REPLACE INTO sector_alerts"
                    " (ts, date, index_id, index_name, alert_type,"
                    "  cumulative_pct, slope, r_squared, message, display)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts_now, date_str, index_id, index_name, alert_type,
                        round(cum_gain, 4), round(slope, 6), round(r_squared, 4),
                        message, display,
                    ),
                )
                logger.info("  -> alert: %s", alert_type)

        conn.commit()
        logger.info("detect_mainline done")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. cleanup_old_data
# ---------------------------------------------------------------------------

def cleanup_old_data():
    """Remove stale data: rotation 90d, daily 180d, alerts 30d."""
    now = datetime.now()
    cutoff_rotation = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_daily = (now - timedelta(days=180)).strftime("%Y-%m-%d")
    cutoff_alerts = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        c1 = conn.execute(
            "DELETE FROM sector_rotation WHERE date < ?", (cutoff_rotation,)
        ).rowcount
        c2 = conn.execute(
            "DELETE FROM sector_daily WHERE date < ?", (cutoff_daily,)
        ).rowcount
        c3 = conn.execute(
            "DELETE FROM sector_alerts WHERE date < ?", (cutoff_alerts,)
        ).rowcount
        conn.commit()
        logger.info(
            "cleanup: rotation=%d daily=%d alerts=%d rows deleted",
            c1, c2, c3,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. run_daily
# ---------------------------------------------------------------------------

def run_daily():
    """Full daily pipeline: init -> collect -> compute -> detect -> cleanup."""
    logger.info("=== run_daily start ===")
    init_db()
    collect_rotation()
    compute_custom_indices()
    detect_mainline()
    cleanup_old_data()
    logger.info("=== run_daily complete ===")


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Sector rotation & custom index engine")
    parser.add_argument("--rotation", action="store_true", help="Collect rotation data only")
    parser.add_argument("--indices", action="store_true", help="Compute custom indices only")
    parser.add_argument("--backfill", type=str, metavar="INDEX_ID", help="Backfill index history")
    parser.add_argument("--detect", action="store_true", help="Run mainline detection only")

    args = parser.parse_args()

    has_flag = args.rotation or args.indices or args.backfill or args.detect

    if not has_flag:
        run_daily()
    else:
        init_db()
        if args.rotation:
            collect_rotation()
        if args.indices:
            compute_custom_indices()
        if args.backfill:
            backfill_index(args.backfill)
        if args.detect:
            detect_mainline()
