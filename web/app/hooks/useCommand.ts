import { useState, useCallback } from "react";

export interface LogEntry {
  time: string;
  level: "info" | "ok" | "error" | "warn";
  source: string;
  message: string;
}

export function useLogEntries() {
  const [logs, setLogs] = useState<LogEntry[]>([]);

  const addLogs = useCallback((entries: LogEntry[]) => {
    setLogs((prev) => [...prev, ...entries].slice(-100));
  }, []);

  const clearLogs = useCallback(() => setLogs([]), []);

  return { logs, addLogs, clearLogs };
}
