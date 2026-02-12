import { useEffect, useRef } from "react";
import type { LogEntry } from "./useCommand";

/**
 * useAlerts — 读取 notifier 写入的告警事件，展示在 web 终端日志区。
 *
 * 不做任何告警计算，只是 notifier 产出的消费者。
 * 确保 terminal 弹窗和 web 日志完全一致。
 */

interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  message: string;   // stealth 格式（与 terminal 通知一致）
  display: string;   // 中文可读格式（web 日志展示用）
  change_pct: number;
}

export function useAlerts(
  alertEvents: AlertEvent[],
  addLogs: (entries: LogEntry[]) => void,
) {
  // 记录已展示过的最新事件时间戳，避免重复
  const lastSeenTs = useRef<number>(0);

  useEffect(() => {
    if (!alertEvents || alertEvents.length === 0) {
      // 事件被清空（每日重置），重置水位线
      lastSeenTs.current = 0;
      return;
    }

    // 只展示比上次更新的事件
    const newEvents = alertEvents.filter((e) => e.ts > lastSeenTs.current);
    if (newEvents.length === 0) return;

    // 更新水位线
    lastSeenTs.current = Math.max(...newEvents.map((e) => e.ts));

    const entries: LogEntry[] = newEvents.map((e) => ({
      time: e.time,
      level: "warn" as const,
      source: "alert",
      message: e.display || e.message,
    }));

    addLogs(entries);
  }, [alertEvents, addLogs]);
}
