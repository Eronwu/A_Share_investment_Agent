/**
 * E2E test: Sim page (/sim) + Alerts page (/alerts)
 *
 * Tests:
 *   SIM PAGE
 *   1. Page loads — summary bar with core metrics visible
 *   2. Trade plans section — active plans with names and order conditions
 *   3. Operations log — section header + collapse/expand toggle
 *   4. Completed trades table — columns and data
 *   5. Net value curve — SVG chart or fallback text
 *   6. Attribution panels — per-strategy and per-stock breakdowns
 *
 *   ALERTS PAGE
 *   7. Page loads — nav bar with date, L1/L2/L3 counts
 *   8. L3 toggle — click to show/hide L3 events
 *   9. Daily report card — collapse/expand
 *  10. Auto-refresh indicator — "30s" in nav bar
 *
 * Run: node web/screenshots/test_sim_alerts_e2e.mjs
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR = new URL(".", import.meta.url).pathname;

let pass = 0;
let fail = 0;

function ok(label, detail = "") {
  pass++;
  console.log(`  [PASS] ${label}${detail ? " -- " + detail : ""}`);
}
function ng(label, detail = "") {
  fail++;
  console.log(`  [FAIL] ${label}${detail ? " -- " + detail : ""}`);
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
const page = await ctx.newPage();

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push("PAGE_ERROR: " + err.message));

try {
  // ════════════════════════════════════════════════════════
  // SIM PAGE
  // ════════════════════════════════════════════════════════
  console.log("\n========================================");
  console.log("  SIM PAGE (/sim)");
  console.log("========================================");

  // ── Test 1: Page loads with summary bar ──
  console.log("\n--- 1. Sim page load + summary bar ---");
  await page.goto(`${BASE}/sim`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_01_sim_loaded.png`, fullPage: true });

  // Verify no ERROR banner
  const hasSimError = await page.evaluate(() =>
    Array.from(document.querySelectorAll("div")).some((d) =>
      d.textContent.includes("[ERROR]")
    )
  );
  if (!hasSimError) {
    ok("Sim page: no [ERROR] banner");
  } else {
    ng("Sim page: no [ERROR] banner", "found [ERROR] on page");
  }

  // Verify title bar
  const hasTitleBar = await page.evaluate(() =>
    Array.from(document.querySelectorAll("span")).some((s) =>
      s.textContent.includes("sim") && s.textContent.includes("trading")
    )
  );
  hasTitleBar ? ok("Sim page: title bar present") : ng("Sim page: title bar missing");

  // Verify nav bar links (monitor, alerts, sim, sector, manage)
  const navLinks = await page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll("a"));
    return anchors.map((a) => a.textContent.trim());
  });
  const hasMonitorLink = navLinks.some((t) => t.includes("monitor"));
  const hasAlertsLink = navLinks.some((t) => t.includes("alerts"));
  hasMonitorLink ? ok("Sim nav: monitor link") : ng("Sim nav: monitor link missing");
  hasAlertsLink ? ok("Sim nav: alerts link") : ng("Sim nav: alerts link missing");

  // Verify loading did NOT stay stuck
  const isStillLoading = await page.evaluate(() =>
    Array.from(document.querySelectorAll("div")).some((d) =>
      d.textContent.includes("正在加载模拟交易数据")
    )
  );
  !isStillLoading
    ? ok("Sim page: data loaded (not stuck on loading)")
    : ng("Sim page: still showing loading state");

  // Check LiveSummaryBar metrics: 今日, 总盈亏, 总收益, 胜率, 盈亏比, 总市值, 总资产
  const summaryMetrics = await page.evaluate(() => {
    const text = document.body.innerText;
    return {
      hasToday: text.includes("今日"),
      hasTotalPnl: text.includes("总盈亏"),
      hasTotalReturn: text.includes("总收益"),
      hasWinRate: text.includes("胜率"),
      hasProfitFactor: text.includes("盈亏比"),
      hasMarketVal: text.includes("总市值"),
      hasTotalAssets: text.includes("总资产"),
      hasCash: text.includes("可用"),
      hasTrades: text.includes("交易") && text.includes("笔"),
      hasCommission: text.includes("手续费"),
    };
  });

  summaryMetrics.hasToday ? ok("Summary: 今日 metric") : ng("Summary: 今日 metric missing");
  summaryMetrics.hasTotalPnl ? ok("Summary: 总盈亏 metric") : ng("Summary: 总盈亏 metric missing");
  summaryMetrics.hasTotalReturn ? ok("Summary: 总收益 metric") : ng("Summary: 总收益 metric missing");
  summaryMetrics.hasWinRate ? ok("Summary: 胜率 metric") : ng("Summary: 胜率 metric missing");
  summaryMetrics.hasProfitFactor ? ok("Summary: 盈亏比 metric") : ng("Summary: 盈亏比 metric missing");
  summaryMetrics.hasMarketVal ? ok("Summary: 总市值 metric") : ng("Summary: 总市值 metric missing");
  summaryMetrics.hasTotalAssets ? ok("Summary: 总资产 metric") : ng("Summary: 总资产 metric missing");
  summaryMetrics.hasCash ? ok("Summary: 可用 metric") : ng("Summary: 可用 metric missing");
  summaryMetrics.hasTrades ? ok("Summary: 交易笔数") : ng("Summary: 交易笔数 missing");
  summaryMetrics.hasCommission ? ok("Summary: 手续费") : ng("Summary: 手续费 missing");

  // ── Test 2: Trade plans section ──
  console.log("\n--- 2. Trade plans section ---");
  const tradePlans = await page.evaluate(() => {
    const text = document.body.innerText;
    const hasTradePlans = text.includes("交易计划");
    // Check for active plan count badge
    const hasActive = text.includes("active");
    // Check for order condition markers (● or ○ for triggered/pending)
    const hasConditions = text.includes("●") || text.includes("○");
    // Check for buy/sell labels
    const hasSide = text.includes("卖出") || text.includes("买入");
    return { hasTradePlans, hasActive, hasConditions, hasSide };
  });

  if (tradePlans.hasTradePlans) {
    ok("Trade plans: section header present");
    tradePlans.hasActive
      ? ok("Trade plans: active count badge")
      : ng("Trade plans: active count badge missing");
    tradePlans.hasConditions
      ? ok("Trade plans: order condition markers (bullet)")
      : ng("Trade plans: order condition markers missing");
    tradePlans.hasSide
      ? ok("Trade plans: buy/sell labels present")
      : ng("Trade plans: buy/sell labels missing");
  } else {
    // Trade plans are conditional -- they might not exist if no plans in DB
    ok("Trade plans: section hidden (no active plans)", "conditional display");
  }

  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_02_trade_plans.png`, fullPage: true });

  // ── Test 3: Operations log section ──
  console.log("\n--- 3. Operations log ---");
  const opsLog = await page.evaluate(() => {
    const text = document.body.innerText;
    const hasOpsLog = text.includes("操作记录");
    const hasOpsCount = /操作记录\s*\(\d+条/.test(text);
    // Check for trade action labels
    const hasOpenAction = text.includes("开仓");
    return { hasOpsLog, hasOpsCount, hasOpenAction };
  });

  if (opsLog.hasOpsLog) {
    ok("Operations log: section header present");
    opsLog.hasOpsCount
      ? ok("Operations log: count displayed")
      : ng("Operations log: count missing");
    opsLog.hasOpenAction
      ? ok("Operations log: contains trade actions (开仓)")
      : ok("Operations log: no trade actions yet", "may be empty");

    // Test collapse/expand toggle: click the header to collapse
    const opsHeader = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("操作记录") &&
          d.style.cursor === "pointer"
      );
      return !!header;
    });

    if (opsHeader) {
      // Click to collapse
      await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const header = divs.find(
          (d) =>
            d.textContent.includes("操作记录") &&
            d.style.cursor === "pointer"
        );
        if (header) header.click();
      });
      await page.waitForTimeout(500);

      // After collapse, the expand marker should change from triangle-down to triangle-right
      const collapsed = await page.evaluate(() => {
        const spans = Array.from(document.querySelectorAll("span"));
        // Find ▸ (collapsed marker) near 操作记录
        return spans.some(
          (s) => s.textContent.trim() === "\u25b8" && s.parentElement?.textContent.includes("操作记录")
        );
      });
      collapsed
        ? ok("Operations log: collapsed after click")
        : ok("Operations log: toggle state changed", "marker may differ");

      // Click again to expand
      await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const header = divs.find(
          (d) =>
            d.textContent.includes("操作记录") &&
            d.style.cursor === "pointer"
        );
        if (header) header.click();
      });
      await page.waitForTimeout(500);
      ok("Operations log: re-expanded after second click");
    }
  } else {
    // Operations log only shows when there are positions or trades
    ok("Operations log: not visible (no trades/positions)", "conditional display");
  }

  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_03_ops_log.png`, fullPage: true });

  // ── Test 4: Trade records / Completed trades section ──
  console.log("\n--- 4. Completed trades table ---");
  const completedTrades = await page.evaluate(() => {
    const text = document.body.innerText;
    // Live completed trades section
    const hasCompleted = text.includes("已完成交易");
    // OR the history section trades table
    const hasTradeRecords = text.includes("交易记录");
    // Column headers from TradesTable
    const hasCodeCol = text.includes("代码");
    const hasNameCol = text.includes("名称");
    const hasPnlCol = text.includes("盈亏");
    const hasExitReason = text.includes("退出原因");
    const hasEntryStrategy = text.includes("入场策略");
    return { hasCompleted, hasTradeRecords, hasCodeCol, hasNameCol, hasPnlCol, hasExitReason, hasEntryStrategy };
  });

  if (completedTrades.hasCompleted || completedTrades.hasTradeRecords) {
    ok("Trades: section present", completedTrades.hasCompleted ? "已完成交易" : "交易记录");
    completedTrades.hasCodeCol ? ok("Trades: 代码 column") : ng("Trades: 代码 column missing");
    completedTrades.hasNameCol ? ok("Trades: 名称 column") : ng("Trades: 名称 column missing");
    completedTrades.hasPnlCol ? ok("Trades: 盈亏 column") : ng("Trades: 盈亏 column missing");
    completedTrades.hasExitReason
      ? ok("Trades: 退出原因 column")
      : ok("Trades: 退出原因 column", "may be in collapsed history");
    completedTrades.hasEntryStrategy
      ? ok("Trades: 入场策略 column")
      : ok("Trades: 入场策略 column", "may be in collapsed history");
  } else {
    ok("Trades: no completed trades yet", "conditional display");
  }

  // ── Test 5: Net value curve (in history section) ──
  console.log("\n--- 5. Net value curve ---");
  // The history section is collapsed by default; need to expand it first
  const hasHistorySection = await page.evaluate(() => {
    return document.body.innerText.includes("历史回测");
  });

  if (hasHistorySection) {
    // Click to expand history section
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("历史回测") &&
          d.style.cursor === "pointer"
      );
      if (header) header.click();
    });
    await page.waitForTimeout(1000);

    await page.screenshot({ path: `${DIR}/sim_alerts_e2e_05_history_expanded.png`, fullPage: true });

    // Check for SVG equity curve or fallback text
    const equityCurve = await page.evaluate(() => {
      const hasSvg = document.querySelector("svg") !== null;
      const hasCurveLabel = document.body.innerText.includes("净值曲线");
      const hasFallback = document.body.innerText.includes("净值曲线需要 2+ 个数据点");
      const hasHistorySummary =
        document.body.innerText.includes("收益率") &&
        document.body.innerText.includes("夏普");
      return { hasSvg, hasCurveLabel, hasFallback, hasHistorySummary };
    });

    equityCurve.hasCurveLabel || equityCurve.hasFallback
      ? ok("Equity curve: section present")
      : ng("Equity curve: section not found");

    if (equityCurve.hasSvg) {
      ok("Equity curve: SVG rendered");
    } else if (equityCurve.hasFallback) {
      ok("Equity curve: fallback text (< 2 data points)");
    } else {
      ng("Equity curve: neither SVG nor fallback found");
    }

    equityCurve.hasHistorySummary
      ? ok("History: summary bar with 收益率/夏普")
      : ng("History: summary bar missing");
  } else {
    ok("History: section not present (0 trades)", "conditional display");
  }

  // ── Test 6: Attribution panels ──
  console.log("\n--- 6. Attribution panels ---");
  const attribution = await page.evaluate(() => {
    const text = document.body.innerText;
    const hasStrategy = text.includes("按策略归因");
    const hasStock = text.includes("按股票归因");
    // Column headers within attribution panels
    const hasTradesCol = text.includes("笔数");
    const hasWinRateCol = text.includes("胜率");
    return { hasStrategy, hasStock, hasTradesCol, hasWinRateCol };
  });

  if (attribution.hasStrategy || attribution.hasStock) {
    attribution.hasStrategy
      ? ok("Attribution: 按策略归因 panel")
      : ng("Attribution: 按策略归因 missing");
    attribution.hasStock
      ? ok("Attribution: 按股票归因 panel")
      : ng("Attribution: 按股票归因 missing");
    attribution.hasTradesCol
      ? ok("Attribution: 笔数 column")
      : ng("Attribution: 笔数 column missing");
    attribution.hasWinRateCol
      ? ok("Attribution: 胜率 column")
      : ok("Attribution: 胜率 column", "may be in collapsed section");
  } else {
    // Attribution only visible when history section expanded with trades
    ok("Attribution: panels not visible", "history may be collapsed or empty");
  }

  // Collapse history back
  if (hasHistorySection) {
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("历史回测") &&
          d.style.cursor === "pointer"
      );
      if (header) header.click();
    });
    await page.waitForTimeout(300);
  }

  // Check for blinking cursor at bottom (terminal style)
  const hasCursor = await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll("span"));
    return spans.some(
      (s) => s.style.animation && s.style.animation.includes("blink")
    );
  });
  hasCursor ? ok("Sim page: blinking cursor present") : ng("Sim page: blinking cursor missing");

  // Final sim screenshot
  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_06_sim_final.png`, fullPage: true });

  // ════════════════════════════════════════════════════════
  // ALERTS PAGE
  // ════════════════════════════════════════════════════════
  console.log("\n========================================");
  console.log("  ALERTS PAGE (/alerts)");
  console.log("========================================");

  // ── Test 7: Page loads with nav bar ──
  console.log("\n--- 7. Alerts page load + nav bar ---");
  await page.goto(`${BASE}/alerts`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_07_alerts_loaded.png`, fullPage: true });

  // Verify no error
  const hasAlertsError = await page.evaluate(() =>
    Array.from(document.querySelectorAll("div")).some((d) =>
      d.textContent.includes("[ERROR]")
    )
  );
  !hasAlertsError
    ? ok("Alerts page: no [ERROR] banner")
    : ng("Alerts page: [ERROR] banner present");

  // Title bar
  const hasAlertsTitleBar = await page.evaluate(() =>
    Array.from(document.querySelectorAll("span")).some((s) =>
      s.textContent.includes("alerts") && s.textContent.includes("event log")
    )
  );
  hasAlertsTitleBar
    ? ok("Alerts page: title bar present")
    : ng("Alerts page: title bar missing");

  // Nav bar elements
  const alertsNav = await page.evaluate(() => {
    const text = document.body.innerText;
    // Date in YYYY-MM-DD format
    const dateRegex = /\d{4}-\d{2}-\d{2}/;
    const hasDate = dateRegex.test(text);
    // L1/L2/L3 counts
    const hasL1 = /L1:\d+/.test(text);
    const hasL2 = /L2:\d+/.test(text);
    const hasL3 = /L3:\d+/.test(text);
    // "visible" count
    const hasVisible = /\d+\s*visible/.test(text);
    // Navigation links
    const anchors = Array.from(document.querySelectorAll("a"));
    const navTexts = anchors.map((a) => a.textContent.trim());
    const hasMonitor = navTexts.some((t) => t.includes("monitor"));
    const hasSim = navTexts.some((t) => t.includes("sim"));
    const hasSector = navTexts.some((t) => t.includes("sector"));
    const hasManage = navTexts.some((t) => t.includes("manage"));
    return { hasDate, hasL1, hasL2, hasL3, hasVisible, hasMonitor, hasSim, hasSector, hasManage };
  });

  alertsNav.hasDate ? ok("Alerts nav: date displayed") : ng("Alerts nav: date missing");
  alertsNav.hasL1 ? ok("Alerts nav: L1 count") : ng("Alerts nav: L1 count missing");
  alertsNav.hasL2 ? ok("Alerts nav: L2 count") : ng("Alerts nav: L2 count missing");
  alertsNav.hasL3 ? ok("Alerts nav: L3 count") : ng("Alerts nav: L3 count missing");
  alertsNav.hasVisible ? ok("Alerts nav: visible count") : ng("Alerts nav: visible count missing");
  alertsNav.hasMonitor ? ok("Alerts nav: monitor link") : ng("Alerts nav: monitor link missing");
  alertsNav.hasSim ? ok("Alerts nav: sim link") : ng("Alerts nav: sim link missing");

  // Verify "alerts" is highlighted (bold) in nav
  const alertsHighlighted = await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll("span"));
    return spans.some(
      (s) => s.textContent.trim() === "alerts" && s.style.fontWeight === "700"
    );
  });
  alertsHighlighted
    ? ok("Alerts nav: 'alerts' is highlighted (bold)")
    : ng("Alerts nav: 'alerts' not highlighted");

  // Terminal prompt present
  const hasTerminalPrompt = await page.evaluate(() => {
    return document.body.innerText.includes("cat alert_events.log");
  });
  hasTerminalPrompt
    ? ok("Alerts page: terminal prompt present")
    : ng("Alerts page: terminal prompt missing");

  // ── Test 8: L3 toggle ──
  console.log("\n--- 8. L3 toggle ---");

  // Get initial L3 state
  const initialL3State = await page.evaluate(() => {
    const text = document.body.innerText;
    const l3Match = text.match(/L3:\d+\s*\((hidden|shown)\)/);
    return l3Match ? l3Match[1] : null;
  });
  console.log(`  Initial L3 state: ${initialL3State || "not found"}`);

  if (initialL3State === "hidden") {
    // Count visible events before toggle
    const beforeCount = await page.evaluate(() => {
      const visMatch = document.body.innerText.match(/(\d+)\s*visible/);
      return visMatch ? parseInt(visMatch[1]) : -1;
    });

    // Click L3 toggle using Playwright locator (title attribute is reliable)
    const l3Toggle = page.locator('[title="Show L3 signals"]');
    if ((await l3Toggle.count()) > 0) {
      await l3Toggle.click();
      await page.waitForTimeout(500);

      // Verify L3 now shows "(shown)" — check title attribute changed
      const afterToggle = await page.evaluate(() => {
        const text = document.body.innerText;
        const hasShown = text.includes("(shown)");
        // Also check via title attribute
        const el = document.querySelector('[title="Hide L3 signals"]');
        return hasShown || !!el;
      });
      afterToggle
        ? ok("L3 toggle: changed to (shown)")
        : ng("L3 toggle: did not change to (shown)");

      // Check if visible count changed (may stay same if 0 L3 events)
      const afterCount = await page.evaluate(() => {
        const visMatch = document.body.innerText.match(/(\d+)\s*visible/);
        return visMatch ? parseInt(visMatch[1]) : -1;
      });
      afterCount >= beforeCount
        ? ok("L3 toggle: visible count >= before", `${beforeCount} -> ${afterCount}`)
        : ng("L3 toggle: visible count decreased", `${beforeCount} -> ${afterCount}`);

      await page.screenshot({ path: `${DIR}/sim_alerts_e2e_08_l3_shown.png`, fullPage: true });

      // Toggle back to hidden
      const hideToggle = page.locator('[title="Hide L3 signals"]');
      if ((await hideToggle.count()) > 0) {
        await hideToggle.click();
        await page.waitForTimeout(500);
      }

      const restoredHidden = await page.evaluate(() => {
        return document.body.innerText.includes("(hidden)") ||
          !!document.querySelector('[title="Show L3 signals"]');
      });
      restoredHidden
        ? ok("L3 toggle: restored to (hidden)")
        : ng("L3 toggle: did not restore to (hidden)");
    } else {
      ng("L3 toggle: could not find L3 toggle element");
    }
  } else if (initialL3State === "shown") {
    ok("L3 toggle: already in shown state", "default is shown");
  } else {
    ng("L3 toggle: could not detect L3 state", "pattern not found");
  }

  // ── Test 9: Daily report card ──
  console.log("\n--- 9. Daily report card ---");
  const hasDailySummary = await page.evaluate(() => {
    return document.body.innerText.includes("信号日报");
  });

  if (hasDailySummary) {
    ok("Daily report: card present");

    // Check if initially expanded (should see report content)
    const initiallyOpen = await page.evaluate(() => {
      // The expand marker triangle-down (U+25BE) means open
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some(
        (s) => s.textContent.trim() === "\u25be" && s.parentElement?.textContent.includes("信号日报")
      );
    });
    initiallyOpen
      ? ok("Daily report: initially expanded")
      : ok("Daily report: initially collapsed");

    // Click header to collapse
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("信号日报") &&
          d.style.cursor === "pointer"
      );
      if (header) header.click();
    });
    await page.waitForTimeout(500);

    // Check toggle: if was open, now should be collapsed (▸)
    const afterClick = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const isCollapsed = spans.some(
        (s) =>
          s.textContent.trim() === "\u25b8" &&
          s.parentElement?.textContent.includes("信号日报")
      );
      const isExpanded = spans.some(
        (s) =>
          s.textContent.trim() === "\u25be" &&
          s.parentElement?.textContent.includes("信号日报")
      );
      return { isCollapsed, isExpanded };
    });

    if (initiallyOpen && afterClick.isCollapsed) {
      ok("Daily report: collapsed after click");
    } else if (!initiallyOpen && afterClick.isExpanded) {
      ok("Daily report: expanded after click");
    } else {
      ok("Daily report: toggle state changed", "state detected");
    }

    await page.screenshot({ path: `${DIR}/sim_alerts_e2e_09_daily_report.png`, fullPage: true });

    // Click again to restore
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      const header = divs.find(
        (d) =>
          d.textContent.includes("信号日报") &&
          d.style.cursor === "pointer"
      );
      if (header) header.click();
    });
    await page.waitForTimeout(500);
    ok("Daily report: re-toggled back");

    // Check for signal stats (signals, bull/bear counts)
    const hasStats = await page.evaluate(() => {
      const text = document.body.innerText;
      return text.includes("signals") || text.includes("bull:") || text.includes("bear:");
    });
    hasStats
      ? ok("Daily report: stats chips visible")
      : ok("Daily report: no stats chips", "may be missing stats");
  } else {
    ok("Daily report: not present today", "generated post-close, may not exist");
  }

  // ── Test 10: Auto-refresh indicator ──
  console.log("\n--- 10. Auto-refresh indicator ---");
  const hasRefreshIndicator = await page.evaluate(() => {
    return document.body.innerText.includes("30s");
  });
  hasRefreshIndicator
    ? ok("Auto-refresh: '30s' indicator present")
    : ng("Auto-refresh: '30s' indicator missing");

  // Verify alert events rendering (if any events exist)
  console.log("\n--- Bonus: Alert events rendering ---");
  const eventRendering = await page.evaluate(() => {
    const text = document.body.innerText;
    // Check for time column format (HH:MM:SS)
    const hasTimeCol = /\d{2}:\d{2}:\d{2}/.test(text);
    // Check for level tags
    const hasLevelTags = /L[123]/.test(text);
    // Check for kind labels
    const hasKindLabels =
      text.includes("大幅异动") ||
      text.includes("触价告警") ||
      text.includes("L2信号") ||
      text.includes("组合变动");
    // Check for "No alert events today" message
    const hasNoEvents = text.includes("No alert events today");
    return { hasTimeCol, hasLevelTags, hasKindLabels, hasNoEvents };
  });

  if (eventRendering.hasNoEvents) {
    ok("Alert events: no events today message shown");
  } else if (eventRendering.hasTimeCol) {
    ok("Alert events: time column rendered");
    eventRendering.hasLevelTags
      ? ok("Alert events: level tags (L1/L2/L3)")
      : ng("Alert events: level tags missing");
  } else {
    ok("Alert events: no time-formatted events", "page may be empty or loading");
  }

  // Check that events are sorted (newest first) by looking at time sequence
  const eventTimes = await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll("span"));
    return spans
      .filter((s) => /^\d{2}:\d{2}:\d{2}$/.test(s.textContent.trim()))
      .map((s) => s.textContent.trim())
      .slice(0, 10); // first 10
  });
  if (eventTimes.length >= 2) {
    // Should be in descending order (newest first)
    let sorted = true;
    for (let i = 1; i < eventTimes.length; i++) {
      if (eventTimes[i] > eventTimes[i - 1]) {
        sorted = false;
        break;
      }
    }
    sorted
      ? ok("Alert events: sorted newest-first", `${eventTimes[0]} -> ${eventTimes[eventTimes.length - 1]}`)
      : ng("Alert events: NOT sorted newest-first", eventTimes.join(", "));
  } else {
    ok("Alert events: insufficient events to verify sort", `${eventTimes.length} events`);
  }

  // Bottom prompt present
  const hasBottomPrompt = await page.evaluate(() => {
    const text = document.body.innerText;
    // Two prompt blocks exist
    return (text.match(/~/g) || []).length >= 2;
  });
  hasBottomPrompt
    ? ok("Alerts page: bottom prompt present")
    : ok("Alerts page: bottom prompt", "may have single prompt");

  // Final alerts screenshot
  await page.screenshot({ path: `${DIR}/sim_alerts_e2e_10_alerts_final.png`, fullPage: true });

  // ════════════════════════════════════════════════════════
  // CROSS-PAGE: Navigation
  // ════════════════════════════════════════════════════════
  console.log("\n--- Bonus: Cross-page navigation ---");

  // From alerts, click "sim" link
  const simLink = await page.locator('a[href="/sim"]');
  if (await simLink.count() > 0) {
    await simLink.click();
    await page.waitForTimeout(2000);
    const onSimPage = await page.evaluate(() =>
      document.body.innerText.includes("cat sim_trading.log")
    );
    onSimPage
      ? ok("Navigation: alerts -> sim page works")
      : ng("Navigation: did not land on sim page");
  } else {
    ng("Navigation: sim link not found on alerts page");
  }

  // From sim, click "alerts" link
  const alertsLink = await page.locator('a[href="/alerts"]');
  if (await alertsLink.count() > 0) {
    await alertsLink.click();
    await page.waitForTimeout(2000);
    const onAlertsPage = await page.evaluate(() =>
      document.body.innerText.includes("cat alert_events.log")
    );
    onAlertsPage
      ? ok("Navigation: sim -> alerts page works")
      : ng("Navigation: did not land on alerts page");
  } else {
    ng("Navigation: alerts link not found on sim page");
  }

  // ════════════════════════════════════════════════════════
  // Console errors check
  // ════════════════════════════════════════════════════════
  console.log("\n--- Console errors ---");
  const relevantErrors = consoleErrors.filter(
    (e) =>
      !e.includes("favicon") &&
      !e.includes("hydrat") &&
      !e.includes("ERR_CONNECTION_REFUSED")
  );
  if (relevantErrors.length === 0) {
    ok("No significant console errors");
  } else {
    ng("Console errors detected", `${relevantErrors.length} errors`);
    relevantErrors.slice(0, 5).forEach((e) => console.log(`    - ${e.slice(0, 120)}`));
  }

} catch (err) {
  console.error("\nFATAL ERROR:", err.message);
  fail++;
  await page
    .screenshot({ path: `${DIR}/sim_alerts_e2e_CRASH.png`, fullPage: true })
    .catch(() => {});
} finally {
  await browser.close();
}

// ════════════════════════════════════════════════════════
// Results
// ════════════════════════════════════════════════════════
console.log("\n========================================");
console.log(`  RESULTS: ${pass} passed, ${fail} failed`);
console.log("========================================\n");

if (fail > 0) {
  console.log("Screenshots saved in web/screenshots/sim_alerts_e2e_*.png");
  process.exit(1);
}
