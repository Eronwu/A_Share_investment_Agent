"""Database schema and connection for sim_trading.db (SQLite)."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sim_trading.db"
_db_path_override: str | None = None  # set to ":memory:" in tests

SCHEMA = """
-- 历史信号归档
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    strategy TEXT NOT NULL,
    code TEXT NOT NULL,
    direction TEXT,
    notify INTEGER DEFAULT 0,
    detail TEXT,
    display TEXT,
    price_at_signal REAL,
    daily_change_pct REAL,
    UNIQUE(ts, strategy, code)
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy);
CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code);

-- 价格快照 (30s 粒度)
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    price REAL,
    volume REAL,
    amount REAL,
    change_pct REAL,
    UNIQUE(ts, code)
);
CREATE INDEX IF NOT EXISTS idx_price_date_code ON price_snapshots(date, code);

-- 模拟交易
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    param_version TEXT NOT NULL,
    code TEXT NOT NULL,
    action TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    entry_time INTEGER,
    exit_time INTEGER,
    entry_date TEXT,
    exit_date TEXT,
    hold_days INTEGER,
    pnl REAL,
    pnl_pct REAL,
    commission REAL,
    total_cost REAL,
    confidence REAL,
    trigger_signals TEXT,
    exit_reason TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
CREATE INDEX IF NOT EXISTS idx_trades_version ON trades(param_version);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(entry_date);

-- 每日快照
CREATE TABLE IF NOT EXISTS daily_pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    param_version TEXT NOT NULL,
    total_equity REAL,
    cash REAL,
    invested REAL,
    daily_return REAL,
    cumulative_return REAL,
    drawdown_pct REAL,
    positions_json TEXT,
    UNIQUE(date, param_version)
);

-- 参数版本
CREATE TABLE IF NOT EXISTS param_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    trade_rules_json TEXT,
    optimization_score REAL,
    train_sharpe REAL,
    test_sharpe REAL,
    train_win_rate REAL,
    test_win_rate REAL,
    train_max_dd REAL,
    test_max_dd REAL,
    notes TEXT,
    is_active INTEGER DEFAULT 0
);

-- 实时持仓状态 (RT engine UPSERT, web API 只读)
CREATE TABLE IF NOT EXISTS live_state (
    code TEXT PRIMARY KEY,
    entry_price REAL,
    quantity INTEGER,
    current_price REAL,
    entry_time INTEGER,
    entry_date TEXT,
    stop_loss REAL,
    take_profit REAL,
    max_hold_days INTEGER,
    entry_strategy TEXT,
    confidence REAL,
    trigger_signals TEXT,
    unrealized_pnl REAL,
    pnl_pct REAL,
    daily_score INTEGER DEFAULT 0,
    buy_cost_per_share REAL DEFAULT 0,
    last_updated INTEGER
);

-- 优化运行记录
CREATE TABLE IF NOT EXISTS optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    n_trials INTEGER,
    best_score REAL,
    best_params TEXT,
    train_period TEXT,
    test_period TEXT,
    status TEXT
);

-- 告警事件 (从 alert_events.json 迁移到 SQLite)
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 2,
    message TEXT NOT NULL DEFAULT '',
    display TEXT NOT NULL DEFAULT '',
    change_pct REAL NOT NULL DEFAULT 0,
    UNIQUE(ts, symbol, message)
);
CREATE INDEX IF NOT EXISTS idx_alert_events_date ON alert_events(date);
CREATE INDEX IF NOT EXISTS idx_alert_events_date_ts ON alert_events(date, ts);

-- L2 session 上下文快照 (资金流、盘口状态，量化回测用)
CREATE TABLE IF NOT EXISTS session_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    code TEXT NOT NULL,
    session_json TEXT NOT NULL,
    UNIQUE(ts, code)
);
CREATE INDEX IF NOT EXISTS idx_session_snap_date_code ON session_snapshots(date, code);

-- 板块轮动排名（东方财富行业/概念板块每日排名）
CREATE TABLE IF NOT EXISTS sector_rotation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    board_name TEXT NOT NULL,
    change_pct REAL,
    rank INTEGER,
    UNIQUE(date, category, board_name)
);
CREATE INDEX IF NOT EXISTS idx_sector_rot_date ON sector_rotation(date);
CREATE INDEX IF NOT EXISTS idx_sector_rot_cat_date ON sector_rotation(category, date);

-- 自定义指数每日值
CREATE TABLE IF NOT EXISTS sector_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    avg_change_pct REAL,
    index_value REAL,
    up_count INTEGER,
    down_count INTEGER,
    components_json TEXT,
    UNIQUE(date, index_id)
);
CREATE INDEX IF NOT EXISTS idx_sector_daily_idx ON sector_daily(index_id);

-- 主线告警
CREATE TABLE IF NOT EXISTS sector_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    index_id TEXT NOT NULL,
    index_name TEXT,
    alert_type TEXT,
    cumulative_pct REAL,
    slope REAL,
    r_squared REAL,
    message TEXT,
    display TEXT,
    UNIQUE(date, index_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_sector_alerts_date ON sector_alerts(date);

-- 日线 L2 微观结构聚合 (收盘后从 session_snapshots + signals 计算)
CREATE TABLE IF NOT EXISTS daily_l2_digest (
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    lo_buy_count INTEGER DEFAULT 0,
    lo_sell_count INTEGER DEFAULT 0,
    lo_net_amount REAL DEFAULT 0,
    lo_net_ratio REAL DEFAULT 0,
    tick_imbalance REAL DEFAULT 0,
    tick_buy_vol INTEGER DEFAULT 0,
    tick_sell_vol INTEGER DEFAULT 0,
    cf_net_inflow REAL DEFAULT 0,
    cf_net_inflow_pct REAL DEFAULT 0,
    vpd_count INTEGER DEFAULT 0,
    lor_count INTEGER DEFAULT 0,
    lor_direction TEXT,
    direction_score INTEGER DEFAULT 0,
    direction TEXT DEFAULT 'neutral',
    session_json TEXT,
    UNIQUE(date, code)
);
CREATE INDEX IF NOT EXISTS idx_l2_digest_date ON daily_l2_digest(date);

-- 交易计划事件 (条件触发记录)
CREATE TABLE IF NOT EXISTS trade_plan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    condition_id TEXT,
    label TEXT,
    price REAL,
    shares INTEGER,
    sim_executed INTEGER DEFAULT 0,
    message TEXT,
    UNIQUE(ts, plan_id, condition_id)
);
CREATE INDEX IF NOT EXISTS idx_tpe_date ON trade_plan_events(date);
CREATE INDEX IF NOT EXISTS idx_tpe_plan ON trade_plan_events(plan_id);

-- Monitor 配置（DB 为主，JSON 为快照）
CREATE TABLE IF NOT EXISTS monitor_watchlist (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    list_type TEXT NOT NULL CHECK (list_type IN ('holding', 'watching')),
    cost REAL,
    shares INTEGER,
    lot INTEGER,
    hidden INTEGER NOT NULL DEFAULT 0,
    star INTEGER NOT NULL DEFAULT 0,
    dip_buy INTEGER NOT NULL DEFAULT 0,
    tags TEXT DEFAULT '[]',
    watch_price REAL,
    watch_price_date TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_type ON monitor_watchlist(list_type);
CREATE INDEX IF NOT EXISTS idx_monitor_watchlist_updated ON monitor_watchlist(updated_at);

CREATE TABLE IF NOT EXISTS monitor_settings (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_meta (
    tag TEXT PRIMARY KEY,
    star INTEGER DEFAULT 0,
    watch INTEGER DEFAULT 1,
    baseline_value REAL DEFAULT 100,
    parent TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Futu 模拟盘订单审计追踪
CREATE TABLE IF NOT EXISTS futu_orders (
    order_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL,
    quantity INTEGER,
    filled_qty INTEGER DEFAULT 0,
    avg_fill_price REAL,
    status TEXT DEFAULT 'PENDING',
    futu_acc_id INTEGER,
    exit_reason TEXT,
    created_at INTEGER,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_futu_orders_code ON futu_orders(code);
CREATE INDEX IF NOT EXISTS idx_futu_orders_status ON futu_orders(status);
"""


def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode for concurrent read/write."""
    path = _db_path_override if _db_path_override is not None else str(DB_PATH)
    use_uri = path.startswith("file:")
    conn = sqlite3.connect(path, timeout=10, uri=use_uri)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    # Migration: add daily_score column if missing (existing DBs)
    try:
        conn.execute("SELECT daily_score FROM live_state LIMIT 1")
    except sqlite3.OperationalError:
        try:
            conn.execute("ALTER TABLE live_state ADD COLUMN daily_score INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Migration: add tags, watch_price, watch_price_date columns to monitor_watchlist
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(monitor_watchlist)").fetchall()
    }
    if "tags" not in existing_cols:
        try:
            conn.execute("ALTER TABLE monitor_watchlist ADD COLUMN tags TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
    if "watch_price" not in existing_cols:
        try:
            conn.execute("ALTER TABLE monitor_watchlist ADD COLUMN watch_price REAL")
        except sqlite3.OperationalError:
            pass
    if "watch_price_date" not in existing_cols:
        try:
            conn.execute("ALTER TABLE monitor_watchlist ADD COLUMN watch_price_date TEXT")
        except sqlite3.OperationalError:
            pass
    # Migration: add parent column to tag_meta if missing
    tag_meta_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(tag_meta)").fetchall()
    }
    if "parent" not in tag_meta_cols:
        try:
            conn.execute("ALTER TABLE tag_meta ADD COLUMN parent TEXT")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
        print(f"  {t['name']}: {count} rows")
    conn.close()
