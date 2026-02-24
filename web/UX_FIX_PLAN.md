# UX Fix Plan — Web Dashboard

> Generated: 2026-02-24 | Based on: Playwright screenshots + code review + finance-ux-reviewer audit
> P0 (C1/C3/H7) 已完成，本文档记录剩余待修复项。

---

## P1 — 数据正确性 & 核心体验（立即修复）

### P1-1: HoldRow P&L 未守护 cost=0

- **文件**: `web/app/page.tsx` HoldRow 函数
- **问题**: API 用 `cost > 0` 守护返回 `pnl=null`，但前端 `totalPnlRaw = (s.price - s.cost) * s.shares * rowFx` 在 cost=0 时算出无意义值（等于全额市值）
- **修复**: 将 `s.cost != null` 改为 `s.cost != null && s.cost > 0`，同理 `mktVal` 不受影响但 `dayPnl` 无需守护
- **工作量**: 1 行

### P1-2: Watching 股票缺少 hide/unhide 开关

- **文件**: `web/app/manage/page.tsx` StockRow 组件 (~line 390)
- **问题**: hide toggle 被 `{isHolding && (` 条件限制，但 CLI `svc hide` 支持任意类型，GUI 功能不一致
- **修复**: 去掉 `isHolding` 条件，让 watching 类型也显示 hide 开关
- **工作量**: 1 行（删除条件判断）

### P1-3: Dashboard 首次加载闪烁空状态

- **文件**: `web/app/page.tsx`
- **截图证据**: `dashboard-a-loading.png` — 显示 `Nodes: 0 holdings:0` + `# no services in A-share tab`，大片空白
- **问题**: `loading=true` 时直接渲染空数据区域，无 loading 指示器
- **修复**: 在 terminal body 区域检查 `loading` 状态，显示 terminal 风格的 loading 文本（如 `Loading metrics...` 带闪烁光标），而非渲染空表格
- **工作量**: ~10 行

---

## P2 — 重要体验改进（本周修复）

### P2-1: Alerts 页面 fetch 失败完全静默

- **文件**: `web/app/alerts/page.tsx`
- **问题**: catch 块为空，API 出错时显示 "No alert events today" 与真正无告警无法区分
- **修复**: 仿 Dashboard 加 `fetchError` state + 红色 `[ERROR]` banner + 保留旧数据
- **工作量**: ~15 行

### P2-2: P&L 汇总包含 hidden 但 holdCount 排除 hidden

- **文件**: `web/app/page.tsx` (~line 292-301)
- **问题**: `tabHoldCount` 用 `!s.hidden` 过滤，但 `tabHoldings`（P&L 汇总源）不过滤 hidden，summary 行显示 "holdings:3" 但 P&L 实际包含 4 只
- **修复**: 两种方案任选：
  - A) `tabHoldings` 也排除 hidden（summary 只反映可见持仓）
  - B) holdCount 改为 `tabHoldings.length` 并加注 "(+N hidden)"
- **建议**: 方案 B，因为 P&L 含 hidden 更准确（用户 hide 不代表不想算盈亏）
- **工作量**: ~5 行

### P2-3: HK FX fallback 0.92 警告不够醒目

- **文件**: `web/app/page.tsx` (~line 529)
- **截图证据**: `dashboard-hk-top.png` — `(FX≈0.92)` 是 11px 灰色小字，几乎看不到
- **问题**: 大持仓用 fallback 汇率，误差可达数千元，当前警告级别不匹配风险
- **修复**: 当 `hkdCnyRate === null`（fallback 生效）时，在 HK summary 区域加黄色 `[WARN] FX rate unavailable, using fallback 0.92` banner
- **工作量**: ~5 行

---

## P3 — 体验优化（按需修复）

### P3-1: Manage above/below 可发现性低

- **文件**: `web/app/manage/page.tsx`
- **截图证据**: `manage-holdings.png` — ▲/▼ 和 above/below 编辑单元格存在但视觉上融入背景
- **问题**: `-` 占位符与深色背景融合，用户不易发现可编辑告警阈值
- **修复**:
  - 在 section header 行添加 `above` / `below` 列标签
  - 或给 EditableCell 加轻微背景色区分（如 `background: #ffffff08`）
- **工作量**: ~5 行

### P3-2: API metrics 对 services 非数组无校验

- **文件**: `web/app/api/metrics/route.ts` (line 44)
- **问题**: `market_data.json` 损坏时 services 非数组原样透传，前端 `.filter()` 报错
- **修复**: 加 `if (!Array.isArray(data.services)) return NextResponse.json({ ...EMPTY, error: "invalid data format" })`
- **工作量**: 2 行

### P3-3: Config API 读-改-写无并发保护

- **文件**: `web/app/api/config/route.ts`
- **问题**: 快速连续编辑（如 onBlur 触发 + 下一个 onBlur）产生并发请求，后写覆盖先写
- **修复**: 前端 `apiPost` 改为串行队列（用 promise chain 或 mutex），或后端加简单文件锁
- **工作量**: ~15 行

### P3-4: 中文表头 pad() 对齐偏移

- **文件**: `web/app/page.tsx` (~line 549-561)
- **问题**: `pad("涨跌幅▼", 8)` 按 `.length` 计算，中文字符实际占 2ch 但 length=1，导致列头和数据对不齐
- **修复**: 去掉 `pad()` 对表头的使用，改用 CSS `text-align: right` + 固定 `width` span（已有部分实现）
- **工作量**: ~10 行

### P3-5: Manage promote 不验证 cost/shares 有效性

- **文件**: `web/app/manage/page.tsx` (~line 594)
- **问题**: watching → holding promote 可不填 cost/shares 直接 Confirm，产生 cost=null 的 holding
- **修复**: Confirm 按钮在 cost 和 shares 都非空时才启用
- **工作量**: 3 行

### P3-6: Demote (holding→watching) 无确认弹窗

- **文件**: `web/app/manage/page.tsx` (~line 318)
- **问题**: 点击 "↓ DEV" 直接降级无确认，不像 Remove 有 `confirm()` 保护
- **修复**: 加 `if (!confirm("Demote to watching? Cost/shares data will be kept.")) return`
- **工作量**: 1 行

---

## P4 — 代码质量 & 小优化（空闲时修复）

### P4-1: AlertSettings 类型缺少 L1-L3 字段

- **文件**: `web/app/types.ts`
- **修复**: 补全 `l1_trigger_pct`, `l1_delta_pct`, `l1_cooldown_min` 等 7 个字段

### P4-2: AlertEvent 接口三处重复定义

- **文件**: `useAlerts.ts`, `alerts/page.tsx`, Dashboard 用 `unknown[]`
- **修复**: 移入 `types.ts` 统一 import

### P4-3: TitleBar 组件三页面重复

- **文件**: `page.tsx`, `manage/page.tsx`, `alerts/page.tsx`
- **修复**: 提取为 `components/TitleBar.tsx`

### P4-4: Manage/Alerts `<a href>` 改 `<Link>`

- **文件**: `manage/page.tsx`, `alerts/page.tsx`
- **修复**: 用 Next.js `<Link>` 替代，避免全页刷新

### P4-5: Alerts 页面硬编码 30s 轮询

- **文件**: `alerts/page.tsx`
- **修复**: 从 API 响应读取 `settings.poll_interval`

### P4-6: isETF 正则不覆盖所有 ETF 代码段

- **文件**: `web/app/page.tsx`
- **修复**: 扩展为 `^(51|15|58|52|56|16)\d{4}$` 或用名称判断

### P4-7: derivedVal -Infinity 在升序时排最前

- **文件**: `web/app/page.tsx`
- **修复**: 比较函数特判 -Infinity 始终排尾

### P4-8: Tab 切换不更新 document.title

- **文件**: `web/app/page.tsx`
- **修复**: `switchTab` 中加 `document.title = "node -- " + tab`

### P4-9: CommandPrompt 日志 scrollIntoView 方向错误

- **文件**: `web/app/components/CommandPrompt.tsx`
- **修复**: `logsEndRef` 移到列表顶部或用 `flex-direction: column-reverse`

---

## 验证方式

每个 P 级别修复完成后，使用 `/playwright-test` skill 截图 + 功能测试验证：
- P1: Dashboard loading 态截图、manage watching hide 截图、cost=0 持仓 P&L 显示
- P2: Alerts 页面错误态截图、HK summary hidden 标注、FX warning banner
- P3/P4: 逐项对应截图验证
