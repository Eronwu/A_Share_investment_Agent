import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

const DATA_PATH = join(process.cwd(), "..", "src", "data", "market_data.json");
const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");

const EMPTY = { services: [], ts: 0, settings: {} };

/**
 * GET /api/metrics
 *
 * 职责分离:
 * - market_data.json (poller 写): 纯行情数据 (price/change/vol/...)
 * - monitor_config.json (UI 写):  config 字段 (type/cost/shares/hidden/above/below)
 *
 * 本 API 负责合并两者 + 计算 pnl，确保 UI 端操作立即生效。
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
    } catch {
      // config 读取失败不影响行情数据
    }

    // 合并 config 字段到每条 service
    if (Array.isArray(data.services)) {
      data.services = data.services.map((s: Record<string, unknown>) => {
        const id = s.id as string;
        const entry = watchlist[id];
        if (!entry) return { ...s, type: "watching", hidden: false };

        const price = Number(s.price) || 0;
        const cost = entry.cost != null ? Number(entry.cost) : null;
        const shares = entry.shares != null ? Number(entry.shares) : null;
        const type = (entry.type as string) || "watching";
        const isHolding = type === "holding";

        // 计算 pnl
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
          above: entry.above ?? null,
          below: entry.below ?? null,
          hidden: Boolean(entry.hidden),
        };
      });
    }

    // 覆盖 settings（始终用 config 最新值）
    data.settings = settings;

    return NextResponse.json(data);
  } catch {
    return NextResponse.json(EMPTY);
  }
}
