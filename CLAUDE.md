# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Python (Poetry)

```bash
poetry install                                          # install dependencies
poetry run python src/main.py --ticker 000000           # CLI analysis
poetry run python src/main.py --ticker 000000 --show-reasoning  # with agent reasoning
poetry run python src/main.py --ticker 000000 --summary # with summary report
poetry run python src/backtester.py --ticker 301157 --start-date 2024-12-11 --end-date 2025-01-07
poetry run python run_with_backend.py                   # FastAPI on :8000 (Swagger at /docs)
poetry run python run_with_backend.py --ticker 002848   # API server + immediate analysis
```

### Web Dashboard (Next.js)

```bash
cd web
npm run dev      # dev server on :3120
npm run build    # production build
```

Webpack is configured with in-memory cache for dev mode (`next.config.ts`). If you hit stale chunk errors, `rm -rf web/.next` and restart — but never add `rm -rf .next` as a pre-dev script (causes ENOENT race conditions).

**IMPORTANT**: Never run `next build` while the dev server is running. The build output overwrites `.next/` and causes `Cannot find module './XXX.js'` errors in the dev server. To verify code correctness, rely on the dev server's TypeScript checking instead.

## Architecture

### Multi-Agent System (LangGraph)

The core is a LangGraph `StateGraph` defined in `src/main.py`. Shared state flows through `AgentState` (`src/agents/state.py`):

```
market_data_agent → [technical, fundamentals, sentiment, valuation] (parallel)
                  → researcher_bull + researcher_bear
                  → debate_room_agent (LLM arbitration of bull/bear)
                  → risk_management_agent → macro_analyst_agent
                  → portfolio_management_agent → END

macro_news_agent runs in parallel, feeds into portfolio_management_agent
```

Each agent lives in `src/agents/<name>.py`. They read from and write to `AgentState.data` (a merge-dict) and `AgentState.messages`.

### LLM Provider Configuration

`src/utils/llm_clients.py` implements a factory with auto-fallback:
- **Priority**: OpenAI-compatible API (if `OPENAI_COMPATIBLE_*` env vars set) → Gemini API
- **Gemini fallback chain**: `gemini-1.5-flash` → `gemini-2.0-flash` → `gemini-flash-latest` → `gemini-pro-latest`
- Exponential backoff retry (5 attempts, 300s max)

Environment variables in `.env`:
```
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
OPENAI_COMPATIBLE_API_KEY=...        # optional, takes priority
OPENAI_COMPATIBLE_BASE_URL=...       # optional
OPENAI_COMPATIBLE_MODEL=...          # optional
```

### FastAPI Backend (`backend/`)

Dual data model:
- **`api_state`** (in-memory): real-time agent status, latest LLM request/response
- **`BaseLogStorage`**: extensible historical logs (currently `InMemoryLogStorage`)

Key routers: `analysis.py` (trigger analysis, async via ThreadPoolExecutor), `agents.py` (agent status), `workflow.py` (graph status), `logs.py` + `runs.py` (execution history).

All responses follow `ApiResponse<T>` schema with `success`, `message`, `data`, `timestamp`.

### Web Dashboard (`web/`)

Next.js 15 + React 19 + TypeScript. Dracula-themed terminal UI on port 3120.

**Data flow**: `src/data/monitor_config.json` → `/api/config` (read/write watchlist) and `/api/metrics` (polls 东方财富 API for live prices, enriches with above/below thresholds and settings).

**Key hooks**:
- `useCommand` — parses `svc add|update|rm|ls|config|help` commands, manages terminal log entries. Returns `addLogs` for external log injection.
- `useAlerts` — checks price breach (above/below) and big moves (|change| >= `big_move_pct`), per-stock cooldown via in-memory Map.

**`monitor_config.json` structure**:
```json
{
  "watchlist": {
    "HK09988": { "name": "...", "type": "holding", "cost": 155, "shares": 200, "above": 166, "below": 150 }
  },
  "settings": { "poll_interval": 30, "big_move_pct": 3, "cooldown_minutes": 10 }
}
```

`type: "holding"` = production (PROD), otherwise watching (DEV). `poll_interval` controls refresh rate. Interactive commands modify this file via `/api/config` POST.

### Data Sources & Tools (`src/tools/`)

- `stock_data_fetcher.py` — A-share data via akshare
- `news_crawler.py` — multi-source (新浪财经, 网易财经, 东方财富) with caching, dedup, Sina fallback
- `data_analyzer.py` — technical indicator calculations
- `openrouter_config.py` — `get_chat_completion()` LLM wrapper

**Stock data API rate limiting**: always use 1-2s delays between requests, max 5 stocks per batch. APIs throttle aggressively.

### Architecture Rules

- **Poller 是生产者，UI 是消费者，二者无耦合。** Poller (`src/tools/market_data_poller.py`) 只写行情数据到 `market_data.json`（price/change/vol/amount 等）；用户配置（type/cost/shares/hidden/above/below）只存 `monitor_config.json`，由 UI 通过 `/api/config` 读写。`/api/metrics` 负责合并两个 JSON + 计算派生字段（pnl）。任何 UI 端操作立即生效，不依赖 poller 周期。
- **All market data and FX rate fetching must happen in the Python poller script**, not in Next.js API routes. The web layer (`/api/metrics`) only reads from `market_data.json` written by the poller. This keeps the data pipeline centralized and avoids duplicate API calls from the frontend.

### HK Stock Codes

Hong Kong stocks use `HK` prefix (e.g., `HK09988`). The web metrics API strips the prefix for 东方财富 API calls and maps market code `116` for HK stocks, vs `1` (Shanghai) / `0` (Shenzhen) for A-shares. See `emMarket()` and `rawCode()` in `web/app/api/metrics/route.ts`.
