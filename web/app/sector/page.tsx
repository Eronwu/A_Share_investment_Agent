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
        ✱ sector — rotation
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

/* ── Rank badge ── */
function RankBadge({ rank }: { rank: number }) {
  let bg: string = "transparent";
  let fg: string = D.comment;
  if (rank === 1) { bg = D.red; fg = "#fff"; }
  else if (rank === 2) { bg = D.orange; fg = D.bg; }
  else if (rank === 3) { bg = D.yellow; fg = D.bg; }
  return (
    <span
      style={{
        display: "inline-block",
        width: 24,
        textAlign: "center",
        borderRadius: 3,
        fontWeight: 700,
        fontSize: 12,
        background: bg,
        color: fg,
        padding: "1px 0",
      }}
    >
      {rank}
    </span>
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

  // filter state
  const [category, setCategory] = useState("industry");
  const [sort, setSort] = useState("change_pct");
  const [topN, setTopN] = useState(10);

  // board detail
  const [selectedBoard, setSelectedBoard] = useState<string | null>(null);

  // expanded index row
  const [expandedIndex, setExpandedIndex] = useState<string | null>(null);

  // section collapse
  const [rotationOpen, setRotationOpen] = useState(true);
  const [indicesOpen, setIndicesOpen] = useState(true);
  const [alertsOpen, setAlertsOpen] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        category,
        sort,
        top_n: String(topN),
      });
      if (selectedBoard) params.set("board", selectedBoard);

      const resp = await fetch(`/api/sector?${params}`, { cache: "no-store" });
      const json = await resp.json();
      if (json.error && !json.rotation) {
        setFetchError(json.error);
      } else {
        setData(json);
        if (json.error) {
          setFetchError(json.error);
        } else {
          setFetchError(null);
        }
      }
    } catch (e) {
      setFetchError(`network error: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [category, sort, topN, selectedBoard]);

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

  /* ── Filter dropdown style ── */
  const selectStyle: React.CSSProperties = {
    background: D.currentLine,
    color: D.fg,
    border: `1px solid ${D.comment}`,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    padding: "3px 8px",
    borderRadius: 3,
    outline: "none",
    cursor: "pointer",
  };

  /* ── Render ── */

  const rotation = data?.rotation;
  const dates = rotation?.dates ?? [];
  const rows = rotation?.rows ?? [];
  const indices = data?.indices ?? [];
  const alerts = data?.alerts ?? [];
  const boardDetail = data?.boardDetail ?? null;

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
        <Prompt cmd="cat sector_rotation.log" />
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
            {/* Section 1: 板块轮动 (Rotation Matrix)       */}
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
                onClick={() => setRotationOpen((v) => !v)}
              >
                <span>
                  <span style={{ color: D.purple }}>{rotationOpen ? "▾" : "▸"}</span>
                  {" "}# ── 板块轮动 ──
                </span>
              </div>

              {rotationOpen && (
                <>
                  {/* filter bar */}
                  <div
                    style={{
                      display: "flex",
                      gap: 12,
                      alignItems: "center",
                      padding: "6px 0",
                    }}
                  >
                    <select
                      style={selectStyle}
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                    >
                      <option value="industry">行业(新浪49)</option>
                      <option value="concept">行业(证监会84)</option>
                    </select>
                    <select
                      style={selectStyle}
                      value={sort}
                      onChange={(e) => setSort(e.target.value)}
                    >
                      <option value="change_pct">涨幅</option>
                      <option value="drop_pct">跌幅</option>
                    </select>
                    <select
                      style={selectStyle}
                      value={topN}
                      onChange={(e) => setTopN(Number(e.target.value))}
                    >
                      <option value={10}>前10名</option>
                      <option value={20}>前20名</option>
                      <option value={30}>前30名</option>
                    </select>
                  </div>

                  {/* rotation table */}
                  {rows.length > 0 && dates.length > 0 ? (
                    <div style={{ display: "flex" }}>
                      {/* fixed rank column */}
                      <div style={{ flexShrink: 0 }}>
                        {/* header */}
                        <div
                          style={{
                            height: 32,
                            display: "flex",
                            alignItems: "center",
                            borderBottom: `1px solid ${D.currentLine}`,
                            paddingRight: 8,
                          }}
                        >
                          <span style={{ color: D.pink, fontWeight: 500, width: 40, textAlign: "center" }}>
                            排名
                          </span>
                        </div>
                        {/* rank cells */}
                        {rows.map((r) => (
                          <div
                            key={r.rank}
                            style={{
                              height: 48,
                              display: "flex",
                              alignItems: "center",
                              borderBottom: "1px solid #191a21",
                              paddingRight: 8,
                            }}
                          >
                            <span style={{ width: 40, textAlign: "center" }}>
                              <RankBadge rank={r.rank} />
                            </span>
                          </div>
                        ))}
                      </div>

                      {/* scrollable date columns */}
                      <div style={{ overflowX: "auto", flex: 1 }}>
                        {/* date header row */}
                        <div style={{ display: "flex" }}>
                          {dates.map((d) => (
                            <div
                              key={d}
                              style={{
                                minWidth: 120,
                                height: 32,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                borderBottom: `1px solid ${D.currentLine}`,
                                color: D.pink,
                                fontWeight: 500,
                                fontSize: 12,
                              }}
                            >
                              {shortDate(d)}
                            </div>
                          ))}
                        </div>

                        {/* data rows */}
                        {rows.map((r) => (
                          <div key={r.rank} style={{ display: "flex" }}>
                            {r.cells.map((cell, ci) => (
                              <div
                                key={ci}
                                style={{
                                  minWidth: 120,
                                  height: 48,
                                  display: "flex",
                                  flexDirection: "column",
                                  justifyContent: "center",
                                  alignItems: "center",
                                  borderBottom: "1px solid #191a21",
                                  cursor: cell.board ? "pointer" : "default",
                                  padding: "2px 4px",
                                }}
                                onClick={() => {
                                  if (cell.board) {
                                    setSelectedBoard(
                                      selectedBoard === cell.board ? null : cell.board,
                                    );
                                  }
                                }}
                              >
                                {cell.board ? (
                                  <>
                                    <span
                                      style={{
                                        color: selectedBoard === cell.board ? D.yellow : D.fg,
                                        fontSize: 12,
                                        fontWeight: selectedBoard === cell.board ? 700 : 400,
                                        whiteSpace: "nowrap",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        maxWidth: 116,
                                      }}
                                    >
                                      {cell.board}
                                    </span>
                                    <span
                                      style={{
                                        color: chgColor(cell.change),
                                        fontSize: 11,
                                        fontWeight: 500,
                                      }}
                                    >
                                      {fmtPct(cell.change)}
                                    </span>
                                  </>
                                ) : (
                                  <span style={{ color: D.comment, fontSize: 11 }}>-</span>
                                )}
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: D.comment, padding: "8px 0" }}>
                      # 暂无轮动数据。等待数据采集...
                    </div>
                  )}

                  {/* board detail panel */}
                  {boardDetail && (
                    <div style={{ marginTop: 8 }}>
                      {/* summary row */}
                      <div
                        style={{
                          background: D.currentLine,
                          borderRadius: "4px 4px 0 0",
                          padding: "8px 14px",
                          display: "flex",
                          alignItems: "center",
                          gap: 16,
                          fontSize: 12,
                        }}
                      >
                        <span style={{ color: D.purple }}>▸</span>
                        <span style={{ color: D.yellow, fontWeight: 700 }}>
                          {boardDetail.name}
                        </span>
                        {boardDetail.rankHistory.length > 0 && (
                          <span style={{ color: chgColor(boardDetail.rankHistory[0].rank <= 5 ? 1 : -1) }}>
                            排名{boardDetail.rankHistory[0].rank}
                          </span>
                        )}
                        <span style={{ color: D.fg }}>
                          近1月 <span style={{ color: D.orange }}>{boardDetail.top10Count}</span>次进前10
                        </span>
                        <span style={{ color: D.comment }}>
                          历史排名:{" "}
                          {boardDetail.rankHistory.slice(0, 7).map((h, i) => (
                            <span key={i} style={{ color: h.rank <= 5 ? D.red : h.rank <= 10 ? D.orange : D.comment }}>
                              {i > 0 ? " → " : ""}
                              {h.rank}
                            </span>
                          ))}
                        </span>
                        {boardDetail.stocks.length > 0 && (
                          <span style={{ color: D.comment, marginLeft: "auto" }}>
                            {boardDetail.stocks.length}只成分股
                          </span>
                        )}
                      </div>
                      {/* constituent stocks grid */}
                      {boardDetail.stocks.length > 0 && (
                        <div
                          style={{
                            background: "#1e1f29",
                            borderRadius: "0 0 4px 4px",
                            padding: "6px 14px 8px",
                            display: "flex",
                            flexWrap: "wrap",
                            gap: "2px 0",
                            fontSize: 12,
                            maxHeight: 200,
                            overflowY: "auto",
                          }}
                        >
                          {/* header */}
                          <div style={{ width: "100%", display: "flex", whiteSpace: "pre", color: D.pink, paddingBottom: 3, marginBottom: 2, borderBottom: `1px solid ${D.currentLine}` }}>
                            <span style={{ width: "10ch" }}>代码</span>
                            <span style={{ width: "8ch" }}>名称</span>
                            <span style={{ width: "9ch", textAlign: "right" }}>现价</span>
                            <span style={{ width: "8ch", textAlign: "right" }}>涨跌幅</span>
                            <span style={{ width: "9ch", textAlign: "right" }}>涨跌额</span>
                            <span style={{ width: "10ch", textAlign: "right" }}>成交额</span>
                          </div>
                          {boardDetail.stocks.map((s) => (
                            <div key={s.code} style={{ width: "100%", display: "flex", whiteSpace: "pre", padding: "1px 0", borderBottom: "1px solid #191a21" }}>
                              <span style={{ color: D.cyan, width: "10ch" }}>{s.code}</span>
                              <span style={{ color: D.fg, width: "8ch" }}>{s.name.slice(0, 6)}</span>
                              <span style={{ color: D.fg, width: "9ch", textAlign: "right" }}>{s.price.toFixed(2)}</span>
                              <span style={{ color: chgColor(s.changePct), width: "8ch", textAlign: "right", fontWeight: 500 }}>
                                {s.changePct >= 0 ? "+" : ""}{s.changePct.toFixed(2)}%
                              </span>
                              <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right" }}>
                                {s.change >= 0 ? "+" : ""}{s.change.toFixed(2)}
                              </span>
                              <span style={{ color: D.comment, width: "10ch", textAlign: "right" }}>
                                {s.amount >= 1e8 ? (s.amount / 1e8).toFixed(1) + "亿" : s.amount >= 1e4 ? (s.amount / 1e4).toFixed(0) + "万" : s.amount.toFixed(0)}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            <div style={{ height: 12 }} />

            {/* ════════════════════════════════════════════ */}
            {/* Section 2: 我的指数 (Custom Indices)         */}
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
            {/* Section 3: 主线告警 (Mainline Alerts)        */}
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
