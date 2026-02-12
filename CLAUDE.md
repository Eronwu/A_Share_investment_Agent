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

**URL routing**: `/?tab=A`（A 股）、`/?tab=HK`（港股）。无参数时按时间自动选：15:00 前默认 A 股，15:00 后默认港股。Tab 切换同步更新 URL，刷新保持状态。

**Data flow**: 四文件分离，`/api/metrics` 负责合并。

**Key hooks**:
- `useCommand` — parses `svc add|update|rm|hide|unhide|ls|config|help` commands, manages terminal log entries. Returns `addLogs` for external log injection.
- `useAlerts` — 读取 notifier 写入的 `alert_events.json` 展示在 web 日志区，不做任何告警计算（纯消费者）。用 `display` 字段展示中文详细格式。

**四文件职责分离**:

| 文件 | 写入方 | 内容 |
|------|--------|------|
| `market_data.json` | Poller (Python) | 个股行情 + 两市成交额 (marketTurnover) + 汇率 |
| `monitor_config.json` | UI (/api/config) | 持仓配置 (name/type/cost/shares/hidden) |
| `alert_config.json` | UI (/api/config) | 告警规则 (above/below，按股票代码索引) |
| `alert_events.json` | Notifier (Python) | 告警事件流 (message/display 双格式) |

`/api/metrics` 合并四者 + 计算 pnl，任何 UI 操作立即生效，不依赖 poller 周期。

**`alert_events.json`** — Notifier 写入的告警事件流（运行时数据，不入库）。每条事件含两种格式：`message`（stealth 简短，terminal 通知用）和 `display`（中文详细，web 日志展示用）。同一数据源，两端各取所需，terminal 清掉后可在 web 追溯。

**`monitor_config.json` structure** (不含 above/below):
```json
{
  "watchlist": {
    "HK09988": { "name": "...", "type": "holding", "cost": 155, "shares": 200 }
  },
  "settings": { "poll_interval": 30, "big_move_pct": 3, "cooldown_minutes": 10 }
}
```

**`alert_config.json` structure** (独立告警规则):
```json
{
  "alerts": {
    "HK09988": { "above": 166, "below": 150 },
    "688676": { "above": 100, "below": 85 }
  }
}
```

`type: "holding"` = production (PROD), otherwise watching (DEV). `poll_interval` controls refresh rate. Interactive commands modify config via `/api/config` POST.

### Data Sources & Tools (`src/tools/`)

- `stock_data_fetcher.py` — A-share data via akshare
- `news_crawler.py` — multi-source (新浪财经, 网易财经, 东方财富) with caching, dedup, Sina fallback
- `data_analyzer.py` — technical indicator calculations
- `openrouter_config.py` — `get_chat_completion()` LLM wrapper

**Stock data API rate limiting**: always use 1-2s delays between requests, max 5 stocks per batch. APIs throttle aggressively.

### Architecture Rules

- **Poller 是生产者，UI 是消费者，二者无耦合。** Poller (`src/tools/market_data_poller.py`) 只写行情数据到 `market_data.json`（price/change/vol/amount 等）；用户配置（type/cost/shares/hidden）只存 `monitor_config.json`；告警规则（above/below）独立存 `alert_config.json`。`/api/metrics` 负责合并四个 JSON + 计算派生字段（pnl）。任何 UI 端操作立即生效，不依赖 poller 周期。
- **All market data and FX rate fetching must happen in the Python poller script**, not in Next.js API routes. The web layer (`/api/metrics`) only reads from `market_data.json` written by the poller. This keeps the data pipeline centralized and avoids duplicate API calls from the frontend.
- **告警规则与持仓配置分离。** `above`/`below` 阈值存在 `alert_config.json`，不存在 `monitor_config.json` 的 watchlist 条目里。所有读写告警的代码（web API、CLI、notifier）统一从 `alert_config.json` 操作。删除股票时同步清理两个文件。
- **告警计算单一数据源。** Notifier (`stock_notifier.py` DeltaAlertEngine) 是唯一的告警计算引擎，产出写入 `alert_events.json`。Web 前端 (`useAlerts`) 只读取展示，不做任何告警计算。确保 terminal 弹窗和 web 日志完全一致，不重复计算，不重复告警。

### HK Stock Codes

Hong Kong stocks use `HK` prefix (e.g., `HK09988`). The web metrics API strips the prefix for 东方财富 API calls and maps market code `116` for HK stocks, vs `1` (Shanghai) / `0` (Shenzhen) for A-shares. See `emMarket()` and `rawCode()` in `web/app/api/metrics/route.ts`.

### Stock Notifier (`src/tools/stock_notifier.py`)

Lightweight macOS notification daemon. Reads poller output, never fetches data directly.

**Data flow**: `market_data.json` (poller) + `monitor_config.json` (config) + `alert_config.json` (thresholds) → DeltaAlertEngine → stealth_dispatch → terminal-notifier + `alert_events.json` → web

**启动**: `./start_monitor.sh` 一键启动 Poller + Notifier + Web，或单独运行 `poetry run python src/tools/stock_notifier.py`。修改代码后必须重启进程（kill old pid → restart）。

#### Tiered Notification (L1-L4)

分级通知，策略表驱动（`NOTIFY_POLICIES`），由 `star` + `type` + `hidden` 推导级别：

| Level | 匹配规则 | trigger | delta | cooldown | 通知方式 |
|-------|---------|---------|-------|----------|---------|
| L1 ★ | `star=true` | 4% | 3% | 5min | 弹窗+声音 |
| L2 | `holding & !star & !hidden` | 6% | 5% | 15min | 弹窗静默 |
| L3 | `watching & !star & !hidden` | 仅threshold | 仅threshold | 30min | 仅web日志 |
| L4 | `hidden` 或不在watchlist | - | - | - | 不通知 |

#### Notification Design Rules

- **通知 = 大事。** 弹窗意味着需要立刻关注，必须精简、低频、不打扰。
- **变化驱动，非状态驱动。** 使用 `DeltaAlertEngine`：记录每只股票上次通知时的价格，只在价格发生显著变化时再次通知。涨停/跌停通知一次后，价格不变就不再弹。
- **首次触发**: `|日涨跌幅| >= trigger_pct`（默认 5%）。**再次触发**: 距上次通知价变化 >= `delta_pct`（默认 4%）。
- **只通知持仓**，自选股大涨大跌不弹窗（除非设了 above/below 阈值）。
- **合并通知**: 同一轮检测的所有告警合并为 1~2 条 macOS 通知，不逐条弹。
- **内容极简**: 只显示股票名称 + 涨跌幅% + 现价。不显示盈亏金额、持仓数量等敏感数据。
- **Stealth 模式**: 通知标题伪装为 CI/监控系统（"CI Pipeline Alert"、"SRE Notification"），同事看到不会察觉是股票。
- **无声为主**: 只有严重告警（跌幅 > 8% 或触价）才有提示音，其余静默弹窗。
- **每日重置**: 每天 8:00 清除所有 delta 追踪状态 + 清空 `alert_events.json`，新交易日重新开始。

#### Settings (monitor_config.json → settings)

| Key | Default | Description |
|-----|---------|-------------|
| `l1_trigger_pct` | 4 | L1 首次触发阈值% |
| `l1_delta_pct` | 3 | L1 再次触发阈值% |
| `l1_cooldown_min` | 5 | L1 冷却时间（分钟） |
| `l2_trigger_pct` | 6 | L2 首次触发阈值% |
| `l2_delta_pct` | 5 | L2 再次触发阈值% |
| `l2_cooldown_min` | 15 | L2 冷却时间（分钟） |
| `l3_cooldown_min` | 30 | L3 冷却时间（分钟） |
| `portfolio_delta_pct` | 2 | 组合级别 P&L 变化阈值% |
| `poll_interval` | 30 | Poller 轮询间隔（秒） |

#### Config Write Safety

`/api/config` 和 poller 都使用原子写入（tmp → rename），防止并发读到半截 JSON。修改 config 写入逻辑时必须保持此模式。

#### Hidden List

`hiddenList` 在前端不按 tab 过滤，统一显示所有 hidden 股票（跨 A股/HK tab）。避免用户 hide HK 股后在 A股 tab 看不到。
