"use client";

import { useEffect, useRef } from "react";
import { D } from "../theme";
import { Service } from "../types";
import { PlanMap } from "../hooks/useTradePlans";
import { EditableCell } from "./EditableCell";
import { Toast } from "./Toast";

/* ── Props ── */
export interface StockDrawerProps {
  symbol: string | null;
  services: Service[];
  planMap: PlanMap;
  allTags: string[];
  onClose: () => void;
  onRefreshPlans: () => void;
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
          <div style={{ color: D.comment }}>（内容即将添加）</div>
        </div>
      </div>
    </>
  );
}

// Re-export shared helpers so existing imports from this file keep working
export { EditableCell, Toast };
