"""Tests for sim_trading Phase 2 modules.

Run: poetry run pytest src/sim_trading/test_sim_trading.py -v
"""

import json
import math
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .signal_mapper import TradeDecision, TradeSignalMapper
from .position_manager import Position, PositionManager
from .simulation_engine import SimulationEngine
from .trade_analyzer import TradeAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_rules.json"


@pytest.fixture
def rules():
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mapper(rules):
    return TradeSignalMapper(rules)


@pytest.fixture
def engine(rules):
    return SimulationEngine(rules["cost_model"], rules["slippage"])


@pytest.fixture
def pos_mgr(rules):
    return PositionManager(rules["initial_capital"], rules.get("lot_sizes", {}))


def _make_signal(strategy, code, direction="bullish", sig_id=1, ts=1000000, detail=None):
    return {
        "id": sig_id,
        "ts": ts,
        "strategy": strategy,
        "code": code,
        "direction": direction,
        "detail": detail or {},
        "price_at_signal": 100.0,
    }


# ---------------------------------------------------------------------------
# TradeSignalMapper
# ---------------------------------------------------------------------------

class TestTradeSignalMapper:

    def test_tier1_buy_signal(self, mapper):
        sig = _make_signal("composite_bullish", "HK00700")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "BUY"
        assert decision.code == "HK00700"
        assert decision.confidence == 0.60
        assert decision.position_pct == 0.10

    def test_tier1_sell_signal(self, mapper):
        # Sell requires existing position — mapper doesn't check that, risk check does
        sig = _make_signal("momentum_sell_alert", "HK00700", direction="bearish")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "SELL"
        assert decision.confidence == 0.70

    def test_tier1_direction_signal_bullish_below_min_confidence(self, mapper):
        """DIRECTION BUY with 0.55 confidence is below min_confidence=0.60 → rejected by risk."""
        sig = _make_signal("bollinger_squeeze_breakout", "HK00700", direction="bullish")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None  # 0.55 < 0.60 min_confidence

    def test_tier1_direction_signal_bullish_resolves_buy(self, mapper):
        """DIRECTION signal with bullish direction produces BUY (test _apply_tier1 directly)."""
        sig = _make_signal("bollinger_squeeze_breakout", "HK00700", direction="bullish")
        decision = mapper._apply_tier1(sig)
        assert decision is not None
        assert decision.action == "BUY"
        assert decision.confidence == 0.55

    def test_tier1_direction_signal_bearish(self, mapper):
        """DIRECTION SELL bypasses risk check (only BUY is risk-gated)."""
        sig = _make_signal("bollinger_squeeze_breakout", "HK00700", direction="bearish")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "SELL"

    def test_tier1_direction_signal_neutral_skipped(self, mapper):
        sig = _make_signal("bollinger_squeeze_breakout", "HK00700", direction="neutral")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None

    def test_tier4_log_only(self, mapper):
        sig = _make_signal("tick_imbalance", "HK00700")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None

    def test_tier3_sell_on_existing_position(self, mapper):
        pos = Position(
            code="HK00700", entry_price=500.0, quantity=100,
            entry_time=0, entry_date="2026-01-01",
            stop_loss=480.0, take_profit=550.0,
            max_hold_days=5, confidence=0.6,
        )
        sig = _make_signal(
            "large_order_reversal", "HK00700", direction="bearish",
            detail={"direction": "bearish"},
        )
        decision = mapper.process_signal(sig, {"HK00700": pos}, 1_000_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "SELL"
        assert "T3:" in decision.reason

    def test_tier3_ignores_no_position(self, mapper):
        sig = _make_signal("large_order_reversal", "HK00700", direction="bearish")
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None

    def test_tier3_tighten_sl(self, mapper):
        pos = Position(
            code="HK00700", entry_price=500.0, quantity=100,
            entry_time=0, entry_date="2026-01-01",
            stop_loss=480.0, take_profit=550.0,
            max_hold_days=5, confidence=0.6,
        )
        sig = _make_signal("volume_price_divergence", "HK00700", direction="bearish")
        decision = mapper.process_signal(sig, {"HK00700": pos}, 1_000_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "TIGHTEN_SL"

    def test_conflict_detection(self, mapper):
        """Same stock BUY then SELL in same session → SELL blocked."""
        buy_sig = _make_signal("composite_bullish", "HK00700", ts=1000)
        mapper.process_signal(buy_sig, {}, 1_000_000, "2026-01-01")

        sell_sig = _make_signal("macd_death_cross", "HK00700", direction="bearish", sig_id=2, ts=2000)
        decision = mapper.process_signal(sell_sig, {}, 1_000_000, "2026-01-01")
        assert decision is None  # Blocked by conflict

    def test_session_reset(self, mapper):
        """After reset_session, conflict state is cleared."""
        buy_sig = _make_signal("composite_bullish", "HK00700", ts=1000)
        mapper.process_signal(buy_sig, {}, 1_000_000, "2026-01-01")

        mapper.reset_session("2026-01-02")

        sell_sig = _make_signal("macd_death_cross", "HK00700", direction="bearish", sig_id=2, ts=2000)
        decision = mapper.process_signal(sell_sig, {}, 1_000_000, "2026-01-02")
        assert decision is not None

    def test_risk_check_low_confidence(self, mapper):
        """BUY with confidence below min_confidence is rejected."""
        sig = _make_signal("ma_bullish_align", "HK00700")  # confidence=0.50
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None  # 0.50 < 0.60 min_confidence

    def test_risk_check_total_invested_limit(self, mapper):
        """BUY blocked when total invested exceeds 80%."""
        # Create fake positions consuming 75% of equity
        positions = {}
        for code, price, qty in [
            ("HK01810", 35.0, 6000),   # 210k
            ("HK00700", 520.0, 500),    # 260k
            ("HK03986", 400.0, 250),    # 100k
            ("HK01211", 100.0, 2000),   # 200k
        ]:
            positions[code] = Position(
                code=code, entry_price=price, quantity=qty,
                entry_time=0, entry_date="2026-01-01",
                stop_loss=price * 0.95, take_profit=price * 1.1,
                max_hold_days=5, confidence=0.6,
            )

        sig = _make_signal("composite_bullish", "HK09988")
        decision = mapper.process_signal(sig, positions, 1_000_000, "2026-01-01")
        assert decision is None  # Over 80% invested

    def test_max_signals_per_stock(self, mapper):
        """After 3 signals for same stock, further signals are rejected."""
        for i in range(3):
            sig = _make_signal("composite_bullish", "HK00700", sig_id=i + 1, ts=1000 + i * 1000)
            mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")

        sig = _make_signal("macd_golden_cross", "HK00700", sig_id=4, ts=5000)
        decision = mapper.process_signal(sig, {}, 1_000_000, "2026-01-01")
        assert decision is None

    def test_tier2_boost(self, mapper):
        """Tier 2 large_order boosts recent Tier 1 decision."""
        buy_sig = _make_signal("composite_bullish", "HK00700", ts=1000)
        decision = mapper.process_signal(buy_sig, {}, 1_000_000, "2026-01-01")
        assert decision.confidence == 0.60

        boost_sig = _make_signal(
            "large_order", "HK00700", ts=1500, sig_id=2,
            detail={"amount": 20_000_000, "direction": "BUY"},
        )
        mapper.process_signal(boost_sig, {}, 1_000_000, "2026-01-01")
        # The boost is applied in-place to the cached decision
        assert decision.confidence == 0.70

    def test_tier2_boost_wrong_direction(self, mapper):
        """Tier 2 boost with opposite direction does not apply."""
        buy_sig = _make_signal("composite_bullish", "HK00700", ts=1000)
        decision = mapper.process_signal(buy_sig, {}, 1_000_000, "2026-01-01")

        boost_sig = _make_signal(
            "large_order", "HK00700", direction="bearish", ts=1500, sig_id=2,
            detail={"amount": 20_000_000, "direction": "SELL"},
        )
        mapper.process_signal(boost_sig, {}, 1_000_000, "2026-01-01")
        assert decision.confidence == 0.60  # Unchanged


# ---------------------------------------------------------------------------
# PositionManager
# ---------------------------------------------------------------------------

class TestPositionManager:

    def test_open_position(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="composite_bullish",
        )
        pos = pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                                     current_date="2026-01-01", current_ts=1000)
        assert pos is not None
        assert pos.code == "HK00700"
        assert pos.quantity == 100  # lot_size=100, 100k/520 ≈ 192 → 100
        assert pos.stop_loss == 500.0  # 520 - 2*10
        assert pos.entry_strategy == "composite_bullish"
        assert pos_mgr.cash < 1_000_000

    def test_open_position_lot_alignment(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK01060", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=3,
            trigger_signal_ids=[1], reason="test",
        )
        # HK01060 lot=10000, 100k/0.8 = 125000 → 120000
        pos = pos_mgr.open_position(decision, price=0.80, atr=0.02, trade_cost=50.0,
                                     current_date="2026-01-01", current_ts=1000)
        assert pos is not None
        assert pos.quantity % 10000 == 0

    def test_no_duplicate_position(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos1 = pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                                      current_date="2026-01-01", current_ts=1000)
        pos2 = pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                                      current_date="2026-01-01", current_ts=2000)
        assert pos1 is not None
        assert pos2 is None  # Already holding

    def test_close_position(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)

        trade = pos_mgr.close_position(
            "HK00700", price=540.0, reason="take_profit",
            trade_cost=80.0, current_ts=2000, current_date="2026-01-02", day_index=1,
        )
        assert trade is not None
        assert trade["pnl"] > 0
        assert trade["exit_reason"] == "take_profit"
        assert "HK00700" not in pos_mgr.positions

    def test_partial_close(self, pos_mgr):
        """Closing 50% of a position keeps the remainder."""
        decision = TradeDecision(
            action="BUY", code="HK01211", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=100.0, atr=3.0, trade_cost=50.0,
                               current_date="2026-01-01", current_ts=1000)
        qty_before = pos_mgr.positions["HK01211"].quantity

        trade = pos_mgr.close_position(
            "HK01211", price=105.0, reason="partial_sell", pct=0.50,
            trade_cost=30.0, current_ts=2000, current_date="2026-01-02", day_index=1,
        )
        assert trade is not None
        assert "HK01211" in pos_mgr.positions  # Still has remaining
        assert pos_mgr.positions["HK01211"].quantity < qty_before

    def test_check_exits_stop_loss(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)

        closed = pos_mgr.check_exits(
            {"HK00700": 495.0},  # Below SL=500
            day_index=1, current_date="2026-01-02",
        )
        assert len(closed) == 1
        assert "stop_loss" in closed[0]["exit_reason"]

    def test_check_exits_take_profit(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)
        # TP = 520 + 2 * 1.5 * 10 = 550
        closed = pos_mgr.check_exits(
            {"HK00700": 555.0},
            day_index=1, current_date="2026-01-02",
        )
        assert len(closed) == 1
        assert "take_profit" in closed[0]["exit_reason"]

    def test_check_exits_max_hold(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=3,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000, day_index=0)

        # Day 3 → should trigger max hold (3 >= 3)
        closed = pos_mgr.check_exits(
            {"HK00700": 525.0},
            day_index=3, current_date="2026-01-04",
        )
        assert len(closed) == 1
        assert "max_hold" in closed[0]["exit_reason"]

    def test_tighten_stop(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)
        old_sl = pos_mgr.positions["HK00700"].stop_loss  # 500

        pos_mgr.tighten_stop("HK00700", new_atr_mult=1.0, price=530.0, atr=10.0)
        new_sl = pos_mgr.positions["HK00700"].stop_loss
        assert new_sl > old_sl  # 520 > 500

    def test_get_equity(self, pos_mgr):
        assert pos_mgr.get_equity({}) == 1_000_000

        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)

        # Price unchanged → equity ≈ initial - commission
        equity = pos_mgr.get_equity({"HK00700": 520.0})
        assert abs(equity - (1_000_000 - 100.0)) < 1.0

    def test_snapshot(self, pos_mgr):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos_mgr.open_position(decision, price=520.0, atr=10.0, trade_cost=100.0,
                               current_date="2026-01-01", current_ts=1000)

        snap = pos_mgr.snapshot({"HK00700": 530.0})
        assert snap["n_positions"] == 1
        assert "HK00700" in snap["positions"]
        assert snap["positions"]["HK00700"]["unrealized_pnl"] > 0


# ---------------------------------------------------------------------------
# SimulationEngine
# ---------------------------------------------------------------------------

class TestSimulationEngine:

    def test_calc_cost_buy(self, engine):
        cost = engine.calc_cost(price=520.0, quantity=100, action="BUY")
        assert "commission" in cost
        assert "stamp_duty" in cost
        assert "exchange_fee" in cost
        assert "settlement_fee" in cost
        assert cost["total"] > 0
        # Commission = max(52000 * 0.0003, 3) = 15.6
        assert cost["commission"] == 15.6
        # Stamp duty = ceil(52000 * 0.0013) = ceil(67.6) = 68
        assert cost["stamp_duty"] == 68

    def test_calc_cost_min_commission(self, engine):
        """Small trade should hit minimum commission."""
        cost = engine.calc_cost(price=1.0, quantity=100, action="BUY")
        assert cost["commission"] == 3.0  # Min commission

    def test_calc_cost_settlement_bounds(self, engine):
        """Settlement fee has min=2, max=100."""
        # Small trade → settlement min
        cost = engine.calc_cost(price=1.0, quantity=100, action="BUY")
        assert cost["settlement_fee"] == 2.0

        # Huge trade → settlement max
        cost = engine.calc_cost(price=500.0, quantity=100000, action="SELL")
        assert cost["settlement_fee"] == 100.0

    def test_slippage_buy(self, engine):
        # High liquidity stock → low slippage
        exec_price = engine.apply_slippage(100.0, daily_amount=2e9, action="BUY")
        assert exec_price > 100.0
        assert exec_price < 100.1  # 0.05% slippage

    def test_slippage_sell(self, engine):
        exec_price = engine.apply_slippage(100.0, daily_amount=2e9, action="SELL")
        assert exec_price < 100.0

    def test_slippage_low_liquidity(self, engine):
        # Low liquidity → higher slippage
        exec_high = engine.apply_slippage(100.0, daily_amount=5e6, action="BUY")
        exec_low = engine.apply_slippage(100.0, daily_amount=5e9, action="BUY")
        assert exec_high > exec_low  # More slippage for illiquid

    def test_execute_trade(self, engine):
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
        )
        result = engine.execute_trade(decision, current_price=520.0, daily_amount=1e9, atr=10.0)
        assert "exec_price" in result
        assert result["exec_price"] > 520.0  # BUY slippage
        assert result["action"] == "BUY"


# ---------------------------------------------------------------------------
# TradeAnalyzer
# ---------------------------------------------------------------------------

class TestTradeAnalyzer:

    def _make_trades(self):
        return [
            {"code": "HK00700", "pnl": 2000, "pnl_pct": 0.04, "hold_days": 3,
             "commission": 80, "exit_reason": "take_profit", "notes": "composite_bullish",
             "trigger_signals": [1]},
            {"code": "HK01810", "pnl": -1000, "pnl_pct": -0.02, "hold_days": 2,
             "commission": 60, "exit_reason": "stop_loss", "notes": "composite_bullish",
             "trigger_signals": [2]},
            {"code": "HK03986", "pnl": 5000, "pnl_pct": 0.10, "hold_days": 1,
             "commission": 70, "exit_reason": "take_profit", "notes": "momentum_alert",
             "trigger_signals": [3]},
            {"code": "HK09988", "pnl": -500, "pnl_pct": -0.01, "hold_days": 5,
             "commission": 50, "exit_reason": "max_hold", "notes": "macd_golden_cross",
             "trigger_signals": [4]},
        ]

    def _make_daily_pnl(self):
        return [
            {"date": "2026-01-01", "total_equity": 1_000_000, "daily_return": 0.0,
             "cumulative_return": 0.0, "cash": 500_000, "invested": 500_000},
            {"date": "2026-01-02", "total_equity": 1_002_000, "daily_return": 0.002,
             "cumulative_return": 0.002, "cash": 600_000, "invested": 402_000},
            {"date": "2026-01-03", "total_equity": 1_005_500, "daily_return": 0.0035,
             "cumulative_return": 0.0055, "cash": 1_005_500, "invested": 0},
        ]

    def test_summary(self):
        analyzer = TradeAnalyzer(self._make_trades(), self._make_daily_pnl())
        s = analyzer.summary()

        assert s["total_trades"] == 4
        assert s["winning_trades"] == 2
        assert s["losing_trades"] == 2
        assert s["win_rate"] == 0.5
        assert s["total_pnl"] == 5500
        assert s["total_commission"] == 260
        assert s["final_equity"] == 1_005_500

    def test_empty_trades(self):
        analyzer = TradeAnalyzer([], [])
        s = analyzer.summary()
        assert "error" in s

    def test_per_stock(self):
        analyzer = TradeAnalyzer(self._make_trades(), self._make_daily_pnl())
        by_stock = analyzer.per_stock()
        assert "HK03986" in by_stock
        assert by_stock["HK03986"]["pnl"] == 5000
        assert by_stock["HK03986"]["win_rate"] == 1.0

    def test_per_strategy(self):
        analyzer = TradeAnalyzer(self._make_trades(), self._make_daily_pnl())
        by_strat = analyzer.per_strategy()
        assert "composite_bullish" in by_strat
        assert by_strat["composite_bullish"]["trades"] == 2

    def test_sharpe_positive(self):
        analyzer = TradeAnalyzer(self._make_trades(), self._make_daily_pnl())
        s = analyzer.summary()
        assert s["sharpe_ratio"] > 0  # Positive daily returns → positive Sharpe

    def test_max_drawdown_zero_for_monotonic(self):
        """Monotonically increasing equity → 0 drawdown."""
        daily_pnl = [
            {"date": f"2026-01-0{i}", "total_equity": 1_000_000 + i * 1000,
             "daily_return": 0.001, "cumulative_return": 0.001 * i,
             "cash": 500_000, "invested": 500_000}
            for i in range(1, 6)
        ]
        analyzer = TradeAnalyzer(self._make_trades(), daily_pnl)
        s = analyzer.summary()
        assert s["max_drawdown_pct"] == 0.0

    def test_print_report_no_error(self, capsys):
        analyzer = TradeAnalyzer(self._make_trades(), self._make_daily_pnl())
        analyzer.print_report()
        captured = capsys.readouterr()
        assert "SIM TRADING REPORT" in captured.out
        assert "Sharpe" in captured.out


# ---------------------------------------------------------------------------
# signal_archiver _infer_direction fix
# ---------------------------------------------------------------------------

class TestInferDirection:

    def test_macd_golden_cross(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "macd_golden_cross", "detail": {}}
        assert _infer_direction(sig) == "bullish"

    def test_macd_death_cross(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "macd_death_cross", "detail": {}}
        assert _infer_direction(sig) == "bearish"

    def test_engulfing_with_direction(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "engulfing_pattern", "detail": {"direction": "bearish"}}
        assert _infer_direction(sig) == "bearish"

    def test_adx_trend_start_bullish(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "adx_trend_start", "detail": {"direction": "bullish"}}
        assert _infer_direction(sig) == "bullish"

    def test_volume_price_divergence(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "volume_price_divergence", "detail": {"main_net_inflow": -5000000}}
        assert _infer_direction(sig) == "bearish"

    def test_composite_bullish(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "composite_bullish", "detail": {}}
        assert _infer_direction(sig) == "bullish"

    def test_momentum_sell(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "momentum_sell_alert", "detail": {}}
        assert _infer_direction(sig) == "bearish"

    def test_large_order_buy(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "large_order", "detail": {"direction": "BUY"}}
        assert _infer_direction(sig) == "bullish"

    def test_breakout_pullback(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "breakout_pullback", "detail": {}}
        assert _infer_direction(sig) == "bullish"

    def test_ma_bearish_align(self):
        from .signal_archiver import _infer_direction
        sig = {"strategy": "ma_bearish_align", "detail": {}}
        assert _infer_direction(sig) == "bearish"


# ---------------------------------------------------------------------------
# Integration: full pipeline mini-test
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_buy_then_stop_loss(self, rules):
        """Full pipeline: signal → mapper → engine → position → exit."""
        mapper = TradeSignalMapper(rules)
        engine = SimulationEngine(rules["cost_model"], rules["slippage"])
        pos_mgr = PositionManager(500_000, rules.get("lot_sizes", {}))

        # BUY signal
        sig = _make_signal("composite_bullish", "HK09988", ts=1000)
        decision = mapper.process_signal(sig, {}, 500_000, "2026-01-01")
        assert decision is not None
        assert decision.action == "BUY"

        # Execute trade
        exec_info = engine.execute_trade(decision, current_price=150.0, daily_amount=5e9, atr=3.0)
        cost = engine.calc_cost(exec_info["exec_price"], 300, "BUY")

        pos = pos_mgr.open_position(
            decision, exec_info["exec_price"], atr=3.0,
            trade_cost=cost["total"],
            current_date="2026-01-01", current_ts=1000,
        )
        assert pos is not None
        assert pos.code == "HK09988"

        # Price drops below stop loss
        closed = pos_mgr.check_exits(
            {"HK09988": pos.stop_loss - 1},
            day_index=1, current_date="2026-01-02",
        )
        assert len(closed) == 1
        assert closed[0]["pnl"] < 0
        assert "stop_loss" in closed[0]["exit_reason"]

    def test_buy_then_take_profit(self, rules):
        mapper = TradeSignalMapper(rules)
        engine = SimulationEngine(rules["cost_model"], rules["slippage"])
        pos_mgr = PositionManager(500_000, rules.get("lot_sizes", {}))

        sig = _make_signal("composite_bullish", "HK03986", ts=1000)
        decision = mapper.process_signal(sig, {}, 500_000, "2026-01-01")
        exec_info = engine.execute_trade(decision, current_price=400.0, daily_amount=1e9, atr=15.0)
        cost = engine.calc_cost(exec_info["exec_price"], 100, "BUY")

        pos = pos_mgr.open_position(
            decision, exec_info["exec_price"], atr=15.0,
            trade_cost=cost["total"],
            current_date="2026-01-01", current_ts=1000,
        )
        assert pos is not None

        # Price rises above take profit
        closed = pos_mgr.check_exits(
            {"HK03986": pos.take_profit + 10},
            day_index=1, current_date="2026-01-02",
        )
        assert len(closed) == 1
        assert closed[0]["pnl"] > 0


# ---------------------------------------------------------------------------
# Bug fix tests: min_hold guard, emergency stop
# ---------------------------------------------------------------------------

class TestMinHoldGuard:
    """Bug 2: check_exits() should respect min_hold_minutes."""

    def _open_pos(self, pos_mgr, code="HK00700", price=100.0, atr=1.5, entry_ts=1000000):
        """Open position with tight SL (100-2*1.5=97) so SL tests don't hit emergency."""
        decision = TradeDecision(
            action="BUY", code=code, confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        return pos_mgr.open_position(
            decision, price=price, atr=atr, trade_cost=50.0,
            current_date="2026-01-01", current_ts=entry_ts, day_index=0,
        )

    def test_sl_blocked_during_hold_period(self, pos_mgr):
        """SL should NOT trigger within min_hold period."""
        entry_ts = 1_000_000
        # SL = 100 - 2*1.5 = 97.0, so price 96.5 is below SL but > -5% (95.0)
        pos = self._open_pos(pos_mgr, entry_ts=entry_ts)
        assert pos.stop_loss == 97.0

        # 10 minutes later → within 30min hold period
        now_ts = entry_ts + 10 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": 96.5},  # Below SL (97) but above emergency (95)
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 0, "SL should be blocked during min_hold period"

    def test_sl_triggers_after_hold_period(self, pos_mgr):
        """SL should trigger normally after min_hold expires."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, entry_ts=entry_ts)

        # 35 minutes later → past hold period
        now_ts = entry_ts + 35 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": 96.5},  # Below SL (97) but above emergency (95)
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 1
        assert "stop_loss" in closed[0]["exit_reason"]

    def test_extreme_loss_bypasses_hold_period(self, pos_mgr):
        """Extreme loss (>8%) should trigger even during hold period."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, price=100.0, entry_ts=entry_ts)

        # 5 minutes later, price crashed 10%
        now_ts = entry_ts + 5 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": 90.0},  # -10% → extreme loss
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 1
        assert "emergency_stop" in closed[0]["exit_reason"]

    def test_tp_blocked_during_hold_period(self, pos_mgr):
        """TP should also be blocked during hold period."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, entry_ts=entry_ts)

        now_ts = entry_ts + 10 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": pos.take_profit + 10},
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 0, "TP should be blocked during min_hold period"

    def test_max_hold_not_affected_by_min_hold(self, pos_mgr):
        """Max hold days exit should NOT be blocked by min_hold."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, entry_ts=entry_ts)

        now_ts = entry_ts + 10 * 60 * 1000  # Still in hold period
        closed = pos_mgr.check_exits(
            {"HK00700": 100.0},  # Price unchanged
            day_index=10,  # Way past max_hold_days=5
            current_ts=now_ts,
            current_date="2026-01-10",
            min_hold_minutes=30,
        )
        assert len(closed) == 1
        assert "max_hold" in closed[0]["exit_reason"]


class TestEmergencyStop:
    """Bug 3: emergency stop at -5% independent of SL price."""

    def _open_pos(self, pos_mgr, code="HK00700", price=100.0, entry_ts=1000000):
        decision = TradeDecision(
            action="BUY", code=code, confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        return pos_mgr.open_position(
            decision, price=price, atr=5.0, trade_cost=50.0,
            current_date="2026-01-01", current_ts=entry_ts, day_index=0,
        )

    def test_emergency_stop_at_5pct(self, pos_mgr):
        """Price drop of 5% should trigger emergency stop."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, price=100.0, entry_ts=entry_ts)

        # SL is at 90.0 (100 - 2*5), but emergency triggers at 95.0 (-5%)
        now_ts = entry_ts + 60 * 60 * 1000  # 1 hour later
        closed = pos_mgr.check_exits(
            {"HK00700": 94.5},  # -5.5% < -5% threshold
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 1
        assert "emergency_stop" in closed[0]["exit_reason"]

    def test_no_emergency_at_4pct(self, pos_mgr):
        """Price drop of 4% should NOT trigger emergency stop (SL at 90%)."""
        entry_ts = 1_000_000
        pos = self._open_pos(pos_mgr, price=100.0, entry_ts=entry_ts)

        now_ts = entry_ts + 60 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": 96.0},  # -4%, above emergency but also above SL (90)
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 0

    def test_emergency_before_sl(self, pos_mgr):
        """Emergency stop should trigger before normal SL if loss > 5%."""
        entry_ts = 1_000_000
        # Use narrow ATR so SL is at 98 (100-2*1), but emergency is at 95
        decision = TradeDecision(
            action="BUY", code="HK00700", confidence=0.65,
            position_pct=0.10, stop_atr=2.0, max_hold_days=5,
            trigger_signal_ids=[1], reason="test",
        )
        pos = pos_mgr.open_position(
            decision, price=100.0, atr=1.0, trade_cost=50.0,
            current_date="2026-01-01", current_ts=entry_ts, day_index=0,
        )
        assert pos.stop_loss == 98.0  # Confirm narrow SL

        now_ts = entry_ts + 60 * 60 * 1000
        closed = pos_mgr.check_exits(
            {"HK00700": 94.0},  # -6%, below both SL (98) and emergency (95)
            day_index=0, current_ts=now_ts,
            current_date="2026-01-01",
            min_hold_minutes=30,
        )
        assert len(closed) == 1
        # Emergency stop takes priority since it's checked first
        assert "emergency_stop" in closed[0]["exit_reason"]


# ---------------------------------------------------------------------------
# Broker layer tests
# ---------------------------------------------------------------------------

def _make_decision(code="HK09988", position_pct=0.10):
    return TradeDecision(
        action="BUY", code=code, confidence=0.70,
        position_pct=position_pct, stop_atr=2.0, max_hold_days=5,
        trigger_signal_ids=[1], reason="test",
    )


def _make_adapter(buy_success=True, sell_success=True, order_id="ORD001",
                  fill_price=100.0, fill_status="FILLED_ALL"):
    """Build a minimal FutuTradeAdapter mock."""
    adapter = MagicMock()

    buy_result = MagicMock()
    buy_result.success = buy_success
    buy_result.order_id = order_id
    buy_result.error_msg = "buy_err"
    adapter.buy.return_value = buy_result

    sell_result = MagicMock()
    sell_result.success = sell_success
    sell_result.order_id = order_id
    sell_result.error_msg = "sell_err"
    adapter.sell.return_value = sell_result

    order_stub = MagicMock()
    order_stub.order_id = order_id
    order_stub.status = fill_status
    order_stub.avg_fill_price = fill_price
    adapter.get_today_orders.return_value = [order_stub]

    return adapter


# ---------------------------------------------------------------------------
# VirtualBroker
# ---------------------------------------------------------------------------

class TestVirtualBroker:

    def _make(self, rules):
        from .broker import VirtualBroker
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        return VirtualBroker(pm), pm

    def test_open_position_delegates_to_pm(self, rules):
        broker, pm = self._make(rules)
        decision = _make_decision("HK09988")
        pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                   trade_cost=50.0, date="2026-01-01",
                                   ts=1_000_000, day_index=0)
        assert pos is not None
        assert pos.code == "HK09988"
        assert "HK09988" in pm.positions

    def test_close_position_delegates_to_pm(self, rules):
        broker, pm = self._make(rules)
        decision = _make_decision("HK09988")
        broker.open_position(decision, exec_price=100.0, atr=2.0,
                             trade_cost=50.0, date="2026-01-01",
                             ts=1_000_000, day_index=0)
        trade = broker.close_position("HK09988", exec_price=105.0,
                                      reason="test", ts=2_000_000,
                                      date="2026-01-02", day_index=1)
        assert trade is not None
        assert "HK09988" not in pm.positions

    def test_check_exits_delegates_to_pm(self, rules):
        broker, pm = self._make(rules)
        decision = _make_decision("HK09988")
        pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                   trade_cost=50.0, date="2026-01-01",
                                   ts=1_000_000, day_index=0)
        # Price dropped below SL
        closed = broker.check_exits(
            {"HK09988": pos.stop_loss - 1},
            day_index=1, ts=2_000_000, date="2026-01-02",
        )
        assert len(closed) == 1
        assert "HK09988" not in pm.positions

    def test_tighten_stop_delegates_to_pm(self, rules):
        broker, pm = self._make(rules)
        decision = _make_decision("HK09988")
        pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                   trade_cost=50.0, date="2026-01-01",
                                   ts=1_000_000, day_index=0)
        old_sl = pos.stop_loss
        broker.tighten_stop("HK09988", stop_atr=1.5, price=110.0, atr=2.0)
        assert pm.positions["HK09988"].stop_loss >= old_sl

    def test_positions_and_cash_properties(self, rules):
        broker, pm = self._make(rules)
        assert broker.positions is pm.positions
        assert broker.cash == pm.cash

    def test_get_equity(self, rules):
        broker, pm = self._make(rules)
        equity = broker.get_equity({"HK09988": 100.0})
        assert equity == broker.cash  # no positions yet

    def test_sync_from_futu_is_noop(self, rules):
        broker, pm = self._make(rules)
        # Should not raise; VirtualBroker.sync_from_futu delegates to PM
        broker.sync_from_futu({}, 100_000.0)


# ---------------------------------------------------------------------------
# FutuBroker.open_position
# ---------------------------------------------------------------------------

class TestFutuBrokerOpenPosition:

    def _make(self, rules, **adapter_kwargs):
        from .broker import FutuBroker
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        adapter = _make_adapter(**adapter_kwargs)
        broker = FutuBroker(pm, adapter)
        return broker, pm, adapter

    def test_success_path_opens_shadow_pm(self, rules):
        """Futu buy succeeds → PM position opened with fill_price."""
        broker, pm, adapter = self._make(rules, fill_price=101.0)
        decision = _make_decision("HK09988")
        pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                   trade_cost=50.0, date="2026-01-01",
                                   ts=1_000_000, day_index=0)
        assert pos is not None
        assert "HK09988" in pm.positions
        adapter.buy.assert_called_once()
        # PM should use fill_price (101), not exec_price (100)
        assert pm.positions["HK09988"].entry_price == 101.0

    def test_futu_buy_failure_does_not_open_pm(self, rules):
        """Futu buy fails → PM must NOT record any position."""
        broker, pm, adapter = self._make(rules, buy_success=False)
        decision = _make_decision("HK09988")
        pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                   trade_cost=50.0, date="2026-01-01",
                                   ts=1_000_000, day_index=0)
        assert pos is None
        assert "HK09988" not in pm.positions

    def test_fill_timeout_falls_back_to_exec_price(self, rules):
        """If _wait_for_fill times out, use exec_price for PM."""
        broker, pm, adapter = self._make(rules, fill_status="SUBMITTED")
        decision = _make_decision("HK09988")
        # Patch timeout to 0 so the poll loop never fires
        with patch("src.sim_trading.broker.FILL_TIMEOUT", 0):
            pos = broker.open_position(decision, exec_price=100.0, atr=2.0,
                                       trade_cost=50.0, date="2026-01-01",
                                       ts=1_000_000, day_index=0)
        # Should still open (using exec_price fallback)
        assert pos is not None
        assert pm.positions["HK09988"].entry_price == 100.0

    def test_order_audit_saved_when_futu_sync_present(self, rules):
        """save_order() called on futu_sync when buy succeeds."""
        broker, pm, adapter = self._make(rules, fill_price=100.0)
        futu_sync = MagicMock()
        broker._futu_sync = futu_sync
        decision = _make_decision("HK09988")
        broker.open_position(decision, exec_price=100.0, atr=2.0,
                             trade_cost=50.0, date="2026-01-01",
                             ts=1_000_000, day_index=0)
        futu_sync.save_order.assert_called_once()


# ---------------------------------------------------------------------------
# FutuBroker.close_position
# ---------------------------------------------------------------------------

class TestFutuBrokerClosePosition:

    def _make_with_open_position(self, rules, close_fill_price=105.0):
        from .broker import FutuBroker
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        adapter = _make_adapter(fill_price=100.0)  # open at 100
        broker = FutuBroker(pm, adapter)
        decision = _make_decision("HK09988")
        # Open position: patch _wait_for_fill so entry is deterministically 100
        with patch.object(broker, '_wait_for_fill', return_value=100.0):
            broker.open_position(decision, exec_price=100.0, atr=2.0,
                                 trade_cost=50.0, date="2026-01-01",
                                 ts=1_000_000, day_index=0)
        # Now configure adapter to fill the SELL at close_fill_price
        adapter.get_today_orders.return_value[0].avg_fill_price = close_fill_price
        return broker, pm, adapter

    def test_success_path_closes_shadow_pm(self, rules):
        broker, pm, adapter = self._make_with_open_position(rules, close_fill_price=105.0)
        trade = broker.close_position("HK09988", exec_price=105.0,
                                      reason="take_profit", ts=2_000_000,
                                      date="2026-01-02", day_index=1)
        assert trade is not None
        assert "HK09988" not in pm.positions
        # entry=100, exit=105 — pnl positive after commission allocation
        assert trade["exit_price"] == 105.0

    def test_futu_sell_failure_does_not_close_pm(self, rules):
        """Futu SELL fails → PM position must remain open."""
        broker, pm, adapter = self._make_with_open_position(rules)
        adapter.sell.return_value.success = False
        trade = broker.close_position("HK09988", exec_price=105.0,
                                      reason="manual", ts=2_000_000,
                                      date="2026-01-02", day_index=1)
        assert trade is None
        assert "HK09988" in pm.positions

    def test_close_nonexistent_position_returns_none(self, rules):
        from .broker import FutuBroker
        pm = PositionManager(500_000, {})
        broker = FutuBroker(pm, _make_adapter())
        result = broker.close_position("HK99999", exec_price=100.0, reason="test")
        assert result is None


# ---------------------------------------------------------------------------
# FutuBroker.check_exits
# ---------------------------------------------------------------------------

class TestFutuBrokerCheckExits:

    def _setup(self, rules, sell_success=True):
        from .broker import FutuBroker
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        adapter = _make_adapter(sell_success=sell_success, fill_price=94.0)
        broker = FutuBroker(pm, adapter)
        # Patch _wait_for_fill so entry is deterministically 100
        # → SL = 100 - 2*2 = 96, TP ~ 100 + 3*2 = 106
        decision = _make_decision("HK09988")
        with patch.object(broker, '_wait_for_fill', return_value=100.0):
            broker.open_position(decision, exec_price=100.0, atr=2.0,
                                 trade_cost=50.0, date="2026-01-01",
                                 ts=1_000_000, day_index=0)
        return broker, pm, adapter

    def test_futu_sell_success_closes_pm(self, rules):
        """Futu SELL fills → PM position closed."""
        broker, pm, adapter = self._setup(rules, sell_success=True)
        # Price 94 < SL (96) → stop_loss
        closed = broker.check_exits({"HK09988": 94.0}, day_index=1,
                                    ts=2_000_000, date="2026-01-02")
        assert len(closed) == 1
        assert "HK09988" not in pm.positions
        adapter.sell.assert_called_once()

    def test_futu_sell_failure_still_closes_pm(self, rules):
        """Risk control priority: PM closes even when Futu SELL fails."""
        broker, pm, adapter = self._setup(rules, sell_success=False)
        # Price 94 < SL (96) — should trigger stop_loss
        closed = broker.check_exits({"HK09988": 94.0}, day_index=1,
                                    ts=2_000_000, date="2026-01-02")
        assert len(closed) == 1
        assert "HK09988" not in pm.positions  # PM still closed

    def test_no_exit_candidates_returns_empty(self, rules):
        broker, pm, _ = self._setup(rules)
        # 102 is between SL (96) and TP (~106) — no exit
        closed = broker.check_exits({"HK09988": 102.0}, day_index=1,
                                    ts=2_000_000, date="2026-01-02")
        assert closed == []
        assert "HK09988" in pm.positions

    def test_missing_price_skipped(self, rules):
        broker, pm, _ = self._setup(rules)
        closed = broker.check_exits({}, day_index=1, ts=2_000_000, date="2026-01-02")
        assert closed == []


# ---------------------------------------------------------------------------
# FutuBroker._wait_for_fill
# ---------------------------------------------------------------------------

class TestFutuBrokerWaitForFill:

    def _make_broker(self, rules):
        from .broker import FutuBroker
        pm = PositionManager(500_000, {})
        adapter = MagicMock()
        return FutuBroker(pm, adapter), adapter

    def test_immediate_fill_returns_price(self, rules):
        broker, adapter = self._make_broker(rules)
        order_stub = MagicMock()
        order_stub.order_id = "X1"
        order_stub.status = "FILLED_ALL"
        order_stub.avg_fill_price = 123.45
        adapter.get_today_orders.return_value = [order_stub]

        price = broker._wait_for_fill("X1", "HK09988")
        assert price == 123.45

    def test_fill_after_two_polls(self, rules):
        broker, adapter = self._make_broker(rules)
        pending = MagicMock()
        pending.order_id = "X2"
        pending.status = "SUBMITTED"
        pending.avg_fill_price = 0.0

        filled = MagicMock()
        filled.order_id = "X2"
        filled.status = "FILLED_ALL"
        filled.avg_fill_price = 99.0

        # First call returns pending, second returns filled
        adapter.get_today_orders.side_effect = [[pending], [filled]]

        with patch("src.sim_trading.broker.FILL_POLL_INTERVAL", 0):
            price = broker._wait_for_fill("X2", "HK09988")
        assert price == 99.0

    def test_timeout_returns_none(self, rules):
        broker, adapter = self._make_broker(rules)
        order_stub = MagicMock()
        order_stub.order_id = "X3"
        order_stub.status = "SUBMITTED"
        order_stub.avg_fill_price = 0.0
        adapter.get_today_orders.return_value = [order_stub]

        with patch("src.sim_trading.broker.FILL_TIMEOUT", 0):
            price = broker._wait_for_fill("X3", "HK09988")
        assert price is None

    def test_poll_exception_is_swallowed(self, rules):
        """Transient errors during polling should not crash."""
        broker, adapter = self._make_broker(rules)
        # First call raises, second returns filled
        filled = MagicMock()
        filled.order_id = "X4"
        filled.status = "FILLED_ALL"
        filled.avg_fill_price = 50.0
        adapter.get_today_orders.side_effect = [Exception("connection lost"), [filled]]

        with patch("src.sim_trading.broker.FILL_POLL_INTERVAL", 0):
            price = broker._wait_for_fill("X4", "HK09988")
        assert price == 50.0


# ---------------------------------------------------------------------------
# FutuBroker._get_exit_candidates (condition logic)
# ---------------------------------------------------------------------------

class TestFutuBrokerGetExitCandidates:

    def _setup(self, rules):
        from .broker import FutuBroker
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        broker = FutuBroker(pm, _make_adapter())
        decision = _make_decision("HK09988")
        # Open: entry 100, SL=96, TP via take_profit field
        broker.open_position(decision, exec_price=100.0, atr=2.0,
                             trade_cost=50.0, date="2026-01-01",
                             ts=1_000_000, day_index=0)
        return broker, pm

    def test_stop_loss_candidate(self, rules):
        broker, pm = self._setup(rules)
        sl = pm.positions["HK09988"].stop_loss
        candidates = broker._get_exit_candidates(
            {"HK09988": sl - 0.5}, day_index=1, ts=2_000_000, min_hold_minutes=0
        )
        assert len(candidates) == 1
        code, price, reason, qty = candidates[0]
        assert code == "HK09988"
        assert "stop_loss" in reason

    def test_emergency_stop_candidate(self, rules):
        broker, _ = self._setup(rules)
        # -6% → emergency_stop
        candidates = broker._get_exit_candidates(
            {"HK09988": 94.0}, day_index=1, ts=2_000_000, min_hold_minutes=0
        )
        assert len(candidates) == 1
        assert "emergency_stop" in candidates[0][2]

    def test_max_hold_candidate(self, rules):
        broker, pm = self._setup(rules)
        max_hold = pm.positions["HK09988"].max_hold_days
        candidates = broker._get_exit_candidates(
            {"HK09988": 100.0}, day_index=max_hold + 1,
            ts=2_000_000, min_hold_minutes=0
        )
        assert len(candidates) == 1
        assert "max_hold" in candidates[0][2]

    def test_hold_period_blocks_sl(self, rules):
        broker, pm = self._setup(rules)
        sl = pm.positions["HK09988"].stop_loss
        # Within 30 min hold period
        entry_ts = 1_000_000
        now_ts = entry_ts + 10 * 60 * 1000
        candidates = broker._get_exit_candidates(
            {"HK09988": sl - 0.5}, day_index=1,
            ts=now_ts, min_hold_minutes=30
        )
        assert candidates == []

    def test_no_candidates_when_price_ok(self, rules):
        broker, _ = self._setup(rules)
        candidates = broker._get_exit_candidates(
            {"HK09988": 102.0}, day_index=1, ts=2_000_000, min_hold_minutes=0
        )
        assert candidates == []


# ── TestHKTickRounding ─────────────────────────────────────────────────────────


class TestHKTickRounding:
    """FutuTradeAdapter._round_to_hk_tick — 港交所 tick size 取整。"""

    def _r(self, price):
        from src.sim_trading.futu_trade_adapter import FutuTradeAdapter
        return FutuTradeAdapter._round_to_hk_tick(price)

    def test_sub_025_tick_0001(self):
        assert self._r(0.123) == 0.123
        assert self._r(0.1234) == 0.123

    def test_025_to_050_tick_0005(self):
        assert self._r(0.333) == 0.335
        assert self._r(0.450) == 0.450

    def test_050_to_10_tick_001(self):
        assert self._r(3.4168) == 3.42
        assert self._r(5.005) == 5.00  # banker's rounding: 500.5 → 500

    def test_20_to_100_tick_005(self):
        # Previously failing: slippage-rounded price rejected by Futu
        assert self._r(65.13) == 65.15
        assert self._r(98.12) == 98.10

    def test_100_to_200_tick_010(self):
        assert self._r(130.44) == 130.40
        assert self._r(150.16) == 150.20

    def test_200_to_500_tick_020(self):
        assert self._r(250.13) == 250.20   # 200-500 range, tick=0.2
        assert self._r(299.91) == 300.00

    def test_500_to_1000_tick_050(self):
        assert self._r(526.23) == 526.00   # 500-1000 range, tick=0.5

    def test_already_aligned_unchanged(self):
        assert self._r(65.15) == 65.15
        assert self._r(130.4) == 130.4
        assert self._r(3.42) == 3.42

    def test_buy_applies_tick_rounding(self):
        """buy() 下单前自动取整，不会因精度被 Futu 拒绝。"""
        from src.sim_trading.futu_trade_adapter import FutuTradeAdapter
        adapter = MagicMock(spec=FutuTradeAdapter)
        adapter._round_to_hk_tick = FutuTradeAdapter._round_to_hk_tick
        adapter.buy = FutuTradeAdapter.buy.__get__(adapter, FutuTradeAdapter)

        captured = []
        def fake_place_order(code, price, qty, side, order_type):
            captured.append(price)
            return MagicMock(success=True, order_id="X")
        adapter._place_order = fake_place_order

        adapter.buy("HK02577", 65.13, 100)
        assert captured[0] == 65.15, f"Expected 65.15, got {captured[0]}"

    def test_sell_applies_tick_rounding(self):
        """sell() 下单前自动取整。"""
        from src.sim_trading.futu_trade_adapter import FutuTradeAdapter
        adapter = MagicMock(spec=FutuTradeAdapter)
        adapter._round_to_hk_tick = FutuTradeAdapter._round_to_hk_tick
        adapter.sell = FutuTradeAdapter.sell.__get__(adapter, FutuTradeAdapter)

        captured = []
        def fake_place_order(code, price, qty, side, order_type):
            captured.append(price)
            return MagicMock(success=True, order_id="X")
        adapter._place_order = fake_place_order
        # Bypass T+1 guard (non-HK not needed, but make it HK)
        adapter._get_today_buy_qty = MagicMock(return_value=0)

        adapter.sell("HK09988", 130.44, 100)
        assert captured[0] == 130.40, f"Expected 130.40, got {captured[0]}"

    def test_a_share_not_rounded(self):
        """A 股不走港交所 tick size 逻辑。"""
        from src.sim_trading.futu_trade_adapter import FutuTradeAdapter
        adapter = MagicMock(spec=FutuTradeAdapter)
        adapter._round_to_hk_tick = FutuTradeAdapter._round_to_hk_tick
        adapter.buy = FutuTradeAdapter.buy.__get__(adapter, FutuTradeAdapter)

        captured = []
        def fake_place_order(code, price, qty, side, order_type):
            captured.append(price)
            return MagicMock(success=True, order_id="X")
        adapter._place_order = fake_place_order

        adapter.buy("000792", 18.513, 100)
        assert captured[0] == 18.513  # A 股原价传入，不取整


# ── TestEvaluateEntriesFallback ───────────────────────────────────────────────


class TestEvaluateEntriesFallback:
    """当第一候选股 Futu 下单失败时，应继续尝试下一候选股。"""

    def test_fallback_to_second_candidate_when_first_futu_fails(self, rules):
        """top candidate Futu 拒绝 → second candidate 成功开仓。"""
        from src.sim_trading.broker import FutuBroker
        from src.sim_trading.realtime_engine import RealtimeSimEngine

        lot_sizes = rules.get("lot_sizes", {})
        pm = PositionManager(initial_capital=500_000, lot_sizes=lot_sizes)
        adapter = MagicMock()

        # First BUY call fails (price precision), second succeeds
        fail_result = MagicMock(); fail_result.success = False
        fail_result.error_msg = "价格参数精度不符合规范"
        ok_result = MagicMock(); ok_result.success = True; ok_result.order_id = "ORD002"
        adapter.buy.side_effect = [fail_result, ok_result]

        order_stub = MagicMock()
        order_stub.order_id = "ORD002"
        order_stub.status = "FILLED_ALL"
        order_stub.avg_fill_price = 98.0
        adapter.get_today_orders.return_value = [order_stub]

        broker = FutuBroker(pm, adapter)

        engine = RealtimeSimEngine.__new__(RealtimeSimEngine)
        engine._broker = broker
        engine._pos_mgr = pm
        engine._new_positions_today = 0
        engine._last_exit_ts = {}
        engine._day_index = 1

        # Inject two candidates directly (bypass scoring)
        from unittest.mock import patch
        score_high = {"total": 76, "action": "BUY", "atr": 2.0,
                      "stop_loss": 63.0, "take_profit": 71.0}
        score_low  = {"total": 73, "action": "BUY", "atr": 2.0,
                      "stop_loss": 94.0, "take_profit": 106.0}
        candidates = [("HK02577", score_high), ("HK01211", score_low)]

        from src.sim_trading.simulation_engine import SimulationEngine
        sim_engine = MagicMock(spec=SimulationEngine)
        sim_engine.execute_trade.side_effect = lambda d, p, amt, atr: {"exec_price": p, "slippage_pct": 0}
        sim_engine.calc_cost.return_value = {"total": 50}

        engine._engine = sim_engine
        engine._score_cfg = rules.get("daily_score", {})

        prices  = {"HK02577": 65.15, "HK01211": 98.0}
        market  = {"HK02577": {"amount": 1e8}, "HK01211": {"amount": 1e8}}

        with patch.object(engine, '_get_watchlist_codes', return_value=[]):
            # Call internal loop directly
            engine._run_candidates(candidates, prices, market, "2026-03-10")

        # HK02577 failed → HK01211 opened
        assert "HK01211" in pm.positions
        assert "HK02577" not in pm.positions
        assert engine._new_positions_today == 1


# ---------------------------------------------------------------------------
# Regression: sync_from_futu preserves entry_time (T3 min_hold bug)
# ---------------------------------------------------------------------------

class TestSyncFromFutuPreservesEntryTime:
    """Regression tests for the T3/min_hold bug.

    Root cause: pm.sync_from_futu() was clearing all positions and
    re-creating them with entry_time=0. This caused hold_ms = now_ts - 0
    to be huge, bypassing the 30-min min_hold guard and allowing T3
    large_order_reversal to fire immediately after a buy.

    Fix 1 (root): sync_from_futu preserves existing positions' entry_time.
    Fix 2 (guard): _handle_t3_with_filters blocks when entry_time==0.
    """

    def _make_futu_position(self, code, avg_price=100.0, qty=100, market_val=None):
        fp = MagicMock()
        fp.avg_price = avg_price
        fp.quantity = qty
        fp.market_val = market_val or avg_price * qty
        return fp

    def test_existing_position_entry_time_preserved(self, rules):
        """sync_from_futu must NOT overwrite entry_time of existing position."""
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        decision = _make_decision("HK00700")
        entry_ts = 1_600_000_000_000
        pm.open_position(decision, price=100.0, atr=2.0, trade_cost=50.0,
                         current_date="2026-03-11", current_ts=entry_ts,
                         day_index=0)
        assert pm.positions["HK00700"].entry_time == entry_ts

        # Simulate tick-level Futu sync (called every 3s in daemon)
        fp = self._make_futu_position("HK00700", avg_price=101.0, qty=100)
        pm.sync_from_futu({"HK00700": fp}, cash=450_000)

        # entry_time must be preserved, not reset to 0
        assert pm.positions["HK00700"].entry_time == entry_ts, (
            "sync_from_futu must preserve entry_time for existing positions"
        )

    def test_existing_position_price_qty_updated(self, rules):
        """sync_from_futu should still refresh entry_price and quantity."""
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        decision = _make_decision("HK00700")
        pm.open_position(decision, price=100.0, atr=2.0, trade_cost=50.0,
                         current_date="2026-03-11", current_ts=1_600_000_000_000,
                         day_index=0)

        fp = self._make_futu_position("HK00700", avg_price=102.5, qty=200)
        pm.sync_from_futu({"HK00700": fp}, cash=400_000)

        pos = pm.positions["HK00700"]
        assert pos.entry_price == 102.5
        assert pos.quantity == 200
        assert pm.cash == 400_000

    def test_new_position_gets_now_ts_not_zero(self, rules):
        """New position discovered via Futu sync gets now_ts, not 0."""
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        before = int(time.time() * 1000)

        fp = self._make_futu_position("HK00700", avg_price=100.0, qty=100)
        pm.sync_from_futu({"HK00700": fp}, cash=490_000)

        after = int(time.time() * 1000)
        entry_ts = pm.positions["HK00700"].entry_time
        assert entry_ts != 0, "New Futu-synced position must not have entry_time=0"
        assert before <= entry_ts <= after

    def test_position_removed_when_gone_from_futu(self, rules):
        """Position closed in Futu must be removed from shadow PM on next sync."""
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        decision = _make_decision("HK00700")
        pm.open_position(decision, price=100.0, atr=2.0, trade_cost=50.0,
                         current_date="2026-03-11", current_ts=1_600_000_000_000,
                         day_index=0)
        assert "HK00700" in pm.positions

        # Futu reports empty positions → position was closed externally
        pm.sync_from_futu({}, cash=500_000)
        assert "HK00700" not in pm.positions

    def test_repeated_syncs_do_not_reset_entry_time(self, rules):
        """Multiple consecutive syncs (as happens every 3s) must not reset entry_time."""
        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        decision = _make_decision("HK00700")
        entry_ts = 1_600_000_000_000
        pm.open_position(decision, price=100.0, atr=2.0, trade_cost=50.0,
                         current_date="2026-03-11", current_ts=entry_ts,
                         day_index=0)

        fp = self._make_futu_position("HK00700")
        for _ in range(10):
            pm.sync_from_futu({"HK00700": fp}, cash=450_000)

        assert pm.positions["HK00700"].entry_time == entry_ts, (
            "10 consecutive syncs must not drift entry_time"
        )


class TestT3EntryTimeZeroGuard:
    """T3 _handle_t3_with_filters: entry_time=0 must block the sell.

    This is the defense-in-depth fix. Even if sync_from_futu somehow
    produces entry_time=0, T3 must not fire on an unknown hold time.
    """

    def _make_engine_with_position(self, rules, entry_time, price=564.0):
        """Create a minimal RealtimeSimEngine with one open position."""
        from .realtime_engine import RealtimeSimEngine
        from .broker import VirtualBroker

        engine = RealtimeSimEngine.__new__(RealtimeSimEngine)
        engine._rules = rules
        engine._score_cfg = rules.get("daily_score", {})
        engine._current_date = "2026-03-11"
        engine._day_index = 0
        engine._last_exit_ts = {}

        pm = PositionManager(500_000, rules.get("lot_sizes", {}))
        pm._positions["HK00700"] = Position(
            code="HK00700", entry_price=price, quantity=100,
            entry_time=entry_time, entry_date="2026-03-11",
            stop_loss=price * 0.90, take_profit=price * 1.10,
            max_hold_days=10, confidence=0.7,
        )
        engine._broker = VirtualBroker(pm)
        engine._pos_mgr = pm
        engine._mapper = MagicMock()
        engine._engine = MagicMock()
        engine._daily_tracker = None
        engine._futu = None
        engine._futu_enabled = False
        return engine, pm

    def test_t3_blocked_when_entry_time_zero(self, rules):
        """entry_time=0 → T3 must be blocked regardless of strategy."""
        engine, pm = self._make_engine_with_position(rules, entry_time=0)

        sig = _make_signal(
            "large_order_reversal", "HK00700",
            direction="bearish",
            detail={"direction": "bearish"},
            ts=int(time.time() * 1000),
        )
        prices = {"HK00700": 560.0}  # price below entry → loss → would normally trigger
        market = {"HK00700": {"amount": 1_000_000_000}}
        now_ts = int(time.time() * 1000)

        engine._handle_t3_with_filters(sig, prices, market, "2026-03-11")

        # Position must NOT be closed
        assert "HK00700" in pm.positions, (
            "T3 must not close position when entry_time=0"
        )
        engine._broker.close_position  # just access; broker.close_position not called
        # Verify: no sell was dispatched (mapper was not called for a SELL)
        engine._mapper.process_signal.assert_not_called()

    def test_t3_fires_normally_after_min_hold(self, rules):
        """T3 fires normally once position is past min_hold with valid entry_time."""
        min_hold_ms = rules.get("daily_score", {}).get("min_hold_minutes", 30) * 60 * 1000
        entry_ts = int(time.time() * 1000) - min_hold_ms - 5_000  # 5s past hold
        engine, pm = self._make_engine_with_position(
            rules, entry_time=entry_ts, price=564.0
        )

        # Mock mapper to return a SELL decision
        sell_decision = MagicMock()
        sell_decision.action = "SELL"
        sell_decision.position_pct = 1.0
        engine._mapper.process_signal.return_value = sell_decision
        engine._engine.execute_trade.return_value = {"exec_price": 558.0}
        engine._engine.calc_cost.return_value = {"total": 50.0}

        sig = _make_signal(
            "large_order_reversal", "HK00700",
            direction="bearish",
            detail={"direction": "bearish"},
            ts=int(time.time() * 1000),
        )
        prices = {"HK00700": 558.0}  # below entry (564) → loss
        market = {"HK00700": {"amount": 1_000_000_000}}

        engine._handle_t3_with_filters(sig, prices, market, "2026-03-11")

        engine._mapper.process_signal.assert_called_once()

    def test_t3_blocked_within_min_hold_valid_entry_time(self, rules):
        """T3 blocked when within min_hold period even with valid entry_time."""
        # entry just 10 minutes ago
        entry_ts = int(time.time() * 1000) - 10 * 60 * 1000
        engine, pm = self._make_engine_with_position(
            rules, entry_time=entry_ts, price=564.0
        )

        sig = _make_signal(
            "large_order_reversal", "HK00700",
            direction="bearish",
            detail={"direction": "bearish"},
            ts=int(time.time() * 1000),
        )
        prices = {"HK00700": 558.0}
        market = {"HK00700": {"amount": 1_000_000_000}}

        engine._handle_t3_with_filters(sig, prices, market, "2026-03-11")

        # Still in min_hold window → no sell
        assert "HK00700" in pm.positions
        engine._mapper.process_signal.assert_not_called()
