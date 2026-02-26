# Simulated Trading Safety — ABSOLUTE PRIORITY

## The Rule

**模拟盘绝对不能亏光本金。** 这是最高优先级规则，高于一切策略优化、收益追求。违反此规则 = 断电。

## Mandatory Risk Controls

以下风控参数是硬性底线，**任何代码改动都不得放松**：

| 参数 | 值 | 含义 | 绝不可 |
|------|---|------|-------|
| `max_single_stock_pct` | 25% | 单股最大仓位 | 不得提高 |
| `max_total_invested_pct` | 80% | 总投资上限 | 不得提高 |
| `position_pct` | 25% | 每笔开仓比例 | 不得提高 |
| `max_new_positions_per_day` | 1 | 每日最多新开仓 | 不得提高 |
| 止损 | 必须存在 | 每个持仓必须有 SL | 不得移除 |
| `min_notional` | 30,000 | 最小交易金额 | 不得降低 |
| `min_hold_minutes` | 30 | T3 最短持有 | 不得降低 |

## Before Any Strategy Change

修改模拟交易相关代码前，必须确认：

1. **不会绕过止损逻辑** — `check_exits()` 中的 stop_loss 始终实时检查，不受任何条件门控
2. **不会放大仓位** — position_pct、max_single_stock 不被提高
3. **不会增加交易频率** — max_new_positions_per_day 不被提高
4. **不会移除成本过滤** — min_notional、min_profit_after_cost_pct 不被降低
5. **数据正确性** — kline 数据时效性检查（stale check）和 SL sanity check 不被移除

## Known Failure Modes (Must Prevent)

| 失败模式 | 后果 | 防护措施 |
|---------|------|---------|
| Stale kline → 错误评分 → 错误开仓 | SL > entry → 瞬间止损亏手续费 | kline stale check + SL sanity check |
| T3 反复割肉 | 手续费吞噬本金 | min_hold + cost filter + 盈利时只收窄SL |
| 同股反复开平 | 手续费累积 | reentry_cooldown 120min + 每日最多1只 |
| 多进程 daemon 重复下单 | 重复交易 | 启动前 pkill 旧进程 |
| 评分系统全面失效 | 盲目开仓 | score=0(WAIT) → 不开仓不平仓 |

## When in Doubt

**不交易就是最好的风控。** 宁可错过机会，也不冒亏光本金的风险。
