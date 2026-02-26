"""Tests for sim_trading Phase 2 modules.

Run: poetry run pytest src/sim_trading/test_sim_trading.py -v
"""

import json
import math
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

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
