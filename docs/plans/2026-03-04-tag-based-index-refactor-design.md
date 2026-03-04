# Tag-Based Index Refactor Design

Date: 2026-03-04

## Background

Current custom index system (`sector_config.json`) maintains explicit stock lists per index. This creates dual maintenance: stocks in watchlist AND stocks in index config. Refactor to stock-centric model: tag each stock with sector/theme tags, indices derive automatically from tags.

Also unify the alert system: both individual stocks and tag-derived indices share a common notification framework, including a new "watch drift" alert (e.g., "距关注已涨 10%").

## Core Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tag storage | JSON array on `monitor_watchlist.tags` | Consistent with DB-first architecture, <200 stocks, no perf concern |
| Index definition | Pure tag aggregation | No separate config needed; tag exists → index exists |
| Alert model | Unified (stock + index) | One engine, one dispatch, one alert table |
| Baseline price | Auto-record + manual override | `watch_price` captured on add, user can reset |
| UI layout | /sector retained for index overview | Tag management on /manage, tags visible on holdings/watching/manage |

## §1 Data Model

### DB Schema Changes

```sql
-- monitor_watchlist: new columns
ALTER TABLE monitor_watchlist ADD COLUMN tags TEXT DEFAULT '[]';
ALTER TABLE monitor_watchlist ADD COLUMN watch_price REAL DEFAULT NULL;
ALTER TABLE monitor_watchlist ADD COLUMN watch_price_date TEXT DEFAULT NULL;

-- New table: tag metadata
CREATE TABLE IF NOT EXISTS tag_meta (
    tag TEXT PRIMARY KEY,
    star INTEGER DEFAULT 0,
    watch INTEGER DEFAULT 1,
    baseline_value REAL DEFAULT 100,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### Field Reference

| Field | Table | Description |
|-------|-------|-------------|
| `tags` | monitor_watchlist | JSON array `["半导体","AI"]`, one stock → many tags |
| `watch_price` | monitor_watchlist | Market price when added to watchlist (baseline) |
| `watch_price_date` | monitor_watchlist | Date of baseline price |
| `star` | tag_meta | ★ highlight, star tags sort first |
| `watch` | tag_meta | Enable mainline detection (false = compute only, no alert) |
| `baseline_value` | tag_meta | Chained index value starting point (default 100) |

### JSON Snapshot

`monitor_config.json` watchlist entries include tags and watch_price naturally:

```json
{
  "HK09988": {
    "name": "阿里巴巴", "type": "holding", "cost": 149,
    "tags": ["AI", "互联网"],
    "watch_price": 142.5, "watch_price_date": "2026-03-01"
  }
}
```

### Index Derivation

- Each unique tag → one index (automatic)
- Index components = all non-hidden stocks with that tag
- Equal-weight average (unchanged)
- Tag with zero stocks → index disappears (tag_meta row retained)

## §2 Unified Alert System

### Alert Types

| Type | Target | Trigger | Example |
|------|--------|---------|---------|
| `threshold` | Stock | Price crosses above/below | "突破 100 元" |
| `big_move` | Stock | Daily change% >= trigger_pct | "涨 6.5%" (existing) |
| `watch_drift` | Stock + Index | Cumulative drift from watch_price >= N% | "距关注已涨 10%" |
| `mainline` | Index | cum_gain + slope + R² | "主线行情确认" (migrated) |

### watch_drift Logic

```
drift% = (current_price - watch_price) / watch_price × 100

Stock:  current_price = realtime price
Index:  current_price = today's chained index value

Trigger ladder (default step = 5%):
  ±5%, ±10%, ±15%, ±20% ...

First breach of a tier → notify once.
Price falls back below tier then re-breaches → notify again.
```

### Index Alert Level

Derived from `tag_meta.star`:

| tag_meta | Level | Dispatch |
|----------|-------|----------|
| `star=true` | L1 | macOS popup + sound |
| `star=false, watch=true` | L2 | macOS popup silent |
| `watch=false` | none | Web display only |

Stock alert levels unchanged (existing L1-L4 logic).

### alert_config.json Extension

```json
{
  "alerts": {
    "HK09988": { "above": 200, "below": 120 },
    "tag:半导体": { "watch_drift_pct": 5 }
  },
  "defaults": {
    "watch_drift_pct": 5,
    "watch_drift_enabled": true
  }
}
```

- Stock key = code, index key = `tag:` prefix
- `defaults` applies to all watched stocks/indices unless overridden

### Engine Changes

- `DeltaAlertEngine` retained: big_move + threshold (unchanged)
- New `WatchDriftTracker` class: handles watch_drift for stocks and indices
- Mainline detection migrated from `sector_index_engine.py` into `stock_notifier.py`
- `sector_alerts` table retained, writer changes to notifier

## §3 Index Engine Refactor

### Data Source Change

```
Before: sector_config.json → stocks list → fetch prices → compute
After:  monitor_watchlist.tags → query stocks per tag → fetch prices → compute
```

### sector_config.json Deprecation

| Original Field | Migration Target |
|----------------|-----------------|
| `indices.{id}.stocks` | `monitor_watchlist.tags` (inverted: stock → tag) |
| `indices.{id}.name` | `tag_meta.tag` (tag IS the name) |
| `indices.{id}.star` | `tag_meta.star` |
| `indices.{id}.watch` | `tag_meta.watch` |
| `indices.{id}.baseline_value` | `tag_meta.baseline_value` |
| `indices.{id}.created_at` | `tag_meta.created_at` |
| `alert_rules` | `alert_config.json → defaults` |
| `rotation` | Retain in minimal config (archived feature, minimal impact) |

### sector_daily Table

`index_id` stores tag name directly (was "phosphorus", becomes "磷化工"). Schema otherwise unchanged.

### API Changes (`/api/sector`)

**GET** response shape mostly unchanged:

```typescript
indices: [{
  id: "半导体",        // tag name = id
  name: "半导体",
  star, watch,         // from tag_meta
  stockCount: 12,
  today, d3, d5, d10, cumGain,
  components, history,
}]
```

**POST** actions:

| Old | New | Change |
|-----|-----|--------|
| `create` (with stocks) | Removed | Tag stocks to auto-create |
| `delete` | `delete-tag` | Delete tag_meta + strip tag from all stocks |
| `star` / `watch` | Unchanged | Operates on tag_meta |
| `update` (change stocks) | Removed | Edit stock tags instead |
| — | `reset-baseline` | Reset tag baseline_value to current index value |

## §4 UI

### /manage — Tag Management

Stocks table adds `tags` column:

```
| code   | name   | cost | shares | tags              | ★ | hide |
|--------|--------|------|--------|-------------------|---|------|
| 002371 | 北方华创 | 280  | 100    | [半导体] [AI]      | ★ | ○   |
```

- Click tags cell → popup with existing tags (multi-select checkbox) + new tag input
- New tag auto-creates `tag_meta` row
- Batch operation: select multiple stocks → "批量打 tag"
- Optional `watch_price` column: shows baseline + drift%, click to reset

### /holdings + /watching — Tag Display

Both pages add `tags` column with colored tag chips (color derived from tag name hash).

- Click a tag chip → filter mode: show only stocks with that tag
- Top bar shows "筛选: 半导体 ×" with clear button

### /sector — Tag Index Overview

Layout unchanged:

```
┌─────────────────────────────────────────┐
│ 轮动矩阵 (retained, archived feature)   │
├─────────────────────────────────────────┤
│ 我的指数 (tag aggregated)                │
│ ★ 半导体  12只  今日+1.2%  3日+3.5%  ...│
│   磷化工   4只  今日-0.3%  3日+1.1%  ...│
│   AI      8只  今日+2.1%  主线 🔴       │
├─────────────────────────────────────────┤
│ 主线告警 + watch_drift 告警              │
└─────────────────────────────────────────┘
```

- Click index row → expand components + K-line (unchanged)
- Components table adds "距关注涨跌%" column
- Stock count badge on each index row

### /alerts — New Alert Types

```
14:30  L2  距关注涨10%  002371  ¥308  +10.0%  北方华创  加入时¥280，已涨10%
15:01  L1  主线确认     tag:AI         +12.3%  AI指数   累涨12.3%，斜率0.08
```

- `watch_drift` signal: "距关注涨N%" / "距关注跌N%"
- Color: green (up), red (down)
- Index alerts symbol: `tag:名称`

### Stock Add Flow

- CLI: `svc add 002371 tags=半导体,AI`
- Manage page: add dialog includes optional tags multi-select
- watch_price auto-recorded from current market price at add time

## §5 Migration Plan

1. Add `tags` + `watch_price` columns to `monitor_watchlist`
2. Create `tag_meta` table
3. Read `sector_config.json`, for each index:
   - For each stock in index.stocks: append index.name to stock's tags
   - Insert tag_meta row with star/watch/baseline/created_at
4. Export JSON snapshot (dual-write)
5. Update `sector_index_engine.py` to read from DB tags instead of config
6. Update `/api/sector` to query tag-aggregated indices
7. Update `/api/config` to handle tags CRUD
8. Add `WatchDriftTracker` to `stock_notifier.py`
9. Migrate mainline detection into notifier
10. Update all frontend pages (manage/holdings/watching/sector/alerts)
11. Delete `sector_config.json` after verification
12. Update CLAUDE.md

## Files Affected

| File | Change |
|------|--------|
| `src/tools/monitor_config_db_migrator.py` | Add tags/watch_price migration |
| `src/tools/sector_index_engine.py` | Read tags from DB instead of config |
| `src/tools/stock_notifier.py` | Add WatchDriftTracker, migrate mainline detection |
| `web/app/api/config/route.ts` | Handle tags CRUD, record watch_price on add |
| `web/app/api/sector/route.ts` | Query tag-aggregated indices |
| `web/app/api/metrics/route.ts` | Include tags in stock data |
| `web/app/page.tsx` | Add tags column to holdings |
| `web/app/watching/page.tsx` | Add tags column |
| `web/app/manage/page.tsx` | Tag editor UI, batch tag, watch_price display |
| `web/app/sector/page.tsx` | Adapt to tag-based indices |
| `web/app/alerts/page.tsx` | Render watch_drift + index alerts |
| `web/app/types.ts` | Add tags/watch_price to WatchEntry |
| `web/app/lib/db.ts` | tag_meta table access |
| `src/data/sector_config.json` | Deprecated (deleted after migration) |
| `src/data/alert_config.json` | Add tag: keys + defaults |
| `CLAUDE.md` | Update architecture docs |
