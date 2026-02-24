"""SimulationEngine — HK 交易成本计算 + 滑点模型。

Hong Kong stock market cost structure:
- Broker commission (佣金): 0.03%, min HKD 3
- Stamp duty (印花税): 0.13% (sell only for HK; both sides since 2021.08)
- Exchange fee (交易费): 0.00565%
- Settlement fee (结算费): 0.002%, min HKD 2, max HKD 100

Slippage model: liquidity-based (daily turnover → slippage %).
"""

import logging

logger = logging.getLogger("simulation_engine")


class SimulationEngine:
    """Calculate HK trading costs and apply slippage."""

    def __init__(self, cost_model: dict, slippage_model: dict):
        self._cost = cost_model
        self._slippage = slippage_model

        # Parse slippage tiers: {threshold: rate} sorted descending
        self._slip_tiers = []
        for key, rate in slippage_model.items():
            if key == "default":
                continue
            # Parse "10e8" → 1_000_000_000, "1e8" → 100_000_000, etc.
            try:
                threshold = float(key)
            except ValueError:
                threshold = 0
            self._slip_tiers.append((threshold, rate))
        self._slip_tiers.sort(key=lambda x: x[0], reverse=True)
        self._slip_default = slippage_model.get("default", 0.005)

    def calc_cost(self, price: float, quantity: int, action: str) -> dict:
        """Calculate total trading cost for a trade.

        Args:
            price: execution price per share
            quantity: number of shares
            action: "BUY" or "SELL"

        Returns:
            dict with breakdown: commission, stamp_duty, exchange_fee, settlement_fee, total
        """
        turnover = price * quantity

        # Commission
        commission = max(
            turnover * self._cost.get("commission_rate", 0.0003),
            self._cost.get("commission_min", 3.0),
        )

        # Stamp duty (HK applies to both buy and sell since 2021.08)
        stamp_duty = turnover * self._cost.get("stamp_duty_rate", 0.0013)
        # Round up to nearest dollar
        import math
        stamp_duty = math.ceil(stamp_duty)

        # Exchange fee
        exchange_fee = turnover * self._cost.get("exchange_fee_rate", 0.0000565)

        # Settlement fee
        settlement = turnover * self._cost.get("settlement_fee_rate", 0.00002)
        settlement = max(settlement, self._cost.get("settlement_min", 2.0))
        settlement = min(settlement, self._cost.get("settlement_max", 100.0))

        total = commission + stamp_duty + exchange_fee + settlement

        return {
            "commission": round(commission, 2),
            "stamp_duty": round(stamp_duty, 2),
            "exchange_fee": round(exchange_fee, 2),
            "settlement_fee": round(settlement, 2),
            "total": round(total, 2),
        }

    def apply_slippage(self, price: float, daily_amount: float, action: str) -> float:
        """Apply slippage to price based on stock liquidity.

        Args:
            price: theoretical price
            daily_amount: daily trading turnover (HKD)
            action: "BUY" or "SELL"

        Returns:
            adjusted execution price (higher for BUY, lower for SELL)
        """
        slip_rate = self._slip_default
        for threshold, rate in self._slip_tiers:
            if daily_amount >= threshold:
                slip_rate = rate
                break

        if action == "BUY":
            return price * (1 + slip_rate)
        else:
            return price * (1 - slip_rate)

    def execute_trade(
        self,
        decision,  # TradeDecision
        current_price: float,
        daily_amount: float,
        atr: float,
    ) -> dict:
        """Execute a simulated trade with slippage and cost.

        Returns:
            {
                "exec_price": float,
                "slippage_pct": float,
                "cost": dict,
                "atr": float,
            }
        """
        action = decision.action
        if action in ("SELL", "SELL_NEXT_OPEN"):
            exec_action = "SELL"
        else:
            exec_action = "BUY"

        # Apply slippage
        exec_price = self.apply_slippage(current_price, daily_amount, exec_action)
        slippage_pct = abs(exec_price - current_price) / current_price if current_price > 0 else 0

        # We don't know quantity yet for BUY (position manager decides that)
        # Return the execution context; actual cost calculated by position manager
        return {
            "exec_price": round(exec_price, 4),
            "slippage_pct": round(slippage_pct, 6),
            "atr": atr,
            "action": exec_action,
        }
