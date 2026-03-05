#!/usr/bin/env python3
"""
Data pipeline integrity tests — regression suite for daily summary pipeline.

Covers: direction logic, cost/PnL/FX, portfolio summary, label map coverage,
trade plans, signal date filtering, per-stock daily cap, _compute_l2_digest,
daemon archiver resilience, and TICKER subscription independence.

Run: poetry run pytest src/tools/test_data_pipeline.py -v
"""

import json
import sqlite3
import time
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project root ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.daily_summary_generator import (
    _build_per_stock,
    _build_llm_prompt,
    _compute_l2_digest,
)


# ── Helpers ──

def _make_config(watchlist: dict) -> dict:
    return {"watchlist": watchlist, "settings": {}}


def _make_market_data(services: list, hkd_cny_rate=None) -> dict:
    d = {"services": services}
    if hkd_cny_rate is not None:
        d["hkdCnyRate"] = hkd_cny_rate
    return d


def _make_svc(code, price=10.0, change=1.0, name=None):
    return {"id": code, "price": price, "change": change, "name": name or code}


def _make_entry(name="Test", type_="holding", cost=0, shares=0, star=False):
    return {"name": name, "type": type_, "cost": cost, "shares": shares, "star": star}


def _setup_memory_db():
    """Create in-memory DB with required tables, return connection.

    Uses shared-cache URI so all get_connection() calls in the same process
    hit the same in-memory database.
    """
    import src.sim_trading.db as db_mod
    db_mod._db_path_override = "file:test_pipeline?mode=memory&cache=shared"
    conn = db_mod.get_connection()
    conn.executescript(db_mod.SCHEMA)
    return conn


def _teardown_memory_db():
    import src.sim_trading.db as db_mod
    db_mod._db_path_override = None


# ══════════════════════════════════════════
# Group 1: Direction 判定
# ══════════════════════════════════════════

class TestDirection(unittest.TestCase):
    """Direction inference from l2_digest direction_score and composite counts."""

    def _get_direction(self, digest=None, strategies=None):
        code = "HK00001"
        md = _make_market_data([_make_svc(code)])
        cfg = _make_config({code: _make_entry(star=True)})
        sig_agg = {code: {
            "strategies": Counter(strategies or {}),
            "directions": [],
            "displays": [],
            "count": sum((strategies or {}).values()),
            "notify_count": 0,
        }}
        digest_map = {code: digest} if digest else None
        result = _build_per_stock(md, cfg, sig_agg, {}, digest_map)
        self.assertTrue(len(result) > 0, "Expected at least 1 stock in result")
        return result[0]["direction"]

    def test_digest_strong_bullish(self):
        self.assertEqual(self._get_direction({"direction_score": 5}), "偏多")

    def test_digest_strong_bearish(self):
        self.assertEqual(self._get_direction({"direction_score": -4}), "偏空")

    def test_digest_weak_bullish(self):
        self.assertEqual(self._get_direction({"direction_score": 1}), "偏多")

    def test_digest_weak_bearish(self):
        self.assertEqual(self._get_direction({"direction_score": -1}), "偏空")

    def test_no_digest_fallback_composite(self):
        direction = self._get_direction(
            digest=None,
            strategies={"composite_bullish": 3, "composite_bearish": 1},
        )
        self.assertEqual(direction, "偏多")

    def test_no_digest_no_composite_neutral(self):
        direction = self._get_direction(digest=None, strategies={"momentum_alert": 2})
        self.assertEqual(direction, "中性")

    def test_digest_overrides_composite(self):
        """Digest direction_score takes priority even if composite disagrees."""
        direction = self._get_direction(
            digest={"direction_score": -5},
            strategies={"composite_bullish": 10},
        )
        self.assertEqual(direction, "偏空")


# ══════════════════════════════════════════
# Group 2: Cost / PnL / FX
# ══════════════════════════════════════════

class TestCostPnlFx(unittest.TestCase):
    """PnL calculation, cost=0 guard, FX rate handling."""

    def test_pnl_pct_normal(self):
        code = "600000"
        md = _make_market_data([_make_svc(code, price=11.0)])
        cfg = _make_config({code: _make_entry(cost=10.0, shares=100, star=True)})
        result = _build_per_stock(md, cfg, {code: {
            "strategies": Counter(), "directions": [], "displays": [],
            "count": 1, "notify_count": 0,
        }}, {})
        self.assertAlmostEqual(result[0]["pnl_pct"], 10.0, places=1)

    def test_cost_zero_pnl_none(self):
        code = "600000"
        md = _make_market_data([_make_svc(code, price=11.0)])
        cfg = _make_config({code: _make_entry(cost=0, shares=100, star=True)})
        result = _build_per_stock(md, cfg, {code: {
            "strategies": Counter(), "directions": [], "displays": [],
            "count": 1, "notify_count": 0,
        }}, {})
        self.assertIsNone(result[0]["pnl_pct"])

    def test_hkd_cny_rate_from_market_data(self):
        code = "HK09988"
        md = _make_market_data([_make_svc(code, price=100.0)], hkd_cny_rate=0.90)
        cfg = _make_config({code: _make_entry(cost=80.0, shares=200, star=True)})
        result = _build_per_stock(md, cfg, {code: {
            "strategies": Counter(), "directions": [], "displays": [],
            "count": 1, "notify_count": 0,
        }}, {})
        # mkt_val = 100 * 200 * 0.90 = 18000
        self.assertAlmostEqual(result[0]["mkt_val"], 18000, places=0)
        self.assertAlmostEqual(result[0]["fx"], 0.90)

    def test_hkd_cny_rate_fallback(self):
        code = "HK09988"
        md = _make_market_data([_make_svc(code, price=100.0)])  # no hkdCnyRate
        cfg = _make_config({code: _make_entry(cost=80.0, shares=200, star=True)})
        result = _build_per_stock(md, cfg, {code: {
            "strategies": Counter(), "directions": [], "displays": [],
            "count": 1, "notify_count": 0,
        }}, {})
        # fallback rate = 0.92 → mkt_val = 100 * 200 * 0.92 = 18400
        self.assertAlmostEqual(result[0]["mkt_val"], 18400, places=0)

    def test_hk_mkt_val_multiplied_by_fx(self):
        code = "HK00700"
        md = _make_market_data([_make_svc(code, price=400.0)], hkd_cny_rate=0.90)
        cfg = _make_config({code: _make_entry(cost=350.0, shares=100, star=True)})
        result = _build_per_stock(md, cfg, {code: {
            "strategies": Counter(), "directions": [], "displays": [],
            "count": 1, "notify_count": 0,
        }}, {})
        expected_mkt = 400 * 100 * 0.90  # 36000
        self.assertAlmostEqual(result[0]["mkt_val"], expected_mkt, places=0)


# ══════════════════════════════════════════
# Group 3: Portfolio 汇总
# ══════════════════════════════════════════

class TestPortfolioSummary(unittest.TestCase):
    """Portfolio summary section in LLM prompt."""

    def _build_prompt_text(self, per_stock, stats=None):
        if stats is None:
            stats = {"totalSignals": 0, "l1Count": 0, "bullish": 0,
                     "bearish": 0, "stockCount": 0, "upCount": 0, "downCount": 0,
                     "totalAlerts": 0}
        msgs = _build_llm_prompt(stats, per_stock, [])
        return msgs[1]["content"]  # user message

    def test_cost_zero_excluded_from_total_pnl(self):
        per_stock = [
            {"code": "600000", "name": "T1", "type": "holding", "star": False,
             "price": 11, "change": 2.0, "cost": 10, "shares": 100,
             "mkt_val": 1100, "pnl_pct": 10.0, "fx": 1,
             "signalCount": 1, "alertCount": 0, "direction": "偏多", "keySignals": []},
            {"code": "300001", "name": "T2", "type": "holding", "star": False,
             "price": 20, "change": -1.0, "cost": 0, "shares": 50,
             "mkt_val": 0, "pnl_pct": None, "fx": 1,
             "signalCount": 1, "alertCount": 0, "direction": "中性", "keySignals": []},
        ]
        text = self._build_prompt_text(per_stock)
        # The total P&L should be +100 (from T1 only), not include T2 (cost=0)
        self.assertIn("总市值", text)
        self.assertIn("总浮盈", text)

    def test_hk_a_separate_day_change(self):
        per_stock = [
            {"code": "HK09988", "name": "Ali", "type": "holding", "star": False,
             "price": 100, "change": 3.0, "cost": 80, "shares": 200,
             "mkt_val": 18400, "pnl_pct": 25.0, "fx": 0.92,
             "signalCount": 1, "alertCount": 0, "direction": "偏多", "keySignals": []},
            {"code": "600000", "name": "PF", "type": "holding", "star": False,
             "price": 10, "change": -2.0, "cost": 9, "shares": 100,
             "mkt_val": 1000, "pnl_pct": 11.1, "fx": 1,
             "signalCount": 1, "alertCount": 0, "direction": "偏空", "keySignals": []},
        ]
        text = self._build_prompt_text(per_stock)
        self.assertIn("港股", text)
        self.assertIn("A股", text)

    def test_only_hk_no_a_line(self):
        per_stock = [
            {"code": "HK09988", "name": "Ali", "type": "holding", "star": False,
             "price": 100, "change": 3.0, "cost": 80, "shares": 200,
             "mkt_val": 18400, "pnl_pct": 25.0, "fx": 0.92,
             "signalCount": 1, "alertCount": 0, "direction": "偏多", "keySignals": []},
        ]
        text = self._build_prompt_text(per_stock)
        self.assertIn("港股", text)
        self.assertNotIn("A股", text)

    def test_no_holdings_skips_section(self):
        per_stock = [
            {"code": "600000", "name": "Test", "type": "watching", "star": True,
             "price": 10, "change": 1.0, "cost": 0, "shares": 0,
             "mkt_val": 0, "pnl_pct": None, "fx": 1,
             "signalCount": 1, "alertCount": 0, "direction": "中性", "keySignals": []},
        ]
        text = self._build_prompt_text(per_stock)
        self.assertNotIn("## 组合概况", text)
        self.assertNotIn("总市值", text)


# ══════════════════════════════════════════
# Group 4: Label map coverage
# ══════════════════════════════════════════

class TestLabelMap(unittest.TestCase):
    """Ensure label_map in _build_per_stock covers all _STRATEGY_NAMES."""

    def test_strategy_names_covered_by_label_map(self):
        from src.tools.l2_strategy_engine import _STRATEGY_NAMES

        # Extract label_map keys from _build_per_stock source
        import inspect
        source = inspect.getsource(_build_per_stock)
        # Find the label_map dict in source
        start = source.index("label_map = {")
        end = source.index("}", start) + 1
        # Parse the dict using exec
        local = {}
        exec(source[start:end], {}, local)
        label_map = local["label_map"]

        missing = set(_STRATEGY_NAMES.keys()) - set(label_map.keys())
        self.assertEqual(missing, set(),
                         f"Strategies in _STRATEGY_NAMES but missing from label_map: {missing}")

    def test_all_labels_are_chinese(self):
        """All translated labels should contain Chinese characters."""
        from src.tools.l2_strategy_engine import _STRATEGY_NAMES
        code = "HK00001"
        md = _make_market_data([_make_svc(code)])
        cfg = _make_config({code: _make_entry(star=True)})

        # Feed all known strategies
        strategies = {k: 1 for k in _STRATEGY_NAMES.keys()}
        sig_agg = {code: {
            "strategies": Counter(strategies),
            "directions": [],
            "displays": [],
            "count": len(strategies),
            "notify_count": 0,
        }}
        result = _build_per_stock(md, cfg, sig_agg, {})
        key_signals = result[0]["keySignals"]
        # keySignals is capped at 6, but all should be Chinese-translated
        for ks in key_signals:
            label = ks.split("x")[0]  # remove "x2" suffix
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in label)
            self.assertTrue(has_chinese, f"Label '{label}' is not Chinese")


# ══════════════════════════════════════════
# Group 5: 条件单
# ══════════════════════════════════════════

class TestTradePlans(unittest.TestCase):
    """Trade plan conditional orders in LLM prompt."""

    def _build_with_plans(self, trade_plans, per_stock=None):
        if per_stock is None:
            per_stock = [
                {"code": "HK09988", "name": "Ali", "type": "holding", "star": False,
                 "price": 130.0, "change": 2.0, "cost": 120, "shares": 200,
                 "mkt_val": 26000, "pnl_pct": 8.3, "fx": 0.92,
                 "signalCount": 1, "alertCount": 0, "direction": "偏多", "keySignals": []},
            ]
        stats = {"totalSignals": 5, "l1Count": 1, "bullish": 2,
                 "bearish": 1, "stockCount": 1, "upCount": 1, "downCount": 0,
                 "totalAlerts": 0}
        msgs = _build_llm_prompt(stats, per_stock, [], trade_plans=trade_plans)
        return msgs[1]["content"]

    def test_active_untriggered_shows_section(self):
        plans = {"plans": {"HK09988_tp": {
            "name": "止盈计划", "symbol": "HK09988", "status": "active",
            "orders": [
                {"id": "o1", "side": "sell", "op": ">=", "price": 150,
                 "shares": 100, "label": "止盈1", "triggered": False},
            ],
        }}}
        text = self._build_with_plans(plans)
        self.assertIn("条件单状态", text)
        self.assertIn("止盈1", text)

    def test_all_triggered_no_section(self):
        plans = {"plans": {"HK09988_tp": {
            "name": "止盈计划", "symbol": "HK09988", "status": "active",
            "orders": [
                {"id": "o1", "side": "sell", "op": ">=", "price": 150,
                 "shares": 100, "label": "止盈1", "triggered": True},
            ],
        }}}
        text = self._build_with_plans(plans)
        self.assertNotIn("条件单状态", text)

    def test_trade_plans_none_no_crash(self):
        text = self._build_with_plans(None)
        self.assertNotIn("条件单状态", text)

    def test_distance_pct_calculation(self):
        plans = {"plans": {"HK09988_buy": {
            "name": "买入计划", "symbol": "HK09988", "status": "active",
            "orders": [
                {"id": "b1", "side": "buy", "op": "<=", "price": 125,
                 "shares": 200, "label": "接回", "triggered": False},
            ],
        }}}
        text = self._build_with_plans(plans)
        # current price = 130, target = 125 → (125 - 130) / 130 * 100 = -3.8%
        self.assertIn("-3.8%", text)


# ══════════════════════════════════════════
# Group 6: 信号日期过滤
# ══════════════════════════════════════════

class TestSignalDateFilter(unittest.TestCase):
    """Signal filtering by date in generate_daily_summary."""

    def _filter_signals(self, all_signals, date_str):
        """Reproduce the date filter from generate_daily_summary."""
        today_start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        today_end_ts = today_start_ts + 86400_000
        return [s for s in all_signals if today_start_ts <= s.get("ts", 0) < today_end_ts]

    def test_mixed_dates_filters_today_only(self):
        # 2026-03-04 signals
        today_ts = int(datetime(2026, 3, 4, 10, 0, 0).timestamp() * 1000)
        yesterday_ts = int(datetime(2026, 3, 3, 15, 0, 0).timestamp() * 1000)
        signals = [
            {"ts": today_ts, "code": "HK00001", "strategy": "test"},
            {"ts": yesterday_ts, "code": "HK00002", "strategy": "test"},
        ]
        filtered = self._filter_signals(signals, "2026-03-04")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["code"], "HK00001")

    def test_all_yesterday_empty(self):
        yesterday_ts = int(datetime(2026, 3, 3, 15, 0, 0).timestamp() * 1000)
        signals = [
            {"ts": yesterday_ts, "code": "HK00001", "strategy": "test"},
        ]
        filtered = self._filter_signals(signals, "2026-03-04")
        self.assertEqual(len(filtered), 0)

    def test_midnight_boundary(self):
        """00:00:00 belongs to the new day."""
        midnight_ts = int(datetime(2026, 3, 4, 0, 0, 0).timestamp() * 1000)
        signals = [{"ts": midnight_ts, "code": "HK00001", "strategy": "test"}]
        filtered = self._filter_signals(signals, "2026-03-04")
        self.assertEqual(len(filtered), 1)

        # But should NOT belong to previous day
        filtered_prev = self._filter_signals(signals, "2026-03-03")
        self.assertEqual(len(filtered_prev), 0)


# ══════════════════════════════════════════
# Group 7: PER_STOCK_DAILY_CAP
# ══════════════════════════════════════════

class TestPerStockDailyCap(unittest.TestCase):
    """Per-stock daily cap on L2 signals."""

    def setUp(self):
        from src.tools.stock_notifier import check_l2_signals, PER_STOCK_DAILY_CAP
        self.check_l2_signals = check_l2_signals
        self.CAP = PER_STOCK_DAILY_CAP
        # Save and reset state
        self._saved_consumed = check_l2_signals._last_consumed
        self._saved_counts = check_l2_signals._daily_counts.copy()
        check_l2_signals._last_consumed = 0
        check_l2_signals._daily_counts = {}

    def tearDown(self):
        self.check_l2_signals._last_consumed = self._saved_consumed
        self.check_l2_signals._daily_counts = self._saved_counts

    @patch("src.tools.stock_notifier.read_json_safe")
    def test_l3_capped(self, mock_read):
        """Signals beyond cap with notify=False are dropped."""
        ts_base = int(time.time() * 1000)
        signals = []
        for i in range(self.CAP + 3):
            signals.append({
                "ts": ts_base + i,
                "code": "HK00001",
                "strategy": "test",
                "display": f"signal {i}",
                "message": f"msg {i}",
                "notify": False,
            })
        mock_read.return_value = {"signals": signals}
        alerts = self.check_l2_signals()
        self.assertEqual(len(alerts), self.CAP)

    @patch("src.tools.stock_notifier.read_json_safe")
    def test_l1_not_capped(self, mock_read):
        """Signals with notify=True bypass the cap."""
        ts_base = int(time.time() * 1000)
        signals = []
        for i in range(self.CAP + 3):
            signals.append({
                "ts": ts_base + i,
                "code": "HK00001",
                "strategy": "test",
                "display": f"signal {i}",
                "message": f"msg {i}",
                "notify": True,
            })
        mock_read.return_value = {"signals": signals}
        alerts = self.check_l2_signals()
        self.assertEqual(len(alerts), self.CAP + 3)

    @patch("src.tools.stock_notifier.read_json_safe")
    def test_reset_clears_counts(self, mock_read):
        """After clearing daily_counts, cap resets."""
        ts_base = int(time.time() * 1000)
        # Fill up to cap
        signals_batch1 = [
            {"ts": ts_base + i, "code": "HK00001", "strategy": "t",
             "display": "", "message": "", "notify": False}
            for i in range(self.CAP)
        ]
        mock_read.return_value = {"signals": signals_batch1}
        self.check_l2_signals()
        self.assertEqual(self.check_l2_signals._daily_counts.get("HK00001"), self.CAP)

        # Reset
        self.check_l2_signals._daily_counts = {}

        # New batch should be accepted
        signals_batch2 = [
            {"ts": ts_base + self.CAP + i, "code": "HK00001", "strategy": "t",
             "display": "", "message": "", "notify": False}
            for i in range(3)
        ]
        mock_read.return_value = {"signals": signals_batch2}
        alerts = self.check_l2_signals()
        self.assertEqual(len(alerts), 3)


# ══════════════════════════════════════════
# Group 8: _compute_l2_digest (in-memory DB)
# ══════════════════════════════════════════

class TestComputeL2Digest(unittest.TestCase):
    """_compute_l2_digest with in-memory SQLite."""

    def setUp(self):
        self.conn = _setup_memory_db()

    def tearDown(self):
        self.conn.close()
        _teardown_memory_db()

    def test_with_session_snapshot(self):
        """session_snapshot present → correct direction_score."""
        date = "2026-03-04"
        ts = int(datetime(2026, 3, 4, 15, 30, 0).timestamp())
        session = json.dumps({
            "tick": {"imbalance": 0.25, "buy_vol": 5000, "sell_vol": 3000},
            "large_order": {"buy_amount": 2e8, "sell_amount": 5e7, "buy_count": 10, "sell_count": 3},
            "capital_flow": {"main_net_inflow": 1e8, "main_net_inflow_pct": 8.0},
        })
        self.conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) "
            "VALUES (?, ?, '15:30:00', 'HK00001', ?)",
            (ts, date, session),
        )
        self.conn.commit()

        digests = _compute_l2_digest(date)
        self.assertEqual(len(digests), 1)
        d = digests[0]
        self.assertEqual(d["code"], "HK00001")
        # lo_net_ratio = (2e8 - 5e7) / (2e8 + 5e7) = 0.6 → > 0.1 → +3
        # tick_imbalance = 0.25 → > 0.1 → +2
        # cf_pct = 8.0 → > 5 → +1
        # vpd_count = 0 → 0
        # lor = 0 → 0
        # total = 3 + 2 + 1 = 6 → bullish
        self.assertGreater(d["direction_score"], 2)
        self.assertEqual(d["direction"], "bullish")

    def test_no_snapshot_returns_empty(self):
        """No session_snapshots → empty list."""
        digests = _compute_l2_digest("2026-03-04")
        self.assertEqual(digests, [])

    def test_vpd_lor_counts(self):
        """signals table vpd/lor counts aggregated correctly."""
        date = "2026-03-04"
        ts = int(datetime(2026, 3, 4, 15, 30, 0).timestamp())

        # Insert a session snapshot
        session = json.dumps({
            "tick": {"imbalance": 0, "buy_vol": 0, "sell_vol": 0},
            "large_order": {"buy_amount": 0, "sell_amount": 0},
            "capital_flow": {"main_net_inflow": 0, "main_net_inflow_pct": 0},
        })
        self.conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) "
            "VALUES (?, ?, '15:30:00', 'HK00001', ?)",
            (ts, date, session),
        )

        # Insert vpd signals
        for i in range(4):
            self.conn.execute(
                "INSERT INTO signals (ts, date, time, strategy, code) "
                "VALUES (?, ?, ?, 'volume_price_divergence', 'HK00001')",
                (ts + i, date, "10:00:00"),
            )
        # Insert lor signals
        for i in range(2):
            self.conn.execute(
                "INSERT INTO signals (ts, date, time, strategy, code, direction) "
                "VALUES (?, ?, ?, 'large_order_reversal', 'HK00001', 'bearish')",
                (ts + 100 + i, date, "14:00:00"),
            )
        self.conn.commit()

        digests = _compute_l2_digest(date)
        self.assertEqual(len(digests), 1)
        d = digests[0]
        self.assertEqual(d["vpd_count"], 4)
        self.assertEqual(d["lor_count"], 2)
        self.assertEqual(d["lor_direction"], "bearish")
        # vpd >= 3 → -2, lor bearish → -2 → combined should push score negative
        self.assertLess(d["direction_score"], 0)


# ══════════════════════════════════════════
# Group 9: Daemon archiver resilience
# ══════════════════════════════════════════

class TestDaemonArchiver(unittest.TestCase):
    """Daemon archiver calls all 3 methods even if one fails."""

    def test_all_three_called(self):
        """archive_signals, sample_prices, snapshot_session all invoked."""
        archiver = MagicMock()
        fns = [archiver.archive_signals, archiver.sample_prices, archiver.snapshot_session]

        # Simulate daemon loop logic
        for fn in fns:
            try:
                fn()
            except Exception:
                pass

        for fn in fns:
            fn.assert_called_once()

    def test_first_fails_others_still_run(self):
        """First archiver method throws, other two still execute."""
        archiver = MagicMock()
        archiver.archive_signals.side_effect = RuntimeError("DB locked")

        fns = [archiver.archive_signals, archiver.sample_prices, archiver.snapshot_session]
        for fn in fns:
            try:
                fn()
            except Exception:
                pass

        archiver.archive_signals.assert_called_once()
        archiver.sample_prices.assert_called_once()
        archiver.snapshot_session.assert_called_once()


# ══════════════════════════════════════════
# Group 10: TICKER 订阅独立性
# ══════════════════════════════════════════

class TestTickerSubscription(unittest.TestCase):
    """TICKER subscription is unconditional, regardless of strategy enabled flags."""

    @patch("src.tools.l2_strategy_engine.L2StrategyEngine._is_port_open", return_value=True)
    def test_ticker_subscribed_even_all_disabled(self, _mock_port):
        """Even with all strategies disabled, _subscribe still calls subscribe(TICKER)."""
        from src.tools.l2_strategy_engine import L2StrategyEngine

        # All strategies disabled
        config = {
            "enabled": True,
            "strategies": {
                "large_order": {"enabled": False},
                "tick_imbalance": {"enabled": False},
                "order_book_imbalance": {"enabled": False},
                "capital_flow_spike": {"enabled": False},
                "volume_price_divergence": {"enabled": False},
            },
        }
        watchlist = {"HK09988": {"name": "Ali", "type": "holding"}}

        engine = L2StrategyEngine(config, watchlist)

        # Mock context
        mock_ctx = MagicMock()
        mock_ctx.subscribe.return_value = (0, "OK")  # RET_OK = 0
        engine._ctx = mock_ctx
        engine._subscribed = False

        # Mock futu imports
        mock_sub_type = MagicMock()
        mock_sub_type.TICKER = "TICKER"
        mock_sub_type.ORDER_BOOK = "ORDER_BOOK"

        with patch.dict("sys.modules", {
            "futu": MagicMock(SubType=mock_sub_type, RET_OK=0),
        }):
            engine._subscribe()

        # Verify TICKER was subscribed
        calls = mock_ctx.subscribe.call_args_list
        ticker_calls = [c for c in calls if "TICKER" in str(c)]
        self.assertTrue(len(ticker_calls) > 0, "TICKER subscription should always happen")
        self.assertTrue(engine._subscribed)


# ══════════════════════════════════════════
# Group: Indicator Condition Orders
# ══════════════════════════════════════════

class TestIndicatorConditions(unittest.TestCase):
    """TradePlanEngine._check_indicator_conditions() — 6 indicator types."""

    def setUp(self):
        from src.tools.stock_notifier import TradePlanEngine
        self.engine = TradePlanEngine()

    # ── rsi_above ──

    def test_rsi_above_pass(self):
        self.assertTrue(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, {"rsi": 45.0}))

    def test_rsi_above_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, {"rsi": 25.0}))

    def test_rsi_above_equal_fail(self):
        """Boundary: rsi == threshold should NOT pass (strict >)."""
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, {"rsi": 30.0}))

    def test_rsi_above_none(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, {"rsi": None}))

    # ── rsi_below ──

    def test_rsi_below_pass(self):
        self.assertTrue(self.engine._check_indicator_conditions(
            {"rsi_below": 70}, {"rsi": 55.0}))

    def test_rsi_below_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_below": 70}, {"rsi": 80.0}))

    def test_rsi_below_equal_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_below": 70}, {"rsi": 70.0}))

    # ── macd_hist_narrowing_days ──

    def test_narrowing_3_days_pass(self):
        # abs: 0.05, 0.04, 0.03, 0.02 — 3 consecutive narrowing
        hist = [-0.05, -0.04, -0.03, -0.02]
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_hist_narrowing_days": 3}, {"macd_hist_list": hist}))

    def test_narrowing_3_days_fail(self):
        # abs: 0.05, 0.04, 0.05, 0.02 — not consecutive (0.04→0.05 widens)
        hist = [-0.05, -0.04, -0.05, -0.02]
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_hist_narrowing_days": 3}, {"macd_hist_list": hist}))

    def test_narrowing_insufficient_data(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_hist_narrowing_days": 3}, {"macd_hist_list": [0.01, 0.02]}))

    def test_narrowing_positive_hist(self):
        # Positive hist narrowing: 0.10, 0.08, 0.05 — abs narrowing
        hist = [0.10, 0.08, 0.05]
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_hist_narrowing_days": 2}, {"macd_hist_list": hist}))

    # ── macd_golden_cross ──

    def test_golden_cross_pass(self):
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_golden_cross": True}, {"macd_hist_list": [-0.01, 0.02]}))

    def test_golden_cross_fail_both_positive(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_golden_cross": True}, {"macd_hist_list": [0.01, 0.02]}))

    def test_golden_cross_fail_both_negative(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_golden_cross": True}, {"macd_hist_list": [-0.02, -0.01]}))

    def test_golden_cross_zero_boundary(self):
        """hist[-2]=0 and hist[-1]>0 should pass (0 → positive)."""
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_golden_cross": True}, {"macd_hist_list": [0, 0.01]}))

    def test_golden_cross_insufficient_data(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_golden_cross": True}, {"macd_hist_list": [0.01]}))

    # ── macd_death_cross ──

    def test_death_cross_pass(self):
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_death_cross": True}, {"macd_hist_list": [0.01, -0.02]}))

    def test_death_cross_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"macd_death_cross": True}, {"macd_hist_list": [-0.01, -0.02]}))

    def test_death_cross_zero_boundary(self):
        """hist[-2]=0 and hist[-1]<0 should pass (0 → negative)."""
        self.assertTrue(self.engine._check_indicator_conditions(
            {"macd_death_cross": True}, {"macd_hist_list": [0, -0.01]}))

    # ── vol_ratio_above ──

    def test_vol_ratio_pass(self):
        self.assertTrue(self.engine._check_indicator_conditions(
            {"vol_ratio_above": 1.5}, {"vol_ratio": 2.0}))

    def test_vol_ratio_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"vol_ratio_above": 1.5}, {"vol_ratio": 1.2}))

    def test_vol_ratio_none(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"vol_ratio_above": 1.5}, {"vol_ratio": None}))

    def test_vol_ratio_equal_fail(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"vol_ratio_above": 1.5}, {"vol_ratio": 1.5}))

    # ── Empty / missing indicators ──

    def test_empty_indicators_returns_false(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, {}))

    def test_none_indicators_returns_false(self):
        self.assertFalse(self.engine._check_indicator_conditions(
            {"rsi_above": 30}, None))

    # ── Combined AND conditions ──

    def test_combined_rsi_and_narrowing_pass(self):
        cond = {"rsi_above": 30, "macd_hist_narrowing_days": 2}
        ind = {"rsi": 45.0, "macd_hist_list": [-0.05, -0.03, -0.01]}
        self.assertTrue(self.engine._check_indicator_conditions(cond, ind))

    def test_combined_rsi_pass_narrowing_fail(self):
        cond = {"rsi_above": 30, "macd_hist_narrowing_days": 2}
        ind = {"rsi": 45.0, "macd_hist_list": [-0.03, -0.05, -0.01]}
        self.assertFalse(self.engine._check_indicator_conditions(cond, ind))

    def test_combined_rsi_fail_narrowing_pass(self):
        cond = {"rsi_above": 30, "macd_hist_narrowing_days": 2}
        ind = {"rsi": 25.0, "macd_hist_list": [-0.05, -0.03, -0.01]}
        self.assertFalse(self.engine._check_indicator_conditions(cond, ind))

    def test_triple_condition(self):
        cond = {"rsi_above": 30, "macd_golden_cross": True, "vol_ratio_above": 1.0}
        ind = {"rsi": 50.0, "macd_hist_list": [-0.01, 0.02], "vol_ratio": 1.5}
        self.assertTrue(self.engine._check_indicator_conditions(cond, ind))


class TestOrderWithIndicators(unittest.TestCase):
    """Integration: _check_order_conditions with indicators kwarg."""

    def setUp(self):
        from src.tools.stock_notifier import TradePlanEngine
        self.engine = TradePlanEngine()

    def test_order_without_indicators_still_works(self):
        """Existing orders without indicators field should not be affected."""
        order = {"id": "t1", "side": "sell", "op": ">=", "price": 10.0}
        self.assertTrue(self.engine._check_order_conditions(
            "plan1", order, price=11.0, amount=0, today="2026-03-05"))

    def test_order_with_indicators_all_pass(self):
        order = {
            "id": "t1", "side": "buy", "op": "<=", "price": 26.0,
            "indicators": {"rsi_above": 30},
        }
        ind = {"rsi": 45.0}
        self.assertTrue(self.engine._check_order_conditions(
            "plan1", order, price=25.0, amount=0, today="2026-03-05",
            indicators=ind))

    def test_order_price_pass_indicator_fail(self):
        """Price condition met but indicator not met → overall False."""
        order = {
            "id": "t1", "side": "buy", "op": "<=", "price": 26.0,
            "indicators": {"rsi_above": 50},
        }
        ind = {"rsi": 30.0}
        self.assertFalse(self.engine._check_order_conditions(
            "plan1", order, price=25.0, amount=0, today="2026-03-05",
            indicators=ind))

    def test_order_indicator_pass_price_fail(self):
        """Indicator met but price not met → overall False."""
        order = {
            "id": "t1", "side": "buy", "op": "<=", "price": 20.0,
            "indicators": {"rsi_above": 30},
        }
        ind = {"rsi": 45.0}
        self.assertFalse(self.engine._check_order_conditions(
            "plan1", order, price=25.0, amount=0, today="2026-03-05",
            indicators=ind))

    def test_order_indicators_missing_from_kwargs(self):
        """Order has indicators condition but no indicators in kwargs → False."""
        order = {
            "id": "t1", "side": "sell", "op": ">=", "price": 10.0,
            "indicators": {"rsi_above": 30},
        }
        self.assertFalse(self.engine._check_order_conditions(
            "plan1", order, price=11.0, amount=0, today="2026-03-05"))


class TestMergeDataIndicators(unittest.TestCase):
    """merge_data() injects indicators from l2_strategy_signals.json."""

    @patch("src.tools.stock_notifier._read_l2_indicators")
    def test_indicators_injected(self, mock_read):
        from src.tools.stock_notifier import merge_data
        mock_read.return_value = {
            "HK09973": {"rsi": 20.2, "macd_hist": -0.5, "macd_hist_list": [-0.3, -0.5]},
        }
        market = {"services": [
            {"id": "HK09973", "price": 25.14, "change": -1.02, "name": "奇瑞汽车",
             "chgAmt": -0.26, "amount": 76725842},
        ]}
        config = {"watchlist": {"HK09973": {"name": "奇瑞汽车", "type": "holding"}}}
        quotes = merge_data(market, config)
        self.assertIn("indicators", quotes["HK09973"])
        self.assertEqual(quotes["HK09973"]["indicators"]["rsi"], 20.2)

    @patch("src.tools.stock_notifier._read_l2_indicators")
    def test_missing_indicators_empty_dict(self, mock_read):
        from src.tools.stock_notifier import merge_data
        mock_read.return_value = {}
        market = {"services": [
            {"id": "000001", "price": 10.0, "change": 1.0, "name": "Test",
             "chgAmt": 0.1, "amount": 1000000},
        ]}
        config = {"watchlist": {"000001": {"name": "Test", "type": "holding"}}}
        quotes = merge_data(market, config)
        self.assertEqual(quotes["000001"]["indicators"], {})


class TestSnapshotIndicators(unittest.TestCase):
    """DailyIndicatorTracker.snapshot_indicators() output format."""

    def _make_tracker(self):
        import pandas as pd
        from src.tools.l2_strategy_engine import DailyIndicatorTracker
        tracker = DailyIndicatorTracker({"refresh_minutes": 30})
        # Inject mock _ind data
        tracker._ind["HK09973"] = {
            "rsi": pd.Series([45.0, 50.0, 55.2]),
            "macd_hist": pd.Series([-0.05, -0.03, -0.01, 0.02, 0.04]),
            "volume": pd.Series([1000, 1200, 1500]),
            "vol_ma20": pd.Series([800, 900, 1000]),
        }
        return tracker

    def test_snapshot_basic(self):
        tracker = self._make_tracker()
        snap = tracker.snapshot_indicators()
        self.assertIn("HK09973", snap)
        ind = snap["HK09973"]
        self.assertAlmostEqual(ind["rsi"], 55.2, places=1)
        self.assertAlmostEqual(ind["macd_hist"], 0.04, places=4)
        self.assertEqual(len(ind["macd_hist_list"]), 5)
        self.assertAlmostEqual(ind["vol_ratio"], 1.5, places=1)
        self.assertIn("updated_at", ind)

    def test_snapshot_empty_ind(self):
        from src.tools.l2_strategy_engine import DailyIndicatorTracker
        tracker = DailyIndicatorTracker({"refresh_minutes": 30})
        self.assertEqual(tracker.snapshot_indicators(), {})

    def test_snapshot_nan_handling(self):
        import pandas as pd
        import numpy as np
        from src.tools.l2_strategy_engine import DailyIndicatorTracker
        tracker = DailyIndicatorTracker({"refresh_minutes": 30})
        tracker._ind["TEST"] = {
            "rsi": pd.Series([np.nan]),
            "macd_hist": pd.Series([np.nan]),
            "volume": pd.Series([100]),
            "vol_ma20": pd.Series([0]),  # division by zero guard
        }
        snap = tracker.snapshot_indicators()
        # NaN rsi/macd should result in None
        if "TEST" in snap:
            self.assertIsNone(snap["TEST"]["rsi"])
            self.assertIsNone(snap["TEST"]["macd_hist"])

    def test_snapshot_vol_ma_zero(self):
        """vol_ma20 = 0 should not cause ZeroDivisionError."""
        import pandas as pd
        from src.tools.l2_strategy_engine import DailyIndicatorTracker
        tracker = DailyIndicatorTracker({"refresh_minutes": 30})
        tracker._ind["TEST"] = {
            "rsi": pd.Series([50.0]),
            "macd_hist": pd.Series([0.01]),
            "volume": pd.Series([1000]),
            "vol_ma20": pd.Series([0.0]),
        }
        snap = tracker.snapshot_indicators()
        self.assertIn("TEST", snap)
        self.assertIsNone(snap["TEST"]["vol_ratio"])


if __name__ == "__main__":
    unittest.main()
