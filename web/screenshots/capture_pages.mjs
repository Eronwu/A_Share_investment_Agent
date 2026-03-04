import { chromium } from 'playwright';

const BASE = 'http://localhost:3120';
const DIR = new URL('.', import.meta.url).pathname;

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });

  // --- 1. Manage page (full page scroll) ---
  console.log('=== Manage page ===');
  const manage = await ctx.newPage();
  await manage.goto(`${BASE}/manage`, { waitUntil: 'networkidle' });
  await manage.waitForTimeout(2000);
  await manage.screenshot({ path: `${DIR}capture_manage_full.png`, fullPage: true });
  console.log('  [OK] capture_manage_full.png');

  // Scroll to trade plans section if it exists
  const tradePlans = manage.locator('text=交易计划').or(manage.locator('text=Trade Plan')).first();
  if (await tradePlans.count() > 0) {
    await tradePlans.scrollIntoViewIfNeeded();
    await manage.waitForTimeout(500);
    await manage.screenshot({ path: `${DIR}capture_manage_tradeplans.png`, fullPage: true });
    console.log('  [OK] capture_manage_tradeplans.png');
  } else {
    console.log('  [SKIP] No trade plans section found');
  }
  await manage.close();

  // --- 2. Sector page ---
  console.log('=== Sector page ===');
  const sector = await ctx.newPage();
  await sector.goto(`${BASE}/sector`, { waitUntil: 'networkidle' });
  await sector.waitForTimeout(2000);
  await sector.screenshot({ path: `${DIR}capture_sector_full.png`, fullPage: true });
  console.log('  [OK] capture_sector_full.png');

  // Click on the first index row to open K-line modal if possible
  const indexRow = sector.locator('tr').filter({ hasText: /\d{4,6}/ }).first();
  if (await indexRow.count() > 0) {
    await indexRow.click();
    await sector.waitForTimeout(1000);
    await sector.screenshot({ path: `${DIR}capture_sector_modal.png`, fullPage: true });
    console.log('  [OK] capture_sector_modal.png');
  }
  await sector.close();

  // --- 3. Sector rotation page ---
  console.log('=== Sector Rotation page ===');
  const rotation = await ctx.newPage();
  await rotation.goto(`${BASE}/sector/rotation`, { waitUntil: 'networkidle' });
  await rotation.waitForTimeout(2000);
  await rotation.screenshot({ path: `${DIR}capture_rotation_full.png`, fullPage: true });
  console.log('  [OK] capture_rotation_full.png');
  await rotation.close();

  // --- 4. Sim page (full page) ---
  console.log('=== Sim page ===');
  const sim = await ctx.newPage();
  await sim.goto(`${BASE}/sim`, { waitUntil: 'networkidle' });
  await sim.waitForTimeout(2000);
  await sim.screenshot({ path: `${DIR}capture_sim_full.png`, fullPage: true });
  console.log('  [OK] capture_sim_full.png');

  // Scroll down to see more sections
  await sim.evaluate(() => window.scrollBy(0, 800));
  await sim.waitForTimeout(500);
  await sim.screenshot({ path: `${DIR}capture_sim_bottom.png` });
  console.log('  [OK] capture_sim_bottom.png');
  await sim.close();

  // --- 5. Alerts page ---
  console.log('=== Alerts page ===');
  const alerts = await ctx.newPage();
  await alerts.goto(`${BASE}/alerts`, { waitUntil: 'networkidle' });
  await alerts.waitForTimeout(2000);
  await alerts.screenshot({ path: `${DIR}capture_alerts_full.png`, fullPage: true });
  console.log('  [OK] capture_alerts_full.png');
  await alerts.close();

  // --- 6. Dashboard A-share (viewport) ---
  console.log('=== Dashboard A-share ===');
  const dashA = await ctx.newPage();
  await dashA.goto(`${BASE}/?tab=A`, { waitUntil: 'networkidle' });
  await dashA.waitForTimeout(2000);
  await dashA.screenshot({ path: `${DIR}capture_dash_A.png`, fullPage: true });
  console.log('  [OK] capture_dash_A.png');
  await dashA.close();

  // --- 7. Dashboard HK ---
  console.log('=== Dashboard HK ===');
  const dashHK = await ctx.newPage();
  await dashHK.goto(`${BASE}/?tab=HK`, { waitUntil: 'networkidle' });
  await dashHK.waitForTimeout(2000);
  await dashHK.screenshot({ path: `${DIR}capture_dash_HK.png`, fullPage: true });
  console.log('  [OK] capture_dash_HK.png');
  await dashHK.close();

  await browser.close();
  console.log('\nAll screenshots captured.');
}

main().catch(e => { console.error(e); process.exit(1); });
