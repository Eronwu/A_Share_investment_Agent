"use client";

import { useState, useCallback, useEffect, useRef } from "react";

export interface PlanOrder {
  id: string;
  side: "buy" | "sell";
  op: ">=" | "<=";
  price: number;
  shares: number;
  volume_min: number | null;
  consecutive_days: number | null;
  trailing: { pct: number; watermark: number | null; active: boolean } | null;
  label: string;
  triggered: boolean;
  triggered_at: string | null;
}

export interface PlanPosition {
  cost: number;
  shares: number;
  price: number;
  change_pct: number;
  name: string;
}

export interface TradePlan {
  id: string;
  name: string;
  symbol: string;
  status: "active" | "paused";
  scope?: string;
  created_at: string;
  orders: PlanOrder[];
  position?: PlanPosition;
  lot_size?: number;
}

/** Plans grouped by stock symbol */
export type PlanMap = Record<string, TradePlan[]>;

export function useTradePlans(pollMs = 30000): {
  planMap: PlanMap;
  loading: boolean;
  refresh: () => void;
} {
  const [planMap, setPlanMap] = useState<PlanMap>({});
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPlans = useCallback(async () => {
    try {
      const res = await fetch("/api/trade-plans");
      if (!res.ok) return;
      const data = (await res.json()) as {
        plans: Record<string, Omit<TradePlan, "id">>;
      };
      if (!data?.plans) return;

      // Inject `id` from the record key, then group by symbol
      const map: PlanMap = {};
      for (const [planId, plan] of Object.entries(data.plans)) {
        const typed = plan as Omit<TradePlan, "id">;
        const withId: TradePlan = { ...typed, id: planId };
        const sym = withId.symbol;
        if (!map[sym]) map[sym] = [];
        map[sym].push(withId);
      }

      setPlanMap(map);
    } catch {
      // Network error — keep previous state, don't crash
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    void fetchPlans();
  }, [fetchPlans]);

  useEffect(() => {
    void fetchPlans();

    intervalRef.current = setInterval(() => {
      void fetchPlans();
    }, pollMs);

    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [fetchPlans, pollMs]);

  return { planMap, loading, refresh };
}
