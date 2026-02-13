# Manage Page UX Review — Executive Summary

## Context
The manage page (`web/app/manage/page.tsx`) is the portfolio configuration hub — add/remove stocks, adjust cost basis, toggle holdings/watching status, set alerts, manage star priority. With 70 holdings + 10 watchlist stocks, the current linear scroll layout becomes unmanageable.

## Current State (Problems)

| Issue | Severity | Impact | Why It Matters |
|-------|----------|--------|----------------|
| **No search/filter** | CRITICAL | Finding 1 stock requires scrolling entire page (~2400px) | Portfolio rebalancing becomes slow; friction accumulates |
| **Settings at top of scroll** | HIGH | Settings occupy first 150px but touched rarely | Users scroll past unused controls on every interaction |
| **No A-share vs HK grouping** | HIGH | Mixed market codes without visual separation | Confusing mental model; users must remember code patterns |
| **No sticky table headers** | MEDIUM | Column headers disappear when editing deep rows | Risk of editing wrong field; cognitive load |
| **No batch operations** | MEDIUM | Each hide/star action requires individual clicks | 70+ stocks × operations = inefficient UX |
| **Settings/add form at bottom** | MEDIUM | Must scroll 2000px to add a stock | Asymmetric interaction (find at top, add at bottom) |
| **Dense row layout** | MEDIUM | 9 UI elements per row; cramped on small code/name area | Hard to read Chinese company names with suffixes |
| **No keyboard shortcuts** | LOW | Power users must mouse for every action | Accessibility for frequent operators |

## Quick Wins (High Impact, Low Effort)

### 1. Add Filter/Search Bar (2 hours)
**What**: Sticky filter bar at top of scrollable body with real-time code/name search.
```
🔍 filter: [_____code_____] | all(80) holdings(70) watching(10)
```
**Impact**: 30s → 5s find time. Essential for 70+ stock management.

### 2. Collapsible Settings/Alerts (1 hour)
**What**: Settings/alert sections start closed, click to expand.
```
▶ # ── settings ──        [click to expand]
▶ # ── alert levels ──    [click to expand]
▼ # ── production (70) ── [auto-expanded]
```
**Impact**: Reduces initial scroll clutter; users never see config they don't touch.

### 3. Sticky Table Headers (1 hour)
**What**: CODE/NAME/COST/SHARES headers stay fixed when scrolling through rows.
**Impact**: Reduces edit errors; maintains context while operating deep in list.

### 4. Group by Market (1 hour)
**What**: After filtering, split results into A-share and HK sections with visual separation.
```
── A-share (63) ──
[rows]

── HK (7) ──
[rows]
```
**Impact**: Familiar financial mental model; easier to reason about portfolio composition.

**Combined effort**: ~5 hours. **Expected benefit**: 70% of usability gain.

## Medium-Term Improvements (Better UX)

### 5. Batch Select + Actions (3 hours)
**What**: Checkboxes on rows, sticky action bar at bottom.
```
☐ TYPE | CODE | NAME | ...
☐ [row]
☐ [row]

[Batch Actions: Hide(5) | Show(5) | Star(5) | Remove(5)]  ← sticky bottom
```
**Impact**: Hide 5 old positions in 15 seconds vs. 100 seconds (one at a time).

### 6. Keyboard Shortcuts (2 hours)
**What**:
- `Cmd+F` → Focus filter
- `Cmd+K` → Quick add dialog
- `Escape` → Clear filter
- Per-row: `E` (edit), `H` (toggle hide), `S` (star), `P` (promote), `X` (remove)

**Impact**: Power users operate manage page at terminal speed.

### 7. Compact Mode Toggle (1 hour)
**What**: Hide cost/shares columns by default; show on hover or secondary row.
```
Compact OFF: CODE | NAME | COST | SHARES (current, dense)
Compact ON:  CODE | NAME
             ↳ cost: 16.50, shares: 500 (second row)
```
**Impact**: More breathing room; easier to read long company names.

## Visual Enhancements (Polish)

### 8. Market Badges (1 hour)
**What**: Add small badge (SZ/SH/HK/ETF) per stock.
```
CODE  | NAME              | [SZ] | COST | SHARES
003985| 阿里巴巴         | [HK] | 165  | 200
```
**Impact**: Quick market identification; reduces cognitive load.

### 9. Store Last Promote Values (30 min)
**What**: Remember last cost/shares used when promoting watchlist stocks.
```
First promote: cost=[_____], shares=[_____]
Second promote: cost=[165.00], shares=[200]  ← auto-filled from last
```
**Impact**: Reduces typing for bulk promotions (especially for same-size positions).

## Architecture Impact

All improvements are **client-side only**. No backend changes needed:
- Filter/search: React state in ManagePage
- Collapsible sections: CSS + React state toggle
- Sticky headers: CSS position:sticky
- Batch operations: POST existing `/api/config` API in loop
- Keyboard shortcuts: Window keydown listeners

**No data model changes. API compatibility maintained.**

## Implementation Priority

| Phase | Features | Time | ROI |
|-------|----------|------|-----|
| Phase 1 (Quick Wins) | Filter + Collapse + Sticky headers + Market grouping | 5h | 70% |
| Phase 2 (UX) | Batch select + Shortcuts + Compact mode | 6h | 25% |
| Phase 3 (Polish) | Market badges + Store last promote | 1.5h | 5% |

## File References

- **Main page**: `/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/web/app/manage/page.tsx`
- **Component**: `EditableCell` (lines 10-95) — inline cost/shares editor
- **Component**: `StockRow` (lines 192-471) — single stock row with all actions
- **API route**: `/web/app/api/config` — handles add/update/remove/settings
- **Types**: `/web/app/types.ts` — WatchEntry interface

## Prototype

HTML prototype showing target layout available at:
`/Users/zhul1/Documents/dev/openWorkspace/A_Share_investment_Agent/.claude/agent-memory/finance-ux-reviewer/manage-page-prototype.html`

## Key Design Decisions

1. **Keep terminal aesthetic** — All improvements respect Dracula theme, JetBrains Mono, inline styles
2. **Respect financial conventions** — Market grouping, star priority, type badges align with trader mental models
3. **Progressive disclosure** — Settings hidden by default; advanced features (shortcuts) optional
4. **Minimal API changes** — Leverage existing `/api/config` endpoint; no new backend work
5. **Power user friendly** — Keyboard shortcuts + batch operations for frequent managers

## Estimated User Impact

| Task | Before | After | Improvement |
|------|--------|-------|-------------|
| Find stock to edit | 30-60s (scroll searching) | 5s (Cmd+F + type) | 6-12x faster |
| Hide 5 old positions | 100s (5 × click + toggle) | 15s (select + batch hide) | 6x faster |
| Add new stock | 20s (scroll to bottom) | 5s (Cmd+K + type) | 4x faster |
| Promote watchlist stock | 15s | 8s (auto-filled cost/shares) | 1.8x faster |

**Overall time savings**: 30-40% reduction in manage page interactions.

---

## Next Steps

1. **Review & approve** improvements in priority order
2. **Implement Phase 1** (filter, collapse, sticky, grouping) — 5 hours
3. **A/B test** with 1-2 weeks of real portfolio management
4. **Iterate** on Phase 2 based on feedback
5. **Polish** Phase 3 enhancements

See `manage-page-review.md` for detailed component specs and code examples.
