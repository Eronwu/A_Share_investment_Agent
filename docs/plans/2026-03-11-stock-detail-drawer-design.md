# Stock Detail Drawer — Design Document

## Goal

将交易计划从 Manage 页面的独立区块迁移到 Holdings/Watching 行内触发的右侧 Drawer，Drawer 同时承载单股的成本/股数、告警阈值、标签编辑和完整的交易计划 CRUD。

## Interaction Model

- **整行可点击**：Holdings/Watching 的每一只股票行，点击任意位置打开 Drawer
- **第一列指示器**（6ch，替换原 ★PROD/DEV 类型列）：
  - `[★][条]`：已星标 + 有活跃/暂停计划
  - `[★]`：已星标，无计划
  - `[条]`：有计划，未星标
  - （空白）：无星标，无计划
  - `[★]` 黄色边框 + 黄色文字；`[条]` 青色边框 + 青色文字
- 行整体 `cursor: pointer`，hover 轻微背景高亮

## Drawer Layout

```
┌─ 右侧固定，宽 440px ─────────────────────────────┐
│  HK02722  重庆机电                     [× 关闭]  │
├──────────────────────────────────────────────────┤
│  基本信息                                        │
│  成本 [ 3.52 ]   股数 [ 4000 ]                   │
├──────────────────────────────────────────────────┤
│  告警阈值                                        │
│  上限 [ 3.80 ]   下限 [ 3.20 ]                   │
├──────────────────────────────────────────────────┤
│  标签                                            │
│  [机械设备] [港股]  [+ 添加]                     │
├──────────────────────────────────────────────────┤
│  交易计划                              [+ 新建]  │
│  ┌────────────────────────────────────────────┐ │
│  │ 分批止盈  ● 运行中                         │ │
│  │ ...PlanCard 内容与 Manage 页完全一致...     │ │
│  └────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

- 背景半透明遮罩（`rgba(0,0,0,0.5)`），点击遮罩关闭
- Drawer 内部可滚动
- ESC 键关闭

## Components

| 组件 | 路径 | 职责 |
|------|------|------|
| `StockDrawer` | `web/app/components/StockDrawer.tsx` | 右侧抽屉主体，接收 `symbol` prop |
| `PlanIndicator` | （内联于 page.tsx）| 第一列指示器，`[★][条]` |
| `useTradePlans` | `web/app/hooks/useTradePlans.ts` | 独立 hook，GET /api/trade-plans，30s 轮询 |

`PlanCard`、`EditableCell`、`Toast`、`TagEditor` 等组件从 `manage/page.tsx` 提取到 `StockDrawer.tsx`，实现复用。

## Data Flow

```
page.tsx / watching/page.tsx
  ├── useMetrics() → services (行情 + cost/shares/above/below/star/tags)
  ├── useTradePlans() → plans (按 symbol 索引)
  └── HoldRow click → setDrawerSymbol(symbol)
        └── StockDrawer(symbol)
              ├── service = services.find(s.id === symbol)
              ├── stockPlans = plans.filter(p.symbol === symbol)
              └── CRUD → POST /api/trade-plans + POST /api/config
```

## Manage Page Changes

- **移除**：PlanCard 列表、「+ 新建计划」按钮、运行中/已暂停 section
- **保留**：股票管理表（hide/star/promote/demote/搜索），成本/shares/above/below/tags 列留在 Manage 表中作为备用批量编辑入口（Drawer 是主入口，Manage 是辅助）
- Manage 页标题区块简化，不再有「交易计划」heading

## Tests

| 脚本 | 覆盖内容 |
|------|---------|
| `web/screenshots/test_stock_drawer_e2e.mjs` | 行点击→Drawer 开，cost/above 编辑，计划 CRUD，关闭 |
| `test_full_checkup.mjs` | 更新断言：Manage 页无「新建计划」按钮；Holdings 行有 indicator 列 |
