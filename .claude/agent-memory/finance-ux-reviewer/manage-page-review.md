# Manage Page UX Review — 70+ Stock Scalability

**Target user**: Personal portfolio manager with 70 holdings + 10 watchlist stocks, frequent rebalancing, Dracula terminal aesthetic.

## Current State Analysis

**File**: `web/app/manage/page.tsx` (867 lines)

**Layout structure**:
- Fixed titlebar (30px) + nav bar (40px)
- Scrollable body with settings, alert levels, production (holdings), staging (watching), add form
- All rows in single column, must scroll to find any stock
- Inline editing (EditableCell) for cost/shares
- Promote/demote via row-level buttons

**Data structure** (from `types.ts`):
```typescript
watchlist: Record<string, WatchEntry>
{
  name: string;
  type?: "holding" | "watching";
  cost?: number;
  shares?: number;
  hidden?: boolean;
  star?: boolean;
}
```

**Current operations**:
- Type toggle (holding ↔ watching)
- Star/unstar (L1 priority)
- Hide/show (holdings only)
- Edit cost/shares inline
- Promote to holding (with modal)
- Remove stock
- Settings adjustment
- Add new stock (at bottom)

## Critical UX Issues

### 1. No Search/Filter (MAJOR)
With 70 stocks, finding a single stock requires scrolling entire page. User experience degradation is severe.

**Issue**: User must scroll through entire list to find, edit, or remove one stock.
**Financial impact**: Increases operational friction for rebalancing decisions.
**Evidence**: 70 holdings + 10 watching = 80 rows × 30px ≈ 2400px scroll height.

**Recommendation**: Add persistent filter bar at top of scrollable body.

```
Layout:
┌─ TitleBar (30px) ──────────────────┐
├─ NavBar (40px) ────────────────────┤
├─ FILTER BOX (sticky, 50px) ────────┤  ← NEW: "Filter by code/name"
│ 🔍 [____code____] ★[all] 📊[All]   │    - Real-time case-insensitive filter
├─────────────────────────────────────┤    - Tab: All | Holdings | Watching
│ Settings                            │
│ Alert Levels                        │
│ ── production (N) ──────────────────┤
│ [filtered results]                  │
└─────────────────────────────────────┘
```

**Implementation**:
- Add `filterText: string` state in ManagePage
- Add `filterTab: "all" | "holdings" | "watching"` state
- Filter entries: `entries.filter(([code, entry]) => code.includes(filterText.toUpperCase()) || entry.name.includes(filterText))`
- Sticky position on filter box

### 2. Settings/Alert Levels at Top (BAD FOR LARGE LISTS)
Settings take up ~150px of vertical space, but users touch them rarely.

**Issue**: Scrolling past settings/alert config to reach first stock is wasted scrolling on every interaction.
**Better pattern**: Collapsible section or separate page.

**Recommendation**:
- Option A: Collapsible sections (Settings/Alerts start closed)
- Option B: Move to modal/accordion
- Option C: Separate settings page (least disruptive)

Suggest **Option A** for minimal changes:

```typescript
const [settingsExpanded, setSettingsExpanded] = useState(false);
const [alertsExpanded, setAlertsExpanded] = useState(false);

<SectionHeader onClick={() => setSettingsExpanded(!settingsExpanded)}>
  {settingsExpanded ? "▼" : "▶"} # ── settings ──
</SectionHeader>
{settingsExpanded && (
  <div>... settings content ...</div>
)}
```

### 3. No Grouping By Market (A-share vs HK)
With mixed A-share/HK portfolio, there's no visual separation.

**Issue**: Code prefixes differ (A-share: 6 digits, HK: HK prefix), but no grouping. Hard to scan.
**Financial context**: Portfolio rebalancing often separates by market (mainland taxes, liquidation rules, margin rules differ).

**Recommendation**: Add market grouping after filtering.

```typescript
// Group filtered entries by market
function groupByMarket(entries: [string, WatchEntry][]) {
  return {
    ashare: entries.filter(([c]) => !c.startsWith('HK')),
    hk: entries.filter(([c]) => c.startsWith('HK')),
  };
}

// Render with visual separators
const { ashare, hk } = groupByMarket(filtered);
{ashare.length > 0 && (
  <SectionHeader>── A-share ({ashare.length}) ──</SectionHeader>
)}
{ashare.map(([code, entry]) => <StockRow ... />)}
{hk.length > 0 && (
  <SectionHeader>── HK ({hk.length}) ──</SectionHeader>
)}
{hk.map(([code, entry]) => <StockRow ... />)}
```

### 4. Promote Modal Not Inline (FRICTION)
Current: Click "↑ PROD" → Inline form appears on same row → Type cost/shares → Confirm.

**Issue**:
- Form occupies row 2, visual disconnect
- For many watchlist stocks, each promote action requires two steps
- No batch operations (e.g., promote 3 at once with same cost)

**Recommendation for now**: Keep inline (simpler), but improve batch promote.

Add **quick promote** for watchlist stocks:
- Shift+Click "↑ PROD" → Shows mini dialog: cost + shares + confirm
- Stores last cost/shares used → auto-fills next promote

```typescript
const [promoCost, setPromoCost] = useState("");
const [promoShares, setPromoShares] = useState("");

// Remember last used for UX
useEffect(() => {
  localStorage.setItem("lastPromoCost", promoCost);
  localStorage.setItem("lastPromoShares", promoShares);
}, [promoCost, promoShares]);

// On load
useEffect(() => {
  setPromoCost(localStorage.getItem("lastPromoCost") || "");
  setPromoShares(localStorage.getItem("lastPromoShares") || "");
}, []);
```

### 5. No Sticky Header for Stock Table
When scrolling through 70 holdings, column headers (CODE, NAME, COST, SHARES) disappear.

**Issue**: User loses column context while editing deep rows.
**Financial impact**: Potential error (editing wrong field).

**Recommendation**: Make headers sticky above scrollable body.

```typescript
// Wrapper for holdings section
<div style={{ position: "relative", marginBottom: 20 }}>
  <div style={{ position: "sticky", top: 50, background: D.bg, zIndex: 10, paddingTop: 4 }}>
    <ColHeader ...>CODE</ColHeader>
    ...
  </div>
  {holdings.map(row => <StockRow ... />)}
</div>
```

## Important Improvements

### 1. Add "Quick Add" Widget at Top
Currently "Add stock" form is at bottom (scroll to reach).

**Issue**: User must scroll 2000px+ to add a stock to watchlist.
**Improvement**: Add compact "Add" input in filter bar or floating action button.

```typescript
// In filter bar row 2:
<label style={{ display: "flex", gap: 4, alignItems: "center" }}>
  <span style={{ color: D.comment, fontSize: 11 }}>quick add:</span>
  <input placeholder="code" ... />
  <select>{holding|watching}</select>
  <button>+</button>
</label>
```

### 2. Batch Edit (Multi-Select)
With 70 stocks, common operations:
- Hide multiple old positions at once
- Star/unstar several related stocks
- Update cost basis for whole sector

**Improvement**: Add checkbox column, batch actions bar.

```typescript
// Add to StockRow
<input
  type="checkbox"
  checked={selected.has(code)}
  onChange={() => toggleSelection(code)}
  style={{ width: 16, height: 16 }}
/>

// Batch actions (sticky bottom bar when selected > 0)
{selected.size > 0 && (
  <div style={{ position: "sticky", bottom: 0, background: D.currentLine, padding: 8, display: "flex", gap: 8 }}>
    <button onClick={() => batchHide()}>Hide ({selected.size})</button>
    <button onClick={() => batchShow()}>Show ({selected.size})</button>
    <button onClick={() => batchStar()}>Star ({selected.size})</button>
    <button onClick={() => batchRemove()}>Remove ({selected.size})</button>
  </div>
)}
```

### 3. Keyboard Shortcuts
Power users with 70 stocks will heavily rely on shortcuts.

**Improvements**:
- `Cmd+F` → Focus filter input
- `Cmd+K` → Quick add dialog
- `Escape` → Clear filter
- In row: `E` → Edit, `H` → Toggle hide, `S` → Toggle star, `P` → Promote, `X` → Remove

```typescript
useEffect(() => {
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.metaKey && e.key === 'f') {
      e.preventDefault();
      filterInputRef.current?.focus();
    }
    if (e.metaKey && e.key === 'k') {
      e.preventDefault();
      setAddCode(""); // focus quick add
    }
    if (e.key === 'Escape') {
      setFilterText("");
    }
  };
  window.addEventListener('keydown', handleKeyDown);
  return () => window.removeEventListener('keydown', handleKeyDown);
}, []);
```

### 4. Column Resizing / Reduce Visual Density
Current row has many columns: TYPE, CODE, NAME, COST, SHARES, TYPE-TOGGLE, STAR, HIDE, DELETE (9 elements).

**Issue**: Cramped, hard to read code/name with long Chinese names.
**Improvement**:
- Make NAME column wider (min 150px)
- Stack cost/shares on second row if needed
- Add "compact mode" toggle

```typescript
// Compact mode: hide cost/shares by default, show on hover
const [compactMode, setCompactMode] = useState(true);

// In settings bar:
<button onClick={() => setCompactMode(!compactMode)}>
  {compactMode ? "Show Cost/Shares" : "Hide Cost/Shares"}
</button>

{compactMode ? (
  <div style={{ fontSize: 11, color: D.comment }}>
    cost: {entry.cost}, shares: {entry.shares}
  </div>
) : (
  <>
    <EditableCell value={entry.cost} ... />
    <EditableCell value={entry.shares} ... />
  </>
)}
```

### 5. Display Stock Type (Indicator)
No visual indicator for A-share vs ETF (A-shares can be 300xxx ETF, 519xxx fund). User must remember code patterns.

**Improvement**: Add type badge.

```typescript
// Detect type from code
function detectStockType(code: string) {
  if (code.startsWith('HK')) return 'HK';
  if (['300xxx', '519xxx', '163xxx'].some(p => code.includes(p))) return 'ETF';
  if (code.startsWith('0') || code.startsWith('3')) return 'SZ';
  if (code.startsWith('6')) return 'SH';
  return 'A';
}

// Render badge
<span style={{ fontSize: 10, padding: "1px 4px", background: marketColor, color: D.bg }}>
  {detectStockType(code)}
</span>
```

## Nice-to-Have Enhancements

1. **Export/Import Config**
   - "Export watchlist as CSV" button
   - "Import from CSV" (allows backup/sharing)

2. **Historical Cost Tracking**
   - Show when cost was last updated
   - Track multiple cost entries over time

3. **Performance Dashboard**
   - Summary stats: total holdings value, total P&L, avg unrealized return
   - Show in sticky header

4. **Alert Rule Quick Editor**
   - Per-stock above/below thresholds inline
   - Currently in web/app/api/metrics (hidden from manage page)

5. **Sync Indicator**
   - Show last config sync time, API status
   - Visual indicator when unsaved changes exist

## Specific Component Recommendations

### ManagePage State

```typescript
// Add these states
const [filterText, setFilterText] = useState("");
const [filterTab, setFilterTab] = useState<"all" | "holdings" | "watching">("all");
const [settingsExpanded, setSettingsExpanded] = useState(false);
const [alertsExpanded, setAlertsExpanded] = useState(false);
const [selected, setSelected] = useState<Set<string>>(new Set());
const [compactMode, setCompactMode] = useState(true);

// Add refs for keyboard shortcuts
const filterInputRef = useRef<HTMLInputElement>(null);
const addCodeInputRef = useRef<HTMLInputElement>(null);
```

### Filter Bar Component (NEW)

```typescript
function FilterBar({
  filterText,
  setFilterText,
  filterTab,
  setFilterTab,
  totalCount,
  holdingsCount,
  watchingCount,
}: {
  filterText: string;
  setFilterText: (t: string) => void;
  filterTab: "all" | "holdings" | "watching";
  setFilterTab: (t: "all" | "holdings" | "watching") => void;
  totalCount: number;
  holdingsCount: number;
  watchingCount: number;
}) {
  return (
    <div style={{
      position: "sticky",
      top: 0,
      background: D.currentLine,
      padding: "8px 20px",
      borderBottom: `1px solid #191a21`,
      display: "flex",
      gap: 12,
      alignItems: "center",
      zIndex: 50,
      fontSize: 13,
    }}>
      <span style={{ color: D.comment, flex: 1 }}>
        🔍 filter:
        <input
          type="text"
          placeholder="code or name..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value.toUpperCase())}
          style={{
            background: D.bg,
            border: `1px solid ${D.comment}`,
            color: D.fg,
            padding: "4px 8px",
            marginLeft: 8,
            width: 180,
            fontFamily: "JetBrains Mono, monospace",
          }}
        />
      </span>
      <div style={{ display: "flex", gap: 8 }}>
        {[
          { label: "all", value: "all", count: totalCount },
          { label: "holdings", value: "holdings", count: holdingsCount },
          { label: "watching", value: "watching", count: watchingCount },
        ].map((tab) => (
          <button
            key={tab.value}
            onClick={() => setFilterTab(tab.value as any)}
            style={{
              background: filterTab === tab.value ? D.purple : "transparent",
              color: filterTab === tab.value ? D.bg : D.comment,
              border: `1px solid ${filterTab === tab.value ? D.purple : D.comment}`,
              padding: "2px 8px",
              fontSize: 11,
              cursor: "pointer",
              borderRadius: 2,
              fontFamily: "JetBrains Mono, monospace",
            }}
          >
            {tab.label} ({tab.count})
          </button>
        ))}
      </div>
    </div>
  );
}
```

### StockRow Improvements

```typescript
// Add checkbox at start
<input
  type="checkbox"
  checked={selected.has(code)}
  onChange={() => {
    const newSet = new Set(selected);
    if (newSet.has(code)) newSet.delete(code);
    else newSet.add(code);
    setSelected(newSet);
  }}
  style={{ marginRight: 4, cursor: "pointer" }}
/>

// Add market indicator
<span style={{
  fontSize: 10,
  padding: "1px 4px",
  background: code.startsWith('HK') ? D.cyan : D.orange,
  color: D.bg,
  borderRadius: 2,
}}>
  {code.startsWith('HK') ? 'HK' : 'CN'}
</span>

// In compact mode, show cost/shares on second line
{compactMode && isHolding && (
  <div style={{ fontSize: 11, color: D.comment, paddingLeft: 50, marginTop: 2 }}>
    ↳ cost: {entry.cost}, shares: {entry.shares}
  </div>
)}
```

## Prioritized Roadmap

| Priority | Feature | Effort | Impact | Est. Hours |
|----------|---------|--------|--------|-----------|
| P0 | Add filter/search bar | 2h | High (essential for 70+ stocks) | 2 |
| P0 | Collapsible settings/alerts | 1h | Medium (reduces initial scroll) | 1 |
| P1 | Group by market (A/HK) | 1h | Medium (familiar financial mental model) | 1 |
| P1 | Sticky table headers | 1h | Medium (reduces edit errors) | 1 |
| P1 | Batch select + actions | 3h | High (enables bulk hide/star) | 3 |
| P2 | Keyboard shortcuts | 2h | Medium (power user feature) | 2 |
| P2 | Compact mode toggle | 1h | Low (polish) | 1 |
| P3 | Market badges | 1h | Low (nice-to-have clarity) | 1 |
| P3 | Quick add in filter bar | 1h | Low (convenience) | 1 |

**Quick wins** (implement first): Filter + Collapse + Sticky headers ≈ 4 hours, 70% of benefit.

## Visual Reference: Target Layout (After Improvements)

```
┌─────────────────────────────────────────────────────┐
│ TitleBar (macOS style)                              │
├─────────────────────────────────────────────────────┤
│ Nav: ← monitor | manage                             │
├─────────────────────────────────────────────────────┤
│ 🔍 Filter: [_____] | all(80) holdings(70) watch(10)│ ← STICKY
├─────────────────────────────────────────────────────┤
│ ▶ # ── settings ── [click to expand]                │
├─────────────────────────────────────────────────────┤
│ ▶ # ── alert levels ── [click to expand]            │
├─────────────────────────────────────────────────────┤
│ ▼ # ── production (70) ──────────────────────────────│
│   ☐ TYPE | CODE | NAME | MARKET (sticky headers)    │ ← STICKY
│   ☐ [row] [code] [name] [HK]  ★ H ×                 │
│   ☐ [row] [code] [name] [CN]  ☆ h ×                 │
│   ...                                               │
│                                                     │
│ [Batch Actions: Hide(5) | Star(5) | Remove(5)] ← STICKY BOTTOM
└─────────────────────────────────────────────────────┘
```

## Code-Level Changes Summary

1. **manage/page.tsx**: Add filter/sort/group logic, collapsible sections, sticky components
2. **StockRow**: Add checkbox, market badge, compact mode support
3. **New component**: FilterBar (reusable across dashboard)
4. **No backend changes needed** (all client-side filtering/display)

---

## User Testing Notes (Estimated Impact)

With current state (70+ stocks, no filter):
- Time to find a specific stock: ~30-60 seconds (scroll searching)
- Time to promote 1 stock: 15 seconds
- Time to hide 3 old positions: 3 × 20 seconds = 60 seconds

With proposed improvements:
- Time to find stock: ~5 seconds (Cmd+F → type code)
- Time to promote 1 stock: 8 seconds (stored cost/shares)
- Time to hide 3 positions: 15 seconds (batch select + hide)

**Estimated time savings**: 30-40% reduction in manage page interactions.
