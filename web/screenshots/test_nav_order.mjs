import { chromium } from "playwright";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  for (const url of ["/", "/alerts", "/sim", "/manage", "/sector", "/sector/rotation"]) {
    await page.goto("http://localhost:3120" + url, { waitUntil: "networkidle", timeout: 15000 });
    await page.waitForTimeout(800);
    const navItems = await page.evaluate(() => {
      // Grab all links + bold spans in the nav area
      const bar = [...document.querySelectorAll("div")].find(
        (d) => d.style.fontSize === "13px" && d.style.gap === "16px" && d.style.display === "flex" && d.querySelectorAll("a").length >= 2
      );
      if (!bar) return "nav not found";
      return [...bar.children]
        .map((c) => {
          const t = c.textContent?.trim();
          const tag = c.tagName;
          const bold = c.style?.fontWeight === "700" ? "*" : "";
          return t ? `${bold}${t}${bold}` : null;
        })
        .filter(Boolean)
        .join("  ");
    });
    console.log(`${url.padEnd(20)} ${navItems}`);
  }
  await browser.close();
})();
