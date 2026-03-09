"""AbstractBroker — Strategy pattern for order execution.

Defines a uniform interface for position management, with two implementations:
  - VirtualBroker: pure in-memory PositionManager, no real exchange
  - FutuBroker: Futu-first — submit order to Futu, wait for fill confirmation,
                then write to shadow PositionManager (Plan A consistency)

Write consistency (Plan A):
  open_position:  Futu BUY  → wait fill → shadow PM open
  close_position: Futu SELL → wait fill → shadow PM close
  check_exits:    detect conditions on shadow PM → Futu SELL (best-effort) → shadow PM close
    Note: stop-loss / take-profit always close shadow PM even if Futu SELL fails,
    because risk control must not be blocked by connectivity issues.
"""

import logging
import time
from abc import ABC, abstractmethod

from .position_manager import Position, PositionManager

logger = logging.getLogger("l2_daemon.broker")


class AbstractBroker(ABC):
    """Abstract interface for order execution and position management."""

    @abstractmethod
    def open_position(
        self, decision, exec_price: float, atr: float, trade_cost: float,
        date: str, ts: int, day_index: int,
    ) -> "Position | None":
        """Open a new position. Returns Position if successful, None otherwise."""

    @abstractmethod
    def close_position(
        self, code: str, exec_price: float, reason: str,
        pct: float = 1.0, trade_cost: float = 0.0,
        ts: int = 0, date: str = "", day_index: int = 0,
    ) -> "dict | None":
        """Close a position. Returns trade record dict, or None if no position."""

    @abstractmethod
    def check_exits(
        self, prices: dict, day_index: int,
        ts: int = 0, date: str = "",
        cost_calculator=None, min_hold_minutes: int = 0,
    ) -> list[dict]:
        """Check stop-loss / take-profit / max-hold conditions. Returns closed trades."""

    @abstractmethod
    def tighten_stop(self, code: str, stop_atr: float, price: float, atr: float) -> None:
        """Tighten stop loss for an existing position."""

    @property
    @abstractmethod
    def positions(self) -> dict:
        """Current open positions {code: Position}."""

    @property
    @abstractmethod
    def cash(self) -> float:
        """Available cash."""

    def get_equity(self, prices: dict) -> float:
        """Total equity = cash + market value of all positions."""
        mv = sum(prices.get(code, pos.entry_price) * pos.quantity
                 for code, pos in self.positions.items())
        return self.cash + mv

    def sync_from_futu(self, futu_positions: dict, cash: float) -> None:
        """Sync shadow PM state from Futu positions (no-op for VirtualBroker)."""


# ── VirtualBroker ─────────────────────────────────────────────────────────────


class VirtualBroker(AbstractBroker):
    """Virtual broker: delegates entirely to PositionManager. No real trading."""

    def __init__(self, pos_mgr: PositionManager):
        self._pm = pos_mgr

    def open_position(self, decision, exec_price, atr, trade_cost, date, ts, day_index):
        return self._pm.open_position(
            decision, exec_price, atr, trade_cost,
            current_date=date, current_ts=ts, day_index=day_index,
        )

    def close_position(self, code, exec_price, reason, pct=1.0, trade_cost=0.0,
                       ts=0, date="", day_index=0):
        return self._pm.close_position(
            code, exec_price, reason,
            pct=pct, trade_cost=trade_cost,
            current_ts=ts, current_date=date, day_index=day_index,
        )

    def check_exits(self, prices, day_index, ts=0, date="",
                    cost_calculator=None, min_hold_minutes=0):
        return self._pm.check_exits(
            prices, day_index,
            current_ts=ts, current_date=date,
            cost_calculator=cost_calculator,
            min_hold_minutes=min_hold_minutes,
        )

    def tighten_stop(self, code, stop_atr, price, atr):
        self._pm.tighten_stop(code, stop_atr, price, atr)

    @property
    def positions(self):
        return self._pm.positions

    @property
    def cash(self):
        return self._pm.cash

    def sync_from_futu(self, futu_positions, cash):
        self._pm.sync_from_futu(futu_positions, cash)


# ── FutuBroker ────────────────────────────────────────────────────────────────


FILL_POLL_INTERVAL = 0.5   # seconds between fill status polls
FILL_TIMEOUT = 30          # seconds to wait for fill confirmation


class FutuBroker(AbstractBroker):
    """Futu-first broker: send order to Futu, wait for fill, then write shadow PM.

    Consistency guarantee (Plan A):
      - BUY:  Futu order succeeds + filled → open shadow PM position
      - SELL: Futu order succeeds + filled → close shadow PM position
      - Stop-loss exits: attempt Futu SELL (best-effort), always close shadow PM
    """

    def __init__(self, pos_mgr: PositionManager, adapter, futu_sync=None):
        """
        Args:
            pos_mgr: Shadow PositionManager for risk tracking
            adapter: FutuTradeAdapter instance
            futu_sync: Optional FutuPositionSync for order audit
        """
        self._pm = pos_mgr
        self._adapter = adapter
        self._futu_sync = futu_sync

    # ── AbstractBroker implementation ──────────────────────────────────────

    def open_position(self, decision, exec_price, atr, trade_cost, date, ts, day_index):
        """Futu-first BUY: send order → wait fill → open shadow PM."""
        code = decision.code

        # Pre-calculate qty (mirrors PositionManager.open_position logic)
        alloc = self._pm.cash * decision.position_pct
        if alloc <= 0:
            return None
        qty = self._pm._align_lot(code, int(alloc / exec_price))
        if qty <= 0:
            logger.debug(f"FutuBroker: lot alignment yields 0 for {code}")
            return None

        # Submit to Futu first
        result = self._adapter.buy(code, exec_price, qty)
        if not result.success:
            logger.warning(f"FutuBroker: BUY failed for {code}: {result.error_msg}")
            return None

        # Wait for fill
        fill_price = self._wait_for_fill(result.order_id, code) or exec_price
        logger.info(
            f"FutuBroker: BUY filled {code} {qty}@{fill_price:.2f} "
            f"order={result.order_id}"
        )

        # Record order in audit table
        if self._futu_sync:
            self._futu_sync.save_order(
                result.order_id, code, "BUY", fill_price, qty,
                reason=f"broker:open:{decision.reason[:40]}",
            )

        # Open shadow PM position (using fill_price, not exec_price)
        pos = self._pm.open_position(
            decision, fill_price, atr, trade_cost,
            current_date=date, current_ts=ts, day_index=day_index,
        )
        return pos

    def close_position(self, code, exec_price, reason, pct=1.0, trade_cost=0.0,
                       ts=0, date="", day_index=0):
        """Futu-first SELL: send order → wait fill → close shadow PM."""
        pos = self._pm.positions.get(code)
        if not pos:
            return None

        close_qty = self._pm._align_lot(code, int(pos.quantity * pct)) or pos.quantity

        # Submit to Futu first
        result = self._adapter.sell(code, exec_price, close_qty)
        if not result.success:
            logger.warning(f"FutuBroker: SELL failed for {code}: {result.error_msg}")
            return None

        # Wait for fill
        fill_price = self._wait_for_fill(result.order_id, code) or exec_price
        logger.info(
            f"FutuBroker: SELL filled {code} {close_qty}@{fill_price:.2f} "
            f"order={result.order_id} reason={reason}"
        )

        if self._futu_sync:
            self._futu_sync.save_order(
                result.order_id, code, "SELL", fill_price, close_qty,
                reason=f"broker:close:{reason[:40]}",
            )

        return self._pm.close_position(
            code, fill_price, reason,
            pct=pct, trade_cost=trade_cost,
            current_ts=ts, current_date=date, day_index=day_index,
        )

    def check_exits(self, prices, day_index, ts=0, date="",
                    cost_calculator=None, min_hold_minutes=0):
        """Risk exits: evaluate conditions on shadow PM, then execute via Futu.

        For stop-loss / emergency exits, shadow PM is closed regardless of Futu status
        (risk control must not be blocked by connectivity issues).
        """
        candidates = self._get_exit_candidates(prices, day_index, ts, min_hold_minutes)
        if not candidates:
            return []

        closed = []
        for code, price, reason, qty in candidates:
            pos = self._pm.positions.get(code)
            if not pos:
                continue

            # Attempt Futu SELL (best-effort for risk exits)
            sell_result = self._adapter.sell(code, price, qty)
            if sell_result.success:
                fill_price = self._wait_for_fill(sell_result.order_id, code) or price
                if self._futu_sync:
                    self._futu_sync.save_order(
                        sell_result.order_id, code, "SELL", fill_price, qty,
                        reason=f"broker:exit:{reason[:40]}",
                    )
            else:
                fill_price = price
                logger.warning(
                    f"FutuBroker check_exits: Futu SELL failed for {code} ({reason}), "
                    f"closing shadow PM anyway (risk control priority)"
                )

            trade_cost = 0.0
            if cost_calculator:
                cost_info = cost_calculator(fill_price, qty, "SELL")
                trade_cost = cost_info.get("total", 0)

            trade = self._pm.close_position(
                code, fill_price, reason,
                pct=1.0, trade_cost=trade_cost,
                current_ts=ts, current_date=date, day_index=day_index,
            )
            if trade:
                closed.append(trade)

        return closed

    def tighten_stop(self, code, stop_atr, price, atr):
        self._pm.tighten_stop(code, stop_atr, price, atr)

    @property
    def positions(self):
        return self._pm.positions

    @property
    def cash(self):
        return self._pm.cash

    def sync_from_futu(self, futu_positions, cash):
        self._pm.sync_from_futu(futu_positions, cash)

    # ── Fill waiting ──────────────────────────────────────────────────────

    def _wait_for_fill(self, order_id: str, code: str) -> float | None:
        """Poll Futu until order is filled or timeout. Returns avg_fill_price or None."""
        deadline = time.time() + FILL_TIMEOUT
        while time.time() < deadline:
            try:
                orders = self._adapter.get_today_orders(code)
                for o in orders:
                    if o.order_id == order_id:
                        # Futu status strings: "FILLED_ALL", "FILLED_PART", etc.
                        if "FILLED" in str(o.status).upper() and o.avg_fill_price > 0:
                            return o.avg_fill_price
            except Exception as e:
                logger.debug(f"_wait_for_fill poll error: {e}")
            time.sleep(FILL_POLL_INTERVAL)

        logger.warning(f"Fill timeout for order={order_id} code={code} after {FILL_TIMEOUT}s")
        return None

    # ── Exit condition check (read-only) ─────────────────────────────────

    def _get_exit_candidates(
        self, prices: dict, day_index: int, ts: int, min_hold_minutes: int,
    ) -> list[tuple]:
        """Evaluate exit conditions on shadow PM without closing. Returns
        [(code, price, reason, qty)] for each position that should exit.
        Mirrors PositionManager.check_exits condition logic.
        """
        candidates = []
        for code in list(self._pm.positions.keys()):
            pos = self._pm.positions.get(code)
            if not pos:
                continue

            price = prices.get(code)
            if not price or price <= 0:
                continue

            # Update highest price (same as PM.check_exits)
            if price > pos.highest_price:
                pos.highest_price = price

            hold_minutes = (
                (ts - pos.entry_time) / 60000
                if ts and pos.entry_time else 999
            )
            pnl_pct = (
                (price - pos.entry_price) / pos.entry_price
                if pos.entry_price > 0 else 0
            )
            extreme_loss = pnl_pct <= -0.08
            in_hold = hold_minutes < min_hold_minutes and not extreme_loss

            reason = None
            if pnl_pct <= -0.05 and not in_hold:
                reason = f"emergency_stop({pnl_pct:.1%})"
            elif extreme_loss:
                reason = f"emergency_stop({pnl_pct:.1%})"
            elif price <= pos.stop_loss and not in_hold:
                reason = f"stop_loss({pos.stop_loss:.2f})"
            elif pos.take_profit and price >= pos.take_profit and not in_hold:
                reason = f"take_profit({pos.take_profit:.2f})"
            elif (day_index - pos.entry_day_index) >= pos.max_hold_days:
                reason = f"max_hold({pos.max_hold_days}d)"

            if reason:
                candidates.append((code, price, reason, pos.quantity))

        return candidates
