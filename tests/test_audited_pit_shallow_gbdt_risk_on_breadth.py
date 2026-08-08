from copy import deepcopy

import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt_risk_on_breadth as risk_on


def _candidate(security_id: str, breadth: float) -> dict[str, object]:
    return {
        "candidate_key": f"{security_id}|2025-01-02",
        "security_id": security_id,
        "signal_date": "2025-01-02",
        "cross_section_above_ma20_fraction": breadth,
    }


def test_risk_on_breadth_strategy_is_frozen_and_registered() -> None:
    strategy = risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC

    assert strategy["selection"]["market_breadth_gate"] == {
        "feature": "cross_section_above_ma20_fraction",
        "comparison": "greater_than_or_equal_unrounded_float64",
        "minimum": 0.5,
        "missing_policy": "reject_candidate",
    }
    assert (
        ridge.resolve_model_oof_adapter(strategy).model_id
        == "shallow_gbdt_risk_on_breadth"
    )
    assert (
        ridge.resolve_ranked_liquidity_run_variant(strategy)["main_rank_mode"]
        == "positive_utility_probability"
    )


def test_market_breadth_gate_keeps_boundary_and_rejects_risk_off_rows() -> None:
    filtered, receipt = ridge.filter_scored_candidates_for_frozen_selection(
        [
            _candidate("cn-a-share:000001.SZ", 0.499999999999),
            _candidate("cn-a-share:000002.SZ", 0.5),
            _candidate("cn-a-share:000003.SZ", 0.75),
        ],
        strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
    )

    assert [item["security_id"] for item in filtered] == [
        "cn-a-share:000002.SZ",
        "cn-a-share:000003.SZ",
    ]
    assert receipt["filter"] == {
        "feature": "cross_section_above_ma20_fraction",
        "comparison": "greater_than_or_equal_unrounded_float64",
        "minimum": 0.5,
        "missing_policy": "reject_candidate",
    }
    assert receipt["input_candidate_count"] == 3
    assert receipt["eligible_candidate_count"] == 2
    assert receipt["excluded_candidate_count"] == 1
    assert receipt["receipt_sha256"]


def test_market_breadth_gate_fails_closed_on_invalid_rows_or_strategy_drift() -> None:
    with pytest.raises(ValueError, match="market breadth"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [_candidate("cn-a-share:000001.SZ", float("nan"))],
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        )

    drifted = deepcopy(risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC)
    drifted["selection"]["market_breadth_gate"]["minimum"] = 0.6
    with pytest.raises(ValueError, match="frozen"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [_candidate("cn-a-share:000001.SZ", 0.75)],
            strategy_spec=drifted,
        )
