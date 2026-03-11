"use client";

import { useEffect, useState, useCallback } from "react";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";

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
  stockCount: number;
  createdAt: string;
  today: number;
  d3: number;
  d5: number;
  d10: number;
  d15: number;
  d30: number;
  cumGain: number;
  status: "mainline" | "approaching" | "watching" | "inactive";
  components: ComponentEntry[];
  history: DayPoint[];
  parent: string | null;
  isParent: boolean;
  children: string[];
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

function TabBar({ indicesCount, alertsCount, lastFetchTime }: { indicesCount: number; alertsCount: number; lastFetchTime: Date | null }) {
  const timeLabel = (() => {
    if (!lastFetchTime) return "...";
    const hh = lastFetchTime.getHours();
    const mm = lastFetchTime.getMinutes();
    const timeStr = `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
    // Trading hours: 09:15 - 15:30
    const mins = hh * 60 + mm;
    if (mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30) {
      return `${timeStr} 更新 | 60s`;
    }
    return `${timeStr} 已收盘`;
  })();

  return (
    <AppTabs
      active="sector"
      rightSlot={(
        <span style={{ color: D.comment, fontSize: 11, marginLeft: 12, marginRight: 16 }}>
          {indicesCount} indices | {alertsCount} alerts | {timeLabel}
        </span>
      )}
    />
  );
}

/* ── Create Index Modal ── */
function CreateModal({
  onClose,
  onCreated,
  parentOptions,
}: {
  onClose: () => void;
  onCreated: () => void;
  parentOptions: { id: string; name: string }[];
}) {
  const [tagName, setTagName] = useState("");
  const [parentTag, setParentTag] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleCreate() {
    const trimTag = tagName.trim();
    if (!trimTag) {
      setError("Tag 名称不能为空");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const resp = await fetch("/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "create-tag",
          tag: trimTag,
          ...(parentTag ? { parent: parentTag } : {}),
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
          # ── 新建 Tag 指数 ──
        </div>

        <label style={{ color: D.comment, fontSize: 12, display: "block", marginBottom: 4 }}>
          Tag 名称:
        </label>
        <input
          style={{ ...inputStyle, marginBottom: 8 }}
          value={tagName}
          onChange={(e) => setTagName(e.target.value)}
          placeholder="磷化工"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter") handleCreate();
          }}
        />
        {parentOptions.length > 0 && (
          <>
            <label style={{ color: D.comment, fontSize: 12, display: "block", marginBottom: 4 }}>
              父指数 (可选):
            </label>
            <select
              style={{ ...inputStyle, marginBottom: 8, appearance: "auto" }}
              value={parentTag}
              onChange={(e) => setParentTag(e.target.value)}
            >
              <option value="">无 (独立指数)</option>
              {parentOptions.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </>
        )}
        <div style={{ color: D.comment, fontSize: 11, marginBottom: 16 }}>
          创建后在 /manage 页面给股票打上此 tag 即可加入指数
        </div>

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
  const [lastFetchTime, setLastFetchTime] = useState<Date | null>(null);

  // expanded index row + hover highlight
  const [expandedIndex, setExpandedIndex] = useState<string | null>(null);
  const [hoverIndex, setHoverIndex] = useState<string | null>(null);

  // section collapse
  const [indicesOpen, setIndicesOpen] = useState(true);
  const [alertsOpen, setAlertsOpen] = useState(true);

  // parent-child collapse: collapsed parent tags
  const [collapsedParents, setCollapsedParents] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch("/api/sector", { cache: "no-store" });
      const json = await resp.json();
      if (json.error && !json.indices) {
        setFetchError(json.error);
      } else {
        setData(json);
        setFetchError(json.error || null);
        setLastFetchTime(new Date());
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
    await postAction({ action: "delete-tag", tag: id });
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
      <AppTitleBar title="sector — indices" />

      <TabBar indicesCount={indices.length} alertsCount={alerts.length} lastFetchTime={lastFetchTime} />

      {/* scrollable body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "8px 16px 24px",
          lineHeight: 1.55,
        }}
      >
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
                    // Build ordered display list: parents first with children, then standalone
                    const parentIds = new Set(indices.filter((i) => i.isParent).map((i) => i.id));
                    const childOf = new Map<string, string>(); // childId -> parentId
                    for (const idx of indices) {
                      if (idx.parent) childOf.set(idx.id, idx.parent);
                    }

                    const orderedIndices: IndexEntry[] = [];
                    const added = new Set<string>();

                    // First: parents + their children
                    for (const idx of indices) {
                      if (idx.isParent && !added.has(idx.id)) {
                        orderedIndices.push(idx);
                        added.add(idx.id);
                        // Add children (sorted same as overall: star first, cumGain desc)
                        const children = indices
                          .filter((c) => c.parent === idx.id)
                          .sort((a, b) => {
                            if (a.star !== b.star) return a.star ? -1 : 1;
                            return b.cumGain - a.cumGain;
                          });
                        for (const child of children) {
                          orderedIndices.push(child);
                          added.add(child.id);
                        }
                      }
                    }

                    // Then: standalone tags (no parent, not a parent)
                    for (const idx of indices) {
                      if (!added.has(idx.id)) {
                        orderedIndices.push(idx);
                        added.add(idx.id);
                      }
                    }

                    // Filter out collapsed children
                    const visibleIndices = orderedIndices.filter((idx) => {
                      if (idx.parent && collapsedParents.has(idx.parent)) return false;
                      return true;
                    });

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
                    const NAME_W = 220;

                    function toggleParentCollapse(parentId: string) {
                      setCollapsedParents((prev) => {
                        const next = new Set(prev);
                        if (next.has(parentId)) next.delete(parentId);
                        else next.add(parentId);
                        return next;
                      });
                    }

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
                          {visibleIndices.map((idx) => {
                            const statusInfo = statusStyle(idx.status);
                            const isChild = !!idx.parent;
                            const isParent = idx.isParent;
                            const isCollapsed = isParent && collapsedParents.has(idx.id);
                            return (
                              <div
                                key={idx.id}
                                style={{
                                  height: ROW_H,
                                  display: "flex",
                                  alignItems: "center",
                                  borderBottom: "1px solid #191a21",
                                  gap: 2,
                                  background: hoverIndex === idx.id
                                    ? "#2a2b36"
                                    : isParent
                                      ? "#1e1f29"
                                      : "transparent",
                                  transition: "background 0.1s",
                                  paddingLeft: isChild ? 14 : 0,
                                }}
                                onMouseEnter={() => setHoverIndex(idx.id)}
                                onMouseLeave={() => setHoverIndex(null)}
                              >
                                {/* collapse arrow (parent only) + star toggle */}
                                {isParent && (
                                  <span
                                    style={{
                                      width: 14,
                                      color: D.purple,
                                      cursor: "pointer",
                                      userSelect: "none",
                                      fontSize: 12,
                                      textAlign: "center",
                                      fontWeight: 700,
                                      flexShrink: 0,
                                    }}
                                    onClick={() => toggleParentCollapse(idx.id)}
                                  >
                                    {isCollapsed ? "▸" : "▾"}
                                  </span>
                                )}
                                <span
                                  style={{
                                    width: isChild ? 12 : 16,
                                    color: idx.star ? D.yellow : D.comment,
                                    cursor: "pointer",
                                    userSelect: "none",
                                    fontSize: 12,
                                    textAlign: "center",
                                    flexShrink: 0,
                                  }}
                                  onClick={() => handleToggleStar(idx.id, idx.star)}
                                >
                                  {idx.star ? "★" : "☆"}
                                </span>
                                {/* name -- click to open chart modal */}
                                <div
                                  style={{ flex: 1, minWidth: 0, overflow: "hidden", cursor: "pointer" }}
                                  onClick={() => setExpandedIndex(idx.id)}
                                >
                                  <div style={{
                                    fontSize: isParent ? 12 : isChild ? 11 : 12,
                                    color: isParent ? D.fg : D.cyan,
                                    fontWeight: isParent ? 700 : 400,
                                    whiteSpace: "nowrap",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                  }}>
                                    {idx.name}
                                  </div>
                                  <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 1, overflow: "hidden", whiteSpace: "nowrap" }}>
                                    {(idx.status === "mainline" || idx.status === "approaching") && (
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
                                          flexShrink: 0,
                                        }}
                                      >
                                        {statusInfo.label}
                                      </span>
                                    )}
                                    <span style={{ fontSize: 10, color: D.comment }}>
                                      {idx.stockCount ?? idx.stocks?.length ?? 0}只{isParent ? ` (${idx.children.length}子)` : ""}
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
                          {visibleIndices.map((idx) => {
                            const idxLookup = lookup.get(idx.id);
                            const isParent = idx.isParent;
                            const isChild = !!idx.parent;
                            return (
                              <div
                                key={idx.id}
                                style={{
                                  display: "flex",
                                  cursor: "pointer",
                                  background: hoverIndex === idx.id
                                    ? "#2a2b36"
                                    : isParent
                                      ? "#1e1f29"
                                      : "transparent",
                                  transition: "background 0.1s",
                                }}
                                onClick={() => setExpandedIndex(idx.id)}
                                onMouseEnter={() => setHoverIndex(idx.id)}
                                onMouseLeave={() => setHoverIndex(null)}
                              >
                                {allDates.map((d) => {
                                  const pt = idxLookup?.get(d);
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
                                        fontSize: isChild ? 11 : 12,
                                      }}
                                    >
                                      {pt ? (
                                        <>
                                          <span style={{ color: chgColor(pt.change), fontWeight: isParent ? 600 : 500 }}>
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

                  {/* nothing inline -- chart is in modal */}
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

      </div>

      {/* create modal */}
      {showCreateModal && (
        <CreateModal
          onClose={() => setShowCreateModal(false)}
          onCreated={fetchData}
          parentOptions={
            indices
              .filter((i) => i.isParent || (!i.parent && i.children.length === 0))
              .map((i) => ({ id: i.id, name: i.name }))
          }
        />
      )}

      {/* chart modal */}
      {expandedIndex && (() => {
        const idx = indices.find((i) => i.id === expandedIndex);
        if (!idx) return null;

        const hasChart = idx.history.length >= 2;
        const W = 680, H = 200, PAD_T = 20, PAD_B = 26, PAD_L = 44, PAD_R = 12;

        // Chart data (only computed when hasChart)
        const pts = hasChart ? [...idx.history].reverse() : [];
        const vals = pts.map((p) => p.value);
        const minV = hasChart ? Math.min(...vals) : 0;
        const maxV = hasChart ? Math.max(...vals) : 0;
        const range = maxV - minV || 1;

        const xStep = hasChart ? (W - PAD_L - PAD_R) / (pts.length - 1) : 0;
        const yScale = (v: number) =>
          PAD_T + (H - PAD_T - PAD_B) * (1 - (v - minV) / range);

        const line = hasChart
          ? pts.map((p, i) => `${PAD_L + i * xStep},${yScale(p.value)}`).join(" ")
          : "";
        const area = hasChart
          ? `${PAD_L},${yScale(minV)} ` +
            pts.map((p, i) => `${PAD_L + i * xStep},${yScale(p.value)}`).join(" ") +
            ` ${PAD_L + (pts.length - 1) * xStep},${yScale(minV)}`
          : "";

        const lastVal = hasChart ? vals[vals.length - 1] : 0;
        const firstVal = hasChart ? vals[0] : 0;
        const trendUp = lastVal >= firstVal;
        const lineColor = trendUp ? D.red : D.green;
        const fillColor = trendUp ? "rgba(255,85,85,0.12)" : "rgba(80,250,123,0.12)";

        const yTicks = hasChart ? Array.from({ length: 5 }, (_, i) => minV + (range * i) / 4) : [];
        const xLabelStep = Math.max(1, Math.floor(pts.length / 8));
        const xLabels = pts.filter((_, i) => i % xLabelStep === 0 || i === pts.length - 1);

        const baseY = hasChart ? yScale(100) : 0;
        const baseInRange = hasChart && 100 >= minV && 100 <= maxV;

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
                {hasChart && (
                  <span style={{ color: D.comment, fontSize: 12 }}>
                    指数值 {lastVal.toFixed(1)}
                  </span>
                )}
                <span style={{ color: chgColor(idx.cumGain), fontSize: 13, fontWeight: 600 }}>
                  {fmtPct(idx.cumGain)}
                </span>
                {hasChart && (
                  <span style={{ color: D.comment, fontSize: 11 }}>
                    {pts.length}日
                  </span>
                )}
                {(idx.status === "mainline" || idx.status === "approaching") && (() => {
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

              {/* SVG chart or placeholder */}
              {hasChart ? (
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
              ) : (
                <div style={{
                  height: 80,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: D.comment,
                  fontSize: 12,
                  border: `1px dashed ${D.currentLine}`,
                  borderRadius: 4,
                }}>
                  K线数据将在收盘后生成
                </div>
              )}

              {/* component stocks table */}
              {(() => {
                // For parent indices, build code → child tag mapping
                const codeToChild: Record<string, string[]> = {};
                if (idx.isParent && idx.children.length > 0) {
                  for (const childId of idx.children) {
                    const childIdx = indices.find((i) => i.id === childId);
                    if (childIdx) {
                      for (const code of childIdx.stocks || []) {
                        if (!codeToChild[code]) codeToChild[code] = [];
                        if (!codeToChild[code].includes(childId)) codeToChild[code].push(childId);
                      }
                    }
                  }
                }
                const hasChildTags = Object.keys(codeToChild).length > 0;
                return (
              <div style={{ marginTop: 12, borderTop: `1px solid ${D.currentLine}`, paddingTop: 8 }}>
                <div style={{ color: D.comment, fontSize: 11, marginBottom: 6 }}>
                  成分股 ({idx.stockCount ?? idx.stocks?.length ?? 0}):
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
                        {hasChildTags && (
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 500 }}>分类</th>
                        )}
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
                        const childTags = codeToChild[c.code] || [];
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
                            {hasChildTags && (
                              <td style={{ padding: "5px 8px" }}>
                                {childTags.map((t) => (
                                  <span
                                    key={t}
                                    style={{
                                      display: "inline-block",
                                      fontSize: 10,
                                      padding: "1px 5px",
                                      marginRight: 3,
                                      borderRadius: 3,
                                      background: D.currentLine,
                                      color: D.purple,
                                    }}
                                  >
                                    {t}
                                  </span>
                                ))}
                              </td>
                            )}
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
                );
              })()}
            </div>
          </div>
        );
      })()}

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
