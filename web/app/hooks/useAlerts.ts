import { useEffect, useRef } from "react";
import type { LogEntry } from "./useCommand";

interface AlertService {
  id: string;
  name: string;
  price: number;
  change: number;
  above: number | null;
  below: number | null;
}

interface AlertSettings {
  big_move_pct?: number;
  cooldown_minutes?: number;
}

type AlertKind = "above" | "below" | "big_move";

function now(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

export function useAlerts(
  services: AlertService[],
  settings: AlertSettings,
  addLogs: (entries: LogEntry[]) => void,
) {
  const cooldownMap = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (services.length === 0) return;

    const bigMovePct = settings.big_move_pct ?? 3;
    const cooldownMs = (settings.cooldown_minutes ?? 10) * 60 * 1000;
    const t = now();
    const entries: LogEntry[] = [];
    const nowMs = Date.now();

    for (const s of services) {
      const checks: { kind: AlertKind; msg: string }[] = [];

      if (s.above != null && s.price >= s.above) {
        checks.push({
          kind: "above",
          msg: `${s.id} ${s.name} \u4ef7\u683c\u7a81\u7834\u4e0a\u9650 ${s.above} \u2192 \u5f53\u524d ${s.price}`,
        });
      }

      if (s.below != null && s.price > 0 && s.price <= s.below) {
        checks.push({
          kind: "below",
          msg: `${s.id} ${s.name} \u4ef7\u683c\u8dcc\u7834\u4e0b\u9650 ${s.below} \u2192 \u5f53\u524d ${s.price}`,
        });
      }

      if (Math.abs(s.change) >= bigMovePct) {
        const dir = s.change > 0 ? "\u6da8\u5e45" : "\u8dcc\u5e45";
        checks.push({
          kind: "big_move",
          msg: `${s.id} ${s.name} ${dir} ${Math.abs(s.change).toFixed(1)}% \u8d85\u8fc7\u9608\u503c ${bigMovePct}%`,
        });
      }

      for (const { kind, msg } of checks) {
        const key = `${s.id}:${kind}`;
        const last = cooldownMap.current.get(key);
        if (last && nowMs - last < cooldownMs) continue;

        cooldownMap.current.set(key, nowMs);
        entries.push({ time: t, level: "warn", source: "alert", message: msg });
      }
    }

    if (entries.length > 0) {
      addLogs(entries);
    }
  }, [services, settings, addLogs]);
}
