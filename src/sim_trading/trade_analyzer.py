"""TradeAnalyzer — 回测绩效分析。

核心指标:
- 胜率 (Win Rate)
- 盈亏比 (Profit Factor)
- Sharpe Ratio (annualized)
- 最大回撤 (Max Drawdown)
- Calmar Ratio (annualized return / max drawdown)
- 按策略/股票归因
"""

import math
from collections import defaultdict


class TradeAnalyzer:
    """Analyze simulated trading results."""

    def __init__(
        self,
        trades: list[dict],
        daily_pnl: list[dict],
        initial_capital: float = 1_000_000,
    ):
        self._trades = trades
        self._daily_pnl = daily_pnl
        self._initial_capital = initial_capital

    def summary(self) -> dict:
        """Overall performance summary."""
        if not self._trades:
            return {"error": "No trades to analyze"}

        pnls = [t["pnl"] for t in self._trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")
        avg_hold = sum(t.get("hold_days", 0) for t in self._trades) / len(self._trades)
        total_commission = sum(t.get("commission", 0) for t in self._trades)

        # Daily returns for Sharpe / drawdown
        daily_returns = [d.get("daily_return", 0) for d in self._daily_pnl]
        sharpe = self._calc_sharpe(daily_returns)
        max_dd, max_dd_pct = self._calc_max_drawdown()
        calmar = self._calc_calmar(daily_returns, max_dd_pct)

        # Final equity
        final_equity = self._daily_pnl[-1].get("total_equity", self._initial_capital) if self._daily_pnl else self._initial_capital
        total_return = (final_equity - self._initial_capital) / self._initial_capital

        return {
            "total_trades": len(self._trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "total_return": round(total_return, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "avg_hold_days": round(avg_hold, 1),
            "total_commission": round(total_commission, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "max_drawdown_abs": round(max_dd, 2),
            "calmar_ratio": round(calmar, 2),
            "final_equity": round(final_equity, 2),
            "initial_capital": self._initial_capital,
            "trading_days": len(self._daily_pnl),
        }

    def per_strategy(self) -> dict:
        """P&L breakdown by trigger strategy."""
        by_strategy = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})

        for t in self._trades:
            # Primary strategy is the first part of reason (before +)
            reason = t.get("exit_reason", "")
            # Use trigger strategy from entry, stored in notes or reason
            # Fall back to code-based grouping
            signals = t.get("trigger_signals", [])
            strategy = t.get("notes", "unknown")

            entry = by_strategy[strategy]
            entry["trades"] += 1
            entry["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                entry["wins"] += 1

        # Calculate win rates
        result = {}
        for strat, data in sorted(by_strategy.items(), key=lambda x: x[1]["pnl"], reverse=True):
            result[strat] = {
                "trades": data["trades"],
                "pnl": round(data["pnl"], 2),
                "win_rate": round(data["wins"] / data["trades"], 4) if data["trades"] > 0 else 0,
            }
        return result

    def per_stock(self) -> dict:
        """P&L breakdown by stock code."""
        by_stock = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})

        for t in self._trades:
            code = t.get("code", "unknown")
            entry = by_stock[code]
            entry["trades"] += 1
            entry["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                entry["wins"] += 1

        result = {}
        for code, data in sorted(by_stock.items(), key=lambda x: x[1]["pnl"], reverse=True):
            result[code] = {
                "trades": data["trades"],
                "pnl": round(data["pnl"], 2),
                "win_rate": round(data["wins"] / data["trades"], 4) if data["trades"] > 0 else 0,
            }
        return result

    def _calc_sharpe(self, daily_returns: list[float], risk_free_annual: float = 0.02) -> float:
        """Annualized Sharpe ratio (252 trading days)."""
        if len(daily_returns) < 2:
            return 0.0

        mean_r = sum(daily_returns) / len(daily_returns)
        daily_rf = risk_free_annual / 252
        excess = [r - daily_rf for r in daily_returns]
        mean_excess = sum(excess) / len(excess)

        variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(variance) if variance > 0 else 0

        if std == 0:
            return 0.0
        return (mean_excess / std) * math.sqrt(252)

    def _calc_max_drawdown(self) -> tuple[float, float]:
        """Max drawdown in absolute HKD and percentage."""
        if not self._daily_pnl:
            return 0.0, 0.0

        equities = [d.get("total_equity", self._initial_capital) for d in self._daily_pnl]
        peak = equities[0]
        max_dd = 0.0
        max_dd_pct = 0.0

        for eq in equities:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct

        return max_dd, max_dd_pct

    def _calc_calmar(self, daily_returns: list[float], max_dd_pct: float) -> float:
        """Calmar ratio: annualized return / max drawdown."""
        if max_dd_pct <= 0 or len(daily_returns) < 2:
            return 0.0

        total_return = 1.0
        for r in daily_returns:
            total_return *= (1 + r)

        n_days = len(daily_returns)
        annual_return = total_return ** (252 / n_days) - 1 if n_days > 0 else 0
        return annual_return / max_dd_pct if max_dd_pct > 0 else 0.0

    def print_report(self):
        """Print a terminal-friendly performance report."""
        s = self.summary()
        if "error" in s:
            print(f"\n  {s['error']}\n")
            return

        print("\n" + "=" * 60)
        print("  SIM TRADING REPORT")
        print("=" * 60)

        print(f"\n  Capital:  {s['initial_capital']:>12,.0f} HKD")
        print(f"  Final:    {s['final_equity']:>12,.0f} HKD")
        print(f"  Return:   {s['total_return']:>+11.2%}")
        print(f"  Days:     {s['trading_days']:>12d}")

        print(f"\n  --- Trades ---")
        print(f"  Total:    {s['total_trades']:>12d}")
        print(f"  Winners:  {s['winning_trades']:>12d}  ({s['win_rate']:.1%})")
        print(f"  Losers:   {s['losing_trades']:>12d}")
        print(f"  Avg Win:  {s['avg_win']:>+12,.0f} HKD")
        print(f"  Avg Loss: {s['avg_loss']:>+12,.0f} HKD")
        print(f"  P/F:      {s['profit_factor']:>12}")
        print(f"  Hold:     {s['avg_hold_days']:>11.1f}d")
        print(f"  Costs:    {s['total_commission']:>12,.0f} HKD")

        print(f"\n  --- Risk ---")
        print(f"  Sharpe:   {s['sharpe_ratio']:>12.2f}")
        print(f"  Max DD:   {s['max_drawdown_pct']:>+11.2%}  ({s['max_drawdown_abs']:>+,.0f} HKD)")
        print(f"  Calmar:   {s['calmar_ratio']:>12.2f}")

        # Per-strategy
        strats = self.per_strategy()
        if strats:
            print(f"\n  --- By Strategy ---")
            print(f"  {'Strategy':<30s} {'Trades':>6s} {'PnL':>10s} {'WinR':>6s}")
            for name, data in strats.items():
                print(f"  {name:<30s} {data['trades']:>6d} {data['pnl']:>+10,.0f} {data['win_rate']:>5.0%}")

        # Per-stock
        stocks = self.per_stock()
        if stocks:
            print(f"\n  --- By Stock ---")
            print(f"  {'Code':<12s} {'Trades':>6s} {'PnL':>10s} {'WinR':>6s}")
            for code, data in stocks.items():
                print(f"  {code:<12s} {data['trades']:>6d} {data['pnl']:>+10,.0f} {data['win_rate']:>5.0%}")

        print("\n" + "=" * 60)
