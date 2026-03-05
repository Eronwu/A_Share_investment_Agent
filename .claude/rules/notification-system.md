# Notification System Rules

## macOS 依赖

L1/L2 告警弹窗需要安装 `terminal-notifier`:

```bash
brew install terminal-notifier
```

## Tiered Notification (L1-L4)

Level is derived from `star` + `type` + `hidden` on each watchlist entry:

| Level | Match | trigger_pct | delta_pct | cooldown | Dispatch |
|-------|-------|-------------|-----------|----------|----------|
| L1 | `star=true` | 4% | 3% | 5min | macOS popup + sound |
| L2 | `type=holding & !star & !hidden` | 6% | 5% | 15min | macOS popup silent |
| L3 | `type=watching & !star & !hidden` | threshold only | threshold only | 30min | web log only |
| L4 | `hidden=true` or not in watchlist | - | - | - | no notification |

Resolution priority: `hidden → star → type`.

## Policy Table (code-level)

Defined in `stock_notifier.py` as `NOTIFY_POLICIES` dict. Each level is a strategy:

```python
{level: {"trigger_pct", "delta_pct", "cooldown_min", "big_move", "threshold", "dispatch"}}
```

User can override per-level via `monitor_config.json → settings`:
- `l1_trigger_pct`, `l1_delta_pct`, `l1_cooldown_min`
- `l2_trigger_pct`, `l2_delta_pct`, `l2_cooldown_min`
- `l3_cooldown_min`

Merge: `{**DEFAULT_POLICIES[level], **user_overrides}`

## Single Computation Source

- **Notifier** (`DeltaAlertEngine`) is the ONLY alert computation engine
- Writes results to `sim_trading.db:alert_events` table (dual format: `message` for terminal, `display` for web)
- Web `useAlerts` hook ONLY reads and displays, never computes
- Terminal notifications and web logs are always in sync

## Dispatch Rules

- L1 alerts → `stealth_dispatch(sound="default")` — always audible
- L2 alerts → `stealth_dispatch(sound="")` — silent popup
- L3 alerts → `write_alert_events()` only — no macOS notification
- L4 → skipped entirely (but still counted in portfolio P&L)

## Delta-Driven (Not State-Driven)

- First trigger: `|daily change%| >= trigger_pct`
- Re-trigger: `|price - last_notified_price| / last_notified_price >= delta_pct`
- Threshold (above/below): also protected by delta — first breach notifies, then needs delta change
- Price unchanged → no repeat notification

## Daily Reset

- Triggers at **08:00** (not midnight) — avoids clearing HK after-hours events
- Clears: `_notified` dict, `_last_portfolio_pnl`
- Runs 30-day cleanup: `DELETE FROM alert_events WHERE date < (today - 30d)`
- Web reads only today's events via `WHERE date = ?` — auto-scoped per day

## Stealth Mode

- Notification titles rotate: "CI Pipeline Alert", "Deploy Monitor", "SRE Notification", "Build Status"
- Content uses stock name + change%, no monetary values
- Max 2 lines per notification + "+N more" summary
- Click opens `http://localhost:3120/alerts`

## Star Feature

- `star: boolean` field on watchlist entry
- Set via manage page ★/☆ toggle or `svc star/unstar <code>`
- Star works for both holdings and watchlist stocks
- Star stocks sort to top in dashboard sections
- Star stocks show ★ prefix in yellow
