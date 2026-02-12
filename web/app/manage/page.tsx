"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Link from "next/link";
import { D } from "../theme";

import type { WatchEntry, MonitorConfig } from "../types";

/* ── Inline-editable cell ── */
function EditableCell({
  value,
  onSave,
  width,
  placeholder,
  isNumber,
}: {
  value: string | number | null | undefined;
  onSave: (val: string) => void;
  width: string;
  placeholder?: string;
  isNumber?: boolean;
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
        step={isNumber ? "any" : undefined}
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
        color: value != null && value !== "" ? D.fg : D.comment,
        borderBottom: `1px dashed ${D.currentLine}`,
        padding: "1px 2px",
      }}
      title="Click to edit"
    >
      {display}
    </span>
  );
}

/* ── Toast notification ── */
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
        zIndex: 100,
        boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
      }}
    >
      {message}
    </div>
  );
}

/* ── TitleBar ── */
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
        ✱ manage — watchlist
      </span>
    </div>
  );
}

/* ── Section header ── */
function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        color: D.comment,
        padding: "10px 0 6px",
        borderBottom: `1px solid ${D.currentLine}`,
        marginBottom: 4,
        fontSize: 13,
      }}
    >
      {children}
    </div>
  );
}

/* ── Column header ── */
function ColHeader({ children, width, align }: { children: React.ReactNode; width: string; align?: string }) {
  return (
    <span
      style={{
        width,
        display: "inline-block",
        color: D.pink,
        fontWeight: 500,
        textAlign: (align as React.CSSProperties["textAlign"]) || "left",
      }}
    >
      {children}
    </span>
  );
}

/* ── Stock row ── */
function StockRow({
  code,
  entry,
  isHolding,
  promoting,
  promoCost,
  promoShares,
  setPromoting,
  setPromoCost,
  setPromoShares,
  onUpdateField,
  onUpdateType,
  onPromote,
  onRemove,
  onToggleHidden,
  onToggleStar,
}: {
  code: string;
  entry: WatchEntry;
  isHolding: boolean;
  promoting: string | null;
  promoCost: string;
  promoShares: string;
  setPromoting: (v: string | null) => void;
  setPromoCost: (v: string) => void;
  setPromoShares: (v: string) => void;
  onUpdateField: (code: string, field: string, val: string) => void;
  onUpdateType: (code: string, currentType: string) => void;
  onPromote: (code: string) => void;
  onRemove: (code: string, name: string) => void;
  onToggleHidden: (code: string, hidden: boolean) => void;
  onToggleStar: (code: string, star: boolean) => void;
}) {
  const isPromoting = promoting === code;

  const typeBadgeStyle: React.CSSProperties = {
    display: "inline-block",
    padding: "1px 8px",
    borderRadius: 3,
    fontSize: 11,
    fontWeight: 700,
    cursor: "pointer",
    background: isHolding ? D.orange : D.comment,
    color: D.bg,
    userSelect: "none",
  };

  const promoteBtn: React.CSSProperties = {
    background: "transparent",
    border: `1px solid ${D.orange}`,
    color: D.orange,
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 700,
    padding: "1px 8px",
    borderRadius: 3,
    fontFamily: "JetBrains Mono, monospace",
  };

  const promoteSmallInput: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.orange}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 12,
    padding: "2px 6px",
    outline: "none",
    borderRadius: 2,
    width: 70,
  };

  const deleteBtn: React.CSSProperties = {
    background: "transparent",
    border: "none",
    color: D.red,
    cursor: "pointer",
    fontSize: 15,
    fontWeight: 700,
    padding: "0 6px",
    fontFamily: "JetBrains Mono, monospace",
  };

  return (
    <div style={{ borderBottom: `1px solid #191a21` }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "4px 0",
          whiteSpace: "nowrap",
        }}
      >
        <span
          style={typeBadgeStyle}
          onClick={() => onUpdateType(code, entry.type || "watching")}
          title="Click to toggle type"
        >
          {isHolding ? "PROD" : "DEV"}
        </span>
        <span style={{ width: 90, color: D.cyan, display: "inline-block", paddingLeft: 4 }}>
          {code}
        </span>
        <span style={{ width: 110, color: D.fg, display: "inline-block" }}>
          {entry.name}
        </span>
        {isHolding && (
          <>
            <EditableCell
              value={entry.cost}
              onSave={(v) => onUpdateField(code, "cost", v)}
              width="80px"
              placeholder="cost"
              isNumber
            />
            <EditableCell
              value={entry.shares}
              onSave={(v) => onUpdateField(code, "shares", v)}
              width="80px"
              placeholder="shares"
              isNumber
            />
            <button
              style={{
                background: "transparent",
                border: `1px solid ${D.comment}`,
                color: D.comment,
                cursor: "pointer",
                fontSize: 11,
                fontWeight: 700,
                padding: "1px 8px",
                borderRadius: 3,
                fontFamily: "JetBrains Mono, monospace",
              }}
              onClick={() => onUpdateType(code, "holding")}
              title="Demote to watching"
            >
              ↓ DEV
            </button>
          </>
        )}
        {!isHolding && (
          <button
            style={promoteBtn}
            onClick={() => {
              if (isPromoting) {
                setPromoting(null);
              } else {
                setPromoting(code);
                setPromoCost("");
                setPromoShares("");
              }
            }}
            title="Promote to holding"
          >
            ↑ PROD
          </button>
        )}
        {/* star toggle — available for all stocks */}
        <span
          onClick={() => onToggleStar(code, !entry.star)}
          title={entry.star ? "Remove L1 priority" : "Mark as L1 priority"}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            cursor: "pointer",
            userSelect: "none",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 10,
            color: entry.star ? D.yellow : D.comment,
            padding: "2px 0",
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: 28,
              height: 14,
              borderRadius: 7,
              background: entry.star ? D.yellow : D.currentLine,
              position: "relative",
              transition: "background 0.2s",
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: entry.star ? D.bg : D.comment,
                position: "absolute",
                top: 2,
                left: entry.star ? 16 : 2,
                transition: "left 0.2s",
              }}
            />
          </span>
          {entry.star ? "★ L1" : "☆"}
        </span>
        {isHolding && (
          <span
            onClick={() => onToggleHidden(code, !entry.hidden)}
            title={entry.hidden ? "Unhide (show on dashboard)" : "Hide (out of sight)"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              cursor: "pointer",
              userSelect: "none",
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 10,
              color: entry.hidden ? D.yellow : D.comment,
            }}
          >
            {/* toggle track */}
            <span
              style={{
                display: "inline-block",
                width: 28,
                height: 14,
                borderRadius: 7,
                background: entry.hidden ? D.orange : D.currentLine,
                position: "relative",
                transition: "background 0.2s",
              }}
            >
              {/* toggle knob */}
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: entry.hidden ? D.bg : D.comment,
                  position: "absolute",
                  top: 2,
                  left: entry.hidden ? 16 : 2,
                  transition: "left 0.2s",
                }}
              />
            </span>
            {entry.hidden ? "hidden" : "hide"}
          </span>
        )}
        <button style={deleteBtn} onClick={() => onRemove(code, entry.name)} title="Remove">
          ×
        </button>
      </div>
      {/* inline promote form */}
      {isPromoting && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "4px 0 6px 50px",
            color: D.comment,
            fontSize: 12,
          }}
        >
          <span style={{ color: D.orange }}>→</span>
          <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
            cost:
            <input
              style={promoteSmallInput}
              type="number"
              step="any"
              value={promoCost}
              onChange={(e) => setPromoCost(e.target.value)}
              placeholder="0.00"
              autoFocus
              onKeyDown={(e) => { if (e.key === "Enter") onPromote(code); }}
            />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
            shares:
            <input
              style={promoteSmallInput}
              type="number"
              value={promoShares}
              onChange={(e) => setPromoShares(e.target.value)}
              placeholder="0"
              onKeyDown={(e) => { if (e.key === "Enter") onPromote(code); }}
            />
          </label>
          <button
            style={{ ...promoteBtn, background: D.orange, color: D.bg }}
            onClick={() => onPromote(code)}
          >
            Confirm
          </button>
          <button
            style={{ ...promoteBtn, borderColor: D.comment, color: D.comment }}
            onClick={() => setPromoting(null)}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Main page ── */
export default function ManagePage() {
  const [config, setConfig] = useState<MonitorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ message: string; type: "ok" | "err" } | null>(null);

  // promote-to-holding inline form: { code: { cost, shares } }
  const [promoting, setPromoting] = useState<string | null>(null);
  const [promoCost, setPromoCost] = useState("");
  const [promoShares, setPromoShares] = useState("");

  // add form state
  const [addCode, setAddCode] = useState("");
  const [addType, setAddType] = useState<"watching" | "holding">("watching");
  const [addCost, setAddCost] = useState("");
  const [addShares, setAddShares] = useState("");

  // settings draft
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({});

  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  function showToast(message: string, type: "ok" | "err" = "ok") {
    setToast({ message, type });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2500);
  }

  const fetchConfig = useCallback(async () => {
    try {
      const resp = await fetch("/api/config", { cache: "no-store" });
      const data: MonitorConfig = await resp.json();
      setConfig(data);
      const s = data.settings;
      setSettingsDraft({
        poll_interval: String(s.poll_interval ?? 30),
        big_move_pct: String(s.big_move_pct ?? 3),
        cooldown_minutes: String(s.cooldown_minutes ?? 10),
        l1_trigger_pct: String(s.l1_trigger_pct ?? 4),
        l1_delta_pct: String(s.l1_delta_pct ?? 3),
        l1_cooldown_min: String(s.l1_cooldown_min ?? 5),
        l2_trigger_pct: String(s.l2_trigger_pct ?? 6),
        l2_delta_pct: String(s.l2_delta_pct ?? 5),
        l2_cooldown_min: String(s.l2_cooldown_min ?? 15),
        l3_cooldown_min: String(s.l3_cooldown_min ?? 30),
      });
    } catch {
      showToast("Failed to load config", "err");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  async function apiPost(body: Record<string, unknown>) {
    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await resp.json();
      if (result.success) {
        showToast(result.message);
        await fetchConfig();
      } else {
        showToast(result.message || "Failed", "err");
      }
      return result;
    } catch (e) {
      showToast(String(e), "err");
      return null;
    }
  }

  async function handleUpdateField(code: string, field: string, rawVal: string) {
    const numVal = rawVal === "" ? null : Number(rawVal);
    await apiPost({ action: "update", code, data: { [field]: numVal } });
  }

  async function handleUpdateType(code: string, currentType: string) {
    const newType = currentType === "holding" ? "watching" : "holding";
    await apiPost({ action: "update", code, data: { type: newType } });
  }

  async function handlePromote(code: string) {
    const cost = promoCost ? Number(promoCost) : null;
    const shares = promoShares ? Number(promoShares) : null;
    const result = await apiPost({
      action: "update",
      code,
      data: { type: "holding", cost, shares },
    });
    if (result?.success) {
      setPromoting(null);
      setPromoCost("");
      setPromoShares("");
    }
  }

  async function handleToggleStar(code: string, star: boolean) {
    await apiPost({ action: "update", code, data: { star } });
  }

  async function handleToggleHidden(code: string, hidden: boolean) {
    await apiPost({ action: "update", code, data: { hidden } });
  }

  async function handleRemove(code: string, name: string) {
    if (!confirm(`Remove ${code} (${name})?`)) return;
    await apiPost({ action: "remove", code });
  }

  async function handleAdd() {
    const code = addCode.trim();
    if (!code) {
      showToast("Code is required", "err");
      return;
    }
    const data: Record<string, unknown> = {};
    if (addType === "holding") data.type = "holding";
    if (addCost) data.cost = Number(addCost);
    if (addShares) data.shares = Number(addShares);

    const result = await apiPost({ action: "add", code, data });
    if (result?.success) {
      setAddCode("");
      setAddCost("");
      setAddShares("");
      setAddType("watching");
    }
  }

  async function handleSaveSettings() {
    const settings: Record<string, number> = {};
    for (const [k, v] of Object.entries(settingsDraft)) {
      const n = Number(v);
      if (!isNaN(n) && n > 0) settings[k] = n;
    }
    await apiPost({ action: "settings", settings });
  }

  if (loading) {
    return (
      <div style={{ height: "100vh", background: D.bg, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: D.comment, fontFamily: "JetBrains Mono, monospace", fontSize: 14 }}>Loading...</span>
      </div>
    );
  }

  if (!config) {
    return (
      <div style={{ height: "100vh", background: D.bg, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ color: D.red, fontFamily: "JetBrains Mono, monospace", fontSize: 14 }}>Failed to load config</span>
      </div>
    );
  }

  const entries = Object.entries(config.watchlist);
  const holdings = entries.filter(([, v]) => v.type === "holding");
  const watching = entries.filter(([, v]) => v.type !== "holding");

  const inputStyle: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 13,
    padding: "4px 8px",
    outline: "none",
    borderRadius: 2,
    width: 80,
  };

  const btnStyle: React.CSSProperties = {
    background: D.purple,
    color: D.bg,
    border: "none",
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 13,
    fontWeight: 700,
    padding: "5px 16px",
    borderRadius: 3,
    cursor: "pointer",
  };


  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
        fontFamily: "JetBrains Mono, monospace",
        color: D.fg,
      }}
    >
      <TitleBar />

      {toast && <Toast message={toast.message} type={toast.type} />}

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
        <span style={{ color: D.purple, fontWeight: 700 }}>manage</span>
      </div>

      {/* scrollable body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "10px 20px 40px",
          fontSize: 13,
          lineHeight: 1.7,
        }}
      >
        {/* ── settings ── */}
        <SectionHeader># ── settings ──</SectionHeader>
        <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "6px 0", flexWrap: "wrap" }}>
          {(["poll_interval"] as const).map((key) => (
            <label key={key} style={{ display: "flex", alignItems: "center", gap: 6, color: D.comment }}>
              <span>{key}:</span>
              <input
                style={{ ...inputStyle, width: 60 }}
                type="number"
                value={settingsDraft[key] ?? ""}
                onChange={(e) => setSettingsDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") handleSaveSettings(); }}
              />
            </label>
          ))}
          <button style={btnStyle} onClick={handleSaveSettings}>
            Save
          </button>
        </div>

        {/* ── alert levels ── */}
        <SectionHeader># ── alert levels ──</SectionHeader>
        {([
          { label: "L1 ★ Star", prefix: "l1", keys: ["trigger_pct", "delta_pct", "cooldown_min"], color: D.yellow },
          { label: "L2 Holding", prefix: "l2", keys: ["trigger_pct", "delta_pct", "cooldown_min"], color: D.orange },
          { label: "L3 Watching", prefix: "l3", keys: ["cooldown_min"], color: D.comment },
        ] as const).map((tier) => (
          <div key={tier.prefix} style={{ display: "flex", gap: 12, alignItems: "center", padding: "4px 0", flexWrap: "wrap" }}>
            <span style={{ color: tier.color, fontWeight: 700, width: 100 }}>{tier.label}</span>
            {tier.keys.map((k) => {
              const fullKey = `${tier.prefix}_${k}`;
              return (
                <label key={fullKey} style={{ display: "flex", alignItems: "center", gap: 4, color: D.comment, fontSize: 12 }}>
                  <span>{k}:</span>
                  <input
                    style={{ ...inputStyle, width: 50, fontSize: 12, padding: "2px 4px" }}
                    type="number"
                    step="any"
                    value={settingsDraft[fullKey] ?? ""}
                    onChange={(e) => setSettingsDraft((prev) => ({ ...prev, [fullKey]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") handleSaveSettings(); }}
                  />
                </label>
              );
            })}
          </div>
        ))}
        <div style={{ padding: "4px 0" }}>
          <button style={{ ...btnStyle, fontSize: 12, padding: "3px 12px" }} onClick={handleSaveSettings}>
            Save Levels
          </button>
        </div>

        {/* ── production ── */}
        <SectionHeader># ── production ({holdings.length}) ──</SectionHeader>
        {holdings.length > 0 && (
          <div style={{ display: "flex", gap: 4, padding: "4px 0", whiteSpace: "nowrap" }}>
            <ColHeader width="46px">TYPE</ColHeader>
            <ColHeader width="90px">CODE</ColHeader>
            <ColHeader width="110px">NAME</ColHeader>
            <ColHeader width="80px">COST</ColHeader>
            <ColHeader width="80px">SHARES</ColHeader>
            <span style={{ width: 30 }} />
          </div>
        )}
        {holdings.map(([code, entry]) => (
          <StockRow
            key={code} code={code} entry={entry} isHolding
            promoting={promoting} promoCost={promoCost} promoShares={promoShares}
            setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
            onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
            onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar}
          />
        ))}
        {holdings.length === 0 && (
          <div style={{ color: D.comment, padding: "6px 0" }}>No holdings. Add a stock with type &quot;holding&quot; below.</div>
        )}

        {/* ── staging ── */}
        <SectionHeader># ── staging ({watching.length}) ──</SectionHeader>
        {watching.length > 0 && (
          <div style={{ display: "flex", gap: 4, padding: "4px 0", whiteSpace: "nowrap" }}>
            <ColHeader width="46px">TYPE</ColHeader>
            <ColHeader width="90px">CODE</ColHeader>
            <ColHeader width="110px">NAME</ColHeader>
            <span style={{ width: 30 }} />
          </div>
        )}
        {watching.map(([code, entry]) => (
          <StockRow
            key={code} code={code} entry={entry} isHolding={false}
            promoting={promoting} promoCost={promoCost} promoShares={promoShares}
            setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
            onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
            onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar}
          />
        ))}
        {watching.length === 0 && (
          <div style={{ color: D.comment, padding: "6px 0" }}>No watching stocks.</div>
        )}

        {/* ── add stock ── */}
        <SectionHeader># ── add stock ──</SectionHeader>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", padding: "8px 0" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: D.comment }}>
            code:
            <input
              style={{ ...inputStyle, width: 100 }}
              value={addCode}
              onChange={(e) => setAddCode(e.target.value.toUpperCase())}
              placeholder="000001"
              onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
            />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: D.comment }}>
            type:
            <select
              style={{
                ...inputStyle,
                width: 100,
                appearance: "auto",
              }}
              value={addType}
              onChange={(e) => setAddType(e.target.value as "watching" | "holding")}
            >
              <option value="watching">watching</option>
              <option value="holding">holding</option>
            </select>
          </label>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", padding: "4px 0" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: D.comment }}>
            cost:
            <input
              style={inputStyle}
              type="number"
              step="any"
              value={addCost}
              onChange={(e) => setAddCost(e.target.value)}
              placeholder="-"
              disabled={addType !== "holding"}
            />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: D.comment }}>
            shares:
            <input
              style={inputStyle}
              type="number"
              value={addShares}
              onChange={(e) => setAddShares(e.target.value)}
              placeholder="-"
              disabled={addType !== "holding"}
            />
          </label>
          <button style={btnStyle} onClick={handleAdd}>
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
