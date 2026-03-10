#!/usr/bin/env python3
"""
Market data poller — 轻量守护脚本

定时从东方财富 API 抓取实时行情，写入 src/data/market_data.json，
供 Next.js 前端直接读取展示。

用法:
    poetry run python src/tools/market_data_poller.py
"""

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 东方财富 push API 公开 token（所有 quant 库共用）
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"

from src.tools.futu_enricher import FutuL2Enricher
from src.tools.stock_monitor import (
    fetch_realtime_eastmoney, fetch_realtime_sina,
    fetch_realtime_yahoo, is_kr_symbol,
)
from src.utils.logging_config import setup_logger

logger = setup_logger("market_data_poller")

# Futu L2 增强器 — 全局单例，懒连接，失败不影响主流程
_futu_enricher = FutuL2Enricher()

CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
OUTPUT_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"
DB_PATH = PROJECT_ROOT / "src" / "data" / "sim_trading.db"


def load_watchlist_from_db() -> tuple[dict, dict]:
    """从 DB 读取 watchlist 和 settings，单一数据源。

    Returns:
        (watchlist, settings) — watchlist: {symbol: {name, type, ...}}
    """
    watchlist: dict = {}
    settings: dict = {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # watchlist
        rows = conn.execute(
            "SELECT symbol, name, list_type, cost, shares, lot, "
            "hidden, star, tags, watch_price FROM monitor_watchlist"
        ).fetchall()
        for row in rows:
            entry: dict = {
                "name": row["name"] or "",
                "type": row["list_type"] or "watching",
            }
            if row["cost"]:
                entry["cost"] = row["cost"]
            if row["shares"]:
                entry["shares"] = row["shares"]
            if row["lot"]:
                entry["lot"] = row["lot"]
            if row["hidden"]:
                entry["hidden"] = bool(row["hidden"])
            if row["star"]:
                entry["star"] = bool(row["star"])
            if row["tags"]:
                entry["tags"] = row["tags"]
            if row["watch_price"]:
                entry["watch_price"] = row["watch_price"]
            watchlist[row["symbol"]] = entry
        # settings
        setting_rows = conn.execute(
            "SELECT key, value FROM monitor_settings"
        ).fetchall()
        for row in setting_rows:
            try:
                settings[row["key"]] = int(row["value"])
            except (TypeError, ValueError):
                try:
                    settings[row["key"]] = float(row["value"])
                except (TypeError, ValueError):
                    settings[row["key"]] = row["value"]
        conn.close()
    except Exception as e:
        logger.error(f"load_watchlist_from_db 失败，回退 JSON: {e}")
        # 降级到 JSON（应急 fallback，避免 poller 停摆）
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("watchlist", {}), cfg.get("settings", {})
        except Exception:
            return {}, {}
    return watchlist, settings


def _backfill_missing_names(stocks: list[dict], watchlist: dict) -> bool:
    """用行情抓取结果填充 watchlist 中名称缺失的股票。

    只写 DB（单一数据源）。返回 True 表示有更新。
    """
    updates: dict[str, str] = {}

    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", "").strip()
        if not code or not name:
            continue
        existing_name = (watchlist.get(code, {}).get("name") or "").strip()
        if not existing_name or existing_name == code:
            updates[code] = name

    if not updates:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()
        for code, name in updates.items():
            cur.execute(
                "UPDATE monitor_watchlist SET name = ?, updated_at = ? "
                "WHERE symbol = ? "
                "AND (name IS NULL OR name = '' OR name = symbol)",
                (name, now, code),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"backfill names DB write failed: {e}")

    logger.info(f"自动填充股票名称: {updates}")
    return True


def fetch_realtime_with_fallback(symbols: list[str]) -> tuple[list[dict], bool]:
    """优先东方财富，失败回退新浪（价格能刷新，但无量比/换手率）

    Returns:
        (stocks, is_sina_fallback) — is_sina_fallback=True 时 turnover/vol_ratio 为 0
    """
    stocks = fetch_realtime_eastmoney(symbols)
    if stocks:
        return stocks, False

    logger.warning("东方财富不可达，回退新浪行情")
    sina_quotes = fetch_realtime_sina(symbols)
    if not sina_quotes:
        return [], True

    # 转换新浪格式 → 东方财富格式
    results = []
    for sym in symbols:
        q = sina_quotes.get(sym)
        if not q:
            continue
        prev = q.get("prev_close", 0)
        price = q.get("price", 0)
        if price <= 0 and prev > 0:
            price = prev  # 停牌：用昨收价填充，涨跌=0
        elif price <= 0:
            continue  # 无任何价格数据，跳过
        pct = q.get("change_pct", 0)
        # 港股盘前: price==prevClose 且 pct==0，跳过以保留昨日收盘涨跌
        # 仅限港股；A 股 price==prevClose 可能是停牌，应保留
        if pct == 0 and prev > 0 and abs(price - prev) < 0.001 and sym.startswith("HK"):
            continue
        chg = price - prev if prev > 0 and price > 0 else 0
        results.append({
            "code": sym,
            "name": q.get("name", ""),
            "price": price,
            "pct": pct,
            "change": round(chg, 3),
            "volume": q.get("volume", 0),
            "amount": q.get("amount", 0),
            "amplitude": round((q.get("high", 0) - q.get("low", 0)) / prev * 100, 2) if prev > 0 else 0,
            "turnover": 0,     # 新浪无换手率，poll_once 会从旧数据继承
            "vol_ratio": 0,    # 新浪无量比，poll_once 会从旧数据继承
            "high": q.get("high", 0),
            "low": q.get("low", 0),
            "open": q.get("open", 0),
            "prev_close": prev,
        })
    return results, True


def build_services(stocks: list[dict], watchlist: dict) -> list[dict]:
    """将东方财富原始数据组装为前端 Service 格式

    只写行情数据，不写 config 字段（type/cost/shares/hidden/above/below/pnl）。
    config 字段由 web /api/metrics 实时从 monitor_config.json 合并，
    确保 UI 端操作（隐藏、改持仓等）立即生效，不依赖 poller 周期。
    """
    services = []
    for s in stocks:
        code = s.get("code", "")
        price = s.get("price", 0)
        prev = s.get("prev_close", 0)
        # 停牌股: price=0 但有昨收，用昨收价填充，涨跌=0
        if (not price or price <= 0) and prev > 0:
            price = prev
        services.append({
            "id": code,
            "name": s.get("name", ""),
            "price": price,
            "change": s.get("pct", 0) if price != prev else 0,
            "chgAmt": s.get("change", 0) if price != prev else 0,
            "vol": s.get("volume", 0),
            "amount": s.get("amount", 0),
            "amp": s.get("amplitude", 0),
            "turnover": s.get("turnover", 0),
            "volRatio": s.get("vol_ratio", 0),
            "high": s.get("high", 0),
            "low": s.get("low", 0),
            "open": s.get("open", 0),
            "prevClose": s.get("prev_close", 0),
        })
    return services


EM_FX_API = "https://push2.eastmoney.com/api/qt/ulist.np/get"


def fetch_hkd_cny_rate() -> float | None:
    """从东方财富获取 HKD/CNY 实时汇率"""
    try:
        resp = requests.get(
            EM_FX_API,
            params={
                "fltt": "2",
                "secids": "119.HKDCNY",
                "fields": "f2",
                "ut": EM_UT,
            },
            timeout=5,
        )
        data = resp.json()
        diff = (data.get("data") or {}).get("diff") or []
        if diff and isinstance(diff[0], dict):
            rate = diff[0].get("f2")
            if isinstance(rate, (int, float)) and rate > 0:
                return round(float(rate), 4)
    except Exception as e:
        logger.warning(f"获取 HKD/CNY 汇率失败: {e}")
    return None


SINA_INDEX_URL = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001"
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def fetch_market_turnover() -> dict | None:
    """从新浪获取沪深两市实时成交额（单次请求，<50ms）

    Returns:
        {sh, sz, total (亿), shPct, szPct, verdict}
    """
    import re

    try:
        resp = requests.get(SINA_INDEX_URL, headers=SINA_HEADERS, timeout=5)
        resp.encoding = "gbk"
    except Exception as e:
        logger.warning(f"获取两市成交额失败: {e}")
        return None

    result = {}
    for line in resp.text.strip().split("\n"):
        m = re.match(r'var hq_str_s_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        code = m.group(1)
        fields = m.group(2).split(",")
        if len(fields) < 6:
            continue
        # 简化格式: 名称,点位,涨跌点,涨跌幅%,成交量(万手),成交额(万元)
        key = "sh" if "sh" in code else "sz"
        result[key] = {
            "name": fields[0],
            "price": float(fields[1]),
            "pct": float(fields[3]),
            "amount": float(fields[5]) / 10000,  # 万元 → 亿元
        }

    if "sh" not in result or "sz" not in result:
        return None

    sh_yi = result["sh"]["amount"]
    sz_yi = result["sz"]["amount"]
    total = sh_yi + sz_yi

    # 缩放量判断
    if total >= 20000:
        verdict = "extreme_high"
    elif total >= 15000:
        verdict = "high"
    elif total >= 12000:
        verdict = "above_avg"
    elif total >= 10000:
        verdict = "normal"
    elif total >= 8000:
        verdict = "below_avg"
    elif total >= 6000:
        verdict = "low"
    else:
        verdict = "extreme_low"

    return {
        "sh": round(sh_yi),
        "sz": round(sz_yi),
        "total": round(total),
        "shIndex": result["sh"]["price"],
        "szIndex": result["sz"]["price"],
        "shPct": result["sh"]["pct"],
        "szPct": result["sz"]["pct"],
        "verdict": verdict,
    }


def poll_once() -> bool:
    """执行一次抓取+写入，返回是否成功

    即使个股行情抓取失败，也尝试写入大盘数据（成交额/汇率），
    确保 dashboard 至少能看到市场概览。
    """
    watchlist, settings = load_watchlist_from_db()
    symbols = list(watchlist.keys())

    if not symbols:
        logger.warning("watchlist 为空，跳过本轮")
        return False

    # 分离 KR 股票（Yahoo Finance），其余走东方财富/新浪
    kr_symbols = [s for s in symbols if is_kr_symbol(s)]
    em_symbols = [s for s in symbols if not is_kr_symbol(s)]

    stocks, is_sina_fallback = fetch_realtime_with_fallback(em_symbols)

    # 追加 Yahoo Finance 数据（KR 股票）
    if kr_symbols:
        kr_stocks = fetch_realtime_yahoo(kr_symbols)
        stocks = stocks + kr_stocks

    # 两市成交额（新浪源，独立于东方财富，不受其故障影响）
    turnover = fetch_market_turnover()

    if not stocks:
        # 个股数据失败，但尝试 partial update（保留旧 services，更新成交额）
        logger.warning("个股行情获取失败（东方财富不可达），尝试更新大盘数据")
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {"services": []}
        existing["ts"] = int(time.time() * 1000)
        existing["settings"] = settings
        existing["_source"] = {"primary": "unavailable", "is_fallback": True, "futu_connected": False}
        if turnover:
            existing["marketTurnover"] = turnover
        tmp = OUTPUT_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        tmp.replace(OUTPUT_PATH)
        if turnover:
            logger.info(f"大盘数据已更新: 两市 {turnover['total']:,}亿 ({turnover['verdict']})")
        return False

    # 自动填充缺失的股票名称（只在首次抓到名称时写入，后续 no-op）
    _backfill_missing_names(stocks, watchlist)

    services = build_services(stocks, watchlist)

    # 新浪降级时继承旧数据中的量比/换手率（新浪不提供这两个字段）
    if is_sina_fallback:
        try:
            old_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            old_map = {s["id"]: s for s in old_data.get("services", []) if s.get("id")}
            carried = 0
            for svc in services:
                old = old_map.get(svc["id"])
                if old:
                    if old.get("turnover"):
                        svc["turnover"] = old["turnover"]
                    if old.get("volRatio"):
                        svc["volRatio"] = old["volRatio"]
                    carried += 1
            if carried:
                logger.info(f"新浪降级: 从旧数据继承量比/换手率 ({carried} 只)")
        except Exception:
            pass

    # Futu L2 增强（可选，失败时 l2_data = {}，不影响后续）
    l2_data = _futu_enricher.enrich(services)
    if l2_data:
        for svc in services:
            extra = l2_data.get(svc["id"])
            if extra:
                svc.update(extra)
        logger.info(f"L2 增强: {len(l2_data)}/{len(services)} 只")

    # 有港股持仓时获取汇率（失败时从旧数据继承）
    has_hk = any(s.startswith("HK") for s in symbols)
    hkd_cny_rate = fetch_hkd_cny_rate() if has_hk else None
    if has_hk and hkd_cny_rate is None:
        try:
            old_rate = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("hkdCnyRate")
            if old_rate:
                hkd_cny_rate = old_rate
                logger.info(f"汇率获取失败，继承上次值: {hkd_cny_rate}")
        except Exception:
            pass

    # 合并旧数据中缺失的 service（盘前 price=0 被跳过的股票保留昨日收盘价）
    new_ids = {s["id"] for s in services}
    try:
        old_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        for old_svc in old_data.get("services", []):
            if old_svc.get("id") and old_svc["id"] not in new_ids:
                services.append(old_svc)
    except Exception:
        pass

    payload = {
        "services": services,
        "ts": int(time.time() * 1000),
        "settings": settings,
        "hkdCnyRate": hkd_cny_rate,
        "marketTurnover": turnover,
        # 数据源元数据，供 Data Freshness Watchdog 检测降级
        "_source": {
            "primary": "sina" if is_sina_fallback else "eastmoney",
            "is_fallback": is_sina_fallback,
            "futu_connected": bool(l2_data),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(OUTPUT_PATH)

    now = datetime.now().strftime("%H:%M:%S")
    logger.info(f"[{now}] 已更新 {len(services)} 只标的 -> {OUTPUT_PATH.name}")
    return True


def main():
    watchlist, settings = load_watchlist_from_db()
    interval = settings.get("poll_interval", 30)

    print("Market Data Poller 启动")
    print(f"  标的数: {len(watchlist)}")
    print(f"  轮询间隔: {interval}s")
    print(f"  输出文件: {OUTPUT_PATH}")
    print("  按 Ctrl+C 退出\n")

    # 启动时立即执行一次
    poll_once()

    try:
        while True:
            time.sleep(interval)
            # 每轮重新读取 DB，这样 watchlist 变化能自动生效
            _, settings = load_watchlist_from_db()
            interval = settings.get("poll_interval", 30)
            poll_once()
    except KeyboardInterrupt:
        print("\nPoller 已停止")


if __name__ == "__main__":
    main()
