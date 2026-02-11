#!/usr/bin/env python3
"""
Futu OpenAPI 行情验证脚本

连接本地 OpenD，拉取 watchlist 实时行情 + 港股 LV2 盘口，
打印到终端供人工对比验证。不写入任何文件，不影响现有 poller。

前置条件:
    1. 安装 OpenD 并登录: https://openapi.futunn.com/futu-api-doc/opend/opend-cmd.html
    2. poetry install (futu-api 已加入 pyproject.toml)

用法:
    poetry run python src/tools/futu_quote_demo.py
"""

import json
import socket
import sys
from pathlib import Path

from futu import (
    OpenQuoteContext,
    RET_OK,
    SubType,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111


# ── 代码映射 ─────────────────────────────────────────────


def to_futu_code(code: str) -> str:
    """本项目代码 → Futu 格式

    HK09988  → HK.09988
    688676   → SH.688676
    002848   → SZ.002848
    000001   → SZ.000001
    """
    if code.startswith("HK"):
        return f"HK.{code[2:]}"

    # A 股: 6/5 开头 → 上海, 0/1/3 开头 → 深圳
    first = code[0]
    if first in ("6", "5"):
        return f"SH.{code}"
    if first in ("0", "1", "2", "3"):
        return f"SZ.{code}"

    # 未知前缀，兜底上海
    return f"SH.{code}"


def from_futu_code(futu_code: str) -> str:
    """Futu 格式 → 本项目代码

    HK.09988  → HK09988
    SH.688676 → 688676
    SZ.002848 → 002848
    """
    market, num = futu_code.split(".", 1)
    if market == "HK":
        return f"HK{num}"
    return num


# ── 加载 watchlist ───────────────────────────────────────


def load_watchlist() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("watchlist", {})


# ── 拉行情 ──────────────────────────────────────────────


def _is_etf(code: str) -> bool:
    """判断 A 股 ETF（1/5 开头的 6 位代码）"""
    if code.startswith(("HK.", "US.")):
        return False
    num = code.split(".", 1)[1]
    return num.startswith(("1", "5"))


def fetch_snapshots(ctx: OpenQuoteContext, futu_codes: list[str]) -> list[dict]:
    """分批拉快照 — 港股、A 股个股、A 股 ETF 分开请求，互不影响"""
    import pandas as pd

    # 分三组
    hk = [c for c in futu_codes if c.startswith("HK.")]
    a_stock = [c for c in futu_codes if not c.startswith("HK.") and not _is_etf(c)]
    a_etf = [c for c in futu_codes if not c.startswith("HK.") and _is_etf(c)]

    frames = []
    for label, batch in [("港股", hk), ("A股个股", a_stock), ("A股ETF", a_etf)]:
        if not batch:
            continue
        ret, data = ctx.get_market_snapshot(batch)
        if ret == RET_OK:
            frames.append(data)
            print(f"[OK] {label}: {len(data)} 只")
        else:
            print(f"[WARN] {label} ({len(batch)} 只) 失败: {data}")

    if not frames:
        return []

    all_data = pd.concat(frames, ignore_index=True)
    results = []
    for _, row in all_data.iterrows():
        prev_close = row.get("prev_close_price", 0)
        last_price = row.get("last_price", 0)
        change_rate = (
            (last_price - prev_close) / prev_close * 100 if prev_close else 0
        )

        results.append({
            "code": from_futu_code(row["code"]),
            "name": row.get("name", ""),
            "price": last_price,
            "change": round(change_rate, 2),
            "chgAmt": row.get("price_spread", 0),
            "vol": row.get("volume", 0),
            "amount": row.get("turnover", 0),
            "amp": row.get("amplitude", 0),
            "turnover": row.get("turnover_rate", 0),
            "volRatio": row.get("volume_ratio", 0),
            "high": row.get("high_price", 0),
            "low": row.get("low_price", 0),
            "open": row.get("open_price", 0),
            "prevClose": prev_close,
        })
    return results


def fetch_order_books(
    ctx: OpenQuoteContext, hk_futu_codes: list[str]
) -> dict[str, dict]:
    """港股 LV2 盘口（买卖 10 档）"""
    if not hk_futu_codes:
        return {}

    # 订阅盘口数据
    ret, err = ctx.subscribe(hk_futu_codes, [SubType.ORDER_BOOK], subscribe_push=False)
    if ret != RET_OK:
        print(f"[ERROR] subscribe ORDER_BOOK failed: {err}")
        return {}

    books = {}
    for code in hk_futu_codes:
        ret, data = ctx.get_order_book(code, num=10)
        if ret == RET_OK:
            books[from_futu_code(code)] = data
        else:
            print(f"[WARN] get_order_book({code}) failed: {data}")
    return books


# ── 格式化输出 ───────────────────────────────────────────


def print_quote_table(title: str, quotes: list[dict]) -> None:
    """打印行情表"""
    if not quotes:
        print(f"\n{'=' * 60}")
        print(f"  {title} — 无数据")
        return

    print(f"\n{'=' * 80}")
    print(f"  {title} ({len(quotes)} 只)")
    print(f"{'=' * 80}")
    header = f"{'代码':<10} {'名称':<10} {'最新价':>8} {'涨跌幅':>7} {'涨跌额':>8} {'成交量':>10} {'振幅':>6} {'换手':>6}"
    print(header)
    print("-" * 80)

    for q in sorted(quotes, key=lambda x: x["change"], reverse=True):
        change_str = f"{q['change']:+.2f}%"
        print(
            f"{q['code']:<10} {q['name']:<10} {q['price']:>8.3f} {change_str:>7} "
            f"{q['chgAmt']:>+8.3f} {q['vol']:>10.0f} {q['amp']:>5.2f}% {q['turnover']:>5.2f}%"
        )


def print_order_book(code: str, name: str, book: dict) -> None:
    """打印单只港股的盘口"""
    asks = book.get("Ask", [])
    bids = book.get("Bid", [])

    print(f"\n  {code} {name} — LV2 盘口")
    print(f"  {'─' * 40}")

    # 卖盘（倒序，卖 10 在上）
    for i, ask in reversed(list(enumerate(asks))):
        price, vol, count, _ = ask
        print(f"  卖{i+1:>2}  {price:>10.3f}  {vol:>8.0f}  ({count} 笔)")

    print(f"  {'─' * 40}")

    # 买盘
    for i, bid in enumerate(bids):
        price, vol, count, _ = bid
        print(f"  买{i+1:>2}  {price:>10.3f}  {vol:>8.0f}  ({count} 笔)")


# ── main ─────────────────────────────────────────────────


def main():
    watchlist = load_watchlist()
    if not watchlist:
        print("[ERROR] watchlist is empty")
        sys.exit(1)

    # 分离 A 股和港股
    a_codes, hk_codes = [], []
    names = {}
    for code, info in watchlist.items():
        futu_code = to_futu_code(code)
        names[code] = info.get("name", code)
        if code.startswith("HK"):
            hk_codes.append(futu_code)
        else:
            a_codes.append(futu_code)

    all_codes = a_codes + hk_codes
    print(f"[INFO] watchlist: {len(a_codes)} A股 + {len(hk_codes)} 港股 = {len(all_codes)} 只")
    print(f"[INFO] connecting to OpenD at {OPEND_HOST}:{OPEND_PORT} ...")

    # 先检测 OpenD 端口是否可达，避免 SDK 内部长时间重试
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    if sock.connect_ex((OPEND_HOST, OPEND_PORT)) != 0:
        sock.close()
        print(f"[ERROR] OpenD 未运行 ({OPEND_HOST}:{OPEND_PORT})")
        print("[HINT] 请先下载并启动 OpenD，登录富途账号后再运行此脚本")
        print("[LINK] https://www.futunn.com/download/openAPI")
        sys.exit(1)
    sock.close()

    ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    try:
        # 拉快照
        all_quotes = fetch_snapshots(ctx, all_codes)
        a_quotes = [q for q in all_quotes if not q["code"].startswith("HK")]
        hk_quotes = [q for q in all_quotes if q["code"].startswith("HK")]

        print_quote_table("A 股行情", a_quotes)
        print_quote_table("港股行情", hk_quotes)

        # 港股 LV2 盘口
        if hk_codes:
            books = fetch_order_books(ctx, hk_codes)
            if books:
                print(f"\n{'=' * 80}")
                print(f"  港股 LV2 盘口 ({len(books)} 只)")
                print(f"{'=' * 80}")
                for code, book in books.items():
                    print_order_book(code, names.get(code, ""), book)
    finally:
        ctx.close()

    print(f"\n[INFO] done.")


if __name__ == "__main__":
    main()
