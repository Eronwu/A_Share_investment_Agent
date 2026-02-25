---
name: playwright-test
description: Use when verifying UI changes after frontend code modifications, checking UX fixes visually, or validating data consistency between dashboard summary bar and row-level values.
user_invocable: true
---

# Playwright Full Checkup & Functional Test

## Overview

Run the automated 49-test checkup suite against the web dashboard at `http://localhost:3120`, then visually inspect screenshots and report findings. Optionally write targeted tests for specific changes.

## When to Use

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

This runs ~49 automated tests across 10 sections:

| # | Section | Tests | Checks |
|---|---------|-------|--------|
| 1 | Dashboard A tab | 7 | ERROR banner, PROD rows, summary bar, 量比 no "0.00", 股数 column+values, alert log |
| 2 | Dashboard HK tab | 6 | ERROR banner, PROD rows, HK codes, FX rate, hidden isolation, 药明康德 negative cost |
| 3 | Tab switching | 2 | A→HK and HK→A URL updates via click |
| 4 | Alerts page | 4 | ERROR banner, event count, path ~/projects/alerts, event rows |
| 5 | Sim page | 8 | ERROR banner, 收益率/夏普/胜率/总市值/总资产/股数, positions, trades |
| 6 | Manage page | 3 | ERROR banner, table headers, PROD/DEV rows |
| 7 | API health | 2 | /api/metrics (services+events), /api/sim (trades+positions) |
| 8 | Data consistency | 10 | Holdings have cost+shares, no NaN, alert events valid, new/sold stocks |
| 9 | Navigation | 5 | All pages 200, invalid path 404 |
| 10 | Console errors | 1 | No JS runtime errors |

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

### Step 4 (optional): Targeted tests

If specific verification is needed beyond the checkup, write a focused script at `web/screenshots/test_<name>.mjs` and run it.

## Test Script Location

Scripts live in `web/screenshots/` (must be under `web/` for playwright module resolution):

- **Full checkup**: `web/screenshots/test_full_checkup.mjs` (always run this first)
- **Alerts**: `web/screenshots/test_alerts_page.mjs`
- **Navigation**: `web/screenshots/test_navigation.mjs`
- **Sim**: `web/screenshots/test_sim_page.mjs`
- **Targeted tests**: `web/screenshots/test_<name>.mjs`

## Quick Reference

| Page | URL | Key Selectors |
|------|-----|---------------|
| Dashboard A | `/?tab=A` | Summary: `Nodes:`, `position:`. Rows: `★PROD`/` PROD`. |
| Dashboard HK | `/?tab=HK` | FX: `FX`/`WARN FX`/`0.92`. HK-prefixed codes. |
| Alerts | `/alerts` | Path: `~/projects/alerts`. Events: `[HH:MM:SS]` + `[L1-3]`. |
| Sim | `/sim` | Summary: 收益率/夏普/胜率/总市值/总资产. Positions: `SIM` badge. |
| Manage | `/manage` | Headers: type/code/name/cost/shares/above/below/hide. |

## UI Selector Patterns

- **Tab bar**: `div[style*="cursor:pointer"][style*="minWidth:130px"]` containing "A-share" or "HK"
- **EditableCell**: `<span title="Click to edit">` → click → `<input>` → Enter saves
- **Row**: flex div with `whiteSpace: pre`, children `<span>` with `width: Nch`
- **PROD/DEV badge**: `span` with `width: 6ch`, text `★PROD` / ` PROD` / `  DEV`
- **Money display**: `+1.2万`, `-3,456`, `+0.5亿` — Unicode minus: `\u2212`, `\u2013`, `\uff0d`
- **量比/换手率**: Shows `-` when value is 0 (API unavailable)

## parseMoney Helper

```javascript
function parseMoney(s) {
  if (!s || s === '-') return NaN;
  let t = s.trim().replace(/[¥,\s]/g, '').replace(/[\u2212\u2013\uff0d]/g, '-');
  let m = 1;
  if (t.includes('亿')) { m = 1e8; t = t.replace('亿', ''); }
  else if (t.includes('万')) { m = 1e4; t = t.replace('万', ''); }
  return parseFloat(t) * m;
}
```

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
