"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

/* ── Types ── */

interface ComponentEntry {
  code: string;
  change_pct: number;
  close: number | null;
  name: string;
}

interface DayPoint {
  date: string;
  change: number;
  value: number;
}

interface IndexEntry {
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
  status: "mainline" | "approaching" | "watching" | "inactive";
  components: ComponentEntry[];
  history: DayPoint[];
}

interface AlertEntry {
  ts: number;
  date: string;
  index_id: string;
  index_name: string;
  alert_type: string;
  cumulative_pct: number;
  slope: number;
  message: string;
  display: string;
}

interface SectorData {
  indices: IndexEntry[];
  alerts: AlertEntry[];
  error?: string;
}

/* ── Helpers ── */

/** A-share convention: red = positive, green = negative */
function chgColor(v: number): string {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** Strip year from "YYYY-MM-DD" → "MM-DD" */
function shortDate(d: string): string {
  return d.slice(5);
}

/** epoch ts → "MM-DD HH:MM" */
function fmtAlertTime(ts: number, date: string): string {
  if (!ts || ts <= 0) return shortDate(date);
  const d = new Date(ts * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function statusStyle(status: string): { label: string; bg: string; fg: string } {
  switch (status) {
    case "mainline": return { label: "主线", bg: "#ff5555", fg: "#fff" };
    case "approaching": return { label: "接近", bg: "#ffb86c", fg: "#282a36" };
    case "watching": return { label: "关注中", bg: "#44475a", fg: "#6272a4" };
    default: return { label: "未关注", bg: "#191a21", fg: "#6272a4" };
  }
}

/* ── Reusable UI ── */

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
        ✱ sector — indices
      </span>
    </div>
  );
}

function Prompt({ cmd }: { cmd: string }) {
  return (
    <div>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sector</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>{cmd}</span>
    </div>
  );
}

function BlinkCursor() {
  return (
    <div style={{ paddingTop: 16 }}>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sector</span>
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
  );
}

/* ── Create Index Modal ── */
function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [stocks, setStocks] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleCreate() {
    const trimName = name.trim();
    const trimId = id.trim();
    const stockList = stocks
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!trimName || !trimId || stockList.length === 0) {
      setError("名称、ID、成分股均不能为空");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const resp = await fetch("/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "create",
          id: trimId,
          name: trimName,
          stocks: stockList,
        }),
      });
      const result = await resp.json();
      if (result.ok) {
        onCreated();
        onClose();
      } else {
        setError(result.error || "创建失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 13,
    padding: "6px 10px",
    outline: "none",
    borderRadius: 2,
    width: "100%",
    boxSizing: "border-box",
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.7)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: D.currentLine,
          border: `1px solid ${D.comment}`,
          borderRadius: 6,
          padding: "20px 24px",
          width: 400,
          fontFamily: "'JetBrains Mono', monospace",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ color: D.purple, fontWeight: 700, marginBottom: 16, fontSize: 14 }}>
          # ── 新建自定义指数 ──
        </div>

        <label style={{ color: D.comment, fontSize: 12, display: "block", marginBottom: 4 }}>
          名称:
        </label>
        <input
          style={{ ...inputStyle, marginBottom: 12 }}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="磷化工"
          autoFocus
        />

        <label style={{ color: D.comment, fontSize: 12, display: "block", marginBottom: 4 }}>
          ID:
        </label>
        <input
          style={{ ...inputStyle, marginBottom: 12 }}
          value={id}
          onChange={(e) => setId(e.target.value)}
          placeholder="phosphorus"
        />

        <label style={{ color: D.comment, fontSize: 12, display: "block", marginBottom: 4 }}>
          成分股 (逗号分隔):
        </label>
        <input
          style={{ ...inputStyle, marginBottom: 16 }}
          value={stocks}
          onChange={(e) => setStocks(e.target.value)}
          placeholder="000792,600096,002895"
          onKeyDown={(e) => {
            if (e.key === "Enter") handleCreate();
          }}
        />

        {error && (
          <div style={{ color: D.red, fontSize: 12, marginBottom: 10 }}>
            [ERROR] {error}
          </div>
        )}

        <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
          <button
            style={{
              background: "transparent",
              border: `1px solid ${D.comment}`,
              color: D.comment,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 13,
              padding: "5px 16px",
              borderRadius: 3,
              cursor: "pointer",
            }}
            onClick={onClose}
          >
            取消
          </button>
          <button
            style={{
              background: D.purple,
              border: "none",
              color: D.bg,
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 13,
              fontWeight: 700,
              padding: "5px 16px",
              borderRadius: 3,
              cursor: saving ? "wait" : "pointer",
              opacity: saving ? 0.6 : 1,
            }}
            onClick={handleCreate}
            disabled={saving}
          >
            创建
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Main Page ── */

export default function SectorPage() {
  const [data, setData] = useState<SectorData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // expanded index row + hover highlight
  const [expandedIndex, setExpandedIndex] = useState<string | null>(null);
  const [hoverIndex, setHoverIndex] = useState<string | null>(null);

  // section collapse
  const [indicesOpen, setIndicesOpen] = useState(true);
  const [alertsOpen, setAlertsOpen] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch("/api/sector", { cache: "no-store" });
      const json = await resp.json();
      if (json.error && !json.indices) {
        setFetchError(json.error);
      } else {
        setData(json);
        setFetchError(json.error || null);
      }
    } catch (e) {
      setFetchError(`network error: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60_000);
    return () => clearInterval(timer);
  }, [fetchData]);

  /* ── API actions ── */

  async function postAction(body: Record<string, unknown>) {
    try {
      const resp = await fetch("/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await resp.json();
      if (result.ok) {
        await fetchData();
      }
      return result;
    } catch {
      /* swallow */
      return null;
    }
  }

  async function handleToggleStar(id: string, currentStar: boolean) {
    await postAction({ action: "star", id, value: !currentStar });
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`删除指数 ${name}?`)) return;
    await postAction({ action: "delete", id });
  }

  // Escape key closes modals
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (expandedIndex) setExpandedIndex(null);
        else if (showCreateModal) setShowCreateModal(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expandedIndex, showCreateModal]);

  /* ── Render ── */

  const indices = data?.indices ?? [];
  const alerts = data?.alerts ?? [];

  return (
    <div
      style={{
        background: D.bg,
        color: D.fg,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
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
        <span style={{ color: D.comment }}>|</span>
        <Link href="/alerts" style={{ color: D.comment, textDecoration: "none" }}>alerts</Link>
        <Link href="/sim" style={{ color: D.comment, textDecoration: "none" }}>sim</Link>
        <Link href="/watching" style={{ color: D.comment, textDecoration: "none" }}>watching</Link>
        <span style={{ color: D.purple, fontWeight: 700 }}>sector</span>
        <Link href="/manage" style={{ color: D.comment, textDecoration: "none" }}>manage</Link>
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
          {indices.length} indices | {alerts.length} alerts | auto-refresh 60s
        </span>
      </div>

      {/* scrollable body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "8px 16px 24px",
          lineHeight: 1.55,
        }}
      >
        <Prompt cmd="cat sector_indices.log" />
        <div style={{ height: 8 }} />

        {/* loading */}
        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.cyan }}>info</span> Loading sector data
            <span style={{ animation: "blink 1s step-end infinite" }}>...</span>
          </div>
        )}

        {/* error banner */}
        {fetchError && (
          <div
            style={{
              color: D.red,
              padding: "4px 8px",
              background: "#3a1f1f",
              borderRadius: 4,
              margin: "4px 0",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            [ERROR] {fetchError}
          </div>
        )}

        {!loading && (
          <>
            {/* ════════════════════════════════════════════ */}
            {/* Section 1: 我的指数 (Custom Indices)         */}
            {/* ════════════════════════════════════════════ */}
            <div style={{ padding: "4px 0" }}>
              <div
                style={{
                  color: D.comment,
                  padding: "4px 0 2px",
                  cursor: "pointer",
                  userSelect: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                }}
              >
                <span onClick={() => setIndicesOpen((v) => !v)}>
                  <span style={{ color: D.purple }}>{indicesOpen ? "▾" : "▸"}</span>
                  {" "}# ── 我的指数 ({indices.length}) ──
                </span>
                <button
                  style={{
                    background: D.purple,
                    border: "none",
                    color: D.bg,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "2px 10px",
                    borderRadius: 3,
                    cursor: "pointer",
                  }}
                  onClick={() => setShowCreateModal(true)}
                >
                  + 新建
                </button>
              </div>

              {indicesOpen && (
                <>
                  {indices.length > 0 ? (() => {
                    // Collect all unique dates across indices (chronological)
                    const allDates = Array.from(
                      new Set(indices.flatMap((idx) => idx.history.map((h) => h.date))),
                    ).sort().reverse();

                    // Build lookup: indexId -> { date -> DayPoint }
                    const lookup = new Map<string, Map<string, DayPoint>>();
                    for (const idx of indices) {
                      const m = new Map<string, DayPoint>();
                      for (const h of idx.history) m.set(h.date, h);
                      lookup.set(idx.id, m);
                    }

                    const COL_W = 72;
                    const ROW_H = 48;
                    const NAME_W = 160;

                    return (
                      <div style={{ display: "flex" }}>
                        {/* fixed left: index names + summary */}
                        <div style={{ flexShrink: 0, width: NAME_W }}>
                          {/* header */}
                          <div
                            style={{
                              height: 32,
                              display: "flex",
                              alignItems: "center",
                              borderBottom: `1px solid ${D.currentLine}`,
                              color: D.pink,
                              fontWeight: 500,
                              fontSize: 12,
                              gap: 4,
                            }}
                          >
                            <span style={{ width: 16 }}></span>
                            <span>板块</span>
                            <span style={{ marginLeft: "auto", paddingRight: 8, fontSize: 11 }}>累涨</span>
                          </div>
                          {/* index rows */}
                          {indices.map((idx) => {
                            const statusInfo = statusStyle(idx.status);
                            return (
                              <div
                                key={idx.id}
                                style={{
                                  height: ROW_H,
                                  display: "flex",
                                  alignItems: "center",
                                  borderBottom: "1px solid #191a21",
                                  gap: 2,
                                  background: hoverIndex === idx.id ? "#2a2b36" : "transparent",
                                  transition: "background 0.1s",
                                }}
                                onMouseEnter={() => setHoverIndex(idx.id)}
                                onMouseLeave={() => setHoverIndex(null)}
                              >
                                {/* star */}
                                <span
                                  style={{
                                    width: 16,
                                    color: idx.star ? D.yellow : D.comment,
                                    cursor: "pointer",
                                    userSelect: "none",
                                    fontSize: 12,
                                    textAlign: "center",
                                  }}
                                  onClick={() => handleToggleStar(idx.id, idx.star)}
                                >
                                  {idx.star ? "★" : "☆"}
                                </span>
                                {/* name — click to open chart modal */}
                                <div
                                  style={{ flex: 1, minWidth: 0, overflow: "hidden", cursor: "pointer" }}
                                  onClick={() => setExpandedIndex(idx.id)}
                                >
                                  <div style={{ fontSize: 12, color: D.cyan, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                    {idx.name}
                                  </div>
                                  <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 1 }}>
                                    <span
                                      style={{
                                        display: "inline-block",
                                        padding: "0 5px",
                                        borderRadius: 2,
                                        fontSize: 10,
                                        fontWeight: 700,
                                        background: statusInfo.bg,
                                        color: statusInfo.fg,
                                        lineHeight: "16px",
                                      }}
                                    >
                                      {statusInfo.label}
                                    </span>
                                    <span style={{ fontSize: 10, color: D.comment }}>
                                      {idx.stocks.length}股
                                    </span>
                                  </div>
                                </div>
                                {/* cumGain */}
                                <span
                                  style={{
                                    fontSize: 12,
                                    fontWeight: 700,
                                    color: chgColor(idx.cumGain),
                                    paddingRight: 4,
                                    whiteSpace: "nowrap",
                                  }}
                                >
                                  {fmtPct(idx.cumGain)}
                                </span>
                                {/* delete */}
                                <span
                                  style={{
                                    color: D.comment,
                                    cursor: "pointer",
                                    fontWeight: 700,
                                    fontSize: 14,
                                    userSelect: "none",
                                    padding: "0 4px",
                                    lineHeight: 1,
                                  }}
                                  onClick={() => handleDelete(idx.id, idx.name)}
                                  title="删除"
                                >
                                  ×
                                </span>
                              </div>
                            );
                          })}
                        </div>

                        {/* scrollable right: date columns */}
                        <div style={{ overflowX: "auto", flex: 1 }}>
                          {/* date header */}
                          <div style={{ display: "flex" }}>
                            {allDates.map((d) => (
                              <div
                                key={d}
                                style={{
                                  minWidth: COL_W,
                                  height: 32,
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  borderBottom: `1px solid ${D.currentLine}`,
                                  color: D.pink,
                                  fontWeight: 500,
                                  fontSize: 11,
                                }}
                              >
                                {shortDate(d)}
                              </div>
                            ))}
                          </div>

                          {/* data rows */}
                          {indices.map((idx) => {
                            const idxLookup = lookup.get(idx.id)!;
                            return (
                              <div
                                key={idx.id}
                                style={{
                                  display: "flex",
                                  cursor: "pointer",
                                  background: hoverIndex === idx.id ? "#2a2b36" : "transparent",
                                  transition: "background 0.1s",
                                }}
                                onClick={() => setExpandedIndex(idx.id)}
                                onMouseEnter={() => setHoverIndex(idx.id)}
                                onMouseLeave={() => setHoverIndex(null)}
                              >
                                {allDates.map((d) => {
                                  const pt = idxLookup.get(d);
                                  return (
                                    <div
                                      key={d}
                                      style={{
                                        minWidth: COL_W,
                                        height: ROW_H,
                                        display: "flex",
                                        flexDirection: "column",
                                        justifyContent: "center",
                                        alignItems: "center",
                                        borderBottom: "1px solid #191a21",
                                        fontSize: 12,
                                      }}
                                    >
                                      {pt ? (
                                        <>
                                          <span style={{ color: chgColor(pt.change), fontWeight: 500 }}>
                                            {fmtPct(pt.change)}
                                          </span>
                                          <span style={{ color: D.comment, fontSize: 10 }}>
                                            {pt.value.toFixed(1)}
                                          </span>
                                        </>
                                      ) : (
                                        <span style={{ color: D.comment, fontSize: 11 }}>-</span>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })() : (
                    <div style={{ color: D.comment, padding: "8px 0" }}>
                      # 暂无自定义指数。点击 [+ 新建] 创建。
                    </div>
                  )}

                  {/* nothing inline — chart is in modal */}
                </>
              )}
            </div>

            <div style={{ height: 12 }} />

            {/* ════════════════════════════════════════════ */}
            {/* Section 2: 主线告警 (Mainline Alerts)        */}
            {/* ════════════════════════════════════════════ */}
            <div style={{ padding: "4px 0" }}>
              <div
                style={{
                  color: D.comment,
                  padding: "4px 0 2px",
                  cursor: "pointer",
                  userSelect: "none",
                }}
                onClick={() => setAlertsOpen((v) => !v)}
              >
                <span style={{ color: D.purple }}>{alertsOpen ? "▾" : "▸"}</span>
                {" "}# ── 主线告警 ({alerts.length}) ──
              </div>

              {alertsOpen && (
                <>
                  {alerts.length > 0 ? (
                    alerts.map((a, i) => {
                      const isMainline = a.alert_type === "mainline";
                      const alertColor = isMainline ? D.red : a.alert_type === "approaching" ? D.orange : D.comment;
                      const timeStr = fmtAlertTime(a.ts, a.date);

                      return (
                        <div
                          key={`${a.ts}-${i}`}
                          style={{
                            padding: "2px 0",
                            borderBottom: "1px solid #191a21",
                            fontSize: 12,
                          }}
                        >
                          <span style={{ color: D.comment }}>[{timeStr}]</span>
                          {" "}
                          <span style={{ color: alertColor, fontWeight: isMainline ? 700 : 500 }}>
                            {a.display || a.message}
                          </span>
                        </div>
                      );
                    })
                  ) : (
                    <div style={{ color: D.comment, padding: "8px 0" }}>
                      # 暂无告警记录。
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}

        <BlinkCursor />
      </div>

      {/* create modal */}
      {showCreateModal && (
        <CreateModal
          onClose={() => setShowCreateModal(false)}
          onCreated={fetchData}
        />
      )}

      {/* chart modal */}
      {expandedIndex && (() => {
        const idx = indices.find((i) => i.id === expandedIndex);
        if (!idx || idx.history.length < 2) return null;

        const W = 680, H = 200, PAD_T = 20, PAD_B = 26, PAD_L = 44, PAD_R = 12;
        const pts = [...idx.history].reverse();
        const vals = pts.map((p) => p.value);
        const minV = Math.min(...vals);
        const maxV = Math.max(...vals);
        const range = maxV - minV || 1;

        const xStep = (W - PAD_L - PAD_R) / (pts.length - 1);
        const yScale = (v: number) =>
          PAD_T + (H - PAD_T - PAD_B) * (1 - (v - minV) / range);

        const line = pts
          .map((p, i) => `${PAD_L + i * xStep},${yScale(p.value)}`)
          .join(" ");
        const area =
          `${PAD_L},${yScale(minV)} ` +
          pts.map((p, i) => `${PAD_L + i * xStep},${yScale(p.value)}`).join(" ") +
          ` ${PAD_L + (pts.length - 1) * xStep},${yScale(minV)}`;

        const lastVal = vals[vals.length - 1];
        const firstVal = vals[0];
        const trendUp = lastVal >= firstVal;
        const lineColor = trendUp ? D.red : D.green;
        const fillColor = trendUp ? "rgba(255,85,85,0.12)" : "rgba(80,250,123,0.12)";

        const yTicks = Array.from({ length: 5 }, (_, i) => minV + (range * i) / 4);
        const xLabelStep = Math.max(1, Math.floor(pts.length / 8));
        const xLabels = pts.filter((_, i) => i % xLabelStep === 0 || i === pts.length - 1);

        const baseY = yScale(100);
        const baseInRange = 100 >= minV && 100 <= maxV;

        return (
          <div
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.7)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 100,
            }}
            onClick={() => setExpandedIndex(null)}
          >
            <div
              style={{
                background: D.bg,
                border: `1px solid ${D.comment}`,
                borderRadius: 8,
                padding: "16px 20px",
                width: 720,
                maxHeight: "80vh",
                overflowY: "auto",
                fontFamily: "'JetBrains Mono', monospace",
              }}
              onClick={(e) => e.stopPropagation()}
            >
              {/* header */}
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
                <span style={{ color: D.fg, fontWeight: 700, fontSize: 15 }}>
                  {idx.name}
                </span>
                <span style={{ color: D.comment, fontSize: 12 }}>
                  指数值 {lastVal.toFixed(1)}
                </span>
                <span style={{ color: chgColor(idx.cumGain), fontSize: 13, fontWeight: 600 }}>
                  {fmtPct(idx.cumGain)}
                </span>
                <span style={{ color: D.comment, fontSize: 11 }}>
                  {pts.length}日
                </span>
                {(() => {
                  const st = statusStyle(idx.status);
                  return (
                    <span style={{
                      padding: "1px 8px", borderRadius: 3, fontSize: 11,
                      fontWeight: 700, background: st.bg, color: st.fg,
                    }}>
                      {st.label}
                    </span>
                  );
                })()}
                <span
                  style={{
                    marginLeft: "auto",
                    color: D.comment,
                    cursor: "pointer",
                    fontSize: 18,
                    fontWeight: 700,
                    lineHeight: 1,
                  }}
                  onClick={() => setExpandedIndex(null)}
                >
                  ×
                </span>
              </div>

              {/* SVG chart */}
              <svg
                width={W}
                height={H}
                style={{ display: "block", maxWidth: "100%" }}
                viewBox={`0 0 ${W} ${H}`}
              >
                {yTicks.map((v, i) => (
                  <g key={i}>
                    <line
                      x1={PAD_L} y1={yScale(v)}
                      x2={W - PAD_R} y2={yScale(v)}
                      stroke="#333" strokeDasharray="2,3"
                    />
                    <text
                      x={PAD_L - 4} y={yScale(v) + 3}
                      fill={D.comment} fontSize={9} textAnchor="end"
                    >
                      {v.toFixed(1)}
                    </text>
                  </g>
                ))}

                {baseInRange && (
                  <line
                    x1={PAD_L} y1={baseY}
                    x2={W - PAD_R} y2={baseY}
                    stroke={D.yellow} strokeDasharray="4,3" strokeWidth={0.8} opacity={0.5}
                  />
                )}

                <polygon points={area} fill={fillColor} />
                <polyline points={line} fill="none" stroke={lineColor} strokeWidth={1.8} />
                <circle
                  cx={PAD_L + (pts.length - 1) * xStep}
                  cy={yScale(lastVal)}
                  r={3.5}
                  fill={lineColor}
                />

                {xLabels.map((p) => {
                  const i = pts.indexOf(p);
                  return (
                    <text
                      key={p.date}
                      x={PAD_L + i * xStep}
                      y={H - 4}
                      fill={D.comment}
                      fontSize={9}
                      textAnchor="middle"
                    >
                      {shortDate(p.date)}
                    </text>
                  );
                })}
              </svg>

              {/* component stocks table */}
              <div style={{ marginTop: 12, borderTop: `1px solid ${D.currentLine}`, paddingTop: 8 }}>
                <div style={{ color: D.comment, fontSize: 11, marginBottom: 6 }}>
                  成分股 ({idx.stocks.length}):
                </div>
                {idx.components.length > 0 ? (
                  <table
                    style={{
                      width: "100%",
                      borderCollapse: "collapse",
                      fontSize: 12,
                      fontFamily: "'JetBrains Mono', monospace",
                    }}
                  >
                    <thead>
                      <tr
                        style={{
                          borderBottom: `1px solid ${D.currentLine}`,
                          color: D.comment,
                          fontSize: 11,
                        }}
                      >
                        <th style={{ textAlign: "left", padding: "4px 8px 4px 0", fontWeight: 500 }}>代码</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 500 }}>名称</th>
                        <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 500 }}>最新价</th>
                        <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 500 }}>涨跌幅</th>
                        <th style={{ textAlign: "right", padding: "4px 0 4px 8px", fontWeight: 500 }}>涨跌额</th>
                      </tr>
                    </thead>
                    <tbody>
                      {idx.components.map((c) => {
                        const chgAmt =
                          c.close != null && c.change_pct !== 0
                            ? c.close * c.change_pct / (100 + c.change_pct)
                            : null;
                        return (
                          <tr
                            key={c.code}
                            style={{ borderBottom: `1px solid #191a21` }}
                          >
                            <td style={{ padding: "5px 8px 5px 0", color: D.cyan }}>
                              {c.code}
                            </td>
                            <td
                              style={{
                                padding: "5px 8px",
                                color: c.name ? D.fg : D.comment,
                                maxWidth: 120,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {c.name || "-"}
                            </td>
                            <td style={{ padding: "5px 8px", textAlign: "right", color: D.fg }}>
                              {c.close != null ? c.close.toFixed(2) : "-"}
                            </td>
                            <td
                              style={{
                                padding: "5px 8px",
                                textAlign: "right",
                                color: chgColor(c.change_pct),
                                fontWeight: 600,
                              }}
                            >
                              {fmtPct(c.change_pct)}
                            </td>
                            <td
                              style={{
                                padding: "5px 0 5px 8px",
                                textAlign: "right",
                                color: chgAmt != null ? chgColor(chgAmt) : D.comment,
                              }}
                            >
                              {chgAmt != null
                                ? `${chgAmt >= 0 ? "+" : ""}${chgAmt.toFixed(2)}`
                                : "-"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div style={{ color: D.comment, fontSize: 11 }}>
                    暂无今日成分股涨跌数据
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
