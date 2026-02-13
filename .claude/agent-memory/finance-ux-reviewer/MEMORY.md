# Finance UX Reviewer - Agent Memory

## Project: A_Share_investment_Agent Web Dashboard

### Architecture
- **Frontend**: Next.js 15 + React 19, Dracula theme, terminal disguise
- **Data pipeline**: Python poller (`src/tools/market_data_poller.py`) -> JSON file (`src/data/market_data.json`) -> Next.js API route (`web/app/api/metrics/route.ts`) -> React page
- **Data source**: EastMoney push2 API (`fetch_realtime_eastmoney` in `src/tools/stock_monitor.py`)
- **Config**: `src/data/monitor_config.json` - watchlist with holdings/watching types
- **Command system**: `web/app/utils/commandParser.ts` - svc add/update/rm/ls/config/help

### Design System
- Dracula color palette defined as `D` object in `web/app/page.tsx` line 40-52
- Chinese market conventions: red=gain, green=loss (correctly implemented in `chgColor` function)
- Font: JetBrains Mono, 13px, line-height 1.55
- All inline styles (no CSS modules); Tailwind imported but unused in globals.css
- Duplicated `D` color object in both page.tsx and CommandPrompt.tsx

### Current Data Fields Available from API
From EastMoney: f2(price), f3(pct), f4(change), f5(volume), f6(amount), f7(amplitude), f8(turnover), f10(vol_ratio), f12(code), f14(name), f15(high), f16(low), f17(open), f18(prev_close)

### Key File Paths
- Main page: `web/app/page.tsx`
- API route: `web/app/api/metrics/route.ts` (uses readFileSync - blocking)
- Alerts hook: `web/app/hooks/useAlerts.ts`
- Command hook: `web/app/hooks/useCommand.ts`
- Command prompt: `web/app/components/CommandPrompt.tsx`
- Command parser: `web/app/utils/commandParser.ts`
- Poller: `src/tools/market_data_poller.py`
- Stock monitor (API calls): `src/tools/stock_monitor.py`
- Global CSS: `web/app/globals.css`
- Layout: `web/app/layout.tsx`

### Watchlist Profile (as of 2026-02)
- ~24 stocks total: 13 HK holdings, 1 A-share holding, 11 A-share watching
- HK stocks heavily weighted in holdings
- Holdings have cost basis, shares, above/below thresholds

### Identified UX Patterns
- Column sorting via clickable headers (toggleSort function)
- Conditional columns: COST/P&L/SHARES/TODAY only show when holdings exist
- Volume ratio color coding: >=1.5 red, <=0.5 dim
- Turnover rate color coding: >=5 red
- Summary stats bar above table with aggregate metrics
- HKD/CNY FX rate fetched from EastMoney, fallback 0.92

### Known Bugs (from 2026-02-10 review)
- Line 296 page.tsx: dead ternary `s.cost != null ? D.comment : D.comment`
- TODAY column header not sortable (missing onClick handler)
- P&L header ambiguous (shows % but header says "P&L")
- Silent error swallowing in fetchData catch block (line 194)
- VOL column toLocaleString inside pad() causes alignment issues

### Key Review Findings
**Dashboard page** (see ux-review-findings.md for details):
- Column order doesn't match trader scanning priority
- No currency/market indicator per row (HKD vs CNY mixed)
- Summary bar lacks total cost basis and return %
- Watchlist rows waste space showing dash columns
- Alerts lack priority levels and browser notifications
- NAME column width too narrow for CJK names with suffixes

**Manage page** (70+ stocks scalability review — 2026-02-13):
- **CRITICAL**: No search/filter — finding 1 stock in 70+ requires scrolling entire page
- **HIGH**: Settings at top waste space; users scroll past unused controls
- **HIGH**: No A-share vs HK grouping; confusing with mixed market codes
- **MEDIUM**: No sticky table headers when editing deep rows (edit error risk)
- **MEDIUM**: No batch operations (hide/star multiple stocks at once)
- **MEDIUM**: Settings/add form at bottom requires scrolling; asymmetric UX
- Recommendations: Filter bar + Collapse sections + Sticky headers + Market grouping (5h, 70% ROI)
- See `/manage-page-review.md` and `/MANAGE_PAGE_SUMMARY.md` for detailed specs and prototype

### Manage Page (web/app/manage/page.tsx)
- **Location**: `/web/app/manage/page.tsx` (867 lines)
- **Current UX**: Linear scroll with settings → holdings → watching → add form
- **Data structure**: `WatchEntry` with type/cost/shares/hidden/star fields
- **API**: `/api/config` POST — add/update/remove/settings
- **Styling**: Dracula theme, inline styles, JetBrains Mono 13px
- **Components**: TitleBar, SectionHeader, ColHeader, StockRow, EditableCell, Toast
- **Scalability issue**: 70 holdings + 10 watching = 80 rows × 30px = ~2400px scroll height
