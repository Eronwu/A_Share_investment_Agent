import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";
import Database from "better-sqlite3";
import { SIM_DB_PATH } from "../../lib/db";

const DATA_PATH = join(process.cwd(), "..", "src", "data", "market_data.json");
const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");
const ALERT_PATH = join(process.cwd(), "..", "src", "data", "alert_config.json");

const EMPTY = { services: [], ts: 0, settings: {} };

function getConfigSource(): "db" | "json" {
  return process.env.CONFIG_SOURCE === "json" ? "json" : "db";
}

type DbWatchRow = {
  symbol: string;
  name: string;
  list_type: "holding" | "watching";
  cost: number | null;
  shares: number | null;
  hidden: number;
  star: number;
};

function mergeSplitLists(
  holdings: Record<string, Record<string, unknown>>,
  watching: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  const merged: Record<string, Record<string, unknown>> = {};
  for (const [code, entry] of Object.entries(holdings || {})) {
    merged[code] = { ...entry, type: "holding" };
  }
  for (const [code, entry] of Object.entries(watching || {})) {
    if (!merged[code]) merged[code] = { ...entry, type: "watching" };
  }
  return merged;
}

function parseJsonConfig(raw: string): {
  watchlist: Record<string, Record<string, unknown>>;
  settings: Record<string, number>;
} {
  const cfg = JSON.parse(raw) as {
    watchlist?: Record<string, Record<string, unknown>>;
    holdings?: Record<string, Record<string, unknown>>;
    watching?: Record<string, Record<string, unknown>>;
    settings?: Record<string, number>;
  };
  const watchlist =
    cfg.watchlist && Object.keys(cfg.watchlist).length > 0
      ? cfg.watchlist
      : mergeSplitLists(cfg.holdings || {}, cfg.watching || {});
  return { watchlist, settings: cfg.settings || {} };
}

function readMonitorConfigFromDb(): {
  watchlist: Record<string, Record<string, unknown>>;
  settings: Record<string, number>;
  empty: boolean;
} {
  const db = new Database(SIM_DB_PATH, { readonly: true });
  try {
    const watchRows = db
      .prepare(
        `SELECT symbol, name, list_type, cost, shares, hidden, star
         FROM monitor_watchlist`,
      )
      .all() as DbWatchRow[];
    const settingsRows = db
      .prepare("SELECT key, value FROM monitor_settings")
      .all() as { key: string; value: number }[];

    const watchlist: Record<string, Record<string, unknown>> = {};
    for (const r of watchRows) {
      watchlist[r.symbol] = {
        name: r.name,
        type: r.list_type,
        cost: r.cost,
        shares: r.shares,
        hidden: Boolean(r.hidden),
        star: Boolean(r.star),
      };
    }

    const settings: Record<string, number> = {};
    for (const r of settingsRows) {
      settings[r.key] = Number(r.value);
    }

    return {
      watchlist,
      settings,
      empty: watchRows.length === 0 && settingsRows.length === 0,
    };
  } finally {
    db.close();
  }
}

/**
 * GET /api/metrics
 *
 * 三源合并:
 * - market_data.json  (poller 写): 纯行情数据
 * - monitor_config.json (UI 写):   持仓配置 (type/cost/shares/hidden)
 * - alert_config.json   (UI 写):   告警规则 (above/below)
 */
export async function GET() {
  try {
    const raw = readFileSync(DATA_PATH, "utf-8");
    const data = JSON.parse(raw);

    // 读 config（CONFIG_SOURCE=json 时强制文件源）
    let watchlist: Record<string, Record<string, unknown>> = {};
    let settings: Record<string, number> = {};
    try {
      if (getConfigSource() === "json") {
        const cfgRaw = readFileSync(CONFIG_PATH, "utf-8");
        const cfg = parseJsonConfig(cfgRaw);
        watchlist = cfg.watchlist;
        settings = cfg.settings;
      } else {
        const dbCfg = readMonitorConfigFromDb();
        if (!dbCfg.empty) {
          watchlist = dbCfg.watchlist;
          settings = dbCfg.settings;
        } else {
          const cfgRaw = readFileSync(CONFIG_PATH, "utf-8");
          const cfg = parseJsonConfig(cfgRaw);
          watchlist = cfg.watchlist;
          settings = cfg.settings;
        }
      }
    } catch { /* */ }

    // 读 alert config
    let alerts: Record<string, Record<string, number>> = {};
    try {
      const alertRaw = readFileSync(ALERT_PATH, "utf-8");
      const alertCfg = JSON.parse(alertRaw);
      alerts = alertCfg.alerts || {};
    } catch { /* */ }

    // 校验 services 是数组
    if (!Array.isArray(data.services)) {
      return NextResponse.json({ ...EMPTY, error: "invalid data: services is not an array" });
    }

    // 合并到每条 service
    if (Array.isArray(data.services)) {
      data.services = data.services.map((s: Record<string, unknown>) => {
        const id = s.id as string;
        const entry = watchlist[id];
        const alert = alerts[id];
        if (!entry) return { ...s, type: "watching", hidden: false, above: alert?.above ?? null, below: alert?.below ?? null };

        const price = Number(s.price) || 0;
        const cost = entry.cost != null ? Number(entry.cost) : null;
        const shares = entry.shares != null ? Number(entry.shares) : null;
        const type = (entry.type as string) || "watching";
        const isHolding = type === "holding";

        let pnl: number | null = null;
        if (isHolding && cost != null && cost !== 0 && price > 0) {
          pnl = Math.round(((price - cost) / Math.abs(cost)) * 10000) / 100;
        }

        return {
          ...s,
          type,
          cost,
          shares,
          pnl,
          above: alert?.above ?? null,
          below: alert?.below ?? null,
          hidden: Boolean(entry.hidden),
          star: Boolean(entry.star),
        };
      });
    }

    data.settings = settings;

    // 读 alert events（notifier 写入 SQLite，web 只读）
    try {
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      const db = new Database(SIM_DB_PATH, { readonly: true });
      data.alertEvents = db
        .prepare(
          "SELECT ts, time, symbol, kind, level, message, display, change_pct " +
          "FROM alert_events WHERE date = ? ORDER BY ts",
        )
        .all(today);
      db.close();
    } catch {
      data.alertEvents = [];
    }

    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ ...EMPTY, error: String(e) });
  }
}
