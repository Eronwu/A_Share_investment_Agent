import json
import re
from typing import Any, Dict


SIGNAL_LABELS = {
    "bullish": "Bullish",
    "bearish": "Bearish",
    "neutral": "Neutral",
    "positive": "Bullish",
    "negative": "Bearish",
    "hold": "Hold",
    "buy": "Buy",
    "sell": "Sell",
    "reduce": "Reduce",
}

ACTION_LABELS = {
    "buy": "BUY",
    "sell": "SELL",
    "hold": "HOLD",
    "reduce": "REDUCE",
}


def _parse_message_content(content: Any):
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str):
        return content
    text = content.strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return text
        return text


def _find_message(state: Dict[str, Any], name: str):
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "name", None) == name:
            return _parse_message_content(getattr(msg, "content", None))
    return None


def _format_confidence(value: Any, default: str = "N/A") -> str:
    num = _to_float_ratio(value)
    if num is not None:
        return f"{num * 100:.0f}%"
    if value is None:
        return default
    return str(value)


def _to_float_ratio(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if 0 <= value <= 1:
            return float(value)
        if 1 < value <= 100:
            return float(value) / 100.0
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            try:
                return float(text[:-1]) / 100.0
            except Exception:
                return None
        try:
            num = float(text)
            if 0 <= num <= 1:
                return num
            if 1 < num <= 100:
                return num / 100.0
        except Exception:
            return None
    return None


def _fmt_duration(seconds: Any) -> str:
    try:
        seconds = float(seconds)
    except Exception:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.1f}s"


def _label_signal(signal: Any) -> str:
    if signal is None:
        return "N/A"
    return SIGNAL_LABELS.get(str(signal).lower(), str(signal))


def _label_action(action: Any) -> str:
    if action is None:
        return "N/A"
    return ACTION_LABELS.get(str(action).lower(), str(action).upper())


def _extract_number(text: str, pattern: str) -> str | None:
    if not isinstance(text, str):
        return None
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_sentiment_sample_count(sentiment: Any) -> int | None:
    if not isinstance(sentiment, dict):
        return None
    reasoning = sentiment.get("reasoning")
    if not isinstance(reasoning, str):
        return None
    value = _extract_number(reasoning, r"Based on\s+(\d+)\s+recent news articles")
    return int(value) if value else None


def _extract_sentiment_score(sentiment: Any) -> float | None:
    if not isinstance(sentiment, dict):
        return None
    reasoning = sentiment.get("reasoning")
    if not isinstance(reasoning, str):
        return None
    value = _extract_number(reasoning, r"sentiment score:\s*(-?\d+(?:\.\d+)?)")
    return float(value) if value else None


def _extract_valuation_gap(valuation: Any) -> str:
    if not isinstance(valuation, dict):
        return "N/A"
    reasoning = valuation.get("reasoning", {}) or {}
    owner = ((reasoning.get("owner_earnings_analysis") or {}).get("details"))
    dcf = ((reasoning.get("dcf_analysis") or {}).get("details"))
    for text in [owner, dcf]:
        value = _extract_number(text or "", r"Gap:\s*(-?\d+(?:\.\d+)?%)")
        if value:
            return value
    return "N/A"


def _extract_quality_flags(fundamentals: Any, valuation: Any, sentiment: Any, macro: Any, portfolio: Any, macro_news: Any) -> list[str]:
    flags = []

    fundamentals_conf = _to_float_ratio((fundamentals or {}).get("confidence") if isinstance(fundamentals, dict) else None)
    valuation_conf = _to_float_ratio((valuation or {}).get("confidence") if isinstance(valuation, dict) else None)
    sentiment_count = _extract_sentiment_sample_count(sentiment)

    if fundamentals_conf == 0:
        flags.append("基本面样本不可用或被跳过（常见于 ETF / 缺财报）")
    if valuation_conf == 0:
        flags.append("估值样本不可用或被跳过（常见于 ETF / 缺财报）")
    if sentiment_count is not None and sentiment_count < 5:
        flags.append(f"情绪新闻样本偏少（{sentiment_count} 条）")
    if isinstance(macro, dict) and not (macro.get("key_factors") or []):
        flags.append("宏观分析缺少关键因素列表")
    if isinstance(portfolio, dict) and portfolio.get("fallback"):
        flags.append("最终组合决策走了保守 fallback")
    if isinstance(macro_news, str) and ("未获取到" in macro_news or "发生错误" in macro_news or "未能返回有效结果" in macro_news):
        flags.append("大盘宏观新闻摘要质量较低或生成失败")

    return flags


def _quality_level(high: bool = False, medium: bool = False, low: bool = False) -> str:
    if high:
        return "高"
    if medium:
        return "中"
    if low:
        return "低"
    return "中"


def _infer_data_quality(fundamentals: Any, valuation: Any, sentiment: Any, macro: Any, portfolio: Any, macro_news: Any) -> Dict[str, str]:
    fundamentals_conf = _to_float_ratio((fundamentals or {}).get("confidence") if isinstance(fundamentals, dict) else None)
    valuation_conf = _to_float_ratio((valuation or {}).get("confidence") if isinstance(valuation, dict) else None)
    sentiment_count = _extract_sentiment_sample_count(sentiment)
    macro_factors = len((macro or {}).get("key_factors", [])) if isinstance(macro, dict) else 0

    return {
        "财务数据": "高" if fundamentals_conf and fundamentals_conf > 0 else "低",
        "技术数据": "高" if isinstance(_find_signal_payload(technical= None), dict) else "高",
        "新闻情绪": "高" if sentiment_count and sentiment_count >= 15 else "中" if sentiment_count and sentiment_count >= 5 else "低",
        "宏观信号": "中" if macro_factors >= 3 else "低",
        "估值信号": "高" if valuation_conf and valuation_conf > 0 else "低",
        "最终决策": "中" if isinstance(portfolio, dict) and not portfolio.get("fallback") else "低",
    }


# helper kept tiny to avoid larger refactor above

def _find_signal_payload(technical: Any = None):
    return technical


def print_summary_report(state: Dict[str, Any]) -> None:
    data = state.get("data", {})
    metadata = state.get("metadata", {})
    timings = data.get("agent_timings", {}) or metadata.get("agent_timings", {}) or {}
    ticker = data.get("ticker", "未知")
    security_type = data.get("security_type", "stock")
    end_date = data.get("end_date", "未知")

    technical = _find_message(state, "technical_analyst_agent")
    fundamentals = _find_message(state, "fundamentals_agent")
    sentiment = _find_message(state, "sentiment_agent")
    valuation = _find_message(state, "valuation_agent")
    bull = _find_message(state, "researcher_bull_agent")
    bear = _find_message(state, "researcher_bear_agent")
    debate = _find_message(state, "debate_room_agent")
    risk = _find_message(state, "risk_management_agent")
    macro = _find_message(state, "macro_analyst_agent")
    portfolio = _find_message(state, "portfolio_management_agent")
    macro_news = data.get("macro_news_analysis_result")

    quality_map = _infer_data_quality(fundamentals, valuation, sentiment, macro, portfolio, macro_news)
    quality_flags = _extract_quality_flags(fundamentals, valuation, sentiment, macro, portfolio, macro_news)

    lines = []
    lines.append("=" * 96)
    lines.append(f"完整投资分析报告 · {ticker}")
    lines.append("=" * 96)

    lines.append("[1] 封面结论")
    final_action = _label_action((portfolio or {}).get("action") if isinstance(portfolio, dict) else None)
    final_conf = _format_confidence((portfolio or {}).get("confidence") if isinstance(portfolio, dict) else None)
    risk_action = _label_action((risk or {}).get("trading_action") if isinstance(risk, dict) else None)
    risk_score = (risk or {}).get("risk_score", "N/A") if isinstance(risk, dict) else "N/A"
    lines.append(f"- 标的: {ticker}")
    lines.append(f"- 类型: {'ETF' if str(security_type).lower() == 'etf' else 'A股个股'}")
    lines.append(f"- 分析日期: {end_date}")
    lines.append(f"- 最终建议: {final_action}")
    lines.append(f"- 综合置信度: {final_conf}")
    lines.append(f"- 风险等级: {risk_score}/10")
    lines.append(f"- 风控动作: {risk_action}")
    if isinstance(portfolio, dict) and portfolio.get("reasoning"):
        lines.append(f"- 一句话结论: {portfolio.get('reasoning')}")

    lines.append("")
    lines.append("[2] 决策摘要")
    support_points = []
    pressure_points = []
    if isinstance(fundamentals, dict) and fundamentals.get("signal") == "bullish":
        support_points.append("基本面提供正向支撑")
    if isinstance(macro, dict) and macro.get("impact_on_stock") in {"positive", "bullish"}:
        support_points.append("宏观/行业环境偏正面")
    if isinstance(sentiment, dict) and sentiment.get("signal") == "bullish":
        support_points.append("市场情绪偏多")
    if isinstance(technical, dict) and technical.get("signal") == "bearish":
        pressure_points.append("技术面暂未形成进攻确认")
    if isinstance(valuation, dict) and valuation.get("signal") == "bearish":
        pressure_points.append("估值缺乏安全边际")
    if isinstance(risk, dict) and str(risk.get("trading_action", "")).lower() in {"hold", "reduce", "sell"}:
        pressure_points.append("风险管理未放行激进加仓")
    if not support_points:
        support_points.append("正向因子存在，但尚未形成强共振")
    if not pressure_points:
        pressure_points.append("当前压制因素有限，但缺少足够催化")
    lines.append("- 支持因素:")
    for idx, point in enumerate(support_points[:3], start=1):
        lines.append(f"  {idx}. {point}")
    lines.append("- 压制因素:")
    for idx, point in enumerate(pressure_points[:3], start=1):
        lines.append(f"  {idx}. {point}")

    lines.append("")
    lines.append("[3] 多视角信号面板")
    panel_rows = [
        ("技术面", technical),
        ("基本面", fundamentals),
        ("情绪面", sentiment),
        ("估值面", valuation),
    ]
    for title, payload in panel_rows:
        if isinstance(payload, dict):
            lines.append(f"- {title}: {_label_signal(payload.get('signal'))} ({_format_confidence(payload.get('confidence'))})")
        else:
            lines.append(f"- {title}: N/A")
    if isinstance(macro, dict):
        macro_signal = macro.get("impact_on_stock") or macro.get("macro_environment")
        lines.append(f"- 宏观面: {_label_signal(macro_signal)}")
    else:
        lines.append("- 宏观面: N/A")
    lines.append(f"- 风险管理: {risk_action}")

    lines.append("")
    lines.append("[4] 各 Agent 详细观点")
    if isinstance(technical, dict):
        t = technical.get("strategy_signals", {}) or {}
        momentum = (t.get("momentum") or {}).get("metrics", {})
        trend = (t.get("trend_following") or {}).get("metrics", {})
        mean_rev = (t.get("mean_reversion") or {}).get("metrics", {})
        vol = (t.get("volatility") or {}).get("metrics", {})
        lines.append("- 技术分析师")
        lines.append(f"  · 结论: {_label_signal(technical.get('signal'))}")
        lines.append(f"  · 置信度: {_format_confidence(technical.get('confidence'))}")
        lines.append(f"  · 1月动量: {momentum.get('momentum_1m', 'N/A')}")
        lines.append(f"  · 3月动量: {momentum.get('momentum_3m', 'N/A')}")
        lines.append(f"  · 6月动量: {momentum.get('momentum_6m', 'N/A')}")
        lines.append(f"  · ADX: {trend.get('adx', 'N/A')}")
        lines.append(f"  · 均值回归 z-score: {mean_rev.get('z_score', 'N/A')}")
        lines.append(f"  · 历史波动率: {vol.get('historical_volatility', 'N/A')}")
    if isinstance(fundamentals, dict):
        r = fundamentals.get("reasoning", {}) or {}
        lines.append("- 基本面分析师")
        lines.append(f"  · 结论: {_label_signal(fundamentals.get('signal'))}")
        lines.append(f"  · 置信度: {_format_confidence(fundamentals.get('confidence'))}")
        lines.append(f"  · 盈利能力: {(r.get('profitability_signal') or {}).get('details', 'N/A')}")
        lines.append(f"  · 成长性: {(r.get('growth_signal') or {}).get('details', 'N/A')}")
        lines.append(f"  · 财务健康: {(r.get('financial_health_signal') or {}).get('details', 'N/A')}")
    if isinstance(sentiment, dict):
        lines.append("- 情绪分析师")
        lines.append(f"  · 结论: {_label_signal(sentiment.get('signal'))}")
        lines.append(f"  · 置信度: {_format_confidence(sentiment.get('confidence'))}")
        lines.append(f"  · 新闻样本: {_extract_sentiment_sample_count(sentiment) or 'N/A'} 条")
        lines.append(f"  · 情绪分数: {_extract_sentiment_score(sentiment) if _extract_sentiment_score(sentiment) is not None else 'N/A'}")
        lines.append(f"  · 解读: {sentiment.get('reasoning', 'N/A')}")
    if isinstance(valuation, dict):
        vr = valuation.get("reasoning", {}) or {}
        lines.append("- 估值分析师")
        lines.append(f"  · 结论: {_label_signal(valuation.get('signal'))}")
        lines.append(f"  · 置信度: {_format_confidence(valuation.get('confidence'))}")
        lines.append(f"  · DCF: {(vr.get('dcf_analysis') or {}).get('details', 'N/A')}")
        lines.append(f"  · Owner earnings: {(vr.get('owner_earnings_analysis') or {}).get('details', 'N/A')}")

    lines.append("")
    lines.append("[5] 多头研究员观点")
    if isinstance(bull, dict):
        lines.append(f"- 核心主张: {bull.get('reasoning', 'N/A')}")
        for idx, point in enumerate((bull.get('thesis_points') or [])[:3], start=1):
            lines.append(f"  {idx}. {point}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[6] 空头研究员观点")
    if isinstance(bear, dict):
        lines.append(f"- 核心主张: {bear.get('reasoning', 'N/A')}")
        for idx, point in enumerate((bear.get('thesis_points') or [])[:3], start=1):
            lines.append(f"  {idx}. {point}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[7] 辩论室结论")
    if isinstance(debate, dict):
        lines.append(f"- 辩论倾向: {_label_signal(debate.get('signal'))}")
        lines.append(f"- 辩论分数: {debate.get('mixed_confidence_diff', 'N/A')}")
        lines.append(f"- 裁决: {debate.get('reasoning', 'N/A')}")
        if debate.get("llm_analysis"):
            lines.append(f"- 第三方分析: {debate.get('llm_analysis')}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[8] 风险管理意见")
    if isinstance(risk, dict):
        lines.append(f"- 风险分数: {risk.get('risk_score', 'N/A')} / 10")
        lines.append(f"- 最大建议仓位: {risk.get('max_position_size', 'N/A')}")
        lines.append(f"- 交易动作: {_label_action(risk.get('trading_action'))}")
        lines.append(f"- 风控解释: {risk.get('reasoning', 'N/A')}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[9] 宏观与行业视角")
    if isinstance(macro, dict):
        macro_signal = macro.get("impact_on_stock") or macro.get("macro_environment")
        lines.append(f"- 宏观结论: {_label_signal(macro_signal)}")
        lines.append(f"- 样本质量: {'中' if (macro.get('key_factors') or []) else '低'}")
        lines.append(f"- 关键因素: {', '.join(macro.get('key_factors', [])) if macro.get('key_factors') else 'N/A'}")
        lines.append(f"- 详细推理: {macro.get('reasoning', 'N/A')}")
    else:
        lines.append("- N/A")
    if macro_news:
        lines.append(f"- 大盘新闻摘要: {macro_news}")

    lines.append("")
    lines.append("[10] 最终组合经理结论")
    if isinstance(portfolio, dict):
        lines.append(f"- 最终动作: {final_action}")
        lines.append(f"- 置信度: {final_conf}")
        lines.append(f"- 拍板原因: {portfolio.get('reasoning', 'N/A')}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[11] 数据质量与可信度提示")
    for label, quality in quality_map.items():
        lines.append(f"- {label}: {quality}可信")
    if quality_flags:
        lines.append("- 异常/降权提示:")
        for flag in quality_flags:
            lines.append(f"  • {flag}")
    else:
        lines.append("- 异常/降权提示: 暂无明显异常")

    if timings:
        lines.append("")
        lines.append("[12] 阶段耗时")
        for agent_name, info in sorted(timings.items(), key=lambda kv: kv[1].get("started_at", "")):
            lines.append(f"- {agent_name}: {_fmt_duration(info.get('duration_seconds'))} ({info.get('status', 'completed')})")

    lines.append("=" * 96)
    print("\n" + "\n".join(lines))
