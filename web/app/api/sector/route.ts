import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import { readFileSync, writeFileSync, renameSync, existsSync } from "fs";
import { join } from "path";

import { SIM_DB_PATH } from "../../lib/db";

export const dynamic = "force-dynamic";

const SECTOR_CONFIG_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "sector_config.json",
);
const MONITOR_CONFIG_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "monitor_config.json",
);
const MARKET_DATA_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "market_data.json",
);

/* ── Types ── */

interface RotationRow {
  date: string;
  category: string;
  board_name: string;
  change_pct: number;
  rank: number;
}

interface SectorDailyRow {
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

interface IndexDef {
  id: string;
  name: string;
  stocks: string[];
  star: boolean;
  watch: boolean;
  created_at: string;
  baseline_value: number;
}

interface SectorConfig {
  indices: IndexDef[];
  alertRules: Record<string, unknown>;
  rotation: Record<string, unknown>;
}

interface ComponentEntry {
  code: string;
  change_pct: number;
  close: number | null;
  name: string;
}

/** Raw shape from Python engine's components_json in SQLite */
interface RawComponentEntry {
  code: string;
  change_pct: number;
  close: number | null;
  name?: string;
}

/* ── Config helpers ── */

function readSectorConfig(): SectorConfig {
  try {
    if (!existsSync(SECTOR_CONFIG_PATH)) {
      return { indices: [], alertRules: {}, rotation: {} };
    }
    const raw = JSON.parse(readFileSync(SECTOR_CONFIG_PATH, "utf-8"));
    // Config file stores indices as dict { id: {...} }, convert to array
    const indicesObj = raw.indices || {};
    const indices: IndexDef[] = Object.entries(indicesObj).map(([id, v]) => ({
      id,
      ...(v as Omit<IndexDef, "id">),
    }));
    return {
      indices,
      alertRules: raw.alert_rules || raw.alertRules || {},
      rotation: raw.rotation || {},
    };
  } catch {
    return { indices: [], alertRules: {}, rotation: {} };
  }
}

function writeSectorConfig(config: SectorConfig) {
  // Convert array back to dict format for Python compatibility
  const indicesObj: Record<string, Omit<IndexDef, "id">> = {};
  for (const idx of config.indices) {
    const { id, ...rest } = idx;
    indicesObj[id] = rest;
  }
  const out = {
    indices: indicesObj,
    alert_rules: config.alertRules,
    rotation: config.rotation,
  };
  const tmp = SECTOR_CONFIG_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(out, null, 2) + "\n", "utf-8");
  renameSync(tmp, SECTOR_CONFIG_PATH);
}

function round(n: number, d: number): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
}

/** Build code→name map from monitor_config.json + market_data.json (best effort) */
function buildStockNameMap(): Record<string, string> {
  const map: Record<string, string> = {};
  try {
    if (existsSync(MONITOR_CONFIG_PATH)) {
      const cfg = JSON.parse(readFileSync(MONITOR_CONFIG_PATH, "utf-8"));
      const wl = cfg.watchlist || {};
      for (const [code, entry] of Object.entries(wl)) {
        const e = entry as { name?: string };
        if (e.name) map[code] = e.name;
      }
    }
  } catch { /* ignore */ }
  try {
    if (existsSync(MARKET_DATA_PATH)) {
      const md = JSON.parse(readFileSync(MARKET_DATA_PATH, "utf-8"));
      const services = md.services;
      if (Array.isArray(services)) {
        for (const s of services) {
          if (s.id && s.name && !map[s.id]) map[s.id] = s.name;
        }
      }
    }
  } catch { /* ignore */ }
  return map;
}

/* ── Sina board helpers ── */

const SINA_URLS: Record<string, string> = {
  industry: "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
  concept: "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php",
};

const SINA_NODE_URL =
  "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeDataSimple";

// Cache board name→code mapping (refreshed with live rotation)
let _boardCodeMap: Record<string, Record<string, string>> = {}; // category → {name: code}

interface BoardStock {
  code: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  volume: number;
  amount: number;
}

async function fetchBoardStocks(boardName: string, category: string): Promise<BoardStock[]> {
  // Find the Sina board code for this name
  let codeMap = _boardCodeMap[category];
  if (!codeMap || !codeMap[boardName]) {
    // Refresh the code map from Sina board list
    const sinaUrl = SINA_URLS[category];
    if (!sinaUrl) return [];
    try {
      const resp = await fetch(sinaUrl, {
        headers: { "User-Agent": "Mozilla/5.0" },
        signal: AbortSignal.timeout(5000),
      });
      const buf = await resp.arrayBuffer();
      const text = new TextDecoder("gbk").decode(buf);
      const match = text.match(/=\s*(\{[\s\S]*\})/);
      if (match) {
        const data: Record<string, string> = JSON.parse(match[1]);
        codeMap = {};
        for (const [code, val] of Object.entries(data)) {
          const name = val.split(",")[1];
          if (name) codeMap[name] = code;
        }
        _boardCodeMap[category] = codeMap;
      }
    } catch {
      return [];
    }
  }

  const boardCode = codeMap?.[boardName];
  if (!boardCode) return [];

  // Fetch constituent stocks
  try {
    const resp = await fetch(
      `${SINA_NODE_URL}?node=${encodeURIComponent(boardCode)}&page=1&num=40`,
      { headers: { "User-Agent": "Mozilla/5.0" }, signal: AbortSignal.timeout(5000) },
    );
    const text = await resp.text();
    const stocks: Array<Record<string, string>> = JSON.parse(text);
    return stocks.map((s) => ({
      code: s.symbol || "",
      name: s.name || "",
      price: parseFloat(s.trade) || 0,
      change: parseFloat(s.pricechange) || 0,
      changePct: parseFloat(s.changepercent) || 0,
      volume: parseFloat(s.volume) || 0,
      amount: parseFloat(s.amount) || 0,
    })).sort((a, b) => b.changePct - a.changePct);
  } catch {
    return [];
  }
}

/* ── Live Sina fetch (real-time board rankings during trading hours) ── */

// Throttle: at most once per 60s per category
const _liveCache: Record<string, { ts: number }> = {};
const LIVE_INTERVAL_MS = 60_000;

async function refreshLiveRotation(category: string): Promise<void> {
  // Only during A-share trading hours (roughly 9:15-15:05 CST)
  const now = new Date();
  const hhmm = now.getHours() * 100 + now.getMinutes();
  const weekday = now.getDay();
  if (weekday === 0 || weekday === 6 || hhmm < 915 || hhmm > 1505) return;

  // Throttle
  const cacheKey = category;
  const last = _liveCache[cacheKey]?.ts || 0;
  if (Date.now() - last < LIVE_INTERVAL_MS) return;

  const sinaUrl = SINA_URLS[category];
  if (!sinaUrl) return;

  try {
    const resp = await fetch(sinaUrl, {
      headers: { "User-Agent": "Mozilla/5.0" },
      signal: AbortSignal.timeout(8000),
    });
    if (!resp.ok) return;
    // Sina returns GBK-encoded text, decode properly
    const buf = await resp.arrayBuffer();
    const text = new TextDecoder("gbk").decode(buf);
    const match = text.match(/=\s*(\{[\s\S]*\})/);
    if (!match) return;
    const data: Record<string, string> = JSON.parse(match[1]);

    // Parse boards: value = "code,name,count,avg_price,change_amt,change_pct,..."
    const boards: Array<{ name: string; pct: number }> = [];
    for (const val of Object.values(data)) {
      const parts = val.split(",");
      if (parts.length >= 6) {
        boards.push({ name: parts[1], pct: parseFloat(parts[5]) || 0 });
      }
    }
    if (boards.length === 0) return;

    boards.sort((a, b) => b.pct - a.pct);
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

    // Write to DB (need writable connection)
    const wdb = new Database(SIM_DB_PATH);
    try {
      // Delete today's data for this category and re-insert (ranks may have changed)
      wdb.prepare("DELETE FROM sector_rotation WHERE date = ? AND category = ?").run(today, category);
      const ins = wdb.prepare(
        "INSERT OR IGNORE INTO sector_rotation (date, category, board_name, change_pct, rank) VALUES (?, ?, ?, ?, ?)"
      );
      const tx = wdb.transaction(() => {
        for (let i = 0; i < boards.length; i++) {
          ins.run(today, category, boards[i].name, Math.round(boards[i].pct * 10000) / 10000, i + 1);
        }
      });
      tx();
    } finally {
      wdb.close();
    }

    _liveCache[cacheKey] = { ts: Date.now() };
  } catch {
    // Sina fetch failed — silently use stale SQLite data
  }
}

/* ── GET /api/sector ── */

export async function GET(request: NextRequest) {
  let db: InstanceType<typeof Database> | null = null;
  try {
    const url = request.nextUrl;
    const category = url.searchParams.get("category") || "industry";
    const sort = url.searchParams.get("sort") || "change_pct";
    const topN = Math.min(Math.max(parseInt(url.searchParams.get("top_n") || "10", 10) || 10, 1), 50);
    const boardFilter = url.searchParams.get("board") || null;

    // Refresh live data from Sina (throttled, trading hours only)
    await refreshLiveRotation(category);

    db = new Database(SIM_DB_PATH, { readonly: true });

    // ── Rotation matrix ──

    // Get distinct dates for this category
    const dateRows = db
      .prepare(
        `SELECT DISTINCT date FROM sector_rotation
         WHERE category = ?
         ORDER BY date DESC LIMIT 30`,
      )
      .all(category) as { date: string }[];

    const dates = dateRows.map((r) => r.date);

    // Build rotation rows per date
    const sortAsc = sort === "drop_pct";
    const rotationRows: Array<{
      rank: number;
      cells: Array<{ board: string; change: number }>;
    }> = [];

    if (dates.length > 0) {
      const datePlaceholders = dates.map(() => "?").join(",");

      if (sortAsc) {
        // 跌幅排序: query all boards per date, sort by change_pct ASC, take top N
        for (let displayRank = 1; displayRank <= topN; displayRank++) {
          const cells: Array<{ board: string; change: number }> = [];
          for (const date of dates) {
            const row = db
              .prepare(
                `SELECT board_name, change_pct FROM sector_rotation
                 WHERE category = ? AND date = ?
                 ORDER BY change_pct ASC
                 LIMIT 1 OFFSET ?`,
              )
              .get(category, date, displayRank - 1) as
              | { board_name: string; change_pct: number }
              | undefined;
            cells.push({
              board: row?.board_name ?? "",
              change: row ? round(row.change_pct, 2) : 0,
            });
          }
          rotationRows.push({ rank: displayRank, cells });
        }
      } else {
        // 涨幅排序 (default): use stored rank
        const allRows = db
          .prepare(
            `SELECT date, board_name, change_pct, rank FROM sector_rotation
             WHERE category = ? AND date IN (${datePlaceholders}) AND rank <= ?
             ORDER BY date DESC, rank ASC`,
          )
          .all(category, ...dates, topN) as RotationRow[];

        // Group by rank → cells aligned with dates
        const byRank = new Map<
          number,
          Map<string, { board: string; change: number }>
        >();
        for (const row of allRows) {
          if (!byRank.has(row.rank)) byRank.set(row.rank, new Map());
          byRank.get(row.rank)!.set(row.date, {
            board: row.board_name,
            change: round(row.change_pct, 2),
          });
        }

        for (let rank = 1; rank <= topN; rank++) {
          const dateMap = byRank.get(rank);
          if (!dateMap) continue;
          const cells = dates.map((d) => dateMap.get(d) ?? { board: "", change: 0 });
          rotationRows.push({ rank, cells });
        }
      }
    }

    // ── Board detail ──

    let boardDetail: {
      name: string;
      top10Count: number;
      rankHistory: Array<{ date: string; rank: number }>;
      stocks: BoardStock[];
    } | null = null;

    if (boardFilter) {
      const boardRows = db
        .prepare(
          `SELECT date, rank, change_pct FROM sector_rotation
           WHERE category = ? AND board_name = ?
           ORDER BY date DESC LIMIT 30`,
        )
        .all(category, boardFilter) as {
        date: string;
        rank: number;
        change_pct: number;
      }[];

      const top10Count = boardRows.filter((r) => r.rank <= 10).length;

      // Fetch constituent stocks from Sina
      const stocks = await fetchBoardStocks(boardFilter, category);

      boardDetail = {
        name: boardFilter,
        top10Count,
        rankHistory: boardRows.map((r) => ({
          date: r.date,
          rank: r.rank,
        })),
        stocks,
      };
    }

    // ── Custom indices ──

    const config = readSectorConfig();
    const stockNameMap = buildStockNameMap();
    const indices: Array<{
      id: string;
      name: string;
      star: boolean;
      watch: boolean;
      stocks: string[];
      createdAt: string;
      today: number;
      d3: number;
      d5: number;
      d10: number;
      cumGain: number;
      status: string;
      components: ComponentEntry[];
      history: Array<{ date: string; change: number; value: number }>;
    }> = [];

    for (const idx of config.indices) {
      const dailyRows = db
        .prepare(
          `SELECT date, avg_change_pct, index_value, components_json
           FROM sector_daily
           WHERE index_id = ?
           ORDER BY date DESC LIMIT 30`,
        )
        .all(idx.id) as SectorDailyRow[];

      const today = dailyRows.length > 0 ? round(dailyRows[0].avg_change_pct, 2) : 0;
      const d3 = round(
        dailyRows.slice(0, 3).reduce((s, r) => s + r.avg_change_pct, 0),
        2,
      );
      const d5 = round(
        dailyRows.slice(0, 5).reduce((s, r) => s + r.avg_change_pct, 0),
        2,
      );
      const d10 = round(
        dailyRows.slice(0, 10).reduce((s, r) => s + r.avg_change_pct, 0),
        2,
      );

      const baseline = idx.baseline_value || 100;
      const latestValue =
        dailyRows.length > 0 ? dailyRows[0].index_value : baseline;
      const cumGain = round(((latestValue / baseline) - 1) * 100, 2);

      // Status from latest alert
      let status: string = "watching";
      try {
        const latestAlert = db
          .prepare(
            `SELECT alert_type FROM sector_alerts
             WHERE index_id = ?
             ORDER BY ts DESC LIMIT 1`,
          )
          .get(idx.id) as { alert_type: string } | undefined;
        if (latestAlert) {
          status = latestAlert.alert_type;
        }
      } catch {
        /* table may not exist */
      }

      // Components from latest row, enriched with stock names
      let components: ComponentEntry[] = [];
      if (dailyRows.length > 0 && dailyRows[0].components_json) {
        try {
          const raw: RawComponentEntry[] = JSON.parse(dailyRows[0].components_json);
          components = raw.map((c) => ({
            code: c.code,
            change_pct: c.change_pct,
            close: c.close ?? null,
            name: c.name || stockNameMap[c.code] || "",
          }));
        } catch {
          /* invalid json */
        }
      }

      // Daily history (chronological, newest last) for matrix view
      const history = dailyRows
        .map((r) => ({
          date: r.date,
          change: round(r.avg_change_pct, 2),
          value: round(r.index_value, 2),
        }))
        .reverse();

      indices.push({
        id: idx.id,
        name: idx.name,
        star: idx.star ?? false,
        watch: idx.watch ?? true,
        stocks: idx.stocks,
        createdAt: idx.created_at,
        today,
        d3,
        d5,
        d10,
        cumGain,
        status,
        components,
        history,
      });
    }

    // Sort: star first, then by cumGain descending
    indices.sort((a, b) => {
      if (a.star !== b.star) return a.star ? -1 : 1;
      return b.cumGain - a.cumGain;
    });

    // ── Alerts ──

    let alerts: AlertRow[] = [];
    try {
      alerts = db
        .prepare(
          `SELECT ts, date, index_id, index_name, alert_type,
                  cumulative_pct, slope, r_squared, message, display
           FROM sector_alerts
           ORDER BY ts DESC LIMIT 50`,
        )
        .all() as AlertRow[];
    } catch {
      /* table may not exist */
    }

    db.close();
    db = null;

    return NextResponse.json({
      rotation: { dates, rows: rotationRows },
      boardDetail,
      indices,
      alerts,
      config: {
        alertRules: config.alertRules,
        rotation: config.rotation,
      },
    });
  } catch (e) {
    return NextResponse.json(
      {
        error: String(e),
        rotation: { dates: [], rows: [] },
        boardDetail: null,
        indices: [],
        alerts: [],
        config: {},
      },
      { status: 500 },
    );
  } finally {
    try {
      db?.close();
    } catch {
      /* already closed */
    }
  }
}

/* ── POST /api/sector ── */

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action } = body as { action: string };
    const config = readSectorConfig();

    switch (action) {
      case "create": {
        const { id, name, stocks } = body as {
          id: string;
          name: string;
          stocks: string[];
        };
        if (!id || !name || !stocks?.length) {
          return NextResponse.json(
            { error: "Missing id, name, or stocks" },
            { status: 400 },
          );
        }
        if (config.indices.some((idx) => idx.id === id)) {
          return NextResponse.json(
            { error: `Index ${id} already exists` },
            { status: 400 },
          );
        }
        config.indices.push({
          id,
          name,
          stocks,
          star: false,
          watch: true,
          created_at: new Date().toISOString(),
          baseline_value: 100,
        });
        writeSectorConfig(config);
        return NextResponse.json({ ok: true, action: "create", id });
      }

      case "update": {
        const { id, stocks, name } = body as {
          id: string;
          stocks?: string[];
          name?: string;
        };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        const idx = config.indices.find((i) => i.id === id);
        if (!idx) {
          return NextResponse.json(
            { error: `Index ${id} not found` },
            { status: 404 },
          );
        }
        if (stocks) idx.stocks = stocks;
        if (name) idx.name = name;
        writeSectorConfig(config);
        return NextResponse.json({ ok: true, action: "update", id });
      }

      case "delete": {
        const { id } = body as { id: string };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        const before = config.indices.length;
        config.indices = config.indices.filter((i) => i.id !== id);
        if (config.indices.length === before) {
          return NextResponse.json(
            { error: `Index ${id} not found` },
            { status: 404 },
          );
        }
        writeSectorConfig(config);

        // Clean up DB rows (need write mode)
        let db: InstanceType<typeof Database> | null = null;
        try {
          db = new Database(SIM_DB_PATH);
          db.prepare("DELETE FROM sector_daily WHERE index_id = ?").run(id);
          db.prepare("DELETE FROM sector_alerts WHERE index_id = ?").run(id);
        } catch {
          /* tables may not exist */
        } finally {
          try {
            db?.close();
          } catch {
            /* already closed */
          }
        }

        return NextResponse.json({ ok: true, action: "delete", id });
      }

      case "watch": {
        const { id, value } = body as { id: string; value: boolean };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        const idx = config.indices.find((i) => i.id === id);
        if (!idx) {
          return NextResponse.json(
            { error: `Index ${id} not found` },
            { status: 404 },
          );
        }
        idx.watch = !!value;
        writeSectorConfig(config);
        return NextResponse.json({ ok: true, action: "watch", id });
      }

      case "star": {
        const { id, value } = body as { id: string; value: boolean };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        const idx = config.indices.find((i) => i.id === id);
        if (!idx) {
          return NextResponse.json(
            { error: `Index ${id} not found` },
            { status: 404 },
          );
        }
        idx.star = !!value;
        writeSectorConfig(config);
        return NextResponse.json({ ok: true, action: "star", id });
      }

      case "config": {
        const { alertRules, rotation } = body as {
          alertRules?: Record<string, unknown>;
          rotation?: Record<string, unknown>;
        };
        if (alertRules) {
          config.alertRules = { ...config.alertRules, ...alertRules };
        }
        if (rotation) {
          config.rotation = { ...config.rotation, ...rotation };
        }
        writeSectorConfig(config);
        return NextResponse.json({ ok: true, action: "config" });
      }

      default:
        return NextResponse.json(
          { error: `Unknown action: ${action}` },
          { status: 400 },
        );
    }
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
