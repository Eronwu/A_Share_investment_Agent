"use client";

import { D } from "../theme";

export function Toast({ message, type }: { message: string; type: "ok" | "err" }) {
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
