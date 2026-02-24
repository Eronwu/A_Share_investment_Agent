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

### Review History
- 2026-02-10: Initial column/data review
- 2026-02-13: Manage page scalability review (search/filter added)
- 2026-02-24: Full UX/UI/UE professional audit (comprehensive)
