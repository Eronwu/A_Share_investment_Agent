import { chromium } from 'playwright';

const SCREENSHOTS_DIR = '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots';
const BASE_URL = 'http://localhost:3120';
const VIEWPORT = { width: 1440, height: 900 };

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  // 1. Manage page - above/below columns
  console.log('1. Navigating to /manage ...');
  await page.goto(`${BASE_URL}/manage`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/01-manage-page-above-below.png`,
    fullPage: true,
  });
  console.log('   Saved: 01-manage-page-above-below.png');

  // 2. Dashboard A-share tab
  console.log('2. Navigating to /?tab=A ...');
  await page.goto(`${BASE_URL}/?tab=A`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/02-dashboard-a-share-tab.png`,
    fullPage: true,
  });
  console.log('   Saved: 02-dashboard-a-share-tab.png');

  // 3. Dashboard HK tab
  console.log('3. Navigating to /?tab=HK ...');
  await page.goto(`${BASE_URL}/?tab=HK`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);
  await page.screenshot({
    path: `${SCREENSHOTS_DIR}/03-dashboard-hk-tab.png`,
    fullPage: true,
  });
  console.log('   Saved: 03-dashboard-hk-tab.png');

  await browser.close();
  console.log('\nAll screenshots captured successfully.');
}

main().catch((err) => {
  console.error('Screenshot capture failed:', err);
  process.exit(1);
});
