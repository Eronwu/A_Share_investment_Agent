import { chromium } from 'playwright';
const BASE = 'http://localhost:3120';
const DIR = new URL('.', import.meta.url).pathname;

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();

  const consoleErrors = [];
  page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  page.on('pageerror', err => consoleErrors.push('PAGE_ERROR: ' + err.message));

  await page.route('**/*.googleapis.com/**', route => route.abort());
  await page.route('**/*.gstatic.com/**', route => route.abort());

  try {
    // ════════════════════════════════════════════════
    // 1. Dashboard A-share tab
    // ════════════════════════════════════════════════
    console.log('\n═══ 1. Dashboard A-share ═══');
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(5000);
    await page.screenshot({ path: `${DIR}/checkup_01_dash_A.png`, fullPage: true });

    // Check no ERROR banner
    const hasError_A = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]'))
    );
    record('A tab: no [ERROR] banner', !hasError_A);

    // Check data loaded (has PROD rows)
    const prodCount_A = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).filter(s => s.textContent.trim() === 'PROD' || s.textContent.trim() === '★PROD').length
    );
    record('A tab: PROD rows rendered', prodCount_A > 0, `${prodCount_A} rows`);

    // Check summary bar has data
    const summaryText_A = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      const summary = divs.find(d => d.textContent.includes('Nodes:') || d.textContent.includes('position:'));
      return summary ? summary.textContent.trim().slice(0, 200) : '';
    });
    record('A tab: summary bar has content', summaryText_A.length > 20, summaryText_A.slice(0, 80));

    const summaryHoldingsA = await page.evaluate(async () => {
      const txt = document.body?.innerText || '';
      const m = txt.match(/Nodes:\s*(\d+)[\s\S]*?holdings:\s*(\d+)(?:\(\+(\d+)\s+hidden\))?/);
      if (!m) return { ok: false, detail: 'summary parse failed' };
      const uiNodes = Number(m[1]);
      const uiVisible = Number(m[2]);
      const uiHidden = Number(m[3] || '0');
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const holdings = (data.services || []).filter(s => !String(s.id).startsWith('HK') && s.type === 'holding');
      const apiNodes = holdings.length;
      const apiVisible = holdings.filter(s => !s.hidden).length;
      const apiHidden = apiNodes - apiVisible;
      const ok = uiNodes === apiNodes && uiVisible === apiVisible && uiHidden === apiHidden;
      return { ok, detail: `ui ${uiNodes}/${uiVisible}/${uiHidden} vs api ${apiNodes}/${apiVisible}/${apiHidden}` };
    });
    record('A tab: summary uses holdings-only stats', summaryHoldingsA.ok, summaryHoldingsA.detail);

    // Check no "0.00" in 量比 column for A-share (should be "-" after close)
    const volRatioValues_A = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      // Find spans that look like volRatio values (near 量比 header)
      return spans.filter(s => s.textContent.trim() === '0.00' && s.style.width === '7ch').length;
    });
    record('A tab: no "0.00" in 量比 (shows "-")', volRatioValues_A === 0, `${volRatioValues_A} instances of "0.00"`);

    // Check 股数 column exists in header
    const hasSharesHeader_A = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).some(s => s.textContent.trim().startsWith('股数'))
    );
    record('A tab: 股数 column in header', hasSharesHeader_A);

    // Check shares values are rendered (not all "-")
    const sharesValues = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      return spans.filter(s => {
        const t = s.textContent.trim();
        return s.style.width === '7ch' && /^\d+$/.test(t) && parseInt(t) > 0;
      }).length;
    });
    record('A tab: shares values rendered', sharesValues > 0, `${sharesValues} rows with shares`);

    // Check alert log area at bottom
    const hasAlertLog = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('alert') || d.textContent.includes('L2'))
    );
    record('A tab: alert log area present', hasAlertLog);

    // ════════════════════════════════════════════════
    // 2. Dashboard HK tab
    // ════════════════════════════════════════════════
    console.log('\n═══ 2. Dashboard HK tab ═══');
    await page.goto(`${BASE}/?tab=HK`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${DIR}/checkup_02_dash_HK.png`, fullPage: true });

    const hasError_HK = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]'))
    );
    record('HK tab: no [ERROR] banner', !hasError_HK);

    const prodCount_HK = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).filter(s => s.textContent.trim() === 'PROD' || s.textContent.trim() === '★PROD').length
    );
    record('HK tab: PROD rows rendered', prodCount_HK > 0, `${prodCount_HK} rows`);

    // Check HK codes start with HK
    const hkCodes = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      return spans.filter(s => /^HK\d{5}/.test(s.textContent.trim())).map(s => s.textContent.trim());
    });
    record('HK tab: HK-prefixed codes', hkCodes.length > 0, `${hkCodes.length} HK codes`);

    // Check FX rate in summary (may show as "FX:" or "WARN FX unavailable")
    const hasFx = await page.evaluate(() => {
      const text = document.body?.innerText || '';
      return text.includes('FX') || text.includes('HKD') || text.includes('hkd') || text.includes('0.92');
    });
    record('HK tab: FX rate displayed', hasFx);

    const summaryHoldingsHK = await page.evaluate(async () => {
      const txt = document.body?.innerText || '';
      const m = txt.match(/Nodes:\s*(\d+)[\s\S]*?holdings:\s*(\d+)(?:\(\+(\d+)\s+hidden\))?/);
      if (!m) return { ok: false, detail: 'summary parse failed' };
      const uiNodes = Number(m[1]);
      const uiVisible = Number(m[2]);
      const uiHidden = Number(m[3] || '0');
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const holdings = (data.services || []).filter(s => String(s.id).startsWith('HK') && s.type === 'holding');
      const apiNodes = holdings.length;
      const apiVisible = holdings.filter(s => !s.hidden).length;
      const apiHidden = apiNodes - apiVisible;
      const ok = uiNodes === apiNodes && uiVisible === apiVisible && uiHidden === apiHidden;
      return { ok, detail: `ui ${uiNodes}/${uiVisible}/${uiHidden} vs api ${apiNodes}/${apiVisible}/${apiHidden}` };
    });
    record('HK tab: summary uses holdings-only stats', summaryHoldingsHK.ok, summaryHoldingsHK.detail);

    // Hidden section — should only show HK hidden stocks
    const hiddenSection = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      const hiddenHeader = divs.find(d => d.textContent.includes('hidden'));
      if (!hiddenHeader) return { found: false, codes: [] };
      // Get codes in hidden section
      const parent = hiddenHeader.closest('div');
      const spans = parent ? Array.from(parent.querySelectorAll('span')) : [];
      const codes = spans.filter(s => /^HK\d{5}|^\d{6}/.test(s.textContent.trim())).map(s => s.textContent.trim());
      return { found: true, codes };
    });
    if (hiddenSection.found) {
      const allHK = hiddenSection.codes.every(c => c.startsWith('HK'));
      record('HK tab: hidden only shows HK stocks', allHK || hiddenSection.codes.length === 0,
        `codes: ${hiddenSection.codes.join(', ')}`);
    } else {
      record('HK tab: hidden section', true, 'no hidden section (ok if no HK hidden)');
    }

    // Check 药明康德 displays correct cost (76.21) and positive P&L
    const yakuming = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const codeSpan = spans.find(s => s.textContent.trim() === 'HK02359');
      if (!codeSpan) return null;
      const row = codeSpan.closest('div');
      return row ? row.textContent : null;
    });
    if (yakuming) {
      const hasCorrectCost = yakuming.includes('80.9');
      record('HK tab: 药明康德 cost correct (80.9x)', hasCorrectCost, yakuming.slice(0, 120));
    }

    // ════════════════════════════════════════════════
    // 3. Dashboard A tab — switch to HK and back
    // ════════════════════════════════════════════════
    console.log('\n═══ 3. Tab switching ═══');
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(2000);

    // Click market switch button: HK
    const clickedHK = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const hk = btns.find(b => (b.textContent || '').trim() === 'HK');
      if (hk) { hk.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);
    record('Tab switch: A → HK', clickedHK && page.url().includes('tab=HK'));

    // Click back to A-share
    const clickedA = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const a = btns.find(b => (b.textContent || '').trim() === 'A-share');
      if (a) { a.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);
    record('Tab switch: HK → A', clickedA && page.url().includes('tab=A'));

    // ════════════════════════════════════════════════
    // 4. Alerts page
    // ════════════════════════════════════════════════
    console.log('\n═══ 4. Alerts page ═══');
    await page.goto(`${BASE}/alerts`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${DIR}/checkup_03_alerts.png`, fullPage: true });

    const hasError_alerts = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]'))
    );
    record('Alerts: no [ERROR] banner', !hasError_alerts);

    const alertCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const match = spans.find(s => (s.textContent || '').match(/(\d+) visible/));
      return match ? match.textContent.trim() : 'not found';
    });
    record('Alerts: event count shown', alertCount !== 'not found', alertCount);

    const alertsHasUnifiedTabs = await page.evaluate(() => {
      const txt = document.body?.innerText || '';
      return txt.includes('holdings') && txt.includes('watching');
    });
    record('Alerts: unified holdings/watching tabs', alertsHasUnifiedTabs);

    // Check events have proper structure (time, level, kind),
    // or an explicit empty-state message when there are no events.
    const alertEventState = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      const eventRows = divs.filter(d => {
        const t = d.textContent || '';
        return /\d{2}:\d{2}:\d{2}/.test(t) && /L[1-3]/.test(t);
      }).length;
      const body = document.body?.innerText || '';
      const hasEmptyState =
        body.includes('No alert events today') ||
        body.includes('0 visible') ||
        body.includes('Events reset daily');
      return { eventRows, hasEmptyState };
    });
    record(
      'Alerts: events rendered (or empty-state)',
      alertEventState.eventRows > 0 || alertEventState.hasEmptyState,
      alertEventState.eventRows > 0
        ? `${alertEventState.eventRows} event rows`
        : 'empty-state shown'
    );

    // ════════════════════════════════════════════════
    // 5. Sim page
    // ════════════════════════════════════════════════
    console.log('\n═══ 5. Sim page ═══');
    await page.goto(`${BASE}/sim`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${DIR}/checkup_04_sim.png`, fullPage: true });

    const hasError_sim = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]') || d.textContent.includes('error'))
    );
    // Sim may show "error" in data, so check for [ERROR] banner specifically
    const simBannerError = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => {
        const t = d.textContent || '';
        return t.startsWith('[ERROR]');
      })
    );
    record('Sim: no [ERROR] banner', !simBannerError);

    // Check summary stats present
    const simSummary = await page.evaluate(() => {
      const text = document.body?.innerText || '';
      return {
        hasReturn: text.includes('收益率') || text.includes('total_return') || text.includes('收益'),
        hasSharpe: text.includes('夏普') || text.includes('Sharpe') || text.includes('sharpe'),
        hasWinRate: text.includes('胜率'),
        hasTotalMktVal: text.includes('总市值'),
        hasTotalAsset: text.includes('总资产'),
        hasShares: text.includes('股数'),
      };
    });
    const simApiForSections = await page.evaluate(async () => {
      try {
        const res = await fetch('/api/sim');
        const data = await res.json();
        return {
          ok: !data.error,
          livePositions: data.live?.positions?.length || 0,
        };
      } catch {
        return { ok: false, livePositions: 0 };
      }
    });
    record('Sim: 收益率 displayed', simSummary.hasReturn);
    record('Sim: 夏普/Sharpe displayed', simSummary.hasSharpe);
    record('Sim: 胜率 displayed', simSummary.hasWinRate);
    record('Sim: 总市值 displayed', simSummary.hasTotalMktVal);
    record('Sim: 总资产 displayed', simSummary.hasTotalAsset);
    const simSharesOk = simSummary.hasShares || (simApiForSections.ok && simApiForSections.livePositions === 0);
    record(
      'Sim: 股数 column displayed (or no live positions)',
      simSharesOk,
      simSummary.hasShares ? 'shares column found' : `live positions: ${simApiForSections.livePositions}`
    );

    // Check sim has live positions or replay trades
    const simContent = await page.evaluate(() => {
      const text = document.body?.innerText || '';
      return {
        hasPositions: text.includes('实时持仓') || text.includes('SIM'),
        hasTrades: text.includes('交易记录') || text.includes('已完成') || text.includes('操作记录'),
      };
    });
    const simHasUnifiedTabs = await page.evaluate(() => {
      const txt = document.body?.innerText || '';
      return txt.includes('holdings') && txt.includes('watching');
    });
    const simPositionsOk = simContent.hasPositions || (simApiForSections.ok && simApiForSections.livePositions === 0);
    record(
      'Sim: positions section (or no live positions)',
      simPositionsOk,
      simContent.hasPositions ? 'positions section found' : `live positions: ${simApiForSections.livePositions}`
    );
    record('Sim: trades section', simContent.hasTrades);
    record('Sim: unified holdings/watching tabs', simHasUnifiedTabs);

    // ════════════════════════════════════════════════
    // 6. Manage page
    // ════════════════════════════════════════════════
    console.log('\n═══ 6. Manage page ═══');
    await page.goto(`${BASE}/manage`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForFunction(() => !(document.body?.innerText || '').includes('Loading config...'), { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${DIR}/checkup_05_manage.png`, fullPage: true });

    const hasError_manage = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]'))
    );
    record('Manage: no [ERROR] banner', !hasError_manage);

    // Check table headers
    const manageHeaders = await page.evaluate(() => {
      const text = document.body?.innerText || '';
      return {
        hasType: text.includes('type'),
        hasCode: text.includes('code'),
        hasCost: text.includes('cost'),
        hasAbove: text.includes('above'),
        hasBelow: text.includes('below'),
        hasHide: text.includes('hide'),
      };
    });
    record('Manage: table headers present',
      manageHeaders.hasType && manageHeaders.hasCode,
      `type:${manageHeaders.hasType} code:${manageHeaders.hasCode} above:${manageHeaders.hasAbove}`);

    // Check rows rendered
    const manageRows = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).filter(s =>
        s.textContent.trim() === 'PROD' || s.textContent.trim() === 'DEV'
      ).length
    );
    record('Manage: rows rendered', manageRows > 0, `${manageRows} rows`);
    const manageHasUnifiedTabs = await page.evaluate(() => {
      const txt = document.body?.innerText || '';
      return txt.includes('holdings') && txt.includes('watching');
    });
    record('Manage: unified holdings/watching tabs', manageHasUnifiedTabs);

    // Trade plan section should NOT be on manage page (moved to StockDrawer)
    const manageHasPlans = await page.locator('button:has-text("新建计划")').count();
    record('Manage: no trade plan section (新建计划 removed)', manageHasPlans === 0, `count=${manageHasPlans}`);

    const manageHasPlanControls = await page.locator('button:has-text("添加条件")').count();
    record('Manage: no plan order controls (添加条件 removed)', manageHasPlanControls === 0, `count=${manageHasPlanControls}`);

    // ════════════════════════════════════════════════
    // 6.5 Watching page
    // ════════════════════════════════════════════════
    console.log('\n═══ 6.5 Watching page ═══');
    await page.goto(`${BASE}/watching?tab=A`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${DIR}/checkup_06_watching.png`, fullPage: true });

    const hasError_watching = await page.evaluate(() =>
      Array.from(document.querySelectorAll('div')).some(d => d.textContent.includes('[ERROR]'))
    );
    record('Watching: no [ERROR] banner', !hasError_watching);

    const watchingRows = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).filter(s =>
        s.textContent.trim() === 'DEV' || s.textContent.trim() === '★ DEV' || s.textContent.trim() === '★DEV'
      ).length
    );
    record('Watching: DEV rows rendered', watchingRows > 0, `${watchingRows} rows`);

    const watchingHasSections = await page.evaluate(() => {
      const txt = document.body?.innerText || '';
      return txt.includes('watching:stocks') || txt.includes('watching:ETF');
    });
    record('Watching: section headers present', watchingHasSections);

    const getWatchingColumnValues = async (colIdx, parserKind = 'number') => page.evaluate(({ colIdx, parserKind }) => {
      const parseCell = (txt) => {
        const raw = String(txt || '').trim();
        if (!raw || raw === '-') return NaN;
        if (parserKind === 'pct') {
          const n = Number(raw.replace('%', '').replace(/\s/g, ''));
          return Number.isFinite(n) ? n : NaN;
        }
        if (parserKind === 'money') {
          let t = raw.replace(/[¥,\s]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
          let m = 1;
          if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
          else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
          const n = Number(t);
          return Number.isFinite(n) ? n * m : NaN;
        }
        const n = Number(raw.replace(/\s/g, ''));
        return Number.isFinite(n) ? n : NaN;
      };
      const rows = Array.from(document.querySelectorAll('span'))
        .filter((s) => {
          const t = (s.textContent || '').trim();
          return t === 'DEV' || t === '★ DEV' || t === '★DEV';
        })
        .map((s) => s.parentElement)
        .filter(Boolean);
      return rows
        .slice(0, 8)
        .map((row) => {
          const spans = row.querySelectorAll(':scope > span');
          return parseCell(spans[colIdx]?.textContent || '');
        })
        .filter((v) => Number.isFinite(v));
    }, { colIdx, parserKind });
    const isAsc = (arr) => arr.length >= 2 && arr.every((v, i) => i === 0 || arr[i - 1] <= v);
    const isDesc = (arr) => arr.length >= 2 && arr.every((v, i) => i === 0 || arr[i - 1] >= v);
    const sameHead = (a, b, n = 4) => a.slice(0, n).every((v, i) => v === b[i]);

    async function verifyWatchingSort(label, colIdx, parserKind) {
      let clicked = false;
      try {
        await page.locator('span', { hasText: label }).first().click();
        await page.waitForTimeout(120);
        clicked = true;
      } catch { clicked = false; }
      const first = await getWatchingColumnValues(colIdx, parserKind);
      if (clicked) {
        await page.locator('span', { hasText: label }).first().click();
        await page.waitForTimeout(120);
      }
      const second = await getWatchingColumnValues(colIdx, parserKind);
      return {
        clicked,
        toggled: clicked && isDesc(first) && isAsc(second),
        ascOk: isAsc(second),
        first,
        second,
      };
    }

    // watching sort: 涨跌幅 (col 4: type/code/name/price/change/chgAmt/volRatio/turnover/amount/...)
    const sortChange = await verifyWatchingSort('涨跌幅', 4, 'pct');
    record('Watching sort[涨跌幅]: header clickable', sortChange.clicked, `first=${sortChange.first.slice(0,4).join(',')}`);
    record('Watching sort[涨跌幅]: direction toggles', sortChange.toggled, `first=${sortChange.first.slice(0,4).join(',')} second=${sortChange.second.slice(0,4).join(',')}`);
    record('Watching sort[涨跌幅]: second click ascending', sortChange.ascOk, `second=${sortChange.second.slice(0,4).join(',')}`);

    // watching sort: 成交额 (col 8)
    const sortAmount = await verifyWatchingSort('成交额', 8, 'money');
    record('Watching sort[成交额]: direction toggles', sortAmount.toggled, `first=${sortAmount.first.slice(0,4).join(',')} second=${sortAmount.second.slice(0,4).join(',')}`);

    // watching sort: 量比 (col 6)
    const sortVolRatio = await verifyWatchingSort('量比', 6, 'number');
    record('Watching sort[量比]: direction toggles', sortVolRatio.toggled, `first=${sortVolRatio.first.slice(0,4).join(',')} second=${sortVolRatio.second.slice(0,4).join(',')}`);

    // collapse/expand should not destroy current sort
    const beforeCollapse = await getWatchingColumnValues(6, 'number');
    const stockSectionToggle = page.locator('div', { hasText: 'watching:stocks' }).first();
    await stockSectionToggle.click();
    await page.waitForTimeout(300);
    await stockSectionToggle.click();
    await page.waitForTimeout(500);
    const afterExpand = await getWatchingColumnValues(6, 'number');
    // Check sort direction preserved (ascending), not exact values (data may refresh)
    const sortPreserved = afterExpand.length >= 2 && afterExpand.every((v, i) => i === 0 || afterExpand[i - 1] <= v);
    record('Watching: collapse/expand keeps sort order', sortPreserved, `before=${beforeCollapse.slice(0,4).join(',')} after=${afterExpand.slice(0,4).join(',')}`);

    // tab switch should keep watching sort state
    const beforeTabSwitch = await getWatchingColumnValues(8, 'money');
    await page.locator('button', { hasText: 'HK' }).first().click();
    await page.waitForTimeout(250);
    await page.locator('button', { hasText: 'A-share' }).first().click();
    await page.waitForTimeout(250);
    const afterTabSwitch = await getWatchingColumnValues(8, 'money');
    record('Watching: sort persists across A/HK switch', sameHead(beforeTabSwitch, afterTabSwitch), `before=${beforeTabSwitch.slice(0,4).join(',')} after=${afterTabSwitch.slice(0,4).join(',')}`);

    // ════════════════════════════════════════════════
    // 7. API health checks
    // ════════════════════════════════════════════════
    console.log('\n═══ 7. API health ═══');

    // /api/metrics
    const metrics = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      return {
        ok: !data.error,
        serviceCount: (data.services || []).length,
        hasAlertEvents: (data.alertEvents || []).length,
        hasSettings: !!data.settings,
        error: data.error || null,
      };
    });
    record('API /api/metrics', metrics.ok, `${metrics.serviceCount} services, ${metrics.hasAlertEvents} events`);
    if (metrics.error) console.log(`    error: ${metrics.error}`);

    // /api/sim
    const sim = await page.evaluate(async () => {
      try {
        const res = await fetch('/api/sim');
        const text = await res.text();
        if (!text.startsWith('{') && !text.startsWith('[')) {
          return { ok: false, error: `Non-JSON response (status ${res.status})` };
        }
        const data = JSON.parse(text);
        return {
          ok: !data.error,
          hasSummary: !!data.summary,
          tradeCount: (data.trades || []).length,
          dailyCount: (data.daily_pnl || []).length,
          hasLive: !!data.live,
          livePositions: data.live?.positions?.length || 0,
          liveTrades: data.live?.trades?.length || 0,
          error: data.error || null,
        };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });
    record('API /api/sim', sim.ok,
      `trades:${sim.tradeCount} daily:${sim.dailyCount} live_pos:${sim.livePositions} live_trades:${sim.liveTrades}`);
    if (sim.error) console.log(`    error: ${sim.error}`);

    // ════════════════════════════════════════════════
    // 8. Data consistency checks
    // ════════════════════════════════════════════════
    console.log('\n═══ 8. Data consistency ═══');

    // Navigate to dashboard first (ensures API fetch context is correct)
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(1000);

    const consistency = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const services = data.services || [];
      const issues = [];

      for (const s of services) {
        // Holdings should have cost and shares
        if (s.type === 'holding') {
          if (s.cost == null) issues.push(`${s.id} ${s.name}: holding missing cost`);
          if (s.shares == null) issues.push(`${s.id} ${s.name}: holding missing shares`);
        }

        // Price should be > 0
        if (s.price <= 0 && s.type === 'holding') {
          issues.push(`${s.id} ${s.name}: price is ${s.price}`);
        }

        // P&L should be calculated for holdings with cost
        if (s.type === 'holding' && s.cost != null && s.cost !== 0 && s.pnl == null) {
          issues.push(`${s.id} ${s.name}: holding with cost but no pnl`);
        }

        // Check for NaN/undefined in critical fields
        if (isNaN(s.price)) issues.push(`${s.id}: price is NaN`);
        if (isNaN(s.change)) issues.push(`${s.id}: change is NaN`);
      }

      // Alert events from SQLite
      const events = data.alertEvents || [];
      for (const e of events.slice(0, 5)) {
        if (!e.ts) issues.push(`alert event missing ts`);
        if (!e.display) issues.push(`alert event missing display`);
      }

      return { issues, total: services.length };
    });
    record('Data: no consistency issues', consistency.issues.length === 0,
      consistency.issues.length > 0 ? consistency.issues.join('; ') : `${consistency.total} services checked`);

    // Check new stocks added correctly
    const newStocks = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const services = data.services || [];
      const check = ['HK09618', 'HK09927', 'HK03288', 'HK07262'];
      return check.map(code => {
        const s = services.find(x => x.id === code);
        return { code, found: !!s, type: s?.type, cost: s?.cost, shares: s?.shares, name: s?.name };
      });
    });
    for (const s of newStocks) {
      record(`New stock ${s.code} ${s.name || '?'}`,
        s.found && s.type === 'holding' && s.cost > 0 && s.shares > 0,
        s.found ? `type=${s.type} cost=${s.cost} shares=${s.shares}` : 'NOT FOUND');
    }

    // Check sold stocks are watching (no cost/shares)
    const soldStocks = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const services = data.services || [];
      const check = ['601088', '601318', 'HK03896', '159326', '159691'];
      return check.map(code => {
        const s = services.find(x => x.id === code);
        return { code, found: !!s, type: s?.type, cost: s?.cost, shares: s?.shares, name: s?.name };
      });
    });
    for (const s of soldStocks) {
      const isWatching = !s.found || s.type !== 'holding' || (s.cost == null && s.shares == null);
      record(`Sold ${s.code} ${s.name || '?'}: not holding`,
        isWatching,
        s.found ? `type=${s.type} cost=${s.cost} shares=${s.shares}` : 'not in services');
    }

    // ════════════════════════════════════════════════
    // 9. Navigation completeness
    // ════════════════════════════════════════════════
    console.log('\n═══ 9. Navigation ═══');

    const pages = [
      { path: '/', name: 'Dashboard' },
      { path: '/alerts', name: 'Alerts' },
      { path: '/sim', name: 'Sim' },
      { path: '/sector', name: 'Sector' },
      { path: '/watching', name: 'Watching' },
      { path: '/manage', name: 'Manage' },
    ];
    for (const pg of pages) {
      const resp = await page.goto(`${BASE}${pg.path}`, { waitUntil: 'commit', timeout: 30000 });
      record(`${pg.name} ${pg.path} loads`, resp?.status() === 200);
    }

    // 404
    const resp404 = await page.goto(`${BASE}/nonexistent`, { waitUntil: 'commit', timeout: 30000 });
    record('404 for invalid path', resp404?.status() === 404);

    // ════════════════════════════════════════════════
    // 10. Console errors summary
    // ════════════════════════════════════════════════
    console.log('\n═══ 10. Console errors ═══');
    const realErrors = consoleErrors.filter(e =>
      !e.includes('favicon') && !e.includes('404') && !e.includes('ERR_CONNECTION')
      && !e.includes('/api/summary')  // summary endpoint may not exist
      && !e.includes('Unexpected end of JSON')  // transient API response
      && !e.includes('net::ERR_FAILED') // font/network flakiness in headless
      && !e.includes('fonts.googleapis.com')
      && !e.includes('fonts.gstatic.com')
    );
    record('No JS console errors', realErrors.length === 0,
      realErrors.length > 0 ? realErrors.slice(0, 3).join(' | ').slice(0, 200) : 'clean');

  } finally {
    await browser.close();
  }

  // ════════════════════════════════════════════════
  // Final report
  // ════════════════════════════════════════════════
  const pass = results.filter(r => r.ok).length;
  const fail = results.filter(r => !r.ok).length;
  console.log(`\n${'═'.repeat(60)}`);
  console.log(`CHECKUP COMPLETE: ${results.length} tests | ${pass} PASS | ${fail} FAIL`);
  if (fail > 0) {
    console.log('\nFailed:');
    results.filter(r => !r.ok).forEach(r => console.log(`  ✗ ${r.name}`));
  }
  console.log(`${'═'.repeat(60)}`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(2); });
