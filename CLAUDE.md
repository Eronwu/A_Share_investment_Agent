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
poetry run pytest src/sim_trading/test_sim_trading.py -v # sim trading tests (62 tests)
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

**页面结构**: Holdings (`/`) 和 Watching (`/watching`) 分离，各自支持 `?tab=A` / `?tab=HK` 市场切换。无参数时按时间自动选：15:00 前默认 A 股，15:00 后默认港股。Tab 切换同步更新 URL，刷新保持状态。

| 页面 | URL | 数据范围 | 功能 |
|------|-----|---------|------|
| Holdings | `/` | `type=holding` (PROD) | 持仓 P&L、成本、股数、日盈亏 |
| Watching | `/watching` | `type!=holding` (DEV) | 自选行情、涨跌、成交额 |
| Alerts | `/alerts` | 全部 | 告警事件流 |
| Sim | `/sim` | 全部 | 模拟交易持仓+绩效 |
| Sector | `/sector` | 全部 | 自定义板块指数+主线告警 |
| Manage | `/manage` | 全部 | 交易计划+持仓管理 |

两页共享同一个 `/api/metrics` 数据源（返回全量 `services[]`），客户端按 `s.type` 过滤。

**共享组件** (`web/app/components/`):
- `AppTitleBar` — macOS 风格标题栏（红黄绿圆点 + 居中标题），全 6 页使用
- `AppTabs` — 全站统一顶部导航 tab 栏（holdings/watching/alerts/sim/sector/manage），active tab 紫色上边框
- `MarketSwitch` — `A-share/HK` 切换按钮，用于 holdings + watching 页面，15:00 时间自动切换

**摘要栏按 market tab 独立统计**：Nodes/holdings(+N hidden)/up/down/throughput/avg_delta/P&L 全部按当前 tab 计算。A 股 tab 显示两市指数+成交额（SH/SZ/vol），HK tab 显示 FX 汇率。港股 P&L（行级和汇总级）自动乘汇率转 CNY。当 FX 不可用时显示黄色 `[WARN FX unavailable]` banner。

**错误处理**: Dashboard 和 Alerts 页面都有 `fetchError` state。API 返回 `{ error: "..." }` 时保留旧数据、显示红色 `[ERROR]` banner、触发 STALE 标记。`/api/metrics` catch 块返回 `{ ...EMPTY, error: String(e) }`，并校验 `services` 必须是数组。

**Loading 状态**: Dashboard 首次加载时显示 `info Loading metrics...`（终端风格），不渲染空表格。

**Data flow**: `market_data.json` + `monitor_config`(DB 或 JSON) + `alert_config.json` + SQLite alert_events 分离，`/api/metrics` 负责合并。

**Key hooks**:
- `useCommand` — parses `svc add|update|rm|hide|unhide|star|unstar|ls|config|help` commands, manages terminal log entries. Returns `addLogs` for external log injection.
- `useAlerts` — 读取 notifier 写入 SQLite `alert_events` 表的数据（经 `/api/metrics` 返回），展示在 web 日志区，不做任何告警计算（纯消费者）。用 `display` 字段展示中文详细格式。

**Alerts 页面 (`/alerts`)**:
- 列布局: `时间 | 级别 | 信号名 | 代码 | 价格 | 涨跌% | 名称+详情`
- `parseAlert()` 从 `display` 文本中解析出结构化字段（信号名、股票名、代码、价格、详情），避免重复显示
- 信号名列显示具体策略（`MACD底背离`、`均线多排`、`空头信号(分=5)`），不是泛化分类
- `getSignalColor()` 按语义着色：绿=多头、红=空头、黄=预警、青=信息
- 默认隐藏 L3，nav 栏显示 `日期 | L1:N L2:N L3:N(hidden) | N visible | 30s`
- 日报卡片可折叠，点击标题收起/展开

**模拟盘页面 (`/sim`)**:
- `/api/sim` 读 `sim_trading.db`（better-sqlite3，只读），TS 端计算 Sharpe/MaxDD/归因。手续费由 Python 端计算写入 DB，TS 直接读取不重算。
- 摘要栏: 收益率/夏普/胜率/最大回撤/盈亏比/总市值/总资产/可用/交易笔数/手续费
- 交易计划: 显示所有激活计划的条件单状态和当前价格
- 操作记录: 分页表格（每页 2 条），显示开仓/平仓/评分退出等事件
- 模拟持仓: 与主页 `HoldRow` 风格一致 (SIM 类型标签，代码/评分/现价/成本/盈亏%/市值/浮盈/止损/止盈)
- 已完成交易 + 净值曲线(SVG) + 回测归因（按策略+按股票）

### 板块轮动 & 自定义指数 (`/sector`)

**概览**: 自定义板块指数 + 主线行情告警。板块轮动功能已归档（前端页面删除，DB 数据保留，代码在 git 历史 `4eeacb8` 中）。

**Tag-Based Index 架构**: 自定义指数不再使用独立配置文件，而是从 `monitor_watchlist.tags` 自动聚合。给股票打标签（如 `磷化工`）即自动创建/加入该标签的指数。`tag_meta` 表存储每个标签的元数据（star/watch/baseline_value）。无需维护独立的成分股列表——标签即指数。

**页面结构**:
- `/sector` — 我的指数（轮动矩阵风格）+ 主线告警 + K 线图弹窗

**数据流**:
```
[每日 15:30 cron，自动跳过周末]
sector_index_engine.py
  ├── _load_tag_indices() → 从 monitor_watchlist.tags + tag_meta 聚合指数定义
  ├── akshare stock_zh_a_hist() → 成分股日线（EM push2）
  │   └── fallback: 腾讯财经 web.ifzq.gtimg.cn（EM 被封时自动切换）
  ├── 腾讯 qt API → 成分股名称（batch 获取，进程内缓存）
  ├── 新浪财经 API → 板块排名（GBK 解码）
  │   ├── 行业板块: newSinaHy.php (49 板块)
  │   └── 证监会行业: newFLJK.php (84 板块)
  ├── 等权平均涨跌幅 → 自定义指数值
  ├── 日收益率线性回归 + R² 过滤 + 累涨检测 → 主线告警
  └── 写入 sim_trading.db: sector_rotation / sector_daily / sector_alerts
          ↓
/api/sector (Next.js, GET 只读 + POST 管理)
  ├── indices: 从 tag_meta + monitor_watchlist.tags 聚合，含 30d history
  ├── components: 含 name/close/change_pct（名称从 DB 读取，fallback watchlist）
  └── alerts: 主线/接近主线告警
          ↓
/sector 页面 (矩阵 + K线弹窗 + 告警)
```

**自定义指数（`/sector` 主页面）**:
- 轮动矩阵风格: 左列固定（名称/状态/累涨/星标/删除），右侧横向滚动日涨跌%+指数值
- 日期排列: 最新日期在左，向右滚动看历史
- 点击指数行 → 弹窗显示 K 线图（SVG 折线图 + 面积填充 + baseline 100 参考线）
- K 线图下方: 成分股表格（代码/名称/最新价/涨跌幅/涨跌额）
- 停牌股处理: 无数据时按 0% 涨跌计入指数（不排除，防止指数被小盘股主导）
- components_json 存储: `{code, name, change_pct, close}` — 名称从腾讯 qt API 获取
- 标签管理: 在 Manage 页面给股票打 tags，sector 页面自动出现对应指数

**主线行情检测**:
- 规则: `累涨 >= 8%` AND `日收益率回归斜率 >= 0.05` AND `R² >= 0.4`
- 斜率归一化: 对日收益率%序列做线性回归（非绝对指数值），scale-independent
- R² 过滤: 趋势必须可靠（R²<0.4 说明波动大，不是稳定趋势）
- 接近告警: `累涨 >= 75%阈值` AND `slope > 0`（必须上行，防止回调误报）
- watch 过滤: `watch=false` 的指数跳过检测
- 过滤: `min_days_since_create (默认3天)` 排除一日游

**板块轮动（已归档）**: 前端页面已删除，`sector_rotation` 表数据保留（90 天自动清理）。`--rotation` 命令仍可采集数据。恢复前端: `git show 4eeacb8:web/app/sector/rotation/page.tsx`。

**交易日检测**: `_is_trading_day()` 检查周一至周五。周末运行 `--indices`/`--rotation`/`--detect` 自动跳过，不产生脏数据。节假日由数据源返回空数据处理（停牌逻辑兜底）。

**运行方式**:
```bash
poetry run python -m src.tools.sector_index_engine              # 全量运行（跳过周末）
poetry run python -m src.tools.sector_index_engine --rotation    # 仅采集板块排名
poetry run python -m src.tools.sector_index_engine --indices     # 仅计算自定义指数
poetry run python -m src.tools.sector_index_engine --backfill ID # 回填指数30天历史（不受交易日限制）
poetry run python -m src.tools.sector_index_engine --detect      # 仅检测主线信号
```

**指数定义（Tag-Based）**: 指数由 `monitor_watchlist.tags` 自动聚合，不需要独立配置文件。

```
给 000792 打 tag "磷化工" → 自动出现在 /sector 页面的"磷化工"指数中
tag_meta 表存 star/watch/baseline_value → 控制指数是否监测主线、是否星标
```

**告警规则 (`sector_config.json`)**（仅保留 alert_rules + rotation，indices 已迁移到 tags）:
```json
{
  "alert_rules": {
    "cumulative_gain_pct": 8,
    "slope_threshold": 0.05,
    "r_squared_min": 0.4,
    "lookback_days": 10,
    "min_days_since_create": 3
  },
  "rotation": { "category": "industry", "sort": "change_pct", "top_n": 10 }
}
```

**API**:
- `GET /api/sector` → indices (从 tags 聚合，含 history + 成分股) + alerts + rotation
- `GET /api/sector?category=industry&sort=change_pct&top_n=10&board=板块名` → 轮动矩阵 + 板块详情
- `POST /api/sector` → `{action: "create"|"update"|"delete"|"watch"|"star"|"config", ...}`
- 指数的创建/删除实际操作 `monitor_watchlist.tags` + `tag_meta` 表

**SQLite 表** (in `sim_trading.db`):
- `sector_rotation`: 板块每日排名 (date, category, board_name, change_pct, rank) — 90 天保留
- `sector_daily`: 自定义指数日线 (date, index_id, avg_change_pct, index_value, components_json) — 180 天保留
- `sector_alerts`: 主线告警 (date, index_id, alert_type, cumulative_pct, slope, r_squared, message) — 30 天保留

**数据源降级链**: akshare (EM push2) → 腾讯财经 kline (web.ifzq.gtimg.cn) → 新浪财经 (板块排名)。腾讯 API 同时提供股票名称（qt 批量接口）和 QFQ 日 K 线，是 EM 被封时的主要 fallback。

**历史回填**: `--backfill ID` 从腾讯财经拉取成分股 30 天日线（akshare 失败时自动切换），INSERT OR REPLACE 覆盖旧数据。回填不受交易日限制。

**管理页面 (`/manage`)**:
- 交易计划管理: 创建/编辑/暂停/删除计划，支持条件单（到价买卖/移动止损/反弹买入）
- 持仓管理: `type | code | name | cost | shares | ▲ above | ▼ below | ★ | hide`
- `above`/`below` 告警阈值通过 `EditableCell` 内联编辑，保存到 `alert_config.json`（适用于 holding 和 watching）
- `hide` 开关对 holding 和 watching 类型都可用（与 CLI `svc hide` 一致）
- Promote (watching→holding) 必须填写 cost 和 shares 才能 Confirm
- Demote (holding→watching) 有 `confirm()` 确认弹窗

**Config API** (`/api/config` POST actions):
- `add` — 添加股票到 watchlist
- `update` — 更新 cost/shares/name 等字段
- `rm` — 删除股票（同步清理 alert_config）
- `hide`/`unhide` — 切换隐藏状态
- `star`/`unstar` — 切换星标
- `promote` — watching → holding（需 cost/shares）
- `demote` — holding → watching（清除 cost/shares）
- 所有操作 DB-first 写入 `monitor_watchlist` 表，然后导出 JSON 快照

**数据职责分离**:

| 存储 | 写入方 | 内容 |
|------|--------|------|
| `market_data.json` | Poller (Python) | 个股行情 + 两市成交额 (marketTurnover) + 汇率 |
| `sim_trading.db` → `monitor_watchlist` | UI (/api/config) | 持仓配置 (主存储，DB-first) |
| `monitor_config.json` | UI (/api/config) 双写 | 持仓配置 JSON 快照 (name/type/cost/shares/hidden) |
| `alert_config.json` | UI (/api/config) | 告警规则 (above/below，按股票代码索引) |
| `sim_trading.db` → `alert_events` | Notifier (Python) | 告警事件流 (message/display 双格式) |
| `trade_plans.json` | UI (/api/trade-plans) + TradePlanEngine | 交易计划条件单 (orders 统一模型) |
| `sim_trading.db` → `trade_plan_events` | TradePlanEngine (Python) | 交易计划触发事件 |
| `sector_config.json` | UI (/api/sector) | 告警规则 + 轮动配置（indices 已迁移到 tags） |
| `sim_trading.db` → `tag_meta` | UI (/api/sector, /api/config) | 标签元数据 (star/watch/baseline_value) — 标签即指数 |
| `sim_trading.db` → `sector_*` | sector_index_engine (Python) | 板块轮动排名 + 自定义指数日线 + 主线告警 |

`/api/metrics` 合并 `market_data.json` + `monitor_config`(DB 或 JSON) + `alert_config.json` + SQLite alert_events + 计算 pnl，任何 UI 操作立即生效，不依赖 poller 周期。

**`alert_events` 表（`sim_trading.db`）** — Notifier 写入的告警事件，存储在 SQLite 中（原 `alert_events.json` 已迁移）。每条事件含两种格式：`message`（stealth 简短，terminal 通知用）和 `display`（中文详细，web 日志展示用）。`INSERT OR IGNORE` + `UNIQUE(ts, symbol, message)` 零成本去重。date 索引支持历史查询。30 天自动清理。

**`monitor_config.json` structure** (不含 above/below):
```json
{
  "watchlist": {
    "HK09988": { "name": "...", "type": "holding", "cost": 155, "shares": 200, "lot": 100, "tags": "港股科技" },
    "000792": { "name": "盐湖股份", "tags": "磷化工,有色金属", "watch_price": 18.5 }
  },
  "holdings": { "HK09988": { "name": "...", "type": "holding", "cost": 155, "shares": 200, "lot": 100, "tags": "港股科技" } },
  "watching": { "000792": { "name": "盐湖股份", "tags": "磷化工,有色金属", "watch_price": 18.5 } },
  "settings": { "poll_interval": 30, "big_move_pct": 3, "cooldown_minutes": 10 }
}
```

`tags` 字段为逗号分隔的标签字符串，与 `monitor_watchlist.tags` DB 列同步。`watch_price` 为关注价（可选）。标签自动聚合为 `/sector` 页面的自定义指数。

JSON 同时存储三种视图：`watchlist`（统一）、`holdings`（仅持仓）、`watching`（仅自选）。读取时 `normalizeConfig()` 自动兼容任意组合。

**Monitor Config DB 迁移**:

持仓配置主存储已迁移到 SQLite（`sim_trading.db` 的 `monitor_watchlist` + `monitor_settings` 表），JSON 作为可读快照同步保存。

```sql
-- monitor_watchlist: symbol/name/list_type(holding|watching)/cost/shares/lot/hidden/star/timestamps
-- monitor_settings: key/value/updated_at
```

- **`CONFIG_SOURCE` 环境变量**: 设 `json` 强制读 JSON，默认读 DB（DB 空时自动 fallback JSON）
- **双写模式**: `/api/config` 所有写操作先写 DB，然后导出快照到 JSON，保证两者一致
- **迁移工具**: `poetry run python -m src.tools.monitor_config_db_migrator --action import-verify`
- **向后兼容**: Python 端 Poller/Notifier 仍读 JSON；Web 端 `/api/config` 和 `/api/metrics` 支持 DB 优先

**`alert_config.json` structure** (独立告警规则):
```json
{
  "alerts": {
    "HK09988": { "above": 166, "below": 150 },
    "688676": { "above": 100, "below": 85 }
  }
}
```

`type: "holding"` = production (PROD), otherwise watching (DEV). `poll_interval` controls refresh rate. Interactive commands modify config via `/api/config` POST (DB-first + JSON snapshot 双写)。

**共享模块**:
- `web/app/lib/db.ts` — SQLite 路径统一 (`SIM_DB_PATH`)，所有 API route 共用
- `web/app/types.ts` — `WatchEntry`（含 `lot`）、`MonitorConfig`（含 `holdings`/`watching` 分组）
- `src/tools/monitor_config_db_migrator.py` — JSON ↔ DB 迁移工具 (import/verify/export)
- `src/tools/migrate_sector_to_tags.py` — sector_config.json indices → stock tags + tag_meta 一次性迁移工具

### Git 版本控制

`src/data/` 下的文件分为两类：

**需要提交的（含持久状态）**:

| 文件 | 说明 |
|------|------|
| `monitor_config.json` | 持仓配置（用户手动维护） |
| `alert_config.json` | 告警规则（用户手动维护） |
| `trade_plans.json` | 交易计划条件单（TradePlanEngine + UI 维护） |
| `sim_trading.db` | SQLite 数据库（信号归档、交易记录、alert_events、trade_plan_events、实时持仓） |
| `market_data.json` | 最新行情快照（poller 写入，提交保留最后状态） |
| `l2_strategy_signals.json` | L2 信号 + session 上下文（资金流快照、盘口状态）。daemon 每 3s 覆盖，**每日 08:00 自动归档到 `archive/`**，防止 session 数据丢失 |
| `signal_rules.json` | 信号规则配置 |
| `l2_strategy_config.json` | L2 策略参数 |
| `sector_config.json` | 板块告警规则 + 轮动配置（indices 已迁移到 DB tags） |

**不需要提交的（临时/派生）**:

| 文件 | 说明 |
|------|------|
| `sim_trading.db-shm` / `sim_trading.db-wal` | SQLite WAL 临时文件 |
| `daily_summary.json` | 每日报告（收盘后生成，可重新生成） |
| `archive/` | 历史归档目录（每日 08:00 归档 market_data + l2_signals + daily_summary，90 天自动清理） |

### Simulated Trading (`src/sim_trading/`)

模拟交易系统，v2 策略：日线评分驱动型交易（替代 v1 的 L2 秒级信号驱动）。

**v2 策略核心**:
- **开仓**: 日线综合评分 >= 70 分（6 维度加权：MACD/RSI/MA排列/主力资金/量价/支撑位）
- **平仓**: 止损/止盈实时检查 + 15:30 收盘评估（评分 < 40 → 平仓）
- **入场窗口**: 10:00-10:30，每天最多 1 只新开仓
- **日内例外**: 极强 L2 信号（confidence >= 0.85 且 score >= 60）可在窗口外入场
- **T3 纠偏**: 加手续费过滤（|pnl| > 0.5%）+ min_hold 30 分钟检查；盈利时只收窄止损不卖出
- **冷却**: 同一股票平仓后 120 分钟内不再入场
- **最小交易额**: notional < 30,000 HKD 的交易不执行（避免小仓位手续费率过高）

**v1→v2 改进背景**: v1 在实盘中 17 笔交易全部被 T3:large_order_reversal 反复平仓，手续费 1,406 HKD（占 |PnL| 的 171%），6/17 笔毛利为正但扣费后亏损。

**数据流**:
```
实时: L2 signals → signal_archiver → sim_trading.db
      DailyIndicatorTracker.score() → RealtimeSimEngine.tick() → live_state / trades
回放: sim_trading.db → replay_runner → trades/daily_pnl
Web:  sim_trading.db → /api/sim → /sim 页面
```

**模块**:

| 模块 | 职责 |
|------|------|
| `signal_archiver.py` | 实时归档 L2 信号 + 30s 价格快照到 SQLite |
| `realtime_engine.py` | **v2 实时引擎**: 日线评分入场、时间窗口控制、T3 手续费过滤、per-tick score 缓存 |
| `signal_mapper.py` | 4 层信号规则引擎 (Tier1 独立→Tier2 增强→Tier3 纠偏→Tier4 仅日志) |
| `position_manager.py` | 虚拟持仓管理 (lot-size 对齐, SL/TP/max-hold 退出) |
| `simulation_engine.py` | HK 交易成本 (佣金+印花税+交易费+结算费) + 流动性滑点 |
| `trade_analyzer.py` | 绩效分析: 胜率/Sharpe/最大回撤/Calmar/归因 |
| `replay_runner.py` | 历史回放入口 |

**DailyIndicatorTracker 评分系统** (`l2_strategy_engine.py`):
- `score(code, main_net_inflow_pct)` → 0-100 分，6 个子维度
- `get_atr(code)` → 缓存在 `_ind[code]["atr"]` 中的 14 期 ATR
- 每 30 分钟从 Futu OpenD 刷新日 K 线（120 天），计算 RSI/MACD/MA/BB/ADX/ATR/Vol
- 无 kline 数据时返回 `{"total": 0, "action": "WAIT"}` — 不会误开仓或误平仓

**评分维度 (`signal_rules.json → scoring_weights`)**:

| 维度 | 满分 | 计算逻辑 |
|------|------|---------|
| MACD | 20 | 金叉=20, hist扩张=18, 死叉=0, DIF/DEA零轴上方+2 |
| RSI | 15 | 60-70=15, 50-60=10, >80=3(极度超买), <30=2 |
| MA排列 | 20 | 多头排列+站上MA20=20, 空头排列=0 |
| 主力资金 | 20 | 净流入>10%=20, 无数据时固定=8 (需L2数据传入) |
| 量价配合 | 15 | 放量上涨=15, 缩量上涨=6, 放量下跌=2 |
| 支撑位 | 10 | 距支撑<2%=10, <5%=8, >10%=3 |

**信号规则 (`src/data/signal_rules.json`)**:
- `tiers.1-4`: 同 v1（14 种独立 + 2 种增强 + 6 种纠偏 + 11 种日志）
- `daily_score`: v2 核心配置（entry/exit 阈值、时间窗口、冷却、min_hold、min_notional）
- `scoring_weights`: 6 维度权重（合计 100 分）
- `risk_control`: min_confidence=0.60, max_single_stock=25%, max_total_invested=80%
- `cost_model`: HK 市场费率 (佣金 0.03% min 3 HKD, 印花税 0.13%, 交易费 0.00565%, 结算费 0.002%)
- `lot_sizes`: 每只 HK 股的每手股数

**Daemon 架构** (`l2_strategy_daemon.py`):
```
L2StrategyEngine (poll_once → L2 signals)
  ├── _daily_indicators: DailyIndicatorTracker (日K指标 + 评分)
  └── 传递引用 → RealtimeSimEngine(rules, daily_tracker=engine._daily_indicators)
       ├── tick() 每 3s 调用（交易时段）
       ├── _evaluate_entries() → 10:00-10:30 评分选股
       ├── _process_signal_v2() → T3 过滤 + 日内例外
       ├── _evaluate_exits() → 15:30 收盘评估
       └── _persist_state() → live_state 表 (含 daily_score)
```

**运行回放**:
```bash
poetry run python -m src.sim_trading.replay_runner                    # 默认 v1_baseline
poetry run python -m src.sim_trading.replay_runner --version v2_test  # 指定参数版本
```

**重启 daemon (v2)**:
```bash
pkill -f l2_strategy_daemon
nohup poetry run python src/tools/l2_strategy_daemon.py >> logs/l2_daemon_out.log 2>&1 &
tail -f logs/l2_daemon.log | grep rt_sim   # 观察 v2 RT 日志
```

**测试**: `poetry run pytest src/sim_trading/test_sim_trading.py -v` (62 tests)

**SQLite 数据库 (`src/data/sim_trading.db`)**:
- `signals`: 归档的 L2 信号 (strategy, code, direction, price_at_signal) — 180 天保留
- `price_snapshots`: 30s 粒度价格快照 (HK only) — 180 天保留
- `session_snapshots`: 5 分钟 session 上下文快照 (资金流、盘口状态) — 180 天保留
- `trades`: 已平仓交易 (entry/exit price, pnl, exit_reason, param_version="live") — 永久保留
- `daily_pnl`: 每日权益快照 (equity, cash, invested, positions_json) — 永久保留
- `live_state`: 实时持仓 (entry_price, SL/TP, daily_score, unrealized_pnl)
- `param_versions`: 参数版本配置
- `alert_events`: 告警事件 (ts, date, symbol, kind, level, message, display, change_pct) — 30 天保留
- `trade_plan_events`: 交易计划触发事件 (ts, date, plan_id, event_type, condition_id, label, price, shares) — 30 天保留
- `sector_rotation`: EM 板块每日排名 (date, category, board_name, change_pct, rank) — 90 天保留
- `sector_daily`: 自定义指数日线 (date, index_id, avg_change_pct, index_value, components_json) — 180 天保留
- `sector_alerts`: 主线告警 (date, index_id, alert_type, cumulative_pct, slope, message) — 30 天保留
- `daily_l2_digest`: 日线微观结构聚合 (date, code, lo_net_amount, tick_imbalance, cf_net_inflow, direction_score, direction) — 收盘后计算
- `monitor_watchlist`: 持仓配置主存储 (symbol, name, list_type, cost, shares, lot, hidden, star, tags, watch_price) — DB-first，JSON 为快照
- `monitor_settings`: 监控设置 (key, value) — 与 monitor_config.json settings 同步
- `tag_meta`: 标签元数据 (tag, star, watch, baseline_value, created_at) — 标签即指数，控制主线检测和星标

**RT engine 日志**: logger 名 `l2_daemon.rt_sim`，继承 daemon handler，写入 `logs/l2_daemon.log`。

**Web 页面 (`/sim`)**:
- `/api/sim` 路由: 用 `better-sqlite3` 读 SQLite，`SELECT * FROM live_state` 自动包含 daily_score
- 实时持仓表: 类型/代码/名称/**评分**/现价/涨跌幅/成本/盈亏%/市值/浮盈/止损/距止损/止盈
- 评分列着色: >= 70 绿色 (BUY), 40-69 橙色 (HOLD), < 40 红色 (SELL)
- 摘要栏: 收益率/夏普/胜率/回撤/盈亏比/总市值/总资产/可用/交易笔数/手续费
- 页面布局: 摘要栏 → 交易计划 → 实时持仓 → 操作记录(分页) → 已完成交易 → 净值曲线(SVG) → 回测归因
- 全中文标签，Dracula 终端风格

### Data Sources & Tools (`src/tools/`)

- `stock_data_fetcher.py` — A-share data via akshare
- `news_crawler.py` — multi-source (新浪财经, 网易财经, 东方财富) with caching, dedup, Sina fallback
- `data_analyzer.py` — technical indicator calculations
- `openrouter_config.py` — `get_chat_completion()` LLM wrapper

**Stock data API rate limiting**: always use 1-2s delays between requests, max 5 stocks per batch. APIs throttle aggressively.

### Daily Summary (`src/tools/daily_summary_generator.py`)

收盘后自动生成 LLM 信号日报（16:05-16:15 由 notifier 触发），写入 `src/data/daily_summary.json`，web `/alerts` 页面展示。

**数据流**: `l2_strategy_signals.json` (信号) + `alert_events` (告警) + `session_snapshots` + `signals` (微观聚合) → `_compute_l2_digest()` → LLM prompt → `daily_summary.json`

**`_compute_l2_digest(date_str)`**: 从 `session_snapshots`（收盘最后一条 session）+ `signals` 表聚合日线微观结构，写入 `daily_l2_digest` 表。指标：大单净额/净比、tick imbalance、主力净流入、量价背离/大单翻转频次、综合方向评分（加权: 大单*3 + tick*2 + 资金流*1 + 背离*-2 + 翻转*-2）。

**LLM prompt 结构**:
- 持仓标的：按 ★star → 市值降序排列，每只标注 `[★重点, 持仓X.X万]`，附微观数据行
- ★重点自选：star watching 股票独立区域，有完整微观数据（即使 0 信号也不过滤）
- 自选标的：普通 watching，仅有信号的列出
- LLM 必须在"重点关注"中深度分析所有 ★ 股票（持仓+自选）
- 要求自然引用具体数字（"大单净买0.19亿, tick偏买12.4%"），禁止模糊表述

**手动重新生成**: `poetry run python -c "from src.tools.daily_summary_generator import generate_daily_summary; generate_daily_summary()"`

### 交易计划系统 (`src/data/trade_plans.json`)

条件单引擎，管理分批建仓/止盈/止损/移动止损计划。统一 `orders` 模型，没有单独的 `stop_loss`/`entries`/`exits` 字段——止损只是 `side=sell, op=<=` 的普通条件单。

**数据结构**:
```json
{
  "plans": {
    "HK02722_tp": {
      "name": "重庆机电分批止盈",
      "symbol": "HK02722",
      "status": "active",
      "created_at": "2026-02-27",
      "orders": [
        { "id": "sl1", "side": "sell", "op": "<=", "price": 3.4, "shares": 4000,
          "volume_min": null, "consecutive_days": null, "trailing": null,
          "label": "固定止损", "triggered": false, "triggered_at": null },
        { "id": "sl2", "side": "sell", "op": ">=", "price": 3.8, "shares": 4000,
          "trailing": { "pct": 8.2, "watermark": null, "active": false },
          "label": "移动止损", "triggered": false, "triggered_at": null }
      ]
    }
  }
}
```

Order 字段: `side` (buy/sell), `op` (>=/<= 价格方向), `price` (触发价), `shares` (股数, 按 lot 对齐), `volume_min` (成交额条件), `consecutive_days` (连续满足天数), `trailing` (移动止损: `{pct, watermark, active}`), `label`, `triggered`/`triggered_at`。

**引擎**: `TradePlanEngine` (`src/tools/stock_notifier.py`)
- 每 tick 检查所有 `status=active` 的计划
- 支持: 到价买卖、移动止损（回落卖出）、反弹买入、成交额过滤、连续天数
- 触发后标记 `triggered=true`，写入 `trade_plan_events` SQLite 表
- 告警结果与 `DeltaAlertEngine` 合并后统一派发

**Web API** (`web/app/api/trade-plans/route.ts`):
- `GET` → 所有计划 + 持仓数据 (cost/shares/price) + lot_size
- `POST` → create/update/delete/toggle/reset

### Architecture Rules

- **Poller 是生产者，UI 是消费者，二者无耦合。** Poller (`src/tools/market_data_poller.py`) 只写行情数据到 `market_data.json`（price/change/vol/amount 等）；用户配置（type/cost/shares/hidden）主存储在 `sim_trading.db:monitor_watchlist`，JSON 为快照；告警规则（above/below）独立存 `alert_config.json`；告警事件存 `sim_trading.db` 的 `alert_events` 表。`/api/metrics` 负责合并行情 + 配置 + 告警 + 计算派生字段（pnl）。任何 UI 端操作立即生效，不依赖 poller 周期。
- **Monitor Config DB-first 双写。** `/api/config` 所有写操作先写 SQLite `monitor_watchlist`/`monitor_settings` 表，然后自动导出 JSON 快照到 `monitor_config.json`。Python 端（Poller/Notifier）仍读 JSON。`CONFIG_SOURCE=json` 环境变量可强制 Web 端也读 JSON。
- **All market data and FX rate fetching must happen in the Python poller script**, not in Next.js API routes. The web layer (`/api/metrics`) only reads from `market_data.json` written by the poller. This keeps the data pipeline centralized and avoids duplicate API calls from the frontend.
- **Poller 降级不丢数据。** 东方财富不可用时 fallback 到新浪（价格刷新，但无量比/换手率）。Sina 降级时从上轮 `market_data.json` 继承 `volRatio`/`turnover`，避免用 0 覆盖。FX 汇率获取失败时同理继承上次值。
- **板块轮动独立于 Poller。** `sector_index_engine.py` 是独立 cron，不嵌入 poller 循环。数据源降级链: akshare (EM push2) → 腾讯财经 kline → 新浪财经。指数定义从 `monitor_watchlist.tags` + `tag_meta` 聚合（不再读 `sector_config.json` 的 indices）。Web 层 `/api/sector` 只读 SQLite，不调用外部 API。周末自动跳过（`_is_trading_day()` 检查）。
- **告警规则与持仓配置分离。** `above`/`below` 阈值存在 `alert_config.json`，不存在 `monitor_config.json` 的 watchlist 条目里。所有读写告警的代码（web API、CLI、notifier）统一从 `alert_config.json` 操作。删除股票时同步清理两个文件。
- **告警计算单一数据源。** Notifier (`stock_notifier.py` DeltaAlertEngine) 是唯一的告警计算引擎，产出写入 `sim_trading.db` 的 `alert_events` 表。Web 前端 (`useAlerts`) 只读取展示，不做任何告警计算。确保 terminal 弹窗和 web 日志完全一致，不重复计算，不重复告警。
- **手续费单一计算源。** 交易成本只在 Python `SimulationEngine.calc_cost()` 中计算，`position_manager.close_position` 写入 DB 的 `pnl` 字段已包含买卖双边手续费（`buy_cost_per_share` 按比例分配）。Web `/api/sim` 直接读 DB pnl，不重新计算手续费。

### HK Stock Codes

Hong Kong stocks use `HK` prefix (e.g., `HK09988`). The web metrics API strips the prefix for 东方财富 API calls and maps market code `116` for HK stocks, vs `1` (Shanghai) / `0` (Shenzhen) for A-shares. See `emMarket()` and `rawCode()` in `web/app/api/metrics/route.ts`.

**HK P&L FX 转换**: 行级和汇总级都乘 `fxRate`（来自 poller 的 `hkdCnyRate`，fallback 0.92）。`HoldRow` 中 `rowFx = s.id.startsWith("HK") ? fxRate : 1` 应用于 `mktVal`、`totalPnlRaw`、`dayPnl`。百分比字段（`pnl%`、`change%`）不转换。

**P&L 守护**: 前端 `totalPnlRaw` 计算需要 `s.cost > 0`（不仅 `!= null`），与 API 端 `pnl` 计算的守护条件一致。cost=0 的 holding 显示 `-` 而非无意义大数。

**今日盈亏 (`calcDayPnl`)**: 前端 `page.tsx` 用 `calcDayPnl(s, fx)` 计算行级和汇总级 dayPnl。基础公式 `chgAmt × shares × fx`，但对当日买入的股票做封顶：当 rawDayPnl 与 totalPnl 同向且绝对值超过 totalPnl 时，用 totalPnl 封顶。原因：当日买入的股票，今日盈亏应从买入成本算起，不应包含昨收→买入价之间的隔夜跳空。

### Poller 降级保护

`market_data_poller.py` 的 `fetch_realtime_with_fallback` 返回 `(stocks, is_sina_fallback)` 标记数据来源：

- **量比/换手率继承**: 新浪不提供 `turnover`/`volRatio`，降级时从上一轮 `market_data.json` 继承（而非写 0 覆盖）
- **汇率继承**: `hkdCnyRate` 获取失败时从旧数据继承上次有效值（而非写 null 触发前端 WARN）
- **孤儿进程防护**: `start_monitor.sh` 的 `_stop_one` 使用进程组 kill + `pkill -f` 兜底清理；`_ensure_no_orphan` 在启动前检测并清理 PID 文件失效但进程仍在的孤儿，防止 restart 后出现多实例

### Stock Notifier (`src/tools/stock_notifier.py`)

Lightweight macOS notification daemon. Reads poller output, never fetches data directly.

**Data flow**: `market_data.json` (poller) + `monitor_config.json` (config) + `alert_config.json` (thresholds) → DeltaAlertEngine + TradePlanEngine → stealth_dispatch → terminal-notifier + `sim_trading.db:alert_events` → web

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

#### L2 Signal Noise Reduction (中长线优化)

用户策略为中长线（持仓数周到数月），对 L2 tick 级信号做了降噪优化。

**关闭 8 个 tick/session 级信号** (`l2_strategy_config.json` → `enabled: false`)：

| 信号 | 关闭理由 |
|------|---------|
| `capital_flow_spike` | 5 分钟资金脉冲，收盘时大概率被抹平，日内噪音 |
| `large_order` | HK 大票每天数百笔 1500 万以上成交是常态，非方向信号 |
| `order_book_imbalance` | 盘口挂单变化是毫秒级噪音，与中长线持仓决策无关 |
| `volume_price_divergence` | 30 分钟窗口背离无统计意义，日线版 `macd_top_divergence` 已覆盖 |
| `tick_imbalance` | 5 分钟 tick 方向偏移，信噪比低 |
| `volume_accel_alert` | 算法拆单拉升信号，纯日内交易工具 |
| `large_order_reversal` | v1 模拟盘惨案（17 笔全被反复平仓），信噪比最差 |
| `closing_surge` | 尾盘异动预测次日开盘，与中长线无关 |

**保留 5 个 session 级** (仍 enabled)：`momentum_alert`(L1)、`momentum_sell_alert`、`volume_accel_sell_alert`、`tick_persistence`、`institutional_retail_divergence`。v2 模拟盘不受影响（核心循环只依赖日线评分）。

**升级 7 个日线信号到 L2 弹窗** (`DAILY_NOTIFY_STRATEGIES` 从 6 → 13)：

| 信号 | 中长线意义 |
|------|-----------|
| `ma_bullish_align` | 均线多头排列，趋势确认基石 |
| `ma_bearish_align` | 均线空头排列，趋势转空确认 |
| `adx_trend_start` | ADX 上穿 25，震荡→趋势转换 |
| `volume_breakout` | 放量突破新高，经典入场信号 |
| `support_breakdown` | 放量跌破支撑/MA60，止损信号 |
| `macd_golden_cross` | MACD 金叉，中长线买入信号 |
| `macd_death_cross` | MACD 死叉，中长线风险预警 |

这些日线信号每天最多触发 1 次（480min 冷却），弹窗增量可控（~1-3 条/天/股）。

**静默采集 + 日线聚合**: 底层数据（ticker/capital/snapshot）始终获取，大单检测始终运行并喂入 `SessionAccumulator`，但 disabled 策略不产出 alert。收盘后 `_compute_l2_digest()` 从 `session_snapshots` + `signals` 表聚合日线摘要到 `daily_l2_digest` 表，注入 LLM 日报。

**日线微观结构指标** (`daily_l2_digest` 表)：
- 大单净额/净比 (lo_net_amount / lo_net_ratio) — 机构净买卖方向
- Tick imbalance — 全天主动买/卖方力量对比
- 主力资金净流入 (cf_net_inflow / cf_net_inflow_pct) — 资金流向
- 量价背离/大单翻转频次 (vpd_count / lor_count) — 事件聚合
- 综合方向评分 (direction_score) — 加权: 大单*3 + tick*2 + 资金流*1 + 背离*-2 + 翻转*-2

**Notifier 降噪兜底**：
- Per-stock daily cap = 8（`PER_STOCK_DAILY_CAP`），超出的 L2 信号不写入 `alert_events`
- Alert 页面默认隐藏 L3，只显示 L1+L2，可点击 `L3:N (hidden)` 展开

#### Config Write Safety

- **DB 写入**: `/api/config` 通过 `better-sqlite3` 同步写入 `monitor_watchlist`/`monitor_settings` 表（SQLite WAL 模式，自带原子性）
- **JSON 快照**: 写 DB 后自动导出 JSON，使用原子写入（tmp → rename），防止并发读到半截 JSON
- **Poller 写入**: `market_data.json` 也使用 tmp → rename 原子写入
- 修改 config 写入逻辑时必须保持双写模式（DB + JSON snapshot）

#### Playwright Testing

**强制规则：每个新功能或 UI 改动必须附带 E2E 测试。** 不写测试的功能视为未完成。

脚本存放在 `web/screenshots/`，截图输出到同目录。命名规则：`test_<feature>.mjs`。

**每次功能开发必须包含：**
1. **功能测试脚本** — 模拟完整用户流程（点击、填表、提交、验证结果）
2. **截图验证** — 每个关键步骤截图，用 Read 工具查看 UI 是否正确
3. **API 验证** — 拦截 API 响应，确认数据正确写入/读取
4. **清理** — 测试创建的数据在测试结束时删除，不污染生产数据

**测试模式参考：**
- **CRUD 全流程**: 创建→验证显示→编辑→验证更新→删除→验证消失（参考 `test_plan_e2e.mjs`）
- **EditableCell**: 点击 `[title="Click to edit"]` → fill → Enter → 等待 API → 验证 toast
- **Visual**: 截图对比（loading 态、错误态、正常态）
- **Data consistency**: summary 汇总 vs 行级求和（tolerance ~500 for 万-level rounding）
- **Route intercept**: `page.route()` 模拟 API 失败/延迟，验证 error banner 和 loading 状态

**检查清单（commit 前）：**
```bash
# 1. 全量回归
node screenshots/test_full_checkup.mjs

# 2. 功能专项测试
node screenshots/test_<feature>.mjs

# 3. TypeScript 编译
cd web && npx tsc --noEmit

# 4. 截图审查（用 Read 工具查看每张截图）
```

**现有测试脚本（~330+ tests 总计）：**
| 脚本 | 测试数 | 覆盖范围 |
|------|--------|---------|
| `test_full_checkup.mjs` | ~55 | 全站回归（Holdings/Watching/Alerts/Sim/Manage/API/Navigation + 排序/折叠/跨 tab 断言） |
| `test_dashboard_e2e.mjs` | 55 | Dashboard 交互（折叠/排序/星标/EditableCell/FX/摘要栏） |
| `test_manage_stocks_e2e.mjs` | 32 | Manage 持仓表（above/below/hide/star/promote/demote/搜索） |
| `test_plan_e2e.mjs` | ~15 | 交易计划 CRUD（创建/编辑/暂停/删除） |
| `test_sim_alerts_e2e.mjs` | 60 | Sim+Alerts（交易计划/分页/L3切换/日报折叠/自动刷新） |
| `test_sector_e2e.mjs` | 46 | Sector（K线弹窗/新建删除指数/星标关注/横向滚动/折叠） |
| `test_navigation.mjs` | 18 | 全站路由+跨页导航 |

#### Hidden List

`hiddenList` 按当前 market tab 过滤。A 股 tab 只显示 A 股 hidden，HK tab 只显示港股 hidden。Holdings (`/`) 页面只显示 `type=holding` 的 hidden，Watching (`/watching`) 页面只显示 `type!=holding` 的 hidden。
