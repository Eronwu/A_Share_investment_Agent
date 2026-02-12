"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { getCompletions } from "../utils/commandParser";
import type { LogEntry } from "../hooks/useCommand";
import { D } from "../theme";

const LOG_COLORS: Record<LogEntry["level"], string> = {
  info: D.green,
  ok: D.cyan,
  error: D.red,
  warn: D.yellow,
};

interface CommandState {
  logs: LogEntry[];
  execute: (input: string) => Promise<void>;
  historyUp: () => string;
  historyDown: () => string;
  clearLogs: () => void;
  addLogs: (entries: LogEntry[]) => void;
}

interface CommandPromptProps {
  cmd: CommandState;
}

export default function CommandPrompt({ cmd }: CommandPromptProps) {
  const { logs, execute, historyUp, historyDown, clearLogs } = cmd;
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // auto-focus on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // scroll logs to bottom
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // click anywhere on prompt area to focus
  const focusInput = useCallback(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const cmd = value.trim();
        if (cmd) {
          if (cmd === "clear") {
            clearLogs();
          } else {
            execute(cmd);
          }
        }
        setValue("");
        return;
      }

      if (e.key === "ArrowUp") {
        e.preventDefault();
        setValue(historyUp());
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setValue(historyDown());
        return;
      }

      if (e.key === "Tab") {
        e.preventDefault();
        const completions = getCompletions(value);
        if (completions.length === 1) {
          setValue(completions[0]);
        }
        return;
      }
    },
    [value, execute, historyUp, historyDown, clearLogs]
  );

  return (
    <div>
      {/* Command output logs */}
      {logs.length > 0 && (
        <div style={{ marginBottom: 4 }}>
          {[...logs].reverse().map((log, i) => (
            <div key={i} style={{ fontSize: 12, color: D.comment }}>
              <span>[{log.time}] </span>
              <span style={{ color: LOG_COLORS[log.level] }}>
                {log.level.padEnd(5)}
              </span>
              <span> {log.source}: </span>
              <span style={{ color: log.level === "error" ? D.red : log.level === "ok" ? D.fg : D.comment }}>
                {log.message}
              </span>
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      )}

      {/* Prompt line — always active */}
      <div
        style={{ display: "flex", alignItems: "center", cursor: "text" }}
        onClick={focusInput}
      >
        <span style={{ color: D.green, flexShrink: 0 }}>➜ </span>
        <span style={{ color: D.cyan, flexShrink: 0 }}>~/projects/monitor</span>
        <span style={{ color: D.purple, flexShrink: 0 }}> git:(</span>
        <span style={{ color: D.red, flexShrink: 0 }}>main</span>
        <span style={{ color: D.purple, flexShrink: 0 }}>) </span>

        <div style={{ flex: 1, position: "relative", minHeight: 20 }}>
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            style={{
              width: "100%",
              background: "transparent",
              border: "none",
              outline: "none",
              color: D.fg,
              font: "inherit",
              fontSize: "inherit",
              lineHeight: "inherit",
              padding: 0,
              margin: 0,
              caretColor: "transparent",
            }}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
          />
          {/* fake cursor overlay */}
          <span
            style={{
              position: "absolute",
              top: 2,
              left: `${value.length}ch`,
              display: "inline-block",
              width: 8,
              height: 15,
              background: D.fg,
              animation: "blink 1s step-end infinite",
              pointerEvents: "none",
            }}
          />
        </div>
      </div>
    </div>
  );
}
