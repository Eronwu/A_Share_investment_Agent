"""TradeSignalMapper — 将 L2/日K 原始信号转换为交易决策。

Tier 1: 独立信号 → BUY/SELL 决策
Tier 2: 增强信号 → 叠加 confidence boost 到最近同方向 Tier 1 决策
Tier 3: 纠偏信号 → 减仓/止损收紧 (优先级高于 Tier 1)
Tier 4: 仅日志，不产生决策
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("signal_mapper")


@dataclass
class TradeDecision:
    action: str  # BUY / SELL / TIGHTEN_SL / SELL_NEXT_OPEN / SKIP
    code: str
    confidence: float = 0.0
    position_pct: float = 0.0  # BUY: 仓位占比; SELL: sell_pct
    stop_atr: float = 2.0
    max_hold_days: int = 5
    trigger_signal_ids: list[int] = field(default_factory=list)
    reason: str = ""
    direction: str = ""  # bullish / bearish


class TradeSignalMapper:
    """Process raw signals into trade decisions using tiered rules."""

    def __init__(self, rules: dict):
        self._rules = rules
        self._tier1 = rules.get("tiers", {}).get("1_independent", {})
        self._tier2 = rules.get("tiers", {}).get("2_enhance", {})
        self._tier3 = rules.get("tiers", {}).get("3_correction", {})
        self._tier4 = set(rules.get("tiers", {}).get("4_log_only", []))
        self._risk = rules.get("risk_control", {})
        # Recent Tier 1 decisions for Tier 2 boost (code → TradeDecision)
        self._recent_t1: dict[str, tuple[float, TradeDecision]] = {}
        # Session state for conflict detection: (date, code) → list of actions
        self._session_actions: dict[tuple[str, str], list[str]] = {}

    def process_signal(
        self,
        signal: dict,
        active_positions: dict,
        portfolio_equity: float,
        session_date: str,
    ) -> TradeDecision | None:
        """Process a single signal and return a TradeDecision or None.

        Args:
            signal: DB row dict with keys: id, ts, strategy, code, direction, detail, price_at_signal, ...
            active_positions: {code: Position} of current holdings
            portfolio_equity: current total equity
            session_date: YYYY-MM-DD for session tracking
        """
        strategy = signal.get("strategy", "")
        code = signal.get("code", "")
        sig_id = signal.get("id", 0)
        sig_ts = signal.get("ts", 0)
        direction = signal.get("direction", "neutral")

        # Tier 4 — log only
        if strategy in self._tier4:
            return None

        # Tier 3 — correction (highest priority, affects existing positions)
        t3_decision = self._apply_tier3(signal, active_positions)
        if t3_decision:
            return t3_decision

        # Tier 2 — enhance recent Tier 1 decision
        if strategy in self._tier2:
            return self._apply_tier2_boost(signal)

        # Tier 1 — independent signal
        if strategy in self._tier1:
            decision = self._apply_tier1(signal)
            if decision is None:
                return None

            # Risk checks
            if not self._check_risk(decision, active_positions, portfolio_equity):
                return None

            # Conflict check
            key = (session_date, code)
            actions = self._session_actions.setdefault(key, [])

            # Max signals per stock per session
            max_sigs = self._risk.get("max_signals_per_stock_per_session", 3)
            if len(actions) >= max_sigs:
                logger.debug(f"Signal limit reached for {code} on {session_date}")
                return None

            # Bull/bear conflict in same session
            if self._has_conflict(decision.action, actions):
                logger.info(f"Conflict detected for {code}: {decision.action} vs {actions}")
                return None

            actions.append(decision.action)

            # Cache for Tier 2 boost
            self._recent_t1[code] = (sig_ts, decision)

            return decision

        return None

    def _apply_tier1(self, signal: dict) -> TradeDecision | None:
        """Generate a trade decision from a Tier 1 signal."""
        strategy = signal.get("strategy", "")
        code = signal.get("code", "")
        direction = signal.get("direction", "neutral")
        sig_id = signal.get("id", 0)

        rule = self._tier1.get(strategy)
        if not rule:
            return None

        action = rule["action"]

        # DIRECTION action: resolve from signal direction
        if action == "DIRECTION":
            if direction == "bullish":
                action = "BUY"
            elif direction == "bearish":
                action = "SELL"
            else:
                logger.debug(f"DIRECTION signal {strategy} has neutral direction, skip")
                return None

        confidence = rule.get("confidence", 0.5)

        if action == "BUY":
            return TradeDecision(
                action="BUY",
                code=code,
                confidence=confidence,
                position_pct=rule.get("position_pct", 0.10),
                stop_atr=rule.get("stop_atr", 2.0),
                max_hold_days=rule.get("max_hold_days", 5),
                trigger_signal_ids=[sig_id],
                reason=strategy,
                direction="bullish",
            )
        elif action == "SELL":
            return TradeDecision(
                action="SELL",
                code=code,
                confidence=confidence,
                position_pct=rule.get("sell_pct", 1.0),
                trigger_signal_ids=[sig_id],
                reason=strategy,
                direction="bearish",
            )

        return None

    def _apply_tier2_boost(self, signal: dict) -> TradeDecision | None:
        """Apply Tier 2 confidence boost to a recent Tier 1 decision."""
        strategy = signal.get("strategy", "")
        code = signal.get("code", "")
        sig_ts = signal.get("ts", 0)
        direction = signal.get("direction", "neutral")
        sig_id = signal.get("id", 0)
        detail = signal.get("detail", {})
        if isinstance(detail, str):
            import json
            try:
                detail = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                detail = {}

        rule = self._tier2.get(strategy)
        if not rule:
            return None

        # Check if there's a recent T1 decision for same code
        recent = self._recent_t1.get(code)
        if not recent:
            return None

        t1_ts, t1_decision = recent
        boost_window = self._risk.get("min_signal_interval_minutes", 15) * 60 * 1000  # ms
        if sig_ts - t1_ts > boost_window:
            return None  # Too old

        # Direction must match
        if direction not in ("neutral", t1_decision.direction):
            return None

        # Additional checks per strategy
        if strategy == "large_order":
            min_amount = rule.get("min_amount", 10000000)
            lo_amount = detail.get("amount", 0)
            if lo_amount < min_amount:
                return None

        # Apply boost
        boost = rule.get("confidence_boost", 0.10)
        t1_decision.confidence = min(t1_decision.confidence + boost, 0.95)
        t1_decision.trigger_signal_ids.append(sig_id)
        t1_decision.reason += f"+{strategy}"
        logger.info(f"Tier 2 boost: {code} +{boost:.2f} → confidence={t1_decision.confidence:.2f}")

        # Return None — the boost was applied in-place to the cached decision.
        # The original T1 decision was already returned; this just upgrades it.
        return None

    def _apply_tier3(self, signal: dict, positions: dict) -> TradeDecision | None:
        """Generate correction decisions from Tier 3 signals."""
        strategy = signal.get("strategy", "")
        code = signal.get("code", "")
        sig_id = signal.get("id", 0)
        direction = signal.get("direction", "neutral")
        detail = signal.get("detail", {})
        if isinstance(detail, str):
            import json
            try:
                detail = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                detail = {}

        rule = self._tier3.get(strategy)
        if not rule:
            return None

        # Tier 3 only acts on existing positions
        if code not in positions:
            return None

        action = rule["action"]

        # Direction filter (e.g., closing_surge only acts on bearish)
        direction_filter = rule.get("direction_filter")
        if direction_filter and direction != direction_filter:
            return None

        if action == "SELL":
            return TradeDecision(
                action="SELL",
                code=code,
                confidence=0.80,  # Tier 3 is high confidence
                position_pct=rule.get("sell_pct", 0.50),
                trigger_signal_ids=[sig_id],
                reason=f"T3:{strategy}",
                direction="bearish",
            )
        elif action == "TIGHTEN_SL":
            return TradeDecision(
                action="TIGHTEN_SL",
                code=code,
                confidence=0.75,
                stop_atr=rule.get("stop_atr", 1.0),
                trigger_signal_ids=[sig_id],
                reason=f"T3:{strategy}",
            )
        elif action == "SELL_NEXT_OPEN":
            return TradeDecision(
                action="SELL_NEXT_OPEN",
                code=code,
                confidence=0.75,
                position_pct=rule.get("sell_pct", 1.0),
                trigger_signal_ids=[sig_id],
                reason=f"T3:{strategy}",
                direction="bearish",
            )

        return None

    def _has_conflict(self, new_action: str, existing_actions: list[str]) -> bool:
        """Check if new action conflicts with existing session actions."""
        buys = {"BUY"}
        sells = {"SELL", "SELL_NEXT_OPEN"}
        if new_action in buys and any(a in sells for a in existing_actions):
            return True
        if new_action in sells and any(a in buys for a in existing_actions):
            return True
        return False

    def _check_risk(
        self, decision: TradeDecision, positions: dict, equity: float
    ) -> bool:
        """Validate decision against risk controls."""
        if decision.action != "BUY":
            return True  # Only BUY needs risk check

        min_conf = self._risk.get("min_confidence", 0.60)
        if decision.confidence < min_conf:
            logger.debug(f"Confidence {decision.confidence:.2f} < min {min_conf}")
            return False

        # Single stock concentration
        max_single = self._risk.get("max_single_stock_pct", 0.25)
        code = decision.code
        existing_invested = 0
        if code in positions:
            pos = positions[code]
            existing_invested = pos.entry_price * pos.quantity
        proposed = equity * decision.position_pct
        if (existing_invested + proposed) / equity > max_single:
            logger.debug(f"Single stock limit: {code}")
            return False

        # Total invested check
        max_total = self._risk.get("max_total_invested_pct", 0.80)
        total_invested = sum(p.entry_price * p.quantity for p in positions.values())
        if (total_invested + proposed) / equity > max_total:
            logger.debug(f"Total invested limit reached")
            return False

        return True

    def reset_session(self, date: str):
        """Reset session tracking for a new trading day."""
        # Clear session actions for previous dates
        self._session_actions = {
            k: v for k, v in self._session_actions.items() if k[0] == date
        }
        self._recent_t1.clear()
