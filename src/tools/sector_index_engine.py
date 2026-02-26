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

import akshare as ak

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


# ---------------------------------------------------------------------------
# 1. collect_rotation
# ---------------------------------------------------------------------------

def collect_rotation(today=None):
    """Fetch EM industry + concept board rankings and store in sector_rotation."""
    date_str = _today_str(today)
    logger.info("collect_rotation for %s", date_str)

    conn = get_connection()
    try:
        inserted = 0

        # --- industry boards ---
        df_ind = _akshare_call(
            lambda: ak.stock_board_industry_name_em(),
            "industry_boards",
        )
        if df_ind is not None and not df_ind.empty:
            df_ind = df_ind.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            for rank, (_, row) in enumerate(df_ind.iterrows(), start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO sector_rotation"
                    " (date, category, board_name, change_pct, rank)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (date_str, "industry", row["板块名称"], float(row["涨跌幅"]), rank),
                )
                inserted += 1
            logger.info("industry boards: %d rows", len(df_ind))
        else:
            logger.warning("industry boards: no data")

        # --- concept boards ---
        df_con = _akshare_call(
            lambda: ak.stock_board_concept_name_em(),
            "concept_boards",
        )
        if df_con is not None and not df_con.empty:
            df_con = df_con.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            for rank, (_, row) in enumerate(df_con.iterrows(), start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO sector_rotation"
                    " (date, category, board_name, change_pct, rank)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (date_str, "concept", row["板块名称"], float(row["涨跌幅"]), rank),
                )
                inserted += 1
            logger.info("concept boards: %d rows", len(df_con))
        else:
            logger.warning("concept boards: no data")

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
    components = index_def.get("components", [])
    if not components:
        logger.warning("index %s has no components, skipping", index_id)
        return

    changes = []
    comp_details = []

    for code in components:
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
            changes.append(chg)
            comp_details.append({
                "code": code, "change_pct": chg, "close": close,
            })
            logger.debug("  %s: change=%.2f%%", code, chg)
        else:
            logger.warning("  %s: no data for %s", code, date_str)
            comp_details.append({"code": code, "change_pct": None, "close": None})

    if not changes:
        logger.warning("index %s: no component data for %s", index_id, date_str)
        return

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

    components = index_def.get("components", [])
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

    for code in components:
        df = _akshare_call(
            lambda c=code: ak.stock_zh_a_hist(
                symbol=c, period="daily",
                start_date=start_compact, end_date=end_compact,
                adjust="qfq",
            ),
            f"backfill_{code}",
        )
        if df is None or df.empty:
            logger.warning("backfill: %s returned no data", code)
            continue

        code_hist = {}
        for _, row in df.iterrows():
            d = str(row["日期"])[:10]  # YYYY-MM-DD
            code_hist[d] = {
                "close": float(row["收盘"]),
                "change_pct": float(row["涨跌幅"]),
            }
            all_dates.add(d)
        hist_by_code[code] = code_hist
        logger.info("backfill: %s fetched %d days", code, len(code_hist))

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
                        "change_pct": day_data["change_pct"],
                        "close": day_data["close"],
                    })
                else:
                    comp_details.append({
                        "code": code, "change_pct": None, "close": None,
                    })

            if not changes:
                continue

            avg_change = mean(changes)
            up_count = sum(1 for c in changes if c > 0)
            down_count = sum(1 for c in changes if c < 0)
            index_value = round(prev_value * (1 + avg_change / 100), 4)

            conn.execute(
                "INSERT OR IGNORE INTO sector_daily"
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
    config = _load_config()
    indices = config.get("indices", {})
    alert_rules = config.get("alert_rules", {})

    cum_gain_threshold = alert_rules.get("cumulative_gain_pct", 8)
    slope_threshold = alert_rules.get("slope_threshold", 0.3)
    lookback_days = alert_rules.get("lookback_days", 10)
    min_days_since_create = alert_rules.get("min_days_since_create", 3)

    if not indices:
        logger.info("No indices configured, skipping mainline detection")
        return

    logger.info(
        "detect_mainline for %s (threshold=%.1f%% slope>=%.2f lookback=%d)",
        date_str, cum_gain_threshold, slope_threshold, lookback_days,
    )

    conn = get_connection()
    try:
        ts_now = int(time.time() * 1000)

        for index_id, index_def in indices.items():
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
            slope, r_squared = _linear_regression(values)

            logger.info(
                "index %s (%s): cum_gain=%.2f%% slope=%.4f R2=%.4f",
                index_id, index_name, cum_gain, slope, r_squared,
            )

            # Determine alert type
            alert_type = None
            message = None
            display = None

            if cum_gain >= cum_gain_threshold and slope >= slope_threshold:
                alert_type = "mainline"
                message = (
                    f"{index_name}: mainline detected "
                    f"(+{cum_gain:.1f}% in {len(rows)}d, slope={slope:.3f})"
                )
                display = (
                    f"[主线] {index_name} 累计涨幅 {cum_gain:.1f}%"
                    f"（{len(rows)}日），趋势斜率 {slope:.3f}，R2={r_squared:.2f}"
                )
            elif cum_gain >= cum_gain_threshold * 0.75:
                alert_type = "approaching"
                message = (
                    f"{index_name}: approaching mainline "
                    f"(+{cum_gain:.1f}% in {len(rows)}d, slope={slope:.3f})"
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
