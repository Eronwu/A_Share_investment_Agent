import { useState, useCallback, useRef } from "react";
import { parseCommand, type ParsedCommand } from "../utils/commandParser";

export interface LogEntry {
  time: string;
  level: "info" | "ok" | "error" | "warn";
  source: string;
  message: string;
}

const HISTORY_KEY = "svc-cmd-history";
const MAX_HISTORY = 50;

function now(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function loadHistory(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(history: string[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-MAX_HISTORY)));
}

function formatList(config: { watchlist: Record<string, Record<string, unknown>>; settings: Record<string, number>; alerts?: Record<string, Record<string, number>> }, filter?: string): LogEntry[] {
  const entries: LogEntry[] = [];
  const t = now();
  const alertRules = config.alerts || {};

  const items = Object.entries(config.watchlist).filter(([, v]) => {
    if (!filter) return true;
    const type = (v.type as string) || "watching";
    return type === filter;
  });

  if (items.length === 0) {
    entries.push({ time: t, level: "info", source: "config", message: "No entries found" });
    return entries;
  }

  const prodItems = items.filter(([, v]) => v.type === "holding");
  const devItems = items.filter(([, v]) => v.type !== "holding");

  if (prodItems.length > 0 && !filter) {
    entries.push({ time: t, level: "info", source: "config", message: `── PROD (${prodItems.length}) ──` });
    for (const [code, v] of prodItems) {
      const a = alertRules[code] || {};
      const parts = [`${code} ${v.name}`];
      if (v.cost != null) parts.push(`cost:${v.cost}`);
      if (v.shares != null) parts.push(`shares:${v.shares}`);
      if (a.above != null) parts.push(`above:${a.above}`);
      if (a.below != null) parts.push(`below:${a.below}`);
      entries.push({ time: t, level: "ok", source: "config", message: parts.join(" | ") });
    }
  }

  if (devItems.length > 0 && !filter) {
    entries.push({ time: t, level: "info", source: "config", message: `── DEV (${devItems.length}) ──` });
    for (const [code, v] of devItems) {
      const a = alertRules[code] || {};
      const parts = [`${code} ${v.name}`];
      if (a.above != null) parts.push(`above:${a.above}`);
      if (a.below != null) parts.push(`below:${a.below}`);
      entries.push({ time: t, level: "ok", source: "config", message: parts.join(" | ") });
    }
  }

  if (filter === "holding" && prodItems.length > 0) {
    for (const [code, v] of prodItems) {
      const parts = [`${code} ${v.name}`];
      if (v.cost != null) parts.push(`cost:${v.cost}`);
      if (v.shares != null) parts.push(`shares:${v.shares}`);
      entries.push({ time: t, level: "ok", source: "config", message: parts.join(" | ") });
    }
  }

  if (filter === "watching" && devItems.length > 0) {
    for (const [code, v] of devItems) {
      entries.push({ time: t, level: "ok", source: "config", message: `${code} ${v.name}` });
    }
  }

  entries.push({
    time: t,
    level: "info",
    source: "config",
    message: `settings: ${Object.entries(config.settings).map(([k, v]) => `${k}=${v}`).join(" ")}`,
  });

  return entries;
}

function helpEntries(): LogEntry[] {
  const t = now();
  return [
    { time: t, level: "info", source: "help", message: "svc add <code> [--env prod] [--cost N] [--shares N] [--above N] [--below N]" },
    { time: t, level: "info", source: "help", message: "svc update <code> [--cost N] [--shares N] [--above N] [--below N] [--env prod|dev]" },
    { time: t, level: "info", source: "help", message: "svc rm <code> [code2 ...]" },
    { time: t, level: "info", source: "help", message: "svc star <code>              -- mark as L1 priority (most frequent alerts)" },
    { time: t, level: "info", source: "help", message: "svc unstar <code>            -- remove L1 priority" },
    { time: t, level: "info", source: "help", message: "svc hide <code>              -- hide stock (out of sight, still counted)" },
    { time: t, level: "info", source: "help", message: "svc unhide <code>            -- restore hidden stock" },
    { time: t, level: "info", source: "help", message: "svc ls [prod|dev]" },
    { time: t, level: "info", source: "help", message: "svc config <key> <value>" },
    { time: t, level: "info", source: "help", message: "svc help" },
  ];
}

export function useCommand(onRefresh: () => void) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const historyRef = useRef<string[]>(loadHistory());
  const [historyIndex, setHistoryIndex] = useState(-1);

  const addLogs = useCallback((entries: LogEntry[]) => {
    setLogs((prev) => [...prev, ...entries].slice(-100));
  }, []);

  const execute = useCallback(
    async (input: string) => {
      const trimmed = input.trim();
      if (!trimmed) return;

      // save to history
      const hist = historyRef.current;
      if (hist[hist.length - 1] !== trimmed) {
        hist.push(trimmed);
        saveHistory(hist);
      }
      setHistoryIndex(-1);

      const parsed: ParsedCommand = parseCommand(trimmed);
      const t = now();

      // handle errors
      if (parsed.errors && parsed.errors.length > 0) {
        addLogs(parsed.errors.map((e) => ({ time: t, level: "error" as const, source: "parser", message: e })));
        return;
      }

      // help — local only
      if (parsed.action === "help") {
        addLogs(helpEntries());
        return;
      }

      // list — fetch config and display locally
      if (parsed.action === "list") {
        addLogs([{ time: t, level: "info", source: "config-reader", message: "fetching watchlist..." }]);
        try {
          const resp = await fetch("/api/config");
          const config = await resp.json();
          const filter = parsed.data?.filter as string | undefined;
          const entries = formatList(config, filter);
          addLogs(entries);
        } catch (e) {
          addLogs([{ time: t, level: "error", source: "config-reader", message: String(e) }]);
        }
        return;
      }

      // config
      if (parsed.action === "config") {
        addLogs([{ time: t, level: "info", source: "config-writer", message: "updating settings..." }]);
        try {
          const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "settings", settings: parsed.settings }),
          });
          const result = await resp.json();
          addLogs([{
            time: now(),
            level: result.success ? "ok" : "error",
            source: "config-writer",
            message: result.message,
          }]);
        } catch (e) {
          addLogs([{ time: now(), level: "error", source: "config-writer", message: String(e) }]);
        }
        return;
      }

      // add / update / remove — call API then refresh
      const apiAction = parsed.action;
      const apiBody: Record<string, unknown> = { action: apiAction };

      if (parsed.code) apiBody.code = parsed.code;
      if (parsed.codes) {
        apiBody.codes = parsed.codes;
        if (parsed.codes.length === 1) apiBody.code = parsed.codes[0];
      }
      if (parsed.data) apiBody.data = parsed.data;

      addLogs([{
        time: t,
        level: "info",
        source: "config-writer",
        message: `updating watchlist (${parsed.code || parsed.codes?.join(", ")})`,
      }]);

      try {
        const resp = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(apiBody),
        });
        const result = await resp.json();
        addLogs([{
          time: now(),
          level: result.success ? "ok" : "error",
          source: "config-writer",
          message: result.message,
        }]);

        if (result.success) {
          onRefresh();
        }
      } catch (e) {
        addLogs([{ time: now(), level: "error", source: "config-writer", message: String(e) }]);
      }
    },
    [addLogs, onRefresh]
  );

  const historyUp = useCallback((): string => {
    const hist = historyRef.current;
    if (hist.length === 0) return "";
    const newIdx = historyIndex === -1 ? hist.length - 1 : Math.max(0, historyIndex - 1);
    setHistoryIndex(newIdx);
    return hist[newIdx] || "";
  }, [historyIndex]);

  const historyDown = useCallback((): string => {
    const hist = historyRef.current;
    if (historyIndex === -1) return "";
    const newIdx = historyIndex + 1;
    if (newIdx >= hist.length) {
      setHistoryIndex(-1);
      return "";
    }
    setHistoryIndex(newIdx);
    return hist[newIdx] || "";
  }, [historyIndex]);

  const clearLogs = useCallback(() => setLogs([]), []);

  return { logs, execute, historyUp, historyDown, clearLogs, addLogs };
}
