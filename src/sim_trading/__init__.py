"""Simulated trading system — consumes L2 signals, generates virtual trades.

Modules:
- signal_archiver: Real-time archival of L2 signals + price snapshots to SQLite
- signal_mapper:   Signal → TradeDecision (4-tier rule engine)
- position_manager: Virtual position lifecycle (open/close/exit checks)
- simulation_engine: HK cost model + slippage
- trade_analyzer:  Performance metrics (Sharpe, drawdown, attribution)
- replay_runner:   Historical replay from SQLite
"""
