from __future__ import annotations

import re
from typing import Any


ACTION_ALIASES = {
    "buy": "buy",
    "BUY": "buy",
    "买入": "buy",
    "增持": "buy",
    "hold": "hold",
    "HOLD": "hold",
    "持有": "hold",
    "观望": "hold",
    "reduce": "reduce",
    "REDUCE": "reduce",
    "减仓": "reduce",
    "sell": "sell",
    "SELL": "sell",
    "卖出": "sell",
}

ACTION_LABELS = {
    "buy": "BUY",
    "hold": "HOLD",
    "reduce": "REDUCE",
    "sell": "SELL",
}

MIXED_PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("The Risk Management signal", "风险管理信号"),
    ("The risk management signal", "风险管理信号"),
    ("Risk Management signal", "风险管理信号"),
    ("risk management signal", "风险管理信号"),
    ("风险管理 signal", "风险管理信号"),
    ("The 风险管理 signal", "风险管理信号"),
    ("risk management", "风险管理"),
    ("Risk Management", "风险管理"),
    ("technical analysis", "技术面分析"),
    ("Technical analysis", "技术面分析"),
    ("技术面 analysis", "技术面分析"),
    ("fundamental analysis", "基本面分析"),
    ("Fundamental analysis", "基本面分析"),
    ("fundamentals analysis", "基本面分析"),
    ("基本面 analysis", "基本面分析"),
    ("valuation analysis", "估值分析"),
    ("Valuation analysis", "估值分析"),
    ("估值 analysis", "估值分析"),
    ("sentiment analysis", "情绪分析"),
    ("Sentiment analysis", "情绪分析"),
    ("情绪 analysis", "情绪分析"),
    ("macro analysis", "宏观分析"),
    ("Macro Analysis", "宏观分析"),
    ("Macro analysis", "宏观分析"),
    ("宏观 analysis", "宏观分析"),
    ("macro environment", "宏观环境"),
    ("Macro environment", "宏观环境"),
    ("宏观 environment", "宏观环境"),
    ("market sentiment", "市场情绪"),
    ("Market sentiment", "市场情绪"),
    ("market conditions", "市场环境"),
    ("Market conditions", "市场环境"),
    ("market volatility", "市场波动"),
    ("Market volatility", "市场波动"),
    ("market-wide news summary", "市场面摘要"),
    ("Market-wide news summary", "市场面摘要"),
    ("daily market-wide news summary", "市场面摘要"),
    ("Daily Market-Wide News Summary", "市场面摘要"),
    ("daily market summary", "市场摘要"),
    ("Daily market summary", "市场摘要"),
    ("confidence level", "置信度"),
    ("Confidence level", "置信度"),
    ("confidence", "置信度"),
    ("bullish", "看多"),
    ("Bullish", "看多"),
    ("bearish", "看空"),
    ("Bearish", "看空"),
    ("neutral", "中性"),
    ("Neutral", "中性"),
)

ACTION_PATTERN_BUCKETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "buy": (
        (
            r"\bfinal decision is buy\b",
            r"\bfinal action is buy\b",
            r"\boptimal action is to buy\b",
            r"\bdecision is to buy\b",
            r"\bbuy remains the best option\b",
            r"\bbuying action is justified\b",
            r"\bmake a buy decision\b",
            r"最终(?:动作|建议|决策).{0,6}(?:买入|BUY)",
            r"(?:买入|BUY).{0,8}最佳选择",
        ),
        (
            r"\brisk management recommends a buy action\b",
            r"\bprudent to allocate\b",
            r"\b买入 recommendation\b",
            r"\b建议买入\b",
            r"\b看多 风险管理 action\b",
        ),
    ),
    "hold": (
        (
            r"\bfinal decision is hold\b",
            r"\bfinal action is hold\b",
            r"\boptimal action is to hold\b",
            r"\bdecision is to hold\b",
            r"\bdecision to maintain the current position\b",
            r"\bmaintain the current position\b",
            r"\bholding current cash\b",
            r"\bthere is no action needed\b",
            r"\bdictating a ['\"]?hold['\"]? action\b",
            r"最终(?:动作|建议|决策).{0,6}(?:持有|HOLD)",
            r"(?:持有|HOLD).{0,8}(?:最佳选择|最优动作)",
        ),
        (
            r"\brisk management signal is ['\"]?hold['\"]? and it outweighs all other signals\b",
            r"\brisk management signal to hold outweighs\b",
            r"\brisk management constraint requires holding\b",
            r"\btrading action .* must be ['\"]?hold['\"]?\b",
            r"\bshould be adhered to\b",
            r"\bmaintain no position\b",
            r"\b继续持有\b",
        ),
    ),
    "reduce": (
        (
            r"\bfinal decision is reduce\b",
            r"\bfinal action is reduce\b",
            r"\boptimal action is to reduce\b",
            r"最终(?:动作|建议|决策).{0,6}(?:减仓|REDUCE)",
        ),
        (
            r"\breduce exposure\b",
            r"\btrim the position\b",
            r"\b建议减仓\b",
        ),
    ),
    "sell": (
        (
            r"\bfinal decision is sell\b",
            r"\bfinal action is sell\b",
            r"\boptimal action is to sell\b",
            r"\bdecision is to sell\b",
            r"最终(?:动作|建议|决策).{0,6}(?:卖出|SELL)",
        ),
        (
            r"\bmandates a sell action\b",
            r"\bexit the position\b",
            r"\b建议卖出\b",
        ),
    ),
}


def normalize_action_token(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return ACTION_ALIASES.get(text) or ACTION_ALIASES.get(text.lower())


def action_label(value: str | None) -> str:
    return ACTION_LABELS.get(value or "", "N/A")


def clean_mixed_language_text(text: Any) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        return str(text)

    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return None

    for src, dst in MIXED_PHRASE_REPLACEMENTS:
        cleaned = cleaned.replace(src, dst)

    action_phrase_map = {
        "buy": "买入",
        "hold": "持有",
        "reduce": "减仓",
        "sell": "卖出",
    }
    for token, label in action_phrase_map.items():
        cleaned = re.sub(rf"\b{token}ing\b", label, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"{label}ing\b", label, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\bto {token}\b", f"转为{label}", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{token} action\b", f"{label}动作", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{token} decision\b", f"{label}决策", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{token} recommendation\b", f"{label}建议", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\bhold position\b", "持有仓位", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bcurrent position\b", "当前仓位", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bcash-only\b", "空仓", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bno positions\b", "无持仓", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bshares\b", "股", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bposition size\b", "仓位", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmandates 持有(?: the position)?\b", "要求继续持有", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bportfolio is currently 持有 无持仓\b", "当前组合为空仓", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bportfolio is currently 持有 no positions\b", "当前组合为空仓", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bholding no positions\b", "保持空仓", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bis unnecessary\b", "并无必要", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"(风险管理信号){2,}", "风险管理信号", cleaned)
    return cleaned.strip()


def infer_action_from_text(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None

    normalized = " ".join(text.strip().split())
    scores = {key: 0 for key in ACTION_PATTERN_BUCKETS}

    for action, (strong_patterns, medium_patterns) in ACTION_PATTERN_BUCKETS.items():
        for pattern in strong_patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                scores[action] += 5
        for pattern in medium_patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                scores[action] += 3

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_action, top_score = ranked[0]
    if top_score <= 0:
        return None
    if len(ranked) > 1 and top_score == ranked[1][1]:
        return None
    return top_action


def resolve_action_consistency(final_action: Any, risk_action: Any, *texts: Any) -> str | None:
    normalized_final = normalize_action_token(final_action)
    normalized_risk = normalize_action_token(risk_action)

    for text in texts:
        inferred = infer_action_from_text(text)
        if inferred:
            return inferred

    return normalized_final or normalized_risk


def rewrite_summary_action_prefix(summary: str | None, action: str | None) -> str | None:
    if not summary or not action:
        return summary
    target = action_label(action)
    return re.sub(r"^(BUY|HOLD|REDUCE|SELL)\b", target, summary, count=1)
