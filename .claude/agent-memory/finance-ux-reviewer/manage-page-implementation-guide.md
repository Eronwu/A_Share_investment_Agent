# Manage Page Implementation Guide

## Overview
This document provides copy-paste code snippets for implementing Phase 1 UX improvements (filter + collapse + sticky + grouping). Each section is independent and can be applied incrementally.

## Phase 1: Quick Wins (5 hours total)

### Feature 1: Filter/Search Bar (2 hours)

**Step 1a**: Add state to ManagePage component (after existing state declarations ~line 475)

```typescript
// Add after existing useState declarations
const [filterText, setFilterText] = useState("");
const [filterTab, setFilterTab] = useState<"all" | "holdings" | "watching">("all");
const filterInputRef = useRef<HTMLInputElement>(null);
```

**Step 1b**: Create FilterBar component (insert before ManagePage, around line 474)

```typescript
/* ── FilterBar (sticky) ── */
function FilterBar({
  filterText,
  setFilterText,
  filterTab,
  setFilterTab,
  holdingsCount,
  watchingCount,
}: {
  filterText: string;
  setFilterText: (t: string) => void;
  filterTab: "all" | "holdings" | "watching";
  setFilterTab: (t: "all" | "holdings" | "watching") => void;
  holdingsCount: number;
  watchingCount: number;
}) {
  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        background: D.currentLine,
        padding: "10px 20px",
        borderBottom: `1px solid #191a21`,
        display: "flex",
        gap: 12,
        alignItems: "center",
        zIndex: 50,
        fontSize: 13,
      }}
    >
      <span style={{ color: D.comment }}>🔍 filter:</span>
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
          width: 180,
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 12,
          borderRadius: 2,
          outline: "none",
        }}
      />
      <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
        {[
          { label: "all", value: "all", count: holdingsCount + watchingCount },
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
              padding: "4px 10px",
              fontSize: 11,
              cursor: "pointer",
              borderRadius: 2,
              fontFamily: "JetBrains Mono, monospace",
              fontWeight: 700,
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

**Step 1c**: Add filter logic (in ManagePage render, after getting entries at line 634)

```typescript
// After: const entries = Object.entries(config.watchlist);
// Add filtering:
let filteredEntries = entries;

if (filterText || filterTab !== "all") {
  filteredEntries = entries.filter(([code, entry]) => {
    // Type filter
    const typeMatch =
      filterTab === "all" ||
      (filterTab === "holdings" && entry.type === "holding") ||
      (filterTab === "watching" && entry.type !== "holding");

    if (!typeMatch) return false;

    // Text filter (case-insensitive)
    if (filterText) {
      const codeMatch = code.toUpperCase().includes(filterText);
      const nameMatch = (entry.name || "").toUpperCase().includes(filterText);
      return codeMatch || nameMatch;
    }

    return true;
  });
}

// Update holdings/watching to use filtered list
const filteredHoldings = filteredEntries.filter(([, v]) => v.type === "holding");
const filteredWatching = filteredEntries.filter(([, v]) => v.type !== "holding");
```

**Step 1d**: Render FilterBar in JSX (after navBar, before scrollable body ~line 693)

```typescript
<FilterBar
  filterText={filterText}
  setFilterText={setFilterText}
  filterTab={filterTab}
  setFilterTab={setFilterTab}
  holdingsCount={holdings.length}
  watchingCount={watching.length}
/>
```

**Step 1e**: Update table rendering to use filtered lists (replace lines 771-802)

```typescript
// Replace the entire holdings and watching map sections:

{holdings.length > 0 && (
  <div style={{ display: "flex", gap: 4, padding: "4px 0", whiteSpace: "nowrap" }}>
    <ColHeader width="46px">TYPE</ColHeader>
    <ColHeader width="90px">CODE</ColHeader>
    <ColHeader width="110px">NAME</ColHeader>
    <ColHeader width="80px">COST</ColHeader>
    <ColHeader width="80px">SHARES</ColHeader>
    <span style={{ width: 30 }} />
  </div>
)}
{filteredHoldings.map(([code, entry]) => (
  <StockRow
    key={code}
    code={code}
    entry={entry}
    isHolding
    promoting={promoting}
    promoCost={promoCost}
    promoShares={promoShares}
    setPromoting={setPromoting}
    setPromoCost={setPromoCost}
    setPromoShares={setPromoShares}
    onUpdateField={handleUpdateField}
    onUpdateType={handleUpdateType}
    onPromote={handlePromote}
    onRemove={handleRemove}
    onToggleHidden={handleToggleHidden}
    onToggleStar={handleToggleStar}
  />
))}
{filteredHoldings.length === 0 && (
  <div style={{ color: D.comment, padding: "6px 0" }}>
    {filterText || filterTab !== "all"
      ? "No results. Try a different filter."
      : "No holdings. Add a stock with type \"holding\" below."}
  </div>
)}

{/* ── staging ── */}
<SectionHeader># ── staging ({filteredWatching.length}) ──</SectionHeader>
{filteredWatching.length > 0 && (
  <div style={{ display: "flex", gap: 4, padding: "4px 0", whiteSpace: "nowrap" }}>
    <ColHeader width="46px">TYPE</ColHeader>
    <ColHeader width="90px">CODE</ColHeader>
    <ColHeader width="110px">NAME</ColHeader>
    <span style={{ width: 30 }} />
  </div>
)}
{filteredWatching.map(([code, entry]) => (
  <StockRow
    key={code}
    code={code}
    entry={entry}
    isHolding={false}
    promoting={promoting}
    promoCost={promoCost}
    promoShares={promoShares}
    setPromoting={setPromoting}
    setPromoCost={setPromoCost}
    setPromoShares={setPromoShares}
    onUpdateField={handleUpdateField}
    onUpdateType={handleUpdateType}
    onPromote={handlePromote}
    onRemove={handleRemove}
    onToggleHidden={handleToggleHidden}
    onToggleStar={handleToggleStar}
  />
))}
{filteredWatching.length === 0 && (
  <div style={{ color: D.comment, padding: "6px 0" }}>
    {filterText || filterTab !== "all"
      ? "No results. Try a different filter."
      : "No watching stocks."}
  </div>
)}
```

---

### Feature 2: Collapsible Sections (1 hour)

**Step 2a**: Add state (after filterTab ~line 483)

```typescript
const [settingsExpanded, setSettingsExpanded] = useState(false);
const [alertsExpanded, setAlertsExpanded] = useState(false);
```

**Step 2b**: Update SectionHeader component (lines 157-172) to support toggle

```typescript
function SectionHeader({
  children,
  onClick,
  expanded,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  expanded?: boolean;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        color: D.comment,
        padding: "10px 0 6px",
        borderBottom: `1px solid ${D.currentLine}`,
        marginBottom: 4,
        fontSize: 13,
        cursor: onClick ? "pointer" : "default",
        userSelect: "none",
      }}
    >
      {onClick && (
        <span style={{ display: "inline-block", width: 12, color: D.pink, marginRight: 4 }}>
          {expanded ? "▼" : "▶"}
        </span>
      )}
      {children}
    </div>
  );
}
```

**Step 2c**: Update section rendering (~lines 706-757)

```typescript
{/* ── settings ── */}
<SectionHeader
  onClick={() => setSettingsExpanded(!settingsExpanded)}
  expanded={settingsExpanded}
>
  # ── settings ──
</SectionHeader>
{settingsExpanded && (
  <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "6px 0", flexWrap: "wrap" }}>
    {/* existing settings code ... */}
  </div>
)}

{/* ── alert levels ── */}
<SectionHeader
  onClick={() => setAlertsExpanded(!alertsExpanded)}
  expanded={alertsExpanded}
>
  # ── alert levels ──
</SectionHeader>
{alertsExpanded && (
  <div>
    {([
      { label: "L1 ★ Star", prefix: "l1", keys: ["trigger_pct", "delta_pct", "cooldown_min"], color: D.yellow },
      { label: "L2 Holding", prefix: "l2", keys: ["trigger_pct", "delta_pct", "cooldown_min"], color: D.orange },
      { label: "L3 Watching", prefix: "l3", keys: ["cooldown_min"], color: D.comment },
    ] as const).map((tier) => (
      <div key={tier.prefix} style={{ display: "flex", gap: 12, alignItems: "center", padding: "4px 0", flexWrap: "wrap" }}>
        {/* existing tier code ... */}
      </div>
    ))}
    <div style={{ padding: "4px 0" }}>
      <button style={{ ...btnStyle, fontSize: 12, padding: "3px 12px" }} onClick={handleSaveSettings}>
        Save Levels
      </button>
    </div>
  </div>
)}
```

---

### Feature 3: Sticky Table Headers (1 hour)

**Step 3**: Wrap production and watching sections with sticky headers

```typescript
{/* ── production ── */}
<SectionHeader># ── production ({holdings.length}) ──</SectionHeader>
<div style={{ position: "relative" }}>
  {holdings.length > 0 && (
    <div
      style={{
        position: "sticky",
        top: 0,
        background: D.bg,
        zIndex: 10,
        display: "flex",
        gap: 4,
        padding: "4px 0",
        whiteSpace: "nowrap",
        borderBottom: `1px solid ${D.currentLine}`,
        paddingBottom: 6,
        marginBottom: 6,
      }}
    >
      <ColHeader width="46px">TYPE</ColHeader>
      <ColHeader width="90px">CODE</ColHeader>
      <ColHeader width="110px">NAME</ColHeader>
      <ColHeader width="80px">COST</ColHeader>
      <ColHeader width="80px">SHARES</ColHeader>
      <span style={{ width: 30 }} />
    </div>
  )}
  {filteredHoldings.map(([code, entry]) => (
    <StockRow key={code} /* ...props... */ />
  ))}
</div>

{/* ── staging ── */}
<SectionHeader># ── staging ({watching.length}) ──</SectionHeader>
<div style={{ position: "relative" }}>
  {filteredWatching.length > 0 && (
    <div
      style={{
        position: "sticky",
        top: 0,
        background: D.bg,
        zIndex: 10,
        display: "flex",
        gap: 4,
        padding: "4px 0",
        whiteSpace: "nowrap",
        borderBottom: `1px solid ${D.currentLine}`,
        paddingBottom: 6,
        marginBottom: 6,
      }}
    >
      <ColHeader width="46px">TYPE</ColHeader>
      <ColHeader width="90px">CODE</ColHeader>
      <ColHeader width="110px">NAME</ColHeader>
      <span style={{ width: 30 }} />
    </div>
  )}
  {filteredWatching.map(([code, entry]) => (
    <StockRow key={code} /* ...props... */ />
  ))}
</div>
```

---

### Feature 4: Group by Market (1 hour)

**Step 4a**: Add helper function (before ManagePage component)

```typescript
function groupByMarket(entries: [string, WatchEntry][]) {
  const ashare: typeof entries = [];
  const hk: typeof entries = [];

  entries.forEach((entry) => {
    if (entry[0].startsWith("HK")) {
      hk.push(entry);
    } else {
      ashare.push(entry);
    }
  });

  return { ashare, hk };
}

function getMarketLabel(code: string): string {
  if (code.startsWith("HK")) return "HK";
  if (code.startsWith("6")) return "SH";
  return "SZ"; // default to Shenzhen for 0, 3, others
}
```

**Step 4b**: Update production section rendering (replace holdings section ~line 760-783)

```typescript
{/* ── production ── */}
<SectionHeader># ── production ({filteredHoldings.length}) ──</SectionHeader>

{(() => {
  const { ashare, hk } = groupByMarket(filteredHoldings);

  return (
    <>
      {ashare.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ color: D.comment, fontSize: 12, padding: "6px 0 4px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 6 }}>
            A-share ({ashare.length})
          </div>
          <div style={{ position: "relative" }}>
            <div
              style={{
                position: "sticky",
                top: 0,
                background: D.bg,
                zIndex: 10,
                display: "flex",
                gap: 4,
                padding: "4px 0",
                whiteSpace: "nowrap",
                borderBottom: `1px solid ${D.currentLine}`,
                paddingBottom: 6,
                marginBottom: 6,
              }}
            >
              <ColHeader width="46px">TYPE</ColHeader>
              <ColHeader width="90px">CODE</ColHeader>
              <ColHeader width="50px">MKT</ColHeader>
              <ColHeader width="110px">NAME</ColHeader>
              <ColHeader width="80px">COST</ColHeader>
              <ColHeader width="80px">SHARES</ColHeader>
              <span style={{ width: 30 }} />
            </div>
            {ashare.map(([code, entry]) => (
              <StockRow key={code} code={code} entry={entry} isHolding /* ...props... */ />
            ))}
          </div>
        </div>
      )}

      {hk.length > 0 && (
        <div>
          <div style={{ color: D.comment, fontSize: 12, padding: "6px 0 4px", borderBottom: `1px solid ${D.currentLine}`, marginBottom: 6 }}>
            HK ({hk.length})
          </div>
          <div style={{ position: "relative" }}>
            <div
              style={{
                position: "sticky",
                top: 0,
                background: D.bg,
                zIndex: 10,
                display: "flex",
                gap: 4,
                padding: "4px 0",
                whiteSpace: "nowrap",
                borderBottom: `1px solid ${D.currentLine}`,
                paddingBottom: 6,
                marginBottom: 6,
              }}
            >
              <ColHeader width="46px">TYPE</ColHeader>
              <ColHeader width="90px">CODE</ColHeader>
              <ColHeader width="50px">MKT</ColHeader>
              <ColHeader width="110px">NAME</ColHeader>
              <ColHeader width="80px">COST</ColHeader>
              <ColHeader width="80px">SHARES</ColHeader>
              <span style={{ width: 30 }} />
            </div>
            {hk.map(([code, entry]) => (
              <StockRow key={code} code={code} entry={entry} isHolding /* ...props... */ />
            ))}
          </div>
        </div>
      )}

      {ashare.length === 0 && hk.length === 0 && (
        <div style={{ color: D.comment, padding: "6px 0" }}>
          {filterText || filterTab !== "all"
            ? "No results. Try a different filter."
            : "No holdings. Add a stock with type \"holding\" below."}
        </div>
      )}
    </>
  );
})()}
```

**Step 4c**: Update StockRow to include market indicator

In StockRow component (around line 295), add after code span:

```typescript
<span
  style={{
    width: 50,
    display: "inline-block",
    color: code.startsWith("HK") ? D.cyan : D.orange,
    fontSize: 11,
    fontWeight: 700,
  }}
>
  {getMarketLabel(code)}
</span>
```

Adjust `ColHeader` widths accordingly in both production and watching sections.

---

## Testing Checklist

After implementing Phase 1:

- [ ] Filter by code: type "000001" → shows matching stocks
- [ ] Filter by name: type "银行" → shows matching stocks
- [ ] Tab switching: click "holdings" → shows only holdings, count updates
- [ ] Clear filter: type in filter → ESC clears it
- [ ] Settings collapse: click "# ── settings ──" → collapses/expands
- [ ] Scroll holdings: headers stay at top when scrolling
- [ ] Market grouping: holdings split into A-share / HK sections
- [ ] Empty state: filter with no results → shows "No results" message
- [ ] Performance: smooth scroll with 70+ stocks (no lag)

---

## Phase 2: UX Improvements (6 hours)

These features are more complex and should be implemented after Phase 1 is tested:

- **Batch select + actions** (3h) — Checkboxes, batch hide/star/remove
- **Keyboard shortcuts** (2h) — Cmd+F for filter, Cmd+K for add, etc.
- **Compact mode** (1h) — Hide cost/shares by default

See `manage-page-review.md` section "Important Improvements" for detailed specs.

---

## Common Mistakes to Avoid

1. **Don't break existing API calls** — All mutations still POST to `/api/config`
2. **Don't change MonitorConfig interface** — Filter is UI-only, not persisted
3. **Don't forget refs** — If you add keyboard shortcuts, use refs for input focus
4. **Don't over-optimize** — For 70 stocks, filtering is O(n) and fast; no need for advanced indexing
5. **Don't forget mobile** — Ensure filter bar and headers work on smaller screens (optional for Phase 1)

---

## Performance Notes

- **Filter**: 70 stocks, O(n) string search — <1ms per keystroke
- **Re-render**: React batches state updates; no perceptible lag
- **Sticky positioning**: CSS-only, no JavaScript overhead
- **Grouping**: Single pass through entries, no external libraries needed

No performance concerns; all Phase 1 features are client-side and lightweight.
