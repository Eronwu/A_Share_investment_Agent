#!/usr/bin/env python3
"""
Futu L2 高级数据探测脚本

逐项测试 Futu API 的 L2 数据能力，对比基础行情，
看哪些高级数据可以辅助判断走向。

用法:
    poetry run python src/tools/futu_l2_explore.py
"""

import socket
import sys
from futu import (
    OpenQuoteContext,
    RET_OK,
    SubType,
)

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
# 用腾讯做测试标的
TEST_CODE = "HK.00700"
TEST_NAME = "腾讯控股"


def check_opend():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    if sock.connect_ex((OPEND_HOST, OPEND_PORT)) != 0:
        sock.close()
        print(f"[ERROR] OpenD 未运行 ({OPEND_HOST}:{OPEND_PORT})")
        sys.exit(1)
    sock.close()


def section(title: str):
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")


def test_order_book(ctx: OpenQuoteContext):
    """10 档盘口深度 — 看买卖力量对比"""
    section(f"1. 盘口深度 10 档 — {TEST_NAME}")

    ret, _ = ctx.subscribe([TEST_CODE], [SubType.ORDER_BOOK], subscribe_push=False)
    if ret != RET_OK:
        print("[FAIL] subscribe ORDER_BOOK failed")
        return

    ret, data = ctx.get_order_book(TEST_CODE, num=10)
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    asks = data.get("Ask", [])
    bids = data.get("Bid", [])

    total_ask_vol = sum(a[1] for a in asks)
    total_bid_vol = sum(b[1] for b in bids)
    ratio = total_bid_vol / total_ask_vol if total_ask_vol else 0

    # 卖盘
    for i, ask in reversed(list(enumerate(asks))):
        price, vol, count, details = ask
        print(f"  卖{i+1:>2}  {price:>10.3f}  {vol:>10.0f}  ({count:>3} 笔)")
    print(f"  {'─' * 50}")
    # 买盘
    for i, bid in enumerate(bids):
        price, vol, count, details = bid
        print(f"  买{i+1:>2}  {price:>10.3f}  {vol:>10.0f}  ({count:>3} 笔)")

    print(f"\n  卖盘总量: {total_ask_vol:,.0f}  |  买盘总量: {total_bid_vol:,.0f}")
    print(f"  委比 (买/卖): {ratio:.2f}x  {'← 买方强势' if ratio > 1.2 else '← 卖方强势' if ratio < 0.8 else '← 均衡'}")

    # 检查 order_details（SF 权限才有）
    sample_details = asks[0][3] if asks else {}
    if sample_details:
        print(f"\n  [SF权限] 卖一挂单明细: {sample_details}")
    else:
        print(f"\n  [INFO] 无挂单明细（需要 SF 权限）")


def test_ticker(ctx: OpenQuoteContext):
    """逐笔成交 — 看大单方向"""
    section(f"2. 逐笔成交 (最近 20 笔) — {TEST_NAME}")

    ret, _ = ctx.subscribe([TEST_CODE], [SubType.TICKER], subscribe_push=False)
    if ret != RET_OK:
        print("[FAIL] subscribe TICKER failed")
        return

    ret, data = ctx.get_rt_ticker(TEST_CODE, num=20)
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    print(f"  返回字段: {list(data.columns)}")
    print()

    buy_vol, sell_vol, neutral_vol = 0, 0, 0
    big_trades = []  # 大单: > 10万股或 > 100万金额

    for _, row in data.iterrows():
        time_str = str(row.get("time", ""))
        price = row.get("price", 0)
        volume = row.get("volume", 0)
        turnover = row.get("turnover", 0)
        ticker_direction = row.get("ticker_direction", "")
        trade_type = row.get("type", "")

        direction_symbol = "B" if "BUY" in str(ticker_direction).upper() else \
                          "S" if "SELL" in str(ticker_direction).upper() else "N"

        if direction_symbol == "B":
            buy_vol += volume
        elif direction_symbol == "S":
            sell_vol += volume
        else:
            neutral_vol += volume

        # 大单判断
        if volume >= 100000 or turnover >= 1000000:
            big_trades.append((time_str, price, volume, turnover, direction_symbol))

        print(f"  {time_str[-8:]}  {price:>10.3f}  {volume:>8.0f} 股  "
              f"{turnover:>12,.0f} 元  {direction_symbol}")

    total = buy_vol + sell_vol + neutral_vol
    print(f"\n  主买: {buy_vol:,.0f}  主卖: {sell_vol:,.0f}  中性: {neutral_vol:,.0f}")
    if buy_vol + sell_vol > 0:
        net = buy_vol - sell_vol
        print(f"  净买入: {net:+,.0f} 股  ({'买方主导' if net > 0 else '卖方主导'})")

    if big_trades:
        print(f"\n  大单 ({len(big_trades)} 笔):")
        for t in big_trades:
            print(f"    {t[0][-8:]}  {t[1]:>10.3f}  {t[2]:>8.0f} 股  {t[3]:>12,.0f} 元  {t[4]}")


def test_broker_queue(ctx: OpenQuoteContext):
    """经纪商排队 — 看哪些券商在买卖"""
    section(f"3. 经纪商排队 — {TEST_NAME}")

    ret, _ = ctx.subscribe([TEST_CODE], [SubType.BROKER], subscribe_push=False)
    if ret != RET_OK:
        print("[FAIL] subscribe BROKER failed")
        return

    ret, bid_data, ask_data = ctx.get_broker_queue(TEST_CODE)
    if ret != RET_OK:
        print(f"[FAIL] {bid_data}")
        return

    print(f"  买方经纪 (bid) 字段: {list(bid_data.columns) if hasattr(bid_data, 'columns') else type(bid_data)}")
    print(f"  卖方经纪 (ask) 字段: {list(ask_data.columns) if hasattr(ask_data, 'columns') else type(ask_data)}")

    if hasattr(bid_data, 'iterrows'):
        print(f"\n  买方经纪 (前 10):")
        for i, (_, row) in enumerate(bid_data.head(10).iterrows()):
            print(f"    {row.to_dict()}")

        print(f"\n  卖方经纪 (前 10):")
        for i, (_, row) in enumerate(ask_data.head(10).iterrows()):
            print(f"    {row.to_dict()}")


def test_capital_flow(ctx: OpenQuoteContext):
    """资金流向 — 大单/中单/小单 资金净流入"""
    section(f"4. 资金流向 — {TEST_NAME}")

    ret, data = ctx.get_capital_flow(TEST_CODE)
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    print(f"  返回字段: {list(data.columns)}")
    print()

    # 显示最近几条
    for _, row in data.tail(5).iterrows():
        print(f"  {row.to_dict()}")


def test_capital_distribution(ctx: OpenQuoteContext):
    """资金分布 — 各价位的资金量"""
    section(f"5. 资金分布 — {TEST_NAME}")

    ret, data = ctx.get_capital_distribution(TEST_CODE)
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    print(f"  返回字段: {list(data.columns)}")
    print()

    if hasattr(data, 'iterrows'):
        for _, row in data.iterrows():
            print(f"  {row.to_dict()}")
    else:
        print(f"  数据: {data}")


def test_rt_data(ctx: OpenQuoteContext):
    """分时数据 — 每分钟均价/成交量"""
    section(f"6. 分时数据 (最近 10 条) — {TEST_NAME}")

    ret, _ = ctx.subscribe([TEST_CODE], [SubType.RT_DATA], subscribe_push=False)
    if ret != RET_OK:
        print("[FAIL] subscribe RT_DATA failed")
        return

    ret, data = ctx.get_rt_data(TEST_CODE)
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    print(f"  返回字段: {list(data.columns)}")
    print()

    for _, row in data.tail(10).iterrows():
        print(f"  {row.to_dict()}")


def test_snapshot_extra(ctx: OpenQuoteContext):
    """快照里的额外 L2 字段"""
    section(f"7. 快照额外字段 — {TEST_NAME}")

    ret, data = ctx.get_market_snapshot([TEST_CODE])
    if ret != RET_OK:
        print(f"[FAIL] {data}")
        return

    row = data.iloc[0]

    # 找出所有非空、非零、非 N/A 的字段
    interesting = {}
    for col in data.columns:
        val = row[col]
        if val is not None and str(val) not in ("0", "0.0", "", "nan", "N/A", "False"):
            interesting[col] = val

    # 基础字段
    basic_keys = {"code", "name", "last_price", "open_price", "high_price",
                  "low_price", "prev_close_price", "volume", "turnover",
                  "turnover_rate", "update_time", "lot_size", "listing_date",
                  "price_spread"}

    print("  基础字段 (你已有的):")
    for k in sorted(basic_keys):
        if k in interesting:
            print(f"    {k}: {interesting[k]}")

    print("\n  额外字段 (L2/高级):")
    for k, v in sorted(interesting.items()):
        if k not in basic_keys:
            print(f"    {k}: {v}")


def main():
    check_opend()
    print(f"[INFO] 探测 Futu L2 高级数据 — 标的: {TEST_CODE} {TEST_NAME}")

    ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    try:
        test_snapshot_extra(ctx)
        test_order_book(ctx)
        test_ticker(ctx)
        test_broker_queue(ctx)
        test_capital_flow(ctx)
        test_capital_distribution(ctx)
        test_rt_data(ctx)
    finally:
        ctx.close()

    print(f"\n{'=' * 80}")
    print(f"  探测完成")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
