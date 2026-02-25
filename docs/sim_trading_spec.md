# Simulated Trading System Specification

> Version: 2.0 | Updated: 2025-02-25
> Source: `src/sim_trading/`
> Database: `src/data/sim_trading.db`

## 1. System Overview

日线评分驱动型模拟交易系统（v2），用 DailyIndicatorTracker 的综合评分替代 v1 的 L2 秒级信号驱动入场。

### 1.1 v1→v2 改进背景

v1 在实盘中暴露严重问题：
- 17 笔交易全部被 T3:large_order_reversal 反复平仓
- 手续费 1,406 HKD，占 |PnL| 的 **171%**
- 6/17 笔毛利为正但扣费后亏损（手续费杀利润）
- 59% 持仓 < 30 分钟，中位仅 26 分钟
- 同一股票 1 天内反复开平（腾讯被开平 8 次）

### 1.2 Architecture (v2)

```
┌─────────────────────────────────────────────────────────────────────┐
│                 L2 Simulation Trading System v2                      │
│                                                                      │
│  Layer 0: Data Collection                                            │
│  ┌─────────────────────┐  ┌─────────────────────┐                  │
│  │ l2_strategy_engine   │  │ market_data_poller   │                  │
│  │ → L2 signals         │  │ → market_data.json   │                  │
│  │ → DailyIndicatorTracker (日K指标 + 评分)       │                  │
│  └────────┬─────────────┘  └────────┬─────────────┘                  │
│           └──────────┬──────────────┘                                 │
│                      ▼                                                │
│  ┌──────────────────────────────────┐                                │
│  │  signal_archiver.py              │ → sim_trading.db               │
│  │  (归档信号 + 采样价格快照)        │   (SQLite)                     │
│  └──────────────────────────────────┘                                │
│                      │                                                │
│  Layer 1: Daily Score Evaluation (v2 核心)                           │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │  RealtimeSimEngine v2                                     │       │
│  │  ┌─────────────────┐  ┌───────────────────────────────┐  │       │
│  │  │ DailyScorer     │  │ Time Window Control            │  │       │
│  │  │ score() 0-100   │  │ entry: 10:00-10:30            │  │       │
│  │  │ 6 维度加权评分   │  │ exit:  15:30                  │  │       │
│  │  └─────────────────┘  │ cooldown: 120min              │  │       │
│  │                        │ max: 1 new/day                │  │       │
│  │  ┌─────────────────┐  └───────────────────────────────┘  │       │
│  │  │ T3 Cost Filter  │  ← min_hold 30min + |pnl|>0.5%     │       │
│  │  │ + Intraday      │  ← conf≥0.85 & score≥60 例外入场    │       │
│  │  │   Exception     │                                      │       │
│  │  └─────────────────┘                                      │       │
│  └──────────────────────────────────────────────────────────┘       │
│                      │                                                │
│  Layer 2: Execution                                                  │
│  ┌────────────────────┐  ┌──────────────────┐                       │
│  │  SimulationEngine   │  │  PositionManager  │                       │
│  │  (成本/滑点/执行)   │  │  (持仓/止损/止盈) │                       │
│  └────────────────────┘  └──────────────────┘                       │
│                      │                                                │
│  Layer 3: Analytics & Optimization                                   │
│  ┌────────────────────┐  ┌──────────────────┐                       │
│  │  TradeAnalyzer      │  │  ParameterOptimizer│                      │
│  │  (胜率/Sharpe/归因) │  │  (Optuna贝叶斯)   │                      │
│  └────────────────────┘  └──────────────────┘                       │
│                                    │                                  │
│                                    ▼                                  │
│                      signal_rules.json (参数回写)                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 Design Principles

- **日线驱动**: 开仓/平仓由日 K 线综合评分决定，不再被 L2 秒级信号反复触发
- **成本优先**: 所有交易决策先过手续费过滤，不值得的交易不做
- **时间窗口**: 固定入场/退出时间，减少噪音交易
- **纯消费者**: 不修改任何现有数据生产者（l2_daemon, poller, notifier）
- **模块解耦**: `src/sim_trading/` 与 `src/tools/` 分离
- **防过拟合**: Walk-Forward OOS 验证，参数正则化

## 2. Daily Score System (v2 核心)

### 2.1 评分维度

`DailyIndicatorTracker.score()` 返回 0-100 分，6 个子维度加权：

| 维度 | 权重 | 满分 | 计算逻辑 |
|------|------|------|---------|
| **MACD** | 20% | 20 | 金叉=20, hist扩张=18, hist正但收缩=14, 死叉=0, DIF/DEA零轴上方+2 |
| **RSI** | 15% | 15 | 60-70=15, 50-60=10, 70-80=10, 40-50=7, >80=3(极度超买), <30=2 |
| **MA排列** | 20% | 20 | 多头排列(ma5>10>20>60)+站上MA20=20, 空头排列=0 |
| **主力资金** | 20% | 20 | 净流入>10%=20, >5%=16, >0=12, 无数据=8, <-10%=0 |
| **量价配合** | 15% | 15 | 放量上涨=15, 温和放量=12, 缩量上涨=6, 放量下跌=2 |
| **支撑位** | 10% | 10 | 距支撑<2%=10, <5%=8, <10%=5, >10%=3 |

**注意**: 无 L2 主力资金数据时，`capital_flow` 固定 = 8。此时理论最高分 = 88，BUY 阈值 70 仍可达。

### 2.2 评分驱动的交易决策

| 评分区间 | 动作 | 说明 |
|---------|------|------|
| >= 70 | **BUY** | 入场窗口内开仓 |
| 40-69 | **HOLD** | 持有不动 |
| < 40 | **SELL** | 收盘评估时平仓 |
| = 0 (WAIT) | **跳过** | 无 kline 数据，不做任何操作（安全兜底） |

### 2.3 止损/止盈计算

由 `score()` 方法基于日 K 数据计算：
- `stop_loss = max(support - ATR×0.5, close - ATR×2)`
- `take_profit = min(resistance, close + ATR×3)`
- `support = max(30日最低价, MA60)`
- `resistance = 30日最高价`
- `ATR = 14期 True Range EWM`

## 3. RealtimeSimEngine v2

### 3.1 Tick 流程（每 3 秒）

```
tick()
 ├── 日切检测 → daily_reset()
 ├── 清空 per-tick score 缓存
 ├── 读取 market_data.json 行情
 │
 ├── [10:00-10:30] _evaluate_entries()
 │    ├── 遍历 watchlist HK holdings
 │    ├── 跳过已持有 / 冷却中的股票
 │    ├── score() >= entry_threshold(70) → 候选
 │    ├── 按评分排序，开最高分的（每天最多 1 只）
 │    └── 检查 min_notional(30000)，执行开仓
 │
 ├── [全天] 消费新信号 _process_signal_v2()
 │    ├── T3 纠偏 → _handle_t3_with_filters()
 │    │    ├── min_hold 30min 检查
 │    │    ├── |pnl%| > 0.5% 手续费过滤
 │    │    └── 盈利时只收窄止损，不卖出
 │    └── T1 极强信号 → _handle_intraday_exception()
 │         └── conf >= 0.85 且 score >= 60 才入场
 │
 ├── [15:30] _evaluate_exits()
 │    ├── score=0(WAIT) → 跳过（不误平仓）
 │    └── score < exit_threshold(40) → 平仓
 │
 ├── [始终] check_exits() — 止损/止盈/max_hold
 │
 └── _persist_state() → live_state 表（含 daily_score）
```

### 3.2 配置参数 (`signal_rules.json → daily_score`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `entry_threshold` | 70 | 开仓最低评分 |
| `exit_threshold` | 40 | 收盘平仓评分线 |
| `intraday_exception_confidence` | 0.85 | 日内例外入场最低置信度 |
| `intraday_exception_min_score` | 60 | 日内例外入场最低评分 |
| `entry_window_start` | "10:00" | 入场窗口开始 |
| `entry_window_end` | "10:30" | 入场窗口结束 |
| `exit_review_time` | "15:30" | 收盘评估时间 |
| `max_new_positions_per_day` | 1 | 每日最多新开仓数 |
| `reentry_cooldown_minutes` | 120 | 平仓后再入场冷却（分钟） |
| `min_hold_minutes` | 30 | T3 平仓最短持有时间 |
| `max_hold_days` | 10 | 最长持仓天数 |
| `position_pct` | 0.25 | 每笔仓位占可用资金比例 |
| `min_profit_after_cost_pct` | 0.005 | T3 平仓最低盈亏幅度（覆盖 round-trip 手续费） |
| `min_notional` | 30000 | 最小交易金额 HKD（避免小仓位手续费率过高） |

## 4. Signal → Trade Mapping (v1 层，v2 仍保留)

### 4.1 信号分层

| Tier | 信号 | 动作 | 基础置信度 | v2 处理方式 |
|------|------|------|-----------|------------|
| **1 独立触发** | `momentum_alert` | BUY | 0.70 | 仅日内例外路径（conf>=0.85） |
| | `volume_accel_alert` | BUY | 0.65 | 仅日内例外路径 |
| | `composite_bullish` | BUY | 0.60 | 仅日内例外路径 |
| | `momentum_sell_alert` | SELL | 0.70 | 不再直接触发平仓 |
| **2 增强叠加** | `tick_persistence` | +0.15 | — | 同 v1 |
| | `large_order` (>1000万) | +0.10 | — | 同 v1 |
| **3 纠偏** | `large_order_reversal` | SELL 50% | 0.60 | **加 min_hold + cost filter** |
| | `volume_price_divergence` | TIGHTEN_SL | — | 同 v1 |
| | `closing_surge` (bearish) | SELL_NEXT_OPEN | 0.55 | 同 v1 |
| **4 仅记录** | tick_imbalance 等 11 种 | LOG | — | 同 v1 |

### 4.2 v2 对 T3 纠偏的过滤

v1 中 T3:large_order_reversal 是最大问题源。v2 在 RT engine 层（非 mapper 层）加了三重过滤：

1. **min_hold**: 持有 < 30 分钟 → 不卖
2. **cost filter**: |pnl%| < 0.5% → 不卖（亏损不值手续费）
3. **盈利保护**: pnl > 0 时 T3 只收窄止损，不直接卖出

### 4.3 冲突消解 & 风控

- 同 v1: 同股同 session 多空冲突取消、最多 3 笔信号/session、15 分钟最小间隔
- `risk_control`: min_confidence=0.60, max_single_stock=25%, max_total_invested=80%

## 5. Simulation Engine

### 5.1 HK 交易成本模型

| 费项 | 费率 | 备注 |
|------|------|------|
| 佣金 | 0.03% | min HKD 3 |
| 印花税 | 0.13% | 买卖双向，四舍五入到 HKD 1 |
| 交易费 | 0.00565% | 交易所 |
| 结算费 | 0.002% | min HKD 2, max HKD 100 |

**Round-trip 总成本约 0.34-0.53%**（视金额而定）。小仓位（<1 万 HKD）因最低收费，round-trip 可达 1%+。

### 5.2 滑点模型

| 日成交额 | 滑点 |
|---------|------|
| >10亿 | 0.05% |
| >1亿 | 0.10% |
| >1000万 | 0.20% |
| <1000万 | 0.50% |

### 5.3 资金模型

- 初始资金: 100万 HKD (可配置)
- Lot-size 对齐: 腾讯/阿里 100, 小米 200, 比亚迪 500 等
- ATR: 优先用 DailyIndicatorTracker 的 14 期 ATR，fallback 到 price_snapshots 估算

## 6. Daemon 架构

```
l2_strategy_daemon.py
 ├── L2StrategyEngine(l2_config, watchlist)
 │    ├── poll_once() → L2 signals
 │    └── _daily_indicators: DailyIndicatorTracker
 │         ├── update(code, ctx) → 刷新日K + 检测信号
 │         ├── score(code) → 0-100 综合评分
 │         └── get_atr(code) → 14期 ATR
 │
 ├── SignalArchiver → signals + price_snapshots 归档
 │
 └── RealtimeSimEngine(rules, daily_tracker=engine._daily_indicators)
      ├── tick() → 3s 轮询
      ├── _get_score(code) → per-tick 缓存
      ├── _evaluate_entries() → 10:00-10:30 入场评估
      ├── _process_signal_v2() → T3 过滤 + 日内例外
      ├── _evaluate_exits() → 15:30 收盘评估
      └── _persist_state() → live_state 表
```

**日志**: RT engine logger 名 `l2_daemon.rt_sim`，继承 daemon handler，写入 `logs/l2_daemon.log`。

**重启命令**:
```bash
pkill -f l2_strategy_daemon
nohup poetry run python src/tools/l2_strategy_daemon.py >> logs/l2_daemon_out.log 2>&1 &
tail -f logs/l2_daemon.log | grep rt_sim   # 观察 v2 RT 日志
```

## 7. Analytics

### 7.1 核心指标

| 指标 | 公式 |
|------|------|
| 胜率 | 盈利笔数 / 总笔数 |
| 盈亏比 | 总盈利 / 总亏损 |
| Sharpe | (mean_daily_return - rf) / std × √252 |
| 最大回撤 | max(peak - trough) / peak |
| Calmar | annualized_return / max_drawdown |

### 7.2 v2 关注指标

| 指标 | v1 实际 | v2 目标 |
|------|--------|---------|
| 日均交易 | 17 笔 | 0-2 笔 |
| 手续费占比 | 171% | < 5% |
| 持有时间 | 26 分钟 (中位) | 3-10 天 |
| 开仓依据 | L2 秒级信号 | 日线评分 >= 70 |
| 平仓依据 | T3 大单翻转 | 止损/止盈/评分 < 40 |
| 手续费杀利润 | 35% (6/17) | 0% |

### 7.3 信号归因分析

- Per-strategy: 胜率、平均盈亏、贡献度%
- Per-stock: 每只股票的 PnL 和交易次数
- Per-exit-reason: 止损/止盈/评分退出/T3 退出各占比
- Fee analysis: 每笔交易 notional vs fee vs net PnL

## 8. Data Storage (SQLite)

### 8.1 Tables

```sql
-- 历史信号归档 (180 天保留)
signals (ts, date, time, strategy, code, direction, notify, detail, display,
         price_at_signal, daily_change_pct)

-- 价格快照 30s 粒度 (180 天保留)
price_snapshots (ts, date, code, price, volume, amount, change_pct)

-- Session 上下文快照 5min (180 天保留)
session_snapshots (ts, date, code, session_json)

-- 模拟交易 (永久保留)
trades (trade_id, param_version, code, action, direction,
        entry_price, exit_price, quantity, entry_time, exit_time,
        entry_date, exit_date, hold_days, pnl, pnl_pct,
        commission, total_cost, confidence, trigger_signals,
        exit_reason, notes)

-- 每日权益快照 (永久保留)
daily_pnl (date, param_version, total_equity, cash, invested,
           daily_return, cumulative_return, drawdown_pct, positions_json)

-- 实时持仓状态 (RT engine UPSERT, web API 只读)
live_state (code PK, entry_price, quantity, current_price,
            entry_time, entry_date, stop_loss, take_profit,
            max_hold_days, entry_strategy, confidence,
            trigger_signals, unrealized_pnl, pnl_pct,
            daily_score, last_updated)

-- 参数版本
param_versions (version, config_json, trade_rules_json,
                train_sharpe, test_sharpe, train_win_rate,
                test_win_rate, is_active)

-- 告警事件 (30 天保留)
alert_events (ts, date, time, symbol, kind, level, message, display, change_pct)
```

### 8.2 存储估算

| 表 | 日增量 | 30天 |
|----|--------|------|
| signals | ~200 行 | ~6K 行 |
| price_snapshots | ~3900 行 | ~117K 行 |
| trades (v2) | ~0-2 行 | ~30-60 行 |
| daily_pnl | 1 行 | 30 行 |
| live_state | 0-5 行 (UPSERT) | — |

总计约 10MB/月，SQLite 完全够用。

## 9. File Layout

```
src/
├── sim_trading/
│   ├── __init__.py
│   ├── signal_archiver.py           # Layer 0: 信号归档
│   ├── realtime_engine.py           # Layer 1: v2 实时引擎 (评分驱动)
│   ├── signal_mapper.py             # Layer 1: 信号→交易 (v1 4层规则)
│   ├── simulation_engine.py         # Layer 2: 模拟执行 (成本/滑点)
│   ├── position_manager.py          # Layer 2: 持仓管理 (SL/TP/max-hold)
│   ├── trade_analyzer.py            # Layer 3: 交易分析
│   ├── db.py                        # SQLite schema + migration
│   ├── replay_runner.py             # 历史回放入口
│   └── test_sim_trading.py          # 54 tests
├── tools/
│   ├── l2_strategy_engine.py        # DailyIndicatorTracker.score() + get_atr()
│   └── l2_strategy_daemon.py        # Daemon: 传 daily_tracker 给 RT engine
├── data/
│   ├── sim_trading.db               # SQLite 数据库
│   └── signal_rules.json            # 交易规则 + daily_score 配置 + scoring_weights
```

## 10. Web Integration

### 10.1 /api/sim 路由

- `better-sqlite3` 只读打开 `sim_trading.db`
- `SELECT * FROM live_state` 自动包含 `daily_score`
- 回测和实时数据分离：`param_version='live'` vs 其他版本

### 10.2 /sim 页面

- 实时持仓表: 类型/代码/名称/**评分**/现价/涨跌幅/成本/盈亏%/市值/浮盈/止损/距止损/止盈
- 评分列着色: >= 70 绿 (BUY), 40-69 橙 (HOLD), < 40 红 (SELL), 0 显示 "-"
- 摘要栏: 收益率/夏普/胜率/回撤/盈亏比/总市值/总资产/可用/交易笔数/手续费
- 操作记录: BUY/SELL 逐笔时间线
- 已完成交易: 完整闭环交易表
- 策略归因 + 股票归因

## 11. Parameter Optimization

### 11.1 方法: Optuna 贝叶斯优化

- TPE sampler，50-200 次试验
- Walk-Forward OOS: 70% train / 30% test
- Early pruning: train 胜率 <40% 或回撤 >15% 直接淘汰
- Test 集 Sharpe 需 ≥ train 集的 70%

### 11.2 v2 优化参数空间

**评分参数**:
- entry_threshold (60~80)
- exit_threshold (30~50)
- scoring_weights (各维度权重微调)

**交易参数**:
- position_pct (0.10~0.30)
- min_hold_minutes (15~60)
- min_profit_after_cost_pct (0.003~0.010)
- min_notional (10000~50000)
- reentry_cooldown_minutes (60~240)
- max_hold_days (5~20)

**止损/止盈**:
- stop_loss ATR mult (1.0~3.0)
- take_profit ATR mult (2.0~5.0)

### 11.3 目标函数

```
score = sharpe × 1.0 + profit_factor × 0.3 - max_dd% × 0.05 + trade_count_bonus × 0.2 - fee_ratio × 0.5
```

v2 新增 `fee_ratio` 惩罚项，防止优化出高频低利润策略。

## 12. Risks & Mitigations

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| Kline 数据不可用（Futu OpenD 断连） | 高 | score=0→WAIT，不会误操作；止损/止盈不依赖 score |
| 评分 threshold 过高导致从不入场 | 中 | 无 L2 数据时 max=88，70 阈值仍可达；监控入场频率 |
| 评分 threshold 过低导致错误入场 | 中 | 有 min_notional + max_single_stock 风控兜底 |
| 过拟合 (评分权重) | 中 | Walk-Forward OOS, 权重总和固定 100 |
| T3 过滤太严导致真止损延迟 | 中 | min_hold=30min 相对保守；止损始终实时生效不受 T3 过滤影响 |
| 同一天 daily_reset 被调用多次 | 低 | daily_reset() 是幂等的，不增 _day_index |
