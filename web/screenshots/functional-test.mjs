/**
 * Comprehensive Playwright functional tests for 3 UX fixes:
 *   C1 - Manage page above/below alert editing
 *   C3 - Dashboard error handling
 *   H7 - HK tab FX rate consistency (P&L sum)
 *
 * Run: node functional-test.mjs
 */
import { chromium } from 'playwright';

const SCREENSHOTS_DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots';
const BASE_URL = 'http://localhost:3120';
const VIEWPORT = { width: 1440, height: 900 };

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Parse a Chinese money string like "+1.2万", "-3,587", "−800", "+7,058¥" into a raw number.
 * 万 = 10000, 亿 = 100000000. Strips commas, ¥, +, whitespace.
 */
function parseMoney(s) {
  if (!s || s === '-' || s === '—') return 0;
  let text = s.trim()
    .replace(/¥/g, '')
    .replace(/,/g, '')
    .replace(/\s/g, '')
    .replace(/\u2212/g, '-')   // Unicode minus
    .replace(/\u2013/g, '-')   // en-dash
    .replace(/\uff0d/g, '-');  // fullwidth minus

  let multiplier = 1;
  if (text.includes('亿')) {
    multiplier = 1e8;
    text = text.replace('亿', '');
  } else if (text.includes('万')) {
    multiplier = 1e4;
    text = text.replace('万', '');
  }

  const num = parseFloat(text);
  if (isNaN(num)) return NaN;
  return num * multiplier;
}

/** Scroll the main terminal content area to the top */
async function scrollToTop(page) {
  await page.evaluate(() => {
    const containers = document.querySelectorAll('div');
    for (const c of containers) {
      const style = window.getComputedStyle(c);
      if (
        (style.overflow === 'auto' || style.overflowY === 'auto' ||
         style.overflow === 'scroll' || style.overflowY === 'scroll') &&
        c.scrollHeight > c.clientHeight + 100
      ) {
        c.scrollTop = 0;
      }
    }
  });
  await page.waitForTimeout(500);
}

function log(msg) {
  console.log(`  ${msg}`);
}

// ── Results tracker ──────────────────────────────────────────────────

const results = [];
function recordResult(testName, passed, detail = '') {
  results.push({ testName, passed, detail });
  const status = passed ? 'PASS' : 'FAIL';
  console.log(`  [${status}] ${testName}${detail ? ' -- ' + detail : ''}`);
}

// ── Test 1: Manage page above/below alert editing (C1) ──────────────

async function testManageAlertEditing(page) {
  console.log('\n========================================');
  console.log('TEST 1: Manage page above/below editing (C1)');
  console.log('========================================');

  try {
    await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    // Step 1: Verify triangle indicators and EditableCell elements exist
    log('Step 1: Checking for triangle indicators and editable cells...');

    const triangleUp = await page.locator('span:has-text("▲")').count();
    const triangleDown = await page.locator('span:has-text("▼")').count();
    log(`  Found ${triangleUp} up-indicators, ${triangleDown} down-indicators`);

    recordResult(
      'C1.1 -- Triangle indicators exist',
      triangleUp > 0 && triangleDown > 0,
      `up: ${triangleUp}, down: ${triangleDown}`
    );

    const editableCells = await page.locator('span[title="Click to edit"]').count();
    log(`  Found ${editableCells} EditableCell elements`);
    recordResult(
      'C1.2 -- EditableCell elements exist',
      editableCells > 0,
      `count: ${editableCells}`
    );

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/C1-01-manage-initial.png`,
      fullPage: true,
    });
    log('  Screenshot: C1-01-manage-initial.png');

    // Step 2: Find first PROD row and its above EditableCell
    log('Step 2: Finding first PROD row and editing "above" threshold...');

    // Row pattern: PROD | code | name | cost(editable,w=80px) | shares(editable,w=80px) |
    //   [demote btn] | ▲ | above(editable,w=60px) | ▼ | below(editable,w=60px) | ...
    const firstProdAbove = await page.evaluate(() => {
      const allSpans = document.querySelectorAll('span');
      let foundProd = false;
      let foundTriangleUp = false;

      for (const span of allSpans) {
        const text = span.textContent.trim();
        const style = span.getAttribute('style') || '';

        if (!foundProd && text === 'PROD' && style.includes('font-weight')) {
          foundProd = true;
          continue;
        }
        if (foundProd && !foundTriangleUp && text === '▲') {
          foundTriangleUp = true;
          continue;
        }
        if (foundProd && foundTriangleUp && span.getAttribute('title') === 'Click to edit' && style.includes('60px')) {
          return {
            found: true,
            currentValue: text,
            index: Array.from(document.querySelectorAll('span[title="Click to edit"]')).indexOf(span)
          };
        }
      }
      return { found: false };
    });

    if (!firstProdAbove.found) {
      recordResult('C1.3 -- Found PROD above editable cell', false, 'Could not locate above EditableCell');
      return;
    }

    log(`  Found above cell at editable index ${firstProdAbove.index}, current value: "${firstProdAbove.currentValue}"`);
    recordResult('C1.3 -- Found PROD above editable cell', true, `value: "${firstProdAbove.currentValue}"`);

    // Step 3: Click to enter edit mode, type "999", press Enter
    log('Step 3: Clicking to edit, typing "999", pressing Enter...');

    const aboveCell = page.locator('span[title="Click to edit"]').nth(firstProdAbove.index);
    await aboveCell.click();
    await page.waitForTimeout(500);

    await page.keyboard.press('Meta+A');
    await page.waitForTimeout(100);
    await page.keyboard.type('999');
    await page.waitForTimeout(200);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/C1-02-editing-above.png`,
      fullPage: true,
    });
    log('  Screenshot: C1-02-editing-above.png');

    await page.keyboard.press('Enter');
    await page.waitForTimeout(1000);

    const savedValue = await page.evaluate(() => {
      const spans = document.querySelectorAll('span');
      let foundProd = false;
      let foundTriangleUp = false;
      for (const span of spans) {
        const text = span.textContent.trim();
        const style = span.getAttribute('style') || '';
        if (!foundProd && text === 'PROD' && style.includes('font-weight')) { foundProd = true; continue; }
        if (foundProd && !foundTriangleUp && text === '▲') { foundTriangleUp = true; continue; }
        if (foundProd && foundTriangleUp && span.getAttribute('title') === 'Click to edit' && style.includes('60px')) return text;
      }
      return null;
    });

    log(`  Value after save: "${savedValue}"`);
    recordResult('C1.4 -- Value saved to "999"', savedValue === '999', `got: "${savedValue}"`);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/C1-03-after-save.png`,
      fullPage: true,
    });
    log('  Screenshot: C1-03-after-save.png');

    // Step 4: Reload and verify persistence
    log('Step 4: Reloading page to verify persistence...');
    await page.waitForTimeout(1000);
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    const persistedValue = await page.evaluate(() => {
      const spans = document.querySelectorAll('span');
      let foundProd = false;
      let foundTriangleUp = false;
      for (const span of spans) {
        const text = span.textContent.trim();
        const style = span.getAttribute('style') || '';
        if (!foundProd && text === 'PROD' && style.includes('font-weight')) { foundProd = true; continue; }
        if (foundProd && !foundTriangleUp && text === '▲') { foundTriangleUp = true; continue; }
        if (foundProd && foundTriangleUp && span.getAttribute('title') === 'Click to edit' && style.includes('60px')) return text;
      }
      return null;
    });

    log(`  Value after reload: "${persistedValue}"`);
    recordResult('C1.5 -- Value persists after reload', persistedValue === '999', `got: "${persistedValue}"`);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/C1-04-after-reload.png`,
      fullPage: true,
    });
    log('  Screenshot: C1-04-after-reload.png');

    // Step 5: Clear the value
    log('Step 5: Clearing the value...');

    const aboveCellAfterReload = await page.evaluate(() => {
      const spans = document.querySelectorAll('span');
      let foundProd = false;
      let foundTriangleUp = false;
      for (const span of spans) {
        const text = span.textContent.trim();
        const style = span.getAttribute('style') || '';
        if (!foundProd && text === 'PROD' && style.includes('font-weight')) { foundProd = true; continue; }
        if (foundProd && !foundTriangleUp && text === '▲') { foundTriangleUp = true; continue; }
        if (foundProd && foundTriangleUp && span.getAttribute('title') === 'Click to edit' && style.includes('60px')) {
          return Array.from(document.querySelectorAll('span[title="Click to edit"]')).indexOf(span);
        }
      }
      return -1;
    });

    if (aboveCellAfterReload >= 0) {
      const cellToClick = page.locator('span[title="Click to edit"]').nth(aboveCellAfterReload);
      await cellToClick.click();
      await page.waitForTimeout(500);

      await page.keyboard.press('Meta+A');
      await page.waitForTimeout(100);
      await page.keyboard.press('Backspace');
      await page.waitForTimeout(200);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(1000);

      const clearedValue = await page.evaluate(() => {
        const spans = document.querySelectorAll('span');
        let foundProd = false;
        let foundTriangleUp = false;
        for (const span of spans) {
          const text = span.textContent.trim();
          const style = span.getAttribute('style') || '';
          if (!foundProd && text === 'PROD' && style.includes('font-weight')) { foundProd = true; continue; }
          if (foundProd && !foundTriangleUp && text === '▲') { foundTriangleUp = true; continue; }
          if (foundProd && foundTriangleUp && span.getAttribute('title') === 'Click to edit' && style.includes('60px')) return text;
        }
        return null;
      });

      log(`  Value after clear: "${clearedValue}"`);
      recordResult('C1.6 -- Value cleared back to "-"', clearedValue === '-', `got: "${clearedValue}"`);

      await page.screenshot({
        path: `${SCREENSHOTS_DIR}/C1-05-after-clear.png`,
        fullPage: true,
      });
      log('  Screenshot: C1-05-after-clear.png');
    } else {
      recordResult('C1.6 -- Value cleared back to "-"', false, 'Could not locate cell after reload');
    }

  } catch (err) {
    recordResult('C1 -- Manage page alert editing', false, `Exception: ${err.message}`);
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/C1-ERROR.png`, fullPage: true }).catch(() => {});
    log(`  ERROR screenshot saved. ${err.message}`);
  }
}

// ── Test 2: Dashboard error handling (C3) ────────────────────────────

async function testDashboardErrorHandling(page) {
  console.log('\n========================================');
  console.log('TEST 2: Dashboard error handling (C3)');
  console.log('========================================');

  try {
    await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);
    await scrollToTop(page);

    // Step 1: No error banner
    log('Step 1: Checking for error banners...');

    const errorBanners = await page.evaluate(() => {
      const allElements = document.querySelectorAll('div, span');
      const errors = [];
      for (const el of allElements) {
        const text = (el.textContent || '').trim();
        const style = el.getAttribute('style') || '';
        if (
          text.length < 200 && text.length > 0 &&
          (text.toLowerCase().includes('error') || text.toLowerCase().includes('failed to fetch')) &&
          (style.includes('background') && (style.includes('red') || style.includes('255, 85') || style.includes('ff5555')))
        ) {
          errors.push(text.substring(0, 100));
        }
      }
      return errors;
    });

    log(`  Error banners found: ${errorBanners.length}`);
    recordResult('C3.1 -- No error banner visible', errorBanners.length === 0, errorBanners.length > 0 ? errorBanners[0] : 'clean');

    // Step 2: Summary bar content
    log('Step 2: Checking summary bar content...');

    const summaryData = await page.evaluate(() => {
      const result = {};
      const divs = document.querySelectorAll('div');
      for (const d of divs) {
        const text = d.textContent.trim();
        if (text.includes('Nodes:') && text.includes('holdings:') && text.length < 300) {
          result.summaryLine = text.substring(0, 250);
        }
        if (text.includes('position:') && text.includes('today:') && text.length < 300) {
          result.positionLine = text.substring(0, 250);
        }
        if ((text.includes('SH:') || text.includes('SZ:')) && text.length < 200) {
          result.marketLine = text.substring(0, 200);
        }
      }
      return result;
    });

    log(`  Summary: ${summaryData.summaryLine || 'NOT FOUND'}`);
    log(`  Position: ${summaryData.positionLine || 'NOT FOUND'}`);
    log(`  Market: ${summaryData.marketLine || 'NOT FOUND'}`);

    recordResult('C3.2 -- Summary bar shows node counts', !!(summaryData.summaryLine && summaryData.summaryLine.includes('Nodes:')), summaryData.summaryLine || 'missing');
    recordResult('C3.3 -- Summary bar shows position and today stats', !!(summaryData.positionLine && summaryData.positionLine.includes('today:')), summaryData.positionLine || 'missing');
    recordResult('C3.4 -- Summary bar shows market indices (SH/SZ)', !!(summaryData.marketLine && (summaryData.marketLine.includes('SH:') || summaryData.marketLine.includes('SZ:'))), summaryData.marketLine || 'missing');

    // Step 3: STALE indicator
    log('Step 3: Checking for STALE indicator...');

    const stalePresent = await page.evaluate(() => {
      const spans = document.querySelectorAll('span');
      for (const s of spans) {
        if (s.textContent.trim() === 'STALE') {
          const rect = s.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) return true;
        }
      }
      return false;
    });

    recordResult('C3.5 -- STALE indicator NOT shown for fresh data', !stalePresent, stalePresent ? 'STALE is visible' : 'not visible');

    // Step 4: PROD rows
    log('Step 4: Checking PROD row data...');

    const prodRowCount = await page.evaluate(() => {
      let count = 0;
      for (const s of document.querySelectorAll('span')) {
        const style = s.getAttribute('style') || '';
        if (s.textContent.trim() === 'PROD' && style.includes('6ch')) count++;
      }
      return count;
    });

    log(`  PROD rows found: ${prodRowCount}`);
    recordResult('C3.6 -- PROD rows rendered', prodRowCount > 0, `count: ${prodRowCount}`);

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/C3-01-dashboard-normal.png`, fullPage: true });
    log('  Screenshot: C3-01-dashboard-normal.png');
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/C3-02-summary-bar.png` });
    log('  Screenshot: C3-02-summary-bar.png');

  } catch (err) {
    recordResult('C3 -- Dashboard error handling', false, `Exception: ${err.message}`);
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/C3-ERROR.png`, fullPage: true }).catch(() => {});
    log(`  ERROR screenshot saved. ${err.message}`);
  }
}

// ── Test 3: HK tab FX rate consistency (H7) ──────────────────────────

async function testHkFxConsistency(page) {
  console.log('\n========================================');
  console.log('TEST 3: HK tab FX rate P&L consistency (H7)');
  console.log('========================================');

  try {
    await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);
    await scrollToTop(page);

    // Step 1: Extract summary bar "today:" value
    log('Step 1: Extracting summary bar "today:" value...');

    const summaryToday = await page.evaluate(() => {
      const divs = document.querySelectorAll('div');
      for (const d of divs) {
        const text = d.textContent.trim();
        if (text.includes('position:') && text.includes('today:') && text.length < 300) {
          const match = text.match(/today:([^\s(]+)/);
          if (match) return match[1];
        }
      }
      return null;
    });

    log(`  Summary bar today value: "${summaryToday}"`);
    const summaryTodayNum = parseMoney(summaryToday);
    log(`  Parsed to number: ${summaryTodayNum}`);
    recordResult('H7.1 -- Summary bar today value found', summaryToday !== null && !isNaN(summaryTodayNum), `"${summaryToday}" = ${summaryTodayNum}`);

    // Step 2: Extract FX rate
    const fxRate = await page.evaluate(() => {
      const divs = document.querySelectorAll('div');
      for (const d of divs) {
        const text = d.textContent.trim();
        const match = text.match(/FX[^\d]*([\d.]+)/);
        if (match) return parseFloat(match[1]);
      }
      return null;
    });

    log(`  FX rate: ${fxRate}`);
    recordResult('H7.2 -- FX rate found in summary', fxRate !== null && fxRate > 0, `FX = ${fxRate}`);

    // Step 3: Extract visible PROD row dayPnl values
    log('Step 2: Extracting per-row dayPnl values from visible rows...');

    const rowData = await page.evaluate(() => {
      const rows = [];
      const allSpans = Array.from(document.querySelectorAll('span'));

      for (let i = 0; i < allSpans.length; i++) {
        const span = allSpans[i];
        const text = span.textContent.trim();
        const style = span.getAttribute('style') || '';

        if (style.includes('6ch') && text.includes('PROD')) {
          const rowSpans = [{ text, w: '6ch' }];
          let j = i + 1;
          while (j < allSpans.length && rowSpans.length < 16) {
            const nextSpan = allSpans[j];
            const nextStyle = nextSpan.getAttribute('style') || '';
            const nextText = nextSpan.textContent.trim();
            if (nextStyle.includes('6ch') && (nextText.includes('PROD') || nextText.includes('DEV'))) break;
            if (nextStyle.includes('ch')) {
              rowSpans.push({ text: nextText, w: (nextStyle.match(/([\d]+ch)/) || [''])[0] });
            }
            j++;
          }

          if (rowSpans.length >= 10) {
            rows.push({
              type: rowSpans[0].text,
              code: rowSpans[1].text,
              name: rowSpans[2].text,
              price: rowSpans[3].text,
              dayPnl: rowSpans[9].text,
              allCols: rowSpans.map(s => s.text)
            });
          }
        }
      }
      return rows;
    });

    log(`  Found ${rowData.length} visible PROD rows`);
    for (const row of rowData) {
      log(`    ${row.code} ${row.name}: dayPnl="${row.dayPnl}"`);
    }

    recordResult('H7.3 -- PROD rows found on HK tab', rowData.length > 0, `count: ${rowData.length}`);

    // Step 4: Also fetch API data to account for hidden holdings
    log('Step 3: Fetching API data to compute expected total (incl. hidden)...');

    const apiData = await page.evaluate(async () => {
      try {
        const resp = await fetch('/api/metrics?tab=HK');
        const data = await resp.json();
        const hkServices = (data.services || []).filter(s => s.id && s.id.startsWith('HK'));
        return hkServices.map(s => ({
          id: s.id,
          name: s.name,
          price: s.price,
          prevClose: s.prevClose,
          shares: s.shares,
          hidden: s.hidden,
          type: s.type
        }));
      } catch (e) {
        return [];
      }
    });

    // Compute expected dayPnl from API data
    const fx = fxRate || 0.92;
    let expectedTotalCNY = 0;
    let visibleTotalCNY = 0;
    let hiddenTotalCNY = 0;
    const hiddenStocks = [];

    for (const s of apiData) {
      if (s.type !== 'holding') continue;
      const dayPnlHKD = (s.price - s.prevClose) * s.shares;
      const dayPnlCNY = dayPnlHKD * fx;
      expectedTotalCNY += dayPnlCNY;
      if (s.hidden) {
        hiddenTotalCNY += dayPnlCNY;
        hiddenStocks.push(`${s.id}(${s.name}): ${Math.round(dayPnlCNY)}`);
      } else {
        visibleTotalCNY += dayPnlCNY;
      }
    }

    log(`  API: total HK holdings: ${apiData.length}`);
    log(`  API: expected total dayPnl (CNY): ${Math.round(expectedTotalCNY)}`);
    log(`  API: visible stocks dayPnl: ${Math.round(visibleTotalCNY)}`);
    log(`  API: hidden stocks dayPnl: ${Math.round(hiddenTotalCNY)}`);
    if (hiddenStocks.length > 0) {
      log(`  Hidden stocks: ${hiddenStocks.join(', ')}`);
    }

    // Step 5: Sum visible row dayPnl values
    let visibleRowSum = 0;
    for (const row of rowData) {
      const val = parseMoney(row.dayPnl);
      if (!isNaN(val)) visibleRowSum += val;
    }

    log(`  Visible row sum (from UI): ${visibleRowSum}`);

    // Step 6: Compare - two checks:
    // (a) Summary should match API-computed total (within rounding of 万 display)
    log('Step 4: Comparing summary vs API total...');

    const diffSummaryVsApi = Math.abs(summaryTodayNum - expectedTotalCNY);
    // Summary displays in 万 units, so rounding can be up to 500
    const summaryTolerance = 1000; // generous for 万-level rounding
    const summaryMatchesApi = diffSummaryVsApi <= summaryTolerance;

    log(`  Summary (${summaryTodayNum}) vs API total (${Math.round(expectedTotalCNY)}), diff=${Math.round(diffSummaryVsApi)}`);
    recordResult(
      'H7.4 -- Summary today matches API-computed total (incl. hidden)',
      summaryMatchesApi,
      `summary=${summaryTodayNum}, apiTotal=${Math.round(expectedTotalCNY)}, diff=${Math.round(diffSummaryVsApi)}`
    );

    // (b) Visible row sum should match API visible total
    log('Step 5: Comparing visible row sum vs API visible total...');

    const diffVisibleVsApi = Math.abs(visibleRowSum - visibleTotalCNY);
    const visibleTolerance = 500; // per-row rounding
    const visibleMatchesApi = diffVisibleVsApi <= visibleTolerance;

    log(`  UI visible sum (${visibleRowSum}) vs API visible total (${Math.round(visibleTotalCNY)}), diff=${Math.round(diffVisibleVsApi)}`);
    recordResult(
      'H7.5 -- Visible row sum matches API (excl. hidden)',
      visibleMatchesApi,
      `uiSum=${visibleRowSum}, apiVisible=${Math.round(visibleTotalCNY)}, diff=${Math.round(diffVisibleVsApi)}`
    );

    // (c) If there are hidden stocks, verify summary = visible + hidden
    if (hiddenStocks.length > 0) {
      log('Step 6: Verifying summary = visible + hidden...');
      const reconstructed = visibleRowSum + hiddenTotalCNY;
      const diffReconstructed = Math.abs(summaryTodayNum - reconstructed);
      const reconstructedMatch = diffReconstructed <= summaryTolerance;

      log(`  Reconstructed: visible(${visibleRowSum}) + hidden(${Math.round(hiddenTotalCNY)}) = ${Math.round(reconstructed)}`);
      log(`  Summary: ${summaryTodayNum}, diff: ${Math.round(diffReconstructed)}`);
      recordResult(
        'H7.6 -- Summary = visible rows + hidden stocks dayPnl',
        reconstructedMatch,
        `reconstructed=${Math.round(reconstructed)}, summary=${summaryTodayNum}, diff=${Math.round(diffReconstructed)}`
      );
    }

    // (d) FX rate reasonable
    if (fxRate !== null) {
      recordResult('H7.7 -- FX rate is reasonable (0.7-1.1)', fxRate > 0.7 && fxRate < 1.1, `FX = ${fxRate}`);
    }

    await page.screenshot({ path: `${SCREENSHOTS_DIR}/H7-01-hk-tab.png`, fullPage: true });
    log('  Screenshot: H7-01-hk-tab.png');
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/H7-02-hk-summary.png` });
    log('  Screenshot: H7-02-hk-summary.png');

  } catch (err) {
    recordResult('H7 -- HK tab FX consistency', false, `Exception: ${err.message}`);
    await page.screenshot({ path: `${SCREENSHOTS_DIR}/H7-ERROR.png`, fullPage: true }).catch(() => {});
    log(`  ERROR screenshot saved. ${err.message}`);
  }
}

// ── Main ─────────────────────────────────────────────────────────────

async function main() {
  console.log('==================================================');
  console.log('Functional Tests for UX Fixes (C1, C3, H7)');
  console.log(`Target: ${BASE_URL}`);
  console.log(`Screenshots: ${SCREENSHOTS_DIR}`);
  console.log(`Viewport: ${VIEWPORT.width}x${VIEWPORT.height}`);
  console.log('==================================================');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  try {
    await testManageAlertEditing(page);
    await testDashboardErrorHandling(page);
    await testHkFxConsistency(page);
  } finally {
    await browser.close();
  }

  // ── Summary ──────────────────────────────────────────────────────
  console.log('\n==================================================');
  console.log('TEST RESULTS SUMMARY');
  console.log('==================================================');

  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed).length;
  const total = results.length;

  for (const r of results) {
    const icon = r.passed ? 'PASS' : 'FAIL';
    console.log(`  [${icon}] ${r.testName}${r.detail ? '  (' + r.detail + ')' : ''}`);
  }

  console.log('--------------------------------------------------');
  console.log(`  Total: ${total}  |  Passed: ${passed}  |  Failed: ${failed}`);
  console.log('==================================================');

  if (failed > 0) process.exit(1);
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(2);
});
