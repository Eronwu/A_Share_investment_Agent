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

const LEVEL_COLORS: Record<number, string> = {
  1: D.yellow,
  2: D.orange,
  3: D.comment,
};

const KIND_LABELS: Record<string, { label: string; color: string }> = {
  big_move: { label: "BIG_MOVE", color: D.orange },
  threshold: { label: "THRESHOLD", color: D.red },
  portfolio: { label: "PORTFOLIO", color: D.purple },
  l2_strategy: { label: "L2_SIGNAL", color: D.cyan },
};

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

  const fetchEvents = useCallback(async () => {
    try {
      const resp = await fetch("/api/metrics", { cache: "no-store" });
      const data = await resp.json();
      setEvents(data.alertEvents || []);
    } catch {
      /* */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEvents();
    const timer = setInterval(fetchEvents, 30_000);
    return () => clearInterval(timer);
  }, [fetchEvents]);

  // 按时间倒序（最新在前）
  const sorted = [...events].reverse();

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
        <Link href="/" style={{ color: D.cyan, textDecoration: "none" }}>
          ← monitor
        </Link>
        <span style={{ color: D.purple, fontWeight: 700 }}>alerts</span>
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
          {sorted.length} events today | auto-refresh 30s
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
          <span style={{ color: D.cyan }}>~/projects/monitor</span>
          <span style={{ color: D.purple }}> git:(</span>
          <span style={{ color: D.red }}>main</span>
          <span style={{ color: D.purple }}>) </span>
          <span style={{ color: D.fg }}>cat alert_events.log | sort -r</span>
        </div>

        {loading && (
          <div style={{ color: D.comment }}>Loading...</div>
        )}

        {!loading && sorted.length === 0 && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            # No alert events today. Events reset daily at 08:00.
          </div>
        )}

        {sorted.map((e, i) => {
          const kinfo = KIND_LABELS[e.kind] || { label: e.kind.toUpperCase(), color: D.comment };
          const chgColor = e.change_pct > 0 ? D.red : e.change_pct < 0 ? D.green : D.comment;

          return (
            <div
              key={`${e.ts}-${i}`}
              style={{
                display: "flex",
                whiteSpace: "pre",
                padding: "2px 0",
                borderBottom: "1px solid #191a21",
              }}
            >
              <span style={{ color: D.comment, width: "10ch" }}>[{e.time}]</span>
              <span style={{ color: LEVEL_COLORS[e.level ?? 2] || D.comment, width: "5ch", fontWeight: 500 }}>
                {`[L${e.level ?? 2}]`}
              </span>
              <span style={{ color: kinfo.color, width: "12ch", fontWeight: 500 }}>
                {kinfo.label.padEnd(11)}
              </span>
              <span style={{ color: D.cyan, width: "10ch" }}>
                {(e.symbol || "").padEnd(9)}
              </span>
              <span style={{ color: chgColor, width: "8ch", textAlign: "right" }}>
                {e.change_pct ? `${e.change_pct >= 0 ? "+" : ""}${e.change_pct.toFixed(1)}%` : ""}
              </span>
              <span style={{ color: D.fg, marginLeft: "2ch" }}>
                {e.display || e.message}
              </span>
            </div>
          );
        })}

        {/* bottom prompt */}
        <div style={{ height: 16 }} />
        <div>
          <span style={{ color: D.green }}>➜ </span>
          <span style={{ color: D.cyan }}>~/projects/monitor</span>
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
