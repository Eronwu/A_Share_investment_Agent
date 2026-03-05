"""Futu 持仓同步 — Futu positions → SQLite live_state/trades/daily_pnl。

职责:
  1. sync_live_state: Futu get_positions() → live_state 表（替代影子 PM 的 _persist_state）
  2. detect_and_save_closed: 对比前后快照，检测平仓 → trades 表
  3. save_daily_pnl: get_funds() → daily_pnl 表
  4. save_order / update_order_status: futu_orders 审计表
"""

import json
import logging
import time
import uuid
from datetime import datetime

from .db import get_connection, init_db
from .futu_trade_adapter import FutuTradeAdapter, FutuPosition, OrderUpdate

logger = logging.getLogger("l2_daemon.futu_sync")

PARAM_VERSION = "live"


class FutuPositionSync:
    """Futu 持仓 → SQLite 同步器。"""

    def __init__(self, adapter: FutuTradeAdapter):
        self._adapter = adapter
        self._prev_positions: dict[str, FutuPosition] = {}
        init_db()

    def sync_live_state(self, daily_scores: dict[str, int] | None = None) -> None:
        """从 Futu get_positions() 读取，写入 live_state 表。

        Args:
            daily_scores: {code: score_int} 可选，用于填充 daily_score 列。
        """
        positions = self._adapter.get_positions()
        if not positions and not self._prev_positions:
            return

        scores = daily_scores or {}
        now_ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            # 清理不再持有的
            current_codes = set(positions.keys())
            if current_codes:
                placeholders = ",".join("?" for _ in current_codes)
                conn.execute(
                    f"DELETE FROM live_state WHERE code NOT IN ({placeholders})",
                    list(current_codes),
                )
            else:
                conn.execute("DELETE FROM live_state")

            for code, fp in positions.items():
                unrealized = fp.unrealized_pnl
                pnl_pct = (
                    (fp.market_val / (fp.avg_price * fp.quantity) - 1)
                    if fp.avg_price > 0 and fp.quantity > 0 else 0
                )

                conn.execute(
                    """INSERT OR REPLACE INTO live_state
                       (code, name, entry_price, quantity, current_price, entry_time, entry_date,
                        stop_loss, take_profit, max_hold_days, entry_strategy, confidence,
                        trigger_signals, unrealized_pnl, pnl_pct, daily_score,
                        buy_cost_per_share, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        code, fp.name,
                        fp.avg_price, fp.quantity,
                        round(fp.market_val / fp.quantity, 4) if fp.quantity > 0 else 0,
                        0, "",  # entry_time/date not available from Futu positions
                        0, None, 10, "futu_sim", 0.0,
                        "[]",
                        round(unrealized, 2), round(pnl_pct, 6),
                        scores.get(code, 0),
                        0, now_ts,
                    ),
                )

            conn.commit()
        except Exception as e:
            logger.error(f"sync_live_state failed: {e}")
        finally:
            conn.close()

        # 更新前一快照（用于 detect_and_save_closed）
        self._prev_positions = positions

    def detect_and_save_closed(self) -> list[dict]:
        """对比前后快照，检测平仓 → 写入 trades 表。

        需在 sync_live_state() 之前调用（用旧 prev vs 新 current）。
        """
        current = self._adapter.get_positions()
        closed_trades = []

        for code, prev_pos in self._prev_positions.items():
            if code not in current:
                # 完全平仓
                trade = self._create_trade_record(prev_pos, 0, "futu_close")
                if trade:
                    closed_trades.append(trade)
            else:
                cur_pos = current[code]
                if cur_pos.quantity < prev_pos.quantity:
                    # 部分平仓
                    trade = self._create_trade_record(
                        prev_pos,
                        prev_pos.quantity - cur_pos.quantity,
                        "futu_partial_close",
                    )
                    if trade:
                        closed_trades.append(trade)

        # 保存到 DB
        for trade in closed_trades:
            self._save_trade(trade)

        return closed_trades

    def _create_trade_record(self, prev_pos: FutuPosition,
                             close_qty: int, reason: str) -> dict | None:
        """从前后持仓变化构建 trade record。"""
        qty = close_qty if close_qty > 0 else prev_pos.quantity
        if qty <= 0:
            return None

        # 退出价 = 当前市值 / 当前数量（近似）
        # 更精确的方式需要查询 Futu order 的成交均价
        exit_price = prev_pos.avg_price  # fallback to avg_price

        now_ts = int(time.time() * 1000)
        today = datetime.now().strftime("%Y-%m-%d")

        pnl = prev_pos.unrealized_pnl if close_qty == 0 else 0
        pnl_pct = pnl / (prev_pos.avg_price * qty) if prev_pos.avg_price > 0 and qty > 0 else 0

        return {
            "trade_id": str(uuid.uuid4())[:8],
            "code": prev_pos.code,
            "action": "SELL",
            "direction": "close_long",
            "entry_price": prev_pos.avg_price,
            "exit_price": exit_price,
            "quantity": qty,
            "entry_time": 0,
            "exit_time": now_ts,
            "entry_date": "",
            "exit_date": today,
            "hold_days": 0,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 6),
            "commission": 0,  # Futu 模拟盘自动扣除
            "total_cost": 0,
            "confidence": 0,
            "trigger_signals": [],
            "exit_reason": reason,
            "notes": "futu_sim",
        }

    def _save_trade(self, trade: dict):
        """Save trade to trades table."""
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
                    trade["commission"], trade.get("total_cost", 0),
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

    def save_daily_pnl(self, date: str | None = None) -> None:
        """从 get_funds() 读取权益 → 写入 daily_pnl 表。"""
        funds = self._adapter.get_funds()
        if funds.total_assets <= 0:
            return

        today = date or datetime.now().strftime("%Y-%m-%d")
        positions = self._adapter.get_positions()
        positions_json = json.dumps({
            code: {
                "entry_price": fp.avg_price,
                "quantity": fp.quantity,
                "market_val": fp.market_val,
            }
            for code, fp in positions.items()
        })

        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO daily_pnl
                   (date, param_version, total_equity, cash, invested,
                    daily_return, cumulative_return, drawdown_pct, positions_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today, PARAM_VERSION,
                    funds.total_assets, funds.cash, funds.market_val,
                    0, 0, 0,  # 需要与前日对比计算，暂填 0
                    positions_json,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save daily_pnl error: {e}")
        finally:
            conn.close()

    # ── Order audit ────────────────────────────────────────

    def save_order(self, order_id: str, code: str, side: str,
                   price: float, qty: int, reason: str = "",
                   acc_id: int | None = None) -> None:
        """记录订单到 futu_orders 表。"""
        now_ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO futu_orders
                   (order_id, code, side, price, quantity, status,
                    futu_acc_id, exit_reason, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)""",
                (order_id, code, side, price, qty, acc_id, reason, now_ts, now_ts),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Save order error: {e}")
        finally:
            conn.close()

    def update_order_status(self, updates: list[OrderUpdate]) -> None:
        """批量更新 futu_orders 状态。"""
        if not updates:
            return

        now_ts = int(time.time() * 1000)
        conn = get_connection()
        try:
            for u in updates:
                conn.execute(
                    """UPDATE futu_orders
                       SET status=?, filled_qty=?, avg_fill_price=?, updated_at=?
                       WHERE order_id=?""",
                    (u.status, u.filled_qty, u.avg_fill_price, now_ts, u.order_id),
                )
            conn.commit()
        except Exception as e:
            logger.warning(f"Update order status error: {e}")
        finally:
            conn.close()
