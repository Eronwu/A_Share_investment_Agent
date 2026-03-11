"use client";

import { useEffect, useRef, useState } from "react";
import { D } from "../theme";
import { Service } from "../types";
import { PlanMap } from "../hooks/useTradePlans";

/* ── Props ── */
export interface StockDrawerProps {
  symbol: string | null;
  services: Service[];
  planMap: PlanMap;
  allTags: string[];
  onClose: () => void;
  onRefreshPlans: () => void;
}

/* ── Inline-editable cell (copied from manage/page.tsx) ── */
function EditableCell({
  value,
  onSave,
  width,
  placeholder,
  isNumber,
  step,
  color,
}: {
  value: string | number | null | undefined;
  onSave: (val: string) => void;
  width: string;
  placeholder?: string;
  isNumber?: boolean;
  step?: number | string;
  color?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value ?? ""));
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  // sync external value changes
  useEffect(() => {
    if (!editing) setDraft(String(value ?? ""));
  }, [value, editing]);

  function commit() {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed !== String(value ?? "")) {
      onSave(trimmed);
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setDraft(String(value ?? ""));
            setEditing(false);
          }
        }}
        style={{
          width,
          background: D.currentLine,
          border: `1px solid ${D.purple}`,
          color: D.fg,
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 13,
          padding: "1px 4px",
          outline: "none",
          borderRadius: 2,
        }}
        type={isNumber ? "number" : "text"}
        step={step ?? (isNumber ? "any" : undefined)}
      />
    );
  }

  const display = value != null && value !== "" ? String(value) : placeholder || "-";
  return (
    <span
      onClick={() => setEditing(true)}
      style={{
        width,
        display: "inline-block",
        cursor: "pointer",
        color: color || (value != null && value !== "" ? D.fg : D.comment),
        borderBottom: `1px dashed ${D.currentLine}`,
        padding: "1px 2px",
      }}
      title="Click to edit"
    >
      {display}
    </span>
  );
}

/* ── Toast notification (copied from manage/page.tsx) ── */
function Toast({ message, type }: { message: string; type: "ok" | "err" }) {
  return (
    <div
      style={{
        position: "fixed",
        top: 16,
        right: 16,
        background: type === "ok" ? D.green : D.red,
        color: D.bg,
        padding: "8px 16px",
        borderRadius: 4,
        fontSize: 13,
        fontFamily: "JetBrains Mono, monospace",
        fontWeight: 500,
        zIndex: 300,
        boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
      }}
    >
      {message}
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
  // ESC key handler (Task 8)
  useEffect(() => {
    if (!symbol) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [symbol, onClose]);

  if (!symbol) return null;

  const service = services.find((s) => s.id === symbol);

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
          width: 440,
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
          <div style={{ color: D.comment }}>（内容即将添加）</div>
        </div>
      </div>
    </>
  );
}

// Re-export helpers so other modules can import them from this file if needed
export { EditableCell, Toast };
