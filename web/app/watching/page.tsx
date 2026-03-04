"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { useAlerts } from "../hooks/useAlerts";
import { useLogEntries } from "../hooks/useCommand";
import type { Service, AlertSettings } from "../types";
import { tagColor } from "../lib/tag-utils";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";
import { MarketSwitch, type MarketTab } from "../components/MarketSwitch";

const DEFAULT_POLL_SEC = 30;

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

export default function WatchingPage() {
  return (
    <Suspense>
      <WatchingContent />
    </Suspense>
  );
}

function WatchingContent() {
  const searchParams = useSearchParams();
  const [services, setServices] = useState<Service[]>([]);
  const [ts, setTs] = useState(0);
  const [tick, setTick] = useState(0);
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<AlertSettings>({});
  const [fetchError, setFetchError] = useState<string | null>(null);

  function getDefaultTab(): MarketTab {
    const param = searchParams.get("tab")?.toUpperCase();
    if (param === "A" || param === "HK") return param;
    return new Date().getHours() < 15 ? "A" : "HK";
  }
  const [activeTab, setActiveTab] = useState<MarketTab>(getDefaultTab);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [watchStockOpen, setWatchStockOpen] = useState(true);
  const [watchETFOpen, setWatchETFOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);
  type SortKey = keyof Service;
  type SortState = { key: SortKey | null; asc: boolean };
  const [watchSort, setWatchSort] = useState<SortState>({ key: null, asc: false });
  const { logs, addLogs, clearLogs } = useLogEntries();
  const [alertEvents, setAlertEvents] = useState<unknown[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch("/api/metrics", { cache: "no-store" });
      const data = await resp.json();
      if (data.error) setFetchError(data.error);
      else {
        setFetchError(null);
        setServices(data.services || []);
        setTs(data.ts || Date.now());
        if (data.settings) setSettings(data.settings);
        if (data.alertEvents) setAlertEvents(data.alertEvents);
      }
      setTick((t) => t + 1);
    } catch (e) {
      setFetchError(`network error: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;
  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, pollMs);
    return () => clearInterval(timer);
  }, [fetchData, pollMs]);

  function switchTab(tab: MarketTab) {
    setActiveTab(tab);
    clearLogs();
    window.history.replaceState(null, "", `/watching?tab=${tab}`);
  }

  const tabAlertEvents = (alertEvents as Parameters<typeof useAlerts>[0]).filter(
    (e) => !e.symbol || (activeTab === "HK" ? e.symbol.startsWith("HK") : !e.symbol.startsWith("HK"))
  );
  useAlerts(tabAlertEvents, addLogs, activeTab);

  const showL2 = activeTab === "HK";
  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);
  function toggleWatchSort(key: SortKey) {
    setWatchSort((prev) =>
      prev.key === key ? { key, asc: !prev.asc } : { key, asc: false }
    );
  }
  function derivedVal(s: Service, key: SortKey): number {
    return (s[key] as number | null | undefined) ?? -Infinity;
  }
  function applySortList(list: Service[]): Service[] {
    if (!watchSort.key) return list.slice().sort((a, b) => b.change - a.change);
    return list.slice().sort((a, b) => {
      const av = derivedVal(a, watchSort.key!);
      const bv = derivedVal(b, watchSort.key!);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return watchSort.asc ? cmp : -cmp;
    });
  }
  const tabServices = services.filter((s) => inTab(s) && s.type !== "holding");
  const tagFiltered = filterTag
    ? tabServices.filter((s) => s.tags?.includes(filterTag))
    : tabServices;
  const watchStock = applySortList(tagFiltered.filter((s) => !s.hidden && !isETF(s)));
  const watchETF = applySortList(tagFiltered.filter((s) => !s.hidden && isETF(s)));
  const hiddenList = applySortList(tagFiltered.filter((s) => s.hidden));

  const now = ts ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false }) : "--:--:--";
  const isStale = (ts > 0 && Date.now() - ts > pollMs * 3) || fetchError !== null;

  const tagChipStyle = (tag: string): React.CSSProperties => ({
    display: "inline-block",
    padding: "1px 6px",
    borderRadius: 3,
    fontSize: 10,
    marginRight: 3,
    cursor: "pointer",
    color: "#282a36",
    background: tagColor(tag),
    whiteSpace: "nowrap",
  });

  function WatchRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";
    return (
      <div style={{ display: "flex", whiteSpace: "pre", padding: "1px 0", borderBottom: `1px solid #191a21` }}>
        <span style={{ color: s.star ? D.yellow : D.comment, width: "6ch" }}>{s.star ? "★" : " "} DEV</span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad(s.name.slice(0, 6), 8)}</span>
        <span style={{ width: "12ch", overflow: "hidden", whiteSpace: "nowrap" }}>
          {(s.tags ?? []).map((t) => (
            <span key={t} style={tagChipStyle(t)} onClick={() => setFilterTag(t)}>{t}</span>
          ))}
        </span>
        <span style={{ color: D.fg, width: "10ch", textAlign: "right" }}>{pad(s.price.toFixed(2), 9, true)}</span>
        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right", fontWeight: 500 }}>{pad(`${sign}${s.change.toFixed(2)}%`, 8, true)}</span>
        <span style={{ color: chgColor(s.chgAmt), width: "8ch", textAlign: "right" }}>{pad(`${csign}${s.chgAmt.toFixed(2)}`, 7, true)}</span>
        <span style={{ color: s.volRatio >= 1.5 ? D.red : s.volRatio <= 0.5 ? D.comment : D.fg, width: "7ch", textAlign: "right" }}>
          {pad(s.volRatio > 0 ? s.volRatio.toFixed(2) : "-", 6, true)}
        </span>
        <span style={{ color: s.turnover >= 5 ? D.red : D.fg, width: "8ch", textAlign: "right" }}>
          {pad(s.turnover > 0 ? s.turnover.toFixed(2) : "-", 7, true)}
        </span>
        <span style={{ color: D.comment, width: "9ch", textAlign: "right" }}>{pad(fmtAmt(s.amount), 8, true)}</span>
        {showL2 && (
          <span style={{ color: s.mainNetInflow != null ? chgColor(s.mainNetInflow) : D.comment, width: "9ch", textAlign: "right" }}>
            {pad(s.mainNetInflow != null ? fmtAmt(s.mainNetInflow) : "-", 8, true)}
          </span>
        )}
        {showL2 && (
          <span style={{ color: s.mainNetInflowPct != null ? chgColor(s.mainNetInflowPct) : D.comment, width: "7ch", textAlign: "right" }}>
            {s.mainNetInflowPct != null ? `${s.mainNetInflowPct >= 0 ? "+" : ""}${s.mainNetInflowPct.toFixed(1)}%` : pad("-", 6, true)}
          </span>
        )}
        <span style={{ color: D.comment, width: "13ch", textAlign: "right" }}>{pad(`${s.low.toFixed(2)}-${s.high.toFixed(2)}`, 12, true)}</span>
      </div>
    );
  }

  const mkArrow = (k: SortKey) => (watchSort.key === k ? (watchSort.asc ? " ▲" : " ▼") : "");
  const mkHStyle = (w: string, k: SortKey, right = false) => ({
    width: w,
    textAlign: right ? "right" as const : "left" as const,
    cursor: "pointer",
    userSelect: "none" as const,
    color: watchSort.key === k ? D.yellow : D.pink,
  });

  const header = (
    <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
      <span style={{ width: "6ch" }}> 类型</span>
      <span style={mkHStyle("10ch", "id")} onClick={() => toggleWatchSort("id")}>代码{mkArrow("id")}</span>
      <span style={{ width: "10ch" }}>名称</span>
      <span style={{ width: "12ch", color: D.pink }}>标签</span>
      <span style={mkHStyle("10ch", "price", true)} onClick={() => toggleWatchSort("price")}>{pad("现价" + mkArrow("price"), 9, true)}</span>
      <span style={mkHStyle("9ch", "change", true)} onClick={() => toggleWatchSort("change")}>{pad("涨跌幅" + mkArrow("change"), 8, true)}</span>
      <span style={mkHStyle("8ch", "chgAmt", true)} onClick={() => toggleWatchSort("chgAmt")}>{pad("涨跌" + mkArrow("chgAmt"), 7, true)}</span>
      <span style={mkHStyle("7ch", "volRatio", true)} onClick={() => toggleWatchSort("volRatio")}>{pad("量比" + mkArrow("volRatio"), 6, true)}</span>
      <span style={mkHStyle("8ch", "turnover", true)} onClick={() => toggleWatchSort("turnover")}>{pad("换手%" + mkArrow("turnover"), 7, true)}</span>
      <span style={mkHStyle("9ch", "amount", true)} onClick={() => toggleWatchSort("amount")}>{pad("成交额" + mkArrow("amount"), 8, true)}</span>
      {showL2 && <span style={mkHStyle("9ch", "mainNetInflow" as SortKey, true)} onClick={() => toggleWatchSort("mainNetInflow" as SortKey)}>{pad("主力" + mkArrow("mainNetInflow" as SortKey), 8, true)}</span>}
      {showL2 && <span style={mkHStyle("7ch", "mainNetInflowPct" as SortKey, true)} onClick={() => toggleWatchSort("mainNetInflowPct" as SortKey)}>{pad("主力%" + mkArrow("mainNetInflowPct" as SortKey), 6, true)}</span>}
      <span style={{ width: "13ch", textAlign: "right" }}>{pad("高低", 12, true)}</span>
    </div>
  );

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: D.bg }}>
      <AppTitleBar title="watching" />
      <AppTabs active="watching" />
      <div style={{ flex: 1, padding: "10px 16px", overflow: "auto", fontSize: 13, lineHeight: 1.55 }}>
        <div style={{ height: 8 }} />
        <MarketSwitch activeTab={activeTab} onTabChange={switchTab} />

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.green }}>info</span> Loading watching list...
          </div>
        )}

        {!loading && (
          <>
            {/* tag filter bar */}
            {filterTag && (
              <div style={{ padding: "4px 8px", backgroundColor: "#44475a", color: D.fg, fontSize: 12, marginBottom: 4, display: "flex", alignItems: "center", gap: 8, borderRadius: 3 }}>
                <span>筛选: <span style={{ color: tagColor(filterTag), fontWeight: 500 }}>{filterTag}</span></span>
                <span style={{ cursor: "pointer", color: D.red, fontWeight: 500 }} onClick={() => setFilterTag(null)}>x</span>
              </div>
            )}
            <div style={{ color: D.comment, marginBottom: 6 }}>
              <span>Every {pollMs / 1000}.0s: svc-monitor --watching</span>
              <span style={{ float: "right" }}>
                {isStale && <span style={{ color: D.red, fontWeight: 500, marginRight: 8 }}>STALE</span>}
                devbox: <span style={{ color: isStale ? D.red : D.comment }}>{now}</span> &nbsp; refresh #{tick}
              </span>
            </div>
            {fetchError && (
              <div style={{ color: D.red, marginBottom: 6, fontWeight: 500 }}>
                [ERROR] metrics fetch failed: {fetchError}
              </div>
            )}

            <div style={{ color: D.comment, marginBottom: 6 }}>
              Nodes: <span style={{ color: D.purple }}>{tabServices.length}</span>{"  "}
              visible:<span style={{ color: D.fg }}>{watchStock.length + watchETF.length}</span>{"  "}
              hidden:<span style={{ color: D.comment }}>{hiddenList.length}</span>
            </div>

            {watchStock.length > 0 && (
              <>
                <div style={{ color: D.comment, padding: "4px 0 1px", cursor: "pointer", userSelect: "none" }} onClick={() => setWatchStockOpen((v) => !v)}>
                  <span style={{ color: D.purple }}>{watchStockOpen ? "▾" : "▸"}</span> # ── watching:stocks ({watchStock.length}) ──
                </div>
                {watchStockOpen && <>{header}{watchStock.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {watchETF.length > 0 && (
              <>
                <div style={{ color: D.comment, padding: "4px 0 1px", cursor: "pointer", userSelect: "none" }} onClick={() => setWatchETFOpen((v) => !v)}>
                  <span style={{ color: D.purple }}>{watchETFOpen ? "▾" : "▸"}</span> # ── watching:ETF ({watchETF.length}) ──
                </div>
                {watchETFOpen && <>{header}{watchETF.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {hiddenList.length > 0 && (
              <>
                <div style={{ color: D.comment, opacity: 0.6, padding: "4px 0 1px", cursor: "pointer", userSelect: "none" }} onClick={() => setHiddenOpen((v) => !v)}>
                  <span style={{ color: D.purple }}>{hiddenOpen ? "▾" : "▸"}</span> # ── hidden ({hiddenList.length}) ──
                </div>
                {hiddenOpen && <>{header}{hiddenList.map((s) => <WatchRow key={s.id} s={s} />)}</>}
              </>
            )}

            {watchStock.length === 0 && watchETF.length === 0 && hiddenList.length === 0 && (
              <div style={{ color: D.comment, padding: "8px 0" }}>
                # no watching services in {activeTab === "HK" ? "HK" : "A-share"} tab
              </div>
            )}

            {logs.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {[...logs].reverse().slice(0, 8).reverse().map((log, i) => (
                  <div key={i} style={{ fontSize: 12, color: D.comment }}>
                    <span>[{log.time}] </span>
                    <span style={{ color: log.level === "error" ? D.red : log.level === "warn" ? D.yellow : D.green }}>
                      {log.level.padEnd(5)}
                    </span>
                    <span> {log.source}: </span>
                    <span style={{ color: log.level === "error" ? D.red : log.level === "warn" ? D.orange : D.comment }}>
                      {log.message}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
