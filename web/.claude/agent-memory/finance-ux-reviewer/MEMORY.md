# Finance UX Reviewer - Project Memory

## Project Stack
- Next.js 15 + React 19 + TypeScript, port 3120
- Dracula theme (`web/app/theme.ts`), inline styles throughout (no Tailwind/CSS modules)
- Monospace font: JetBrains Mono
- All layout uses `ch` units for column widths, `display: flex` with `whiteSpace: "pre"`

## Color Convention (Chinese/HK Market)
- `D.red` (#ff5555) = gain/positive (Chinese convention)
- `D.green` (#50fa7b) = loss/negative (Chinese convention)
- `D.orange` (#ffb86c) = neutral highlights, stop loss, commission, SIM/PROD badges
- `D.pink` (#ff79c6) = table headers
- `D.comment` (#6272a4) = labels, secondary text
- `D.purple` (#bd93f9) = section markers, active nav

## Key Pages
- `/` (page.tsx) - Main monitor dashboard with A-share/HK tabs
- `/sim` (sim/page.tsx) - Simulated trading dashboard
- `/alerts` - Alert log viewer
- `/manage` - Config management (editable cells)

## Recurring UX Patterns Found
- Summary bars use flat flex-wrap with equal-weight KPIs (needs tiered hierarchy)
- Tables lack sorting/filtering (all client-side render of full dataset)
- No data freshness indicators despite 5s polling
- SVG equity charts use fixed width (760px), no responsive viewBox
- Collapsible sections use purple triangle markers
- Section headers follow pattern: `# -- Title (count) --`

## Data Architecture
- 4-file separation: market_data.json, monitor_config.json, alert_config.json, alert_events.json
- `/api/metrics` merges all 4 for main page
- `/api/sim` reads sim_trading.db (SQLite, better-sqlite3, readonly)
- Python poller is sole data producer; web is pure consumer

## Anti-Patterns Noted
- Duplicate information between NavBar and SummaryBar (P&L shown in both)
- Numbers formatted inconsistently (toFixed vs toLocaleString)
- SIM badge repeated on every row in a page entirely about SIM trading
- Trade table shows only exit date, not entry date + hold duration
- Equity curve labels overlap when data.length > ~17 points
- No keyboard navigation or shortcuts despite terminal aesthetic
