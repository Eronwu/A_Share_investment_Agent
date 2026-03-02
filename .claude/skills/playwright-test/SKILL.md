---
name: playwright-test
description: Use when verifying UI changes after frontend code modifications, checking UX fixes visually, or validating data consistency between dashboard summary bar and row-level values.
user_invocable: true
---

# Playwright Full Checkup & Functional Test

## Overview

Run the automated test suites (~330+ total tests) against the web dashboard at `http://localhost:3120`, then visually inspect screenshots and report findings. The full checkup (65 tests) is the single-entry regression; per-page E2E tests cover interactive features in depth.

## Mandatory Rule

**每个新功能或 UI 改动必须编写对应 E2E 测试。不写测试的功能视为未完成。**

新功能测试脚本必须包含：
1. **完整用户流程** — 模拟点击、填表、选择、提交等真实交互
2. **每步截图** — 关键步骤截图并用 Read 工具审查 UI 是否正确
3. **API 验证** — 拦截 API response 确认数据正确读写
4. **数据清理** — 测试创建的数据在测试结束时删除，不污染生产环境
5. **结果输出** — `[PASS]`/`[FAIL]` 格式，方便快速扫描

## When to Use

- **必须**: 开发任何新功能或 UI 改动后（不可跳过）
- After modifying any `web/app/**/*.tsx` component or API route
- When verifying visual layout, data rendering, or interactive components
- When checking data consistency (summary totals vs row-level values)
- When the user says `/playwright-test` or asks to verify the UI
- **Not for**: unit tests, API-only tests, or Python backend testing

## Workflow

### Step 1: Run the full checkup

```bash
cd web && node screenshots/test_full_checkup.mjs
```

This runs ~65 automated tests across dashboard/alerts/sim/manage/watching/api/navigation checks:

Important: trust the script's `[PASS]/[FAIL]` output as source of truth for exact section counts; keep this skill updated when `test_full_checkup.mjs` expands.

### Step 2: Review screenshots

Read each screenshot with the Read tool:

```
web/screenshots/checkup_01_dash_A.png    — Dashboard A-share tab
web/screenshots/checkup_02_dash_HK.png   — Dashboard HK tab
web/screenshots/checkup_03_alerts.png    — Alerts page
web/screenshots/checkup_04_sim.png       — Sim page
web/screenshots/checkup_05_manage.png    — Manage page
```

Check for:
- Layout breakage (columns misaligned, overlapping text)
- Missing data (empty rows, all zeros, all dashes)
- Color coding (red for loss, green for gain, orange for warnings)
- Correct tab isolation (HK tab shows only HK stocks, including hidden)

### Step 3: Report

Summarize as a table:

```markdown
| Section | Result | Issues |
|---------|--------|--------|
| Dashboard A | 7/7 PASS | none |
| Dashboard HK | 6/6 PASS | none |
| ... | ... | ... |
```

### Step 4: Feature E2E test (MANDATORY for new features)

每个新功能必须编写 `web/screenshots/test_<feature>.mjs`，覆盖完整 CRUD 流程：

```javascript
// test_<feature>.mjs 模板
import { chromium } from "playwright";

const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}: ${d || ""}`);

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// 拦截 API 验证
p.on("response", async (resp) => {
  if (resp.url().includes("/api/xxx") && resp.request().method() === "POST") {
    console.log("  API:", (await resp.text()).substring(0, 150));
  }
});

await p.goto("http://localhost:3120/xxx", { waitUntil: "networkidle" });

// ═══ 1. CREATE ═══
console.log("\n═══ 1. Create ═══");
// ... 点击、填表、提交
await p.screenshot({ path: "screenshots/<feature>_01_create.png" });
// 验证: 页面包含新创建的内容
const body = await p.textContent("body");
body.includes("xxx") ? ok("Created") : ng("Not visible");

// ═══ 2. EDIT ═══
console.log("\n═══ 2. Edit ═══");
// ... 点击 EditableCell、修改值、Enter
await p.screenshot({ path: "screenshots/<feature>_02_edit.png" });

// ═══ 3. DELETE / CLEANUP ═══
console.log("\n═══ 3. Cleanup ═══");
p.on("dialog", (d) => d.accept());
// ... 删除测试数据
await p.screenshot({ path: "screenshots/<feature>_03_cleanup.png" });

await browser.close();
```

**关键交互模式：**

| 交互 | 代码 |
|------|------|
| EditableCell 编辑 | `await p.click('[title="Click to edit"]'); await p.locator('input:focus').fill('val'); await p.keyboard.press('Enter');` |
| Select 下拉 | `await p.locator('select').first().selectOption({ index: N });` |
| Checkbox 勾选 | `await p.click('input[type=checkbox]');` |
| 确认弹窗 | `p.on("dialog", d => d.accept());` |
| 等待 API 完成 | `await p.waitForTimeout(1000);` (API POST 后) |
| 验证 toast | `(await p.textContent("body")).includes("Updated")` |

## Test Script Location

Scripts live in `web/screenshots/` (must be under `web/` for playwright module resolution).

**Commit 前必须执行：**
```bash
node screenshots/test_full_checkup.mjs        # 全站回归 (65 tests)
node screenshots/test_<feature>.mjs            # 功能专项
cd web && npx tsc --noEmit                      # TypeScript 编译
# 用 Read 工具查看截图确认 UI 无异常
```

**现有脚本：**

| 脚本 | 测试数 | 覆盖范围 | 必跑 |
|------|--------|---------|------|
| `test_full_checkup.mjs` | 65 | 全站回归（Dashboard/Alerts/Sim/Manage/Watching/API/Navigation + Watching 排序/折叠/跨 tab） | 每次 commit |
| `test_dashboard_e2e.mjs` | 55 | Dashboard 交互（折叠/排序/星标/EditableCell/FX/摘要栏） | 改 dashboard 时 |
| `test_manage_stocks_e2e.mjs` | 32 | Manage 持仓表（above/below/hide/star/promote/demote/搜索） | 改 manage 持仓时 |
| `test_plan_e2e.mjs` | ~15 | 交易计划 CRUD（创建/编辑/暂停/删除） | 改 plan 相关代码时 |
| `test_sim_alerts_e2e.mjs` | 60 | Sim+Alerts（交易计划/分页/L3切换/日报折叠/自动刷新） | 改 sim/alerts 时 |
| `test_sector_e2e.mjs` | 46 | Sector（K线弹窗/新建删除指数/星标关注/横向滚动/折叠） | 改 sector 时 |
| `test_trade_plans.mjs` | ~10 | PlanCard 渲染验证 | 改 manage 页面时 |
| `test_navigation.mjs` | 18 | 全站路由+跨页导航 | 改路由/导航时 |
| `test_alerts_page.mjs` | ~15 | Alerts 页面渲染 | 改告警相关代码时 |
| `test_sim_page.mjs` | ~15 | Sim 页面渲染 | 改模拟盘相关代码时 |

## Quick Reference

| Page | URL | Key Selectors |
|------|-----|---------------|
| Dashboard A | `/?tab=A` | Summary: `Nodes:`, `position:`. Rows: `★PROD`/` PROD`. Sort headers. |
| Dashboard HK | `/?tab=HK` | FX: `FX`/`WARN FX`/`0.92`. HK-prefixed codes. |
| Alerts | `/alerts` | Events: `[HH:MM:SS]` + `[L1-3]`. L3 toggle: `L3:N (hidden)`. |
| Sim | `/sim` | Summary: 收益率/夏普/胜率/总市值/总资产. Trade plans + 操作记录(分页). |
| Manage | `/manage` | Plans: `+ 新建计划`/`运行中`/`已暂停`. Stocks: type/code/above/below/hide. `title="Promote"/"Demote"`. |
| Sector | `/sector` | Indices matrix: 板块名+累涨+日涨跌%. K线弹窗: SVG polyline. `+ 新建`/`title="删除"`. |

## UI Selector Patterns

- **Top nav**: use visible tab text in `AppTabs` (e.g., "holdings", "watching", "alerts", "sim", "sector", "manage")
- **EditableCell**: `<span title="Click to edit">` → click → `<input>` → Enter saves
- **Row**: flex div with `whiteSpace: pre`, children `<span>` with `width: Nch`
- **PROD/DEV badge**: `span` with `width: 6ch`, text `★PROD` / ` PROD` / `  DEV`
- **Money display**: `+1.2万`, `-3,456`, `+0.5亿` — Unicode minus: `\u2212`, `\u2013`, `\uff0d`
- **量比/换手率**: Shows `-` when value is 0 (API unavailable)
- **Sector index row**: 48px height div with cyan-colored name, `getComputedStyle` for color detection
- **Sector K-line modal**: Fixed overlay `z-index: 100`, SVG `polyline`, close via Escape/×/backdrop
- **Sector star/delete**: `title="删除"` for delete, star icon `☆`/`★` toggle
- **Manage promote/demote**: `title="Promote to holding"`, `title="Demote to watching"`
- **Manage hide toggle**: `title="Hide (out of sight)"`, `title="Unhide (show on dashboard)"`
- **Manage star**: `title="Mark as L1 priority"`, `title="Remove L1 priority"`

## Parsing Helpers

Prefer using helper functions that already exist in the active test file instead of redefining stale utility snippets in this skill.

## Common Targeted Patterns

| Pattern | Approach |
|---------|----------|
| **EditableCell CRUD** | Click span → type → Enter → verify → reload → verify persistence |
| **Data consistency** | Sum row P&L vs summary P&L (tolerance ~500 for 万-rounding) |
| **FX consistency** | Extract fxRate → verify HK monetary = HKD * fxRate |
| **Error state** | `page.route()` intercept API → return error → check [ERROR] banner |
| **Tab isolation** | Switch tab → verify hidden list only contains current market stocks |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `chromium` not found | `cd web && npx playwright install chromium` |
| Timeout on goto | Dev server must run: `cd web && npm run dev` |
| `clientReferenceManifest` | `rm -rf web/.next` + restart dev server |
| Element not found | No CSS classes — use `page.evaluate()` with inline style matching |
