/**
 * perf_audit.mjs — Playwright-based performance audit for the dashboard.
 *
 * Measures: Navigation timing, Core Web Vitals (LCP/CLS/TBT),
 * resource counts, API response times, tab switch latency,
 * React render counts, and memory usage.
 *
 * Outputs JSON report to screenshots/perf_report.json
 *
 * Usage: node web/screenshots/perf_audit.mjs
 */
import { chromium } from "playwright";
import { writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const BASE = "http://localhost:3120";
const DIR = dirname(fileURLToPath(import.meta.url));
const REPORT_PATH = join(DIR, "perf_report.json");

const report = {
  timestamp: new Date().toISOString(),
  pages: {},
  tabSwitches: {},
  apiTiming: {},
  summary: {},
};

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });

// ── Helper: measure page load ──
async function measurePageLoad(name, url) {
  const page = await context.newPage();

  // Track API calls
  const apiCalls = [];
  page.on("response", (res) => {
    if (res.url().includes("/api/")) {
      apiCalls.push({
        url: res.url().replace(BASE, ""),
        status: res.status(),
        timing: res.timing(),
      });
    }
  });

  // Navigate
  const start = Date.now();
  await page.goto(url, { waitUntil: "commit", timeout: 30000 });

  // Wait for meaningful content
  await page.waitForTimeout(5000);
  const loadTime = Date.now() - start;

  // Collect Navigation Timing
  const navTiming = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    if (!nav) return null;
    return {
      dns: Math.round(nav.domainLookupEnd - nav.domainLookupStart),
      tcp: Math.round(nav.connectEnd - nav.connectStart),
      ttfb: Math.round(nav.responseStart - nav.requestStart),
      domContentLoaded: Math.round(nav.domContentLoadedEventEnd - nav.startTime),
      domComplete: Math.round(nav.domComplete - nav.startTime),
      loadEvent: Math.round(nav.loadEventEnd - nav.startTime),
      transferSize: nav.transferSize,
    };
  });

  // Collect resource stats
  const resources = await page.evaluate(() => {
    const entries = performance.getEntriesByType("resource");
    const byType = {};
    for (const e of entries) {
      const ext = e.name.split("?")[0].split(".").pop() || "other";
      const type = e.initiatorType || ext;
      if (!byType[type]) byType[type] = { count: 0, totalSize: 0, totalDuration: 0 };
      byType[type].count++;
      byType[type].totalSize += e.transferSize || 0;
      byType[type].totalDuration += e.duration || 0;
    }
    return {
      total: entries.length,
      byType,
    };
  });

  // LCP (Largest Contentful Paint)
  const lcp = await page.evaluate(() => {
    return new Promise((resolve) => {
      let lastLcp = 0;
      const obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) lastLcp = e.startTime;
      });
      obs.observe({ type: "largest-contentful-paint", buffered: true });
      setTimeout(() => { obs.disconnect(); resolve(Math.round(lastLcp)); }, 100);
    });
  });

  // CLS (Cumulative Layout Shift)
  const cls = await page.evaluate(() => {
    return new Promise((resolve) => {
      let total = 0;
      const obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) if (!e.hadRecentInput) total += e.value;
      });
      obs.observe({ type: "layout-shift", buffered: true });
      setTimeout(() => { obs.disconnect(); resolve(parseFloat(total.toFixed(4))); }, 100);
    });
  });

  // Long tasks (> 50ms)
  const longTasks = await page.evaluate(() => {
    return new Promise((resolve) => {
      const tasks = [];
      const obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) tasks.push(Math.round(e.duration));
      });
      obs.observe({ type: "longtask", buffered: true });
      setTimeout(() => { obs.disconnect(); resolve(tasks); }, 100);
    });
  });

  // Memory (if available)
  const memory = await page.evaluate(() => {
    if (performance.memory) {
      return {
        usedJSHeapSize: Math.round(performance.memory.usedJSHeapSize / 1024 / 1024 * 10) / 10,
        totalJSHeapSize: Math.round(performance.memory.totalJSHeapSize / 1024 / 1024 * 10) / 10,
      };
    }
    return null;
  });

  // DOM node count
  const domNodes = await page.evaluate(() => document.querySelectorAll("*").length);

  const result = {
    loadTime,
    navTiming,
    resources,
    lcp,
    cls,
    longTasks,
    longTaskCount: longTasks.length,
    tbt: longTasks.reduce((sum, d) => sum + (d - 50), 0), // Total Blocking Time
    memory,
    domNodes,
    apiCalls: apiCalls.map((c) => ({
      url: c.url,
      status: c.status,
    })),
    apiCallCount: apiCalls.length,
  };

  await page.close();
  return result;
}

// ── Helper: measure tab switch with existing page ──
async function measureTabSwitches(page) {
  const results = {};
  const routes = [
    { name: "Holdings→Watching", tab: 'a[href="/watching"]', waitFor: "svc-monitor --watching" },
    { name: "Watching→Alerts", tab: 'a[href="/alerts"]', waitFor: "event log" },
    { name: "Alerts→Holdings", tab: 'a[href="/"]', waitFor: "refresh #" },
    { name: "Holdings→Alerts", tab: 'a[href="/alerts"]', waitFor: "event log" },
    { name: "Alerts→Watching", tab: 'a[href="/watching"]', waitFor: "svc-monitor --watching" },
    { name: "Watching→Holdings", tab: 'a[href="/"]', waitFor: "refresh #" },
  ];

  for (const r of routes) {
    const metricsReqs = [];
    const handler = (req) => { if (req.url().includes("/api/metrics")) metricsReqs.push(1); };
    page.on("request", handler);

    const t0 = Date.now();
    await page.locator(r.tab).first().click({ force: true });
    await page.waitForSelector(`text=${r.waitFor}`, { timeout: 10000 }).catch(() => {});
    const elapsed = Date.now() - t0;

    // Check loading flash
    const loadingVisible = await page.locator('text=Loading').isVisible().catch(() => false);

    page.removeListener("request", handler);

    results[r.name] = {
      ms: elapsed,
      extraApiCalls: metricsReqs.length,
      loadingFlash: loadingVisible,
    };
  }

  return results;
}

// ── Helper: measure API response times ──
async function measureApiTiming(page) {
  const apis = ["/api/metrics", "/api/sim", "/api/sector", "/api/summary"];
  const results = {};

  for (const api of apis) {
    const times = [];
    for (let i = 0; i < 3; i++) {
      const t0 = Date.now();
      try {
        const resp = await page.evaluate(async (url) => {
          const r = await fetch(url, { cache: "no-store" });
          const data = await r.json();
          return { ok: r.ok, size: JSON.stringify(data).length };
        }, api);
        times.push({ ms: Date.now() - t0, size: resp.size, ok: resp.ok });
      } catch {
        times.push({ ms: Date.now() - t0, error: true });
      }
    }

    const avgMs = Math.round(times.reduce((s, t) => s + t.ms, 0) / times.length);
    const minMs = Math.min(...times.map((t) => t.ms));
    const maxMs = Math.max(...times.map((t) => t.ms));

    results[api] = { avgMs, minMs, maxMs, samples: times };
  }

  return results;
}

// ── Helper: count poll requests over duration ──
async function measurePollRate(page, durationSec) {
  const reqs = [];
  const handler = (req) => {
    if (req.url().includes("/api/metrics")) reqs.push(Date.now());
  };
  page.on("request", handler);

  await page.waitForTimeout(durationSec * 1000);

  page.removeListener("request", handler);
  return {
    duration: durationSec,
    count: reqs.length,
    ratePerMin: parseFloat((reqs.length / durationSec * 60).toFixed(1)),
    intervals: reqs.slice(1).map((t, i) => t - reqs[i]),
  };
}

// ═══ Run audit ═══
console.log("Performance Audit");
console.log("═".repeat(60));

// 1. Page load times
console.log("\n[1/5] Measuring page loads...");
for (const [name, path] of [["holdings", "/"], ["watching", "/watching"], ["alerts", "/alerts"], ["sim", "/sim"], ["sector", "/sector"]]) {
  process.stdout.write(`  ${name}... `);
  report.pages[name] = await measurePageLoad(name, `${BASE}${path}`);
  console.log(`${report.pages[name].loadTime}ms (LCP:${report.pages[name].lcp}ms, CLS:${report.pages[name].cls}, DOM:${report.pages[name].domNodes})`);
}

// 2. Tab switch performance (need a warm page)
console.log("\n[2/5] Measuring tab switches...");
const switchPage = await context.newPage();
await switchPage.goto(BASE, { waitUntil: "commit", timeout: 15000 });
await switchPage.waitForSelector("text=refresh #", { timeout: 15000 }).catch(() => {});

// Pre-warm all routes
for (const href of ["/watching", "/alerts", "/"]) {
  await switchPage.locator(`a[href="${href}"]`).first().click({ force: true });
  await switchPage.waitForTimeout(3000);
}
await switchPage.locator('a[href="/"]').first().click({ force: true });
await switchPage.waitForSelector("text=refresh #", { timeout: 10000 }).catch(() => {});
await switchPage.waitForTimeout(2000);

report.tabSwitches = await measureTabSwitches(switchPage);
for (const [name, r] of Object.entries(report.tabSwitches)) {
  const status = r.ms < 500 ? "fast" : r.ms < 1000 ? "ok" : "SLOW";
  console.log(`  ${name}: ${r.ms}ms [${status}] api:${r.extraApiCalls} flash:${r.loadingFlash}`);
}

// 3. API response times
console.log("\n[3/5] Measuring API response times...");
report.apiTiming = await measureApiTiming(switchPage);
for (const [api, r] of Object.entries(report.apiTiming)) {
  console.log(`  ${api}: avg=${r.avgMs}ms min=${r.minMs}ms max=${r.maxMs}ms`);
}

// 4. Poll rate
console.log("\n[4/5] Measuring poll rate (32s)...");
report.pollRate = await measurePollRate(switchPage, 32);
console.log(`  ${report.pollRate.count} requests in ${report.pollRate.duration}s = ${report.pollRate.ratePerMin}/min`);
if (report.pollRate.intervals.length > 0) {
  const avgInterval = Math.round(report.pollRate.intervals.reduce((s, v) => s + v, 0) / report.pollRate.intervals.length / 1000);
  console.log(`  avg interval: ${avgInterval}s`);
}

await switchPage.close();

// 5. Summary / scoring
console.log("\n[5/5] Computing summary...");

const avgSwitchMs = Object.values(report.tabSwitches).reduce((s, r) => s + r.ms, 0) / Object.keys(report.tabSwitches).length;
const anyLoadingFlash = Object.values(report.tabSwitches).some((r) => r.loadingFlash);
const extraApiOnSwitch = Object.values(report.tabSwitches).reduce((s, r) => s + r.extraApiCalls, 0);
const holdingsLoad = report.pages.holdings?.loadTime || 0;
const metricsAvg = report.apiTiming["/api/metrics"]?.avgMs || 0;
const pollPerMin = report.pollRate?.ratePerMin || 0;

report.summary = {
  avgTabSwitchMs: Math.round(avgSwitchMs),
  anyLoadingFlash,
  extraApiCallsOnSwitch: extraApiOnSwitch,
  holdingsLoadMs: holdingsLoad,
  metricsApiAvgMs: metricsAvg,
  pollRatePerMin: pollPerMin,
  grades: {
    tabSwitch: avgSwitchMs < 300 ? "A" : avgSwitchMs < 500 ? "B" : avgSwitchMs < 1000 ? "C" : "F",
    loadingFlash: anyLoadingFlash ? "F" : "A",
    pollDedup: pollPerMin <= 2.5 ? "A" : pollPerMin <= 4 ? "B" : "F",
    apiSpeed: metricsAvg < 50 ? "A" : metricsAvg < 100 ? "B" : metricsAvg < 200 ? "C" : "F",
  },
};

// Write report
writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2));

// Print summary
console.log("\n" + "═".repeat(60));
console.log("PERFORMANCE SUMMARY");
console.log("═".repeat(60));
console.log(`  Tab switch avg:     ${report.summary.avgTabSwitchMs}ms  [${report.summary.grades.tabSwitch}]`);
console.log(`  Loading flash:      ${anyLoadingFlash ? "YES" : "none"}  [${report.summary.grades.loadingFlash}]`);
console.log(`  Extra API on switch: ${extraApiOnSwitch}  [${extraApiOnSwitch === 0 ? "A" : "F"}]`);
console.log(`  /api/metrics avg:   ${metricsAvg}ms  [${report.summary.grades.apiSpeed}]`);
console.log(`  Poll rate:          ${pollPerMin}/min  [${report.summary.grades.pollDedup}]`);
console.log(`  Holdings load:      ${holdingsLoad}ms`);
console.log(`\n  Report saved to: ${REPORT_PATH}`);
console.log("═".repeat(60));

await browser.close();
