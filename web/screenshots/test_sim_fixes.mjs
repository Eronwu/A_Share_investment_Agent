import { chromium } from "playwright";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = "http://localhost:3120";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });

let pass = 0, fail = 0;
function check(label, ok, detail = "") {
  if (ok) { pass++; console.log(`  [PASS] ${label}${detail ? " -- " + detail : ""}`); }
  else    { fail++; console.log(`  [FAIL] ${label}${detail ? " -- " + detail : ""}`); }
}

// Navigate to sim page
await page.goto(`${BASE}/sim`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

console.log("═══ Sim Page Fix Verification ═══\n");

// === Fix 1: Color collision - check no D.green (#50fa7b) on performance indicators ===
console.log("── Fix 1: Green color collision ──");

// Get all text content with colors
const colorCheck = await page.evaluate(() => {
  const GREEN = "rgb(80, 250, 123)";  // D.green = #50fa7b
  const CYAN = "rgb(139, 233, 253)";  // D.cyan = #8be9fd

  const results = { greenTexts: [], cyanTexts: [] };
  const spans = document.querySelectorAll("span");

  for (const s of spans) {
    const color = getComputedStyle(s).color;
    const text = s.textContent?.trim() || "";
    if (!text) continue;

    if (color === GREEN) results.greenTexts.push(text.slice(0, 40));
    // Check cyan for performance metrics
    if (color === CYAN && (text.includes("%") || text.includes("止盈") || text.match(/^\d+\.\d+$/)))
      results.cyanTexts.push(text.slice(0, 40));
  }
  return results;
});

// 胜率, 盈亏比, 评分 should NOT be green
const greenPerfKeywords = ["胜率", "盈亏比", "评分"];
const greenHasPerfMetric = colorCheck.greenTexts.some(t =>
  greenPerfKeywords.some(k => t.includes(k))
);
check("No green on performance metrics (胜率/盈亏比/评分)", !greenHasPerfMetric,
  `green texts: ${colorCheck.greenTexts.slice(0, 5).join(", ")}`);

check("Cyan used for some indicators", colorCheck.cyanTexts.length > 0,
  `cyan texts: ${colorCheck.cyanTexts.slice(0, 5).join(", ")}`);

// === Fix 2: No SIM badge ===
console.log("\n── Fix 2: SIM badge removed ──");

const bodyText = await page.evaluate(() => document.body.textContent);
const hasSIMBadge = await page.evaluate(() => {
  const spans = document.querySelectorAll("span");
  for (const s of spans) {
    if (s.textContent?.trim() === "SIM" && s.style.width === "6ch") return true;
  }
  return false;
});
check("No SIM type badge in positions tables", !hasSIMBadge);

const hasTypeHeader = await page.evaluate(() => {
  const spans = document.querySelectorAll("span");
  for (const s of spans) {
    if (s.textContent?.trim() === "类型" && s.style.width === "6ch") return true;
  }
  return false;
});
check("No '类型' header in positions tables", !hasTypeHeader);

// === Fix 3: Entry date + hold duration columns ===
console.log("\n── Fix 3: Entry date + hold duration ──");

const tradeHeaders = await page.evaluate(() => {
  const spans = document.querySelectorAll("span");
  const headers = { entry: false, hold: false, date: false, review: false };
  for (const s of spans) {
    const t = s.textContent?.trim();
    if (t === "入场" && s.style.width === "6ch") headers.entry = true;
    if (t === "持仓" && s.style.width === "5ch") headers.hold = true;
    if (t === "日期" && s.style.width === "10ch") headers.date = true;
    if (t === "复盘") headers.review = true;
  }
  return headers;
});
check("'入场' column header present", tradeHeaders.entry);
check("'持仓' column header present", tradeHeaders.hold);
check("Old '日期' column removed", !tradeHeaders.date);
check("'复盘' column still present", tradeHeaders.review);

// Check that entry dates show MM-DD format
const entryDates = await page.evaluate(() => {
  const spans = document.querySelectorAll("span");
  const dates = [];
  for (const s of spans) {
    const t = s.textContent?.trim();
    if (t && /^\d{2}-\d{2}$/.test(t) && s.style.width === "6ch" && s.style.textAlign === "right") {
      dates.push(t);
    }
  }
  return dates;
});
check("Entry dates in MM-DD format", entryDates.length > 0,
  `found: ${entryDates.slice(0, 5).join(", ")}`);

// Check hold duration format (Xh or Xd)
const holdDurations = await page.evaluate(() => {
  const spans = document.querySelectorAll("span");
  const durations = [];
  for (const s of spans) {
    const t = s.textContent?.trim();
    if (t && /^\d+[hd]$/.test(t) && s.style.width === "5ch" && s.style.textAlign === "right") {
      durations.push(t);
    }
  }
  return durations;
});
check("Hold durations in Xh/Xd format", holdDurations.length > 0,
  `found: ${holdDurations.slice(0, 5).join(", ")}`);

// === Fix 4: Equity curve responsive + label thinning ===
console.log("\n── Fix 4: Equity curve responsive ──");

// Expand collapsed "历史回测" section first
await page.evaluate(() => {
  const divs = [...document.querySelectorAll("div")];
  for (const d of divs) {
    if (d.textContent?.includes("历史回测") && d.style.cursor === "pointer") {
      d.click();
      return;
    }
  }
});
await page.waitForTimeout(1000);

const svgCheck = await page.evaluate(() => {
  const svgs = document.querySelectorAll("svg");
  for (const svg of svgs) {
    const vb = svg.getAttribute("viewBox");
    const w = svg.getAttribute("width");
    if (vb && vb.startsWith("0 0 760 130") && w === "100%") {
      // Count date labels (x-axis text at bottom)
      const texts = svg.querySelectorAll("text");
      let dateLabels = 0;
      let valueLabels = 0;
      const padLeft = 56;
      for (const t of texts) {
        const y = parseFloat(t.getAttribute("y") || "0");
        const x = parseFloat(t.getAttribute("x") || "0");
        if (y > 120) dateLabels++;  // bottom labels = dates
        // value labels are above data points (x > padLeft), grid labels are at x < padLeft
        if (x > padLeft && t.textContent?.includes("k")) valueLabels++;
      }
      return { hasViewBox: true, responsive: true, dateLabels, valueLabels };
    }
  }
  return { hasViewBox: false, responsive: false, dateLabels: 0, valueLabels: 0 };
});

check("SVG has viewBox='0 0 760 130'", svgCheck.hasViewBox);
check("SVG width='100%' (responsive)", svgCheck.responsive);
check("Date labels thinned (≤11)", svgCheck.dateLabels <= 11,
  `${svgCheck.dateLabels} date labels`);
check("Value labels sparse (≤4)", svgCheck.valueLabels <= 4,
  `${svgCheck.valueLabels} value labels`);

// Take screenshots
console.log("\n── Screenshots ──");
await page.screenshot({ path: path.join(__dirname, "sim_fixes_full.png"), fullPage: true });

// Scroll to equity curve area for detailed shot
const equityHeader = await page.evaluate(() => {
  const els = [...document.querySelectorAll("span")];
  const el = els.find(e => e.textContent?.includes("净值曲线"));
  if (el) { el.scrollIntoView({ block: "start" }); return true; }
  return false;
});
if (equityHeader) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(__dirname, "sim_fixes_equity.png") });
}

// Go back to top for positions shot
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.screenshot({ path: path.join(__dirname, "sim_fixes_top.png") });

await browser.close();

console.log(`\n════════════════════════════════════════════`);
console.log(`SIM FIXES: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
console.log(`════════════════════════════════════════════`);
process.exit(fail > 0 ? 1 : 0);
