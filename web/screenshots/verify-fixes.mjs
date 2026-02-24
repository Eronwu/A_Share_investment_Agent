/**
 * Playwright verification script for P1-P3 fixes.
 *
 * Tests:
 *  1. P1-3: Loading state — "Loading metrics" text at 200ms instead of "no services"
 *  2. P1-2: Watching hide toggle — DEV rows have a hide toggle on /manage
 *  3. P2-2: Hidden count annotation — "(+N hidden)" in HK summary bar
 *  4. P2-3: FX warning — "[WARN" text on HK tab portfolio summary
 *  5. P3-1: Column legend — "▲ above" and "▼ below" on /manage
 *  6. P3-5: Promote button — Confirm button disabled when cost/shares empty
 *
 * Run: node screenshots/verify-fixes.mjs
 */

import { chromium } from "playwright";
import { mkdirSync } from "fs";
import { join } from "path";

const BASE_URL = "http://localhost:3120";
const SCREENSHOT_DIR = join(
  "/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/verify"
);
const VIEWPORT = { width: 1440, height: 900 };

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const results = [];

function report(name, passed, detail = "") {
  const status = passed ? "PASS" : "FAIL";
  results.push({ name, status, detail });
  console.log(`  [${status}] ${name}${detail ? " — " + detail : ""}`);
}

function ssPath(name) {
  return join(SCREENSHOT_DIR, `${name}.png`);
}

(async () => {
  console.log("\n=== P1-P3 Fix Verification ===\n");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });

  // ── Test 1: P1-3 Loading state ──
  console.log("Test 1: P1-3 Loading state");
  try {
    const page = await context.newPage();
    // Block the metrics API to ensure loading state persists
    await page.route("**/api/metrics*", (route) => {
      // Delay the response so we can capture loading state
      setTimeout(() => route.continue(), 5000);
    });
    await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: "commit" });
    // Wait 200ms for initial render
    await page.waitForTimeout(200);
    await page.screenshot({ path: ssPath("01-loading-state"), fullPage: false });

    const bodyText = await page.textContent("body");
    const hasLoadingText = bodyText.includes("Loading metrics");
    const hasNoServices = bodyText.includes("no services");

    if (hasLoadingText && !hasNoServices) {
      report("P1-3: Loading state shows 'Loading metrics'", true);
    } else if (hasLoadingText && hasNoServices) {
      report("P1-3: Loading state shows 'Loading metrics'", true, "but 'no services' also present");
    } else {
      report(
        "P1-3: Loading state shows 'Loading metrics'",
        false,
        `hasLoadingText=${hasLoadingText}, hasNoServices=${hasNoServices}`
      );
    }
    await page.close();
  } catch (err) {
    report("P1-3: Loading state", false, err.message);
  }

  // ── Test 2: P1-2 Watching hide toggle ──
  console.log("\nTest 2: P1-2 Watching hide toggle");
  try {
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);

    // Scroll to watching/自选 section
    const watchingSection = page.locator("text=自选");
    if (await watchingSection.count() > 0) {
      await watchingSection.first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
    }

    await page.screenshot({ path: ssPath("02-manage-watching-section"), fullPage: true });

    // Find DEV rows and check for hide toggle
    const devBadges = page.locator('span:text-is("DEV")');
    const devCount = await devBadges.count();

    if (devCount === 0) {
      report("P1-2: Watching hide toggle", false, "No DEV rows found on /manage");
    } else {
      // Check the first DEV row's parent for hide toggle
      // The StockRow renders hide toggle as a span with text "hide" or "hidden"
      let foundHideToggle = false;
      for (let i = 0; i < Math.min(devCount, 3); i++) {
        const devBadge = devBadges.nth(i);
        // Go up to the row container (the outermost div with borderBottom)
        const rowContainer = devBadge.locator("xpath=ancestor::div[contains(@style, 'border-bottom')]").first();
        const rowText = await rowContainer.textContent();
        if (rowText.includes("hide") || rowText.includes("hidden")) {
          foundHideToggle = true;
          break;
        }
      }
      report("P1-2: Watching hide toggle exists on DEV rows", foundHideToggle,
        foundHideToggle ? `Found in ${devCount} DEV rows` : "No hide toggle text found in DEV rows"
      );
    }
    await page.close();
  } catch (err) {
    report("P1-2: Watching hide toggle", false, err.message);
  }

  // ── Test 3: P2-2 Hidden count annotation ──
  console.log("\nTest 3: P2-2 Hidden count annotation");
  try {
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: ssPath("03-hk-hidden-count"), fullPage: false });

    const bodyText = await page.textContent("body");
    // Look for "(+N hidden)" pattern
    const hiddenPattern = /\(\+\d+ hidden\)/;
    const hasHiddenAnnotation = hiddenPattern.test(bodyText);

    if (hasHiddenAnnotation) {
      const match = bodyText.match(hiddenPattern);
      report("P2-2: Hidden count annotation on HK tab", true, `Found: ${match[0]}`);
    } else {
      // Check if there are any hidden HK stocks — if none, annotation won't appear
      const hasHoldings = bodyText.includes("holdings:");
      report(
        "P2-2: Hidden count annotation on HK tab",
        false,
        hasHoldings
          ? "Holdings found but no (+N hidden) text — maybe no hidden HK stocks"
          : "No holdings section found"
      );
    }
    await page.close();
  } catch (err) {
    report("P2-2: Hidden count annotation", false, err.message);
  }

  // ── Test 4: P2-3 FX warning ──
  console.log("\nTest 4: P2-3 FX warning");
  try {
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: ssPath("04-hk-fx-warning"), fullPage: false });

    const bodyText = await page.textContent("body");
    const hasWarnFX = bodyText.includes("[WARN");

    report(
      "P2-3: FX warning on HK tab",
      hasWarnFX,
      hasWarnFX
        ? "Found [WARN text in portfolio summary"
        : "No [WARN text found — FX rate may be available (poller running)"
    );
    await page.close();
  } catch (err) {
    report("P2-3: FX warning", false, err.message);
  }

  // ── Test 5: P3-1 Column legend ──
  console.log("\nTest 5: P3-1 Column legend");
  try {
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.screenshot({ path: ssPath("05-manage-column-legend"), fullPage: false });

    const bodyText = await page.textContent("body");
    const hasAbove = bodyText.includes("▲ above");
    const hasBelow = bodyText.includes("▼ below");

    report(
      "P3-1: Column legend on /manage",
      hasAbove && hasBelow,
      `▲ above: ${hasAbove ? "found" : "missing"}, ▼ below: ${hasBelow ? "found" : "missing"}`
    );
    await page.close();
  } catch (err) {
    report("P3-1: Column legend", false, err.message);
  }

  // ── Test 6: P3-5 Promote button disabled ──
  console.log("\nTest 6: P3-5 Promote button disabled");
  try {
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);

    // Find a DEV row and click its "↑ PROD" button
    const promoteBtn = page.locator('button:has-text("↑ PROD")');
    const promoteCount = await promoteBtn.count();

    if (promoteCount === 0) {
      report("P3-5: Promote button disabled", false, "No DEV rows with ↑ PROD button found");
    } else {
      // Click the first promote button
      await promoteBtn.first().click();
      await page.waitForTimeout(300);

      // Find the Confirm button in the promote form
      const confirmBtn = page.locator('button:has-text("Confirm")');
      const confirmCount = await confirmBtn.count();

      if (confirmCount === 0) {
        report("P3-5: Promote button disabled", false, "Confirm button not found after clicking ↑ PROD");
      } else {
        const isDisabled = await confirmBtn.first().isDisabled();
        await page.screenshot({ path: ssPath("06-promote-confirm-disabled"), fullPage: true });

        report(
          "P3-5: Confirm button disabled when cost/shares empty",
          isDisabled,
          isDisabled
            ? "Confirm button is correctly disabled"
            : "Confirm button is NOT disabled — cost/shares may have values"
        );
      }
    }
    await page.close();
  } catch (err) {
    report("P3-5: Promote button disabled", false, err.message);
  }

  // ── Summary ──
  await browser.close();

  console.log("\n=== Summary ===\n");
  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  for (const r of results) {
    console.log(`  [${r.status}] ${r.name}`);
  }
  console.log(`\nTotal: ${passed} PASS, ${failed} FAIL out of ${results.length} tests`);
  console.log(`Screenshots saved to: ${SCREENSHOT_DIR}/`);

  process.exit(failed > 0 ? 1 : 0);
})();
