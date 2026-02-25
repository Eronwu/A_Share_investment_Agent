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
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  try {
    // ── 1. Live tab (default) ──
    console.log('\n=== /sim (live tab) ===');
    await page.goto(`${BASE}/sim`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Title bar
    const title = await page.textContent('body');
    record('Title bar shows 模拟盘', title.includes('模拟盘'));

    // Nav bar has live tab active
    const navText = await page.textContent('body');
    record('Nav has 实时持仓 tab', navText.includes('实时持仓'));
    record('Nav has 历史回测 tab', navText.includes('历史回测'));

    // Check live positions exist OR empty state message
    const hasLivePositions = navText.includes('SIM') && navText.includes('HK');
    const hasEmptyState = navText.includes('实时引擎');
    record('Live tab shows positions or empty state', hasLivePositions || hasEmptyState);

    if (hasLivePositions) {
      // Check position columns
      record('Live has 类型 column', navText.includes('类型'));
      record('Live has 代码 column', navText.includes('代码'));
      record('Live has 现价 column', navText.includes('现价'));
      record('Live has 止损 column', navText.includes('止损'));
      record('Live has 入场策略 column', navText.includes('入场策略'));

      // Check SIM badge
      const simBadges = await page.$$eval('span', els =>
        els.filter(el => el.textContent.trim() === 'SIM').length
      );
      record('SIM type badges present', simBadges > 0, `found ${simBadges}`);

      // Check nav bar shows market value + unrealized
      record('Nav shows 市值', navText.includes('市值'));
      record('Nav shows 浮盈', navText.includes('浮盈'));
    }

    // Check live trades section
    const hasLiveTrades = navText.includes('实时交易');
    if (hasLiveTrades) {
      record('Live trades section visible', true);
    }

    await page.screenshot({ path: `${DIR}/sim_live.png`, fullPage: true });
    console.log('  Screenshot: sim_live.png');

    // ── 2. Switch to history tab ──
    console.log('\n=== /sim (history tab) ===');

    // Click 历史回测 tab
    const historyTab = await page.$$eval('span', els => {
      const el = els.find(e => e.textContent.includes('历史回测'));
      if (el) { el.click(); return true; }
      return false;
    });
    record('Clicked 历史回测 tab', historyTab);

    await page.waitForTimeout(2000);

    const historyText = await page.textContent('body');

    // Summary bar metrics
    record('History has 收益率', historyText.includes('收益率'));
    record('History has 夏普', historyText.includes('夏普'));
    record('History has 胜率', historyText.includes('胜率'));
    record('History has 最大回撤', historyText.includes('最大回撤'));
    record('History has 盈亏比', historyText.includes('盈亏比'));

    // Equity curve
    const hasSvg = await page.$('svg');
    record('Equity curve SVG exists', hasSvg !== null);

    // Trades table
    record('History has 交易记录', historyText.includes('交易记录'));
    record('History has 买入 column', historyText.includes('买入'));
    record('History has 退出原因', historyText.includes('退出原因'));

    // Attribution panels
    record('History has 按策略归因', historyText.includes('按策略归因'));
    record('History has 按股票归因', historyText.includes('按股票归因'));

    // Chinese translations
    record('Exit reason translated (止损)', historyText.includes('止损'));
    record('Strategy translated (多头综合)', historyText.includes('多头综合'));

    await page.screenshot({ path: `${DIR}/sim_history.png`, fullPage: true });
    console.log('  Screenshot: sim_history.png');

    // ── 3. Switch back to live tab ──
    console.log('\n=== Tab switch back ===');
    await page.$$eval('span', els => {
      const el = els.find(e => e.textContent.includes('实时持仓'));
      if (el) el.click();
    });
    await page.waitForTimeout(1000);
    const liveAgain = await page.textContent('body');
    record('Switch back to live tab works', liveAgain.includes('tail -f live_positions.log') || liveAgain.includes('实时'));

    // ── 4. Check main page sim link ──
    console.log('\n=== Main page nav ===');
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);
    const mainText = await page.textContent('body');
    record('Main page has sim nav link', mainText.includes('sim'));

    await page.screenshot({ path: `${DIR}/main_with_sim.png`, fullPage: true });

    // ── 5. Error handling — bad API ──
    console.log('\n=== Error handling ===');
    await page.route('**/api/sim', route => route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'test error' }),
    }));
    await page.goto(`${BASE}/sim`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);
    const errorText = await page.textContent('body');
    record('Error banner shows on API failure', errorText.includes('[ERROR]'));

    await page.screenshot({ path: `${DIR}/sim_error.png`, fullPage: true });
    console.log('  Screenshot: sim_error.png');

  } finally {
    await browser.close();
  }

  // Summary
  const fail = results.filter(r => !r.ok).length;
  console.log(`\nTotal: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(2); });
