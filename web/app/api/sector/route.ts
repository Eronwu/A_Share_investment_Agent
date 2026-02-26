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
  change: number;
}

/* ── Config helpers ── */

function readSectorConfig(): SectorConfig {
  try {
    if (!existsSync(SECTOR_CONFIG_PATH)) {
      return { indices: [], alertRules: {}, rotation: {} };
    }
    const raw = readFileSync(SECTOR_CONFIG_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { indices: [], alertRules: {}, rotation: {} };
  }
}

function writeSectorConfig(config: SectorConfig) {
  const tmp = SECTOR_CONFIG_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(config, null, 2) + "\n", "utf-8");
  renameSync(tmp, SECTOR_CONFIG_PATH);
}

function round(n: number, d: number): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
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
      boardDetail = {
        name: boardFilter,
        top10Count,
        rankHistory: boardRows.map((r) => ({
          date: r.date,
          rank: r.rank,
        })),
      };
    }

    // ── Custom indices ──

    const config = readSectorConfig();
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

      // Components from latest row
      let components: ComponentEntry[] = [];
      if (dailyRows.length > 0 && dailyRows[0].components_json) {
        try {
          components = JSON.parse(dailyRows[0].components_json);
        } catch {
          /* invalid json */
        }
      }

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
