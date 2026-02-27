"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

/* ── Types ── */

interface RotationCell {
  board: string;
  change: number;
}

interface RotationRow {
  rank: number;
  cells: RotationCell[];
}

interface ComponentEntry {
  code: string;
  change: number;
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

interface BoardStock {
  code: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  volume: number;
  amount: number;
}

interface BoardDetail {
  name: string;
  top10Count: number;
  rankHistory: Array<{ date: string; rank: number }>;
  stocks: BoardStock[];
}

interface SectorData {
  rotation: {
    dates: string[];
    rows: RotationRow[];
  };
  boardDetail: BoardDetail | null;
  indices: IndexEntry[];
  alerts: AlertEntry[];
  config: {
    alertRules: Record<string, number>;
    rotation: { category?: string; sort?: string; top_n?: number };
  };
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

  // expanded index row
  const [expandedIndex, setExpandedIndex] = useState<string | null>(null);

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
        <Link href="/manage" style={{ color: D.comment, textDecoration: "none" }}>manage</Link>
        <span style={{ color: D.purple, fontWeight: 700 }}>sector</span>
        <Link href="/sector/rotation" style={{ color: D.comment, textDecoration: "none", fontSize: 11 }}>rotation</Link>
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
                  {indices.length > 0 ? (
                    <>
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
                          fontSize: 12,
                        }}
                      >
                        <span style={{ width: "4ch", textAlign: "right" }}>#</span>
                        <span style={{ width: "14ch", paddingLeft: 8 }}>板块</span>
                        <span style={{ width: "10ch", textAlign: "right" }}>今日</span>
                        <span style={{ width: "10ch", textAlign: "right" }}>3日</span>
                        <span style={{ width: "10ch", textAlign: "right" }}>5日</span>
                        <span style={{ width: "10ch", textAlign: "right" }}>10日</span>
                        <span style={{ width: "10ch", textAlign: "right" }}>累涨</span>
                        <span style={{ width: "10ch", textAlign: "center" }}>状态</span>
                        <span style={{ width: "4ch" }}></span>
                      </div>

                      {indices.map((idx, i) => {
                        const statusInfo = statusStyle(idx.status);
                        const isExpanded = expandedIndex === idx.id;

                        return (
                          <div key={idx.id}>
                            <div
                              style={{
                                display: "flex",
                                whiteSpace: "pre",
                                padding: "3px 0",
                                borderBottom: isExpanded ? "none" : "1px solid #191a21",
                                cursor: "pointer",
                                alignItems: "center",
                              }}
                              onClick={() =>
                                setExpandedIndex(isExpanded ? null : idx.id)
                              }
                            >
                              <span style={{ width: "4ch", textAlign: "right", color: D.comment }}>
                                {i + 1}
                              </span>
                              <span style={{ width: "14ch", paddingLeft: 8 }}>
                                {/* star toggle */}
                                <span
                                  style={{
                                    color: idx.star ? D.yellow : D.comment,
                                    cursor: "pointer",
                                    userSelect: "none",
                                  }}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleToggleStar(idx.id, idx.star);
                                  }}
                                  title={idx.star ? "取消关注" : "标记关注"}
                                >
                                  {idx.star ? "★" : "☆"}
                                </span>
                                <span style={{ color: D.fg }}>{idx.name}</span>
                              </span>
                              <span style={{ width: "10ch", textAlign: "right", color: chgColor(idx.today), fontWeight: 500 }}>
                                {fmtPct(idx.today)}
                              </span>
                              <span style={{ width: "10ch", textAlign: "right", color: chgColor(idx.d3) }}>
                                {fmtPct(idx.d3)}
                              </span>
                              <span style={{ width: "10ch", textAlign: "right", color: chgColor(idx.d5) }}>
                                {fmtPct(idx.d5)}
                              </span>
                              <span style={{ width: "10ch", textAlign: "right", color: chgColor(idx.d10) }}>
                                {fmtPct(idx.d10)}
                              </span>
                              <span style={{ width: "10ch", textAlign: "right", color: chgColor(idx.cumGain), fontWeight: 700 }}>
                                {fmtPct(idx.cumGain)}
                              </span>
                              <span style={{ width: "10ch", textAlign: "center" }}>
                                <span
                                  style={{
                                    display: "inline-block",
                                    padding: "1px 8px",
                                    borderRadius: 3,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    background: statusInfo.bg,
                                    color: statusInfo.fg,
                                  }}
                                >
                                  {statusInfo.label}
                                </span>
                              </span>
                              <span style={{ width: "4ch", textAlign: "center" }}>
                                <span
                                  style={{
                                    color: D.red,
                                    cursor: "pointer",
                                    fontWeight: 700,
                                    fontSize: 15,
                                    userSelect: "none",
                                  }}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleDelete(idx.id, idx.name);
                                  }}
                                  title="删除"
                                >
                                  ×
                                </span>
                              </span>
                            </div>

                            {/* expanded component stocks */}
                            {isExpanded && (
                              <div
                                style={{
                                  background: D.currentLine,
                                  borderRadius: 4,
                                  padding: "6px 12px",
                                  marginBottom: 4,
                                  borderBottom: "1px solid #191a21",
                                }}
                              >
                                <div style={{ color: D.comment, fontSize: 11, marginBottom: 4 }}>
                                  成分股 ({idx.stocks.length}):
                                  <span style={{ color: D.comment, marginLeft: 8 }}>
                                    {idx.stocks.join(", ")}
                                  </span>
                                </div>
                                {idx.components.length > 0 ? (
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                    {idx.components.map((c) => (
                                      <span
                                        key={c.code}
                                        style={{
                                          display: "inline-block",
                                          padding: "2px 8px",
                                          borderRadius: 3,
                                          fontSize: 11,
                                          background: "#191a21",
                                          color: D.fg,
                                        }}
                                      >
                                        <span style={{ color: D.cyan }}>{c.code}</span>
                                        {" "}
                                        <span style={{ color: chgColor(c.change), fontWeight: 500 }}>
                                          {fmtPct(c.change)}
                                        </span>
                                      </span>
                                    ))}
                                  </div>
                                ) : (
                                  <div style={{ color: D.comment, fontSize: 11 }}>
                                    暂无今日成分股涨跌数据
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </>
                  ) : (
                    <div style={{ color: D.comment, padding: "8px 0" }}>
                      # 暂无自定义指数。点击 [+ 新建] 创建。
                    </div>
                  )}
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

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
