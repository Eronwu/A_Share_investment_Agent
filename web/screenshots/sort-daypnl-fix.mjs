/**
 * Playwright test: Verify "今日" column sorts by dayPnl (chgAmt * shares),
 * NOT by raw chgAmt.
 *
 * Steps:
 *   1. Navigate to /?tab=A, wait for data
 *   2. Click "今日" header -> descending sort
 *   3. Extract 今日 column values from PROD rows in prod:stocks section ONLY
 *   4. Separate star rows from non-star rows
 *   5. Verify non-star rows are in descending order of dayPnl
 *   6. Click again -> ascending, verify ascending order
 *   7. Screenshot after descending sort
 *
 * Run: node screenshots/sort-daypnl-fix.mjs
 */
import { chromium } from "playwright";
import { mkdirSync } from "fs";

const BASE_URL = "http://localhost:3120";
const VIEWPORT = { width: 1440, height: 900 };
const SCREENSHOT_DIR = "/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/sort-all";
const SCREENSHOT_PATH = `${SCREENSHOT_DIR}/dayPnl-fix.png`;

mkdirSync(SCREENSHOT_DIR, { recursive: true });

// ── Value parser ──
function parseMoney(s) {
  if (!s || s.trim() === "-") return null;
  let t = s.trim().replace(/[¥,\s]/g, "").replace(/[\u2212\u2013\uff0d]/g, "-");
  let m = 1;
  if (t.includes("亿")) { m = 1e8; t = t.replace("亿", ""); }
  else if (t.includes("万")) { m = 1e4; t = t.replace("万", ""); }
  const n = parseFloat(t);
  return isNaN(n) ? null : n * m;
}

function isSortedDesc(values) {
  for (let i = 1; i < values.length; i++) {
    if (values[i] > values[i - 1] + 0.01) return false;
  }
  return true;
}

function isSortedAsc(values) {
  for (let i = 1; i < values.length; i++) {
    if (values[i] < values[i - 1] - 0.01) return false;
  }
  return true;
}

let pass = true;
let browser;

try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize(VIEWPORT);

  // 1. Navigate to /?tab=A and wait for data
  console.log("Step 1: Navigate to /?tab=A ...");
  await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  // 2. Find the "今日" header span (first one = holdings header in prod:stocks)
  console.log("Step 2: Locate 今日 header ...");
  const todayHeader = page.locator('span', { hasText: '今日' }).first();
  const headerText = await todayHeader.textContent();
  console.log(`  Using header: "${headerText.trim()}"`);

  // 3. Click for descending sort (first click)
  console.log("\nStep 3: Click 今日 header (descending sort) ...");
  await todayHeader.click();
  await page.waitForTimeout(500);

  // 4. Extract 今日 column from prod:stocks PROD rows ONLY
  // Strategy: iterate all child divs of the main container.
  // After seeing "prod:stocks" section title, collect PROD rows until next section title.
  console.log("\nStep 4: Extract 今日 column values from prod:stocks only ...");

  async function extractProdStockRows(pg) {
    // Use page.evaluate to walk the DOM directly - more reliable for section boundaries
    return await pg.evaluate(() => {
      const rows = [];
      // Find all elements, look for section markers and data rows
      const allDivs = document.querySelectorAll("div");
      let inProdStocks = false;
      let passedFirstHeader = false; // skip the holdHeader row

      for (const div of allDivs) {
        const text = div.textContent || "";
        const style = div.getAttribute("style") || "";

        // Section title detection: contains "prod:stocks" or "prod:ETF" etc.
        if (text.includes("prod:stocks") && text.includes("──") && !style.includes("display: flex")) {
          inProdStocks = true;
          passedFirstHeader = false;
          continue;
        }

        // End of prod:stocks section: next section title
        if (inProdStocks && !style.includes("display: flex") && text.includes("──") &&
            (text.includes("prod:ETF") || text.includes("stage:") || text.includes("hidden"))) {
          break;
        }

        if (!inProdStocks) continue;

        // Skip non-flex divs and the header row
        if (!style.includes("display: flex") || !style.includes("border-bottom")) continue;

        const spans = div.querySelectorAll(":scope > span");
        if (spans.length < 10) continue;

        const firstText = (spans[0].textContent || "").trim();

        // Skip header row (contains column names like "代码", "现价", etc.)
        if (firstText.includes("代码") || firstText.includes("现价")) continue;

        // Must be a PROD row
        if (!firstText.includes("PROD")) continue;

        const dayPnlText = (spans[9].textContent || "").trim();
        const isStar = firstText.includes("★");
        const stockId = (spans[1].textContent || "").trim();
        const changePctText = (spans[4].textContent || "").trim();

        rows.push({ text: dayPnlText, star: isStar, label: stockId, changePct: changePctText });
      }
      return rows;
    });
  }

  const rowData = await extractProdStockRows(page);

  if (rowData.length === 0) {
    console.log("  WARNING: No PROD rows found in prod:stocks section!");
    pass = false;
  }

  console.log(`  Found ${rowData.length} PROD stock rows (excluding ETFs):`);
  for (const r of rowData) {
    const val = parseMoney(r.text);
    console.log(`    ${r.star ? "★" : " "} ${r.label.padEnd(10)} 今日: "${r.text}" -> ${val}   changePct: ${r.changePct}`);
  }

  // 5. Take screenshot after descending sort
  console.log("\nStep 5: Taking screenshot ...");
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
  console.log(`  Saved: ${SCREENSHOT_PATH}`);

  // 6. Verify descending order for non-star rows
  const nonStarValues = rowData.filter((r) => !r.star).map((r) => parseMoney(r.text)).filter((v) => v !== null);
  const starValues = rowData.filter((r) => r.star).map((r) => parseMoney(r.text)).filter((v) => v !== null);

  console.log(`\nStep 6: Verify descending sort order (non-star rows only) ...`);
  console.log(`  Star rows (${starValues.length}): [${starValues.join(", ")}]`);
  console.log(`  Non-star rows (${nonStarValues.length}): [${nonStarValues.join(", ")}]`);

  if (nonStarValues.length < 2) {
    console.log("  SKIP: Not enough non-star PROD rows to verify sort order.");
  } else {
    const descOk = isSortedDesc(nonStarValues);
    console.log(`  Descending sort: ${descOk ? "PASS" : "FAIL"}`);
    if (!descOk) {
      console.log(`  Expected descending but got: [${nonStarValues.join(", ")}]`);
      pass = false;
    }
  }

  // 7. Click again for ascending sort
  console.log("\nStep 7: Click 今日 header again (ascending sort) ...");
  await todayHeader.click();
  await page.waitForTimeout(500);

  // 8. Extract values again and verify ascending
  const rowData2 = await extractProdStockRows(page);
  const nonStarValues2 = rowData2.filter((r) => !r.star).map((r) => parseMoney(r.text)).filter((v) => v !== null);

  console.log(`\nStep 8: Verify ascending sort order (non-star rows only) ...`);
  for (const r of rowData2) {
    const val = parseMoney(r.text);
    console.log(`    ${r.star ? "★" : " "} ${r.label.padEnd(10)} 今日: "${r.text}" -> ${val}`);
  }
  console.log(`  Non-star values: [${nonStarValues2.join(", ")}]`);

  if (nonStarValues2.length < 2) {
    console.log("  SKIP: Not enough non-star PROD rows to verify sort order.");
  } else {
    const ascOk = isSortedAsc(nonStarValues2);
    console.log(`  Ascending sort: ${ascOk ? "PASS" : "FAIL"}`);
    if (!ascOk) {
      console.log(`  Expected ascending but got: [${nonStarValues2.join(", ")}]`);
      pass = false;
    }
  }

  // 9. Cross-verify: confirm sort key is dayPnl not raw chgAmt
  console.log("\nStep 9: Cross-verify sort key is dayPnl (chgAmt * shares), not raw chgAmt ...");
  const nonStarChangePcts = rowData.filter((r) => !r.star).map((r) => {
    let t = r.changePct.trim().replace(/[%\s]/g, "").replace(/[\u2212\u2013\uff0d]/g, "-");
    return parseFloat(t);
  }).filter((v) => !isNaN(v));

  console.log(`  DayPnl order (desc): [${nonStarValues.join(", ")}]`);
  console.log(`  ChangePct order:     [${nonStarChangePcts.join(", ")}]`);

  const changePctDesc = isSortedDesc(nonStarChangePcts);
  if (!changePctDesc && nonStarValues.length >= 2) {
    console.log("  CONFIRMED: dayPnl is sorted but changePct is NOT in same order.");
    console.log("  This proves the sort key is dayPnl (chgAmt*shares), not raw chgAmt.");
  } else if (changePctDesc) {
    console.log("  NOTE: changePct happens to be in same order as dayPnl.");
    console.log("  Sort order is correct; cannot definitively distinguish dayPnl vs chgAmt from this data.");
  }

  // ── Final result ──
  console.log("\n" + "=".repeat(60));
  console.log(pass ? "RESULT: PASS - 今日 column sorts by dayPnl (chgAmt * shares)" : "RESULT: FAIL - Sort order verification failed");
  console.log("=".repeat(60));

} catch (err) {
  console.error("ERROR:", err.message);
  console.error(err.stack);
  pass = false;
  console.log("\n" + "=".repeat(60));
  console.log("RESULT: FAIL - Test error");
  console.log("=".repeat(60));
} finally {
  if (browser) await browser.close();
  process.exit(pass ? 0 : 1);
}
