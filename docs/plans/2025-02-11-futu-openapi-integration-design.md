# Futu OpenAPI Integration Design

> Date: 2025-02-11
> Status: Approved

## Motivation

1. **替换东方财富** — 东方财富公开接口不稳定，Futu 官方 SDK 有文档保障
2. **港股 LV2 数据** — 大陆 IP 免费获取港股实时深度行情（10 档盘口）
3. **未来接交易** — 行情是第一步，核心目标是程序化下单
4. **多数据源冗余** — Futu 为主、东方财富 fallback，提高可用性

## Context

- 富途账户 Level 2（资产 1万~50万 HKD），300 只订阅配额
- OpenD 跑在开发 Mac，和 poller/notifier 同机部署
- 先做最小验证，不碰现有代码

## Phase 1 — 最小验证（本次实现）

### 目标

独立 Python 脚本，连接本地 OpenD，拉取 watchlist 实时行情 + 港股 LV2 盘口，打印到终端验证数据质量。

### 不做的事

- 不改现有 poller / notifier / web 任何一行代码
- 不写入 `market_data.json`
- 不处理 fallback 逻辑
- 不接交易接口

### 文件

- `src/tools/futu_quote_demo.py` — 验证脚本
- 依赖：`futu-api`（加入 pyproject.toml）

### 股票代码映射

| 本项目格式 | Futu 格式 | 规则 |
|-----------|----------|------|
| `HK09988` | `HK.09988` | 去 `HK` 前缀，加 `HK.` |
| `688676` | `SH.688676` | 6 开头 → 上海 `SH.` |
| `002848` | `SZ.002848` | 0/3 开头 → 深圳 `SZ.` |

工具函数 `to_futu_code()` / `from_futu_code()` 独立可复用。

暂不处理：北交所 `8xxxxx`/`4xxxxx`。

### 数据字段映射

| 本项目字段 | Futu Snapshot 字段 | 说明 |
|-----------|-------------------|------|
| `price` | `last_price` | 最新价 |
| `change` | `change_rate` | 涨跌幅% |
| `chgAmt` | `price_spread` | 涨跌额 |
| `vol` | `volume` | 成交量 |
| `amount` | `turnover` | 成交额 |
| `amp` | `amplitude` | 振幅% |
| `turnover` | `turnover_rate` | 换手率% |
| `high` | `high_price` | 最高 |
| `low` | `low_price` | 最低 |
| `open` | `open_price` | 开盘 |
| `prevClose` | `prev_close_price` | 昨收 |

### 脚本流程

```
读取 monitor_config.json
        ↓
  代码映射 → Futu 格式
        ↓
  OpenQuoteContext 连接 OpenD (127.0.0.1:11111)
        ↓
  get_market_snapshot() 批量拉快照
        ↓
  港股额外: get_order_book() 拉 LV2 盘口
        ↓
  格式化打印到终端（A股表 + 港股表）
```

错误处理：连接失败 / OpenD 未启动 / 订阅超限 → 打日志退出，不重试。

## Phase 2 — Poller 集成（后续）

- `market_data_poller.py` 新增 Futu 数据源，抽象 `DataSource` 接口
- Futu 为主，东方财富 fallback（OpenD 挂了自动切换）
- 输出格式不变，`market_data.json` 结构零改动，前端无感

## Phase 3 — 港股 LV2 盘口展示（后续）

- `market_data.json` 扩展可选 `orderBook` 字段（买卖 10 档）
- Web dashboard 新增盘口组件，港股 tab 有数据时渲染
- Notifier 可基于盘口做精准告警（大单检测等）

## Phase 4 — 交易接口（后续）

- `OpenSecTradeContext` 接入模拟交易
- portfolio_management_agent 决策 → 自动下单
- 先模拟盘验证，再切实盘
- 需独立安全审计

## Design Principles

- 每个 phase 独立可交付，无跨 phase 依赖
- Poller 是生产者，UI 是消费者，二者无耦合（延续现有架构）
- 所有行情数据获取在 Python 层完成，Web 层只读 JSON
