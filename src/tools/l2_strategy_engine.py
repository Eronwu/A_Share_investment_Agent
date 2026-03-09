"""
L2 Strategy Engine — 基于 Futu OpenD 实时数据的策略信号检测

5 个短窗口检测策略:
  1. capital_flow_spike      — 主力资金异动（5分钟窗口，净流入占比跳升）
  2. large_order             — 逐笔大单（单笔成交 >500万，含买卖方向）
  3. order_book_imbalance    — 盘口异动（委比短时剧烈变化）
  4. volume_price_divergence — 量价背离（价格新高 + 主力资金净流出）
  5. tick_imbalance          — 主买主卖失衡（5分钟窗口逐笔方向累积）

5 个 session 级别信号:
  6. momentum_alert          — 动量确认（持续大单买入 + 价格上涨 + 资金流入 + 无背离）
  7. volume_accel_alert      — 放量加速（成交额阶梯翻倍 + tick持续偏买，捕捉算法拆单拉升）
  8. tick_persistence        — 主买/主卖持续（session 内 tick 方向偏压 ≥80%）
  9. institutional_retail_divergence — 散户机构分歧（主力进散户出/反向）
 10. large_order_reversal    — 大单翻转（prior BUY → recent SELL 或反向）
 11. closing_surge           — 尾盘异动（15:30+ 信号密度 ≥2.5x 盘中均值）

Bearish mirrors: momentum_sell_alert, volume_accel_sell_alert

复合评分（加权，每策略类型最多贡献 1 次权重）:
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

import numpy as np
import pandas as pd

from src.utils.futu_codes import to_futu_code, from_futu_code
from src.utils.logging_config import setup_logger

logger = setup_logger("l2_strategy")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
RECONNECT_COOLDOWN = 60


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

        # 当前价 = 窗口最高 AND 主力净流出显著（> 500 万，过滤小盘股噪音）
        if price >= max_price and main_net_inflow < -5_000_000:
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

    def get_current_stats(self, code: str) -> Optional[dict]:
        """Return current window turnover and imbalance without threshold check.

        Used by VolumeAccelTracker to sample tick stats at ~2min intervals.
        Reuses the same _history data maintained by update().
        """
        now = time.time()
        q = self._history.get(code)
        if not q:
            return None

        # Trim window (same as update)
        cutoff = now - self.window_sec
        while q and q[0][0] < cutoff:
            q.popleft()

        if not q:
            return None

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
        if total_vol == 0:
            return None

        imbalance = (buy_vol - sell_vol) / total_vol
        return {
            "turnover": round(total_turnover, 0),
            "imbalance": round(imbalance, 3),
        }

    def reset(self):
        self._history.clear()
        self._last_seq.clear()
        self._warmed_up.clear()


# ══════════════════════════════════════════
# Volume Acceleration Tracker — 放量加速检测
# ══════════════════════════════════════════

class VolumeAccelTracker:
    """策略7: 放量加速 — 成交额阶梯式翻倍 + tick 持续偏买

    每 5 分钟采样 TickImbalanceTracker 的窗口统计 (turnover, imbalance),
    采样间隔 = 窗口长度，消除重叠，确保每次采样是独立的 5 分钟区间。
    检测:
      1. 当前窗口成交额 >= 更早 N 窗口均值 × accel_ratio (加速)
      2. 最近 consecutive_min 个采样 imbalance 全 > imbalance_min (持续偏买)
      3. 当前窗口成交额 >= min_turnover (绝对量过滤)

    不依赖大单检测，专门捕捉算法拆单型机构拉升。
    """

    SAMPLE_INTERVAL_SEC = 300  # 5 分钟采样间隔 = TickImbalanceTracker 窗口长度

    def __init__(self, accel_ratio: float = 1.8,
                 imbalance_min: float = 0.35,
                 consecutive_min: int = 3,
                 min_turnover: float = 10_000_000,
                 lookback: int = 10):
        self.accel_ratio = accel_ratio
        self.imbalance_min = imbalance_min
        self.consecutive_min = consecutive_min
        self.min_turnover = min_turnover
        self.lookback = lookback
        # {code: deque of (timestamp, turnover, imbalance)}
        self._samples: dict[str, deque] = {}
        # {code: last_sample_time}
        self._last_sample_time: dict[str, float] = {}

    def observe(self, code: str, turnover: float, imbalance: float):
        """Record a sample (called every poll, throttled to ~2min intervals)"""
        now = time.time()
        last = self._last_sample_time.get(code, 0)
        if now - last < self.SAMPLE_INTERVAL_SEC:
            return

        self._last_sample_time[code] = now
        if code not in self._samples:
            self._samples[code] = deque(maxlen=self.lookback)
        self._samples[code].append((now, turnover, imbalance))

    def evaluate(self, code: str) -> Optional[dict]:
        """Check conditions 1-3 (acceleration + sustained imbalance + min turnover)

        Returns:
            dict with accel_ratio, turnover, imbalance details if triggered, else None
        """
        samples = self._samples.get(code)
        # Need at least consecutive_min (for recent) + 1 (for prior baseline)
        if not samples or len(samples) < self.consecutive_min + 1:
            return None

        # Current window stats (latest sample)
        _, curr_turnover, curr_imbalance = samples[-1]

        # Condition 3: minimum turnover
        if curr_turnover < self.min_turnover:
            return None

        # Condition 2: sustained imbalance (last N samples all > threshold)
        recent = list(samples)[-self.consecutive_min:]
        if not all(imb > self.imbalance_min for _, _, imb in recent):
            return None

        # Condition 1: acceleration vs earlier samples mean (exclude recent window)
        prior = list(samples)[:-self.consecutive_min]
        if not prior:
            return None
        avg_turnover = sum(t for _, t, _ in prior) / len(prior)
        if avg_turnover <= 0:
            return None

        ratio = curr_turnover / avg_turnover
        if ratio < self.accel_ratio:
            return None

        return {
            "accel_ratio": round(ratio, 2),
            "curr_turnover": round(curr_turnover, 0),
            "avg_prior_turnover": round(avg_turnover, 0),
            "curr_imbalance": round(curr_imbalance, 3),
            "consecutive_above": self.consecutive_min,
        }

    def evaluate_bearish(self, code: str) -> Optional[dict]:
        """Check bearish acceleration: turnover accelerating + sustained selling.

        Same as evaluate() but checks imbalance < -imbalance_min (sustained selling).
        """
        samples = self._samples.get(code)
        if not samples or len(samples) < self.consecutive_min + 1:
            return None

        _, curr_turnover, curr_imbalance = samples[-1]

        if curr_turnover < self.min_turnover:
            return None

        # Sustained SELLING: all recent imbalance < -threshold
        recent = list(samples)[-self.consecutive_min:]
        if not all(imb < -self.imbalance_min for _, _, imb in recent):
            return None

        prior = list(samples)[:-self.consecutive_min]
        if not prior:
            return None
        avg_turnover = sum(t for _, t, _ in prior) / len(prior)
        if avg_turnover <= 0:
            return None

        ratio = curr_turnover / avg_turnover
        if ratio < self.accel_ratio:
            return None

        return {
            "accel_ratio": round(ratio, 2),
            "curr_turnover": round(curr_turnover, 0),
            "avg_prior_turnover": round(avg_turnover, 0),
            "curr_imbalance": round(curr_imbalance, 3),
            "consecutive_above": self.consecutive_min,
        }

    def reset(self):
        self._samples.clear()
        self._last_sample_time.clear()


# ══════════════════════════════════════════
# Tick Persistence Tracker — 主买/主卖持续
# ══════════════════════════════════════════

class TickPersistenceTracker:
    """策略8: Tick方向一致性异常 — 检测 session 内 tick 方向持续偏向一侧

    当某只股票在 session 内累积的 tick_imbalance 信号中，同方向占比
    >= persistence_ratio，说明有持续性的方向偏压（通常是机构算法
    在持续执行买入/卖出）。

    捕捉场景: 低涨幅静默吸筹（腾讯: session score=5, 净买入2.18亿, 但日跌-0.7%）
    """

    def __init__(self, min_tick_signals: int = 8,
                 persistence_ratio: float = 0.80,
                 recent_consistent: int = 3):
        self.min_tick_signals = min_tick_signals
        self.persistence_ratio = persistence_ratio
        self.recent_consistent = recent_consistent
        # {code: [(direction, imbalance, timestamp), ...]}
        self._tick_history: dict[str, list] = {}

    def feed(self, code: str, imbalance: float):
        """每次 tick_imbalance 信号触发时调用（冷却后）"""
        direction = "bullish" if imbalance > 0 else "bearish"
        if code not in self._tick_history:
            self._tick_history[code] = []
        self._tick_history[code].append((direction, imbalance, time.time()))

    def evaluate(self, code: str) -> Optional[dict]:
        """评估是否满足持续性条件"""
        history = self._tick_history.get(code, [])
        if len(history) < self.min_tick_signals:
            return None

        bullish_count = sum(1 for d, _, _ in history if d == "bullish")
        bearish_count = len(history) - bullish_count

        dominant = "bullish" if bullish_count >= bearish_count else "bearish"
        dominant_count = max(bullish_count, bearish_count)
        ratio = dominant_count / len(history)

        if ratio < self.persistence_ratio:
            return None

        # 最近 N 个信号须与主方向一致（排除尾部反转）
        recent = history[-self.recent_consistent:]
        if not all(d == dominant for d, _, _ in recent):
            return None

        avg_imb = sum(abs(v) for _, v, _ in history) / len(history)

        return {
            "strategy": "tick_persistence",
            "code": code,
            "detail": {
                "total_tick_signals": len(history),
                "dominant_direction": dominant,
                "dominant_count": dominant_count,
                "persistence_ratio": round(ratio, 2),
                "recent_consistent": self.recent_consistent,
                "avg_imbalance": round(avg_imb, 3),
            },
        }

    def reset(self):
        self._tick_history.clear()


# ══════════════════════════════════════════
# Institutional Retail Divergence Tracker
# ══════════════════════════════════════════

class InstitutionalRetailTracker:
    """策略: 散户机构分歧 — 主力进散户出 / 主力出散户进

    检测逻辑:
      Bullish: institutional(super+big) > institutional_min AND retail(sml) < -retail_min
      Bearish: institutional < -institutional_min AND retail > retail_min
    """

    def __init__(self, institutional_min: float = 10_000_000,
                 retail_min: float = 5_000_000):
        self.institutional_min = institutional_min
        self.retail_min = retail_min

    def evaluate(self, code: str, capital_data: dict) -> Optional[dict]:
        """评估单只股票的散户机构分歧"""
        data = capital_data.get(code)
        if not data:
            return None

        institutional = data.get("superNetInflow", 0) + data.get("bigNetInflow", 0)
        retail = data.get("retailNetInflow", 0)

        if institutional > self.institutional_min and retail < -self.retail_min:
            return {
                "strategy": "institutional_retail_divergence",
                "code": code,
                "detail": {
                    "direction": "bullish",
                    "institutional_net": round(institutional, 0),
                    "retail_net": round(retail, 0),
                    "super_net": round(data.get("superNetInflow", 0), 0),
                    "big_net": round(data.get("bigNetInflow", 0), 0),
                },
            }
        elif institutional < -self.institutional_min and retail > self.retail_min:
            return {
                "strategy": "institutional_retail_divergence",
                "code": code,
                "detail": {
                    "direction": "bearish",
                    "institutional_net": round(institutional, 0),
                    "retail_net": round(retail, 0),
                    "super_net": round(data.get("superNetInflow", 0), 0),
                    "big_net": round(data.get("bigNetInflow", 0), 0),
                },
            }
        return None


# ══════════════════════════════════════════
# Session Accumulator — 开盘至今多空研判
# ══════════════════════════════════════════

class SessionAccumulator:
    """全天累积统计 — 从开盘到当前的多空方向

    不触发信号，只维护 per-stock 的全天累积统计。
    每次 poll_once() 更新，结果写入 session 字段。

    三个累积维度:
      1. Tick 方向: 全天 buy_vol / sell_vol → imbalance
      2. 大单方向: 全天大单 buy/sell count + amount → net_amount
      3. 资金流向: Futu INTRADAY 当日累积快照 → mainNetInflow
    """

    # Direction thresholds
    TICK_IMBALANCE_THRESHOLD = 0.1
    # Minimum data guards — 数据不足时 score=0，避免开盘初期噪音
    MIN_TICK_VOL = 50_000          # 全天累积最低成交量（股）才给 tick 方向分
    MIN_LARGE_ORDER_COUNT = 3      # 全天至少 3 笔大单才给大单方向分
    MIN_LARGE_ORDER_NET = 10_000_000  # 大单净额绝对值 > 1000万 才给方向分
    MIN_CAPITAL_FLOW_PCT = 1.0     # 资金流净流入占比 > 1% 才给方向分
    # Weights for composite score
    TICK_WEIGHT = 2
    LARGE_ORDER_WEIGHT = 2
    CAPITAL_FLOW_WEIGHT = 1

    def __init__(self):
        # tick 方向累积: {code: {buy_vol, sell_vol}}
        self._tick_totals: dict[str, dict] = {}
        # 大单方向累积: {code: {buy_count, sell_count, buy_amount, sell_amount}}
        self._large_order_totals: dict[str, dict] = {}
        # 资金流（每次取最新快照，Futu INTRADAY 已是当日累积）
        self._capital_flow: dict[str, dict] = {}
        # tick sequence 水位（去重）
        self._last_seq: dict[str, int] = {}
        self._warmed_up: set[str] = set()

    def feed_ticks(self, code: str, tickers: list[dict]):
        """累积全天 tick 方向"""
        if not tickers:
            return

        # Warmup: first call records seq watermark only (skip stale ticks)
        if code not in self._warmed_up:
            max_seq = max(t.get("sequence", 0) for t in tickers)
            self._last_seq[code] = max_seq
            self._warmed_up.add(code)
            return

        if code not in self._tick_totals:
            self._tick_totals[code] = {"buy_vol": 0, "sell_vol": 0}

        last_seq = self._last_seq.get(code, -1)
        totals = self._tick_totals[code]

        for tick in tickers:
            seq = tick.get("sequence", 0)
            if seq <= last_seq:
                continue
            self._last_seq[code] = seq

            vol = int(tick.get("volume", 0))
            raw_dir = str(tick.get("direction", "")).upper()
            if "BUY" in raw_dir:
                totals["buy_vol"] += vol
            elif "SELL" in raw_dir:
                totals["sell_vol"] += vol

    def feed_large_order(self, code: str, detail: dict):
        """累积大单买卖统计（由 poll_once 在 LargeOrderTracker 触发后调用）"""
        if code not in self._large_order_totals:
            self._large_order_totals[code] = {
                "buy_count": 0, "sell_count": 0,
                "buy_amount": 0.0, "sell_amount": 0.0,
                "orders": [],
            }

        totals = self._large_order_totals[code]
        direction = detail.get("direction", "")
        amount = float(detail.get("amount", 0))

        # Track order time series for reversal detection
        totals["orders"].append((time.time(), direction, amount))

        if direction == "BUY":
            totals["buy_count"] += 1
            totals["buy_amount"] += amount
        elif direction == "SELL":
            totals["sell_count"] += 1
            totals["sell_amount"] += amount

    def update_capital_flow(self, code: str, capital_data: dict):
        """更新当日资金流快照（Futu INTRADAY 已是当日累积值）"""
        self._capital_flow[code] = {
            "main_net_inflow": capital_data.get("mainNetInflow", 0),
            "main_net_inflow_pct": capital_data.get("mainNetInflowPct", 0),
        }

    def snapshot(self) -> dict:
        """返回所有股票的 session summary"""
        all_codes = set(self._tick_totals) | set(self._large_order_totals) | set(self._capital_flow)
        result = {}

        for code in all_codes:
            # ── Tick direction ──
            tick = self._tick_totals.get(code, {"buy_vol": 0, "sell_vol": 0})
            buy_vol = tick["buy_vol"]
            sell_vol = tick["sell_vol"]
            total_vol = buy_vol + sell_vol
            tick_imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0

            # Guard: 成交量不足时不给方向分（开盘初期几笔 tick 无统计意义）
            if total_vol < self.MIN_TICK_VOL:
                tick_score = 0
            elif tick_imbalance > self.TICK_IMBALANCE_THRESHOLD:
                tick_score = 1
            elif tick_imbalance < -self.TICK_IMBALANCE_THRESHOLD:
                tick_score = -1
            else:
                tick_score = 0

            # ── Large order direction ──
            lo = self._large_order_totals.get(code, {
                "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0,
            })
            lo_net = lo["buy_amount"] - lo["sell_amount"]
            lo_count = lo["buy_count"] + lo["sell_count"]

            # Guard: 大单笔数不足 OR 净额不显著时不给方向分
            if lo_count < self.MIN_LARGE_ORDER_COUNT or abs(lo_net) < self.MIN_LARGE_ORDER_NET:
                lo_score = 0
            elif lo_net > 0:
                lo_score = 1
            else:
                lo_score = -1

            # ── Capital flow direction ──
            # Use absolute inflow value for direction (pct can contradict abs value)
            cf = self._capital_flow.get(code, {"main_net_inflow": 0, "main_net_inflow_pct": 0})
            cf_inflow = cf["main_net_inflow"]

            # Guard: 绝对值 < 500万 时不给方向分（过滤噪音）
            MIN_INFLOW_ABS = 5_000_000
            if abs(cf_inflow) < MIN_INFLOW_ABS:
                cf_score = 0
            elif cf_inflow > 0:
                cf_score = 1
            else:
                cf_score = -1

            # ── Composite ──
            total_score = (
                tick_score * self.TICK_WEIGHT
                + lo_score * self.LARGE_ORDER_WEIGHT
                + cf_score * self.CAPITAL_FLOW_WEIGHT
            )

            if total_score > 0:
                direction = "bullish"
            elif total_score < 0:
                direction = "bearish"
            else:
                direction = "neutral"

            result[code] = {
                "direction": direction,
                "score": total_score,
                "tick": {
                    "buy_vol": buy_vol,
                    "sell_vol": sell_vol,
                    "imbalance": round(tick_imbalance, 3),
                    "direction_score": tick_score,
                },
                "large_order": {
                    "buy_count": lo["buy_count"],
                    "sell_count": lo["sell_count"],
                    "buy_amount": lo["buy_amount"],
                    "sell_amount": lo["sell_amount"],
                    "net_amount": lo_net,
                    "direction_score": lo_score,
                    "orders": lo.get("orders", []),
                },
                "capital_flow": {
                    "main_net_inflow": cf["main_net_inflow"],
                    "main_net_inflow_pct": cf["main_net_inflow_pct"],
                    "direction_score": cf_score,
                },
            }

        return result

    def reset(self):
        """每日重置"""
        self._tick_totals.clear()
        self._large_order_totals.clear()
        self._capital_flow.clear()
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
    elif strategy in ("institutional_retail_divergence", "large_order_reversal", "closing_surge"):
        return detail.get("direction", "neutral")
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

            # 按方向统计加权分 + 策略类型（每策略类型最多贡献 1 次权重）
            bearish_by_type: dict[str, int] = {}
            bullish_by_type: dict[str, int] = {}
            bearish_types: set[str] = set()
            bullish_types: set[str] = set()
            for _, strategy, direction, weight in events:
                if direction == "bearish":
                    bearish_by_type[strategy] = max(bearish_by_type.get(strategy, 0), weight)
                    bearish_types.add(strategy)
                elif direction == "bullish":
                    bullish_by_type[strategy] = max(bullish_by_type.get(strategy, 0), weight)
                    bullish_types.add(strategy)
            bearish_score = sum(bearish_by_type.values())
            bullish_score = sum(bullish_by_type.values())

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
    "momentum_alert": "动量确认",
    "volume_accel_alert": "放量加速",
    "momentum_sell_alert": "动量卖出",
    "volume_accel_sell_alert": "放量砸盘",
    "tick_persistence": "主买持续",
    "institutional_retail_divergence": "散户机构分歧",
    "large_order_reversal": "大单翻转",
    "closing_surge": "尾盘异动",
    # Daily indicator strategies
    "rsi_overbought": "RSI超买",
    "rsi_oversold": "RSI超卖",
    "rsi_extreme_overbought": "RSI严重超买",
    "rsi_extreme_oversold": "RSI严重超卖",
    "macd_golden_cross": "MACD金叉",
    "macd_death_cross": "MACD死叉",
    "macd_top_divergence": "MACD顶背离",
    "macd_bottom_divergence": "MACD底背离",
    "ma_bullish_align": "均线多头排列",
    "ma_bearish_align": "均线空头排列",
    "bollinger_squeeze_breakout": "布林带突破",
    "adx_trend_start": "趋势启动",
    "volume_breakout": "放量突破",
    "volume_divergence_top": "缩量创高",
    "engulfing_pattern": "吞没形态",
    "morning_evening_star": "晨暮星",
    "breakout_pullback": "突破回踩",
    "support_breakdown": "破位下跌",
    "relative_strength": "相对强弱",
}

# Daily strategies that trigger macOS notifications (L1/L2)
# 6 original high-priority + 7 mid-long-term trend signals
DAILY_NOTIFY_STRATEGIES = {
    # High priority
    "macd_top_divergence", "macd_bottom_divergence",
    "bollinger_squeeze_breakout",
    "breakout_pullback",
    "rsi_extreme_overbought", "rsi_extreme_oversold",
    # Mid-long-term trend signals (upgraded from L3)
    "ma_bullish_align", "ma_bearish_align",
    "adx_trend_start",
    "volume_breakout",
    "support_breakdown",
    "macd_golden_cross", "macd_death_cross",
}



def format_signal(raw: dict, name_map: dict[str, str], *, notify: bool = False,
                   snapshot_data: dict | None = None,
                   capital_data: dict | None = None) -> dict:
    """将原始信号转换为 dual-format event（message + display）

    Args:
        raw: tracker 输出的 {strategy, code, detail}
        name_map: {code: stock_name}
        notify: True = 弹通知（复合信号），False = 只写 web 日志
        snapshot_data: {code: {price, prevClose, ...}} for price context (web display only)
        capital_data: {code: {mainNetInflow, ...}} for capital context (web display only)

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
    elif strategy == "volume_accel_alert":
        change_pct = detail.get("daily_change_pct", 0)
        accel = detail.get("accel_ratio", 0)
        turnover_wan = detail.get("curr_turnover", 0) / 10000
        imb = detail.get("curr_imbalance", 0)
        consec = detail.get("consecutive_above", 0)
        inflow_label = "资金流入" if detail.get("capital_inflow", False) else "资金流出"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"日涨{change_pct:.1f}% | 成交额加速{accel:.1f}x({turnover_wan:.0f}万) | "
            f"tick偏买(imb={imb:+.2f}, 连续{consec}窗口) | {inflow_label}"
        )
    elif strategy == "momentum_alert":
        change_pct = detail.get("daily_change_pct", 0)
        net_amount_wan = detail.get("large_order_net_amount", 0) / 10000
        buy_count = detail.get("large_order_buy_count", 0)
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"日涨{change_pct:.1f}% | 大单净买入{net_amount_wan:.0f}万({buy_count}笔) | "
            f"资金流入 | 无背离"
        )
    elif strategy == "momentum_sell_alert":
        change_pct = detail.get("daily_change_pct", 0)
        net_amount_wan = abs(detail.get("large_order_net_amount", 0)) / 10000
        sell_count = detail.get("large_order_sell_count", 0)
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"日跌{change_pct:.1f}% | 大单净卖出{net_amount_wan:.0f}万({sell_count}笔) | "
            f"资金流出"
        )
    elif strategy == "volume_accel_sell_alert":
        change_pct = detail.get("daily_change_pct", 0)
        accel = detail.get("accel_ratio", 0)
        turnover_wan = detail.get("curr_turnover", 0) / 10000
        imb = detail.get("curr_imbalance", 0)
        consec = detail.get("consecutive_above", 0)
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"日跌{change_pct:.1f}% | 成交额加速{accel:.1f}x({turnover_wan:.0f}万) | "
            f"tick偏卖(imb={imb:+.2f}, 连续{consec}窗口) | 资金流出"
        )
    elif strategy == "tick_persistence":
        total = detail.get("total_tick_signals", 0)
        dominant = detail.get("dominant_direction", "")
        dominant_count = detail.get("dominant_count", 0)
        ratio_pct = detail.get("persistence_ratio", 0) * 100
        avg_imb = detail.get("avg_imbalance", 0)
        dir_label = "偏买" if dominant == "bullish" else "偏卖"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"tick{dir_label}率{ratio_pct:.0f}%({dominant_count}/{total}窗口) | "
            f"平均imb={avg_imb:.3f}"
        )
    elif strategy == "institutional_retail_divergence":
        dir_label = "主力进散户出" if detail.get("direction") == "bullish" else "主力出散户进"
        inst_wan = detail.get("institutional_net", 0) / 10000
        retail_wan = detail.get("retail_net", 0) / 10000
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"{dir_label} | 机构净{inst_wan:+.0f}万 散户净{retail_wan:+.0f}万"
        )
    elif strategy == "large_order_reversal":
        dir_label = "翻多" if detail.get("direction") == "bullish" else "翻空"
        prior_label = detail.get("prior_direction", "")
        recent_label = detail.get("recent_direction", "")
        net_wan = detail.get("recent_net_amount", 0) / 10000
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"大单{dir_label}({prior_label}→{recent_label}) | 近期净额{net_wan:+.0f}万"
        )
    elif strategy == "closing_surge":
        dir_label = "偏多" if detail.get("direction") == "bullish" else "偏空"
        closing_count = detail.get("closing_count", 0)
        ratio = detail.get("density_ratio", 0)
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"尾盘信号{closing_count}条(密度{ratio:.1f}x) {dir_label}"
        )
    elif strategy in ("composite_bearish", "composite_bullish"):
        signals_cn = [_STRATEGY_NAMES.get(s, s) for s in detail.get("signals", [])]
        score = detail.get("score", 0)
        display = f"{code} {stock_name} {cn_name}(分={score}): {' + '.join(signals_cn)}"
    # ── Daily indicator strategies ──
    elif strategy == "rsi_overbought":
        display = f"{code} {stock_name} {cn_name}: RSI={detail.get('rsi', 0):.1f} (前日{detail.get('prev_rsi', 0):.1f}) 突破{detail.get('threshold', 70)}"
    elif strategy == "rsi_oversold":
        display = f"{code} {stock_name} {cn_name}: RSI={detail.get('rsi', 0):.1f} (前日{detail.get('prev_rsi', 0):.1f}) 跌破{detail.get('threshold', 30)}"
    elif strategy == "rsi_extreme_overbought":
        days = detail.get("consecutive_days", 1)
        day_label = f"连续{days}日" if days >= 2 else ""
        display = f"{code} {stock_name} {cn_name}: {day_label}RSI={detail.get('rsi', 0):.1f} >{detail.get('threshold', 90)}"
    elif strategy == "rsi_extreme_oversold":
        days = detail.get("consecutive_days", 1)
        day_label = f"连续{days}日" if days >= 2 else ""
        display = f"{code} {stock_name} {cn_name}: {day_label}RSI={detail.get('rsi', 0):.1f} <{detail.get('threshold', 20)}"
    elif strategy == "macd_golden_cross":
        display = f"{code} {stock_name} {cn_name}: DIF={detail.get('dif', 0):.4f} DEA={detail.get('dea', 0):.4f}"
    elif strategy == "macd_death_cross":
        display = f"{code} {stock_name} {cn_name}: DIF={detail.get('dif', 0):.4f} DEA={detail.get('dea', 0):.4f}"
    elif strategy == "macd_top_divergence":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"价格{detail.get('price', 0):.2f}>{detail.get('prior_high', 0):.2f} "
            f"但DIF{detail.get('dif', 0):.4f}<{detail.get('prior_dif', 0):.4f}"
        )
    elif strategy == "macd_bottom_divergence":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"价格{detail.get('price', 0):.2f}<{detail.get('prior_low', 0):.2f} "
            f"但DIF{detail.get('dif', 0):.4f}>{detail.get('prior_dif', 0):.4f}"
        )
    elif strategy in ("ma_bullish_align", "ma_bearish_align"):
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"MA5={detail.get('ma5', 0):.2f} MA10={detail.get('ma10', 0):.2f} "
            f"MA20={detail.get('ma20', 0):.2f} MA60={detail.get('ma60', 0):.2f} "
            f"斜率{detail.get('ma20_slope_pct', 0):+.2f}%"
        )
    elif strategy == "bollinger_squeeze_breakout":
        dir_cn = "上破" if detail.get("direction") == "bullish" else "下破"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"{dir_cn} 带宽{detail.get('bandwidth', 0):.2f}%(60日最窄{detail.get('min_bandwidth_60', 0):.2f}%) "
            f"量比{detail.get('volume_ratio', 0):.1f}"
        )
    elif strategy == "adx_trend_start":
        dir_cn = "多头" if detail.get("direction") == "bullish" else "空头"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"ADX={detail.get('adx', 0):.1f}(前日{detail.get('prev_adx', 0):.1f}) {dir_cn}趋势"
        )
    elif strategy == "volume_breakout":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"收盘{detail.get('close', 0):.2f}>前高{detail.get('prev_high', 0):.2f} "
            f"量比MA20={detail.get('vol_ratio', 0):.1f}x"
        )
    elif strategy == "volume_divergence_top":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"价格新高{detail.get('close', 0):.2f} 但成交量萎缩至前高量{detail.get('vol_shrink_ratio', 0):.0%}"
        )
    elif strategy == "engulfing_pattern":
        dir_cn = "看涨" if detail.get("direction") == "bullish" else "看跌"
        display = f"{code} {stock_name} {dir_cn}{cn_name}"
    elif strategy == "morning_evening_star":
        pattern = detail.get("pattern", "")
        pattern_cn = "晨星" if pattern == "morning_star" else "暮星"
        display = f"{code} {stock_name} {pattern_cn}形态"
    elif strategy == "breakout_pullback":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"回踩支撑{detail.get('support_level', 0):.2f} 现价{detail.get('close', 0):.2f} "
            f"缩量{detail.get('vol_shrink', 0):.2f}x"
        )
    elif strategy == "support_breakdown":
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"跌破{detail.get('support_level', 0):.2f} 现价{detail.get('close', 0):.2f} "
            f"放量{detail.get('vol_ratio', 0):.1f}x"
        )
    elif strategy == "relative_strength":
        dir_cn = "强于" if detail.get("direction") == "bullish" else "弱于"
        display = (
            f"{code} {stock_name} {cn_name}: "
            f"60日{dir_cn}恒指 个股{detail.get('stock_return_60d', 0):+.1f}% "
            f"恒指{detail.get('index_return_60d', 0):+.1f}% "
            f"超额{detail.get('excess_return', 0):+.1f}%"
        )
    else:
        display = f"{code} {stock_name} {cn_name}"

    # Append market context to display (web only, not stealth message)
    if snapshot_data or capital_data:
        ctx_parts = []
        snap = (snapshot_data or {}).get(code, {})
        price = snap.get("price", 0)
        prev_close = snap.get("prevClose", 0)
        if price > 0:
            ctx_parts.append(f"现价{price:.2f}")
        if price > 0 and prev_close > 0:
            chg = (price - prev_close) / prev_close * 100
            ctx_parts.append(f"日{'涨' if chg >= 0 else '跌'}{chg:+.1f}%")
        vol_ratio = snap.get("volumeRatio", 0)
        if vol_ratio and vol_ratio > 1.5:
            ctx_parts.append(f"量比{vol_ratio:.1f}")
        cap = (capital_data or {}).get(code, {})
        inflow = cap.get("mainNetInflow", 0)
        if abs(inflow) >= 1_000_000:  # only show if >= 100万
            inflow_wan = inflow / 10000
            label = "净流入" if inflow > 0 else "净流出"
            display += f" | {' '.join(ctx_parts)} {label}{abs(inflow_wan):.0f}万" if ctx_parts else ""
        elif ctx_parts:
            display += f" | {' '.join(ctx_parts)}"

    # Build message (terminal 简短中文)
    message = f"{stock_name} {cn_name}"

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
# Daily Indicator Tracker — 日K线技术指标
# ══════════════════════════════════════════

def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 计算 — 处理 avg_loss=0 (全涨→100) 和 avg_gain=0 (全跌→0)"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # avg_loss=0 → RSI=100 (all gains); avg_gain=0 → RSI=0 (all losses)
    rsi = rsi.fillna(100)
    rsi = rsi.where(avg_gain > 0, 0)
    return rsi


def _calc_macd(series: pd.Series, fast: int = 12, slow: int = 26,
               signal: int = 9) -> dict:
    """MACD 计算 (复用 stock_data_fetcher 逻辑)"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return {"dif": dif, "dea": dea, "hist": hist}


def _calc_adx(high: pd.Series, low: pd.Series, close: pd.Series,
              period: int = 14) -> pd.Series:
    """Average Directional Index"""
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    plus_dm = (high - high.shift()).clip(lower=0).where(
        high - high.shift() > low.shift() - low, 0)
    minus_dm = (low.shift() - low).clip(lower=0).where(
        low.shift() - low > high - high.shift(), 0)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    dx = dx.fillna(0)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx


class DailyIndicatorTracker:
    """日K线技术指标追踪器

    - Futu request_history_kline(K_DAY) 获取 120 天日K (够算 MA60 + lookback)
    - 缓存 K 线, 每 30 分钟刷新 (盘中当日 K 线更新)
    - 计算所有指标, 检测状态变化触发信号
    """

    def __init__(self, config: dict):
        self._cfg = config
        self._refresh_sec = config.get("refresh_minutes", 30) * 60
        # {code: last_refresh_timestamp}
        self._last_refresh: dict[str, float] = {}
        # {code: DataFrame of kline}
        self._kline_cache: dict[str, pd.DataFrame] = {}
        # {code: dict of computed indicators}
        self._ind: dict[str, dict] = {}
        # Index kline cache (for relative strength)
        self._index_kline: Optional[pd.DataFrame] = None
        self._index_last_refresh: float = 0
        # {code: set of strategy names already triggered today}
        self._triggered_today: dict[str, set] = {}
        # {code: main_net_inflow_pct as fraction (0-1)} — updated by L2StrategyEngine.poll_once()
        self._cf_pct_cache: dict[str, float] = {}

    def update(self, code: str, ctx) -> list:
        """刷新 + 检测, 返回 0~N 个信号"""
        if not self._should_refresh(code):
            return []

        kline = self._fetch_kline(code, ctx)
        if kline is None or len(kline) < 60:
            return []

        self._kline_cache[code] = kline
        self._last_refresh[code] = time.time()
        self._compute_all(code, kline)
        return self._check_all_signals(code)

    def _should_refresh(self, code: str) -> bool:
        last = self._last_refresh.get(code, 0)
        return time.time() - last >= self._refresh_sec

    def _fetch_kline(self, code: str, ctx) -> Optional[pd.DataFrame]:
        """Fetch ~120 trading days of daily kline from Futu.

        IMPORTANT: Futu API ignores end+max_count and returns stale data
        unless BOTH start AND end are explicitly provided. We request
        200 calendar days (~135 trading days) to ensure >= 120 bars.
        """
        try:
            from futu import RET_OK, KLType, AuType
            import datetime as _dt
            today = _dt.date.today()
            start = (today - _dt.timedelta(days=200)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            ret, data, _ = ctx.request_history_kline(
                to_futu_code(code), ktype=KLType.K_DAY,
                autype=AuType.QFQ, start=start, end=end, max_count=200)
            if ret == RET_OK and data is not None and not data.empty:
                return data
        except Exception as e:
            logger.debug(f"daily kline fetch({code}) error: {e}")
        return None

    def _fetch_index_kline(self, ctx) -> Optional[pd.DataFrame]:
        """Fetch HSI index kline for relative strength"""
        now = time.time()
        if self._index_kline is not None and now - self._index_last_refresh < self._refresh_sec:
            return self._index_kline
        try:
            from futu import RET_OK, KLType, AuType
            import datetime as _dt
            today = _dt.date.today()
            start = (today - _dt.timedelta(days=200)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            ret, data, _ = ctx.request_history_kline(
                "HK.800000", ktype=KLType.K_DAY,
                autype=AuType.QFQ, start=start, end=end, max_count=200)
            if ret == RET_OK and data is not None and not data.empty:
                self._index_kline = data
                self._index_last_refresh = now
                return data
        except Exception as e:
            logger.debug(f"index kline fetch error: {e}")
        return None

    def _compute_all(self, code: str, kline: pd.DataFrame):
        """计算所有技术指标并缓存"""
        close = kline["close"].astype(float)
        high = kline["high"].astype(float)
        low = kline["low"].astype(float)
        volume = kline["volume"].astype(float)

        cfg = self._cfg

        # RSI
        rsi = _calc_rsi(close, cfg.get("rsi_period", 14))

        # MACD
        macd = _calc_macd(close, cfg.get("macd_fast", 12),
                          cfg.get("macd_slow", 26), cfg.get("macd_signal", 9))

        # Moving averages
        ma_periods = cfg.get("ma_periods", [5, 10, 20, 60])
        mas = {}
        for p in ma_periods:
            mas[f"ma{p}"] = close.rolling(p).mean()

        # Bollinger Bands
        bb_period = cfg.get("bollinger_period", 20)
        bb_std = cfg.get("bollinger_std", 2)
        bb_mid = close.rolling(bb_period).mean()
        bb_rolling_std = close.rolling(bb_period).std()
        bb_upper = bb_mid + bb_std * bb_rolling_std
        bb_lower = bb_mid - bb_std * bb_rolling_std
        bb_width = (bb_upper - bb_lower) / bb_mid * 100  # 百分比带宽

        # ADX
        adx = _calc_adx(high, low, close, cfg.get("adx_period", 14))

        # ATR (True Range → EWM, independent of ADX period)
        atr_period = cfg.get("atr_period", 14)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=atr_period, adjust=False).mean()

        # Volume MA
        vol_ma20 = volume.rolling(20).mean()

        self._ind[code] = {
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "rsi": rsi,
            "macd_dif": macd["dif"],
            "macd_dea": macd["dea"],
            "macd_hist": macd["hist"],
            "mas": mas,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            "adx": adx,
            "atr": atr,
            "vol_ma20": vol_ma20,
        }

    def _check_all_signals(self, code: str) -> list:
        """运行所有策略检测"""
        signals = []
        signals.extend(self._check_rsi(code))
        signals.extend(self._check_macd_cross(code))
        signals.extend(self._check_macd_divergence(code))
        signals.extend(self._check_ma_alignment(code))
        signals.extend(self._check_bollinger(code))
        signals.extend(self._check_adx(code))
        signals.extend(self._check_volume_patterns(code))
        signals.extend(self._check_kline_patterns(code))
        signals.extend(self._check_support_resistance(code))
        signals.extend(self._check_relative_strength(code))
        return signals

    def _already_triggered(self, code: str, strategy: str) -> bool:
        """日内去重: 同一策略同一股票每天只触发一次"""
        return strategy in self._triggered_today.get(code, set())

    def _mark_triggered(self, code: str, strategy: str):
        if code not in self._triggered_today:
            self._triggered_today[code] = set()
        self._triggered_today[code].add(strategy)

    def _make_signal(self, code: str, strategy: str, detail: dict) -> Optional[dict]:
        """构造信号 dict，自动去重"""
        if self._already_triggered(code, strategy):
            return None
        self._mark_triggered(code, strategy)
        return {"strategy": strategy, "code": code, "detail": detail}

    # ── Batch 1: RSI ──

    def _check_rsi(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []
        rsi = ind["rsi"]
        if len(rsi) < 3:
            return []

        signals = []
        curr = rsi.iloc[-1]
        prev = rsi.iloc[-2]
        if pd.isna(curr) or pd.isna(prev):
            return []
        ob = self._cfg.get("rsi_overbought", 70)
        os_val = self._cfg.get("rsi_oversold", 30)
        extreme_ob = self._cfg.get("rsi_extreme_overbought", 90)
        extreme_os = self._cfg.get("rsi_extreme_oversold", 20)
        extreme_days = self._cfg.get("rsi_extreme_consecutive_days", 2)

        # ── Extreme overbought (RSI > 90) — 优先于普通超买 ──
        if curr > extreme_ob:
            # Count consecutive days above extreme threshold
            consec = 0
            for i in range(1, min(len(rsi), 10)):
                v = rsi.iloc[-i]
                if pd.isna(v) or float(v) <= extreme_ob:
                    break
                consec += 1
            sig = self._make_signal(code, "rsi_extreme_overbought", {
                "rsi": round(float(curr), 1),
                "prev_rsi": round(float(prev), 1),
                "threshold": extreme_ob,
                "consecutive_days": consec,
            })
            if sig:
                signals.append(sig)
        # ── Normal overbought crossing (RSI crosses above 70) ──
        elif curr > ob and prev <= ob:
            sig = self._make_signal(code, "rsi_overbought", {
                "rsi": round(float(curr), 1),
                "prev_rsi": round(float(prev), 1),
                "threshold": ob,
            })
            if sig:
                signals.append(sig)

        # ── Extreme oversold (RSI < 20) — 优先于普通超卖 ──
        if curr < extreme_os:
            consec = 0
            for i in range(1, min(len(rsi), 10)):
                v = rsi.iloc[-i]
                if pd.isna(v) or float(v) >= extreme_os:
                    break
                consec += 1
            sig = self._make_signal(code, "rsi_extreme_oversold", {
                "rsi": round(float(curr), 1),
                "prev_rsi": round(float(prev), 1),
                "threshold": extreme_os,
                "consecutive_days": consec,
            })
            if sig:
                signals.append(sig)
        # ── Normal oversold crossing (RSI crosses below 30) ──
        elif curr < os_val and prev >= os_val:
            sig = self._make_signal(code, "rsi_oversold", {
                "rsi": round(float(curr), 1),
                "prev_rsi": round(float(prev), 1),
                "threshold": os_val,
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 1: MACD Cross ──

    def _check_macd_cross(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []
        hist = ind["macd_hist"]
        if len(hist) < 3:
            return []

        signals = []
        curr_hist = hist.iloc[-1]
        prev_hist = hist.iloc[-2]
        if pd.isna(curr_hist) or pd.isna(prev_hist):
            return []
        curr_dif = float(ind["macd_dif"].iloc[-1])
        curr_dea = float(ind["macd_dea"].iloc[-1])

        # Golden cross: hist turns positive
        if curr_hist > 0 and prev_hist <= 0:
            sig = self._make_signal(code, "macd_golden_cross", {
                "hist": round(float(curr_hist), 4),
                "prev_hist": round(float(prev_hist), 4),
                "dif": round(curr_dif, 4),
                "dea": round(curr_dea, 4),
            })
            if sig:
                signals.append(sig)

        # Death cross: hist turns negative
        if curr_hist < 0 and prev_hist >= 0:
            sig = self._make_signal(code, "macd_death_cross", {
                "hist": round(float(curr_hist), 4),
                "prev_hist": round(float(prev_hist), 4),
                "dif": round(curr_dif, 4),
                "dea": round(curr_dea, 4),
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 1: MACD Divergence ──

    def _check_macd_divergence(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        lookback = self._cfg.get("divergence_lookback", 20)
        close = ind["close"]
        dif = ind["macd_dif"]

        if len(close) < lookback + 5:
            return []

        signals = []
        recent_close = close.iloc[-lookback:]
        recent_dif = dif.iloc[-lookback:]
        prior_close = close.iloc[-lookback * 2:-lookback] if len(close) >= lookback * 2 else close.iloc[:-lookback]
        prior_dif = dif.iloc[-lookback * 2:-lookback] if len(dif) >= lookback * 2 else dif.iloc[:-lookback]

        if len(prior_close) < 5:
            return []

        curr_price = float(recent_close.iloc[-1])
        prior_high = float(prior_close.max())
        curr_dif = float(recent_dif.iloc[-1])
        prior_dif_at_high = float(prior_dif.loc[prior_close.idxmax()])

        # Top divergence: price new high + DIF not new high
        if curr_price > prior_high and curr_dif < prior_dif_at_high:
            sig = self._make_signal(code, "macd_top_divergence", {
                "price": round(curr_price, 2),
                "prior_high": round(prior_high, 2),
                "dif": round(curr_dif, 4),
                "prior_dif": round(prior_dif_at_high, 4),
                "lookback": lookback,
            })
            if sig:
                signals.append(sig)

        # Bottom divergence: price new low + DIF not new low
        prior_low = float(prior_close.min())
        prior_dif_at_low = float(prior_dif.loc[prior_close.idxmin()])

        if curr_price < prior_low and curr_dif > prior_dif_at_low:
            sig = self._make_signal(code, "macd_bottom_divergence", {
                "price": round(curr_price, 2),
                "prior_low": round(prior_low, 2),
                "dif": round(curr_dif, 4),
                "prior_dif": round(prior_dif_at_low, 4),
                "lookback": lookback,
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 2: MA Alignment ──

    def _check_ma_alignment(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        mas = ind["mas"]
        required = ["ma5", "ma10", "ma20", "ma60"]
        for k in required:
            if k not in mas or len(mas[k]) < 5:
                return []

        signals = []
        ma5 = float(mas["ma5"].iloc[-1])
        ma10 = float(mas["ma10"].iloc[-1])
        ma20 = float(mas["ma20"].iloc[-1])
        ma60 = float(mas["ma60"].iloc[-1])

        # MA20 slope (last 5 days)
        ma20_now = float(mas["ma20"].iloc[-1])
        ma20_5ago = float(mas["ma20"].iloc[-5])
        ma20_slope = (ma20_now - ma20_5ago) / ma20_5ago * 100 if ma20_5ago > 0 else 0

        # Bullish alignment
        if ma5 > ma10 > ma20 > ma60 and ma20_slope > 0:
            sig = self._make_signal(code, "ma_bullish_align", {
                "ma5": round(ma5, 2), "ma10": round(ma10, 2),
                "ma20": round(ma20, 2), "ma60": round(ma60, 2),
                "ma20_slope_pct": round(ma20_slope, 2),
            })
            if sig:
                signals.append(sig)

        # Bearish alignment
        if ma5 < ma10 < ma20 < ma60 and ma20_slope < 0:
            sig = self._make_signal(code, "ma_bearish_align", {
                "ma5": round(ma5, 2), "ma10": round(ma10, 2),
                "ma20": round(ma20, 2), "ma60": round(ma60, 2),
                "ma20_slope_pct": round(ma20_slope, 2),
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 2: Bollinger Squeeze Breakout ──

    def _check_bollinger(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        bb_width = ind["bb_width"]
        bb_upper = ind["bb_upper"]
        bb_lower = ind["bb_lower"]
        close = ind["close"]
        volume = ind["volume"]
        vol_ma20 = ind["vol_ma20"]

        if len(bb_width) < 60:
            return []

        signals = []
        curr_width = float(bb_width.iloc[-1])
        min_width_60 = float(bb_width.iloc[-60:].min())
        curr_close = float(close.iloc[-1])
        curr_upper = float(bb_upper.iloc[-1])
        curr_lower = float(bb_lower.iloc[-1])
        curr_vol = float(volume.iloc[-1])
        curr_vol_ma = float(vol_ma20.iloc[-1])

        # Squeeze: current width near 60-day minimum
        is_squeeze = curr_width <= min_width_60 * 1.1  # within 10% of minimum

        if is_squeeze and curr_vol_ma > 0:
            vol_ratio = curr_vol / curr_vol_ma
            # Breakout upper with volume
            if curr_close > curr_upper and vol_ratio > 1.2:
                sig = self._make_signal(code, "bollinger_squeeze_breakout", {
                    "direction": "bullish",
                    "close": round(curr_close, 2),
                    "upper": round(curr_upper, 2),
                    "bandwidth": round(curr_width, 2),
                    "min_bandwidth_60": round(min_width_60, 2),
                    "volume_ratio": round(vol_ratio, 2),
                })
                if sig:
                    signals.append(sig)
            # Breakdown lower with volume
            elif curr_close < curr_lower and vol_ratio > 1.2:
                sig = self._make_signal(code, "bollinger_squeeze_breakout", {
                    "direction": "bearish",
                    "close": round(curr_close, 2),
                    "lower": round(curr_lower, 2),
                    "bandwidth": round(curr_width, 2),
                    "min_bandwidth_60": round(min_width_60, 2),
                    "volume_ratio": round(vol_ratio, 2),
                })
                if sig:
                    signals.append(sig)

        return signals

    # ── Batch 2: ADX Trend Start ──

    def _check_adx(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        adx = ind["adx"]
        if len(adx) < 5:
            return []

        signals = []
        curr_adx = adx.iloc[-1]
        prev_adx = adx.iloc[-2]
        if pd.isna(curr_adx) or pd.isna(prev_adx):
            return []
        curr_adx = float(curr_adx)
        prev_adx = float(prev_adx)
        threshold = self._cfg.get("adx_trend_threshold", 25)

        # ADX crosses above threshold (relaxed: prev just needs to be <= threshold)
        if curr_adx > threshold and prev_adx <= threshold:
            # Direction inferred from MA alignment
            mas = ind.get("mas", {})
            ma5 = float(mas.get("ma5", pd.Series([0])).iloc[-1])
            ma20 = float(mas.get("ma20", pd.Series([0])).iloc[-1])
            direction = "bullish" if ma5 > ma20 else "bearish"

            sig = self._make_signal(code, "adx_trend_start", {
                "adx": round(curr_adx, 1),
                "prev_adx": round(prev_adx, 1),
                "direction": direction,
                "threshold": threshold,
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 3: Volume Patterns ──

    def _check_volume_patterns(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        close = ind["close"]
        high = ind["high"]
        volume = ind["volume"]
        vol_ma20 = ind["vol_ma20"]

        if len(close) < 25:
            return []

        signals = []
        curr_close = float(close.iloc[-1])
        curr_vol = float(volume.iloc[-1])
        curr_vol_ma = float(vol_ma20.iloc[-1])
        vol_ratio = self._cfg.get("volume_breakout_ratio", 1.5)

        # Find previous high (excluding last bar)
        prev_high = float(high.iloc[:-1].max())
        prev_high_idx = int(high.iloc[:-1].values.argmax())
        prev_high_vol = float(volume.iloc[prev_high_idx])

        # volume_breakout: close > prev high + volume > MA20 * ratio
        if curr_close > prev_high and curr_vol_ma > 0 and curr_vol > curr_vol_ma * vol_ratio:
            sig = self._make_signal(code, "volume_breakout", {
                "close": round(curr_close, 2),
                "prev_high": round(prev_high, 2),
                "volume": round(curr_vol, 0),
                "vol_ma20": round(curr_vol_ma, 0),
                "vol_ratio": round(curr_vol / curr_vol_ma, 2),
            })
            if sig:
                signals.append(sig)

        # volume_divergence_top: new high + volume < prev_high_volume * 0.7
        if curr_close > prev_high and prev_high_vol > 0 and curr_vol < prev_high_vol * 0.7:
            sig = self._make_signal(code, "volume_divergence_top", {
                "close": round(curr_close, 2),
                "prev_high": round(prev_high, 2),
                "volume": round(curr_vol, 0),
                "prev_high_volume": round(prev_high_vol, 0),
                "vol_shrink_ratio": round(curr_vol / prev_high_vol, 2),
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 3: K-line Patterns ──

    def _check_kline_patterns(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        close = ind["close"]
        kline = self._kline_cache.get(code)
        if kline is None or len(kline) < 4:
            return []

        signals = []
        open_prices = kline["open"].astype(float)

        # Need at least 3 bars for morning/evening star
        c0 = float(close.iloc[-3])   # 3 days ago
        o0 = float(open_prices.iloc[-3])
        c1 = float(close.iloc[-2])   # 2 days ago (middle)
        o1 = float(open_prices.iloc[-2])
        c2 = float(close.iloc[-1])   # today
        o2 = float(open_prices.iloc[-1])

        body0 = c0 - o0
        body1 = c1 - o1
        body2 = c2 - o2

        # ── Engulfing pattern (last 2 bars) ──
        prev_c = float(close.iloc[-2])
        prev_o = float(open_prices.iloc[-2])
        curr_c = float(close.iloc[-1])
        curr_o = float(open_prices.iloc[-1])
        prev_body = prev_c - prev_o
        curr_body = curr_c - curr_o

        # Bullish engulfing: prev bearish + curr bullish engulfs prev body
        if prev_body < 0 and curr_body > 0 and curr_o <= prev_c and curr_c >= prev_o:
            sig = self._make_signal(code, "engulfing_pattern", {
                "direction": "bullish",
                "prev_open": round(prev_o, 2), "prev_close": round(prev_c, 2),
                "curr_open": round(curr_o, 2), "curr_close": round(curr_c, 2),
            })
            if sig:
                signals.append(sig)

        # Bearish engulfing: prev bullish + curr bearish engulfs prev body
        if prev_body > 0 and curr_body < 0 and curr_o >= prev_c and curr_c <= prev_o:
            sig = self._make_signal(code, "engulfing_pattern", {
                "direction": "bearish",
                "prev_open": round(prev_o, 2), "prev_close": round(prev_c, 2),
                "curr_open": round(curr_o, 2), "curr_close": round(curr_c, 2),
            })
            if sig:
                signals.append(sig)

        # ── Morning / Evening Star (3-bar pattern) ──
        avg_body = (close - open_prices).abs().iloc[-20:].mean()
        if avg_body == 0:
            avg_body = 1  # avoid division by zero

        is_doji = abs(body1) < avg_body * 0.3  # middle bar is doji/small body

        # Morning star: big bearish + doji + big bullish
        if body0 < -avg_body * 0.8 and is_doji and body2 > avg_body * 0.8:
            sig = self._make_signal(code, "morning_evening_star", {
                "direction": "bullish",
                "pattern": "morning_star",
                "day1_body": round(body0, 2),
                "day2_body": round(body1, 2),
                "day3_body": round(body2, 2),
            })
            if sig:
                signals.append(sig)

        # Evening star: big bullish + doji + big bearish
        if body0 > avg_body * 0.8 and is_doji and body2 < -avg_body * 0.8:
            sig = self._make_signal(code, "morning_evening_star", {
                "direction": "bearish",
                "pattern": "evening_star",
                "day1_body": round(body0, 2),
                "day2_body": round(body1, 2),
                "day3_body": round(body2, 2),
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 4: Support / Resistance ──

    def _check_support_resistance(self, code: str) -> list:
        ind = self._ind.get(code)
        if not ind:
            return []

        close = ind["close"]
        high = ind["high"]
        low = ind["low"]
        volume = ind["volume"]
        vol_ma20 = ind["vol_ma20"]
        mas = ind.get("mas", {})

        if len(close) < 30:
            return []

        signals = []
        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        curr_vol = float(volume.iloc[-1])
        curr_vol_ma = float(vol_ma20.iloc[-1]) if vol_ma20 is not None else 0

        # ── breakout_pullback: 突破回踩 ──
        # Find recent breakout: was there a bar in last 10 days that broke prev high with volume?
        prev_high_20 = float(high.iloc[-30:-10].max()) if len(high) >= 30 else float(high.iloc[:-10].max())

        # Check last 10 days for breakout
        breakout_idx = None
        for i in range(-10, -1):
            bar_vol_ma = float(vol_ma20.iloc[i]) if not pd.isna(vol_ma20.iloc[i]) else curr_vol_ma
            if float(close.iloc[i]) > prev_high_20 and float(volume.iloc[i]) > bar_vol_ma * 1.3:
                breakout_idx = i
                break

        if breakout_idx is not None:
            # Current bar is pullback: volume < MA20 (shrinking) and close > old resistance (support)
            breakout_level = prev_high_20
            if (curr_vol < curr_vol_ma and curr_close > breakout_level * 0.98
                    and curr_close < float(close.iloc[breakout_idx]) * 1.02):
                sig = self._make_signal(code, "breakout_pullback", {
                    "close": round(curr_close, 2),
                    "support_level": round(breakout_level, 2),
                    "breakout_close": round(float(close.iloc[breakout_idx]), 2),
                    "vol_shrink": round(curr_vol / curr_vol_ma, 2) if curr_vol_ma > 0 else 0,
                })
                if sig:
                    signals.append(sig)

        # ── support_breakdown: 破位下跌 ──
        # Key support: 前低 or MA60
        prev_low_20 = float(low.iloc[-30:-3].min()) if len(low) >= 30 else float(low.iloc[:-3].min())
        ma60_val = float(mas.get("ma60", pd.Series([0])).iloc[-1])
        support_level = max(prev_low_20, ma60_val) if ma60_val > 0 else prev_low_20

        # Breakdown: close < support + volume > MA20
        if (curr_close < support_level and prev_close >= support_level
                and curr_vol_ma > 0 and curr_vol > curr_vol_ma):
            sig = self._make_signal(code, "support_breakdown", {
                "close": round(curr_close, 2),
                "support_level": round(support_level, 2),
                "prev_low_20": round(prev_low_20, 2),
                "ma60": round(ma60_val, 2),
                "vol_ratio": round(curr_vol / curr_vol_ma, 2),
            })
            if sig:
                signals.append(sig)

        return signals

    # ── Batch 4: Relative Strength ──

    def _check_relative_strength(self, code: str) -> list:
        """个股60日涨幅 vs 恒指60日涨幅"""
        ind = self._ind.get(code)
        if not ind:
            return []

        close = ind["close"]
        if len(close) < 60:
            return []

        # Stock 60-day return
        curr_price = float(close.iloc[-1])
        price_60ago = float(close.iloc[-60])
        if price_60ago <= 0:
            return []
        stock_return = (curr_price - price_60ago) / price_60ago * 100

        # Index 60-day return (use cached index kline)
        if self._index_kline is None or len(self._index_kline) < 60:
            return []

        idx_close = self._index_kline["close"].astype(float)
        idx_curr = float(idx_close.iloc[-1])
        idx_60ago = float(idx_close.iloc[-60])
        if idx_60ago <= 0:
            return []
        idx_return = (idx_curr - idx_60ago) / idx_60ago * 100

        excess_return = stock_return - idx_return

        signals = []
        rs_threshold = self._cfg.get("relative_strength_threshold", 15)
        # Strong: excess return top 20% proxy
        if excess_return > rs_threshold:
            sig = self._make_signal(code, "relative_strength", {
                "direction": "bullish",
                "stock_return_60d": round(stock_return, 1),
                "index_return_60d": round(idx_return, 1),
                "excess_return": round(excess_return, 1),
            })
            if sig:
                signals.append(sig)

        # Weak: bottom 20% proxy
        if excess_return < -rs_threshold:
            sig = self._make_signal(code, "relative_strength", {
                "direction": "bearish",
                "stock_return_60d": round(stock_return, 1),
                "index_return_60d": round(idx_return, 1),
                "excess_return": round(excess_return, 1),
            })
            if sig:
                signals.append(sig)

        return signals

    def update_index(self, ctx):
        """Refresh index kline (call once per poll cycle, not per stock)"""
        self._fetch_index_kline(ctx)

    # ── Daily Scorer (v2 策略核心) ──

    def get_atr(self, code: str, period: int = 14) -> float:
        """返回最新 ATR 值（已在 _compute_all 中缓存）"""
        ind = self._ind.get(code)
        if not ind or "atr" not in ind:
            return 0.0
        atr_series = ind["atr"]
        if len(atr_series) < 1 or pd.isna(atr_series.iloc[-1]):
            return 0.0
        return float(atr_series.iloc[-1])

    def get_recent_high(self, code: str, days: int = 20) -> float:
        """返回近 N 个交易日的最高价（用于 dip_buy 回撤检测）。"""
        ind = self._ind.get(code)
        if not ind or "high" not in ind or len(ind["high"]) < days:
            return 0.0
        return float(ind["high"].iloc[-days:].max())

    def snapshot_indicators(self) -> dict[str, dict]:
        """导出指标快照供 notifier TradePlanEngine 读取。

        Returns: {code: {rsi, macd_hist, macd_hist_list, vol_ratio, updated_at}}
        指标不可用的股票被跳过。
        """
        result = {}
        for code, ind in self._ind.items():
            hist_series = ind.get("macd_hist")
            rsi_series = ind.get("rsi")
            vol_series = ind.get("volume")
            vol_ma_series = ind.get("vol_ma20")
            if hist_series is None or rsi_series is None:
                continue

            # 最近 10 天 histogram (用于 narrowing / 金叉死叉判断)
            hist_list = []
            for v in hist_series.iloc[-10:].tolist():
                if not pd.isna(v):
                    hist_list.append(round(float(v), 6))

            rsi_val = None
            if len(rsi_series) > 0 and not pd.isna(rsi_series.iloc[-1]):
                rsi_val = round(float(rsi_series.iloc[-1]), 2)

            macd_hist_val = None
            if len(hist_series) > 0 and not pd.isna(hist_series.iloc[-1]):
                macd_hist_val = round(float(hist_series.iloc[-1]), 6)

            vol_ratio = None
            if (vol_series is not None and vol_ma_series is not None
                    and len(vol_series) > 0 and len(vol_ma_series) > 0
                    and not pd.isna(vol_series.iloc[-1])
                    and not pd.isna(vol_ma_series.iloc[-1])
                    and float(vol_ma_series.iloc[-1]) > 0):
                vol_ratio = round(float(vol_series.iloc[-1]) / float(vol_ma_series.iloc[-1]), 2)

            result[code] = {
                "rsi": rsi_val,
                "macd_hist": macd_hist_val,
                "macd_hist_list": hist_list,
                "vol_ratio": vol_ratio,
                "updated_at": int(time.time() * 1000),
            }
        return result

    def score(self, code: str,
              main_net_inflow: float = 0,
              main_net_inflow_pct: float = 0) -> dict:
        """综合评分 0-100，驱动 v2 日线级开仓/平仓决策。

        Args:
            code: 股票代码
            main_net_inflow: 主力净流入金额 (元)
            main_net_inflow_pct: 主力净流入占比 (0-1)

        Returns:
            {"total": int, "macd": int, "rsi": int, "ma": int,
             "capital_flow": int, "volume_price": int, "support": int,
             "action": "BUY"|"HOLD"|"SELL",
             "stop_loss": float, "take_profit": float, "atr": float}
        """
        ind = self._ind.get(code)
        if not ind:
            return {"total": 0, "action": "WAIT",
                    "stop_loss": 0, "take_profit": 0, "atr": 0}

        close = ind["close"]
        if len(close) < 60:
            return {"total": 0, "action": "WAIT",
                    "stop_loss": 0, "take_profit": 0, "atr": 0}

        # Stale data check: if kline is >5 days old, score is unreliable
        kline = self._kline_cache.get(code)
        if kline is not None and "time_key" in kline.columns:
            import datetime as _dt
            try:
                last_date = pd.to_datetime(kline["time_key"].iloc[-1]).date()
                today = _dt.date.today()
                stale_days = (today - last_date).days
                if stale_days > 5:
                    logger.warning(
                        f"score({code}): kline stale by {stale_days} days "
                        f"(last={last_date}), returning WAIT"
                    )
                    return {"total": 0, "action": "WAIT",
                            "stop_loss": 0, "take_profit": 0, "atr": 0}
            except Exception:
                pass

        # 读最新值
        c = float(close.iloc[-1])
        rsi_val = float(ind["rsi"].iloc[-1]) if not pd.isna(ind["rsi"].iloc[-1]) else 50
        macd_hist = float(ind["macd_hist"].iloc[-1]) if not pd.isna(ind["macd_hist"].iloc[-1]) else 0
        prev_hist = float(ind["macd_hist"].iloc[-2]) if len(ind["macd_hist"]) >= 2 and not pd.isna(ind["macd_hist"].iloc[-2]) else 0
        dif = float(ind["macd_dif"].iloc[-1]) if not pd.isna(ind["macd_dif"].iloc[-1]) else 0
        dea = float(ind["macd_dea"].iloc[-1]) if not pd.isna(ind["macd_dea"].iloc[-1]) else 0
        mas = ind["mas"]
        ma5 = float(mas["ma5"].iloc[-1]) if not pd.isna(mas["ma5"].iloc[-1]) else c
        ma10 = float(mas["ma10"].iloc[-1]) if not pd.isna(mas["ma10"].iloc[-1]) else c
        ma20 = float(mas["ma20"].iloc[-1]) if not pd.isna(mas["ma20"].iloc[-1]) else c
        ma60 = float(mas["ma60"].iloc[-1]) if not pd.isna(mas["ma60"].iloc[-1]) else c
        vol = float(ind["volume"].iloc[-1]) if not pd.isna(ind["volume"].iloc[-1]) else 0
        vol_ma20 = float(ind["vol_ma20"].iloc[-1]) if not pd.isna(ind["vol_ma20"].iloc[-1]) else 1
        # ADX: 衡量趋势强度，ADX<20 为震荡市，动量信号可信度下降
        adx_val = 25.0  # default: assume trending
        if "adx" in ind and len(ind["adx"]) > 0:
            _adx = ind["adx"].iloc[-1]
            if not pd.isna(_adx):
                adx_val = float(_adx)
        choppy_market = adx_val < 20  # 震荡市：MACD/RSI 各减 5 分

        scores = {}

        # ── MACD (20分) ──
        # 金叉 + hist 扩张 = 满分；死叉 = 0
        macd_score = 10  # 中性基线
        if macd_hist > 0 and prev_hist <= 0:
            macd_score = 20  # 刚金叉
        elif macd_hist > 0 and macd_hist > prev_hist:
            macd_score = 18  # hist 扩张
        elif macd_hist > 0:
            macd_score = 14  # hist 正但收缩
        elif macd_hist < 0 and prev_hist >= 0:
            macd_score = 0   # 刚死叉
        elif macd_hist < 0 and macd_hist < prev_hist:
            macd_score = 2   # hist 扩大负值
        elif macd_hist < 0:
            macd_score = 6   # hist 负但收窄
        # DIF/DEA 零轴上方加分
        if dif > 0 and dea > 0:
            macd_score = min(20, macd_score + 2)
        # ADX 震荡市过滤：动量信号降权
        if choppy_market:
            macd_score = max(0, macd_score - 5)
        scores["macd"] = macd_score

        # ── RSI (15分) ──
        # 适合中线建仓：50-65 为最优区间（温和多头），低位超卖区仍有价值
        if 50 <= rsi_val <= 65:
            rsi_score = 15  # 最优：温和上涨动能，未超买
        elif 45 <= rsi_val < 50:
            rsi_score = 12  # 中性偏强，回调后入场机会
        elif 30 <= rsi_val < 45:
            rsi_score = 10  # 超卖区，中线低位建仓价值
        elif 65 < rsi_val <= 75:
            rsi_score = 10  # 偏高，趋势延续但需谨慎
        elif 75 < rsi_val <= 80:
            rsi_score = 6   # 超买区，追涨风险上升
        elif rsi_val > 80:
            rsi_score = 3   # 极度超买
        else:
            rsi_score = 5   # RSI < 30 极度超卖（高风险高潜力）
        # ADX 震荡市过滤：RSI 动量信号同样降权
        if choppy_market:
            rsi_score = max(0, rsi_score - 5)
        scores["rsi"] = rsi_score

        # ── MA 排列 (20分) ──
        # 多头排列: ma5 > ma10 > ma20 > ma60
        bullish_align = (ma5 > ma10 > ma20 > ma60)
        bearish_align = (ma5 < ma10 < ma20 < ma60)
        above_ma20 = c > ma20
        above_ma60 = c > ma60

        if bullish_align and above_ma20:
            ma_score = 20
        elif above_ma20 and above_ma60 and ma5 > ma10:
            ma_score = 16
        elif above_ma20 and above_ma60:
            ma_score = 12
        elif above_ma60:
            ma_score = 8
        elif bearish_align:
            ma_score = 0
        else:
            ma_score = 5
        scores["ma"] = ma_score

        # ── 主力资金 (20分) ──
        # L2 实时主力净流入占比（基础分 0-20）
        # Use cached L2 capital flow if available; caller-provided param as fallback.
        # _cf_pct_cache stores fraction (0-1); mainNetInflowPct from Futu is percentage (0-100),
        # converted to fraction when stored via set_capital_flow_pct().
        effective_cf_pct = self._cf_pct_cache.get(code, main_net_inflow_pct)
        if effective_cf_pct > 0.10:
            cf_score = 20
        elif effective_cf_pct > 0.05:
            cf_score = 16
        elif effective_cf_pct > 0:
            cf_score = 12
        elif effective_cf_pct > -0.05:
            cf_score = 8
        elif effective_cf_pct > -0.10:
            cf_score = 4
        else:
            cf_score = 0

        cf_score = max(0, min(20, cf_score))
        scores["capital_flow"] = cf_score

        # ── 量价配合 (15分) ──
        # 价涨量增 = 好；量缩价涨 = 谨慎
        vol_ratio = vol / vol_ma20 if vol_ma20 > 0 else 1
        price_up = c > float(close.iloc[-2]) if len(close) >= 2 else False

        if price_up and vol_ratio > 1.5:
            vp_score = 15  # 放量上涨
        elif price_up and vol_ratio > 1.0:
            vp_score = 12  # 温和放量
        elif price_up and vol_ratio < 0.8:
            vp_score = 6   # 缩量上涨 (谨慎)
        elif not price_up and vol_ratio > 1.5:
            vp_score = 2   # 放量下跌
        elif not price_up and vol_ratio < 0.8:
            vp_score = 7   # 缩量回调 (不严重)
        else:
            vp_score = 8   # 中性
        scores["volume_price"] = vp_score

        # ── 支撑位 (10分) ──
        # 接近支撑 = 高分 (买入安全垫); 远离支撑 = 中性
        low_30 = float(ind["low"].iloc[-30:].min()) if len(ind["low"]) >= 30 else c * 0.95
        support = max(low_30, ma60)
        dist_to_support = (c - support) / c if c > 0 else 0

        if dist_to_support < 0.02:
            sup_score = 10  # 非常接近支撑
        elif dist_to_support < 0.05:
            sup_score = 8
        elif dist_to_support < 0.10:
            sup_score = 5
        else:
            sup_score = 3   # 远离支撑
        scores["support"] = sup_score

        total = sum(scores.values())

        # 动作判定
        action = "BUY" if total >= 70 else "HOLD" if total >= 40 else "SELL"

        # 计算建议止损止盈
        atr = self.get_atr(code)
        if atr <= 0:
            atr = c * 0.02  # fallback

        resistance = float(ind["high"].iloc[-30:].max()) if len(ind["high"]) >= 30 else c * 1.05
        stop_loss = max(support - atr * 0.5, c - atr * 2)
        take_profit = min(resistance, c + atr * 3)

        return {
            "total": total, **scores,
            "action": action,
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "atr": round(atr, 4),
        }

    def set_capital_flow_pct(self, code: str, pct_fraction: float):
        """Cache the latest L2 main capital flow pct (as a 0-1 fraction) for use in score()."""
        self._cf_pct_cache[code] = pct_fraction

    def reset(self):
        """每日重置"""
        self._last_refresh.clear()
        self._kline_cache.clear()
        self._ind.clear()
        self._index_kline = None
        self._index_last_refresh = 0
        self._triggered_today.clear()
        self._cf_pct_cache.clear()
        logger.info("DailyIndicatorTracker daily reset complete")


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

        # HK codes to monitor: all holdings + star watching (star = highest priority)
        self._hk_holdings = [
            code for code, info in watchlist.items()
            if code.startswith("HK")
            and not info.get("hidden", False)
            and (info.get("type") == "holding" or info.get("star", False))
        ]

        # Initialize trackers
        self._init_trackers()

        # Cooldowns (for raw signal dedup, not for notifications)
        cooldowns = {}
        for name, cfg in self._strategies.items():
            cooldowns[name] = cfg.get("cooldown_minutes", 15)
        # Daily indicator strategies use longer cooldown (default 480 min = 8h, effectively once/day)
        daily_cooldown = strategy_config.get("daily_indicators", {}).get("cooldown_minutes", 480)
        for s in DAILY_NOTIFY_STRATEGIES | {"rsi_overbought", "rsi_oversold", "adx_trend_start",
                "volume_divergence_top", "engulfing_pattern", "relative_strength"}:
            cooldowns[s] = daily_cooldown
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

        va_cfg = s.get("volume_accel_alert", {})
        self._volume_accel = VolumeAccelTracker(
            accel_ratio=va_cfg.get("accel_ratio", 1.8),
            imbalance_min=va_cfg.get("imbalance_min", 0.35),
            consecutive_min=va_cfg.get("consecutive_min", 3),
            min_turnover=va_cfg.get("min_turnover", 10_000_000),
        )

        tp_cfg = s.get("tick_persistence", {})
        self._tick_persistence = TickPersistenceTracker(
            min_tick_signals=tp_cfg.get("min_tick_signals", 8),
            persistence_ratio=tp_cfg.get("persistence_ratio", 0.80),
            recent_consistent=tp_cfg.get("recent_consistent", 3),
        )

        ird_cfg = s.get("institutional_retail_divergence", {})
        self._inst_retail = InstitutionalRetailTracker(
            institutional_min=ird_cfg.get("institutional_min", 10_000_000),
            retail_min=ird_cfg.get("retail_min", 5_000_000),
        )

        self._session = SessionAccumulator()

        # Daily indicator tracker
        daily_cfg = self._config.get("daily_indicators", {})
        self._daily_indicators = DailyIndicatorTracker(daily_cfg)

        # Signal timestamps for closing_surge (code -> [timestamp, ...])
        self._signal_timestamps: dict[str, list[float]] = {}

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

            # Subscribe TICKER (逐笔成交 — SessionAccumulator 始终需要，
            # 即使 large_order / tick_imbalance 策略 disabled 也要订阅)
            if True:
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
                mid_in = float(latest.get("mid_in_flow", 0) or 0)
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
                    "superNetInflow": round(super_in, 2),
                    "bigNetInflow": round(big_in, 2),
                    "midNetInflow": round(mid_in, 2),
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
                prev_close = row.get("prev_close_price")
                if prev_close is not None and prev_close > 0:
                    entry["prevClose"] = float(prev_close)
                volume_ratio = row.get("volume_ratio")
                if volume_ratio is not None:
                    entry["volumeRatio"] = round(float(volume_ratio), 2)
                amplitude = row.get("amplitude")
                if amplitude is not None:
                    entry["amplitude"] = round(float(amplitude), 2)
                avg_price = row.get("avg_price")
                if avg_price is not None and avg_price > 0:
                    entry["avgPrice"] = float(avg_price)
                turnover_rate = row.get("turnover_rate")
                if turnover_rate is not None:
                    entry["turnoverRate"] = round(float(turnover_rate), 2)
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
                ret, data = self._ctx.get_rt_ticker(futu_code, num=1000)
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

    # ── Momentum alert ──

    def _evaluate_momentum(self, code: str, session_data: dict,
                           capital_data: dict, snapshot_data: dict) -> Optional[dict]:
        """评估单只股票的动量确认信号

        5 个条件全部满足才触发:
          1. 大单净买笔数 >= large_order_buy_count_min
          2. 大单净买金额 > large_order_net_amount_min
          3. 日涨幅 > daily_change_pct_min
          4. Session direction = bullish
          5. 资金净流入 > 0 (无背离)
        """
        cfg = self._strategies.get("momentum_alert", {})
        if not cfg.get("enabled", True):
            return None

        # Session data for this stock
        sess = session_data.get(code)
        if not sess:
            return None

        # Condition 4: session direction must be bullish
        if sess.get("direction") != "bullish":
            return None

        # Condition 1 & 2: large order buy count and net amount
        lo = sess.get("large_order", {})
        buy_count = lo.get("buy_count", 0)
        net_amount = lo.get("net_amount", 0)

        min_count = cfg.get("large_order_buy_count_min", 3)
        min_amount = cfg.get("large_order_net_amount_min", 30_000_000)

        if buy_count < min_count:
            return None
        if net_amount <= min_amount:
            return None

        # Condition 3: daily change pct
        snap = snapshot_data.get(code, {})
        price = snap.get("price", 0)
        prev_close = snap.get("prevClose", 0)
        if price <= 0 or prev_close <= 0:
            return None

        daily_change_pct = (price - prev_close) / prev_close * 100
        min_change = cfg.get("daily_change_pct_min", 3.0)
        if daily_change_pct <= min_change:
            return None

        # Condition 5: capital net inflow > 0 (no divergence)
        cap = capital_data.get(code, {})
        main_inflow = cap.get("mainNetInflow", 0)
        if main_inflow <= 0:
            return None

        return {
            "strategy": "momentum_alert",
            "code": code,
            "detail": {
                "daily_change_pct": round(daily_change_pct, 2),
                "large_order_buy_count": buy_count,
                "large_order_net_amount": round(net_amount, 0),
                "main_net_inflow": round(main_inflow, 0),
                "session_direction": "bullish",
                "session_score": sess.get("score", 0),
            },
        }

    # ── Momentum sell alert (bearish mirror) ──

    def _evaluate_momentum_sell(self, code: str, session_data: dict,
                                capital_data: dict, snapshot_data: dict) -> Optional[dict]:
        """大单持续净卖出 + 日跌幅 + session bearish + 资金流出"""
        cfg = self._strategies.get("momentum_sell_alert", {})
        if not cfg.get("enabled", True):
            return None

        sess = session_data.get(code)
        if not sess:
            return None

        if sess.get("direction") != "bearish":
            return None

        lo = sess.get("large_order", {})
        sell_count = lo.get("sell_count", 0)
        net_amount = lo.get("net_amount", 0)  # negative when selling

        min_count = cfg.get("large_order_sell_count_min", 2)
        min_amount = cfg.get("large_order_net_amount_min", 15_000_000)

        if sell_count < min_count:
            return None
        if net_amount >= -min_amount:  # net_amount is negative; need abs > min
            return None

        snap = snapshot_data.get(code, {})
        price = snap.get("price", 0)
        prev_close = snap.get("prevClose", 0)
        if price <= 0 or prev_close <= 0:
            return None

        daily_change_pct = (price - prev_close) / prev_close * 100
        min_change = cfg.get("daily_change_pct_min", 2.0)
        if daily_change_pct >= -min_change:  # must be falling
            return None

        cap = capital_data.get(code, {})
        main_inflow = cap.get("mainNetInflow", 0)
        if main_inflow >= 0:  # must be outflowing
            return None

        return {
            "strategy": "momentum_sell_alert",
            "code": code,
            "detail": {
                "daily_change_pct": round(daily_change_pct, 2),
                "large_order_sell_count": sell_count,
                "large_order_net_amount": round(net_amount, 0),
                "main_net_inflow": round(main_inflow, 0),
                "session_direction": "bearish",
                "session_score": sess.get("score", 0),
            },
        }

    # ── Volume acceleration alert ──

    def _evaluate_volume_accel(self, code: str, session_data: dict,
                               capital_data: dict, snapshot_data: dict) -> Optional[dict]:
        """评估单只股票的放量加速信号

        6 个条件全部满足才触发:
          1. 成交额加速 (VolumeAccelTracker conditions 1-3)
          2. 日涨幅 > daily_change_pct_min
          3. Session direction = bullish
          4. 资金净流入 > 0
        """
        cfg = self._strategies.get("volume_accel_alert", {})
        if not cfg.get("enabled", True):
            return None

        # VolumeAccelTracker conditions (accel + sustained imbalance + min turnover)
        accel_result = self._volume_accel.evaluate(code)
        if not accel_result:
            return None

        # Condition: daily change pct
        snap = snapshot_data.get(code, {})
        price = snap.get("price", 0)
        prev_close = snap.get("prevClose", 0)
        if price <= 0 or prev_close <= 0:
            return None

        daily_change_pct = (price - prev_close) / prev_close * 100
        min_change = cfg.get("daily_change_pct_min", 3.0)
        if daily_change_pct <= min_change:
            return None

        # Condition: session score > 0 (relaxed from strict "bullish" —
        # in algo-splitting scenarios, large_order score is often 0 due to
        # insufficient large orders, making strict bullish unreachable)
        sess = session_data.get(code)
        if not sess or sess.get("score", 0) <= 0:
            return None

        # Condition: capital net inflow > 0
        cap = capital_data.get(code, {})
        main_inflow = cap.get("mainNetInflow", 0)
        if main_inflow <= 0:
            return None

        return {
            "strategy": "volume_accel_alert",
            "code": code,
            "detail": {
                "daily_change_pct": round(daily_change_pct, 2),
                "accel_ratio": accel_result["accel_ratio"],
                "curr_turnover": accel_result["curr_turnover"],
                "avg_prior_turnover": accel_result["avg_prior_turnover"],
                "curr_imbalance": accel_result["curr_imbalance"],
                "consecutive_above": accel_result["consecutive_above"],
                "main_net_inflow": round(main_inflow, 0),
                "capital_inflow": main_inflow > 0,
                "session_direction": sess.get("direction", "neutral"),
                "session_score": sess.get("score", 0),
            },
        }

    # ── Volume acceleration sell alert (bearish mirror) ──

    def _evaluate_volume_accel_sell(self, code: str, session_data: dict,
                                    capital_data: dict, snapshot_data: dict) -> Optional[dict]:
        """放量加速卖出: 成交额加速 + tick 持续偏卖 + 日跌 + 资金流出"""
        cfg = self._strategies.get("volume_accel_sell_alert", {})
        if not cfg.get("enabled", True):
            return None

        accel_result = self._volume_accel.evaluate_bearish(code)
        if not accel_result:
            return None

        snap = snapshot_data.get(code, {})
        price = snap.get("price", 0)
        prev_close = snap.get("prevClose", 0)
        if price <= 0 or prev_close <= 0:
            return None

        daily_change_pct = (price - prev_close) / prev_close * 100
        min_change = cfg.get("daily_change_pct_min", 2.0)
        if daily_change_pct >= -min_change:  # must be falling
            return None

        sess = session_data.get(code)
        if not sess or sess.get("score", 0) >= 0:  # must be bearish
            return None

        cap = capital_data.get(code, {})
        main_inflow = cap.get("mainNetInflow", 0)
        if main_inflow >= 0:  # must be outflowing
            return None

        return {
            "strategy": "volume_accel_sell_alert",
            "code": code,
            "detail": {
                "daily_change_pct": round(daily_change_pct, 2),
                "accel_ratio": accel_result["accel_ratio"],
                "curr_turnover": accel_result["curr_turnover"],
                "avg_prior_turnover": accel_result["avg_prior_turnover"],
                "curr_imbalance": accel_result["curr_imbalance"],
                "consecutive_above": accel_result["consecutive_above"],
                "main_net_inflow": round(main_inflow, 0),
                "capital_inflow": False,
                "session_direction": sess.get("direction", "neutral"),
                "session_score": sess.get("score", 0),
            },
        }

    # ── Large order reversal ──

    def _evaluate_large_order_reversal(self, code: str, session_data: dict) -> Optional[dict]:
        """检测大单方向翻转: prior 净方向 != recent 净方向 AND |recent 净额| >= min_net_amount"""
        cfg = self._strategies.get("large_order_reversal", {})
        if not cfg.get("enabled", True):
            return None

        sess = session_data.get(code)
        if not sess:
            return None

        orders = sess.get("large_order", {}).get("orders", [])
        min_prior = cfg.get("min_prior_count", 2)
        min_recent = cfg.get("min_recent_count", 3)
        min_net_amount = cfg.get("min_net_amount", 10_000_000)

        if len(orders) < min_prior + min_recent:
            return None

        prior_orders = orders[:len(orders) - min_recent]
        recent_orders = orders[-min_recent:]

        # Calculate net amounts
        def net_direction(order_list):
            net = 0.0
            for _, direction, amount in order_list:
                if direction == "BUY":
                    net += amount
                elif direction == "SELL":
                    net -= amount
            return net

        prior_net = net_direction(prior_orders)
        recent_net = net_direction(recent_orders)

        # Direction must flip AND recent must be significant
        if abs(recent_net) < min_net_amount:
            return None

        prior_dir = "BUY" if prior_net > 0 else "SELL"
        recent_dir = "BUY" if recent_net > 0 else "SELL"

        if prior_dir == recent_dir:
            return None

        direction = "bullish" if recent_dir == "BUY" else "bearish"

        return {
            "strategy": "large_order_reversal",
            "code": code,
            "detail": {
                "direction": direction,
                "prior_direction": prior_dir,
                "recent_direction": recent_dir,
                "prior_net_amount": round(prior_net, 0),
                "recent_net_amount": round(recent_net, 0),
                "prior_count": len(prior_orders),
                "recent_count": len(recent_orders),
            },
        }

    # ── Closing surge ──

    def _evaluate_closing_surge(self, code: str) -> Optional[dict]:
        """尾盘信号密度异常: 最后30分钟信号密度 vs 盘中平均"""
        cfg = self._strategies.get("closing_surge", {})
        if not cfg.get("enabled", True):
            return None

        # Only check after closing_start_time (default 15:30)
        start_str = cfg.get("closing_start_time", "15:30")
        start_h, start_m = map(int, start_str.split(":"))
        now = datetime.now()
        if now.hour * 100 + now.minute < start_h * 100 + start_m:
            return None

        timestamps = self._signal_timestamps.get(code, [])
        if not timestamps:
            return None

        # Closing window: last 30 minutes
        closing_cutoff = time.time() - 1800
        closing_signals = [t for t in timestamps if t >= closing_cutoff]
        closing_count = len(closing_signals)

        min_signal_count = cfg.get("min_signal_count", 5)
        if closing_count < min_signal_count:
            return None

        # Intraday average per 30-min window
        if len(timestamps) <= closing_count:
            return None  # All signals are in closing window, no baseline

        total = len(timestamps)
        first_ts = timestamps[0]
        elapsed_sec = time.time() - first_ts
        elapsed_windows = max(elapsed_sec / 1800, 1)
        intraday_avg = (total - closing_count) / max(elapsed_windows - 1, 1)

        if intraday_avg <= 0:
            return None

        density_ratio = closing_count / intraday_avg
        min_ratio = cfg.get("density_ratio", 2.5)
        if density_ratio < min_ratio:
            return None

        # Check if closing signals include large_order (from signal_timestamps we only have timestamps,
        # so we check session accumulator for recent large orders in closing window)
        # Simplified: just require density + count conditions

        # Direction: use session direction as proxy
        direction = "neutral"
        # We'll set this from caller context

        return {
            "strategy": "closing_surge",
            "code": code,
            "detail": {
                "direction": direction,
                "closing_count": closing_count,
                "intraday_avg": round(intraday_avg, 1),
                "density_ratio": round(density_ratio, 1),
                "total_signals": total,
            },
        }

    # ── Main detection loop ──

    def poll_once(self) -> tuple[list[dict], dict]:
        """执行一轮检测，返回格式化后的信号列表 + session 累积快照

        Returns:
            (signals, session_snapshot)
            signals: [{"ts", "time", "strategy", "code", ...}, ...]
            session_snapshot: {code: {direction, score, tick, large_order, capital_flow}}
        """
        if not self.connect():
            return [], {}

        self._subscribe()

        raw_signals: list[dict] = []

        try:
            # Always fetch all data sources — SessionAccumulator + session_snapshots
            # need them regardless of individual strategy enabled state
            vol_accel_enabled = self._strategies.get("volume_accel_alert", {}).get("enabled", True)
            capital_data = self._fetch_capital_flow()
            snapshot_data = self._fetch_snapshots()
            ticker_data = self._fetch_rt_tickers()

            # ── 1. Capital flow spike ──
            if self._strategies.get("capital_flow_spike", {}).get("enabled", True):
                for code, data in capital_data.items():
                    pct = data.get("mainNetInflowPct", 0)
                    sig = self._capital_flow.update(code, pct)
                    if sig:
                        raw_signals.append(sig)

            # ── 2. Large order (always detect for SessionAccumulator) ──
            for code, ticks in ticker_data.items():
                sigs = self._large_order.check_tickers(code, ticks)
                for sig in sigs:
                    self._session.feed_large_order(code, sig["detail"])
                if self._strategies.get("large_order", {}).get("enabled", True):
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

            # ── Feed VolumeAccelTracker from tick imbalance window stats ──
            # Guard: only during continuous trading to avoid stale data during lunch break
            if vol_accel_enabled and self._is_continuous_trading():
                for code in self._hk_holdings:
                    stats = self._tick_imbalance.get_current_stats(code)
                    if stats:
                        self._volume_accel.observe(
                            code, stats["turnover"], stats["imbalance"]
                        )

            # ── Session accumulation (always, regardless of cooldown) ──
            for code, ticks in ticker_data.items():
                self._session.feed_ticks(code, ticks)
            for code, data in capital_data.items():
                self._session.update_capital_flow(code, data)
                # Feed capital flow pct into DailyIndicatorTracker for score() use.
                # mainNetInflowPct is a percentage (e.g. 12.5 = 12.5%); convert to fraction (0-1).
                pct_raw = data.get("mainNetInflowPct", 0)
                self._daily_indicators.set_capital_flow_pct(code, pct_raw / 100.0)

        except Exception as e:
            logger.warning(f"L2 poll error: {e}")
            self.close()
            self._last_fail_time = time.time()
            return [], {}

        # Take session snapshot early — momentum evaluation needs latest state
        session_snapshot = self._session.snapshot()

        signals = []

        # ── 6. Momentum alert (session-level, not short-window) ──
        for code in self._hk_holdings:
            sig = self._evaluate_momentum(code, session_snapshot, capital_data, snapshot_data)
            if sig and self._cooldown.can_trigger("momentum_alert", code):
                self._cooldown.record("momentum_alert", code)
                signals.append(format_signal(sig, self._name_map, notify=True, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 6b. Momentum sell alert (bearish mirror) ──
        if self._strategies.get("momentum_sell_alert", {}).get("enabled", True):
            for code in self._hk_holdings:
                sig = self._evaluate_momentum_sell(code, session_snapshot, capital_data, snapshot_data)
                if sig and self._cooldown.can_trigger("momentum_sell_alert", code):
                    self._cooldown.record("momentum_sell_alert", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 7. Volume acceleration alert (session-level) ──
        if vol_accel_enabled:
            for code in self._hk_holdings:
                sig = self._evaluate_volume_accel(code, session_snapshot, capital_data, snapshot_data)
                if sig and self._cooldown.can_trigger("volume_accel_alert", code):
                    self._cooldown.record("volume_accel_alert", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 7b. Volume acceleration sell alert (bearish mirror) ──
        if self._strategies.get("volume_accel_sell_alert", {}).get("enabled", True):
            for code in self._hk_holdings:
                sig = self._evaluate_volume_accel_sell(code, session_snapshot, capital_data, snapshot_data)
                if sig and self._cooldown.can_trigger("volume_accel_sell_alert", code):
                    self._cooldown.record("volume_accel_sell_alert", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # Apply cooldowns and format raw signals (notify=false, web only)
        accepted_raw = []
        for raw in raw_signals:
            strategy = raw["strategy"]
            code = raw["code"]
            if self._cooldown.can_trigger(strategy, code):
                self._cooldown.record(strategy, code)
                signals.append(format_signal(raw, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))
                accepted_raw.append(raw)

        # ── 8. Tick persistence (feed from accepted tick_imbalance signals) ──
        if self._strategies.get("tick_persistence", {}).get("enabled", True):
            for raw in accepted_raw:
                if raw["strategy"] == "tick_imbalance":
                    self._tick_persistence.feed(raw["code"], raw["detail"]["imbalance"])
            for code in self._hk_holdings:
                sig = self._tick_persistence.evaluate(code)
                if sig and self._cooldown.can_trigger("tick_persistence", code):
                    self._cooldown.record("tick_persistence", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 9. Institutional retail divergence ──
        if self._strategies.get("institutional_retail_divergence", {}).get("enabled", True):
            for code in self._hk_holdings:
                sig = self._inst_retail.evaluate(code, capital_data)
                if sig and self._cooldown.can_trigger("institutional_retail_divergence", code):
                    self._cooldown.record("institutional_retail_divergence", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 10. Large order reversal ──
        if self._strategies.get("large_order_reversal", {}).get("enabled", True):
            for code in self._hk_holdings:
                sig = self._evaluate_large_order_reversal(code, session_snapshot)
                if sig and self._cooldown.can_trigger("large_order_reversal", code):
                    self._cooldown.record("large_order_reversal", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── Track signal timestamps for closing_surge ──
        now_ts = time.time()
        for s in signals:
            code = s.get("code", "")
            if code:
                if code not in self._signal_timestamps:
                    self._signal_timestamps[code] = []
                self._signal_timestamps[code].append(now_ts)

        # ── 11. Closing surge ──
        if self._strategies.get("closing_surge", {}).get("enabled", True):
            for code in self._hk_holdings:
                sig = self._evaluate_closing_surge(code)
                if sig and self._cooldown.can_trigger("closing_surge", code):
                    # Set direction from session
                    sess_dir = session_snapshot.get(code, {}).get("direction", "neutral")
                    if sess_dir != "neutral":
                        sig["detail"]["direction"] = sess_dir
                    self._cooldown.record("closing_surge", code)
                    signals.append(format_signal(sig, self._name_map, notify=False, snapshot_data=snapshot_data, capital_data=capital_data))

        # ── 12. Daily indicator signals (refresh every 30min) ──
        daily_cfg = self._config.get("daily_indicators", {})
        if daily_cfg.get("enabled", True) and self._ctx is not None:
            try:
                self._daily_indicators.update_index(self._ctx)
                for code in self._hk_holdings:
                    signals_daily = self._daily_indicators.update(code, self._ctx)
                    for sig in signals_daily:
                        strategy = sig["strategy"]
                        if self._cooldown.can_trigger(strategy, code):
                            self._cooldown.record(strategy, code)
                            notify = strategy in DAILY_NOTIFY_STRATEGIES
                            signals.append(format_signal(
                                sig, self._name_map, notify=notify,
                                snapshot_data=snapshot_data,
                                capital_data=capital_data))
            except Exception as e:
                logger.warning(f"Daily indicator error: {e}")

        # Feed raw signals to scorer and evaluate composite verdicts
        if accepted_raw:
            self._scorer.feed(accepted_raw)
        composites = self._scorer.evaluate()
        for comp in composites:
            # Composite notify logic: only L1 when high-score AND session-aligned
            comp_score = comp.get("detail", {}).get("score", 0)
            comp_strategy = comp.get("strategy", "")
            comp_code = comp.get("code", "")
            sess_dir = session_snapshot.get(comp_code, {}).get("direction", "neutral")
            comp_dir = "bullish" if "bullish" in comp_strategy else "bearish"
            session_aligned = (comp_dir == sess_dir)
            comp_notify = comp_score >= 7
            signals.append(format_signal(comp, self._name_map, notify=comp_notify, snapshot_data=snapshot_data, capital_data=capital_data))

        return signals, session_snapshot

    def reset_daily(self):
        """每日重置 — 清除所有追踪状态"""
        self._capital_flow.reset()
        self._large_order.reset()
        self._order_book.reset()
        self._divergence.reset()
        self._tick_imbalance.reset()
        self._volume_accel.reset()
        self._tick_persistence.reset()
        self._cooldown.reset()
        self._scorer.reset()
        self._session.reset()
        self._daily_indicators.reset()
        self._signal_timestamps.clear()
        logger.info("L2 strategy engine daily reset complete")
