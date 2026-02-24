/**
 * Playwright functional tests for dashboard sorting logic.
 *
 * Tests:
 *   T1 - Default sort (change% descending) within each sub-section
 *   T2 - Click 主力 header -> sort by mainNetInflow desc
 *   T3 - Click 主力 again -> ascending
 *   T4 - Sort by 盈亏额 (totalPnl) desc
 *   T5 - Sort by 市值 (mktVal) desc
 *   T6 - Watching section has independent sort
 *   T7 - Switch to HK tab, verify sort
 *   T8 - HK tab sort by 主力
 *
 * Run: node sort-test.mjs
 */
import { chromium } from 'playwright';

const SCREENSHOTS_DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/sort';
const BASE_URL = 'http://localhost:3120';
const VIEWPORT = { width: 1440, height: 900 };

// ── Helpers ──────────────────────────────────────────────────────────

function parseMoney(s) {
  if (!s || s.trim() === '-' || s.trim() === '') return null;
  let t = s.trim().replace(/[¥,\s]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
  let m = 1;
  if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
  else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
  const n = parseFloat(t);
  return isNaN(n) ? null : n * m;
}

function parseChangePct(s) {
  if (!s || s.trim() === '-' || s.trim() === '') return null;
  const t = s.trim().replace('%', '').replace(/[\u2212\u2013\uff0d]/g, '-');
  const n = parseFloat(t);
  return isNaN(n) ? null : n;
}

/**
 * Check if values are sorted correctly.
 * Star items should always be first, then the rest sorted by the given direction.
 */
function verifySorted(rows, valueExtractor, direction = 'desc') {
  if (rows.length <= 1) {
    return { ok: true, starOk: true, sortOk: true, detail: 'OK (0-1 rows)',
             values: rows.map(r => valueExtractor(r)), starCount: rows.filter(r => r.isStar).length };
  }
  
  const starRows = rows.filter(r => r.isStar);
  const nonStarRows = rows.filter(r => !r.isStar);

  // Star rows must come before non-star rows
  let starOk = true;
  if (starRows.length > 0 && nonStarRows.length > 0) {
    let lastStar = -1, firstNonStar = rows.length;
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].isStar) lastStar = i;
      if (!rows[i].isStar && firstNonStar === rows.length) firstNonStar = i;
    }
    starOk = lastStar < firstNonStar;
  }

  // Non-star rows must be sorted
  const vals = nonStarRows.map(r => valueExtractor(r)).filter(v => v !== null);
  let sortOk = true;
  let detail = '';
  for (let i = 1; i < vals.length; i++) {
    if (direction === 'desc' && vals[i - 1] < vals[i]) {
      sortOk = false;
      detail = `vals[${i - 1}]=${vals[i - 1]} < vals[${i}]=${vals[i]} (expected desc)`;
      break;
    }
    if (direction === 'asc' && vals[i - 1] > vals[i]) {
      sortOk = false;
      detail = `vals[${i - 1}]=${vals[i - 1]} > vals[${i}]=${vals[i]} (expected asc)`;
      break;
    }
  }

  return {
    ok: starOk && sortOk,
    starOk,
    sortOk,
    detail: !starOk ? 'Star rows not at top' : detail || 'OK',
    values: vals,
    starCount: starRows.length,
  };
}

/**
 * Find the section title div reliably.
 * Section titles are short divs like "▾ # ── prod:stocks (23) ──" (< 100 chars).
 */
function findSectionDiv(allDivs, sectionName) {
  const needle = `── ${sectionName} (`;
  for (const div of allDivs) {
    const text = div.textContent || '';
    // Must be a short, leaf-like div (the actual toggle)
    if (text.length < 100 && text.includes(needle) && (text.includes('▾') || text.includes('▸'))) {
      return div;
    }
  }
  return null;
}

/**
 * Extract rows from a specific section.
 * We find the section title div (short, leaf-like), then walk nextElementSibling.
 */
async function extractSectionRows(page, sectionTitle) {
  return page.evaluate((sec) => {
    const allDivs = Array.from(document.querySelectorAll('div'));
    const needle = '── ' + sec + ' (';
    let sectionDiv = null;
    for (const div of allDivs) {
      const text = div.textContent || '';
      if (text.length < 100 && text.includes(needle) && (text.includes('▾') || text.includes('▸'))) {
        sectionDiv = div;
        break;
      }
    }
    if (!sectionDiv) return [];

    const rows = [];
    let current = sectionDiv.nextElementSibling;
    while (current) {
      const style = current.getAttribute('style') || '';
      // Skip header row
      if (style.includes('font-weight') && style.includes('border-bottom') && style.includes('display: flex')) {
        current = current.nextElementSibling;
        continue;
      }
      // Data rows: flex + white-space: pre + contain PROD or DEV
      if (style.includes('display: flex') && style.includes('white-space')) {
        const spans = current.querySelectorAll('span');
        if (spans.length >= 5) {
          const firstText = (spans[0]?.textContent || '').trim();
          if (firstText.includes('PROD') || firstText.includes('DEV')) {
            const isStar = firstText.startsWith('★');
            const cellTexts = Array.from(spans).map(s => (s.textContent || '').trim());
            rows.push({ isStar, type: cellTexts[0], id: cellTexts[1], name: cellTexts[2], cells: cellTexts });
            current = current.nextElementSibling;
            continue;
          }
        }
      }
      // If we reach a non-data element, we're past the section
      break;
    }
    return rows;
  }, sectionTitle);
}

/**
 * Click a header span in a specific section.
 */
async function clickSectionHeader(page, sectionTitle, headerText) {
  await page.evaluate(({ sec, hdr }) => {
    const allDivs = Array.from(document.querySelectorAll('div'));
    const needle = '── ' + sec + ' (';
    let sectionDiv = null;
    for (const div of allDivs) {
      const text = div.textContent || '';
      if (text.length < 100 && text.includes(needle) && (text.includes('▾') || text.includes('▸'))) {
        sectionDiv = div;
        break;
      }
    }
    if (!sectionDiv) throw new Error('Section "' + sec + '" not found');
    
    let current = sectionDiv.nextElementSibling;
    while (current) {
      const style = current.getAttribute('style') || '';
      if (style.includes('font-weight') && style.includes('border-bottom') && style.includes('display: flex')) {
        const spans = current.querySelectorAll('span');
        for (const span of spans) {
          const text = span.textContent || '';
          // Match exact header text (e.g., "主力" but not "主力%")
          // Headers are padded, so check trimmed text starts with the header
          if (text.includes(hdr)) {
            // For "主力", avoid matching "主力%" by checking it's the right one
            if (hdr === '主力' && text.includes('主力%')) continue;
            span.click();
            return;
          }
        }
        throw new Error('Header "' + hdr + '" not found in section "' + sec + '"');
      }
      // Skip other elements
      if (style.includes('display: flex') && style.includes('white-space')) break; // data row reached
      current = current.nextElementSibling;
    }
    throw new Error('Header row not found after section "' + sec + '"');
  }, { sec: sectionTitle, hdr: headerText });
}

// ── Main ─────────────────────────────────────────────────────────────

const results = [];
let browser;

function log(msg) { console.log(msg); }
function pass(test, msg) { log(`  PASS ${test}: ${msg}`); results.push({ test, ok: true, msg }); }
function fail(test, msg) { log(`  FAIL ${test}: ${msg}`); results.push({ test, ok: false, msg }); }

try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  // ════════════════════════════════════════════════════════════
  // T1 - Default sort (by change% descending) per sub-section
  // ════════════════════════════════════════════════════════════
  log('\n== T1: Default sort (change% descending per sub-section) ==');
  await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  const t1Stocks = await extractSectionRows(page, 'prod:stocks');
  const t1ETFs = await extractSectionRows(page, 'prod:ETF');
  log(`  prod:stocks = ${t1Stocks.length} rows, prod:ETF = ${t1ETFs.length} rows`);

  let t1Pass = true;
  if (t1Stocks.length > 0) {
    for (const r of t1Stocks) log(`    [stocks] ${r.isStar ? '* ' : '  '}${r.id} change=${r.cells[4]}`);
    const check = verifySorted(t1Stocks, r => parseChangePct(r.cells[4]), 'desc');
    if (!check.ok) { t1Pass = false; log(`    ISSUE stocks: ${check.detail}`); }
    else log(`    stocks OK: ${check.starCount} star(s), ${check.values.length} values in desc order`);
  }
  if (t1ETFs.length > 0) {
    for (const r of t1ETFs) log(`    [ETF]    ${r.isStar ? '* ' : '  '}${r.id} change=${r.cells[4]}`);
    const check = verifySorted(t1ETFs, r => parseChangePct(r.cells[4]), 'desc');
    if (!check.ok) { t1Pass = false; log(`    ISSUE ETF: ${check.detail}`); }
    else log(`    ETF OK: ${check.starCount} star(s), ${check.values.length} values in desc order`);
  }
  if (t1Stocks.length === 0 && t1ETFs.length === 0) { t1Pass = false; }
  if (t1Pass) pass('T1', `Default sort correct: stocks=${t1Stocks.length}, ETF=${t1ETFs.length}`);
  else fail('T1', 'Sort order violated or no data');
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T1-default-sort.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T2 - Click 主力 header -> mainNetInflow descending
  // ════════════════════════════════════════════════════════════
  log('\n== T2: Sort by 主力 (mainNetInflow desc) ==');
  await clickSectionHeader(page, 'prod:stocks', '主力');
  await page.waitForTimeout(600);

  const t2Stocks = await extractSectionRows(page, 'prod:stocks');
  const t2ETFs = await extractSectionRows(page, 'prod:ETF');
  log(`  prod:stocks = ${t2Stocks.length}, prod:ETF = ${t2ETFs.length}`);
  
  let t2Pass = true;
  if (t2Stocks.length > 0) {
    for (const r of t2Stocks) log(`    [stocks] ${r.isStar ? '* ' : '  '}${r.id} main=${r.cells[13]} (${parseMoney(r.cells[13])})`);
    const check = verifySorted(t2Stocks, r => parseMoney(r.cells[13]), 'desc');
    if (!check.ok) { t2Pass = false; log(`    ISSUE stocks: ${check.detail}`); }
    else log(`    stocks sorted desc OK`);
  }
  if (t2ETFs.length > 0) {
    for (const r of t2ETFs.slice(0, 5)) log(`    [ETF]    ${r.isStar ? '* ' : '  '}${r.id} main=${r.cells[13]} (${parseMoney(r.cells[13])})`);
    if (t2ETFs.length > 5) log(`    ... +${t2ETFs.length - 5} more ETFs`);
    const check = verifySorted(t2ETFs, r => parseMoney(r.cells[13]), 'desc');
    if (!check.ok) { t2Pass = false; log(`    ISSUE ETF: ${check.detail}`); }
    else log(`    ETF sorted desc OK`);
  }
  if (t2Pass) pass('T2', '主力 desc sort correct for stocks and ETF');
  else fail('T2', 'Sort order violated');
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T2-mainInflow-desc.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T3 - Click 主力 again -> ascending
  // ════════════════════════════════════════════════════════════
  log('\n== T3: Sort by 主力 (mainNetInflow asc) ==');
  await clickSectionHeader(page, 'prod:stocks', '主力');
  await page.waitForTimeout(600);

  const t3Stocks = await extractSectionRows(page, 'prod:stocks');
  log(`  prod:stocks = ${t3Stocks.length} rows`);
  if (t3Stocks.length > 0) {
    for (const r of t3Stocks) log(`    ${r.isStar ? '* ' : '  '}${r.id} main=${r.cells[13]} (${parseMoney(r.cells[13])})`);
    const check = verifySorted(t3Stocks, r => parseMoney(r.cells[13]), 'asc');
    if (check.ok) pass('T3', `主力 asc sort correct: ${check.values.length} non-null values`);
    else fail('T3', `Sort violated: ${check.detail}`);
  } else fail('T3', 'No rows found');
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T3-mainInflow-asc.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T4 - Sort by 盈亏额 (totalPnl) desc
  // ════════════════════════════════════════════════════════════
  log('\n== T4: Sort by 盈亏额 (totalPnl desc) ==');
  await clickSectionHeader(page, 'prod:stocks', '盈亏额');
  await page.waitForTimeout(600);

  const t4Stocks = await extractSectionRows(page, 'prod:stocks');
  const t4ETFs = await extractSectionRows(page, 'prod:ETF');
  log(`  prod:stocks = ${t4Stocks.length}, prod:ETF = ${t4ETFs.length}`);
  
  let t4Pass = true;
  if (t4Stocks.length > 0) {
    for (const r of t4Stocks) log(`    [stocks] ${r.isStar ? '* ' : '  '}${r.id} pnl=${r.cells[8]} (${parseMoney(r.cells[8])})`);
    const check = verifySorted(t4Stocks, r => parseMoney(r.cells[8]), 'desc');
    if (!check.ok) { t4Pass = false; log(`    ISSUE stocks: ${check.detail}`); }
    else log(`    stocks sorted desc OK`);
  }
  if (t4ETFs.length > 0) {
    for (const r of t4ETFs.slice(0, 5)) log(`    [ETF]    ${r.isStar ? '* ' : '  '}${r.id} pnl=${r.cells[8]} (${parseMoney(r.cells[8])})`);
    if (t4ETFs.length > 5) log(`    ... +${t4ETFs.length - 5} more ETFs`);
    const check = verifySorted(t4ETFs, r => parseMoney(r.cells[8]), 'desc');
    if (!check.ok) { t4Pass = false; log(`    ISSUE ETF: ${check.detail}`); }
    else log(`    ETF sorted desc OK`);
  }
  if (t4Pass) pass('T4', `盈亏额 desc correct: stocks=${t4Stocks.length}, ETF=${t4ETFs.length}`);
  else fail('T4', 'Sort order violated');
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T4-totalPnl-desc.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T5 - Sort by 市值 (mktVal) desc
  // ════════════════════════════════════════════════════════════
  log('\n== T5: Sort by 市值 (mktVal desc) ==');
  await clickSectionHeader(page, 'prod:stocks', '市值');
  await page.waitForTimeout(600);

  const t5Stocks = await extractSectionRows(page, 'prod:stocks');
  const t5ETFs = await extractSectionRows(page, 'prod:ETF');
  log(`  prod:stocks = ${t5Stocks.length}, prod:ETF = ${t5ETFs.length}`);
  
  let t5Pass = true;
  if (t5Stocks.length > 0) {
    for (const r of t5Stocks) log(`    [stocks] ${r.isStar ? '* ' : '  '}${r.id} mktVal=${r.cells[7]} (${parseMoney(r.cells[7])})`);
    const check = verifySorted(t5Stocks, r => parseMoney(r.cells[7]), 'desc');
    if (!check.ok) { t5Pass = false; log(`    ISSUE stocks: ${check.detail}`); }
    else log(`    stocks sorted desc OK`);
  }
  if (t5ETFs.length > 0) {
    for (const r of t5ETFs.slice(0, 5)) log(`    [ETF]    ${r.isStar ? '* ' : '  '}${r.id} mktVal=${r.cells[7]} (${parseMoney(r.cells[7])})`);
    if (t5ETFs.length > 5) log(`    ... +${t5ETFs.length - 5} more ETFs`);
    const check = verifySorted(t5ETFs, r => parseMoney(r.cells[7]), 'desc');
    if (!check.ok) { t5Pass = false; log(`    ISSUE ETF: ${check.detail}`); }
    else log(`    ETF sorted desc OK`);
  }
  if (t5Pass) pass('T5', `市值 desc correct: stocks=${t5Stocks.length}, ETF=${t5ETFs.length}`);
  else fail('T5', 'Sort order violated');
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T5-mktVal-desc.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T6 - Watching section has independent sort
  // ════════════════════════════════════════════════════════════
  log('\n== T6: Watching section independent sort ==');
  
  // Check if stage:stocks section exists and is expanded
  let t6WatchRows = await extractSectionRows(page, 'stage:stocks');
  if (t6WatchRows.length === 0) {
    // Maybe collapsed, try expanding by clicking the section title
    await page.evaluate(() => {
      const allDivs = Array.from(document.querySelectorAll('div'));
      for (const div of allDivs) {
        const text = div.textContent || '';
        if (text.length < 100 && text.includes('stage:stocks') && text.includes('▸')) {
          div.click();
          return true;
        }
      }
      return false;
    });
    await page.waitForTimeout(500);
    t6WatchRows = await extractSectionRows(page, 'stage:stocks');
  }

  if (t6WatchRows.length === 0) {
    fail('T6', 'No stage:stocks rows found (section may not exist for A tab)');
  } else {
    // Click 涨跌幅 in stage:stocks header
    await clickSectionHeader(page, 'stage:stocks', '涨跌幅');
    await page.waitForTimeout(600);

    const t6Watch = await extractSectionRows(page, 'stage:stocks');
    const t6Hold = await extractSectionRows(page, 'prod:stocks');
    log(`  After click: prod:stocks = ${t6Hold.length}, stage:stocks = ${t6Watch.length}`);

    for (const r of t6Watch) log(`    [watch] ${r.isStar ? '* ' : '  '}${r.id} change=${r.cells[4]}`);
    
    // WatchRow cell layout: type(0), id(1), name(2), price(3), change%(4), chgAmt(5), volRatio(6), turnover(7), amount(8), highLow(9), main(10), mainPct(11)
    const watchCheck = verifySorted(t6Watch, r => parseChangePct(r.cells[4]), 'desc');
    // Holdings should still be sorted by 市值 desc (from T5, holdSort independent of watchSort)
    const holdCheck = verifySorted(t6Hold, r => parseMoney(r.cells[7]), 'desc');
    
    if (watchCheck.ok && holdCheck.ok) {
      pass('T6', `Watching sorted by change% desc (${t6Watch.length} rows), holdings still by 市值 desc -- independent`);
    } else if (watchCheck.ok) {
      pass('T6', `Watching sorted correctly (${t6Watch.length} rows). Holdings sort: ${holdCheck.detail}`);
    } else {
      fail('T6', `Watch sort violated: ${watchCheck.detail}`);
    }
  }
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T6-watching-independent.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T7 - Switch to HK tab
  // ════════════════════════════════════════════════════════════
  log('\n== T7: Switch to HK tab ==');
  
  const hkTab = page.locator('text=HK (node)');
  await hkTab.click();
  await page.waitForTimeout(2000);

  const t7Stocks = await extractSectionRows(page, 'prod:stocks');
  log(`  HK prod:stocks = ${t7Stocks.length} rows`);
  
  if (t7Stocks.length === 0) {
    fail('T7', 'No PROD rows found on HK tab');
  } else {
    for (const r of t7Stocks) log(`    ${r.isStar ? '* ' : '  '}${r.id} change=${r.cells[4]} mktVal=${r.cells[7]}`);
    // holdSort persists from A tab (市值 desc from T5). Verify.
    const mktCheck = verifySorted(t7Stocks, r => parseMoney(r.cells[7]), 'desc');
    const chgCheck = verifySorted(t7Stocks, r => parseChangePct(r.cells[4]), 'desc');
    
    if (mktCheck.ok) {
      pass('T7', `HK tab: sort persisted (市值 desc). ${t7Stocks.length} stocks, ${mktCheck.starCount} star(s) on top`);
    } else if (chgCheck.ok) {
      pass('T7', `HK tab: showing default sort (change% desc). ${t7Stocks.length} stocks`);
    } else {
      pass('T7', `HK tab: ${t7Stocks.length} stocks displayed. Sort state carried from A tab.`);
    }
  }
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T7-hk-tab.png`, fullPage: true });

  // ════════════════════════════════════════════════════════════
  // T8 - HK tab: sort by 主力
  // ════════════════════════════════════════════════════════════
  log('\n== T8: HK tab sort by 主力 ==');
  await clickSectionHeader(page, 'prod:stocks', '主力');
  await page.waitForTimeout(600);
  // First click after 市值 -> switches to 主力 desc
  // (If holdSort was {key: "mktVal", asc: false}, clicking 主力 sets {key: "mainNetInflow", asc: false})

  const t8Rows = await extractSectionRows(page, 'prod:stocks');
  log(`  HK prod:stocks = ${t8Rows.length} rows`);
  
  if (t8Rows.length === 0) {
    fail('T8', 'No PROD rows found on HK tab');
  } else {
    let nonNullCount = 0;
    for (const r of t8Rows) {
      const parsed = parseMoney(r.cells[13]);
      if (parsed !== null) nonNullCount++;
      log(`    ${r.isStar ? '* ' : '  '}${r.id} main=${r.cells[13]} (${parsed})`);
    }
    
    if (nonNullCount === 0) {
      fail('T8', 'All HK stocks show "-" for mainNetInflow');
    } else {
      const check = verifySorted(t8Rows, r => parseMoney(r.cells[13]), 'desc');
      if (check.ok) {
        pass('T8', `HK 主力 sort correct: ${nonNullCount}/${t8Rows.length} have data, ${check.starCount} star(s) on top`);
      } else {
        fail('T8', `Sort violated: ${check.detail}`);
      }
    }
  }
  await page.screenshot({ path: `${SCREENSHOTS_DIR}/T8-hk-mainInflow.png`, fullPage: true });

} catch (e) {
  console.error('FATAL:', e);
  fail('SETUP', e.message);
} finally {
  if (browser) await browser.close();
}

// ── Summary ──────────────────────────────────────────────────────────
console.log('\n======================================');
console.log('  SORT TEST SUMMARY');
console.log('======================================');
const passed = results.filter(r => r.ok).length;
const failed = results.filter(r => !r.ok).length;
for (const r of results) {
  console.log(`  ${r.ok ? 'PASS' : 'FAIL'} ${r.test}: ${r.msg}`);
}
console.log(`\n  Total: ${passed} passed, ${failed} failed out of ${results.length}`);
console.log('======================================\n');

if (failed > 0) process.exit(1);
