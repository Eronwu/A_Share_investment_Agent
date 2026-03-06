import { chromium } from 'playwright';

const DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots';
const BASE = 'http://localhost:3120';

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    // Block Google Fonts to avoid font-loading timeout
    bypassCSP: true,
  });
  await context.route('**/fonts.googleapis.com/**', (route) => route.abort());
  await context.route('**/fonts.gstatic.com/**', (route) => route.abort());

  const page = await context.newPage();

  // Navigate to sector page
  console.log('Navigating to /sector...');
  try {
    await page.goto(`${BASE}/sector`, { timeout: 15000, waitUntil: 'load' });
  } catch {
    console.log('Navigation timeout, continuing anyway...');
  }

  // Wait for data to load
  await page.waitForTimeout(6000);

  // Force font readiness
  await page.evaluate(() => document.fonts.ready).catch(() => {});

  // Full page screenshot
  console.log('Taking full page screenshot...');
  await page.screenshot({
    path: `${DIR}/sector_review_full.png`,
    fullPage: true,
    timeout: 10000,
  });
  console.log('Saved sector_review_full.png');

  // Viewport only
  await page.screenshot({
    path: `${DIR}/sector_review_viewport.png`,
    fullPage: false,
    timeout: 10000,
  });
  console.log('Saved sector_review_viewport.png');

  // Try clicking an index row to see the chart modal
  try {
    // Click on first index name to open chart modal
    const firstIndexName = await page.$('[style*="cursor: pointer"] div[style*="cursor: pointer"]');
    if (firstIndexName) {
      await firstIndexName.click();
      await page.waitForTimeout(1000);
      await page.screenshot({
        path: `${DIR}/sector_review_chart_modal.png`,
        fullPage: false,
        timeout: 10000,
      });
      console.log('Saved sector_review_chart_modal.png');
    } else {
      console.log('No index found to click for chart modal');
    }
  } catch (e) {
    console.log('Chart modal screenshot failed:', e.message);
  }

  await browser.close();
  console.log('Done');
}

main().catch(console.error);
