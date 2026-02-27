"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { D } from "../../theme";

/* ── Types ── */

interface RotationCell {
  board: string;
  change: number;
}

interface RotationRow {
  rank: number;
  cells: RotationCell[];
}

interface BoardStock {
  code: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  volume: number;
  amount: number;
}

interface BoardDetail {
  name: string;
  top10Count: number;
  rankHistory: Array<{ date: string; rank: number }>;
  stocks: BoardStock[];
}

/* ── Helpers ── */

function chgColor(v: number): string {
  return v > 0 ? D.red : v < 0 ? D.green : D.comment;
}

function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function shortDate(d: string): string {
  return d.slice(5);
}

/* ── Rank badge ── */
function RankBadge({ rank }: { rank: number }) {
  let bg: string = "transparent";
  let fg: string = D.comment;
  if (rank === 1) { bg = D.red; fg = "#fff"; }
  else if (rank === 2) { bg = D.orange; fg = D.bg; }
  else if (rank === 3) { bg = D.yellow; fg = D.bg; }
  return (
    <span
      style={{
        display: "inline-block",
        width: 24,
        textAlign: "center",
        borderRadius: 3,
        fontWeight: 700,
        fontSize: 12,
        background: bg,
        color: fg,
        padding: "1px 0",
      }}
    >
      {rank}
    </span>
  );
}

/* ── Reusable UI ── */

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
          <span
            key={c}
            style={{
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: c,
              display: "inline-block",
            }}
          />
        ))}
      </div>
      <span style={{ color: D.comment, fontSize: 12 }}>
        ✱ sector — rotation
      </span>
    </div>
  );
}

function Prompt({ cmd }: { cmd: string }) {
  return (
    <div>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sector</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span style={{ color: D.fg }}>{cmd}</span>
    </div>
  );
}

function BlinkCursor() {
  return (
    <div style={{ paddingTop: 16 }}>
      <span style={{ color: D.green }}>➜ </span>
      <span style={{ color: D.cyan }}>~/projects/sector</span>
      <span style={{ color: D.purple }}> git:(</span>
      <span style={{ color: D.red }}>main</span>
      <span style={{ color: D.purple }}>) </span>
      <span
        style={{
          display: "inline-block",
          width: 8,
          height: 15,
          background: D.fg,
          verticalAlign: "text-bottom",
          animation: "blink 1s step-end infinite",
        }}
      />
    </div>
  );
}

/* ── Main Page ── */

export default function RotationPage() {
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // filter state
  const [category, setCategory] = useState("industry");
  const [sort, setSort] = useState("change_pct");
  const [topN, setTopN] = useState(10);

  // rotation data
  const [dates, setDates] = useState<string[]>([]);
  const [rows, setRows] = useState<RotationRow[]>([]);

  // board detail
  const [selectedBoard, setSelectedBoard] = useState<string | null>(null);
  const [boardDetail, setBoardDetail] = useState<BoardDetail | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        category,
        sort,
        top_n: String(topN),
      });
      if (selectedBoard) params.set("board", selectedBoard);

      const resp = await fetch(`/api/sector?${params}`, { cache: "no-store" });
      const json = await resp.json();
      if (json.error && !json.rotation) {
        setFetchError(json.error);
      } else {
        setDates(json.rotation?.dates ?? []);
        setRows(json.rotation?.rows ?? []);
        setBoardDetail(json.boardDetail ?? null);
        setFetchError(json.error || null);
      }
    } catch (e) {
      setFetchError(`network error: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [category, sort, topN, selectedBoard]);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60_000);
    return () => clearInterval(timer);
  }, [fetchData]);

  /* ── Filter dropdown style ── */
  const selectStyle: React.CSSProperties = {
    background: D.currentLine,
    color: D.fg,
    border: `1px solid ${D.comment}`,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    padding: "3px 8px",
    borderRadius: 3,
    outline: "none",
    cursor: "pointer",
  };

  return (
    <div
      style={{
        background: D.bg,
        color: D.fg,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <TitleBar />

      {/* nav bar */}
      <div
        style={{
          background: "#21222c",
          padding: "6px 16px",
          display: "flex",
          gap: 16,
          alignItems: "center",
          borderBottom: "1px solid #191a21",
          fontSize: 13,
        }}
      >
        <Link href="/" style={{ color: D.cyan, textDecoration: "none" }}>
          ← monitor
        </Link>
        <span style={{ color: D.comment }}>|</span>
        <Link href="/sector" style={{ color: D.comment, textDecoration: "none" }}>sector</Link>
        <span style={{ color: D.purple, fontWeight: 700 }}>rotation</span>
        <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
          {category} | top {topN} | auto-refresh 60s
        </span>
      </div>

      {/* scrollable body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "8px 16px 24px",
          lineHeight: 1.55,
        }}
      >
        <Prompt cmd="cat sector_rotation.log" />
        <div style={{ height: 8 }} />

        {loading && (
          <div style={{ color: D.comment, padding: "16px 0" }}>
            <span style={{ color: D.cyan }}>info</span> Loading rotation data
            <span style={{ animation: "blink 1s step-end infinite" }}>...</span>
          </div>
        )}

        {fetchError && (
          <div
            style={{
              color: D.red,
              padding: "4px 8px",
              background: "#3a1f1f",
              borderRadius: 4,
              margin: "4px 0",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            [ERROR] {fetchError}
          </div>
        )}

        {!loading && (
          <>
            {/* filter bar */}
            <div
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
                padding: "6px 0",
              }}
            >
              <select
                style={selectStyle}
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="industry">行业(新浪49)</option>
                <option value="concept">行业(证监会84)</option>
              </select>
              <select
                style={selectStyle}
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="change_pct">涨幅</option>
                <option value="drop_pct">跌幅</option>
              </select>
              <select
                style={selectStyle}
                value={topN}
                onChange={(e) => setTopN(Number(e.target.value))}
              >
                <option value={10}>前10名</option>
                <option value={20}>前20名</option>
                <option value={30}>前30名</option>
              </select>
            </div>

            {/* rotation table */}
            {rows.length > 0 && dates.length > 0 ? (
              <div style={{ display: "flex" }}>
                {/* fixed rank column */}
                <div style={{ flexShrink: 0 }}>
                  <div
                    style={{
                      height: 32,
                      display: "flex",
                      alignItems: "center",
                      borderBottom: `1px solid ${D.currentLine}`,
                      paddingRight: 8,
                    }}
                  >
                    <span style={{ color: D.pink, fontWeight: 500, width: 40, textAlign: "center" }}>
                      排名
                    </span>
                  </div>
                  {rows.map((r) => (
                    <div
                      key={r.rank}
                      style={{
                        height: 48,
                        display: "flex",
                        alignItems: "center",
                        borderBottom: "1px solid #191a21",
                        paddingRight: 8,
                      }}
                    >
                      <span style={{ width: 40, textAlign: "center" }}>
                        <RankBadge rank={r.rank} />
                      </span>
                    </div>
                  ))}
                </div>

                {/* scrollable date columns */}
                <div style={{ overflowX: "auto", flex: 1 }}>
                  <div style={{ display: "flex" }}>
                    {dates.map((d) => (
                      <div
                        key={d}
                        style={{
                          minWidth: 120,
                          height: 32,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          borderBottom: `1px solid ${D.currentLine}`,
                          color: D.pink,
                          fontWeight: 500,
                          fontSize: 12,
                        }}
                      >
                        {shortDate(d)}
                      </div>
                    ))}
                  </div>

                  {rows.map((r) => (
                    <div key={r.rank} style={{ display: "flex" }}>
                      {r.cells.map((cell, ci) => (
                        <div
                          key={ci}
                          style={{
                            minWidth: 120,
                            height: 48,
                            display: "flex",
                            flexDirection: "column",
                            justifyContent: "center",
                            alignItems: "center",
                            borderBottom: "1px solid #191a21",
                            cursor: cell.board ? "pointer" : "default",
                            padding: "2px 4px",
                          }}
                          onClick={() => {
                            if (cell.board) {
                              setSelectedBoard(
                                selectedBoard === cell.board ? null : cell.board,
                              );
                            }
                          }}
                        >
                          {cell.board ? (
                            <>
                              <span
                                style={{
                                  color: selectedBoard === cell.board ? D.yellow : D.fg,
                                  fontSize: 12,
                                  fontWeight: selectedBoard === cell.board ? 700 : 400,
                                  whiteSpace: "nowrap",
                                  overflow: "hidden",
                                  textOverflow: "ellipsis",
                                  maxWidth: 116,
                                }}
                              >
                                {cell.board}
                              </span>
                              <span
                                style={{
                                  color: chgColor(cell.change),
                                  fontSize: 11,
                                  fontWeight: 500,
                                }}
                              >
                                {fmtPct(cell.change)}
                              </span>
                            </>
                          ) : (
                            <span style={{ color: D.comment, fontSize: 11 }}>-</span>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div style={{ color: D.comment, padding: "8px 0" }}>
                # 暂无轮动数据。等待数据采集...
              </div>
            )}

            {/* board detail panel */}
            {boardDetail && (
              <div style={{ marginTop: 8 }}>
                <div
                  style={{
                    background: D.currentLine,
                    borderRadius: "4px 4px 0 0",
                    padding: "8px 14px",
                    display: "flex",
                    alignItems: "center",
                    gap: 16,
                    fontSize: 12,
                  }}
                >
                  <span style={{ color: D.purple }}>▸</span>
                  <span style={{ color: D.yellow, fontWeight: 700 }}>
                    {boardDetail.name}
                  </span>
                  {boardDetail.rankHistory.length > 0 && (
                    <span style={{ color: chgColor(boardDetail.rankHistory[0].rank <= 5 ? 1 : -1) }}>
                      排名{boardDetail.rankHistory[0].rank}
                    </span>
                  )}
                  <span style={{ color: D.fg }}>
                    近1月 <span style={{ color: D.orange }}>{boardDetail.top10Count}</span>次进前10
                  </span>
                  <span style={{ color: D.comment }}>
                    历史排名:{" "}
                    {boardDetail.rankHistory.slice(0, 7).map((h, i) => (
                      <span key={i} style={{ color: h.rank <= 5 ? D.red : h.rank <= 10 ? D.orange : D.comment }}>
                        {i > 0 ? " → " : ""}
                        {h.rank}
                      </span>
                    ))}
                  </span>
                  {boardDetail.stocks.length > 0 && (
                    <span style={{ color: D.comment, marginLeft: "auto" }}>
                      {boardDetail.stocks.length}只成分股
                    </span>
                  )}
                </div>
                {boardDetail.stocks.length > 0 && (
                  <div
                    style={{
                      background: "#1e1f29",
                      borderRadius: "0 0 4px 4px",
                      padding: "6px 14px 8px",
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "2px 0",
                      fontSize: 12,
                      maxHeight: 200,
                      overflowY: "auto",
                    }}
                  >
                    <div style={{ width: "100%", display: "flex", whiteSpace: "pre", color: D.pink, paddingBottom: 3, marginBottom: 2, borderBottom: `1px solid ${D.currentLine}` }}>
                      <span style={{ width: "10ch" }}>代码</span>
                      <span style={{ width: "8ch" }}>名称</span>
                      <span style={{ width: "9ch", textAlign: "right" }}>现价</span>
                      <span style={{ width: "8ch", textAlign: "right" }}>涨跌幅</span>
                      <span style={{ width: "9ch", textAlign: "right" }}>涨跌额</span>
                      <span style={{ width: "10ch", textAlign: "right" }}>成交额</span>
                    </div>
                    {boardDetail.stocks.map((s) => (
                      <div key={s.code} style={{ width: "100%", display: "flex", whiteSpace: "pre", padding: "1px 0", borderBottom: "1px solid #191a21" }}>
                        <span style={{ color: D.cyan, width: "10ch" }}>{s.code}</span>
                        <span style={{ color: D.fg, width: "8ch" }}>{s.name.slice(0, 6)}</span>
                        <span style={{ color: D.fg, width: "9ch", textAlign: "right" }}>{s.price.toFixed(2)}</span>
                        <span style={{ color: chgColor(s.changePct), width: "8ch", textAlign: "right", fontWeight: 500 }}>
                          {s.changePct >= 0 ? "+" : ""}{s.changePct.toFixed(2)}%
                        </span>
                        <span style={{ color: chgColor(s.change), width: "9ch", textAlign: "right" }}>
                          {s.change >= 0 ? "+" : ""}{s.change.toFixed(2)}
                        </span>
                        <span style={{ color: D.comment, width: "10ch", textAlign: "right" }}>
                          {s.amount >= 1e8 ? (s.amount / 1e8).toFixed(1) + "亿" : s.amount >= 1e4 ? (s.amount / 1e4).toFixed(0) + "万" : s.amount.toFixed(0)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        <BlinkCursor />
      </div>

      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
    </div>
  );
}
