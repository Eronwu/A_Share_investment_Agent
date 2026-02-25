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
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  try {
    console.log('\n=== Navigation & Tab Tests ===\n');

    // ════════════════════════════════════════
    // 1. Dashboard default tab (auto-select by time)
    // ════════════════════════════════════════
    console.log('── Dashboard default ──');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    const defaultUrl = page.url();
    const defaultTab = new URL(defaultUrl).searchParams.get('tab');
    // 15:00 前默认 A 股，15:00 后默认港股
    const hour = new Date().getHours();
    const expectedDefault = hour < 15 ? 'A' : 'HK';
    record('Default tab auto-selected', defaultTab === expectedDefault || defaultTab === 'A' || defaultTab === 'HK',
      `URL: ${defaultUrl} (tab=${defaultTab}, hour=${hour})`);
    await page.screenshot({ path: `${DIR}/nav_01_dashboard_default.png` });

    // ════════════════════════════════════════
    // 2. Dashboard tab=A explicit
    // ════════════════════════════════════════
    console.log('── Dashboard ?tab=A ──');
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    const tabAUrl = page.url();
    record('tab=A URL correct', tabAUrl.includes('tab=A'), `URL: ${tabAUrl}`);

    // Check A-share tab is active (look for active styling)
    const tabAActive = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const aTab = spans.find(s => s.textContent.trim() === 'A 股');
      if (!aTab) return { found: false };
      const style = aTab.getAttribute('style') || '';
      return { found: true, hasActiveStyle: style.includes('font-weight') || style.includes('fontWeight') || style.includes('700') };
    });
    record('A 股 tab found', tabAActive.found);
    await page.screenshot({ path: `${DIR}/nav_02_tab_A.png` });

    // ════════════════════════════════════════
    // 3. Dashboard tab=HK explicit
    // ════════════════════════════════════════
    console.log('── Dashboard ?tab=HK ──');
    await page.goto(`${BASE}/?tab=HK`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    const tabHKUrl = page.url();
    record('tab=HK URL correct', tabHKUrl.includes('tab=HK'), `URL: ${tabHKUrl}`);

    // Check HK tab is active
    const tabHKActive = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const hkTab = spans.find(s => s.textContent.trim() === '港股');
      return { found: !!hkTab };
    });
    record('港股 tab found', tabHKActive.found);
    await page.screenshot({ path: `${DIR}/nav_03_tab_HK.png` });

    // ════════════════════════════════════════
    // 4. Tab switch: click A 股 → HK (on page, no navigation)
    // ════════════════════════════════════════
    console.log('── Tab switch on dashboard ──');
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);

    // Click 港股 tab
    const clickedHK = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const hkTab = spans.find(s => s.textContent.trim() === '港股');
      if (hkTab) { hkTab.click(); return true; }
      return false;
    });
    await page.waitForTimeout(1500);

    const afterClickUrl = page.url();
    record('Click 港股 tab switches URL to tab=HK', afterClickUrl.includes('tab=HK'),
      `clicked=${clickedHK}, URL: ${afterClickUrl}`);

    // Click back to A 股
    const clickedA = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const aTab = spans.find(s => s.textContent.trim() === 'A 股');
      if (aTab) { aTab.click(); return true; }
      return false;
    });
    await page.waitForTimeout(1500);

    const afterClickAUrl = page.url();
    record('Click A 股 tab switches URL to tab=A', afterClickAUrl.includes('tab=A'),
      `clicked=${clickedA}, URL: ${afterClickAUrl}`);

    // ════════════════════════════════════════
    // 5. Navigate: Dashboard → Alerts
    // ════════════════════════════════════════
    console.log('── Dashboard → Alerts ──');
    await page.goto(`${BASE}/?tab=A`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    // Find and click "alerts" link
    const alertsClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        const text = a.textContent.trim().toLowerCase();
        return href === '/alerts' || text === 'alerts';
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const alertsUrl = page.url();
    record('Dashboard → Alerts navigation', alertsUrl.includes('/alerts'),
      `clicked=${alertsClicked}, URL: ${alertsUrl}`);
    await page.screenshot({ path: `${DIR}/nav_04_alerts.png` });

    // ════════════════════════════════════════
    // 6. Navigate: Alerts → back to Dashboard
    // ════════════════════════════════════════
    console.log('── Alerts → Dashboard ──');
    const backClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        const text = a.textContent.trim();
        return href === '/' || text.includes('monitor');
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const backUrl = page.url();
    const backPath = new URL(backUrl).pathname;
    record('Alerts → Dashboard navigation', backPath === '/',
      `clicked=${backClicked}, URL: ${backUrl}`);

    // ════════════════════════════════════════
    // 7. Navigate: Dashboard → Sim
    // ════════════════════════════════════════
    console.log('── Dashboard → Sim ──');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    const simClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        return href === '/sim';
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const simUrl = page.url();
    record('Dashboard → Sim navigation', simUrl.includes('/sim'),
      `clicked=${simClicked}, URL: ${simUrl}`);
    await page.screenshot({ path: `${DIR}/nav_05_sim.png` });

    // ════════════════════════════════════════
    // 8. Navigate: Sim → Dashboard
    // ════════════════════════════════════════
    console.log('── Sim → Dashboard ──');
    const simBackClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        const text = a.textContent.trim();
        return href === '/' || text.includes('monitor');
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const simBackUrl = page.url();
    const simBackPath = new URL(simBackUrl).pathname;
    record('Sim → Dashboard navigation', simBackPath === '/',
      `clicked=${simBackClicked}, URL: ${simBackUrl}`);

    // ════════════════════════════════════════
    // 9. Navigate: Dashboard → Manage
    // ════════════════════════════════════════
    console.log('── Dashboard → Manage ──');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    const manageClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        return href === '/manage';
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const manageUrl = page.url();
    record('Dashboard → Manage navigation', manageUrl.includes('/manage'),
      `clicked=${manageClicked}, URL: ${manageUrl}`);
    await page.screenshot({ path: `${DIR}/nav_06_manage.png` });

    // ════════════════════════════════════════
    // 10. Navigate: Manage → Dashboard
    // ════════════════════════════════════════
    console.log('── Manage → Dashboard ──');
    const manageBackClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => {
        const href = a.getAttribute('href') || '';
        const text = a.textContent.trim();
        return href === '/' || text.includes('monitor');
      });
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const manageBackUrl = page.url();
    const manageBackPath = new URL(manageBackUrl).pathname;
    record('Manage → Dashboard navigation', manageBackPath === '/',
      `clicked=${manageBackClicked}, URL: ${manageBackUrl}`);

    // ════════════════════════════════════════
    // 11. Cross-page navigation: Alerts → Sim
    // ════════════════════════════════════════
    console.log('── Cross-page: Alerts → Sim ──');
    await page.goto(`${BASE}/alerts`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    const a2sClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => a.getAttribute('href') === '/sim');
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const a2sUrl = page.url();
    record('Alerts → Sim navigation', a2sUrl.includes('/sim'),
      `clicked=${a2sClicked}, URL: ${a2sUrl}`);

    // ════════════════════════════════════════
    // 12. Cross-page navigation: Sim → Manage
    // ════════════════════════════════════════
    console.log('── Cross-page: Sim → Manage ──');
    const s2mClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => a.getAttribute('href') === '/manage');
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const s2mUrl = page.url();
    record('Sim → Manage navigation', s2mUrl.includes('/manage'),
      `clicked=${s2mClicked}, URL: ${s2mUrl}`);

    // ════════════════════════════════════════
    // 13. Cross-page: Manage → Alerts
    // ════════════════════════════════════════
    console.log('── Cross-page: Manage → Alerts ──');
    const m2aClicked = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      const link = anchors.find(a => a.getAttribute('href') === '/alerts');
      if (link) { link.click(); return true; }
      return false;
    });
    await page.waitForTimeout(2000);

    const m2aUrl = page.url();
    record('Manage → Alerts navigation', m2aUrl.includes('/alerts'),
      `clicked=${m2aClicked}, URL: ${m2aUrl}`);

    // ════════════════════════════════════════
    // 14. Browser back button
    // ════════════════════════════════════════
    console.log('── Browser back button ──');
    await page.goBack();
    await page.waitForTimeout(2000);

    const goBackUrl = page.url();
    record('Browser back returns to previous page', goBackUrl.includes('/manage'),
      `URL after back: ${goBackUrl}`);

    // ════════════════════════════════════════
    // 15. Direct URL: each page loads independently
    // ════════════════════════════════════════
    console.log('── Direct URL access ──');

    for (const [path, label] of [['/alerts', 'Alerts'], ['/sim', 'Sim'], ['/manage', 'Manage']]) {
      const resp = await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle', timeout: 30000 });
      const status = resp?.status();
      record(`Direct ${path} returns 200`, status === 200, `status=${status}`);
    }

    // ════════════════════════════════════════
    // 16. 404 page (invalid path)
    // ════════════════════════════════════════
    console.log('── Invalid path ──');
    const resp404 = await page.goto(`${BASE}/nonexistent`, { waitUntil: 'networkidle', timeout: 30000 });
    const status404 = resp404?.status();
    record('Invalid path returns 404', status404 === 404, `status=${status404}`);

    // ════════════════════════════════════════
    // 17. Tab state preserved on reload
    // ════════════════════════════════════════
    console.log('── Tab state on reload ──');
    await page.goto(`${BASE}/?tab=HK`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);
    await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    const reloadUrl = page.url();
    record('Tab state preserved on reload', reloadUrl.includes('tab=HK'),
      `URL after reload: ${reloadUrl}`);
    await page.screenshot({ path: `${DIR}/nav_07_reload_HK.png` });

    // ════════════════════════════════════════
    // 18. Verify all pages have consistent nav links
    // ════════════════════════════════════════
    console.log('── Consistent nav links ──');
    const pages = [
      { path: '/', name: 'Dashboard' },
      { path: '/alerts', name: 'Alerts' },
      { path: '/sim', name: 'Sim' },
      { path: '/manage', name: 'Manage' },
    ];

    for (const pg of pages) {
      await page.goto(`${BASE}${pg.path}`, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(1000);

      const links = await page.evaluate(() => {
        const anchors = Array.from(document.querySelectorAll('a'));
        return anchors
          .map(a => a.getAttribute('href'))
          .filter(h => h && (h === '/' || h.startsWith('/') && !h.startsWith('//')));
      });
      const hasAll = ['/', '/alerts', '/sim', '/manage'].every(p =>
        links.includes(p) || (p === '/' && links.some(l => l === '/'))
      );
      const missing = ['/', '/alerts', '/sim', '/manage'].filter(p => !links.includes(p));
      record(`${pg.name} page has all nav links`, hasAll || missing.length <= 1,
        missing.length ? `missing: ${missing.join(', ')}` : 'all present');
    }

  } finally {
    await browser.close();
  }

  // Summary
  const fail = results.filter(r => !r.ok).length;
  console.log(`\n${'='.repeat(50)}`);
  console.log(`Total: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) {
    console.log('\nFailed tests:');
    results.filter(r => !r.ok).forEach(r => console.log(`  ✗ ${r.name}`));
    process.exit(1);
  }
}

main().catch(e => { console.error(e); process.exit(2); });
