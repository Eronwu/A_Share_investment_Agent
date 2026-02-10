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

from src.tools.stock_monitor import fetch_realtime_eastmoney, load_config
from src.utils.logging_config import setup_logger

logger = setup_logger("market_data_poller")

CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
OUTPUT_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"


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
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
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


def poll_once() -> bool:
    """执行一次抓取+写入，返回是否成功"""
    config = load_config()
    watchlist = config.get("watchlist", {})
    settings = config.get("settings", {})
    symbols = list(watchlist.keys())

    if not symbols:
        logger.warning("watchlist 为空，跳过本轮")
        return False

    stocks = fetch_realtime_eastmoney(symbols)
    if not stocks:
        logger.warning("未获取到行情数据")
        return False

    services = build_services(stocks, watchlist)

    # 有港股持仓时获取汇率
    has_hk = any(s.startswith("HK") for s in symbols)
    hkd_cny_rate = fetch_hkd_cny_rate() if has_hk else None

    payload = {
        "services": services,
        "ts": int(time.time() * 1000),
        "settings": settings,
        "hkdCnyRate": hkd_cny_rate,
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
