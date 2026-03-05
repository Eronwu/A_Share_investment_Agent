"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import type { Service, AlertSettings, MarketTurnover, AlertEvent } from "../types";

const DEFAULT_POLL_SEC = 30;

interface MetricsContextValue {
  services: Service[];
  ts: number;
  tick: number;
  loading: boolean;
  settings: AlertSettings;
  hkdCnyRate: number | null;
  fetchError: string | null;
  alertEvents: AlertEvent[];
  marketTurnover: MarketTurnover | null;
}

const MetricsContext = createContext<MetricsContextValue | null>(null);

export function useMetrics(): MetricsContextValue {
  const ctx = useContext(MetricsContext);
  if (!ctx) throw new Error("useMetrics must be used within MetricsProvider");
  return ctx;
}

export function MetricsProvider({ children }: { children: React.ReactNode }) {
  const [services, setServices] = useState<Service[]>([]);
  const [ts, setTs] = useState(0);
  const [tick, setTick] = useState(0);
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<AlertSettings>({});
  const [hkdCnyRate, setHkdCnyRate] = useState<number | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [alertEvents, setAlertEvents] = useState<AlertEvent[]>([]);
  const [marketTurnover, setMarketTurnover] = useState<MarketTurnover | null>(null);

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

  const pollMs = (settings.poll_interval ?? DEFAULT_POLL_SEC) * 1000;
  const pollMsRef = useRef(pollMs);
  pollMsRef.current = pollMs;

  useEffect(() => {
    fetchData();
    const timer = setInterval(() => fetchData(), pollMs);
    return () => clearInterval(timer);
  }, [fetchData, pollMs]);

  return (
    <MetricsContext.Provider
      value={{ services, ts, tick, loading, settings, hkdCnyRate, fetchError, alertEvents, marketTurnover }}
    >
      {children}
    </MetricsContext.Provider>
  );
}
