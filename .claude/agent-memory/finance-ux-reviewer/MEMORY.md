# Finance UX Reviewer - Agent Memory

## Project: A_Share_investment_Agent Web Dashboard

### Architecture
- **Frontend**: Next.js 15 + React 19, Dracula theme, terminal disguise
- **Data pipeline**: Python poller -> JSON files -> Next.js API route -> React page
- **Config**: 4 JSON files (market_data, monitor_config, alert_config, alert_events)
- **Command system**: `web/app/utils/commandParser.ts` - svc add/update/rm/ls/config/help

### Design System
- Dracula color palette defined as `D` object in `web/app/theme.ts`
- Chinese market conventions: red=gain, green=loss (correctly implemented)
- Font: JetBrains Mono, 13px, line-height 1.55
- All inline styles; Tailwind imported but unused in globals.css
- `ch` units for column widths (leverages monospace)

### Key File Paths
- Main page: `web/app/page.tsx` (~660 lines)
- Manage: `web/app/manage/page.tsx` (~960 lines)
- Alerts: `web/app/alerts/page.tsx` (~240 lines)
- Theme: `web/app/theme.ts`
- API: `web/app/api/metrics/route.ts`, `web/app/api/config/route.ts`
- Hooks: `web/app/hooks/useCommand.ts`, `web/app/hooks/useAlerts.ts`
- Components: `web/app/components/CommandPrompt.tsx`
- Parser: `web/app/utils/commandParser.ts`

### Known Issues (2026-02-24 audit)
- Critical: cost=0 causes incorrect P&L in HoldRow (API guards cost>0, frontend doesn't)
- Critical: watching stocks cannot be hidden via Manage GUI (only CLI)
- High: No loading skeleton on Dashboard initial load
- High: Config API has read-modify-write race condition on concurrent edits
- High: Chinese header + sort arrow overflow due to char-width vs display-width in pad()
- Medium: AlertEvent interface duplicated in 3 files
- Medium: Add action overwrites existing watchlist entries without warning
- Low: TitleBar, ZshPrompt duplicated across 3 pages
- Fixed: C1 alert threshold edit, C3 fetchError/isStale, H7 HK fx rate in row P&L

### Sim Trading Page (`web/app/sim/page.tsx`, ~1035 lines)
- API route: `web/app/api/sim/route.ts` (reads sim_trading.db via better-sqlite3)
- Live data polls every 5s, no freshness indicator
- Summary bar: 10 KPIs at equal visual weight (needs tiered hierarchy)
- SVG equity curve: fixed 760px width, label overlap >17 data points
- SIM badge repeated redundantly on every position row
- Trade history: no sort/filter, only shows exit date (not entry+hold duration)
- Distance-to-stop-loss not computed/displayed despite data being available
- NavBar and LiveSummaryBar show overlapping P&L info (different definitions)

### Review History
- 2026-02-10: Initial column/data review
- 2026-02-13: Manage page scalability review (search/filter added)
- 2026-02-24: Full UX/UI/UE professional audit (comprehensive)
- 2026-02-25: Sim trading page (/sim) detailed UX review
