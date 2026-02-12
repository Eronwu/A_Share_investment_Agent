"""
L2 Strategy Engine — 基于 Futu OpenD 实时数据的策略信号检测

5 个检测策略:
  1. capital_flow_spike      — 主力资金异动（5分钟窗口，净流入占比跳升）
  2. large_order             — 逐笔大单（单笔成交 >500万，含买卖方向）
  3. order_book_imbalance    — 盘口异动（委比短时剧烈变化）
  4. volume_price_divergence — 量价背离（价格新高 + 主力资金净流出）
  5. tick_imbalance          — 主买主卖失衡（5分钟窗口逐笔方向累积）

复合评分（加权）:
  tick_imbalance=3, large_order=2, volume_price_divergence=2,
  capital_flow_spike=1, order_book_imbalance=1
  加权分 ≥5 且 ≥2 种策略同向 → 复合信号（弹通知）

设计原则:
  - 复用 futu_enricher.py 的懒连接/降级模式
  - 冷却机制防止重复告警
  - 信号输出为 dual-format dict（message + display），与 notifier 对接
"""

import socket
import time
from collections import deque
from datetime import datetime
from typing import Optional

from src.utils.logging_config import setup_logger

logger = setup_logger("l2_strategy")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
RECONNECT_COOLDOWN = 60


# ── Code mapping (reuse from futu_enricher) ──────────────

def to_futu_code(code: str) -> str:
    """本项目代码 -> Futu 格式"""
    if code.startswith("HK"):
        return f"HK.{code[2:]}"
    first = code[0]
    if first in ("6", "5"):
        return f"SH.{code}"
    if first in ("0", "1", "2", "3"):
        return f"SZ.{code}"
    return f"SH.{code}"


def from_futu_code(futu_code: str) -> str:
    """Futu 格式 -> 本项目代码"""
    market, num = futu_code.split(".", 1)
    if market == "HK":
        return f"HK{num}"
    return num


# ══════════════════════════════════════════
# Tracker Classes
# ══════════════════════════════════════════

class CapitalFlowTracker:
    """策略1: 主力资金异动 — 滑动窗口检测净流入占比跳升

    检测逻辑:
      窗口内最早记录的 mainNetInflowPct < low_threshold
      AND 最新记录的 mainNetInflowPct > high_threshold
      → 触发信号
    """

    def __init__(self, window_minutes: int = 5,
                 low_threshold_pct: float = 2.0,
                 high_threshold_pct: float = 8.0):
        self.window_sec = window_minutes * 60
        self.low_pct = low_threshold_pct
        self.high_pct = high_threshold_pct
        # {code: deque of (timestamp, mainNetInflowPct)}
        self._history: dict[str, deque] = {}

    def update(self, code: str, inflow_pct: float) -> Optional[dict]:
        """记录最新数据点，检测是否触发信号"""
        now = time.time()

        if code not in self._history:
            self._history[code] = deque()
        q = self._history[code]
        q.append((now, inflow_pct))

        # 清理窗口外的数据
        cutoff = now - self.window_sec
        while q and q[0][0] < cutoff:
            q.popleft()

        if len(q) < 2:
            return None

        earliest_pct = q[0][1]
        latest_pct = q[-1][1]

        if earliest_pct < self.low_pct and latest_pct > self.high_pct:
            return {
                "strategy": "capital_flow_spike",
                "code": code,
                "detail": {
                    "from_pct": round(earliest_pct, 2),
                    "to_pct": round(latest_pct, 2),
                    "window_sec": self.window_sec,
                },
            }
        return None

    def reset(self):
        self._history.clear()


class LargeOrderTracker:
    """策略2: 逐笔大单检测

    检测逻辑: 单笔成交金额 > min_amount → 触发信号
    """

    def __init__(self, min_amount: float = 5_000_000):
        self.min_amount = min_amount
        # {code: last_processed_seq} — 避免重复处理同一笔
        self._last_seq: dict[str, int] = {}
        # 首次调用只记录水位，不触发信号（跳过启动时的存量 tick）
        self._warmed_up: set[str] = set()

    def check_tickers(self, code: str, tickers: list[dict]) -> list[dict]:
        """检查逐笔成交中是否有大单

        Args:
            code: 本项目股票代码
            tickers: Futu get_rt_ticker 返回的行列表
                     每条: {sequence, turnover, volume, price, ...}

        Returns:
            触发的信号列表（首次调用只 warmup，不触发）
        """
        if not tickers:
            return []

        # 首次调用: 只记录最大 seq 水位，跳过存量 tick
        if code not in self._warmed_up:
            max_seq = max(t.get("sequence", 0) for t in tickers)
            self._last_seq[code] = max_seq
            self._warmed_up.add(code)
            return []

        signals = []
        last_seq = self._last_seq.get(code, -1)

        for tick in tickers:
            seq = tick.get("sequence", 0)
            if seq <= last_seq:
                continue
            self._last_seq[code] = seq

            turnover = float(tick.get("turnover", 0))
            if turnover >= self.min_amount:
                # Map Futu ticker_direction to BUY/SELL/NEUTRAL
                raw_dir = str(tick.get("direction", "")).upper()
                if "BUY" in raw_dir:
                    direction = "BUY"
                elif "SELL" in raw_dir:
                    direction = "SELL"
                else:
                    direction = "NEUTRAL"
                signals.append({
                    "strategy": "large_order",
                    "code": code,
                    "detail": {
                        "amount": round(turnover, 0),
                        "price": tick.get("price", 0),
                        "volume": tick.get("volume", 0),
                        "direction": direction,
                    },
                })

        return signals

    def reset(self):
        self._last_seq.clear()
        self._warmed_up.clear()


class OrderBookTracker:
    """策略3: 盘口异动 — 委比短时剧烈变化

    检测逻辑:
      |当前 bidAskRatio - 上次记录| > delta_threshold → 触发
    """

    def __init__(self, delta_threshold: float = 40.0):
        self.delta_threshold = delta_threshold
        # {code: last_ratio}
        self._last_ratio: dict[str, float] = {}

    def update(self, code: str, bid_ask_ratio: float) -> Optional[dict]:
        """记录委比，检测是否剧烈变化"""
        prev = self._last_ratio.get(code)
        self._last_ratio[code] = bid_ask_ratio

        if prev is None:
            return None

        delta = bid_ask_ratio - prev
        if abs(delta) >= self.delta_threshold:
            return {
                "strategy": "order_book_imbalance",
                "code": code,
                "detail": {
                    "prev_ratio": round(prev, 2),
                    "curr_ratio": round(bid_ask_ratio, 2),
                    "delta": round(delta, 2),
                },
            }
        return None

    def reset(self):
        self._last_ratio.clear()


class DivergenceTracker:
    """策略4: 量价背离 — 价格新高 + 主力资金净流出

    检测逻辑:
      当前价格 = 窗口内最高价
      AND mainNetInflow < 0
      → 触发信号
    """

    def __init__(self, lookback_minutes: int = 30):
        self.lookback_sec = lookback_minutes * 60
        # {code: deque of (timestamp, price)}
        self._price_history: dict[str, deque] = {}

    def update(self, code: str, price: float, main_net_inflow: float) -> Optional[dict]:
        """记录价格，结合资金流判断背离"""
        now = time.time()

        if code not in self._price_history:
            self._price_history[code] = deque()
        q = self._price_history[code]
        q.append((now, price))

        # 清理窗口外数据
        cutoff = now - self.lookback_sec
        while q and q[0][0] < cutoff:
            q.popleft()

        # 至少 10 个采样点（~30 秒）才开始判断，避免刚启动就误报
        if len(q) < 10:
            return None

        max_price = max(p for _, p in q)

        # 当前价 = 窗口最高 AND 主力净流出显著（> 100 万）
        if price >= max_price and main_net_inflow < -1_000_000:
            return {
                "strategy": "volume_price_divergence",
                "code": code,
                "detail": {
                    "price": price,
                    "window_high": max_price,
                    "main_net_inflow": round(main_net_inflow, 0),
                },
            }
        return None

    def reset(self):
        self._price_history.clear()


class TickImbalanceTracker:
    """策略5: 逐笔主动买卖失衡检测

    5 分钟窗口内累积:
      buy_vol  = sum(vol where direction=BUY)
      sell_vol = sum(vol where direction=SELL)
      imbalance = (buy - sell) / (buy + sell)

    |imbalance| > threshold AND window_turnover > min_turnover → 触发
    """

    def __init__(self, window_minutes: int = 5,
                 imbalance_threshold: float = 0.4,
                 min_turnover: float = 10_000_000):
        self.window_sec = window_minutes * 60
        self.imbalance_threshold = imbalance_threshold
        self.min_turnover = min_turnover
        # {code: deque of (timestamp, volume, turnover, direction)}
        self._history: dict[str, deque] = {}
        # {code: last_processed_seq} — avoid reprocessing
        self._last_seq: dict[str, int] = {}
        # warmup: first call only records watermark
        self._warmed_up: set[str] = set()

    def update(self, code: str, tickers: list[dict]) -> Optional[dict]:
        """Feed ticks and check for imbalance signal.

        Args:
            code: stock code
            tickers: list of tick dicts with sequence, volume, turnover, direction

        Returns:
            Signal dict if imbalance detected, None otherwise
        """
        if not tickers:
            return None

        now = time.time()

        # First call: warmup — record seq watermark only
        if code not in self._warmed_up:
            max_seq = max(t.get("sequence", 0) for t in tickers)
            self._last_seq[code] = max_seq
            self._warmed_up.add(code)
            return None

        if code not in self._history:
            self._history[code] = deque()

        q = self._history[code]
        last_seq = self._last_seq.get(code, -1)

        # Accumulate new ticks only
        for tick in tickers:
            seq = tick.get("sequence", 0)
            if seq <= last_seq:
                continue
            self._last_seq[code] = seq
            q.append((
                now,
                int(tick.get("volume", 0)),
                float(tick.get("turnover", 0)),
                str(tick.get("direction", "")).upper(),
            ))

        # Trim window
        cutoff = now - self.window_sec
        while q and q[0][0] < cutoff:
            q.popleft()

        if not q:
            return None

        # Calculate imbalance
        buy_vol = 0
        sell_vol = 0
        total_turnover = 0.0
        for _, vol, turnover, direction in q:
            total_turnover += turnover
            if "BUY" in direction:
                buy_vol += vol
            elif "SELL" in direction:
                sell_vol += vol

        total_vol = buy_vol + sell_vol
        if total_vol == 0 or total_turnover < self.min_turnover:
            return None

        imbalance = (buy_vol - sell_vol) / total_vol

        if abs(imbalance) >= self.imbalance_threshold:
            return {
                "strategy": "tick_imbalance",
                "code": code,
                "detail": {
                    "imbalance": round(imbalance, 3),
                    "buy_vol": buy_vol,
                    "sell_vol": sell_vol,
                    "turnover": round(total_turnover, 0),
                    "window_sec": self.window_sec,
                },
            }
        return None

    def reset(self):
        self._history.clear()
        self._last_seq.clear()
        self._warmed_up.clear()


# ══════════════════════════════════════════
# Cooldown Manager
# ══════════════════════════════════════════

class CooldownManager:
    """策略冷却管理器 — 同一策略+股票在冷却期内不重复触发"""

    def __init__(self, cooldowns: dict[str, int]):
        """
        Args:
            cooldowns: {strategy_name: cooldown_minutes}
        """
        self._cooldowns = {k: v * 60 for k, v in cooldowns.items()}
        # {(strategy, code): last_trigger_timestamp}
        self._last_trigger: dict[tuple[str, str], float] = {}

    def can_trigger(self, strategy: str, code: str) -> bool:
        now = time.time()
        key = (strategy, code)
        last = self._last_trigger.get(key, 0)
        cooldown = self._cooldowns.get(strategy, 600)
        return now - last >= cooldown

    def record(self, strategy: str, code: str):
        self._last_trigger[(strategy, code)] = time.time()

    def reset(self):
        self._last_trigger.clear()


# ══════════════════════════════════════════
# Signal Scorer — 复合研判
# ══════════════════════════════════════════
#
# 原始信号安静积累，只有同一只股票 10 分钟内 ≥2 种不同策略
# 同向触发时才产出复合信号（notify=true → 弹通知）。
#
# 方向推断:
#   capital_flow_spike:         to > from → bullish, else bearish
#   order_book_imbalance:       delta > 0 → bullish, else bearish
#   volume_price_divergence:    always bearish
#   large_order:                BUY→bullish, SELL→bearish, NEUTRAL→neutral
#   tick_imbalance:             imbalance>0→bullish, <0→bearish

def _infer_direction(raw: dict) -> str:
    """从原始信号推断多空方向"""
    strategy = raw["strategy"]
    detail = raw.get("detail", {})

    if strategy == "volume_price_divergence":
        return "bearish"
    elif strategy == "capital_flow_spike":
        return "bullish" if detail.get("to_pct", 0) > detail.get("from_pct", 0) else "bearish"
    elif strategy == "order_book_imbalance":
        return "bullish" if detail.get("delta", 0) > 0 else "bearish"
    elif strategy == "large_order":
        d = detail.get("direction", "")
        if d == "BUY":
            return "bullish"
        elif d == "SELL":
            return "bearish"
        return "neutral"
    elif strategy == "tick_imbalance":
        return "bullish" if detail.get("imbalance", 0) > 0 else "bearish"
    return "neutral"


# Fallback weights (overridden by scoring.weights in l2_strategy_config.json)
SIGNAL_WEIGHTS = {
    "tick_imbalance": 3,
    "large_order": 2,
    "volume_price_divergence": 2,
    "capital_flow_spike": 1,
    "order_book_imbalance": 1,
}


class SignalScorer:
    """复合信号评分器 — 加权多策略共振研判

    同一只股票在 10 分钟窗口内，加权分 ≥ composite_threshold
    且 ≥ min_strategy_types 种不同策略类型同向触发 → 产出复合信号。

    权重:
      tick_imbalance=3, large_order=2, volume_price_divergence=2,
      capital_flow_spike=1, order_book_imbalance=1
    """

    WINDOW_SEC = 600   # 10 分钟滑动窗口
    COOLDOWN_SEC = 3600  # 复合信号冷却 1 小时/股

    def __init__(self, weights: Optional[dict[str, int]] = None,
                 composite_threshold: int = 5,
                 min_strategy_types: int = 2):
        self._weights = weights or SIGNAL_WEIGHTS
        self._composite_threshold = composite_threshold
        self._min_strategy_types = min_strategy_types
        # {code: [(timestamp, strategy, direction, weight)]}
        self._history: dict[str, list] = {}
        # {code: last_composite_timestamp}
        self._last_notify: dict[str, float] = {}

    def feed(self, raw_signals: list[dict]):
        """将本轮原始信号喂入历史缓冲"""
        now = time.time()
        for sig in raw_signals:
            code = sig["code"]
            strategy = sig["strategy"]
            direction = _infer_direction(sig)
            if direction == "neutral":
                continue
            weight = self._weights.get(strategy, 1)
            if code not in self._history:
                self._history[code] = []
            self._history[code].append((now, strategy, direction, weight))

    def evaluate(self) -> list[dict]:
        """评估所有股票，返回复合信号（加权分达标 + 策略类型数达标）"""
        now = time.time()
        cutoff = now - self.WINDOW_SEC
        composites = []

        for code, events in list(self._history.items()):
            # 清理窗口外数据
            events = [(t, s, d, w) for t, s, d, w in events if t >= cutoff]
            self._history[code] = events

            if not events:
                continue

            # 冷却检查
            if now - self._last_notify.get(code, 0) < self.COOLDOWN_SEC:
                continue

            # 按方向统计加权分 + 策略类型
            bearish_score = 0
            bullish_score = 0
            bearish_types: set[str] = set()
            bullish_types: set[str] = set()
            for _, strategy, direction, weight in events:
                if direction == "bearish":
                    bearish_score += weight
                    bearish_types.add(strategy)
                elif direction == "bullish":
                    bullish_score += weight
                    bullish_types.add(strategy)

            if (bearish_score >= self._composite_threshold
                    and len(bearish_types) >= self._min_strategy_types):
                composites.append({
                    "strategy": "composite_bearish",
                    "code": code,
                    "detail": {
                        "signals": sorted(bearish_types),
                        "count": len(bearish_types),
                        "score": bearish_score,
                    },
                })
                self._last_notify[code] = now

            elif (bullish_score >= self._composite_threshold
                    and len(bullish_types) >= self._min_strategy_types):
                composites.append({
                    "strategy": "composite_bullish",
                    "code": code,
                    "detail": {
                        "signals": sorted(bullish_types),
                        "count": len(bullish_types),
                        "score": bullish_score,
                    },
                })
                self._last_notify[code] = now

        return composites

    def reset(self):
        self._history.clear()
        self._last_notify.clear()


# ══════════════════════════════════════════
# Signal Formatter
# ══════════════════════════════════════════

# Strategy display names (Chinese)
_STRATEGY_NAMES = {
    "capital_flow_spike": "主力资金异动",
    "large_order": "大单成交",
    "order_book_imbalance": "盘口异动",
    "volume_price_divergence": "量价背离",
    "tick_imbalance": "主买主卖失衡",
    "composite_bearish": "空头信号",
    "composite_bullish": "多头信号",
}

# Stealth display (CI/monitoring style)
_STRATEGY_STEALTH = {
    "capital_flow_spike": "capital flow spike",
    "large_order": "large order detected",
    "order_book_imbalance": "order book shift",
    "volume_price_divergence": "divergence alert",
    "tick_imbalance": "tick imbalance",
    "composite_bearish": "bearish composite",
    "composite_bullish": "bullish composite",
}


def format_signal(raw: dict, name_map: dict[str, str], *, notify: bool = False) -> dict:
    """将原始信号转换为 dual-format event（message + display）

    Args:
        raw: tracker 输出的 {strategy, code, detail}
        name_map: {code: stock_name}
        notify: True = 弹通知（复合信号），False = 只写 web 日志

    Returns:
        完整的信号事件 dict
    """
    strategy = raw["strategy"]
    code = raw["code"]
    detail = raw.get("detail", {})
    stock_name = name_map.get(code, code)
    ts = int(time.time() * 1000)
    t = datetime.now().strftime("%H:%M:%S")

    # Build display (中文详细)
    cn_name = _STRATEGY_NAMES.get(strategy, strategy)
    if strategy == "capital_flow_spike":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"净流入占比 {detail.get('from_pct', 0):.1f}% → {detail.get('to_pct', 0):.1f}%"
        )
    elif strategy == "large_order":
        amt = detail.get("amount", 0)
        amt_wan = amt / 10000
        dir_label = {"BUY": "主买", "SELL": "主卖"}.get(detail.get("direction", ""), "")
        dir_suffix = f" ({dir_label})" if dir_label else ""
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"成交 {amt_wan:.0f}万 @ {detail.get('price', 0):.2f}{dir_suffix}"
        )
    elif strategy == "order_book_imbalance":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"委比 {detail.get('prev_ratio', 0):.0f} → {detail.get('curr_ratio', 0):.0f} "
            f"(变化 {detail.get('delta', 0):+.0f})"
        )
    elif strategy == "volume_price_divergence":
        inflow_wan = detail.get("main_net_inflow", 0) / 10000
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"价格 {detail.get('price', 0):.2f} (窗口最高) "
            f"主力净流入 {inflow_wan:.0f}万"
        )
    elif strategy == "tick_imbalance":
        imb = detail.get("imbalance", 0)
        turnover_wan = detail.get("turnover", 0) / 10000
        side = "主买" if imb > 0 else "主卖"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"{side}占优 imbalance={imb:+.3f} 窗口成交额{turnover_wan:.0f}万"
        )
    elif strategy in ("composite_bearish", "composite_bullish"):
        signals_cn = [_STRATEGY_NAMES.get(s, s) for s in detail.get("signals", [])]
        score = detail.get("score", 0)
        display = f"{code} {stock_name} {cn_name}(分={score}): {' + '.join(signals_cn)}"
    else:
        display = f"{code} {stock_name} {cn_name}"

    # Build message (stealth)
    stealth_label = _STRATEGY_STEALTH.get(strategy, strategy)
    message = f"{stock_name}: {stealth_label}"

    return {
        "ts": ts,
        "time": t,
        "strategy": strategy,
        "code": code,
        "kind": "l2_strategy",
        "notify": notify,
        "message": message,
        "display": display,
        "detail": detail,
    }


# ══════════════════════════════════════════
# L2 Strategy Engine
# ══════════════════════════════════════════

class L2StrategyEngine:
    """L2 策略引擎 — 管理 Futu 连接、订阅、5 个 Tracker

    用法:
        engine = L2StrategyEngine(config, watchlist)
        if engine.connect():
            signals = engine.poll_once()
    """

    def __init__(self, strategy_config: dict, watchlist: dict,
                 host: str = OPEND_HOST, port: int = OPEND_PORT):
        self._host = host
        self._port = port
        self._ctx = None
        self._last_fail_time: float = 0
        self._subscribed: bool = False

        self._config = strategy_config
        self._watchlist = watchlist
        self._strategies = strategy_config.get("strategies", {})

        # Name map for signal formatting
        self._name_map: dict[str, str] = {
            code: info.get("name", code)
            for code, info in watchlist.items()
        }

        # HK holding codes (only subscribe HK holdings)
        self._hk_holdings = [
            code for code, info in watchlist.items()
            if code.startswith("HK")
            and info.get("type") == "holding"
            and not info.get("hidden", False)
        ]

        # Initialize trackers
        self._init_trackers()

        # Cooldowns (for raw signal dedup, not for notifications)
        cooldowns = {}
        for name, cfg in self._strategies.items():
            cooldowns[name] = cfg.get("cooldown_minutes", 15)
        self._cooldown = CooldownManager(cooldowns)

        # Composite scorer (决定是否弹通知)
        scoring_cfg = strategy_config.get("scoring", {})
        self._scorer = SignalScorer(
            weights=scoring_cfg.get("weights"),
            composite_threshold=scoring_cfg.get("composite_threshold", 5),
            min_strategy_types=scoring_cfg.get("min_strategy_types", 2),
        )

    def _init_trackers(self):
        """Initialize tracker instances from config"""
        s = self._strategies

        cf_cfg = s.get("capital_flow_spike", {})
        self._capital_flow = CapitalFlowTracker(
            window_minutes=cf_cfg.get("window_minutes", 5),
            low_threshold_pct=cf_cfg.get("low_threshold_pct", 2.0),
            high_threshold_pct=cf_cfg.get("high_threshold_pct", 8.0),
        )

        lo_cfg = s.get("large_order", {})
        self._large_order = LargeOrderTracker(
            min_amount=lo_cfg.get("min_amount", 5_000_000),
        )

        ob_cfg = s.get("order_book_imbalance", {})
        self._order_book = OrderBookTracker(
            delta_threshold=ob_cfg.get("delta_threshold", 40.0),
        )

        dv_cfg = s.get("volume_price_divergence", {})
        self._divergence = DivergenceTracker(
            lookback_minutes=dv_cfg.get("lookback_minutes", 30),
        )

        ti_cfg = s.get("tick_imbalance", {})
        self._tick_imbalance = TickImbalanceTracker(
            window_minutes=ti_cfg.get("window_minutes", 5),
            imbalance_threshold=ti_cfg.get("imbalance_threshold", 0.4),
            min_turnover=ti_cfg.get("min_turnover", 10_000_000),
        )

    # ── Connection management ──

    def _is_port_open(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((self._host, self._port)) == 0
        sock.close()
        return result

    def connect(self) -> bool:
        """懒连接 + 冷却期控制"""
        if self._ctx is not None:
            return True

        if time.time() - self._last_fail_time < RECONNECT_COOLDOWN:
            return False

        if not self._is_port_open():
            self._last_fail_time = time.time()
            logger.debug("OpenD not running, L2 strategy engine skipped")
            return False

        try:
            from futu import OpenQuoteContext
            self._ctx = OpenQuoteContext(host=self._host, port=self._port)
            logger.info("L2 Strategy Engine connected to OpenD")
            return True
        except Exception as e:
            self._last_fail_time = time.time()
            logger.warning(f"L2 Strategy Engine connection failed: {e}")
            return False

    def _subscribe(self):
        """订阅 HK 持仓的 TICKER + ORDER_BOOK（每只 2 个订阅位）"""
        if self._subscribed or not self._ctx:
            return

        try:
            from futu import SubType, RET_OK

            codes = [to_futu_code(c) for c in self._hk_holdings]
            if not codes:
                self._subscribed = True
                return

            # Subscribe TICKER (逐笔成交 — large_order + tick_imbalance 共用)
            need_ticker = (
                self._strategies.get("large_order", {}).get("enabled", True)
                or self._strategies.get("tick_imbalance", {}).get("enabled", True)
            )
            if need_ticker:
                ret, msg = self._ctx.subscribe(codes, [SubType.TICKER])
                if ret == RET_OK:
                    logger.info(f"Subscribed TICKER for {len(codes)} HK stocks")
                else:
                    logger.warning(f"TICKER subscription failed: {msg}")

            # Subscribe ORDER_BOOK (盘口)
            if self._strategies.get("order_book_imbalance", {}).get("enabled", True):
                ret, msg = self._ctx.subscribe(codes, [SubType.ORDER_BOOK])
                if ret == RET_OK:
                    logger.info(f"Subscribed ORDER_BOOK for {len(codes)} HK stocks")
                else:
                    logger.warning(f"ORDER_BOOK subscription failed: {msg}")

            self._subscribed = True
        except Exception as e:
            logger.warning(f"Subscription failed: {e}")

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
            self._subscribed = False

    # ── Data fetching ──

    def _fetch_capital_flow(self) -> dict[str, dict]:
        """获取 HK 持仓的资金流向（不消耗订阅位）"""
        from futu import RET_OK, PeriodType

        result = {}
        for code in self._hk_holdings:
            futu_code = to_futu_code(code)
            try:
                ret, data = self._ctx.get_capital_flow(
                    futu_code, period_type=PeriodType.INTRADAY
                )
                if ret != RET_OK or data.empty:
                    continue

                latest = data.iloc[-1]
                super_in = float(latest.get("super_in_flow", 0) or 0)
                big_in = float(latest.get("big_in_flow", 0) or 0)
                sml_in = float(latest.get("sml_in_flow", 0) or 0)
                amount = float(latest.get("in_flow", 0) or 0) + float(latest.get("out_flow", 0) or 0)

                main_inflow = super_in + big_in
                # 成交额太小时占比无意义（开盘初期噪音）
                MIN_AMOUNT_FOR_PCT = 5_000_000  # 500万
                if abs(amount) >= MIN_AMOUNT_FOR_PCT:
                    inflow_pct = (main_inflow / amount * 100)
                else:
                    inflow_pct = 0

                result[code] = {
                    "mainNetInflow": round(main_inflow, 2),
                    "mainNetInflowPct": round(inflow_pct, 2),
                    "retailNetInflow": round(sml_in, 2),
                }
            except Exception as e:
                logger.debug(f"capital_flow({code}) error: {e}")

        return result

    def _fetch_snapshots(self) -> dict[str, dict]:
        """获取 HK 持仓的快照（bidAskRatio + price）"""
        from futu import RET_OK

        result = {}
        codes = [to_futu_code(c) for c in self._hk_holdings]
        if not codes:
            return result

        try:
            ret, data = self._ctx.get_market_snapshot(codes)
            if ret != RET_OK:
                return result

            for _, row in data.iterrows():
                code = from_futu_code(row["code"])
                entry = {}
                bid_ask = row.get("bid_ask_ratio")
                if bid_ask is not None and bid_ask != 0:
                    entry["bidAskRatio"] = round(float(bid_ask), 2)
                price = row.get("last_price")
                if price is not None and price > 0:
                    entry["price"] = float(price)
                high = row.get("high_price")
                if high is not None and high > 0:
                    entry["highPrice"] = float(high)
                if entry:
                    result[code] = entry
        except Exception as e:
            logger.debug(f"snapshot fetch error: {e}")

        return result

    def _is_continuous_trading(self) -> bool:
        """判断是否在连续交易时段（排除竞价）

        HK 连续交易: 09:30-12:00, 13:00-16:00
        A-share 连续交易: 09:30-11:30, 13:00-15:00
        竞价时段的聚合撮合成交不算大单。
        """
        now = datetime.now()
        t = now.hour * 100 + now.minute
        # HK continuous
        if (930 <= t <= 1200) or (1300 <= t <= 1600):
            return True
        return False

    def _fetch_rt_tickers(self) -> dict[str, list[dict]]:
        """获取已订阅股票的逐笔成交数据

        仅在连续交易时段获取，竞价时段跳过（竞价撮合是聚合成交，不是真正大单）。
        """
        if not self._is_continuous_trading():
            return {}

        from futu import RET_OK

        result = {}
        for code in self._hk_holdings:
            futu_code = to_futu_code(code)
            try:
                ret, data = self._ctx.get_rt_ticker(futu_code, num=50)
                if ret != RET_OK or data.empty:
                    continue

                ticks = []
                for _, row in data.iterrows():
                    ticks.append({
                        "sequence": int(row.get("sequence", 0)),
                        "turnover": float(row.get("turnover", 0)),
                        "price": float(row.get("price", 0)),
                        "volume": int(row.get("volume", 0)),
                        "direction": str(row.get("ticker_direction", "")),
                    })
                if ticks:
                    result[code] = ticks
            except Exception as e:
                logger.debug(f"rt_ticker({code}) error: {e}")

        return result

    # ── Main detection loop ──

    def poll_once(self) -> list[dict]:
        """执行一轮检测，返回格式化后的信号列表

        Returns:
            [{"ts", "time", "strategy", "code", "kind", "message", "display", "detail"}, ...]
        """
        if not self.connect():
            return []

        self._subscribe()

        raw_signals: list[dict] = []

        try:
            # Fetch shared data once (strategies 1,3,4 share capital_flow / snapshot)
            need_capital = (
                self._strategies.get("capital_flow_spike", {}).get("enabled", True)
                or self._strategies.get("volume_price_divergence", {}).get("enabled", True)
            )
            need_snapshot = (
                self._strategies.get("order_book_imbalance", {}).get("enabled", True)
                or self._strategies.get("volume_price_divergence", {}).get("enabled", True)
            )

            capital_data = self._fetch_capital_flow() if need_capital else {}
            snapshot_data = self._fetch_snapshots() if need_snapshot else {}

            # ── 1. Capital flow spike ──
            if self._strategies.get("capital_flow_spike", {}).get("enabled", True):
                for code, data in capital_data.items():
                    pct = data.get("mainNetInflowPct", 0)
                    sig = self._capital_flow.update(code, pct)
                    if sig:
                        raw_signals.append(sig)

            # Fetch ticker data (shared by large_order + tick_imbalance)
            need_tickers = (
                self._strategies.get("large_order", {}).get("enabled", True)
                or self._strategies.get("tick_imbalance", {}).get("enabled", True)
            )
            ticker_data = self._fetch_rt_tickers() if need_tickers else {}

            # ── 2. Large order ──
            if self._strategies.get("large_order", {}).get("enabled", True):
                for code, ticks in ticker_data.items():
                    sigs = self._large_order.check_tickers(code, ticks)
                    raw_signals.extend(sigs)

            # ── 3. Order book imbalance ──
            if self._strategies.get("order_book_imbalance", {}).get("enabled", True):
                for code, data in snapshot_data.items():
                    ratio = data.get("bidAskRatio")
                    if ratio is not None:
                        sig = self._order_book.update(code, ratio)
                        if sig:
                            raw_signals.append(sig)

            # ── 4. Volume-price divergence ──
            if self._strategies.get("volume_price_divergence", {}).get("enabled", True):
                for code in self._hk_holdings:
                    snap = snapshot_data.get(code, {})
                    cap = capital_data.get(code, {})
                    price = snap.get("price", 0)
                    inflow = cap.get("mainNetInflow", 0)
                    if price > 0:
                        sig = self._divergence.update(code, price, inflow)
                        if sig:
                            raw_signals.append(sig)

            # ── 5. Tick imbalance ──
            if self._strategies.get("tick_imbalance", {}).get("enabled", True):
                for code, ticks in ticker_data.items():
                    sig = self._tick_imbalance.update(code, ticks)
                    if sig:
                        raw_signals.append(sig)

        except Exception as e:
            logger.warning(f"L2 poll error: {e}")
            self.close()
            self._last_fail_time = time.time()
            return []

        # Apply cooldowns and format raw signals (notify=false, web only)
        signals = []
        accepted_raw = []
        for raw in raw_signals:
            strategy = raw["strategy"]
            code = raw["code"]
            if self._cooldown.can_trigger(strategy, code):
                self._cooldown.record(strategy, code)
                signals.append(format_signal(raw, self._name_map, notify=False))
                accepted_raw.append(raw)

        # Feed raw signals to scorer and evaluate composite verdicts
        if accepted_raw:
            self._scorer.feed(accepted_raw)
        composites = self._scorer.evaluate()
        for comp in composites:
            signals.append(format_signal(comp, self._name_map, notify=True))

        return signals

    def reset_daily(self):
        """每日重置 — 清除所有追踪状态"""
        self._capital_flow.reset()
        self._large_order.reset()
        self._order_book.reset()
        self._divergence.reset()
        self._tick_imbalance.reset()
        self._cooldown.reset()
        self._scorer.reset()
        logger.info("L2 strategy engine daily reset complete")
