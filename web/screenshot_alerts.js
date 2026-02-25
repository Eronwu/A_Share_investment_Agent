const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 }
  });
  
  // Navigate and wait for network idle + 2 seconds
  await page.goto('http://localhost:3120/alerts', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  
  // Take full-page screenshot
  await page.screenshot({
    path: '/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/screenshots/alerts-check.png',
    fullPage: true
  });
  
  console.log('Screenshot saved');
  
  await browser.close();
})();
