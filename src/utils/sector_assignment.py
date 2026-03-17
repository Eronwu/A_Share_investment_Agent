from __future__ import annotations

from copy import deepcopy
from typing import Any

PREFERRED_TICKER_SECTORS: dict[str, str] = {
    # 比亚迪会被“半导体概念”等宽泛概念误吸入，夜跑里优先归到新能源车更稳定。
    "002594": "新能源车",
}


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _sector_anchors(sector: dict[str, Any]) -> list[str]:
    anchors: list[Any] = [sector.get("name")]
    anchors.extend(sector.get("keywords") or [])
    anchors.extend((item or {}).get("board") for item in sector.get("matched_boards") or [] if isinstance(item, dict))
    anchors.extend((item or {}).get("board") for item in sector.get("boards") or [] if isinstance(item, dict))
    return _unique_strings(list(anchors))


def _fallback_tickers(sector: dict[str, Any]) -> set[str]:
    raw = sector.get("fallback_tickers") or []
    return {str(item).strip() for item in raw if str(item).strip()}


def _sample_row_lookup(sector: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in sector.get("sample_rows") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", "")).strip()
        if ticker:
            lookup[ticker] = row
    return lookup


def _global_sample_row_lookup(sectors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for sector in sectors:
        for ticker, row in _sample_row_lookup(sector).items():
            lookup.setdefault(ticker, row)
    return lookup


def _anchor_match_score(source_board: str, anchors: list[str]) -> tuple[int, int]:
    best_score = 0
    best_order = 10**6
    if not source_board:
        return best_score, best_order

    for idx, anchor in enumerate(anchors):
        if not anchor:
            continue
        if source_board == anchor:
            score = 50 + len(anchor)
        elif anchor in source_board or source_board in anchor:
            score = 25 + len(anchor)
        else:
            continue
        if score > best_score or (score == best_score and idx < best_order):
            best_score = score
            best_order = idx

    return best_score, best_order


def dedupe_sector_memberships(sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: dict[str, tuple[tuple[int, int, int, int, int, int, int], int]] = {}
    sector_rows = [_sample_row_lookup(sector) for sector in sectors]
    global_rows = _global_sample_row_lookup(sectors)
    sector_anchors = [_sector_anchors(sector) for sector in sectors]

    for sector_idx, sector in enumerate(sectors):
        sector_name = str(sector.get("name", "")).strip()
        fallbacks = _fallback_tickers(sector)
        tickers = [str(item).strip() for item in sector.get("tickers") or [] if str(item).strip()]
        candidate_tickers = tickers + [ticker for ticker in fallbacks if ticker not in tickers]

        for ticker_pos, ticker in enumerate(candidate_tickers):
            row = sector_rows[sector_idx].get(ticker, {})
            source_board = str(row.get("source_board", "")).strip()
            source_category = str(row.get("source_category", "")).strip().lower()
            board_score, board_order = _anchor_match_score(source_board, sector_anchors[sector_idx])
            preferred_owner = PREFERRED_TICKER_SECTORS.get(ticker)
            claim_key = (
                1 if preferred_owner == sector_name else 0,
                1 if ticker in fallbacks else 0,
                1 if source_category == "industry" else 0,
                board_score,
                -board_order,
                -ticker_pos,
                -sector_idx,
            )
            current = claims.get(ticker)
            if current is None or claim_key > current[0]:
                claims[ticker] = (claim_key, sector_idx)

    deduped = deepcopy(sectors)
    for sector_idx, sector in enumerate(deduped):
        sector_name = str(sector.get("name", "")).strip()
        rows_by_ticker = _sample_row_lookup(sector)
        ordered_tickers = [str(item).strip() for item in sector.get("tickers") or [] if str(item).strip()]
        fallbacks = [ticker for ticker in sector.get("fallback_tickers") or [] if str(ticker).strip()]
        candidate_tickers = ordered_tickers + [str(ticker).strip() for ticker in fallbacks if str(ticker).strip() and str(ticker).strip() not in ordered_tickers]
        preferred_tickers = [
            ticker for ticker in candidate_tickers
            if claims.get(ticker, ((0, 0, 0, 0, 0, 0, 0), -1))[1] == sector_idx and PREFERRED_TICKER_SECTORS.get(ticker) == sector_name
        ]
        kept_tickers = preferred_tickers + [
            ticker for ticker in candidate_tickers
            if claims.get(ticker, ((0, 0, 0, 0, 0, 0, 0), -1))[1] == sector_idx and ticker not in preferred_tickers
        ]
        target_size = len(ordered_tickers)
        if target_size <= 0:
            sample_rows = sector.get("sample_rows") or []
            target_size = len(sample_rows) if isinstance(sample_rows, list) and sample_rows else len(fallbacks)
        if target_size <= 0:
            target_size = len(kept_tickers)
        sector["tickers"] = kept_tickers[:target_size]
        if sector.get("sample_rows"):
            sample_rows: list[dict[str, Any]] = []
            for ticker in sector["tickers"]:
                if ticker in rows_by_ticker:
                    sample_rows.append(rows_by_ticker[ticker])
                    continue
                fallback_row = dict(global_rows.get(ticker, {}))
                if fallback_row:
                    fallback_row["ticker"] = ticker
                    if PREFERRED_TICKER_SECTORS.get(ticker) == sector_name:
                        fallback_row["source_category"] = "preferred_fallback"
                        fallback_row["source_board"] = sector_name
                    sample_rows.append(fallback_row)
            sector["sample_rows"] = sample_rows
    return deduped
