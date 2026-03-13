#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL = ROOT / "config" / "sector_pool.v1.json"
REPORT_ROOT = ROOT / "reports"
STOCK_CMD = ROOT / "stock"

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

SIGNAL_ALIASES = {
    "bullish": "bullish",
    "Bullish": "bullish",
    "positive": "bullish",
    "看多": "bullish",
    "偏多": "bullish",
    "bearish": "bearish",
    "Bearish": "bearish",
    "negative": "bearish",
    "看空": "bearish",
    "偏空": "bearish",
    "neutral": "neutral",
    "Neutral": "neutral",
    "中性": "neutral",
    "hold": "neutral",
    "HOLD": "neutral",
}

ACTION_LABELS = {
    "buy": "BUY",
    "hold": "HOLD",
    "reduce": "REDUCE",
    "sell": "SELL",
}

SIGNAL_LABELS = {
    "bullish": "Bullish",
    "bearish": "Bearish",
    "neutral": "Neutral",
}


@dataclass
class ScanResult:
    ticker: str
    sector: str
    stage: str
    success: bool
    returncode: int
    started_at: str
    finished_at: str
    raw_path: str
    summary_text_path: str | None = None
    summary_json_path: str | None = None
    used_structured_summary: bool = False
    composite_score: float | None = None
    confidence: float | None = None
    risk_score: float | None = None
    action: str | None = None
    signal: str | None = None
    summary: str | None = None
    hold_reason: str | None = None
    data_quality_flags: list[str] | None = None
    support_points: list[str] | None = None
    pressure_points: list[str] | None = None
    error: str | None = None
    skipped: bool = False
    reused_from: str | None = None
    attempts: int = 1


def load_pool(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sectors = data.get("sectors")
    if not isinstance(sectors, list):
        raise ValueError(f"Invalid pool file: {path}")
    return sectors


def slugify(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip().lower())
    return re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "", text)


def normalize_action(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return ACTION_ALIASES.get(text) or ACTION_ALIASES.get(text.lower())


def normalize_signal(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return SIGNAL_ALIASES.get(text) or SIGNAL_ALIASES.get(text.lower())


def action_label(value: str | None) -> str:
    return ACTION_LABELS.get(value or "", "N/A")


def signal_label(value: str | None) -> str:
    return SIGNAL_LABELS.get(value or "", "N/A")


def parse_ratio(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if 0 <= float(value) <= 1:
            return float(value)
        if 1 < float(value) <= 100:
            return float(value) / 100.0
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            return None
    try:
        number = float(text)
    except ValueError:
        return None
    if 0 <= number <= 1:
        return number
    if 1 < number <= 100:
        return number / 100.0
    return None


def parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def safe_read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def extract_text_field(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_final_result_payload(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    anchor = text.rfind("Final Result:")
    search_text = text[anchor:] if anchor >= 0 else text

    for match in re.finditer(r"\{", search_text):
        try:
            payload, _ = decoder.raw_decode(search_text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and any(key in payload for key in ("action", "agent_signals", "confidence")):
            candidates.append(payload)

    return candidates[-1] if candidates else None


def aggregate_agent_signal(agent_signals: list[dict[str, Any]]) -> str | None:
    total = 0.0
    count = 0
    for item in agent_signals:
        if not isinstance(item, dict):
            continue
        signal = normalize_signal(item.get("signal"))
        if signal == "bullish":
            total += 1.0
            count += 1
        elif signal == "bearish":
            total -= 1.0
            count += 1
        elif signal == "neutral":
            count += 1
    if count == 0:
        return None
    average = total / count
    if average > 0.2:
        return "bullish"
    if average < -0.2:
        return "bearish"
    return "neutral"


def derive_composite_score(
    action: str | None,
    signal: str | None,
    confidence: float | None,
    risk_score: float | None,
    agent_signals: list[dict[str, Any]] | None = None,
) -> float | None:
    if not any(value is not None for value in (action, signal, confidence, risk_score)) and not agent_signals:
        return None

    score = 50.0
    score += {"buy": 18.0, "hold": 4.0, "reduce": -8.0, "sell": -18.0}.get(action or "", 0.0)
    score += {"bullish": 10.0, "neutral": 0.0, "bearish": -10.0}.get(signal or "", 0.0)
    if confidence is not None:
        score += (confidence - 0.5) * 20.0
    if risk_score is not None:
        score += (5.0 - risk_score) * 2.0
    if agent_signals:
        aggregate = aggregate_agent_signal(agent_signals)
        score += {"bullish": 6.0, "neutral": 0.0, "bearish": -6.0}.get(aggregate or "", 0.0)

    return round(max(0.0, min(100.0, score)), 2)


def summarize_from_structured(payload: dict[str, Any]) -> str | None:
    final = payload.get("final", {}) if isinstance(payload.get("final"), dict) else {}
    support_points = payload.get("support_points") if isinstance(payload.get("support_points"), list) else []
    pressure_points = payload.get("pressure_points") if isinstance(payload.get("pressure_points"), list) else []
    final_action = action_label(normalize_action(final.get("action") or final.get("action_label")))
    confidence = parse_ratio(final.get("confidence") or final.get("confidence_label"))
    leading_parts = [final_action]
    if confidence is not None:
        leading_parts.append(f"{confidence * 100:.0f}%")

    snippets = []
    if support_points:
        snippets.append(f"支持: {support_points[0]}")
    if pressure_points:
        snippets.append(f"压制: {pressure_points[0]}")

    reasoning = final.get("reasoning_cn") or final.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        snippets.append(reasoning.strip())

    body = " | ".join(snippets[:3]).strip()
    header = " ".join([part for part in leading_parts if part]).strip()
    summary = f"{header} | {body}".strip(" |")
    return summary or None


def summarize_from_text(text: str, max_lines: int = 6) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    interesting = [
        line
        for line in lines
        if any(
            marker in line
            for marker in [
                "最终建议",
                "综合置信度",
                "风险等级",
                "风控动作",
                "一句话结论",
                "Final Result",
                '"action"',
                '"confidence"',
            ]
        )
    ]
    sample = interesting[:max_lines] if interesting else lines[-max_lines:]
    return " | ".join(sample) if sample else None


def extract_error_message(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if "ERROR" in line or "Traceback" in line or "Connection error" in line:
            return line
    return lines[-1] if lines else None


def extract_from_text_report(summary_text: str) -> dict[str, Any]:
    return {
        "action": normalize_action(
            extract_text_field(summary_text, [r"- 最终建议[:：]\s*([^\n]+)", r"- 风控动作[:：]\s*([^\n]+)"])
        ),
        "confidence": parse_ratio(
            extract_text_field(summary_text, [r"- 综合置信度[:：]\s*([^\n]+)", r"confidence[\"']?\s*[:：]\s*([0-9.]+%?)"])
        ),
        "risk_score": parse_float(extract_text_field(summary_text, [r"- 风险等级[:：]\s*([0-9.]+)", r"risk_score[\"']?\s*[:：]\s*([0-9.]+)"])),
        "signal": normalize_signal(
            extract_text_field(summary_text, [r"- 宏观面[:：]\s*([^\n(]+)", r"- 辩论倾向[:：]\s*([^\n]+)"])
        ),
    }


def parse_result_payload(raw_text: str, summary_text: str | None, summary_payload: dict[str, Any] | None) -> dict[str, Any]:
    final_result = extract_final_result_payload(raw_text) or {}
    text_fields = extract_from_text_report(summary_text or raw_text)

    action = None
    confidence = None
    risk_score = None
    signal = None
    composite_score = None
    summary = None
    hold_reason = None
    data_quality_flags: list[str] = []
    support_points: list[str] = []
    pressure_points: list[str] = []
    used_structured_summary = False
    agent_signals: list[dict[str, Any]] | None = None

    if summary_payload:
        used_structured_summary = True
        final = summary_payload.get("final", {}) if isinstance(summary_payload.get("final"), dict) else {}
        risk = summary_payload.get("risk", {}) if isinstance(summary_payload.get("risk"), dict) else {}
        signals = summary_payload.get("signals", {}) if isinstance(summary_payload.get("signals"), dict) else {}
        quality = summary_payload.get("quality", {}) if isinstance(summary_payload.get("quality"), dict) else {}

        action = normalize_action(final.get("action") or final.get("action_label"))
        confidence = parse_ratio(final.get("confidence") or final.get("confidence_label"))
        risk_score = parse_float(risk.get("score"))
        signal = normalize_signal(
            (signals.get("debate", {}) or {}).get("signal")
            or (signals.get("macro", {}) or {}).get("signal")
        )
        composite_score = parse_float(summary_payload.get("composite_score"))
        summary = summarize_from_structured(summary_payload)
        hold_reason = (final.get("reasoning_cn") or final.get("reasoning") or risk.get("reasoning"))
        support_points = [str(item).strip() for item in summary_payload.get("support_points", []) if str(item).strip()]
        pressure_points = [str(item).strip() for item in summary_payload.get("pressure_points", []) if str(item).strip()]
        data_quality_flags = [str(item).strip() for item in quality.get("flags", []) if str(item).strip()]

    if isinstance(final_result.get("agent_signals"), list):
        agent_signals = [item for item in final_result["agent_signals"] if isinstance(item, dict)]

    action = action or normalize_action(final_result.get("action")) or text_fields["action"]
    if confidence is None:
        final_confidence = parse_ratio(final_result.get("confidence"))
        confidence = final_confidence if final_confidence is not None else text_fields["confidence"]
    risk_score = risk_score if risk_score is not None else text_fields["risk_score"]
    signal = signal or aggregate_agent_signal(agent_signals or []) or text_fields["signal"]

    if not signal and action in {"buy", "hold", "reduce", "sell"}:
        signal = {
            "buy": "bullish",
            "hold": "neutral",
            "reduce": "bearish",
            "sell": "bearish",
        }.get(action)

    if composite_score is None:
        composite_score = derive_composite_score(action, signal, confidence, risk_score, agent_signals)

    if not summary:
        summary = summarize_from_text(summary_text or raw_text)

    return {
        "action": action,
        "confidence": confidence,
        "risk_score": risk_score,
        "signal": signal,
        "composite_score": composite_score,
        "summary": summary,
        "hold_reason": hold_reason,
        "data_quality_flags": data_quality_flags,
        "support_points": support_points,
        "pressure_points": pressure_points,
        "used_structured_summary": used_structured_summary,
    }


def build_stock_command(
    ticker: str,
    reasoning: bool,
    num_of_news: int,
    summary_json_path: Path,
    summary_text_path: Path,
    model: str | None,
    hq: bool,
) -> list[str]:
    cmd = [
        str(STOCK_CMD),
        ticker,
        "--summary",
        "--num-of-news",
        str(num_of_news),
        "--summary-json-out",
        str(summary_json_path),
        "--summary-text-out",
        str(summary_text_path),
    ]
    if reasoning:
        cmd.append("--show-reasoning")
    if hq:
        cmd.append("--hq")
    if model:
        cmd.extend(["--model", model])
    return cmd


def run_stock_once(
    ticker: str,
    out_path: Path,
    summary_text_path: Path,
    summary_json_path: Path,
    reasoning: bool,
    num_of_news: int,
    timeout: int,
    model: str | None,
    hq: bool,
) -> tuple[int, str, str | None]:
    cmd = build_stock_command(
        ticker=ticker,
        reasoning=reasoning,
        num_of_news=num_of_news,
        summary_json_path=summary_json_path,
        summary_text_path=summary_text_path,
        model=model,
        hq=hq,
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        combined = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        out_path.write_text((combined + "\n") if combined else "TIMEOUT\n", encoding="utf-8")
        return 124, combined, "timeout"

    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    out_path.write_text(combined + "\n", encoding="utf-8")
    return proc.returncode, combined, None


def result_has_cached_artifacts(raw_path: Path, summary_text_path: Path, summary_json_path: Path) -> bool:
    return raw_path.exists() and (summary_text_path.exists() or summary_json_path.exists())


def load_cached_stage_result(
    ticker: str,
    sector_name: str,
    stage: str,
    started: str,
    report_dir: Path,
    raw_path: Path,
    summary_text_path: Path,
    summary_json_path: Path,
    reused_from: str,
) -> ScanResult:
    raw_output = safe_read_text(raw_path) or ""
    return build_stage_result(
        ticker=ticker,
        sector_name=sector_name,
        stage=stage,
        started=started,
        report_dir=report_dir,
        raw_path=raw_path,
        summary_text_path=summary_text_path,
        summary_json_path=summary_json_path,
        returncode=0,
        raw_output=raw_output,
        err=None,
        skipped=True,
        reused_from=reused_from,
        attempts=0,
    )


def build_stage_result(
    ticker: str,
    sector_name: str,
    stage: str,
    started: str,
    report_dir: Path,
    raw_path: Path,
    summary_text_path: Path,
    summary_json_path: Path,
    returncode: int,
    raw_output: str,
    err: str | None,
    *,
    skipped: bool = False,
    reused_from: str | None = None,
    attempts: int = 1,
) -> ScanResult:
    summary_payload = read_json(summary_json_path)
    summary_text = safe_read_text(summary_text_path)
    parsed = parse_result_payload(raw_output, summary_text, summary_payload)
    effective_error = err
    if not effective_error and returncode != 0:
        effective_error = extract_error_message(raw_output) or f"returncode={returncode}"

    return ScanResult(
        ticker=ticker,
        sector=sector_name,
        stage=stage,
        success=returncode == 0,
        returncode=returncode,
        started_at=started,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        raw_path=str(raw_path.relative_to(report_dir)),
        summary_text_path=str(summary_text_path.relative_to(report_dir)) if summary_text_path.exists() else None,
        summary_json_path=str(summary_json_path.relative_to(report_dir)) if summary_json_path.exists() else None,
        used_structured_summary=bool(parsed["used_structured_summary"]),
        composite_score=parsed["composite_score"],
        confidence=parsed["confidence"],
        risk_score=parsed["risk_score"],
        action=parsed["action"],
        signal=parsed["signal"],
        summary=parsed["summary"],
        hold_reason=parsed["hold_reason"],
        data_quality_flags=parsed["data_quality_flags"],
        support_points=parsed["support_points"],
        pressure_points=parsed["pressure_points"],
        error=effective_error,
        skipped=skipped,
        reused_from=reused_from,
        attempts=attempts,
    )


def ranking_key(result: ScanResult) -> tuple[bool, float, float, str]:
    return (
        not result.success,
        -(result.composite_score if result.composite_score is not None else -1.0),
        -(result.confidence if result.confidence is not None else -1.0),
        result.ticker,
    )


def reasoning_ranking_key(result: ScanResult) -> tuple[bool, float, float, str]:
    return ranking_key(result)


def limit_universe(universe: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    if limit <= 0 or len(universe) <= limit:
        return universe
    sector_buckets: dict[str, list[str]] = {}
    sector_order: list[str] = []
    for sector, ticker in universe:
        if sector not in sector_buckets:
            sector_buckets[sector] = []
            sector_order.append(sector)
        sector_buckets[sector].append(ticker)

    limited: list[tuple[str, str]] = []
    cursor = 0
    while len(limited) < limit:
        progressed = False
        for sector in sector_order:
            bucket = sector_buckets[sector]
            if cursor < len(bucket):
                limited.append((sector, bucket[cursor]))
                progressed = True
                if len(limited) >= limit:
                    break
        if not progressed:
            break
        cursor += 1
    return limited


def select_reasoning_candidates(ranked: list[ScanResult], top_n: int) -> list[ScanResult]:
    if top_n <= 0:
        return []

    selected: list[ScanResult] = []
    seen_sectors: set[str] = set()

    for item in ranked:
        if item.sector in seen_sectors:
            continue
        selected.append(item)
        seen_sectors.add(item.sector)
        if len(selected) >= top_n:
            return selected

    for item in ranked:
        if item in selected:
            continue
        selected.append(item)
        if len(selected) >= top_n:
            break
    return selected


def build_result_index(results: list[ScanResult], stage: str) -> dict[str, ScanResult]:
    indexed: dict[str, ScanResult] = {}
    for item in results:
        if item.stage == stage:
            indexed[item.ticker] = item
    return indexed


def merge_final_candidates(summary_results: list[ScanResult], reasoning_results: list[ScanResult]) -> list[ScanResult]:
    summary_map = build_result_index(summary_results, "summary")
    reasoning_map = build_result_index(reasoning_results, "reasoning")

    merged: list[ScanResult] = []
    seen: set[str] = set()

    for ticker, item in reasoning_map.items():
        merged.append(item)
        seen.add(ticker)

    for item in summary_results:
        if item.ticker in seen:
            continue
        merged.append(summary_map.get(item.ticker, item))

    return sorted([r for r in merged if r.success], key=reasoning_ranking_key)


def compute_stage_stats(results: list[ScanResult], stage: str) -> dict[str, int]:
    scoped = [r for r in results if r.stage == stage]
    return {
        "count": len(scoped),
        "success": sum(1 for r in scoped if r.success),
        "failed": sum(1 for r in scoped if not r.success),
        "skipped": sum(1 for r in scoped if r.skipped),
    }


def compute_action_diff(before: ScanResult | None, after: ScanResult | None) -> str | None:
    if before is None or after is None:
        return None
    if before.action == after.action and before.signal == after.signal and before.composite_score == after.composite_score:
        return None
    before_label = f"{action_label(before.action)}/{signal_label(before.signal)}"
    after_label = f"{action_label(after.action)}/{signal_label(after.signal)}"
    before_score = "N/A" if before.composite_score is None else f"{before.composite_score:.2f}"
    after_score = "N/A" if after.composite_score is None else f"{after.composite_score:.2f}"
    return f"{before_label}({before_score}) → {after_label}({after_score})"


def load_previous_report(report_root: Path, current_report_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    if not report_root.exists():
        return None, None

    candidates = sorted(
        p for p in report_root.iterdir()
        if p.is_dir() and p.name != current_report_dir.name and (p / "report.json").exists()
    )
    if not candidates:
        return None, None

    prev_dir = candidates[-1]
    prev_payload = read_json(prev_dir / "report.json")
    return prev_payload, prev_dir


def extract_previous_final_map(previous_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not previous_payload:
        return {}
    results = previous_payload.get("results", [])
    latest_by_ticker: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip()
        if not ticker:
            continue
        current = latest_by_ticker.get(ticker)
        current_stage = str(current.get("stage")) if current else ""
        new_stage = str(item.get("stage"))
        if current is None or (current_stage != "reasoning" and new_stage == "reasoning"):
            latest_by_ticker[ticker] = item
    return latest_by_ticker


def build_daily_diff(final_ranked: list[ScanResult], previous_payload: dict[str, Any] | None) -> dict[str, Any]:
    previous_map = extract_previous_final_map(previous_payload)

    current_top = final_ranked[: min(10, len(final_ranked))]
    previous_top = set(previous_payload.get("selected_for_reasoning", [])) if previous_payload else set()
    current_top_tickers = [item.ticker for item in current_top]
    current_top_set = set(current_top_tickers)

    newly_entered = [ticker for ticker in current_top_tickers if ticker not in previous_top]
    dropped = sorted(ticker for ticker in previous_top if ticker not in current_top_set)

    changed: list[dict[str, Any]] = []
    for item in current_top:
        prev = previous_map.get(item.ticker)
        if not prev:
            continue
        prev_result = ScanResult(
            ticker=item.ticker,
            sector=str(prev.get("sector", item.sector)),
            stage=str(prev.get("stage", "summary")),
            success=bool(prev.get("success", True)),
            returncode=int(prev.get("returncode", 0)),
            started_at=str(prev.get("started_at", "")),
            finished_at=str(prev.get("finished_at", "")),
            raw_path=str(prev.get("raw_path", "")),
            summary_text_path=prev.get("summary_text_path"),
            summary_json_path=prev.get("summary_json_path"),
            used_structured_summary=bool(prev.get("used_structured_summary", False)),
            composite_score=parse_float(prev.get("composite_score")),
            confidence=parse_ratio(prev.get("confidence")),
            risk_score=parse_float(prev.get("risk_score")),
            action=normalize_action(prev.get("action")),
            signal=normalize_signal(prev.get("signal")),
            summary=prev.get("summary"),
            hold_reason=prev.get("hold_reason"),
            data_quality_flags=prev.get("data_quality_flags") or [],
            support_points=prev.get("support_points") or [],
            pressure_points=prev.get("pressure_points") or [],
            error=prev.get("error"),
            skipped=bool(prev.get("skipped", False)),
            reused_from=prev.get("reused_from"),
            attempts=int(prev.get("attempts", 1) or 1),
        )
        diff_text = compute_action_diff(prev_result, item)
        if not diff_text:
            continue
        changed.append({
            "ticker": item.ticker,
            "sector": item.sector,
            "change": diff_text,
        })

    return {
        "has_previous": previous_payload is not None,
        "newly_entered": newly_entered,
        "dropped": dropped,
        "changed": changed,
        "current_top": current_top_tickers,
        "previous_top": sorted(previous_top),
    }


def classify_candidate(item: ScanResult, summary_map: dict[str, ScanResult]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    flags = item.data_quality_flags or []
    confidence = item.confidence if item.confidence is not None else -1.0
    score = item.composite_score if item.composite_score is not None else -1.0
    source = item.stage
    summary_peer = summary_map.get(item.ticker)

    if not item.success:
        return "excluded", ["analysis_failed"]
    if item.error:
        reasons.append("has_error")
    if score < 0:
        reasons.append("missing_score")
    if confidence < 0:
        reasons.append("missing_confidence")
    if confidence == 0:
        reasons.append("zero_confidence")
    if any("fallback" in flag.lower() for flag in flags):
        reasons.append("fallback_detected")
    if any("缺少" in flag or "偏少" in flag for flag in flags):
        reasons.append("data_quality_warning")
    if item.action is None:
        reasons.append("missing_action")
    if source == "summary":
        reasons.append("not_reasoning_promoted")

    if summary_peer and item.stage == "reasoning":
        summary_score = summary_peer.composite_score if summary_peer.composite_score is not None else -1.0
        if item.action != summary_peer.action:
            reasons.append("summary_reasoning_action_changed")
        if summary_score >= 0 and abs(score - summary_score) >= 25:
            reasons.append("summary_reasoning_score_gap")

    if item.action == "buy" and score >= 70 and confidence >= 0.55 and "fallback_detected" not in reasons and "missing_action" not in reasons:
        if item.stage == "reasoning" or score >= 85:
            return "top_picks", reasons
        return "watchlist", reasons

    if item.action == "hold":
        if score >= 60 and confidence >= 0.5 and "zero_confidence" not in reasons:
            return "watchlist", reasons
        return "excluded", reasons

    if item.action in {"sell", "reduce"}:
        reasons.append("negative_action")
        return "excluded", reasons

    if score >= 60:
        return "watchlist", reasons
    return "excluded", reasons


def build_final_candidates(final_ranked: list[ScanResult], summary_results: list[ScanResult]) -> dict[str, Any]:
    summary_map = {item.ticker: item for item in summary_results}
    top_picks: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for item in final_ranked:
        bucket, reasons = classify_candidate(item, summary_map)
        entry = {
            "ticker": item.ticker,
            "sector": item.sector,
            "stage": item.stage,
            "action": item.action,
            "signal": item.signal,
            "composite_score": item.composite_score,
            "confidence": item.confidence,
            "risk_score": item.risk_score,
            "summary": item.summary,
            "data_quality_flags": item.data_quality_flags or [],
            "reasons": reasons,
        }
        if bucket == "top_picks":
            top_picks.append(entry)
        elif bucket == "watchlist":
            watchlist.append(entry)
        else:
            excluded.append(entry)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rules_version": 1,
        "top_picks": top_picks,
        "watchlist": watchlist,
        "excluded": excluded,
    }


def build_report(results: list[ScanResult], report_dir: Path, top_n: int, pool_path: Path, report_root: Path) -> None:
    summary_results = [r for r in results if r.stage == "summary"]
    reasoning_results = [r for r in results if r.stage == "reasoning"]
    ranked_summary = sorted([r for r in summary_results if r.success], key=ranking_key)
    final_ranked = merge_final_candidates(summary_results, reasoning_results)
    selected_for_reasoning = [r.ticker for r in reasoning_results]
    reasoning_selection_strategy = {
        "summary_ranking": "先按 composite_score 降序；若缺失则回退 action/confidence/signal 的启发式排序",
        "reasoning_top_n": top_n,
        "reasoning_selection_mode": "diversified-first",
        "reasoning_selection_rule": "优先从不同板块各取 1 只进入 Reasoning；若板块数不足或 TopN 未满，再按总分从高到低回填",
        "final_report_priority": "若股票进入 Reasoning，则最终榜单与结论优先采用 Reasoning 结果覆写 Summary 结果",
    }
    sector_best: dict[str, ScanResult] = {}
    failures = [r for r in results if not r.success]

    for item in final_ranked:
        if item.sector not in sector_best:
            sector_best[item.sector] = item

    previous_payload, previous_dir = load_previous_report(report_root, report_dir)
    daily_diff = build_daily_diff(final_ranked, previous_payload)
    summary_stats = compute_stage_stats(results, "summary")
    reasoning_stats = compute_stage_stats(results, "reasoning")
    final_top = final_ranked[: max(top_n, min(5, len(final_ranked)))]
    final_candidates = build_final_candidates(final_ranked, summary_results)

    lines: list[str] = []
    lines.append(f"# A股夜间扫描报告 - {report_dir.name}")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 股票池: `{pool_path.relative_to(ROOT)}`")
    lines.append(f"- Summary 样本数: {len(summary_results)}")
    lines.append(f"- Reasoning 样本数: {len(reasoning_results)}")
    lines.append(f"- TopN: {top_n}")
    lines.append("")
    lines.append("## 本轮选股策略说明")
    lines.append("")
    lines.append(f"- Summary 排名规则: {reasoning_selection_strategy['summary_ranking']}")
    lines.append(f"- Reasoning TopN: {reasoning_selection_strategy['reasoning_top_n']}")
    lines.append(f"- Reasoning 选股模式: **{reasoning_selection_strategy['reasoning_selection_mode']}**")
    lines.append(f"- 具体规则: {reasoning_selection_strategy['reasoning_selection_rule']}")
    lines.append(f"- 最终榜单规则: {reasoning_selection_strategy['final_report_priority']}")
    if selected_for_reasoning:
        lines.append(f"- 本轮进入 Reasoning: {', '.join(selected_for_reasoning)}")
    lines.append("")

    lines.append("## 运行统计")
    lines.append("")
    lines.append(f"- Summary: 成功 {summary_stats['success']} / 失败 {summary_stats['failed']} / 复用 {summary_stats['skipped']}")
    lines.append(f"- Reasoning: 成功 {reasoning_stats['success']} / 失败 {reasoning_stats['failed']} / 复用 {reasoning_stats['skipped']}")
    lines.append("")

    lines.append("## Top Picks（最终以 Reasoning 结果优先）")
    lines.append("")
    if final_top:
        lines.append("| 排名 | 股票 | 板块 | 来源 | 分数 | 动作 | 信号 | 置信度 | 风险 | 摘要 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for idx, item in enumerate(final_top, start=1):
            score = f"{item.composite_score:.2f}" if item.composite_score is not None else "N/A"
            confidence = f"{item.confidence * 100:.0f}%" if item.confidence is not None else "N/A"
            risk = f"{item.risk_score:.1f}" if item.risk_score is not None else "N/A"
            summary = (item.summary or "N/A").replace("\n", " ").replace("|", "/")
            source = "Reasoning" if item.stage == "reasoning" else "Summary"
            if item.skipped:
                source += "(reused)"
            lines.append(
                f"| {idx} | {item.ticker} | {item.sector} | {source} | {score} | {action_label(item.action)} | {signal_label(item.signal)} | {confidence} | {risk} | {summary} |"
            )
        lines.append("")
    else:
        lines.append("- 无成功样本")
        lines.append("")

    lines.append("## Final Candidates")
    lines.append("")
    if final_candidates["top_picks"]:
        lines.append(f"- Top Picks: {', '.join(item['ticker'] for item in final_candidates['top_picks'])}")
    else:
        lines.append("- Top Picks: 无")
    if final_candidates["watchlist"]:
        lines.append(f"- Watchlist: {', '.join(item['ticker'] for item in final_candidates['watchlist'])}")
    else:
        lines.append("- Watchlist: 无")
    if final_candidates["excluded"]:
        lines.append(f"- Excluded: {', '.join(item['ticker'] for item in final_candidates['excluded'][:10])}")
    else:
        lines.append("- Excluded: 无")
    lines.append("")

    if final_candidates["top_picks"]:
        lines.append("### Top Picks 说明")
        lines.append("")
        for item in final_candidates["top_picks"]:
            reason_text = ", ".join(item["reasons"]) if item["reasons"] else "passed_default_filters"
            lines.append(f"- **{item['ticker']} ({item['sector']})**: action={action_label(item['action'])} | score={item['composite_score']} | source={item['stage']} | {reason_text}")
        lines.append("")

    if final_candidates["watchlist"]:
        lines.append("### Watchlist 说明")
        lines.append("")
        for item in final_candidates["watchlist"]:
            reason_text = ", ".join(item["reasons"]) if item["reasons"] else "monitor"
            lines.append(f"- **{item['ticker']} ({item['sector']})**: action={action_label(item['action'])} | score={item['composite_score']} | source={item['stage']} | {reason_text}")
        lines.append("")

    hold_items = [item for item in final_top if item.action == "hold"]
    if hold_items:
        lines.append("## HOLD 解释")
        lines.append("")
        for item in hold_items:
            reasons: list[str] = []
            if item.pressure_points:
                reasons.append("压制因素: " + "；".join(item.pressure_points[:2]))
            if item.hold_reason:
                reasons.append(item.hold_reason.strip().replace("\n", " "))
            if item.data_quality_flags:
                reasons.append("数据质量: " + "；".join(item.data_quality_flags[:2]))
            lines.append(f"- **{item.ticker} ({item.sector})**: {' | '.join(reasons) if reasons else '模型给出 HOLD，但缺少结构化解释'}")
        lines.append("")

    if daily_diff["has_previous"]:
        lines.append("## 昨日 vs 今日 Diff")
        lines.append("")
        if previous_dir:
            lines.append(f"- 对比基线: `{previous_dir.name}`")
        if daily_diff["newly_entered"]:
            lines.append(f"- 新入选: {', '.join(daily_diff['newly_entered'])}")
        else:
            lines.append("- 新入选: 无")
        if daily_diff["dropped"]:
            lines.append(f"- 掉出榜单: {', '.join(daily_diff['dropped'])}")
        else:
            lines.append("- 掉出榜单: 无")
        if daily_diff["changed"]:
            lines.append("- 结论变化:")
            for item in daily_diff["changed"]:
                lines.append(f"  - {item['ticker']} ({item['sector']}): {item['change']}")
        else:
            lines.append("- 结论变化: 无")
        lines.append("")

    if sector_best:
        lines.append("## 板块代表")
        lines.append("")
        for sector, item in sector_best.items():
            score = f"{item.composite_score:.2f}" if item.composite_score is not None else "N/A"
            extra = ""
            if item.support_points:
                extra = f" | support={'; '.join(item.support_points[:1])}"
            source = "reasoning" if item.stage == "reasoning" else "summary"
            lines.append(
                f"- **{sector}**: {item.ticker} | source={source} | score={score} | action={action_label(item.action)} | signal={signal_label(item.signal)}{extra}"
            )
        lines.append("")

    if reasoning_results:
        lines.append("## Reasoning 复核")
        lines.append("")
        for item in sorted(reasoning_results, key=reasoning_ranking_key):
            score = item.composite_score if item.composite_score is not None else 'N/A'
            reuse_tag = " [reused]" if item.skipped else ""
            lines.append(f"- {item.ticker} | {item.sector} | score={score} | attempts={item.attempts}{reuse_tag} | {item.summary or 'N/A'}")
        lines.append("")

    if failures:
        lines.append("## 失败样本")
        lines.append("")
        for item in failures:
            lines.append(
                f"- {item.stage} | {item.ticker} | rc={item.returncode} | attempts={item.attempts} | {item.error or 'unknown error'} | `{item.raw_path}`"
            )
        lines.append("")

    report_payload = {
        "version": 3,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pool": str(pool_path.relative_to(ROOT)) if pool_path.is_relative_to(ROOT) else str(pool_path),
        "report_dir": str(report_dir),
        "top_n": top_n,
        "reasoning_strategy": reasoning_selection_strategy,
        "selected_for_reasoning": selected_for_reasoning,
        "summary_count": len(summary_results),
        "reasoning_count": len(reasoning_results),
        "summary_stats": summary_stats,
        "reasoning_stats": reasoning_stats,
        "daily_diff": daily_diff,
        "final_top_picks": [asdict(item) for item in final_top],
        "final_candidates": final_candidates,
        "sector_leaders": {
            sector: {
                "ticker": item.ticker,
                "composite_score": item.composite_score,
                "action": item.action,
                "signal": item.signal,
                "stage": item.stage,
            }
            for sector, item in sector_best.items()
        },
        "results": [asdict(r) for r in results],
    }

    (report_dir / "daily_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "final_candidates.json").write_text(
        json.dumps(final_candidates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_stage(
    *,
    ticker: str,
    sector_name: str,
    stage: str,
    report_dir: Path,
    raw_path: Path,
    summary_text_path: Path,
    summary_json_path: Path,
    reasoning: bool,
    num_of_news: int,
    timeout: int,
    model: str | None,
    hq: bool,
    retries: int,
    skip_existing: bool,
) -> ScanResult:
    started = datetime.now().isoformat(timespec="seconds")

    if skip_existing and result_has_cached_artifacts(raw_path, summary_text_path, summary_json_path):
        return load_cached_stage_result(
            ticker=ticker,
            sector_name=sector_name,
            stage=stage,
            started=started,
            report_dir=report_dir,
            raw_path=raw_path,
            summary_text_path=summary_text_path,
            summary_json_path=summary_json_path,
            reused_from="existing-artifacts",
        )

    max_attempts = max(1, retries + 1)
    last_returncode = 1
    last_output = ""
    last_err: str | None = None

    for attempt in range(1, max_attempts + 1):
        last_returncode, last_output, last_err = run_stock_once(
            ticker=ticker,
            out_path=raw_path,
            summary_text_path=summary_text_path,
            summary_json_path=summary_json_path,
            reasoning=reasoning,
            num_of_news=num_of_news,
            timeout=timeout,
            model=model,
            hq=hq,
        )
        if last_returncode == 0:
            return build_stage_result(
                ticker=ticker,
                sector_name=sector_name,
                stage=stage,
                started=started,
                report_dir=report_dir,
                raw_path=raw_path,
                summary_text_path=summary_text_path,
                summary_json_path=summary_json_path,
                returncode=last_returncode,
                raw_output=last_output,
                err=last_err,
                attempts=attempt,
            )

    return build_stage_result(
        ticker=ticker,
        sector_name=sector_name,
        stage=stage,
        started=started,
        report_dir=report_dir,
        raw_path=raw_path,
        summary_text_path=summary_text_path,
        summary_json_path=summary_json_path,
        returncode=last_returncode,
        raw_output=last_output,
        err=last_err,
        attempts=max_attempts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly sector scan wrapper for A-share investment agent")
    parser.add_argument("--pool", default=str(DEFAULT_POOL), help="股票池配置 JSON")
    parser.add_argument("--top-n", type=int, default=10, help="进入 reasoning 的 TopN")
    parser.add_argument("--num-of-news", type=int, default=6, help="summary 阶段新闻数")
    parser.add_argument("--reasoning-num-of-news", type=int, default=10, help="reasoning 阶段新闻数")
    parser.add_argument("--timeout", type=int, default=1800, help="单票超时秒数")
    parser.add_argument("--limit", type=int, default=0, help="仅跑前 N 只（调试用，0=全部）")
    parser.add_argument("--skip-reasoning", action="store_true", help="只跑 summary")
    parser.add_argument("--report-root", default=str(REPORT_ROOT), help="报告根目录")
    parser.add_argument("--report-dir", default=None, help="复用已有报告目录（用于 resume）")
    parser.add_argument("--model", default=None, help="透传给 ./stock 的 --model")
    parser.add_argument("--hq", action="store_true", help="透传给 ./stock 的 --hq")
    parser.add_argument("--retries", type=int, default=1, help="失败重试次数（默认 1，即最多跑 2 次）")
    parser.add_argument("--skip-existing", action="store_true", help="若当前 report_dir 下已有阶段产物，则直接复用，避免重复跑")
    args = parser.parse_args()

    pool_path = Path(args.pool).resolve()
    sectors = load_pool(pool_path)
    report_root = Path(args.report_root).resolve()
    if args.report_dir:
        report_dir = Path(args.report_dir).resolve()
    else:
        day = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        report_dir = report_root / day
    raw_summary_dir = report_dir / "raw" / "summary"
    raw_reasoning_dir = report_dir / "raw" / "reasoning"
    raw_summary_dir.mkdir(parents=True, exist_ok=True)
    raw_reasoning_dir.mkdir(parents=True, exist_ok=True)

    universe: list[tuple[str, str]] = []
    for sector in sectors:
        sector_name = str(sector.get("name", "")).strip()
        tickers = sector.get("tickers", [])
        if not sector_name or not isinstance(tickers, list):
            continue
        for ticker in tickers:
            text = str(ticker).strip()
            if text:
                universe.append((sector_name, text))

    if args.limit > 0:
        universe = limit_universe(universe, args.limit)

    results: list[ScanResult] = []
    for sector_name, ticker in universe:
        base_name = f"{slugify(sector_name)}__{ticker}"
        raw_path = raw_summary_dir / f"{base_name}.txt"
        summary_text_path = raw_summary_dir / f"{base_name}.summary.txt"
        summary_json_path = raw_summary_dir / f"{base_name}.summary.json"
        results.append(
            run_stage(
                ticker=ticker,
                sector_name=sector_name,
                stage="summary",
                report_dir=report_dir,
                raw_path=raw_path,
                summary_text_path=summary_text_path,
                summary_json_path=summary_json_path,
                reasoning=False,
                num_of_news=args.num_of_news,
                timeout=args.timeout,
                model=args.model,
                hq=args.hq,
                retries=args.retries,
                skip_existing=args.skip_existing,
            )
        )

    ranked = sorted([r for r in results if r.stage == "summary" and r.success], key=ranking_key)
    selected = select_reasoning_candidates(ranked, args.top_n)

    if not args.skip_reasoning:
        for base in selected:
            base_name = f"{slugify(base.sector)}__{base.ticker}"
            raw_path = raw_reasoning_dir / f"{base_name}.txt"
            summary_text_path = raw_reasoning_dir / f"{base_name}.summary.txt"
            summary_json_path = raw_reasoning_dir / f"{base_name}.summary.json"
            results.append(
                run_stage(
                    ticker=base.ticker,
                    sector_name=base.sector,
                    stage="reasoning",
                    report_dir=report_dir,
                    raw_path=raw_path,
                    summary_text_path=summary_text_path,
                    summary_json_path=summary_json_path,
                    reasoning=True,
                    num_of_news=args.reasoning_num_of_news,
                    timeout=args.timeout,
                    model=args.model,
                    hq=args.hq,
                    retries=args.retries,
                    skip_existing=args.skip_existing,
                )
            )

    build_report(results, report_dir, args.top_n, pool_path, report_root)
    print(
        json.dumps(
            {
                "report_dir": str(report_dir),
                "report_md": str(report_dir / "daily_report.md"),
                "report_json": str(report_dir / "report.json"),
                "final_candidates_json": str(report_dir / "final_candidates.json"),
                "count": len(results),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
