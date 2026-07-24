from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta

import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as shallow_gbdt


def _sessions(count: int = 504) -> list[str]:
    start = date(2024, 1, 2)
    return [
        (start + timedelta(days=index)).isoformat()
        for index in range(count)
    ]


def _passing_metrics(*args, **kwargs) -> dict:
    return {
        "gate_metric_basis": "unrounded_float64",
        "trade_win_rate_pct_raw": 60.0,
        "portfolio_max_drawdown_pct_raw": -10.0,
        "trade_profit_factor_raw": 2.0,
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct_raw": 60.0,
        "calmar_latest_12m_raw": 2.0,
        "rolling_1y_windows": [
            {
                "return_pct_raw": 60.0,
                "max_drawdown_pct_raw": -10.0,
                "payoff_ratio_raw": 2.0,
                "profit_factor_raw": 2.0,
                "calmar_raw": 2.0,
            }
        ],
    }


def _variant_inputs(*, sessions: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    signal_date = sessions[126]
    exit_date = sessions[130]
    rows = []
    for index, (security_id, amount, industry) in enumerate(
        (
            ("entity-a", 1.0, "industry-a"),
            ("entity-b", 1_000.0, "industry-b"),
            ("entity-c", 500.0, "industry-c"),
        )
    ):
        candidate_key = f"{security_id}|{signal_date}"
        rows.append(
            {
                "candidate_key": candidate_key,
                "symbol": f"00000{index + 1}",
                "ts_code": f"00000{index + 1}.SZ",
                "security_id": security_id,
                "signal_date": signal_date,
                "entry_date": sessions[127],
                "exit_date": exit_date,
                "signal_industry": industry,
                "candidate_amount": amount,
                "right_censored": False,
                "return_pct": 1.0,
            }
        )
    features = pd.DataFrame(
        [
            {
                "candidate_key": row["candidate_key"],
                "signal_date": row["signal_date"],
                **{
                    name: float(index + 1)
                    for index, name in enumerate(ridge.FEATURE_NAMES)
                },
            }
            for row in rows
        ]
    )
    return features, rows


def _fold_receipt(sessions: list[str]) -> dict:
    ranges = ridge._fold_ranges(
        sessions,
        minimum_training_sessions=126,
        validation_sessions=63,
    )
    return {
        "folds": [
            {
                "validation_start": start,
                "validation_end": end,
            }
            for start, end in ranges
        ],
        "receipt_sha256": "a" * 64,
        "oof_scores_sha256": "b" * 64,
    }


def _fake_adapter(
    *,
    model_id: str,
    score_contract: dict,
    score_field: str,
    scores: list[float],
    captured: dict,
) -> ridge.ModelOOFAdapter:
    def build_scores(features, outcomes, sessions, **kwargs):
        captured["build_features"] = features.copy()
        captured["build_outcomes"] = [dict(row) for row in outcomes]
        captured["build_sessions"] = list(sessions)
        captured["build_kwargs"] = dict(kwargs)
        return (
            pd.DataFrame(
                {
                    "candidate_key": features["candidate_key"].tolist(),
                    score_field: scores,
                }
            ),
            _fold_receipt(list(sessions)),
        )

    def verify_receipt(*args, **kwargs):
        captured["verify_args"] = args
        captured["verify_kwargs"] = dict(kwargs)
        return {"verified": True, "receipt_sha256": "a" * 64}

    return ridge.ModelOOFAdapter(
        model_id=model_id,
        score_contract=score_contract,
        score_field=score_field,
        build_scores=build_scores,
        verify_receipt=verify_receipt,
    )


def test_variant_scoring_keeps_gbdt_probability_contract_and_double_ranking(
    monkeypatch,
):
    sessions = _sessions()
    features, outcomes = _variant_inputs(sessions=sessions)
    captured: dict = {}
    adapter = _fake_adapter(
        model_id="shallow_gbdt_utility_logit",
        score_contract=dict(ridge.SHALLOW_GBDT_SCORE_CONTRACT),
        score_field="predicted_positive_utility_probability",
        scores=[0.5, 0.7, 0.8],
        captured=captured,
    )
    monkeypatch.setattr(
        ridge,
        "resolve_model_oof_adapter",
        lambda strategy_spec: adapter,
    )
    monkeypatch.setattr(ridge, "_trade_metrics", _passing_metrics)

    result = ridge._score_and_evaluate_oof_variant(
        tail_features=features,
        outcome_candidates=outcomes,
        sessions=sessions,
        strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
    )

    assert captured["build_kwargs"]["minimum_training_sessions"] == 126
    assert captured["build_kwargs"]["training_window_sessions"] == 126
    assert captured["build_kwargs"]["validation_sessions"] == 63
    assert captured["verify_kwargs"]["minimum_training_sessions"] == 126
    assert captured["verify_kwargs"]["training_window_sessions"] == 126
    assert captured["verify_kwargs"]["validation_sessions"] == 63
    assert result["positive_pool_receipt"]["comparison"] == (
        "predicted_positive_utility_probability_strictly_greater_than_0.5"
    )
    assert [
        row["candidate_key"]
        for row in result["positive_candidates"]
    ] == [
        "entity-b|2024-05-07",
        "entity-c|2024-05-07",
    ]
    assert result["main_selected"][0]["security_id"] == "entity-c"
    assert result["baseline_selected"][0]["security_id"] == "entity-b"
    assert all(
        row["score"]
        == row["rank_score"]
        == row["predicted_positive_utility_probability"]
        for row in result["scored_execution_candidates"]
    )
    assert "predicted_net_return_pct" not in json.dumps(result)


def test_variant_scoring_preserves_ridge_score_field_and_rank_mode(
    monkeypatch,
):
    sessions = _sessions()
    features, outcomes = _variant_inputs(sessions=sessions)
    captured: dict = {}
    adapter = _fake_adapter(
        model_id="continuous_ridge",
        score_contract=dict(ridge.RIDGE_SCORE_CONTRACT),
        score_field="predicted_net_return_pct",
        scores=[-1.0, 2.0, 3.0],
        captured=captured,
    )
    monkeypatch.setattr(
        ridge,
        "resolve_model_oof_adapter",
        lambda strategy_spec: adapter,
    )
    monkeypatch.setattr(ridge, "_trade_metrics", _passing_metrics)

    result = ridge._score_and_evaluate_oof_variant(
        tail_features=features,
        outcome_candidates=outcomes,
        sessions=sessions,
        strategy_spec=ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
    )

    assert captured["build_kwargs"]["minimum_training_sessions"] == 126
    assert captured["build_kwargs"]["training_window_sessions"] == 126
    assert captured["build_kwargs"]["validation_sessions"] == 63
    assert result["main_selection_receipt"]["parameters"]["rank_mode"] == (
        "predicted_net_return"
    )
    assert result["main_selected"][0]["security_id"] == "entity-c"
    assert all(
        row["score"]
        == row["rank_score"]
        == row["predicted_net_return_pct"]
        for row in result["scored_execution_candidates"]
    )


def test_producer_assertion_selects_the_shallow_gbdt_binding(monkeypatch):
    expected = {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
        ),
        "root_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        ridge,
        "_shallow_gbdt_producer_binding",
        lambda: deepcopy(expected),
    )

    ridge._assert_producer_binding_unchanged(expected)

    monkeypatch.setattr(
        ridge,
        "_shallow_gbdt_producer_binding",
        lambda: {**expected, "root_sha256": "0" * 64},
    )
    with pytest.raises(ValueError, match="producer code changed"):
        ridge._assert_producer_binding_unchanged(expected)
