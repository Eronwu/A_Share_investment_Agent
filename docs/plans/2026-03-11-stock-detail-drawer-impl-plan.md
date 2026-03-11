# Stock Detail Drawer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将交易计划 CRUD 和单股设置（成本/告警/标签）从 Manage 页独立区块，迁移到 Holdings/Watching 行点击触发的右侧 Drawer。

**Architecture:** 新建 `StockDrawer` 组件（右侧固定 440px 抽屉），从 manage/page.tsx 提取 PlanCard 等组件复用。新增 `useTradePlans` hook 独立轮询。Holdings/Watching 第一列替换为 `[★][条]` 双指示器，整行可点击。

**Tech Stack:** Next.js 15 / React 19 / TypeScript，`better-sqlite3`（already used），`/api/trade-plans` + `/api/config`（existing endpoints），Playwright for E2E。

---

## Task 1: 新建 `useTradePlans` hook

**Files:**
- Create: `web/app/hooks/useTradePlans.ts`

**Step 1: 写 hook**

```typescript
"use client";
import { useState, useEffect, useRef } from "react";

export interface PlanOrder {
  id: string;
  side: "buy" | "sell";
  op: ">=" | "<=";
  price: number;
  shares: number | null;
  volume_min: number | null;
  consecutive_days: number | null;
  trailing: { pct: number; watermark: number | null; active: boolean } | null;
  label: string;
  triggered: boolean;
  triggered_at: string | null;
}

export interface PlanPosition {
  cost: number | null;
  shares: number | null;
  price: number | null;
  change_pct: number | null;
  name: string;
}

export interface TradePlan {
  id: string;
  name: string;
  symbol: string;
  status: "active" | "paused";
  scope?: "real" | "sim";
  created_at: string;
  orders: PlanOrder[];
  position?: PlanPosition | null;
  lot_size?: number | null;
}

export type PlanMap = Record<string, TradePlan[]>; // keyed by symbol

export function useTradePlans(pollMs = 30000) {
  const [planMap, setPlanMap] = useState<PlanMap>({});
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function fetch_() {
    try {
      const res = await fetch("/api/trade-plans");
      if (!res.ok) return;
      const data = await res.json();
      // data.plans is Record<planId, TradePlan>
      const plans: TradePlan[] = Object.entries(data.plans ?? {}).map(
        ([id, p]) => ({ ...(p as Omit<TradePlan, "id">), id })
      );
      const map: PlanMap = {};
      for (const p of plans) {
        if (!map[p.symbol]) map[p.symbol] = [];
        map[p.symbol].push(p);
      }
      setPlanMap(map);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetch_();
    timerRef.current = setInterval(fetch_, pollMs);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [pollMs]);

  return { planMap, loading, refresh: fetch_ };
}
```

**Step 2: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors for this file.

**Step 3: Commit**

```bash
git add web/app/hooks/useTradePlans.ts
git commit -m "feat: add useTradePlans hook for independent plan polling"
```

---

## Task 2: 新建 `StockDrawer` 组件（骨架）

**Files:**
- Create: `web/app/components/StockDrawer.tsx`

**Step 1: 写 Drawer 骨架（先不含 PlanCard，只含基本信息 + 告警 + 标签 + 占位）**

从 `manage/page.tsx` 复制以下类型和组件，放入 `StockDrawer.tsx`：
- `PlanOrder`, `PlanPosition`, `TradePlan` 类型（改从 `useTradePlans` 导入）
- `EditableCell` 组件（完整复制，manage/page.tsx 也保留其本地副本，Task 5 再删）
- `Toast` 组件（完整复制）
- `ORDER_TYPE_OPTIONS`, `orderTypeLabel` 工具函数

```typescript
"use client";
import { useState, useCallback } from "react";
import { D } from "../theme";
import type { Service } from "../types";
import type { TradePlan, PlanMap } from "../hooks/useTradePlans";
import { tagColor } from "../lib/tag-utils";

// [复制 EditableCell from manage/page.tsx]
// [复制 Toast from manage/page.tsx]
// [复制 PlanOrder types already from useTradePlans]

interface StockDrawerProps {
  symbol: string | null;           // null = closed
  services: Service[];
  planMap: PlanMap;
  allTags: string[];
  onClose: () => void;
  onRefreshPlans: () => void;
  onRefreshMetrics?: () => void;
}

export function StockDrawer({
  symbol, services, planMap, allTags, onClose, onRefreshPlans
}: StockDrawerProps) {
  const service = services.find((s) => s.id === symbol);
  const plans = symbol ? (planMap[symbol] ?? []) : [];

  if (!symbol) return null;

  return (
    <>
      {/* backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 200,
        }}
      />
      {/* drawer panel */}
      <div
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: 440,
          background: D.bg,
          borderLeft: `1px solid ${D.currentLine}`,
          zIndex: 201,
          display: "flex", flexDirection: "column",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 13,
          color: D.fg,
          overflowY: "auto",
        }}
      >
        {/* header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 16px",
          borderBottom: `1px solid ${D.currentLine}`,
          background: D.currentLine,
          position: "sticky", top: 0, zIndex: 1,
        }}>
          <span style={{ color: D.cyan, fontWeight: 700 }}>
            {symbol}{"  "}{service?.name ?? ""}
          </span>
          <span
            onClick={onClose}
            style={{ cursor: "pointer", color: D.comment, fontSize: 16 }}
          >×</span>
        </div>

        <div style={{ padding: "12px 16px", flex: 1 }}>
          {/* TODO: BasicInfo section */}
          {/* TODO: AlertThreshold section */}
          {/* TODO: Tags section */}
          {/* TODO: TradePlans section */}
          <div style={{ color: D.comment }}>（内容即将添加）</div>
        </div>
      </div>
    </>
  );
}
```

**Step 2: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

**Step 3: Commit**

```bash
git add web/app/components/StockDrawer.tsx
git commit -m "feat: StockDrawer skeleton with backdrop and header"
```

---

## Task 3: Drawer 基本信息 + 告警阈值 + 标签编辑

**Files:**
- Modify: `web/app/components/StockDrawer.tsx`

**Step 1: 实现基本信息区块（成本/股数 EditableCell）**

在 StockDrawer 的 `<div style={{ padding... }}>` 内，替换 TODO 占位符：

```typescript
// 基本信息
function BasicInfo() {
  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" } | null>(null);

  async function saveField(field: "cost" | "shares", val: string) {
    const num = parseFloat(val);
    if (isNaN(num)) return;
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "update", code: symbol, [field]: num }),
    });
    setToast(res.ok ? { msg: "已保存", type: "ok" } : { msg: "保存失败", type: "err" });
    setTimeout(() => setToast(null), 2000);
    onRefreshPlans(); // triggers metrics refresh too
  }

  return (
    <section style={{ marginBottom: 16 }}>
      <div style={{ color: D.comment, fontSize: 11, marginBottom: 6 }}>基本信息</div>
      <div style={{ display: "flex", gap: 16 }}>
        <span>成本 <EditableCell value={service?.cost} onSave={(v) => saveField("cost", v)}
          width="80px" isNumber placeholder="-" /></span>
        <span>股数 <EditableCell value={service?.shares} onSave={(v) => saveField("shares", v)}
          width="80px" isNumber placeholder="-" /></span>
      </div>
      {toast && <Toast message={toast.msg} type={toast.type} />}
    </section>
  );
}
```

**Step 2: 实现告警阈值区块**

从 `manage/page.tsx` 参考 above/below 保存逻辑（POST /api/config action=update 含 above/below 字段）：

```typescript
function AlertThresholds() {
  async function saveThreshold(field: "above" | "below", val: string) {
    const num = parseFloat(val);
    if (isNaN(num) && val !== "") return;
    const body: Record<string, unknown> = { action: "update", code: symbol };
    body[field] = val === "" ? null : num;
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  return (
    <section style={{ marginBottom: 16 }}>
      <div style={{ color: D.comment, fontSize: 11, marginBottom: 6 }}>告警阈值</div>
      <div style={{ display: "flex", gap: 16 }}>
        <span>上限 <EditableCell value={service?.above} onSave={(v) => saveThreshold("above", v)}
          width="80px" isNumber placeholder="-" color={D.red} /></span>
        <span>下限 <EditableCell value={service?.below} onSave={(v) => saveThreshold("below", v)}
          width="80px" isNumber placeholder="-" color={D.green} /></span>
      </div>
    </section>
  );
}
```

**Step 3: 实现标签编辑区块**

从 manage/page.tsx 复制 `TagEditor` 组件，在 Drawer 内使用（inline chip 点击编辑模式，与 Manage 相同）：

```typescript
// [复制 TagEditor 组件] — 与 manage/page.tsx 相同实现
function TagsSection() {
  const [tagEditorOpen, setTagEditorOpen] = useState(false);
  async function onSaveTags(code: string, tags: string[]) {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "update", code, tags }),
    });
    setTagEditorOpen(false);
  }
  return (
    <section style={{ marginBottom: 16 }}>
      <div style={{ color: D.comment, fontSize: 11, marginBottom: 6 }}>标签</div>
      <span style={{ cursor: "pointer" }} onClick={() => setTagEditorOpen(v => !v)}>
        {(service?.tags ?? []).map((t) => (
          <span key={t} style={{ background: tagColor(t), color: "#282a36",
            padding: "0 5px", borderRadius: 3, fontSize: 10, marginRight: 3 }}>{t}</span>
        ))}
        <span style={{ color: D.comment, fontSize: 10 }}>+tag</span>
      </span>
      {tagEditorOpen && (
        <TagEditor code={symbol!} currentTags={service?.tags ?? []}
          allTags={allTags} onSave={onSaveTags} onClose={() => setTagEditorOpen(false)} />
      )}
    </section>
  );
}
```

**Step 4: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

**Step 5: Commit**

```bash
git add web/app/components/StockDrawer.tsx
git commit -m "feat: StockDrawer basic info, alert thresholds, tags sections"
```

---

## Task 4: Drawer 交易计划 CRUD 区块

**Files:**
- Modify: `web/app/components/StockDrawer.tsx`

**Step 1: 从 manage/page.tsx 提取 PlanCard 组件**

将 `manage/page.tsx` 中的 `PlanCard` 组件（含 `OrderRow`、`NewOrderForm`、`NewPlanForm`）完整复制到 `StockDrawer.tsx`。

注意：PlanCard 中需要的 API 调用路径、状态逻辑保持不变；只是移动组件位置。

**Step 2: 在 Drawer 内添加 TradePlans 区块**

```typescript
function TradePlansSection() {
  const [showNewForm, setShowNewForm] = useState(false);
  return (
    <section>
      <div style={{ display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 8 }}>
        <span style={{ color: D.comment, fontSize: 11 }}>交易计划</span>
        <button
          onClick={() => setShowNewForm(true)}
          style={{ fontSize: 11, background: "none", border: `1px solid ${D.cyan}`,
            color: D.cyan, padding: "1px 8px", cursor: "pointer", borderRadius: 2 }}
        >+ 新建</button>
      </div>
      {showNewForm && (
        <NewPlanForm
          symbol={symbol!}
          onCreated={() => { setShowNewForm(false); onRefreshPlans(); }}
          onCancel={() => setShowNewForm(false)}
        />
      )}
      {plans.length === 0 && !showNewForm && (
        <div style={{ color: D.comment, fontSize: 11 }}>暂无计划</div>
      )}
      {plans.map((plan) => (
        <PlanCard key={plan.id} plan={plan} onChanged={onRefreshPlans} />
      ))}
    </section>
  );
}
```

**Step 3: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -20
```

**Step 4: 启动 dev server 手动验证 Drawer 可正常打开**

```bash
# dev server 应已运行在 :3120
# 暂时在 page.tsx 临时添加一个测试按钮，点击触发 setDrawerSymbol("HK02722")
```

**Step 5: Commit**

```bash
git add web/app/components/StockDrawer.tsx
git commit -m "feat: StockDrawer trade plans CRUD section (PlanCard integration)"
```

---

## Task 5: Holdings 页面集成 Drawer + 指示器列

**Files:**
- Modify: `web/app/page.tsx`

**Step 1: 导入 hook 和组件**

在 `page.tsx` 顶部 import 区添加：

```typescript
import { useTradePlans } from "./hooks/useTradePlans";
import { StockDrawer } from "./components/StockDrawer";
```

**Step 2: 在 `Home()` 函数内添加状态和 hook**

```typescript
const { planMap, refresh: refreshPlans } = useTradePlans();
const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);
```

**Step 3: 修改 `HoldRow` — 替换类型列，整行可点击**

将 `HoldRow` 中：

```typescript
// 删除这行:
<span style={{ color: D.orange, width: "6ch" }}>{s.star ? "★" : " "}PROD</span>
```

替换为指示器列 + 整行点击：

```typescript
// 在 return 的 <div> 上加 onClick 和 cursor:
<div
  style={{
    display: "flex", whiteSpace: "pre",
    padding: "1px 0",
    borderBottom: `1px solid #191a21`,
    background: nearAlert ? "#44475a33" : "transparent",
    cursor: "pointer",           // 新增
  }}
  onClick={() => setDrawerSymbol(s.id)}   // 新增
>
  {/* 指示器列 - 替换原类型列 */}
  <span style={{ width: "6ch", display: "inline-flex", gap: 1, alignItems: "center" }}>
    {s.star && (
      <span style={{
        border: `1px solid ${D.yellow}`, color: D.yellow,
        fontSize: 10, padding: "0 2px", lineHeight: 1.4,
      }}>★</span>
    )}
    {(planMap[s.id]?.length ?? 0) > 0 && (
      <span style={{
        border: `1px solid ${D.cyan}`, color: D.cyan,
        fontSize: 10, padding: "0 2px", lineHeight: 1.4,
      }}>条</span>
    )}
  </span>
  {/* 其余列保持不变 */}
  <span style={{ color: D.cyan, width: "10ch" }}>{pad(s.id, 9)}</span>
  ...
```

**Step 4: 在 `Home()` 的 return JSX 末尾（`</div>` 前）挂载 Drawer**

```typescript
<StockDrawer
  symbol={drawerSymbol}
  services={services}
  planMap={planMap}
  allTags={/* 从 services 聚合 */ [...new Set(services.flatMap(s => s.tags ?? []))]}
  onClose={() => setDrawerSymbol(null)}
  onRefreshPlans={refreshPlans}
/>
```

**Step 5: TypeScript check + lint**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

**Step 6: 在 dev server 上目测验证**

- Holdings 页面每行有指示器列
- 有计划的股票显示青色「条」框
- 有星标的股票显示黄色「★」框
- 点击行 → Drawer 从右侧滑出

**Step 7: Commit**

```bash
git add web/app/page.tsx
git commit -m "feat: Holdings rows open StockDrawer on click, show plan/star indicators"
```

---

## Task 6: Watching 页面同样集成

**Files:**
- Modify: `web/app/watching/page.tsx`

**Step 1: 同 Task 5，在 watching/page.tsx 做同样修改**

- 导入 `useTradePlans` + `StockDrawer`
- 在 `WatchRow` 中替换类型列（原来是 `DEV` badge）为指示器列
- 整行可点击 → `setDrawerSymbol`
- JSX 末尾挂载 `<StockDrawer>`

**Step 2: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

**Step 3: Commit**

```bash
git add web/app/watching/page.tsx
git commit -m "feat: Watching rows open StockDrawer on click"
```

---

## Task 7: Manage 页面移除交易计划区块

**Files:**
- Modify: `web/app/manage/page.tsx`

**Step 1: 识别要删除的代码**

在 `manage/page.tsx` 中找到：
- `TradePlan` 类型定义（现在由 `useTradePlans` 提供，可删）
- `PlanCard` 组件（已移入 StockDrawer，可删）
- `NewPlanForm` / `NewOrderForm` / `OrderRow` 组件（已移入 StockDrawer，可删）
- `plans` 状态和 `/api/trade-plans` fetch 逻辑
- JSX 中「交易计划」section（`<section>` 整块，含「运行中」「已暂停」分组）
- `ORDER_TYPE_OPTIONS`, `orderTypeLabel` 工具函数（已移入 StockDrawer）

**Step 2: 删除上述代码**

保留：
- `EditableCell` — Manage 股票表仍用到
- `Toast` — Manage 仍用到
- `TagEditor` — Manage 仍用到
- 股票管理表相关的所有 state 和 JSX

**Step 3: TypeScript check**

```bash
cd web && npx tsc --noEmit 2>&1 | head -30
```

**Step 4: 目测 Manage 页面**

Manage 页应只剩：搜索框 + 股票管理表（type/code/name/above/below/star/hide/promote/demote/tags/delete）。

**Step 5: Commit**

```bash
git add web/app/manage/page.tsx
git commit -m "refactor: remove trade plans section from Manage page (moved to StockDrawer)"
```

---

## Task 8: ESC 键关闭 Drawer

**Files:**
- Modify: `web/app/components/StockDrawer.tsx`

**Step 1: 添加 keydown 监听**

```typescript
import { useEffect } from "react";

// 在 StockDrawer 函数体内：
useEffect(() => {
  function handleKey(e: KeyboardEvent) {
    if (e.key === "Escape") onClose();
  }
  if (symbol) {
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }
}, [symbol, onClose]);
```

**Step 2: Commit**

```bash
git add web/app/components/StockDrawer.tsx
git commit -m "feat: StockDrawer closes on ESC key"
```

---

## Task 9: E2E 测试

**Files:**
- Create: `web/screenshots/test_stock_drawer_e2e.mjs`
- Modify: `web/screenshots/test_full_checkup.mjs`

**Step 1: 编写 test_stock_drawer_e2e.mjs**

```javascript
import { chromium } from "playwright";

const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}: ${d || ""}`);

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

await p.goto("http://localhost:3120/?tab=A", { waitUntil: "networkidle" });

// ═══ 1. Indicator column visible ═══
console.log("\n═══ 1. Indicator column ═══");
await p.screenshot({ path: "screenshots/drawer_01_indicators.png" });
// 类型列（★PROD/DEV）不再出现
const body = await p.textContent("body");
body.includes("★PROD") ? ng("type column removed", "still shows ★PROD") : ok("type column removed");

// ═══ 2. Row click opens Drawer ═══
console.log("\n═══ 2. Row click → Drawer ═══");
// 点击第一行
const firstRow = p.locator("div").filter({ hasText: /\d{6}/ }).first();
await firstRow.click();
await p.waitForTimeout(500);
await p.screenshot({ path: "screenshots/drawer_02_open.png" });
const drawerVisible = await p.locator("text=基本信息").isVisible();
drawerVisible ? ok("drawer opens on row click") : ng("drawer did not open");

// ═══ 3. ESC closes Drawer ═══
console.log("\n═══ 3. ESC closes Drawer ═══");
await p.keyboard.press("Escape");
await p.waitForTimeout(300);
const drawerGone = !(await p.locator("text=基本信息").isVisible().catch(() => false));
drawerGone ? ok("ESC closes drawer") : ng("ESC did not close drawer");

// ═══ 4. Backdrop click closes Drawer ═══
console.log("\n═══ 4. Backdrop click closes ═══");
await firstRow.click();
await p.waitForTimeout(300);
await p.mouse.click(100, 400); // click backdrop (left side, outside drawer)
await p.waitForTimeout(300);
await p.screenshot({ path: "screenshots/drawer_03_closed.png" });
const closedByBackdrop = !(await p.locator("text=基本信息").isVisible().catch(() => false));
closedByBackdrop ? ok("backdrop click closes drawer") : ng("backdrop click did not close");

// ═══ 5. Manage page has no plan section ═══
console.log("\n═══ 5. Manage page cleanup ═══");
await p.goto("http://localhost:3120/manage", { waitUntil: "networkidle" });
await p.screenshot({ path: "screenshots/drawer_04_manage.png" });
const manageBody = await p.textContent("body");
manageBody.includes("新建计划") ? ng("plan section removed", "still shows 新建计划") : ok("trade plan section removed from manage");
manageBody.includes("运行中") && manageBody.includes("已暂停") ? ng("plan groups removed", "still shows 运行中/已暂停") : ok("plan groups removed");

await browser.close();
console.log("\nDone.");
```

**Step 2: 运行测试**

```bash
cd web && node screenshots/test_stock_drawer_e2e.mjs
```

Expected: 全 PASS，用 Read 工具查看截图确认 UI 正确。

**Step 3: 更新 test_full_checkup.mjs**

找到 Manage 页相关断言，移除「新建计划」存在的检查（如有），改为检查其不存在。

**Step 4: 运行全量回归**

```bash
cd web && node screenshots/test_full_checkup.mjs 2>&1 | tail -20
```

Expected: 全 PASS 或仅更新的断言变化。

**Step 5: Commit**

```bash
git add web/screenshots/test_stock_drawer_e2e.mjs web/screenshots/test_full_checkup.mjs
git commit -m "test: E2E tests for StockDrawer + update full checkup assertions"
```

---

## Task 10: TypeScript 最终检查 + 截图审查

**Step 1:**

```bash
cd web && npx tsc --noEmit
```

Expected: zero errors.

**Step 2: 查看所有截图**

用 Read 工具查看：
- `web/screenshots/drawer_01_indicators.png` — 验证指示器列正确显示
- `web/screenshots/drawer_02_open.png` — 验证 Drawer 布局正确
- `web/screenshots/drawer_03_closed.png` — 验证关闭后干净
- `web/screenshots/drawer_04_manage.png` — 验证 Manage 页简洁

**Step 3: 最终 commit（如有遗漏）**

```bash
git status
# 如有未提交文件:
git add -p
git commit -m "chore: final cleanup for stock-detail-drawer feature"
```
