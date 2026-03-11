"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { EditableCell } from "../components/EditableCell";
import { Toast } from "../components/Toast";

import type { WatchEntry, MonitorConfig } from "../types";
import { tagColor } from "../lib/tag-utils";

function TabBar({ holdingsCount, watchingCount }: { holdingsCount: number; watchingCount: number }) {
  return (
    <AppTabs
      active="manage"
      rightSlot={(
        <span style={{ color: D.comment, fontSize: 11, marginLeft: 12, marginRight: 16, display: "flex", alignItems: "center" }}>
          {holdingsCount} holdings | {watchingCount} watching
        </span>
      )}
    />
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

/* ── Tag Editor dropdown ── */
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
        zIndex: 100,
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

/* ── Stock row ── */
function StockRow({
  code,
  entry,
  isHolding,
  above,
  below,
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
  onToggleDipBuy,
  allTags,
  onSaveTags,
  selected,
  onToggleSelect,
}: {
  code: string;
  entry: WatchEntry;
  isHolding: boolean;
  above?: number;
  below?: number;
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
  onToggleDipBuy: (code: string, dipBuy: boolean) => void;
  allTags: string[];
  onSaveTags: (code: string, tags: string[]) => void;
  selected: boolean;
  onToggleSelect: (code: string) => void;
}) {
  const [tagEditorOpen, setTagEditorOpen] = useState(false);
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
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(code)}
          style={{ accentColor: D.purple, cursor: "pointer", marginRight: 2 }}
        />
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
              step={100}
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
              onClick={() => { if (confirm(`Demote ${code} to watching?`)) onUpdateType(code, "holding"); }}
              title="Demote to watching"
            >
              DEV
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
            PROD
          </button>
        )}
        {/* alert thresholds */}
        <span style={{ color: D.comment, fontSize: 11, paddingLeft: 4 }}>^</span>
        <EditableCell
          value={above}
          onSave={(v) => onUpdateField(code, "above", v)}
          width="60px"
          placeholder="-"
          isNumber
        />
        <span style={{ color: D.comment, fontSize: 11 }}>v</span>
        <EditableCell
          value={below}
          onSave={(v) => onUpdateField(code, "below", v)}
          width="60px"
          placeholder="-"
          isNumber
        />
        {/* star toggle */}
        <button
          onClick={() => onToggleStar(code, !entry.star)}
          title={entry.star ? "Remove L1 priority" : "Mark as L1 priority"}
          style={{
            background: entry.star ? D.yellow : "transparent",
            border: `1px solid ${entry.star ? D.yellow : D.currentLine}`,
            color: entry.star ? D.bg : D.comment,
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 700,
            padding: "1px 8px",
            borderRadius: 3,
            fontFamily: "JetBrains Mono, monospace",
            userSelect: "none",
          }}
        >
          *
        </button>
        {/* dip_buy toggle */}
        <span
          onClick={() => onToggleDipBuy(code, !entry.dip_buy)}
          title={entry.dip_buy ? "Disable dip-buy monitoring" : "Enable dip-buy monitoring"}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            cursor: "pointer",
            userSelect: "none",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 10,
            color: entry.dip_buy ? D.cyan : D.comment,
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: 28,
              height: 14,
              borderRadius: 7,
              background: entry.dip_buy ? D.cyan : D.currentLine,
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
                background: entry.dip_buy ? D.bg : D.comment,
                position: "absolute",
                top: 2,
                left: entry.dip_buy ? 16 : 2,
                transition: "left 0.2s",
              }}
            />
          </span>
          {entry.dip_buy ? "dip" : "dip"}
        </span>
        {/* hide toggle */}
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
        {/* tags */}
        <span
          style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 3, minWidth: 80, cursor: "pointer", marginLeft: 8 }}
          onClick={() => setTagEditorOpen((v) => !v)}
          title="Click to edit tags"
        >
          {(entry.tags || []).length > 0
            ? (entry.tags || []).map((t) => (
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
          {tagEditorOpen && (
            <TagEditor
              code={code}
              currentTags={entry.tags || []}
              allTags={allTags}
              onSave={onSaveTags}
              onClose={() => setTagEditorOpen(false)}
            />
          )}
        </span>
        <button style={deleteBtn} onClick={() => onRemove(code, entry.name)} title="Remove">
          x
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
          <span style={{ color: D.orange }}>-&gt;</span>
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
              step={100}
              min={0}
              value={promoShares}
              onChange={(e) => setPromoShares(e.target.value)}
              placeholder="0"
              onKeyDown={(e) => { if (e.key === "Enter") onPromote(code); }}
            />
          </label>
          <button
            style={{ ...promoteBtn, background: promoCost && promoShares ? D.orange : D.comment, color: D.bg }}
            onClick={() => onPromote(code)}
            disabled={!promoCost || !promoShares}
            title={!promoCost || !promoShares ? "Fill cost and shares first" : "Promote to holding"}
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

  // promote-to-holding inline form
  const [promoting, setPromoting] = useState<string | null>(null);
  const [promoCost, setPromoCost] = useState("");
  const [promoShares, setPromoShares] = useState("");

  // alert rules from alert_config.json
  const [alertRules, setAlertRules] = useState<Record<string, { above?: number; below?: number }>>({});

  // add form state
  const [addCode, setAddCode] = useState("");
  const [addType, setAddType] = useState<"watching" | "holding">("watching");
  const [addCost, setAddCost] = useState("");
  const [addShares, setAddShares] = useState("");

  // settings draft
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({});

  // search & collapse
  const [search, setSearch] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [prodStockOpen, setProdStockOpen] = useState(true);
  const [prodETFOpen, setProdETFOpen] = useState(false);
  const [prodHKOpen, setProdHKOpen] = useState(true);
  const [watchOpen, setWatchOpen] = useState(true);

  // batch selection
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const [batchTagOpen, setBatchTagOpen] = useState(false);
  const [batchNewTag, setBatchNewTag] = useState("");
  const batchRef = useRef<HTMLDivElement>(null);

  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  function showToast(message: string, type: "ok" | "err" = "ok") {
    setToast({ message, type });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2500);
  }

  const fetchConfig = useCallback(async () => {
    try {
      const resp = await fetch("/api/config", { cache: "no-store" });
      const data = await resp.json();
      setAlertRules(data.alerts || {});
      setConfig(data as MonitorConfig);
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

  async function handleToggleDipBuy(code: string, dip_buy: boolean) {
    await apiPost({ action: "update", code, data: { dip_buy } });
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

  const entries = config ? Object.entries(config.watchlist) : [];
  const isHK = (code: string) => code.startsWith("HK");
  const isETF = (code: string) => !isHK(code) && /^(51|15|58)\d{4}$/.test(code);

  // search filter
  const q = search.trim().toLowerCase();
  const filtered = q
    ? entries.filter(([code, v]) => code.toLowerCase().includes(q) || v.name.toLowerCase().includes(q))
    : entries;

  // grouping
  const holdings = filtered.filter(([, v]) => v.type === "holding");
  const watching = filtered.filter(([, v]) => v.type !== "holding");
  const prodStock = holdings.filter(([c]) => !isHK(c) && !isETF(c));
  const prodETF = holdings.filter(([c]) => isETF(c));
  const prodHK = holdings.filter(([c]) => isHK(c));

  // collect all unique tags across all stocks
  const allTags = Array.from(
    new Set(entries.flatMap(([, v]) => v.tags || []))
  ).sort();

  function toggleSelect(code: string) {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function handleSaveTags(code: string, tags: string[]) {
    await apiPost({ action: "update", code, data: { tags } });
  }

  async function handleBatchTagAdd(tag: string) {
    const codes = Array.from(selectedCodes);
    if (codes.length === 0 || !tag) return;
    await apiPost({ action: "tag-add", codes, tag });
    setSelectedCodes(new Set());
    setBatchTagOpen(false);
    setBatchNewTag("");
  }

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
        background: D.bg,
        color: D.fg,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <AppTitleBar title="manage — 持仓管理" />

      {toast && <Toast message={toast.message} type={toast.type} />}

      <TabBar holdingsCount={holdings.length} watchingCount={watching.length} />

      {/* scrollable body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "8px 16px 24px",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <div style={{ height: 6 }} />

        {/* inline search */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <span style={{ color: D.comment }}>filter:</span>
          <input
            placeholder="code or name..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              background: D.currentLine,
              border: `1px solid ${D.comment}`,
              color: D.fg,
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 12,
              padding: "2px 8px",
              outline: "none",
              borderRadius: 2,
              width: 160,
            }}
          />
          {search && (
            <span style={{ color: D.comment, fontSize: 11 }}>
              {filtered.length}/{entries.length} matched
            </span>
          )}
        </div>

        {loading && (
          <div style={{ color: D.comment, padding: "8px 0" }}>
            <span style={{ color: D.cyan }}>info</span> Loading config...
          </div>
        )}

        {!loading && !config && (
          <div style={{ color: D.red, padding: "8px 0" }}>Failed to load config</div>
        )}

        {!loading && config && (<>
        {/* ── settings (collapsed by default) ── */}
        <div
          style={{ color: D.comment, padding: "10px 0 6px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 4, fontSize: 13, cursor: "pointer", userSelect: "none" }}
          onClick={() => setSettingsOpen((v) => !v)}
        >
          <span style={{ color: D.purple }}>{settingsOpen ? "v" : ">"}</span>
          {" "}# -- settings --
        </div>
        {settingsOpen && (<>
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
        <SectionHeader># -- alert levels --</SectionHeader>
        {([
          { label: "L1 * Star", prefix: "l1", keys: ["trigger_pct", "delta_pct", "cooldown_min"], color: D.yellow },
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
        </>)}

        {/* batch tag toolbar */}
        {selectedCodes.size > 0 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 8px", marginBottom: 4,
            background: D.currentLine, borderRadius: 4,
            fontSize: 12,
          }}>
            <span style={{ color: D.purple, fontWeight: 700 }}>
              {selectedCodes.size} selected
            </span>
            <div style={{ position: "relative" }} ref={batchRef}>
              <button
                onClick={() => setBatchTagOpen((v) => !v)}
                style={{
                  background: "transparent",
                  border: `1px solid ${D.purple}`,
                  color: D.purple,
                  cursor: "pointer",
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "2px 10px",
                  borderRadius: 3,
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >批量打 Tag</button>
              {batchTagOpen && (
                <div style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  zIndex: 100,
                  background: D.bg,
                  border: `1px solid ${D.purple}`,
                  borderRadius: 4,
                  padding: "8px 10px",
                  minWidth: 180,
                  marginTop: 4,
                  fontFamily: "JetBrains Mono, monospace",
                  fontSize: 11,
                }}>
                  {allTags.map((t) => (
                    <div
                      key={t}
                      onClick={() => handleBatchTagAdd(t)}
                      style={{
                        display: "flex", alignItems: "center", gap: 6,
                        padding: "3px 4px", cursor: "pointer",
                        borderRadius: 2,
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = D.currentLine; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                    >
                      <span style={{
                        background: tagColor(t),
                        color: "#282a36",
                        padding: "0 6px",
                        borderRadius: 3,
                        fontSize: 10,
                        fontWeight: 700,
                      }}>{t}</span>
                    </div>
                  ))}
                  <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                    <input
                      value={batchNewTag}
                      onChange={(e) => setBatchNewTag(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && batchNewTag.trim()) handleBatchTagAdd(batchNewTag.trim()); }}
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
                      onClick={() => { if (batchNewTag.trim()) handleBatchTagAdd(batchNewTag.trim()); }}
                      style={{
                        background: D.purple,
                        border: "none",
                        color: D.bg,
                        cursor: "pointer",
                        fontSize: 11,
                        fontWeight: 700,
                        padding: "2px 8px",
                        borderRadius: 2,
                        fontFamily: "JetBrains Mono, monospace",
                      }}
                    >+</button>
                  </div>
                </div>
              )}
            </div>
            <button
              onClick={() => setSelectedCodes(new Set())}
              style={{
                background: "transparent",
                border: "none",
                color: D.comment,
                cursor: "pointer",
                fontSize: 11,
                fontFamily: "JetBrains Mono, monospace",
              }}
            >clear</button>
          </div>
        )}

        {/* column legend */}
        <div style={{ display: "flex", gap: 4, padding: "4px 0 2px", color: D.comment, fontSize: 11, borderBottom: `1px solid ${D.currentLine}`, alignItems: "center" }}>
          <span style={{ width: 20 }}></span>
          <span style={{ width: 50 }}>type</span>
          <span style={{ width: 90 }}>code</span>
          <span style={{ width: 110 }}>name</span>
          <span style={{ width: 80 }}>cost</span>
          <span style={{ width: 80 }}>shares</span>
          <span style={{ width: 60 }}></span>
          <span style={{ width: 75, color: D.orange }}>^ above</span>
          <span style={{ width: 75, color: D.cyan }}>v below</span>
          <span style={{ width: 30 }}>*</span>
          <span style={{ width: 40 }}>dip</span>
          <span style={{ width: 40 }}>hide</span>
          <span style={{ minWidth: 80, marginLeft: 8 }}>tags</span>
        </div>

        {/* ── prod:A share stocks ── */}
        {prodStock.length > 0 && (
          <>
            <div
              style={{ color: D.comment, padding: "10px 0 6px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 4, fontSize: 13, cursor: "pointer", userSelect: "none" }}
              onClick={() => setProdStockOpen((v) => !v)}
            >
              <span style={{ color: D.purple }}>{prodStockOpen ? "v" : ">"}</span>
              {" "}# -- A stocks ({prodStock.length}) --
            </div>
            {prodStockOpen && prodStock.map(([code, entry]) => (
              <StockRow
                key={code} code={code} entry={entry} isHolding
                above={alertRules[code]?.above} below={alertRules[code]?.below}
                promoting={promoting} promoCost={promoCost} promoShares={promoShares}
                setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
                onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
                onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar} onToggleDipBuy={handleToggleDipBuy}
                allTags={allTags} onSaveTags={handleSaveTags}
                selected={selectedCodes.has(code)} onToggleSelect={toggleSelect}
              />
            ))}
          </>
        )}

        {/* ── prod:ETF ── */}
        {prodETF.length > 0 && (
          <>
            <div
              style={{ color: D.comment, padding: "10px 0 6px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 4, fontSize: 13, cursor: "pointer", userSelect: "none" }}
              onClick={() => setProdETFOpen((v) => !v)}
            >
              <span style={{ color: D.purple }}>{prodETFOpen ? "v" : ">"}</span>
              {" "}# -- ETF ({prodETF.length}) --
            </div>
            {prodETFOpen && prodETF.map(([code, entry]) => (
              <StockRow
                key={code} code={code} entry={entry} isHolding
                above={alertRules[code]?.above} below={alertRules[code]?.below}
                promoting={promoting} promoCost={promoCost} promoShares={promoShares}
                setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
                onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
                onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar} onToggleDipBuy={handleToggleDipBuy}
                allTags={allTags} onSaveTags={handleSaveTags}
                selected={selectedCodes.has(code)} onToggleSelect={toggleSelect}
              />
            ))}
          </>
        )}

        {/* ── prod:HK ── */}
        {prodHK.length > 0 && (
          <>
            <div
              style={{ color: D.comment, padding: "10px 0 6px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 4, fontSize: 13, cursor: "pointer", userSelect: "none" }}
              onClick={() => setProdHKOpen((v) => !v)}
            >
              <span style={{ color: D.purple }}>{prodHKOpen ? "v" : ">"}</span>
              {" "}# -- HK ({prodHK.length}) --
            </div>
            {prodHKOpen && prodHK.map(([code, entry]) => (
              <StockRow
                key={code} code={code} entry={entry} isHolding
                above={alertRules[code]?.above} below={alertRules[code]?.below}
                promoting={promoting} promoCost={promoCost} promoShares={promoShares}
                setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
                onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
                onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar} onToggleDipBuy={handleToggleDipBuy}
                allTags={allTags} onSaveTags={handleSaveTags}
                selected={selectedCodes.has(code)} onToggleSelect={toggleSelect}
              />
            ))}
          </>
        )}

        {holdings.length === 0 && (
          <div style={{ color: D.comment, padding: "6px 0" }}>No holdings.</div>
        )}

        {/* ── watching ── */}
        {watching.length > 0 && (
          <>
            <div
              style={{ color: D.comment, padding: "10px 0 6px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 4, fontSize: 13, cursor: "pointer", userSelect: "none" }}
              onClick={() => setWatchOpen((v) => !v)}
            >
              <span style={{ color: D.purple }}>{watchOpen ? "v" : ">"}</span>
              {" "}# -- watching ({watching.length}) --
            </div>
            {watchOpen && watching.map(([code, entry]) => (
              <StockRow
                key={code} code={code} entry={entry} isHolding={false}
                above={alertRules[code]?.above} below={alertRules[code]?.below}
                promoting={promoting} promoCost={promoCost} promoShares={promoShares}
                setPromoting={setPromoting} setPromoCost={setPromoCost} setPromoShares={setPromoShares}
                onUpdateField={handleUpdateField} onUpdateType={handleUpdateType}
                onPromote={handlePromote} onRemove={handleRemove} onToggleHidden={handleToggleHidden} onToggleStar={handleToggleStar} onToggleDipBuy={handleToggleDipBuy}
                allTags={allTags} onSaveTags={handleSaveTags}
                selected={selectedCodes.has(code)} onToggleSelect={toggleSelect}
              />
            ))}
          </>
        )}

        {/* ── add stock ── */}
        <SectionHeader># -- add stock --</SectionHeader>
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
              step={100}
              min={0}
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
        </>)}

      </div>
    </div>
  );
}
