"""Replay Runner — 回放历史信号，模拟交易。

从 sim_trading.db 读取历史信号和价格快照，依次通过:
  SignalMapper → SimulationEngine → PositionManager → TradeAnalyzer

Usage:
    poetry run python src/sim_trading/replay_runner.py
    poetry run python src/sim_trading/replay_runner.py --version v2_tight_sl
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .db import get_connection, init_db
from .signal_mapper import TradeSignalMapper
from .position_manager import PositionManager
from .simulation_engine import SimulationEngine
from .trade_analyzer import TradeAnalyzer

logger = logging.getLogger("replay_runner")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "data" / "signal_rules.json"


def _load_rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_trading_dates(conn) -> list[str]:
    """Get sorted list of unique trading dates from signals."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM signals ORDER BY date"
    ).fetchall()
    return [r["date"] for r in rows]


def _get_signals_for_date(conn, date: str) -> list[dict]:
    """Get all signals for a date, ordered by timestamp."""
    rows = conn.execute(
        "SELECT * FROM signals WHERE date = ? ORDER BY ts",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_price_snapshot(conn, date: str, code: str, ts: int) -> dict | None:
    """Get the closest price snapshot to a given timestamp.

    Looks for the nearest snapshot within 5 minutes.
    """
    row = conn.execute(
        """SELECT * FROM price_snapshots
           WHERE date = ? AND code = ? AND ABS(ts - ?) < 300000
           ORDER BY ABS(ts - ?) LIMIT 1""",
        (date, code, ts, ts),
    ).fetchone()
    return dict(row) if row else None


def _get_day_prices(conn, date: str) -> dict[str, float]:
    """Get latest price for each stock on a given date (for EOD valuation)."""
    rows = conn.execute(
        """SELECT code, price FROM price_snapshots
           WHERE date = ? AND id IN (
               SELECT MAX(id) FROM price_snapshots WHERE date = ? GROUP BY code
           )""",
        (date, date),
    ).fetchall()
    return {r["code"]: r["price"] for r in rows}


def _get_day_amounts(conn, date: str) -> dict[str, float]:
    """Get average daily turnover amount per stock (for slippage)."""
    rows = conn.execute(
        """SELECT code, AVG(amount) as avg_amount FROM price_snapshots
           WHERE date = ? AND amount > 0 GROUP BY code""",
        (date,),
    ).fetchall()
    return {r["code"]: r["avg_amount"] for r in rows}


def _estimate_atr(conn, date: str, code: str) -> float:
    """Estimate ATR from price snapshot range on the day.

    Uses (high - low) approximation from snapshot min/max prices.
    Falls back to 2% of latest price if insufficient data.
    """
    row = conn.execute(
        """SELECT MIN(price) as low, MAX(price) as high, AVG(price) as avg_price,
                  COUNT(*) as cnt
           FROM price_snapshots WHERE date = ? AND code = ?""",
        (date, code),
    ).fetchone()

    if row and row["cnt"] >= 5 and row["high"] > 0:
        range_val = row["high"] - row["low"]
        if range_val > 0:
            return range_val
        # Flat day — use 1% of price
        return row["avg_price"] * 0.01

    # Fallback: 2% of signal price from same date
    price_row = conn.execute(
        "SELECT price_at_signal FROM signals WHERE date = ? AND code = ? AND price_at_signal > 0 LIMIT 1",
        (date, code),
    ).fetchone()
    if price_row:
        return price_row["price_at_signal"] * 0.02

    return 1.0  # absolute fallback


def _save_results(conn, trades: list[dict], daily_pnl: list[dict], version: str, rules: dict):
    """Write trades and daily_pnl to DB."""
    # Save param version
    conn.execute(
        """INSERT OR REPLACE INTO param_versions
           (version, created_at, config_json, is_active)
           VALUES (?, ?, ?, 1)""",
        (version, datetime.now().isoformat(), json.dumps(rules, ensure_ascii=False)),
    )

    # Save trades
    for t in trades:
        conn.execute(
            """INSERT OR REPLACE INTO trades
               (trade_id, param_version, code, action, direction,
                entry_price, exit_price, quantity, entry_time, exit_time,
                entry_date, exit_date, hold_days, pnl, pnl_pct,
                commission, total_cost, confidence, trigger_signals,
                exit_reason, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t["trade_id"], version, t["code"], t["action"], t["direction"],
                t["entry_price"], t["exit_price"], t["quantity"],
                t["entry_time"], t["exit_time"], t["entry_date"], t["exit_date"],
                t["hold_days"], t["pnl"], t["pnl_pct"],
                t["commission"], t.get("total_cost", t["commission"]),
                t["confidence"],
                json.dumps(t.get("trigger_signals", [])),
                t["exit_reason"], t.get("notes", ""),
            ),
        )

    # Save daily PnL
    for d in daily_pnl:
        conn.execute(
            """INSERT OR REPLACE INTO daily_pnl
               (date, param_version, total_equity, cash, invested,
                daily_return, cumulative_return, drawdown_pct, positions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["date"], version, d["total_equity"], d["cash"], d["invested"],
                d["daily_return"], d["cumulative_return"], d["drawdown_pct"],
                json.dumps(d.get("positions", {})),
            ),
        )

    conn.commit()


def replay(param_version: str = "v1_baseline"):
    """Replay historical signals from sim_trading.db."""
    rules = _load_rules()
    init_db()
    conn = get_connection()

    # Initialize components
    mapper = TradeSignalMapper(rules)
    engine = SimulationEngine(rules["cost_model"], rules["slippage"])
    pos_mgr = PositionManager(rules["initial_capital"], rules.get("lot_sizes", {}))

    trading_dates = _get_trading_dates(conn)
    if not trading_dates:
        print("No trading data found in database.")
        conn.close()
        return

    print(f"\n  Replay: {param_version}")
    print(f"  Capital: {rules['initial_capital']:,.0f} {rules['currency']}")
    print(f"  Dates: {trading_dates[0]} → {trading_dates[-1]} ({len(trading_dates)} days)")
    print()

    daily_pnl_records = []
    prev_equity = rules["initial_capital"]
    peak_equity = rules["initial_capital"]
    all_trades = []
    signals_processed = 0
    decisions_made = 0

    for day_idx, date in enumerate(trading_dates):
        # Reset session state for new day
        mapper.reset_session(date)

        # Day start: check exits on open prices
        day_prices = _get_day_prices(conn, date)
        day_amounts = _get_day_amounts(conn, date)

        # If we have positions from previous day and this is not day 0,
        # check exits at day open prices
        if day_idx > 0 and pos_mgr.positions:
            def cost_calc(price, qty, action):
                return engine.calc_cost(price, qty, action)

            exit_trades = pos_mgr.check_exits(
                day_prices, day_idx,
                current_ts=0, current_date=date,
                cost_calculator=cost_calc,
            )
            for t in exit_trades:
                t["notes"] = t.get("notes", "")  # ensure notes field
                all_trades.append(t)
                print(f"  EXIT  {t['code']:>8s}  {t['exit_price']:>8.2f}  "
                      f"PnL={t['pnl']:>+8.0f}  ({t['exit_reason']})")

        # Process signals chronologically
        signals = _get_signals_for_date(conn, date)
        for sig in signals:
            signals_processed += 1

            # Parse detail if stored as string
            detail = sig.get("detail", "{}")
            if isinstance(detail, str):
                try:
                    sig["detail"] = json.loads(detail)
                except (json.JSONDecodeError, TypeError):
                    sig["detail"] = {}

            # Get price context at signal time
            snap = _get_price_snapshot(conn, date, sig["code"], sig["ts"])
            if not snap and sig.get("price_at_signal"):
                # Use signal's own price if no snapshot nearby
                snap = {"price": sig["price_at_signal"], "amount": 0}

            if not snap or snap["price"] <= 0:
                continue

            equity = pos_mgr.get_equity(day_prices) if day_prices else prev_equity

            # Map signal to decision
            decision = mapper.process_signal(
                sig, pos_mgr.positions, equity, date
            )
            if not decision:
                continue

            decisions_made += 1
            code = decision.code

            # Estimate ATR and daily amount for execution
            atr = _estimate_atr(conn, date, code)
            daily_amount = day_amounts.get(code, 0)

            if decision.action == "BUY":
                exec_info = engine.execute_trade(decision, snap["price"], daily_amount, atr)
                exec_price = exec_info["exec_price"]

                # Calculate cost for position sizing
                # Estimate quantity first to get cost
                alloc = pos_mgr.cash * decision.position_pct
                est_qty = pos_mgr._align_lot(code, int(alloc / exec_price))
                if est_qty <= 0:
                    continue
                cost_info = engine.calc_cost(exec_price, est_qty, "BUY")

                pos = pos_mgr.open_position(
                    decision, exec_price, atr,
                    trade_cost=cost_info["total"],
                    current_date=date, current_ts=sig["ts"],
                    day_index=day_idx,
                )
                if pos:
                    print(f"  BUY   {code:>8s}  {est_qty:>5d} @ {exec_price:>8.2f}  "
                          f"SL={pos.stop_loss:.2f}  conf={decision.confidence:.0%}  "
                          f"({decision.reason})")

            elif decision.action == "SELL":
                if code not in pos_mgr.positions:
                    continue  # No position to sell
                pos = pos_mgr.positions[code]
                exec_info = engine.execute_trade(decision, snap["price"], daily_amount, atr)
                exec_price = exec_info["exec_price"]
                sell_qty = pos_mgr._align_lot(code, int(pos.quantity * decision.position_pct))
                if sell_qty <= 0:
                    sell_qty = pos.quantity
                cost_info = engine.calc_cost(exec_price, sell_qty, "SELL")

                trade = pos_mgr.close_position(
                    code, exec_price, decision.reason,
                    pct=decision.position_pct,
                    trade_cost=cost_info["total"],
                    current_ts=sig["ts"], current_date=date,
                    day_index=day_idx,
                )
                if trade:
                    trade["notes"] = decision.reason
                    all_trades.append(trade)
                    print(f"  SELL  {code:>8s}  {sell_qty:>5d} @ {exec_price:>8.2f}  "
                          f"PnL={trade['pnl']:>+8.0f}  ({decision.reason})")

            elif decision.action == "TIGHTEN_SL":
                pos_mgr.tighten_stop(code, decision.stop_atr, snap["price"], atr)

            elif decision.action == "SELL_NEXT_OPEN":
                # Mark for sell at next day's open — store in a pending list
                # For simplicity in v1, treat as immediate SELL
                if code not in pos_mgr.positions:
                    continue
                pos = pos_mgr.positions[code]
                exec_info = engine.execute_trade(decision, snap["price"], daily_amount, atr)
                exec_price = exec_info["exec_price"]
                cost_info = engine.calc_cost(exec_price, pos.quantity, "SELL")

                trade = pos_mgr.close_position(
                    code, exec_price, f"sell_next_open:{decision.reason}",
                    pct=decision.position_pct,
                    trade_cost=cost_info["total"],
                    current_ts=sig["ts"], current_date=date,
                    day_index=day_idx,
                )
                if trade:
                    trade["notes"] = decision.reason
                    all_trades.append(trade)

        # End of day snapshot
        eod_prices = _get_day_prices(conn, date) or day_prices
        equity = pos_mgr.get_equity(eod_prices)
        invested = pos_mgr.get_invested(eod_prices)
        daily_return = (equity - prev_equity) / prev_equity if prev_equity > 0 else 0
        cum_return = (equity - rules["initial_capital"]) / rules["initial_capital"]

        if equity > peak_equity:
            peak_equity = equity
        drawdown_pct = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0

        snap_dict = pos_mgr.snapshot(eod_prices)
        daily_pnl_records.append({
            "date": date,
            "total_equity": round(equity, 2),
            "cash": round(pos_mgr.cash, 2),
            "invested": round(invested, 2),
            "daily_return": round(daily_return, 6),
            "cumulative_return": round(cum_return, 6),
            "drawdown_pct": round(drawdown_pct, 6),
            "positions": snap_dict.get("positions", {}),
        })

        prev_equity = equity
        print(f"  [{date}]  equity={equity:>12,.0f}  cash={pos_mgr.cash:>12,.0f}  "
              f"invested={invested:>12,.0f}  positions={len(pos_mgr.positions)}")

    # Force-close any remaining positions at last known prices
    if pos_mgr.positions:
        print(f"\n  Force-closing {len(pos_mgr.positions)} remaining positions...")
        last_date = trading_dates[-1]
        last_prices = _get_day_prices(conn, last_date)
        for code in list(pos_mgr.positions.keys()):
            price = last_prices.get(code)
            if not price:
                continue
            pos = pos_mgr.positions[code]
            cost_info = engine.calc_cost(price, pos.quantity, "SELL")
            trade = pos_mgr.close_position(
                code, price, "force_close_eod",
                trade_cost=cost_info["total"],
                current_ts=0, current_date=last_date,
                day_index=len(trading_dates) - 1,
            )
            if trade:
                trade["notes"] = "force_close"
                all_trades.append(trade)
                print(f"  CLOSE {code:>8s}  {trade['quantity']:>5d} @ {price:>8.2f}  "
                      f"PnL={trade['pnl']:>+8.0f}")

    # Save results
    _save_results(conn, all_trades, daily_pnl_records, param_version, rules)
    conn.close()

    # Analyze
    print(f"\n  Signals processed: {signals_processed}")
    print(f"  Decisions made:    {decisions_made}")
    print(f"  Trades executed:   {len(all_trades)}")

    analyzer = TradeAnalyzer(all_trades, daily_pnl_records, rules["initial_capital"])
    analyzer.print_report()

    return analyzer.summary()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    version = "v1_baseline"
    if len(sys.argv) > 1 and sys.argv[1] == "--version" and len(sys.argv) > 2:
        version = sys.argv[2]

    replay(version)


if __name__ == "__main__":
    main()
