import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import { readFileSync, writeFileSync, renameSync, existsSync } from "fs";
import { join } from "path";

import { SIM_DB_PATH } from "../../lib/db";

export const dynamic = "force-dynamic";

const MARKET_DATA_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "market_data.json",
);
const SECTOR_CONFIG_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "sector_config.json",
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

interface TagMetaRow {
  tag: string;
  star: number;
  watch: number;
  baseline_value: number;
  parent: string | null;
  created_at: string;
}

interface WatchlistTagRow {
  symbol: string;
  name: string;
  tags: string;
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

/* ── Helpers ── */

function round(n: number, d: number): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
}

/** Read alert_rules + rotation from sector_config.json (minimal reader, no indices) */
function readSectorConfigMeta(): {
  alertRules: Record<string, unknown>;
  rotation: Record<string, unknown>;
} {
  try {
    if (!existsSync(SECTOR_CONFIG_PATH)) return { alertRules: {}, rotation: {} };
    const raw = JSON.parse(readFileSync(SECTOR_CONFIG_PATH, "utf-8"));
    return {
      alertRules: raw.alert_rules || raw.alertRules || {},
      rotation: raw.rotation || {},
    };
  } catch {
    return { alertRules: {}, rotation: {} };
  }
}

/** Build code→name map from market_data.json (best effort) */
function buildStockNameMap(): Record<string, string> {
  const map: Record<string, string> = {};
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

    // ── Custom indices (tag-aggregated from DB) ──

    const tagRows = db
      .prepare("SELECT tag, star, watch, baseline_value, parent, created_at FROM tag_meta")
      .all() as TagMetaRow[];

    const stockRows = db
      .prepare(
        `SELECT symbol, name, tags FROM monitor_watchlist
         WHERE hidden = 0 AND tags IS NOT NULL AND tags != '[]'`,
      )
      .all() as WatchlistTagRow[];

    // Build tag → stocks map
    const tagStocks: Record<string, { code: string; name: string }[]> = {};
    for (const row of stockRows) {
      try {
        const tags: string[] = JSON.parse(row.tags || "[]");
        for (const tag of tags) {
          if (!tagStocks[tag]) tagStocks[tag] = [];
          tagStocks[tag].push({ code: row.symbol, name: row.name });
        }
      } catch { /* invalid json */ }
    }

    const stockNameMap = buildStockNameMap();

    // Build parent→children mapping
    const parentChildren: Record<string, string[]> = {};
    const tagMetaMap: Record<string, TagMetaRow> = {};
    for (const tag of tagRows) {
      tagMetaMap[tag.tag] = tag;
      if (tag.parent) {
        if (!parentChildren[tag.parent]) parentChildren[tag.parent] = [];
        parentChildren[tag.parent].push(tag.tag);
      }
    }

    // For parent tags, aggregate children's stocks (deduplicated)
    const parentStocks: Record<string, { code: string; name: string }[]> = {};
    for (const [parentTag, children] of Object.entries(parentChildren)) {
      const seen = new Set<string>();
      const agg: { code: string; name: string }[] = [];
      for (const child of children) {
        for (const s of tagStocks[child] || []) {
          if (!seen.has(s.code)) {
            seen.add(s.code);
            agg.push(s);
          }
        }
      }
      parentStocks[parentTag] = agg;
    }

    type IndexItem = {
      id: string;
      name: string;
      star: boolean;
      watch: boolean;
      stocks: string[];
      stockCount: number;
      createdAt: string;
      today: number;
      d3: number;
      d5: number;
      d10: number;
      cumGain: number;
      status: string;
      components: ComponentEntry[];
      history: Array<{ date: string; change: number; value: number }>;
      parent: string | null;
      isParent: boolean;
      children: string[];
    };

    const indices: IndexItem[] = [];

    for (const tag of tagRows) {
      const tagName = tag.tag;
      const isParentTag = !tag.parent && !!parentChildren[tagName];
      const stockList = isParentTag
        ? parentStocks[tagName] || []
        : tagStocks[tagName] || [];
      const stockCodes = stockList.map((s) => s.code);

      // Skip tags that have no stocks (and are not parent tags with children)
      if (stockList.length === 0 && !isParentTag) continue;

      const dailyRows = db
        .prepare(
          `SELECT date, avg_change_pct, index_value, components_json
           FROM sector_daily
           WHERE index_id = ?
           ORDER BY date DESC LIMIT 30`,
        )
        .all(tagName) as SectorDailyRow[];

      const todayVal = dailyRows.length > 0 ? round(dailyRows[0].avg_change_pct, 2) : 0;
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

      const baseline = tag.baseline_value || 100;
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
          .get(tagName) as { alert_type: string } | undefined;
        if (latestAlert) {
          status = latestAlert.alert_type;
        }
      } catch { /* table may not exist */ }

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
        } catch { /* invalid json */ }
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
        id: tagName,
        name: tagName,
        star: !!tag.star,
        watch: !!tag.watch,
        stocks: stockCodes,
        stockCount: stockList.length,
        createdAt: tag.created_at,
        today: todayVal,
        d3,
        d5,
        d10,
        cumGain,
        status,
        components,
        history,
        parent: tag.parent || null,
        isParent: isParentTag,
        children: isParentTag ? (parentChildren[tagName] || []) : [],
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
    } catch { /* table may not exist */ }

    db.close();
    db = null;

    // Read config meta (alert_rules, rotation) from sector_config.json
    const configMeta = readSectorConfigMeta();

    return NextResponse.json({
      rotation: { dates, rows: rotationRows },
      boardDetail,
      indices,
      alerts,
      config: {
        alertRules: configMeta.alertRules,
        rotation: configMeta.rotation,
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
    } catch { /* already closed */ }
  }
}

/* ── POST /api/sector ── */

export async function POST(request: NextRequest) {
  let db: InstanceType<typeof Database> | null = null;
  try {
    const body = await request.json();
    const { action } = body as { action: string };

    switch (action) {
      case "create-tag": {
        const { tag, parent: parentTag } = body as { tag: string; parent?: string | null };
        if (!tag || !tag.trim()) {
          return NextResponse.json(
            { error: "Missing tag name" },
            { status: 400 },
          );
        }
        db = new Database(SIM_DB_PATH);
        // Check if already exists
        const existing = db
          .prepare("SELECT tag FROM tag_meta WHERE tag = ?")
          .get(tag.trim());
        if (existing) {
          db.close();
          return NextResponse.json(
            { error: `Tag "${tag}" already exists` },
            { status: 400 },
          );
        }
        db.prepare(
          `INSERT INTO tag_meta (tag, star, watch, baseline_value, parent, created_at, updated_at)
           VALUES (?, 0, 1, 100, ?, datetime('now'), datetime('now'))`,
        ).run(tag.trim(), parentTag?.trim() || null);
        db.close();
        db = null;
        return NextResponse.json({ ok: true, action: "create-tag", tag: tag.trim() });
      }

      case "set-parent": {
        const { tag: spTag, parent: spParent } = body as { tag: string; parent: string | null };
        if (!spTag) {
          return NextResponse.json(
            { error: "Missing tag" },
            { status: 400 },
          );
        }
        db = new Database(SIM_DB_PATH);
        const spResult = db
          .prepare("UPDATE tag_meta SET parent = ?, updated_at = datetime('now') WHERE tag = ?")
          .run(spParent?.trim() || null, spTag);
        db.close();
        db = null;
        if (spResult.changes === 0) {
          return NextResponse.json(
            { error: `Tag "${spTag}" not found` },
            { status: 404 },
          );
        }
        return NextResponse.json({ ok: true, action: "set-parent", tag: spTag, parent: spParent || null });
      }

      case "delete-tag": {
        const { tag } = body as { tag: string };
        if (!tag) {
          return NextResponse.json(
            { error: "Missing tag" },
            { status: 400 },
          );
        }
        db = new Database(SIM_DB_PATH);
        // Delete from tag_meta
        const result = db
          .prepare("DELETE FROM tag_meta WHERE tag = ?")
          .run(tag);
        if (result.changes === 0) {
          db.close();
          return NextResponse.json(
            { error: `Tag "${tag}" not found` },
            { status: 404 },
          );
        }
        // Strip tag from all stocks in monitor_watchlist
        const rows = db
          .prepare(
            `SELECT symbol, tags FROM monitor_watchlist
             WHERE tags IS NOT NULL AND tags != '[]'`,
          )
          .all() as { symbol: string; tags: string }[];
        const updateStmt = db.prepare(
          "UPDATE monitor_watchlist SET tags = ?, updated_at = ? WHERE symbol = ?",
        );
        const now = Date.now();
        const tx = db.transaction(() => {
          for (const row of rows) {
            try {
              const tags: string[] = JSON.parse(row.tags || "[]");
              const idx = tags.indexOf(tag);
              if (idx !== -1) {
                tags.splice(idx, 1);
                updateStmt.run(JSON.stringify(tags), now, row.symbol);
              }
            } catch { /* invalid json */ }
          }
        });
        tx();
        // Delete associated DB rows
        try {
          db.prepare("DELETE FROM sector_daily WHERE index_id = ?").run(tag);
        } catch { /* table may not exist */ }
        try {
          db.prepare("DELETE FROM sector_alerts WHERE index_id = ?").run(tag);
        } catch { /* table may not exist */ }
        db.close();
        db = null;
        return NextResponse.json({ ok: true, action: "delete-tag", tag });
      }

      // Legacy alias: "delete" maps to "delete-tag" with id→tag
      case "delete": {
        const { id } = body as { id: string };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        // Look up the tag name: try tag_meta directly first (id might be tag name)
        db = new Database(SIM_DB_PATH);
        const tagRow = db
          .prepare("SELECT tag FROM tag_meta WHERE tag = ?")
          .get(id) as { tag: string } | undefined;
        const tagName = tagRow?.tag || id;

        // Delete from tag_meta
        db.prepare("DELETE FROM tag_meta WHERE tag = ?").run(tagName);
        // Strip tag from stocks
        const stocksWithTag = db
          .prepare(
            `SELECT symbol, tags FROM monitor_watchlist
             WHERE tags IS NOT NULL AND tags != '[]'`,
          )
          .all() as { symbol: string; tags: string }[];
        const updStmt = db.prepare(
          "UPDATE monitor_watchlist SET tags = ?, updated_at = ? WHERE symbol = ?",
        );
        const nowTs = Date.now();
        const delTx = db.transaction(() => {
          for (const row of stocksWithTag) {
            try {
              const tags: string[] = JSON.parse(row.tags || "[]");
              const tidx = tags.indexOf(tagName);
              if (tidx !== -1) {
                tags.splice(tidx, 1);
                updStmt.run(JSON.stringify(tags), nowTs, row.symbol);
              }
            } catch { /* invalid json */ }
          }
        });
        delTx();
        // Delete DB rows
        try {
          db.prepare("DELETE FROM sector_daily WHERE index_id = ?").run(tagName);
        } catch { /* table may not exist */ }
        try {
          db.prepare("DELETE FROM sector_alerts WHERE index_id = ?").run(tagName);
        } catch { /* table may not exist */ }
        db.close();
        db = null;
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
        db = new Database(SIM_DB_PATH);
        const wResult = db
          .prepare("UPDATE tag_meta SET watch = ?, updated_at = datetime('now') WHERE tag = ?")
          .run(value ? 1 : 0, id);
        db.close();
        db = null;
        if (wResult.changes === 0) {
          return NextResponse.json(
            { error: `Tag "${id}" not found` },
            { status: 404 },
          );
        }
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
        db = new Database(SIM_DB_PATH);
        const sResult = db
          .prepare("UPDATE tag_meta SET star = ?, updated_at = datetime('now') WHERE tag = ?")
          .run(value ? 1 : 0, id);
        db.close();
        db = null;
        if (sResult.changes === 0) {
          return NextResponse.json(
            { error: `Tag "${id}" not found` },
            { status: 404 },
          );
        }
        return NextResponse.json({ ok: true, action: "star", id });
      }

      case "reset-baseline": {
        const { id } = body as { id: string };
        if (!id) {
          return NextResponse.json(
            { error: "Missing id" },
            { status: 400 },
          );
        }
        db = new Database(SIM_DB_PATH);
        // Get latest index value from sector_daily
        const latestRow = db
          .prepare(
            `SELECT index_value FROM sector_daily
             WHERE index_id = ?
             ORDER BY date DESC LIMIT 1`,
          )
          .get(id) as { index_value: number } | undefined;
        const newBaseline = latestRow?.index_value || 100;
        const rbResult = db
          .prepare("UPDATE tag_meta SET baseline_value = ?, updated_at = datetime('now') WHERE tag = ?")
          .run(newBaseline, id);
        db.close();
        db = null;
        if (rbResult.changes === 0) {
          return NextResponse.json(
            { error: `Tag "${id}" not found` },
            { status: 404 },
          );
        }
        return NextResponse.json({
          ok: true,
          action: "reset-baseline",
          id,
          baseline: newBaseline,
        });
      }

      case "config": {
        // Keep config action for alert_rules / rotation updates via sector_config.json
        const { alertRules, rotation } = body as {
          alertRules?: Record<string, unknown>;
          rotation?: Record<string, unknown>;
        };
        try {
          if (!existsSync(SECTOR_CONFIG_PATH)) {
            return NextResponse.json(
              { error: "sector_config.json not found" },
              { status: 404 },
            );
          }
          const raw = JSON.parse(readFileSync(SECTOR_CONFIG_PATH, "utf-8"));
          if (alertRules) {
            raw.alert_rules = { ...(raw.alert_rules || {}), ...alertRules };
          }
          if (rotation) {
            raw.rotation = { ...(raw.rotation || {}), ...rotation };
          }
          const tmp = SECTOR_CONFIG_PATH + ".tmp";
          writeFileSync(tmp, JSON.stringify(raw, null, 2) + "\n", "utf-8");
          renameSync(tmp, SECTOR_CONFIG_PATH);
        } catch (e) {
          return NextResponse.json(
            { error: `Failed to update config: ${e}` },
            { status: 500 },
          );
        }
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
  } finally {
    try {
      db?.close();
    } catch { /* already closed */ }
  }
}
