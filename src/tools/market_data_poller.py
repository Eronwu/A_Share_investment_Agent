#!/usr/bin/env python3
"""
Market data poller — 轻量守护脚本

定时从东方财富 API 抓取实时行情，写入 src/data/market_data.json，
供 Next.js 前端直接读取展示。

用法:
    poetry run python src/tools/market_data_poller.py
"""

import json
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
from src.tools.stock_monitor import fetch_realtime_eastmoney, fetch_realtime_sina, load_config
from src.utils.logging_config import setup_logger

logger = setup_logger("market_data_poller")

# Futu L2 增强器 — 全局单例，懒连接，失败不影响主流程
_futu_enricher = FutuL2Enricher()

CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
OUTPUT_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"


def fetch_realtime_with_fallback(symbols: list[str]) -> list[dict]:
    """优先东方财富，失败回退新浪（价格能刷新，但无量比/换手率）"""
    stocks = fetch_realtime_eastmoney(symbols)
    if stocks:
        return stocks

    logger.warning("东方财富不可达，回退新浪行情")
    sina_quotes = fetch_realtime_sina(symbols)
    if not sina_quotes:
        return []

    # 转换新浪格式 → 东方财富格式（跳过 price=0，盘前/停牌）
    results = []
    for sym in symbols:
        q = sina_quotes.get(sym)
        if not q:
            continue
        prev = q.get("prev_close", 0)
        price = q.get("price", 0)
        if price <= 0:
            continue  # 盘前返回 0，跳过以保留旧数据
        pct = q.get("change_pct", 0)
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
            "turnover": 0,     # 新浪无换手率
            "vol_ratio": 0,    # 新浪无量比
            "high": q.get("high", 0),
            "low": q.get("low", 0),
            "open": q.get("open", 0),
            "prev_close": prev,
        })
    return results


def build_services(stocks: list[dict], watchlist: dict) -> list[dict]:
    """将东方财富原始数据组装为前端 Service 格式

    只写行情数据，不写 config 字段（type/cost/shares/hidden/above/below/pnl）。
    config 字段由 web /api/metrics 实时从 monitor_config.json 合并，
    确保 UI 端操作（隐藏、改持仓等）立即生效，不依赖 poller 周期。
    """
    services = []
    for s in stocks:
        code = s.get("code", "")
        services.append({
            "id": code,
            "name": s.get("name", ""),
            "price": s.get("price", 0),
            "change": s.get("pct", 0),
            "chgAmt": s.get("change", 0),
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
    config = load_config()
    watchlist = config.get("watchlist", {})
    settings = config.get("settings", {})
    symbols = list(watchlist.keys())

    if not symbols:
        logger.warning("watchlist 为空，跳过本轮")
        return False

    stocks = fetch_realtime_with_fallback(symbols)

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
        if turnover:
            existing["marketTurnover"] = turnover
        tmp = OUTPUT_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        tmp.replace(OUTPUT_PATH)
        if turnover:
            logger.info(f"大盘数据已更新: 两市 {turnover['total']:,}亿 ({turnover['verdict']})")
        return False

    services = build_services(stocks, watchlist)

    # Futu L2 增强（可选，失败时 l2_data = {}，不影响后续）
    l2_data = _futu_enricher.enrich(services)
    if l2_data:
        for svc in services:
            extra = l2_data.get(svc["id"])
            if extra:
                svc.update(extra)
        logger.info(f"L2 增强: {len(l2_data)}/{len(services)} 只")

    # 有港股持仓时获取汇率
    has_hk = any(s.startswith("HK") for s in symbols)
    hkd_cny_rate = fetch_hkd_cny_rate() if has_hk else None

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
    config = load_config()
    interval = config.get("settings", {}).get("poll_interval", 30)
    symbols = list(config.get("watchlist", {}).keys())

    print(f"Market Data Poller 启动")
    print(f"  标的数: {len(symbols)}")
    print(f"  轮询间隔: {interval}s")
    print(f"  输出文件: {OUTPUT_PATH}")
    print(f"  按 Ctrl+C 退出\n")

    # 启动时立即执行一次
    poll_once()

    try:
        while True:
            time.sleep(interval)
            # 每轮重新读取 config，这样 watchlist 变化能自动生效
            config = load_config()
            interval = config.get("settings", {}).get("poll_interval", 30)
            poll_once()
    except KeyboardInterrupt:
        print("\nPoller 已停止")


if __name__ == "__main__":
    main()
