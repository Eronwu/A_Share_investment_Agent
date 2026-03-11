"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { D } from "../theme";
import { Service } from "../types";
import { PlanMap, TradePlan, PlanOrder } from "../hooks/useTradePlans";
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

/* ── Order type options ── */
const ORDER_TYPE_OPTIONS = [
  { label: "到价卖出", side: "sell" as const, op: ">=" as const, trailing: false },
  { label: "到价买入", side: "buy" as const, op: ">=" as const, trailing: false },
  { label: "回落卖出", side: "sell" as const, op: ">=" as const, trailing: true },
  { label: "反弹买入", side: "buy" as const, op: "<=" as const, trailing: true },
];

function orderTypeLabel(o: PlanOrder): string {
  if (o.trailing) {
    return o.side === "sell" ? "回落卖出" : "反弹买入";
  }
  return o.side === "sell" ? "到价卖出" : "到价买入";
}

/* ── PlanCard ── */
function PlanCard({
  plan,
  onChanged,
}: {
  plan: TradePlan;
  onChanged: () => void;
}) {
  const planId = plan.id;
  const pos = plan.position;
  const lotSize = plan.lot_size || 1;
  void lotSize;
  const posShares = pos?.shares ?? 0;
  const posCost = pos?.cost ?? 0;
  const posPrice = pos?.price ?? 0;
  const pnlPct = posCost > 0 && posPrice > 0 ? ((posPrice - posCost) / posCost * 100) : null;
  const pnlAmt = posCost > 0 && posPrice > 0 && posShares > 0 ? (posPrice - posCost) * posShares : null;

  const [showAddOrder, setShowAddOrder] = useState(false);

  const cardBorder = plan.status === "active" ? D.green : D.comment;

  async function planPost(body: Record<string, unknown>) {
    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      onChanged();
    } catch {
      // silent
    }
  }

  async function handleToggle() {
    await planPost({ action: "toggle", id: planId });
  }

  async function handleDelete() {
    if (!confirm(`Delete plan ${planId}?`)) return;
    await planPost({ action: "delete", id: planId });
  }

  async function handleUpdateOrder(orderId: string, field: string, val: string) {
    const updatedOrders = plan.orders.map((o) => {
      if (o.id !== orderId) return o;
      const updated = { ...o };
      if (field === "price") updated.price = Number(val) || 0;
      if (field === "shares") updated.shares = Number(val) || 0;
      return updated;
    });
    await planPost({ action: "update", id: planId, updates: { orders: updatedOrders } });
  }

  return (
    <div style={{
      border: `1px solid ${cardBorder}`,
      borderRadius: 4,
      padding: "8px 12px",
      marginBottom: 8,
      background: D.bg,
    }}>
      {/* title row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{
          display: "inline-block",
          padding: "1px 6px",
          borderRadius: 3,
          fontSize: 10,
          fontWeight: 700,
          background: plan.status === "active" ? D.green : D.comment,
          color: D.bg,
          cursor: "pointer",
          userSelect: "none",
        }} onClick={handleToggle} title="Toggle active/paused">
          {plan.status === "active" ? "运行中" : "已暂停"}
        </span>
        <span style={{ color: D.fg }}>{plan.name}</span>
        {pos && posShares > 0 && (
          <span style={{ color: D.comment, fontSize: 11 }}>
            {posShares}股 @{posCost > 0 ? posCost.toFixed(2) : "-"}
            {posPrice > 0 && <>{" "}now:{posPrice.toFixed(2)}</>}
            {pnlPct != null && (
              <span style={{ color: pnlPct >= 0 ? D.red : D.green, marginLeft: 4 }}>
                {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
              </span>
            )}
            {pnlAmt != null && (
              <span style={{ color: pnlAmt >= 0 ? D.red : D.green, marginLeft: 4 }}>
                {pnlAmt >= 0 ? "+" : ""}{pnlAmt.toFixed(0)}
              </span>
            )}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button
            onClick={() => setShowAddOrder((v) => !v)}
            style={{
              background: "transparent",
              border: `1px solid ${D.cyan}`,
              color: D.cyan,
              cursor: "pointer",
              fontSize: 11,
              fontFamily: "JetBrains Mono, monospace",
              padding: "1px 6px",
              borderRadius: 2,
            }}
            title="Add condition order"
          >+条件</button>
          <button
            onClick={handleDelete}
            style={{
              background: "transparent",
              border: "none",
              color: D.red,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 700,
              fontFamily: "JetBrains Mono, monospace",
            }}
            title="Delete plan"
          >x</button>
        </span>
      </div>

      {/* orders */}
      {plan.orders.length > 0 && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          <div style={{ color: D.comment, fontSize: 11, marginBottom: 2 }}>-- 条件单 --</div>
          {plan.orders.map((o) => {
            const isSell = o.side === "sell";
            return (
              <div key={o.id} style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "2px 0",
                opacity: o.triggered ? 0.5 : 1,
                borderLeft: `2px solid ${isSell ? D.red : D.green}`,
                paddingLeft: 8,
                marginBottom: 2,
              }}>
                <span style={{
                  fontSize: 10,
                  color: D.bg,
                  background: isSell ? D.red : D.green,
                  padding: "0 4px",
                  borderRadius: 2,
                  fontWeight: 700,
                }}>
                  {orderTypeLabel(o)}
                </span>
                <span style={{ color: D.comment }}>{o.op}</span>
                <EditableCell
                  value={o.price}
                  onSave={(v) => handleUpdateOrder(o.id, "price", v)}
                  width="60px"
                  isNumber
                  color={isSell ? D.red : D.green}
                />
                {o.shares != null && o.shares > 0 && (
                  <EditableCell
                    value={o.shares}
                    onSave={(v) => handleUpdateOrder(o.id, "shares", v)}
                    width="56px"
                    isNumber
                    color={isSell ? D.orange : D.green}
                  />
                )}
                <span style={{ color: isSell ? D.orange : D.green, fontSize: 11 }}>股</span>
                {o.trailing && (
                  <span style={{ color: D.purple, fontSize: 10 }}>回落{o.trailing.pct}%</span>
                )}
                <span style={{ color: D.comment, fontSize: 11 }}>{o.label}</span>
                {o.triggered && (
                  <span style={{ color: D.yellow, fontSize: 10 }}>已触发 {o.triggered_at?.slice(0, 10) || ""}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* inline add order form */}
      {showAddOrder && (
        <NewOrderForm
          planId={planId}
          plan={plan}
          onCreated={() => { setShowAddOrder(false); onChanged(); }}
          onCancel={() => setShowAddOrder(false)}
        />
      )}
    </div>
  );
}

/* ── NewOrderForm (inline, appends an order to an existing plan) ── */
function NewOrderForm({
  planId,
  plan,
  onCreated,
  onCancel,
}: {
  planId: string;
  plan: TradePlan;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [typeIdx, setTypeIdx] = useState(0);
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [trailPct, setTrailPct] = useState("");
  const [label, setLabel] = useState("");

  const opt = ORDER_TYPE_OPTIONS[typeIdx];

  const inputS: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11,
    padding: "3px 6px",
    outline: "none",
    borderRadius: 2,
  };

  const selectS: React.CSSProperties = {
    ...inputS,
    appearance: "auto" as const,
  };

  async function handleSubmit() {
    const qty = Number(quantity) || 0;
    const newOrder: Record<string, unknown> = {
      id: `o${plan.orders.length + 1}_${Date.now().toString(36)}`,
      side: opt.side,
      op: opt.op,
      price: Number(price) || 0,
      shares: qty || null,
      volume_min: null,
      consecutive_days: null,
      trailing: opt.trailing && trailPct ? { pct: Number(trailPct), watermark: null, active: false } : null,
      label: label || "",
      triggered: false,
      triggered_at: null,
    };
    const updatedOrders = [...plan.orders, newOrder];
    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", id: planId, updates: { orders: updatedOrders } }),
      });
      onCreated();
    } catch {
      // silent
    }
  }

  return (
    <div style={{
      marginTop: 8,
      padding: "8px",
      background: D.currentLine,
      borderRadius: 3,
      display: "flex",
      flexWrap: "wrap",
      gap: 6,
      alignItems: "center",
      fontSize: 11,
    }}>
      <select
        style={{ ...selectS, width: 90 }}
        value={typeIdx}
        onChange={(e) => setTypeIdx(Number(e.target.value))}
      >
        {ORDER_TYPE_OPTIONS.map((t, ti) => (
          <option key={ti} value={ti}>{t.label}</option>
        ))}
      </select>
      <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
        价格:
        <input
          style={{ ...inputS, width: 60 }}
          type="number"
          step="any"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
        股数:
        <input
          style={{ ...inputS, width: 55 }}
          type="number"
          step="100"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          placeholder="1000"
        />
      </label>
      {opt.trailing && (
        <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.purple }}>
          回落%:
          <input
            style={{ ...inputS, width: 45 }}
            type="number"
            step="0.1"
            value={trailPct}
            onChange={(e) => setTrailPct(e.target.value)}
          />
        </label>
      )}
      <input
        style={{ ...inputS, width: 90 }}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="标签"
      />
      <button
        onClick={handleSubmit}
        style={{
          background: D.green,
          border: "none",
          color: D.bg,
          cursor: "pointer",
          fontSize: 11,
          fontWeight: 700,
          padding: "3px 10px",
          borderRadius: 2,
          fontFamily: "JetBrains Mono, monospace",
        }}
      >确定</button>
      <button
        onClick={onCancel}
        style={{
          background: "transparent",
          border: `1px solid ${D.comment}`,
          color: D.comment,
          cursor: "pointer",
          fontSize: 11,
          padding: "3px 8px",
          borderRadius: 2,
          fontFamily: "JetBrains Mono, monospace",
        }}
      >取消</button>
    </div>
  );
}

/* ── NewPlanForm (inline, symbol pre-filled) ── */
function NewPlanForm({
  defaultSymbol,
  onCreated,
  onCancel,
}: {
  defaultSymbol: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [planName, setPlanName] = useState("");

  interface OrderDraft {
    typeIdx: number;
    price: string;
    quantity: string;
    trailPct: string;
    label: string;
  }
  const [orders, setOrders] = useState<OrderDraft[]>([]);

  function addOrderRow() {
    setOrders((prev) => [...prev, { typeIdx: 0, price: "", quantity: "", trailPct: "", label: "" }]);
  }

  function updateOrder(idx: number, field: keyof OrderDraft, val: string | number) {
    setOrders((prev) => prev.map((o, i) => i === idx ? { ...o, [field]: String(val) } : o));
  }

  function removeOrder(idx: number) {
    setOrders((prev) => prev.filter((_, i) => i !== idx));
  }

  const inputS: React.CSSProperties = {
    background: D.currentLine,
    border: `1px solid ${D.comment}`,
    color: D.fg,
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11,
    padding: "3px 6px",
    outline: "none",
    borderRadius: 2,
  };

  const selectS: React.CSSProperties = {
    ...inputS,
    appearance: "auto" as const,
  };

  async function handleSubmit() {
    if (!planName) return;
    const id = `${defaultSymbol}_${Date.now().toString(36)}`;

    const builtOrders = orders.map((o, i) => {
      const opt = ORDER_TYPE_OPTIONS[o.typeIdx];
      const qty = Number(o.quantity) || 0;
      return {
        id: `o${i + 1}`,
        side: opt.side,
        op: opt.op,
        price: Number(o.price) || 0,
        shares: qty || null,
        volume_min: null,
        consecutive_days: null,
        trailing: opt.trailing && o.trailPct ? { pct: Number(o.trailPct), watermark: null, active: false } : null,
        label: o.label || "",
        triggered: false,
        triggered_at: null,
      };
    });

    const plan = {
      name: planName,
      symbol: defaultSymbol,
      status: "active",
      scope: "real",
      created_at: new Date().toISOString().slice(0, 10),
      orders: builtOrders,
    };

    try {
      await fetch("/api/trade-plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create", id, plan }),
      });
      onCreated();
    } catch {
      // silent
    }
  }

  return (
    <div style={{
      border: `1px solid ${D.purple}`,
      borderRadius: 4,
      padding: "10px 12px",
      marginBottom: 8,
      background: D.bg,
      fontSize: 12,
    }}>
      <div style={{ color: D.purple, fontWeight: 700, marginBottom: 8, fontSize: 12 }}>新建交易计划</div>

      {/* symbol (read-only) + plan name */}
      <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
        <span style={{ color: D.comment, fontSize: 11 }}>股票:</span>
        <span style={{ color: D.cyan, fontWeight: 700, fontSize: 12 }}>{defaultSymbol}</span>
        <label style={{ display: "flex", alignItems: "center", gap: 4, color: D.comment, marginLeft: 8 }}>
          计划名:
          <input
            style={{ ...inputS, width: 140 }}
            value={planName}
            onChange={(e) => setPlanName(e.target.value)}
            placeholder="分批建仓"
            autoFocus
          />
        </label>
      </div>

      {/* orders */}
      {orders.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ color: D.comment, fontSize: 11, marginBottom: 4 }}>-- 条件单 --</div>
          {orders.map((o, idx) => {
            const opt = ORDER_TYPE_OPTIONS[o.typeIdx];
            return (
              <div key={idx} style={{ display: "flex", gap: 5, marginBottom: 5, alignItems: "center", flexWrap: "wrap" }}>
                <select
                  style={{ ...selectS, width: 90 }}
                  value={o.typeIdx}
                  onChange={(e) => updateOrder(idx, "typeIdx", Number(e.target.value))}
                >
                  {ORDER_TYPE_OPTIONS.map((t, ti) => (
                    <option key={ti} value={ti}>{t.label}</option>
                  ))}
                </select>
                <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
                  价格:
                  <input
                    style={{ ...inputS, width: 60 }}
                    type="number"
                    step="any"
                    value={o.price}
                    onChange={(e) => updateOrder(idx, "price", e.target.value)}
                  />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.comment }}>
                  股数:
                  <input
                    style={{ ...inputS, width: 55 }}
                    type="number"
                    step="100"
                    value={o.quantity}
                    onChange={(e) => updateOrder(idx, "quantity", e.target.value)}
                    placeholder="1000"
                  />
                </label>
                {opt.trailing && (
                  <label style={{ display: "flex", alignItems: "center", gap: 2, color: D.purple }}>
                    回落%:
                    <input
                      style={{ ...inputS, width: 45 }}
                      type="number"
                      step="0.1"
                      value={o.trailPct}
                      onChange={(e) => updateOrder(idx, "trailPct", e.target.value)}
                    />
                  </label>
                )}
                <input
                  style={{ ...inputS, width: 90 }}
                  value={o.label}
                  onChange={(e) => updateOrder(idx, "label", e.target.value)}
                  placeholder="标签"
                />
                <button
                  onClick={() => removeOrder(idx)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: D.red,
                    cursor: "pointer",
                    fontSize: 13,
                    fontWeight: 700,
                    fontFamily: "JetBrains Mono, monospace",
                  }}
                >x</button>
              </div>
            );
          })}
        </div>
      )}

      <button
        onClick={addOrderRow}
        style={{
          background: "transparent",
          border: `1px dashed ${D.comment}`,
          color: D.comment,
          cursor: "pointer",
          fontSize: 11,
          padding: "2px 10px",
          borderRadius: 3,
          fontFamily: "JetBrains Mono, monospace",
          marginBottom: 10,
        }}
      >+ 添加条件</button>

      {/* actions */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          onClick={onCancel}
          style={{
            background: "transparent",
            border: `1px solid ${D.comment}`,
            color: D.comment,
            cursor: "pointer",
            fontSize: 11,
            padding: "3px 12px",
            borderRadius: 3,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >取消</button>
        <button
          onClick={handleSubmit}
          disabled={!planName}
          style={{
            background: planName ? D.purple : D.comment,
            border: "none",
            color: D.bg,
            cursor: planName ? "pointer" : "not-allowed",
            fontSize: 11,
            fontWeight: 700,
            padding: "3px 12px",
            borderRadius: 3,
            fontFamily: "JetBrains Mono, monospace",
          }}
        >创建</button>
      </div>
    </div>
  );
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
  const [showNewPlanForm, setShowNewPlanForm] = useState(false);

  // Optimistic overrides: take precedence over stale service data from 30s poll
  const [starOverride, setStarOverride] = useState<boolean | null>(null);
  const [dipOverride, setDipOverride] = useState<boolean | null>(null);

  // Reset overrides when drawer opens a different symbol
  useEffect(() => {
    setStarOverride(null);
    setDipOverride(null);
  }, [symbol]);

  const saveConfig = useCallback(async (field: string, value: unknown) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code: symbol, data: { [field]: value } }),
      });
      setToast(res.ok ? { msg: "已保存", type: "ok" } : { msg: "保存失败", type: "err" });
    } catch {
      setToast({ msg: "保存失败", type: "err" });
    }
    setTimeout(() => setToast(null), 2000);
  }, [symbol]);

  const saveData = useCallback(async (data: Record<string, unknown>) => {
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update", code: symbol, data }),
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
  const symbolPlans = planMap[symbol] ?? [];

  // Optimistic values: override takes precedence over stale poll data
  const isStar = starOverride !== null ? starOverride : (service?.star ?? false);
  const isDip = dipOverride !== null ? dipOverride : (service?.dip_buy ?? false);

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
              <span style={{ color: D.comment }}>别名&nbsp;
                <EditableCell
                  value={service?.alias ?? null}
                  onSave={(v) => saveConfig("alias", v.trim() || null)}
                  width="90px"
                  placeholder="-"
                />
              </span>
            </div>
            {/* star + dip toggles */}
            <div style={{ display: "flex", gap: 16, marginTop: 10, alignItems: "center" }}>
              {/* star */}
              <button
                onClick={() => { setStarOverride(!isStar); saveData({ star: !isStar }); }}
                title={isStar ? "取消 L1 优先级" : "标记为 L1 优先级"}
                style={{
                  background: isStar ? D.yellow : "transparent",
                  border: `1px solid ${isStar ? D.yellow : D.currentLine}`,
                  color: isStar ? D.bg : D.comment,
                  cursor: "pointer",
                  fontSize: 13,
                  fontWeight: 700,
                  padding: "2px 10px",
                  borderRadius: 3,
                  fontFamily: "JetBrains Mono, monospace",
                  userSelect: "none",
                }}
              >
                ★ {isStar ? "已星标" : "星标"}
              </button>
              {/* dip_buy */}
              <span
                onClick={() => { setDipOverride(!isDip); saveData({ dip_buy: !isDip }); }}
                title={isDip ? "关闭回调监控" : "开启回调买入监控"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  cursor: "pointer",
                  userSelect: "none",
                  fontFamily: "JetBrains Mono, monospace",
                  fontSize: 12,
                  color: isDip ? D.cyan : D.comment,
                }}
              >
                <span style={{
                  display: "inline-block",
                  width: 28, height: 14,
                  borderRadius: 7,
                  background: isDip ? D.cyan : D.currentLine,
                  position: "relative",
                  transition: "background 0.2s",
                }}>
                  <span style={{
                    display: "inline-block",
                    width: 10, height: 10,
                    borderRadius: "50%",
                    background: isDip ? D.bg : D.comment,
                    position: "absolute",
                    top: 2,
                    left: isDip ? 16 : 2,
                    transition: "left 0.2s",
                  }} />
                </span>
                dip 回调监控
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

          {/* Section 4: 交易计划 */}
          <div style={{ borderTop: `1px solid ${D.currentLine}`, marginTop: 8, paddingTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ color: D.comment, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>交易计划</div>
              <button
                onClick={() => setShowNewPlanForm(true)}
                style={{
                  fontSize: 11, background: "none",
                  border: `1px solid ${D.cyan}`, color: D.cyan,
                  padding: "2px 10px", cursor: "pointer", borderRadius: 2,
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >+ 新建</button>
            </div>
            {showNewPlanForm && (
              <NewPlanForm
                defaultSymbol={symbol!}
                onCreated={() => { setShowNewPlanForm(false); onRefreshPlans(); }}
                onCancel={() => setShowNewPlanForm(false)}
              />
            )}
            {symbolPlans.length === 0 && !showNewPlanForm && (
              <div style={{ color: D.comment, fontSize: 11 }}>暂无计划，点击「+ 新建」创建</div>
            )}
            {symbolPlans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} onChanged={onRefreshPlans} />
            ))}
          </div>

        </div>
      </div>

      {/* Toast */}
      {toast && <Toast message={toast.msg} type={toast.type} />}
    </>
  );
}

// Re-export shared helpers so existing imports from this file keep working
export { EditableCell, Toast };
