import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR = new URL(".", import.meta.url).pathname;

const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}${d ? ": " + d : ""}`);

const browser = await chromium.launch({ headless: true });
const page = await (
  await browser.newContext({ viewport: { width: 1280, height: 900 } })
).newPage();

page.on("console", (msg) => {
  if (msg.type() === "error") console.log("  [CONSOLE ERROR]", msg.text().slice(0, 120));
});

// Block external requests that cause font-load timeouts
await page.route("**/*.googleapis.com/**", (r) => r.abort());
await page.route("**/*.gstatic.com/**", (r) => r.abort());

// Screenshot helper — avoids font-load timeout
async function shot(name) {
  try {
    await page.screenshot({ path: `${DIR}/${name}`, timeout: 8000 });
  } catch {
    try {
      await page.screenshot({ path: `${DIR}/${name}`, timeout: 8000, animations: "disabled" });
    } catch (e2) {
      console.log(`  [WARN] screenshot ${name} failed: ${e2.message.slice(0, 60)}`);
    }
  }
}

let pass = 0;
let fail = 0;
function record(name, result, detail = "") {
  if (result) {
    ok(name + (detail ? " -- " + detail : ""));
    pass++;
  } else {
    ng(name, detail);
    fail++;
  }
}

// ════════════════════════════════════════════════
// 1. Indicator column — no ★PROD / PROD / DEV in rows
// ════════════════════════════════════════════════
console.log("\n═══ 1. Indicator column (no type labels in rows) ═══");
await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(6000);
await shot("drawer_01_holdings_A.png");

// Check no '★PROD' or 'PROD' or 'DEV' text labels in rows
// (they are replaced by the new indicator column with plan/star icons)
const typeLabelsInRows = await page.evaluate(() => {
  const spans = Array.from(document.querySelectorAll("span"));
  return spans.filter((s) => {
    const t = s.textContent.trim();
    return t === "★PROD" || t === "PROD" || t === "★DEV" || t === "DEV";
  }).length;
});
record(
  "Holdings A: no ★PROD/PROD/DEV type labels in rows",
  typeLabelsInRows === 0,
  `found ${typeLabelsInRows} type label spans`
);

// Verify rows ARE rendered (data loaded)
const rowsWithCode_A = await page.evaluate(() => {
  const spans = Array.from(document.querySelectorAll("span"));
  return spans.filter((s) => /^\d{6}$/.test(s.textContent.trim())).length;
});
record("Holdings A: stock rows rendered", rowsWithCode_A > 0, `${rowsWithCode_A} A-share codes`);

// Check for rows with click indicator (title="点击查看详情")
const hasIndicatorCol = await page.evaluate(() => {
  return Array.from(document.querySelectorAll('[title="点击查看详情"]')).length > 0;
});
record("Holdings A: rows have click-to-open title", hasIndicatorCol);

// Also check HK tab
await page.goto(`${BASE}/?tab=HK`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(3000);
const typeLabelsHK = await page.evaluate(() => {
  const spans = Array.from(document.querySelectorAll("span"));
  return spans.filter((s) => {
    const t = s.textContent.trim();
    return t === "★PROD" || t === "PROD" || t === "★DEV" || t === "DEV";
  }).length;
});
record(
  "Holdings HK: no ★PROD/PROD/DEV type labels in rows",
  typeLabelsHK === 0,
  `found ${typeLabelsHK} type label spans`
);

// ════════════════════════════════════════════════
// 2. Row click opens drawer
// ════════════════════════════════════════════════
console.log("\n═══ 2. Row click opens drawer ═══");
await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(6000);
await shot("drawer_02_before_click.png");

// Drawer should NOT be open yet
const drawerBeforeClick = await page.locator("text=基本信息").count();
record("Drawer: not open before click", drawerBeforeClick === 0, `count=${drawerBeforeClick}`);

// Click first stock row
const firstRow = page.locator('[title="点击查看详情"]').first();
const firstRowCount = await firstRow.count();
if (firstRowCount > 0) {
  await firstRow.click();
  await page.waitForTimeout(600);
  await shot("drawer_03_opened.png");

  const drawerOpen = await page.locator("text=基本信息").count();
  record("Drawer: opens on row click", drawerOpen > 0, `基本信息 count=${drawerOpen}`);

  // Verify close button visible
  const closeBtn = page.locator('button[aria-label="Close"]');
  const closeBtnVisible = await closeBtn.isVisible().catch(() => false);
  record("Drawer: close button (aria-label=Close) visible", closeBtnVisible);
} else {
  record("Drawer: row found to click", false, "no rows with title='点击查看详情'");
  record("Drawer: opens on row click", false, "skipped — no row");
  record("Drawer: close button visible", false, "skipped");
}

// ════════════════════════════════════════════════
// 3. × close button closes drawer
// ════════════════════════════════════════════════
console.log("\n═══ 3. × close button ═══");

// Ensure drawer is open
let drawerIsOpen = (await page.locator("text=基本信息").count()) > 0;
if (!drawerIsOpen) {
  const r = page.locator('[title="点击查看详情"]').first();
  if ((await r.count()) > 0) {
    await r.click();
    await page.waitForTimeout(600);
    drawerIsOpen = (await page.locator("text=基本信息").count()) > 0;
  }
}

if (drawerIsOpen) {
  const closeBtn = page.locator('button[aria-label="Close"]');
  await closeBtn.click();
  await page.waitForTimeout(500);
  await shot("drawer_04_closed_x.png");
  const drawerAfterClose = await page.locator("text=基本信息").count();
  record("Drawer: × button closes drawer", drawerAfterClose === 0, `count after close=${drawerAfterClose}`);
} else {
  record("Drawer: × button closes drawer", false, "could not open drawer");
}

// ════════════════════════════════════════════════
// 4. ESC key closes drawer
// ════════════════════════════════════════════════
console.log("\n═══ 4. ESC key closes drawer ═══");
await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(6000);

const escRow = page.locator('[title="点击查看详情"]').first();
if ((await escRow.count()) > 0) {
  await escRow.click();
  await page.waitForTimeout(600);
  const drawerOpenForEsc = await page.locator("text=基本信息").count();
  if (drawerOpenForEsc > 0) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    await shot("drawer_05_closed_esc.png");
    const drawerAfterEsc = await page.locator("text=基本信息").count();
    record("Drawer: ESC key closes drawer", drawerAfterEsc === 0, `count after ESC=${drawerAfterEsc}`);
  } else {
    record("Drawer: ESC key closes drawer", false, "could not open drawer");
  }
} else {
  record("Drawer: ESC key closes drawer", false, "no rows to click");
}

// ════════════════════════════════════════════════
// 5. Backdrop click closes drawer
// ════════════════════════════════════════════════
console.log("\n═══ 5. Backdrop click closes drawer ═══");
await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(6000);

const backdropRow = page.locator('[title="点击查看详情"]').first();
if ((await backdropRow.count()) > 0) {
  await backdropRow.click();
  await page.waitForTimeout(600);
  await shot("drawer_06_before_backdrop.png");
  const drawerOpenForBackdrop = await page.locator("text=基本信息").count();
  if (drawerOpenForBackdrop > 0) {
    // Drawer is 440px wide on right side (1280px viewport)
    // Click at x=100 is well in the backdrop/overlay area
    await page.mouse.click(100, 400);
    await page.waitForTimeout(500);
    await shot("drawer_07_closed_backdrop.png");
    const drawerAfterBackdrop = await page.locator("text=基本信息").count();
    record("Drawer: backdrop click closes drawer", drawerAfterBackdrop === 0, `count after backdrop=${drawerAfterBackdrop}`);
  } else {
    record("Drawer: backdrop click closes drawer", false, "could not open drawer");
  }
} else {
  record("Drawer: backdrop click closes drawer", false, "no rows to click");
}

// ════════════════════════════════════════════════
// 6. Drawer contains expected sections
// ════════════════════════════════════════════════
console.log("\n═══ 6. Drawer content sections ═══");
await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(6000);

const contentRow = page.locator('[title="点击查看详情"]').first();
if ((await contentRow.count()) > 0) {
  await contentRow.click();
  await page.waitForTimeout(800);
  await shot("drawer_08_content.png");

  const pageText = await page.evaluate(() => document.body?.innerText || "");
  record("Drawer: 基本信息 section visible", pageText.includes("基本信息"));
  record("Drawer: 告警阈值 or threshold section visible",
    pageText.includes("告警阈值") || pageText.includes("above") || pageText.includes("上限") || pageText.includes("threshold")
  );
  record("Drawer: 标签 section visible",
    pageText.includes("标签") || pageText.includes("tags") || pageText.includes("tag")
  );

  // Close drawer
  const closeBtn2 = page.locator('button[aria-label="Close"]');
  if ((await closeBtn2.count()) > 0) await closeBtn2.click();
  await page.waitForTimeout(300);
} else {
  record("Drawer: 基本信息 section visible", false, "no rows to click");
  record("Drawer: 告警阈值 or threshold section visible", false, "no rows to click");
  record("Drawer: 标签 section visible", false, "no rows to click");
}

// ════════════════════════════════════════════════
// 7. Manage page has no trade plan section
// ════════════════════════════════════════════════
console.log("\n═══ 7. Manage page — no trade plan section ═══");
await page.goto(`${BASE}/manage`, { waitUntil: "commit", timeout: 30000 });
await page.waitForFunction(
  () => !(document.body?.innerText || "").includes("Loading config..."),
  { timeout: 15000 }
).catch(() => {});
await page.waitForTimeout(2000);
await shot("drawer_09_manage.png");

// Should NOT have 新建计划 button
const newPlanBtn = await page.locator('button:has-text("新建计划")').count();
record("Manage: no 新建计划 button", newPlanBtn === 0, `count=${newPlanBtn}`);

// Should NOT have plan CRUD controls
const planControls = await page.locator('button:has-text("添加条件")').count();
record("Manage: no 添加条件 plan button", planControls === 0, `count=${planControls}`);

// Should NOT have plan creation form (select for stock picker only used in plan form)
const planFormSymbolSelect = await page.evaluate(() => {
  // The manage page may have selects for other purposes; look for plan-specific text
  const text = document.body?.innerText || "";
  // Plan section would have both 新建计划 and 运行中 headers together
  return text.includes("新建计划") || text.includes("添加条件");
});
record("Manage: no plan form content", !planFormSymbolSelect);

// Verify manage page still has its normal content
const manageHasTable = await page.evaluate(() => {
  const text = document.body?.innerText || "";
  return text.includes("type") && text.includes("code");
});
record("Manage: holdings table still present", manageHasTable);

await shot("drawer_10_manage_final.png");

// ════════════════════════════════════════════════
// 8. Watching page — no type labels in rows + drawer works
// ════════════════════════════════════════════════
console.log("\n═══ 8. Watching page — no type labels + drawer ═══");
await page.goto(`${BASE}/watching?tab=A`, { waitUntil: "commit", timeout: 30000 });
await page.waitForTimeout(4000);
await shot("drawer_11_watching_A.png");

const typeLabelsWatching = await page.evaluate(() => {
  const spans = Array.from(document.querySelectorAll("span"));
  return spans.filter((s) => {
    const t = s.textContent.trim();
    return t === "★PROD" || t === "PROD" || t === "★DEV" || t === "DEV";
  }).length;
});
record(
  "Watching A: no ★PROD/PROD/DEV labels in rows",
  typeLabelsWatching === 0,
  `found ${typeLabelsWatching} type label spans`
);

const watchingClickableRows = page.locator('[title="点击查看详情"]');
const watchingRowCount = await watchingClickableRows.count();
record("Watching A: rows have click-to-open indicator", watchingRowCount > 0, `${watchingRowCount} rows`);

if (watchingRowCount > 0) {
  await watchingClickableRows.first().click();
  await page.waitForTimeout(600);
  const watchingDrawerOpen = await page.locator("text=基本信息").count();
  record("Watching A: drawer opens on row click", watchingDrawerOpen > 0);
  if (watchingDrawerOpen > 0) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  }
}

// ════════════════════════════════════════════════
// SUMMARY
// ════════════════════════════════════════════════
console.log(`\n═══ SUMMARY: ${pass} passed, ${fail} failed ═══`);
if (fail > 0) {
  console.log("  Some tests FAILED — review screenshots in web/screenshots/");
}

await browser.close();
process.exit(fail > 0 ? 1 : 0);
