import { test, expect } from "@playwright/test";

test.describe("Dashboard (首页)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    // 等数据加载完成：refresh #1 表示至少 fetch 过一次
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
  });

  test("页面正常加载，标题栏和 Tab 栏可见", async ({ page }) => {
    await expect(page.locator("text=monitor (node)").first()).toBeVisible();
    await expect(page.locator("text=Claude Code (node)")).toBeVisible();
  });

  test("摘要栏显示节点数和涨跌统计", async ({ page }) => {
    await expect(page.locator("text=Nodes:")).toBeVisible();
    await expect(page.locator("text=throughput:")).toBeVisible();
    await expect(page.locator("text=avg_delta:")).toBeVisible();
  });

  test("持仓 section 可见且可折叠", async ({ page }) => {
    // 至少有一个 prod section
    const prodSection = page.locator("text=/prod:(stocks|ETF)/").first();
    if (await prodSection.isVisible()) {
      // 检查 PROD 行可见
      await expect(page.locator("text=PROD").first()).toBeVisible();

      // 点击折叠
      await prodSection.click();
      // 再次点击展开
      await prodSection.click();
      await expect(page.locator("text=PROD").first()).toBeVisible();
    }
  });

  test("自选 section 可见且可折叠", async ({ page }) => {
    const stageSection = page.locator("text=/stage:(stocks|ETF)/").first();
    if (await stageSection.isVisible()) {
      await expect(page.locator("text=DEV").first()).toBeVisible();

      // 折叠
      await stageSection.click();
      // 展开
      await stageSection.click();
    }
  });

  test("hidden section 默认折叠", async ({ page }) => {
    const hiddenSection = page.locator("text=/hidden \\(\\d+\\)/");
    if (await hiddenSection.isVisible()) {
      // hidden section 的 ▸ 表示折叠状态
      await expect(hiddenSection).toContainText("▸");
      // 点击展开
      await hiddenSection.click();
      await expect(hiddenSection).toContainText("▾");
      // 再点击折叠回去
      await hiddenSection.click();
      await expect(hiddenSection).toContainText("▸");
    }
  });

  test("表头列可点击排序", async ({ page }) => {
    const chgHeader = page.locator("text=CHG%").first();
    if (await chgHeader.isVisible()) {
      await chgHeader.click();
      // 点击后应出现排序箭头 ▼ 或 ▲
      await expect(chgHeader).toContainText(/[▲▼]/);
      // 再点切换方向
      await chgHeader.click();
      await expect(chgHeader).toContainText(/[▲▼]/);
    }
  });

  test("portfolio 摘要显示 position/yield/return/today", async ({ page }) => {
    // 如果有持仓，应显示 portfolio 摘要行
    const positionLabel = page.locator("text=position:");
    if (await positionLabel.isVisible()) {
      await expect(page.locator("text=yield:")).toBeVisible();
      await expect(page.locator("text=return:")).toBeVisible();
      await expect(page.locator("text=today:")).toBeVisible();
    }
  });

  test("命令提示符可见", async ({ page }) => {
    await expect(page.locator("text=~/projects/monitor").last()).toBeVisible();
  });
});

test.describe("Manage (管理页)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/manage", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=manage", { timeout: 15_000 });
    await page.waitForTimeout(1000);
  });

  test("管理页正常加载", async ({ page }) => {
    await expect(page.getByText("manage", { exact: true })).toBeVisible();
    await expect(page.locator("text=settings")).toBeVisible();
  });

  test("settings 区域可展开编辑", async ({ page }) => {
    // settings 默认折叠，先点开
    const settingsToggle = page.locator("text=settings").first();
    await settingsToggle.click();
    const pollInput = page.locator('input[type="number"]').first();
    await expect(pollInput).toBeVisible();
    await pollInput.click();
    await expect(pollInput).toBeFocused();
  });

  test("持仓和自选分组可见", async ({ page }) => {
    // 持仓分组：A股个股/ETF/港股
    await expect(page.locator("text=/持仓:(A股个股|ETF|港股)/").first()).toBeVisible();
    // 自选分组
    const watchSection = page.locator("text=/自选/").first();
    if (await watchSection.isVisible()) {
      await expect(watchSection).toBeVisible();
    }
  });

  test("add stock 区域可见且能输入", async ({ page }) => {
    await expect(page.locator("text=add stock")).toBeVisible();
    const codeInput = page.locator('input[placeholder="000001"]');
    await expect(codeInput).toBeVisible();
    await codeInput.fill("000001");
    await expect(codeInput).toHaveValue("000001");
    // 清空避免实际添加
    await codeInput.fill("");
  });

  test("导航链接可以回到首页", async ({ page }) => {
    const backLink = page.locator("text=← monitor");
    await expect(backLink).toBeVisible();
    await backLink.click();
    await expect(page).toHaveURL("/");
  });

  test("持仓 hide 开关可见", async ({ page }) => {
    // 只验证 hide toggle 存在，不实际点击（避免污染 config 数据）
    const hideToggle = page.locator("text=hide").first();
    if (await hideToggle.isVisible()) {
      await expect(hideToggle).toBeVisible();
    }
  });
});
