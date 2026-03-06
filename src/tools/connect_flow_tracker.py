"""
connect_flow_tracker.py — 沪深港通资金流向追踪器

功能:
  - A 股: 读取北向资金个股每日增减（akshare stock_hsgt_individual_em）
  - 港股: 读取 CCASS 南向资金增减（A 前缀参与者日间变化）
  - 结果缓存到 SQLite connect_flow_cache 表
  - 对外暴露 get_consecutive_days(code) → int
      正数 = 连续净流入天数，负数 = 连续净流出天数，0 = 无数据/持平

评分集成:
  与 DailyIndicatorTracker.score() 的 capital_flow 维度对接，
  提供 ±3 的修正量 (连续≥3天 ±3, 2天 ±2, 1天 ±1)。

使用:
    tracker = ConnectFlowTracker()
    tracker.refresh_all(codes)   # 每日市场收盘后调用一次
    days = tracker.get_consecutive_days("HK03986")  # -3 到 +3
"""

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────
DB_PATH = "src/data/sim_trading.db"
LOOKBACK_DAYS = 10          # 最多回看交易日
CACHE_TTL_HOURS = 6         # 缓存有效期（小时），防止频繁请求
CCASS_REQUEST_DELAY = 1.5   # CCASS 请求间隔（秒）


class ConnectFlowTracker:
    """北向/南向资金逐日增减追踪，缓存到 SQLite。"""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._ensure_table()

    # ── DB 操作 ────────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS connect_flow_cache (
                    code        TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    change_shares   REAL,
                    change_amount   REAL,
                    market_type     TEXT,
                    fetched_at  TEXT,
                    PRIMARY KEY (code, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_connect_flow_code
                ON connect_flow_cache(code, date DESC)
            """)

    def _upsert(self, code: str, rows: list[dict], market_type: str):
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO connect_flow_cache
                    (code, date, change_shares, change_amount, market_type, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (code, r["date"], r.get("change_shares", 0),
                 r.get("change_amount", 0), market_type, now)
                for r in rows
            ])

    def _is_stale(self, code: str) -> bool:
        """若最新记录超过 CACHE_TTL_HOURS 则视为过期。"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT fetched_at FROM connect_flow_cache
                WHERE code = ? ORDER BY date DESC LIMIT 1
            """, (code,)).fetchone()
        if not row:
            return True
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            return (datetime.now() - fetched).total_seconds() > CACHE_TTL_HOURS * 3600
        except Exception:
            return True

    def _recent_rows(self, code: str, n: int = LOOKBACK_DAYS) -> list[dict]:
        """读取最近 n 条记录，按日期降序。"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT date, change_shares, change_amount
                FROM connect_flow_cache
                WHERE code = ?
                ORDER BY date DESC LIMIT ?
            """, (code, n)).fetchall()
        return [dict(r) for r in rows]

    # ── 数据获取 ──────────────────────────────────────────────────────────────

    def _fetch_a_northbound(self, code: str) -> list[dict]:
        """
        获取 A 股北向资金个股每日增减（akshare）。
        返回 [{date, change_shares, change_amount}, ...]，按日期降序。
        """
        try:
            import akshare as ak
            df = ak.stock_hsgt_individual_em(symbol=code)
            if df is None or df.empty:
                return []
            # 列: 持股日期 / 今日增持股数 / 今日增持资金
            df = df.rename(columns={
                "持股日期": "date",
                "今日增持股数": "change_shares",
                "今日增持资金": "change_amount",
            })
            df = df[["date", "change_shares", "change_amount"]].copy()
            df["date"] = df["date"].astype(str)
            df = df.sort_values("date", ascending=False).head(LOOKBACK_DAYS)
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"fetch_a_northbound({code}): {e}")
            return []

    def _fetch_hk_southbound(self, code: str) -> list[dict]:
        """
        获取港股南向资金每日变化（CCASS A 前缀参与者增减）。
        返回 [{date, change_shares, change_amount}, ...]，按日期降序。
        """
        try:
            from src.tools.ccass_scraper import fetch_ccass
            import requests

            raw_code = code.lstrip("HKhk").zfill(5)
            session = requests.Session()

            # 取最近 LOOKBACK_DAYS 个工作日
            days = self._trading_days_back(LOOKBACK_DAYS)
            history: dict[str, int] = {}

            for d in days:
                recs = fetch_ccass(raw_code, d, session)
                if recs:
                    # 只汇总 A-prefix（中国结算，南向持仓）
                    southbound = sum(
                        r["shareholding"] for r in recs
                        if r["participant_id"].startswith("A")
                    )
                    history[d.strftime("%Y-%m-%d")] = southbound
                time.sleep(CCASS_REQUEST_DELAY)

            # 计算日间差值
            result = []
            sorted_dates = sorted(history.keys(), reverse=True)
            for i, ds in enumerate(sorted_dates):
                if i + 1 < len(sorted_dates):
                    prev_ds = sorted_dates[i + 1]
                    delta = history[ds] - history[prev_ds]
                else:
                    delta = 0
                result.append({
                    "date": ds,
                    "change_shares": float(delta),
                    "change_amount": 0.0,   # CCASS 无金额字段
                })
            return result
        except Exception as e:
            logger.warning(f"fetch_hk_southbound({code}): {e}")
            return []

    @staticmethod
    def _trading_days_back(n: int) -> list[date]:
        days = []
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        return days

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def refresh(self, code: str, force: bool = False) -> bool:
        """
        刷新指定股票的资金流缓存。
        若缓存未过期且 force=False，则跳过。
        返回 True 表示实际发起了请求。
        """
        if not force and not self._is_stale(code):
            return False

        is_hk = code.upper().startswith("HK")
        if is_hk:
            rows = self._fetch_hk_southbound(code)
            market_type = "HK_southbound"
        else:
            rows = self._fetch_a_northbound(code)
            market_type = "A_northbound"

        if rows:
            self._upsert(code, rows, market_type)
            logger.info(f"connect_flow: refreshed {code} ({market_type}), {len(rows)} records")
        else:
            logger.debug(f"connect_flow: no data for {code}")

        return True

    def refresh_all(self, codes: list[str], force: bool = False):
        """批量刷新，A 股和港股分别处理。"""
        for code in codes:
            try:
                self.refresh(code, force=force)
            except Exception as e:
                logger.error(f"connect_flow refresh_all({code}): {e}")

    def get_consecutive_days(self, code: str) -> int:
        """
        返回连续净流入/流出天数。
          正数: 最近连续 N 天净流入 (change_shares > 0)
          负数: 最近连续 N 天净流出 (change_shares < 0)
          0:    无数据 / 混合 / 持平

        结果限制在 [-3, +3]，用于评分修正。
        """
        rows = self._recent_rows(code, n=LOOKBACK_DAYS)
        if not rows:
            return 0

        # 排除 change_shares 为 None 或 0 的行
        valid = [r for r in rows if r.get("change_shares") is not None
                 and r["change_shares"] != 0]
        if not valid:
            return 0

        # 计算最近连续同向天数
        first_dir = 1 if valid[0]["change_shares"] > 0 else -1
        count = 0
        for r in valid:
            cur_dir = 1 if r["change_shares"] > 0 else -1
            if cur_dir == first_dir:
                count += 1
            else:
                break

        result = first_dir * count
        return max(-3, min(3, result))

    def summary(self, code: str) -> dict:
        """返回最近 N 天的详细数据，用于日志或调试。"""
        rows = self._recent_rows(code, n=LOOKBACK_DAYS)
        consecutive = self.get_consecutive_days(code)
        return {
            "code": code,
            "consecutive_days": consecutive,
            "recent": rows[:5],
        }
