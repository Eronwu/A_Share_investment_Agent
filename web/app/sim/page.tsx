"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

/* ── Types ── */

interface Trade {
  trade_id: string;
  code: string;
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

interface SimData {
  summary: Summary;
  trades: Trade[];
  daily_pnl: DailyPnl[];
  per_strategy: Record<string, Attribution>;
  per_stock: Record<string, Attribution>;
  positions: Record<string, Record<string, PositionSnap>>;
  error?: string;
}

/* ── Helpers ── */

const pnlColor = (v: number) => (v > 0 ? D.red : v < 0 ? D.green : D.comment);
const pctFmt = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
const numFmt = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });

/** 退出原因翻译 */
function exitReasonCN(r: string): string {
  if (r.startsWith("stop_loss")) return "止损 " + r.replace("stop_loss", "").replace(/[()]/g, "");
  if (r.startsWith("take_profit")) return "止盈 " + r.replace("take_profit", "").replace(/[()]/g, "");
  if (r.startsWith("max_hold")) return "到期平仓";
  if (r === "force_close_eod") return "强制平仓";
  if (r.includes("composite_bearish")) return "空头信号";
  if (r.includes("composite_bullish")) return "多头信号";
  if (r.includes("momentum_sell")) return "动量卖出";
  if (r.includes("large_order_reversal")) return "大单翻转";
  if (r.includes("sell_next_open")) return "次日开盘卖";
  return r;
}

/** 策略名翻译 */
function strategyCN(s: string): string {
  const map: Record<string, string> = {
    composite_bullish: "多头综合",
    composite_bearish: "空头综合",
    momentum_alert: "动量确认",
    momentum_sell_alert: "动量卖出",
    volume_accel_alert: "放量加速",
    volume_accel_sell_alert: "放量砸盘",
    macd_golden_cross: "MACD金叉",
    macd_death_cross: "MACD死叉",
    ma_bullish_align: "均线多头排列",
    ma_bearish_align: "均线空头排列",
    volume_breakout: "放量突破",
    breakout_pullback: "突破回踩",
    force_close: "强制平仓",
    "T3:large_order_reversal": "T3:大单翻转",
    "T3:volume_price_divergence": "T3:量价背离",
    "T3:macd_top_divergence": "T3:MACD顶背离",
  };
  return map[s] || s;
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
        模拟盘 — 回测报告
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
        ← 监控台
      </Link>
      <Link href="/alerts" style={{ color: D.comment, textDecoration: "none" }}>
        告警
      </Link>
      <span style={{ color: D.purple, fontWeight: 700 }}>模拟盘</span>
      {data?.summary && (
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
          {data.summary.total_trades} 笔交易 | {data.summary.trading_days} 交易日 | v1_baseline
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

function TradesTable({ trades }: { trades: Trade[] }) {
  const [open, setOpen] = useState(true);

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
      {open && (
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
            <span style={{ width: "9ch", textAlign: "right" }}>买入</span>
            <span style={{ width: "3ch", textAlign: "center" }}>→</span>
            <span style={{ width: "9ch", textAlign: "right" }}>卖出</span>
            <span style={{ width: "8ch", textAlign: "right" }}>数量</span>
            <span style={{ width: "11ch", textAlign: "right" }}>盈亏</span>
            <span style={{ width: "8ch", textAlign: "right" }}>盈亏%</span>
            <span style={{ width: "5ch", textAlign: "right" }}>天数</span>
            <span style={{ width: "16ch", paddingLeft: "2ch" }}>退出原因</span>
            <span style={{ width: "14ch" }}>入场策略</span>
          </div>
          {/* 行 */}
          {trades.map((t) => {
            const c = pnlColor(t.pnl);
            return (
              <div
                key={t.trade_id}
                style={{
                  display: "flex", whiteSpace: "pre", padding: "1px 0",
                  borderBottom: "1px solid #191a21", fontSize: 12,
                }}
              >
                <span style={{ color: D.cyan, width: "10ch" }}>{t.code}</span>
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
                <span style={{ color: c, width: "11ch", textAlign: "right", fontWeight: 500 }}>
                  {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(0)}
                </span>
                <span style={{ color: c, width: "8ch", textAlign: "right", fontWeight: 500 }}>
                  {t.pnl_pct * 100 >= 0 ? "+" : ""}{(t.pnl_pct * 100).toFixed(1)}%
                </span>
                <span style={{ color: D.comment, width: "5ch", textAlign: "right" }}>
                  {t.hold_days}天
                </span>
                <span style={{ color: D.orange, width: "16ch", paddingLeft: "2ch" }}>
                  {exitReasonCN(t.exit_reason)}
                </span>
                <span style={{ color: D.comment, width: "14ch" }}>
                  {strategyCN(t.notes)}
                </span>
              </div>
            );
          })}
        </>
      )}
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

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/sim");
      const json = await res.json();
      if (json.error && !json.summary) {
        setFetchError(json.error);
      } else {
        setData(json);
        setFetchError("");
      }
    } catch (e) {
      setFetchError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 30_000);
    return () => clearInterval(iv);
  }, [fetchData]);

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
        <Prompt cmd="cat sim_report.log" />

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

        {/* 空状态 */}
        {!loading && data && data.summary.total_trades === 0 && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.yellow }}>warn</span> 暂无交易记录。请先运行{" "}
            <span style={{ color: D.green }}>
              poetry run python -m src.sim_trading.replay_runner
            </span>{" "}
            生成回测数据。
          </div>
        )}

        {/* 主体内容 */}
        {data && data.summary.total_trades > 0 && (
          <>
            <SummaryBar s={data.summary} />
            <EquityCurve
              data={data.daily_pnl}
              initialCapital={data.summary.initial_capital}
            />
            {data.positions && Object.keys(data.positions).length > 0 && (
              <PositionsTable positions={data.positions} />
            )}
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
          </>
        )}

        <BlinkCursor />
      </div>

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
