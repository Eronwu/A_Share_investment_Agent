# Sector Rotation & Custom Index — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/sector` page with EM board rotation matrix (东方财富 style) and user-defined custom indices with mainline alert detection.

**Architecture:** Python cron engine (`sector_index_engine.py`) collects EM board rankings + computes custom index values daily, stores in SQLite. Next.js `/api/sector` reads SQLite + `sector_config.json`. New `/sector` page renders rotation matrix, custom indices table, and mainline alerts in Dracula terminal style.

**Tech Stack:** Python (akshare, sqlite3, numpy), Next.js 15 (TypeScript, better-sqlite3), inline SVG for heatmap, Dracula theme.

**Design Doc:** `docs/plans/2026-02-26-sector-rotation-custom-index-design.md`

---

## Phase 1: Database Schema + Python Engine Foundation

### Task 1: Add SQLite tables to db.py

**Files:**
- Modify: `src/sim_trading/db.py` (append to SCHEMA string, line ~166)

**Step 1: Add DDL to SCHEMA**

In `src/sim_trading/db.py`, append these tables to the end of the `SCHEMA` string (before the closing `"""`):

```sql
-- 板块轮动排名（东方财富行业/概念板块每日排名）
CREATE TABLE IF NOT EXISTS sector_rotation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    board_name TEXT NOT NULL,
    change_pct REAL,
    rank INTEGER,
    UNIQUE(date, category, board_name)
);
CREATE INDEX IF NOT EXISTS idx_sector_rot_date ON sector_rotation(date);
CREATE INDEX IF NOT EXISTS idx_sector_rot_cat_date ON sector_rotation(category, date);

-- 自定义指数每日值
CREATE TABLE IF NOT EXISTS sector_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    avg_change_pct REAL,
    index_value REAL,
    up_count INTEGER,
    down_count INTEGER,
    components_json TEXT,
    UNIQUE(date, index_id)
);
CREATE INDEX IF NOT EXISTS idx_sector_daily_idx ON sector_daily(index_id);

-- 主线告警
CREATE TABLE IF NOT EXISTS sector_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    index_name TEXT,
    alert_type TEXT,
    cumulative_pct REAL,
    slope REAL,
    r_squared REAL,
    message TEXT,
    display TEXT,
    UNIQUE(date, index_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_sector_alerts_date ON sector_alerts(date);
```

**Step 2: Verify table creation**

Run: `poetry run python -c "from src.sim_trading.db import init_db; init_db(); print('OK')"`
Expected: `OK` (no errors)

Run: `sqlite3 src/data/sim_trading.db ".tables" | grep sector`
Expected: `sector_alerts  sector_daily  sector_rotation`

**Step 3: Commit**

```bash
git add src/sim_trading/db.py
git commit -m "feat(sector): add sector_rotation, sector_daily, sector_alerts tables"
```

---

### Task 2: Create sector_config.json

**Files:**
- Create: `src/data/sector_config.json`

**Step 1: Write initial config**

```json
{
  "indices": {},
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

Empty `indices` — users add via UI. `alert_rules` and `rotation` have sensible defaults.

**Step 2: Commit**

```bash
git add src/data/sector_config.json
git commit -m "feat(sector): add sector_config.json with default alert rules"
```

---

### Task 3: Build sector_index_engine.py — rotation collection

**Files:**
- Create: `src/tools/sector_index_engine.py`

**Step 1: Write the rotation collector**

```python
"""Sector Index Engine — daily collection of EM board rankings + custom index computation.

Usage:
  poetry run python -m src.tools.sector_index_engine              # full daily run
  poetry run python -m src.tools.sector_index_engine --rotation    # rotation only
  poetry run python -m src.tools.sector_index_engine --backfill <index_id>  # backfill custom index
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

import akshare as ak

from src.sim_trading.db import get_connection, init_db

logger = logging.getLogger("sector_engine")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "data" / "sector_config.json"

# Rate limiting: 1.5s between akshare calls
_CALL_DELAY = 1.5


def _read_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"indices": {}, "alert_rules": {}, "rotation": {}}


def _call_ak(fn, label: str = ""):
    """Call akshare with retry + delay."""
    for attempt in range(1, 4):
        try:
            time.sleep(_CALL_DELAY)
            result = fn()
            return result
        except Exception as e:
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning(f"{label} attempt {attempt} failed: {e}, retry in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"{label} all retries failed: {e}")
                return None


def collect_rotation(today: str | None = None):
    """Fetch today's EM board rankings (industry + concept) and store in DB."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    total = 0

    for category, fetch_fn, label in [
        ("industry", lambda: ak.stock_board_industry_name_em(), "行业板块"),
        ("concept", lambda: ak.stock_board_concept_name_em(), "概念板块"),
    ]:
        df = _call_ak(fetch_fn, label)
        if df is None or df.empty:
            logger.warning(f"{label}: no data returned")
            continue

        # Sort by 涨跌幅 descending, assign rank
        df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)

        for idx, row in df.iterrows():
            rank = idx + 1
            board_name = row.get("板块名称", "")
            change_pct = float(row.get("涨跌幅", 0) or 0)

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO sector_rotation
                       (date, category, board_name, change_pct, rank)
                       VALUES (?, ?, ?, ?, ?)""",
                    (today, category, board_name, change_pct, rank),
                )
                total += 1
            except Exception as e:
                logger.debug(f"Rotation insert error: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Rotation: {total} boards archived for {today}")
    return total
```

**Step 2: Verify rotation collection**

Run: `poetry run python -c "from src.tools.sector_index_engine import collect_rotation; n = collect_rotation(); print(f'{n} boards')"`
Expected: ~200+ boards (100+ industry + 100+ concept)

Run: `sqlite3 src/data/sim_trading.db "SELECT category, COUNT(*), MIN(rank), MAX(rank) FROM sector_rotation WHERE date = date('now') GROUP BY category"`
Expected: Two rows (industry ~90, concept ~100+)

**Step 3: Commit**

```bash
git add src/tools/sector_index_engine.py
git commit -m "feat(sector): rotation collector — fetch EM industry+concept board rankings"
```

---

### Task 4: Add custom index computation to engine

**Files:**
- Modify: `src/tools/sector_index_engine.py`

**Step 1: Add compute_custom_indices function**

Append after `collect_rotation`:

```python
def compute_custom_indices(today: str | None = None):
    """Calculate equal-weight avg change% for each custom index."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    config = _read_config()
    indices = config.get("indices", {})
    if not indices:
        return 0

    conn = get_connection()
    computed = 0

    for index_id, idx_cfg in indices.items():
        stocks = idx_cfg.get("stocks", [])
        if not stocks:
            continue

        # Fetch today's change% for each component stock
        components = []
        for code in stocks:
            df = _call_ak(
                lambda c=code: ak.stock_zh_a_hist(
                    symbol=c, period="daily",
                    start_date=today.replace("-", ""),
                    end_date=today.replace("-", ""),
                    adjust="qfq",
                ),
                f"hist_{code}",
            )
            if df is not None and not df.empty:
                change = float(df.iloc[-1].get("涨跌幅", 0) or 0)
                components.append({"code": code, "change": change})
            else:
                logger.warning(f"No data for {code} on {today}")

        if not components:
            continue

        changes = [c["change"] for c in components]
        avg_change = mean(changes)
        up_count = sum(1 for c in changes if c > 0)
        down_count = sum(1 for c in changes if c < 0)

        # Get previous index_value (or use baseline)
        prev_row = conn.execute(
            "SELECT index_value FROM sector_daily WHERE index_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (index_id, today),
        ).fetchone()
        prev_value = prev_row["index_value"] if prev_row else idx_cfg.get("baseline_value", 100)
        index_value = prev_value * (1 + avg_change / 100)

        try:
            conn.execute(
                """INSERT OR REPLACE INTO sector_daily
                   (date, index_id, avg_change_pct, index_value, up_count, down_count, components_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (today, index_id, avg_change, index_value, up_count, down_count,
                 json.dumps(components, ensure_ascii=False)),
            )
            computed += 1
        except Exception as e:
            logger.warning(f"Index {index_id} insert error: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Custom indices: {computed} computed for {today}")
    return computed
```

**Step 2: Add backfill function**

Append after `compute_custom_indices`:

```python
def backfill_index(index_id: str, days: int = 30):
    """Backfill historical data for a custom index using akshare daily K-line."""
    config = _read_config()
    idx_cfg = config.get("indices", {}).get(index_id)
    if not idx_cfg:
        logger.error(f"Index {index_id} not found in config")
        return 0

    stocks = idx_cfg.get("stocks", [])
    if not stocks:
        return 0

    end = datetime.now()
    start = end - timedelta(days=days + 10)  # extra buffer for non-trading days
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    # Fetch daily data for all component stocks
    stock_data: dict[str, dict[str, float]] = {}  # code -> {date: change%}
    for code in stocks:
        df = _call_ak(
            lambda c=code: ak.stock_zh_a_hist(
                symbol=c, period="daily",
                start_date=start_str, end_date=end_str,
                adjust="qfq",
            ),
            f"backfill_{code}",
        )
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                d = str(row["日期"])[:10]
                stock_data.setdefault(code, {})[d] = float(row.get("涨跌幅", 0) or 0)

    if not stock_data:
        return 0

    # Get all unique trading dates, sorted
    all_dates = sorted(set(d for sd in stock_data.values() for d in sd))
    if len(all_dates) > days:
        all_dates = all_dates[-days:]

    # Compute index values
    baseline = idx_cfg.get("baseline_value", 100)
    index_value = baseline
    conn = get_connection()
    filled = 0

    for date in all_dates:
        changes = []
        components = []
        for code in stocks:
            ch = stock_data.get(code, {}).get(date)
            if ch is not None:
                changes.append(ch)
                components.append({"code": code, "change": ch})

        if not changes:
            continue

        avg_change = mean(changes)
        index_value = index_value * (1 + avg_change / 100)
        up_count = sum(1 for c in changes if c > 0)
        down_count = sum(1 for c in changes if c < 0)

        try:
            conn.execute(
                """INSERT OR IGNORE INTO sector_daily
                   (date, index_id, avg_change_pct, index_value, up_count, down_count, components_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date, index_id, avg_change, index_value, up_count, down_count,
                 json.dumps(components, ensure_ascii=False)),
            )
            filled += 1
        except Exception as e:
            logger.debug(f"Backfill insert error: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Backfill {index_id}: {filled} days filled")
    return filled
```

**Step 3: Commit**

```bash
git add src/tools/sector_index_engine.py
git commit -m "feat(sector): custom index computation + 30-day backfill"
```

---

### Task 5: Add mainline detection + cleanup + CLI entry point

**Files:**
- Modify: `src/tools/sector_index_engine.py`

**Step 1: Add mainline detection**

Append after `backfill_index`:

```python
def detect_mainline():
    """Check each watched custom index for mainline signal."""
    config = _read_config()
    indices = config.get("indices", {})
    rules = config.get("alert_rules", {})

    cum_threshold = rules.get("cumulative_gain_pct", 8)
    slope_threshold = rules.get("slope_threshold", 0.3)
    lookback = rules.get("lookback_days", 10)
    min_days = rules.get("min_days_since_create", 3)

    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    alerts_emitted = 0

    for index_id, idx_cfg in indices.items():
        if not idx_cfg.get("watch", False):
            continue

        # Get recent history
        rows = conn.execute(
            "SELECT date, index_value, avg_change_pct FROM sector_daily "
            "WHERE index_id = ? ORDER BY date DESC LIMIT ?",
            (index_id, lookback),
        ).fetchall()

        if len(rows) < min_days:
            continue

        rows = list(reversed(rows))  # oldest first
        values = [r["index_value"] for r in rows]
        baseline = idx_cfg.get("baseline_value", 100)
        cum_gain = (values[-1] / baseline - 1) * 100

        # Linear regression: y = slope * x + intercept
        n = len(values)
        xs = list(range(n))
        x_mean = mean(xs)
        y_mean = mean(values)
        ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
        ss_xx = sum((x - x_mean) ** 2 for x in xs)
        ss_yy = sum((y - y_mean) ** 2 for y in values)

        slope = ss_xy / ss_xx if ss_xx > 0 else 0
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0

        # Decision
        alert_type = None
        if cum_gain >= cum_threshold and slope >= slope_threshold:
            alert_type = "mainline"
            msg = f"{idx_cfg['name']} 触发主线信号: 累涨{cum_gain:+.1f}% 斜率{slope:.2f} R²={r_squared:.2f}"
            display = f"★ {idx_cfg['name']} 主线行情确认 — 累计涨幅{cum_gain:+.1f}%，趋势斜率{slope:.2f}"
        elif cum_gain >= cum_threshold * 0.75:
            alert_type = "approaching"
            msg = f"{idx_cfg['name']} 接近主线: 累涨{cum_gain:+.1f}% (阈值{cum_threshold}%)"
            display = f"{idx_cfg['name']} 接近主线阈值 — 累涨{cum_gain:+.1f}% (距{cum_threshold}%差{cum_threshold - cum_gain:.1f}%)"

        if alert_type:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO sector_alerts
                       (ts, date, index_id, index_name, alert_type, cumulative_pct, slope, r_squared, message, display)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (int(time.time() * 1000), today, index_id, idx_cfg["name"],
                     alert_type, cum_gain, slope, r_squared, msg, display),
                )
                alerts_emitted += 1
                logger.info(f"Alert: {msg}")
            except Exception as e:
                logger.warning(f"Alert insert error: {e}")

    conn.commit()
    conn.close()
    return alerts_emitted


def cleanup_old_data():
    """Retention: 90d rotation, 180d sector_daily, 30d sector_alerts."""
    conn = get_connection()
    today = datetime.now()
    cutoffs = [
        ("sector_rotation", (today - timedelta(days=90)).strftime("%Y-%m-%d")),
        ("sector_daily", (today - timedelta(days=180)).strftime("%Y-%m-%d")),
        ("sector_alerts", (today - timedelta(days=30)).strftime("%Y-%m-%d")),
    ]
    for table, cutoff in cutoffs:
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE date < ?", (cutoff,))
            if cur.rowcount > 0:
                logger.info(f"Cleanup {table}: {cur.rowcount} rows deleted (before {cutoff})")
        except Exception as e:
            logger.debug(f"Cleanup {table} error: {e}")
    conn.commit()
    conn.close()


def run_daily():
    """Main entry — full daily run."""
    init_db()
    logger.info("=== Sector Index Engine: daily run ===")
    collect_rotation()
    compute_custom_indices()
    detect_mainline()
    cleanup_old_data()
    logger.info("=== Done ===")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Sector Index Engine")
    parser.add_argument("--rotation", action="store_true", help="Collect rotation data only")
    parser.add_argument("--indices", action="store_true", help="Compute custom indices only")
    parser.add_argument("--backfill", type=str, help="Backfill a custom index by ID")
    parser.add_argument("--detect", action="store_true", help="Run mainline detection only")
    args = parser.parse_args()

    init_db()

    if args.rotation:
        collect_rotation()
    elif args.indices:
        compute_custom_indices()
    elif args.backfill:
        backfill_index(args.backfill)
    elif args.detect:
        detect_mainline()
    else:
        run_daily()
```

**Step 2: Verify full run**

Run: `poetry run python -m src.tools.sector_index_engine --rotation`
Expected: logs showing ~200 boards archived

**Step 3: Commit**

```bash
git add src/tools/sector_index_engine.py
git commit -m "feat(sector): mainline detection + cleanup + CLI entry point"
```

---

## Phase 2: Web API

### Task 6: Create GET /api/sector route

**Files:**
- Create: `web/app/api/sector/route.ts`

**Step 1: Write the GET handler**

```typescript
import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import { readFileSync, writeFileSync, renameSync } from "fs";
import { join } from "path";
import { SIM_DB_PATH } from "../../lib/db";

const SECTOR_CONFIG_PATH = join(process.cwd(), "..", "src", "data", "sector_config.json");

export const dynamic = "force-dynamic";

interface RotationRow {
  date: string;
  category: string;
  board_name: string;
  change_pct: number;
  rank: number;
}

interface DailyRow {
  date: string;
  index_id: string;
  avg_change_pct: number;
  index_value: number;
  up_count: number;
  down_count: number;
  components_json: string;
}

interface AlertRow {
  ts: number;
  date: string;
  index_id: string;
  index_name: string;
  alert_type: string;
  cumulative_pct: number;
  slope: number;
  r_squared: number;
  message: string;
  display: string;
}

function readConfig(): Record<string, unknown> {
  try {
    return JSON.parse(readFileSync(SECTOR_CONFIG_PATH, "utf-8"));
  } catch {
    return { indices: {}, alert_rules: {}, rotation: {} };
  }
}

export async function GET(req: NextRequest) {
  try {
    const config = readConfig() as {
      indices: Record<string, { name: string; stocks: string[]; baseline_value: number; watch: boolean; star: boolean; created_at: string }>;
      alert_rules: Record<string, number>;
      rotation: { category: string; sort: string; top_n: number };
    };

    // Query params
    const sp = req.nextUrl.searchParams;
    const category = sp.get("category") || config.rotation?.category || "industry";
    const sortDir = sp.get("sort") || config.rotation?.sort || "change_pct";
    const topN = parseInt(sp.get("top_n") || String(config.rotation?.top_n || 10), 10);
    const boardQuery = sp.get("board") || "";

    const db = new Database(SIM_DB_PATH, { readonly: true });

    // ── Rotation matrix (last 30 trading days) ──
    const rotDates = db
      .prepare("SELECT DISTINCT date FROM sector_rotation WHERE category = ? ORDER BY date DESC LIMIT 30")
      .all(category) as { date: string }[];
    const dates = rotDates.map((r) => r.date);

    // Build rank → cells matrix
    const rotRows = db
      .prepare(
        `SELECT date, board_name, change_pct, rank FROM sector_rotation
         WHERE category = ? AND date IN (${dates.map(() => "?").join(",")})
         AND rank <= ?
         ORDER BY date DESC, rank ASC`
      )
      .all(category, ...dates, topN) as RotationRow[];

    // Group by rank
    const matrixMap = new Map<number, { board: string; change: number; date: string }[]>();
    for (const r of rotRows) {
      const rank = r.rank;
      if (!matrixMap.has(rank)) matrixMap.set(rank, []);
      matrixMap.get(rank)!.push({ board: r.board_name, change: r.change_pct, date: r.date });
    }

    const rows = Array.from({ length: topN }, (_, i) => {
      const rank = i + 1;
      const cells = dates.map((d) => {
        const cell = matrixMap.get(rank)?.find((c) => c.date === d);
        return cell ? { board: cell.board, change: cell.change } : { board: "", change: 0 };
      });
      return { rank, cells };
    });

    // ── Board detail (optional) ──
    let boardDetail = null;
    if (boardQuery) {
      const hist = db
        .prepare(
          "SELECT date, rank FROM sector_rotation WHERE category = ? AND board_name = ? ORDER BY date DESC LIMIT 30"
        )
        .all(category, boardQuery) as { date: string; rank: number }[];
      const top10Count = hist.filter((r) => r.rank <= 10).length;
      boardDetail = { name: boardQuery, top10Count, rankHistory: hist };
    }

    // ── Custom indices ──
    const indices = Object.entries(config.indices || {}).map(([id, cfg]) => {
      const dailyRows = db
        .prepare("SELECT date, avg_change_pct, index_value FROM sector_daily WHERE index_id = ? ORDER BY date DESC LIMIT 30")
        .all(id) as DailyRow[];

      const today = dailyRows[0]?.avg_change_pct ?? 0;
      const d3 = dailyRows.slice(0, 3).reduce((s, r) => s + (r.avg_change_pct || 0), 0);
      const d5 = dailyRows.slice(0, 5).reduce((s, r) => s + (r.avg_change_pct || 0), 0);
      const d10 = dailyRows.slice(0, 10).reduce((s, r) => s + (r.avg_change_pct || 0), 0);
      const latestValue = dailyRows[0]?.index_value ?? cfg.baseline_value;
      const cumGain = ((latestValue / cfg.baseline_value) - 1) * 100;

      // Check if mainline alert exists for today
      const alertRow = db
        .prepare("SELECT alert_type FROM sector_alerts WHERE index_id = ? ORDER BY date DESC LIMIT 1")
        .get(id) as { alert_type: string } | undefined;

      const status = alertRow?.alert_type === "mainline" ? "mainline"
        : alertRow?.alert_type === "approaching" ? "approaching"
        : cfg.watch ? "watching" : "inactive";

      // Components from latest day
      const compRow = db
        .prepare("SELECT components_json FROM sector_daily WHERE index_id = ? ORDER BY date DESC LIMIT 1")
        .get(id) as { components_json: string } | undefined;
      const components = compRow ? JSON.parse(compRow.components_json) : [];

      return {
        id, name: cfg.name, star: cfg.star || false, watch: cfg.watch || false,
        stocks: cfg.stocks, createdAt: cfg.created_at,
        today, d3, d5, d10, cumGain, status, components,
      };
    });

    // Sort: star first, then by cumGain descending
    indices.sort((a, b) => {
      if (a.star !== b.star) return a.star ? -1 : 1;
      return b.cumGain - a.cumGain;
    });

    // ── Alerts (last 30 days) ──
    const alerts = db
      .prepare("SELECT ts, date, index_id, index_name, alert_type, cumulative_pct, slope, r_squared, message, display FROM sector_alerts ORDER BY ts DESC LIMIT 50")
      .all() as AlertRow[];

    db.close();

    return NextResponse.json({
      rotation: { dates, rows },
      boardDetail,
      indices,
      alerts,
      config: { alertRules: config.alert_rules, rotation: config.rotation },
    });
  } catch (e) {
    return NextResponse.json({ error: String(e), rotation: { dates: [], rows: [] }, indices: [], alerts: [], config: {} });
  }
}
```

**Step 2: Verify**

Run: `curl -s http://localhost:3120/api/sector | python3 -m json.tool | head -20`
Expected: JSON with rotation.dates, rows, indices, alerts

**Step 3: Commit**

```bash
git add web/app/api/sector/route.ts
git commit -m "feat(sector): GET /api/sector — rotation matrix + custom indices + alerts"
```

---

### Task 7: Add POST /api/sector for index management

**Files:**
- Modify: `web/app/api/sector/route.ts`

**Step 1: Add POST handler**

Append to the same file:

```typescript
function writeConfig(cfg: Record<string, unknown>) {
  const tmp = SECTOR_CONFIG_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(cfg, null, 2) + "\n", "utf-8");
  renameSync(tmp, SECTOR_CONFIG_PATH);
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const action = body.action as string;
    const config = readConfig() as Record<string, Record<string, unknown>>;
    const indices = (config.indices || {}) as Record<string, Record<string, unknown>>;

    switch (action) {
      case "create": {
        const { id, name, stocks } = body as { id: string; name: string; stocks: string[] };
        if (!id || !name || !stocks?.length) {
          return NextResponse.json({ error: "id, name, stocks required" }, { status: 400 });
        }
        indices[id] = {
          name,
          stocks,
          created_at: new Date().toISOString().slice(0, 10),
          baseline_value: 100,
          watch: true,
          star: false,
        };
        config.indices = indices;
        writeConfig(config);
        return NextResponse.json({ ok: true, action: "created", id });
      }

      case "update": {
        const { id, stocks, name } = body as { id: string; stocks?: string[]; name?: string };
        if (!indices[id]) return NextResponse.json({ error: "index not found" }, { status: 404 });
        if (stocks) indices[id].stocks = stocks;
        if (name) indices[id].name = name;
        config.indices = indices;
        writeConfig(config);
        return NextResponse.json({ ok: true, action: "updated", id });
      }

      case "delete": {
        const { id } = body as { id: string };
        delete indices[id];
        config.indices = indices;
        writeConfig(config);
        // Also clean DB rows
        try {
          const db = new Database(SIM_DB_PATH);
          db.prepare("DELETE FROM sector_daily WHERE index_id = ?").run(id);
          db.prepare("DELETE FROM sector_alerts WHERE index_id = ?").run(id);
          db.close();
        } catch { /* ignore */ }
        return NextResponse.json({ ok: true, action: "deleted", id });
      }

      case "watch":
      case "star": {
        const { id, value } = body as { id: string; value: boolean };
        if (!indices[id]) return NextResponse.json({ error: "index not found" }, { status: 404 });
        indices[id][action] = value;
        config.indices = indices;
        writeConfig(config);
        return NextResponse.json({ ok: true, action, id, value });
      }

      case "config": {
        const { alertRules, rotation } = body as {
          alertRules?: Record<string, number>;
          rotation?: Record<string, unknown>;
        };
        if (alertRules) config.alert_rules = { ...((config.alert_rules || {}) as Record<string, unknown>), ...alertRules };
        if (rotation) config.rotation = { ...((config.rotation || {}) as Record<string, unknown>), ...rotation };
        writeConfig(config);
        return NextResponse.json({ ok: true, action: "config_updated" });
      }

      default:
        return NextResponse.json({ error: `unknown action: ${action}` }, { status: 400 });
    }
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
```

**Step 2: Verify**

Run: `curl -s -X POST http://localhost:3120/api/sector -H 'Content-Type: application/json' -d '{"action":"create","id":"test_idx","name":"测试","stocks":["000001","000002"]}' | python3 -m json.tool`
Expected: `{ "ok": true, "action": "created", "id": "test_idx" }`

Then clean up: `curl -s -X POST http://localhost:3120/api/sector -H 'Content-Type: application/json' -d '{"action":"delete","id":"test_idx"}'`

**Step 3: Commit**

```bash
git add web/app/api/sector/route.ts
git commit -m "feat(sector): POST /api/sector — create/update/delete/watch/star/config"
```

---

## Phase 3: Web UI

### Task 8: Add "Sector" tab to main page TabBar

**Files:**
- Modify: `web/app/page.tsx` (TabBar component, ~line 75)

**Step 1: Add Sector tab entry**

In the `tabs` array inside `TabBar`, add a Sector entry. Since Sector is a separate page (not a market tab), make it a Link instead of a tab switch. Change the tab entry with `key: null` for the last `~ (-zsh)` item and insert a `Sector (node)` before it:

Actually, looking at the existing pattern, the A-share and HK tabs switch `MarketTab` state within the same page. Sector is a different route (`/sector`). So it should be a Link, similar to how alerts/sim/manage are linked from the nav bar at the bottom.

Instead, add Sector as a Link-based tab (like the existing tab bar but navigating to `/sector`):

In the `tabs` array at line 75-80, change to:

```typescript
const tabs: { label: string; key: MarketTab | null; href?: string }[] = [
    { label: "Claude Code (node)", key: null },
    { label: "A-share (node)", key: "A" },
    { label: "HK (node)", key: "HK" },
    { label: "Sector (node)", key: null, href: "/sector" },
    { label: "~ (-zsh)", key: null },
  ];
```

Then in the render, wrap clickable tabs with Link when `href` is present:

```typescript
const clickable = t.key !== null || !!t.href;
// ...in onClick:
onClick={() => {
  if (t.key) onTabChange(t.key);
  // href handled by Link wrapper
}}
```

Actually, simpler approach — use `window.location` or Next.js `useRouter` for the Sector tab. But the cleanest pattern matching the existing `/alerts` `/sim` links at the bottom of the page is to just add a link in the tab bar.

Add the tab, and wrap with `<Link>` when `href` is present. Keep changes minimal.

**Step 2: Verify**

Visit `http://localhost:3120` — see "Sector (node)" tab in tab bar
Click it — navigates to `/sector`

**Step 3: Commit**

```bash
git add web/app/page.tsx
git commit -m "feat(sector): add Sector tab to main page tab bar"
```

---

### Task 9: Create /sector page — rotation matrix

**Files:**
- Create: `web/app/sector/page.tsx`

**Step 1: Write the page component**

Build the full `/sector` page with:
1. Rotation matrix (top section) — horizontal scroll table, rank badges, click-to-detail
2. Custom index table (middle section) — multi-timeframe columns
3. Mainline alert log (bottom section)
4. Dracula terminal style consistent with other pages

Key UI patterns to follow from existing pages:
- `D` color constants from `../theme`
- `Link` for navigation back to `/`
- Terminal prompt style (`zhul1@mbp ~/projects/sector $`)
- Inline styles (no CSS modules — matching project convention)
- `useEffect` + `fetch` + `useState` for data loading
- Loading state: `info Loading sector data...`
- Error state: red `[ERROR]` banner

The rotation matrix should use a grid/table with:
- Fixed left column for rank numbers (1-10 or 1-20)
- Scrollable area for date columns (most recent on left)
- Each cell: board name (bold) + change% below
- Rank 1-3: red/orange/yellow left border or badge
- Click cell → update detail panel below matrix

The complete page component is ~400 lines. Key sections:
- Types: `RotationData`, `IndexData`, `AlertData`
- State: `data`, `loading`, `error`, `selectedBoard`, `category`, `sortDir`, `topN`
- `fetchData()` with query params for filters
- `RotationMatrix` sub-component with horizontal scroll
- `CustomIndices` sub-component with multi-timeframe table
- `AlertLog` sub-component
- Filter dropdowns (styled as terminal select)

**Step 2: Verify**

Visit `http://localhost:3120/sector`
Expected: Page loads with rotation matrix (if data exists) or empty state, custom indices section, alert log

**Step 3: Commit**

```bash
git add web/app/sector/page.tsx
git commit -m "feat(sector): /sector page — rotation matrix + custom indices + alerts UI"
```

---

### Task 10: Add index creation modal + management interactions

**Files:**
- Modify: `web/app/sector/page.tsx`

**Step 1: Add create index modal**

Add a simple modal component triggered by `[+ 新建]` button:
- Input: 名称 (text) + 成分股代码 (comma-separated text)
- Auto-generate ID from name (pinyin or lowercase)
- POST to `/api/sector` with action: "create"
- On success, refresh data

**Step 2: Add star/watch toggles**

In the custom index table rows, add ★/☆ click handler that POSTs `action: "star"`.

**Step 3: Add delete button**

Each index row has a small `×` button at the end. Click → `confirm()` → POST `action: "delete"`.

**Step 4: Verify**

Click [+ 新建] → enter "测试" + "000001,000002" → Confirm → index appears
Click ★ → star toggles
Click × → confirm → index removed

**Step 5: Commit**

```bash
git add web/app/sector/page.tsx
git commit -m "feat(sector): index management — create/star/delete UI"
```

---

## Phase 4: Integration + Polish

### Task 11: Add Sector link to page navigation bars

**Files:**
- Modify: `web/app/page.tsx` (bottom nav links)
- Modify: `web/app/alerts/page.tsx` (bottom nav links)
- Modify: `web/app/sim/page.tsx` (bottom nav links)
- Modify: `web/app/manage/page.tsx` (bottom nav links)

**Step 1: Add Sector to bottom nav**

Each page has a bottom prompt area with links like `[alerts]` `[sim]` `[manage]`. Add `[sector]` to each.

Find the pattern (varies by page) and add a Link to `/sector`.

**Step 2: Verify**

Navigate between pages — all should have [sector] link. `/sector` should link back to `/`.

**Step 3: Commit**

```bash
git add web/app/page.tsx web/app/alerts/page.tsx web/app/sim/page.tsx web/app/manage/page.tsx
git commit -m "feat(sector): add sector link to all page navigation bars"
```

---

### Task 12: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add sector documentation**

Add a new section documenting:
- `/sector` page purpose and layout
- `sector_config.json` structure
- `sector_index_engine.py` usage (daily cron + CLI flags)
- SQLite tables (sector_rotation, sector_daily, sector_alerts) with retention
- API routes (GET/POST /api/sector)
- Mainline alert detection algorithm
- Integration with existing notification system

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add sector rotation & custom index feature documentation"
```

---

### Task 13: Verify end-to-end + collect initial data

**Step 1: Run rotation collection**

Run: `poetry run python -m src.tools.sector_index_engine --rotation`
Expected: ~200 boards archived

**Step 2: Verify API returns data**

Run: `curl -s http://localhost:3120/api/sector | python3 -m json.tool | head -30`
Expected: rotation.dates has today, rows have board names

**Step 3: Create a test custom index via API**

```bash
curl -s -X POST http://localhost:3120/api/sector \
  -H 'Content-Type: application/json' \
  -d '{"action":"create","id":"phosphorus","name":"磷化工","stocks":["000792","600096","002895"]}'
```

**Step 4: Backfill the index**

Run: `poetry run python -m src.tools.sector_index_engine --backfill phosphorus`
Expected: ~20-25 trading days backfilled

**Step 5: Verify custom index shows in API**

Run: `curl -s http://localhost:3120/api/sector | python3 -m json.tool | grep phosphorus`
Expected: index with today/d3/d5/d10 values

**Step 6: Visual verification**

Visit `http://localhost:3120/sector`:
- Rotation matrix shows today's board rankings
- 磷化工 index shows with multi-timeframe data
- Click board name → detail panel appears
- Filters (行业/概念, 涨幅/跌幅, 前10/20) work

**Step 7: Run Playwright checkup (if skill available)**

Extend `web/screenshots/test_full_checkup.mjs` with sector page tests.

---

## Summary

| Phase | Tasks | Key Deliverable |
|-------|-------|-----------------|
| 1: DB + Engine | Tasks 1-5 | SQLite tables + Python engine with rotation/indices/mainline/backfill |
| 2: Web API | Tasks 6-7 | GET/POST /api/sector |
| 3: Web UI | Tasks 8-10 | /sector page with rotation matrix, custom indices, management |
| 4: Integration | Tasks 11-13 | Navigation, docs, E2E verification |

Total: ~13 tasks, estimated commit count: 10-12.
