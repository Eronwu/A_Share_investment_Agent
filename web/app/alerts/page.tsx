"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  level?: number;
  message: string;
  display: string;
  change_pct: number;
}

interface DailySummary {
  date: string;
  generatedAt: number;
  market: string;
  stats: {
    totalSignals: number;
    totalAlerts: number;
    l1Count: number;
    bullish: number;
    bearish: number;
    stockCount: number;
    upCount: number;
    downCount: number;
  };
  perStock: {
    code: string;
    name: string;
    change: number;
    signalCount: number;
    direction: string;
    keySignals: string[];
  }[];
  report: string;
}

const LEVEL_COLORS: Record<number, string> = {
  1: D.yellow,
  2: D.orange,
  3: D.comment,
};

const KIND_LABELS: Record<string, { label: string; color: string }> = {
  big_move: { label: "大幅异动", color: D.orange },
  threshold: { label: "触价告警", color: D.red },
  portfolio: { label: "组合变动", color: D.purple },
  l2_strategy: { label: "L2信号", color: D.cyan },
};

/** Parse display text into structured fields for cleaner rendering */
function parseAlert(e: AlertEvent): {
  stockName: string;  // "海天味业"
  stockCode: string;  // "03288"
  signal: string;     // actual signal name: "均线多排", "MACD底背离"
  signalColor: string;
  price: string;      // "33.84"
  detail: string;     // remaining detail stripped of redundancy
} {
  const d = e.display || "";
  const sym = e.symbol || "";
  // Strip HK prefix for compact display
  const shortCode = sym.replace(/^HK/, "");

  if (e.kind === "l2_strategy") {
    // L2 format: "{code} {name} {signal}: {detail} | 现价{price} 日涨{chg}% ..."
    // or: "{code} {name} {signal} | 现价{price} ..."
    // Split on " | 现价" first to isolate price
    const priceSplit = d.split(/\s*\|\s*现价/);
    const mainPart = priceSplit[0] || "";
    const priceTail = priceSplit[1] || "";
    const priceMatch = priceTail.match(/^([\d.]+)/);
    const price = priceMatch?.[1] || "";
    // Extract trailing info after price (净流入/净流出)
    const tail = priceTail.replace(/^[\d.]+\s*/, "").replace(/^日[涨跌][+-]?[\d.]+%\s*/, "").trim();

    // Parse main part: "{code} {name} {signal}: {detail}" or "{code} {name} {signal}"
    // Name can contain full-width chars (－Ｗ), digits, latin — match greedily up to known signal keywords
    const signalKeywords = "MACD|RSI|均线|布林|ADX|趋势|放量|缩量|突破|破位|吞没|看涨|看跌|星|盘口|委比|量价|相对|散户|机构|主力|主买|主卖|大单|动量|资金|尾盘|多头|空头";
    const mainMatch = mainPart.match(new RegExp(`^(?:HK)?\\d+\\s+(.+?)\\s+((?:${signalKeywords})[^:：]*)(?:[:：]\\s*(.*))?$`));
    if (mainMatch) {
      const name = mainMatch[1].trim();
      const signal = mainMatch[2].trim();
      const detailPart = mainMatch[3]?.trim() || "";
      const detail = [detailPart, tail].filter(Boolean).join(" ");
      return {
        stockName: name,
        stockCode: shortCode,
        signal,
        signalColor: getSignalColor(signal),
        price,
        detail,
      };
    }
  }

  if (e.kind === "big_move") {
    // big_move: "{code} {name} 涨幅/跌幅 X.X% 现价X.XX"
    const m = d.match(/^(?:HK)?\d+\s+(.+?)\s+(涨幅|跌幅)\s+([\d.]+)%(?:\s+现价([\d.]+))?/);
    if (m) {
      return {
        stockName: m[1].trim(),
        stockCode: shortCode,
        signal: m[2] === "涨幅" ? "大涨" : "大跌",
        signalColor: m[2] === "涨幅" ? D.red : D.green,
        price: m[4] || "",
        detail: `${m[2]}${m[3]}%`,
      };
    }
  }

  if (e.kind === "threshold") {
    const m = d.match(/^(?:HK)?\d+\s+(.+?)\s+触价告警\s*(.*)/);
    if (m) {
      return {
        stockName: m[1].trim(),
        stockCode: shortCode,
        signal: "触价",
        signalColor: D.red,
        price: m[2]?.replace(/[! ]/g, "") || "",
        detail: "触及阈值",
      };
    }
  }

  if (e.kind === "portfolio") {
    return { stockName: "组合", stockCode: "", signal: "组合P&L", signalColor: D.purple, price: "", detail: d };
  }

  // Fallback
  return { stockName: "", stockCode: shortCode, signal: e.kind, signalColor: D.comment, price: "", detail: d };
}

/** Color for signal names */
function getSignalColor(signal: string): string {
  // Bearish signals → red/orange
  if (/空头|死叉|顶背离|破位|大跌|主卖|空排/.test(signal)) return "#ff6b6b";
  // Bullish signals → green
  if (/多头|金叉|底背离|多排|大涨|突破回踩/.test(signal)) return D.green;
  // Divergence/reversal → yellow (warning)
  if (/背离|翻转|失衡|超买|超卖/.test(signal)) return D.yellow;
  // Neutral/info
  if (/盘口|相对|趋势|RSI|布林|吞没/.test(signal)) return D.cyan;
  return D.orange;
}

/** Strip **bold** markers from heading text (headings are already styled bold) */
function stripBold(s: string): string {
  return s.replace(/\*\*([^*]+)\*\*/g, "$1");
}

/** Render markdown report in terminal style */
function TerminalMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        const trimmed = line.trimStart();
        // H3 (must check before H2 — "###" starts with "##")
        if (trimmed.startsWith("### ")) {
          return (
            <div key={i} style={{ color: D.cyan, fontWeight: 600, marginTop: 6, marginBottom: 2 }}>
              {stripBold(trimmed.slice(4))}
            </div>
          );
        }
        // H2
        if (trimmed.startsWith("## ")) {
          return (
            <div key={i} style={{ color: D.purple, fontWeight: 700, marginTop: 8, marginBottom: 2 }}>
              {stripBold(trimmed.slice(3))}
            </div>
          );
        }
        // H1
        if (trimmed.startsWith("# ")) {
          return (
            <div key={i} style={{ color: D.yellow, fontWeight: 700, marginTop: i > 0 ? 8 : 0, marginBottom: 4 }}>
              {stripBold(trimmed.slice(2))}
            </div>
          );
        }
        // Bullet
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          const content = trimmed.slice(2);
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  - </span>
              <MarkdownInline text={content} />
            </div>
          );
        }
        // Numbered list
        const numMatch = trimmed.match(/^(\d+)\.\s(.+)/);
        if (numMatch) {
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  {numMatch[1]}. </span>
              <MarkdownInline text={numMatch[2]} />
            </div>
          );
        }
        // Empty line
        if (!trimmed) {
          return <div key={i} style={{ height: 6 }} />;
        }
        // Regular text
        return (
          <div key={i} style={{ color: D.fg }}>
            <MarkdownInline text={trimmed} />
          </div>
        );
      })}
    </>
  );
}

/** Render inline markdown: **bold** */
function MarkdownInline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <span key={i} style={{ color: D.orange, fontWeight: 600 }}>
              {part.slice(2, -2)}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

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
              width: 12, height: 12, borderRadius: "50%",
              background: c, display: "inline-block",
            }}
          />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>
        ✱ alerts — event log
      </span>
    </div>
  );
}

export default function AlertsPage() {
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [summary, setSummary] = useState<DailySummary | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(true);
  const [showL3, setShowL3] = useState(false);

  const fetchData = useCallback(async () => {
    // Fetch events and summary in parallel
    const [eventsRes, summaryRes] = await Promise.allSettled([
      fetch("/api/metrics", { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/summary", { cache: "no-store" }).then((r) => r.json()),
    ]);

    // Handle events
    if (eventsRes.status === "fulfilled") {
      const data = eventsRes.value;
      if (data.error) {
        setFetchError(data.error);
      } else {
        setFetchError(null);
        setEvents(data.alertEvents || []);
      }
    } else {
      setFetchError(`network error: ${eventsRes.reason}`);
    }

    // Handle summary — only show today's report
    if (summaryRes.status === "fulfilled") {
      const sData = summaryRes.value;
      const s = sData.data;
      const today = new Date().toISOString().slice(0, 10);
      setSummary(s && s.date === today ? s : null);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 30_000);
    return () => clearInterval(timer);
  }, [fetchData]);

  // Level counts
  const l1Count = events.filter((e) => (e.level ?? 2) === 1).length;
  const l2Count = events.filter((e) => (e.level ?? 2) === 2).length;
  const l3Count = events.filter((e) => (e.level ?? 2) === 3).length;

  // 按时间倒序（最新在前），默认隐藏 L3
  const filtered = showL3 ? events : events.filter((e) => (e.level ?? 2) <= 2);
  const sorted = [...filtered].reverse();

  const st = summary?.stats;

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
        fontFamily: '"JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
        color: D.fg,
        fontSize: 13,
      }}
    >
      <TitleBar />

      {/* nav bar */}
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
        <a href="/" style={{ color: D.cyan, textDecoration: "none" }}>
          ← monitor
        </a>
        <span style={{ color: D.comment }}>|</span>
        <span style={{ color: D.purple, fontWeight: 700 }}>alerts</span>
        <a href="/sim" style={{ color: D.comment, textDecoration: "none" }}>sim</a>
        <a href="/sector" style={{ color: D.comment, textDecoration: "none" }}>sector</a>
        <a href="/manage" style={{ color: D.comment, textDecoration: "none" }}>manage</a>
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: D.fg }}>{new Date().toISOString().slice(0, 10)}</span>
          <span style={{ color: D.comment }}>|</span>
          <span style={{ color: D.yellow }}>L1:{l1Count}</span>
          <span style={{ color: D.orange }}>L2:{l2Count}</span>
          <span
            onClick={() => setShowL3(!showL3)}
            style={{
              color: showL3 ? D.comment : D.comment,
              cursor: "pointer",
              textDecoration: "underline",
              textDecorationStyle: "dotted" as const,
            }}
            title={showL3 ? "Hide L3 signals" : "Show L3 signals"}
          >
            L3:{l3Count} {showL3 ? "(shown)" : "(hidden)"}
          </span>
          <span style={{ color: D.comment }}>|</span>
          <span>{sorted.length} visible | 30s</span>
        </span>
      </div>

      {/* event list */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "10px 16px",
          lineHeight: 1.6,
        }}
      >
        {/* prompt */}
        <div style={{ marginBottom: 8 }}>
          <span style={{ color: D.green }}>➜ </span>
          <span style={{ color: D.cyan }}>~/projects/alerts</span>
          <span style={{ color: D.purple }}> git:(</span>
          <span style={{ color: D.red }}>main</span>
          <span style={{ color: D.purple }}>) </span>
          <span style={{ color: D.fg }}>cat alert_events.log | sort -r</span>
        </div>

        {/* ── Daily Summary Card ── */}
        {summary && (
          <div
            style={{
              border: `1px solid ${D.purple}44`,
              borderRadius: 4,
              marginBottom: 12,
              background: "#21222c",
            }}
          >
            {/* Header row — always visible */}
            <div
              onClick={() => setSummaryOpen(!summaryOpen)}
              style={{
                padding: "6px 12px",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 8,
                userSelect: "none",
                borderBottom: summaryOpen ? `1px solid ${D.purple}33` : "none",
              }}
            >
              <span style={{ color: D.purple, fontSize: 11, width: "2ch" }}>
                {summaryOpen ? "\u25be" : "\u25b8"}
              </span>
              <span style={{ color: D.purple, fontWeight: 700 }}>
                # ── 信号日报 {summary.date}
              </span>
              {st && (
                <span style={{ color: D.comment, fontSize: 11 }}>
                  ({st.totalSignals} signals)
                </span>
              )}
              {/* Stats chips */}
              {st && (
                <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
                  L1:{st.l1Count}
                  {" | "}
                  <span style={{ color: D.green }}>bull:{st.bullish}</span>
                  {" "}
                  <span style={{ color: D.red }}>bear:{st.bearish}</span>
                  {" | "}
                  <span style={{ color: D.green }}>up:{st.upCount}</span>
                  {" "}
                  <span style={{ color: D.red }}>down:{st.downCount}</span>
                </span>
              )}
            </div>

            {/* Collapsible report content */}
            {summaryOpen && (
              <div style={{ padding: "8px 12px 12px", lineHeight: 1.7 }}>
                <TerminalMarkdown text={summary.report} />
              </div>
            )}
          </div>
        )}

        {fetchError && (
          <div style={{ color: D.red, marginBottom: 8, fontWeight: 500 }}>
            [ERROR] alert events fetch failed: {fetchError}
          </div>
        )}

        {loading && (
          <div style={{ color: D.comment }}>Loading...</div>
        )}

        {!loading && !fetchError && sorted.length === 0 && !summary && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            # No alert events today. Events reset daily at 08:00.
          </div>
        )}

        {sorted.map((e, i) => {
          const parsed = parseAlert(e);
          const chgColor = e.change_pct > 0 ? D.red : e.change_pct < 0 ? D.green : D.comment;
          const isHighPriority = (e.level ?? 2) <= 1;

          return (
            <div
              key={`${e.ts}-${i}`}
              style={{
                display: "flex",
                alignItems: "baseline",
                padding: "3px 6px",
                borderBottom: "1px solid #191a21",
                background: isHighPriority ? "#44475a" : "transparent",
                borderLeft: isHighPriority ? `3px solid ${D.yellow}` : "3px solid transparent",
              }}
            >
              {/* Time */}
              <span style={{ color: D.comment, flexShrink: 0, width: 72 }}>{e.time}</span>
              {/* Level */}
              <span style={{ color: LEVEL_COLORS[e.level ?? 2] || D.comment, flexShrink: 0, width: 28, fontWeight: isHighPriority ? 700 : 500 }}>
                {`L${e.level ?? 2}`}
              </span>
              {/* Signal name (actual strategy) */}
              <span style={{ color: parsed.signalColor, flexShrink: 0, width: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: isHighPriority ? 700 : 500 }}>
                {parsed.signal}
              </span>
              {/* Stock code */}
              <span style={{ color: D.cyan, flexShrink: 0, width: 72, overflow: "hidden", whiteSpace: "nowrap" }}>
                {parsed.stockCode}
              </span>
              {/* Price */}
              <span style={{ color: D.fg, flexShrink: 0, width: 60, textAlign: "right" }}>
                {parsed.price}
              </span>
              {/* Change% */}
              <span style={{ color: chgColor, flexShrink: 0, width: 52, textAlign: "right" }}>
                {e.change_pct ? `${e.change_pct >= 0 ? "+" : ""}${e.change_pct.toFixed(1)}%` : ""}
              </span>
              {/* Name + Detail */}
              <span style={{ color: isHighPriority ? D.yellow : D.comment, marginLeft: 10, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {parsed.stockName ? <span style={{ color: D.fg }}>{parsed.stockName} </span> : null}{parsed.detail}
              </span>
            </div>
          );
        })}

        {/* bottom prompt */}
        <div style={{ height: 16 }} />
        <div>
          <span style={{ color: D.green }}>➜ </span>
          <span style={{ color: D.cyan }}>~/projects/alerts</span>
          <span style={{ color: D.purple }}> git:(</span>
          <span style={{ color: D.red }}>main</span>
          <span style={{ color: D.purple }}>) </span>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 15,
              background: D.fg,
              verticalAlign: "text-bottom",
              animation: "blink 1s step-end infinite",
            }}
          />
        </div>
      </div>

      <style>{`
        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
