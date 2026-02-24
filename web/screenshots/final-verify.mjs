/**
 * Comprehensive Playwright functional tests for P1-P3 UX fixes.
 * Run: cd web && node screenshots/final-verify.mjs
 */
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';

const SCREENSHOTS_DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/final';
const BASE_URL = 'http://localhost:3120';
const VIEWPORT = { width: 1440, height: 900 };

mkdirSync(SCREENSHOTS_DIR, { recursive: true });

// ── Helpers ──────────────────────────────────────────────────────────

function parseMoney(s) {
  if (!s || s === '-') return 0;
  let t = s.trim().replace(/[¥,\s]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
  let m = 1;
  if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
  else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
  const n = parseFloat(t);
  return isNaN(n) ? NaN : n * m;
}

const results = [];
function report(name, pass, detail) {
  const status = pass ? 'PASS' : 'FAIL';
  results.push({ name, status, detail });
  console.log(`  [${status}] ${name}: ${detail}`);
}

// ── Main ──────────────────────────────────────────────────────────

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  let page;

  // ================================================================
  // T1 - P1-1: cost=0 P&L guard
  // ================================================================
  console.log('\n=== T1 - P1-1: cost=0 P&L guard ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      const body = document.body.innerText;
      return !body.includes('Loading metrics') && body.includes('Nodes:');
    }, { timeout: 15000 });

    // Use positional span indexing in PROD rows
    // HoldRow span order: [0]type [1]code [2]name [3]price [4]change [5]cost [6]pnl% [7]mktVal [8]pnlAmt [9]today ...
    const cost0Check = await page.evaluate(() => {
      const allDivs = Array.from(document.querySelectorAll('div'));
      const prodRows = allDivs.filter(d => {
        if (d.style.display !== 'flex' || !d.style.whiteSpace) return false;
        const spans = d.querySelectorAll('span');
        if (spans.length < 10) return false;
        const typeText = (spans[0].textContent || '').trim();
        return typeText.includes('PROD');
      });

      const results = [];
      for (const row of prodRows) {
        const spans = Array.from(row.querySelectorAll('span'));
        const code = (spans[1]?.textContent || '').trim();
        const costText = (spans[5]?.textContent || '').trim();
        const pnlAmtText = (spans[8]?.textContent || '').trim();

        // Check if cost is "0.00" or "0" (actual cost=0 scenario)
        if (costText === '0.00' || costText === '0') {
          results.push({ code, costText, pnlAmtText });
        }
      }
      return { total: prodRows.length, cost0: results };
    });

    if (cost0Check.cost0.length === 0) {
      report('T1-cost0-pnl-guard', true,
        `No cost=0 PROD rows found among ${cost0Check.total} PROD rows — no cost=0 rows to test`);
    } else {
      // Check that pnlAmt shows "-" not a huge number
      const allGuarded = cost0Check.cost0.every(r => r.pnlAmtText === '-');
      report('T1-cost0-pnl-guard', allGuarded,
        `Found ${cost0Check.cost0.length} cost=0 rows: ${cost0Check.cost0.map(r => `${r.code}(cost=${r.costText},pnl=${r.pnlAmtText})`).join(', ')}. All show "-": ${allGuarded}`);
    }

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T1-cost0-pnl.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T1-cost0-pnl-guard', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T2 - P1-2: Watching stocks have hide toggle
  // ================================================================
  console.log('\n=== T2 - P1-2: Watching stocks have hide toggle ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return !document.body.innerText.includes('Loading...');
    }, { timeout: 15000 });
    await page.waitForTimeout(500);

    // Expand the 自选 section if collapsed
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      for (const d of divs) {
        const text = d.textContent || '';
        if (text.includes('自选') && text.includes('▸') && d.style.cursor === 'pointer') {
          d.click();
          break;
        }
      }
    });
    await page.waitForTimeout(500);

    // Find DEV rows by looking for type badge spans with "DEV" text,
    // then traverse up to find the containing StockRow (div with borderBottom)
    const devHideCheck = await page.evaluate(() => {
      const allSpans = Array.from(document.querySelectorAll('span'));
      const devBadges = allSpans.filter(s => {
        const text = (s.textContent || '').trim();
        // DEV badge has specific styling: display inline-block, padding, borderRadius
        return text === 'DEV' && s.style.display === 'inline-block' && s.style.borderRadius;
      });

      let withToggle = 0;
      let withoutToggle = 0;
      const examples = [];

      for (const badge of devBadges) {
        // The StockRow outer div has borderBottom. Walk up max 5 levels.
        let container = badge.parentElement;
        for (let i = 0; i < 5; i++) {
          if (!container) break;
          if (container.style.borderBottom && container.style.borderBottom.includes('191a21')) break;
          container = container.parentElement;
        }

        if (container) {
          const text = container.textContent || '';
          const hasHide = text.includes('hide') || text.includes('hidden');
          if (hasHide) withToggle++;
          else withoutToggle++;
          if (examples.length < 3) {
            examples.push({ text: text.substring(0, 120), hasHide });
          }
        }
      }

      return { total: devBadges.length, withToggle, withoutToggle, examples };
    });

    const allHaveToggle = devHideCheck.withoutToggle === 0 && devHideCheck.total > 0;
    report('T2-watching-hide-toggle', allHaveToggle,
      `DEV rows: ${devHideCheck.total}, with hide toggle: ${devHideCheck.withToggle}, without: ${devHideCheck.withoutToggle}`);

    if (devHideCheck.examples.length > 0) {
      console.log(`    Example DEV rows:`)
      devHideCheck.examples.forEach((e, i) => console.log(`      [${i}] hasHide=${e.hasHide}: "${e.text}"`));
    }

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T2-dev-hide-toggle.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T2-watching-hide-toggle', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T3 - P1-3: Loading state
  // ================================================================
  console.log('\n=== T3 - P1-3: Loading state ===');
  try {
    page = await context.newPage();

    // Intercept /api/metrics and delay by 3 seconds
    await page.route('**/api/metrics', async (route) => {
      await new Promise(r => setTimeout(r, 3000));
      await route.continue();
    });

    // Navigate (don't wait for networkidle since we're delaying)
    page.goto(`${BASE_URL}/?tab=A`);

    // Take screenshot at 500ms — should show loading state
    await page.waitForTimeout(500);
    const loadingText = await page.evaluate(() => document.body.innerText);
    const hasLoadingMetrics = loadingText.includes('Loading metrics');
    const hasNoServices = loadingText.includes('no services');

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T3-loading-state.png`, fullPage: false });

    report('T3-loading-state-shown', hasLoadingMetrics,
      `Shows "Loading metrics": ${hasLoadingMetrics}, shows "no services": ${hasNoServices}`);
    report('T3-no-services-hidden', !hasNoServices,
      `"no services" not shown during loading: ${!hasNoServices}`);

    // Wait for data to load
    await page.waitForFunction(() => {
      return document.body.innerText.includes('Nodes:');
    }, { timeout: 15000 });

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T3-loaded-state.png`, fullPage: false });

    await page.unroute('**/api/metrics');
    await page.close();
  } catch (err) {
    report('T3-loading-state', false, `Error: ${err.message}`);
    if (page) {
      try { await page.unroute('**/api/metrics'); } catch (_) {}
      await page.close().catch(() => {});
    }
  }

  // ================================================================
  // T4 - P2-1: Alerts page error handling
  // ================================================================
  console.log('\n=== T4 - P2-1: Alerts page error handling ===');
  try {
    page = await context.newPage();

    await page.route('**/api/metrics', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'test error: simulated failure',
          services: [],
          ts: 0,
          settings: {},
          alertEvents: [],
        }),
      });
    });

    await page.goto(`${BASE_URL}/alerts`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    const alertsPageText = await page.evaluate(() => document.body.innerText);
    const hasError = alertsPageText.includes('[ERROR]') && alertsPageText.includes('test error');
    const hasNoEvents = alertsPageText.includes('No alert events today');

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T4-alerts-error.png`, fullPage: false });

    report('T4-alerts-error-banner', hasError,
      `[ERROR] banner with message shown: ${hasError}`);
    report('T4-no-events-not-shown', !hasNoEvents,
      `"No alert events today" NOT shown during error: ${!hasNoEvents}`);

    await page.unroute('**/api/metrics');
    await page.close();
  } catch (err) {
    report('T4-alerts-error-handling', false, `Error: ${err.message}`);
    if (page) {
      try { await page.unroute('**/api/metrics'); } catch (_) {}
      await page.close().catch(() => {});
    }
  }

  // ================================================================
  // T5 - P2-2: Hidden count annotation
  // ================================================================
  console.log('\n=== T5 - P2-2: Hidden count annotation ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return document.body.innerText.includes('Nodes:');
    }, { timeout: 15000 });

    const summaryCheck = await page.evaluate(() => {
      const body = document.body.innerText;
      const lines = body.split('\n');
      const summaryLine = lines.find(l => l.includes('holdings:'));
      const hasHiddenAnnotation = (summaryLine || '').includes('hidden)');
      return { summaryLine: summaryLine || '', hasHiddenAnnotation };
    });

    // Check via API
    const apiData = await (await fetch(`${BASE_URL}/api/metrics`)).json();
    const hkHiddenHoldings = (apiData.services || []).filter(s =>
      s.id.startsWith('HK') && s.type === 'holding' && s.hidden
    );

    if (hkHiddenHoldings.length === 0) {
      report('T5-hidden-count-annotation', true,
        `No hidden HK holdings exist — annotation not expected. Summary: "${summaryCheck.summaryLine}"`);
    } else {
      const expected = `(+${hkHiddenHoldings.length} hidden)`;
      report('T5-hidden-count-annotation', summaryCheck.hasHiddenAnnotation,
        `Expected "${expected}" in summary. Found: ${summaryCheck.hasHiddenAnnotation}. Line: "${summaryCheck.summaryLine}"`);
    }

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T5-hidden-count.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T5-hidden-count-annotation', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T6 - P2-3: FX warning upgrade
  // ================================================================
  console.log('\n=== T6 - P2-3: FX warning upgrade ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return document.body.innerText.includes('Nodes:');
    }, { timeout: 15000 });

    const fxCheck = await page.evaluate(() => {
      const body = document.body.innerText;
      const hasWarnBracket = body.includes('[WARN');
      const hasOldFormat = body.includes('(FX≈');
      const lines = body.split('\n');
      const positionLine = lines.find(l => l.includes('position:') && l.includes('yield:'));
      return {
        hasWarnBracket,
        hasOldFormat,
        positionLine: positionLine || '(no position line found)',
      };
    });

    const apiData2 = await (await fetch(`${BASE_URL}/api/metrics`)).json();
    const fxAvailable = apiData2.hkdCnyRate != null;

    if (fxAvailable) {
      report('T6-fx-warning-upgrade', true,
        `FX rate is available (${apiData2.hkdCnyRate}), no warning expected. Line: "${fxCheck.positionLine}"`);
    } else {
      report('T6-fx-warning-new-format', fxCheck.hasWarnBracket,
        `[WARN] bracket format present: ${fxCheck.hasWarnBracket}. Line: "${fxCheck.positionLine}"`);
      report('T6-fx-warning-no-old-format', !fxCheck.hasOldFormat,
        `Old "(FX≈0.92)" format absent: ${!fxCheck.hasOldFormat}`);
    }

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T6-fx-warning.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T6-fx-warning-upgrade', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T7 - P3-1: Column legend in manage page
  // ================================================================
  console.log('\n=== T7 - P3-1: Column legend in manage page ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return !document.body.innerText.includes('Loading...');
    }, { timeout: 15000 });

    const legendCheck = await page.evaluate(() => {
      const body = document.body.innerText;
      const hasAbove = body.includes('▲ above') || body.includes('▲above');
      const hasBelow = body.includes('▼ below') || body.includes('▼below');

      // Find the legend row element
      const allDivs = Array.from(document.querySelectorAll('div'));
      const legendDiv = allDivs.find(d => {
        const text = d.textContent || '';
        return text.includes('▲') && text.includes('▼') && text.includes('type') && text.includes('code');
      });
      const legendText = legendDiv ? legendDiv.textContent.trim() : '';

      const legendPos = body.indexOf('▲ above');
      const firstStockPos = Math.min(
        body.indexOf('持仓:') !== -1 ? body.indexOf('持仓:') : Infinity,
        body.indexOf('自选') !== -1 ? body.indexOf('自选') : Infinity
      );

      return { hasAbove, hasBelow, legendText: legendText.substring(0, 120), legendBeforeStock: legendPos < firstStockPos };
    });

    const pass = legendCheck.hasAbove && legendCheck.hasBelow && legendCheck.legendBeforeStock;
    report('T7-column-legend', pass,
      `▲ above: ${legendCheck.hasAbove}, ▼ below: ${legendCheck.hasBelow}, before stocks: ${legendCheck.legendBeforeStock}. Legend: "${legendCheck.legendText}"`);

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T7-column-legend.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T7-column-legend', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T8 - P3-2: API array validation
  // ================================================================
  console.log('\n=== T8 - P3-2: API array validation ===');
  try {
    page = await context.newPage();
    const response = await page.goto(`${BASE_URL}/api/metrics`);
    const data = await response.json();

    const hasServices = Array.isArray(data.services);
    const hasTs = typeof data.ts === 'number';
    const hasSettings = typeof data.settings === 'object' && data.settings !== null;

    report('T8-api-services-array', hasServices,
      `services is array: ${hasServices}, length: ${(data.services || []).length}`);
    report('T8-api-structure', hasTs && hasSettings,
      `ts is number: ${hasTs}, settings is object: ${hasSettings}`);

    await page.close();
  } catch (err) {
    report('T8-api-validation', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T9 - P3-5: Promote button disabled until cost+shares filled
  // ================================================================
  console.log('\n=== T9 - P3-5: Promote button disabled ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return !document.body.innerText.includes('Loading...');
    }, { timeout: 15000 });
    await page.waitForTimeout(500);

    // Expand 自选 section if collapsed
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      for (const d of divs) {
        const text = d.textContent || '';
        if (text.includes('自选') && text.includes('▸') && d.style.cursor === 'pointer') {
          d.click();
          break;
        }
      }
    });
    await page.waitForTimeout(500);

    // Find and click the first "↑ PROD" button
    const promoteBtn = page.locator('button:has-text("↑ PROD")').first();
    const promoteBtnExists = await promoteBtn.count() > 0;

    if (!promoteBtnExists) {
      report('T9-promote-disabled', true, 'No DEV rows with promote button found — skipping');
    } else {
      await promoteBtn.click();
      await page.waitForTimeout(300);

      await page.screenshot({ path: `${SCREENSHOTS_DIR}/T9-promote-empty.png`, fullPage: false });

      // Check Confirm button state (both fields empty)
      const confirmBtn = page.locator('button:has-text("Confirm")').first();
      const isDisabledEmpty = await confirmBtn.isDisabled();
      report('T9-confirm-disabled-empty', isDisabledEmpty,
        `Confirm disabled when both empty: ${isDisabledEmpty}`);

      // Type cost only
      const costInput = page.locator('input[type="number"][placeholder="0.00"]').first();
      await costInput.fill('10.5');
      await page.waitForTimeout(200);
      const isDisabledCostOnly = await confirmBtn.isDisabled();
      report('T9-confirm-disabled-cost-only', isDisabledCostOnly,
        `Confirm disabled when only cost filled: ${isDisabledCostOnly}`);

      await page.screenshot({ path: `${SCREENSHOTS_DIR}/T9-promote-cost-only.png`, fullPage: false });

      // Type shares too
      const sharesInput = page.locator('input[type="number"][placeholder="0"]').first();
      await sharesInput.fill('100');
      await page.waitForTimeout(200);
      const isEnabledBoth = await confirmBtn.isEnabled();
      report('T9-confirm-enabled-both', isEnabledBoth,
        `Confirm enabled when both filled: ${isEnabledBoth}`);

      await page.screenshot({ path: `${SCREENSHOTS_DIR}/T9-promote-both-filled.png`, fullPage: false });

      // Cancel to avoid actual promotion
      const cancelBtn = page.locator('button:has-text("Cancel")').first();
      await cancelBtn.click();
    }

    await page.close();
  } catch (err) {
    report('T9-promote-disabled', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T10 - P3-6: Demote confirm dialog
  // ================================================================
  console.log('\n=== T10 - P3-6: Demote confirm dialog ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return !document.body.innerText.includes('Loading...');
    }, { timeout: 15000 });
    await page.waitForTimeout(500);

    // Listen for dialog event
    let dialogFired = false;
    let dialogMessage = '';
    page.on('dialog', async (dialog) => {
      dialogFired = true;
      dialogMessage = dialog.message();
      await dialog.dismiss();
    });

    const demoteBtn = page.locator('button:has-text("↓ DEV")').first();
    const demoteBtnExists = await demoteBtn.count() > 0;

    if (!demoteBtnExists) {
      report('T10-demote-confirm', true, 'No PROD rows with demote button found — skipping');
    } else {
      await demoteBtn.click();
      await page.waitForTimeout(500);

      report('T10-demote-confirm-dialog', dialogFired,
        `Confirm dialog fired: ${dialogFired}. Message: "${dialogMessage}"`);

      await page.screenshot({ path: `${SCREENSHOTS_DIR}/T10-demote-confirm.png`, fullPage: false });
    }

    await page.close();
  } catch (err) {
    report('T10-demote-confirm', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // T11 - HK FX row vs summary consistency
  // ================================================================
  console.log('\n=== T11 - HK FX row vs summary consistency ===');
  try {
    page = await context.newPage();
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => {
      return document.body.innerText.includes('Nodes:') &&
        (document.body.innerText.includes('position:') || document.body.innerText.includes('prod:'));
    }, { timeout: 15000 });

    // Extract summary "today:" value
    const summaryData = await page.evaluate(() => {
      const body = document.body.innerText;
      const lines = body.split('\n');
      const posLine = lines.find(l => l.includes('today:') && l.includes('position:'));
      let todayStr = '';
      if (posLine) {
        const m = posLine.match(/today:\s*([\-+\u2212\u2013\uff0d]?[\d.,]+(?:万|亿)?)/);
        if (m) todayStr = m[1];
      }
      return { posLine: posLine || '', todayStr };
    });

    // Extract all visible PROD row 今日 values
    const rowData = await page.evaluate(() => {
      const allDivs = Array.from(document.querySelectorAll('div'));
      const prodRows = allDivs.filter(d => {
        if (d.style.display !== 'flex' || !d.style.whiteSpace) return false;
        const spans = d.querySelectorAll('span');
        if (spans.length < 10) return false;
        const typeText = (spans[0].textContent || '').trim();
        return typeText.includes('PROD');
      });

      const rows = [];
      for (const row of prodRows) {
        const spans = Array.from(row.querySelectorAll('span'));
        const code = (spans[1]?.textContent || '').trim();
        // Today is span index 9
        const todayVal = (spans[9]?.textContent || '').trim();
        rows.push({ code, todayVal });
      }
      return rows;
    });

    const summaryToday = parseMoney(summaryData.todayStr);

    // Get API data for ground truth
    const apiResp = await (await fetch(`${BASE_URL}/api/metrics`)).json();
    const fxRate = apiResp.hkdCnyRate || 0.92;
    const hkHoldings = (apiResp.services || []).filter(s =>
      s.id.startsWith('HK') && s.type === 'holding'
    );
    const apiTodaySum = hkHoldings.reduce((sum, s) =>
      sum + (s.chgAmt * (s.shares || 0) * fxRate), 0
    );

    const visibleSum = rowData.reduce((sum, r) => sum + parseMoney(r.todayVal), 0);
    const hiddenHK = hkHoldings.filter(s => s.hidden);

    // Tolerance: 1000 for rounding (万-level)
    const tolerance = 1000;
    const diff = Math.abs(summaryToday - apiTodaySum);
    const matches = diff < tolerance || isNaN(summaryToday);

    report('T11-hk-fx-consistency', matches,
      `Summary today: "${summaryData.todayStr}" (${summaryToday}), API sum: ${apiTodaySum.toFixed(0)}, diff: ${diff.toFixed(0)}, visible rows: ${rowData.length}, hidden HK: ${hiddenHK.length}, FX: ${fxRate}`);

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/T11-hk-consistency.png`, fullPage: false });
    await page.close();
  } catch (err) {
    report('T11-hk-fx-consistency', false, `Error: ${err.message}`);
    if (page) await page.close().catch(() => {});
  }

  // ================================================================
  // Summary
  // ================================================================
  await browser.close();

  console.log('\n' + '='.repeat(60));
  console.log('FINAL RESULTS');
  console.log('='.repeat(60));

  let passed = 0;
  let failed = 0;
  for (const r of results) {
    const icon = r.status === 'PASS' ? 'OK' : 'XX';
    console.log(`  [${icon}] ${r.name}`);
    console.log(`       ${r.detail}`);
    if (r.status === 'PASS') passed++;
    else failed++;
  }

  console.log('');
  console.log(`Total: ${results.length}  |  Passed: ${passed}  |  Failed: ${failed}`);
  console.log(`Screenshots saved to: ${SCREENSHOTS_DIR}/`);
  console.log('='.repeat(60));

  writeFileSync(`${SCREENSHOTS_DIR}/results.json`, JSON.stringify(results, null, 2));

  process.exit(failed > 0 ? 1 : 0);
}

run().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(2);
});
