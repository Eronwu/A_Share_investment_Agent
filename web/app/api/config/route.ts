import { NextResponse } from "next/server";
import { readFileSync, writeFileSync, renameSync } from "fs";
import { join } from "path";

const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");
const ALERT_PATH = join(process.cwd(), "..", "src", "data", "alert_config.json");

import type { WatchEntry, MonitorConfig } from "../../types";
import { EM_UT } from "../../theme";

const EM_API = "https://push2.eastmoney.com/api/qt/ulist.np/get";

// ── Config (monitor_config.json) ──

function readConfig(): MonitorConfig {
  const raw = readFileSync(CONFIG_PATH, "utf-8");
  return JSON.parse(raw);
}

function writeConfig(config: MonitorConfig) {
  const tmp = CONFIG_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(config, null, 2) + "\n", "utf-8");
  renameSync(tmp, CONFIG_PATH);
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
  try {
    const config = readConfig();
    const alertCfg = readAlerts();
    return NextResponse.json({ ...config, alerts: alertCfg.alerts });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// ── POST /api/config — modify config and/or alerts ──
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { action } = body;
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

        config.watchlist[code] = entry;
        writeConfig(config);

        // 告警写到 alert_config
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

        if ((existing.cost != null || existing.shares != null) && existing.type !== "holding") {
          existing.type = "holding";
        }

        config.watchlist[code] = existing;
        writeConfig(config);

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

        const changed: string[] = [];
        if (data?.type !== undefined) changed.push(`type:${data.type}`);
        if (data?.cost !== undefined) changed.push(`cost:${data.cost}`);
        if (data?.shares !== undefined) changed.push(`shares:${data.shares}`);
        if (data?.above !== undefined) changed.push(`above:${data.above}`);
        if (data?.below !== undefined) changed.push(`below:${data.below}`);
        if (data?.hidden !== undefined) changed.push(`hidden:${data.hidden}`);

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
            delete alertCfg.alerts[c]; // 同步清理告警
          } else {
            notFound.push(c);
          }
        }

        writeConfig(config);
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
        writeConfig(config);

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
  } catch (e) {
    return NextResponse.json({ success: false, message: String(e) }, { status: 500 });
  }
}
