# Monitor Config DB Migration Design

Date: 2026-03-01  
Status: Approved (brainstorming outcome)  
Scope: Move `monitor_config.json` to DB-first architecture, keep JSON as exported snapshot/log.

## 1. Goal

Migrate watchlist/settings persistence from file-based storage to database-based storage while preserving:

- Existing API contract for frontend (`/api/config`, `/api/metrics`)
- Existing operational flow for poller/notifier during transition
- Human-readable and git-diff-friendly snapshot (`monitor_config.json`)

Final direction:

- **Source of truth**: `src/data/sim_trading.db`
- **Snapshot/log artifact**: `src/data/monitor_config.json` (auto-exported from DB, not primary write target)

## 2. Decision Summary

- Reuse existing `sim_trading.db` (do not create a new DB).
- Use DB single-write path (no DB+JSON dual-write as primary behavior).
- Keep JSON export after successful DB transaction for compatibility and auditability.
- Roll out in phases with rollback switch.

## 3. Architecture

### 3.1 Write path

`web/app/api/config/route.ts`

1. Validate request body.
2. Open DB transaction.
3. Apply config mutation to DB tables.
4. Commit DB transaction.
5. Export JSON snapshot via atomic write (`tmp -> rename`).
6. Return success (or success with warning if export fails).

### 3.2 Read path

Transition mode:

- `/api/config`: read DB first, fallback to JSON when DB unavailable or empty.
- `/api/metrics`: read DB for watchlist/settings merge logic; alert rules remain file-based in phase 1.

### 3.3 Python processes

Phase 1 keeps poller/notifier unchanged (continue reading JSON snapshot).  
After stabilization, optionally switch Python readers to DB directly.

## 4. Data Model

## 4.1 Tables

### `monitor_watchlist`

- `symbol TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `list_type TEXT NOT NULL` (`holding` | `watching`)
- `cost REAL NULL`
- `shares INTEGER NULL`
- `lot INTEGER NULL`
- `hidden INTEGER NOT NULL DEFAULT 0`
- `star INTEGER NOT NULL DEFAULT 0`
- `created_at INTEGER NOT NULL`
- `updated_at INTEGER NOT NULL`

Indexes:

- `idx_monitor_watchlist_type` on `(list_type)`
- `idx_monitor_watchlist_updated` on `(updated_at)`

### `monitor_settings`

- `key TEXT PRIMARY KEY`
- `value REAL NOT NULL`
- `updated_at INTEGER NOT NULL`

This key-value structure allows non-breaking setting extension.

## 4.2 Serialization contract

Exported `monitor_config.json` keeps current shape:

```json
{
  "watchlist": {
    "HK09988": {
      "name": "阿里巴巴－Ｗ",
      "type": "holding",
      "cost": 156.016,
      "shares": 600,
      "lot": 100,
      "hidden": false,
      "star": false
    }
  },
  "settings": {
    "poll_interval": 30
  }
}
```

Mapping:

- `list_type -> type`
- `hidden/star` stored as integer in DB, serialized as boolean in JSON.

## 5. Migration Plan

## Phase 0: Schema + Backfill (No traffic cutover)

1. Add `monitor_watchlist`, `monitor_settings`.
2. Run one-time importer from `monitor_config.json` into DB.
3. Add a validation script: compare DB-derived JSON vs original file JSON.

Exit criteria:

- DB row counts match expected watchlist/settings counts.
- Field-level parity verified for all symbols.

## Phase 1: Write Cutover (DB becomes write source)

1. Update `/api/config` POST actions (`add/update/remove/settings`) to write DB only.
2. After commit, run snapshot exporter.
3. If export fails, keep DB commit and return warning message.

Exit criteria:

- Manage page CRUD works end-to-end.
- Exported `monitor_config.json` reflects every API write.

## Phase 2: Read Cutover

1. `/api/config` GET reads DB first, JSON fallback.
2. `/api/metrics` reads DB first for watchlist/settings.
3. Keep `alert_config.json` unchanged in this phase.

Exit criteria:

- Dashboard/manage data consistent across refreshes.
- No regression in holdings/watching separation, hidden/star behavior.

## Phase 3: Stabilization and Optional Simplification

Options:

- Keep Python reading JSON snapshots (simple and low risk), or
- Switch Python to DB reads directly and keep JSON as passive export artifact.

Recommended timing: switch only after multiple trading-day stability.

## 6. Rollback Strategy

Add runtime switch:

- `CONFIG_SOURCE=db|json` (default `db` after phase 2).

Rollback flow:

1. Set `CONFIG_SOURCE=json`.
2. Restart web service.
3. System reads JSON immediately with no schema rollback needed.

Precondition:

- Export snapshot must remain healthy so rollback data stays fresh.

## 7. Error Handling

- DB transaction failure: return error, no partial writes.
- Snapshot export failure: return success with warning (DB remains authoritative).
- DB read failure in GET routes: fallback to JSON and include warning log.
- Input validation continues to enforce allowed setting keys/ranges.

## 8. Security and Integrity Notes

- Keep static file paths only (no user-derived file paths).
- Keep strict action validation and schema checks in API handlers.
- Use parameterized SQL for all writes/reads.
- Preserve atomic file write for snapshot export.

## 9. Testing Plan

## 9.1 API verification

- `POST /api/config` for `add/update/remove/settings` then `GET /api/config`.
- Verify DB rows and exported JSON fields are identical for changed symbols.
- Simulate export write failure and verify warning path behavior.

## 9.2 E2E verification (Playwright)

Extend or rerun:

- `web/screenshots/test_manage_stocks_e2e.mjs`
- `web/screenshots/test_full_checkup.mjs`

Mandatory checks:

- Add stock, promote/demote, edit cost/shares.
- Toggle hide/star.
- Update above/below thresholds (still via `alert_config.json` in phase 1).
- Refresh consistency after each mutation.

## 9.3 Concurrency checks

- Repeated updates to same symbol in short intervals.
- Ensure final state correctness in DB + snapshot parity.

## 10. Implementation Tasks

1. Add schema initialization for `monitor_*` tables in DB bootstrap.
2. Add `monitor_config` repository module (read/write/export utilities).
3. Refactor `web/app/api/config/route.ts` to DB-first.
4. Refactor `web/app/api/metrics/route.ts` to DB-first watchlist/settings read.
5. Add migration script JSON -> DB and parity checker.
6. Add tests and update existing E2E scripts if needed.
7. Add `CONFIG_SOURCE` feature flag and fallback behavior.

## 11. Non-Goals (Current Iteration)

- No immediate migration of `alert_config.json` to DB.
- No immediate direct DB read migration for poller/notifier.
- No schema redesign for trade plans in this change.

## 12. Acceptance Criteria

- DB is the only write source for monitor config.
- JSON snapshot is always exportable and remains human-readable.
- Existing frontend behavior unchanged.
- Existing monitor/notification runtime remains stable.
- Rollback to JSON mode available and tested.
