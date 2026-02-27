import { chromium } from "playwright";

const BASE = "http://localhost:3120";
let pass = 0, fail = 0;

function ok(label, detail = "") {
  pass++;
  console.log(`  [PASS] ${label}${detail ? " -- " + detail : ""}`);
}
function ng(label, detail = "") {
  fail++;
  console.log(`  [FAIL] ${label}${detail ? " -- " + detail : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// ── 1. Manage page loads ──
console.log("\n═══ 1. Trade Plans Section ═══");
await page.goto(`${BASE}/manage`, { waitUntil: "networkidle" });

// Check trade plans section header exists
const pageText = await page.evaluate(() => document.body.innerText);

if (pageText.includes("交易计划")) {
  ok("Trade plans section header present");
} else {
  ng("Trade plans section header present", "missing '交易计划'");
}

if (pageText.includes("active")) {
  ok("Active plan count badge");
} else {
  ng("Active plan count badge");
}

// ── 2. PlanCard rendering ──
console.log("\n═══ 2. PlanCard Content ═══");

if (pageText.includes("极智嘉分批建仓")) {
  ok("Plan name rendered");
} else {
  ng("Plan name rendered", "missing '极智嘉分批建仓'");
}

if (pageText.includes("HK02590")) {
  ok("Plan symbol rendered");
} else {
  ng("Plan symbol rendered", "missing HK02590");
}

if (pageText.includes("止损") && pageText.includes("22.00")) {
  ok("Stop loss displayed", "22.00");
} else {
  ng("Stop loss displayed");
}

// Check entry conditions
if (pageText.includes("e1:") && pageText.includes("放量")) {
  ok("Entry condition e1 rendered");
} else {
  ng("Entry condition e1 rendered");
}

if (pageText.includes("e2:") && pageText.includes("站稳26")) {
  ok("Entry condition e2 rendered");
} else {
  ng("Entry condition e2 rendered");
}

// Check exit conditions
if (pageText.includes("tp1:") && pageText.includes("28.00")) {
  ok("Exit condition tp1 rendered", "28.00");
} else {
  ng("Exit condition tp1 rendered");
}

if (pageText.includes("tp2:") && pageText.includes("31.00")) {
  ok("Exit condition tp2 rendered", "31.00");
} else {
  ng("Exit condition tp2 rendered");
}

if (pageText.includes("tp3:") && pageText.includes("35.00")) {
  ok("Exit condition tp3 rendered", "35.00");
} else {
  ng("Exit condition tp3 rendered");
}

// Check shares in entries
if (pageText.includes("1000股")) {
  ok("Entry shares rendered", "1000股");
} else {
  ng("Entry shares rendered");
}

// Check sell percentages
if (pageText.includes("33%")) {
  ok("Exit sell percentage rendered", "33%");
} else {
  ng("Exit sell percentage rendered");
}

if (pageText.includes("100%")) {
  ok("Exit 100% (清仓) rendered");
} else {
  ng("Exit 100% (清仓) rendered");
}

// ── 3. Action buttons ──
console.log("\n═══ 3. Action Buttons ═══");

if (pageText.includes("暂停")) {
  ok("Toggle button (暂停) present");
} else {
  ng("Toggle button (暂停) present");
}

if (pageText.includes("删除")) {
  ok("Delete button (删除) present");
} else {
  ng("Delete button (删除) present");
}

// ── 4. AddPlanForm Modal ──
console.log("\n═══ 4. AddPlanForm Modal ═══");

// Check "+ 新建" button exists
if (pageText.includes("+ 新建")) {
  ok("'+ 新建' button present");
} else {
  ng("'+ 新建' button present");
}

// Click "+ 新建" to open modal
const modalOpened = await page.evaluate(() => {
  const btns = [...document.querySelectorAll("button")];
  const btn = btns.find(b => b.textContent?.includes("+ 新建"));
  if (btn) { btn.click(); return true; }
  return false;
});

if (modalOpened) {
  ok("Modal opens on click");
  await page.waitForTimeout(300);

  const modalText = await page.evaluate(() => document.body.innerText);

  if (modalText.includes("# 新建交易计划")) {
    ok("Modal title present");
  } else {
    ng("Modal title present");
  }

  if (modalText.includes("股票:") || modalText.includes("股票")) {
    ok("Stock dropdown label present");
  } else {
    ng("Stock dropdown label present");
  }

  if (modalText.includes("计划名:") || modalText.includes("计划名")) {
    ok("Plan name input label present");
  } else {
    ng("Plan name input label present");
  }

  if (modalText.includes("加仓条件")) {
    ok("Entry conditions section present");
  } else {
    ng("Entry conditions section present");
  }

  if (modalText.includes("止盈条件")) {
    ok("Exit conditions section present");
  } else {
    ng("Exit conditions section present");
  }

  if (modalText.includes("创建")) {
    ok("Create button present");
  } else {
    ng("Create button present");
  }

  // Check stock dropdown has options
  const stockCount = await page.evaluate(() => {
    const selects = [...document.querySelectorAll("select")];
    for (const sel of selects) {
      if (sel.options.length > 1) return sel.options.length - 1;
    }
    return 0;
  });
  if (stockCount > 0) {
    ok("Stock dropdown populated", `${stockCount} stocks`);
  } else {
    ng("Stock dropdown populated");
  }

  // Screenshot modal
  await page.screenshot({ path: "screenshots/trade_plans_modal.png" });
  ok("Modal screenshot saved");

  // Close modal via × button
  const closed = await page.evaluate(() => {
    const btns = [...document.querySelectorAll("button")];
    const closeBtn = btns.find(b => b.textContent?.trim() === "×");
    if (closeBtn) { closeBtn.click(); return true; }
    return false;
  });
  await page.waitForTimeout(200);
  if (closed) {
    const stillOpen = await page.evaluate(() => document.body.innerText.includes("# 新建交易计划"));
    if (!stillOpen) {
      ok("Modal closes on × click");
    } else {
      ng("Modal closes on × click");
    }
  }
} else {
  ng("Modal opens on click");
}

// ── 5. API health ──
console.log("\n═══ 5. Trade Plans API ═══");

const apiResp = await page.evaluate(async () => {
  const r = await fetch("/api/trade-plans");
  return r.json();
});

if (apiResp.plans && Array.isArray(apiResp.plans)) {
  ok("GET /api/trade-plans returns plans array", `${apiResp.plans.length} plans`);
} else {
  ng("GET /api/trade-plans returns plans array");
}

if (apiResp.stocks && Array.isArray(apiResp.stocks)) {
  ok("GET /api/trade-plans returns stocks array", `${apiResp.stocks.length} stocks`);
} else {
  ng("GET /api/trade-plans returns stocks array");
}

if (apiResp.plans?.length > 0) {
  const p = apiResp.plans[0];
  if (p.id && p.name && p.symbol && p.status && p.entries && p.exits) {
    ok("Plan structure valid", `${p.name} (${p.symbol})`);
  } else {
    ng("Plan structure valid", JSON.stringify(p).slice(0, 100));
  }

  if (p.stop_loss && typeof p.stop_loss.price === "number") {
    ok("Stop loss structure valid", `price=${p.stop_loss.price}`);
  } else {
    ng("Stop loss structure valid");
  }
}

// ── 6. Screenshot ──
console.log("\n═══ 6. Screenshots ═══");

// Scroll to trade plans section and screenshot
await page.goto(`${BASE}/manage`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);

// Take full page screenshot
await page.screenshot({ path: "screenshots/trade_plans_full.png", fullPage: true });
ok("Full manage page screenshot saved");

// Scroll to the trade plans area and zoom in
await page.evaluate(() => {
  const spans = [...document.querySelectorAll("span")];
  const header = spans.find(s => s.textContent?.includes("交易计划"));
  if (header) header.scrollIntoView({ block: "start" });
});
await page.waitForTimeout(200);
await page.screenshot({ path: "screenshots/trade_plans_section.png" });
ok("Trade plans section screenshot saved");

ok("Screenshots complete");

await browser.close();

console.log(`\n${"═".repeat(60)}`);
console.log(`TRADE PLANS TEST: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) {
  console.log("\nFailed tests need attention.");
}
console.log("═".repeat(60) + "\n");

process.exit(fail > 0 ? 1 : 0);
