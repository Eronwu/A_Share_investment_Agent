"""RealtimeSimEngine — 实时模拟交易引擎。

被 l2_strategy_daemon 同步调用（非独立线程），每 3s tick() 一次：
1. 查 signals 表中新信号 (watermark)
2. 信号 → mapper → engine → position_manager
3. 用 market_data.json 实时价格做持仓估值 + 退出检查
4. 持仓状态持久化到 live_state 表
5. 平仓交易写入 trades 表 (param_version="live")
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .db import get_connection, init_db
from .signal_mapper import TradeSignalMapper
from .position_manager import Position, PositionManager
from .simulation_engine import SimulationEngine

logger = logging.getLogger("rt_sim_engine")

MARKET_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "market_data.json"
PARAM_VERSION = "live"


class RealtimeSimEngine:
    """Synchronous real-time sim engine, called by l2_daemon.tick()."""

    def __init__(self, rules: dict):
        init_db()
        self._rules = rules
        self._mapper = TradeSignalMapper(rules)
        self._engine = SimulationEngine(rules["cost_model"], rules["slippage"])
        self._pos_mgr = PositionManager(
            rules.get("initial_capital", 1_000_000),
            rules.get("lot_sizes", {}),
        )
        self._last_processed_ts = 0
        self._current_date = datetime.now().strftime("%Y-%m-%d")
        self._day_index = 0
        self._tick_count = 0

        # Load watermark + restore positions from DB
        self._load_watermark()
        self._load_state()
        logger.info(
            f"RT SimEngine init: capital={self._pos_mgr.cash:.0f}, "
            f"positions={len(self._pos_mgr.positions)}, watermark={self._last_processed_ts}"
        )

    # ── State persistence ──

    def _load_watermark(self):
        """Load last processed signal timestamp from DB."""
        conn = get_connection()
        row = conn.execute(
            "SELECT MAX(last_updated) as wm FROM live_state"
        ).fetchone()
        if row and row["wm"]:
            # Use live_state's last_updated as rough watermark
            pass
        # Better: use signals table filtered by what we've already processed
        # Since we persist positions, any signal that led to a position is "processed"
        # Just start from the latest signal ts that has a live position
        row2 = conn.execute(
            "SELECT MAX(ts) as max_ts FROM signals"
        ).fetchone()
        if row2 and row2["max_ts"]:
            self._last_processed_ts = row2["max_ts"]
        conn.close()

    def _load_state(self):
        """Restore PositionManager state from live_state table (restart safety)."""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM live_state").fetchall()
        conn.close()

        for r in rows:
            trigger_signals = []
            try:
                trigger_signals = json.loads(r["trigger_signals"] or "[]")
            except (json.JSONDecodeError, TypeError):
                pass

            pos = Position(
                code=r["code"],
                entry_price=r["entry_price"],
                quantity=r["quantity"],
                entry_time=r["entry_time"] or 0,
                entry_date=r["entry_date"] or "",
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                max_hold_days=r["max_hold_days"] or 5,
                confidence=r["confidence"] or 0.6,
                trigger_signals=trigger_signals,
                entry_strategy=r["entry_strategy"] or "",
                highest_price=max(r["entry_price"], r["current_price"] or 0),
                entry_day_index=0,
            )
            self._pos_mgr._positions[r["code"]] = pos
            # Deduct cash for restored positions
            self._pos_mgr._cash -= r["entry_price"] * r["quantity"]

        if rows:
            logger.info(f"Restored {len(rows)} live positions from DB")

    def _persist_state(self, prices: dict[str, float]):
        """Write all current positions to live_state table (full replace)."""
        conn = get_connection()
        now_ts = int(time.time() * 1000)

        # Clear positions that no longer exist
        current_codes = set(self._pos_mgr.positions.keys())
        conn.execute(
            "DELETE FROM live_state WHERE code NOT IN ({})".format(
                ",".join("?" for _ in current_codes)
            ) if current_codes else "DELETE FROM live_state",
            list(current_codes) if current_codes else [],
        )

        # Upsert each position
        for code, pos in self._pos_mgr.positions.items():
            current_price = prices.get(code, pos.entry_price)
            unrealized = (current_price - pos.entry_price) * pos.quantity
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

            conn.execute(
                """INSERT OR REPLACE INTO live_state
                   (code, entry_price, quantity, current_price, entry_time, entry_date,
                    stop_loss, take_profit, max_hold_days, entry_strategy, confidence,
                    trigger_signals, unrealized_pnl, pnl_pct, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code, pos.entry_price, pos.quantity, round(current_price, 4),
                    pos.entry_time, pos.entry_date,
                    round(pos.stop_loss, 4), round(pos.take_profit, 4) if pos.take_profit else None,
                    pos.max_hold_days, pos.entry_strategy, pos.confidence,
                    json.dumps(pos.trigger_signals),
                    round(unrealized, 2), round(pnl_pct, 6), now_ts,
                ),
            )

        conn.commit()
        conn.close()

    def _save_trade(self, trade: dict):
        """Save completed trade to trades table with param_version='live'."""
        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO trades
                   (trade_id, param_version, code, action, direction,
                    entry_price, exit_price, quantity, entry_time, exit_time,
                    entry_date, exit_date, hold_days, pnl, pnl_pct,
                    commission, total_cost, confidence, trigger_signals,
                    exit_reason, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade["trade_id"], PARAM_VERSION,
                    trade["code"], trade["action"], trade["direction"],
                    trade["entry_price"], trade["exit_price"], trade["quantity"],
                    trade["entry_time"], trade["exit_time"],
                    trade["entry_date"], trade["exit_date"],
                    trade.get("hold_days", 0), trade["pnl"], trade["pnl_pct"],
                    trade["commission"], trade.get("commission", 0),
                    trade["confidence"],
                    json.dumps(trade.get("trigger_signals", [])),
                    trade["exit_reason"], trade.get("notes", ""),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save trade error: {e}")
        finally:
            conn.close()

    # ── Price helpers ──

    def _read_market_prices(self) -> dict[str, dict]:
        """Read live prices from market_data.json."""
        try:
            with open(MARKET_DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

        result = {}
        for svc in data.get("services", []):
            code = svc.get("id", "")
            price = float(svc.get("price", 0) or 0)
            if code and price > 0:
                result[code] = {
                    "price": price,
                    "amount": float(svc.get("amount", 0) or 0),
                    "change": float(svc.get("change", 0) or 0),
                }
        return result

    def _estimate_atr(self, code: str, date: str) -> float:
        """Estimate ATR from today's price snapshots."""
        conn = get_connection()
        row = conn.execute(
            """SELECT MIN(price) as low, MAX(price) as high, AVG(price) as avg_price,
                      COUNT(*) as cnt
               FROM price_snapshots WHERE date = ? AND code = ?""",
            (date, code),
        ).fetchone()
        conn.close()

        if row and row["cnt"] and row["cnt"] >= 3 and row["high"] > row["low"]:
            return row["high"] - row["low"]
        if row and row["avg_price"]:
            return row["avg_price"] * 0.02
        return 1.0

    # ── Core logic ──

    def tick(self):
        """Main entry point — called by daemon every 3s during trading hours."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Day rollover
        if today != self._current_date:
            self.daily_reset()
            self._current_date = today
            self._day_index += 1

        # 1. Fetch new signals from DB
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM signals WHERE ts > ? ORDER BY ts",
            (self._last_processed_ts,),
        ).fetchall()
        conn.close()

        new_signals = [dict(r) for r in rows]

        # 2. Process each new signal
        for sig in new_signals:
            self._process_signal(sig)
            self._last_processed_ts = max(self._last_processed_ts, sig.get("ts", 0))

        # 3. Update valuations + check exits using live prices
        market = self._read_market_prices()
        prices = {code: info["price"] for code, info in market.items()}

        if prices and self._pos_mgr.positions:
            exit_trades = self._pos_mgr.check_exits(
                prices, self._day_index,
                current_ts=int(time.time() * 1000),
                current_date=today,
                cost_calculator=lambda p, q, a: self._engine.calc_cost(p, q, a),
            )
            for trade in exit_trades:
                trade["notes"] = trade.get("notes", "")
                self._save_trade(trade)
                logger.info(
                    f"RT EXIT {trade['code']}: {trade['exit_price']:.2f} "
                    f"PnL={trade['pnl']:+.0f} ({trade['exit_reason']})"
                )

        # 4. Persist positions to live_state
        self._persist_state(prices)
        self._tick_count += 1

    def _process_signal(self, signal: dict):
        """Process a single signal through the trading pipeline."""
        # Parse detail if string
        detail = signal.get("detail", "{}")
        if isinstance(detail, str):
            try:
                signal["detail"] = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                signal["detail"] = {}

        code = signal.get("code", "")
        date = signal.get("date", self._current_date)

        # Get current prices for equity calculation
        market = self._read_market_prices()
        prices = {c: info["price"] for c, info in market.items()}
        equity = self._pos_mgr.get_equity(prices) if prices else self._pos_mgr.cash

        # Map signal → decision
        decision = self._mapper.process_signal(
            signal, self._pos_mgr.positions, equity, date,
        )
        if not decision:
            return

        # Get price context
        current_price = signal.get("price_at_signal", 0)
        if current_price <= 0 and code in market:
            current_price = market[code]["price"]
        if current_price <= 0:
            return

        atr = self._estimate_atr(code, date)
        daily_amount = market.get(code, {}).get("amount", 0)

        if decision.action == "BUY":
            exec_info = self._engine.execute_trade(decision, current_price, daily_amount, atr)
            exec_price = exec_info["exec_price"]

            alloc = self._pos_mgr.cash * decision.position_pct
            est_qty = self._pos_mgr._align_lot(code, int(alloc / exec_price))
            if est_qty <= 0:
                return
            cost_info = self._engine.calc_cost(exec_price, est_qty, "BUY")

            pos = self._pos_mgr.open_position(
                decision, exec_price, atr,
                trade_cost=cost_info["total"],
                current_date=date, current_ts=signal.get("ts", 0),
                day_index=self._day_index,
            )
            if pos:
                logger.info(
                    f"RT BUY {code}: {est_qty} @ {exec_price:.2f} "
                    f"SL={pos.stop_loss:.2f} conf={decision.confidence:.0%} "
                    f"({decision.reason})"
                )

        elif decision.action == "SELL":
            if code not in self._pos_mgr.positions:
                return
            pos = self._pos_mgr.positions[code]
            exec_info = self._engine.execute_trade(decision, current_price, daily_amount, atr)
            exec_price = exec_info["exec_price"]
            cost_info = self._engine.calc_cost(exec_price, pos.quantity, "SELL")

            trade = self._pos_mgr.close_position(
                code, exec_price, decision.reason,
                pct=decision.position_pct,
                trade_cost=cost_info["total"],
                current_ts=signal.get("ts", 0), current_date=date,
                day_index=self._day_index,
            )
            if trade:
                trade["notes"] = decision.reason
                self._save_trade(trade)
                logger.info(
                    f"RT SELL {code}: @ {exec_price:.2f} "
                    f"PnL={trade['pnl']:+.0f} ({decision.reason})"
                )

        elif decision.action == "TIGHTEN_SL":
            self._pos_mgr.tighten_stop(code, decision.stop_atr, current_price, atr)
            logger.info(f"RT TIGHTEN_SL {code}")

    def daily_reset(self):
        """Daily reset: mapper session + increment day index."""
        self._mapper.reset_session(self._current_date)
        self._day_index += 1
        logger.info(f"RT daily reset: day_index={self._day_index}")
