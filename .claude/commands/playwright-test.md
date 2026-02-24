---
name: playwright-test
description: Use when verifying UI changes after frontend code modifications, checking UX fixes visually, or validating data consistency between dashboard summary bar and row-level values. Symptoms include needing to confirm above/below alert editing works, error banners appear correctly, FX rate is applied to HK P&L, or any visual regression check.
---

# Playwright Screenshot & Functional Test

Write and run headless Playwright scripts to capture screenshots and assert functional correctness of the web dashboard at `http://localhost:3120`.

## When to Use

- After modifying `web/app/page.tsx`, `web/app/manage/page.tsx`, or API routes
- When verifying visual layout, data rendering, or interactive components
- When checking data consistency (summary totals vs row-level values)
- **Not for**: unit tests, API-only tests, or Python backend testing

## Workflow

1. Write ESM script at `web/screenshots/<name>.mjs`
2. Run: `cd web && node screenshots/<name>.mjs`
3. Read screenshots with the Read tool to visually verify
4. Report PASS/FAIL summary

## Quick Reference

| Page | URL | Key Selectors |
|------|-----|---------------|
| Dashboard A | `/?tab=A` | Summary: `Nodes:`, `position:`, `SH:/SZ:`. Rows: `span[style*="6ch"]` with PROD/DEV. Stale: `STALE` span. Error: `[ERROR]` div. |
| Dashboard HK | `/?tab=HK` | Same + FX rate in summary, HK-prefixed codes |
| Manage | `/manage` | EditableCell: `span[title="Click to edit"]`. Alerts: ▲/▼ indicators + `width:60px` cells. |
| Alerts | `/alerts` | Alert event list divs |

## UI Selector Patterns

- **EditableCell**: `<span title="Click to edit">` → click → becomes `<input>` → Enter saves, Escape cancels
- **Row**: flex div with `whiteSpace: pre`, children `<span>` with `width: Nch`
- **PROD badge**: `span[style*="6ch"]` containing `★PROD` or ` PROD`
- **Money display**: `+1.2万`, `-3,456`, `+0.5亿` — needs Unicode minus handling (`\u2212`, `\u2013`, `\uff0d`)

## Script Template

```javascript
import { chromium } from 'playwright';
const BASE = 'http://localhost:3120';
const DIR = '<project_root>/web/screenshots';

function parseMoney(s) {
  if (!s || s === '-') return 0;
  let t = s.trim().replace(/[¥,\s]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
  let m = 1;
  if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
  else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
  const n = parseFloat(t);
  return isNaN(n) ? NaN : n * m;
}

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  try {
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(2000);
    // ... assertions + record() ...
    await page.screenshot({ path: `${DIR}/test.png`, fullPage: true });
  } finally { await browser.close(); }
  const fail = results.filter(r => !r.ok).length;
  console.log(`\nTotal: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) process.exit(1);
}
main().catch(e => { console.error(e); process.exit(2); });
```

## Common Test Patterns

| Pattern | Approach |
|---------|----------|
| **Visual check** | Navigate → `waitForTimeout(2000)` → screenshot → Read tool |
| **EditableCell CRUD** | Click span → type value → Enter → verify → reload → verify persistence → clear → verify "-" |
| **Data consistency** | Extract summary value + sum row values → compare (tolerance ~500 for 万-rounding) |
| **Error banner** | Check absence of `[ERROR]` div in normal state; check STALE span |
| **FX consistency** | Extract `fxRate` from summary → verify HK row monetary values = HKD * fxRate |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `chromium` not found | `cd web && npx playwright install chromium` |
| Timeout on goto | Dev server must be running: `cd web && npm run dev` |
| Element not found | No CSS classes — use `page.evaluate()` with inline style matching |
| Money parse NaN | Check Unicode minus variants (`\u2212`) |
