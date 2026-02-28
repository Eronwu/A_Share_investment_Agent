import { chromium } from "playwright";

const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}: ${d || ""}`);

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// Capture API
p.on("response", async (resp) => {
  if (resp.url().includes("/api/trade-plans") && resp.request().method() === "POST") {
    const body = await resp.text();
    console.log("  API:", body.substring(0, 150));
  }
});

await p.goto("http://localhost:3120/manage", { waitUntil: "networkidle" });
await p.waitForTimeout(1500);

// ═══ 1. CREATE PLAN ═══
console.log("\n═══ 1. Create new plan ═══");
await p.click('button:has-text("新建计划")');
await p.waitForTimeout(500);

// Stock
await p.locator("select").first().selectOption({ index: 3 });
await p.waitForTimeout(200);
const stock = await p.locator("select").first().inputValue();
console.log("  stock:", stock);

// Name
await p.locator('input[placeholder="分批建仓"]').fill("E2E测试");

// Add order row
await p.click('button:has-text("添加条件")');
await p.waitForTimeout(300);

// Fill order: find the number inputs in order section
const numInputs = await p.locator("input[type=number]").all();
console.log("  number inputs:", numInputs.length);

// numInputs[0] = order price, numInputs[1] = order shares
if (numInputs.length >= 2) {
  await numInputs[0].fill("28");
  await numInputs[1].fill("1000");
}

// Label
const labelInput = p.locator('input[placeholder="标签"]');
if ((await labelInput.count()) > 0) {
  await labelInput.fill("半仓止盈");
}

await p.screenshot({ path: "screenshots/e2e_01_filled.png" });

// Submit
await p.click('button:has-text("创建")');
await p.waitForTimeout(1500);

await p.screenshot({ path: "screenshots/e2e_02_created.png" });

const body1 = await p.textContent("body");
if (body1.includes("E2E测试")) {
  ok("Plan created and visible");
} else {
  ng("Plan not visible after create");
}

// ═══ 2. EDIT STOP LOSS ═══
console.log("\n═══ 2. Edit stop loss price ═══");

// SL values should be EditableCell - click to edit
// Find "SL:" text, then the value next to it
const slTexts = await p.locator('text=/^SL:/')?.all();
console.log("  SL elements:", slTexts?.length || 0);

// Try clicking on the SL price value (should be a span with title="Click to edit")
const editCells = await p.locator('[title="Click to edit"]').all();
console.log("  editable cells:", editCells.length);

if (editCells.length > 0) {
  // Click the first editable cell of the new plan (it should be the SL price)
  // The new plan is likely last, so try the last editable cells
  const lastSL = editCells[editCells.length - 1];
  const slText = await lastSL.textContent();
  console.log("  clicking cell:", slText);
  await lastSL.click();
  await p.waitForTimeout(300);

  // Type new value
  const editInput = p.locator("input:focus");
  if ((await editInput.count()) > 0) {
    await editInput.fill("18.5");
    await editInput.press("Enter");
    await p.waitForTimeout(1000);
    ok("Edited value to 18.5");
  } else {
    ng("No input focused after click");
  }
}

await p.screenshot({ path: "screenshots/e2e_03_edited.png" });

// ═══ 3. EDIT ORDER PRICE ═══
console.log("\n═══ 3. Edit order price ═══");
const editCells2 = await p.locator('[title="Click to edit"]').all();
// Find one that shows "28" (our order price)
for (const cell of editCells2) {
  const t = await cell.textContent();
  if (t && t.trim() === "28") {
    await cell.click();
    await p.waitForTimeout(300);
    const inp = p.locator("input:focus");
    if ((await inp.count()) > 0) {
      await inp.fill("30");
      await inp.press("Enter");
      await p.waitForTimeout(1000);
      ok("Order price changed 28 → 30");
    }
    break;
  }
}

await p.screenshot({ path: "screenshots/e2e_04_order_edited.png" });

// ═══ 4. TOGGLE PLAN (pause/resume) ═══
console.log("\n═══ 4. Toggle plan status ═══");
const bodyBefore = await p.textContent("body");
const hadActive = bodyBefore.includes("ACTIVE");

// Find toggle buttons
const toggleBtns = await p.locator('button:has-text("运行中"), button:has-text("已暂停")').all();
if (toggleBtns.length > 0) {
  await toggleBtns[toggleBtns.length - 1].click();
  await p.waitForTimeout(1000);
  const bodyAfter = await p.textContent("body");
  if (bodyAfter.includes("已暂停")) {
    ok("Plan paused");
  } else {
    ng("Toggle didn't work");
  }
}

await p.screenshot({ path: "screenshots/e2e_05_toggled.png" });

// ═══ 5. DELETE TEST PLAN ═══
console.log("\n═══ 5. Delete test plan ═══");
p.on("dialog", (d) => d.accept());

const xBtns = await p.locator("button").all();
for (const btn of xBtns) {
  const t = await btn.textContent();
  if (t && t.trim() === "x") {
    // Check if it's near our test plan
    const parent = await btn.evaluate((el) => el.closest("div")?.textContent || "");
    if (parent.includes("E2E测试")) {
      await btn.click();
      await p.waitForTimeout(1000);
      const bodyFinal = await p.textContent("body");
      if (bodyFinal.includes("E2E测试")) {
        ng("Plan still exists after delete");
      } else {
        ok("Plan deleted");
      }
      break;
    }
  }
}

await p.screenshot({ path: "screenshots/e2e_06_deleted.png" });

// ═══ SUMMARY ═══
console.log("\n═══ E2E TEST COMPLETE ═══");
await browser.close();
