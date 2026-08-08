from copy import deepcopy

import numpy as np
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt_risk_on_breadth as risk_on


def _candidate(
    security_id: str,
    breadth: object,
    *,
    signal_date: str = "2025-01-02",
) -> dict[str, object]:
    return {
        "candidate_key": f"{security_id}|{signal_date}",
        "security_id": security_id,
        "signal_date": signal_date,
        "cross_section_above_ma20_fraction": breadth,
    }


def test_risk_on_breadth_strategy_is_frozen_and_registered() -> None:
    strategy = risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC

    assert strategy["selection"]["market_breadth_gate"] == {
        "feature": "cross_section_above_ma20_fraction",
        "comparison": "greater_than_or_equal_unrounded_float64",
        "minimum": 0.5,
        "missing_policy": "fail_closed_run",
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
            _candidate("cn-a-share:000002.SZ", 0.499999999999),
            _candidate(
                "cn-a-share:000003.SZ",
                0.5,
                signal_date="2025-01-03",
            ),
            _candidate(
                "cn-a-share:000004.SZ",
                0.5,
                signal_date="2025-01-03",
            ),
            _candidate(
                "cn-a-share:000005.SZ",
                0.75,
                signal_date="2025-01-06",
            ),
            _candidate(
                "cn-a-share:000006.SZ",
                0.75,
                signal_date="2025-01-06",
            ),
        ],
        strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
    )

    assert [item["security_id"] for item in filtered] == [
        "cn-a-share:000003.SZ",
        "cn-a-share:000004.SZ",
        "cn-a-share:000005.SZ",
        "cn-a-share:000006.SZ",
    ]
    assert receipt["filter"] == {
        "feature": "cross_section_above_ma20_fraction",
        "comparison": "greater_than_or_equal_unrounded_float64",
        "minimum": 0.5,
        "missing_policy": "fail_closed_run",
    }
    assert receipt["input_candidate_count"] == 6
    assert receipt["eligible_candidate_count"] == 4
    assert receipt["excluded_candidate_count"] == 2
    assert receipt["receipt_sha256"]


@pytest.mark.parametrize(
    "invalid_breadth",
    [float("nan"), -0.01, 1.01, 1, np.float32(0.75)],
)
def test_market_breadth_gate_fails_closed_on_invalid_rows(
    invalid_breadth: object,
) -> None:
    with pytest.raises(ValueError, match="market breadth"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [_candidate("cn-a-share:000001.SZ", invalid_breadth)],
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        )


def test_market_breadth_gate_rejects_inconsistent_same_day_values() -> None:
    with pytest.raises(ValueError, match="market breadth"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [
                _candidate("cn-a-share:000001.SZ", 0.25),
                _candidate("cn-a-share:000002.SZ", 0.75),
            ],
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        )


def test_market_breadth_filter_is_order_independent_and_rejects_duplicate_keys() -> None:
    candidates = [
        _candidate("cn-a-share:000002.SZ", 0.75),
        _candidate("cn-a-share:000001.SZ", 0.75),
        _candidate(
            "cn-a-share:000003.SZ",
            0.25,
            signal_date="2025-01-03",
        ),
    ]
    filtered, receipt = ridge.filter_scored_candidates_for_frozen_selection(
        candidates,
        strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
    )
    reversed_filtered, reversed_receipt = (
        ridge.filter_scored_candidates_for_frozen_selection(
            list(reversed(candidates)),
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        )
    )

    assert reversed_filtered == filtered
    assert reversed_receipt == receipt
    with pytest.raises(ValueError, match="duplicated"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [candidates[0], deepcopy(candidates[0])],
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
        )


def test_market_breadth_gate_rejects_strategy_drift() -> None:
    drifted = deepcopy(risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC)
    drifted["selection"]["market_breadth_gate"]["minimum"] = 0.6
    with pytest.raises(ValueError, match="not frozen"):
        risk_on.assert_frozen_risk_on_breadth_strategy(drifted)
    with pytest.raises(ValueError, match="frozen"):
        ridge.filter_scored_candidates_for_frozen_selection(
            [_candidate("cn-a-share:000001.SZ", 0.75)],
            strategy_spec=drifted,
        )


def test_market_breadth_filter_receipt_rejects_tampering() -> None:
    candidates = [_candidate("cn-a-share:000001.SZ", 0.75)]
    _, receipt = ridge.filter_scored_candidates_for_frozen_selection(
        candidates,
        strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
    )
    tampered = deepcopy(receipt)
    tampered["eligible_candidate_count"] = 0

    with pytest.raises(ValueError, match="market breadth filter receipt"):
        ridge.verify_market_breadth_filter_receipt(
            tampered,
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
            input_candidates=candidates,
        )

    resigned = deepcopy(receipt)
    resigned["eligible_candidate_keys_sha256"] = "e" * 64
    unsigned = {
        key: value
        for key, value in resigned.items()
        if key != "receipt_sha256"
    }
    resigned["receipt_sha256"] = ridge._sha256(unsigned)
    with pytest.raises(ValueError, match="market breadth filter receipt"):
        ridge.verify_market_breadth_filter_receipt(
            resigned,
            strategy_spec=risk_on.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
            input_candidates=candidates,
        )
