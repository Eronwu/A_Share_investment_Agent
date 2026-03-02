# Sector Rotation & Custom Index Design

> **Status Update (2026-03-02):** 本文档为历史设计文档。文中涉及的部分 UI 命名与导航示例（如旧 TabBar / `(node)` 文案）可能与当前实现不一致。落地时请以 `CLAUDE.md` 与当前页面代码为准。
>
Date: 2026-02-26

## Problem

A-share market is driven by sector rotation. Currently the dashboard tracks individual stocks but has no way to:
1. Visualize which sectors are leading/lagging across time
2. Create custom thematic indices (e.g. phosphorus chemical, power equipment) from selected stocks
3. Alert when a sector shows sustained uptrend (potential "main line" — 主线行情) vs one-day spike

## Solution

A new `/sector` page with two integrated modules:

1. **Sector Rotation Matrix** — daily ranking grid (东方财富 style) using EM industry/concept board data via akshare. Shows which boards ranked top-N each day, making rotation patterns visible.
2. **Custom Indices** — user-defined stock groups with equal-weight average change% as index value. Supports mainline alert detection (cumulative gain + linear regression slope).

## Architecture

```
[Daily 15:30 cron or manual trigger]
  │
  sector_index_engine.py (Python)
  ├── akshare: stock_board_industry_name_em()  → all industry boards today
  ├── akshare: stock_board_concept_name_em()   → all concept boards today
  ├── akshare: stock_zh_a_hist() per stock     → custom index components
  ├── Compute equal-weight avg change%         → custom index values
  ├── Linear regression + cumulative gain      → mainline detection
  └── Write to sim_trading.db
        │
        ▼
/api/sector (Next.js, read-only)
  ├── sector_rotation table → rotation matrix data
  ├── sector_daily table    → custom index history
  ├── sector_alerts table   → mainline alerts
  └── sector_config.json    → index definitions + alert rules
        │
        ▼
/sector page (new tab)
  ├── Rotation matrix (EM boards, horizontal scroll)
  ├── Custom index overview table (multi-timeframe)
  ├── Mainline alert log
  └── Index management (create/edit/delete)
```

## Data Model

### sector_config.json (user configuration)

```json
{
  "indices": {
    "phosphorus": {
      "name": "磷化工",
      "stocks": ["000792", "600096", "002895", "000902"],
      "created_at": "2026-02-26",
      "baseline_value": 100,
      "watch": true,
      "star": false
    },
    "power_equip": {
      "name": "电力设备",
      "stocks": ["601012", "300274", "002074"],
      "created_at": "2026-02-26",
      "baseline_value": 100,
      "watch": true,
      "star": false
    }
  },
  "alert_rules": {
    "cumulative_gain_pct": 8,
    "slope_threshold": 0.3,
    "lookback_days": 10,
    "min_days_since_create": 3
  },
  "rotation": {
    "category": "industry",
    "sort": "change_pct",
    "top_n": 10
  }
}
```

### SQLite Tables (in sim_trading.db)

```sql
-- EM board daily rotation data
CREATE TABLE IF NOT EXISTS sector_rotation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL,       -- 'industry' or 'concept'
    board_name TEXT NOT NULL,
    change_pct REAL,
    rank INTEGER,
    UNIQUE(date, category, board_name)
);
CREATE INDEX IF NOT EXISTS idx_sector_rot_date ON sector_rotation(date);
CREATE INDEX IF NOT EXISTS idx_sector_rot_cat_date ON sector_rotation(category, date);

-- Custom index daily values
CREATE TABLE IF NOT EXISTS sector_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    avg_change_pct REAL,          -- equal-weight average change%
    index_value REAL,             -- cumulative from baseline 100
    up_count INTEGER,             -- components that went up
    down_count INTEGER,           -- components that went down
    components_json TEXT,         -- per-stock change% detail
    UNIQUE(date, index_id)
);
CREATE INDEX IF NOT EXISTS idx_sector_daily_idx ON sector_daily(index_id);

-- Mainline alerts
CREATE TABLE IF NOT EXISTS sector_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    index_name TEXT,
    alert_type TEXT,              -- 'mainline', 'approaching', etc.
    cumulative_pct REAL,
    slope REAL,
    r_squared REAL,
    message TEXT,
    display TEXT,
    UNIQUE(date, index_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_sector_alerts_date ON sector_alerts(date);
```

Retention: 90 days for sector_rotation, 180 days for sector_daily, 30 days for sector_alerts.

## Sector Rotation Matrix

### Data Collection

Daily at 15:30, one akshare call per category:

```python
# All industry boards with today's change%
df = ak.stock_board_industry_name_em()
# Returns: 排名, 板块名称, 板块代码, 最新价, 涨跌幅, ...

# All concept boards
df = ak.stock_board_concept_name_em()
```

Sort by 涨跌幅 descending, assign rank, INSERT OR IGNORE into sector_rotation.

### Backfill

No per-board historical API needed. The table accumulates daily — after 30 days of running, the matrix shows 30 columns. For initial deployment, the matrix starts from day one.

### Web Display

```
排名   02-26       02-25       02-24       02-21       02-20
 1    元件        小金属      油服工程    ...
      +4.14%      +6.53%      +12.08%
 2    风电设备    能源金属    油气开采
      +2.58%      +5.99%      +7.53%
 3    通信设备    普钢        贵金属
      +2.30%      +4.50%      +7.01%
```

- Horizontal scroll for older dates (most recent on left)
- Top 3 ranks have colored badges (red/orange/yellow)
- All positive values in red, negative in green (A-share convention)
- Click a board name → bottom detail panel shows "近1月共N次进前10"

### Filters

| Filter | Options | Default |
|--------|---------|---------|
| Category | 行业 / 概念 | 行业 |
| Sort | 涨幅 / 跌幅 | 涨幅 |
| Top N | 前10名 / 前20名 | 前10名 |

## Custom Index System

### Index Calculation

Equal-weight average daily change%:

```
index_change_today = mean([stock_i_change_pct for stock_i in index.stocks])
index_value_today = index_value_yesterday * (1 + index_change_today / 100)
```

Baseline value = 100 at creation date. Historical values backfilled via akshare `stock_zh_a_hist()` for 30 trading days.

### Display

```
#  板块       今日    3日    5日    10日   累涨     状态
1  ★磷化工   +2.3%  +5.1%  +8.7%  +12.1% +14.2%  主线
2  电力设备   +1.8%  +3.2%  +4.1%  +6.3%  +7.1%   关注中
3  AI算力    -0.5%  +1.2%  +2.8%  +5.0%  +3.2%   关注中
```

- "累涨" = cumulative gain since index creation
- "状态": 主线 (mainline alert triggered) / 关注中 (watching) / 未关注
- Star indices sorted to top, shown with ★ prefix

### Multi-timeframe Change Calculation

N-day change = sum of last N trading days' `avg_change_pct` from sector_daily table. Computed in the API route, not stored.

### Management

| Action | Method |
|--------|--------|
| Create index | POST /api/sector — name + stock codes → backfill 30d → sector_config.json |
| Edit stocks | POST /api/sector — add/remove stock codes, recalculate from next day |
| Delete index | POST /api/sector — remove from config + DELETE sector_daily rows |
| Toggle watch | POST /api/sector — set watch: true/false |
| Toggle star | POST /api/sector — set star: true/false |

## Mainline Alert Detection

Runs daily after custom index values are computed.

### Algorithm

```python
for index in config.indices:
    if not index.watch:
        continue

    history = last_N_days(index_id, lookback_days)  # from sector_daily
    if len(history) < min_days_since_create:
        continue

    # 1. Cumulative gain since creation
    cum_gain = (latest_value / baseline_value - 1) * 100

    # 2. Linear regression slope on recent index_value
    days = [0, 1, 2, ..., N-1]
    values = [v.index_value for v in history]
    slope, intercept, r_squared = linear_regression(days, values)

    # 3. Decision
    if cum_gain >= cumulative_gain_pct and slope >= slope_threshold:
        alert_type = "mainline"
        message = f"{index.name} 触发主线: 累涨+{cum_gain:.1f}% 斜率{slope:.2f}"
    elif cum_gain >= cumulative_gain_pct * 0.75:
        alert_type = "approaching"
        message = f"{index.name} 接近主线: 累涨+{cum_gain:.1f}% (阈值{cumulative_gain_pct}%)"
```

### Default Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| cumulative_gain_pct | 8 | Cumulative gain threshold since watch start |
| slope_threshold | 0.3 | Linear regression slope minimum |
| lookback_days | 10 | Days for slope calculation |
| min_days_since_create | 3 | Minimum days before alerting (filter one-day spikes) |

### Notification Integration

Mainline alerts write to existing `alert_events` table via `write_alert_events()`:
- Default L2 (silent popup) for watched indices
- L1 (sound) for starred indices
- Visible on `/sector` page mainline section AND `/alerts` page

## Web Page Layout

```
┌─────────────────────────────────────────────────────────┐
│ ✱ monitor (node)                                         │
│ Claude Code │ A-share │ HK │ Sector │ ~ (-zsh)          │
├─────────────────────────────────────────────────────────┤
│ zhul1@mbp ~/projects/sector $ rotation                   │
│                                                          │
│ ── 板块轮动 ──  [行业 ▾] [涨幅 ▾] [前10名 ▾]           │
│                                                          │
│  排名   02-26      02-25      02-24      ...   (scroll) │
│   1    元件       小金属     油服工程                     │
│        +4.14%     +6.53%     +12.08%                     │
│   2    风电设备   能源金属   油气开采                     │
│        +2.58%     +5.99%     +7.53%                      │
│   ...                                                    │
│                                                          │
│  ▸ 元件  +4.14%  排名1  近1月 7次进前10                 │
│                                                          │
│ ── 我的指数 ──  [+ 新建]                                 │
│                                                          │
│  #  板块       今日    3日    5日    10日   累涨   状态   │
│  1  ★磷化工   +2.3%  +5.1%  +8.7%  +12.1% +14.2% 主线  │
│  2  电力设备   +1.8%  +3.2%  +4.1%  +6.3%  +7.1%  关注  │
│  3  AI算力    -0.5%  +1.2%  +2.8%  +5.0%  +3.2%  关注  │
│                                                          │
│ ── 主线告警 ──                                           │
│ [02-26 15:35] ★磷化工 触发主线: 累涨+14.2% 斜率0.45    │
│ [02-24 15:35] 电力设备 接近主线: 累涨+6.3%              │
│                                                          │
│ zhul1@mbp ~/projects/sector $                            │
└─────────────────────────────────────────────────────────┘
```

### Interaction Patterns

| Element | Action | Result |
|---------|--------|--------|
| Category dropdown | Click 行业/概念 | Reload rotation matrix |
| Sort dropdown | Click 涨幅/跌幅 | Reverse rank order |
| Top N dropdown | Click 前10/前20 | Show more/fewer rows |
| Board name in matrix | Click | Show detail panel at bottom of matrix |
| [+ 新建] button | Click | Modal: input name + comma-separated codes |
| Custom index row | Click | Expand to show component stocks |
| Star toggle | Click ★/☆ | Toggle star, re-sort |

### Dracula Terminal Style

Consistent with existing pages:
- Background: #282a36, text: #f8f8f2
- Positive values: red (#ff5555), negative: green (#50fa7b)
- Top-3 rank badges: red background with white text
- Board detail panel: #44475a background
- Monospace font throughout

## API Routes

### GET /api/sector

Returns all sector page data:

```typescript
{
  rotation: {
    dates: ["2026-02-26", "2026-02-25", ...],
    rows: [
      { rank: 1, cells: [{ board: "元件", change: 4.14 }, { board: "小金属", change: 6.53 }, ...] },
      ...
    ]
  },
  boardDetail: {  // for selected board, optional query param
    name: "元件",
    top10Count: 7,  // times in top 10 in last 30 days
    rankHistory: [{ date: "2026-02-26", rank: 1 }, ...]
  },
  indices: [
    {
      id: "phosphorus", name: "磷化工", star: true, watch: true,
      today: 2.3, d3: 5.1, d5: 8.7, d10: 12.1,
      cumGain: 14.2, status: "mainline",
      components: [{ code: "000792", name: "盐湖股份", change: 3.1 }, ...]
    },
    ...
  ],
  alerts: [
    { ts: 1740000000, date: "2026-02-26", indexName: "磷化工", type: "mainline", cumPct: 14.2, slope: 0.45, message: "..." },
    ...
  ],
  config: { alertRules: {...}, rotation: {...} }
}
```

### POST /api/sector

Manages custom indices (config write):

```typescript
// Create
{ action: "create", id: "phosphorus", name: "磷化工", stocks: ["000792", "600096"] }

// Update stocks
{ action: "update", id: "phosphorus", stocks: ["000792", "600096", "002895"] }

// Delete
{ action: "delete", id: "phosphorus" }

// Toggle watch/star
{ action: "watch", id: "phosphorus", value: true }
{ action: "star", id: "phosphorus", value: true }

// Update alert rules
{ action: "config", alertRules: { cumulative_gain_pct: 10, slope_threshold: 0.4 } }

// Update rotation filters
{ action: "config", rotation: { category: "concept", top_n: 20 } }
```

## Python Engine

### src/tools/sector_index_engine.py

```
class SectorIndexEngine:
    def run_daily():
        """Main entry — called by cron or manual trigger."""
        collect_rotation()      # EM board data
        compute_custom_indices() # custom index values
        detect_mainline()       # alert detection
        cleanup_old_data()      # retention policy

    def collect_rotation():
        """Fetch today's EM board rankings via akshare."""
        # ak.stock_board_industry_name_em() → DataFrame
        # ak.stock_board_concept_name_em() → DataFrame
        # INSERT OR IGNORE into sector_rotation

    def compute_custom_indices():
        """Calculate equal-weight avg change% for each custom index."""
        # For each index in sector_config.json:
        #   For each stock: get today's close from ak.stock_zh_a_hist()
        #   avg_change = mean(changes)
        #   index_value = prev_value * (1 + avg_change/100)
        #   INSERT into sector_daily

    def backfill(index_id, days=30):
        """Backfill historical data for a new index."""
        # ak.stock_zh_a_hist() for each stock, 30 days
        # Compute daily index values backwards

    def detect_mainline():
        """Check each watched index for mainline signal."""
        # Linear regression on recent index_value
        # Compare cumulative gain vs threshold
        # Write to sector_alerts + alert_events

    def cleanup_old_data():
        """Retention: 90d rotation, 180d daily, 30d alerts."""
```

### Rate Limiting

akshare API calls use existing `_call_with_retry()` pattern from stock_data_fetcher.py with 1-2s delays. Daily collection is ~3 calls (2 board lists + N custom index stocks). Backfill is the heaviest (N stocks * 1 call each), done only on index creation.

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/sim_trading/db.py` | Modify | Add 3 new tables DDL |
| `src/tools/sector_index_engine.py` | **New** | Daily cron engine |
| `src/data/sector_config.json` | **New** | Custom index config |
| `web/app/sector/page.tsx` | **New** | Sector page UI |
| `web/app/api/sector/route.ts` | **New** | GET/POST API route |
| `web/app/page.tsx` | Modify | Add "Sector" tab to TabBar |
| `CLAUDE.md` | Modify | Document new feature |

## Testing

1. `poetry run python -c "from src.sim_trading.db import init_db; init_db()"` → tables created
2. `poetry run python -m src.tools.sector_index_engine` → data collected
3. `sqlite3 src/data/sim_trading.db "SELECT COUNT(*) FROM sector_rotation"` → rows exist
4. `curl localhost:3120/api/sector | jq '.rotation.dates | length'` → dates returned
5. `/sector` page → rotation matrix renders, filters work
6. Create custom index → backfill runs, index appears in "我的指数"
7. Playwright full checkup → new section passes
