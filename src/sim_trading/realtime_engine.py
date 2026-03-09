"""RealtimeSimEngine v2 — 日线评分驱动型实时模拟交易引擎。

v2 策略核心变化：
- 开仓由日线评分驱动（score >= 70），而非 L2 秒级信号
- 固定入场窗口（10:00-10:30）、收盘评估窗口（15:30）
- 日内仅处理止损/止盈/T3 纠偏（加手续费过滤）
- 极强 L2 信号（confidence >= 0.85 且 score >= 60）作为例外入场
- 每天最多 1 只新开仓，再入场冷却 120 分钟

被 l2_strategy_daemon 同步调用（非独立线程），每 3s tick() 一次：
1. 时间窗口判断 → 入场/退出评估
2. 新信号仅处理 T3 纠偏（加手续费过滤 + min_hold）
3. 止损/止盈始终实时检查
4. 持仓状态持久化到 live_state 表
"""

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .db import get_connection, init_db
from .signal_mapper import TradeSignalMapper, TradeDecision
from .position_manager import Position, PositionManager
from .simulation_engine import SimulationEngine

logger = logging.getLogger("l2_daemon.rt_sim")

MARKET_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "market_data.json"
MONITOR_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "monitor_config.json"
PARAM_VERSION = "live"


class RealtimeSimEngine:
    """v2: Daily-score-driven real-time sim engine, called by l2_daemon.tick().

    支持双模式:
      - futu_trade=False (默认): 原有虚拟 SimEngine + PositionManager
      - futu_trade=True: 下单走 Futu 模拟盘，影子 PM 用于风控
    """

    def __init__(self, rules: dict, daily_tracker=None,
                 futu_trade: bool = False,
                 futu_host: str = '127.0.0.1', futu_port: int = 11111):
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

        # v2: Daily score config
        self._daily_tracker = daily_tracker  # DailyIndicatorTracker reference
        self._score_cfg = rules.get("daily_score", {})
        self._last_exit_ts: dict[str, int] = {}  # code → epoch ms of last exit (cooldown)
        self._low_score_count: dict[str, int] = {}  # code → 连续低分天数（出场惯性保护）
        self._new_positions_today = 0
        self._dip_buy_notified_today: set[str] = set()  # codes already notified for dip_buy today
        self._entry_evaluated_today = False
        self._exit_evaluated_today = False
        # Per-tick score cache: {code: score_dict}, cleared each tick
        self._score_cache: dict[str, dict] = {}

        # ── Futu trade mode ──
        self._futu_enabled = False
        self._futu = None
        self._futu_sync = None
        futu_cfg = rules.get("futu_trade", {})
        if futu_trade or futu_cfg.get("enabled", False):
            try:
                from .futu_trade_adapter import FutuTradeAdapter
                from .futu_position_sync import FutuPositionSync
                host = futu_cfg.get("host", futu_host)
                port = futu_cfg.get("port", futu_port)
                self._futu = FutuTradeAdapter(host, port)
                self._futu_sync = FutuPositionSync(self._futu)
                self._futu_enabled = self._futu.connect()
                if self._futu_enabled:
                    self._load_state_from_futu()
                    logger.info(
                        f"Futu trade mode ENABLED: "
                        f"HK acc={self._futu.hk_acc_id}, "
                        f"A-share acc={self._futu.a_acc_id}"
                    )
                else:
                    logger.warning("Futu trade requested but connection failed, fallback to virtual")
            except Exception as e:
                logger.warning(f"Futu trade init failed: {e}, fallback to virtual")
                self._futu_enabled = False
        self._sync_interval = futu_cfg.get("sync_interval_ticks", 10)

        # Load watermark + restore positions from DB (virtual mode)
        if not self._futu_enabled:
            self._load_watermark()
            self._load_state()

        logger.info(
            f"RT SimEngine v2 init: capital={self._pos_mgr.cash:.0f}, "
            f"positions={len(self._pos_mgr.positions)}, "
            f"daily_tracker={'yes' if daily_tracker else 'no'}, "
            f"futu={'ON' if self._futu_enabled else 'OFF'}, "
            f"watermark={self._last_processed_ts}"
        )

    # ── State persistence ──

    def _load_watermark(self):
        """Load watermark: process all signals from today onward."""
        conn = get_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT MAX(ts) as max_ts FROM signals WHERE date < ?",
            (today,),
        ).fetchone()
        if row and row["max_ts"]:
            self._last_processed_ts = row["max_ts"]
        else:
            self._last_processed_ts = 0
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

            # Restore buy_cost_per_share (may not exist in old schema)
            buy_cps = 0.0
            try:
                buy_cps = float(r["buy_cost_per_share"] or 0)
            except (KeyError, TypeError):
                # Old schema: recompute from entry_price + quantity
                info = self._engine.calc_cost(r["entry_price"], r["quantity"], "BUY")
                buy_cps = info["total"] / r["quantity"] if r["quantity"] > 0 else 0

            pos = Position(
                code=r["code"],
                entry_price=r["entry_price"],
                quantity=r["quantity"],
                entry_time=r["entry_time"] or 0,
                entry_date=r["entry_date"] or "",
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                max_hold_days=r["max_hold_days"] or 10,
                confidence=r["confidence"] or 0.6,
                trigger_signals=trigger_signals,
                entry_strategy=r["entry_strategy"] or "",
                highest_price=max(r["entry_price"], r["current_price"] or 0),
                entry_day_index=0,
                buy_cost_per_share=buy_cps,
            )
            self._pos_mgr._positions[r["code"]] = pos
            self._pos_mgr._cash -= r["entry_price"] * r["quantity"]

        if rows:
            logger.info(f"Restored {len(rows)} live positions from DB")

        # Restore cash from historical trades: replay all buy/sell cash flows.
        # Each closed trade: cash -= entry_price * qty + buy_cost (open)
        #                     cash += exit_price * qty - sell_cost (close)
        # total_cost column = buy_cost + sell_cost (for new trades)
        # For old trades where total_cost == commission (sell-only), recompute buy_cost.
        conn = get_connection()
        all_trades = conn.execute(
            "SELECT entry_price, exit_price, quantity, commission, total_cost "
            "FROM trades WHERE param_version='live'"
        ).fetchall()
        conn.close()
        if all_trades:
            cash_delta = 0.0
            for t in all_trades:
                ep, xp, qty = t["entry_price"], t["exit_price"], t["quantity"]
                # Skip Futu-synced trades with missing entry/exit prices (no cash flow to replay)
                if ep is None or xp is None:
                    logger.debug(f"Skipping cash replay for trade {t['trade_id'] if 'trade_id' in t.keys() else '?'}: null entry/exit price")
                    continue
                sell_cost = t["commission"]
                total_cost = t["total_cost"] or sell_cost
                if abs(total_cost - sell_cost) < 0.01:
                    # Old trade: total_cost == sell_cost, need to recompute buy_cost
                    buy_cost_info = self._engine.calc_cost(ep, qty, "BUY")
                    buy_cost = buy_cost_info["total"]
                else:
                    # New trade: total_cost = buy_cost + sell_cost
                    buy_cost = total_cost - sell_cost
                # Net cash impact: (xp * qty - sell_cost) - (ep * qty + buy_cost)
                cash_delta += (xp - ep) * qty - sell_cost - buy_cost
            self._pos_mgr._cash += cash_delta
            logger.info(
                f"Restored cash from {len(all_trades)} historical trades: "
                f"delta={cash_delta:+.2f}, cash={self._pos_mgr._cash:.2f}"
            )

        # Restore daily counters from DB (restart safety)
        today = datetime.now().strftime("%Y-%m-%d")
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(DISTINCT code) as cnt FROM trades "
            "WHERE param_version='live' AND entry_date=? AND action='SELL'",
            (today,),
        ).fetchone()
        conn.close()
        closed_today = row["cnt"] if row else 0
        self._new_positions_today = len(self._pos_mgr.positions) + closed_today
        if self._new_positions_today > 0:
            logger.info(
                f"Restored _new_positions_today={self._new_positions_today} "
                f"(live={len(self._pos_mgr.positions)}, closed={closed_today})"
            )

        # If past entry window, mark as evaluated to prevent re-entry
        entry_end = self._score_cfg.get("entry_window_end", "10:30")
        now_hm = datetime.now().strftime("%H:%M")
        if now_hm > entry_end:
            self._entry_evaluated_today = True
            logger.info(f"Past entry window ({entry_end}), marked entry_evaluated=True")

        # If past exit review time, mark as evaluated
        exit_time = self._score_cfg.get("exit_review_time", "15:30")
        if now_hm > exit_time:
            self._exit_evaluated_today = True

    def _load_state_from_futu(self):
        """Futu 模式: 从 Futu 持仓 + 资金恢复影子 PM 状态。"""
        if not self._futu or not self._futu_enabled:
            return
        try:
            positions = self._futu.get_positions()
            funds = self._futu.get_funds()
            self._pos_mgr.sync_from_futu(positions, funds.cash)
            logger.info(
                f"Loaded state from Futu: {len(positions)} positions, "
                f"cash={funds.cash:.0f}, equity={funds.total_assets:.0f}"
            )
        except Exception as e:
            logger.warning(f"Failed to load state from Futu: {e}")

    def _sync_shadow_pm(self):
        """Futu 模式: 定期同步 Futu 持仓到影子 PM。"""
        if not self._futu or not self._futu_enabled:
            return
        try:
            positions = self._futu.get_positions()
            funds = self._futu.get_funds()
            self._pos_mgr.sync_from_futu(positions, funds.cash)
        except Exception as e:
            logger.debug(f"Shadow PM sync failed: {e}")

    def _persist_state(self, prices: dict[str, float]):
        """Write all current positions to live_state table (full replace)."""
        conn = get_connection()
        now_ts = int(time.time() * 1000)

        current_codes = set(self._pos_mgr.positions.keys())
        conn.execute(
            "DELETE FROM live_state WHERE code NOT IN ({})".format(
                ",".join("?" for _ in current_codes)
            ) if current_codes else "DELETE FROM live_state",
            list(current_codes) if current_codes else [],
        )

        for code, pos in self._pos_mgr.positions.items():
            current_price = prices.get(code, pos.entry_price)
            unrealized = (current_price - pos.entry_price) * pos.quantity
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

            # v2: get daily score (uses per-tick cache)
            daily_score = self._get_score(code).get("total", 0)

            conn.execute(
                """INSERT OR REPLACE INTO live_state
                   (code, name, entry_price, quantity, current_price, entry_time, entry_date,
                    stop_loss, take_profit, max_hold_days, entry_strategy, confidence,
                    trigger_signals, unrealized_pnl, pnl_pct, daily_score,
                    buy_cost_per_share, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code, "", pos.entry_price, pos.quantity, round(current_price, 4),
                    pos.entry_time, pos.entry_date,
                    round(pos.stop_loss, 4), round(pos.take_profit, 4) if pos.take_profit else None,
                    pos.max_hold_days, pos.entry_strategy, pos.confidence,
                    json.dumps(pos.trigger_signals),
                    round(unrealized, 2), round(pnl_pct, 6), daily_score,
                    round(pos.buy_cost_per_share, 6), now_ts,
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
                    trade["commission"], trade.get("total_cost", trade["commission"]),
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
                    "open": float(svc.get("open", 0) or 0),
                    "prevClose": float(svc.get("prevClose", 0) or 0),
                }
        return result

    def _estimate_atr(self, code: str, date: str) -> float:
        """Estimate ATR: prefer daily tracker, fallback to price snapshots."""
        if self._daily_tracker:
            atr = self._daily_tracker.get_atr(code)
            if atr > 0:
                return atr

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

    def _get_score(self, code: str) -> dict:
        """Get daily score with per-tick caching (avoid 3x computation per position)."""
        if code in self._score_cache:
            return self._score_cache[code]
        if not self._daily_tracker:
            return {"total": 0, "action": "WAIT"}
        try:
            result = self._daily_tracker.score(code)
        except Exception:
            result = {"total": 0, "action": "WAIT"}
        self._score_cache[code] = result
        return result

    def _get_watchlist_codes(self) -> list[str]:
        """Get holding codes from monitor_config.json.

        Virtual mode: HK only (original behavior).
        Futu mode: HK + A-share holdings.
        """
        try:
            with open(MONITOR_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

        codes = []
        for code, info in config.get("watchlist", {}).items():
            if info.get("type") != "holding" or info.get("hidden", False):
                continue
            if self._futu_enabled:
                # Futu mode: both HK and A-share
                codes.append(code)
            else:
                # Virtual mode: HK only
                if code.startswith("HK"):
                    codes.append(code)
        return codes

    # ── v2 Core logic ──

    def tick(self):
        """Main entry point — called by daemon every 3s during trading hours.

        v2 流程:
        1. 时间窗口判断
        2. 入场窗口 (10:00-10:30): 日线评分选股开仓
        3. 全天: 消费新信号 — 仅处理 T3 纠偏 (加手续费过滤)
        4. 日内例外: 极强信号 (conf >= 0.85 且 score >= 60)
        5. 收盘评估 (15:30): 评分跌破阈值 → 平仓
        6. 止损/止盈始终实时检查
        7. 持久化
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Day rollover
        if today != self._current_date:
            self.daily_reset()
            self._current_date = today
            self._day_index += 1

        now = datetime.now()
        time_str = now.strftime("%H:%M")
        now_ts = int(time.time() * 1000)

        # Clear per-tick score cache
        self._score_cache.clear()

        # Read market data once
        market = self._read_market_prices()
        prices = {code: info["price"] for code, info in market.items()}

        # Config
        cfg = self._score_cfg
        entry_start = cfg.get("entry_window_start", "10:00")
        entry_end = cfg.get("entry_window_end", "10:30")
        exit_time = cfg.get("exit_review_time", "15:30")
        max_per_day = cfg.get("max_new_positions_per_day", 1)

        in_entry_window = entry_start <= time_str <= entry_end
        in_exit_window = time_str >= exit_time

        # 1. Entry window: evaluate daily score for potential entries
        if (in_entry_window
                and not self._entry_evaluated_today
                and self._new_positions_today < max_per_day):
            self._evaluate_entries(today, prices, market)
            self._entry_evaluated_today = True

        # 1b. Dip-buy window — conviction + drawdown entry channel
        db_cfg = self._rules.get("dip_buy", {})
        if (db_cfg.get("enabled")
                and db_cfg.get("window_start", "09:45") <= time_str <= db_cfg.get("window_end", "14:30")):
            self._evaluate_dip_buy(today, prices, market)

        # 2. Consume new signals from DB — v2 only processes T3 + intraday exceptions
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM signals WHERE ts > ? ORDER BY ts",
            (self._last_processed_ts,),
        ).fetchall()
        conn.close()

        new_signals = [dict(r) for r in rows]
        for sig in new_signals:
            self._process_signal_v2(sig, time_str, prices, market)
            self._last_processed_ts = max(self._last_processed_ts, sig.get("ts", 0))

        # 3. Exit review: score below threshold → close
        if in_exit_window and not self._exit_evaluated_today:
            self._evaluate_exits(today, prices, market)
            self._exit_evaluated_today = True

        # 4. Risk exits (stop-loss / take-profit / max-hold) — always active
        if prices and self._pos_mgr.positions:
            exit_trades = self._pos_mgr.check_exits(
                prices, self._day_index,
                current_ts=now_ts,
                current_date=today,
                cost_calculator=lambda p, q, a: self._engine.calc_cost(p, q, a),
                min_hold_minutes=cfg.get("min_hold_minutes", 30),
            )
            for trade in exit_trades:
                trade["notes"] = trade.get("notes", "")
                # Futu mode: send sell order to Futu for risk exits
                if self._futu_enabled and self._futu:
                    result = self._futu.sell(
                        trade["code"], trade["exit_price"], trade["quantity"]
                    )
                    if result.success and self._futu_sync:
                        self._futu_sync.save_order(
                            result.order_id, trade["code"], "SELL",
                            trade["exit_price"], trade["quantity"],
                            reason=trade.get("exit_reason", "risk_exit"),
                        )
                    elif not result.success:
                        logger.warning(
                            f"Futu sell failed for risk exit {trade['code']}: "
                            f"{result.error_msg}"
                        )
                self._save_trade(trade)
                self._last_exit_ts[trade["code"]] = now_ts
                logger.info(
                    f"RT EXIT {trade['code']}: {trade['exit_price']:.2f} "
                    f"PnL={trade['pnl']:+.0f} ({trade['exit_reason']})"
                )

        # 5. Futu sync: periodic position sync + order status update
        if self._futu_enabled and self._futu_sync:
            if self._tick_count % self._sync_interval == 0:
                try:
                    # Detect closes before syncing (uses prev vs current snapshot)
                    closed = self._futu_sync.detect_and_save_closed()
                    for c in closed:
                        logger.info(f"Futu close detected: {c['code']} ({c['exit_reason']})")

                    # Sync live state from Futu → SQLite
                    daily_scores = {
                        code: self._get_score(code).get("total", 0)
                        for code in self._pos_mgr.positions
                    }
                    self._futu_sync.sync_live_state(daily_scores)

                    # Update pending order statuses
                    updates = self._futu.check_pending_orders()
                    if updates:
                        self._futu_sync.update_order_status(updates)

                    # Sync Futu positions → shadow PM for risk checks
                    self._sync_shadow_pm()
                except Exception as e:
                    logger.debug(f"Futu sync error: {e}")
        else:
            # 5b. Virtual mode: persist to live_state as before
            self._persist_state(prices)

        self._tick_count += 1

    def _evaluate_entries(self, date: str, prices: dict[str, float],
                          market: dict[str, dict]):
        """入场窗口: 用日线评分选股开仓。

        - 遍历 watchlist 中的 HK holdings
        - 计算 daily score >= entry_threshold → 候选
        - 按评分排序，选最高的开仓（每天最多 max_new_positions_per_day 只）
        """
        if not self._daily_tracker:
            logger.debug("No daily_tracker, skip entry evaluation")
            return

        cfg = self._score_cfg
        entry_threshold = cfg.get("entry_threshold", 70)
        cooldown_min = cfg.get("reentry_cooldown_minutes", 120)
        position_pct = cfg.get("position_pct", 0.25)
        max_hold = cfg.get("max_hold_days", 10)
        max_per_day = cfg.get("max_new_positions_per_day", 1)
        now_ts = int(time.time() * 1000)

        candidates = []
        for code in self._get_watchlist_codes():
            # Skip if already holding
            if code in self._pos_mgr.positions:
                continue

            # Check cooldown
            last_exit = self._last_exit_ts.get(code, 0)
            if (now_ts - last_exit) < cooldown_min * 60 * 1000:
                logger.debug(f"Cooldown active for {code}, skip entry")
                continue

            # Get daily score (cached)
            score_result = self._get_score(code)
            total = score_result.get("total", 0)

            if total >= entry_threshold:
                candidates.append((code, score_result))
                logger.info(
                    f"Entry candidate: {code} score={total} "
                    f"action={score_result['action']} "
                    f"SL={score_result.get('stop_loss', 0):.2f}"
                )

        # Sort by score descending
        candidates.sort(key=lambda x: x[1]["total"], reverse=True)

        # Open positions for top candidates
        slots = max_per_day - self._new_positions_today
        for code, score_result in candidates[:slots]:
            price = prices.get(code, 0)
            if price <= 0:
                continue

            atr = score_result.get("atr", 0) or self._estimate_atr(code, date)
            daily_amount = market.get(code, {}).get("amount", 0)

            # Create a synthetic TradeDecision
            decision = TradeDecision(
                action="BUY",
                code=code,
                confidence=min(score_result["total"] / 100, 0.95),
                position_pct=position_pct,
                stop_atr=2.0,
                max_hold_days=max_hold,
                trigger_signal_ids=[],
                reason=f"daily_score={score_result['total']}",
                direction="bullish",
            )

            # Execute with slippage
            exec_info = self._engine.execute_trade(decision, price, daily_amount, atr)
            exec_price = exec_info["exec_price"]

            alloc = self._pos_mgr.cash * position_pct
            est_qty = self._pos_mgr._align_lot(code, int(alloc / exec_price))
            if est_qty <= 0:
                continue

            # Min notional filter: avoid tiny positions with disproportionate fees
            notional = exec_price * est_qty
            min_notional = cfg.get("min_notional", 30000)
            if notional < min_notional:
                logger.debug(f"Skip {code}: notional {notional:.0f} < min {min_notional}")
                continue

            cost_info = self._engine.calc_cost(exec_price, est_qty, "BUY")

            # Override stop_loss / take_profit from scorer
            # Sanity check: if scorer SL/TP is wildly off from exec_price
            # (e.g. ex-rights kline vs real-time price mismatch), fallback to ATR-based
            sl = score_result.get("stop_loss", 0)
            tp = score_result.get("take_profit", 0)
            if sl <= 0 or sl >= exec_price or abs(sl - exec_price) / exec_price > 0.15:
                sl = exec_price - atr * 2
                logger.warning(
                    f"Scorer SL {score_result.get('stop_loss', 0):.2f} invalid for "
                    f"{code}@{exec_price:.2f}, fallback SL={sl:.2f}"
                )
            if tp <= 0 or tp <= exec_price or abs(tp - exec_price) / exec_price > 0.15:
                tp = exec_price + atr * 3

            # Futu mode: send buy order to Futu
            if self._futu_enabled and self._futu:
                buy_result = self._futu.buy(code, exec_price, est_qty)
                if buy_result.success:
                    if self._futu_sync:
                        self._futu_sync.save_order(
                            buy_result.order_id, code, "BUY",
                            exec_price, est_qty,
                            reason=f"daily_score={score_result['total']}",
                        )
                    logger.info(
                        f"RT FUTU-BUY {code}: {est_qty} @ {exec_price:.2f} "
                        f"score={score_result['total']} order={buy_result.order_id}"
                    )
                else:
                    logger.warning(f"Futu buy failed for {code}: {buy_result.error_msg}")
                    continue  # Skip shadow PM open if Futu order fails

            pos = self._pos_mgr.open_position(
                decision, exec_price, atr,
                trade_cost=cost_info["total"],
                current_date=date, current_ts=int(time.time() * 1000),
                day_index=self._day_index,
            )
            if pos:
                # Override SL/TP from scorer
                pos.stop_loss = sl
                pos.take_profit = tp
                pos.max_hold_days = max_hold
                self._new_positions_today += 1
                logger.info(
                    f"RT BUY {code}: {est_qty} @ {exec_price:.2f} "
                    f"score={score_result['total']} "
                    f"SL={sl:.2f} TP={tp:.2f} "
                    f"(daily_score entry)"
                )

    def _get_dip_buy_codes(self) -> list[str]:
        """Get codes with dip_buy=true from monitor_config.json (any type)."""
        try:
            with open(MONITOR_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

        codes = []
        for code, info in config.get("watchlist", {}).items():
            if info.get("dip_buy") and not info.get("hidden", False):
                codes.append(code)
        return codes

    def _notify_dip_buy(self, code: str, drawdown_pct: float, price: float,
                        score: int, high_20d: float, date: str):
        """Send macOS notification + write alert_event for dip_buy opportunity."""
        now_ts = int(time.time() * 1000)

        # macOS notification (stealth title, audible)
        msg = f"{code} dd={drawdown_pct:+.1f}% @{price:.2f}"
        titles = ["CI Pipeline Alert", "Deploy Monitor", "SRE Notification", "Build Status"]
        title = titles[now_ts % len(titles)]
        try:
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", msg,
                 "-sound", "default", "-open", "http://localhost:3120/sim"],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            logger.debug(f"Dip-buy notification failed: {e}")

        # Write to alert_events table
        display = (
            f"[DIP_BUY] {code} 距20日高点({high_20d:.2f})回撤{abs(drawdown_pct):.1f}%, "
            f"现价{price:.2f}, 评分{score}, 建议关注抄底"
        )
        try:
            conn = get_connection()
            conn.execute(
                """INSERT OR IGNORE INTO alert_events
                   (ts, date, symbol, kind, level, message, display, change_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (now_ts, date, code, "dip_buy", "L1", msg, display, drawdown_pct),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Dip-buy alert_event write failed: {e}")

    def _evaluate_dip_buy(self, date: str, prices: dict[str, float],
                          market: dict[str, dict]):
        """Conviction + Drawdown 抄底: dip_buy 标记的股票回撤达标 → 通知 + 模拟开仓。

        独立于 v2 评分入场，作为并行第二入场通道。
        通知与交易解耦：通知不受仓位限制，交易受限。
        """
        db_cfg = self._rules.get("dip_buy", {})
        if not db_cfg.get("enabled") or not self._daily_tracker:
            return

        drawdown_pct = db_cfg.get("drawdown_pct", 8.0)
        lookback_days = db_cfg.get("lookback_days", 20)
        min_score = db_cfg.get("min_score", 30)
        min_ma_score = db_cfg.get("min_ma_score", 5)
        position_pct = db_cfg.get("position_pct", 0.15)
        sl_atr_mult = db_cfg.get("stop_loss_atr_mult", 3.0)
        max_db_per_day = db_cfg.get("max_per_day", 1)
        cooldown_min = self._score_cfg.get("reentry_cooldown_minutes", 120)
        min_notional = self._score_cfg.get("min_notional", 30000)
        max_hold = self._score_cfg.get("max_hold_days", 10)
        max_per_day = self._score_cfg.get("max_new_positions_per_day", 1)
        now_ts = int(time.time() * 1000)

        dip_buy_codes = self._get_dip_buy_codes()
        if not dip_buy_codes:
            return

        db_entries_today = sum(
            1 for c in self._dip_buy_notified_today
            if c in self._pos_mgr.positions
        )

        candidates = []
        for code in dip_buy_codes:
            # Already notified today — skip (dedup per stock per day)
            if code in self._dip_buy_notified_today:
                continue

            price = prices.get(code, 0)
            if price <= 0:
                continue

            # 20-day high from daily tracker
            high_nd = self._daily_tracker.get_recent_high(code, lookback_days)
            if high_nd <= 0:
                continue

            # Drawdown check
            dd = (price - high_nd) / high_nd * 100  # negative if below high
            if dd > -drawdown_pct:
                continue

            # Daily score check
            score_result = self._get_score(code)
            total = score_result.get("total", 0)
            if total < min_score:
                logger.debug(f"Dip-buy skip {code}: score={total} < {min_score}")
                continue

            # Non-bearish MA check
            ma_score = score_result.get("ma", 0)
            if ma_score < min_ma_score:
                logger.debug(f"Dip-buy skip {code}: ma={ma_score} < {min_ma_score}")
                continue

            candidates.append((code, score_result, high_nd, dd))
            logger.info(
                f"Dip-buy candidate: {code} dd={dd:.1f}% high{lookback_days}d={high_nd:.2f} "
                f"price={price:.2f} score={total} ma={ma_score}"
            )

        if not candidates:
            return

        # Sort by drawdown severity (most oversold first)
        candidates.sort(key=lambda x: x[3])

        for code, score_result, high_nd, dd in candidates:
            price = prices.get(code, 0)
            if price <= 0:
                continue

            # 1. Always notify (not subject to position limits)
            self._notify_dip_buy(code, dd, price, score_result["total"], high_nd, date)
            self._dip_buy_notified_today.add(code)

            # 2. Sim trade (subject to limits)
            if code in self._pos_mgr.positions:
                logger.info(f"Dip-buy {code}: already holding, notify only")
                continue
            if self._new_positions_today >= max_per_day:
                logger.info(f"Dip-buy {code}: daily limit reached, notify only")
                continue
            if db_entries_today >= max_db_per_day:
                logger.info(f"Dip-buy {code}: dip_buy daily limit reached, notify only")
                continue

            # Cooldown check
            last_exit = self._last_exit_ts.get(code, 0)
            if (now_ts - last_exit) < cooldown_min * 60 * 1000:
                logger.info(f"Dip-buy {code}: cooldown active, notify only")
                continue

            atr = score_result.get("atr", 0) or self._estimate_atr(code, date)
            daily_amount = market.get(code, {}).get("amount", 0)

            decision = TradeDecision(
                action="BUY",
                code=code,
                confidence=min(score_result["total"] / 100, 0.80),
                position_pct=position_pct,
                stop_atr=sl_atr_mult,
                max_hold_days=max_hold,
                trigger_signal_ids=[],
                reason=f"dip_buy(score={score_result['total']},dd={dd:.1f}%)",
                direction="bullish",
            )

            exec_info = self._engine.execute_trade(decision, price, daily_amount, atr)
            exec_price = exec_info["exec_price"]

            alloc = self._pos_mgr.cash * position_pct
            est_qty = self._pos_mgr._align_lot(code, int(alloc / exec_price))
            if est_qty <= 0:
                continue

            notional = exec_price * est_qty
            if notional < min_notional:
                logger.debug(f"Dip-buy skip {code}: notional {notional:.0f} < {min_notional}")
                continue

            cost_info = self._engine.calc_cost(exec_price, est_qty, "BUY")

            pos = self._pos_mgr.open_position(
                decision, exec_price, atr,
                trade_cost=cost_info["total"],
                current_date=date, current_ts=now_ts,
                day_index=self._day_index,
            )
            if pos:
                # SL: entry - ATR * 3 (wider for volatility)
                pos.stop_loss = round(exec_price - atr * sl_atr_mult, 4)
                # TP: 20-day high (recovery target)
                pos.take_profit = round(high_nd, 4)
                pos.max_hold_days = max_hold

                self._new_positions_today += 1
                db_entries_today += 1
                logger.info(
                    f"RT DIP-BUY {code}: {est_qty} @ {exec_price:.2f} "
                    f"dd={dd:.1f}% high{lookback_days}d={high_nd:.2f} "
                    f"score={score_result['total']} "
                    f"SL={pos.stop_loss:.2f} TP={pos.take_profit:.2f} "
                    f"(dip_buy)"
                )

    def _evaluate_exits(self, date: str, prices: dict[str, float],
                        market: dict[str, dict]):
        """收盘评估: 评分跌破 exit_threshold → 平仓。"""
        if not self._daily_tracker:
            return

        cfg = self._score_cfg
        exit_threshold = cfg.get("exit_threshold", 40)
        exit_extreme = cfg.get("exit_extreme_threshold", 30)
        exit_days = cfg.get("exit_consecutive_days", 2)
        now_ts = int(time.time() * 1000)

        # 清理已平仓股票的计数（止损/止盈等途径已平仓）
        self._low_score_count = {k: v for k, v in self._low_score_count.items()
                                  if k in self._pos_mgr.positions}

        for code in list(self._pos_mgr.positions.keys()):
            score_result = self._get_score(code)
            total = score_result.get("total", 0)

            # Skip if no reliable data (insufficient kline history)
            if total == 0 or score_result.get("action") == "WAIT":
                logger.debug(f"Exit eval skipped for {code}: insufficient data (score={total})")
                continue

            if total >= exit_threshold:
                # 评分恢复，重置连续低分计数
                if code in self._low_score_count:
                    logger.info(f"Exit inertia reset {code}: score={total} recovered above {exit_threshold}")
                    del self._low_score_count[code]
                continue

            # 极端低分（<exit_extreme）：立即平仓，不等确认
            immediate_exit = total < exit_extreme
            if not immediate_exit:
                # 普通低分：需连续 exit_days 天才平仓
                self._low_score_count[code] = self._low_score_count.get(code, 0) + 1
                count = self._low_score_count[code]
                if count < exit_days:
                    logger.info(
                        f"Exit candidate {code}: score={total}<{exit_threshold}, "
                        f"day {count}/{exit_days} — waiting for confirmation"
                    )
                    continue

            reason = (f"exit_score={total}<{exit_extreme}(extreme)" if immediate_exit
                      else f"exit_score={total}<{exit_threshold}(day{self._low_score_count[code]})")

            pos = self._pos_mgr.positions[code]
            price = prices.get(code, pos.entry_price)
            daily_amount = market.get(code, {}).get("amount", 0)
            atr = self._estimate_atr(code, date)

            decision = TradeDecision(
                action="SELL",
                code=code,
                confidence=0.80,
                position_pct=1.0,
                reason=reason,
                direction="bearish",
            )

            exec_info = self._engine.execute_trade(decision, price, daily_amount, atr)
            exec_price = exec_info["exec_price"]
            cost_info = self._engine.calc_cost(exec_price, pos.quantity, "SELL")

            trade = self._pos_mgr.close_position(
                code, exec_price, decision.reason,
                pct=1.0,
                trade_cost=cost_info["total"],
                current_ts=now_ts, current_date=date,
                day_index=self._day_index,
            )
            # Futu mode: send sell order
            if self._futu_enabled and self._futu:
                sell_result = self._futu.sell(code, exec_price, pos.quantity)
                if sell_result.success and self._futu_sync:
                    self._futu_sync.save_order(
                        sell_result.order_id, code, "SELL",
                        exec_price, pos.quantity,
                        reason=decision.reason,
                    )

            if trade:
                trade["notes"] = decision.reason
                self._save_trade(trade)
                self._last_exit_ts[code] = now_ts
                self._low_score_count.pop(code, None)
                logger.info(
                    f"RT SELL {code}: @ {exec_price:.2f} "
                    f"PnL={trade['pnl']:+.0f} score={total} "
                    f"(exit_review)"
                )

    def _process_signal_v2(self, signal: dict, time_str: str,
                           prices: dict[str, float], market: dict[str, dict]):
        """v2 信号处理: 日内仅处理 T3 纠偏 + 极强日内例外。

        T3 纠偏加手续费过滤 + min_hold 检查。
        极强信号 (confidence >= 0.85 且 score >= 60) 作为日内例外入场。
        """
        # Parse detail
        detail = signal.get("detail", "{}")
        if isinstance(detail, str):
            try:
                signal["detail"] = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                signal["detail"] = {}

        code = signal.get("code", "")
        strategy = signal.get("strategy", "")
        date = signal.get("date", self._current_date)

        # Identify tier
        tier3_strategies = set(self._rules.get("tiers", {}).get("3_correction", {}).keys())
        tier1_strategies = set(self._rules.get("tiers", {}).get("1_independent", {}).keys())

        # ── T3 correction: with cost filter + min_hold ──
        if strategy in tier3_strategies and code in self._pos_mgr.positions:
            self._handle_t3_with_filters(signal, prices, market, date)
            return

        # ── Intraday exception: extremely strong T1 signal ──
        if strategy in tier1_strategies:
            self._handle_intraday_exception(signal, prices, market, date)

    def _handle_t3_with_filters(self, signal: dict, prices: dict[str, float],
                                market: dict[str, dict], date: str):
        """T3 纠偏: 加手续费过滤 + min_hold 检查。

        - 亏损不值手续费 → 不割
        - 持有时间 < min_hold_minutes → 不卖
        """
        code = signal.get("code", "")
        strategy = signal.get("strategy", "")
        pos = self._pos_mgr.positions.get(code)
        if not pos:
            return

        cfg = self._score_cfg
        min_hold_min = cfg.get("min_hold_minutes", 30)
        min_profit_pct = cfg.get("min_profit_after_cost_pct", 0.003)
        now_ts = int(time.time() * 1000)

        # min_hold check
        hold_ms = now_ts - pos.entry_time
        hold_minutes = hold_ms / 60000
        if hold_minutes < min_hold_min:
            logger.debug(
                f"T3 {strategy} blocked for {code}: "
                f"held {hold_minutes:.0f}min < min_hold {min_hold_min}min"
            )
            return

        # Cost filter: don't close if loss < round-trip cost
        current_price = prices.get(code, pos.entry_price)
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        # If unrealized P&L is between -cost and +cost, not worth closing
        if abs(pnl_pct) < min_profit_pct:
            logger.debug(
                f"T3 {strategy} blocked for {code}: "
                f"|pnl_pct|={abs(pnl_pct):.4f} < min_profit {min_profit_pct}"
            )
            return

        # Only close if in loss (T3 is risk correction)
        if pnl_pct > 0:
            # Profitable: T3 can tighten stop instead of selling
            atr = self._estimate_atr(code, date)
            self._pos_mgr.tighten_stop(code, 1.0, current_price, atr)
            logger.info(f"T3 {strategy} → tighten SL for {code} (profitable, pnl={pnl_pct:.2%})")
            return

        # Proceed with T3 sell
        equity = self._pos_mgr.get_equity(prices) if prices else self._pos_mgr.cash
        decision = self._mapper.process_signal(
            signal, self._pos_mgr.positions, equity, date,
        )
        if not decision:
            return

        if decision.action == "SELL":
            daily_amount = market.get(code, {}).get("amount", 0)
            atr = self._estimate_atr(code, date)
            exec_info = self._engine.execute_trade(decision, current_price, daily_amount, atr)
            exec_price = exec_info["exec_price"]
            cost_info = self._engine.calc_cost(exec_price, pos.quantity, "SELL")

            trade = self._pos_mgr.close_position(
                code, exec_price, f"T3:{strategy}(v2)",
                pct=decision.position_pct,
                trade_cost=cost_info["total"],
                current_ts=signal.get("ts", 0), current_date=date,
                day_index=self._day_index,
            )
            # Futu mode: send sell order (same as normal exit path)
            if self._futu_enabled and self._futu:
                sell_result = self._futu.sell(code, exec_price, pos.quantity)
                if sell_result.success and self._futu_sync:
                    self._futu_sync.save_order(
                        sell_result.order_id, code, "SELL",
                        exec_price, pos.quantity,
                        reason=f"T3:{strategy}(v2)",
                    )
            if trade:
                trade["notes"] = f"T3:{strategy}(v2)"
                self._save_trade(trade)
                self._last_exit_ts[code] = int(time.time() * 1000)
                logger.info(
                    f"RT T3-SELL {code}: @ {exec_price:.2f} "
                    f"PnL={trade['pnl']:+.0f} ({strategy})"
                )

        elif decision.action == "TIGHTEN_SL":
            atr = self._estimate_atr(code, date)
            self._pos_mgr.tighten_stop(code, decision.stop_atr, current_price, atr)
            logger.info(f"RT T3-TIGHTEN {code} ({strategy})")

    def _handle_intraday_exception(self, signal: dict, prices: dict[str, float],
                                   market: dict[str, dict], date: str):
        """日内例外: 极强 L2 信号可触发入场。

        条件: confidence >= intraday_exception_confidence
              且 daily score >= intraday_exception_min_score
              且未超过每日最大开仓数
        """
        cfg = self._score_cfg
        min_conf = cfg.get("intraday_exception_confidence", 0.85)
        min_score = cfg.get("intraday_exception_min_score", 60)
        max_per_day = cfg.get("max_new_positions_per_day", 1)
        position_pct = cfg.get("position_pct", 0.25)
        max_hold = cfg.get("max_hold_days", 10)
        cooldown_min = cfg.get("reentry_cooldown_minutes", 120)
        now_ts = int(time.time() * 1000)

        code = signal.get("code", "")
        strategy = signal.get("strategy", "")
        direction = signal.get("direction", "neutral")

        # Only BUY signals
        tier1_rule = self._rules.get("tiers", {}).get("1_independent", {}).get(strategy, {})
        action = tier1_rule.get("action", "")
        if action == "DIRECTION":
            action = "BUY" if direction == "bullish" else ("SELL" if direction == "bearish" else "")
        if action != "BUY":
            return

        # Already holding
        if code in self._pos_mgr.positions:
            return

        # Daily limit
        if self._new_positions_today >= max_per_day:
            return

        # Cooldown
        last_exit = self._last_exit_ts.get(code, 0)
        if (now_ts - last_exit) < cooldown_min * 60 * 1000:
            return

        # Confidence check
        confidence = tier1_rule.get("confidence", 0.5)
        if confidence < min_conf:
            return

        # Score check (cached)
        daily_score = self._get_score(code).get("total", 0)

        if daily_score < min_score:
            return

        # All checks passed — open position
        price = prices.get(code, 0)
        if price <= 0:
            return

        atr = self._estimate_atr(code, date)
        daily_amount = market.get(code, {}).get("amount", 0)

        decision = TradeDecision(
            action="BUY",
            code=code,
            confidence=confidence,
            position_pct=position_pct,
            stop_atr=2.0,
            max_hold_days=max_hold,
            trigger_signal_ids=[signal.get("id", 0)],
            reason=f"intraday_exception:{strategy}(score={daily_score})",
            direction="bullish",
        )

        exec_info = self._engine.execute_trade(decision, price, daily_amount, atr)
        exec_price = exec_info["exec_price"]

        alloc = self._pos_mgr.cash * position_pct
        est_qty = self._pos_mgr._align_lot(code, int(alloc / exec_price))
        if est_qty <= 0:
            return

        # Min notional filter
        notional = exec_price * est_qty
        min_notional = cfg.get("min_notional", 30000)
        if notional < min_notional:
            logger.debug(f"Skip intraday {code}: notional {notional:.0f} < min {min_notional}")
            return

        cost_info = self._engine.calc_cost(exec_price, est_qty, "BUY")

        pos = self._pos_mgr.open_position(
            decision, exec_price, atr,
            trade_cost=cost_info["total"],
            current_date=date, current_ts=signal.get("ts", 0),
            day_index=self._day_index,
        )
        if pos:
            self._new_positions_today += 1
            logger.info(
                f"RT INTRADAY-BUY {code}: {est_qty} @ {exec_price:.2f} "
                f"conf={confidence:.0%} score={daily_score} "
                f"({strategy})"
            )

    def daily_reset(self):
        """Daily reset: mapper session + v2 counters.

        Called by both daemon (midnight) and tick() (day rollover).
        Idempotent — safe to call multiple times on the same day.
        Does NOT increment _day_index; tick() owns that increment.
        Cooldown (_last_exit_ts) cleared intentionally: fresh day, fresh slate.
        """
        self._mapper.reset_session(self._current_date)
        self._new_positions_today = 0
        self._dip_buy_notified_today.clear()
        self._entry_evaluated_today = False
        self._exit_evaluated_today = False
        self._last_exit_ts.clear()
        logger.info(f"RT v2 daily reset: day_index={self._day_index}")
