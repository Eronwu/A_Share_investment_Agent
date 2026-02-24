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
    // Navigate to alerts page
    await page.goto(`${BASE}/alerts`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Check if daily summary card exists
    const summaryCard = await page.evaluate(() => {
      const allDivs = document.querySelectorAll('div');
      for (const div of allDivs) {
        if (div.textContent && div.textContent.includes('信号日报')) {
          return {
            exists: true,
            text: div.textContent.substring(0, 200),
          };
        }
      }
      return { exists: false, text: '' };
    });
    record('Daily summary card exists', summaryCard.exists, summaryCard.text.substring(0, 80));

    // Check stats chips are present
    const hasStats = await page.evaluate(() => {
      const spans = document.querySelectorAll('span');
      for (const span of spans) {
        if (span.textContent && span.textContent.includes('bull:') && span.textContent.includes('bear:')) {
          return true;
        }
      }
      return false;
    });
    record('Stats chips (bull/bear) visible', hasStats);

    // Check report content is rendered (expanded by default)
    const hasReport = await page.evaluate(() => {
      const allDivs = document.querySelectorAll('div');
      for (const div of allDivs) {
        if (div.textContent && (div.textContent.includes('逐股摘要') || div.textContent.includes('市场整体评估') || div.textContent.includes('统计'))) {
          return true;
        }
      }
      return false;
    });
    record('Report content rendered (expanded)', hasReport);

    // Check event list still renders below
    const hasEvents = await page.evaluate(() => {
      const allDivs = document.querySelectorAll('div');
      for (const div of allDivs) {
        if (div.textContent && div.textContent.includes('[L')) {
          return true;
        }
      }
      return false;
    });
    record('Alert event list renders below card', hasEvents);

    // Screenshot full page
    await page.screenshot({ path: `${DIR}/alerts_summary_full.png`, fullPage: true });
    console.log(`\n  Screenshot saved: ${DIR}alerts_summary_full.png`);

    // Screenshot just the top area (summary card)
    await page.screenshot({ path: `${DIR}/alerts_summary_card.png`, clip: { x: 0, y: 0, width: 1440, height: 600 } });
    console.log(`  Screenshot saved: ${DIR}alerts_summary_card.png`);

    // Test collapse/expand toggle
    const toggleResult = await page.evaluate(() => {
      const allDivs = document.querySelectorAll('div');
      for (const div of allDivs) {
        const style = div.getAttribute('style') || '';
        if (style.includes('cursor: pointer') && div.textContent && div.textContent.includes('信号日报')) {
          div.click();
          return true;
        }
      }
      return false;
    });
    await page.waitForTimeout(500);
    record('Collapse toggle clickable', toggleResult);

    // After collapsing, report content should be hidden
    const reportHiddenAfterCollapse = await page.evaluate(() => {
      // Check that report body is no longer visible
      const allDivs = document.querySelectorAll('div');
      for (const div of allDivs) {
        if (div.textContent && div.textContent.includes('逐股摘要')) {
          // Check if it's inside a visible container
          const rect = div.getBoundingClientRect();
          if (rect.height > 0) return false; // still visible
        }
      }
      return true;
    });
    record('Report hidden after collapse', reportHiddenAfterCollapse);

    // Screenshot collapsed state
    await page.screenshot({ path: `${DIR}/alerts_summary_collapsed.png`, clip: { x: 0, y: 0, width: 1440, height: 400 } });
    console.log(`  Screenshot saved: ${DIR}alerts_summary_collapsed.png`);

  } finally {
    await browser.close();
  }

  const fail = results.filter(r => !r.ok).length;
  console.log(`\nTotal: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(2); });
