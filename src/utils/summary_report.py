import json
from typing import Any, Dict


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


def _format_confidence(value: Any) -> str:
    if isinstance(value, (int, float)):
        if 0 <= value <= 1:
            return f"{value * 100:.0f}%"
        return str(value)
    return str(value) if value is not None else "N/A"


def _fmt_duration(seconds: Any) -> str:
    try:
        seconds = float(seconds)
    except Exception:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.1f}s"


def print_summary_report(state: Dict[str, Any]) -> None:
    data = state.get("data", {})
    timings = data.get("agent_timings", {}) or state.get("metadata", {}).get("agent_timings", {}) or {}
    ticker = data.get("ticker", "未知")

    technical = _find_message(state, "technical_analyst_agent")
    fundamentals = _find_message(state, "fundamentals_agent")
    sentiment = _find_message(state, "sentiment_agent")
    valuation = _find_message(state, "valuation_agent")
    debate = _find_message(state, "debate_room_agent")
    risk = _find_message(state, "risk_management_agent")
    macro = _find_message(state, "macro_analyst_agent")
    portfolio = _find_message(state, "portfolio_management_agent")

    lines = []
    lines.append("=" * 88)
    lines.append(f"多视角投资分析摘要 · {ticker}")
    lines.append("=" * 88)

    if portfolio:
        lines.append(f"最终建议: {portfolio.get('action', 'N/A')} | 数量: {portfolio.get('quantity', 'N/A')} | 置信度: {_format_confidence(portfolio.get('confidence'))}")
    else:
        lines.append("最终建议: N/A")

    def add_section(title: str, payload: Any, signal_key: str = "signal", confidence_key: str = "confidence", extra: str | None = None):
        lines.append("")
        lines.append(f"[{title}]")
        if isinstance(payload, dict):
            signal = payload.get(signal_key, "N/A")
            conf = _format_confidence(payload.get(confidence_key))
            lines.append(f"- 信号: {signal}")
            lines.append(f"- 置信度: {conf}")
            if extra:
                value = payload.get(extra)
                if value:
                    lines.append(f"- 要点: {value}")
        elif payload is not None:
            lines.append(f"- {payload}")
        else:
            lines.append("- N/A")

    add_section("技术面", technical)
    add_section("基本面", fundamentals)
    add_section("情绪面", sentiment, extra="reasoning")
    add_section("估值", valuation)
    add_section("多空辩论", debate, extra="reasoning")

    lines.append("")
    lines.append("[风险管理]")
    if isinstance(risk, dict):
        lines.append(f"- 风险分数: {risk.get('risk_score', 'N/A')}")
        lines.append(f"- 建议动作: {risk.get('trading_action', 'N/A')}")
        lines.append(f"- 最大仓位: {risk.get('max_position_size', 'N/A')}")
        lines.append(f"- 理由: {risk.get('reasoning', 'N/A')}")
    else:
        lines.append("- N/A")

    lines.append("")
    lines.append("[宏观视角]")
    if isinstance(macro, dict):
        lines.append(f"- 宏观环境: {macro.get('macro_environment', 'N/A')}")
        lines.append(f"- 对标的影响: {macro.get('impact_on_stock', 'N/A')}")
        lines.append(f"- 理由: {macro.get('reasoning', 'N/A')}")
    else:
        lines.append("- N/A")

    if isinstance(portfolio, dict) and portfolio.get("reasoning"):
        lines.append("")
        lines.append("[最终决策理由]")
        lines.append(f"- {portfolio.get('reasoning')}")

    if timings:
        lines.append("")
        lines.append("[阶段耗时]")
        for agent_name, info in sorted(timings.items(), key=lambda kv: kv[1].get("started_at", "")):
            lines.append(f"- {agent_name}: {_fmt_duration(info.get('duration_seconds'))} ({info.get('status', 'completed')})")

    lines.append("=" * 88)
    print("\n" + "\n".join(lines))
