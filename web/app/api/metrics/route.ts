import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

// 东方财富 API
const EM_API = "https://push2.eastmoney.com/api/qt/ulist.np/get";
const EM_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f7,f8,f10,f15,f16,f17,f18";

function emMarket(code: string): string {
  if (code.toUpperCase().startsWith("HK")) return "116";
  return code.startsWith("6") ? "1" : "0";
}

function rawCode(code: string): string {
  return code.toUpperCase().startsWith("HK")
    ? code.toUpperCase().replace("HK", "")
    : code;
}

interface WatchEntry {
  name: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  above?: number | null;
  below?: number | null;
}

interface MonitorConfig {
  watchlist: Record<string, WatchEntry>;
  settings: Record<string, number>;
}

export async function GET() {
  try {
    // 读取 monitor_config.json
    const configPath = join(
      process.cwd(),
      "..",
      "src",
      "data",
      "monitor_config.json"
    );
    const raw = readFileSync(configPath, "utf-8");
    const config: MonitorConfig = JSON.parse(raw);
    const watchlist = config.watchlist;
    const symbols = Object.keys(watchlist);

    if (symbols.length === 0) {
      return NextResponse.json({ services: [], ts: Date.now() });
    }

    // 构造 secids
    const codeMap: Record<string, string> = {};
    const secids = symbols
      .map((s) => {
        const r = rawCode(s);
        codeMap[r] = s;
        return `${emMarket(s)}.${r}`;
      })
      .join(",");

    const url = `${EM_API}?fltt=2&secids=${secids}&fields=${EM_FIELDS}&ut=fa5fd1943c7b386f172d6893dbfba10b`;
    const resp = await fetch(url, { next: { revalidate: 0 } });
    const data = await resp.json();

    if (!data?.data?.diff) {
      return NextResponse.json({ services: [], ts: Date.now() });
    }

    const services = data.data.diff.map(
      (s: Record<string, string | number>) => {
        const rawC = String(s.f12 || "");
        const code = codeMap[rawC] || rawC;
        const entry = watchlist[code] || {};
        const price = Number(s.f2) || 0;
        const cost = entry.cost;
        const pnl =
          entry.type === "holding" && cost && cost > 0 && price > 0
            ? ((price - cost) / cost) * 100
            : null;

        return {
          id: code,
          name: String(s.f14 || ""),
          type: entry.type || "watching",
          price,
          change: Number(s.f3) || 0,
          chgAmt: Number(s.f4) || 0,
          vol: Number(s.f5) || 0,
          amount: Number(s.f6) || 0,
          amp: Number(s.f7) || 0,
          turnover: Number(s.f8) || 0,
          volRatio: Number(s.f10) || 0,
          high: Number(s.f15) || 0,
          low: Number(s.f16) || 0,
          open: Number(s.f17) || 0,
          prevClose: Number(s.f18) || 0,
          cost: cost || null,
          shares: entry.shares || null,
          pnl: pnl !== null ? Math.round(pnl * 100) / 100 : null,
        };
      }
    );

    return NextResponse.json({ services, ts: Date.now() });
  } catch (e) {
    console.error("API error:", e);
    return NextResponse.json({ services: [], ts: Date.now(), error: String(e) });
  }
}
