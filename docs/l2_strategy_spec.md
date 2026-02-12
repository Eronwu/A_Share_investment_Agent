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
│  │ CooldownManager → 去重                            │  │
│  │ SignalScorer    → 加权复合研判                     │  │
│  └──────────────┬────────────────────────────────────┘  │
│                 │ formatted signals                      │
│  ┌──────────────▼────────────────────────────────────┐  │
│  │ format_signal() → dual-format events               │  │
│  │   notify=false → web 日志                          │  │
│  │   notify=true  → 弹通知 (composite only)           │  │
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
| `notify` | `true` = 弹通知（仅 composite）, `false` = 仅写 web 日志 |

### 4.3 Display Examples

| Strategy | Display Format |
|----------|---------------|
| `capital_flow_spike` | `HK09988 阿里巴巴 主力资金异动: 净流入占比 1.5% → 9.2%` |
| `large_order` | `HK09988 阿里巴巴 大单成交: 成交 850万 @ 88.50 (主买)` |
| `order_book_imbalance` | `HK09988 阿里巴巴 盘口异动: 委比 20 → 65 (变化 +45)` |
| `volume_price_divergence` | `HK09988 阿里巴巴 量价背离: 价格 89.50 (窗口最高) 主力净流入 -250万` |
| `tick_imbalance` | `HK09988 阿里巴巴 主买主卖失衡: 主买占优 imbalance=+0.523 窗口成交额4500万` |
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

| Priority | Idea | Expected Impact |
|----------|------|-----------------|
| P0 | 每策略权重最多贡献一次（cap per strategy type） | 消除 L2，评分更反映信号多样性 |
| P1 | Order book 改为滑动窗口均值对比 | 降低 L5 噪音，提升 order_book 权重可信度 |
| P1 | 加入 VWAP 偏离策略（价格偏离 VWAP > N%） | 新信号源，增强复合研判维度 |
| P2 | Tick imbalance 动态阈值（根据历史波动率调整） | 适应不同波动率环境 |
| P2 | 加入成交量加速检测（Volume Acceleration） | 捕捉放量信号 |
| P3 | Backtest framework for L2 signals | 量化各策略历史胜率 |
| P3 | 引入 regime filter（趋势/震荡市区分） | 不同市场环境使用不同权重 |

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
