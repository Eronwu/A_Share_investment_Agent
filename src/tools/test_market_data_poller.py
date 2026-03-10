"""
Tests for market_data_poller: load_watchlist_from_db, _backfill_missing_names.

Coverage:
  1. load_watchlist_from_db: reads watchlist + settings from DB
  2. load_watchlist_from_db: fallback to JSON when DB unavailable
  3. load_watchlist_from_db: JSON out of sync does NOT affect DB reads
  4. _backfill_missing_names: only writes to DB, not JSON
  5. _backfill_missing_names: skips entries where name already filled
  6. poll_once: uses DB watchlist (regression for the JSON-drift bug)
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Temp DB with monitor_watchlist + monitor_settings tables."""
    db = tmp_path / "sim_trading.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE monitor_watchlist (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            list_type TEXT DEFAULT 'watching',
            cost REAL,
            shares INTEGER,
            lot INTEGER,
            hidden INTEGER DEFAULT 0,
            star INTEGER DEFAULT 0,
            tags TEXT,
            watch_price REAL,
            updated_at TEXT
        );
        CREATE TABLE monitor_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares)
            VALUES ('002080', '中材科技', 'holding', 50.764, 1000);
        INSERT INTO monitor_watchlist (symbol, name, list_type)
            VALUES ('HK09988', '阿里巴巴', 'holding');
        INSERT INTO monitor_watchlist (symbol, name, list_type)
            VALUES ('000001', '平安银行', 'watching');
        INSERT INTO monitor_settings (key, value)
            VALUES ('poll_interval', '30');
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def stale_json(tmp_path):
    """monitor_config.json with only 1 entry — simulates the drift bug."""
    cfg = tmp_path / "monitor_config.json"
    cfg.write_text(json.dumps({
        "watchlist": {"KR000660": {"name": "SK하이닉스"}},
        "settings": {"poll_interval": 30},
    }), encoding="utf-8")
    return cfg


# ─── 1. load_watchlist_from_db ───────────────────────────────────────────────

def test_load_watchlist_reads_from_db(tmp_db, stale_json):
    """DB has 3 entries; stale JSON has 1. Must return DB data."""
    import src.tools.market_data_poller as poller

    with patch.object(poller, "DB_PATH", tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, settings = poller.load_watchlist_from_db()

    assert len(watchlist) == 3, \
        "Should read all 3 entries from DB, not 1 from stale JSON"
    assert "002080" in watchlist
    assert "HK09988" in watchlist
    assert "000001" in watchlist
    assert "KR000660" not in watchlist, \
        "KR000660 is only in stale JSON, must not appear"


def test_load_watchlist_fields(tmp_db, stale_json):
    """Verify field mapping from DB columns to watchlist dict."""
    import src.tools.market_data_poller as poller

    with patch.object(poller, "DB_PATH", tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, _ = poller.load_watchlist_from_db()

    entry = watchlist["002080"]
    assert entry["name"] == "中材科技"
    assert entry["type"] == "holding"
    assert entry["cost"] == 50.764
    assert entry["shares"] == 1000


def test_load_settings_from_db(tmp_db, stale_json):
    """Settings should be read from monitor_settings table."""
    import src.tools.market_data_poller as poller

    with patch.object(poller, "DB_PATH", tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        _, settings = poller.load_watchlist_from_db()

    assert settings.get("poll_interval") == 30


def test_load_watchlist_fallback_to_json_when_db_missing(stale_json, tmp_path):
    """When DB doesn't exist, must fall back to JSON (not crash)."""
    import src.tools.market_data_poller as poller

    missing_db = tmp_path / "nonexistent.db"
    with patch.object(poller, "DB_PATH", missing_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, settings = poller.load_watchlist_from_db()

    assert "KR000660" in watchlist
    assert settings.get("poll_interval") == 30


# ─── 2. _backfill_missing_names ──────────────────────────────────────────────

def test_backfill_writes_to_db_not_json(tmp_db, stale_json):
    """backfill must update DB; JSON must remain unchanged."""
    import src.tools.market_data_poller as poller

    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "UPDATE monitor_watchlist SET name = '' WHERE symbol = '000001'"
    )
    conn.commit()
    conn.close()

    watchlist = {"000001": {"name": "", "type": "watching"}}
    stocks = [{"code": "000001", "name": "平安银行", "price": 12.5}]
    json_before = stale_json.read_text()

    with patch.object(poller, "DB_PATH", tmp_db):
        updated = poller._backfill_missing_names(stocks, watchlist)

    assert updated is True

    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT name FROM monitor_watchlist WHERE symbol = '000001'"
    ).fetchone()
    conn.close()
    assert row[0] == "平安银行", "DB should be updated"
    assert stale_json.read_text() == json_before, \
        "JSON must not be modified by backfill"


def test_backfill_skips_already_named(tmp_db):
    """Entries with existing names should not be updated."""
    import src.tools.market_data_poller as poller

    watchlist = {"002080": {"name": "中材科技", "type": "holding"}}
    stocks = [{"code": "002080", "name": "中材科技NEW", "price": 46.0}]

    with patch.object(poller, "DB_PATH", tmp_db):
        updated = poller._backfill_missing_names(stocks, watchlist)

    assert updated is False

    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT name FROM monitor_watchlist WHERE symbol = '002080'"
    ).fetchone()
    conn.close()
    assert row[0] == "中材科技", "Existing name should not be overwritten"


def test_backfill_returns_false_when_no_updates_needed(tmp_db):
    """All names filled → returns False without touching DB."""
    import src.tools.market_data_poller as poller

    watchlist = {
        "002080": {"name": "中材科技"},
        "HK09988": {"name": "阿里巴巴"},
    }
    stocks = [
        {"code": "002080", "name": "中材科技", "price": 46.0},
        {"code": "HK09988", "name": "阿里巴巴", "price": 80.0},
    ]

    with patch.object(poller, "DB_PATH", tmp_db):
        result = poller._backfill_missing_names(stocks, watchlist)

    assert result is False


# ─── 3. Regression: poll_once reads DB, not JSON ─────────────────────────────

def test_poll_once_uses_db_watchlist(tmp_db, stale_json, tmp_path):
    """
    Regression test for the JSON-drift bug:
    DB has 3 stocks, stale JSON has 1.
    poll_once should fetch all 3 DB stocks from EM.
    """
    import src.tools.market_data_poller as poller

    output = tmp_path / "market_data.json"

    def fake_em_fetch(symbols):
        return [
            {
                "code": s, "name": f"Name_{s}", "price": 10.0,
                "pct": 1.0, "change": 0.1, "volume": 1000,
                "amount": 10000.0, "amplitude": 1.0,
                "turnover": 1.0, "vol_ratio": 1.0,
                "high": 10.5, "low": 9.5, "open": 9.8,
                "prev_close": 9.9,
            }
            for s in symbols
        ]

    with patch.object(poller, "DB_PATH", tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json), \
         patch.object(poller, "OUTPUT_PATH", output), \
         patch(
             "src.tools.market_data_poller.fetch_realtime_eastmoney",
             side_effect=fake_em_fetch,
         ), \
         patch(
             "src.tools.market_data_poller.fetch_realtime_yahoo",
             return_value=[],
         ), \
         patch(
             "src.tools.market_data_poller.fetch_market_turnover",
             return_value=None,
         ), \
         patch(
             "src.tools.market_data_poller.fetch_hkd_cny_rate",
             return_value=0.92,
         ), \
         patch.object(poller._futu_enricher, "enrich", return_value={}):
        result = poller.poll_once()

    assert result is True
    data = json.loads(output.read_text())
    ids = {s["id"] for s in data["services"]}

    assert "002080" in ids, \
        "002080 must be fetched (was absent from stale JSON)"
    assert "HK09988" in ids
    assert "000001" in ids
    assert "KR000660" not in ids, \
        "KR000660 only in stale JSON, must not appear"
