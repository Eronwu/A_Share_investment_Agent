import { useEffect, useRef } from "react";
import type { LogEntry } from "./useCommand";

/**
 * useAlerts — 读取 notifier 写入的告警事件，展示在 web 终端日志区。
 *
 * 不做任何告警计算，只是 notifier 产出的消费者。
 * 主页只展示最新 5 条，完整历史在 /alerts 页面查看。
 */

interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  level?: number;
  message: string;   // stealth 格式（与 terminal 通知一致）
  display: string;   // 中文可读格式（web 日志展示用）
  change_pct: number;
}

const MAX_DISPLAY = 5;

export function useAlerts(
  alertEvents: AlertEvent[],
  addLogs: (entries: LogEntry[]) => void,
) {
  const lastSeenTs = useRef<number>(0);
  const displayedCount = useRef<number>(0);

  useEffect(() => {
    if (!alertEvents || alertEvents.length === 0) {
      lastSeenTs.current = 0;
      displayedCount.current = 0;
      return;
    }

    const newEvents = alertEvents.filter((e) => e.ts > lastSeenTs.current);
    if (newEvents.length === 0) return;

    lastSeenTs.current = Math.max(...newEvents.map((e) => e.ts));

    // 主页只展示最新 MAX_DISPLAY 条
    const toShow = newEvents.slice(-MAX_DISPLAY);
    const entries: LogEntry[] = toShow.map((e) => ({
      time: e.time,
      level: ((e.level ?? 2) <= 1 ? "error" : "warn") as "error" | "warn",
      source: "alert",
      message: `[L${e.level ?? 2}] ${e.display || e.message}`,
    }));

    // 如果有更多被截断的，加一条提示
    const totalNew = newEvents.length;
    if (totalNew > MAX_DISPLAY) {
      entries.unshift({
        time: toShow[0]?.time || "",
        level: "info" as const,
        source: "alert",
        message: `... ${totalNew - MAX_DISPLAY} more alerts, see /alerts for full log`,
      });
    }

    displayedCount.current += toShow.length;
    addLogs(entries);
  }, [alertEvents, addLogs]);
}
