from __future__ import annotations

from app import factor_v3_path_a_protocol_baseline as baseline
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_baseline_specs import (
    assert_variants_obey_protocol,
    iter_protocol_baseline_variants,
)


def test_protocol_locks_split_and_rejects_s85() -> None:
    desc = proto.protocol_descriptor()
    assert desc["protocol_id"] == "path-a-locked-split/v1"
    assert desc["train_start"] == "2023-07-03"
    assert desc["holdout_start"] == "2025-07-01"
    assert desc["holdout_end"] == "2026-07-03"
    assert desc["rejected_slice_candidate_id"] == "vol_skip_rsi_adv_s85"
    assert desc["rejected_slice_status"] == "rejected_slice_hypothesis"
    assert "vol_skip_rsi_adv_s85" in proto.CONTAMINATED_CANDIDATE_IDS
    assert proto.partition_for_signal_date("2024-12-31") == "train"
    assert proto.partition_for_signal_date("2025-07-01") == "holdout"
    assert proto.partition_for_signal_date("2026-07-03") == "holdout"
    assert proto.partition_for_signal_date("2026-07-04") is None
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 50.0
    assert goal.TARGET_MAX_DRAWDOWN_PCT == 15.0


def test_locked_split_uses_latest_12m_not_full_path_return() -> None:
    assert proto.meets_locked_split_targets(
        latest_12m_net_return_pct=62.0,
        partition_max_drawdown_pct=-14.0,
    )
    assert not proto.meets_locked_split_targets(
        latest_12m_net_return_pct=26.0,
        partition_max_drawdown_pct=-14.0,
    )
    assert not proto.meets_locked_split_targets(
        latest_12m_net_return_pct=62.0,
        partition_max_drawdown_pct=-15.1,
    )
    assert not proto.meets_locked_split_targets(
        latest_12m_net_return_pct=None,
        partition_max_drawdown_pct=-10.0,
    )


def test_baseline_variants_are_clean_and_disjoint() -> None:
    assert_variants_obey_protocol()
    ids = [row["candidate_id"] for row in iter_protocol_baseline_variants()]
    assert ids == ["proto_e4", "proto_vol", "proto_t2_m1"]
    assert set(ids).isdisjoint(proto.CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_baseline_variants():
        assert not row.get("skip_tags")
        assert row.get("entry_scale") in (None, 1, 1.0)


def test_baseline_scorer_flags_holdout_via_protocol() -> None:
    from tests.test_path_a_3y_book_round import _trade

    tags = ("breadth_ma20_gte_60", "breakout_20d")
    train = [
        _trade("000001", "2024-01-02", "2024-01-03", "2024-01-10", 12.0, tags=tags),
        _trade("000002", "2024-03-04", "2024-03-05", "2024-03-12", 12.0, tags=tags),
        _trade("000003", "2024-06-03", "2024-06-04", "2024-06-11", 12.0, tags=tags),
        _trade("000004", "2025-01-02", "2025-01-03", "2025-01-10", 12.0, tags=tags),
    ]
    holdout = [
        _trade("000005", "2025-08-04", "2025-08-05", "2025-08-12", -20.0, tags=tags),
        _trade("000006", "2025-10-08", "2025-10-09", "2025-10-16", -20.0, tags=tags),
        _trade("000007", "2026-01-05", "2026-01-06", "2026-01-13", -20.0, tags=tags),
    ]
    one = (
        {
            "candidate_id": "proto_e4",
            "role": "protocol_baseline",
            "kernel": {
                "top_n": 3,
                "max_active_positions": 2,
                "symbol_cooldown_days": 5,
                "market_levels": ("favorable", "neutral"),
                "required_signal_tags": tags,
                "excluded_signal_tags": ("price_gap_down",),
            },
            "rationale": "test",
        },
    )
    report = baseline.score_protocol_baseline(train, holdout, variants=one)
    row = report["variants"][0]
    assert row["holdout"]["dual_pass_via"] == (
        "locked_split latest_12m + partition_mdd"
    )
    assert row["holdout_dual_pass_50_15"] is False
    assert report["fifty_fifteen_holdout_met"] is False
    assert report["effective_strategy"] is False
