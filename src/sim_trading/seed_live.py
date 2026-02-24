"""Seed live_state with historical signals — 让实时 tab 立刻有数据展示。

用法: poetry run python -m src.sim_trading.seed_live
"""

import json
import logging
from pathlib import Path

from .db import get_connection, init_db
from .realtime_engine import RealtimeSimEngine

logger = logging.getLogger("seed_live")

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_rules.json"


def seed():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db()

    with open(RULES_PATH, "r", encoding="utf-8") as f:
        rules = json.load(f)

    # Clear existing live state
    conn = get_connection()
    conn.execute("DELETE FROM live_state")
    conn.execute("DELETE FROM trades WHERE param_version = 'live'")
    conn.commit()

    # Get all signals ordered by time
    rows = conn.execute("SELECT * FROM signals ORDER BY ts").fetchall()
    signals = [dict(r) for r in rows]
    conn.close()

    print(f"Seeding RT engine with {len(signals)} historical signals...")

    # Create engine with watermark at 0 (process all signals)
    engine = RealtimeSimEngine(rules)
    engine._last_processed_ts = 0

    # Process all signals by calling tick repeatedly
    # But tick() reads from DB, so we need to set watermark to 0
    engine.tick()

    # Show results
    conn = get_connection()
    live_count = conn.execute("SELECT COUNT(*) as c FROM live_state").fetchone()["c"]
    trade_count = conn.execute("SELECT COUNT(*) as c FROM trades WHERE param_version='live'").fetchone()["c"]

    print(f"\nResults:")
    print(f"  Live positions: {live_count}")
    print(f"  Live trades:    {trade_count}")

    if live_count > 0:
        print(f"\n  --- Live Positions ---")
        for r in conn.execute("SELECT code, entry_price, current_price, quantity, unrealized_pnl, stop_loss FROM live_state").fetchall():
            print(f"  {r['code']:>8s}  entry={r['entry_price']:.2f}  now={r['current_price']:.2f}  qty={r['quantity']}  PnL={r['unrealized_pnl']:+.0f}  SL={r['stop_loss']:.2f}")

    if trade_count > 0:
        print(f"\n  --- Live Trades ---")
        for r in conn.execute("SELECT code, entry_price, exit_price, pnl, exit_reason FROM trades WHERE param_version='live' ORDER BY id").fetchall():
            print(f"  {r['code']:>8s}  {r['entry_price']:.2f} → {r['exit_price']:.2f}  PnL={r['pnl']:+.0f}  ({r['exit_reason']})")

    conn.close()


if __name__ == "__main__":
    seed()
