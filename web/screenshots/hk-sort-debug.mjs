// hk-sort-debug.mjs — Playwright script to debug HK stock sorting on the dashboard
// Usage: npx playwright test --config=playwright.config.ts  OR  node hk-sort-debug.mjs

import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE_URL = 'http://localhost:3120';
const SHOT_DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/hk-sort';
mkdirSync(SHOT_DIR, { recursive: true });

function parseNum(s) {
  if (!s || s.trim() === '-' || s.trim() === '') return null;
  let t = s.trim().replace(/[¥,\s%]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
  let m = 1;
  if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
  else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
  const n = parseFloat(t);
  return isNaN(n) ? null : n * m;
}

function fmt(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'number') {
    if (!isFinite(v)) return String(v);
    return v.toFixed(4);
  }
  return String(v);
}

// Extract all PROD rows from a section
async function extractProdRows(page) {
  return await page.evaluate(() => {
    // Find all data rows — they are divs with display:flex, whiteSpace:pre inside the terminal body
    // PROD rows start with "★PROD" or " PROD"
    const allDivs = [...document.querySelectorAll('div')];
    const rows = [];
    for (const div of allDivs) {
      const text = div.textContent || '';
      if (!text.includes('PROD')) continue;
      // Check it's a row (has multiple span children with width style)
      const spans = div.querySelectorAll(':scope > span');
      if (spans.length < 10) continue;
      // Verify this is a data row by checking structure
      const style = window.getComputedStyle(div);
      if (style.display !== 'flex') continue;
      if (!style.whiteSpace.includes('pre')) continue;

      const vals = [...spans].map(sp => sp.textContent.trim());
      // Check it's actually a PROD row (first span contains PROD)
      if (!vals[0].includes('PROD')) continue;

      rows.push({
        raw: vals,
        type: vals[0],
        id: vals[1],
        name: vals[2],
        price: vals[3],
        change: vals[4],
        cost: vals[5],
        pnl: vals[6],
        mktVal: vals[7],
        totalPnl: vals[8],
        dayPnl: vals[9],
        volRatio: vals[10],
        turnover: vals[11],
        amount: vals[12],
        mainInflow: vals[13],
        mainPct: vals[14],
      });
    }
    return rows;
  });
}

// Extract the active sort indicator from headers
async function getActiveSort(page) {
  return await page.evaluate(() => {
    const allDivs = [...document.querySelectorAll('div')];
    const results = [];
    for (const div of allDivs) {
      const spans = div.querySelectorAll(':scope > span');
      if (spans.length < 10) continue;
      const text = div.textContent || '';
      // Header row has column labels like 类型, 代码, 名称, 现价, etc.
      if (!text.includes('类型') || !text.includes('代码') || !text.includes('现价')) continue;
      for (const sp of spans) {
        const t = sp.textContent || '';
        if (t.includes('▲') || t.includes('▼')) {
          results.push(t.trim());
        }
      }
    }
    return results;
  });
}

// Find and click a header column by label text
async function clickHeader(page, label) {
  // Find a span inside a header row that contains the label
  const clicked = await page.evaluate((lbl) => {
    const allDivs = [...document.querySelectorAll('div')];
    for (const div of allDivs) {
      const text = div.textContent || '';
      if (!text.includes('类型') || !text.includes('代码') || !text.includes('现价')) continue;
      // This is a header row — only match PROD header rows (which have 盈亏额, 市值, etc.)
      if (!text.includes('盈亏额') && !text.includes('市值')) continue;
      const spans = div.querySelectorAll(':scope > span');
      for (const sp of spans) {
        const spText = (sp.textContent || '').replace(/[▲▼\s]/g, '');
        if (spText === lbl) {
          sp.click();
          return `Clicked "${sp.textContent.trim()}" in header`;
        }
      }
    }
    return null;
  }, label);
  return clicked;
}

// Fetch raw API data for HK stocks
async function fetchRawApi(page) {
  return await page.evaluate(async () => {
    const resp = await fetch('/api/metrics', { cache: 'no-store' });
    const data = await resp.json();
    const svcs = (data.services || []).filter(s => s.id.startsWith('HK'));
    return svcs.map(s => ({
      id: s.id,
      name: s.name,
      type: s.type,
      change: s.change,
      cost: s.cost,
      shares: s.shares,
      price: s.price,
      chgAmt: s.chgAmt,
      mainNetInflow: s.mainNetInflow,
      mainNetInflowPct: s.mainNetInflowPct,
      pnl: s.pnl,
      hidden: s.hidden,
      star: s.star,
      amount: s.amount,
      volRatio: s.volRatio,
      turnover: s.turnover,
    }));
  });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  console.log('=' .repeat(80));
  console.log('HK STOCK SORT DEBUG');
  console.log('=' .repeat(80));

  // ── Step 1: Navigate and capture default state ──
  console.log('\n## STEP 1: Default state\n');
  await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SHOT_DIR}/01-default.png`, fullPage: true });
  console.log('Screenshot: 01-default.png');

  const defaultRows = await extractProdRows(page);
  console.log(`\nFound ${defaultRows.length} PROD rows in HK tab:\n`);

  // Print table header
  const colW = { type: 7, id: 12, name: 10, price: 10, change: 10, cost: 10, pnl: 10, mktVal: 10, totalPnl: 10, dayPnl: 10, volRatio: 8, turnover: 8, amount: 10, mainInflow: 10, mainPct: 8 };
  const hdr = Object.keys(colW).map(k => k.padEnd(colW[k])).join(' ');
  console.log(hdr);
  console.log('-'.repeat(hdr.length));

  for (const r of defaultRows) {
    const line = [
      (r.type || '').padEnd(colW.type),
      (r.id || '').padEnd(colW.id),
      (r.name || '').padEnd(colW.name),
      (r.price || '').padStart(colW.price),
      (r.change || '').padStart(colW.change),
      (r.cost || '').padStart(colW.cost),
      (r.pnl || '').padStart(colW.pnl),
      (r.mktVal || '').padStart(colW.mktVal),
      (r.totalPnl || '').padStart(colW.totalPnl),
      (r.dayPnl || '').padStart(colW.dayPnl),
      (r.volRatio || '').padStart(colW.volRatio),
      (r.turnover || '').padStart(colW.turnover),
      (r.amount || '').padStart(colW.amount),
      (r.mainInflow || '').padStart(colW.mainInflow),
      (r.mainPct || '').padStart(colW.mainPct),
    ].join(' ');
    console.log(line);
  }

  const activeSort = await getActiveSort(page);
  console.log(`\nActive sort indicators: ${activeSort.length > 0 ? activeSort.join(', ') : '(none — default sort by change% desc)'}`);

  // ── Step 2: Test each sortable column ──
  console.log('\n' + '='.repeat(80));
  console.log('## STEP 2: Test sorting for each column\n');

  const sortColumns = [
    { label: '涨跌幅', key: 'change', idx: 4 },
    { label: '市值', key: 'mktVal', idx: 7 },
    { label: '盈亏额', key: 'totalPnl', idx: 8 },
    { label: '今日', key: 'dayPnl', idx: 9 },
    { label: '主力', key: 'mainInflow', idx: 13 },
    { label: '主力%', key: 'mainPct', idx: 14 },
  ];

  for (const col of sortColumns) {
    console.log(`\n--- Sorting by: ${col.label} (${col.key}) ---`);

    const result = await clickHeader(page, col.label);
    if (!result) {
      console.log(`  FAIL: Could not find header "${col.label}" to click!`);
      continue;
    }
    console.log(`  ${result}`);
    await page.waitForTimeout(500);

    await page.screenshot({ path: `${SHOT_DIR}/02-sort-${col.key}.png`, fullPage: true });
    console.log(`  Screenshot: 02-sort-${col.key}.png`);

    // Check active sort
    const sortNow = await getActiveSort(page);
    console.log(`  Active sort indicators: ${sortNow.join(', ') || '(none)'}`);

    // Extract rows and check order
    const rows = await extractProdRows(page);
    if (rows.length === 0) {
      console.log('  No PROD rows found!');
      continue;
    }

    // Parse values for the sorted column
    const values = rows.map(r => {
      const raw = r.raw[col.idx];
      return { id: r.id, star: r.type.includes('★'), rawVal: raw, parsed: parseNum(raw) };
    });

    console.log(`  Display order (${values.length} rows):`);
    for (const v of values) {
      console.log(`    ${v.star ? '★' : ' '} ${v.id.padEnd(10)} ${(v.rawVal || '-').padStart(12)}  → parsed: ${fmt(v.parsed)}`);
    }

    // Check if descending order (excluding star rows from ordering check)
    const nonStar = values.filter(v => !v.star);
    const starRows = values.filter(v => v.star);

    // Verify star rows come first
    let starFirst = true;
    let lastStarIdx = -1;
    let firstNonStarIdx = values.length;
    for (let i = 0; i < values.length; i++) {
      if (values[i].star) lastStarIdx = i;
      if (!values[i].star && firstNonStarIdx === values.length) firstNonStarIdx = i;
    }
    if (lastStarIdx > firstNonStarIdx) {
      starFirst = false;
      console.log(`  FAIL: Star rows not at top! Last star at idx ${lastStarIdx}, first non-star at ${firstNonStarIdx}`);
    }

    // Check descending (first click should be desc)
    let isDesc = true;
    let failDetail = '';
    for (let i = 1; i < nonStar.length; i++) {
      const prev = nonStar[i - 1].parsed;
      const curr = nonStar[i].parsed;
      // null/-Infinity should sort to bottom in desc
      if (prev === null && curr !== null) { isDesc = false; failDetail = `null before ${curr} at idx ${i}`; break; }
      if (prev !== null && curr !== null && prev < curr) {
        isDesc = false;
        failDetail = `${prev} < ${curr} at positions ${i - 1},${i} (${nonStar[i - 1].id} vs ${nonStar[i].id})`;
        break;
      }
    }

    const status = isDesc && starFirst ? 'PASS' : 'FAIL';
    console.log(`  Result: ${status} (descending)`);
    if (!isDesc) console.log(`  Failure: ${failDetail}`);
  }

  // ── Step 3: Check for data issues ──
  console.log('\n' + '='.repeat(80));
  console.log('## STEP 3: Raw data issue check\n');

  const rawData = await fetchRawApi(page);
  console.log(`Found ${rawData.length} HK stocks in API response:\n`);

  console.log('id'.padEnd(12) + 'type'.padEnd(10) + 'star'.padEnd(6) + 'hidden'.padEnd(8) +
    'change%'.padStart(10) + 'cost'.padStart(10) + 'shares'.padStart(10) + 'price'.padStart(10) +
    'chgAmt'.padStart(10) + 'mainInflow'.padStart(14) + 'mainPct'.padStart(10));
  console.log('-'.repeat(110));

  const issues = [];

  for (const s of rawData) {
    const line = [
      s.id.padEnd(12),
      (s.type || 'N/A').padEnd(10),
      String(!!s.star).padEnd(6),
      String(!!s.hidden).padEnd(8),
      fmt(s.change).padStart(10),
      fmt(s.cost).padStart(10),
      fmt(s.shares).padStart(10),
      fmt(s.price).padStart(10),
      fmt(s.chgAmt).padStart(10),
      fmt(s.mainNetInflow).padStart(14),
      fmt(s.mainNetInflowPct).padStart(10),
    ].join('');
    console.log(line);

    // Check for data issues
    if (s.type === 'holding') {
      if (s.cost === null || s.cost === 0) {
        issues.push(`${s.id}: cost=${s.cost} — will cause -Infinity for totalPnl/mktVal derivedVal!`);
      }
      if (s.shares === null || s.shares === 0) {
        issues.push(`${s.id}: shares=${s.shares} — will cause -Infinity for mktVal/totalPnl/dayPnl derivedVal!`);
      }
      if (s.cost === undefined) {
        issues.push(`${s.id}: cost=undefined — will cause -Infinity in derivedVal!`);
      }
      if (s.shares === undefined) {
        issues.push(`${s.id}: shares=undefined — will cause -Infinity in derivedVal!`);
      }
    }
    if (s.mainNetInflow === null || s.mainNetInflow === undefined) {
      issues.push(`${s.id}: mainNetInflow=${s.mainNetInflow} — null causes -Infinity in sort`);
    }
    if (s.mainNetInflowPct === null || s.mainNetInflowPct === undefined) {
      issues.push(`${s.id}: mainNetInflowPct=${s.mainNetInflowPct} — null causes -Infinity in sort`);
    }
  }

  console.log('\n--- Data Issues ---');
  if (issues.length === 0) {
    console.log('No issues found.');
  } else {
    for (const issue of issues) {
      console.log(`  [!] ${issue}`);
    }
  }

  // ── derivedVal simulation ──
  console.log('\n--- derivedVal simulation (holdings only) ---');
  const holdings = rawData.filter(s => s.type === 'holding' && !s.hidden);
  if (holdings.length > 0) {
    console.log('id'.padEnd(12) + 'mktVal'.padStart(14) + 'totalPnl'.padStart(14) + 'dayPnl'.padStart(14));
    console.log('-'.repeat(54));
    for (const s of holdings) {
      const mktVal = s.shares != null ? s.price * s.shares : -Infinity;
      const totalPnl = (s.cost != null && s.shares != null) ? (s.price - s.cost) * s.shares : -Infinity;
      const dayPnl = s.shares != null ? s.chgAmt * s.shares : -Infinity;
      console.log(
        s.id.padEnd(12) +
        fmt(mktVal).padStart(14) +
        fmt(totalPnl).padStart(14) +
        fmt(dayPnl).padStart(14) +
        (mktVal === -Infinity || totalPnl === -Infinity || dayPnl === -Infinity ? '  *** -Infinity ***' : '')
      );
    }
  }

  // ── Expected sort order comparison ──
  console.log('\n--- Expected vs Actual sort order (change% desc, default) ---');
  const visibleHoldings = rawData.filter(s => s.type === 'holding' && !s.hidden);
  const starred = visibleHoldings.filter(s => s.star);
  const unstarred = visibleHoldings.filter(s => !s.star);
  unstarred.sort((a, b) => b.change - a.change);
  const expected = [...starred.sort((a, b) => b.change - a.change), ...unstarred];

  console.log('Expected order:');
  for (const s of expected) {
    console.log(`  ${s.star ? '★' : ' '} ${s.id.padEnd(10)} change=${fmt(s.change)}%`);
  }

  if (defaultRows.length > 0) {
    console.log('\nActual order (from DOM):');
    for (const r of defaultRows) {
      console.log(`  ${r.type.includes('★') ? '★' : ' '} ${(r.id || '').padEnd(10)} change=${r.change}`);
    }
    
    // Check match
    const match = expected.length === defaultRows.length &&
      expected.every((s, i) => s.id === (defaultRows[i].id || '').trim());
    console.log(`\nOrder match: ${match ? 'YES' : 'NO — ORDER MISMATCH'}`);
    if (!match) {
      for (let i = 0; i < Math.max(expected.length, defaultRows.length); i++) {
        const exp = expected[i]?.id || '(missing)';
        const act = (defaultRows[i]?.id || '').trim() || '(missing)';
        const marker = exp === act ? '  ' : '>>'; 
        console.log(`  ${marker} [${i}] expected=${exp.padEnd(10)} actual=${act}`);
      }
    }
  }

  console.log('\n' + '='.repeat(80));
  console.log('DEBUG COMPLETE');
  console.log('='.repeat(80));

  await browser.close();
})();
