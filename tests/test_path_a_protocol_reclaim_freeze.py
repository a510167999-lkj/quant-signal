from __future__ import annotations

from app import factor_v3_path_a_protocol_reclaim_freeze as freeze
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    HOLD5_TAG,
    PULLBACK_TAG,
)


def test_frozen_reclaim_is_negext_h5_and_not_effective() -> None:
    spec = freeze.frozen_reclaim_spec()
    card = freeze.build_frozen_reclaim_card()
    assert spec["candidate_id"] == FROZEN_RECLAIM_CANDIDATE_ID
    assert spec["rank_key"] == "neg_ext20"
    assert spec["kernel"]["required_signal_tags"] == [
        PULLBACK_TAG,
        HOLD5_TAG,
    ]
    assert spec["kernel"]["top_n"] == 2
    assert spec["kernel"]["max_active_positions"] == 1
    assert spec["kernel"]["hold_days"] == 5
    assert spec["kernel"]["market_levels"] == ["favorable", "neutral"]
    assert card["effective_strategy"] is False
    assert card["promotable"] is False
    assert card["meets_primary_targets"] is False
    assert card["user_accepted_hypothesis"] is True
    assert card["automatic_trading_allowed"] is goal.AUTOMATIC_TRADING_ALLOWED
    assert card["refit"] is False
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 26.0
    assert card["target_rolling_12m_net_return_pct"] == 26.0
    assert card["target_max_drawdown_pct"] == 15.0
    table = freeze.format_reclaim_freeze_table(
        {
            "stage_goal_id": freeze.STAGE_GOAL_ID,
            "current_development_candidate": FROZEN_RECLAIM_CANDIDATE_ID,
            "user_accepted_hypothesis": True,
            "effective_strategy": False,
            "holdout_dual_pass_26_15": True,
            "train": {"latest_1y_return_pct": 5.79, "full_path_mdd_pct": -13.32},
            "holdout": {
                "latest_1y_return_pct": 26.45,
                "full_path_mdd_pct": -9.48,
                "selected_trade_count": 22,
            },
            "card": card,
        }
    )
    assert "holdout_dual_pass_26_15=True" in table
    assert "effective_strategy=False" in table
    assert card["candidate_spec_sha256"] == freeze._sha(spec)
    assert (
        card["candidate_spec_sha256"]
        == freeze.build_frozen_reclaim_card()["candidate_spec_sha256"]
    )
