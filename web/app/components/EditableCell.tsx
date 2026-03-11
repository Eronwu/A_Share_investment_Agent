"use client";

import { useEffect, useRef, useState } from "react";
import { D } from "../theme";

export function EditableCell({
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
