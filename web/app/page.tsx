"use client";

import { useEffect, useState, useCallback } from "react";
import CommandPrompt from "./components/CommandPrompt";

interface Service {
  id: string;
  name: string;
  type: string;
  price: number;
  change: number;
  chgAmt: number;
  vol: number;
  amount: number;
  amp: number;
  turnover: number;
  volRatio: number;
  high: number;
  low: number;
  open: number;
  prevClose: number;
  cost: number | null;
  shares: number | null;
  pnl: number | null;
}

const INTERVAL = 60_000;

/* ── Official Dracula colors ── */
const D = {
  bg: "#282a36",
  currentLine: "#44475a",
  fg: "#f8f8f2",
  comment: "#6272a4",
  cyan: "#8be9fd",
  green: "#50fa7b",
  orange: "#ffb86c",
  pink: "#ff79c6",
  purple: "#bd93f9",
  red: "#ff5555",
  yellow: "#f1fa8c",
} as const;

function chgColor(v: number) {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

function pad(s: string, n: number, right = false): string {
  return right ? s.padStart(n) : s.padEnd(n);
}

function fmtAmt(n: number): string {
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(0) + "万";
  return n.toFixed(0);
}

/* ── macOS Title Bar ── */
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
        borderBottom: `1px solid #191a21`,
        userSelect: "none",
      }}
    >
      <div style={{ position: "absolute", left: 12, display: "flex", gap: 8 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
          <span
            key={c}
            style={{
              width: 12, height: 12, borderRadius: "50%",
              background: c, display: "inline-block",
            }}
          />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>
        ✱ monitor (node)
      </span>
    </div>
  );
}

/* ── iTerm2 Tab Bar ── */
function TabBar() {
  const tabs = [
    { label: "Python Script Error (node)", active: false },
    { label: "Claude Code (node)", active: false },
    { label: "monitor (node)", active: true },
    { label: "~ (-zsh)", active: false },
  ];
  return (
    <div
      style={{
        display: "flex",
        background: "#21222c",
        borderBottom: `1px solid #191a21`,
        fontSize: 11,
        userSelect: "none",
      }}
    >
      {tabs.map((t, i) => (
        <div
          key={i}
          style={{
            padding: "5px 16px",
            background: t.active ? D.bg : "#21222c",
            color: t.active ? D.fg : D.comment,
            borderRight: "1px solid #191a21",
            borderTop: t.active
              ? `2px solid ${D.purple}`
              : "2px solid transparent",
            display: "flex",
            alignItems: "center",
            gap: 6,
            minWidth: 130,
          }}
        >
          <span
            style={{
              fontSize: 8,
              color: t.active ? D.green : "#555",
            }}
          >
            {t.active ? "✱" : "●"}
          </span>
          <span>{t.label}</span>
          <span
            style={{ marginLeft: "auto", color: D.comment, fontSize: 10 }}
          >
            ⌘{i + 1}
          </span>
        </div>
      ))}
      <div style={{ flex: 1, background: "#21222c" }} />
      <div style={{ padding: "5px 12px", color: D.comment, background: "#21222c" }}>
        +
      </div>
    </div>
  );
}

/* ── Prompt ── */
function Prompt({ cmd }: { cmd: string }) {
  return (
    <div>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/monitor</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>{cmd}</span>
    </div>
  );
}

/* ── Main ── */
export default function Home() {
  const [services, setServices] = useState<Service[]>([]);
  const [ts, setTs] = useState(0);
  const [tick, setTick] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch("/api/metrics", { cache: "no-store" });
      const data = await resp.json();
      setServices(data.services || []);
      setTs(data.ts || Date.now());
      setTick((t) => t + 1);
    } catch {
      /* */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, INTERVAL);
    return () => clearInterval(timer);
  }, [fetchData]);

  const holdings = services
    .filter((s) => s.type === "holding")
    .sort((a, b) => b.change - a.change);
  const watching = services
    .filter((s) => s.type !== "holding")
    .sort((a, b) => b.change - a.change);
  const hasHold = holdings.length > 0;

  const now = ts
    ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })
    : "--:--:--";
  const totalAmt = services.reduce((a, s) => a + s.amount, 0);
  const avgChg =
    services.length > 0
      ? services.reduce((a, s) => a + s.change, 0) / services.length
      : 0;
  const up = services.filter((s) => s.change > 0).length;
  const dn = services.filter((s) => s.change < 0).length;
  const holdPnl = holdings
    .filter((s) => s.pnl !== null && s.cost && s.shares)
    .reduce((sum, s) => sum + (s.price - s.cost!) * s.shares!, 0);

  /* ── render row ── */
  function Row({ s, hold }: { s: Service; hold: boolean }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";
    const pnlStr =
      hold && s.pnl !== null
        ? `${s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(1)}%`
        : "";

    return (
      <div
        style={{
          display: "flex",
          whiteSpace: "pre",
          padding: "1px 0",
          borderBottom: `1px solid #191a21`,
        }}
      >
        <span style={{ color: hold ? D.orange : D.comment, width: "6ch" }}>
          {hold ? " PROD" : "  DEV"}
        </span>
        <span style={{ color: D.cyan, width: "10ch" }}>
          {pad(s.id, 9)}
        </span>
        <span style={{ color: D.fg, width: "10ch" }}>
          {pad(s.name.slice(0, 6), 8)}
        </span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        <span
          style={{
            color: chgColor(s.change),
            width: "9ch",
            textAlign: "right",
            fontWeight: 500,
          }}
        >
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        <span
          style={{ color: chgColor(s.chgAmt), width: "8ch", textAlign: "right" }}
        >
          {pad(`${csign}${s.chgAmt.toFixed(2)}`, 7, true)}
        </span>
        {hasHold && (
          <span
            style={{
              color: s.pnl !== null ? chgColor(s.pnl) : D.comment,
              width: "9ch",
              textAlign: "right",
              fontWeight: pnlStr ? 500 : 400,
            }}
          >
            {pad(pnlStr || "-", 8, true)}
          </span>
        )}
        <span
          style={{
            color:
              s.volRatio >= 1.5
                ? D.red
                : s.volRatio <= 0.5
                ? D.comment
                : D.fg,
            width: "7ch",
            textAlign: "right",
          }}
        >
          {pad(s.volRatio.toFixed(2), 6, true)}
        </span>
        <span
          style={{
            color: s.turnover >= 5 ? D.red : D.fg,
            width: "8ch",
            textAlign: "right",
          }}
        >
          {pad(s.turnover.toFixed(2), 7, true)}
        </span>
        <span style={{ color: D.comment, width: "7ch", textAlign: "right" }}>
          {pad(s.amp.toFixed(2), 6, true)}
        </span>
        <span style={{ color: D.comment, width: "11ch", textAlign: "right" }}>
          {pad(s.vol.toLocaleString(), 10, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
      }}
    >
      <TitleBar />
      <TabBar />

      {/* Terminal body */}
      <div
        style={{
          flex: 1,
          padding: "10px 16px",
          overflow: "auto",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <Prompt cmd="watch -n 60 ./svc-monitor --format table" />
        <div style={{ height: 8 }} />

        {/* watch header */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span>Every 60.0s: svc-monitor --format table</span>
          <span style={{ float: "right" }}>
            devbox: {now} &nbsp; refresh #{tick}
          </span>
        </div>

        {/* stats */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span style={{ color: D.fg }}>
            Nodes: <span style={{ color: D.purple }}>{services.length}</span>
          </span>
          {"  "}
          up:<span style={{ color: D.green }}>{up}</span>
          {" "}down:<span style={{ color: D.red }}>{dn}</span>
          {"  "}
          throughput:<span style={{ color: D.fg }}>{fmtAmt(totalAmt)}</span>
          {"  "}
          avg_delta:
          <span style={{ color: chgColor(avgChg) }}>
            {avgChg >= 0 ? "+" : ""}{avgChg.toFixed(2)}%
          </span>
          {hasHold && (
            <>
              {"  "}prod_yield:
              <span style={{ color: chgColor(holdPnl) }}>
                {holdPnl >= 0 ? "+" : ""}{holdPnl.toFixed(0)}
              </span>
            </>
          )}
        </div>

        {/* table header */}
        <div
          style={{
            display: "flex",
            whiteSpace: "pre",
            color: D.pink,
            borderBottom: `1px solid ${D.currentLine}`,
            paddingBottom: 3,
            marginBottom: 2,
            fontWeight: 500,
          }}
        >
          <span style={{ width: "6ch" }}> 类型</span>
          <span style={{ width: "10ch" }}>代码</span>
          <span style={{ width: "10ch" }}>名称</span>
          <span style={{ width: "10ch", textAlign: "right" }}>{pad("现价", 9, true)}</span>
          <span style={{ width: "9ch", textAlign: "right" }}>{pad("涨跌幅", 8, true)}</span>
          <span style={{ width: "8ch", textAlign: "right" }}>{pad("涨跌额", 7, true)}</span>
          {hasHold && (
            <span style={{ width: "9ch", textAlign: "right" }}>{pad("盈亏", 8, true)}</span>
          )}
          <span style={{ width: "7ch", textAlign: "right" }}>{pad("量比", 6, true)}</span>
          <span style={{ width: "8ch", textAlign: "right" }}>{pad("换手率", 7, true)}</span>
          <span style={{ width: "7ch", textAlign: "right" }}>{pad("振幅", 6, true)}</span>
          <span style={{ width: "11ch", textAlign: "right" }}>{pad("成交量", 10, true)}</span>
          <span style={{ width: "9ch", textAlign: "right" }}>{pad("成交额", 8, true)}</span>
        </div>

        {/* production */}
        {hasHold && (
          <div style={{ color: D.comment, padding: "3px 0 1px" }}>
            # ── production ({holdings.length}) ──
          </div>
        )}
        {holdings.map((s) => (
          <Row key={s.id} s={s} hold />
        ))}

        {/* staging */}
        {watching.length > 0 && (
          <div style={{ color: D.comment, padding: "6px 0 1px" }}>
            # ── staging ({watching.length}) ──
          </div>
        )}
        {watching.map((s) => (
          <Row key={s.id} s={s} hold={false} />
        ))}

        {/* log tail */}
        <div style={{ height: 16 }} />
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> metrics-collector: polled{" "}
          {services.length} endpoints ({(tick * 7 + 23) % 50 + 15}ms)
        </div>
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> scheduler: next poll in{" "}
          {INTERVAL / 1000}s
        </div>

        {/* interactive command prompt */}
        <div style={{ height: 10 }} />
        <CommandPrompt onRefresh={fetchData} />
      </div>

      <style>{`
        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
