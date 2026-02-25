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
poetry run python -m src.sim_trading.replay_runner       # sim trading replay (writes to sim_trading.db)
poetry run pytest src/sim_trading/test_sim_trading.py -v # sim trading tests (54 tests)
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

**摘要栏按 tab 独立统计**：Nodes/holdings(+N hidden)/up/down/throughput/avg_delta/P&L 全部按当前 tab 计算。A 股 tab 显示两市指数+成交额（SH/SZ/vol），HK tab 显示 FX 汇率。港股 P&L（行级和汇总级）自动乘汇率转 CNY。当 FX 不可用时显示黄色 `[WARN FX unavailable]` banner。

**错误处理**: Dashboard 和 Alerts 页面都有 `fetchError` state。API 返回 `{ error: "..." }` 时保留旧数据、显示红色 `[ERROR]` banner、触发 STALE 标记。`/api/metrics` catch 块返回 `{ ...EMPTY, error: String(e) }`，并校验 `services` 必须是数组。

**Loading 状态**: Dashboard 首次加载时显示 `info Loading metrics...`（终端风格），不渲染空表格。

**Data flow**: 三 JSON + SQLite alert_events 分离，`/api/metrics` 负责合并。

**Key hooks**:
- `useCommand` — parses `svc add|update|rm|hide|unhide|star|unstar|ls|config|help` commands, manages terminal log entries. Returns `addLogs` for external log injection.
- `useAlerts` — 读取 notifier 写入 SQLite `alert_events` 表的数据（经 `/api/metrics` 返回），展示在 web 日志区，不做任何告警计算（纯消费者）。用 `display` 字段展示中文详细格式。

**模拟盘页面 (`/sim`)**:
- `/api/sim` 读 `sim_trading.db`（better-sqlite3，只读），TS 端计算 Sharpe/MaxDD/归因
- 摘要栏: 收益率/夏普/胜率/最大回撤/盈亏比/净值/交易笔数/手续费
- 净值曲线: 内联 SVG 折线图 (760×130)，<2 个数据点时显示文字
- 模拟持仓: 与主页 `HoldRow` 风格一致 (SIM 类型标签，代码/现价/成本/盈亏%/市值/浮盈/止损/止盈)
- 交易记录: 可折叠表格，退出原因和策略名中文翻译
- 归因面板: 按策略 + 按股票，PnL 降序

**管理页面 (`/manage`)**:
- 列标题行: `type | code | name | cost | shares | ▲ above | ▼ below | ★ | hide`
- `above`/`below` 告警阈值通过 `EditableCell` 内联编辑，保存到 `alert_config.json`（适用于 holding 和 watching）
- `hide` 开关对 holding 和 watching 类型都可用（与 CLI `svc hide` 一致）
- Promote (watching→holding) 必须填写 cost 和 shares 才能 Confirm
- Demote (holding→watching) 有 `confirm()` 确认弹窗

**数据职责分离**:

| 存储 | 写入方 | 内容 |
|------|--------|------|
| `market_data.json` | Poller (Python) | 个股行情 + 两市成交额 (marketTurnover) + 汇率 |
| `monitor_config.json` | UI (/api/config) | 持仓配置 (name/type/cost/shares/hidden) |
| `alert_config.json` | UI (/api/config) | 告警规则 (above/below，按股票代码索引) |
| `sim_trading.db` → `alert_events` | Notifier (Python) | 告警事件流 (message/display 双格式) |

`/api/metrics` 合并三 JSON + SQLite alert_events + 计算 pnl，任何 UI 操作立即生效，不依赖 poller 周期。

**`alert_events` 表（`sim_trading.db`）** — Notifier 写入的告警事件，存储在 SQLite 中（原 `alert_events.json` 已迁移）。每条事件含两种格式：`message`（stealth 简短，terminal 通知用）和 `display`（中文详细，web 日志展示用）。`INSERT OR IGNORE` + `UNIQUE(ts, symbol, message)` 零成本去重。date 索引支持历史查询。30 天自动清理。

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

### Simulated Trading (`src/sim_trading/`)

模拟交易系统：消费 L2 信号，生成虚拟交易，计算绩效指标。

**数据流**: `l2_strategy_signals.json` → `signal_archiver` → `sim_trading.db` → `replay_runner` → trades/daily_pnl → `/api/sim` → `/sim` 页面

**模块**:

| 模块 | 职责 |
|------|------|
| `signal_archiver.py` | 实时归档 L2 信号 + 30s 价格快照到 SQLite |
| `signal_mapper.py` | 4 层信号规则引擎 (Tier1 独立→Tier2 增强→Tier3 纠偏→Tier4 仅日志) |
| `position_manager.py` | 虚拟持仓管理 (lot-size 对齐, SL/TP/max-hold 退出) |
| `simulation_engine.py` | HK 交易成本 (佣金+印花税+交易费+结算费) + 流动性滑点 |
| `trade_analyzer.py` | 绩效分析: 胜率/Sharpe/最大回撤/Calmar/归因 |
| `replay_runner.py` | 历史回放入口 |

**信号规则 (`src/data/signal_rules.json`)**:
- `tiers.1_independent`: 14 种独立信号 → BUY/SELL 决策 (composite_bullish, momentum_alert, MACD 金叉等)
- `tiers.2_enhance`: tick_persistence (+0.15 boost), large_order (+0.10 boost)
- `tiers.3_correction`: 6 种纠偏信号 (large_order_reversal→SELL, volume_price_divergence→TIGHTEN_SL)
- `tiers.4_log_only`: 11 种仅记录信号 (tick_imbalance, order_book_imbalance 等)
- `risk_control`: min_confidence=0.60, max_single_stock=25%, max_total_invested=80%, 同股同日多空冲突取消
- `cost_model`: HK 市场费率 (佣金 0.03% min 3 HKD, 印花税 0.13%, 交易费 0.00565%, 结算费 0.002%)
- `lot_sizes`: 每只 HK 股的每手股数

**运行回放**:
```bash
poetry run python -m src.sim_trading.replay_runner                    # 默认 v1_baseline
poetry run python -m src.sim_trading.replay_runner --version v2_test  # 指定参数版本
```

**测试**: `poetry run pytest src/sim_trading/test_sim_trading.py -v` (54 tests)

**SQLite 数据库 (`src/data/sim_trading.db`)**:
- `signals`: 归档的 L2 信号 (strategy, code, direction, price_at_signal)
- `price_snapshots`: 30s 粒度价格快照
- `trades`: 已平仓交易 (entry/exit price, pnl, exit_reason, entry strategy)
- `daily_pnl`: 每日权益快照 (equity, cash, invested, positions_json)
- `param_versions`: 参数版本配置
- `alert_events`: 告警事件 (ts, date, symbol, kind, level, message, display, change_pct)

**Web 页面 (`/sim`)**:
- `/api/sim` 路由: 用 `better-sqlite3` 读 SQLite，TS 端计算 summary/归因
- 页面布局: 摘要栏(收益率/夏普/胜率/回撤/盈亏比) → 净值曲线(SVG) → 模拟持仓(与主页 HoldRow 风格一致) → 交易记录 → 策略归因 + 股票归因
- 全中文标签，Dracula 终端风格

### Data Sources & Tools (`src/tools/`)

- `stock_data_fetcher.py` — A-share data via akshare
- `news_crawler.py` — multi-source (新浪财经, 网易财经, 东方财富) with caching, dedup, Sina fallback
- `data_analyzer.py` — technical indicator calculations
- `openrouter_config.py` — `get_chat_completion()` LLM wrapper

**Stock data API rate limiting**: always use 1-2s delays between requests, max 5 stocks per batch. APIs throttle aggressively.

### Architecture Rules

- **Poller 是生产者，UI 是消费者，二者无耦合。** Poller (`src/tools/market_data_poller.py`) 只写行情数据到 `market_data.json`（price/change/vol/amount 等）；用户配置（type/cost/shares/hidden）只存 `monitor_config.json`；告警规则（above/below）独立存 `alert_config.json`；告警事件存 `sim_trading.db` 的 `alert_events` 表。`/api/metrics` 负责合并三 JSON + SQLite alert_events + 计算派生字段（pnl）。任何 UI 端操作立即生效，不依赖 poller 周期。
- **All market data and FX rate fetching must happen in the Python poller script**, not in Next.js API routes. The web layer (`/api/metrics`) only reads from `market_data.json` written by the poller. This keeps the data pipeline centralized and avoids duplicate API calls from the frontend.
- **告警规则与持仓配置分离。** `above`/`below` 阈值存在 `alert_config.json`，不存在 `monitor_config.json` 的 watchlist 条目里。所有读写告警的代码（web API、CLI、notifier）统一从 `alert_config.json` 操作。删除股票时同步清理两个文件。
- **告警计算单一数据源。** Notifier (`stock_notifier.py` DeltaAlertEngine) 是唯一的告警计算引擎，产出写入 `sim_trading.db` 的 `alert_events` 表。Web 前端 (`useAlerts`) 只读取展示，不做任何告警计算。确保 terminal 弹窗和 web 日志完全一致，不重复计算，不重复告警。

### HK Stock Codes

Hong Kong stocks use `HK` prefix (e.g., `HK09988`). The web metrics API strips the prefix for 东方财富 API calls and maps market code `116` for HK stocks, vs `1` (Shanghai) / `0` (Shenzhen) for A-shares. See `emMarket()` and `rawCode()` in `web/app/api/metrics/route.ts`.

**HK P&L FX 转换**: 行级和汇总级都乘 `fxRate`（来自 poller 的 `hkdCnyRate`，fallback 0.92）。`HoldRow` 中 `rowFx = s.id.startsWith("HK") ? fxRate : 1` 应用于 `mktVal`、`totalPnlRaw`、`dayPnl`。百分比字段（`pnl%`、`change%`）不转换。

**P&L 守护**: 前端 `totalPnlRaw` 计算需要 `s.cost > 0`（不仅 `!= null`），与 API 端 `pnl` 计算的守护条件一致。cost=0 的 holding 显示 `-` 而非无意义大数。

### Stock Notifier (`src/tools/stock_notifier.py`)

Lightweight macOS notification daemon. Reads poller output, never fetches data directly.

**Data flow**: `market_data.json` (poller) + `monitor_config.json` (config) + `alert_config.json` (thresholds) → DeltaAlertEngine → stealth_dispatch → terminal-notifier + `sim_trading.db:alert_events` → web

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
- **每日重置**: 每天 8:00 清除所有 delta 追踪状态，新交易日重新开始。alert_events 保留在 SQLite 中（按 date 索引，30 天自动清理）。

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

#### Playwright Testing

UI 修改后使用 `/playwright-test` skill 验证。脚本存放在 `web/screenshots/`，截图输出到子目录。关键测试模式：
- **Visual**: 截图对比（loading 态、错误态、正常态）
- **EditableCell CRUD**: 点击→输入→Enter→reload 验证持久化
- **Data consistency**: summary 汇总 vs 行级求和（tolerance ~500 for 万-level rounding）
- **Route intercept**: `page.route()` 模拟 API 失败/延迟，验证 error banner 和 loading 状态

#### Hidden List

`hiddenList` 按当前 market tab 过滤（与 prod/stage 分组一致）。A 股 tab 只显示 A 股 hidden，HK tab 只显示港股 hidden。
