# Tag-Based Index Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace sector_config.json with stock-centric tags on monitor_watchlist, unify alerts for stocks and tag-derived indices.

**Architecture:** Tags stored as JSON array on `monitor_watchlist.tags` column. `tag_meta` table holds per-tag metadata (star/watch/baseline). Index = all non-hidden stocks sharing a tag, equal-weight. Unified alert system: existing DeltaAlertEngine + new WatchDriftTracker + migrated mainline detection.

**Tech Stack:** SQLite (better-sqlite3 on TS, sqlite3 on Python), Next.js 15, Python 3.11, Playwright for E2E.

**Design doc:** `docs/plans/2026-03-04-tag-based-index-refactor-design.md`

---

## Phase 1: DB Schema + Migration Infrastructure

### Task 1: Add columns to monitor_watchlist + create tag_meta table

**Files:**
- Modify: `web/app/api/config/route.ts` — `ensureMonitorTables()` at ~line 126
- Modify: `src/tools/monitor_config_db_migrator.py` — `import_json_to_db()` at ~line 90

**Step 1: Update ensureMonitorTables() in config/route.ts**

Add `tags`, `watch_price`, `watch_price_date` columns to `monitor_watchlist` CREATE TABLE. Add new `tag_meta` table creation.

```typescript
// In ensureMonitorTables(), after existing monitor_watchlist CREATE TABLE:
db.exec(`
  ALTER TABLE monitor_watchlist ADD COLUMN tags TEXT DEFAULT '[]';
`);
// Use pragma table_info to check if column exists first (ALTER TABLE IF NOT EXISTS not supported in SQLite)

// After monitor_settings creation:
db.exec(`
  CREATE TABLE IF NOT EXISTS tag_meta (
    tag TEXT PRIMARY KEY,
    star INTEGER DEFAULT 0,
    watch INTEGER DEFAULT 1,
    baseline_value REAL DEFAULT 100,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
  );
`);
```

Use safe column-add pattern: check `PRAGMA table_info(monitor_watchlist)` for existing columns before ALTER TABLE (SQLite throws if column already exists).

**Step 2: Update Python migrator to handle tags/watch_price**

In `import_json_to_db()`, include `tags` and `watch_price` in INSERT. In `read_config_from_db()`, include them in SELECT output. In `_from_db_normalized_to_snapshot()`, include in JSON snapshot.

**Step 3: Verify migration roundtrip**

Run: `poetry run python -m src.tools.monitor_config_db_migrator --action import-verify`
Expected: No parity differences for existing data (tags default to `[]`, watch_price to NULL).

**Step 4: Commit**

```
feat: add tags and watch_price columns to monitor_watchlist, create tag_meta table
```

---

### Task 2: Write migration script — sector_config → tags

**Files:**
- Create: `src/tools/migrate_sector_to_tags.py`

**Step 1: Write migration script**

```python
"""Migrate sector_config.json indices → stock tags + tag_meta."""
import json, sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sim_trading.db")
SECTOR_CFG = os.path.join(os.path.dirname(__file__), "..", "data", "sector_config.json")

def migrate():
    with open(SECTOR_CFG) as f:
        cfg = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for idx_id, idx in cfg.get("indices", {}).items():
        tag = idx["name"]  # e.g. "磷化工"
        stocks = idx.get("stocks", [])
        star = 1 if idx.get("star") else 0
        watch = 1 if idx.get("watch", True) else 0
        baseline = idx.get("baseline_value", 100)
        created = idx.get("created_at", "")

        # Insert tag_meta
        cur.execute("""
            INSERT OR REPLACE INTO tag_meta (tag, star, watch, baseline_value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (tag, star, watch, baseline, created))

        # Update each stock's tags
        for code in stocks:
            cur.execute("SELECT tags FROM monitor_watchlist WHERE symbol = ?", (code,))
            row = cur.fetchone()
            if row:
                existing = json.loads(row[0]) if row[0] else []
                if tag not in existing:
                    existing.append(tag)
                cur.execute("UPDATE monitor_watchlist SET tags = ? WHERE symbol = ?",
                            (json.dumps(existing, ensure_ascii=False), code))
            else:
                print(f"  WARN: {code} not in watchlist, skipping tag '{tag}'")

    # Update sector_daily index_id from old id to tag name
    for idx_id, idx in cfg.get("indices", {}).items():
        tag = idx["name"]
        if idx_id != tag:
            cur.execute("UPDATE sector_daily SET index_id = ? WHERE index_id = ?", (tag, idx_id))
            cur.execute("UPDATE sector_alerts SET index_id = ? WHERE index_id = ?", (tag, idx_id))

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
```

**Step 2: Run migration**

Run: `poetry run python -m src.tools.migrate_sector_to_tags`
Expected: Prints migration results, no WARN for stocks that exist in watchlist.

**Step 3: Verify with DB query**

Run: `sqlite3 src/data/sim_trading.db "SELECT symbol, tags FROM monitor_watchlist WHERE tags != '[]' LIMIT 10"`
Expected: Stocks show their tag arrays.

Run: `sqlite3 src/data/sim_trading.db "SELECT * FROM tag_meta"`
Expected: One row per former index (磷化工, etc.) with star/watch/baseline.

**Step 4: Export updated JSON snapshot**

Run: `poetry run python -m src.tools.monitor_config_db_migrator --action export`
Expected: `monitor_config.json` entries now include `"tags": [...]`.

**Step 5: Commit**

```
feat: migrate sector_config indices to stock tags + tag_meta
```

---

## Phase 2: API Layer — Tags CRUD + watch_price

### Task 3: Config API — tags and watch_price support

**Files:**
- Modify: `web/app/api/config/route.ts` — POST handler `add` (~line 524), `update` (~line 590)
- Modify: `web/app/types.ts` — `WatchEntry` (~line 3)

**Step 1: Update WatchEntry type**

```typescript
// web/app/types.ts — add to WatchEntry interface:
tags?: string[];
watch_price?: number;
watch_price_date?: string;
```

**Step 2: Record watch_price on add**

In POST `add` action (~line 524): after fetching stock name, read current price from `market_data.json` and set as `watch_price` + `watch_price_date = today`. Include `tags` from request body `data.tags` (default `[]`).

```typescript
// In add action, before INSERT:
const tags = data.tags || [];
let watchPrice = data.watch_price || null;
let watchPriceDate = data.watch_price_date || null;
if (!watchPrice) {
  // Auto-record from market_data.json
  const md = readMarketData();
  const svc = md?.services?.find((s: any) => s.id === code);
  if (svc?.price) {
    watchPrice = svc.price;
    watchPriceDate = new Date().toISOString().slice(0, 10);
  }
}

// INSERT includes tags, watch_price, watch_price_date
```

**Step 3: Support tags update**

In POST `update` action (~line 590): if `data.tags` is provided (array), update the `tags` column. If `data.watch_price` is provided, update watch_price + watch_price_date.

**Step 4: Add new actions: tag-add, tag-remove**

Batch-friendly actions for managing tags across multiple stocks:

```typescript
case "tag-add": {
  // body: { codes: string[], tag: string }
  // For each code: append tag to tags array if not present
  // Auto-create tag_meta if tag is new
}
case "tag-remove": {
  // body: { codes: string[], tag: string }
  // For each code: remove tag from tags array
}
case "reset-watch-price": {
  // body: { code: string }
  // Set watch_price = current market price, watch_price_date = today
}
```

**Step 5: Include tags in readConfigFromDb() output**

In `readConfigFromDb()` (~line 159): add `tags` and `watch_price` to SELECT, include in returned dict.

**Step 6: Include tags in writeConfigSnapshot()**

In `writeConfigSnapshot()` (~line 77): ensure tags array is included in JSON output.

**Step 7: Test via curl**

```bash
# Add stock with tags
curl -X POST http://localhost:3120/api/config \
  -H 'Content-Type: application/json' \
  -d '{"action":"add","code":"000001","data":{"tags":["银行","金融"]}}'

# Batch tag-add
curl -X POST http://localhost:3120/api/config \
  -H 'Content-Type: application/json' \
  -d '{"action":"tag-add","codes":["000001","600000"],"tag":"银行"}'
```

**Step 8: Commit**

```
feat: config API supports tags CRUD and watch_price recording
```

---

### Task 4: Metrics API — expose tags to frontend

**Files:**
- Modify: `web/app/api/metrics/route.ts` — `readMonitorConfigFromDb()` (~line 58), merge loop (~line 160)

**Step 1: Include tags and watch_price in DB read**

In `readMonitorConfigFromDb()`: add `tags`, `watch_price`, `watch_price_date` to SELECT. Parse `tags` from JSON string to array.

**Step 2: Merge into service objects**

In GET handler merge loop (~line 160): add `tags`, `watch_price`, `watch_price_date` to each service object.

**Step 3: Update Service type**

In `web/app/types.ts` — `Service` interface (~line 21): add `tags?: string[]`, `watch_price?: number`, `watch_price_date?: string`.

**Step 4: Commit**

```
feat: metrics API exposes tags and watch_price per stock
```

---

## Phase 3: Tag Management UI

### Task 5: Manage page — tag editor

**Files:**
- Modify: `web/app/manage/page.tsx` — stocks table section

**Step 1: Add tags column to stocks table**

After the existing columns (type/code/name/cost/shares/above/below/star/hide), add a `tags` column. Display as colored chips.

**Step 2: Build TagEditor component**

Inline component: click tags cell → dropdown with:
- Existing tags as checkboxes (from all unique tags across stocks)
- Text input for new tag
- Apply button

On apply: call `/api/config` POST with `action: "update"`, `data: { tags: [...] }`.

**Step 3: Add batch tag operations**

Add row checkboxes + top toolbar: "批量打 Tag" button → opens tag selector, applies to all selected stocks via `tag-add` action.

**Step 4: Tag color assignment**

Helper function `tagColor(tag: string): string` — deterministic color from tag name hash. Use a palette of 8-10 distinct colors cycling.

**Step 5: Commit**

```
feat: manage page tag editor with batch operations
```

---

### Task 6: Holdings + Watching pages — tag display + filter

**Files:**
- Modify: `web/app/page.tsx` — `HoldRow()` (~line 214), header (~line 450)
- Modify: `web/app/watching/page.tsx` — `WatchRow()` (~line 130), header (~line 172)

**Step 1: Add tags column to HoldRow**

After existing columns, add tags as colored chips. Reuse `tagColor()` from a shared util (`web/app/lib/tag-utils.ts`).

**Step 2: Add tags column to WatchRow**

Same treatment as HoldRow.

**Step 3: Add tag filter**

State: `filterTag: string | null`. When set, filter services to only those containing that tag. Show filter bar at top: `"筛选: {tag} ×"`.

Click a tag chip → set filterTag. Click × → clear filter.

**Step 4: Extract shared tagColor() to lib**

Create `web/app/lib/tag-utils.ts`:
```typescript
const TAG_COLORS = ["#8be9fd","#ff79c6","#50fa7b","#f1fa8c","#bd93f9","#ffb86c","#ff5555","#6272a4"];
export function tagColor(tag: string): string {
  let hash = 0;
  for (const ch of tag) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}
```

**Step 5: Commit**

```
feat: display tags on holdings and watching pages with click-to-filter
```

---

## Phase 4: Sector Engine Refactor

### Task 7: sector_index_engine reads tags from DB

**Files:**
- Modify: `src/tools/sector_index_engine.py` — `_load_config()` (~line 40), `compute_custom_indices()` (~line 293)

**Step 1: Replace _load_config() for indices**

Instead of reading `sector_config.json` indices, query DB:

```python
def _load_tag_indices(db_path):
    """Load indices from tag_meta + monitor_watchlist.tags."""
    conn = sqlite3.connect(db_path)
    # Get all tags
    tags = conn.execute("SELECT tag, star, watch, baseline_value, created_at FROM tag_meta").fetchall()
    # Get all stocks with tags
    stocks = conn.execute("SELECT symbol, tags FROM monitor_watchlist WHERE hidden = 0 AND tags != '[]'").fetchall()
    conn.close()

    # Build tag → stocks mapping
    tag_stocks = {}
    for symbol, tags_json in stocks:
        for tag in json.loads(tags_json):
            tag_stocks.setdefault(tag, []).append(symbol)

    # Build index defs
    indices = {}
    for tag, star, watch, baseline, created_at in tags:
        if tag in tag_stocks:
            indices[tag] = {
                "name": tag, "stocks": tag_stocks[tag],
                "star": bool(star), "watch": bool(watch),
                "baseline_value": baseline or 100,
                "created_at": created_at or "",
            }
    return indices
```

**Step 2: Update compute_custom_indices() to use _load_tag_indices()**

Replace `cfg["indices"]` with `_load_tag_indices(DB_PATH)`. Rest of computation logic unchanged (equal-weight, chain from baseline).

**Step 3: Update detect_mainline()**

Same change: read indices from DB tags. Alert rules can come from `alert_config.json → defaults` or hardcoded defaults (keep existing values as fallback).

**Step 4: Keep rotation config minimal**

Rotation is archived. Keep a small `rotation_config.json` or hardcode defaults. Not critical.

**Step 5: Test**

Run: `poetry run python -m src.tools.sector_index_engine --indices`
Expected: Same indices computed as before (data comes from tags now, not config).

Run: `poetry run python -m src.tools.sector_index_engine --detect`
Expected: Same mainline alerts as before.

**Step 6: Commit**

```
refactor: sector engine reads indices from DB tags instead of sector_config.json
```

---

### Task 8: Sector API refactor — tag-aggregated indices

**Files:**
- Modify: `web/app/api/sector/route.ts` — GET handler (~line 319), POST handler (~line 616)

**Step 1: GET — read indices from tag_meta + monitor_watchlist**

Replace `readSectorConfig()` index loading with DB query:

```typescript
// Read tag_meta
const tags = db.prepare("SELECT tag, star, watch, baseline_value, created_at FROM tag_meta").all();
// Read stocks with tags
const stocks = db.prepare("SELECT symbol, name, tags FROM monitor_watchlist WHERE hidden = 0 AND tags != '[]'").all();
// Build tag → stocks map (same as Python side)
```

**Step 2: GET — maintain same response shape**

`indices[]` structure stays the same. `id` = tag name, `stockCount` added. History and components from `sector_daily` unchanged (index_id now = tag name).

**Step 3: POST — adapt actions**

- Remove `create` (indices auto-derived from tags)
- Remove `update` (edit stock tags instead)
- `delete-tag`: delete from tag_meta + strip tag from all stocks in monitor_watchlist
- `star` / `watch`: update tag_meta
- `reset-baseline`: update tag_meta.baseline_value to current index value
- `config`: update alert defaults (move to alert_config.json)

**Step 4: Remove readSectorConfig() / writeSectorConfig()**

These functions (~line 98-135) read/write `sector_config.json`. Replace with DB operations. Keep a minimal helper for rotation config if needed.

**Step 5: Test via browser**

Navigate to `http://localhost:3120/sector`. Verify indices display correctly with tag-based data.

**Step 6: Commit**

```
refactor: sector API reads tag-aggregated indices from DB
```

---

## Phase 5: Sector Page UI Adaptation

### Task 9: Sector page — adapt to tag-based indices

**Files:**
- Modify: `web/app/sector/page.tsx` — CreateModal (~line 110), index list rendering

**Step 1: Remove CreateModal stock list input**

Creating an index = tagging stocks. Remove the "stocks" text input from CreateModal. Instead:
- "新建 Tag" modal: just needs tag name
- Creates tag_meta row via `/api/sector` POST `action: "create-tag"`
- User then goes to /manage to tag stocks

Or simpler: remove CreateModal entirely, create tags implicitly when tagging stocks on /manage.

**Recommended**: Keep a simplified CreateModal that only asks for tag name (no stocks field). Stock assignment happens on /manage.

**Step 2: Update index row to show stockCount**

Add `{idx.stockCount}只` badge after index name.

**Step 3: Add "距关注涨跌%" to components table**

When expanding an index to show components, add a column showing each stock's drift from watch_price.

**Step 4: Commit**

```
refactor: sector page adapts to tag-based index model
```

---

## Phase 6: Unified Alert System

### Task 10: WatchDriftTracker in stock_notifier.py

**Files:**
- Modify: `src/tools/stock_notifier.py` — after DeltaAlertEngine class (~line 287)

**Step 1: Implement WatchDriftTracker class**

```python
class WatchDriftTracker:
    """Tracks cumulative drift from watch_price for stocks and tag indices."""

    def __init__(self, db_path, alert_config_path):
        self._db_path = db_path
        self._alert_config_path = alert_config_path
        self._notified_tiers = {}  # {symbol_or_tag: set of triggered tier values}
        self._defaults = {"watch_drift_pct": 5, "watch_drift_enabled": True}

    def reset(self):
        """Daily reset at 08:00."""
        self._notified_tiers.clear()

    def check_stocks(self, quotes):
        """Check individual stock drift. Returns list of alert dicts."""
        alerts = []
        conn = sqlite3.connect(self._db_path)
        rows = conn.execute(
            "SELECT symbol, name, watch_price, tags, star, list_type, hidden "
            "FROM monitor_watchlist WHERE watch_price IS NOT NULL AND watch_price > 0"
        ).fetchall()
        conn.close()

        for symbol, name, wp, tags_json, star, list_type, hidden in rows:
            if hidden:
                continue
            q = quotes.get(symbol)
            if not q or not q.get("price"):
                continue
            price = q["price"]
            drift_pct = (price - wp) / wp * 100
            step = self._get_step(symbol)
            tier = self._calc_tier(drift_pct, step)
            if tier and tier not in self._notified_tiers.get(symbol, set()):
                self._notified_tiers.setdefault(symbol, set()).add(tier)
                level = 1 if star else (2 if list_type == "holding" else 3)
                direction = "涨" if drift_pct > 0 else "跌"
                alerts.append({
                    "symbol": symbol,
                    "_kind": "DRIFT",
                    "_level": level,
                    "_change_pct": round(drift_pct, 1),
                    "message": f"{name} 距关注{direction}{abs(tier)}%",
                    "display": f"📊 {name}({symbol}) 距关注价{wp:.2f}{direction}{abs(drift_pct):.1f}%，现价{price:.2f}",
                    "_stealth": f"{name} {direction}{abs(tier)}%",
                })
        return alerts

    def check_indices(self, index_values):
        """Check tag index drift. index_values = {tag: current_value}."""
        alerts = []
        conn = sqlite3.connect(self._db_path)
        tags = conn.execute(
            "SELECT tag, star, watch, baseline_value FROM tag_meta WHERE watch = 1"
        ).fetchall()
        conn.close()

        for tag, star, watch, baseline in tags:
            val = index_values.get(tag)
            if not val or not baseline:
                continue
            drift_pct = (val - baseline) / baseline * 100
            step = self._get_step(f"tag:{tag}")
            tier = self._calc_tier(drift_pct, step)
            key = f"tag:{tag}"
            if tier and tier not in self._notified_tiers.get(key, set()):
                self._notified_tiers.setdefault(key, set()).add(tier)
                level = 1 if star else 2
                direction = "涨" if drift_pct > 0 else "跌"
                alerts.append({
                    "symbol": key,
                    "_kind": "DRIFT",
                    "_level": level,
                    "_change_pct": round(drift_pct, 1),
                    "message": f"{tag}指数 距创建{direction}{abs(tier)}%",
                    "display": f"📊 {tag}指数 距创建{direction}{abs(drift_pct):.1f}%，当前{val:.1f}",
                    "_stealth": f"{tag} {direction}{abs(tier)}%",
                })
        return alerts

    def _get_step(self, key):
        """Get drift step % for a key (stock code or tag:name)."""
        cfg = read_json_safe(self._alert_config_path) or {}
        alerts = cfg.get("alerts", {})
        if key in alerts and "watch_drift_pct" in alerts[key]:
            return alerts[key]["watch_drift_pct"]
        defaults = cfg.get("defaults", {})
        return defaults.get("watch_drift_pct", self._defaults["watch_drift_pct"])

    def _calc_tier(self, drift_pct, step):
        """Return the tier value if drift crosses a new step boundary, else None."""
        if step <= 0:
            return None
        # Find which tier this drift belongs to
        tier_num = int(abs(drift_pct) / step)
        if tier_num == 0:
            return None
        tier_value = tier_num * step * (1 if drift_pct > 0 else -1)
        return tier_value
```

**Step 2: Integrate into run() main loop**

After DeltaAlertEngine and TradePlanEngine, init `drift_tracker = WatchDriftTracker(...)`.

In the main loop, after engine.check():
```python
drift_alerts = drift_tracker.check_stocks(quotes)
# index_values would need to be computed or read from sector_daily
# For now, stock drift is the priority
all_alerts.extend(drift_alerts)
```

**Step 3: Add DRIFT kind to write_alert_events()**

In the kind branch, add:
```python
elif kind == "DRIFT":
    display = a.get("display", a.get("message", ""))
```

**Step 4: Daily reset**

In the 08:00 reset block, add: `drift_tracker.reset()`

**Step 5: Commit**

```
feat: WatchDriftTracker for stock and index drift alerts
```

---

### Task 11: Migrate mainline detection to notifier

**Files:**
- Modify: `src/tools/stock_notifier.py` — add mainline check in run() loop
- Modify: `src/tools/sector_index_engine.py` — keep compute, remove alert dispatch

**Step 1: Add mainline check to notifier run()**

After daily close (15:30+), once per day:
- Read latest index values from `sector_daily`
- Run mainline detection logic (same regression algorithm)
- Produce alerts via write_alert_events() + stealth_dispatch()

This replaces the `detect_mainline()` call in `sector_index_engine.py` for alert dispatch. Engine still computes indices; notifier handles alerting.

**Step 2: Index alert level from tag_meta.star**

```python
level = 1 if tag_star else 2  # L1 for starred tags, L2 for watched
```

**Step 3: Commit**

```
feat: migrate mainline alert dispatch from sector engine to notifier
```

---

## Phase 7: Alerts Page

### Task 12: Alerts page — render DRIFT + index alerts

**Files:**
- Modify: `web/app/alerts/page.tsx` — `parseAlert()` (~line 59), `KIND_LABELS`

**Step 1: Add DRIFT to KIND_LABELS**

```typescript
DRIFT: { label: "涨跌追踪", color: "cyan" },
```

**Step 2: Add DRIFT parseAlert handler**

```typescript
if (kind === "DRIFT") {
  const direction = changePct > 0;
  return {
    signal: direction ? `距关注涨${Math.abs(changePct)}%` : `距关注跌${Math.abs(changePct)}%`,
    stockName: /* extract from display */,
    stockCode: symbol,
    price: /* extract from display */,
    detail: display,
    color: direction ? "green" : "red",
  };
}
```

**Step 3: Handle tag: prefix in symbol**

For alerts where symbol starts with `tag:`, display as index name instead of stock code.

**Step 4: Commit**

```
feat: alerts page renders DRIFT and index alerts
```

---

## Phase 8: Cleanup + Documentation

### Task 13: Delete sector_config.json, update CLAUDE.md

**Files:**
- Delete: `src/data/sector_config.json`
- Modify: `CLAUDE.md` — sector section, data model section

**Step 1: Remove sector_config.json references**

Search all files for `sector_config` imports/reads and ensure they've been replaced with DB queries.

**Step 2: Update CLAUDE.md**

- Update "板块轮动 & 自定义指数" section: document tag-based model
- Update "数据职责分离" table: remove sector_config.json, add tag_meta
- Update "SQLite 数据库" section: add tag_meta table
- Update monitor_config.json structure: add tags/watch_price fields

**Step 3: Commit**

```
docs: update CLAUDE.md for tag-based index architecture
```

---

### Task 14: E2E tests

**Files:**
- Modify: `web/screenshots/test_sector_e2e.mjs` — adapt to tag-based model
- Modify: `web/screenshots/test_full_checkup.mjs` — add tag assertions

**Step 1: Update sector E2E tests**

- Remove "create index with stocks" test (replaced by tag flow)
- Add: tag a stock on manage page → verify index appears on sector page
- Add: remove tag → verify index disappears when empty
- Add: star/watch toggle on tag index

**Step 2: Add tag tests to full checkup**

- Verify tags column visible on holdings/watching
- Verify tag filter works (click tag → filtered view)

**Step 3: Run full suite**

Run: `node web/screenshots/test_full_checkup.mjs`
Expected: All pass.

**Step 4: Commit**

```
test: update E2E tests for tag-based index model
```

---

## Execution Order + Dependencies

```
Phase 1: [Task 1] → [Task 2]           (schema + migration)
Phase 2: [Task 3] → [Task 4]           (API layer)
Phase 3: [Task 5]                       (manage UI)
         [Task 6]                       (holdings/watching UI) — parallel with Task 5
Phase 4: [Task 7] → [Task 8]           (engine + API refactor)
Phase 5: [Task 9]                       (sector page) — after Task 8
Phase 6: [Task 10] → [Task 11]         (alerts)
Phase 7: [Task 12]                      (alerts page) — after Task 10
Phase 8: [Task 13] → [Task 14]         (cleanup + tests)
```

Parallelizable pairs:
- Task 5 + Task 6 (manage UI + holdings/watching UI)
- Task 7 + Task 10 (engine refactor + drift tracker — independent modules)
