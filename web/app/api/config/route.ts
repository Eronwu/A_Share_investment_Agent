import { NextResponse } from "next/server";
import { readFileSync, writeFileSync, renameSync } from "fs";
import { join } from "path";
import Database from "better-sqlite3";

const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");
const ALERT_PATH = join(process.cwd(), "..", "src", "data", "alert_config.json");
const SIM_DB_PATH = join(process.cwd(), "..", "src", "data", "sim_trading.db");

import type { WatchEntry, MonitorConfig } from "../../types";
import { EM_UT } from "../../theme";

const EM_API = "https://push2.eastmoney.com/api/qt/ulist.np/get";

// ── Config (monitor_config.json) ──

type NormalizedMonitorConfig = MonitorConfig & {
  holdings: Record<string, WatchEntry>;
  watching: Record<string, WatchEntry>;
};

function splitWatchlist(
  watchlist: Record<string, WatchEntry>,
): { holdings: Record<string, WatchEntry>; watching: Record<string, WatchEntry> } {
  const holdings: Record<string, WatchEntry> = {};
  const watching: Record<string, WatchEntry> = {};
  for (const [code, entry] of Object.entries(watchlist || {})) {
    if (entry?.type === "holding") holdings[code] = entry;
    else watching[code] = entry;
  }
  return { holdings, watching };
}

function mergeSplitLists(
  holdings: Record<string, WatchEntry>,
  watching: Record<string, WatchEntry>,
): Record<string, WatchEntry> {
  const merged: Record<string, WatchEntry> = {};
  for (const [code, entry] of Object.entries(holdings || {})) {
    merged[code] = { ...entry, type: "holding" };
  }
  for (const [code, entry] of Object.entries(watching || {})) {
    // If both lists contain same symbol, holding wins.
    if (!merged[code]) merged[code] = { ...entry, type: "watching" };
  }
  return merged;
}

function normalizeConfig(raw: unknown): NormalizedMonitorConfig {
  const obj = (raw || {}) as {
    watchlist?: Record<string, WatchEntry>;
    holdings?: Record<string, WatchEntry>;
    watching?: Record<string, WatchEntry>;
    settings?: Record<string, number>;
  };
  const settings = obj.settings || {};

  let watchlist: Record<string, WatchEntry> = {};
  if (obj.watchlist && Object.keys(obj.watchlist).length > 0) {
    watchlist = obj.watchlist;
  } else {
    watchlist = mergeSplitLists(obj.holdings || {}, obj.watching || {});
  }
  const { holdings, watching } = splitWatchlist(watchlist);
  return { watchlist, holdings, watching, settings };
}

function readConfig(): MonitorConfig {
  const raw = readFileSync(CONFIG_PATH, "utf-8");
  return normalizeConfig(JSON.parse(raw));
}

function getConfigSource(): "db" | "json" {
  return process.env.CONFIG_SOURCE === "json" ? "json" : "db";
}

function writeConfigSnapshot(config: MonitorConfig) {
  const normalized = normalizeConfig(config);
  const holdings: Record<string, WatchEntry> = {};
  const watching: Record<string, WatchEntry> = {};
  for (const [code, entry] of Object.entries(normalized.holdings)) {
    const { type: _type, ...rest } = entry;
    holdings[code] = rest;
  }
  for (const [code, entry] of Object.entries(normalized.watching)) {
    const { type: _type, ...rest } = entry;
    watching[code] = rest;
  }
  const snapshot: MonitorConfig = {
    watchlist: normalized.watchlist,
    holdings,
    watching,
    settings: normalized.settings,
  };
  const tmp = CONFIG_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(snapshot, null, 2) + "\n", "utf-8");
  renameSync(tmp, CONFIG_PATH);
}

// ── Config DB helpers (monitor_* tables) ──

type MonitorDb = Database.Database;

type WatchRow = {
  symbol: string;
  name: string;
  list_type: "holding" | "watching";
  cost: number | null;
  shares: number | null;
  lot: number | null;
  hidden: number;
  star: number;
  dip_buy: number;
};

function openMonitorDb(readonly = false): MonitorDb {
  if (readonly) {
    return new Database(SIM_DB_PATH, { readonly: true });
  }
  const db = new Database(SIM_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 5000");
  return db;
}

function ensureMonitorTables(db: MonitorDb) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS monitor_watchlist (
      symbol TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      list_type TEXT NOT NULL CHECK (list_type IN ('holding', 'watching')),
      cost REAL,
      shares INTEGER,
      lot INTEGER,
      hidden INTEGER NOT NULL DEFAULT 0,
      star INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_type ON monitor_watchlist(list_type);
    CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_updated ON monitor_watchlist(updated_at);
  `);
  // Add dip_buy column if not exists (schema migration)
  try {
    db.exec(`ALTER TABLE monitor_watchlist ADD COLUMN dip_buy INTEGER NOT NULL DEFAULT 0`);
  } catch {
    // Column already exists — ignore
  }
  db.exec(`

    CREATE TABLE IF NOT EXISTS monitor_settings (
      key TEXT PRIMARY KEY,
      value REAL NOT NULL,
      updated_at INTEGER NOT NULL
    );
  `);
}

function readConfigFromDb(db: MonitorDb, ensureSchema = true): MonitorConfig {
  if (ensureSchema) ensureMonitorTables(db);
  const rows = db
    .prepare(
      `SELECT symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy
       FROM monitor_watchlist
       ORDER BY symbol`,
    )
    .all() as WatchRow[];

  const settingsRows = db
    .prepare("SELECT key, value FROM monitor_settings ORDER BY key")
    .all() as { key: string; value: number }[];

  const watchlist: Record<string, WatchEntry> = {};
  const holdings: Record<string, WatchEntry> = {};
  const watching: Record<string, WatchEntry> = {};
  for (const r of rows) {
    const entry: WatchEntry = { name: r.name };
    if (r.list_type === "holding") entry.type = "holding";
    if (r.cost != null) entry.cost = Number(r.cost);
    if (r.shares != null) entry.shares = Number(r.shares);
    if (r.lot != null) {
      (entry as WatchEntry & { lot?: number | null }).lot = Number(r.lot);
    }
    if (Boolean(r.hidden)) entry.hidden = true;
    if (Boolean(r.star)) entry.star = true;
    if (Boolean(r.dip_buy)) entry.dip_buy = true;
    watchlist[r.symbol] = entry;
    if (r.list_type === "holding") holdings[r.symbol] = entry;
    else watching[r.symbol] = entry;
  }

  const settings: Record<string, number> = {};
  for (const r of settingsRows) {
    settings[r.key] = Number(r.value);
  }

  return { watchlist, holdings, watching, settings };
}

function exportMonitorSnapshotFromDb(db: MonitorDb): string | null {
  try {
    const config = readConfigFromDb(db);
    writeConfigSnapshot(config);
    return null;
  } catch (e) {
    return String(e);
  }
}

function readWatchRow(db: MonitorDb, symbol: string): WatchRow | undefined {
  return db
    .prepare(
      `SELECT symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy
       FROM monitor_watchlist
       WHERE symbol = ?`,
    )
    .get(symbol) as WatchRow | undefined;
}

function isConfigEmpty(config: MonitorConfig): boolean {
  return (
    Object.keys(config.watchlist || {}).length === 0 &&
    Object.keys(config.settings || {}).length === 0
  );
}

async function handleJsonPost(body: unknown) {
  const { action } = body as { action?: string };
  const config = readConfig();

  switch (action) {
    case "add": {
      const { code, data } = body as {
        code: string;
        data?: Partial<WatchEntry> & { above?: number; below?: number };
      };
      if (!code) {
        return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
      }

      let name = data?.name || "";
      if (!name) {
        name = await fetchStockName(code);
      }

      const entry: WatchEntry = { name };
      if (data?.type === "holding" || data?.cost != null || data?.shares != null) {
        entry.type = "holding";
        entry.cost = data?.cost ?? null;
        entry.shares = data?.shares ?? null;
      }
      if (data?.hidden !== undefined) entry.hidden = data.hidden;
      if (data?.star !== undefined) entry.star = data.star;
      if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
        (entry as WatchEntry & { lot?: number | null }).lot =
          (data as WatchEntry & { lot?: number | null }).lot ?? null;
      }

      config.watchlist[code] = entry;
      writeConfigSnapshot(config);

      if (data?.above != null || data?.below != null) {
        const alertCfg = readAlerts();
        const alertEntry: AlertEntry = {};
        if (data.above != null) alertEntry.above = data.above;
        if (data.below != null) alertEntry.below = data.below;
        alertCfg.alerts[code] = alertEntry;
        writeAlerts(alertCfg);
      }

      const env = entry.type === "holding" ? "PROD" : "DEV";
      const extras: string[] = [];
      if (entry.cost != null) extras.push(`cost:${entry.cost.toFixed(2)}`);
      if (entry.shares != null) extras.push(`shares:${entry.shares}`);
      if (data?.above != null) extras.push(`above:${data.above}`);
      if (data?.below != null) extras.push(`below:${data.below}`);
      const extStr = extras.length > 0 ? ` | ${extras.join(" ")}` : "";

      return NextResponse.json({
        success: true,
        message: `Added ${code} (${name}) → ${env}${extStr}`,
      });
    }

    case "update": {
      const { code, data } = body as {
        code: string;
        data?: Partial<WatchEntry> & { above?: number; below?: number };
      };
      if (!code || !config.watchlist[code]) {
        return NextResponse.json(
          { success: false, message: `${code || "?"} not in watchlist` },
          { status: 400 }
        );
      }

      const existing = config.watchlist[code];
      if (data?.type !== undefined) existing.type = data.type;
      if (data?.cost !== undefined) existing.cost = data.cost;
      if (data?.shares !== undefined) existing.shares = data.shares;
      if (data?.hidden !== undefined) existing.hidden = data.hidden;
      if (data?.star !== undefined) existing.star = data.star;
      if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
        (existing as WatchEntry & { lot?: number | null }).lot =
          (data as WatchEntry & { lot?: number | null }).lot ?? null;
      }

      if (data?.type === undefined && (data?.cost != null || data?.shares != null)) {
        if ((existing.cost != null || existing.shares != null) && existing.type !== "holding") {
          existing.type = "holding";
        }
      }

      config.watchlist[code] = existing;
      writeConfigSnapshot(config);

      if (data?.above !== undefined || data?.below !== undefined) {
        const alertCfg = readAlerts();
        if (!alertCfg.alerts[code]) alertCfg.alerts[code] = {};
        if (data.above !== undefined) {
          if (data.above === null) delete alertCfg.alerts[code].above;
          else alertCfg.alerts[code].above = data.above;
        }
        if (data.below !== undefined) {
          if (data.below === null) delete alertCfg.alerts[code].below;
          else alertCfg.alerts[code].below = data.below;
        }
        if (Object.keys(alertCfg.alerts[code]).length === 0) {
          delete alertCfg.alerts[code];
        }
        writeAlerts(alertCfg);
      }

      const changed: string[] = [];
      if (data?.type !== undefined) changed.push(`type:${data.type}`);
      if (data?.cost !== undefined) changed.push(`cost:${data.cost}`);
      if (data?.shares !== undefined) changed.push(`shares:${data.shares}`);
      if (data?.above !== undefined) changed.push(`above:${data.above}`);
      if (data?.below !== undefined) changed.push(`below:${data.below}`);
      if (data?.hidden !== undefined) changed.push(`hidden:${data.hidden}`);
      if (data?.star !== undefined) changed.push(`star:${data.star}`);
      if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
        changed.push(`lot:${(data as WatchEntry & { lot?: number | null }).lot}`);
      }

      return NextResponse.json({
        success: true,
        message: `Updated ${code} (${existing.name}) → ${changed.join(" ")}`,
      });
    }

    case "remove": {
      const { code, codes } = body as { code?: string; codes?: string[] };
      const toRemove = codes || (code ? [code] : []);
      if (toRemove.length === 0) {
        return NextResponse.json({ success: false, message: "Missing code(s)" }, { status: 400 });
      }

      const removed: string[] = [];
      const notFound: string[] = [];
      const alertCfg = readAlerts();
      for (const c of toRemove) {
        if (config.watchlist[c]) {
          removed.push(`${c} (${config.watchlist[c].name})`);
          delete config.watchlist[c];
          delete alertCfg.alerts[c];
        } else {
          notFound.push(c);
        }
      }

      writeConfigSnapshot(config);
      writeAlerts(alertCfg);
      let msg = removed.length > 0 ? `Removed ${removed.join(", ")}` : "";
      if (notFound.length > 0) {
        msg += (msg ? "; " : "") + `Not found: ${notFound.join(", ")}`;
      }
      return NextResponse.json({ success: removed.length > 0, message: msg });
    }

    case "settings": {
      const { settings } = body as { settings: Record<string, number> };
      if (!settings) {
        return NextResponse.json({ success: false, message: "Missing settings" }, { status: 400 });
      }

      const ALLOWED: Record<string, [number, number]> = {
        poll_interval: [5, 300],
        big_move_pct: [0.5, 20],
        cooldown_minutes: [1, 120],
        l1_trigger_pct: [1, 20],
        l1_delta_pct: [1, 20],
        l1_cooldown_min: [0, 60],
        l2_trigger_pct: [1, 20],
        l2_delta_pct: [1, 20],
        l2_cooldown_min: [1, 120],
        l3_cooldown_min: [1, 120],
      };

      const rejected: string[] = [];
      const applied: string[] = [];
      for (const [key, val] of Object.entries(settings)) {
        const range = ALLOWED[key];
        if (!range) { rejected.push(`${key} (unknown)`); continue; }
        const n = Number(val);
        if (isNaN(n) || n < range[0] || n > range[1]) {
          rejected.push(`${key}=${val} (must be ${range[0]}-${range[1]})`);
          continue;
        }
        config.settings[key] = n;
        applied.push(`${key}=${n}`);
      }
      writeConfigSnapshot(config);

      const msg = applied.length > 0 ? `Updated: ${applied.join(" ")}` : "No changes";
      const warn = rejected.length > 0 ? ` | Rejected: ${rejected.join(", ")}` : "";
      return NextResponse.json({
        success: applied.length > 0,
        message: msg + warn,
      });
    }

    default:
      return NextResponse.json(
        { success: false, message: `Unknown action: ${action}` },
        { status: 400 }
      );
  }
}

// ── Alerts (alert_config.json) ──

interface AlertEntry { above?: number; below?: number; }
interface AlertConfig { alerts: Record<string, AlertEntry>; }

function readAlerts(): AlertConfig {
  try {
    const raw = readFileSync(ALERT_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { alerts: {} };
  }
}

function writeAlerts(cfg: AlertConfig) {
  const tmp = ALERT_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(cfg, null, 2) + "\n", "utf-8");
  renameSync(tmp, ALERT_PATH);
}

// ── Helpers ──

function emMarket(code: string): string {
  if (code.toUpperCase().startsWith("HK")) return "116";
  return code.startsWith("6") ? "1" : "0";
}

function rawCode(code: string): string {
  return code.toUpperCase().startsWith("HK")
    ? code.toUpperCase().replace("HK", "")
    : code;
}

async function fetchStockName(code: string): Promise<string> {
  try {
    const secid = `${emMarket(code)}.${rawCode(code)}`;
    const url = `${EM_API}?fltt=2&secids=${secid}&fields=f12,f14&ut=${EM_UT}`;
    const resp = await fetch(url);
    const data = await resp.json();
    if (data?.data?.diff?.[0]?.f14) {
      return String(data.data.diff[0].f14);
    }
  } catch {
    // fallback
  }
  return code;
}

// ── GET /api/config — return config + alerts ──
export async function GET() {
  let db: MonitorDb | null = null;
  try {
    let config: MonitorConfig;
    if (getConfigSource() === "json") {
      config = readConfig();
    } else {
      try {
        db = openMonitorDb(true);
        config = readConfigFromDb(db, false);
        if (isConfigEmpty(config)) {
          config = readConfig();
        }
      } catch {
        config = readConfig();
      } finally {
        db?.close();
      }
    }
    const alertCfg = readAlerts();
    return NextResponse.json({ ...config, alerts: alertCfg.alerts });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// ── POST /api/config — modify config and/or alerts ──
export async function POST(request: Request) {
  let db: MonitorDb | null = null;
  try {
    const body = await request.json();
    if (getConfigSource() === "json") {
      return await handleJsonPost(body);
    }
    const { action } = body;
    db = openMonitorDb(false);
    ensureMonitorTables(db);

    const withSnapshotWarning = (message: string, warn: string | null): string => {
      if (!warn) return message;
      return `${message} | WARN: snapshot export failed`;
    };

    switch (action) {
      case "add": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry> & { above?: number; below?: number };
        };
        if (!code) {
          return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
        }

        let name = data?.name || "";
        if (!name) {
          name = await fetchStockName(code);
        }

        const isHolding = data?.type === "holding" || data?.cost != null || data?.shares != null;
        const listType: "holding" | "watching" = isHolding ? "holding" : "watching";
        const cost = data?.cost != null ? Number(data.cost) : null;
        const shares = data?.shares != null ? Number(data.shares) : null;
        const lotRaw = (data as WatchEntry & { lot?: number | null } | undefined)?.lot;
        const lot = lotRaw != null ? Number(lotRaw) : null;
        const hidden = data?.hidden ? 1 : 0;
        const star = data?.star ? 1 : 0;
        const nowTs = Math.floor(Date.now() / 1000);

        db.prepare(
          `INSERT INTO monitor_watchlist(
            symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy, created_at, updated_at
          )
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(symbol) DO UPDATE SET
            name = excluded.name,
            list_type = excluded.list_type,
            cost = excluded.cost,
            shares = excluded.shares,
            lot = excluded.lot,
            hidden = excluded.hidden,
            star = excluded.star,
            dip_buy = excluded.dip_buy,
            updated_at = excluded.updated_at`
        ).run(code, name, listType, cost, shares, lot, hidden, star, 0, nowTs, nowTs);

        // 告警写到 alert_config
        if (data?.above != null || data?.below != null) {
          const alertCfg = readAlerts();
          const alertEntry: AlertEntry = {};
          if (data.above != null) alertEntry.above = data.above;
          if (data.below != null) alertEntry.below = data.below;
          alertCfg.alerts[code] = alertEntry;
          writeAlerts(alertCfg);
        }

        const env = listType === "holding" ? "PROD" : "DEV";
        const extras: string[] = [];
        if (cost != null) extras.push(`cost:${cost.toFixed(2)}`);
        if (shares != null) extras.push(`shares:${shares}`);
        if (data?.above != null) extras.push(`above:${data.above}`);
        if (data?.below != null) extras.push(`below:${data.below}`);
        const extStr = extras.length > 0 ? ` | ${extras.join(" ")}` : "";
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        return NextResponse.json({
          success: true,
          message: withSnapshotWarning(`Added ${code} (${name}) → ${env}${extStr}`, snapshotWarn),
        });
      }

      case "update": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry> & { above?: number; below?: number };
        };
        const existing = code ? readWatchRow(db, code) : undefined;
        if (!code || !existing) {
          return NextResponse.json(
            { success: false, message: `${code || "?"} not in watchlist` },
            { status: 400 }
          );
        }

        let listType: "holding" | "watching" = existing.list_type === "holding" ? "holding" : "watching";
        let cost = existing.cost;
        let shares = existing.shares;
        let hidden = Boolean(existing.hidden);
        let star = Boolean(existing.star);
        let dipBuy = Boolean(existing.dip_buy);
        let lot = existing.lot;

        if (data?.type !== undefined) {
          listType = data.type === "holding" ? "holding" : "watching";
        }
        if (data?.cost !== undefined) {
          cost = data.cost == null ? null : Number(data.cost);
        }
        if (data?.shares !== undefined) {
          shares = data.shares == null ? null : Number(data.shares);
        }
        if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
          lot = (data as WatchEntry & { lot?: number | null }).lot ?? null;
        }
        if (data?.hidden !== undefined) hidden = Boolean(data.hidden);
        if (data?.star !== undefined) star = Boolean(data.star);
        if (data?.dip_buy !== undefined) dipBuy = Boolean(data.dip_buy);

        // 自动提升为 holding：仅当用户未显式设置 type 且新增了 cost/shares 时
        if (data?.type === undefined && (data?.cost != null || data?.shares != null)) {
          if ((cost != null || shares != null) && listType !== "holding") {
            listType = "holding";
          }
        }

        const nowTs = Math.floor(Date.now() / 1000);
        db.prepare(
          `UPDATE monitor_watchlist
           SET list_type = ?, cost = ?, shares = ?, lot = ?, hidden = ?, star = ?, dip_buy = ?, updated_at = ?
           WHERE symbol = ?`
        ).run(listType, cost, shares, lot, hidden ? 1 : 0, star ? 1 : 0, dipBuy ? 1 : 0, nowTs, code);

        // 告警写到 alert_config
        if (data?.above !== undefined || data?.below !== undefined) {
          const alertCfg = readAlerts();
          if (!alertCfg.alerts[code]) alertCfg.alerts[code] = {};
          if (data.above !== undefined) {
            if (data.above === null) delete alertCfg.alerts[code].above;
            else alertCfg.alerts[code].above = data.above;
          }
          if (data.below !== undefined) {
            if (data.below === null) delete alertCfg.alerts[code].below;
            else alertCfg.alerts[code].below = data.below;
          }
          // 如果告警为空则删除条目
          if (Object.keys(alertCfg.alerts[code]).length === 0) {
            delete alertCfg.alerts[code];
          }
          writeAlerts(alertCfg);
        }
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        const changed: string[] = [];
        if (data?.type !== undefined) changed.push(`type:${data.type}`);
        if (data?.cost !== undefined) changed.push(`cost:${data.cost}`);
        if (data?.shares !== undefined) changed.push(`shares:${data.shares}`);
        if (data?.above !== undefined) changed.push(`above:${data.above}`);
        if (data?.below !== undefined) changed.push(`below:${data.below}`);
        if (data?.hidden !== undefined) changed.push(`hidden:${data.hidden}`);
        if (data?.star !== undefined) changed.push(`star:${data.star}`);
        if (data?.dip_buy !== undefined) changed.push(`dip_buy:${data.dip_buy}`);
        if ((data as WatchEntry & { lot?: number | null } | undefined)?.lot !== undefined) {
          changed.push(`lot:${(data as WatchEntry & { lot?: number | null }).lot}`);
        }

        return NextResponse.json({
          success: true,
          message: withSnapshotWarning(
            `Updated ${code} (${existing.name}) → ${changed.join(" ")}`,
            snapshotWarn
          ),
        });
      }

      case "remove": {
        const { code, codes } = body as { code?: string; codes?: string[] };
        const toRemove = codes || (code ? [code] : []);
        if (toRemove.length === 0) {
          return NextResponse.json({ success: false, message: "Missing code(s)" }, { status: 400 });
        }

        const removed: string[] = [];
        const notFound: string[] = [];
        const alertCfg = readAlerts();
        for (const c of toRemove) {
          const row = readWatchRow(db, c);
          if (row) {
            removed.push(`${c} (${row.name})`);
            db.prepare("DELETE FROM monitor_watchlist WHERE symbol = ?").run(c);
            delete alertCfg.alerts[c]; // 同步清理告警
          } else {
            notFound.push(c);
          }
        }

        writeAlerts(alertCfg);
        const snapshotWarn = exportMonitorSnapshotFromDb(db);
        let msg = removed.length > 0 ? `Removed ${removed.join(", ")}` : "";
        if (notFound.length > 0) {
          msg += (msg ? "; " : "") + `Not found: ${notFound.join(", ")}`;
        }
        msg = withSnapshotWarning(msg, snapshotWarn);

        return NextResponse.json({ success: removed.length > 0, message: msg });
      }

      case "settings": {
        const { settings } = body as { settings: Record<string, number> };
        if (!settings) {
          return NextResponse.json({ success: false, message: "Missing settings" }, { status: 400 });
        }

        const ALLOWED: Record<string, [number, number]> = {
          poll_interval: [5, 300],
          big_move_pct: [0.5, 20],
          cooldown_minutes: [1, 120],
          l1_trigger_pct: [1, 20],
          l1_delta_pct: [1, 20],
          l1_cooldown_min: [0, 60],
          l2_trigger_pct: [1, 20],
          l2_delta_pct: [1, 20],
          l2_cooldown_min: [1, 120],
          l3_cooldown_min: [1, 120],
        };

        const rejected: string[] = [];
        const applied: string[] = [];
        for (const [key, val] of Object.entries(settings)) {
          const range = ALLOWED[key];
          if (!range) { rejected.push(`${key} (unknown)`); continue; }
          const n = Number(val);
          if (isNaN(n) || n < range[0] || n > range[1]) {
            rejected.push(`${key}=${val} (must be ${range[0]}-${range[1]})`);
            continue;
          }
          const nowTs = Math.floor(Date.now() / 1000);
          db.prepare(
            `INSERT INTO monitor_settings(key, value, updated_at)
             VALUES (?, ?, ?)
             ON CONFLICT(key) DO UPDATE SET
               value = excluded.value,
               updated_at = excluded.updated_at`
          ).run(key, n, nowTs);
          applied.push(`${key}=${n}`);
        }
        const snapshotWarn = exportMonitorSnapshotFromDb(db);

        const msg = applied.length > 0 ? `Updated: ${applied.join(" ")}` : "No changes";
        const warn = rejected.length > 0 ? ` | Rejected: ${rejected.join(", ")}` : "";
        return NextResponse.json({
          success: applied.length > 0,
          message: withSnapshotWarning(msg + warn, snapshotWarn),
        });
      }

      default:
        return NextResponse.json(
          { success: false, message: `Unknown action: ${action}` },
          { status: 400 }
        );
    }
  } catch (e) {
    return NextResponse.json({ success: false, message: String(e) }, { status: 500 });
  } finally {
    db?.close();
  }
}
