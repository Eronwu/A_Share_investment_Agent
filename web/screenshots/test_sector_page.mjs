import { chromium } from "playwright";

const BASE = "http://localhost:3120";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  // 1. Full page screenshot with all sections expanded
  console.log("1. Loading /sector page...");
  await page.goto(`${BASE}/sector`, { waitUntil: "networkidle", timeout: 15000 });
  await page.waitForTimeout(1500);

  // Take full-page screenshot
  await page.screenshot({
    path: "screenshots/sector_full.png",
    fullPage: true,
  });
  console.log("   -> sector_full.png saved");

  // 2. Check key elements
  const bodyText = await page.evaluate(() => document.body.innerText);

  // Check 4 indices exist
  const indices = ["磷化工", "电力设备", "有色金属", "AI算力"];
  for (const name of indices) {
    if (bodyText.includes(name)) {
      console.log(`   ✓ Index "${name}" found`);
    } else {
      console.log(`   ✗ Index "${name}" NOT found`);
    }
  }

  // Check dropdown labels
  if (bodyText.includes("行业(新浪49)") || bodyText.includes("新浪49")) {
    console.log("   ✓ Dropdown label '行业(新浪49)' correct");
  } else {
    console.log("   ✗ Dropdown label not updated");
  }

  // Check alerts section
  if (bodyText.includes("主线告警")) {
    console.log("   ✓ Mainline alerts section present");
  } else {
    console.log("   ✗ Mainline alerts section missing");
  }

  // Check for approaching alerts
  if (bodyText.includes("接近主线") || bodyText.includes("approaching")) {
    console.log("   ✓ Approaching alert(s) found");
  } else {
    console.log("   ✗ No approaching alerts");
  }

  // 3. Expand first index to see components
  console.log("2. Expanding first index row...");
  // Click on the first index row (磷化工)
  const firstIndexRow = await page.evaluate(() => {
    const spans = [...document.querySelectorAll("span")];
    const target = spans.find(s => s.textContent.includes("磷化工"));
    if (target) {
      // Click the parent row div
      let el = target.closest("div[style*='cursor: pointer']") || target.parentElement?.parentElement;
      if (el) { el.click(); return true; }
    }
    return false;
  });
  if (firstIndexRow) {
    await page.waitForTimeout(500);
    console.log("   -> Expanded 磷化工 row");
  }

  await page.screenshot({
    path: "screenshots/sector_expanded.png",
    fullPage: true,
  });
  console.log("   -> sector_expanded.png saved");

  // 4. Viewport-only screenshot of indices section
  console.log("3. Scrolling to indices section...");
  await page.evaluate(() => {
    const spans = [...document.querySelectorAll("span")];
    const target = spans.find(s => s.textContent.includes("我的指数"));
    if (target) target.scrollIntoView({ block: "start" });
  });
  await page.waitForTimeout(300);
  await page.screenshot({
    path: "screenshots/sector_indices.png",
  });
  console.log("   -> sector_indices.png saved");

  await browser.close();
  console.log("\nDone!");
})();
