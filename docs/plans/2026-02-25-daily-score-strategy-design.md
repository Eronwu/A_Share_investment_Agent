# 策略 v2：日线评分驱动型交易

## 背景

v1 策略（信号驱动型）在实盘验证中暴露严重问题：
- 12 笔交易，手续费 1,129 HKD，净亏 849 HKD — **手续费占比 32.6%**
- 腾讯被交易 6 次，持有仅 14-26 分钟即被 T3:large_order_reversal 平仓
- 全部 12 笔平仓原因都是 large_order_reversal — 买了就被平，平了又买
- 根因：L2 秒级信号太频繁，缺少日线级别的方向判断

## 核心思路变化

```
旧: L2信号 → 立刻交易（秒级反应，日内反复进出）
新: 日线分析 → 综合评分 → 等待入场 → 设SL/TP → 持有到目标
```

## Futu API 约束

- 账户余额 2 万 HKD → 300 订阅额度（13 只 × 3 种 ≈ 39，充足）
- `request_history_kline(K_DAY)` 120 天日K，每 30 分钟刷新（已实现）
- 所有日K指标数据已在 `DailyIndicatorTracker._ind` 中缓存
- **不需要额外 API 调用**

## 详细设计

### 1. DailyScorer — 综合评分引擎

在 `DailyIndicatorTracker` 基础上新增 `score()` 方法，评分 0-100：

| 维度 | 权重 | 评分逻辑 |
|------|------|---------|
| MACD 趋势 | 20 | DIF>DEA +10, MACD柱连续放大 +10, 死叉 -15 |
| RSI 位置 | 15 | 30-50 回升 +15, 50-65 健康 +10, >75 超买 -10, <25 超卖回升 +5 |
| 均线排列 | 20 | MA5>10>20 多头 +15, 价格在MA20上方 +5, 空头排列 -15 |
| 主力资金 | 20 | 近期净流入>0 +10, 今日流入占比>3% +10, 大幅流出 -15 |
| 量价配合 | 15 | 量升价升 +15, 缩量回调 +10, 放量下跌 -10 |
| 支撑位距离 | 10 | 接近支撑 +10, 高位远离支撑 +0, 跌破支撑 -10 |

返回值：
```python
{
    "total": 75,            # 总分
    "macd": 15,             # 分项得分
    "rsi": 10,
    "ma": 20,
    "capital_flow": 15,
    "volume_price": 10,
    "support": 5,
    "action": "BUY",        # BUY / HOLD / SELL / AVOID
    "stop_loss": 34.50,     # 建议止损位
    "take_profit": 38.80,   # 建议止盈位
    "reason": "MACD金叉+均线多头+主力流入" # 评分理由
}
```

### 2. 开仓规则

- 日线评分 >= **70** → 加入候选池
- 入场窗口 **10:00-10:30**（开盘 30 分钟后，初始波动平息）
- 每天最多开 **1 只**新仓（2 万 HKD 本金，不宜分散）
- 仓位：总资金的 **20-30%**
- 止损：`max(支撑位下方, 价格 - 2×ATR)`，取严格者
- 止盈：`min(阻力位, 价格 + 3×ATR)`
- 最大持有 **10 天**

### 3. 持仓管理

- 止损/止盈：**全天实时检查**（风控不松）
- T3 纠偏信号（large_order_reversal 等）：仅当 **浮亏 > 来回手续费** 时执行
  - 来回手续费 ≈ 持仓市值 × 0.3%（佣金+印花税双向）
  - 例：5000 HKD 持仓 → 手续费约 15 HKD → 浮亏 < 15 不平仓
- 每日 **15:30** 收盘评估：日线评分跌破 **40** → 次日开盘平仓
- 被平仓股票 **120 分钟**内不得重新开仓

### 4. 日内例外

仅当同时满足以下条件才允许日内操作：
- L2 实时信号 confidence >= **0.85**（极强信号）
- 日线评分 >= **60**
- 当天该股票尚未被交易过

实际上这相当于 composite_bullish + tick_persistence + large_order 三重叠加。

### 5. signal_rules.json 配置新增

```json
{
  "daily_score": {
    "entry_threshold": 70,
    "exit_threshold": 40,
    "intraday_exception_confidence": 0.85,
    "intraday_exception_min_score": 60,
    "entry_window_start": "10:00",
    "entry_window_end": "10:30",
    "exit_review_time": "15:30",
    "max_new_positions_per_day": 1,
    "reentry_cooldown_minutes": 120,
    "min_hold_minutes": 30,
    "max_hold_days": 10,
    "position_pct": 0.25,
    "min_profit_after_cost_pct": 0.003
  },
  "scoring_weights": {
    "macd": 20,
    "rsi": 15,
    "ma": 20,
    "capital_flow": 20,
    "volume_price": 15,
    "support": 10
  }
}
```

## 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/data/signal_rules.json` | 修改 | 新增 `daily_score` + `scoring_weights` 配置 |
| `src/tools/l2_strategy_engine.py` | 修改 | `DailyIndicatorTracker` 新增 `score()` 方法 |
| `src/sim_trading/realtime_engine.py` | 重构 | 改为评分驱动：时间窗口、冷却期、手续费过滤 |
| `src/sim_trading/signal_mapper.py` | 修改 | T3 平仓加手续费阈值检查 |
| `src/sim_trading/db.py` | 修改 | `live_state` 表新增 `daily_score` 字段 |
| `web/app/api/sim/route.ts` | 修改 | 返回 daily_score |
| `web/app/sim/page.tsx` | 修改 | 持仓表显示日线评分 |

## 实施步骤

### Step 1: 配置 + DailyScorer
1. `signal_rules.json` 新增配置段
2. `l2_strategy_engine.py` 的 `DailyIndicatorTracker` 新增 `score(code, main_net_inflow)` 方法
3. 单元测试验证评分逻辑

### Step 2: RT Engine 重构
1. `realtime_engine.py` 重构 `tick()` 和 `_process_signal()`
2. 加入时间窗口判断 + 评分门槛 + 冷却追踪
3. T3 平仓加手续费过滤
4. `live_state` 表增加 `daily_score` 字段

### Step 3: Web 展示
1. API 返回 daily_score
2. 持仓表增加"评分"列
3. 操作记录增加评分信息

### Step 4: 验证
1. 清空 live_state + live trades
2. 重启 daemon，观察日志
3. 验证：不应频繁交易，每天最多 1-2 笔
4. 对比 v1 的手续费占比

## 预期效果

| 指标 | v1 (当前) | v2 (目标) |
|------|----------|----------|
| 日均交易次数 | 12 笔 | 1-2 笔 |
| 手续费占比 | 32.6% | <5% |
| 平均持有时间 | 14-26 分钟 | 3-10 天 |
| 开仓依据 | L2 秒级信号 | 日线综合评分 ≥70 |
| 平仓依据 | T3 大单翻转 | 止损/止盈/评分<40 |
