"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { D } from "../theme";
import { Service } from "../types";
import { PlanMap } from "../hooks/useTradePlans";
import { EditableCell } from "./EditableCell";
import { Toast } from "./Toast";
import { tagColor } from "../lib/tag-utils";

/* ── Props ── */
export interface StockDrawerProps {
  symbol: string | null;
  services: Service[];
  planMap: PlanMap;
  allTags: string[];
  onClose: () => void;
  onRefreshPlans: () => void;
}

/* ── TagEditor (inline popup) ── */
function TagEditor({
  code,
  currentTags,
  allTags,
  onSave,
  onClose,
}: {
  code: string;
  currentTags: string[];
  allTags: string[];
  onSave: (code: string, tags: string[]) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(currentTags));
  const [newTag, setNewTag] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [onClose]);

  function toggle(tag: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  function addNew() {
    const t = newTag.trim();
    if (!t) return;
    setSelected((prev) => new Set(prev).add(t));
    setNewTag("");
  }

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        top: "100%",
        left: 0,
        zIndex: 300,
        background: D.bg,
        border: `1px solid ${D.purple}`,
        borderRadius: 4,
        padding: "8px 10px",
        minWidth: 180,
        maxHeight: 260,
        overflow: "auto",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 11,
      }}
    >
      {allTags.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          {allTags.map((t) => (
            <label
              key={t}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "2px 0",
                cursor: "pointer",
                color: D.fg,
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(t)}
                onChange={() => toggle(t)}
                style={{ accentColor: D.purple }}
              />
              <span
                style={{
                  background: tagColor(t),
                  color: "#282a36",
                  padding: "0 6px",
                  borderRadius: 3,
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                {t}
              </span>
            </label>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
        <input
          value={newTag}
          onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") addNew(); }}
          placeholder="new tag..."
          style={{
            background: D.currentLine,
            border: `1px solid ${D.comment}`,
            color: D.fg,
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 11,
            padding: "2px 6px",
            outline: "none",
            borderRadius: 2,
            flex: 1,
            minWidth: 0,
          }}
        />
        <button
          onClick={addNew}
          style={{
            background: "transparent",
            border: `1px solid ${D.green}`,
            color: D.green,
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 700,
            padding: "0 6px",
            borderRadius: 2,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >+</button>
      </div>
      <button
        onClick={() => { onSave(code, Array.from(selected)); onClose(); }}
        style={{
          background: D.purple,
          border: "none",
          color: D.bg,
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          padding: "3px 12px",
          borderRadius: 3,
          fontFamily: "JetBrains Mono, monospace",
          width: "100%",
        }}
      >确定</button>
    </div>
  );
}

/* ── StockDrawer ── */
export function StockDrawer({
  symbol,
  services,
  planMap,
  allTags,
  onClose,
  onRefreshPlans,
}: StockDrawerProps) {
  // Keep a stable ref to onClose so the ESC handler doesn't need it as a dep
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // ESC key handler — dep only on symbol (open/close state)
  useEffect(() => {
    if (!symbol) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCloseRef.current();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [symbol]);

  // Body scroll lock while drawer is open
  useEffect(() => {
    if (!symbol) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [symbol]);

  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" } | null>(null);
  const [tagEditorOpen, setTagEditorOpen] = useState(false);

  const saveConfig = useCallback(async (field: string, value: unknown) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code: symbol, [field]: value }),
      });
      setToast(res.ok ? { msg: "已保存", type: "ok" } : { msg: "保存失败", type: "err" });
    } catch {
      setToast({ msg: "保存失败", type: "err" });
    }
    setTimeout(() => setToast(null), 2000);
  }, [symbol]);

  const saveTags = useCallback(async (code: string, tags: string[]) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code, tags }),
      });
      setToast(res.ok ? { msg: "标签已保存", type: "ok" } : { msg: "保存失败", type: "err" });
      if (res.ok) onRefreshPlans();
    } catch {
      setToast({ msg: "保存失败", type: "err" });
    }
    setTimeout(() => setToast(null), 2000);
  }, [onRefreshPlans]);

  if (!symbol) return null;

  const service = services.find((s) => s.id === symbol);

  // planMap is available for future Task 4 (trade plans section)
  void planMap;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 200,
        }}
      />

      {/* Drawer panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(440px, 100vw)",
          background: D.bg,
          borderLeft: "1px solid " + D.currentLine,
          zIndex: 201,
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 13,
        }}
      >
        {/* Header */}
        <div
          style={{
            position: "sticky",
            top: 0,
            background: D.bg,
            borderBottom: "1px solid " + D.currentLine,
            padding: "10px 16px",
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "space-between",
            zIndex: 1,
          }}
        >
          <span style={{ color: D.cyan, fontWeight: "bold" }}>
            {symbol}&nbsp;&nbsp;{service?.name}
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "none",
              border: "none",
              color: D.comment,
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
              padding: "0 4px",
            }}
          >
            ×
          </button>
        </div>

        {/* Content area */}
        <div style={{ padding: "12px 16px" }}>

          {/* Section 1: 基本信息 */}
          <section style={{ marginBottom: 20 }}>
            <div style={{ color: D.comment, fontSize: 11, marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>基本信息</div>
            <div style={{ display: "flex", gap: 24 }}>
              <span>成本&nbsp;
                <EditableCell
                  value={service?.cost ?? null}
                  onSave={(v) => saveConfig("cost", parseFloat(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                />
              </span>
              <span>股数&nbsp;
                <EditableCell
                  value={service?.shares ?? null}
                  onSave={(v) => saveConfig("shares", parseInt(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                />
              </span>
            </div>
          </section>

          {/* Divider */}
          <div style={{ borderTop: "1px solid " + D.currentLine, marginBottom: 16, paddingTop: 16 }} />

          {/* Section 2: 告警阈值 */}
          <section style={{ marginBottom: 20 }}>
            <div style={{ color: D.comment, fontSize: 11, marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>告警阈值</div>
            <div style={{ display: "flex", gap: 24 }}>
              <span style={{ color: D.red }}>上限&nbsp;
                <EditableCell
                  value={service?.above ?? null}
                  onSave={(v) => saveConfig("above", v === "" ? null : parseFloat(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                  color={D.red}
                />
              </span>
              <span style={{ color: D.green }}>下限&nbsp;
                <EditableCell
                  value={service?.below ?? null}
                  onSave={(v) => saveConfig("below", v === "" ? null : parseFloat(v))}
                  width="80px"
                  isNumber
                  placeholder="-"
                  color={D.green}
                />
              </span>
            </div>
          </section>

          {/* Divider */}
          <div style={{ borderTop: "1px solid " + D.currentLine, marginBottom: 16, paddingTop: 16 }} />

          {/* Section 3: 标签 */}
          <section style={{ marginBottom: 20 }}>
            <div style={{ color: D.comment, fontSize: 11, marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>标签</div>
            <span
              style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 3, cursor: "pointer", flexWrap: "wrap" }}
              onClick={() => setTagEditorOpen((v) => !v)}
              title="Click to edit tags"
            >
              {(service?.tags || []).length > 0
                ? (service?.tags || []).map((t) => (
                    <span
                      key={t}
                      style={{
                        background: tagColor(t),
                        color: "#282a36",
                        padding: "0 5px",
                        borderRadius: 3,
                        fontSize: 10,
                        fontWeight: 700,
                        whiteSpace: "nowrap",
                      }}
                    >{t}</span>
                  ))
                : <span style={{ color: D.comment, fontSize: 10 }}>+tag</span>
              }
              {tagEditorOpen && symbol && (
                <TagEditor
                  code={symbol}
                  currentTags={service?.tags || []}
                  allTags={allTags}
                  onSave={saveTags}
                  onClose={() => setTagEditorOpen(false)}
                />
              )}
            </span>
          </section>

        </div>
      </div>

      {/* Toast */}
      {toast && <Toast message={toast.msg} type={toast.type} />}
    </>
  );
}

// Re-export shared helpers so existing imports from this file keep working
export { EditableCell, Toast };
