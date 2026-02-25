"""Signal Archiver — 实时归档 L2 信号 + 价格快照到 SQLite。

与 l2_strategy_daemon 共生运行:
- 每 5s 检查 l2_strategy_signals.json，归档新信号
- 每 30s 从 market_data.json 采样价格快照
- 水位标记避免重复归档

可独立运行: poetry run python src/sim_trading/signal_archiver.py
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .db import get_connection, init_db

logger = logging.getLogger("signal_archiver")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_PATH = PROJECT_ROOT / "data" / "l2_strategy_signals.json"
MARKET_DATA_PATH = PROJECT_ROOT / "data" / "market_data.json"

SIGNAL_CHECK_SEC = 5
PRICE_SAMPLE_SEC = 30
SESSION_SNAPSHOT_SEC = 300  # 每 5 分钟归档一次 session 上下文


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _infer_direction(signal: dict) -> str:
    """Infer signal direction from strategy name or detail."""
    strategy = signal.get("strategy", "")
    detail = signal.get("detail", {})

    # --- explicit direction strategies ---
    if "bearish" in strategy or "sell" in strategy:
        return "bearish"
    if "bullish" in strategy or strategy in ("momentum_alert", "volume_accel_alert"):
        return "bullish"

    # --- daily-K strategies with inherent direction ---
    _BULLISH_STRATEGIES = {
        "macd_golden_cross", "ma_bullish_align", "volume_breakout",
        "breakout_pullback", "macd_bottom_divergence",
    }
    _BEARISH_STRATEGIES = {
        "macd_death_cross", "ma_bearish_align", "macd_top_divergence",
        "support_breakdown", "rsi_extreme_overbought", "volume_divergence_top",
    }
    if strategy in _BULLISH_STRATEGIES:
        return "bullish"
    if strategy in _BEARISH_STRATEGIES:
        return "bearish"

    # --- detail.direction field (used by engulfing, adx_trend_start, etc.) ---
    direction = detail.get("direction", "")
    if direction in ("bullish", "bearish"):
        return direction

    # --- tick_imbalance / tick_persistence ---
    imb = detail.get("imbalance", detail.get("curr_imbalance", 0))
    if imb > 0:
        return "bullish"
    if imb < 0:
        return "bearish"

    dom = detail.get("dominant_direction")
    if dom:
        return dom

    # --- large_order BUY/SELL ---
    if direction == "BUY":
        return "bullish"
    if direction == "SELL":
        return "bearish"

    # --- volume_price_divergence: negative inflow = bearish ---
    if strategy == "volume_price_divergence":
        inflow = detail.get("main_net_inflow", 0)
        return "bearish" if inflow < 0 else "bullish"

    # --- rsi extremes ---
    if strategy == "rsi_overbought":
        return "bearish"
    if strategy == "rsi_oversold":
        return "bearish"  # oversold is a condition, direction depends on context

    return "neutral"


def _get_price_context(market_data: dict, code: str) -> tuple[float, float]:
    """Get (price, daily_change_pct) from market_data for a stock code."""
    services = market_data.get("services", [])
    for svc in services:
        if svc.get("id") == code:
            price = svc.get("price", 0)
            change_pct = svc.get("change", 0)
            return float(price or 0), float(change_pct or 0)
    return 0.0, 0.0


class SignalArchiver:
    """归档 L2 信号、价格快照和 session 上下文到 SQLite。"""

    def __init__(self):
        init_db()
        self._last_signal_ts = 0  # 信号水位标记
        self._last_price_sample = 0.0  # 上次价格采样时间
        self._last_session_snapshot = 0.0  # 上次 session 快照时间

    def _load_watermark(self):
        """从 DB 加载最新信号 ts 作为水位标记。"""
        conn = get_connection()
        row = conn.execute("SELECT MAX(ts) as max_ts FROM signals").fetchone()
        conn.close()
        if row and row["max_ts"]:
            self._last_signal_ts = row["max_ts"]
            logger.info(f"Signal watermark loaded: {self._last_signal_ts}")

    def archive_signals(self) -> int:
        """归档新信号。返回归档数量。"""
        data = _read_json(SIGNALS_PATH)
        if not data:
            return 0

        signals = data.get("signals", []) if isinstance(data, dict) else data
        new_signals = [s for s in signals if s.get("ts", 0) > self._last_signal_ts]
        if not new_signals:
            return 0

        # 获取当前行情用于 price_at_signal
        market_data = _read_json(MARKET_DATA_PATH) or {}
        today = datetime.now().strftime("%Y-%m-%d")

        conn = get_connection()
        archived = 0
        for s in new_signals:
            code = s.get("code", "")
            price, change_pct = _get_price_context(market_data, code)
            direction = _infer_direction(s)

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO signals
                       (ts, date, time, strategy, code, direction, notify, detail, display,
                        price_at_signal, daily_change_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        s.get("ts", 0),
                        today,
                        s.get("time", ""),
                        s.get("strategy", ""),
                        code,
                        direction,
                        1 if s.get("notify") else 0,
                        json.dumps(s.get("detail", {}), ensure_ascii=False),
                        s.get("display", ""),
                        price if price > 0 else None,
                        change_pct if price > 0 else None,
                    ),
                )
                archived += 1
            except Exception as e:
                logger.debug(f"Signal archive error: {e}")

        conn.commit()
        conn.close()

        if archived > 0:
            self._last_signal_ts = max(s.get("ts", 0) for s in new_signals)
            logger.info(f"Archived {archived} signals (watermark={self._last_signal_ts})")

        return archived

    def sample_prices(self) -> int:
        """采样当前价格快照。返回采样数量。"""
        now = time.time()
        if now - self._last_price_sample < PRICE_SAMPLE_SEC:
            return 0

        self._last_price_sample = now

        market_data = _read_json(MARKET_DATA_PATH)
        if not market_data:
            return 0

        services = market_data.get("services", [])
        if not services:
            return 0

        ts = int(now * 1000)
        today = datetime.now().strftime("%Y-%m-%d")

        conn = get_connection()
        sampled = 0
        for svc in services:
            code = svc.get("id", "")
            if not code.startswith("HK"):
                continue  # 只归档 HK 持仓

            price = float(svc.get("price", 0) or 0)
            if price <= 0:
                continue

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO price_snapshots
                       (ts, date, code, price, volume, amount, change_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ts,
                        today,
                        code,
                        price,
                        float(svc.get("volume", 0) or 0),
                        float(svc.get("amount", 0) or 0),
                        float(svc.get("change", 0) or 0),
                    ),
                )
                sampled += 1
            except Exception as e:
                logger.debug(f"Price sample error: {e}")

        conn.commit()
        conn.close()

        return sampled

    def snapshot_session(self) -> int:
        """归档 session 上下文（资金流、盘口状态）到 SQLite。返回归档数量。

        session 包含 capital_flow、tracker 状态等 daemon 运行时数据，
        仅存在于 l2_strategy_signals.json，不归档则丢失。
        每 5 分钟采样一次，保留日内资金流演变轨迹。
        """
        now = time.time()
        if now - self._last_session_snapshot < SESSION_SNAPSHOT_SEC:
            return 0

        self._last_session_snapshot = now

        data = _read_json(SIGNALS_PATH)
        if not data or not isinstance(data, dict):
            return 0

        session = data.get("session", {})
        if not session:
            return 0

        ts = int(now * 1000)
        today = datetime.now().strftime("%Y-%m-%d")
        t = datetime.now().strftime("%H:%M:%S")

        conn = get_connection()
        saved = 0
        for code, ctx in session.items():
            if not code or not isinstance(ctx, dict):
                continue
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO session_snapshots
                       (ts, date, time, code, session_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ts, today, t, code, json.dumps(ctx, ensure_ascii=False)),
                )
                saved += 1
            except Exception as e:
                logger.debug(f"Session snapshot error: {e}")

        conn.commit()
        conn.close()

        if saved > 0:
            logger.info(f"Session snapshot: {saved} stocks archived")
        return saved

    def run(self):
        """主循环: 5s 检查信号 + 30s 采样价格 + 5min session 快照。"""
        self._load_watermark()
        logger.info("Signal Archiver started")
        logger.info(f"  signals: {SIGNALS_PATH.name}")
        logger.info(f"  prices:  {MARKET_DATA_PATH.name}")

        try:
            while True:
                try:
                    self.archive_signals()
                    self.sample_prices()
                    self.snapshot_session()
                except Exception as e:
                    logger.warning(f"Archiver error: {e}")
                time.sleep(SIGNAL_CHECK_SEC)
        except KeyboardInterrupt:
            logger.info("Signal Archiver stopped")

    def backfill(self) -> tuple[int, int]:
        """回填当前 signals.json 中的所有信号。返回 (信号数, 价格快照数)。"""
        self._last_signal_ts = 0  # 清除水位，强制全量归档
        sig_count = self.archive_signals()
        price_count = self.sample_prices()
        return sig_count, price_count


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    archiver = SignalArchiver()
    archiver.run()


if __name__ == "__main__":
    run()
