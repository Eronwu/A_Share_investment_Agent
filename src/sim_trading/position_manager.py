"""PositionManager — 虚拟持仓管理。

管理模拟交易的持仓生命周期:
- 开仓 (lot-size 对齐)
- 平仓 (全部/部分)
- 止损/止盈/到期检查
- 实时权益计算
"""

import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("position_manager")


@dataclass
class Position:
    code: str
    entry_price: float
    quantity: int
    entry_time: int  # epoch ms
    entry_date: str
    stop_loss: float
    take_profit: float | None
    max_hold_days: int
    confidence: float
    trigger_signals: list[int] = field(default_factory=list)
    entry_strategy: str = ""  # the signal strategy that opened this position
    # Tracking
    highest_price: float = 0.0  # for trailing stop
    entry_day_index: int = 0  # trading day index since entry


class PositionManager:
    """Manage virtual positions with lot-size alignment and exit rules."""

    def __init__(self, initial_capital: float, lot_sizes: dict):
        self._cash = initial_capital
        self._initial_capital = initial_capital
        self._lot_sizes = lot_sizes  # {code: lot_size}
        self._positions: dict[str, Position] = {}
        self._closed_trades: list[dict] = []

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    @property
    def closed_trades(self) -> list[dict]:
        return self._closed_trades

    def _align_lot(self, code: str, shares: int) -> int:
        """Round down to nearest lot size."""
        lot = self._lot_sizes.get(code, 100)
        return (shares // lot) * lot

    def open_position(
        self,
        decision,  # TradeDecision
        price: float,
        atr: float,
        trade_cost: float,
        current_date: str,
        current_ts: int,
        day_index: int = 0,
    ) -> Position | None:
        """Open a new position or add to existing.

        Returns the Position if opened, None if insufficient funds or lot alignment yields 0.
        """
        code = decision.code

        # Already have a position — skip (no pyramiding in v1)
        if code in self._positions:
            logger.debug(f"Already holding {code}, skip BUY")
            return None

        # Calculate shares
        alloc = self._cash * decision.position_pct
        if alloc <= 0:
            return None

        raw_shares = int(alloc / price)
        shares = self._align_lot(code, raw_shares)
        if shares <= 0:
            logger.debug(f"Lot alignment yields 0 for {code} (price={price:.2f})")
            return None

        cost_value = shares * price + trade_cost
        if cost_value > self._cash:
            # Try one lot less
            shares = self._align_lot(code, shares - 1)
            if shares <= 0:
                return None
            cost_value = shares * price + trade_cost
            if cost_value > self._cash:
                return None

        # Calculate stop loss
        stop_loss = price - decision.stop_atr * atr
        if stop_loss <= 0:
            stop_loss = price * 0.90  # fallback: 10% stop

        # Take profit: 3x ATR above entry (risk:reward ~ 1:1.5)
        take_profit = price + decision.stop_atr * 1.5 * atr

        self._cash -= cost_value
        pos = Position(
            code=code,
            entry_price=price,
            quantity=shares,
            entry_time=current_ts,
            entry_date=current_date,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold_days=decision.max_hold_days,
            confidence=decision.confidence,
            trigger_signals=list(decision.trigger_signal_ids),
            entry_strategy=decision.reason,
            highest_price=price,
            entry_day_index=day_index,
        )
        self._positions[code] = pos
        logger.info(
            f"OPEN {code}: {shares} @ {price:.2f}, SL={stop_loss:.2f}, "
            f"TP={take_profit:.2f}, cost={trade_cost:.2f}"
        )
        return pos

    def close_position(
        self,
        code: str,
        price: float,
        reason: str,
        pct: float = 1.0,
        trade_cost: float = 0.0,
        current_ts: int = 0,
        current_date: str = "",
        day_index: int = 0,
    ) -> dict | None:
        """Close all or part of a position.

        Returns trade record dict, or None if no position exists.
        """
        pos = self._positions.get(code)
        if not pos:
            return None

        close_qty = self._align_lot(code, int(pos.quantity * pct))
        if close_qty <= 0:
            close_qty = pos.quantity  # Close all if partial rounds to 0

        proceeds = close_qty * price - trade_cost
        self._cash += proceeds

        pnl = (price - pos.entry_price) * close_qty - trade_cost
        pnl_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        hold_days = day_index - pos.entry_day_index

        trade = {
            "trade_id": str(uuid.uuid4())[:8],
            "code": code,
            "action": "SELL",
            "direction": "close_long",
            "entry_price": pos.entry_price,
            "exit_price": price,
            "quantity": close_qty,
            "entry_time": pos.entry_time,
            "exit_time": current_ts,
            "entry_date": pos.entry_date,
            "exit_date": current_date,
            "hold_days": hold_days,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 6),
            "commission": round(trade_cost, 2),
            "confidence": pos.confidence,
            "trigger_signals": pos.trigger_signals,
            "exit_reason": reason,
            "notes": pos.entry_strategy,
        }

        remaining = pos.quantity - close_qty
        if remaining > 0:
            pos.quantity = remaining
        else:
            del self._positions[code]

        self._closed_trades.append(trade)
        logger.info(
            f"CLOSE {code}: {close_qty} @ {price:.2f}, PnL={pnl:+.2f} ({pnl_pct:+.2%}), "
            f"reason={reason}"
        )
        return trade

    def check_exits(
        self,
        prices: dict[str, float],
        day_index: int,
        current_ts: int = 0,
        current_date: str = "",
        cost_calculator=None,
        min_hold_minutes: int = 0,
    ) -> list[dict]:
        """Check all positions for stop-loss, take-profit, or max-hold exits.

        Args:
            prices: {code: current_price}
            day_index: current trading day index
            current_ts: epoch ms
            current_date: YYYY-MM-DD
            cost_calculator: optional callable(price, qty, action) → cost
            min_hold_minutes: skip SL/TP exits within this period (extreme loss exempt)

        Returns list of closed trade records.
        """
        closed = []
        codes = list(self._positions.keys())

        for code in codes:
            pos = self._positions.get(code)
            if not pos:
                continue

            price = prices.get(code)
            if price is None or price <= 0:
                continue

            # Update highest price for trailing stop
            if price > pos.highest_price:
                pos.highest_price = price

            # min_hold guard: skip normal SL/TP during hold period
            # Exception: extreme loss (>8%) or emergency stop (>5%) always exits
            hold_minutes = (
                (current_ts - pos.entry_time) / 60000
                if current_ts and pos.entry_time else 999
            )
            pnl_pct = (
                (price - pos.entry_price) / pos.entry_price
                if pos.entry_price > 0 else 0
            )
            extreme_loss = pnl_pct <= -0.08
            in_hold_period = (
                hold_minutes < min_hold_minutes
                and not extreme_loss
            )

            reason = None

            # Emergency stop: -5% hard cap (independent of SL price)
            if pnl_pct <= -0.05 and not in_hold_period:
                reason = f"emergency_stop({pnl_pct:.1%})"

            # Extreme loss: always exit regardless of hold period
            elif extreme_loss:
                reason = f"emergency_stop({pnl_pct:.1%})"

            # Stop loss (skipped during hold period)
            elif price <= pos.stop_loss and not in_hold_period:
                reason = f"stop_loss({pos.stop_loss:.2f})"

            # Take profit (skipped during hold period)
            elif pos.take_profit and price >= pos.take_profit and not in_hold_period:
                reason = f"take_profit({pos.take_profit:.2f})"

            # Max hold days
            elif (day_index - pos.entry_day_index) >= pos.max_hold_days:
                reason = f"max_hold({pos.max_hold_days}d)"

            if reason:
                trade_cost = 0.0
                if cost_calculator:
                    cost_info = cost_calculator(price, pos.quantity, "SELL")
                    trade_cost = cost_info.get("total", 0)

                trade = self.close_position(
                    code, price, reason,
                    pct=1.0,
                    trade_cost=trade_cost,
                    current_ts=current_ts,
                    current_date=current_date,
                    day_index=day_index,
                )
                if trade:
                    closed.append(trade)

        return closed

    def tighten_stop(self, code: str, new_atr_mult: float, price: float, atr: float):
        """Tighten stop loss to a tighter ATR multiple."""
        pos = self._positions.get(code)
        if not pos:
            return

        new_stop = price - new_atr_mult * atr
        if new_stop > pos.stop_loss:
            old_stop = pos.stop_loss
            pos.stop_loss = new_stop
            logger.info(f"TIGHTEN SL {code}: {old_stop:.2f} → {new_stop:.2f}")

    def get_equity(self, prices: dict[str, float]) -> float:
        """Calculate total equity = cash + market value of all positions."""
        market_value = 0.0
        for code, pos in self._positions.items():
            price = prices.get(code, pos.entry_price)
            market_value += price * pos.quantity
        return self._cash + market_value

    def get_invested(self, prices: dict[str, float]) -> float:
        """Total market value of invested positions."""
        return sum(
            prices.get(code, pos.entry_price) * pos.quantity
            for code, pos in self._positions.items()
        )

    def snapshot(self, prices: dict[str, float]) -> dict:
        """Current portfolio snapshot."""
        equity = self.get_equity(prices)
        invested = self.get_invested(prices)
        positions = {}
        for code, pos in self._positions.items():
            price = prices.get(code, pos.entry_price)
            unrealized = (price - pos.entry_price) * pos.quantity
            positions[code] = {
                "entry_price": pos.entry_price,
                "current_price": price,
                "quantity": pos.quantity,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "unrealized_pnl": round(unrealized, 2),
                "hold_days_target": pos.max_hold_days,
            }
        return {
            "equity": round(equity, 2),
            "cash": round(self._cash, 2),
            "invested": round(invested, 2),
            "invested_pct": round(invested / equity, 4) if equity > 0 else 0,
            "n_positions": len(self._positions),
            "positions": positions,
        }
