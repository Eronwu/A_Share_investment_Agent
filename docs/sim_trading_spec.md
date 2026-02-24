# Simulated Trading System Specification

> Version: 1.0 | Created: 2025-02-14
> Source: `src/sim_trading/`
> Database: `src/data/sim_trading.db`

## 1. System Overview

基于 L2 策略引擎的信号数据，实现模拟量化交易，用交易成功率反向优化策略参数。

### 1.1 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   L2 Simulation Trading System                   │
│                                                                  │
│  Layer 0: Data Collection                                        │
│  ┌─────────────────────┐  ┌─────────────────────┐              │
│  │ l2_strategy_engine   │  │ market_data_poller   │              │
│  │ → signals.json       │  │ → market_data.json   │              │
│  └────────┬─────────────┘  └────────┬─────────────┘              │
│           └──────────┬──────────────┘                             │
│                      ▼                                            │
│  ┌──────────────────────────────────┐                            │
│  │  signal_archiver.py              │ → sim_trading.db           │
│  │  (归档信号 + 采样价格快照)        │   (SQLite)                 │
│  └──────────────────────────────────┘                            │
│                      │                                            │
│  Layer 1: Signal → Trade Mapping                                 │
│  ┌──────────────────────────────────┐                            │
│  │  TradeSignalMapper               │ ← signal_rules.json       │
│  │  (置信度评分 + 冲突消解 + 风控)   │                            │
│  └──────────────────────────────────┘                            │
│                      │                                            │
│  Layer 2: Simulation Engine                                      │
│  ┌────────────────────┐  ┌──────────────────┐                   │
│  │  SimulationEngine   │  │  PositionManager  │                   │
│  │  (成本/滑点/执行)   │  │  (持仓/止损/止盈) │                   │
│  └────────────────────┘  └──────────────────┘                   │
│                      │                                            │
│  Layer 3: Analytics & Optimization                               │
│  ┌────────────────────┐  ┌──────────────────┐                   │
│  │  TradeAnalyzer      │  │  ParameterOptimizer│                  │
│  │  (胜率/Sharpe/归因) │  │  (Optuna贝叶斯)   │                  │
│  └────────────────────┘  └──────────────────┘                   │
│                                    │                              │
│                                    ▼                              │
│                      l2_strategy_config.json (参数回写)           │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Design Principles

- **纯消费者**: 不修改任何现有数据生产者（l2_daemon, poller, notifier）
- **模块解耦**: 新增代码全部在 `src/sim_trading/`，与 `src/tools/` 分离
- **渐进式**: Phase 1 归档数据 → Phase 2 模拟交易 → Phase 3 优化 → Phase 4 生产化
- **防过拟合**: Walk-Forward OOS 验证，参数正则化

## 2. Signal → Trade Mapping

### 2.1 信号分层

| Tier | 信号 | 动作 | 基础置信度 | 仓位% | 止损 | 最长持仓 |
|------|------|------|-----------|-------|------|---------|
| **1 独立触发** | `momentum_alert` | BUY | 0.70 | 20% | 2×ATR | 5天 |
| | `momentum_sell_alert` | SELL清仓 | 0.70 | 100% | — | — |
| | `volume_accel_alert` | BUY | 0.65 | 15% | 2.5×ATR | 3天 |
| | `volume_accel_sell_alert` | SELL清仓 | 0.65 | 100% | — | — |
| | `composite_bullish` (≥7) | BUY | 0.60 | 10% | 2×ATR | 3天 |
| | `composite_bearish` (≥7) | SELL减仓 | 0.60 | 50% | — | — |
| **2 增强叠加** | `tick_persistence` | +0.15 | — | — | — | — |
| | `large_order` (>1000万) | +0.10 | — | — | — | — |
| **3 修正/预警** | `large_order_reversal` | 减半仓 | 0.60 | 50% | — | — |
| | `volume_price_divergence` | 收紧止损 | — | — | 1×ATR | — |
| | `closing_surge` (bearish) | 次日开盘卖 | 0.55 | 100% | — | — |
| **4 仅记录** | tick_imbalance, capital_flow_spike, order_book_imbalance, institutional_retail_divergence | LOG | — | — | — | — |

### 2.2 冲突消解

- 同股同 session 多空冲突 → 取消双方
- 同股最多 3 笔信号/session
- 信号间隔最少 15 分钟
- Tier 3 修正信号优先级高于 Tier 1 入场信号

### 2.3 风控过滤

- 最低置信度: 0.60 才执行
- 单股上限: 25% 账户净值
- 总投资上限: 80%
- 开盘 15 分钟不交易
- 收盘前 30 分钟不开新仓

## 3. Simulation Engine

### 3.1 HK 交易成本模型

| 费项 | 费率 | 备注 |
|------|------|------|
| 佣金 | 0.03% | min HKD 3 |
| 印花税 | 0.13% | 买卖双向，四舍五入到 HKD 1 |
| 交易费 | 0.00565% | 交易所 |
| 结算费 | 0.002% | min HKD 2, max HKD 100 |

### 3.2 滑点模型

| 日成交额 | 滑点 |
|---------|------|
| >10亿 | 0.05% |
| >1亿 | 0.10% |
| >1000万 | 0.20% |
| <1000万 | 0.50% |

### 3.3 资金模型

- 初始资金: 100万 HKD (可配置)
- Lot-size 对齐: 腾讯/阿里/小米/比亚迪 = 100 股/手
- ATR 估算: `price × amplitude% / 100` (from snapshot)

## 4. Analytics

### 4.1 核心指标

| 指标 | 公式 |
|------|------|
| 胜率 | 盈利笔数 / 总笔数 |
| 盈亏比 | 总盈利 / 总亏损 |
| Sharpe | (mean_daily_return - rf) / std |
| 最大回撤 | max(peak - trough) / peak |
| Calmar | annualized_return / max_drawdown |

### 4.2 信号归因分析

- Per-strategy: 胜率、平均盈亏、贡献度%
- Per-time: 上午/下午/各时段表现
- Per-market: 大盘涨日 vs 跌日
- 信号组合: momentum + tick_persistence 胜率 vs 单独 momentum

## 5. Parameter Optimization

### 5.1 方法: Optuna 贝叶斯优化

- TPE sampler，50-200 次试验
- Walk-Forward OOS: 70% train / 30% test
- Early pruning: train 胜率 <40% 或回撤 >15% 直接淘汰
- Test 集 Sharpe 需 ≥ train 集的 70%

### 5.2 优化参数空间 (~20 个)

**策略参数**:
- tick_imbalance threshold (0.25~0.60)
- large_order min_amount (3M~10M)
- volume_accel ratio (1.3~2.5)
- momentum daily_change_pct_min (1.5~5.0)
- composite threshold (4~8)

**交易参数**:
- min_confidence (0.50~0.75)
- stop_loss ATR mult (1.0~3.5)
- take_profit ATR mult (1.5~5.0)
- max_hold_days (1~10)
- position_pct (0.05~0.30)

**评分权重**:
- tick_imbalance weight (1~5)
- large_order weight (1~4)

### 5.3 目标函数

```
score = sharpe × 1.0 + profit_factor × 0.3 - max_dd% × 0.05 + trade_count_bonus × 0.2
```

### 5.4 参数版本管理

每次优化输出新版本参数，存入 `param_versions` 表。需人工确认后才写入 `l2_strategy_config.json`。

## 6. Data Storage (SQLite)

### 6.1 Tables

```sql
-- 历史信号归档
signals (ts, date, time, strategy, code, direction, notify, detail, display, price_at_signal, daily_change_pct)

-- 价格快照 (30s 粒度)
price_snapshots (ts, date, code, price, volume, amount, change_pct)

-- 模拟交易
trades (trade_id, param_version, code, action, entry_price, exit_price, quantity, entry_time, exit_time, hold_days, pnl, pnl_pct, confidence, trigger_signals, exit_reason)

-- 每日快照
daily_pnl (date, param_version, total_equity, cash, invested, daily_return, cumulative_return, drawdown_pct)

-- 参数版本
param_versions (version, config_json, trade_rules_json, train_sharpe, test_sharpe, train_win_rate, test_win_rate)

-- 优化运行
optimization_runs (run_id, n_trials, best_score, best_params, train_period, test_period)
```

### 6.2 存储估算

| 表 | 日增量 | 30天 |
|----|--------|------|
| signals | ~200 行 | ~6K 行 |
| price_snapshots | ~3900 行 (5股×780窗口) | ~117K 行 |
| trades | ~5-10 行 | ~150-300 行 |
| daily_pnl | 1 行 | 30 行 |

总计约 10MB/月，SQLite 完全够用。

## 7. File Layout

```
src/
├── sim_trading/                      [NEW]
│   ├── __init__.py
│   ├── signal_archiver.py           # Layer 0: 信号归档
│   ├── signal_mapper.py             # Layer 1: 信号→交易
│   ├── simulation_engine.py         # Layer 2: 模拟执行
│   ├── position_manager.py          # Layer 2: 持仓管理
│   ├── trade_analyzer.py            # Layer 3: 交易分析
│   ├── optimizer.py                 # Layer 3: Optuna 优化
│   ├── replay_runner.py             # 回放入口
│   └── live_runner.py               # 实时入口
├── data/
│   ├── sim_trading.db               [NEW - SQLite]
│   ├── signal_rules.json            [NEW - 交易规则]
│   └── ... (existing files unchanged)
```

## 8. Implementation Phases

### Phase 1: 数据基础 (1 周) ← **当前阶段**

| 任务 | 输出 |
|------|------|
| SQLite schema 初始化 | `sim_trading.db` |
| `signal_archiver.py` | 实时归档信号 + 价格快照 |
| 集成到 daemon | 自动启动 archiver |
| 回填现有数据 | 导入已有信号 |

### Phase 2: 模拟交易核心 (2 周)

| 任务 | 输出 |
|------|------|
| `signal_rules.json` | 初版映射规则 |
| `TradeSignalMapper` | 信号→交易决策 |
| `PositionManager` | 虚拟持仓 |
| `SimulationEngine` | HK 成本/滑点 |
| `TradeAnalyzer` | 胜率/Sharpe/归因 |
| `replay_runner.py` | 历史回放 |

### Phase 3: 分析与优化 (2 周)

| 任务 | 输出 |
|------|------|
| 信号归因分析 | per-strategy P&L |
| Optuna 集成 | 贝叶斯优化 |
| Walk-Forward OOS | 防过拟合验证 |
| 参数版本管理 | 自动应用最优参数 |

### Phase 4: 生产化 (2 周)

| 任务 | 输出 |
|------|------|
| Web Dashboard 集成 | 模拟交易页面 |
| 定期自动优化 | 每周末跑优化 |
| 原始 L2 数据归档 | tick/capital 存储 |
| L2 引擎参数重评估 | 基于原始数据 |

## 9. Risks & Mitigations

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 数据不足 (需 10-15 天) | 高 | 立即启动归档，Bootstrap resampling |
| 过拟合 (20 参数) | 高 | Walk-Forward OOS, 参数正则化, decay系数 |
| 信号固定 vs 重评估 | 中 | Phase 1-2 只优化交易参数，Phase 4 再优化策略参数 |
| 信号到价格时间差 | 中 | 信号归档时记录 price_at_signal, 保守滑点 |
| 同股多空冲突 | 低 | CANCEL_BOTH + 15min 最小间隔 |
