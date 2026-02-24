#!/usr/bin/env python3
"""
Daily Summary Generator — post-market LLM-powered signal digest

Aggregates L2 strategy signals, alert events, and market data after market close,
then calls LLM to produce a structured daily report for the web dashboard.

Output: src/data/daily_summary.json

Usage:
    poetry run python -c "from src.tools.daily_summary_generator import generate_daily_summary; generate_daily_summary()"
"""

import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.llm_clients import LLMClientFactory
from src.utils.logging_config import setup_logger

logger = setup_logger("daily_summary")

# ── Data file paths ──
DATA_DIR = PROJECT_ROOT / "src" / "data"
MARKET_DATA_PATH = DATA_DIR / "market_data.json"
MONITOR_CONFIG_PATH = DATA_DIR / "monitor_config.json"
ALERT_CONFIG_PATH = DATA_DIR / "alert_config.json"
ALERT_EVENTS_PATH = DATA_DIR / "alert_events.json"
L2_SIGNALS_PATH = DATA_DIR / "l2_strategy_signals.json"
DAILY_SUMMARY_PATH = DATA_DIR / "daily_summary.json"


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _detect_market(signals: list[dict], watchlist: dict) -> str:
    """Detect primary market from signal source, fallback to watchlist."""
    if signals:
        hk_sigs = sum(1 for s in signals if s.get("code", "").startswith("HK"))
        return "HK" if hk_sigs > len(signals) // 2 else "A"
    hk_count = sum(1 for k in watchlist if k.startswith("HK"))
    return "HK" if hk_count >= len(watchlist) - hk_count else "A"


def _aggregate_signals(signals: list[dict]) -> dict:
    """Aggregate L2 signals by stock code.

    Returns: {code: {strategies: Counter, directions: [], displays: [], count: int}}
    """
    by_stock: dict = defaultdict(lambda: {
        "strategies": Counter(),
        "directions": [],
        "displays": [],
        "count": 0,
        "notify_count": 0,
    })

    for s in signals:
        code = s.get("code", "")
        if not code:
            continue
        entry = by_stock[code]
        entry["count"] += 1
        strategy = s.get("strategy", "unknown")
        entry["strategies"][strategy] += 1
        if s.get("notify"):
            entry["notify_count"] += 1
        # Collect display text for key signals (L1 / notify=True)
        if s.get("notify") and s.get("display"):
            entry["displays"].append(s["display"])

    return dict(by_stock)


def _aggregate_alerts(events: list[dict]) -> dict:
    """Aggregate non-L2 alert events by stock code.

    L2 signals are already counted from l2_strategy_signals.json,
    so we exclude kind=l2_strategy to avoid double counting.

    Returns: {code: {kinds: Counter, count: int}}
    """
    by_stock: dict = defaultdict(lambda: {"kinds": Counter(), "count": 0})
    for e in events:
        code = e.get("symbol", "")
        if not code:
            continue
        # Skip L2 signals — already aggregated from l2_strategy_signals.json
        if e.get("kind") == "l2_strategy":
            continue
        by_stock[code]["count"] += 1
        by_stock[code]["kinds"][e.get("kind", "unknown")] += 1
    return dict(by_stock)


def _build_per_stock(
    market_data: dict,
    config: dict,
    signal_agg: dict,
    alert_agg: dict,
) -> list[dict]:
    """Build per-stock summary combining market data + signals + alerts."""
    watchlist = config.get("watchlist", {})
    services = {s["id"]: s for s in market_data.get("services", []) if s.get("id")}

    per_stock = []
    # Combine all codes from watchlist, signals, and market data
    all_codes = set(watchlist.keys()) | set(signal_agg.keys())

    for code in sorted(all_codes):
        svc = services.get(code, {})
        entry = watchlist.get(code, {})
        sig = signal_agg.get(code, {})
        alrt = alert_agg.get(code, {})

        name = entry.get("name") or svc.get("name", code)
        change = svc.get("change", 0)
        price = svc.get("price", 0)
        signal_count = sig.get("count", 0)
        alert_count = alrt.get("count", 0)

        if signal_count == 0 and alert_count == 0:
            continue

        # Determine direction from signals
        strategies = sig.get("strategies", Counter())
        bullish = strategies.get("composite_bullish", 0)
        bearish = strategies.get("composite_bearish", 0)
        if bullish > bearish:
            direction = "偏多"
        elif bearish > bullish:
            direction = "偏空"
        elif bullish > 0 and bearish > 0:
            direction = "多空交织"
        else:
            direction = "中性"

        # Top signal types
        key_signals = []
        for strat, cnt in strategies.most_common(5):
            # Translate strategy names to readable labels
            label_map = {
                "composite_bullish": "多头信号",
                "composite_bearish": "空头信号",
                "large_order": "大单成交",
                "tick_imbalance": "主买卖失衡",
                "volume_price_divergence": "量价背离",
                "momentum_buy": "动量买入",
                "momentum_sell": "动量卖出",
                "momentum_buy_alert": "动量确认",
                "momentum_sell_alert": "动量卖出",
                "volume_accel_alert": "放量加速",
                "volume_crash_alert": "放量砸盘",
                "sustained_buying": "主买持续",
                "sustained_selling": "主卖持续",
                "retail_institutional_divergence": "散户机构分歧",
                "institutional_retail_divergence": "散户机构分歧",
                "large_order_flip": "大单翻转",
                "large_order_reversal": "大单翻转",
                "late_session_activity": "尾盘异动",
                "rsi_extreme": "RSI极值",
                "rsi_overbought": "RSI超买",
                "rsi_oversold": "RSI超卖",
                "macd_cross": "MACD交叉",
                "macd_top_divergence": "MACD顶背离",
                "macd_bottom_divergence": "MACD底背离",
                "ma_alignment": "均线信号",
                "ma_bullish_align": "均线多排",
                "ma_bearish_align": "均线空排",
                "bollinger_breakout": "布林突破",
                "adx_trend_start": "趋势启动",
                "volume_breakout": "放量突破",
                "candlestick_pattern": "K线形态",
                "engulfing_pattern": "吞没形态",
                "key_level_test": "关键位置",
                "breakout_pullback": "突破回踩",
                "relative_strength": "相对强弱",
                "order_book_imbalance": "盘口失衡",
                "capital_flow_spike": "资金异动",
                "macd_golden_cross": "MACD金叉",
                "macd_death_cross": "MACD死叉",
            }
            label = label_map.get(strat, strat)
            key_signals.append(f"{label}x{cnt}" if cnt > 1 else label)

        # Include alert kinds
        for kind, cnt in alrt.get("kinds", Counter()).most_common(3):
            kind_label = {"big_move": "大幅异动", "threshold": "触价告警"}.get(kind, kind)
            if cnt > 1:
                key_signals.append(f"{kind_label}x{cnt}")
            elif kind_label not in key_signals:
                key_signals.append(kind_label)

        per_stock.append({
            "code": code,
            "name": name,
            "type": entry.get("type", "watching"),
            "price": price,
            "change": round(change, 2),
            "signalCount": signal_count,
            "alertCount": alert_count,
            "direction": direction,
            "keySignals": key_signals[:6],
        })

    # Sort: holdings first, then by signal count desc
    per_stock.sort(key=lambda x: (0 if x["type"] == "holding" else 1, -x["signalCount"]))
    return per_stock


def _build_stats(
    signals: list[dict],
    events: list[dict],
    per_stock: list[dict],
) -> dict:
    """Build overall stats summary."""
    total_signals = len(signals)
    l1_count = sum(1 for s in signals if s.get("notify"))

    # Count bullish/bearish composite signals
    bullish = sum(1 for s in signals if s.get("strategy") == "composite_bullish")
    bearish = sum(1 for s in signals if s.get("strategy") == "composite_bearish")

    stock_count = len(per_stock)
    up_count = sum(1 for ps in per_stock if ps["change"] > 0)
    down_count = sum(1 for ps in per_stock if ps["change"] < 0)

    return {
        "totalSignals": total_signals,
        "totalAlerts": len(events),
        "l1Count": l1_count,
        "bullish": bullish,
        "bearish": bearish,
        "stockCount": stock_count,
        "upCount": up_count,
        "downCount": down_count,
    }


def _build_llm_prompt(stats: dict, per_stock: list[dict], l1_displays: list[str]) -> list[dict]:
    """Construct messages for LLM daily report generation."""
    system = """你是一位资深量化工程师，专注 A 股和港股。根据今日 L2 策略信号和告警数据，生成简洁的持仓信号日报。

格式要求（严格遵守，不要偏离）：

## 市场整体评估
（2-3句：今日多空氛围、信号分布、异常情况）

## 重点关注
1. 代码 名称
   - 理由：...
（选2-3只信号最密集或方向最明确的标的）

## 逐股一句话
- 代码 名称 — 总结
（每只有信号的标的一句话）

## 操作建议
1. ...
（2-3条具体可操作建议，包含风控提醒）

规则：
- 标题用 ## 不用 ###，标题上不要加 **加粗**
- 正文中股票名称可以用 **加粗**
- 风格专业简洁，使用量化术语（多空、主力、资金流向）
- 输出纯 Markdown，不要代码块包裹"""

    # Build data section
    holdings = [ps for ps in per_stock if ps["type"] == "holding"]
    watching = [ps for ps in per_stock if ps["type"] != "holding"]

    lines = []
    lines.append(f"## 今日统计")
    lines.append(f"- 总信号数: {stats['totalSignals']} | L1高优: {stats['l1Count']}")
    lines.append(f"- 多头信号: {stats['bullish']} | 空头信号: {stats['bearish']}")
    lines.append(f"- 监控标的: {stats['stockCount']}只 | 收涨: {stats['upCount']} 收跌: {stats['downCount']}")
    lines.append(f"- 告警事件: {stats['totalAlerts']}")
    lines.append("")

    if holdings:
        lines.append("## 持仓标的")
        for ps in holdings:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else "无信号"
            lines.append(f"- {ps['code']} {ps['name']} | 涨跌:{ps['change']:+.2f}% | 信号:{ps['signalCount']}条 | 方向:{ps['direction']} | {sigs}")
        lines.append("")

    if watching:
        lines.append("## 自选标的")
        for ps in watching:
            if ps["signalCount"] > 0:
                sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else ""
                lines.append(f"- {ps['code']} {ps['name']} | {ps['change']:+.2f}% | 信号:{ps['signalCount']} | {sigs}")
        lines.append("")

    if l1_displays:
        lines.append("## L1 关键事件（原文）")
        for d in l1_displays[-10:]:  # Limit to last 10
            lines.append(f"- {d}")

    user_msg = "\n".join(lines)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


def generate_daily_summary(date_str: str | None = None) -> dict | None:
    """Generate daily summary report.

    Args:
        date_str: Override date (YYYY-MM-DD). Defaults to today.

    Returns:
        The generated summary dict, or None on failure.
    """
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Generating daily summary for {today}...")

    # ── Read data sources ──
    market_data = _read_json(MARKET_DATA_PATH) or {"services": []}
    config = _read_json(MONITOR_CONFIG_PATH) or {"watchlist": {}, "settings": {}}
    alert_events_data = _read_json(ALERT_EVENTS_PATH) or {"events": []}
    l2_signals_data = _read_json(L2_SIGNALS_PATH) or {"signals": []}

    signals = l2_signals_data.get("signals", [])
    events = alert_events_data.get("events", [])
    watchlist = config.get("watchlist", {})

    if not signals and not events:
        logger.warning("No signals or events found — skipping summary generation")
        return None

    # ── Aggregate ──
    signal_agg = _aggregate_signals(signals)
    alert_agg = _aggregate_alerts(events)
    per_stock = _build_per_stock(market_data, config, signal_agg, alert_agg)
    stats = _build_stats(signals, events, per_stock)

    # Collect L1 display texts for LLM context
    l1_displays = []
    for s in signals:
        if s.get("notify") and s.get("display"):
            l1_displays.append(s["display"])

    market = _detect_market(signals, watchlist)

    # ── Call LLM ──
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    messages = _build_llm_prompt(stats, per_stock, l1_displays)
    report = None
    try:
        client = LLMClientFactory.create_client()
        report = client.get_completion(messages)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")

    if not report:
        logger.warning("LLM returned empty response — using stats-only summary")
        report = _fallback_report(stats, per_stock)

    # ── Build output ──
    summary = {
        "date": today,
        "generatedAt": int(time.time() * 1000),
        "market": market,
        "stats": stats,
        "perStock": [
            {
                "code": ps["code"],
                "name": ps["name"],
                "change": ps["change"],
                "signalCount": ps["signalCount"],
                "direction": ps["direction"],
                "keySignals": ps["keySignals"],
            }
            for ps in per_stock
        ],
        "report": report,
    }

    # ── Atomic write ──
    tmp = DAILY_SUMMARY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    tmp.replace(DAILY_SUMMARY_PATH)

    logger.info(f"Daily summary written to {DAILY_SUMMARY_PATH.name} "
                f"({stats['totalSignals']} signals, {len(per_stock)} stocks)")
    return summary


def _fallback_report(stats: dict, per_stock: list[dict]) -> str:
    """Generate a plain-text report when LLM is unavailable."""
    lines = [
        f"# 持仓信号日报",
        "",
        f"## 统计",
        f"- 总信号: {stats['totalSignals']} | L1: {stats['l1Count']}",
        f"- 多头: {stats['bullish']} | 空头: {stats['bearish']}",
        f"- 收涨: {stats['upCount']} | 收跌: {stats['downCount']}",
        "",
        f"## 逐股摘要",
    ]
    for ps in per_stock:
        if ps["signalCount"] > 0:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else ""
            lines.append(f"- {ps['code']} {ps['name']} {ps['change']:+.2f}% | {ps['direction']} | {sigs}")
    lines.append("")
    lines.append("*LLM 不可用，仅展示统计数据*")
    return "\n".join(lines)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    result = generate_daily_summary()
    if result:
        print(f"Generated: {result['date']} — {result['stats']['totalSignals']} signals")
    else:
        print("No summary generated (no data)")
