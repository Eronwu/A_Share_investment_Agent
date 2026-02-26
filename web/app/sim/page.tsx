"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

/* ── Types ── */

interface Trade {
  trade_id: string;
  code: string;
  name?: string;
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
  entry_time?: number;  // epoch ms
  exit_time?: number;   // epoch ms
  commission?: number;
}

interface DailyPnl {
  date: string;
  total_equity: number;
  cash: number;
  invested: number;
  daily_return: number;
  cumulative_return: number;
  drawdown_pct: number;
}

interface Attribution {
  trades: number;
  pnl: number;
  win_rate: number;
}

interface Summary {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  total_return: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number | string;
  avg_hold_days: number;
  total_commission: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  max_drawdown_abs: number;
  calmar_ratio: number;
  final_equity: number;
  initial_capital: number;
  trading_days: number;
}

interface PositionSnap {
  entry_price: number;
  current_price: number;
  quantity: number;
  stop_loss: number;
  take_profit: number | null;
  unrealized_pnl: number;
  hold_days_target: number;
}

interface LivePosition {
  code: string;
  name: string;
  entry_price: number;
  quantity: number;
  current_price: number;
  entry_time: number;
  entry_date: string;
  stop_loss: number;
  take_profit: number | null;
  max_hold_days: number;
  entry_strategy: string;
  confidence: number;
  unrealized_pnl: number;
  pnl_pct: number;
  daily_score: number;
  change: number;
  chgAmt: number;
  last_updated: number;
}

interface LiveData {
  positions: LivePosition[];
  trades: Trade[];
  n_positions: number;
  total_unrealized: number;
  total_market_value: number;
  realized_pnl: number;
  total_pnl: number;
  total_return: number;
  current_equity: number;
  cash: number;
  initial_capital: number;
  total_trades: number;
  win_rate: number;
  profit_factor: number | string;
  total_commission: number;
  today_pnl: number;
  today_return: number;
}

interface SimData {
  summary: Summary;
  trades: Trade[];
  daily_pnl: DailyPnl[];
  per_strategy: Record<string, Attribution>;
  per_stock: Record<string, Attribution>;
  positions: Record<string, Record<string, PositionSnap>>;
  live: LiveData;
  error?: string;
}

/* ── Helpers ── */

const pnlColor = (v: number) => (v > 0 ? D.red : v < 0 ? D.green : D.comment);
const pctFmt = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
const numFmt = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });

/** epoch ms → "MM-DD HH:MM" */
function tsToTime(ts: number | undefined, fallbackDate?: string): string {
  if (!ts || ts <= 0) return fallbackDate || "-";
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** 退出原因翻译 — 保留关键数值 */
function exitReasonCN(r: string): string {
  // stop_loss(96.55) → 触发止损 @96.55
  const slMatch = r.match(/stop_loss\(([^)]+)\)/);
  if (slMatch) return `触发止损 @${slMatch[1]}`;
  // take_profit(130.00) → 触发止盈 @130.00
  const tpMatch = r.match(/take_profit\(([^)]+)\)/);
  if (tpMatch) return `触发止盈 @${tpMatch[1]}`;
  // max_hold(10d) → 持仓到期 10天
  if (r.startsWith("max_hold")) return "持仓到期";
  if (r === "force_close_eod") return "收盘强平";
  // exit_score=36<40 → 评分退出 36<40
  const scoreMatch = r.match(/exit_score=(\d+)<(\d+)/);
  if (scoreMatch) return `评分退出 ${scoreMatch[1]}<${scoreMatch[2]}`;
  // T3:large_order_reversal(v2) → T3大单翻转
  if (r.includes("large_order_reversal")) return "T3大单翻转";
  if (r.includes("volume_price_divergence")) return "T3量价背离";
  if (r.includes("macd_top_divergence")) return "T3 MACD顶背离";
  if (r.includes("closing_surge")) return "T3尾盘异动";
  if (r.includes("support_breakdown")) return "T3跌破支撑";
  if (r.includes("rsi_extreme")) return "T3 RSI极值";
  if (r.includes("composite_bearish")) return "空头综合信号";
  if (r.includes("momentum_sell")) return "动量转空";
  if (r.includes("sell_next_open")) return "次日开盘卖";
  return r;
}

/** 策略/入场原因翻译 — 保留评分数值 */
function strategyCN(s: string): string {
  // daily_score=75 → 日线评分75
  const dsMatch = s.match(/daily_score=(\d+)/);
  if (dsMatch) return `日线评分${dsMatch[1]}`;
  // intraday_exception:momentum_alert(score=65) → 日内例外:动量(65)
  const ieMatch = s.match(/intraday_exception:(\w+)\(score=(\d+)\)/);
  if (ieMatch) return `日内例外:${signalCN(ieMatch[1])}(${ieMatch[2]})`;

  const map: Record<string, string> = {
    composite_bullish: "L2多头综合",
    composite_bearish: "L2空头综合",
    momentum_alert: "L2动量确认",
    momentum_sell_alert: "L2动量卖出",
    volume_accel_alert: "L2放量加速",
    volume_accel_sell_alert: "L2放量砸盘",
    macd_golden_cross: "日K MACD金叉",
    macd_death_cross: "日K MACD死叉",
    ma_bullish_align: "日K均线多头排列",
    ma_bearish_align: "日K均线空头排列",
    volume_breakout: "日K放量突破",
    breakout_pullback: "日K突破回踩",
    bollinger_squeeze_breakout: "日K布林突破",
    morning_evening_star: "日K星线形态",
    force_close: "强制平仓",
    "T3:large_order_reversal": "T3大单翻转",
    "T3:large_order_reversal(v2)": "T3大单翻转(v2)",
    "T3:volume_price_divergence": "T3量价背离",
    "T3:macd_top_divergence": "T3 MACD顶背离",
  };
  return map[s] || s;
}

/** 信号名简写翻译 */
function signalCN(s: string): string {
  const m: Record<string, string> = {
    momentum_alert: "动量", volume_accel_alert: "放量",
    composite_bullish: "综合多", macd_golden_cross: "MACD金叉",
    ma_bullish_align: "均线多", volume_breakout: "放量突破",
    breakout_pullback: "突破回踩",
  };
  return m[s] || s;
}

/* ── Components ── */

function TitleBar() {
  return (
    <div
      style={{
        background: "#21222c",
        height: 30,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        borderBottom: "1px solid #191a21",
        userSelect: "none",
      }}
    >
      <div style={{ position: "absolute", left: 12, display: "flex", gap: 8 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
          <span
            key={c}
            style={{
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: c,
              display: "inline-block",
            }}
          />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>
        ✱ sim — trading
      </span>
    </div>
  );
}

function NavBar({ data }: { data: SimData | null }) {
  return (
    <div
      style={{
        background: "#21222c",
        padding: "6px 16px",
        display: "flex",
        gap: 16,
        alignItems: "center",
        borderBottom: "1px solid #191a21",
        fontSize: 13,
      }}
    >
      <Link href="/" style={{ color: D.cyan, textDecoration: "none" }}>
        ← monitor
      </Link>
      <span style={{ color: D.comment }}>|</span>
      <Link href="/alerts" style={{ color: D.comment, textDecoration: "none" }}>
        alerts
      </Link>
      <span style={{ color: D.purple, fontWeight: 700 }}>sim</span>
      <Link href="/sector" style={{ color: D.comment, textDecoration: "none" }}>
        sector
      </Link>
      <Link href="/manage" style={{ color: D.comment, textDecoration: "none" }}>
        manage
      </Link>
      {data && (
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
          {data.live.n_positions > 0 && (
            <>
              {data.live.n_positions} positions |{" "}
              <span style={{ color: pnlColor(data.live.total_unrealized) }}>
                P&L {data.live.total_unrealized >= 0 ? "+" : ""}{numFmt(data.live.total_unrealized)}
              </span>
              {" | "}
            </>
          )}
          {data.summary.total_trades} trades | {data.summary.trading_days} days
        </span>
      )}
    </div>
  );
}

function Prompt({ cmd }: { cmd: string }) {
  return (
    <div style={{ padding: "4px 0" }}>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sim</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>{cmd}</span>
    </div>
  );
}

function SummaryBar({ s }: { s: Summary }) {
  const sharpeColor = s.sharpe_ratio > 1 ? D.purple : s.sharpe_ratio > 0 ? D.orange : D.red;
  const winColor = s.win_rate > 0.5 ? D.green : s.win_rate > 0.3 ? D.orange : D.red;
  const pfVal = typeof s.profit_factor === "string" ? s.profit_factor : s.profit_factor.toFixed(2);
  const pfColor =
    typeof s.profit_factor === "string" || s.profit_factor > 1.5
      ? D.green
      : s.profit_factor > 1
        ? D.orange
        : D.red;

  return (
    <div
      style={{
        padding: "6px 0",
        display: "flex",
        flexWrap: "wrap",
        gap: "4px 20px",
        fontSize: 13,
        borderBottom: `1px solid ${D.currentLine}`,
        marginBottom: 8,
      }}
    >
      <span>
        <span style={{ color: D.comment }}>收益率 </span>
        <span style={{ color: pnlColor(s.total_return), fontWeight: 700 }}>
          {pctFmt(s.total_return)}
        </span>
      </span>
      <span>
        <span style={{ color: D.comment }}>夏普 </span>
        <span style={{ color: sharpeColor, fontWeight: 700 }}>
          {s.sharpe_ratio.toFixed(2)}
        </span>
      </span>
      <span>
        <span style={{ color: D.comment }}>胜率 </span>
        <span style={{ color: winColor, fontWeight: 700 }}>
          {(s.win_rate * 100).toFixed(1)}%
        </span>
      </span>
      <span>
        <span style={{ color: D.comment }}>最大回撤 </span>
        <span style={{ color: D.red, fontWeight: 700 }}>
          -{(s.max_drawdown_pct * 100).toFixed(2)}%
        </span>
      </span>
      <span>
        <span style={{ color: D.comment }}>盈亏比 </span>
        <span style={{ color: pfColor, fontWeight: 700 }}>{pfVal}</span>
      </span>
      <span>
        <span style={{ color: D.comment }}>净值 </span>
        <span style={{ color: D.fg, fontWeight: 700 }}>
          {numFmt(s.final_equity)}
        </span>
      </span>
      <span>
        <span style={{ color: D.comment }}>交易 </span>
        <span style={{ color: D.fg }}>{s.total_trades}笔</span>
      </span>
      <span>
        <span style={{ color: D.comment }}>手续费 </span>
        <span style={{ color: D.orange }}>{numFmt(s.total_commission)}</span>
      </span>
    </div>
  );
}

function EquityCurve({ data, initialCapital }: { data: DailyPnl[]; initialCapital: number }) {
  if (data.length < 2) {
    return (
      <div style={{ color: D.comment, padding: "8px 0" }}>
        <span style={{ color: D.purple }}>▾</span> # 净值曲线需要 2+ 个数据点（当前: {data.length}）
        {data.length === 1 &&
          ` — 净值: ${numFmt(data[0].total_equity)} HKD`}
      </div>
    );
  }

  const W = 760;
  const H = 130;
  const PAD = { top: 12, bottom: 28, left: 56, right: 12 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const equities = data.map((d) => d.total_equity);
  const minE = Math.min(...equities, initialCapital);
  const maxE = Math.max(...equities, initialCapital);
  const range = maxE - minE || 1;

  const toY = (v: number) => PAD.top + plotH - ((v - minE) / range) * plotH;

  const points = data.map((d, i) => ({
    x: PAD.left + (i / (data.length - 1)) * plotW,
    y: toY(d.total_equity),
    date: d.date,
    equity: d.total_equity,
  }));

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
  const areaPath = `${linePath} L${points[points.length - 1].x.toFixed(1)},${(PAD.top + plotH).toFixed(1)} L${points[0].x.toFixed(1)},${(PAD.top + plotH).toFixed(1)} Z`;

  const lineColor = equities[equities.length - 1] >= initialCapital ? D.green : D.red;
  const capY = toY(initialCapital);

  return (
    <div style={{ padding: "4px 0 8px" }}>
      <div style={{ color: D.comment, marginBottom: 4 }}>
        <span style={{ color: D.purple }}>▾</span> # ── 净值曲线 ──
      </div>
      <svg width={W} height={H} style={{ display: "block" }}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity={0.25} />
            <stop offset="100%" stopColor={lineColor} stopOpacity={0.03} />
          </linearGradient>
        </defs>
        {/* 网格线 */}
        {[0, 0.25, 0.5, 0.75, 1].map((pct) => {
          const y = PAD.top + plotH - pct * plotH;
          const val = minE + pct * range;
          return (
            <g key={pct}>
              <line
                x1={PAD.left} y1={y} x2={W - PAD.right} y2={y}
                stroke={D.currentLine} strokeWidth={0.5}
              />
              <text
                x={PAD.left - 4} y={y + 3} textAnchor="end"
                fill={D.comment} fontSize={9} fontFamily="JetBrains Mono"
              >
                {(val / 1000).toFixed(0)}k
              </text>
            </g>
          );
        })}
        {/* 初始资金虚线 */}
        <line
          x1={PAD.left} y1={capY} x2={W - PAD.right} y2={capY}
          stroke={D.comment} strokeWidth={0.5} strokeDasharray="4 3"
        />
        <text
          x={W - PAD.right + 2} y={capY + 3}
          fill={D.comment} fontSize={8} fontFamily="JetBrains Mono"
        >
          初始
        </text>
        {/* 填充 */}
        <path d={areaPath} fill="url(#eqGrad)" />
        {/* 折线 */}
        <path d={linePath} fill="none" stroke={lineColor} strokeWidth={1.5} />
        {/* 数据点 */}
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3}
            fill={lineColor} stroke={D.bg} strokeWidth={1}
          />
        ))}
        {/* X轴日期 */}
        {points.map((p, i) => (
          <text key={i} x={p.x} y={H - 6} textAnchor="middle"
            fill={D.comment} fontSize={9} fontFamily="JetBrains Mono"
          >
            {p.date.slice(5)}
          </text>
        ))}
        {/* 净值标签 */}
        {points.map((p, i) => (
          <text key={`v${i}`} x={p.x} y={p.y - 7} textAnchor="middle"
            fill={D.fg} fontSize={8} fontFamily="JetBrains Mono"
          >
            {(p.equity / 1000).toFixed(0)}k
          </text>
        ))}
      </svg>
    </div>
  );
}

/** 生成单笔交易的复盘点评 */
function tradeReviewCN(t: Trade): { text: string; color: string } {
  const pnlPct = t.pnl_pct * 100;
  const fee = t.commission || 0;
  const gross = t.pnl + fee;
  const holdMs = (t.exit_time || 0) - (t.entry_time || 0);
  const holdMin = holdMs > 0 ? holdMs / 60000 : 0;
  const er = t.exit_reason;

  // 盈利交易
  if (t.pnl > 0) {
    if (er.startsWith("stop_loss")) return { text: "盈利止损,控制得当", color: D.green };
    if (er.startsWith("take_profit")) return { text: "目标达成,纪律执行", color: D.green };
    if (er.includes("T3:")) return { text: "T3平仓,小赚离场", color: D.green };
    return { text: `盈利${pnlPct.toFixed(1)}%,执行OK`, color: D.green };
  }

  // 亏损交易
  // 手续费杀利润
  if (gross > 0 && t.pnl <= 0) {
    return { text: `毛利+${gross.toFixed(0)}被手续费吞`, color: D.orange };
  }

  // 瞬间交易
  if (holdMin < 1) {
    return { text: "瞬间平仓,入场即出错", color: D.red };
  }

  // 评分退出
  if (er.startsWith("exit_score")) {
    if (pnlPct > -3) return { text: "评分退出,小亏离场", color: D.orange };
    return { text: "评分退出,趋势判断错误", color: D.red };
  }

  // 止损退出
  if (er.startsWith("stop_loss")) {
    if (holdMin < 60) return { text: "快速止损,入场时机差", color: D.orange };
    if (pnlPct > -5) return { text: "正常止损,风控有效", color: D.comment };
    return { text: "大幅止损,需优化入场", color: D.red };
  }

  // T3 平仓亏损
  if (er.includes("T3:") || er.includes("large_order_reversal")) {
    if (holdMin < 30) return { text: "T3频繁割肉,策略需优化", color: D.red };
    return { text: "T3纠偏,信号反转", color: D.orange };
  }

  // 其他
  if (pnlPct > -2) return { text: "小亏,可接受", color: D.comment };
  return { text: `亏${pnlPct.toFixed(1)}%,需复盘入场逻辑`, color: D.red };
}

function TradesTable({ trades, bare }: { trades: Trade[]; bare?: boolean }) {
  const [open, setOpen] = useState(true);

  const tableContent = (
    <>
          {/* 表头 */}
          <div
            style={{
              display: "flex", whiteSpace: "pre", color: D.pink,
              borderBottom: `1px solid ${D.currentLine}`,
              paddingBottom: 3, marginBottom: 2, fontWeight: 500, fontSize: 12,
            }}
          >
            <span style={{ width: "10ch" }}>代码</span>
            <span style={{ width: "8ch" }}>名称</span>
            <span style={{ width: "9ch", textAlign: "right" }}>买入</span>
            <span style={{ width: "3ch", textAlign: "center" }}>→</span>
            <span style={{ width: "9ch", textAlign: "right" }}>卖出</span>
            <span style={{ width: "8ch", textAlign: "right" }}>数量</span>
            <span style={{ width: "10ch", textAlign: "right" }}>盈亏</span>
            <span style={{ width: "8ch", textAlign: "right" }}>盈亏%</span>
            <span style={{ width: "12ch", paddingLeft: "1ch" }}>退出原因</span>
            <span style={{ width: "12ch" }}>入场策略</span>
            <span style={{ width: "10ch", textAlign: "right" }}>日期</span>
            <span style={{ paddingLeft: "1ch" }}>复盘</span>
          </div>
          {/* 行 */}
          {trades.map((t) => {
            const c = pnlColor(t.pnl);
            const review = tradeReviewCN(t);
            return (
              <div
                key={t.trade_id}
                style={{
                  display: "flex", whiteSpace: "pre", padding: "1px 0",
                  borderBottom: "1px solid #191a21", fontSize: 12,
                }}
              >
                <span style={{ color: D.cyan, width: "10ch" }}>{t.code}</span>
                <span style={{ color: D.fg, width: "8ch" }}>{(t.name || "").slice(0, 6)}</span>
                <span style={{ color: D.fg, width: "9ch", textAlign: "right" }}>
                  {t.entry_price.toFixed(2)}
                </span>
                <span style={{ color: D.comment, width: "3ch", textAlign: "center" }}>→</span>
                <span style={{ color: D.fg, width: "9ch", textAlign: "right" }}>
                  {t.exit_price.toFixed(2)}
                </span>
                <span style={{ color: D.fg, width: "8ch", textAlign: "right" }}>
                  {t.quantity.toLocaleString()}
                </span>
                <span style={{ color: c, width: "10ch", textAlign: "right", fontWeight: 500 }}>
                  {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(0)}
                </span>
                <span style={{ color: c, width: "8ch", textAlign: "right", fontWeight: 500 }}>
                  {t.pnl_pct * 100 >= 0 ? "+" : ""}{(t.pnl_pct * 100).toFixed(1)}%
                </span>
                <span style={{ color: D.orange, width: "12ch", paddingLeft: "1ch" }}>
                  {exitReasonCN(t.exit_reason)}
                </span>
                <span style={{ color: D.comment, width: "12ch" }}>
                  {strategyCN(t.notes)}
                </span>
                <span style={{ color: D.comment, width: "10ch", textAlign: "right" }}>
                  {t.exit_date || t.entry_date}
                </span>
                <span style={{ color: review.color, paddingLeft: "1ch", fontWeight: 500 }}>
                  {review.text}
                </span>
              </div>
            );
          })}
        </>
  );

  if (bare) return <div style={{ padding: "4px 0" }}>{tableContent}</div>;

  return (
    <div style={{ padding: "4px 0" }}>
      <div
        style={{
          color: D.comment, padding: "4px 0 2px",
          cursor: "pointer", userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span> # ──
        交易记录 ({trades.length}笔) ──
      </div>
      {open && tableContent}
    </div>
  );
}

function AttributionPanel({
  title,
  data,
  nameWidth,
  isStrategy,
}: {
  title: string;
  data: Record<string, Attribution>;
  nameWidth: string;
  isStrategy?: boolean;
}) {
  const entries = Object.entries(data);
  return (
    <div style={{ flex: 1, minWidth: 280 }}>
      <div style={{ color: D.comment, padding: "4px 0 2px" }}>
        <span style={{ color: D.purple }}>▾</span> # ── {title} ──
      </div>
      <div
        style={{
          display: "flex", whiteSpace: "pre", color: D.pink,
          borderBottom: `1px solid ${D.currentLine}`,
          paddingBottom: 3, marginBottom: 2, fontWeight: 500, fontSize: 12,
        }}
      >
        <span style={{ width: nameWidth }}>{isStrategy ? "策略" : "代码"}</span>
        <span style={{ width: "7ch", textAlign: "right" }}>笔数</span>
        <span style={{ width: "11ch", textAlign: "right" }}>盈亏</span>
        <span style={{ width: "7ch", textAlign: "right" }}>胜率</span>
      </div>
      {entries.map(([name, d]) => (
        <div
          key={name}
          style={{
            display: "flex", whiteSpace: "pre", padding: "1px 0",
            borderBottom: "1px solid #191a21", fontSize: 12,
          }}
        >
          <span style={{ color: D.fg, width: nameWidth }}>
            {isStrategy ? strategyCN(name) : name}
          </span>
          <span style={{ color: D.comment, width: "7ch", textAlign: "right" }}>
            {d.trades}
          </span>
          <span
            style={{
              color: pnlColor(d.pnl), width: "11ch",
              textAlign: "right", fontWeight: 500,
            }}
          >
            {d.pnl >= 0 ? "+" : ""}{d.pnl.toFixed(0)}
          </span>
          <span
            style={{
              color: d.win_rate > 0.5 ? D.green : d.win_rate > 0 ? D.orange : D.comment,
              width: "7ch", textAlign: "right",
            }}
          >
            {(d.win_rate * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function PositionsTable({
  positions,
}: {
  positions: Record<string, Record<string, PositionSnap>>;
}) {
  const [open, setOpen] = useState(true);

  // 找最新一天有持仓的快照
  const dates = Object.keys(positions).sort().reverse();
  const latestDate = dates.find((d) => Object.keys(positions[d]).length > 0);
  if (!latestDate) return null;

  const dayPos = positions[latestDate];
  const codes = Object.keys(dayPos);
  if (codes.length === 0) return null;

  const totalMktVal = codes.reduce(
    (sum, c) => sum + dayPos[c].current_price * dayPos[c].quantity, 0,
  );
  const totalUnrealized = codes.reduce(
    (sum, c) => sum + (dayPos[c].unrealized_pnl || 0), 0,
  );

  return (
    <div style={{ padding: "4px 0" }}>
      <div
        style={{
          color: D.comment, padding: "4px 0 2px",
          cursor: "pointer", userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span> # ──
        模拟持仓 ({codes.length}只 | 市值 {numFmt(totalMktVal)} |{" "}
        <span style={{ color: pnlColor(totalUnrealized) }}>
          浮盈 {totalUnrealized >= 0 ? "+" : ""}{numFmt(totalUnrealized)}
        </span>
        ) ── <span style={{ fontSize: 10 }}>{latestDate} EOD</span>
      </div>
      {open && (
        <>
          {/* 表头 — 与主页 holdHeader 风格一致 */}
          <div
            style={{
              display: "flex", whiteSpace: "pre", color: D.pink,
              borderBottom: `1px solid ${D.currentLine}`,
              paddingBottom: 3, marginBottom: 2, fontWeight: 500,
            }}
          >
            <span style={{ width: "6ch" }}> 类型</span>
            <span style={{ width: "10ch" }}>代码</span>
            <span style={{ width: "10ch", textAlign: "right" }}>   现价</span>
            <span style={{ width: "9ch", textAlign: "right" }}>  成本</span>
            <span style={{ width: "10ch", textAlign: "right" }}>  盈亏%</span>
            <span style={{ width: "10ch", textAlign: "right" }}>   市值</span>
            <span style={{ width: "10ch", textAlign: "right" }}>  浮盈</span>
            <span style={{ width: "9ch", textAlign: "right" }}>  止损</span>
            <span style={{ width: "9ch", textAlign: "right" }}>  止盈</span>
          </div>
          {/* 行 — 与主页 HoldRow 风格一致 */}
          {codes.map((code) => {
            const p = dayPos[code];
            const pnlPct = p.entry_price > 0
              ? (p.current_price - p.entry_price) / p.entry_price
              : 0;
            const mktVal = p.current_price * p.quantity;
            const c = pnlColor(p.unrealized_pnl);
            return (
              <div
                key={code}
                style={{
                  display: "flex", whiteSpace: "pre", padding: "1px 0",
                  borderBottom: "1px solid #191a21",
                }}
              >
                <span style={{ color: D.orange, width: "6ch" }}> SIM</span>
                <span style={{ color: D.cyan, width: "10ch" }}>{code}</span>
                <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
                  {p.current_price.toFixed(2)}
                </span>
                <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
                  {p.entry_price.toFixed(2)}
                </span>
                <span style={{ color: c, width: "10ch", textAlign: "right", fontWeight: 500 }}>
                  {pnlPct >= 0 ? "+" : ""}{(pnlPct * 100).toFixed(1)}%
                </span>
                <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
                  {numFmt(mktVal)}
                </span>
                <span style={{ color: c, width: "10ch", textAlign: "right", fontWeight: 500 }}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}{numFmt(p.unrealized_pnl)}
                </span>
                <span style={{ color: D.orange, width: "9ch", textAlign: "right" }}>
                  {p.stop_loss.toFixed(2)}
                </span>
                <span style={{ color: D.green, width: "9ch", textAlign: "right" }}>
                  {p.take_profit ? p.take_profit.toFixed(2) : "-"}
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

function LiveSummaryBar({ live, ts }: { live: LiveData; ts: string }) {
  const pfVal = typeof live.profit_factor === "string" ? live.profit_factor : live.profit_factor.toFixed(2);
  const pfColor =
    typeof live.profit_factor === "string" || live.profit_factor > 1.5
      ? D.green : live.profit_factor > 1 ? D.orange : D.red;
  const winColor = live.win_rate > 0.5 ? D.green : live.win_rate > 0.3 ? D.orange : D.red;

  return (
    <div style={{ borderBottom: `1px solid ${D.currentLine}`, marginBottom: 8, padding: "4px 0 6px" }}>
      {/* 第一行：核心指标（大字） */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 28px", fontSize: 15, marginBottom: 4 }}>
        <span>
          <span style={{ color: D.comment }}>总盈亏 </span>
          <span style={{ color: pnlColor(live.total_pnl), fontWeight: 700, fontSize: 17 }}>
            {live.total_pnl >= 0 ? "+" : ""}{numFmt(live.total_pnl)}
          </span>
        </span>
        <span>
          <span style={{ color: D.comment }}>总收益 </span>
          <span style={{ color: pnlColor(live.total_return), fontWeight: 700, fontSize: 17 }}>
            {pctFmt(live.total_return)}
          </span>
        </span>
        <span>
          <span style={{ color: D.comment }}>今日 </span>
          <span style={{ color: pnlColor(live.today_pnl), fontWeight: 700 }}>
            {live.today_pnl >= 0 ? "+" : ""}{numFmt(live.today_pnl)}
          </span>
          <span style={{ color: pnlColor(live.today_return), fontWeight: 700, marginLeft: 4 }}>
            {pctFmt(live.today_return)}
          </span>
        </span>
        <span>
          <span style={{ color: D.comment }}>浮盈 </span>
          <span style={{ color: pnlColor(live.total_unrealized), fontWeight: 700 }}>
            {live.total_unrealized >= 0 ? "+" : ""}{numFmt(live.total_unrealized)}
          </span>
        </span>
        <span>
          <span style={{ color: D.comment }}>已实现 </span>
          <span style={{ color: pnlColor(live.realized_pnl), fontWeight: 700 }}>
            {live.realized_pnl >= 0 ? "+" : ""}{numFmt(live.realized_pnl)}
          </span>
        </span>
      </div>
      {/* 第二行：辅助指标（小字）+ 时间戳 */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 20px", fontSize: 11, color: D.comment }}>
        <span>
          胜率{" "}
          <span style={{ color: live.total_trades >= 10 ? winColor : D.comment }}>
            {live.total_trades > 0 ? `${(live.win_rate * 100).toFixed(0)}%` : "-"}
          </span>
          {live.total_trades > 0 && live.total_trades < 10 && (
            <span style={{ color: D.comment, fontSize: 10 }}> (n={live.total_trades})</span>
          )}
        </span>
        <span>
          盈亏比{" "}
          <span style={{ color: live.total_trades >= 10 ? pfColor : D.comment }}>
            {live.total_trades > 0 ? pfVal : "-"}
          </span>
        </span>
        <span>总市值 <span style={{ color: D.fg }}>{numFmt(live.total_market_value)}</span></span>
        <span>总资产 <span style={{ color: D.fg }}>{numFmt(live.current_equity)}</span></span>
        <span>可用 <span style={{ color: D.fg }}>{numFmt(live.cash)}</span></span>
        <span>交易 <span style={{ color: D.fg }}>{live.total_trades}笔</span></span>
        <span>手续费 <span style={{ color: D.orange }}>{numFmt(live.total_commission)}</span></span>
        <span style={{ marginLeft: "auto" }}>
          {ts}
        </span>
      </div>
    </div>
  );
}

function LivePanel({ live }: { live: LiveData }) {
  const [posOpen, setPosOpen] = useState(true);
  const [tradesOpen, setTradesOpen] = useState(true);

  if (live.n_positions === 0 && live.trades.length === 0) {
    return (
      <div style={{ color: D.comment, padding: "8px 0" }}>
        <span style={{ color: D.yellow }}>info</span> 实时引擎运行中，暂无持仓和交易。等待信号触发...
      </div>
    );
  }

  return (
    <div>
      {/* 实时持仓 */}
      {live.positions.length > 0 && (
        <div style={{ padding: "4px 0" }}>
          <div
            style={{ color: D.comment, padding: "4px 0 2px", cursor: "pointer", userSelect: "none" }}
            onClick={() => setPosOpen((v) => !v)}
          >
            <span style={{ color: D.purple }}>{posOpen ? "▾" : "▸"}</span> # ──
            实时持仓 ({live.positions.length}只 | 市值 {numFmt(live.total_market_value)} |{" "}
            <span style={{ color: pnlColor(live.total_unrealized) }}>
              浮盈 {live.total_unrealized >= 0 ? "+" : ""}{numFmt(live.total_unrealized)}
            </span>
            ) ──
          </div>
          {posOpen && (
            <>
              <div
                style={{
                  display: "flex", whiteSpace: "pre", color: D.pink,
                  borderBottom: `1px solid ${D.currentLine}`,
                  paddingBottom: 3, marginBottom: 2, fontWeight: 500,
                }}
              >
                <span style={{ width: "6ch" }}> 类型</span>
                <span style={{ width: "10ch" }}>代码</span>
                <span style={{ width: "8ch" }}>名称</span>
                <span style={{ width: "5ch", textAlign: "right" }}>评分</span>
                <span style={{ width: "10ch", textAlign: "right" }}>   现价</span>
                <span style={{ width: "9ch", textAlign: "right" }}>涨跌幅</span>
                <span style={{ width: "9ch", textAlign: "right" }}>  成本</span>
                <span style={{ width: "7ch", textAlign: "right" }}> 股数</span>
                <span style={{ width: "10ch", textAlign: "right" }}>  盈亏%</span>
                <span style={{ width: "10ch", textAlign: "right" }}>   市值</span>
                <span style={{ width: "10ch", textAlign: "right" }}>  浮盈</span>
                <span style={{ width: "9ch", textAlign: "right" }}>  止损</span>
                <span style={{ width: "8ch", textAlign: "right" }}>距止损</span>
                <span style={{ width: "9ch", textAlign: "right" }}>  止盈</span>
              </div>
              {live.positions.map((p) => {
                const mktVal = p.current_price * p.quantity;
                const c = pnlColor(p.unrealized_pnl);
                const chgSign = p.change > 0 ? "+" : "";
                // 距止损百分比: 正值=安全, 越小越危险
                const slDist = p.stop_loss > 0 && p.current_price > 0
                  ? (p.current_price - p.stop_loss) / p.current_price
                  : 0;
                const slDistColor = slDist < 0.01 ? D.red : slDist < 0.02 ? D.orange : D.comment;
                const score = p.daily_score || 0;
                const scoreColor = score >= 70 ? D.green : score >= 40 ? D.orange : D.red;
                return (
                  <div
                    key={p.code}
                    style={{
                      display: "flex", whiteSpace: "pre", padding: "1px 0",
                      borderBottom: "1px solid #191a21",
                      background: slDist < 0.01 ? "#ff555510" : "transparent",
                    }}
                  >
                    <span style={{ color: D.orange, width: "6ch" }}> SIM</span>
                    <span style={{ color: D.cyan, width: "10ch" }}>{p.code}</span>
                    <span style={{ color: D.fg, width: "8ch" }}>{(p.name || "").slice(0, 6)}</span>
                    <span style={{ color: scoreColor, width: "5ch", textAlign: "right", fontWeight: 500 }}>
                      {score > 0 ? score : "-"}
                    </span>
                    <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
                      {p.current_price.toFixed(2)}
                    </span>
                    <span style={{ color: pnlColor(p.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
                      {chgSign}{p.change.toFixed(2)}%
                    </span>
                    <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
                      {p.entry_price.toFixed(2)}
                    </span>
                    <span style={{ color: D.fg, width: "7ch", textAlign: "right" }}>
                      {p.quantity}
                    </span>
                    <span style={{ color: c, width: "10ch", textAlign: "right", fontWeight: 500 }}>
                      {p.pnl_pct >= 0 ? "+" : ""}{(p.pnl_pct * 100).toFixed(1)}%
                    </span>
                    <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
                      {numFmt(mktVal)}
                    </span>
                    <span style={{ color: c, width: "10ch", textAlign: "right", fontWeight: 500 }}>
                      {p.unrealized_pnl >= 0 ? "+" : ""}{numFmt(p.unrealized_pnl)}
                    </span>
                    <span style={{ color: D.orange, width: "9ch", textAlign: "right" }}>
                      {p.stop_loss.toFixed(2)}
                    </span>
                    <span style={{ color: slDistColor, width: "8ch", textAlign: "right", fontWeight: slDist < 0.02 ? 700 : 400 }}>
                      {(slDist * 100).toFixed(1)}%
                    </span>
                    <span style={{ color: D.green, width: "9ch", textAlign: "right" }}>
                      {p.take_profit ? p.take_profit.toFixed(2) : "-"}
                    </span>
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}

      {/* 操作记录 — 每笔 BUY/SELL 单独一行 */}
      <OperationsLog live={live} />

      {/* 已完成交易 — 完整闭环 */}
      {live.trades.length > 0 && (
        <CompletedTradesTable trades={live.trades} />
      )}
    </div>
  );
}

function OperationsLog({ live }: { live: LiveData }) {
  const [open, setOpen] = useState(true);

  // 构造操作记录：开仓(持仓) + 清仓(已平仓交易的SELL) + 开仓+清仓(已平仓交易的BUY+SELL)
  interface Op {
    ts: number;
    display: string;
    action: "开仓" | "清仓" | "止损" | "止盈" | "评分退出" | "T3平仓" | "到期平仓";
    code: string;
    name: string;
    price: number;
    quantity: number;
    reason: string;    // 主行原因
    detail?: string;   // 第二行策略详情
    pnl?: number;
  }

  /** 从 exit_reason 推导操作类型 */
  function exitAction(r: string): Op["action"] {
    if (r.startsWith("stop_loss")) return "止损";
    if (r.startsWith("take_profit")) return "止盈";
    if (r.startsWith("exit_score")) return "评分退出";
    if (r.startsWith("max_hold")) return "到期平仓";
    if (r.includes("T3:") || r.includes("large_order_reversal") || r.includes("volume_price") || r.includes("macd_top")) return "T3平仓";
    return "清仓";
  }

  /** 为开仓生成策略详情 */
  function entryDetail(pos: LivePosition): string {
    const parts: string[] = [];
    if (pos.stop_loss > 0) parts.push(`止损 ${pos.stop_loss.toFixed(2)}`);
    if (pos.take_profit) parts.push(`止盈 ${pos.take_profit.toFixed(2)}`);
    if (pos.daily_score > 0) parts.push(`评分${pos.daily_score}`);
    const scoreAction = pos.daily_score >= 70 ? "持有" : pos.daily_score >= 40 ? "观察" : pos.daily_score > 0 ? "待退出" : "";
    if (scoreAction) parts.push(scoreAction);
    if (pos.max_hold_days > 0) parts.push(`最长${pos.max_hold_days}天`);
    return parts.join(" | ");
  }

  /** 为已平仓开仓生成简要策略 */
  function closedEntryDetail(t: Trade): string {
    const parts: string[] = [];
    parts.push(`→ ${exitReasonCN(t.exit_reason)}`);
    if (t.pnl !== 0) parts.push(`${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(0)}`);
    return parts.join(" ");
  }

  const ops: Op[] = [];

  // 已平仓交易 → 开仓 + 清仓 两条
  for (const t of live.trades) {
    const groupTs = Math.max(t.entry_time || 0, t.exit_time || 0);
    ops.push({
      ts: groupTs,
      display: tsToTime(t.entry_time, t.entry_date),
      action: "开仓",
      code: t.code,
      name: t.name || t.code,
      price: t.entry_price,
      quantity: t.quantity,
      reason: strategyCN(t.notes),
      detail: closedEntryDetail(t),
    });
    ops.push({
      ts: groupTs,
      display: tsToTime(t.exit_time, t.exit_date),
      action: exitAction(t.exit_reason),
      code: t.code,
      name: t.name || t.code,
      price: t.exit_price,
      quantity: t.quantity,
      reason: exitReasonCN(t.exit_reason),
      pnl: t.pnl,
    });
  }

  // 当前持仓 → 开仓记录（含策略详情）
  for (const p of live.positions) {
    ops.push({
      ts: p.entry_time || 0,
      display: tsToTime(p.entry_time, p.entry_date),
      action: "开仓",
      code: p.code,
      name: p.name || p.code,
      price: p.entry_price,
      quantity: p.quantity,
      reason: strategyCN(p.entry_strategy),
      detail: entryDetail(p),
    });
  }

  // 按时间倒序。同一笔交易共享 groupTs，清仓排在开仓前
  ops.sort((a, b) => b.ts - a.ts || (a.action !== "开仓" ? -1 : 1));

  if (ops.length === 0) return null;

  const actionColors: Record<string, string> = {
    "开仓": D.red, "清仓": D.green, "止损": "#ff6b6b", "止盈": "#51cf66",
    "评分退出": D.orange, "T3平仓": D.yellow, "到期平仓": D.comment,
  };

  return (
    <div style={{ padding: "4px 0" }}>
      <div
        style={{ color: D.comment, padding: "4px 0 2px", cursor: "pointer", userSelect: "none" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span> # ──
        操作记录 ({ops.length}条) ──
      </div>
      {open && (
        <>
          <div
            style={{
              display: "flex", whiteSpace: "pre", color: D.pink,
              borderBottom: `1px solid ${D.currentLine}`,
              paddingBottom: 3, marginBottom: 2, fontWeight: 500, fontSize: 12,
            }}
          >
            <span style={{ width: "13ch" }}>时间</span>
            <span style={{ width: "8ch" }}>操作</span>
            <span style={{ width: "10ch" }}>代码</span>
            <span style={{ width: "8ch" }}>名称</span>
            <span style={{ width: "10ch", textAlign: "right" }}>价格</span>
            <span style={{ width: "8ch", textAlign: "right" }}>数量</span>
            <span style={{ width: "10ch", textAlign: "right" }}>盈亏</span>
            <span style={{ paddingLeft: "2ch" }}>原因 / 策略</span>
          </div>
          {ops.map((o, i) => (
            <div key={`${o.code}-${o.action}-${i}`}>
              <div style={{
                display: "flex", whiteSpace: "pre", padding: "1px 0",
                borderBottom: o.detail ? "none" : "1px solid #191a21", fontSize: 12,
              }}>
                <span style={{ color: D.comment, width: "13ch" }}>{o.display}</span>
                <span style={{ color: actionColors[o.action] || D.fg, width: "8ch", fontWeight: 700 }}>{o.action}</span>
                <span style={{ color: D.cyan, width: "10ch" }}>{o.code}</span>
                <span style={{ color: D.fg, width: "8ch" }}>{(o.name || "").slice(0, 6)}</span>
                <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
                  {o.price.toFixed(2)}
                </span>
                <span style={{ color: D.fg, width: "8ch", textAlign: "right" }}>
                  {o.quantity.toLocaleString()}
                </span>
                <span style={{
                  color: o.pnl != null ? pnlColor(o.pnl) : D.comment,
                  width: "10ch", textAlign: "right", fontWeight: o.pnl != null ? 500 : 400,
                }}>
                  {o.pnl != null ? `${o.pnl >= 0 ? "+" : ""}${o.pnl.toFixed(0)}` : "-"}
                </span>
                <span style={{ color: D.orange, paddingLeft: "2ch" }}>
                  {o.reason}
                </span>
              </div>
              {o.detail && (
                <div style={{
                  fontSize: 11, color: D.comment, paddingLeft: "23ch",
                  borderBottom: "1px solid #191a21", paddingBottom: 1,
                }}>
                  {o.detail}
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function CompletedTradesTable({ trades }: { trades: Trade[] }) {
  const [open, setOpen] = useState(true);
  if (trades.length === 0) return null;

  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);

  return (
    <div style={{ padding: "4px 0" }}>
      <div
        style={{ color: D.comment, padding: "4px 0 2px", cursor: "pointer", userSelect: "none" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span> # ──
        已完成交易 ({trades.length}笔 |{" "}
        <span style={{ color: pnlColor(totalPnl) }}>
          {totalPnl >= 0 ? "+" : ""}{numFmt(totalPnl)}
        </span>
        ) ──
      </div>
      {open && <TradesTable trades={trades} bare />}
    </div>
  );
}

function HistorySection({ data }: { data: SimData }) {
  const [open, setOpen] = useState(false); // 默认折叠

  return (
    <div style={{ padding: "8px 0 0" }}>
      <div
        style={{
          color: D.comment, padding: "4px 0 2px",
          cursor: "pointer", userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span> # ══ 历史回测
        <span style={{ fontSize: 11 }}>
          （{data.summary.total_trades}笔 | {pctFmt(data.summary.total_return)} | 夏普 {data.summary.sharpe_ratio.toFixed(2)}）
        </span>
         ══
      </div>
      {open && (
        <div style={{ padding: "4px 0" }}>
          <SummaryBar s={data.summary} />
          <EquityCurve
            data={data.daily_pnl}
            initialCapital={data.summary.initial_capital}
          />
          <TradesTable trades={data.trades} />
          <div
            style={{
              display: "flex", gap: 24,
              flexWrap: "wrap", marginTop: 8,
            }}
          >
            <AttributionPanel
              title="按策略归因"
              data={data.per_strategy}
              nameWidth="16ch"
              isStrategy
            />
            <AttributionPanel
              title="按股票归因"
              data={data.per_stock}
              nameWidth="12ch"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function BlinkCursor() {
  return (
    <div style={{ paddingTop: 16 }}>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sim</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span
        style={{
          display: "inline-block", width: 8, height: 15,
          background: D.fg, verticalAlign: "text-bottom",
          animation: "blink 1s step-end infinite",
        }}
      />
    </div>
  );
}

/* ── Page ── */

export default function SimPage() {
  const [data, setData] = useState<SimData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");
  const [lastUpdate, setLastUpdate] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/sim");
      const json = await res.json();
      if (json.error && !json.summary) {
        setFetchError(json.error);
      } else {
        setData(json);
        setFetchError("");
        setLastUpdate(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      }
    } catch (e) {
      setFetchError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 5_000);
    return () => clearInterval(iv);
  }, [fetchData]);

  const hasLive = data?.live && (data.live.n_positions > 0 || data.live.trades.length > 0);

  return (
    <div
      style={{
        background: D.bg, color: D.fg,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13, height: "100vh",
        display: "flex", flexDirection: "column",
      }}
    >
      <TitleBar />
      <NavBar data={data} />

      <div style={{ flex: 1, overflow: "auto", padding: "8px 16px 24px" }}>
        <Prompt cmd="cat sim_trading.log" />

        {/* 加载中 */}
        {loading && (
          <div style={{ color: D.comment, padding: "8px 0" }}>
            <span style={{ color: D.cyan }}>info</span> 正在加载模拟交易数据...
          </div>
        )}

        {/* 错误提示 */}
        {fetchError && (
          <div
            style={{
              color: D.red, padding: "4px 8px",
              background: "#3a1f1f", borderRadius: 4,
              margin: "4px 0", fontSize: 12,
            }}
          >
            [ERROR] {fetchError}
          </div>
        )}

        {!loading && data && (
          <>
            {/* ── 实时摘要（总盈亏/收益率/胜率一目了然） ── */}
            <LiveSummaryBar live={data.live} ts={lastUpdate} />

            {/* ── 实时持仓 ── */}
            {hasLive ? (
              <LivePanel live={data.live} />
            ) : (
              <div style={{ color: D.comment, padding: "8px 0" }}>
                <span style={{ color: D.yellow }}>info</span> 实时引擎暂无持仓。等待 L2 信号触发...
              </div>
            )}

            {/* ── 历史回测（折叠） ── */}
            {data.summary.total_trades > 0 && (
              <HistorySection data={data} />
            )}
          </>
        )}

        <BlinkCursor />
      </div>

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
