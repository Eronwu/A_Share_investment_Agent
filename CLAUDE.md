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

### Stock Notifier (`src/tools/stock_notifier.py`)

Lightweight macOS notification daemon. Reads poller output, never fetches data directly.

**Data flow**: `market_data.json` (poller) + `monitor_config.json` (config) → DeltaAlertEngine → stealth_dispatch → terminal-notifier

**启动**: `./start_monitor.sh` 一键启动 Poller + Notifier + Web，或单独运行 `poetry run python src/tools/stock_notifier.py`。修改代码后必须重启进程（kill old pid → restart）。

#### Notification Design Rules

- **通知 = 大事。** 弹窗意味着需要立刻关注，必须精简、低频、不打扰。
- **变化驱动，非状态驱动。** 使用 `DeltaAlertEngine`：记录每只股票上次通知时的价格，只在价格发生显著变化时再次通知。涨停/跌停通知一次后，价格不变就不再弹。
- **首次触发**: `|日涨跌幅| >= trigger_pct`（默认 5%）。**再次触发**: 距上次通知价变化 >= `delta_pct`（默认 4%）。
- **只通知持仓**，自选股大涨大跌不弹窗（除非设了 above/below 阈值）。
- **合并通知**: 同一轮检测的所有告警合并为 1~2 条 macOS 通知，不逐条弹。
- **内容极简**: 只显示股票名称 + 涨跌幅% + 现价。不显示盈亏金额、持仓数量等敏感数据。
- **Stealth 模式**: 通知标题伪装为 CI/监控系统（"CI Pipeline Alert"、"SRE Notification"），同事看到不会察觉是股票。
- **无声为主**: 只有严重告警（跌幅 > 8% 或触价）才有提示音，其余静默弹窗。
- **每日重置**: 午夜清除所有 delta 追踪状态，新交易日重新开始。

#### Settings (monitor_config.json → settings)

| Key | Default | Description |
|-----|---------|-------------|
| `trigger_pct` | 5 | 首次触发阈值（日涨跌幅%） |
| `delta_pct` | 4 | 再次触发阈值（距上次通知价格变化%） |
| `portfolio_delta_pct` | 2 | 组合级别 P&L 变化阈值% |
| `poll_interval` | 30 | Poller 轮询间隔（秒） |

#### Config Write Safety

`/api/config` 和 poller 都使用原子写入（tmp → rename），防止并发读到半截 JSON。修改 config 写入逻辑时必须保持此模式。

#### Hidden List

`hiddenList` 在前端不按 tab 过滤，统一显示所有 hidden 股票（跨 A股/HK tab）。避免用户 hide HK 股后在 A股 tab 看不到。
