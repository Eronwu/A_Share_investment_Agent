"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import CommandPrompt from "./components/CommandPrompt";
import { useAlerts } from "./hooks/useAlerts";
import { useCommand } from "./hooks/useCommand";

import type { Service, AlertSettings } from "./types";

const DEFAULT_POLL_SEC = 30;

import { D } from "./theme";

function chgColor(v: number) {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

function pad(s: string, n: number, right = false): string {
  return right ? s.padStart(n) : s.padEnd(n);
}

function fmtAmt(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(1) + "亿";
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0) + "万";
  return n.toFixed(0);
}

function fmtMoney(n: number): string {
  const sign = n >= 0 ? "+" : "";
  const abs = Math.abs(n);
  if (abs >= 1e4) return `${sign}${(n / 1e4).toFixed(1)}万`;
  return `${sign}${Math.round(n).toLocaleString("en-US")}`;
}

/* ── macOS Title Bar ── */
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
        borderBottom: `1px solid #191a21`,
        userSelect: "none",
      }}
    >
      <div style={{ position: "absolute", left: 12, display: "flex", gap: 8 }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
          <span
            key={c}
            style={{
              width: 12, height: 12, borderRadius: "50%",
              background: c, display: "inline-block",
            }}
          />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>
        ✱ monitor (node)
      </span>
    </div>
  );
}

/* ── iTerm2 Tab Bar ── */
type MarketTab = "A" | "HK";

function TabBar({ activeTab, onTabChange }: { activeTab: MarketTab; onTabChange: (t: MarketTab) => void }) {
  const tabs: { label: string; key: MarketTab | null }[] = [
    { label: "Claude Code (node)", key: null },
    { label: "A-share (node)", key: "A" },
    { label: "HK (node)", key: "HK" },
    { label: "~ (-zsh)", key: null },
  ];
  return (
    <div
      style={{
        display: "flex",
        background: "#21222c",
        borderBottom: `1px solid #191a21`,
        fontSize: 11,
        userSelect: "none",
      }}
    >
      {tabs.map((t, i) => {
        const isActive = t.key !== null && t.key === activeTab;
        const clickable = t.key !== null;
        return (
          <div
            key={i}
            onClick={() => clickable && onTabChange(t.key!)}
            style={{
              padding: "5px 16px",
              background: isActive ? D.bg : "#21222c",
              color: isActive ? D.fg : D.comment,
              borderRight: "1px solid #191a21",
              borderTop: isActive
                ? `2px solid ${D.purple}`
                : "2px solid transparent",
              display: "flex",
              alignItems: "center",
              gap: 6,
              minWidth: 130,
              cursor: clickable ? "pointer" : "default",
            }}
          >
            <span style={{ fontSize: 8, color: isActive ? D.green : "#555" }}>
              {isActive ? "✱" : "●"}
            </span>
            <span>{t.label}</span>
            <span style={{ marginLeft: "auto", color: D.comment, fontSize: 10 }}>
              ⌘{i + 1}
            </span>
          </div>
        );
      })}
      <div style={{ flex: 1, background: "#21222c" }} />
      <Link href="/alerts" style={{ padding: "5px 10px", color: D.comment, background: "#21222c", textDecoration: "none", fontSize: 11 }}>
        alerts
      </Link>
      <Link href="/manage" style={{ padding: "5px 10px", color: D.comment, background: "#21222c", textDecoration: "none", fontSize: 11 }}>
        manage
      </Link>
      <Link href="/sim" style={{ padding: "5px 10px", color: D.comment, background: "#21222c", textDecoration: "none", fontSize: 11 }}>
        sim
      </Link>
    </div>
  );
}

/* ── Prompt ── */
function Prompt({ cmd }: { cmd: string }) {
  return (
    <div>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/monitor</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>{cmd}</span>
    </div>
  );
}

/* ── Main ── */
export default function Page() {
  return (
    <Suspense>
      <Home />
    </Suspense>
  );
}

function Home() {
  const [services, setServices] = useState<Service[]>([]);
  const [ts, setTs] = useState(0);
  const [tick, setTick] = useState(0);
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<AlertSettings>({});
  const [hkdCnyRate, setHkdCnyRate] = useState<number | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  // tab state: URL ?tab=A|HK, default by time (before 15:00 → A, after → HK)
  const searchParams = useSearchParams();

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }

  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);

  function switchTab(tab: MarketTab) {
    setActiveTab(tab);
    window.history.replaceState(null, "", `/?tab=${tab}`);
  }
  const [prodStockOpen, setProdStockOpen] = useState(true);
  const [prodETFOpen, setProdETFOpen] = useState(true);
  const [stageStockOpen, setStageStockOpen] = useState(true);
  const [stageETFOpen, setStageETFOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch("/api/metrics", { cache: "no-store" });
      const data = await resp.json();
      if (data.error) {
        setFetchError(data.error);
        // preserve old services/ts — don't overwrite with empty
      } else {
        setFetchError(null);
        setServices(data.services || []);
        setTs(data.ts || Date.now());
        if (data.settings) setSettings(data.settings);
        if (data.hkdCnyRate != null) setHkdCnyRate(data.hkdCnyRate);
        if (data.alertEvents) setAlertEvents(data.alertEvents);
        if (data.marketTurnover) setMarketTurnover(data.marketTurnover);
      }
      setTick((t) => t + 1);
    } catch (e) {
      setFetchError(`network error: ${e}`);
      // preserve old data
    } finally {
      setLoading(false);
    }
  }, []);

  const [alertEvents, setAlertEvents] = useState<unknown[]>([]);
  const [marketTurnover, setMarketTurnover] = useState<{
    sh: number; sz: number; total: number;
    shIndex: number; szIndex: number; shPct: number; szPct: number;
    verdict: string;
  } | null>(null);
  const cmd = useCommand(fetchData);
  // Filter alerts by active market tab (HK symbols start with "HK", rest are A-share)
  // Portfolio-level alerts (empty symbol) show in both tabs
  const tabAlertEvents = (alertEvents as Parameters<typeof useAlerts>[0]).filter(
    (e) => !e.symbol || (activeTab === "HK" ? e.symbol.startsWith("HK") : !e.symbol.startsWith("HK"))
  );
  useAlerts(tabAlertEvents, cmd.addLogs, activeTab);

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, pollMs);
    return () => clearInterval(timer);
  }, [fetchData, pollMs]);

  // 持仓和自选独立排序状态，支持虚拟字段 mktVal / totalPnl / dayPnl
  type SortKey = keyof Service | "mktVal" | "totalPnl" | "dayPnl";
  type SortState = { key: SortKey | null; asc: boolean };
  const [holdSort, setHoldSort] = useState<SortState>({ key: null, asc: false });
  const [watchSort, setWatchSort] = useState<SortState>({ key: null, asc: false });

  function toggleHoldSort(key: SortKey) {
    setHoldSort((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }

  function toggleWatchSort(key: SortKey) {
    setWatchSort((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }

  function derivedVal(s: Service, key: SortKey): number {
    if (key === "mktVal") return s.shares != null ? s.price * s.shares : -Infinity;
    if (key === "totalPnl") return s.cost != null && s.shares != null ? (s.price - s.cost) * s.shares : -Infinity;
    if (key === "dayPnl") return s.shares != null ? s.chgAmt * s.shares : -Infinity;
    return (s[key as keyof Service] as number) ?? -Infinity;
  }

  function applySortList(list: Service[], st: SortState): Service[] {
    // star 置顶，然后按排序键
    const starFirst = (a: Service, b: Service) => (b.star ? 1 : 0) - (a.star ? 1 : 0);
    if (!st.key) return list.slice().sort((a, b) => starFirst(a, b) || b.change - a.change);
    return list.slice().sort((a, b) => {
      const sf = starFirst(a, b);
      if (sf !== 0) return sf;
      const av = derivedVal(a, st.key!);
      const bv = derivedVal(b, st.key!);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return st.asc ? cmp : -cmp;
    });
  }

  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);
  const tabServices = services.filter((s) => inTab(s));
  const prodStock = applySortList(tabServices.filter((s) => s.type === "holding" && !s.hidden && !isETF(s)), holdSort);
  const prodETF = applySortList(tabServices.filter((s) => s.type === "holding" && !s.hidden && isETF(s)), holdSort);
  const stageStock = applySortList(tabServices.filter((s) => s.type !== "holding" && !s.hidden && !isETF(s)), watchSort);
  const stageETF = applySortList(tabServices.filter((s) => s.type !== "holding" && !s.hidden && isETF(s)), watchSort);
  const hiddenList = applySortList(services.filter((s) => s.hidden), holdSort);
  const hasHold = prodStock.length > 0 || prodETF.length > 0;

  const now = ts
    ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })
    : "--:--:--";
  const isStale = (ts > 0 && Date.now() - ts > pollMs * 3) || fetchError !== null;

  const FALLBACK_HKD_CNY = 0.92;
  const fxRate = hkdCnyRate ?? FALLBACK_HKD_CNY;

  // ── 当前 Tab 统计 ──
  const tabUp = tabServices.filter((s) => s.change > 0).length;
  const tabDn = tabServices.filter((s) => s.change < 0).length;
  const tabAmt = tabServices.reduce((a, s) => a + s.amount, 0);
  const tabAvgChg = tabServices.length > 0
    ? tabServices.reduce((a, s) => a + s.change, 0) / tabServices.length : 0;
  const tabHoldCount = tabServices.filter((s) => s.type === "holding" && !s.hidden).length;

  // ── 当前 Tab P&L ──
  const tabHoldings = tabServices.filter((s) => s.type === "holding" && s.pnl !== null && s.cost && s.shares);
  const tabFx = activeTab === "HK" ? fxRate : 1;
  const tabPnl = tabHoldings.reduce((sum, s) => sum + (s.price - s.cost!) * s.shares! * tabFx, 0);
  const tabTodayPnl = tabHoldings.reduce((sum, s) => sum + s.chgAmt * s.shares! * tabFx, 0);
  const tabPosition = tabHoldings.reduce((sum, s) => sum + s.price * s.shares! * tabFx, 0);
  const tabCostBasis = tabHoldings.reduce((sum, s) => sum + s.cost! * s.shares! * tabFx, 0);
  const tabReturnPct = tabCostBasis > 0 ? (tabPnl / tabCostBasis) * 100 : 0;

  /* ── sort header helpers (per-section) ── */
  const mkArrow = (st: SortState) => (k: SortKey) =>
    st.key === k ? (st.asc ? " ▲" : " ▼") : "";
  const mkHStyle = (st: SortState) => (
    w: string,
    k: SortKey | null,
    right = false,
  ): React.CSSProperties => ({
    width: w,
    textAlign: right ? "right" : "left",
    cursor: k ? "pointer" : "default",
    userSelect: "none",
    color: k && st.key === k ? D.yellow : D.pink,
  });

  /* ── Holdings Row ── */
  function HoldRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const pnlPctStr = s.pnl !== null ? `${s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(1)}%` : "-";
    const rowFx = s.id.startsWith("HK") ? fxRate : 1;
    const mktVal = s.shares != null ? s.price * s.shares * rowFx : null;
    const totalPnlRaw = s.cost != null && s.cost > 0 && s.shares != null ? (s.price - s.cost) * s.shares * rowFx : null;
    const dayPnl = s.shares != null ? s.chgAmt * s.shares * rowFx : null;
    // Near alert threshold indicator
    const nearAlert =
      (s.above && s.price > 0 && (s.above - s.price) / s.price < 0.03) ||
      (s.below && s.price > 0 && (s.price - s.below) / s.price < 0.03);

    return (
      <div
        style={{
          display: "flex",
          whiteSpace: "pre",
          padding: "1px 0",
          borderBottom: `1px solid #191a21`,
          background: nearAlert ? "#44475a33" : "transparent",
        }}
      >
        <span style={{ color: D.orange, width: "6ch" }}>{s.star ? "★" : " "}PROD</span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad(s.name.slice(0, 6), 8)}</span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.cost != null ? s.cost.toFixed(2) : "-", 8, true)}
        </span>
        <span style={{ color: s.pnl !== null ? chgColor(s.pnl) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(pnlPctStr, 9, true)}
        </span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(mktVal != null ? fmtAmt(mktVal) : "-", 9, true)}
        </span>
        <span style={{ color: totalPnlRaw !== null ? chgColor(totalPnlRaw) : D.comment, width: "10ch", textAlign: "right", fontWeight: 500 }}>
          {pad(totalPnlRaw !== null ? fmtMoney(totalPnlRaw) : "-", 9, true)}
        </span>
        <span style={{ color: dayPnl != null ? chgColor(dayPnl) : D.comment, width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(dayPnl != null ? fmtMoney(dayPnl) : "-", 8, true)}
        </span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio.toFixed(2), 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover.toFixed(2), 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
        <span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
        </span>
        <span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
          {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
        </span>
      </div>
    );
  }

  /* ── Watching Row (different column set) ── */
  function WatchRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";

    return (
      <div
        style={{
          display: "flex",
          whiteSpace: "pre",
          padding: "1px 0",
          borderBottom: `1px solid #191a21`,
        }}
      >
        <span style={{ color: s.star ? D.yellow : D.comment, width: "6ch" }}>{s.star ? "★" : " "} DEV</span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad(s.name.slice(0, 6), 8)}</span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>
          {pad(s.price.toFixed(2), 9, true)}
        </span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>
          {pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}
        </span>
        <span style={{ color: chgColor(s.chgAmt), width: "8ch", textAlign: "right" }}>
          {pad(`${csign}${s.chgAmt.toFixed(2)}`, 7, true)}
        </span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio.toFixed(2), 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover.toFixed(2), 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>
          {pad(fmtAmt(s.amount), 8, true)}
        </span>
        <span style={{ color: D.comment, width: "13ch", textAlign: "right" }}>
          {pad(`${s.low.toFixed(2)}-${s.high.toFixed(2)}`, 12, true)}
        </span>
        <span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
          {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
        </span>
        <span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
          {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
      }}
    >
      <TitleBar />
      <TabBar activeTab={activeTab} onTabChange={switchTab} />

      {/* Terminal body */}
      <div
        style={{
          flex: 1,
          padding: "10px 16px",
          overflow: "auto",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <Prompt cmd={`watch -n ${pollMs / 1000} ./svc-monitor --format table`} />
        <div style={{ height: 8 }} />

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.green }}>info</span> Loading metrics
            <span style={{ animation: "blink 1s step-end infinite" }}>...</span>
          </div>
        )}

        {!loading && (<>
        {/* watch header */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span>Every {pollMs / 1000}.0s: svc-monitor --format table</span>
          <span style={{ float: "right" }}>
            {isStale && (
              <span style={{ color: D.red, fontWeight: 500, marginRight: 8 }}>STALE</span>
            )}
            devbox: <span style={{ color: isStale ? D.red : D.comment }}>{now}</span> &nbsp; refresh #{tick}
          </span>
        </div>

        {/* error banner */}
        {fetchError && (
          <div style={{ color: D.red, marginBottom: 6, fontWeight: 500 }}>
            [ERROR] metrics fetch failed: {fetchError}
            {ts > 0 && (
              <span style={{ color: D.comment, fontWeight: 400 }}>
                {" "}— showing stale data (last update: {new Date(ts).toLocaleTimeString("zh-CN", { hour12: false })})
              </span>
            )}
          </div>
        )}

        {/* summary bar — current tab */}
        <div style={{ color: D.comment, marginBottom: 6 }}>
          <span style={{ color: D.fg }}>
            Nodes: <span style={{ color: D.purple }}>{tabServices.length}</span>
          </span>
          {"  "}
          holdings:<span style={{ color: D.orange }}>{tabHoldCount}</span>
          {tabHoldings.length > tabHoldCount && (
            <span style={{ color: D.comment, fontSize: 11 }}>(+{tabHoldings.length - tabHoldCount} hidden)</span>
          )}
          {"  "}
          up:<span style={{ color: D.red }}>{tabUp}</span>
          {" "}down:<span style={{ color: D.green }}>{tabDn}</span>
          {"  "}
          throughput:<span style={{ color: D.fg }}>{fmtAmt(tabAmt)}</span>
          {"  "}
          avg_delta:
          <span style={{ color: chgColor(tabAvgChg) }}>
            {tabAvgChg >= 0 ? "+" : ""}{tabAvgChg.toFixed(2)}%
          </span>
          {"  "}alerts:<span style={{ color: isStale ? D.red : D.green }}>{isStale ? "stale" : "on"}</span>
        </div>
        {/* market turnover — A-share tab only */}
        {activeTab === "A" && marketTurnover && (
          <div style={{ color: D.comment, marginBottom: 6 }}>
            SH:<span style={{ color: D.fg }}>{marketTurnover.shIndex.toFixed(0)}</span>
            <span style={{ color: chgColor(marketTurnover.shPct) }}>{` ${marketTurnover.shPct >= 0 ? "+" : ""}${marketTurnover.shPct.toFixed(2)}%`}</span>
            {"  "}
            SZ:<span style={{ color: D.fg }}>{marketTurnover.szIndex.toFixed(0)}</span>
            <span style={{ color: chgColor(marketTurnover.szPct) }}>{` ${marketTurnover.szPct >= 0 ? "+" : ""}${marketTurnover.szPct.toFixed(2)}%`}</span>
            {"  "}
            vol:<span style={{ color: marketTurnover.total >= 15000 ? D.red : marketTurnover.total <= 8000 ? D.green : D.fg }}>
              {(marketTurnover.total / 10000).toFixed(2)}万亿
            </span>
            <span style={{ color: D.comment }}>{` (${
              {extreme_high:"天量",high:"放量",above_avg:"偏强",normal:"正常",below_avg:"偏弱",low:"缩量",extreme_low:"地量"}[marketTurnover.verdict] || ""
            })`}</span>
          </div>
        )}
        {/* tab portfolio summary */}
        {tabHoldings.length > 0 && (
          <div style={{ color: D.comment, marginBottom: 6 }}>
            position:<span style={{ color: D.fg }}>{fmtMoney(tabPosition).replace("+", "")}</span>
            <span style={{ color: D.comment }}>¥</span>
            {"  "}
            yield:<span style={{ color: chgColor(tabPnl) }}>{fmtMoney(tabPnl)}</span>
            <span style={{ color: D.comment }}>¥</span>
            {"  "}
            return:<span style={{ color: chgColor(tabReturnPct) }}>{tabReturnPct >= 0 ? "+" : ""}{tabReturnPct.toFixed(1)}%</span>
            {"  "}
            today:<span style={{ color: chgColor(tabTodayPnl) }}>{fmtMoney(tabTodayPnl)}</span>
            <span style={{ color: D.comment }}>¥</span>
            {activeTab === "HK" && !hkdCnyRate && (
              <span style={{ color: D.yellow, fontSize: 11, fontWeight: 500 }}> [WARN FX unavailable, fallback≈{FALLBACK_HKD_CNY}]</span>
            )}
          </div>
        )}

        {/* ══ Section renderer ══ */}
        {(() => {
          const ha = mkArrow(holdSort);
          const hs = mkHStyle(holdSort);
          const ht = toggleHoldSort;
          const wa = mkArrow(watchSort);
          const ws = mkHStyle(watchSort);
          const wt = toggleWatchSort;

          const holdHeader = (
            <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
              <span style={{ width: "6ch" }}> 类型</span>
              <span style={hs("10ch", "id")} onClick={() => ht("id")}>代码{ha("id")}</span>
              <span style={{ width: "10ch" }}>名称</span>
              <span style={hs("10ch", "price", true)} onClick={() => ht("price")}>{pad("现价" + ha("price"), 9, true)}</span>
              <span style={hs("9ch", "change", true)} onClick={() => ht("change")}>{pad("涨跌幅" + ha("change"), 8, true)}</span>
              <span style={hs("9ch", "cost", true)} onClick={() => ht("cost")}>{pad("成本" + ha("cost"), 8, true)}</span>
              <span style={hs("10ch", "pnl", true)} onClick={() => ht("pnl")}>{pad("盈亏%" + ha("pnl"), 9, true)}</span>
              <span style={hs("10ch", "mktVal", true)} onClick={() => ht("mktVal")}>{pad("市值" + ha("mktVal"), 9, true)}</span>
              <span style={hs("10ch", "totalPnl", true)} onClick={() => ht("totalPnl")}>{pad("盈亏额" + ha("totalPnl"), 9, true)}</span>
              <span style={hs("9ch", "dayPnl", true)} onClick={() => ht("dayPnl")}>{pad("今日" + ha("dayPnl"), 8, true)}</span>
              <span style={hs("7ch", "volRatio", true)} onClick={() => ht("volRatio")}>{pad("量比" + ha("volRatio"), 6, true)}</span>
              <span style={hs("8ch", "turnover", true)} onClick={() => ht("turnover")}>{pad("换手%" + ha("turnover"), 7, true)}</span>
              <span style={hs("9ch", "amount", true)} onClick={() => ht("amount")}>{pad("成交额" + ha("amount"), 8, true)}</span>
              <span style={hs("9ch", "mainNetInflow" as SortKey, true)} onClick={() => ht("mainNetInflow" as SortKey)}>{pad("主力" + ha("mainNetInflow" as SortKey), 8, true)}</span>
              <span style={hs("7ch", "mainNetInflowPct" as SortKey, true)} onClick={() => ht("mainNetInflowPct" as SortKey)}>{pad("主力%" + ha("mainNetInflowPct" as SortKey), 6, true)}</span>
            </div>
          );

          const watchHeader = (
            <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
              <span style={{ width: "6ch" }}> 类型</span>
              <span style={ws("10ch", "id")} onClick={() => wt("id")}>代码{wa("id")}</span>
              <span style={{ width: "10ch" }}>名称</span>
              <span style={ws("10ch", "price", true)} onClick={() => wt("price")}>{pad("现价" + wa("price"), 9, true)}</span>
              <span style={ws("9ch", "change", true)} onClick={() => wt("change")}>{pad("涨跌幅" + wa("change"), 8, true)}</span>
              <span style={ws("8ch", "chgAmt", true)} onClick={() => wt("chgAmt")}>{pad("涨跌" + wa("chgAmt"), 7, true)}</span>
              <span style={ws("7ch", "volRatio", true)} onClick={() => wt("volRatio")}>{pad("量比" + wa("volRatio"), 6, true)}</span>
              <span style={ws("8ch", "turnover", true)} onClick={() => wt("turnover")}>{pad("换手%" + wa("turnover"), 7, true)}</span>
              <span style={ws("9ch", "amount", true)} onClick={() => wt("amount")}>{pad("成交额" + wa("amount"), 8, true)}</span>
              <span style={{ width: "13ch", textAlign: "right", color: D.pink }}>{pad("高低", 12, true)}</span>
              <span style={ws("9ch", "mainNetInflow" as SortKey, true)} onClick={() => wt("mainNetInflow" as SortKey)}>{pad("主力" + wa("mainNetInflow" as SortKey), 8, true)}</span>
              <span style={ws("7ch", "mainNetInflowPct" as SortKey, true)} onClick={() => wt("mainNetInflowPct" as SortKey)}>{pad("主力%" + wa("mainNetInflowPct" as SortKey), 6, true)}</span>
            </div>
          );

          const secTitle = (
            label: string,
            count: number,
            open: boolean,
            toggle: (v: (prev: boolean) => boolean) => void,
            opacity = 1,
          ) => (
            <div
              style={{ color: D.comment, padding: "4px 0 1px", cursor: "pointer", userSelect: "none", opacity }}
              onClick={() => toggle((v) => !v)}
            >
              <span style={{ color: D.purple }}>{open ? "▾" : "▸"}</span>
              {" "}# ── {label} ({count}) ──
            </div>
          );

          return (
            <>
              {/* ── prod:stocks ── */}
              {prodStock.length > 0 && (
                <>
                  {secTitle("prod:stocks", prodStock.length, prodStockOpen, setProdStockOpen)}
                  {prodStockOpen && <>{holdHeader}{prodStock.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── prod:ETF ── */}
              {prodETF.length > 0 && (
                <>
                  {secTitle("prod:ETF", prodETF.length, prodETFOpen, setProdETFOpen)}
                  {prodETFOpen && <>{holdHeader}{prodETF.map((s) => <HoldRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── stage:stocks ── */}
              {stageStock.length > 0 && (
                <>
                  {secTitle("stage:stocks", stageStock.length, stageStockOpen, setStageStockOpen)}
                  {stageStockOpen && <>{watchHeader}{stageStock.map((s) => <WatchRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── stage:ETF ── */}
              {stageETF.length > 0 && (
                <>
                  {secTitle("stage:ETF", stageETF.length, stageETFOpen, setStageETFOpen)}
                  {stageETFOpen && <>{watchHeader}{stageETF.map((s) => <WatchRow key={s.id} s={s} />)}</>}
                </>
              )}

              {/* ── hidden ── */}
              {hiddenList.length > 0 && (
                <>
                  {secTitle("hidden", hiddenList.length, hiddenOpen, setHiddenOpen, 0.6)}
                  {hiddenOpen && (
                    <>
                      {hiddenList.map((s) =>
                        s.type === "holding"
                          ? <HoldRow key={s.id} s={s} />
                          : <WatchRow key={s.id} s={s} />
                      )}
                    </>
                  )}
                </>
              )}

              {prodStock.length === 0 && prodETF.length === 0 && stageStock.length === 0 && stageETF.length === 0 && hiddenList.length === 0 && (
                <div style={{ color: D.comment, padding: "8px 0" }}>
                  # no services in {activeTab === "HK" ? "HK" : "A-share"} tab
                </div>
              )}
            </>
          );
        })()}

        {/* log tail */}
        <div style={{ height: 16 }} />
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> metrics-collector: polled{" "}
          {services.length} endpoints ({(tick * 7 + 23) % 50 + 15}ms)
        </div>
        <div style={{ color: D.comment, fontSize: 12 }}>
          [{now}] <span style={{ color: D.green }}>info</span> scheduler: next poll in{" "}
          {pollMs / 1000}s
        </div>

        </>)}

        {/* interactive command prompt */}
        <div style={{ height: 10 }} />
        <CommandPrompt cmd={cmd} />
      </div>

      <style>{`
        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
