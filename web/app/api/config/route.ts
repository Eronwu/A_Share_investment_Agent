import { NextResponse } from "next/server";
import { readFileSync, writeFileSync } from "fs";
import { join } from "path";

const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");

const EM_API = "https://push2.eastmoney.com/api/qt/ulist.np/get";

interface WatchEntry {
  name: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  above?: number | null;
  below?: number | null;
  hidden?: boolean;
}

interface MonitorConfig {
  watchlist: Record<string, WatchEntry>;
  settings: Record<string, number>;
}

function readConfig(): MonitorConfig {
  const raw = readFileSync(CONFIG_PATH, "utf-8");
  return JSON.parse(raw);
}

function writeConfig(config: MonitorConfig) {
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + "\n", "utf-8");
}

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
    const url = `${EM_API}?fltt=2&secids=${secid}&fields=f12,f14&ut=fa5fd1943c7b386f172d6893dbfba10b`;
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

// GET /api/config — return full config
export async function GET() {
  try {
    const config = readConfig();
    return NextResponse.json(config);
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

// POST /api/config — modify config
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { action } = body;
    const config = readConfig();

    switch (action) {
      case "add": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry>;
        };
        if (!code) {
          return NextResponse.json({ success: false, message: "Missing code" }, { status: 400 });
        }

        let name = data?.name || "";
        if (!name) {
          name = await fetchStockName(code);
        }

        const entry: WatchEntry = {
          name,
          above: data?.above ?? null,
          below: data?.below ?? null,
        };

        if (data?.type === "holding" || data?.cost != null || data?.shares != null) {
          entry.type = "holding";
          entry.cost = data?.cost ?? null;
          entry.shares = data?.shares ?? null;
        }

        config.watchlist[code] = entry;
        writeConfig(config);

        const env = entry.type === "holding" ? "PROD" : "DEV";
        const extras: string[] = [];
        if (entry.cost != null) extras.push(`cost:${entry.cost.toFixed(2)}`);
        if (entry.shares != null) extras.push(`shares:${entry.shares}`);
        if (entry.above != null) extras.push(`above:${entry.above}`);
        if (entry.below != null) extras.push(`below:${entry.below}`);
        const extStr = extras.length > 0 ? ` | ${extras.join(" ")}` : "";

        return NextResponse.json({
          success: true,
          message: `Added ${code} (${name}) → ${env}${extStr}`,
        });
      }

      case "update": {
        const { code, data } = body as {
          code: string;
          data?: Partial<WatchEntry>;
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
        if (data?.above !== undefined) existing.above = data.above;
        if (data?.below !== undefined) existing.below = data.below;
        if (data?.hidden !== undefined) existing.hidden = data.hidden;

        // promote to holding if cost/shares are set
        if ((existing.cost != null || existing.shares != null) && existing.type !== "holding") {
          existing.type = "holding";
        }

        config.watchlist[code] = existing;
        writeConfig(config);

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
        for (const c of toRemove) {
          if (config.watchlist[c]) {
            removed.push(`${c} (${config.watchlist[c].name})`);
            delete config.watchlist[c];
          } else {
            notFound.push(c);
          }
        }

        writeConfig(config);
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

        for (const [key, val] of Object.entries(settings)) {
          config.settings[key] = val;
        }
        writeConfig(config);

        const pairs = Object.entries(settings)
          .map(([k, v]) => `${k}=${v}`)
          .join(" ");
        return NextResponse.json({
          success: true,
          message: `Config updated: ${pairs}`,
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
