from __future__ import annotations

from app import factor_v3_path_a_3y_book_round as rnd
from app import factor_v3_path_a_3y_volclip_freeze as freeze
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import (
    FROZEN_VOLCLIP_CANDIDATE_ID,
    FROZEN_VOLCLIP_VARIANT,
)
from scripts.run_path_a_3y_clean_replay import select_targets


def test_frozen_volclip_spec_is_s85_and_immutable() -> None:
    spec = freeze.frozen_volclip_spec()
    card = freeze.build_frozen_volclip_card()
    assert spec["candidate_id"] == FROZEN_VOLCLIP_CANDIDATE_ID
    assert spec["entry_scale"] == 0.85
    assert set(spec["skip_tags"]) == {"rsi_repair", "breadth_advancing_lt_50"}
    assert spec["kernel"]["required_signal_tags"] == [
        "breadth_ma20_gte_60",
        "breakout_20d",
        "volume_confirmed",
    ]
    assert spec["kernel"]["market_levels"] == ["favorable", "neutral"]
    assert card["effective_strategy"] is False
    assert card["automatic_trading_allowed"] is goal.AUTOMATIC_TRADING_ALLOWED
    assert card["refit"] is False
    assert card["candidate_spec_sha256"] == freeze._sha(spec)
    assert card["candidate_spec_sha256"] == freeze.build_frozen_volclip_card()[
        "candidate_spec_sha256"
    ]


def test_convert_legacy_jiaoch_refuses_akshare_and_rescales() -> None:
    from scripts.run_path_a_3y_clean_replay import convert_legacy_jiaoch_payload

    assert convert_legacy_jiaoch_payload(
        {"source": "AKShare stock_zh_a_daily fallback", "records": []}
    ) is None
    assert convert_legacy_jiaoch_payload(
        {"source": "Jiaoch SQLite daily cache stale fallback", "records": []}
    ) is None
    converted = convert_legacy_jiaoch_payload(
        {
            "source": "Jiaoch stk_mins daily qfq",
            "records": [
                {"date": "2023-07-03", "close": 4.42, "volume": 1000.0, "amount": 2000.0}
            ],
        }
    )
    assert converted is not None
    assert converted["records"][0]["volume"] == 10.0
    assert converted["records"][0]["amount"] == 2.0
    assert "jiaoch-daily-bars/shares-cny/v2" in converted["source"]


def test_holdout_slice_excludes_traded_names() -> None:
    eligible = [
        {"symbol": "000001", "name": "a"},
        {"symbol": "000002", "name": "b"},
        {"symbol": "000003", "name": "c"},
    ]
    holdout = select_targets(
        eligible, slice_name="holdout", max_symbols=0, traded=["000002"]
    )
    assert [row["symbol"] for row in holdout] == ["000001", "000003"]


def test_freeze_score_matches_clip_scorer() -> None:
    vol = ("breadth_ma20_gte_60", "breakout_20d", "volume_confirmed")
    from tests.test_path_a_3y_book_round import _trade

    trades = [
        _trade("000001", "2024-01-02", "2024-01-03", "2024-01-10", 12.0, tags=vol),
        _trade("000002", "2024-02-02", "2024-02-05", "2024-02-12", 12.0, tags=vol),
        _trade("000003", "2024-03-04", "2024-03-05", "2024-03-12", 12.0, tags=vol),
        _trade("000004", "2024-04-02", "2024-04-03", "2024-04-10", 12.0, tags=vol),
        _trade("000005", "2024-05-06", "2024-05-07", "2024-05-14", 12.0, tags=vol),
        _trade("000006", "2024-06-03", "2024-06-04", "2024-06-11", 12.0, tags=vol),
        _trade("000007", "2024-07-02", "2024-07-03", "2024-07-10", 12.0, tags=vol),
        _trade("000008", "2024-08-02", "2024-08-05", "2024-08-12", 12.0, tags=vol),
    ]
    direct = rnd.score_clip_round(
        trades,
        variants=(FROZEN_VOLCLIP_VARIANT,),
        excluded_ids=frozenset(),
    )["variants"][0]
    via_freeze = freeze._score_variant(trades)
    assert via_freeze["full_path_return_pct"] == direct["full_path_return_pct"]
    assert via_freeze["full_path_mdd_pct"] == direct["full_path_mdd_pct"]
    assert via_freeze["entry_scale"] == 0.85
    assert via_freeze["promotable"] is False
