import { NextResponse } from "next/server";
import Database from "better-sqlite3";
import { join } from "path";

import { readFileSync } from "fs";
import { SIM_DB_PATH } from "../../lib/db";

const MARKET_DATA_PATH = join(process.cwd(), "..", "src", "data", "market_data.json");
const CONFIG_PATH = join(process.cwd(), "..", "src", "data", "monitor_config.json");

export const dynamic = "force-dynamic";

/* ── Types ── */

interface TradeRow {
  trade_id: string;
  code: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
  exit_reason: string;
  notes: string;
  confidence: number;
  entry_date: string;
  exit_date: string;
  commission: number;
}

interface DailyRow {
  date: string;
  total_equity: number;
  cash: number;
  invested: number;
  daily_return: number;
  cumulative_return: number;
  drawdown_pct: number;
}

interface Bucket {
  trades: number;
  pnl: number;
  wins: number;
}

/* ── Analytics (ported from trade_analyzer.py) ── */

function computeSummary(
  trades: TradeRow[],
  daily: DailyRow[],
  initialCapital: number,
) {
  if (trades.length === 0) {
    return {
      total_trades: 0, winning_trades: 0, losing_trades: 0, win_rate: 0,
      total_pnl: 0, total_return: 0, avg_win: 0, avg_loss: 0,
      profit_factor: 0, avg_hold_days: 0, total_commission: 0,
      sharpe_ratio: 0, max_drawdown_pct: 0, max_drawdown_abs: 0,
      calmar_ratio: 0, final_equity: initialCapital, initial_capital: initialCapital,
      trading_days: daily.length,
    };
  }

  const pnls = trades.map((t) => t.pnl);
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p <= 0);
  const sumWins = wins.reduce((a, b) => a + b, 0);
  const sumLosses = losses.reduce((a, b) => a + b, 0);

  const totalPnl = pnls.reduce((a, b) => a + b, 0);
  const winRate = wins.length / pnls.length;
  const avgWin = wins.length > 0 ? sumWins / wins.length : 0;
  const avgLoss = losses.length > 0 ? sumLosses / losses.length : 0;
  const profitFactor = sumLosses !== 0 ? Math.abs(sumWins / sumLosses) : Infinity;
  const avgHold = trades.reduce((a, t) => a + (t.hold_days || 0), 0) / trades.length;
  const totalCommission = trades.reduce((a, t) => a + (t.commission || 0), 0);

  const finalEquity = daily.length > 0
    ? daily[daily.length - 1].total_equity
    : initialCapital;
  const totalReturn = (finalEquity - initialCapital) / initialCapital;

  // Sharpe (annualized, 252 trading days)
  const dailyReturns = daily.map((d) => d.daily_return);
  const sharpe = calcSharpe(dailyReturns);

  // Max drawdown
  const equities = daily.map((d) => d.total_equity);
  const { maxDd, maxDdPct } = calcMaxDrawdown(equities, initialCapital);

  // Calmar
  const calmar = maxDdPct > 0 ? calcCalmar(dailyReturns, maxDdPct) : 0;

  return {
    total_trades: trades.length,
    winning_trades: wins.length,
    losing_trades: losses.length,
    win_rate: round(winRate, 4),
    total_pnl: round(totalPnl, 2),
    total_return: round(totalReturn, 4),
    avg_win: round(avgWin, 2),
    avg_loss: round(avgLoss, 2),
    profit_factor: profitFactor === Infinity ? "inf" : round(profitFactor, 2),
    avg_hold_days: round(avgHold, 1),
    total_commission: round(totalCommission, 2),
    sharpe_ratio: round(sharpe, 2),
    max_drawdown_pct: round(maxDdPct, 4),
    max_drawdown_abs: round(maxDd, 2),
    calmar_ratio: round(calmar, 2),
    final_equity: round(finalEquity, 2),
    initial_capital: initialCapital,
    trading_days: daily.length,
  };
}

function calcSharpe(dailyReturns: number[], riskFreeAnnual = 0.02): number {
  if (dailyReturns.length < 2) return 0;
  const dailyRf = riskFreeAnnual / 252;
  const excess = dailyReturns.map((r) => r - dailyRf);
  const mean = excess.reduce((a, b) => a + b, 0) / excess.length;
  const variance =
    excess.reduce((a, r) => a + (r - mean) ** 2, 0) / (excess.length - 1);
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  return (mean / std) * Math.sqrt(252);
}

function calcMaxDrawdown(
  equities: number[],
  initialCapital: number,
): { maxDd: number; maxDdPct: number } {
  if (equities.length === 0) return { maxDd: 0, maxDdPct: 0 };
  let peak = equities[0] || initialCapital;
  let maxDd = 0;
  let maxDdPct = 0;
  for (const eq of equities) {
    if (eq > peak) peak = eq;
    const dd = peak - eq;
    const ddPct = peak > 0 ? dd / peak : 0;
    if (dd > maxDd) {
      maxDd = dd;
      maxDdPct = ddPct;
    }
  }
  return { maxDd, maxDdPct };
}

function calcCalmar(dailyReturns: number[], maxDdPct: number): number {
  if (dailyReturns.length < 2 || maxDdPct <= 0) return 0;
  let totalReturn = 1;
  for (const r of dailyReturns) totalReturn *= 1 + r;
  const annualReturn = totalReturn ** (252 / dailyReturns.length) - 1;
  return annualReturn / maxDdPct;
}

function computeGrouped(
  trades: TradeRow[],
  keyFn: (t: TradeRow) => string,
): Record<string, { trades: number; pnl: number; win_rate: number }> {
  const buckets: Record<string, Bucket> = {};
  for (const t of trades) {
    const key = keyFn(t) || "unknown";
    if (!buckets[key]) buckets[key] = { trades: 0, pnl: 0, wins: 0 };
    buckets[key].trades++;
    buckets[key].pnl += t.pnl;
    if (t.pnl > 0) buckets[key].wins++;
  }
  // Sort by PnL descending
  const sorted = Object.entries(buckets).sort((a, b) => b[1].pnl - a[1].pnl);
  const result: Record<string, { trades: number; pnl: number; win_rate: number }> = {};
  for (const [key, b] of sorted) {
    result[key] = {
      trades: b.trades,
      pnl: round(b.pnl, 2),
      win_rate: round(b.trades > 0 ? b.wins / b.trades : 0, 4),
    };
  }
  return result;
}

function round(n: number, d: number): number {
  const f = 10 ** d;
  return Math.round(n * f) / f;
}

/* ── Route Handler ── */

export async function GET() {
  try {
    const db = new Database(SIM_DB_PATH, { readonly: true });

    const trades = db
      .prepare(
        `SELECT trade_id, code, direction, entry_price, exit_price,
                quantity, pnl, pnl_pct, hold_days, exit_reason, notes,
                confidence, entry_date, exit_date, commission
         FROM trades WHERE param_version != 'live' ORDER BY id`,
      )
      .all() as TradeRow[];

    const dailyPnl = db
      .prepare(
        `SELECT date, total_equity, cash, invested, daily_return,
                cumulative_return, drawdown_pct, positions_json
         FROM daily_pnl ORDER BY date`,
      )
      .all() as (DailyRow & { positions_json?: string })[];

    const paramRow = db
      .prepare(
        `SELECT config_json FROM param_versions WHERE is_active = 1 LIMIT 1`,
      )
      .get() as { config_json: string } | undefined;

    // Live state (real-time positions from RT engine)
    let livePositions: Record<string, unknown>[] = [];
    let liveTrades: TradeRow[] = [];
    try {
      livePositions = db
        .prepare("SELECT * FROM live_state ORDER BY code")
        .all() as Record<string, unknown>[];
    } catch { /* table may not exist yet */ }

    try {
      liveTrades = db
        .prepare(
          `SELECT trade_id, code, direction, entry_price, exit_price,
                  quantity, pnl, pnl_pct, hold_days, exit_reason, notes,
                  confidence, entry_date, exit_date, entry_time, exit_time, commission
           FROM trades WHERE param_version = 'live'
           ORDER BY id DESC LIMIT 100`,
        )
        .all() as TradeRow[];
    } catch { /* */ }

    db.close();

    // Enrich live positions with name + change% from market_data + config
    let marketLookup: Record<string, { name: string; change: number; chgAmt: number }> = {};
    try {
      const md = JSON.parse(readFileSync(MARKET_DATA_PATH, "utf-8"));
      const cfg = JSON.parse(readFileSync(CONFIG_PATH, "utf-8"));
      const wl = cfg.watchlist || {};
      for (const svc of md.services || []) {
        const id = svc.id as string;
        marketLookup[id] = {
          name: (wl[id]?.name as string) || (svc.name as string) || id,
          change: Number(svc.change) || 0,
          chgAmt: Number(svc.chgAmt) || 0,
        };
      }
    } catch { /* market data unavailable — positions still work without names */ }

    for (const p of livePositions) {
      const code = p.code as string;
      const info = marketLookup[code];
      if (info) {
        p.name = info.name;
        p.change = info.change;
        p.chgAmt = info.chgAmt;
      } else {
        p.name = code;
        p.change = 0;
        p.chgAmt = 0;
      }
    }

    // Enrich trades with stock names
    const enrichedLiveTrades = liveTrades.map((t) => ({
      ...t,
      name: marketLookup[t.code]?.name || t.code,
    }));

    const initialCapital = paramRow
      ? (JSON.parse(paramRow.config_json).initial_capital ?? 1_000_000)
      : 1_000_000;

    const summary = computeSummary(trades, dailyPnl, initialCapital);
    const perStrategy = computeGrouped(trades, (t) => t.notes);
    const perStock = computeGrouped(trades, (t) => t.code);

    // Parse positions snapshots per day
    const dailyPositions: Record<string, Record<string, unknown>> = {};
    for (const d of dailyPnl) {
      if (d.positions_json) {
        try {
          dailyPositions[d.date] = JSON.parse(d.positions_json);
        } catch { /* skip */ }
      }
    }

    // Strip positions_json from daily_pnl response (sent separately)
    const dailyPnlClean = dailyPnl.map(({ positions_json: _, ...rest }) => rest);

    // Live summary — computed from live positions + live trades
    const totalUnrealized = livePositions.reduce(
      (sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0,
    );
    const totalMktVal = livePositions.reduce(
      (sum, p) => sum + (Number(p.current_price) || 0) * (Number(p.quantity) || 0), 0,
    );
    const totalCostBasis = livePositions.reduce(
      (sum, p) => sum + (Number(p.entry_price) || 0) * (Number(p.quantity) || 0), 0,
    );

    // Realized P&L from live closed trades
    const realizedPnl = liveTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
    const liveCommission = liveTrades.reduce((sum, t) => sum + (t.commission || 0), 0);
    const liveWins = liveTrades.filter(t => t.pnl > 0);
    const liveLosses = liveTrades.filter(t => t.pnl <= 0);
    const liveWinRate = liveTrades.length > 0 ? liveWins.length / liveTrades.length : 0;
    const liveProfitFactor = liveLosses.length > 0 && liveLosses.reduce((s, t) => s + t.pnl, 0) !== 0
      ? Math.abs(liveWins.reduce((s, t) => s + t.pnl, 0) / liveLosses.reduce((s, t) => s + t.pnl, 0))
      : (liveWins.length > 0 ? Infinity : 0);

    // Total P&L = realized (closed trades) + unrealized (open positions)
    const totalPnl = realizedPnl + totalUnrealized;
    const totalReturn = initialCapital > 0 ? totalPnl / initialCapital : 0;
    const currentEquity = initialCapital + totalPnl;
    const cashAvailable = initialCapital - totalCostBasis + realizedPnl - liveCommission;

    // Today's P&L = today's realized (closed today) + today's unrealized change (positions day pnl)
    const today = new Date().toISOString().slice(0, 10);
    const todayRealizedPnl = liveTrades
      .filter(t => t.exit_date === today)
      .reduce((sum, t) => sum + (t.pnl || 0), 0);
    const todayUnrealizedPnl = livePositions.reduce((sum, p) => {
      const qty = Number(p.quantity) || 0;
      const chgAmt = Number(p.chgAmt) || 0;
      return sum + chgAmt * qty;
    }, 0);
    const todayPnl = todayRealizedPnl + todayUnrealizedPnl;

    return NextResponse.json({
      summary,
      trades,
      daily_pnl: dailyPnlClean,
      per_strategy: perStrategy,
      per_stock: perStock,
      positions: dailyPositions,
      live: {
        positions: livePositions,
        trades: enrichedLiveTrades,
        n_positions: livePositions.length,
        total_unrealized: round(totalUnrealized, 2),
        total_market_value: round(totalMktVal, 2),
        realized_pnl: round(realizedPnl, 2),
        total_pnl: round(totalPnl, 2),
        total_return: round(totalReturn, 4),
        current_equity: round(currentEquity, 2),
        cash: round(cashAvailable, 2),
        initial_capital: initialCapital,
        total_trades: liveTrades.length,
        win_rate: round(liveWinRate, 4),
        profit_factor: liveProfitFactor === Infinity ? "inf" : round(liveProfitFactor, 2),
        total_commission: round(liveCommission, 2),
        today_pnl: round(todayPnl, 2),
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: String(e), summary: null, trades: [], daily_pnl: [], per_strategy: {}, per_stock: {}, positions: {}, live: { positions: [], trades: [], n_positions: 0, total_unrealized: 0, total_market_value: 0, realized_pnl: 0, total_pnl: 0, total_return: 0, current_equity: 0, cash: 0, initial_capital: 0, total_trades: 0, win_rate: 0, profit_factor: 0, total_commission: 0, today_pnl: 0 } },
      { status: 500 },
    );
  }
}
