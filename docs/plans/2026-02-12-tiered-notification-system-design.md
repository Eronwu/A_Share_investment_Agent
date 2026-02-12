# Tiered Notification System Design

**Date:** 2026-02-12
**Status:** Approved

## Overview

Replace the current flat notification logic with a 4-level priority system. Each level has different trigger thresholds, cooldown periods, and notification methods.

## Level Definitions

| Level | Match Rule | trigger_pct | delta_pct | cooldown | Dispatch |
|-------|-----------|-------------|-----------|----------|----------|
| L1 Star | `star=true` | 4% | 3% | 5min | macOS popup + sound |
| L2 Holding | `type=holding & !star & !hidden` | 6% | 5% | 15min | macOS popup silent |
| L3 Watching | `type=watching & !star & !hidden` | threshold only | threshold only | 30min | web log only |
| L4 Other | `hidden=true` or not in watchlist | - | - | - | no notification |

Resolution priority: hidden → star → type.

## Data Model

### monitor_config.json

WatchEntry adds `star: boolean`:

```json
{
  "HK00700": { "name": "腾讯控股", "type": "holding", "cost": 636, "shares": 100, "star": true }
}
```

### Settings (user-overridable defaults)

```json
{
  "l1_trigger_pct": 4, "l1_delta_pct": 3, "l1_cooldown_min": 5,
  "l2_trigger_pct": 6, "l2_delta_pct": 5, "l2_cooldown_min": 15,
  "l3_cooldown_min": 30,
  "poll_interval": 30
}
```

### Notification Policy Table (code-level)

```python
NOTIFY_POLICIES = {
    1: {"trigger_pct": 4, "delta_pct": 3, "cooldown_min": 5,
        "big_move": True, "threshold": True, "dispatch": "sound"},
    2: {"trigger_pct": 6, "delta_pct": 5, "cooldown_min": 15,
        "big_move": True, "threshold": True, "dispatch": "silent"},
    3: {"trigger_pct": None, "delta_pct": None, "cooldown_min": 30,
        "big_move": False, "threshold": True, "dispatch": "web_only"},
    4: {"trigger_pct": None, "delta_pct": None, "cooldown_min": None,
        "big_move": False, "threshold": False, "dispatch": "none"},
}
```

User overrides from settings merged: `{**DEFAULT_POLICIES[level], **user_overrides}`

### alert_events.json

Each event gains `level` field:

```json
{
  "ts": 1707636339000, "time": "10:31:39", "symbol": "HK00700",
  "kind": "big_move", "level": 1,
  "message": "腾讯控股: ok +4.2%",
  "display": "HK00700 腾讯控股 涨幅 4.2%",
  "change_pct": 4.2
}
```

## Engine Changes (stock_notifier.py)

### resolve_level(symbol, entry) → int

```
hidden=true       → 4
star=true          → 1
type="holding"     → 2
else               → 3
```

### get_policy(level, settings) → dict

Merge default policy with user overrides from settings.

### DeltaAlertEngine.check() changes

1. For each stock: resolve level → get policy
2. L4: skip entirely
3. L3: only check threshold (above/below), no big_move
4. L1/L2: check threshold + big_move with level-specific trigger/delta thresholds
5. Attach `_level` to each alert dict

### Dispatch changes

After check(), split alerts by level:
- L1 alerts → `stealth_dispatch(sound="default")`
- L2 alerts → `stealth_dispatch(sound="")`
- L3 alerts → `write_alert_events()` only, no macOS notification

## Frontend Changes

### manage/page.tsx — Star toggle

Toggle switch next to hide, for both holdings and watchlist:
- `★` yellow when star=true, `☆` gray when false
- Calls `POST /api/config { action: "update", code, data: { star: true/false } }`

### page.tsx — Star visual indicator

- Star stocks show `★` prefix in yellow at row start
- Star stocks sort to top within their section

### commandParser.ts — New commands

```
svc star <code>      → update { star: true }
svc unstar <code>    → update { star: false }
```

### alerts/page.tsx — Level coloring

| Level | Color | Label |
|-------|-------|-------|
| L1 | D.yellow | [L1] |
| L2 | D.orange | [L2] |
| L3 | D.comment | [L3] |

### useAlerts.ts — Level prefix

Log entries show `[L1]`/`[L2]` prefix before the message.

## API Changes

### /api/config route.ts

- update action: handle `star` field (same pattern as `hidden`)
- settings whitelist: add l1_*/l2_*/l3_* keys

### /api/metrics route.ts

- Merge `star` from config into service response

### types.ts

- WatchEntry: add `star?: boolean`
- Service: add `star?: boolean`

## File Change List

| File | Change |
|------|--------|
| `stock_notifier.py` | Policy table, resolve_level, check() refactor, dispatch split |
| `config/route.ts` | star field in update, new settings keys in whitelist |
| `metrics/route.ts` | merge star field |
| `types.ts` | star in WatchEntry + Service |
| `manage/page.tsx` | star toggle |
| `page.tsx` | ★ indicator + star sort-to-top |
| `commandParser.ts` | star/unstar commands |
| `useCommand.ts` | help text |
| `alerts/page.tsx` | level coloring |
| `useAlerts.ts` | [L1]/[L2] prefix |

## Extensibility

- Add new level: add row to NOTIFY_POLICIES + matching rule in resolve_level
- Per-stock override: future `level_override` field on WatchEntry
- New dispatch methods: add to policy dispatch enum (e.g., "webhook", "dingtalk")
