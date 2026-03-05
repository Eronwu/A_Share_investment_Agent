import { chromium } from "playwright";

const BASE = "http://localhost:3120";
let pass = 0, fail = 0;

function ok(name, detail = "") {
  pass++;
  console.log(`  [PASS] ${name}${detail ? " -- " + detail : ""}`);
}
function ng(name, detail = "") {
  fail++;
  console.log(`  [FAIL] ${name}${detail ? " -- " + detail : ""}`);
}

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await context.newPage();

async function go(url) {
  await page.goto(url, { waitUntil: "commit", timeout: 15000 });
}

async function waitData(text, ms = 15000) {
  await page.waitForSelector(`text=${text}`, { timeout: ms }).catch(() => {});
}

// ═══ 0. Pre-warm all route chunks (dev mode compiles on first client-side visit) ═══
console.log("\n═══ 0. Pre-warming route chunks ═══");
// Full-page navigations compile server-side modules
await go(BASE);
await waitData("refresh #");
// Client-side clicks compile client-side modules
const wTab = page.locator('a[href="/watching"]').first();
const aTab = page.locator('a[href="/alerts"]').first();
const hTab = page.locator('a[href="/"]').first();

await wTab.click({ force: true });
await waitData("svc-monitor --watching");
await aTab.click({ force: true });
await waitData("event log");
await hTab.click({ force: true });
await waitData("refresh #");
console.log("  All chunks compiled");

// Wait for provider data to settle
await page.waitForTimeout(3000);

// ═══ 1. Single poll stream ═══
console.log("\n═══ 1. Single poll stream (35s) ═══");

const metricsRequests = [];
page.on("request", (req) => {
  if (req.url().includes("/api/metrics")) {
    metricsRequests.push(Date.now());
  }
});

metricsRequests.length = 0;
const pollStart = Date.now();
await page.waitForTimeout(35000);
const pollSec = (Date.now() - pollStart) / 1000;
const pollCount = metricsRequests.length;
console.log(`  ${pollCount} requests in ${pollSec.toFixed(1)}s (expect ~1 per 30s)`);

if (pollCount <= 2) ok("Single poll stream", `${pollCount} in ${pollSec.toFixed(0)}s`);
else ng("Single poll stream", `${pollCount} requests (expected ≤2)`);

// ═══ 2. Client-side tab switch (post-warm) ═══
console.log("\n═══ 2. Client-side tab switch (chunks pre-compiled) ═══");

// Verify data is loaded before starting switch test
const hasData = await page.locator('text=refresh #').isVisible().catch(() => false);
console.log(`  Data loaded before test: ${hasData}`);

metricsRequests.length = 0;

// Holdings → Watching
const t1 = Date.now();
await wTab.click({ force: true });
await waitData("svc-monitor --watching");
const sw1 = Date.now() - t1;
console.log(`  Holdings→Watching: ${sw1}ms`);
if (sw1 < 1000) ok("Holdings→Watching < 1s", `${sw1}ms`);
else if (sw1 < 3000) ok("Holdings→Watching < 3s (dev mode)", `${sw1}ms`);
else ng("Holdings→Watching too slow", `${sw1}ms`);

// Watching → Alerts
const t2 = Date.now();
await aTab.click({ force: true });
await waitData("event log");
const sw2 = Date.now() - t2;
console.log(`  Watching→Alerts: ${sw2}ms`);
if (sw2 < 1000) ok("Watching→Alerts < 1s", `${sw2}ms`);
else if (sw2 < 3000) ok("Watching→Alerts < 3s (dev mode)", `${sw2}ms`);
else ng("Watching→Alerts too slow", `${sw2}ms`);

// Alerts → Holdings
const t3 = Date.now();
await hTab.click({ force: true });
await waitData("refresh #");
const sw3 = Date.now() - t3;
console.log(`  Alerts→Holdings: ${sw3}ms`);
if (sw3 < 1000) ok("Alerts→Holdings < 1s", `${sw3}ms`);
else if (sw3 < 3000) ok("Alerts→Holdings < 3s (dev mode)", `${sw3}ms`);
else ng("Alerts→Holdings too slow", `${sw3}ms`);

// Zero extra requests during switches
const switchReqs = metricsRequests.length;
console.log(`  Extra /api/metrics during 3 switches: ${switchReqs}`);
if (switchReqs <= 1) ok("No extra requests on tab switch", `${switchReqs}`);
else ng("Extra requests on tab switch", `${switchReqs}`);

// ═══ 3. No loading flash ═══
console.log("\n═══ 3. Loading flash check ═══");

// Data is loaded, check that switching doesn't show loading
await wTab.click({ force: true });
await page.waitForTimeout(100); // allow React to render
const loadW = await page.locator('text=Loading watching').isVisible().catch(() => false);
if (!loadW) ok("No loading flash → Watching");
else ng("Loading flash → Watching");

await aTab.click({ force: true });
await page.waitForTimeout(100);
const loadA = await page.locator('text=Loading...').isVisible().catch(() => false);
if (!loadA) ok("No loading flash → Alerts");
else ng("Loading flash → Alerts");

await hTab.click({ force: true });
await page.waitForTimeout(100);
const loadH = await page.locator('text=Loading metrics').isVisible().catch(() => false);
if (!loadH) ok("No loading flash → Holdings");
else ng("Loading flash → Holdings");

// ═══ 4. Shared state ═══
console.log("\n═══ 4. Shared state check ═══");

await page.waitForTimeout(500);
const body1 = await page.textContent("body") || "";
const tick1 = body1.match(/refresh #(\d+)/)?.[1];

await wTab.click({ force: true });
await waitData("refresh #");
const body2 = await page.textContent("body") || "";
const tick2 = body2.match(/refresh #(\d+)/)?.[1];

console.log(`  Holdings tick: ${tick1}, Watching tick: ${tick2}`);
if (tick1 && tick2 && Math.abs(parseInt(tick1) - parseInt(tick2)) <= 1) {
  ok("Shared tick consistent", `${tick1} ≈ ${tick2}`);
} else if (tick1 && tick2) {
  ng("Shared tick consistent", `${tick1} vs ${tick2}`);
} else {
  ng("Could not read tick", `h=${tick1} w=${tick2}`);
}

// ═══ Summary ═══
await browser.close();

console.log(`\n${"═".repeat(60)}`);
console.log(`PERF TEST: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
console.log(`${"═".repeat(60)}`);

process.exit(fail > 0 ? 1 : 0);
