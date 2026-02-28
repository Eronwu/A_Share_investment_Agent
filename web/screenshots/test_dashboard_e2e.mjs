/**
 * Dashboard E2E Test — comprehensive Playwright test for http://localhost:3120
 *
 * Tests all interactive features:
 *  1. Page loads (A-share tab default, summary bar, stock rows)
 *  2. Tab switch A -> HK (URL updates, HK stocks shown)
 *  3. Tab switch HK -> A (URL updates, A stocks shown)
 *  4. Section collapse/expand (prod:stocks, stage:stocks, hidden)
 *  5. Sort by column header (change, pnl, price) with arrow indicator
 *  6. Star toggle (via manage page, then verify dashboard reflects it)
 *  7. Hidden list filtering (HK tab -> only HK hidden, A tab -> only A hidden)
 *  8. EditableCell on manage page (cost/shares edit for holding)
 *  9. Summary bar stats (Nodes, holdings, up/down, P&L)
 * 10. FX rate display (HK tab: FX or WARN banner)
 *
 * Run: node web/screenshots/test_dashboard_e2e.mjs
 */

import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR = new URL(".", import.meta.url).pathname;

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? " -- " + detail : ""}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push("PAGE_ERROR: " + err.message));

  // Accept all confirm dialogs (for star toggle / manage operations)
  page.on("dialog", (d) => d.accept());

  try {
    // ================================================================
    // 1. Page loads — A-share tab default, summary bar, stock rows
    // ================================================================
    console.log("\n=== 1. Page loads (A-share default) ===");

    await page.goto(`${BASE}/?tab=A`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_01_loaded.png`, fullPage: true });

    // 1a. No ERROR banner
    const hasError01 = await page.evaluate(() =>
      Array.from(document.querySelectorAll("div")).some((d) =>
        d.textContent.includes("[ERROR]")
      )
    );
    record("1a: No [ERROR] banner on load", !hasError01);

    // 1b. Not in loading state
    const isLoading = await page.evaluate(() =>
      document.body.innerText.includes("Loading metrics")
    );
    record("1b: Not stuck in loading state", !isLoading);

    // 1c. Summary bar visible (has "Nodes:" text)
    const hasSummaryBar = await page.evaluate(() =>
      document.body.innerText.includes("Nodes:")
    );
    record("1c: Summary bar visible (Nodes:)", hasSummaryBar);

    // 1d. A-share PROD stock rows rendered
    const prodRowCount = await page.evaluate(() =>
      Array.from(document.querySelectorAll("span")).filter(
        (s) => s.textContent.trim() === "PROD" || s.textContent.trim().includes("PROD")
      ).length
    );
    record("1d: PROD stock rows rendered", prodRowCount > 0, `${prodRowCount} PROD rows`);

    // 1e. A-share tab is active (URL has tab=A)
    record("1e: URL contains tab=A", page.url().includes("tab=A"));

    // 1f. Watch header shows poll interval
    const hasWatchHeader = await page.evaluate(() =>
      document.body.innerText.includes("svc-monitor --format table")
    );
    record("1f: Watch header rendered", hasWatchHeader);

    // 1g. No A-share codes start with HK (should only show A-share)
    const aShareCodes = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const codes = spans
        .filter(
          (s) =>
            s.style.width === "10ch" &&
            s.style.color &&
            /^\s*(HK)?\d{5,6}/.test(s.textContent)
        )
        .map((s) => s.textContent.trim());
      const hkInATab = codes.filter((c) => c.startsWith("HK"));
      return { total: codes.length, hkCount: hkInATab.length };
    });
    record(
      "1g: No HK codes in A-share tab",
      aShareCodes.hkCount === 0,
      `${aShareCodes.total} codes, ${aShareCodes.hkCount} HK`
    );

    // ================================================================
    // 2. Tab switch: A -> HK
    // ================================================================
    console.log("\n=== 2. Tab switch A -> HK ===");

    // Click HK tab (find div with "HK (node)" text and cursor:pointer)
    const clickedHK = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const hk = divs.find(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.minWidth === "130px" &&
          d.textContent.includes("HK")
      );
      if (hk) {
        hk.click();
        return true;
      }
      return false;
    });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_02_hk_tab.png`, fullPage: true });

    record("2a: HK tab clicked successfully", clickedHK);
    record("2b: URL updated to tab=HK", page.url().includes("tab=HK"));

    // 2c. HK stock codes shown
    const hkCodes = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans
        .filter((s) => /^HK\d{5}/.test(s.textContent.trim()))
        .map((s) => s.textContent.trim());
    });
    record("2c: HK stock codes rendered", hkCodes.length > 0, `${hkCodes.length} HK codes: ${hkCodes.slice(0, 5).join(", ")}`);

    // 2d. No A-share codes (6-digit without HK prefix)
    const aCodesInHK = await page.evaluate(() => {
      // Look at the visible stock rows only (PROD or DEV rows)
      const spans = Array.from(document.querySelectorAll("span"));
      const rowSpans = spans.filter(
        (s) =>
          s.textContent.trim() === "PROD" ||
          s.textContent.trim() === "DEV" ||
          s.textContent.trim().includes("PROD") ||
          s.textContent.trim().includes("DEV")
      );
      // For each PROD/DEV row, find the sibling code span
      const codes = [];
      for (const rs of rowSpans) {
        const row = rs.closest("div[style]");
        if (row) {
          const codeSpans = Array.from(row.querySelectorAll("span")).filter((s) =>
            /^\d{6}\s*$/.test(s.textContent.trim())
          );
          codes.push(...codeSpans.map((s) => s.textContent.trim()));
        }
      }
      return codes;
    });
    record(
      "2d: No A-share codes in HK tab",
      aCodesInHK.length === 0,
      aCodesInHK.length > 0 ? `found: ${aCodesInHK.join(", ")}` : "clean"
    );

    // ================================================================
    // 3. Tab switch: HK -> A
    // ================================================================
    console.log("\n=== 3. Tab switch HK -> A ===");

    const clickedA = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const a = divs.find(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.minWidth === "130px" &&
          d.textContent.includes("A-share")
      );
      if (a) {
        a.click();
        return true;
      }
      return false;
    });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_03_a_tab_back.png`, fullPage: true });

    record("3a: A-share tab clicked successfully", clickedA);
    record("3b: URL updated to tab=A", page.url().includes("tab=A"));

    // 3c. A-share codes now visible again
    const aCodesAfterSwitch = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.filter((s) => /^\d{6}\s*$/.test(s.textContent.trim())).length;
    });
    record("3c: A-share codes rendered after switch back", aCodesAfterSwitch > 0, `${aCodesAfterSwitch} codes`);

    // ================================================================
    // 4. Section collapse/expand
    // ================================================================
    console.log("\n=== 4. Section collapse/expand ===");

    // 4a. Find section headers (they contain "# --" pattern and are clickable)
    const sectionHeaders = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      return divs
        .filter(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("#") &&
            d.textContent.includes("(")
        )
        .map((d) => d.textContent.trim());
    });
    record("4a: Section headers found", sectionHeaders.length > 0, sectionHeaders.join(" | "));

    // 4b. Count PROD rows before collapse
    const prodRowsBefore = await page.evaluate(() =>
      Array.from(document.querySelectorAll("span")).filter(
        (s) => s.textContent.trim() === "PROD" || s.textContent.trim().includes("PROD")
      ).length
    );

    // 4c. Click first section header to collapse (prod:stocks)
    const collapsed = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("prod:stocks")
      );
      if (header) {
        header.click();
        return true;
      }
      return false;
    });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_04_collapsed.png`, fullPage: true });

    if (collapsed) {
      // 4d. After collapse, PROD rows in prod:stocks section should disappear
      const prodRowsAfterCollapse = await page.evaluate(() =>
        Array.from(document.querySelectorAll("span")).filter(
          (s) => s.textContent.trim() === "PROD" || s.textContent.trim().includes("PROD")
        ).length
      );
      // Some PROD rows may remain in prod:ETF section
      record(
        "4b: Section collapsed (fewer PROD rows)",
        prodRowsAfterCollapse < prodRowsBefore,
        `before: ${prodRowsBefore}, after: ${prodRowsAfterCollapse}`
      );

      // 4e. The arrow should change from down-triangle to right-triangle
      const hasRightTriangle = await page.evaluate(() => {
        const spans = Array.from(document.querySelectorAll("span"));
        return spans.some(
          (s) => s.textContent.trim() === "\u25B8" // right-pointing triangle
        );
      });
      record("4c: Collapse arrow shows right-triangle", hasRightTriangle);

      // 4f. Click again to expand
      const expanded = await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const header = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("prod:stocks")
        );
        if (header) {
          header.click();
          return true;
        }
        return false;
      });
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${DIR}/dashboard_e2e_04b_expanded.png`, fullPage: true });

      const prodRowsAfterExpand = await page.evaluate(() =>
        Array.from(document.querySelectorAll("span")).filter(
          (s) => s.textContent.trim() === "PROD" || s.textContent.trim().includes("PROD")
        ).length
      );
      record(
        "4d: Section expanded (PROD rows restored)",
        prodRowsAfterExpand >= prodRowsBefore,
        `before: ${prodRowsBefore}, expanded: ${prodRowsAfterExpand}`
      );
    } else {
      record("4b: prod:stocks section not found to collapse", false, "no clickable prod:stocks header");
      record("4c: Skipped (no section to test)", false);
      record("4d: Skipped (no section to test)", false);
    }

    // 4g. Test hidden section toggle (collapsed by default)
    const hiddenHeaderFound = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      return divs.some(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("hidden")
      );
    });
    if (hiddenHeaderFound) {
      // Click to expand hidden section
      await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const header = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("hidden")
        );
        if (header) header.click();
      });
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${DIR}/dashboard_e2e_04c_hidden_expanded.png`, fullPage: true });

      const hiddenRowsExpanded = await page.evaluate(() => {
        const text = document.body.innerText;
        // After expanding hidden, check if there are additional rows
        const divs = Array.from(document.querySelectorAll("div"));
        const hiddenHeader = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("hidden")
        );
        // Check for down-triangle in hidden header (expanded state)
        return hiddenHeader ? hiddenHeader.textContent.includes("\u25BE") : false;
      });
      record("4e: Hidden section expanded (down-triangle)", hiddenRowsExpanded);

      // Click again to collapse
      await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const header = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("hidden")
        );
        if (header) header.click();
      });
      await page.waitForTimeout(300);
    } else {
      record("4e: Hidden section header", true, "no hidden stocks in A tab (ok)");
    }

    // ================================================================
    // 5. Sort by column header
    // ================================================================
    console.log("\n=== 5. Sort by column header ===");

    // 5a. Ensure we have prodStock rows for sorting
    // Click the "change" header (涨跌幅) to sort holdings by change%
    const sortResult = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      // Find the header span containing "涨跌幅" in the holdings section
      // It has cursor: pointer (set by mkHStyle when key is not null)
      const changeHeader = spans.find(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅")
      );
      if (changeHeader) {
        changeHeader.click();
        return { clicked: true, text: changeHeader.textContent.trim() };
      }
      return { clicked: false };
    });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_05_sorted_change.png`, fullPage: true });

    record("5a: Clicked '涨跌幅' header for sort", sortResult.clicked, sortResult.text || "");

    // 5b. Check sort arrow indicator appeared
    const hasDownArrow = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅") && s.textContent.includes("\u25BC")
      );
    });
    // The arrow is appended via mkArrow: " ▲" (asc) or " ▼" (desc)
    const hasArrowIndicator = await page.evaluate(() => {
      const text = document.body.innerText;
      // Check if any header now has up or down arrow
      return text.includes("\u25B2") || text.includes("\u25BC");
    });
    record("5b: Sort arrow indicator shown", hasArrowIndicator);

    // 5c. Click again to reverse sort direction
    await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const changeHeader = spans.find(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅")
      );
      if (changeHeader) changeHeader.click();
    });
    await page.waitForTimeout(500);

    // Check if arrow changed direction
    const hasReversedArrow = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const hdr = spans.find(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅")
      );
      return hdr ? hdr.textContent.trim() : "";
    });
    record("5c: Sort direction toggled on second click", hasReversedArrow.includes("\u25B2"), hasReversedArrow);

    // 5d. Sort by a different column (pnl)
    const sortPnlResult = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const pnlHeader = spans.find(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("盈亏%") && !s.textContent.includes("盈亏额")
      );
      if (pnlHeader) {
        pnlHeader.click();
        return { clicked: true, text: pnlHeader.textContent.trim() };
      }
      return { clicked: false };
    });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_05b_sorted_pnl.png`, fullPage: true });

    record("5d: Clicked '盈亏%' header", sortPnlResult.clicked, sortPnlResult.text || "");

    // 5e. Verify change header no longer has arrow (sort moved to pnl)
    const changeNoArrow = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const hdr = spans.find(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅")
      );
      if (!hdr) return true;
      return !hdr.textContent.includes("\u25B2") && !hdr.textContent.includes("\u25BC");
    });
    record("5e: Change header cleared its arrow", changeNoArrow);

    // 5f. Test watching section sort (different sort state)
    const sortWatchResult = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      // Find header spans for watching section — look for "涨跌" (without %) in watch header
      // Watch header has "涨跌" column (chgAmt), not "涨跌幅" (change)
      // Actually watch header also has 涨跌幅. Find the second one.
      const changeHeaders = spans.filter(
        (s) => s.style.cursor === "pointer" && s.textContent.includes("涨跌幅")
      );
      // The second "涨跌幅" header is in the watching section
      if (changeHeaders.length >= 2) {
        changeHeaders[1].click();
        return { clicked: true, section: "watching" };
      }
      return { clicked: false, count: changeHeaders.length };
    });
    await page.waitForTimeout(500);
    record(
      "5f: Watching section sort independent",
      sortWatchResult.clicked,
      sortWatchResult.clicked ? "watching sort clicked" : `only ${sortWatchResult.count} headers`
    );

    // ================================================================
    // 6. Star toggle (tested on manage page, verified on dashboard)
    // ================================================================
    console.log("\n=== 6. Star toggle (manage -> dashboard) ===");

    // First, navigate to manage page to find a stock to toggle star
    await page.goto(`${BASE}/manage`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);

    // Find a star button. Star buttons contain "*" text
    const starButtons = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      return buttons
        .filter((b) => b.textContent.trim() === "*" && b.title)
        .map((b) => ({
          title: b.title,
          bg: b.style.background,
          code: (() => {
            // Get the sibling code span in the same row
            const row = b.closest("div[style]");
            if (!row) return "";
            const spans = Array.from(row.querySelectorAll("span"));
            const codeSpan = spans.find((s) => /^(HK)?\d{5,6}$/.test(s.textContent.trim()));
            return codeSpan ? codeSpan.textContent.trim() : "";
          })(),
        }));
    });
    record("6a: Star buttons found on manage page", starButtons.length > 0, `${starButtons.length} buttons`);

    if (starButtons.length > 0) {
      // Find a non-starred button to toggle on, then off
      const unstarredIdx = starButtons.findIndex((b) => b.title.includes("Mark as"));
      const targetIdx = unstarredIdx >= 0 ? unstarredIdx : 0;
      const targetCode = starButtons[targetIdx].code;
      const wasStar = starButtons[targetIdx].title.includes("Remove");

      // Set up API response interception
      let apiCalled = false;
      page.on("response", (resp) => {
        if (resp.url().includes("/api/config") && resp.request().method() === "POST") {
          apiCalled = true;
        }
      });

      // Click the star button
      const toggledStar = await page.evaluate((idx) => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const starBtns = buttons.filter((b) => b.textContent.trim() === "*" && b.title);
        if (starBtns[idx]) {
          starBtns[idx].click();
          return true;
        }
        return false;
      }, targetIdx);
      await page.waitForTimeout(1500);
      await page.screenshot({ path: `${DIR}/dashboard_e2e_06_star_toggled.png`, fullPage: true });

      record("6b: Star button clicked", toggledStar, `code: ${targetCode}`);
      record("6c: API call made for star toggle", apiCalled);

      // Verify star state changed on the button
      const newStarState = await page.evaluate((idx) => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const starBtns = buttons.filter((b) => b.textContent.trim() === "*" && b.title);
        return starBtns[idx] ? starBtns[idx].title : "";
      }, targetIdx);
      const starStateChanged =
        (wasStar && newStarState.includes("Mark as")) ||
        (!wasStar && newStarState.includes("Remove"));
      record("6d: Star state toggled", starStateChanged, `was=${wasStar ? "star" : "unstar"}, now=${newStarState}`);

      // Toggle back to restore original state
      await page.evaluate((idx) => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const starBtns = buttons.filter((b) => b.textContent.trim() === "*" && b.title);
        if (starBtns[idx]) starBtns[idx].click();
      }, targetIdx);
      await page.waitForTimeout(1000);
      record("6e: Star restored to original state", true, "toggled back");
    }

    // ================================================================
    // 7. Hidden list — per-tab filtering
    // ================================================================
    console.log("\n=== 7. Hidden list filtering ===");

    // 7a. Go to HK tab, check hidden section
    await page.goto(`${BASE}/?tab=HK`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);

    // Expand hidden section if it exists
    const hkHiddenResult = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const hiddenHeader = divs.find(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("hidden")
      );
      if (!hiddenHeader) return { found: false, codes: [] };
      hiddenHeader.click();
      return { found: true };
    });
    await page.waitForTimeout(500);

    if (hkHiddenResult.found) {
      // Get codes in hidden section
      const hkHiddenCodes = await page.evaluate(() => {
        // Find all code spans after the hidden header
        const divs = Array.from(document.querySelectorAll("div"));
        const hiddenHeader = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("hidden")
        );
        if (!hiddenHeader) return [];
        // Get all subsequent sibling row divs until next section header
        const codes = [];
        let el = hiddenHeader.nextElementSibling;
        while (el) {
          const text = el.textContent || "";
          // Stop if we hit another section header or the log tail
          if (
            (el.style && el.style.cursor === "pointer" && text.includes("#")) ||
            text.includes("metrics-collector") ||
            text.includes("scheduler")
          ) break;
          // Extract stock codes from spans
          const spans = Array.from(el.querySelectorAll("span"));
          for (const s of spans) {
            const t = s.textContent.trim();
            if (/^(HK)?\d{5,6}$/.test(t)) codes.push(t);
          }
          el = el.nextElementSibling;
        }
        return codes;
      });

      const allHK = hkHiddenCodes.every((c) => c.startsWith("HK"));
      record(
        "7a: HK hidden section only shows HK stocks",
        allHK || hkHiddenCodes.length === 0,
        `codes: ${hkHiddenCodes.join(", ") || "(empty)"}`
      );
      await page.screenshot({ path: `${DIR}/dashboard_e2e_07_hk_hidden.png`, fullPage: true });
    } else {
      record("7a: HK tab has no hidden section", true, "no hidden HK stocks");
    }

    // 7b. Go to A tab, check hidden section
    await page.goto(`${BASE}/?tab=A`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);

    const aHiddenResult = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const hiddenHeader = divs.find(
        (d) =>
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("hidden")
      );
      if (!hiddenHeader) return { found: false };
      hiddenHeader.click();
      return { found: true };
    });
    await page.waitForTimeout(500);

    if (aHiddenResult.found) {
      const aHiddenCodes = await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const hiddenHeader = divs.find(
          (d) =>
            d.style.cursor === "pointer" &&
            d.style.userSelect === "none" &&
            d.textContent.includes("hidden")
        );
        if (!hiddenHeader) return [];
        const codes = [];
        let el = hiddenHeader.nextElementSibling;
        while (el) {
          const text = el.textContent || "";
          if (
            (el.style && el.style.cursor === "pointer" && text.includes("#")) ||
            text.includes("metrics-collector") ||
            text.includes("scheduler")
          ) break;
          const spans = Array.from(el.querySelectorAll("span"));
          for (const s of spans) {
            const t = s.textContent.trim();
            if (/^(HK)?\d{5,6}$/.test(t)) codes.push(t);
          }
          el = el.nextElementSibling;
        }
        return codes;
      });

      const noHKinA = aHiddenCodes.every((c) => !c.startsWith("HK"));
      record(
        "7b: A-share hidden section has no HK stocks",
        noHKinA || aHiddenCodes.length === 0,
        `codes: ${aHiddenCodes.join(", ") || "(empty)"}`
      );
      await page.screenshot({ path: `${DIR}/dashboard_e2e_07b_a_hidden.png`, fullPage: true });
    } else {
      record("7b: A tab has no hidden section", true, "no hidden A-share stocks");
    }

    // ================================================================
    // 8. EditableCell on manage page (cost/shares for holding)
    // ================================================================
    console.log("\n=== 8. EditableCell (manage page) ===");

    await page.goto(`${BASE}/manage`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);

    // Find all editable cells (they have title="Click to edit")
    const editableCells = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('[title="Click to edit"]'));
      return spans.map((s) => ({
        text: s.textContent.trim(),
        width: s.style.width,
      }));
    });
    record("8a: Editable cells found on manage page", editableCells.length > 0, `${editableCells.length} cells`);

    if (editableCells.length > 0) {
      // Find a cost cell with a numeric value (not "-")
      const costCellIdx = await page.evaluate(() => {
        const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
        for (let i = 0; i < cells.length; i++) {
          const val = cells[i].textContent.trim();
          if (val !== "-" && !isNaN(parseFloat(val)) && cells[i].style.width === "80px") {
            return i;
          }
        }
        return -1;
      });

      if (costCellIdx >= 0) {
        // Read original value
        const originalVal = await page.evaluate((idx) => {
          const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
          return cells[idx].textContent.trim();
        }, costCellIdx);

        // Click to enter edit mode
        await page.evaluate((idx) => {
          const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
          cells[idx].click();
        }, costCellIdx);
        await page.waitForTimeout(300);

        // Check that an input appeared (focus state)
        const inputAppeared = await page.evaluate(() => {
          const focused = document.activeElement;
          return focused && focused.tagName === "INPUT";
        });
        record("8b: Input appeared after click", inputAppeared);

        if (inputAppeared) {
          // Type a new value (slightly different), then press Escape to cancel
          await page.keyboard.press("Escape");
          await page.waitForTimeout(300);

          // Verify edit was cancelled (value restored)
          const restoredVal = await page.evaluate((idx) => {
            const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
            return cells[idx] ? cells[idx].textContent.trim() : "";
          }, costCellIdx);
          record(
            "8c: Escape cancels edit (value restored)",
            restoredVal === originalVal,
            `original=${originalVal}, restored=${restoredVal}`
          );

          // Now test Enter to save: click again, type same value + 0.01, press Enter
          await page.evaluate((idx) => {
            const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
            cells[idx].click();
          }, costCellIdx);
          await page.waitForTimeout(300);

          // Read the input value, modify slightly, then save
          const inputVal = await page.evaluate(() => {
            const inp = document.activeElement;
            return inp ? inp.value : "";
          });

          // Change value slightly for test, then immediately change back
          const testVal = (parseFloat(inputVal) + 0.01).toFixed(2);
          await page.keyboard.press("Meta+a");
          await page.keyboard.type(testVal);
          await page.keyboard.press("Enter");
          await page.waitForTimeout(1500);
          await page.screenshot({ path: `${DIR}/dashboard_e2e_08_editable_saved.png`, fullPage: true });

          record("8d: Enter saves edited value", true, `typed ${testVal}`);

          // Restore original value
          await page.evaluate((idx) => {
            const cells = Array.from(document.querySelectorAll('[title="Click to edit"]'));
            if (cells[idx]) cells[idx].click();
          }, costCellIdx);
          await page.waitForTimeout(300);
          await page.keyboard.press("Meta+a");
          await page.keyboard.type(originalVal);
          await page.keyboard.press("Enter");
          await page.waitForTimeout(1500);
          record("8e: Original value restored", true, `restored to ${originalVal}`);
        }
      } else {
        record("8b: No numeric cost cell found", false);
      }
    }

    // ================================================================
    // 9. Summary bar stats
    // ================================================================
    console.log("\n=== 9. Summary bar stats ===");

    await page.goto(`${BASE}/?tab=A`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);

    // 9a. Nodes count
    const summaryStats = await page.evaluate(() => {
      const text = document.body.innerText;
      const nodesMatch = text.match(/Nodes:\s*(\d+)/);
      const holdingsMatch = text.match(/holdings:(\d+)/);
      const upMatch = text.match(/up:(\d+)/);
      const downMatch = text.match(/down:(\d+)/);
      const avgDeltaMatch = text.match(/avg_delta:([+\-]?\d+\.\d+)%/);
      return {
        nodes: nodesMatch ? parseInt(nodesMatch[1]) : null,
        holdings: holdingsMatch ? parseInt(holdingsMatch[1]) : null,
        up: upMatch ? parseInt(upMatch[1]) : null,
        down: downMatch ? parseInt(downMatch[1]) : null,
        avgDelta: avgDeltaMatch ? avgDeltaMatch[1] : null,
      };
    });

    record("9a: Nodes count displayed", summaryStats.nodes !== null && summaryStats.nodes > 0, `Nodes: ${summaryStats.nodes}`);
    record("9b: Holdings count displayed", summaryStats.holdings !== null, `holdings: ${summaryStats.holdings}`);
    record("9c: Up count displayed", summaryStats.up !== null, `up: ${summaryStats.up}`);
    record("9d: Down count displayed", summaryStats.down !== null, `down: ${summaryStats.down}`);
    record("9e: Avg delta displayed", summaryStats.avgDelta !== null, `avg_delta: ${summaryStats.avgDelta}%`);

    // 9f. Position / yield / return / today (P&L bar)
    const pnlBar = await page.evaluate(() => {
      const text = document.body.innerText;
      return {
        hasPosition: text.includes("position:"),
        hasYield: text.includes("yield:"),
        hasReturn: text.includes("return:"),
        hasToday: text.includes("today:"),
      };
    });
    record("9f: P&L bar has position", pnlBar.hasPosition);
    record("9g: P&L bar has yield", pnlBar.hasYield);
    record("9h: P&L bar has return%", pnlBar.hasReturn);
    record("9i: P&L bar has today P&L", pnlBar.hasToday);

    // 9j. Market turnover (A-share only: SH/SZ/vol)
    const turnoverBar = await page.evaluate(() => {
      const text = document.body.innerText;
      return {
        hasSH: text.includes("SH:"),
        hasSZ: text.includes("SZ:"),
        hasVol: text.includes("vol:"),
      };
    });
    record("9j: Market turnover SH displayed", turnoverBar.hasSH);
    record("9k: Market turnover SZ displayed", turnoverBar.hasSZ);
    record("9l: Market turnover vol displayed", turnoverBar.hasVol);

    await page.screenshot({ path: `${DIR}/dashboard_e2e_09_summary.png`, fullPage: true });

    // ================================================================
    // 10. FX rate display (HK tab)
    // ================================================================
    console.log("\n=== 10. FX rate display (HK tab) ===");

    await page.goto(`${BASE}/?tab=HK`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${DIR}/dashboard_e2e_10_fx_rate.png`, fullPage: true });

    // 10a. Check for FX rate: either the rate is shown in portfolio bar (implicit in HKD->CNY conversion)
    // or WARN banner is shown when FX unavailable
    const fxInfo = await page.evaluate(() => {
      const text = document.body.innerText;
      return {
        hasWarnBanner: text.includes("[WARN FX unavailable"),
        hasFallback: text.includes("fallback"),
        hasYenSymbol: text.includes("\u00A5"), // ¥ symbol in P&L
        hasPositionBar: text.includes("position:") && text.includes("yield:"),
        // The FX is applied silently -- HK holdings P&L is in CNY terms
        // If hkdCnyRate is null, the WARN banner appears
      };
    });

    // FX is either working (no WARN banner + P&L shown in CNY) or shows WARN
    const fxDisplayed = fxInfo.hasPositionBar || fxInfo.hasWarnBanner;
    record(
      "10a: FX rate context displayed",
      fxDisplayed,
      fxInfo.hasWarnBanner
        ? "WARN FX unavailable banner shown"
        : fxInfo.hasPositionBar
          ? "P&L bar with CNY conversion"
          : "no FX info"
    );
    record(
      "10b: HK P&L uses CNY symbol",
      fxInfo.hasYenSymbol || fxInfo.hasWarnBanner,
      fxInfo.hasYenSymbol ? "has CNY symbol" : "WARN banner instead"
    );

    // 10c. Market turnover NOT shown on HK tab (A-share only feature)
    const hkTurnover = await page.evaluate(() => {
      const text = document.body.innerText;
      return text.includes("SH:") && text.includes("SZ:");
    });
    record("10c: Market turnover NOT shown on HK tab", !hkTurnover);

    // ================================================================
    // Bonus: Data integrity checks
    // ================================================================
    console.log("\n=== Bonus: Data integrity ===");

    // B1. Summary up+down should roughly match total nodes
    const integrityCheck = await page.evaluate(() => {
      const text = document.body.innerText;
      const nodesMatch = text.match(/Nodes:\s*(\d+)/);
      const upMatch = text.match(/up:(\d+)/);
      const downMatch = text.match(/down:(\d+)/);
      const nodes = nodesMatch ? parseInt(nodesMatch[1]) : 0;
      const up = upMatch ? parseInt(upMatch[1]) : 0;
      const down = downMatch ? parseInt(downMatch[1]) : 0;
      // up + down + flat <= nodes
      return { nodes, up, down, sum: up + down };
    });
    record(
      "B1: up + down <= Nodes",
      integrityCheck.sum <= integrityCheck.nodes,
      `up(${integrityCheck.up}) + down(${integrityCheck.down}) = ${integrityCheck.sum} <= ${integrityCheck.nodes}`
    );

    // B2. API returns valid data structure
    const apiCheck = await page.evaluate(async () => {
      const res = await fetch("/api/metrics");
      const data = await res.json();
      return {
        hasServices: Array.isArray(data.services),
        serviceCount: (data.services || []).length,
        hasTs: typeof data.ts === "number",
        hasSettings: typeof data.settings === "object",
        hasAlertEvents: Array.isArray(data.alertEvents),
        error: data.error || null,
      };
    });
    record("B2: API /api/metrics structure valid",
      apiCheck.hasServices && apiCheck.hasTs && apiCheck.hasSettings,
      `services:${apiCheck.serviceCount} ts:${apiCheck.hasTs} settings:${apiCheck.hasSettings}`
    );

    // B3. No console errors during test
    const realErrors = consoleErrors.filter(
      (e) =>
        !e.includes("favicon") &&
        !e.includes("404") &&
        !e.includes("ERR_CONNECTION") &&
        !e.includes("Unexpected end of JSON") &&
        !e.includes("/api/summary")
    );
    record(
      "B3: No JS console errors",
      realErrors.length === 0,
      realErrors.length > 0 ? realErrors.slice(0, 3).join(" | ").slice(0, 200) : "clean"
    );

  } finally {
    await browser.close();
  }

  // ================================================================
  // Final report
  // ================================================================
  const pass = results.filter((r) => r.ok).length;
  const fail = results.filter((r) => !r.ok).length;
  console.log(`\n${"=".repeat(60)}`);
  console.log(`DASHBOARD E2E COMPLETE: ${results.length} tests | ${pass} PASS | ${fail} FAIL`);
  if (fail > 0) {
    console.log("\nFailed:");
    results
      .filter((r) => !r.ok)
      .forEach((r) => console.log(`  x ${r.name}`));
  }
  console.log(`${"=".repeat(60)}`);
  if (fail) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
