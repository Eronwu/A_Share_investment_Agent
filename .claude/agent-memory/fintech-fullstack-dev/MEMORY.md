# Agent Memory - Fintech Fullstack Dev

## Architecture Summary
- **Data pipeline**: Python poller (`src/tools/market_data_poller.py`) -> JSON file (`src/data/market_data.json`) -> Next.js API (`web/app/api/metrics/route.ts`) -> React frontend
- **Config**: `src/data/monitor_config.json` -- read/written by both Python and Next.js API
- **Market data source**: 东方财富 push2 API with token `fa5fd1943c7b386f172d6893dbfba10b`
- **Stock codes**: A-shares = 6-digit, HK = `HK` + 5-digit. `emMarket()` maps: 6xxx->1(SH), else->0(SZ), HK->116

## Known Issues (as of 2026-02-10)
- Stats bar up/down colors are swapped (Western convention instead of Chinese)
- Mixed currency P&L summation (RMB + HKD without FX conversion)
- No data staleness indicator when poller dies
- Dracula palette `D` object copy-pasted 4x (page.tsx, CommandPrompt.tsx, manage/page.tsx, globals.css)
- `Row` component defined inside `Home()` -- re-created every render
- Silent error swallowing in `fetchData` catch block
- No trading hours awareness in web frontend (Python CLI has `is_trading_hours()`)
- Stock code regex allows bare 5-digit codes which are invalid A-share codes

## API Quirks
- 东方财富 fields: f2=price, f3=pct, f4=change, f5=vol, f6=amount, f7=amp, f8=turnover, f10=vol_ratio, f12=code, f14=name, f15=high, f16=low, f17=open, f18=prevClose
- Poller uses atomic file write (write .tmp then rename) for safe concurrent reads
- `readFileSync` used in Next.js API routes (blocking but acceptable for single-user)

## Trading Hours Reference
- A-shares: 9:30-11:30, 13:00-15:00 (Beijing)
- HK: 9:30-12:00, 13:00-16:00 (HK time = Beijing time)
- HK pre-market auction: 9:00-9:30

## File Locations
- Monitor page: `web/app/page.tsx`
- Manage page: `web/app/manage/page.tsx`
- Metrics API: `web/app/api/metrics/route.ts` (reads from JSON file)
- Config API: `web/app/api/config/route.ts` (CRUD watchlist)
- Command hook: `web/app/hooks/useCommand.ts`
- Alert hook: `web/app/hooks/useAlerts.ts`
- Command parser: `web/app/utils/commandParser.ts`
- Python poller: `src/tools/market_data_poller.py`
- Python stock monitor (CLI): `src/tools/stock_monitor.py`
- Watchlist config: `src/data/monitor_config.json`
