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
    """v2: Daily-score-driven real-time sim engine, called by l2_daemon.tick()."""

    def __init__(self, rules: dict, daily_tracker=None):
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
        self._new_positions_today = 0
        self._entry_evaluated_today = False
        self._exit_evaluated_today = False
        # Per-tick score cache: {code: score_dict}, cleared each tick
        self._score_cache: dict[str, dict] = {}

        # Load watermark + restore positions from DB
        self._load_watermark()
        self._load_state()
        logger.info(
            f"RT SimEngine v2 init: capital={self._pos_mgr.cash:.0f}, "
            f"positions={len(self._pos_mgr.positions)}, "
            f"daily_tracker={'yes' if daily_tracker else 'no'}, "
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
            )
            self._pos_mgr._positions[r["code"]] = pos
            self._pos_mgr._cash -= r["entry_price"] * r["quantity"]

        if rows:
            logger.info(f"Restored {len(rows)} live positions from DB")

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
                   (code, entry_price, quantity, current_price, entry_time, entry_date,
                    stop_loss, take_profit, max_hold_days, entry_strategy, confidence,
                    trigger_signals, unrealized_pnl, pnl_pct, daily_score, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code, pos.entry_price, pos.quantity, round(current_price, 4),
                    pos.entry_time, pos.entry_date,
                    round(pos.stop_loss, 4), round(pos.take_profit, 4) if pos.take_profit else None,
                    pos.max_hold_days, pos.entry_strategy, pos.confidence,
                    json.dumps(pos.trigger_signals),
                    round(unrealized, 2), round(pnl_pct, 6), daily_score, now_ts,
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
        """Get HK holding codes from monitor_config.json."""
        try:
            with open(MONITOR_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

        codes = []
        for code, info in config.get("watchlist", {}).items():
            if (code.startswith("HK")
                    and info.get("type") == "holding"
                    and not info.get("hidden", False)):
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
                self._save_trade(trade)
                self._last_exit_ts[trade["code"]] = now_ts
                logger.info(
                    f"RT EXIT {trade['code']}: {trade['exit_price']:.2f} "
                    f"PnL={trade['pnl']:+.0f} ({trade['exit_reason']})"
                )

        # 5. Persist positions to live_state
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

    def _evaluate_exits(self, date: str, prices: dict[str, float],
                        market: dict[str, dict]):
        """收盘评估: 评分跌破 exit_threshold → 平仓。"""
        if not self._daily_tracker:
            return

        cfg = self._score_cfg
        exit_threshold = cfg.get("exit_threshold", 40)
        now_ts = int(time.time() * 1000)

        for code in list(self._pos_mgr.positions.keys()):
            score_result = self._get_score(code)
            total = score_result.get("total", 0)

            # Skip if no reliable data (insufficient kline history)
            if total == 0 or score_result.get("action") == "WAIT":
                logger.debug(f"Exit eval skipped for {code}: insufficient data (score={total})")
                continue

            if total < exit_threshold:
                pos = self._pos_mgr.positions[code]
                price = prices.get(code, pos.entry_price)
                daily_amount = market.get(code, {}).get("amount", 0)
                atr = self._estimate_atr(code, date)

                decision = TradeDecision(
                    action="SELL",
                    code=code,
                    confidence=0.80,
                    position_pct=1.0,
                    reason=f"exit_score={total}<{exit_threshold}",
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
                if trade:
                    trade["notes"] = decision.reason
                    self._save_trade(trade)
                    self._last_exit_ts[code] = now_ts
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
        self._entry_evaluated_today = False
        self._exit_evaluated_today = False
        self._last_exit_ts.clear()
        logger.info(f"RT v2 daily reset: day_index={self._day_index}")
