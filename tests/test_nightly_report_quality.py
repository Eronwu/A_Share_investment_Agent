from src.utils.action_consistency import clean_mixed_language_text, infer_action_from_text, resolve_action_consistency
from src.utils.sector_assignment import dedupe_sector_memberships


def test_action_consistency_prefers_reasoning_when_buy_hold_conflict_exists():
    reasoning = (
        "Risk Management Signal is 'hold' and it outweighs all other signals, making it the top priority. "
        "Therefore, the optimal action is to hold the position with a low confidence."
    )
    assert resolve_action_consistency("buy", "hold", reasoning) == "hold"


def test_action_consistency_keeps_buy_when_reasoning_explicitly_chooses_buy():
    reasoning = (
        "Despite the risk management constraint to hold, a cautious buy remains the best option within the risk limit."
    )
    assert infer_action_from_text(reasoning) == "buy"
    assert resolve_action_consistency("buy", "hold", reasoning) == "buy"


def test_sector_membership_dedupes_duplicate_tickers_to_single_owner():
    sectors = [
        {
            "name": "半导体",
            "keywords": ["半导体"],
            "matched_boards": [{"board": "半导体概念"}],
            "tickers": ["000988", "688525", "002594"],
            "sample_rows": [
                {"ticker": "000988", "source_board": "半导体概念", "source_category": "concept"},
                {"ticker": "688525", "source_board": "半导体", "source_category": "industry"},
                {"ticker": "002594", "source_board": "半导体概念", "source_category": "concept"},
            ],
        },
        {
            "name": "算力",
            "keywords": ["CPO概念", "东数西算"],
            "matched_boards": [{"board": "CPO概念"}, {"board": "东数西算"}],
            "tickers": ["000988", "601868"],
            "sample_rows": [
                {"ticker": "000988", "source_board": "CPO概念", "source_category": "concept"},
                {"ticker": "601868", "source_board": "东数西算", "source_category": "concept"},
            ],
        },
        {
            "name": "机器人",
            "keywords": ["人形机器人", "机器人概念"],
            "matched_boards": [{"board": "人形机器人"}, {"board": "机器人概念"}],
            "tickers": ["688525", "002594", "300476"],
            "sample_rows": [
                {"ticker": "688525", "source_board": "机器人概念", "source_category": "concept"},
                {"ticker": "002594", "source_board": "机器人概念", "source_category": "concept"},
                {"ticker": "300476", "source_board": "人形机器人", "source_category": "concept"},
            ],
        },
        {
            "name": "新能源车",
            "keywords": ["新能源车", "汽车整车"],
            "fallback_tickers": ["300750", "002594"],
            "matched_boards": [{"board": "新能源车"}, {"board": "汽车整车"}],
            "tickers": ["300750", "002594", "300476"],
            "sample_rows": [
                {"ticker": "300750", "source_board": "新能源车", "source_category": "concept"},
                {"ticker": "002594", "source_board": "汽车整车", "source_category": "concept"},
                {"ticker": "300476", "source_board": "新能源车", "source_category": "concept"},
            ],
        },
        {
            "name": "黄金有色",
            "keywords": ["黄金", "有色金属"],
            "matched_boards": [{"board": "有色金属"}],
            "tickers": ["601600"],
            "sample_rows": [
                {"ticker": "601600", "source_board": "有色金属", "source_category": "industry"},
            ],
        },
        {
            "name": "红利央企",
            "keywords": ["中字头"],
            "matched_boards": [{"board": "中字头"}],
            "tickers": ["601600"],
            "sample_rows": [
                {"ticker": "601600", "source_board": "中字头", "source_category": "concept"},
            ],
        },
    ]

    deduped = dedupe_sector_memberships(sectors)
    owners = {}
    for sector in deduped:
        for ticker in sector["tickers"]:
            owners[ticker] = sector["name"]

    assert sum("000988" in sector["tickers"] for sector in deduped) == 1
    assert owners["688525"] == "半导体"
    assert owners["002594"] == "新能源车"
    assert owners["300476"] == "机器人"
    assert owners["601600"] == "黄金有色"


def test_clean_mixed_language_text_removes_action_dirty_suffixes():
    text = (
        "The Risk Management signal mandates 持有ing the position. "
        "The portfolio is currently 持有ing no positions, so 卖出ing is unnecessary."
    )
    cleaned = clean_mixed_language_text(text)
    assert cleaned is not None
    assert "ing" not in cleaned
    assert "Risk Management signal" not in cleaned
    assert "风险管理信号" in cleaned


def test_sector_membership_backfills_preferred_owner_from_fallback():
    sectors = [
        {
            "name": "半导体",
            "keywords": ["半导体"],
            "matched_boards": [{"board": "半导体概念"}],
            "tickers": ["603986", "688525", "002594"],
            "sample_rows": [
                {"ticker": "603986", "name": "兆易创新", "source_board": "半导体", "source_category": "industry"},
                {"ticker": "688525", "name": "佰维存储", "source_board": "半导体", "source_category": "industry"},
                {"ticker": "002594", "name": "比亚迪", "source_board": "半导体概念", "source_category": "concept"},
            ],
        },
        {
            "name": "新能源车",
            "keywords": ["新能源车", "汽车整车"],
            "matched_boards": [{"board": "新能源车"}],
            "fallback_tickers": ["300750", "002594", "601633"],
            "tickers": ["002463", "300750", "002384"],
            "sample_rows": [
                {"ticker": "002463", "name": "沪电股份", "source_board": "新能源车", "source_category": "concept"},
                {"ticker": "300750", "name": "宁德时代", "source_board": "麒麟电池", "source_category": "concept"},
                {"ticker": "002384", "name": "东山精密", "source_board": "新能源车", "source_category": "concept"},
            ],
        },
    ]

    deduped = dedupe_sector_memberships(sectors)
    owners = {ticker: sector["name"] for sector in deduped for ticker in sector["tickers"]}
    new_energy = next(sector for sector in deduped if sector["name"] == "新能源车")

    assert owners["002594"] == "新能源车"
    assert "002594" in new_energy["tickers"]
    assert any(row.get("ticker") == "002594" for row in new_energy["sample_rows"])
