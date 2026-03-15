#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import random
import socket
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "config" / "sector_pool_rules.v1.json"
DEFAULT_OUT = ROOT / "config" / "sector_pool.generated.json"
PROXY_ENV_KEYS = [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
]
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
    "Origin": "https://quote.eastmoney.com",
}
EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
EM_INDUSTRY_URL = "https://17.push2.eastmoney.com/api/qt/clist/get"
EM_CONCEPT_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
EM_CONS_URL = "https://29.push2.eastmoney.com/api/qt/clist/get"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


@contextmanager
def direct_network_env() -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    try:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        yield
    finally:
        for key in PROXY_ENV_KEYS:
            value = saved.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def force_ipv4_dns() -> Iterator[None]:
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_only_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def em_request(url: str, params: dict[str, Any], timeout: int = 15, max_retries: int = 4) -> dict[str, Any]:
    last_exception: Exception | None = None
    with direct_network_env():
        with force_ipv4_dns():
            for attempt in range(max_retries):
                try:
                    with requests.Session() as session:
                        session.trust_env = False
                        session.proxies.clear()
                        session.headers.update(BROWSER_HEADERS)
                        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
                        session.mount("http://", adapter)
                        session.mount("https://", adapter)
                        response = session.get(url, params=params, timeout=timeout, allow_redirects=True)
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict) or payload.get("data") is None:
                            raise RuntimeError(f"unexpected eastmoney payload: {payload}")
                        return payload
                except Exception as exc:  # noqa: BLE001
                    last_exception = exc
                    if attempt < max_retries - 1:
                        time.sleep((2**attempt) * 0.8 + random.uniform(0.3, 1.0))
        raise RuntimeError(f"eastmoney request failed: {last_exception}")


def fetch_paginated_diff(url: str, base_params: dict[str, Any], timeout: int = 15) -> pd.DataFrame:
    params = dict(base_params)
    first = em_request(url, params, timeout=timeout)
    data = first.get("data") or {}
    diff = data.get("diff") or []
    total = int(data.get("total") or len(diff) or 0)
    per_page = max(1, len(diff) or int(params.get("pz") or 100))
    total_page = max(1, math.ceil(total / per_page))
    frames = [pd.DataFrame(diff)] if diff else []

    for page in range(2, total_page + 1):
        params["pn"] = str(page)
        time.sleep(random.uniform(0.2, 0.8))
        payload = em_request(url, params, timeout=timeout)
        page_diff = (payload.get("data") or {}).get("diff") or []
        if page_diff:
            frames.append(pd.DataFrame(page_diff))

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_board_catalog() -> dict[str, pd.DataFrame]:
    industry_params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": EM_UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90 t:2 f:!50",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26,f22,f33,f11,f62,f128,f136,f115,f152,f124,f107,f104,f105,f140,f141,f207,f208,f209,f222",
    }
    concept_params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": EM_UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:90 t:3 f:!50",
        "fields": "f2,f3,f4,f8,f12,f14,f15,f16,f17,f18,f20,f21,f24,f25,f22,f33,f11,f62,f128,f124,f107,f104,f105,f136",
    }
    industry_df = fetch_paginated_diff(EM_INDUSTRY_URL, industry_params)
    concept_df = fetch_paginated_diff(EM_CONCEPT_URL, concept_params)
    return {
        "industry": industry_df,
        "concept": concept_df,
    }


def board_names_from_catalog(catalog_df: pd.DataFrame) -> list[str]:
    if catalog_df.empty:
        return []
    return catalog_df.get("f14", pd.Series(dtype=str)).astype(str).tolist()


def find_board_code(catalog_df: pd.DataFrame, board_name: str) -> str | None:
    if catalog_df.empty:
        return None
    matched = catalog_df[catalog_df["f14"].astype(str) == board_name]
    if matched.empty:
        return None
    return str(matched.iloc[0]["f12"])


def match_boards(board_names: list[str], keywords: list[str]) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        for name in board_names:
            if kw in name and name not in seen:
                matched.append(name)
                seen.add(name)
    return matched


def fetch_constituents(board_code: str) -> pd.DataFrame:
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": EM_UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": f"b:{board_code} f:!50",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152,f45",
    }
    return fetch_paginated_diff(EM_CONS_URL, params)


def rank_constituents(df: pd.DataFrame, sort_fields: list[str]) -> pd.DataFrame:
    ranked = df.copy()
    field_map = {
        "成交额": "f6",
        "涨跌幅": "f3",
        "换手率": "f8",
    }
    resolved_fields: list[str] = []
    for field in sort_fields:
        resolved = field_map.get(field, field)
        if resolved not in ranked.columns:
            ranked[resolved] = 0
        ranked[resolved] = ranked[resolved].map(safe_float)
        resolved_fields.append(resolved)
    ranked = ranked.sort_values(resolved_fields, ascending=[False] * len(resolved_fields), kind="stable")
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
            category_df = catalog.get(category, pd.DataFrame())
            names = board_names_from_catalog(category_df)
            matched = match_boards(names, keywords)
            for board_name in matched:
                board_code = find_board_code(category_df, board_name)
                if not board_code:
                    continue
                matched_boards.append({"category": category, "board": board_name, "board_code": board_code})
                try:
                    df = fetch_constituents(board_code)
                except Exception:
                    continue
                if df is None or df.empty or "f12" not in df.columns:
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
                code = str(row.get("f12", "")).strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                tickers.append(code)
                sample_rows.append({
                    "ticker": code,
                    "name": str(row.get("f14", "")),
                    "amount": safe_float(row.get("f6")),
                    "change_pct": safe_float(row.get("f3")),
                    "turnover_rate": safe_float(row.get("f8")),
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
        "description": "A股半自动生成股票池（基于东方财富原始板块接口与成交额排序）",
        "rules_file": str(DEFAULT_RULES.relative_to(ROOT)),
        "sectors": sectors_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build semi-automatic sector pool from Eastmoney raw board APIs")
    parser.add_argument("--rules", default=str(DEFAULT_RULES), help="rules json path")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output json path")
    args = parser.parse_args()

    rules_path = Path(args.rules).resolve()
    out_path = Path(args.out).resolve()
    rules = load_json(rules_path)
    payload = build_pool(rules)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out_path), "sector_count": len(payload.get('sectors', []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
