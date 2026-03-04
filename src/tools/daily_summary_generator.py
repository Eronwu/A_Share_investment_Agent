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
L2_SIGNALS_PATH = DATA_DIR / "l2_strategy_signals.json"
DAILY_SUMMARY_PATH = DATA_DIR / "daily_summary.json"
TRADE_PLANS_PATH = DATA_DIR / "trade_plans.json"


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
    l2_digest_map: dict | None = None,
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

        is_star = entry.get("star", False)
        if signal_count == 0 and alert_count == 0 and not is_star:
            continue

        # Determine direction: prefer l2_digest direction_score, fallback to signal counts
        strategies = sig.get("strategies", Counter())
        digest = (l2_digest_map or {}).get(code, {})
        ds = digest.get("direction_score", 0)
        if ds > 2:
            direction = "偏多"
        elif ds < -2:
            direction = "偏空"
        elif ds != 0:
            # Weak signal from digest
            direction = "偏多" if ds > 0 else "偏空" if ds < 0 else "中性"
        else:
            # Fallback: composite signal counts
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
                "macd_cross": "MACD交叉",  # legacy fallback
                "macd_golden_cross": "MACD金叉",
                "macd_death_cross": "MACD死叉",
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
                "support_breakdown": "破位下跌",
                "morning_evening_star": "晨暮星",
                "volume_divergence_top": "缩量创高",
                "rsi_extreme_overbought": "RSI严重超买",
                "rsi_extreme_oversold": "RSI严重超卖",
                "tick_persistence": "tick持续偏向",
                "closing_surge": "尾盘异动",
                "momentum_sell_alert": "动量卖出确认",
                "volume_accel_sell_alert": "放量砸盘确认",
                "bollinger_squeeze_breakout": "布林带突破",
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

        # Position size (market value in CNY)
        cost = entry.get("cost", 0) or 0
        shares = entry.get("shares", 0) or 0
        is_hk = code.startswith("HK")
        fx = 0.92 if is_hk else 1
        mkt_val = price * shares * fx if price > 0 and shares > 0 else 0
        pnl_pct = ((price - cost) / cost * 100) if cost > 0 and price > 0 else None

        per_stock.append({
            "code": code,
            "name": name,
            "type": entry.get("type", "watching"),
            "star": entry.get("star", False),
            "price": price,
            "change": round(change, 2),
            "cost": cost,
            "shares": shares,
            "mkt_val": round(mkt_val, 0),
            "pnl_pct": round(pnl_pct, 1) if pnl_pct is not None else None,
            "fx": fx,
            "signalCount": signal_count,
            "alertCount": alert_count,
            "direction": direction,
            "keySignals": key_signals[:6],
        })

    # Sort: star first, then holdings by market value desc, then watching by signal count
    per_stock.sort(key=lambda x: (
        0 if x.get("star") else 1,
        0 if x["type"] == "holding" else 1,
        -x["mkt_val"],
        -x["signalCount"],
    ))
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


def _build_llm_prompt(stats: dict, per_stock: list[dict], l1_displays: list[str],
                      l2_digest_map: dict | None = None,
                      trade_plans: dict | None = None) -> list[dict]:
    """Construct messages for LLM daily report generation."""
    system = """你是一位资深量化工程师，专注 A 股和港股。根据今日 L2 策略信号、微观结构数据和告警数据，生成简洁的持仓信号日报。

格式要求（严格遵守，不要偏离）：

## 市场整体评估
（3-5句：今日多空氛围、资金面整体方向、板块分化。用具体数字描述，例如"持仓中X只大单净买入、X只tick偏买超过20%"、"整体主力资金净流入约X亿"等）

## 重点关注
（优先选★重点标记和持仓市值大的标的，每只写2-3句深度分析。★重点股必须出现在此节）

1. **代码 名称** — ...
   在分析中自然引用微观数据，例如：
   "今日大单净买入0.19亿，tick偏买12.4%，主力资金净流入7.57亿，三维共振偏多。但大单翻转3次提示盘中多空拉锯激烈。"
   重点解读：
   - 多维度矛盾（大单方向 vs tick方向 vs 资金流向不一致时，分析原因）
   - tick偏买/偏卖超过20%的异常值
   - 量价背离频次高（≥3次）的风险信号
   - 综合方向评分极端值（>+4 或 <-4）

## 逐股速览
- **代码 名称** — 一句话总结，自然穿插1-2个关键数据（如"tick偏卖28%+主力流出0.51亿，空头格局"）
（只覆盖有信号的标的，无信号的跳过）

## 操作建议
1. ...
（2-3条建议，每条必须基于具体微观数据得出，不要泛泛而谈）

写作规则：
- 标题用 ## 不用 ###，标题上不要加 **加粗**
- 正文中股票名称可以用 **加粗**
- **严禁模糊表述**：不可写"资金流入显著"、"大单活跃"等空话，必须写"大单净买0.19亿"、"tick偏买12.4%"这样的具体数字
- 数据要像专业研报一样自然融入文字中，不要堆砌成表格
- 当大单方向与资金流向矛盾时（如大单净卖但主力净流入），需解读原因（算法拆单、对倒等）
- 标注[★重点]的股票（包括持仓和自选）是用户最关注的，必须在"重点关注"中详细分析
- 持仓市值大的股票对组合影响大，也应优先关注
- 持仓标的标签含成本和盈亏%，据此给出是否加仓/减仓/止损建议
- 如有条件单数据，在操作建议中结合条件单距离给出提醒（如"阿里距买入条件单125仅3.5%"）
- ★重点自选标的同样需要深度分析微观数据，不能只给一句话
- 风格：专业简洁，像给基金经理写的晨会纪要
- 输出纯 Markdown，不要代码块包裹"""

    # Build data section
    holdings = [ps for ps in per_stock if ps["type"] == "holding"]
    watching = [ps for ps in per_stock if ps["type"] != "holding"]

    lines = []

    # ── Fix 5: Portfolio summary ──
    if holdings:
        total_mkt = sum(ps["mkt_val"] for ps in holdings)
        total_cost = sum(ps["cost"] * ps["shares"] * ps.get("fx", 1) for ps in holdings if ps["cost"] > 0 and ps["shares"] > 0)
        total_pnl = total_mkt - total_cost if total_cost > 0 else 0
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        hk_day = sum(ps["change"] / 100 * ps["mkt_val"] for ps in holdings if ps["code"].startswith("HK") and ps["mkt_val"] > 0)
        a_day = sum(ps["change"] / 100 * ps["mkt_val"] for ps in holdings if not ps["code"].startswith("HK") and ps["mkt_val"] > 0)
        h_up = sum(1 for ps in holdings if ps["change"] > 0)
        h_down = sum(1 for ps in holdings if ps["change"] < 0)
        lines.append("## 组合概况")
        lines.append(f"- 持仓 {len(holdings)} 只, 总市值 {total_mkt/10000:.1f}万, 总浮盈 {total_pnl/10000:+.1f}万 ({total_pnl_pct:+.1f}%)")
        day_parts = []
        if any(ps["code"].startswith("HK") for ps in holdings):
            day_parts.append(f"港股 {hk_day/10000:+.1f}万")
        if any(not ps["code"].startswith("HK") for ps in holdings):
            day_parts.append(f"A股 {a_day/10000:+.1f}万")
        if day_parts:
            lines.append(f"- 今日变化: {', '.join(day_parts)}")
        lines.append(f"- 涨: {h_up}只 跌: {h_down}只")
        lines.append("")

    lines.append(f"## 今日统计")
    lines.append(f"- 总信号数: {stats['totalSignals']} | L1高优: {stats['l1Count']}")
    lines.append(f"- 多头信号: {stats['bullish']} | 空头信号: {stats['bearish']}")
    lines.append(f"- 监控标的: {stats['stockCount']}只 | 收涨: {stats['upCount']} 收跌: {stats['downCount']}")
    lines.append(f"- 告警事件: {stats['totalAlerts']}")
    lines.append("")

    digest_map = l2_digest_map or {}

    if holdings:
        lines.append("## 持仓标的")
        for ps in holdings:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else "无信号"
            # Star + position size + cost/pnl tags
            tags = []
            if ps.get("star"):
                tags.append("★重点")
            if ps["mkt_val"] > 0:
                val_wan = ps["mkt_val"] / 10000
                tags.append(f"持仓{val_wan:.1f}万")
            if ps.get("cost") and ps["cost"] > 0:
                tags.append(f"成本{ps['cost']:.2f}")
            if ps.get("pnl_pct") is not None:
                tags.append(f"盈亏{ps['pnl_pct']:+.1f}%")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            line = f"- {ps['code']} {ps['name']}{tag_str} | 涨跌:{ps['change']:+.2f}% | 信号:{ps['signalCount']}条 | 方向:{ps['direction']} | {sigs}"
            # Append L2 microstructure digest
            d = digest_map.get(ps["code"])
            if d:
                lo_net_yi = d["lo_net_amount"] / 1e8
                cf_yi = d["cf_net_inflow"] / 1e8
                tick_pct = d["tick_imbalance"] * 100
                micro = f"  微观: 大单净额{lo_net_yi:+.2f}亿(净比{d['lo_net_ratio']:+.2f}) tick{tick_pct:+.1f}% 主力{cf_yi:+.2f}亿"
                if d["vpd_count"] or d["lor_count"]:
                    events_parts = []
                    if d["vpd_count"]:
                        events_parts.append(f"背离x{d['vpd_count']}")
                    if d["lor_count"]:
                        dir_label = "多" if d["lor_direction"] == "bullish" else "空" if d["lor_direction"] == "bearish" else "?"
                        events_parts.append(f"翻转x{d['lor_count']}({dir_label})")
                    micro += f" | {' '.join(events_parts)}"
                micro += f" | 综合:{d['direction_score']:+d}({d['direction']})"
                line += "\n" + micro
            lines.append(line)
        lines.append("")

    # Star watching stocks get full analysis like holdings
    star_watching = [ps for ps in watching if ps.get("star")]
    other_watching = [ps for ps in watching if not ps.get("star")]

    if star_watching:
        lines.append("## ★ 重点自选")
        for ps in star_watching:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else "无信号"
            line = f"- {ps['code']} {ps['name']} [★重点] | 涨跌:{ps['change']:+.2f}% | 信号:{ps['signalCount']}条 | 方向:{ps['direction']} | {sigs}"
            d = digest_map.get(ps["code"])
            if d:
                lo_net_yi = d["lo_net_amount"] / 1e8
                cf_yi = d["cf_net_inflow"] / 1e8
                tick_pct = d["tick_imbalance"] * 100
                micro = f"  微观: 大单净额{lo_net_yi:+.2f}亿(净比{d['lo_net_ratio']:+.2f}) tick{tick_pct:+.1f}% 主力{cf_yi:+.2f}亿"
                if d["vpd_count"] or d["lor_count"]:
                    events_parts = []
                    if d["vpd_count"]:
                        events_parts.append(f"背离x{d['vpd_count']}")
                    if d["lor_count"]:
                        dir_label = "多" if d["lor_direction"] == "bullish" else "空" if d["lor_direction"] == "bearish" else "?"
                        events_parts.append(f"翻转x{d['lor_count']}({dir_label})")
                    micro += f" | {' '.join(events_parts)}"
                micro += f" | 综合:{d['direction_score']:+d}({d['direction']})"
                line += "\n" + micro
            lines.append(line)
        lines.append("")

    if other_watching:
        lines.append("## 自选标的")
        for ps in other_watching:
            if ps["signalCount"] > 0:
                sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else ""
                lines.append(f"- {ps['code']} {ps['name']} | {ps['change']:+.2f}% | 信号:{ps['signalCount']} | {sigs}")
        lines.append("")

    # ── Fix 4: Trade plans / conditional orders ──
    if trade_plans:
        plans = trade_plans.get("plans", {})
        active_plans = {k: v for k, v in plans.items() if v.get("status") == "active"}
        if active_plans:
            # Build code→price lookup from per_stock
            price_map = {ps["code"]: ps["price"] for ps in per_stock if ps.get("price")}
            plan_lines = []
            for pid, plan in active_plans.items():
                symbol = plan.get("symbol", "")
                pname = plan.get("name", pid)
                cur_price = price_map.get(symbol, 0)
                orders = plan.get("orders", [])
                untriggered = [o for o in orders if not o.get("triggered")]
                if not untriggered:
                    continue
                order_descs = []
                for o in untriggered:
                    side_cn = "买入" if o.get("side") == "buy" else "卖出"
                    op = o.get("op", ">=")
                    tgt = o.get("price", 0)
                    label = o.get("label", f"{side_cn}{op}{tgt}")
                    if cur_price > 0 and tgt > 0:
                        dist = (tgt - cur_price) / cur_price * 100
                        order_descs.append(f"{label}{op}{tgt}(距现价{dist:+.1f}%)")
                    else:
                        order_descs.append(f"{label}{op}{tgt}")
                plan_lines.append(f"- {symbol} {pname}: {', '.join(order_descs)}")
            if plan_lines:
                lines.append("## 条件单状态")
                lines.extend(plan_lines)
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


def _compute_l2_digest(date_str: str) -> list[dict]:
    """从 session_snapshots + signals 表聚合日线级 L2 微观结构指标。

    写入 daily_l2_digest 表并返回 digest 列表。
    """
    from src.sim_trading.db import get_connection

    conn = None
    digests = []
    try:
        conn = get_connection()

        # 1. 取每只股票当日最后一条 session_snapshot
        rows = conn.execute(
            "SELECT code, session_json FROM session_snapshots "
            "WHERE date = ? AND ts = ("
            "  SELECT MAX(ts) FROM session_snapshots ss "
            "  WHERE ss.date = session_snapshots.date AND ss.code = session_snapshots.code"
            ") ORDER BY code",
            (date_str,),
        ).fetchall()

        if not rows:
            logger.info(f"L2 digest: no session_snapshots for {date_str}")
            return []

        # 2. 从 signals 表聚合事件频次
        sig_counts = {}
        for strategy in ("volume_price_divergence", "large_order_reversal"):
            cur = conn.execute(
                "SELECT code, COUNT(*) as cnt FROM signals "
                "WHERE date = ? AND strategy = ? GROUP BY code",
                (date_str, strategy),
            ).fetchall()
            for r in cur:
                sig_counts.setdefault(r["code"], {})[strategy] = r["cnt"]

        # large_order_reversal 最后方向
        lor_dirs = {}
        lor_rows = conn.execute(
            "SELECT code, direction FROM signals "
            "WHERE date = ? AND strategy = 'large_order_reversal' "
            "ORDER BY ts DESC",
            (date_str,),
        ).fetchall()
        for r in lor_rows:
            if r["code"] not in lor_dirs:
                lor_dirs[r["code"]] = r["direction"]

        # 3. 逐股计算 digest
        insert_rows = []
        for row in rows:
            code = row["code"]
            try:
                session = json.loads(row["session_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            tick = session.get("tick", {})
            lo = session.get("large_order", {})
            cf = session.get("capital_flow", {})

            lo_buy_amt = lo.get("buy_amount", 0)
            lo_sell_amt = lo.get("sell_amount", 0)
            lo_total = lo_buy_amt + lo_sell_amt
            lo_net = lo_buy_amt - lo_sell_amt
            lo_net_ratio = round(lo_net / lo_total, 3) if lo_total > 0 else 0

            tick_imb = tick.get("imbalance", 0)
            cf_inflow = cf.get("main_net_inflow", 0)
            cf_pct = cf.get("main_net_inflow_pct", 0)

            code_sigs = sig_counts.get(code, {})
            vpd_count = code_sigs.get("volume_price_divergence", 0)
            lor_count = code_sigs.get("large_order_reversal", 0)
            lor_dir = lor_dirs.get(code)

            # 加权方向评分: 大单*3 + tick*2 + 资金流*1 + 背离*-2 + 翻转*-2
            score = 0
            score += (1 if lo_net_ratio > 0.1 else -1 if lo_net_ratio < -0.1 else 0) * 3
            score += (1 if tick_imb > 0.1 else -1 if tick_imb < -0.1 else 0) * 2
            score += (1 if cf_pct > 5 else -1 if cf_pct < -5 else 0) * 1
            score += (-1 if vpd_count >= 3 else 0) * 2
            score += (-1 if lor_dir == "bearish" else 1 if lor_dir == "bullish" else 0) * 2

            direction = "bullish" if score > 2 else "bearish" if score < -2 else "neutral"

            digest = {
                "code": code,
                "lo_buy_count": lo.get("buy_count", 0),
                "lo_sell_count": lo.get("sell_count", 0),
                "lo_net_amount": round(lo_net, 0),
                "lo_net_ratio": lo_net_ratio,
                "tick_imbalance": round(tick_imb, 3),
                "tick_buy_vol": tick.get("buy_vol", 0),
                "tick_sell_vol": tick.get("sell_vol", 0),
                "cf_net_inflow": round(cf_inflow, 0),
                "cf_net_inflow_pct": round(cf_pct, 1),
                "vpd_count": vpd_count,
                "lor_count": lor_count,
                "lor_direction": lor_dir,
                "direction_score": score,
                "direction": direction,
            }
            digests.append(digest)

            insert_rows.append((
                date_str, code,
                digest["lo_buy_count"], digest["lo_sell_count"],
                digest["lo_net_amount"], digest["lo_net_ratio"],
                digest["tick_imbalance"], digest["tick_buy_vol"], digest["tick_sell_vol"],
                digest["cf_net_inflow"], digest["cf_net_inflow_pct"],
                vpd_count, lor_count, lor_dir,
                score, direction,
                row["session_json"],
            ))

        # 4. 批量写入
        if insert_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_l2_digest "
                "(date, code, lo_buy_count, lo_sell_count, lo_net_amount, lo_net_ratio, "
                "tick_imbalance, tick_buy_vol, tick_sell_vol, "
                "cf_net_inflow, cf_net_inflow_pct, "
                "vpd_count, lor_count, lor_direction, "
                "direction_score, direction, session_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                insert_rows,
            )
            conn.commit()
            logger.info(f"L2 digest: {len(insert_rows)} stocks written for {date_str}")

    except Exception as e:
        logger.warning(f"L2 digest computation failed: {e}")
    finally:
        if conn:
            conn.close()

    return digests


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
    l2_signals_data = _read_json(L2_SIGNALS_PATH) or {"signals": []}

    signals = l2_signals_data.get("signals", [])

    # 从 SQLite 读取当日 alert events
    events: list[dict] = []
    conn = None
    try:
        from src.sim_trading.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT ts, time, symbol, kind, level, message, display, change_pct "
            "FROM alert_events WHERE date = ? ORDER BY ts",
            (today,),
        ).fetchall()
        events = [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"读取 alert_events 失败: {e}")
    finally:
        if conn:
            conn.close()
    watchlist = config.get("watchlist", {})

    if not signals and not events:
        logger.warning("No signals or events found — skipping summary generation")
        return None

    # ── Read trade plans ──
    trade_plans = _read_json(TRADE_PLANS_PATH)

    # ── Compute L2 daily digest early (needed for direction in per_stock) ──
    l2_digests_early = _compute_l2_digest(today)
    l2_digest_map_early = {d["code"]: d for d in l2_digests_early}

    # ── Aggregate ──
    signal_agg = _aggregate_signals(signals)
    alert_agg = _aggregate_alerts(events)
    per_stock = _build_per_stock(market_data, config, signal_agg, alert_agg, l2_digest_map_early)
    stats = _build_stats(signals, events, per_stock)

    # Collect L1 display texts for LLM context
    l1_displays = []
    for s in signals:
        if s.get("notify") and s.get("display"):
            l1_displays.append(s["display"])

    market = _detect_market(signals, watchlist)

    # Use l2_digest_map computed earlier (already written to DB)
    l2_digest_map = l2_digest_map_early

    # ── Call LLM ──
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    messages = _build_llm_prompt(stats, per_stock, l1_displays, l2_digest_map, trade_plans)
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
