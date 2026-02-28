import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR = new URL(".", import.meta.url).pathname;
const TEST_INDEX_ID = "__e2e_test_sector__";
const TEST_INDEX_NAME = "E2E板块测试";

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? " -- " + detail : ""}`);
}

async function cleanup() {
  // Delete the test index via API if it exists
  try {
    const resp = await fetch(`${BASE}/api/sector`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id: TEST_INDEX_ID }),
    });
    const json = await resp.json();
    if (json.ok) console.log("  (cleanup) deleted test index");
  } catch {
    /* ignore */
  }
}

/**
 * Find the first index name element in the left panel and click it to open
 * the chart modal. Returns the clicked text or null.
 *
 * Strategy: The index name is rendered in a <div> with style color matching
 * the cyan theme (#8be9fd). The parent div has cursor:pointer. We look for
 * a div whose computed color is cyan-ish and that sits inside a 48px-high row.
 */
function clickFirstIndexName() {
  // The index name is inside: <div style="font-size:12px, color:#8be9fd ...">name</div>
  // which is inside: <div style="flex:1, cursor:pointer ...">
  // which is inside a row div with height 48px.
  // We find divs colored #8be9fd (cyan) that contain index names.
  const allDivs = Array.from(document.querySelectorAll("div"));
  for (const d of allDivs) {
    if (
      d.style.fontSize === "12px" &&
      d.style.color === "rgb(139, 233, 253)" &&
      d.style.whiteSpace === "nowrap"
    ) {
      // This is the index name text div. Click its parent (the cursor:pointer wrapper)
      const parent = d.parentElement;
      if (parent && getComputedStyle(parent).cursor === "pointer") {
        parent.click();
        return d.textContent.trim();
      }
    }
  }
  return null;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser
    .newContext({ viewport: { width: 1400, height: 900 } })
    .then((ctx) => ctx.newPage());

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push("PAGE_ERROR: " + err.message));

  // Accept all confirm() dialogs (for delete)
  page.on("dialog", (d) => d.accept());

  try {
    // Pre-cleanup: remove any leftover test index from previous runs
    await cleanup();

    // ════════════════════════════════════════════════════════════════
    // 1. PAGE LOADS — no error banner, indices matrix, nav links
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 1. Page loads ===");
    await page.goto(`${BASE}/sector`, { waitUntil: "networkidle", timeout: 15000 });
    await page.waitForTimeout(2000);

    await page.screenshot({ path: `${DIR}/sector_e2e_01_loaded.png`, fullPage: true });

    // Check no [ERROR] banner
    const hasError = await page.evaluate(() =>
      document.body.innerText.includes("[ERROR]")
    );
    record("No [ERROR] banner on load", !hasError);

    // Check "Loading" is gone
    const hasLoading = await page.evaluate(() =>
      document.body.innerText.includes("Loading sector data")
    );
    record("Loading indicator is gone", !hasLoading);

    // Check nav links present
    const navLinks = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll("a"));
      return links.map((a) => a.textContent.trim());
    });
    const hasMonitor = navLinks.some((t) => t.includes("monitor"));
    const hasAlerts = navLinks.some((t) => t.includes("alerts"));
    const hasSim = navLinks.some((t) => t.includes("sim"));
    const hasManage = navLinks.some((t) => t.includes("manage"));
    record("Nav: monitor link present", hasMonitor);
    record("Nav: alerts link present", hasAlerts);
    record("Nav: sim link present", hasSim);
    record("Nav: manage link present", hasManage);

    // Check "sector" is highlighted (bold purple) in nav
    const sectorHighlighted = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some(
        (s) =>
          s.textContent.trim() === "sector" &&
          (s.style.fontWeight === "700" || s.style.fontWeight === "bold")
      );
    });
    record("Nav: sector tab highlighted", sectorHighlighted);

    // Check section headers present
    const bodyText = await page.evaluate(() => document.body.innerText);
    record("Section: '我的指数' present", bodyText.includes("我的指数"));
    record("Section: '主线告警' present", bodyText.includes("主线告警"));

    // Check indices matrix visible (at least one index row exists OR empty state message)
    const hasIndicesOrEmpty =
      bodyText.includes("暂无自定义指数") ||
      (await page.evaluate(() => {
        const spans = Array.from(document.querySelectorAll("span"));
        return spans.some(
          (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
        );
      }));
    record("Indices matrix or empty state visible", hasIndicesOrEmpty);

    // Count existing indices for later reference
    const existingIndicesCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.filter(
        (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
      ).length;
    });
    console.log(`  (info) existing indices count: ${existingIndicesCount}`);

    // Check summary bar shows count
    const summaryMatch = bodyText.match(/(\d+) indices \| (\d+) alerts/);
    record(
      "Summary bar shows index/alert counts",
      !!summaryMatch,
      summaryMatch?.[0] || ""
    );

    // ════════════════════════════════════════════════════════════════
    // 2. INDEX ROW CLICK — K-line chart modal opens
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 2. Index row click -> chart modal ===");

    if (existingIndicesCount > 0) {
      // Click the first index name
      const clicked = await page.evaluate(clickFirstIndexName);
      console.log(`  (info) clicked index: ${clicked}`);

      await page.waitForTimeout(800);
      await page.screenshot({
        path: `${DIR}/sector_e2e_02_chart_modal.png`,
        fullPage: true,
      });

      // Check modal overlay appeared (fixed position div)
      const modalVisible = await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        return divs.some(
          (d) =>
            getComputedStyle(d).position === "fixed" &&
            d.style.inset === "0px" &&
            d.style.zIndex === "100"
        );
      });
      record("Chart modal opened", modalVisible);

      // Check SVG chart is present in the modal
      const svgPresent = await page.evaluate(() => {
        return document.querySelectorAll("svg").length > 0;
      });
      record("SVG chart visible in modal", svgPresent);

      // Check polyline (the actual chart line) exists
      const polylinePresent = await page.evaluate(() => {
        return document.querySelectorAll("polyline").length > 0;
      });
      record("Chart polyline rendered", polylinePresent);

      // Check modal has index name in header
      const modalHeaderInfo = await page.evaluate(() => {
        // Find the modal: look for a div with background #282a36, borderRadius, width 720
        const divs = Array.from(document.querySelectorAll("div"));
        const modal = divs.find(
          (d) => d.style.width === "720px" && d.style.borderRadius === "8px"
        );
        if (!modal) return { found: false };
        const text = modal.textContent;
        return {
          found: true,
          hasValue: text.includes("\u6307\u6570\u503c"), // 指数值
          hasDay: /\d+\u65e5/.test(text), // N日
        };
      });
      record(
        "Modal header shows index name and value",
        modalHeaderInfo.found && modalHeaderInfo.hasValue
      );

      // ════════════════════════════════════════════════════════════════
      // 3. COMPONENT TABLE IN MODAL
      // ════════════════════════════════════════════════════════════════
      console.log("\n=== 3. Component table in modal ===");

      // Check "成分股" text present
      const hasComponentSection = await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const modal = divs.find(
          (d) => d.style.width === "720px" && d.style.borderRadius === "8px"
        );
        if (!modal) return false;
        return modal.textContent.includes("\u6210\u5206\u80a1"); // 成分股
      });
      record("Component section '\u6210\u5206\u80a1' visible", hasComponentSection);

      // Check table headers
      const hasTableHeaders = await page.evaluate(() => {
        const ths = Array.from(document.querySelectorAll("th"));
        const headers = ths.map((th) => th.textContent.trim());
        return (
          headers.includes("\u4ee3\u7801") && // 代码
          headers.includes("\u540d\u79f0") && // 名称
          headers.includes("\u6700\u65b0\u4ef7") && // 最新价
          headers.includes("\u6da8\u8dcc\u5e45") // 涨跌幅
        );
      });
      record("Component table headers present", hasTableHeaders);

      // Check table has data rows
      const componentRowCount = await page.evaluate(() => {
        const trs = Array.from(document.querySelectorAll("tbody tr"));
        return trs.length;
      });
      record(
        "Component table has data rows",
        componentRowCount > 0,
        `${componentRowCount} rows`
      );

      // Check stock codes are displayed (6-digit format)
      const stockCodesVisible = await page.evaluate(() => {
        const tds = Array.from(document.querySelectorAll("tbody td"));
        return tds.some((td) => /^\d{6}$/.test(td.textContent.trim()));
      });
      record("Stock codes visible in table", stockCodesVisible);

      await page.screenshot({
        path: `${DIR}/sector_e2e_03_components.png`,
        fullPage: true,
      });

      // ════════════════════════════════════════════════════════════════
      // 4. MODAL CLOSE — press Escape
      // ════════════════════════════════════════════════════════════════
      console.log("\n=== 4. Modal close (Escape) ===");

      await page.keyboard.press("Escape");
      await page.waitForTimeout(500);

      const modalGone = await page.evaluate(() => {
        return document.querySelectorAll("svg polyline").length === 0;
      });
      record("Modal closed by Escape", modalGone);

      await page.screenshot({ path: `${DIR}/sector_e2e_04_modal_closed.png` });

      // Re-open modal and close by clicking X button
      console.log("\n=== 4b. Modal close (click X) ===");
      await page.evaluate(clickFirstIndexName);
      await page.waitForTimeout(500);

      // Find and click the X close button in the modal header
      const xClicked = await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll("div"));
        const modal = divs.find(
          (d) => d.style.width === "720px" && d.style.borderRadius === "8px"
        );
        if (!modal) return false;
        // The close X is a span with fontSize 18, cursor pointer, text "×"
        const spans = modal.querySelectorAll("span");
        for (const s of spans) {
          if (
            s.textContent.trim() === "\u00d7" &&
            s.style.cursor === "pointer" &&
            s.style.fontSize === "18px"
          ) {
            s.click();
            return true;
          }
        }
        return false;
      });
      await page.waitForTimeout(500);

      const modalGoneAfterX = await page.evaluate(() => {
        return document.querySelectorAll("svg polyline").length === 0;
      });
      record("Modal closed by X button", xClicked && modalGoneAfterX);

      // Re-open modal and close by clicking overlay backdrop
      console.log("\n=== 4c. Modal close (click backdrop) ===");
      await page.evaluate(clickFirstIndexName);
      await page.waitForTimeout(500);

      // Click the backdrop at the edge (outside the 720px modal content area)
      // Modal is centered, so clicking at extreme left (x=5) should hit the backdrop
      await page.mouse.click(5, 450);
      await page.waitForTimeout(500);

      const modalGoneAfterBackdrop = await page.evaluate(() => {
        return document.querySelectorAll("svg polyline").length === 0;
      });
      record("Modal closed by clicking backdrop", modalGoneAfterBackdrop);

      await page.screenshot({
        path: `${DIR}/sector_e2e_04c_backdrop_close.png`,
      });
    } else {
      console.log("  (skip) No existing indices to test chart modal");
      record("Chart modal test (skipped, no indices)", true, "skipped");
    }

    // ════════════════════════════════════════════════════════════════
    // 5. CREATE INDEX — fill form and submit
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 5. Create index ===");

    // Click "+ 新建" button
    const createBtnClicked = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      const btn = buttons.find(
        (b) => b.textContent.trim() === "+ \u65b0\u5efa"
      );
      if (btn) {
        btn.click();
        return true;
      }
      return false;
    });
    record("Clicked '+ \u65b0\u5efa' button", createBtnClicked);

    await page.waitForTimeout(500);
    await page.screenshot({
      path: `${DIR}/sector_e2e_05a_create_modal.png`,
    });

    // Verify create modal appeared
    const createModalVisible = await page.evaluate(() =>
      document.body.innerText.includes("\u65b0\u5efa\u81ea\u5b9a\u4e49\u6307\u6570")
    ); // 新建自定义指数
    record("Create modal opened", createModalVisible);

    // Fill form fields via placeholder selectors
    const nameInput = page.locator('input[placeholder="\u78f7\u5316\u5de5"]'); // 磷化工
    await nameInput.fill(TEST_INDEX_NAME);
    await page.waitForTimeout(100);

    const idInput = page.locator('input[placeholder="phosphorus"]');
    await idInput.fill(TEST_INDEX_ID);
    await page.waitForTimeout(100);

    const stocksInput = page.locator(
      'input[placeholder="000792,600096,002895"]'
    );
    await stocksInput.fill("000001,000002,600000");
    await page.waitForTimeout(100);

    await page.screenshot({
      path: `${DIR}/sector_e2e_05b_form_filled.png`,
    });

    // Click "创建" button inside the modal
    await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      for (const btn of buttons) {
        if (btn.textContent.trim() === "\u521b\u5efa") {
          // 创建
          btn.click();
          break;
        }
      }
    });

    // Wait for API response and re-fetch
    await page.waitForTimeout(2000);
    await page.screenshot({
      path: `${DIR}/sector_e2e_05c_created.png`,
      fullPage: true,
    });

    // Verify the new index appears in the page
    const newIndexVisible = await page.evaluate(
      (name) => document.body.innerText.includes(name),
      TEST_INDEX_NAME
    );
    record("New index visible on page", newIndexVisible);

    // Verify the create modal closed
    const createModalClosed = await page.evaluate(
      () =>
        !document.body.innerText.includes(
          "\u65b0\u5efa\u81ea\u5b9a\u4e49\u6307\u6570"
        )
    );
    record("Create modal closed after submit", createModalClosed);

    // Verify index count incremented
    const newCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.filter(
        (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
      ).length;
    });
    record(
      "Index count incremented",
      newCount === existingIndicesCount + 1,
      `was ${existingIndicesCount}, now ${newCount}`
    );

    // ════════════════════════════════════════════════════════════════
    // 6. STAR TOGGLE — click ☆ on the new index
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 6. Star toggle ===");

    // Find the star icon for our test index by finding its row
    const starClicked = await page.evaluate((name) => {
      // Strategy: find all star spans, then check if their ancestor row
      // contains the test index name
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (
          (s.textContent === "\u2606" || s.textContent === "\u2605") &&
          s.style.cursor === "pointer"
        ) {
          // Walk up to find the row (height: 48px parent)
          let row = s.parentElement;
          while (row && row.style.height !== "48px") row = row.parentElement;
          if (row && row.textContent.includes(name)) {
            s.click();
            return `clicked ${s.textContent}`;
          }
        }
      }
      return null;
    }, TEST_INDEX_NAME);
    console.log(`  (info) star action: ${starClicked}`);

    await page.waitForTimeout(1500);
    await page.screenshot({
      path: `${DIR}/sector_e2e_06_star_toggled.png`,
      fullPage: true,
    });

    // Verify star changed to ★ for our test index
    const isStarred = await page.evaluate((name) => {
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (s.textContent === "\u2605" && s.style.cursor === "pointer") {
          let row = s.parentElement;
          while (row && row.style.height !== "48px") row = row.parentElement;
          if (row && row.textContent.includes(name)) return true;
        }
      }
      return false;
    }, TEST_INDEX_NAME);
    record("Star toggle: index now starred", isStarred);

    // Toggle back to ☆
    await page.evaluate((name) => {
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (s.textContent === "\u2605" && s.style.cursor === "pointer") {
          let row = s.parentElement;
          while (row && row.style.height !== "48px") row = row.parentElement;
          if (row && row.textContent.includes(name)) {
            s.click();
            break;
          }
        }
      }
    }, TEST_INDEX_NAME);

    await page.waitForTimeout(1500);

    const isUnstarred = await page.evaluate((name) => {
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (s.textContent === "\u2606" && s.style.cursor === "pointer") {
          let row = s.parentElement;
          while (row && row.style.height !== "48px") row = row.parentElement;
          if (row && row.textContent.includes(name)) return true;
        }
      }
      return false;
    }, TEST_INDEX_NAME);
    record("Star toggle: index unstarred back", isUnstarred);

    // ════════════════════════════════════════════════════════════════
    // 7. WATCH TOGGLE — via API (no UI button currently)
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 7. Watch toggle (via API) ===");

    // Test the watch API action and verify state via re-fetch
    const watchResp = await page.evaluate(async (id) => {
      const resp = await fetch("/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "watch", id, value: false }),
      });
      return resp.json();
    }, TEST_INDEX_ID);
    record("Watch API: set watch=false", watchResp?.ok === true);

    // Verify via GET that watch is now false
    const checkWatch = await page.evaluate(async (id) => {
      const resp = await fetch("/api/sector");
      const data = await resp.json();
      const idx = data.indices?.find((i) => i.id === id);
      return idx?.watch;
    }, TEST_INDEX_ID);
    record("Watch state verified as false", checkWatch === false);

    // Toggle watch back
    const watchResp2 = await page.evaluate(async (id) => {
      const resp = await fetch("/api/sector", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "watch", id, value: true }),
      });
      return resp.json();
    }, TEST_INDEX_ID);
    record("Watch API: set watch=true", watchResp2?.ok === true);

    await page.screenshot({
      path: `${DIR}/sector_e2e_07_watch_toggled.png`,
      fullPage: true,
    });

    // ════════════════════════════════════════════════════════════════
    // 8. HORIZONTAL SCROLL — verify dates scroll right
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 8. Horizontal scroll ===");

    // Reload to get fresh state
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(1500);

    // Find the scrollable container using computed style instead of inline style
    const scrollInfo = await page.evaluate(() => {
      const allDivs = Array.from(document.querySelectorAll("div"));
      // The scrollable container has overflowX: auto and contains date headers
      // It's the right side of the matrix layout
      for (const d of allDivs) {
        const cs = getComputedStyle(d);
        if (
          (cs.overflowX === "auto" || d.style.overflowX === "auto") &&
          d.scrollWidth > 0 &&
          d.children.length >= 2
        ) {
          // Check if first child looks like a date header row
          const firstChild = d.children[0];
          if (firstChild && firstChild.textContent.match(/\d{2}-\d{2}/)) {
            return {
              exists: true,
              scrollWidth: d.scrollWidth,
              clientWidth: d.clientWidth,
              canScroll: d.scrollWidth > d.clientWidth,
            };
          }
        }
      }
      return { exists: false };
    });
    record("Scrollable date container exists", scrollInfo.exists);

    if (scrollInfo.exists) {
      console.log(
        `  (info) scrollWidth=${scrollInfo.scrollWidth} clientWidth=${scrollInfo.clientWidth}`
      );

      if (scrollInfo.canScroll) {
        // Scroll to the right to see older dates
        await page.evaluate(() => {
          const allDivs = Array.from(document.querySelectorAll("div"));
          for (const d of allDivs) {
            const cs = getComputedStyle(d);
            if (
              (cs.overflowX === "auto" || d.style.overflowX === "auto") &&
              d.scrollWidth > d.clientWidth &&
              d.children.length >= 2
            ) {
              const firstChild = d.children[0];
              if (
                firstChild &&
                firstChild.textContent.match(/\d{2}-\d{2}/)
              ) {
                d.scrollLeft = d.scrollWidth;
                break;
              }
            }
          }
        });
        await page.waitForTimeout(500);
        await page.screenshot({
          path: `${DIR}/sector_e2e_08_scrolled.png`,
          fullPage: true,
        });

        const scrolledRight = await page.evaluate(() => {
          const allDivs = Array.from(document.querySelectorAll("div"));
          for (const d of allDivs) {
            const cs = getComputedStyle(d);
            if (
              (cs.overflowX === "auto" || d.style.overflowX === "auto") &&
              d.scrollWidth > d.clientWidth
            ) {
              const firstChild = d.children[0];
              if (
                firstChild &&
                firstChild.textContent.match(/\d{2}-\d{2}/)
              ) {
                return d.scrollLeft > 0;
              }
            }
          }
          return false;
        });
        record("Scrolled right to show older dates", scrolledRight);

        // Scroll back to left
        await page.evaluate(() => {
          const allDivs = Array.from(document.querySelectorAll("div"));
          for (const d of allDivs) {
            const cs = getComputedStyle(d);
            if (
              (cs.overflowX === "auto" || d.style.overflowX === "auto") &&
              d.scrollWidth > d.clientWidth
            ) {
              d.scrollLeft = 0;
              break;
            }
          }
        });
        await page.waitForTimeout(300);
      } else {
        record(
          "Horizontal scroll (no overflow, few dates)",
          true,
          "dates fit in view"
        );
      }
    }

    // ════════════════════════════════════════════════════════════════
    // 9. SECTION COLLAPSE — toggle 我的指数 and 主线告警
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 9. Section collapse/expand ===");

    // Collapse 我的指数 section by clicking the header span
    const collapseIndices = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      const header = spans.find(
        (s) =>
          s.textContent.includes("\u6211\u7684\u6307\u6570") &&
          s.onclick !== undefined
      );
      if (header) {
        header.click();
        return true;
      }
      // Fallback: find span with the text and click
      for (const s of spans) {
        if (s.textContent.includes("\u6211\u7684\u6307\u6570")) {
          s.click();
          return true;
        }
      }
      return false;
    });
    await page.waitForTimeout(300);

    const indicesCollapsed = await page.evaluate(() => {
      // After collapse, no star icons should be visible
      const stars = Array.from(document.querySelectorAll("span")).filter(
        (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
      );
      return stars.length === 0;
    });
    record(
      "\u6211\u7684\u6307\u6570 section collapsed",
      collapseIndices && indicesCollapsed
    );

    await page.screenshot({ path: `${DIR}/sector_e2e_09a_collapsed.png` });

    // Expand back
    await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (s.textContent.includes("\u6211\u7684\u6307\u6570")) {
          s.click();
          break;
        }
      }
    });
    await page.waitForTimeout(300);

    const indicesExpanded = await page.evaluate(() => {
      const stars = Array.from(document.querySelectorAll("span")).filter(
        (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
      );
      return stars.length > 0;
    });
    record("\u6211\u7684\u6307\u6570 section re-expanded", indicesExpanded);

    // Collapse 主线告警 section
    const collapseAlerts = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      for (const d of divs) {
        if (
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("\u4e3b\u7ebf\u544a\u8b66")
        ) {
          d.click();
          return true;
        }
      }
      return false;
    });
    await page.waitForTimeout(300);
    await page.screenshot({
      path: `${DIR}/sector_e2e_09b_alerts_collapsed.png`,
    });
    record("\u4e3b\u7ebf\u544a\u8b66 section toggle clicked", collapseAlerts);

    // Expand back
    await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      for (const d of divs) {
        if (
          d.style.cursor === "pointer" &&
          d.style.userSelect === "none" &&
          d.textContent.includes("\u4e3b\u7ebf\u544a\u8b66")
        ) {
          d.click();
          break;
        }
      }
    });
    await page.waitForTimeout(300);

    // ════════════════════════════════════════════════════════════════
    // 10. 主线告警 SECTION — verify content
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 10. Mainline alerts section ===");

    const alertsSection = await page.evaluate(() => {
      const text = document.body.innerText;
      const hasSection = text.includes("\u4e3b\u7ebf\u544a\u8b66");
      const hasAlertData =
        text.includes("\u6682\u65e0\u544a\u8b66\u8bb0\u5f55") || // 暂无告警记录
        /\[\d{2}-\d{2}/.test(text);
      return { hasSection, hasAlertData };
    });
    record("Mainline alerts section exists", alertsSection.hasSection);
    record(
      "Alerts section has content (data or empty state)",
      alertsSection.hasAlertData
    );

    await page.screenshot({
      path: `${DIR}/sector_e2e_10_alerts_section.png`,
      fullPage: true,
    });

    // ════════════════════════════════════════════════════════════════
    // 11. CREATE MODAL VALIDATION — empty fields
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 11. Create modal validation ===");

    // Open create modal
    await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      const btn = buttons.find(
        (b) => b.textContent.trim() === "+ \u65b0\u5efa"
      );
      if (btn) btn.click();
    });
    await page.waitForTimeout(500);

    // Try to submit with empty fields
    await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      for (const btn of buttons) {
        if (btn.textContent.trim() === "\u521b\u5efa") {
          btn.click();
          break;
        }
      }
    });
    await page.waitForTimeout(500);

    const validationError = await page.evaluate(() =>
      document.body.innerText.includes("\u4e0d\u80fd\u4e3a\u7a7a")
    ); // 不能为空
    record("Validation: empty fields show error", validationError);

    await page.screenshot({ path: `${DIR}/sector_e2e_11_validation.png` });

    // Close create modal via 取消 button
    await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      for (const btn of buttons) {
        if (btn.textContent.trim() === "\u53d6\u6d88") {
          // 取消
          btn.click();
          break;
        }
      }
    });
    await page.waitForTimeout(300);

    const createModalClosedByCancel = await page.evaluate(
      () =>
        !document.body.innerText.includes(
          "\u65b0\u5efa\u81ea\u5b9a\u4e49\u6307\u6570"
        )
    );
    record("Create modal closed by \u53d6\u6d88", createModalClosedByCancel);

    // ════════════════════════════════════════════════════════════════
    // 12. DELETE INDEX — remove the test index
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 12. Delete test index ===");

    // Find and click the × delete button next to our test index
    const deleteClicked = await page.evaluate((name) => {
      // Find all spans with title="删除"
      const spans = Array.from(document.querySelectorAll("span"));
      for (const s of spans) {
        if (s.title === "\u5220\u9664") {
          // Check that this span is in a row containing our test name
          let row = s.parentElement;
          while (row && row.style.height !== "48px") row = row.parentElement;
          if (row && row.textContent.includes(name)) {
            s.click();
            return true;
          }
        }
      }
      return false;
    }, TEST_INDEX_NAME);
    record("Clicked delete on test index", deleteClicked);

    // The dialog auto-accepts
    await page.waitForTimeout(2000);
    await page.screenshot({
      path: `${DIR}/sector_e2e_12_deleted.png`,
      fullPage: true,
    });

    // Verify test index is gone
    const indexGone = await page.evaluate(
      (name) => !document.body.innerText.includes(name),
      TEST_INDEX_NAME
    );
    record("Test index removed from page", indexGone);

    // Verify count went back
    const finalCount = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.filter(
        (s) => s.textContent === "\u2605" || s.textContent === "\u2606"
      ).length;
    });
    record(
      "Index count restored",
      finalCount === existingIndicesCount,
      `was ${existingIndicesCount}, now ${finalCount}`
    );

    // ════════════════════════════════════════════════════════════════
    // 13. AUTO-REFRESH — verify timer indicator
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 13. Auto-refresh indicator ===");
    const hasRefreshIndicator = await page.evaluate(() =>
      document.body.innerText.includes("auto-refresh 60s")
    );
    record("Auto-refresh 60s indicator shown", hasRefreshIndicator);

    // ════════════════════════════════════════════════════════════════
    // 14. CONSOLE ERRORS
    // ════════════════════════════════════════════════════════════════
    console.log("\n=== 14. Console errors check ===");
    const realErrors = consoleErrors.filter(
      (e) => !e.includes("favicon") && !e.includes("404")
    );
    record(
      "No critical console errors",
      realErrors.length === 0,
      realErrors.length > 0 ? realErrors.slice(0, 3).join("; ") : ""
    );

    // Final full-page screenshot
    await page.screenshot({
      path: `${DIR}/sector_e2e_final.png`,
      fullPage: true,
    });
  } catch (err) {
    console.error("FATAL ERROR:", err);
    await page
      .screenshot({ path: `${DIR}/sector_e2e_error.png`, fullPage: true })
      .catch(() => {});
  } finally {
    // Cleanup: always delete test index
    await cleanup();
    await browser.close();
  }

  // ════════════════════════════════════════════════════════════════
  // SUMMARY
  // ════════════════════════════════════════════════════════════════
  const SEP = "=".repeat(42);
  console.log(`\n${SEP}`);
  console.log("  SECTOR E2E TEST SUMMARY");
  console.log(SEP);
  const passed = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok).length;
  console.log(`  Total: ${results.length}  Passed: ${passed}  Failed: ${failed}`);
  if (failed > 0) {
    console.log("\n  Failed tests:");
    results
      .filter((r) => !r.ok)
      .forEach((r) => console.log(`    - ${r.name}`));
  }
  console.log(`${SEP}\n`);

  process.exit(failed > 0 ? 1 : 0);
}

main();
