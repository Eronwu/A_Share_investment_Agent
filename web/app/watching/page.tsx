"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAlerts } from "../hooks/useAlerts";
import { useLogEntries } from "../hooks/useCommand";
import type { Service, AlertSettings } from "../types";
import { D } from "../theme";

const DEFAULT_POLL_SEC = 30;
type MarketTab = "A" | "HK";

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
          <span key={c} style={{ width: 12, height: 12, borderRadius: "50%", background: c, display: "inline-block" }} />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>✱ watching (node)</span>
    </div>
  );
}

function TabBar() {
  return (
    <div style={{ display: "flex", background: "#21222c", borderBottom: "1px solid #191a21", fontSize: 11, userSelect: "none" }}>
      {[
        { label: "Claude Code (node)" },
        { label: "holdings (node)", href: "/" },
        { label: "watching (node)", active: true },
        { label: "Sector (node)", href: "/sector" },
        { label: "~ (-zsh)" },
      ].map((t, i) => {
        const inner = (
          <div
            key={i}
            style={{
              padding: "5px 16px",
              background: t.active ? D.bg : "#21222c",
              color: t.active ? D.fg : D.comment,
              borderRight: "1px solid #191a21",
              borderTop: t.active ? `2px solid ${D.purple}` : "2px solid transparent",
              minWidth: 130,
              cursor: t.href ? "pointer" : "default",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{ fontSize: 8, color: t.active ? D.green : "#555" }}>{t.active ? "✱" : "●"}</span>
            <span>{t.label}</span>
            <span style={{ marginLeft: "auto", color: D.comment, fontSize: 10 }}>⌘{i + 1}</span>
          </div>
        );
        return t.href
          ? <Link key={i} href={t.href} style={{ textDecoration: "none" }}>{inner}</Link>
          : inner;
      })}
      <div style={{ flex: 1 }} />
      <Link href="/" style={{ padding: "5px 10px", color: D.comment, textDecoration: "none", fontSize: 11 }}>monitor</Link>
      <Link href="/alerts" style={{ padding: "5px 10px", color: D.comment, textDecoration: "none", fontSize: 11 }}>alerts</Link>
      <Link href="/sim" style={{ padding: "5px 10px", color: D.comment, textDecoration: "none", fontSize: 11 }}>sim</Link>
      <Link href="/sector" style={{ padding: "5px 10px", color: D.comment, textDecoration: "none", fontSize: 11 }}>sector</Link>
      <Link href="/manage" style={{ padding: "5px 10px", color: D.comment, textDecoration: "none", fontSize: 11 }}>manage</Link>
    </div>
  );
}

function MarketSwitch({ activeTab, onTabChange }: { activeTab: MarketTab; onTabChange: (t: MarketTab) => void }) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
      <span style={{ color: D.comment, fontSize: 12 }}>market:</span>
      {(["A", "HK"] as MarketTab[]).map((t) => (
        <button
          key={t}
          onClick={() => onTabChange(t)}
          style={{
            background: activeTab === t ? D.purple : "transparent",
            color: activeTab === t ? D.bg : D.comment,
            border: `1px solid ${activeTab === t ? D.purple : D.currentLine}`,
            borderRadius: 3,
            padding: "2px 10px",
            cursor: "pointer",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 12,
            fontWeight: 700,
          }}
          title={`Switch to ${t === "A" ? "A-share" : "HK"} market`}
        >
          {t === "A" ? "A-share" : "HK"}
        </button>
      ))}
    </div>
  );
}

function Prompt() {
  return (
    <div>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/watching</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>watch -n 30 ./svc-monitor --watching</span>
    </div>
  );
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
  const [watchStockOpen, setWatchStockOpen] = useState(true);
  const [watchETFOpen, setWatchETFOpen] = useState(true);
  const [hiddenOpen, setHiddenOpen] = useState(false);
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

  const isHK = (s: Service) => s.id.startsWith("HK");
  const isETF = (s: Service) => !isHK(s) && /^(51|15|58)\d{4}$/.test(s.id);
  const inTab = (s: Service) => activeTab === "HK" ? isHK(s) : !isHK(s);
  const tabServices = services.filter((s) => inTab(s) && s.type !== "holding");
  const watchStock = tabServices.filter((s) => !s.hidden && !isETF(s)).sort((a, b) => b.change - a.change);
  const watchETF = tabServices.filter((s) => !s.hidden && isETF(s)).sort((a, b) => b.change - a.change);
  const hiddenList = tabServices.filter((s) => s.hidden).sort((a, b) => b.change - a.change);

  const now = ts ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false }) : "--:--:--";
  const isStale = (ts > 0 && Date.now() - ts > pollMs * 3) || fetchError !== null;

  function WatchRow({ s }: { s: Service }) {
    const sign = s.change > 0 ? "+" : "";
    const csign = s.chgAmt > 0 ? "+" : "";
    return (
      <div style={{ display: "flex", whiteSpace: "pre", padding: "1px 0", borderBottom: `1px solid #191a21` }}>
        <span style={{ color: s.star ? D.yellow : D.comment, width: "6ch" }}>{s.star ? "★" : " "} DEV</span>
        <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
        <span style={{ color: D.fg, width: "10ch" }}>{pad(s.name.slice(0, 6), 8)}</span>
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
        <span style={{ color: D.comment, width: "13ch", textAlign: "right" }}>{pad(`${s.low.toFixed(2)}-${s.high.toFixed(2)}`, 12, true)}</span>
      </div>
    );
  }

  const header = (
    <div style={{ display: "flex", whiteSpace: "pre", color: D.pink, borderBottom: `1px solid ${D.currentLine}`, paddingBottom: 3, marginBottom: 2, fontWeight: 500 }}>
      <span style={{ width: "6ch" }}> 类型</span>
      <span style={{ width: "10ch" }}>代码</span>
      <span style={{ width: "10ch" }}>名称</span>
      <span style={{ width: "10ch", textAlign: "right" }}>{pad("现价", 9, true)}</span>
      <span style={{ width: "9ch", textAlign: "right" }}>{pad("涨跌幅", 8, true)}</span>
      <span style={{ width: "8ch", textAlign: "right" }}>{pad("涨跌", 7, true)}</span>
      <span style={{ width: "7ch", textAlign: "right" }}>{pad("量比", 6, true)}</span>
      <span style={{ width: "8ch", textAlign: "right" }}>{pad("换手%", 7, true)}</span>
      <span style={{ width: "9ch", textAlign: "right" }}>{pad("成交额", 8, true)}</span>
      <span style={{ width: "13ch", textAlign: "right" }}>{pad("高低", 12, true)}</span>
    </div>
  );

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: D.bg }}>
      <TitleBar />
      <TabBar />
      <div style={{ flex: 1, padding: "10px 16px", overflow: "auto", fontSize: 13, lineHeight: 1.55 }}>
        <Prompt />
        <div style={{ height: 8 }} />
        <MarketSwitch activeTab={activeTab} onTabChange={switchTab} />

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.green }}>info</span> Loading watching list...
          </div>
        )}

        {!loading && (
          <>
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
