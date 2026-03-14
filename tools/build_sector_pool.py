#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "config" / "sector_pool_rules.v1.json"
DEFAULT_OUT = ROOT / "config" / "sector_pool.generated.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def get_board_catalog() -> dict[str, list[str]]:
    industry = ak.stock_board_industry_name_em()["板块名称"].astype(str).tolist()
    concept = ak.stock_board_concept_name_em()["板块名称"].astype(str).tolist()
    return {"industry": industry, "concept": concept}


def match_boards(board_names: list[str], keywords: list[str]) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        for name in board_names:
            if kw in name and name not in seen:
                matched.append(name)
                seen.add(name)
    return matched


def fetch_constituents(category: str, board_name: str) -> pd.DataFrame:
    if category == "industry":
        return ak.stock_board_industry_cons_em(symbol=board_name)
    if category == "concept":
        return ak.stock_board_concept_cons_em(symbol=board_name)
    raise ValueError(f"Unknown category: {category}")


def rank_constituents(df: pd.DataFrame, sort_fields: list[str]) -> pd.DataFrame:
    ranked = df.copy()
    for field in sort_fields:
        if field not in ranked.columns:
            ranked[field] = 0
        ranked[field] = ranked[field].map(safe_float)
    ranked = ranked.sort_values(sort_fields, ascending=[False] * len(sort_fields), kind="stable")
    return ranked.reset_index(drop=True)


def build_pool(rules: dict[str, Any]) -> dict[str, Any]:
    defaults = rules.get("defaults", {}) if isinstance(rules.get("defaults"), dict) else {}
    default_top_n = int(defaults.get("top_n", 5) or 5)
    default_categories = defaults.get("categories", ["concept", "industry"])
    default_sort_by = defaults.get("sort_by", ["成交额", "涨跌幅", "换手率"])

    catalog = get_board_catalog()
    sectors_out: list[dict[str, Any]] = []

    for sector in rules.get("sectors", []):
        name = str(sector.get("name", "")).strip()
        if not name:
            continue
        categories = sector.get("categories") or default_categories
        keywords = [str(x).strip() for x in sector.get("board_keywords", []) if str(x).strip()]
        top_n = int(sector.get("top_n", default_top_n) or default_top_n)
        sort_by = sector.get("sort_by") or default_sort_by
        fallback_tickers = [str(x).strip() for x in sector.get("fallback_tickers", []) if str(x).strip()]

        matched_boards: list[dict[str, str]] = []
        frames: list[pd.DataFrame] = []
        for category in categories:
            names = catalog.get(category, [])
            matched = match_boards(names, keywords)
            for board_name in matched:
                matched_boards.append({"category": category, "board": board_name})
                try:
                    df = fetch_constituents(category, board_name)
                except Exception:
                    continue
                if df is None or df.empty or "代码" not in df.columns:
                    continue
                df = df.copy()
                df["source_category"] = category
                df["source_board"] = board_name
                frames.append(df)

        tickers: list[str] = []
        sample_rows: list[dict[str, Any]] = []
        generation_mode = "matched"
        if frames:
            merged = pd.concat(frames, ignore_index=True)
            merged = rank_constituents(merged, sort_by)
            seen: set[str] = set()
            for _, row in merged.iterrows():
                code = str(row.get("代码", "")).strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                tickers.append(code)
                sample_rows.append({
                    "ticker": code,
                    "name": str(row.get("名称", "")),
                    "amount": safe_float(row.get("成交额")),
                    "change_pct": safe_float(row.get("涨跌幅")),
                    "turnover_rate": safe_float(row.get("换手率")),
                    "source_category": row.get("source_category"),
                    "source_board": row.get("source_board"),
                })
                if len(tickers) >= top_n:
                    break

        if len(tickers) < top_n:
            generation_mode = "fallback" if not tickers else "hybrid"
            for code in fallback_tickers:
                if code not in tickers:
                    tickers.append(code)
                if len(tickers) >= top_n:
                    break

        sectors_out.append({
            "name": name,
            "description": sector.get("description", ""),
            "tickers": tickers[:top_n],
            "generation_mode": generation_mode,
            "matched_boards": matched_boards,
            "keywords": keywords,
            "sample_rows": sample_rows[:top_n],
        })

    return {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "description": "A股半自动生成股票池（基于EM板块成分与成交额排序）",
        "rules_file": str(DEFAULT_RULES.relative_to(ROOT)),
        "sectors": sectors_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build semi-automatic sector pool from EM board constituents")
    parser.add_argument("--rules", default=str(DEFAULT_RULES), help="rules json path")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output json path")
    args = parser.parse_args()

    rules_path = Path(args.rules).resolve()
    out_path = Path(args.out).resolve()
    rules = load_json(rules_path)
    payload = build_pool(rules)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out_path), "sector_count": len(payload.get("sectors", []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
