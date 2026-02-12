# Quant Engineer Memory

## L2 Strategy Engine Lessons

### Sampling Design Pitfalls (2025-02-12)

**Window overlap**: When sampling a sliding-window tracker at intervals < window length, consecutive samples are highly correlated (e.g., 2-min interval on 5-min window = 60% overlap). Acceleration ratios computed on overlapping samples are severely dampened — a real 2x spike appears as ~1.2x. **Rule: SAMPLE_INTERVAL >= window length for independent measurements.**

**Prior/recent separation**: When checking "sustained condition over recent N samples" AND "acceleration vs prior baseline", the two sets MUST NOT overlap. Otherwise the conditions partially contradict — high prior imbalance implies high prior turnover, making acceleration harder to trigger. Fix: `prior = samples[:-consecutive_min]`, `recent = samples[-consecutive_min:]`.

**Stale data across sessions**: Any tracker fed by tick data must guard with trading-hours check. HK lunch break (12:00-13:00) causes sliding window to decay → false acceleration at 13:00 reopen.

### Algorithm Order-Splitting Detection

**Signature**: High turnover + high tick_imbalance + very few large orders. Example: HK03986 兆易创新 — 11亿 turnover, tick 78% buy, only 2 orders >500万 (1.36% of volume).

**Why momentum_alert fails**: Requires >=3 large orders + net amount >3000万. Algorithm splitting keeps individual order size below detection threshold.

**Why volume_accel_alert may also miss**: Algo-splitting often produces **pulse** acceleration (single 5-min spike) not **sustained** acceleration (3+ consecutive windows). Consider a complementary Single-Pulse Alert: `accel ≥ 2.5 AND imb ≥ 0.6 AND large_order present`, no consecutive requirement.

**Session direction caveat**: `SessionAccumulator.lo_score` requires >=3 large orders. In splitting scenarios lo_score=0, cap session score at tick*2 + capital*1 = 3 max. Use `score > 0` instead of strict `direction == "bullish"` for splitting-aware strategies.

### HK Market Patterns

**Shake-and-Take (先砸后拉)**:
- Phase 1: volume_price_divergence + order_book crash (委比 -123) → scare retail
- Phase 2: Large order BUY at lower price (173.70), consolidate 30-60min
- Phase 3: Second large order at higher price (178.00), complete pullup
- Tell: 100% tick_imbalance positive throughout, order_book oscillates wildly but no directional bias
- Example: HK06809 澜起科技 2025-02-12

**Pulse vs Sustained acceleration**:
- Pulse: Single window 2x+ spike (e.g., 1004→2673万), subsequent windows contract immediately. Common in HK mid-caps with concentrated institutional action.
- Sustained: 3+ consecutive windows each higher than prior. More common in broad-based sector rallies or index-level moves.
- Current `volume_accel_alert` only catches sustained. Pulse needs separate detection.

### Key Parameters Reference

| Strategy | Parameter | Value | Rationale |
|----------|-----------|-------|-----------|
| volume_accel_alert | accel_ratio | 1.8 | Post-overlap-fix, reasonable starting point |
| volume_accel_alert | imbalance_min | 0.35 | Lower than tick_imbalance threshold (0.4) because consecutive requirement adds stringency |
| volume_accel_alert | consecutive_min | 3 | 3 × 5min = 15min sustained — early enough to catch 15-60min institutional runs |
| volume_accel_alert | min_turnover | 10M | Filters HK small-caps; may need 5M for mid-caps with 1-3亿 daily volume |
| volume_accel_alert | cooldown | 120min | Same as momentum_alert — high-conviction signals should be infrequent |

### Strategy Interaction Map

```
tick_imbalance (5min window, raw signal, weight=3)
    ↓ feeds stats via get_current_stats()
volume_accel_alert (5min sampling, session-level, notify=true)
    ↕ complementary to
momentum_alert (session-level, large-order dependent, notify=true)
    ↕ both independent of
composite scoring (10min window, multi-strategy convergence, notify=true)
```

- tick_imbalance + volume_accel_alert can co-trigger (different levels: raw vs session)
- momentum_alert + volume_accel_alert can co-trigger (different detection methods)
- Neither feeds into SignalScorer composite scoring

## File Locations

- Engine: `src/tools/l2_strategy_engine.py`
- Config: `src/data/l2_strategy_config.json`
- Spec: `docs/l2_strategy_spec.md`
- Signals: `src/data/l2_strategy_signals.json`
- Daemon: `src/tools/l2_strategy_daemon.py`
