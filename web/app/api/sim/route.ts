import { NextResponse } from "next/server";
import Database from "better-sqlite3";
import { join } from "path";

const DB_PATH = join(process.cwd(), "..", "src", "data", "sim_trading.db");

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
    const db = new Database(DB_PATH, { readonly: true });

    const trades = db
      .prepare(
        `SELECT trade_id, code, direction, entry_price, exit_price,
                quantity, pnl, pnl_pct, hold_days, exit_reason, notes,
                confidence, entry_date, exit_date, commission
         FROM trades ORDER BY id`,
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

    db.close();

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

    return NextResponse.json({
      summary,
      trades,
      daily_pnl: dailyPnlClean,
      per_strategy: perStrategy,
      per_stock: perStock,
      positions: dailyPositions,
    });
  } catch (e) {
    return NextResponse.json(
      { error: String(e), summary: null, trades: [], daily_pnl: [], per_strategy: {}, per_stock: {}, positions: {} },
      { status: 500 },
    );
  }
}
