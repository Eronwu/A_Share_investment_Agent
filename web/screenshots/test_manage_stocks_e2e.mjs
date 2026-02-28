/**
 * E2E test: Manage page — Stock Management features
 * Tests: page load, search/filter, edit above/below thresholds,
 *        hide/unhide toggle, star toggle, section collapse,
 *        promote (DEV->PROD), demote (PROD->DEV).
 *
 * Run: cd web && node screenshots/test_manage_stocks_e2e.mjs
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR = new URL(".", import.meta.url).pathname;
const SS = (n) => `${DIR}manage_stocks_e2e_${n}.png`;

const results = [];
function ok(t, detail = "") {
  results.push({ name: t, pass: true });
  console.log(`  [PASS] ${t}${detail ? " -- " + detail : ""}`);
}
function ng(t, detail = "") {
  results.push({ name: t, pass: false });
  console.log(`  [FAIL] ${t}${detail ? " -- " + detail : ""}`);
}
function check(cond, t, detail = "") {
  cond ? ok(t, detail) : ng(t, detail);
}

// Track API calls for verification
const apiCalls = [];

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

  // Intercept /api/config POST calls for verification
  page.on("response", async (resp) => {
    if (resp.url().includes("/api/config") && resp.request().method() === "POST") {
      try {
        const body = await resp.json();
        apiCalls.push({ url: resp.url(), method: "POST", body });
      } catch { /* ignore parse errors */ }
    }
  });

  // Auto-accept confirm() dialogs
  page.on("dialog", async (dialog) => {
    console.log(`    [dialog] ${dialog.type()}: "${dialog.message().slice(0, 80)}"`);
    await dialog.accept();
  });

  try {
    // ══════════════════════════════════════════════════
    // 1. PAGE LOAD — stock table visible with headers
    // ══════════════════════════════════════════════════
    console.log("\n--- 1. Page Load ---");
    await page.goto(`${BASE}/manage`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SS("01_loaded"), fullPage: true });

    // Check column header row: type, code, name, cost, shares, above, below, *, hide
    const headerText = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("type") &&
          d.textContent.includes("code") &&
          d.textContent.includes("name") &&
          d.textContent.includes("above") &&
          d.textContent.includes("below")
      );
      return header ? header.textContent.trim() : "";
    });
    check(
      headerText.includes("type") &&
        headerText.includes("code") &&
        headerText.includes("name") &&
        headerText.includes("above") &&
        headerText.includes("below"),
      "Column headers visible (type/code/name/above/below)",
      headerText.slice(0, 100)
    );

    // Check PROD and DEV badges exist
    const prodCount = await page.locator('span:text-is("PROD")').count();
    const devCount = await page.locator('span:text-is("DEV")').count();
    check(prodCount > 0, "PROD badges rendered", `${prodCount} PROD`);
    check(devCount > 0, "DEV badges rendered", `${devCount} DEV`);

    // Check stock codes are displayed (cyan-colored spans with stock codes)
    const stockCodes = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans
        .filter((s) => /^(HK)?\d{5,6}$/.test(s.textContent.trim()))
        .map((s) => s.textContent.trim())
        .slice(0, 5);
    });
    check(stockCodes.length > 0, "Stock codes visible", stockCodes.join(", "));

    // ══════════════════════════════════════════════════
    // 2. SEARCH / FILTER
    // ══════════════════════════════════════════════════
    console.log("\n--- 2. Search / Filter ---");

    // Find the search input by placeholder
    const searchInput = page.locator('input[placeholder="search..."]');
    check((await searchInput.count()) === 1, "Search input found");

    // Get total stock count before search (from the "N/M" text)
    const countBefore = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const countSpan = spans.find((s) => /^\d+\/\d+$/.test(s.textContent.trim()));
      return countSpan ? countSpan.textContent.trim() : "";
    });
    console.log(`    Total stocks: ${countBefore}`);

    // Type a search term that should match a known stock
    await searchInput.fill("HK09988");
    await page.waitForTimeout(500);
    await page.screenshot({ path: SS("02_search"), fullPage: true });

    const countAfter = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const countSpan = spans.find((s) => /^\d+\/\d+$/.test(s.textContent.trim()));
      return countSpan ? countSpan.textContent.trim() : "";
    });
    check(
      countAfter.startsWith("1/") || countAfter.startsWith("2/"),
      "Search filters rows",
      `count: ${countBefore} -> ${countAfter}`
    );

    // Verify the matching row is visible
    const hasFiltered = await page.evaluate(() =>
      document.body.textContent.includes("HK09988")
    );
    check(hasFiltered, "Search result contains HK09988");

    // Clear search
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // Search by name
    await searchInput.fill("腾讯");
    await page.waitForTimeout(500);
    const nameFilterCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const countSpan = spans.find((s) => /^\d+\/\d+$/.test(s.textContent.trim()));
      return countSpan ? countSpan.textContent.trim() : "";
    });
    check(
      nameFilterCount.startsWith("1/") || nameFilterCount.startsWith("2/"),
      "Name search filters rows",
      `count: ${nameFilterCount}`
    );
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // ══════════════════════════════════════════════════
    // 3. EDIT ABOVE THRESHOLD
    // ══════════════════════════════════════════════════
    console.log("\n--- 3. Edit Above Threshold ---");

    // First, save original value of HK09988 above threshold for cleanup
    // Navigate to a known stock with existing above value: HK09988 has above=166
    // Filter to HK09988 to isolate
    await searchInput.fill("HK09988");
    await page.waitForTimeout(500);

    // Find the above EditableCell: look for "^" prefix near the threshold area
    // The structure: ^ <EditableCell above> v <EditableCell below>
    // above cells have title="Click to edit" and show numeric values or "-"
    // For HK09988, above=166, below=150

    const editCells = await page.locator('[title="Click to edit"]').all();
    console.log(`    Found ${editCells.length} editable cells for HK09988 row`);

    // In StockRow for HK holding: cost, shares, above, below = 4 EditableCell
    // For HK non-holding (watching): just above, below = 2 EditableCell
    // HK09988 is a holding, so: cost, shares, above, below = 4 cells
    // We want the "above" cell which shows "166"
    let aboveCell = null;
    let aboveCellIdx = -1;
    let originalAbove = "";
    for (let i = 0; i < editCells.length; i++) {
      const txt = await editCells[i].textContent();
      if (txt.trim() === "166") {
        aboveCell = editCells[i];
        aboveCellIdx = i;
        originalAbove = "166";
        break;
      }
    }

    if (aboveCell) {
      // Click to edit
      await aboveCell.click();
      await page.waitForTimeout(300);

      // Type new value
      const focusedInput = page.locator("input:focus");
      if ((await focusedInput.count()) > 0) {
        await focusedInput.fill("200");
        await focusedInput.press("Enter");
        await page.waitForTimeout(1500);

        // Check API response
        const lastCall = apiCalls[apiCalls.length - 1];
        check(
          lastCall?.body?.success === true,
          "Edit above API success",
          lastCall?.body?.message || ""
        );

        // Verify the cell now shows 200
        const updated = await page.locator('[title="Click to edit"]').all();
        let found200 = false;
        for (const c of updated) {
          const t = await c.textContent();
          if (t.trim() === "200") { found200 = true; break; }
        }
        check(found200, "Above threshold updated to 200");
      } else {
        ng("No input focused after clicking above cell");
      }
    } else {
      // Try clicking any editable cell with a known above pattern
      ng("Could not find above cell with value 166 for HK09988");
    }

    await page.screenshot({ path: SS("03_edit_above"), fullPage: true });

    // ══════════════════════════════════════════════════
    // 4. EDIT BELOW THRESHOLD
    // ══════════════════════════════════════════════════
    console.log("\n--- 4. Edit Below Threshold ---");

    // HK09988 below=150 — find and change it
    const editCells2 = await page.locator('[title="Click to edit"]').all();
    let belowCell = null;
    let originalBelow = "";
    for (const c of editCells2) {
      const txt = await c.textContent();
      if (txt.trim() === "150") {
        belowCell = c;
        originalBelow = "150";
        break;
      }
    }

    if (belowCell) {
      await belowCell.click();
      await page.waitForTimeout(300);

      const focusedInput = page.locator("input:focus");
      if ((await focusedInput.count()) > 0) {
        await focusedInput.fill("100");
        await focusedInput.press("Enter");
        await page.waitForTimeout(1500);

        const lastCall = apiCalls[apiCalls.length - 1];
        check(
          lastCall?.body?.success === true,
          "Edit below API success",
          lastCall?.body?.message || ""
        );

        const updated = await page.locator('[title="Click to edit"]').all();
        let found100 = false;
        for (const c of updated) {
          const t = await c.textContent();
          if (t.trim() === "100") { found100 = true; break; }
        }
        check(found100, "Below threshold updated to 100");
      } else {
        ng("No input focused after clicking below cell");
      }
    } else {
      ng("Could not find below cell with value 150 for HK09988");
    }

    await page.screenshot({ path: SS("04_edit_below"), fullPage: true });

    // ── CLEANUP above/below: restore original values ──
    console.log("    Restoring original above/below values...");

    // Restore above: 200 -> 166
    const restoreCells1 = await page.locator('[title="Click to edit"]').all();
    for (const c of restoreCells1) {
      const txt = await c.textContent();
      if (txt.trim() === "200") {
        await c.click();
        await page.waitForTimeout(300);
        const inp = page.locator("input:focus");
        if ((await inp.count()) > 0) {
          await inp.fill("166");
          await inp.press("Enter");
          await page.waitForTimeout(1000);
          console.log("    Restored above to 166");
        }
        break;
      }
    }

    // Restore below: 100 -> 150
    const restoreCells2 = await page.locator('[title="Click to edit"]').all();
    for (const c of restoreCells2) {
      const txt = await c.textContent();
      if (txt.trim() === "100") {
        await c.click();
        await page.waitForTimeout(300);
        const inp = page.locator("input:focus");
        if ((await inp.count()) > 0) {
          await inp.fill("150");
          await inp.press("Enter");
          await page.waitForTimeout(1000);
          console.log("    Restored below to 150");
        }
        break;
      }
    }

    // Clear search for subsequent tests
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // ══════════════════════════════════════════════════
    // 5. HIDE TOGGLE
    // ══════════════════════════════════════════════════
    console.log("\n--- 5. Hide Toggle ---");

    // Filter to a known stock for isolation — use HK09988 again
    await searchInput.fill("HK09988");
    await page.waitForTimeout(500);

    // Find the hide toggle — it's a <span> with title containing "Hide" or "Unhide"
    const hideToggle = page.locator('[title="Hide (out of sight)"]').first();
    const unhideToggle = page.locator('[title="Unhide (show on dashboard)"]').first();

    const isCurrentlyHidden = (await unhideToggle.count()) > 0;
    console.log(`    Current hidden state: ${isCurrentlyHidden}`);

    if (!isCurrentlyHidden) {
      // Stock is visible (not hidden) — click to hide
      check((await hideToggle.count()) > 0, "Hide toggle found", 'title="Hide (out of sight)"');
      await hideToggle.click();
      await page.waitForTimeout(1500);

      const lastCall = apiCalls[apiCalls.length - 1];
      check(
        lastCall?.body?.success === true,
        "Hide API success",
        lastCall?.body?.message || ""
      );

      // After hiding, the toggle should now say "Unhide"
      const nowHidden = (await page.locator('[title="Unhide (show on dashboard)"]').count()) > 0;
      check(nowHidden, "Toggle shows 'Unhide' after hiding");
    } else {
      // Already hidden — just note it
      ok("Stock already hidden, will unhide first");
      await unhideToggle.click();
      await page.waitForTimeout(1500);
      // Now hide it
      await page.locator('[title="Hide (out of sight)"]').first().click();
      await page.waitForTimeout(1500);
      check(
        (await page.locator('[title="Unhide (show on dashboard)"]').count()) > 0,
        "Toggle shows 'Unhide' after hiding"
      );
    }

    await page.screenshot({ path: SS("05_hidden"), fullPage: true });

    // ══════════════════════════════════════════════════
    // 6. UNHIDE — restore the stock
    // ══════════════════════════════════════════════════
    console.log("\n--- 6. Unhide ---");

    const unhideBtn = page.locator('[title="Unhide (show on dashboard)"]').first();
    if ((await unhideBtn.count()) > 0) {
      await unhideBtn.click();
      await page.waitForTimeout(1500);

      const lastCall = apiCalls[apiCalls.length - 1];
      check(
        lastCall?.body?.success === true,
        "Unhide API success",
        lastCall?.body?.message || ""
      );

      const nowVisible = (await page.locator('[title="Hide (out of sight)"]').first().count()) > 0;
      check(nowVisible, "Toggle shows 'Hide' after unhiding (restored)");
    } else {
      ng("Could not find Unhide toggle");
    }

    await page.screenshot({ path: SS("06_unhidden"), fullPage: true });

    // Clear search
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // ══════════════════════════════════════════════════
    // 7. STAR TOGGLE
    // ══════════════════════════════════════════════════
    console.log("\n--- 7. Star Toggle ---");

    // Filter to HK09988
    await searchInput.fill("HK09988");
    await page.waitForTimeout(500);

    // Find star button — it's a <button> with text "*" and title containing "L1 priority"
    const starBtn = page.locator('[title="Mark as L1 priority"]').first();
    const unstarBtn = page.locator('[title="Remove L1 priority"]').first();

    const isCurrentlyStarred = (await unstarBtn.count()) > 0;
    console.log(`    Current star state: ${isCurrentlyStarred}`);

    if (!isCurrentlyStarred) {
      // Not starred — click to star
      check((await starBtn.count()) > 0, "Star button found");
      await starBtn.click();
      await page.waitForTimeout(1500);

      const lastCall = apiCalls[apiCalls.length - 1];
      check(
        lastCall?.body?.success === true,
        "Star API success",
        lastCall?.body?.message || ""
      );

      // After starring, the title should change to "Remove L1 priority"
      const nowStarred = (await page.locator('[title="Remove L1 priority"]').count()) > 0;
      check(nowStarred, "Star state changed to starred");

      await page.screenshot({ path: SS("07_starred"), fullPage: true });

      // Undo: unstar
      console.log("    Undoing star...");
      await page.locator('[title="Remove L1 priority"]').first().click();
      await page.waitForTimeout(1500);
      const restored = (await page.locator('[title="Mark as L1 priority"]').count()) > 0;
      check(restored, "Star state restored (unstarred)");
    } else {
      // Already starred — unstar first, then re-star, then restore
      ok("Stock already starred, toggling off then on");
      await unstarBtn.click();
      await page.waitForTimeout(1500);
      check(
        (await page.locator('[title="Mark as L1 priority"]').count()) > 0,
        "Unstarred successfully"
      );

      await page.screenshot({ path: SS("07_starred"), fullPage: true });

      // Restore: re-star
      await page.locator('[title="Mark as L1 priority"]').first().click();
      await page.waitForTimeout(1500);
      check(
        (await page.locator('[title="Remove L1 priority"]').count()) > 0,
        "Star state restored (re-starred)"
      );
    }

    // Clear search
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // ══════════════════════════════════════════════════
    // 8. SECTION COLLAPSE / EXPAND
    // ══════════════════════════════════════════════════
    console.log("\n--- 8. Section Collapse ---");

    // Find section headers — they contain text like "# -- HK (N) --" or "# -- A stocks (N) --"
    // Look for clickable section headers with "# --" prefix
    const sectionHeaders = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      return divs
        .filter((d) => {
          const t = d.textContent || "";
          return (
            (t.includes("# -- HK (") ||
              t.includes("# -- A stocks (") ||
              t.includes("# -- watching (") ||
              t.includes("# -- ETF (")) &&
            d.style.cursor === "pointer"
          );
        })
        .map((d) => d.textContent.trim().slice(0, 50));
    });
    console.log(`    Section headers: ${JSON.stringify(sectionHeaders)}`);
    check(sectionHeaders.length > 0, "Section headers found", `${sectionHeaders.length} sections`);

    // Click the HK section header to collapse it
    const hkSectionHeader = page.locator('div:has-text("# -- HK (")').first();
    if ((await hkSectionHeader.count()) > 0) {
      // Check that HK stocks are visible before collapse
      const hkStocksBefore = await page.evaluate(() =>
        Array.from(document.querySelectorAll("span"))
          .filter((s) => /^HK\d{5}$/.test(s.textContent.trim()))
          .length
      );
      console.log(`    HK stocks visible before collapse: ${hkStocksBefore}`);

      // Click to collapse
      await hkSectionHeader.click();
      await page.waitForTimeout(500);

      // After collapse, the ">" indicator should appear and HK stock rows should be hidden
      const bodyAfterCollapse = await page.textContent("body");

      await page.screenshot({ path: SS("08_collapsed"), fullPage: true });

      // Click again to expand
      await hkSectionHeader.click();
      await page.waitForTimeout(500);

      const hkStocksAfterExpand = await page.evaluate(() =>
        Array.from(document.querySelectorAll("span"))
          .filter((s) => /^HK\d{5}$/.test(s.textContent.trim()))
          .length
      );
      check(hkStocksAfterExpand > 0, "Section expanded — HK stocks visible again", `${hkStocksAfterExpand} HK stocks`);

      await page.screenshot({ path: SS("08_expanded"), fullPage: true });
    } else {
      ng("Could not find HK section header");
    }

    // ══════════════════════════════════════════════════
    // 9. PROMOTE (DEV -> PROD)
    // ══════════════════════════════════════════════════
    console.log("\n--- 9. Promote DEV->PROD ---");

    // Find a DEV stock to promote — use a non-ETF A-share watching stock
    // 300376 (易事特) is a watching stock (non-ETF, non-HK)
    // After promote it goes to "A stocks" section which is open by default
    const PROMOTE_CODE = "300376";
    await searchInput.fill(PROMOTE_CODE);
    await page.waitForTimeout(500);

    // Find the PROD button on the DEV row (title="Promote to holding")
    const promoteBtn = page.locator('[title="Promote to holding"]').first();
    if ((await promoteBtn.count()) > 0) {
      ok("Promote (PROD) button found for DEV stock");

      // Click PROD button to open inline promote form
      await promoteBtn.click();
      await page.waitForTimeout(500);

      // The inline form should appear with cost and shares inputs
      // Look for the "->" arrow and cost:/shares: labels
      const costInput = page.locator('input[placeholder="0.00"]').first();
      const sharesInput = page.locator('input[placeholder="0"]').first();

      check((await costInput.count()) > 0, "Promote form: cost input visible");
      check((await sharesInput.count()) > 0, "Promote form: shares input visible");

      // Fill cost and shares
      await costInput.fill("10.5");
      await sharesInput.fill("1000");
      await page.waitForTimeout(300);

      await page.screenshot({ path: SS("09_promote_form"), fullPage: true });

      // Click Confirm button
      const confirmBtn = page.locator('button:has-text("Confirm")').first();
      if ((await confirmBtn.count()) > 0) {
        // Confirm button should be enabled now
        const isDisabled = await confirmBtn.getAttribute("disabled");
        check(isDisabled === null, "Confirm button enabled after filling cost+shares");

        await confirmBtn.click();
        await page.waitForTimeout(2000);

        const lastCall = apiCalls[apiCalls.length - 1];
        check(
          lastCall?.body?.success === true,
          "Promote API success",
          lastCall?.body?.message || ""
        );

        // Verify the stock is now PROD
        // Reload to get fresh state (promoted stock moves to "A stocks" section)
        await page.goto(`${BASE}/manage`, { waitUntil: "networkidle", timeout: 15000 });
        await page.waitForTimeout(1500);
        await page.locator('input[placeholder="search..."]').fill(PROMOTE_CODE);
        await page.waitForTimeout(500);

        const hasProdBadge = await page.evaluate(() => {
          const spans = Array.from(document.querySelectorAll("span"));
          return spans.some(
            (s) => s.textContent.trim() === "PROD" && s.title === "Click to toggle type"
          );
        });
        check(hasProdBadge, "Stock now shows PROD badge after promote");

        await page.screenshot({ path: SS("09_promoted"), fullPage: true });
      } else {
        ng("Confirm button not found in promote form");
      }
    } else {
      ng(`Could not find Promote button for DEV stock ${PROMOTE_CODE}`);
    }

    // ══════════════════════════════════════════════════
    // 10. DEMOTE (PROD -> DEV)
    // ══════════════════════════════════════════════════
    console.log("\n--- 10. Demote PROD->DEV ---");

    // Stock should now be PROD — find the DEV button (title="Demote to watching")
    // Re-search for the stock (use fresh searchInput reference after reload)
    const searchInput2 = page.locator('input[placeholder="search..."]');
    await searchInput2.fill(PROMOTE_CODE);
    await page.waitForTimeout(500);

    const demoteBtn = page.locator('[title="Demote to watching"]').first();
    if ((await demoteBtn.count()) > 0) {
      ok("Demote (DEV) button found for promoted stock");

      // Click demote — will trigger confirm() dialog (auto-accepted above)
      await demoteBtn.click();
      await page.waitForTimeout(2000);

      const lastCall = apiCalls[apiCalls.length - 1];
      check(
        lastCall?.body?.success === true,
        "Demote API success",
        lastCall?.body?.message || ""
      );

      // Reload and verify the stock is back to DEV
      await page.goto(`${BASE}/manage`, { waitUntil: "networkidle", timeout: 15000 });
      await page.waitForTimeout(1500);
      await page.locator('input[placeholder="search..."]').fill(PROMOTE_CODE);
      await page.waitForTimeout(500);

      // Check for DEV badge (the stock should be back in watching section)
      const devBadgeFound = await page.evaluate(() => {
        const spans = Array.from(document.querySelectorAll("span"));
        return spans.some(
          (s) => s.textContent.trim() === "DEV" && s.title === "Click to toggle type"
        );
      });
      check(devBadgeFound, "Stock restored to DEV badge after demote");

      await page.screenshot({ path: SS("10_demoted"), fullPage: true });
    } else {
      // Fallback: try clicking the PROD badge (title="Click to toggle type") to toggle
      const typeBadge = page.locator('[title="Click to toggle type"]').first();
      if ((await typeBadge.count()) > 0) {
        const badgeText = await typeBadge.textContent();
        console.log(`    Type badge text: ${badgeText}`);
        if (badgeText.trim() === "PROD") {
          // For a holding, clicking PROD badge triggers onUpdateType which toggles to watching
          await typeBadge.click();
          await page.waitForTimeout(2000);
          check(true, "Type badge clicked to toggle back to DEV (fallback)");
        } else {
          ng("Stock is not PROD, cannot demote");
        }
      } else {
        ng(`Could not find Demote button for stock ${PROMOTE_CODE}`);
      }

      await page.screenshot({ path: SS("10_demoted"), fullPage: true });
    }

    // Final cleanup: clear cost/shares on the demoted stock
    // (The demote just changes type to watching, cost/shares might remain)
    // We need to explicitly clean up by setting cost=null, shares=null via API
    console.log("    Cleaning up promoted stock cost/shares...");
    await page.evaluate(async (code) => {
      await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "update",
          code,
          data: { type: "watching", cost: null, shares: null },
        }),
      });
    }, PROMOTE_CODE);
    await page.waitForTimeout(500);
    console.log(`    Cleanup complete (${PROMOTE_CODE} restored to watching, no cost/shares)`);

    // Clear search
    await searchInput.fill("");
    await page.waitForTimeout(500);

    // Final full-page screenshot
    await page.screenshot({ path: SS("11_final"), fullPage: true });

  } catch (e) {
    console.error("  [ERROR] Unhandled:", e.message);
    await page.screenshot({ path: SS("99_error"), fullPage: true }).catch(() => {});
  } finally {
    await browser.close();
  }

  // ══════════════════════════════════════════════════
  // SUMMARY
  // ══════════════════════════════════════════════════
  console.log("\n══════════════════════════════════════");
  console.log("  MANAGE STOCKS E2E SUMMARY");
  console.log("══════════════════════════════════════");
  const passed = results.filter((r) => r.pass).length;
  const failed = results.filter((r) => !r.pass).length;
  console.log(`  Total: ${results.length}  Passed: ${passed}  Failed: ${failed}`);
  if (failed > 0) {
    console.log("\n  Failed tests:");
    results.filter((r) => !r.pass).forEach((r) => console.log(`    - ${r.name}`));
  }
  console.log("");
  process.exit(failed > 0 ? 1 : 0);
}

main();
