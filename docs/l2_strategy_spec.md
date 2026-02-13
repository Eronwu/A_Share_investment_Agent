# L2 Strategy Engine Specification

> Version: 2.0 | Updated: 2025-02-12
> Source: `src/tools/l2_strategy_engine.py`
> Config: `src/data/l2_strategy_config.json`

## 1. System Overview

L2 Strategy Engine 基于 Futu OpenD 实时 L2 数据，对 HK 持仓股票进行盘中量化信号检测。

### 1.1 Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    L2 Strategy Daemon                    │
│                  (l2_strategy_daemon.py)                 │
│                 3s poll during trading hours             │
└──────────────────────┬──────────────────────────────────┘
                       │ poll_once()
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   L2StrategyEngine                       │
│                                                         │
│  ┌── Data Fetchers ──────────────────────────────────┐  │
│  │ _fetch_capital_flow()  → capital_data             │  │
│  │ _fetch_snapshots()     → snapshot_data            │  │
│  │ _fetch_rt_tickers()    → ticker_data              │  │
│  └───────────────────────────────────────────────────┘  │
│                       │                                  │
│  ┌── 5 Trackers ─────────────────────────────────────┐  │
│  │ 1. CapitalFlowTracker    ← capital_data           │  │
│  │ 2. LargeOrderTracker     ← ticker_data            │  │
│  │ 3. OrderBookTracker      ← snapshot_data          │  │
│  │ 4. DivergenceTracker     ← snapshot + capital     │  │
│  │ 5. TickImbalanceTracker  ← ticker_data            │  │
│  └──────────────┬────────────────────────────────────┘  │
│                 │ raw_signals                            │
│  ┌──────────────▼────────────────────────────────────┐  │
│  │ VolumeAccelTracker  ← TickImbalanceTracker stats   │  │
│  │ SessionAccumulator → session snapshot              │  │
│  │ _evaluate_momentum → momentum_alert (notify=true)  │  │
│  │ _evaluate_volume_accel → vol_accel (notify=true)   │  │
│  │ CooldownManager    → 去重                          │  │
│  │ SignalScorer       → 加权复合研判                   │  │
│  └──────────────┬────────────────────────────────────┘  │
│                 │ formatted signals                      │
│  ┌──────────────▼────────────────────────────────────┐  │
│  │ format_signal() → dual-format events               │  │
│  │   notify=false → web 日志                          │  │
│  │   notify=true  → 弹通知 (composite + momentum)     │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ write to
                       ▼
              l2_strategy_signals.json
                       │
                       ▼ consumed by
              stock_notifier.py
                  │         │
                  ▼         ▼
            macOS notify  alert_events.json → Web Dashboard
```

### 1.2 Data Sources

| API | Method | Used by | Subscription |
|-----|--------|---------|-------------|
| Capital Flow | `get_capital_flow(INTRADAY)` | Strategy 1, 4 | No (on-demand) |
| Market Snapshot | `get_market_snapshot()` | Strategy 3, 4 | No (on-demand) |
| RT Ticker | `get_rt_ticker(num=50)` | Strategy 2, 5 | Yes (`SubType.TICKER`) |
| Order Book | (via snapshot) | Strategy 3 | Yes (`SubType.ORDER_BOOK`) |

### 1.3 Operating Scope

- **Market**: HK 持仓股票 (`type: "holding"`, `hidden: false`, code starts with `HK`)
- **Trading hours**: 连续交易时段（09:30-12:00, 13:00-16:00 HKT）
- **Ticker data**: 仅连续交易时段获取（竞价撮合为聚合成交，不算大单）
- **Poll interval**: Trading 3s / Non-trading 60s
- **Daily reset**: 午夜自动清除所有 Tracker 状态 + 重载 config

---

## 2. Strategy Specifications

### 2.1 Strategy 1: Capital Flow Spike (`capital_flow_spike`)

**中文名**: 主力资金异动
**权重**: 1

#### Logic

在滑动窗口内检测主力资金净流入占比的跳变：

```
窗口最早的 mainNetInflowPct < low_threshold (2%)
AND 窗口最新的 mainNetInflowPct > high_threshold (8%)
→ 触发信号
```

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window_minutes` | 5 | 滑动窗口长度 |
| `low_threshold_pct` | 2.0 | 窗口起点的低阈值（%） |
| `high_threshold_pct` | 8.0 | 窗口终点的高阈值（%） |
| `cooldown_minutes` | 15 | 同一股票冷却时间 |

#### Direction Inference

`to_pct > from_pct` → **bullish**, else → **bearish**

#### Signal Detail

```json
{
  "strategy": "capital_flow_spike",
  "code": "HK09988",
  "detail": {
    "from_pct": 1.5,
    "to_pct": 9.2,
    "window_sec": 300
  }
}
```

#### Noise Characteristics

- **噪音较大**: 开盘初期成交额小，占比波动大
- **已有防噪**: 成交额 < 500 万时 inflow_pct 强制为 0
- **优化方向**: 可结合成交量加权，或要求窗口内至少 N 个采样点

---

### 2.2 Strategy 2: Large Order (`large_order`)

**中文名**: 大单成交
**权重**: 2

#### Logic

逐笔成交中单笔金额 > min_amount → 触发信号。含 Futu `ticker_direction` 买卖方向。

```
单笔 turnover >= min_amount (500万)
→ 触发信号，方向 = ticker_direction (BUY/SELL/NEUTRAL)
```

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_amount` | 5,000,000 | 大单阈值（元） |
| `cooldown_minutes` | 10 | 同一股票冷却时间 |

#### Direction Inference

- `direction == "BUY"` → **bullish**
- `direction == "SELL"` → **bearish**
- `direction == "NEUTRAL"` → **neutral** (不参与复合评分)

#### Signal Detail

```json
{
  "strategy": "large_order",
  "code": "HK09988",
  "detail": {
    "amount": 8500000,
    "price": 88.50,
    "volume": 96000,
    "direction": "BUY"
  }
}
```

#### Warmup

首次调用只记录 sequence 水位，不触发信号（跳过启动时的存量 tick）。

#### Known Limitations

- `ticker_direction` 由 Futu 推断（对比成交价与买一卖一），非交易所官方标记
- Sequence 假设 Futu 返回按升序排列；乱序 tick 会被跳过（概率极低）

---

### 2.3 Strategy 3: Order Book Imbalance (`order_book_imbalance`)

**中文名**: 盘口异动
**权重**: 1

#### Logic

检测委比的短时剧烈变化：

```
|当前 bidAskRatio - 上次 bidAskRatio| > delta_threshold (40)
→ 触发信号
```

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `delta_threshold` | 40.0 | 委比变化阈值 |
| `cooldown_minutes` | 20 | 同一股票冷却时间 |

#### Direction Inference

`delta > 0` → **bullish** (买盘增强), `delta < 0` → **bearish** (卖盘增强)

#### Signal Detail

```json
{
  "strategy": "order_book_imbalance",
  "code": "HK09988",
  "detail": {
    "prev_ratio": 20.0,
    "curr_ratio": 65.0,
    "delta": 45.0
  }
}
```

#### Noise Characteristics

- **噪音较大**: 单次快照对比，盘口挂单变化频繁
- **优化方向**: 可改为 N 次采样的滑动窗口均值对比，降低瞬时噪音

---

### 2.4 Strategy 4: Volume-Price Divergence (`volume_price_divergence`)

**中文名**: 量价背离
**权重**: 2

#### Logic

价格新高但主力资金净流出 — 经典顶部信号：

```
当前价格 = 窗口内最高价
AND 主力净流入 < -100万
→ 触发信号（固定 bearish）
```

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `lookback_minutes` | 30 | 价格回溯窗口 |
| `cooldown_minutes` | 30 | 同一股票冷却时间 |

#### Direction Inference

固定 **bearish** (量价背离 = 看空信号)

#### Signal Detail

```json
{
  "strategy": "volume_price_divergence",
  "code": "HK09988",
  "detail": {
    "price": 89.50,
    "window_high": 89.50,
    "main_net_inflow": -2500000
  }
}
```

#### Anti-Noise

- 窗口需累积至少 10 个采样点（约 30 秒）才开始判断
- 主力净流出要求绝对值 > 100 万，过滤微量噪音

---

### 2.5 Strategy 5: Tick Imbalance (`tick_imbalance`)

**中文名**: 主买主卖失衡
**权重**: 3 (最高)

#### Logic

5 分钟窗口内逐笔累积主动买卖量，计算失衡度：

```
buy_vol  = sum(volume where direction=BUY)
sell_vol = sum(volume where direction=SELL)
imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol)

|imbalance| >= threshold (0.4)
AND window_turnover >= min_turnover (1000万)
→ 触发信号
```

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window_minutes` | 5 | 滑动窗口长度 |
| `imbalance_threshold` | 0.4 | 失衡度阈值（-1 到 +1） |
| `min_turnover` | 10,000,000 | 窗口最低成交额（过滤低流动性噪音） |
| `cooldown_minutes` | 10 | 同一股票冷却时间 |

#### Direction Inference

`imbalance > 0` → **bullish** (主买占优), `imbalance < 0` → **bearish** (主卖占优)

#### Signal Detail

```json
{
  "strategy": "tick_imbalance",
  "code": "HK09988",
  "detail": {
    "imbalance": 0.523,
    "buy_vol": 380000,
    "sell_vol": 120000,
    "turnover": 45000000,
    "window_sec": 300
  }
}
```

#### Warmup

首次调用只记录 sequence 水位，不触发信号。与 `LargeOrderTracker` 共享 `_fetch_rt_tickers()` 数据源。

#### Academic Basis

Tick Imbalance 在学术研究中报告 62-68% 的短线预测胜率（来源: Easley, Lopez de Prado, O'Hara 系列论文）。本实现为简化版，核心假设：短窗口内主动买卖方向的显著失衡预示后续价格变动方向。

---

### 2.6 Session Direction (`SessionAccumulator`)

**中文名**: 开盘至今多空研判

#### Overview

与 5 个 Tracker 的短窗口事件检测不同，`SessionAccumulator` 维护全天累积统计，持续更新直到收盘。它**不触发信号**，只输出当前多空方向判断到 `session` 字段。

#### Accumulation Dimensions

| 维度 | 数据源 | 累积方式 | 方向判断 |
|------|--------|----------|----------|
| **Tick 方向** | `_fetch_rt_tickers()` direction | 全天 buy_vol / sell_vol | imbalance = (buy-sell)/(buy+sell), threshold ±0.1 |
| **大单方向** | `LargeOrderTracker` 识别的大单 | 全天 buy/sell count + amount | \|net_amount\| > 1000万 → 给方向分 |
| **资金流向** | `_fetch_capital_flow()` INTRADAY | Futu 已是当日累积，直接读 latest | \|mainNetInflowPct\| > 1% → 给方向分 |

#### Minimum-Data Guards

各维度在数据不足时强制 `direction_score = 0`，避免开盘初期低基数噪音：

| 维度 | Guard | Default | Rationale |
|------|-------|---------|-----------|
| Tick | `total_vol < MIN_TICK_VOL` | 50,000 股 | 几笔 tick 无统计意义 |
| 大单 | `count < MIN_LARGE_ORDER_COUNT` | 3 笔 | 1-2 笔大单随机性太大 |
| 大单 | `abs(net_amount) < MIN_LARGE_ORDER_NET` | 1000万 | 净额不显著 = 方向不明确 |
| 资金流 | `abs(mainNetInflowPct) < MIN_CAPITAL_FLOW_PCT` | 1.0% | 占比微小 = 无方向意义 |

#### Scoring

三个维度各出一个方向分（+1 bullish / -1 bearish / 0 neutral），加权求和。
**数据不足时该维度 score = 0，不参与评分。**

```
tick_direction_score   = 0 if total_vol < 50000
                         +1 if imbalance > 0.1, -1 if < -0.1, else 0   (weight=2)

large_order_score      = 0 if count < 3 or |net_amount| < 10M
                         +1 if net_amount > 0, -1 if < 0               (weight=2)

capital_flow_score     = 0 if |mainNetInflowPct| < 1%
                         +1 if pct > 0, -1 if < 0                      (weight=1)

total = tick * 2 + large_order * 2 + capital * 1
direction = "bullish" if total > 0, "bearish" if total < 0, "neutral"
```

Score 范围: -5 到 +5。

#### Output Format

```json
{
  "session": {
    "HK09988": {
      "direction": "bearish",
      "score": -3,
      "tick": {
        "buy_vol": 1200000,
        "sell_vol": 1800000,
        "imbalance": -0.2,
        "direction_score": -1
      },
      "large_order": {
        "buy_count": 3,
        "sell_count": 7,
        "buy_amount": 25000000,
        "sell_amount": 52000000,
        "net_amount": -27000000,
        "direction_score": -1
      },
      "capital_flow": {
        "main_net_inflow": -85000000,
        "main_net_inflow_pct": -3.2,
        "direction_score": -1
      }
    }
  }
}
```

#### Warmup & Dedup

- Tick 方向累积复用 sequence 水位去重逻辑（首次调用 warmup，跳过存量 tick）
- 大单累积来自 `LargeOrderTracker` 已识别的信号（cooldown 之前），不重复检测
- 资金流为 Futu INTRADAY 当日快照，每次覆盖更新

#### Key Differences from Trackers

| | 5 Trackers | SessionAccumulator |
|---|---|---|
| 窗口 | 5-30 分钟滑动窗口 | 全天累积（开盘到当前） |
| 输出 | 触发信号 + 冷却 | 持续更新，无信号触发 |
| 通知 | 可触发 macOS 通知 | 不触发通知 |
| 重置 | daily reset | daily reset |

---

### 2.7 Strategy 6: Momentum Alert (`momentum_alert`)

**中文名**: 动量确认

#### Overview

与短窗口事件检测的 5 个 Tracker 不同，`momentum_alert` 是 **session 级别的高置信度信号**。它利用 `SessionAccumulator` 的全天累积数据 + 快照的日涨幅 + 资金流数据，在 5 个条件全部满足时触发 macOS 通知。

仅对 HK 持仓生效。

#### Trigger Conditions (全部满足)

| # | 条件 | 阈值 | 数据源 |
|---|------|------|--------|
| 1 | 大单净买笔数 | >= 3 笔 | `SessionAccumulator.large_order.buy_count` |
| 2 | 大单净买金额 | > 3000万 | `SessionAccumulator.large_order.net_amount` |
| 3 | 日涨幅 | > 3% | `snapshot.price` vs `snapshot.prevClose` |
| 4 | Session 方向 | = bullish | `SessionAccumulator.direction` |
| 5 | 无背离 | 资金净流入 > 0 | `capital_data.mainNetInflow` |

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `large_order_buy_count_min` | 3 | 大单净买最低笔数 |
| `large_order_net_amount_min` | 30,000,000 | 大单净买最低金额（元） |
| `daily_change_pct_min` | 3.0 | 日涨幅最低阈值（%） |
| `cooldown_minutes` | 120 | 同一股票冷却时间（2小时） |

#### Signal Detail

```json
{
  "strategy": "momentum_alert",
  "code": "HK09988",
  "detail": {
    "daily_change_pct": 3.5,
    "large_order_buy_count": 5,
    "large_order_net_amount": 45000000,
    "main_net_inflow": 80000000,
    "session_direction": "bullish",
    "session_score": 4
  }
}
```

#### Display Format

```
stealth: "阿里巴巴: momentum confirmed"
display: "HK09988 阿里巴巴 动量确认: 日涨3.5% | 大单净买入4500万(5笔) | 资金流入 | 无背离"
```

#### Key Differences

| | 5 Trackers | momentum_alert |
|---|---|---|
| 数据窗口 | 5-30 分钟滑动窗口 | Session 全天累积 |
| 信号频率 | 事件驱动，可频繁触发 | 高置信度，冷却 2 小时 |
| 通知 | 原始信号不弹通知 | 直接 `notify=true` |
| 评分参与 | 参与 SignalScorer 复合评分 | 不参与复合评分（独立信号） |
| 方向 | 各策略独立推断 | 固定 bullish（5 条件全满足） |

---

### 2.8 Strategy 7: Volume Acceleration Alert (`volume_accel_alert`)

**中文名**: 放量加速

#### Overview

与 `momentum_alert` 依赖大单笔数/金额不同，`volume_accel_alert` **不依赖大单**检测，专门捕捉算法拆单型机构拉升。当机构用算法将大额订单拆成大量小单时，大单检测失效，但成交额的阶梯式放量和 tick 方向的持续偏买仍可被捕捉。

仅对 HK 持仓生效。

#### Data Source

复用 `TickImbalanceTracker` 已维护的 5 分钟滑动窗口，通过 `get_current_stats(code)` 方法获取当前窗口的 `(turnover, imbalance)`，零额外 API 开销。

`VolumeAccelTracker` 每 5 分钟采样一次窗口状态（采样间隔 = 窗口长度，消除重叠），记录到 per-stock deque（maxlen=10），检测成交额加速趋势和 tick 方向持续性。仅在连续交易时段采样，午间休市不采样（避免 stale data 导致虚假加速）。

#### Trigger Conditions (全部满足)

| # | 条件 | 默认阈值 | 数据源 |
|---|------|----------|--------|
| 1 | 成交额加速 | 当前窗口 >= 前 N 窗口均值 × 1.8 | VolumeAccelTracker |
| 2 | tick 持续偏买 | 最近 3 个采样 imbalance 全 > 0.35 | VolumeAccelTracker |
| 3 | 当前窗口成交额 | >= 1000 万 | VolumeAccelTracker |
| 4 | 日涨幅 | > 3% | snapshot prevClose |
| 5 | Session 方向 | score > 0 | SessionAccumulator |
| 6 | 资金净流入 | > 0 | capital_data |

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `accel_ratio` | 1.8 | 成交额加速倍数（当前/前 N 窗口均值） |
| `imbalance_min` | 0.35 | tick imbalance 最低阈值 |
| `consecutive_min` | 3 | 连续偏买窗口数 |
| `min_turnover` | 10,000,000 | 当前窗口最低成交额（过滤低量噪音） |
| `daily_change_pct_min` | 3.0 | 日涨幅最低阈值（%） |
| `cooldown_minutes` | 120 | 同一股票冷却时间（2 小时） |

#### Signal Detail

```json
{
  "strategy": "volume_accel_alert",
  "code": "HK03986",
  "detail": {
    "daily_change_pct": 20.95,
    "accel_ratio": 2.1,
    "curr_turnover": 41270000,
    "avg_prior_turnover": 19650000,
    "curr_imbalance": 0.55,
    "consecutive_above": 3,
    "main_net_inflow": 179000000,
    "capital_inflow": true,
    "session_direction": "bullish",
    "session_score": 4
  }
}
```

#### Display Format

```
stealth: "兆易创新: volume acceleration"
display: "HK03986 兆易创新 放量加速: 日涨20.9% | 成交额加速2.1x(4127万) | tick偏买(imb=+0.55, 连续3窗口) | 资金流入"
```

#### Key Differences from momentum_alert

| | momentum_alert | volume_accel_alert |
|---|---|---|
| 核心依赖 | 大单笔数 + 金额 | 成交额加速 + tick 偏买 |
| 适用场景 | 传统大单拉升 | 算法拆单型机构拉升 |
| 采样间隔 | 每次 poll (~3s) | 5 分钟（无重叠） |
| 数据窗口 | Session 全天累积 | 滑动 10 个采样窗口（50 分钟） |
| 共同条件 | 日涨幅 + session bullish + 资金流入 | 同左 |

---

## 3. Composite Scoring (SignalScorer)

### 3.1 Overview

原始信号静默积累，不弹通知。只有当同一股票在 10 分钟窗口内，多策略共振达到加权阈值时，才产出复合信号（`notify=true` → 弹 macOS 通知）。

### 3.2 Weights

| Strategy | Weight | Rationale |
|----------|--------|-----------|
| `tick_imbalance` | 3 | 学术胜率最高，逐笔级别数据最精细 |
| `large_order` | 2 | 大单含方向信息，主力意图明确 |
| `volume_price_divergence` | 2 | 经典技术分析信号，顶部预警可靠 |
| `capital_flow_spike` | 1 | 资金流汇总数据，噪音偏大 |
| `order_book_imbalance` | 1 | 盘口瞬时数据，噪音最大 |

### 3.3 Trigger Conditions

复合信号触发需同时满足：

1. **加权分 >= `composite_threshold` (5)**
2. **策略类型数 >= `min_strategy_types` (2)** — 防止单策略重复触发凑分

典型触发组合：

| 组合 | 加权分 | 类型数 | 触发? |
|------|--------|--------|-------|
| tick_imbalance(3) + large_order(2) | 5 | 2 | Yes |
| tick_imbalance(3) + volume_price_div(2) | 5 | 2 | Yes |
| large_order(2) + volume_price_div(2) + capital_flow(1) | 5 | 3 | Yes |
| capital_flow(1) + order_book(1) | 2 | 2 | No (score < 5) |
| tick_imbalance(3) x3 | 9 | 1 | No (types < 2) |

### 3.4 Scoring Behavior

- **同策略多次触发**: 权重累加（如 2 次 large_order BUY = 4 分）。策略类型数只算 1。
- **方向独立**: bullish 和 bearish 分别累积，不互抵。
- **窗口**: 10 分钟滑动窗口 (`WINDOW_SEC = 600`)
- **冷却**: 复合信号冷却 1 小时/股 (`COOLDOWN_SEC = 3600`)
- **bearish 优先**: 当 bearish 和 bullish 同时达标时，仅产出 bearish composite

### 3.5 Composite Signal Detail

```json
{
  "strategy": "composite_bearish",
  "code": "HK09988",
  "detail": {
    "signals": ["large_order", "tick_imbalance"],
    "count": 2,
    "score": 5
  }
}
```

---

## 4. Infrastructure

### 4.1 Cooldown Manager

每个策略 + 股票组合独立冷却，防止短时间重复告警：

| Strategy | Cooldown |
|----------|----------|
| `capital_flow_spike` | 15 min |
| `large_order` | 10 min |
| `order_book_imbalance` | 20 min |
| `volume_price_divergence` | 30 min |
| `tick_imbalance` | 10 min |
| `momentum_alert` | 120 min |
| `volume_accel_alert` | 120 min |

### 4.2 Signal Format

所有信号输出为 dual-format event:

```json
{
  "ts": 1707700000000,
  "time": "14:30:00",
  "strategy": "tick_imbalance",
  "code": "HK09988",
  "kind": "l2_strategy",
  "notify": false,
  "message": "阿里巴巴: tick imbalance",
  "display": "HK09988 阿里巴巴 主买主卖失衡: 主买占优 imbalance=+0.523 窗口成交额4500万",
  "detail": { ... }
}
```

| Field | Usage |
|-------|-------|
| `message` | Stealth 格式，macOS 通知标题（不含敏感信息） |
| `display` | 中文详细格式，Web Dashboard 日志展示 |
| `notify` | `true` = 弹通知（composite + momentum_alert）, `false` = 仅写 web 日志 |

### 4.3 Display Examples

| Strategy | Display Format |
|----------|---------------|
| `capital_flow_spike` | `HK09988 阿里巴巴 主力资金异动: 净流入占比 1.5% → 9.2%` |
| `large_order` | `HK09988 阿里巴巴 大单成交: 成交 850万 @ 88.50 (主买)` |
| `order_book_imbalance` | `HK09988 阿里巴巴 盘口异动: 委比 20 → 65 (变化 +45)` |
| `volume_price_divergence` | `HK09988 阿里巴巴 量价背离: 价格 89.50 (窗口最高) 主力净流入 -250万` |
| `tick_imbalance` | `HK09988 阿里巴巴 主买主卖失衡: 主买占优 imbalance=+0.523 窗口成交额4500万` |
| `momentum_alert` | `HK09988 阿里巴巴 动量确认: 日涨3.5% \| 大单净买入4500万(5笔) \| 资金流入 \| 无背离` |
| `volume_accel_alert` | `HK03986 兆易创新 放量加速: 日涨20.9% \| 成交额加速2.1x(4127万) \| tick偏买(imb=+0.55, 连续3窗口) \| 资金流入` |
| `composite_bearish` | `HK09988 阿里巴巴 空头信号(分=5): 大单成交 + 主买主卖失衡` |

### 4.4 File Layout

```
src/
├── tools/
│   ├── l2_strategy_engine.py    # Engine + Trackers + Scorer + Formatter
│   ├── l2_strategy_daemon.py    # Daemon process (3s poll loop)
│   └── stock_notifier.py        # Consumes l2_strategy_signals.json
├── data/
│   ├── l2_strategy_config.json  # Strategy parameters + scoring config
│   └── l2_strategy_signals.json # Runtime signal output (atomic write)
└── utils/
    └── logging_config.py        # Shared logger
```

---

## 5. Configuration Reference

### 5.1 `l2_strategy_config.json`

```json
{
  "enabled": true,
  "strategies": {
    "capital_flow_spike": {
      "enabled": true,
      "window_minutes": 5,
      "low_threshold_pct": 2,
      "high_threshold_pct": 8,
      "cooldown_minutes": 15
    },
    "large_order": {
      "enabled": true,
      "min_amount": 5000000,
      "cooldown_minutes": 10
    },
    "order_book_imbalance": {
      "enabled": true,
      "delta_threshold": 40,
      "cooldown_minutes": 20
    },
    "volume_price_divergence": {
      "enabled": true,
      "lookback_minutes": 30,
      "cooldown_minutes": 30
    },
    "tick_imbalance": {
      "enabled": true,
      "window_minutes": 5,
      "imbalance_threshold": 0.4,
      "min_turnover": 10000000,
      "cooldown_minutes": 10
    },
    "momentum_alert": {
      "enabled": true,
      "large_order_buy_count_min": 3,
      "large_order_net_amount_min": 30000000,
      "daily_change_pct_min": 3.0,
      "cooldown_minutes": 120
    },
    "volume_accel_alert": {
      "enabled": true,
      "accel_ratio": 1.8,
      "imbalance_min": 0.35,
      "consecutive_min": 3,
      "min_turnover": 10000000,
      "daily_change_pct_min": 3.0,
      "cooldown_minutes": 120
    }
  },
  "scoring": {
    "weights": {
      "tick_imbalance": 3,
      "large_order": 2,
      "volume_price_divergence": 2,
      "capital_flow_spike": 1,
      "order_book_imbalance": 1
    },
    "composite_threshold": 5,
    "min_strategy_types": 2
  },
  "max_signals": 200
}
```

### 5.2 Tuning Guide

| Goal | Parameter | Direction |
|------|-----------|-----------|
| 减少噪音 | `composite_threshold` | 调高 (5→7) |
| 更灵敏 | `composite_threshold` | 调低 (5→4) |
| 大单更敏感 | `large_order.min_amount` | 调低 (500万→300万) |
| 大单更严格 | `large_order.min_amount` | 调高 (500万→800万) |
| Tick imbalance 更严格 | `imbalance_threshold` | 调高 (0.4→0.6) |
| Tick imbalance 过滤小盘 | `min_turnover` | 调高 (1000万→2000万) |
| 减少通知频率 | 各策略 `cooldown_minutes` | 调高 |
| 增加策略多样性要求 | `min_strategy_types` | 调高 (2→3) |
| 提升某策略影响力 | `scoring.weights[strategy]` | 调高 |

---

## 6. Known Issues & Optimization Backlog

### 6.1 Current Limitations

| ID | Issue | Severity | Description |
|----|-------|----------|-------------|
| L1 | Sequence 假设有序 | Low | `LargeOrderTracker` 和 `TickImbalanceTracker` 假设 Futu 返回 tick 按 sequence 升序，乱序 tick 会被跳过 |
| L2 | 同策略权重累加 | Medium | 同一策略在窗口内多次触发会累加权重（如 2 次 large_order = 4 分），可能导致单一信号源主导评分 |
| L3 | Bearish 优先 | Low | bearish/bullish 同时达标时仅产出 bearish，可能遗漏多头信号 |
| L4 | Tracker 连续触发 | Low | `TickImbalanceTracker` 阈值一旦超过会每次 poll 都触发，依赖 CooldownManager 抑制 |
| L5 | Order book 瞬时对比 | Medium | 仅比较两次快照的委比差值，没有滑动窗口平滑 |

### 6.2 Optimization Ideas

| Priority | Idea | Expected Impact | Status |
|----------|------|-----------------|--------|
| P0 | 每策略权重最多贡献一次（cap per strategy type） | 消除 L2，评分更反映信号多样性 | |
| P1 | Order book 改为滑动窗口均值对比 | 降低 L5 噪音，提升 order_book 权重可信度 | |
| P1 | 加入 VWAP 偏离策略（价格偏离 VWAP > N%） | 新信号源，增强复合研判维度 | |
| P2 | Tick imbalance 动态阈值（根据历史波动率调整） | 适应不同波动率环境 | |
| ~~P2~~ | ~~加入成交量加速检测（Volume Acceleration）~~ | ~~捕捉放量信号~~ | Done → `volume_accel_alert` |
| P2 | vol_price_div 动态阈值: `max(-100万, -日均成交额×2%)` | 消除小盘股假背离噪音（美图/布鲁可/众安每天刷5条） | |
| P3 | Backtest framework for L2 signals | 量化各策略历史胜率 | |
| P3 | 引入 regime filter（趋势/震荡市区分） | 不同市场环境使用不同权重 | |

### 6.3 Planned Strategies (基于 2025-02-12 信号数据分析)

> 以下 3 个策略由 quant-engineer agent 从当日 200 条信号中发现的模式提出。
> 按实施优先级排序。

#### Strategy 8: Tick 方向一致性异常 (`tick_persistence`) — P0

**中文名**: 主买持续
**决策价值**: 捕捉"低涨幅静默吸筹" — 机构不拉升价格但持续买入（`volume_accel_alert` 因日涨幅门槛 3% 会漏掉）

**模式来源**: HK03896 武岳峰 tick 正向率 93%（14/15 窗口偏买，持续 2 小时），日涨仅 0.75%。HK03986 兆易创新 tick 正向率 92% + 盘口 7 次震荡掩护。

**触发条件**:
```
session 内已触发的 tick_imbalance 信号数 >= min_tick_signals (8)
AND 同方向信号占比 >= persistence_ratio (0.80)
AND 最近 recent_consistent (3) 个 tick 信号与主方向一致
```

**参数**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_tick_signals` | 8 | 最少 tick 信号数（确保样本量） |
| `persistence_ratio` | 0.80 | 同方向占比阈值 |
| `recent_consistent` | 3 | 最近 N 个信号须一致（排除尾部反转） |
| `cooldown_minutes` | 120 | 冷却 |

**实现要点**:
- 新增 `TickPersistenceTracker` 类，feed 来自 tick_imbalance 信号（冷却后）
- 只统计方向，不需要新 API 调用
- `notify=true`，session 级别高置信度信号
- 方向 = `dominant_direction`

**Display**: `HK03896 武岳峰 主买持续: tick正向率93%(14/15窗口) | 平均imb=+0.58 | 最近3窗口一致`

**验证数据** (2025-02-12):

| 股票 | tick 信号数 | 正向率 | 最近3一致 | 触发? |
|------|-----------|--------|----------|-------|
| HK03896 武岳峰 | 15 | 93% | YES | **YES** |
| HK03986 兆易创新 | 12 | 92% | YES | **YES** |
| HK06809 澜起科技 | 7 | 100% | YES | NO (信号数<8) |
| HK01810 小米 | 10 | 40% | NO | NO |
| HK01211 比亚迪 | 10 | 60% | NO | NO |

---

#### Strategy 9: 大单方向翻转 (`large_order_reversal`) — P1

**中文名**: 大单翻转
**决策价值**: 为 `composite_bullish` 提供"修正信号" — 当大单方向在 session 内从持续买入翻转为持续卖出，发出 bearish 警告

**模式来源**: HK09988 阿里巴巴 14:28-14:44 连续 2 笔 BUY（1517万），14:45 触发 composite_bullish。但 14:55 起连续 5 笔 SELL（3343万），方向完全翻转。现有系统无修正机制。

**触发条件**:
```
session 内所有 large_order 按时间排序
prior_orders (前序) 笔数 >= min_prior_count (2)
AND recent_orders (最近窗口) 笔数 >= min_recent_count (3)
AND prior 净方向 != recent 净方向 (方向翻转)
AND |recent 净额| >= min_net_amount (10,000,000)
```

**参数**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_prior_count` | 2 | 前序最少大单数 |
| `min_recent_count` | 3 | 最近窗口最少大单数 |
| `min_net_amount` | 10,000,000 | 最近窗口净额绝对值最低阈值 |
| `cooldown_minutes` | 120 | 冷却 |

**实现要点**:
- 复用 `SessionAccumulator` 的 large_order 累积数据，加时间序列排序
- 翻转方向 = recent 净方向（BUY→SELL = bearish, SELL→BUY = bullish）
- `notify=true`，高优先级警告信号

**Display**: `HK09988 阿里巴巴 大单翻转: BUY→SELL | 前序2笔净买1517万 → 最近5笔净卖3343万`

**验证数据** (2025-02-12):

| 股票 | Prior | Recent | 翻转? | 触发? |
|------|-------|--------|-------|-------|
| HK09988 阿里巴巴 | 2笔BUY 1517万 | 5笔SELL -3343万 | BUY→SELL | **YES** |
| HK00700 腾讯 | 全天偏BUY | 全天偏BUY | 无翻转 | NO |
| HK01810 小米 | 交替出现 | 无一致方向 | N/A | NO |

---

#### Strategy 10: 尾盘异动 (`closing_surge`) — P1

**中文名**: 尾盘异动
**决策价值**: 检测最后 30 分钟信号密度暴增 — 预测次日开盘方向（HK T+0 可当日交易）

**模式来源**: HK00700 腾讯 15:30-16:00 有 12 条信号（盘中每 30min 平均 3.3 条，密度比 3.6x）。HK01357 美图全天安静，15:57 突发 3 信号（大单+tick+composite）。

**触发条件**:
```
时间在 closing_start_time (15:30) - 16:00 HKT
AND 最近 30 分钟该股 raw_signal 数量 >= closing_signal_count_min (5)
AND 尾盘信号数 / 盘中每 30min 平均信号数 >= closing_density_ratio (2.5)
AND 尾盘窗口内包含 large_order 信号
```

**参数**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `closing_window_minutes` | 30 | 尾盘窗口长度 |
| `closing_start_time` | "15:30" | 尾盘起始时间 (HKT) |
| `closing_signal_count_min` | 5 | 尾盘最少信号数 |
| `closing_density_ratio` | 2.5 | 尾盘/盘中信号密度比 |
| `cooldown_minutes` | 60 | 冷却 |

**实现要点**:
- 需要在引擎层面维护 per-stock 信号计数 + 时间分布
- 方向 = 尾盘 large_order BUY/SELL 比例推断
- `notify=true`，为次日开盘决策提供参考

**Display**: `HK00700 腾讯 尾盘异动: 信号密度3.6x(12条/30min vs 盘中3.3条) | 大单BUY 5笔 SELL 1笔 → bullish`

**验证数据** (2025-02-12):

| 股票 | 尾盘信号 | 盘中均值 | 密度比 | 含大单 | 触发? |
|------|---------|---------|--------|--------|-------|
| HK00700 腾讯 | 12 | 3.3 | 3.6x | YES | **YES** |
| HK01810 小米 | 9 | 3.0 | 3.0x | YES | **YES** |
| HK01357 美图 | 4 | 1.0 | 4.0x | YES | NO (信号数<5) |
| HK03986 兆易 | 5 | 2.8 | 1.8x | YES | NO (密度比<2.5) |

---

## 7. Development Guide

### 7.1 Adding a New Strategy

1. **创建 Tracker 类** in `l2_strategy_engine.py`:
   - 实现 `update()` → `Optional[dict]` 和 `reset()`
   - 返回 `{"strategy": "name", "code": "...", "detail": {...}}`
   - 需要 warmup 的加 `_warmed_up` pattern

2. **注册方向推断** in `_infer_direction()`:
   ```python
   elif strategy == "new_strategy":
       return "bullish" if condition else "bearish"
   ```

3. **添加权重** in `SIGNAL_WEIGHTS` + `l2_strategy_config.json`

4. **添加名称映射** in `_STRATEGY_NAMES` + `_STRATEGY_STEALTH`

5. **添加 display 格式** in `format_signal()`

6. **初始化 Tracker** in `_init_trackers()`

7. **集成到 poll_once()**: 加 `if enabled` 判断 + 调用 tracker

8. **加入 reset_daily()**: `self._new_tracker.reset()`

9. **更新 subscription** (if needed): 在 `_subscribe()` 添加订阅逻辑

10. **更新 config**: `l2_strategy_config.json` 添加策略参数

### 7.2 Testing

```bash
# Syntax check
python3 -m py_compile src/tools/l2_strategy_engine.py

# Unit test imports
poetry run python3 -c "
from src.tools.l2_strategy_engine import (
    CapitalFlowTracker, LargeOrderTracker, OrderBookTracker,
    DivergenceTracker, TickImbalanceTracker,
    SignalScorer, _infer_direction, SIGNAL_WEIGHTS
)
print('All imports OK')
"

# Restart daemon
pkill -f l2_strategy_daemon
nohup poetry run python src/tools/l2_strategy_daemon.py >> logs/l2_daemon_out.log 2>&1 &
sleep 10 && tail -20 logs/l2_daemon_out.log
```

### 7.3 Key Invariants

- **Tracker 只产出原始信号**，不决定是否弹通知
- **CooldownManager 负责去重**，每个 `(strategy, code)` 独立冷却
- **SignalScorer 负责复合研判**，只有 composite 信号 `notify=true`
- **format_signal 负责格式化**，输出 dual-format (message + display)
- **Daemon 不依赖 Engine 内部状态**，poll_once() 是唯一接口
- **ticker_data 共享**: `large_order` 和 `tick_imbalance` 共用 `_fetch_rt_tickers()` 返回数据
- **Daily reset 清除一切**: 午夜重置所有 Tracker + Cooldown + Scorer 状态
