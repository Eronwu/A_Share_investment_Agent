import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

const DATA_PATH = join(process.cwd(), "..", "src", "data", "market_data.json");
const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");
const ALERT_PATH = join(process.cwd(), "..", "src", "data", "alert_config.json");
const ALERT_EVENTS_PATH = join(process.cwd(), "..", "src", "data", "alert_events.json");

const EMPTY = { services: [], ts: 0, settings: {} };

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

    // 读 config
    let watchlist: Record<string, Record<string, unknown>> = {};
    let settings: Record<string, number> = {};
    try {
      const cfgRaw = readFileSync(CONFIG_PATH, "utf-8");
      const cfg = JSON.parse(cfgRaw);
      watchlist = cfg.watchlist || {};
      settings = cfg.settings || {};
    } catch { /* */ }

    // 读 alert config
    let alerts: Record<string, Record<string, number>> = {};
    try {
      const alertRaw = readFileSync(ALERT_PATH, "utf-8");
      const alertCfg = JSON.parse(alertRaw);
      alerts = alertCfg.alerts || {};
    } catch { /* */ }

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
        if (isHolding && cost && cost > 0 && price > 0) {
          pnl = Math.round(((price - cost) / cost) * 10000) / 100;
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

    // 读 alert events（notifier 写入，web 只读）
    try {
      const evRaw = readFileSync(ALERT_EVENTS_PATH, "utf-8");
      const evData = JSON.parse(evRaw);
      data.alertEvents = evData.events || [];
    } catch {
      data.alertEvents = [];
    }

    return NextResponse.json(data);
  } catch {
    return NextResponse.json(EMPTY);
  }
}
