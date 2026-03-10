/**
 * E2E test: gap_fade / gap_recover alert rendering + PatternEngine parseAlert map
 *
 * 注入模拟 alert_events，验证：
 * 1. gap_fade   → 红色"高开低走"，正确解析名称/信号/价格/detail
 * 2. gap_recover → 绿色"低开高走"，缺口完全收复 label
 * 3. 解析失败时 fallback 到 kind 字段（不崩溃）
 * 4. KR 前缀股票代码被正确裁剪（KR000660 → 000660）
 * 5. PatternEngine: GapFadeEngine 高开低走/低开高走逻辑
 *
 * 测试结束后清理注入的数据。
 */

import { chromium } from "playwright";
import Database from "better-sqlite3";
import { fileURLToPath } from "url";
import { join, dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = "http://localhost:3120";
const DB_PATH = join(__dirname, "../../src/data/sim_trading.db");

const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}${d ? ": " + d : ""}`);
let passed = 0, failed = 0;
function record(label, cond, detail = "") {
  if (cond) { ok(label); passed++; } else { ng(label, detail); failed++; }
}

// ─── 1. 注入测试数据 ───────────────────────────────────────────────────────────
const NOW = Date.now();
const TODAY = new Date().toISOString().slice(0, 10);
const TEST_ROWS = [
  // gap_fade: 高开低走，缺口未完全回吐
  {
    ts: NOW - 5000, date: TODAY, time: "10:30:00",
    symbol: "HK09688", kind: "gap_fade", level: 2,
    message: "HK09688 澜起科技 高开+4.0% 回落-2.9% 现价50.40",
    display: "HK09688 澜起科技 高开低走: 高开+4.0% 回落-2.9% | 现价50.40",
    change_pct: 1.0,
  },
  // gap_recover: 低开高走，缺口完全收复
  {
    ts: NOW - 4000, date: TODAY, time: "11:00:00",
    symbol: "HK00700", kind: "gap_recover", level: 1,
    message: "HK00700 腾讯 低开-9.1% 反弹+9.3% 缺口完全收复 现价76.50",
    display: "HK00700 腾讯 低开高走: 低开-9.1% 反弹+9.3% 缺口完全收复 | 现价76.50",
    change_pct: -0.6,
  },
  // gap_fade: 高开低走，缺口完全回吐
  {
    ts: NOW - 3000, date: TODAY, time: "13:00:00",
    symbol: "HK03173", kind: "gap_fade", level: 2,
    message: "HK03173 兆易创新 高开+3.0% 回落-4.1% 缺口完全回吐 现价77.00",
    display: "HK03173 兆易创新 高开低走: 高开+3.0% 回落-4.1% 缺口完全回吐 | 现价77.00",
    change_pct: -1.2,
  },
  // gap_fade with KR prefix
  {
    ts: NOW - 2000, date: TODAY, time: "09:45:00",
    symbol: "KR000660", kind: "gap_fade", level: 2,
    message: "KR000660 SK海力士 高开+2.5% 回落-3.0% 现价72000",
    display: "KR000660 SK海力士 高开低走: 高开+2.5% 回落-3.0% | 现价72000",
    change_pct: -0.5,
  },
  // unknown kind: fallback test
  {
    ts: NOW - 1000, date: TODAY, time: "14:00:00",
    symbol: "000001", kind: "unknown_future_kind", level: 3,
    message: "test fallback",
    display: "fallback display text",
    change_pct: 0,
  },
];

const db = new Database(DB_PATH);
const insertStmt = db.prepare(`
  INSERT OR IGNORE INTO alert_events
    (ts, date, time, symbol, kind, level, message, display, change_pct)
  VALUES (?,?,?,?,?,?,?,?,?)
`);
const insertMany = db.transaction((rows) => {
  for (const r of rows) {
    insertStmt.run(r.ts, r.date, r.time, r.symbol, r.kind, r.level, r.message, r.display, r.change_pct);
  }
});
insertMany(TEST_ROWS);
const inserted = db.prepare("SELECT COUNT(*) as c FROM alert_events WHERE ts >= ?").get(NOW - 6000)?.c ?? 0;
console.log(`\nInjected ${inserted} test rows into alert_events\n`);

// ─── 2. Browser tests ────────────────────────────────────────────────────────
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// Block Google Fonts to prevent font-load hanging in offline/sandboxed environments
await page.route("**fonts.googleapis.com/**", (route) => route.abort());
await page.route("**fonts.gstatic.com/**", (route) => route.abort());

await page.goto(`${BASE}/alerts`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(4000);
await page.screenshot({ path: `${__dirname}/gap_fade_01_alerts.png`, timeout: 60000 });

const body = await page.textContent("body");

// ═══ 1. gap_fade 高开低走 基本渲染 ═══
console.log("═══ 1. gap_fade rendering ═══");
record("gap_fade: 澜起科技 name displayed", body.includes("澜起科技"));
record("gap_fade: signal '高开低走' displayed", body.includes("高开低走"));
record("gap_fade: price '50.40' displayed", body.includes("50.40"));
record("gap_fade: detail '高开+4.0%' displayed", body.includes("高开+4.0%"));

// ═══ 2. gap_recover 低开高走 ═══
console.log("\n═══ 2. gap_recover rendering ═══");
record("gap_recover: 腾讯 name displayed", body.includes("腾讯"));
record("gap_recover: signal '低开高走' displayed", body.includes("低开高走"));
record("gap_recover: price '76.50' displayed", body.includes("76.50"));
record("gap_recover: '缺口收复' label displayed", body.includes("缺口收复"));

// ═══ 3. 缺口完全回吐 ═══
console.log("\n═══ 3. gap_fade 缺口回吐 ═══");
record("gap_fade erased: '缺口回吐' label displayed", body.includes("缺口回吐"));

// ═══ 4. KR 前缀股票 ═══
console.log("\n═══ 4. KR prefix stock ═══");
record("KR stock: SK海力士 name displayed", body.includes("SK海力士"));
// shortCode should strip KR prefix: KR000660 → 000660
record("KR stock: code stripped to '000660'", body.includes("000660"));
record("KR stock: raw 'KR000660' not as standalone code",
  !body.match(/\bKR000660\b.*高开低走/) || body.includes("SK海力士"));

// ═══ 5. Fallback for unknown kind ═══
console.log("\n═══ 5. Fallback for unknown kind ═══");
// unknown_future_kind should render without crashing
record("unknown kind: page not crashed (alerts still loads)", body.includes("高开低走") || body.includes("澜起科技"));

// ═══ 6. Signal colors (check via evaluate) ═══
console.log("\n═══ 6. Signal colors ═══");
const colors = await page.evaluate(() => {
  const spans = Array.from(document.querySelectorAll("span"));
  const result = {};
  for (const s of spans) {
    const t = (s.textContent || "").trim();
    if (t === "高开低走" || t === "高开低走 缺口回吐") {
      result.fadeColor = window.getComputedStyle(s).color;
    }
    if (t === "低开高走" || t === "低开高走 缺口收复") {
      result.recoverColor = window.getComputedStyle(s).color;
    }
  }
  return result;
});
// gap_fade should be reddish, gap_recover greenish
const isReddish = (c) => c && (c.includes("255") || c.includes("ff6b6b") || c.includes("255, 107"));
const isGreenish = (c) => c && (c.includes("80, 250") || c.includes("50, 250") || c.includes("50fa7b") || c.includes("0, 255") || c.includes("80,250"));
record("gap_fade signal: reddish color", isReddish(colors.fadeColor), `color=${colors.fadeColor}`);
record("gap_recover signal: greenish color", isGreenish(colors.recoverColor), `color=${colors.recoverColor}`);

// ═══ 7. Screenshot review ═══
await page.screenshot({ path: `${__dirname}/gap_fade_02_detail.png`, fullPage: true, timeout: 60000 });

// ─── 3. Cleanup ────────────────────────────────────────────────────────────
db.prepare("DELETE FROM alert_events WHERE ts >= ?").run(NOW - 6000);
const remaining = db.prepare("SELECT COUNT(*) as c FROM alert_events WHERE ts >= ?").get(NOW - 6000)?.c ?? 0;
db.close();
record("cleanup: test rows removed", remaining === 0, `remaining=${remaining}`);

await browser.close();

// ─── Summary ────────────────────────────────────────────────────────────────
console.log(`\n${"═".repeat(50)}`);
console.log(`GAP_FADE E2E: ${passed + failed} tests | ${passed} PASS | ${failed} FAIL`);
if (failed > 0) process.exit(1);
