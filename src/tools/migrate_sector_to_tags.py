"""
Migrate sector_config.json indices into stock tags + tag_meta rows.

Reads the old sector_config.json structure and:
1. Inserts each index into tag_meta (tag=name, star, watch, baseline_value, created_at)
2. For each stock in an index: appends the tag name to the stock's tags in monitor_watchlist
3. Updates sector_daily and sector_alerts: changes index_id from old id to tag name
4. Exports updated JSON snapshot

Usage:
  poetry run python -m src.tools.migrate_sector_to_tags
"""
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data"
SECTOR_CONFIG = BASE / "sector_config.json"
DB_PATH = BASE / "sim_trading.db"


def migrate():
    if not SECTOR_CONFIG.exists():
        print(f"ERROR: {SECTOR_CONFIG} not found")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return 1

    with open(SECTOR_CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    indices = cfg.get("indices", {})
    if not indices:
        print("No indices found in sector_config.json — nothing to migrate.")
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # --- Step 1: Insert into tag_meta ---
    tag_meta_inserted = 0
    for idx_id, idx in indices.items():
        tag_name = idx["name"]
        star = 1 if idx.get("star") else 0
        watch = 1 if idx.get("watch", True) else 0
        baseline = idx.get("baseline_value", 100)
        created = idx.get("created_at", "")

        cur.execute(
            """INSERT OR REPLACE INTO tag_meta (tag, star, watch, baseline_value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (tag_name, star, watch, baseline, created),
        )
        tag_meta_inserted += 1
        print(f"  tag_meta: {idx_id} -> '{tag_name}' (star={star}, watch={watch})")

    # --- Step 2: Update stock tags in monitor_watchlist ---
    stocks_updated = 0
    stocks_missing = 0
    for idx_id, idx in indices.items():
        tag_name = idx["name"]
        for code in idx.get("stocks", []):
            cur.execute(
                "SELECT tags FROM monitor_watchlist WHERE symbol = ?", (code,)
            )
            row = cur.fetchone()
            if row is None:
                print(f"  WARN: stock {code} not found in monitor_watchlist (index: {idx_id})")
                stocks_missing += 1
                continue

            try:
                existing_tags = json.loads(row[0]) if row[0] else []
            except (json.JSONDecodeError, TypeError):
                existing_tags = []

            if tag_name not in existing_tags:
                existing_tags.append(tag_name)
                cur.execute(
                    "UPDATE monitor_watchlist SET tags = ? WHERE symbol = ?",
                    (json.dumps(existing_tags, ensure_ascii=False), code),
                )
                stocks_updated += 1
                print(f"  stock {code}: tags -> {existing_tags}")
            else:
                print(f"  stock {code}: already has tag '{tag_name}', skipped")

    # --- Step 3: Update sector_daily and sector_alerts index_id ---
    id_to_name = {idx_id: idx["name"] for idx_id, idx in indices.items()}

    daily_updated = 0
    alerts_updated = 0
    for old_id, new_name in id_to_name.items():
        cur.execute(
            "UPDATE sector_daily SET index_id = ? WHERE index_id = ?",
            (new_name, old_id),
        )
        daily_updated += cur.rowcount

        cur.execute(
            "UPDATE sector_alerts SET index_id = ? WHERE index_id = ?",
            (new_name, old_id),
        )
        alerts_updated += cur.rowcount

    conn.commit()
    conn.close()

    print(f"\n--- Migration Summary ---")
    print(f"  tag_meta rows inserted/updated: {tag_meta_inserted}")
    print(f"  stock tags updated: {stocks_updated}")
    print(f"  stocks not in watchlist (warnings): {stocks_missing}")
    print(f"  sector_daily rows updated: {daily_updated}")
    print(f"  sector_alerts rows updated: {alerts_updated}")

    # --- Step 4: Export JSON snapshot ---
    print("\nExporting monitor_config.json snapshot...")
    try:
        from src.tools.monitor_config_db_migrator import write_snapshot_from_db

        write_snapshot_from_db()
        print("  Snapshot exported successfully.")
    except Exception as e:
        print(f"  WARN: Could not export snapshot: {e}")
        print("  Run manually: poetry run python -m src.tools.monitor_config_db_migrator --action export")

    return 0


if __name__ == "__main__":
    sys.exit(migrate())
