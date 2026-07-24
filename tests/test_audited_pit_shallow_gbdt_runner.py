from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any

import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as gbdt
from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
)


def _expected_shallow_gbdt_spec() -> dict[str, Any]:
    spec = deepcopy(ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC)
    spec.update(
        {
            "schema_version": (
                "development-pit-cross-sectional-shallow-gbdt-"
                "utility-logit-rolling-126-oof/v1"
            ),
            "signal_tag": (
                "cross_sectional_shallow_gbdt_utility_logit_"
                "rolling_126_oof"
            ),
            "label": {
                "target": (
                    "gross_return_pct_minus_0.45_then_fold_p99_"
                    "symmetric_clip"
                ),
                "friction_percentage_points": 0.45,
                "clipping": {
                    "method": (
                        "absolute_p99_zero_based_nearest_rank_symmetric"
                    ),
                    "cutoff_index": "ceil(0.99*N)-1",
                    "scope": "mature_complete_training_rows_per_fold",
                },
                "class_label": "positive_clipped_net_return",
                "sample_weight": (
                    "abs_clipped_return_over_same_signal_date_abs_sum"
                ),
                "utility": (
                    "clipped_net_return_over_same_signal_date_abs_sum"
                ),
                "zero_return_policy": "retain_row_with_zero_weight",
            },
            "model": {
                "type": "weighted_binary_logistic_shallow_gbdt",
                "parameters": {
                    "objective": "binary:logistic",
                    "booster": "gbtree",
                    "tree_method": "hist",
                    "device": "cpu",
                    "max_depth": 2,
                    "eta": 0.05,
                    "min_child_weight": 4.0,
                    "gamma": 0.0,
                    "subsample": 1.0,
                    "colsample_bytree": 1.0,
                    "colsample_bylevel": 1.0,
                    "colsample_bynode": 1.0,
                    "reg_lambda": 1.0,
                    "reg_alpha": 0.0,
                    "max_bin": 256,
                    "grow_policy": "depthwise",
                    "base_score": 0.5,
                    "seed": 20260724,
                    "nthread": 1,
                    "validate_parameters": True,
                },
                "num_boost_round": 200,
                "xgboost_version": "3.2.0",
                "feature_input_dtype": "float64",
                "feature_input_layout": "C_contiguous",
                "feature_types": ["float"] * len(ridge.FEATURE_NAMES),
                "hyperparameter_search": False,
                "early_stopping": False,
                "validation_metric_model_selection": False,
                "feature_standardization": False,
                "interactions": "tree_internal_only",
            },
            "selection": {
                "score_contract": dict(SHALLOW_GBDT_SCORE_CONTRACT),
                "comparison": "strictly_greater_unrounded_float64",
                "top_n": 3,
                "max_active_positions": 3,
                "max_active_positions_per_industry": 1,
                "main_rank": [
                    "predicted_positive_utility_probability_desc",
                    "signal_date_amount_desc",
                    "security_id_asc",
                ],
                "amount_baseline_rank": [
                    "signal_date_amount_desc",
                    "security_id_asc",
                ],
            },
        }
    )
    return spec


def test_shallow_gbdt_oof_spec_freezes_full_preregistered_design() -> None:
    spec = gbdt.SHALLOW_GBDT_OOF_SPEC

    assert spec == _expected_shallow_gbdt_spec()
    assert spec["features"] == list(ridge.FEATURE_NAMES)
    assert spec["market_scope"] == (
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC["market_scope"]
    )
    assert "predicted_net_return_pct" not in str(spec)


def test_model_oof_adapter_preserves_legacy_ridge_and_binds_gbdt_score_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls: list[dict[str, Any]] = []
    verify_calls: list[dict[str, Any]] = []

    def fake_build(
        features: pd.DataFrame,
        outcomes: list[dict[str, Any]],
        sessions: list[str],
        *,
        minimum_training_sessions: int,
        training_window_sessions: int,
        validation_sessions: int,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        build_calls.append(
            {
                "features": features,
                "outcomes": outcomes,
                "sessions": sessions,
                "minimum_training_sessions": minimum_training_sessions,
                "training_window_sessions": training_window_sessions,
                "validation_sessions": validation_sessions,
            }
        )
        return pd.DataFrame(), {"receipt": "gbdt"}

    def fake_verify(
        features: pd.DataFrame,
        outcomes: list[dict[str, Any]],
        sessions: list[str],
        scored_oof: pd.DataFrame,
        receipt: dict[str, Any],
        *,
        minimum_training_sessions: int,
        training_window_sessions: int,
        validation_sessions: int,
    ) -> dict[str, Any]:
        verify_calls.append(
            {
                "features": features,
                "outcomes": outcomes,
                "sessions": sessions,
                "scored_oof": scored_oof,
                "receipt": receipt,
                "minimum_training_sessions": minimum_training_sessions,
                "training_window_sessions": training_window_sessions,
                "validation_sessions": validation_sessions,
            }
        )
        return {"verified": True}

    monkeypatch.setattr(gbdt, "build_shallow_gbdt_rolling_oof_scores", fake_build)
    monkeypatch.setattr(
        gbdt,
        "verify_shallow_gbdt_rolling_oof_receipt",
        fake_verify,
    )

    ridge_adapter = ridge.resolve_model_oof_adapter(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC
    )
    gbdt_adapter = ridge.resolve_model_oof_adapter(
        gbdt.SHALLOW_GBDT_OOF_SPEC
    )

    assert ridge_adapter.model_id == "continuous_ridge"
    assert ridge_adapter.score_contract is RIDGE_SCORE_CONTRACT
    assert ridge_adapter.score_field == "predicted_net_return_pct"
    assert ridge_adapter.build_scores is ridge._build_purged_oof_scores
    assert ridge_adapter.verify_receipt is ridge.verify_rolling_oof_receipt

    assert gbdt_adapter.model_id == "shallow_gbdt_utility_logit"
    assert gbdt_adapter.score_contract is SHALLOW_GBDT_SCORE_CONTRACT
    assert gbdt_adapter.score_field == (
        "predicted_positive_utility_probability"
    )
    assert gbdt_adapter.build_scores is fake_build
    assert gbdt_adapter.verify_receipt is fake_verify
    assert "predicted_net_return_pct" not in str(gbdt_adapter)

    expected_parameter_names = (
        "features",
        "outcomes",
        "sessions",
        "minimum_training_sessions",
        "training_window_sessions",
        "validation_sessions",
    )
    assert tuple(inspect.signature(gbdt_adapter.build_scores).parameters) == (
        expected_parameter_names
    )
    assert tuple(inspect.signature(gbdt_adapter.verify_receipt).parameters) == (
        "features",
        "outcomes",
        "sessions",
        "scored_oof",
        "receipt",
        "minimum_training_sessions",
        "training_window_sessions",
        "validation_sessions",
    )

    features = pd.DataFrame()
    outcomes: list[dict[str, Any]] = []
    sessions = ["2026-01-05"]
    scored, receipt = gbdt_adapter.build_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=126,
        training_window_sessions=126,
        validation_sessions=63,
    )
    verification = gbdt_adapter.verify_receipt(
        features,
        outcomes,
        sessions,
        scored,
        receipt,
        minimum_training_sessions=126,
        training_window_sessions=126,
        validation_sessions=63,
    )

    assert build_calls == [
        {
            "features": features,
            "outcomes": outcomes,
            "sessions": sessions,
            "minimum_training_sessions": 126,
            "training_window_sessions": 126,
            "validation_sessions": 63,
        }
    ]
    assert verify_calls == [
        {
            "features": features,
            "outcomes": outcomes,
            "sessions": sessions,
            "scored_oof": scored,
            "receipt": receipt,
            "minimum_training_sessions": 126,
            "training_window_sessions": 126,
            "validation_sessions": 63,
        }
    ]
    assert verification == {"verified": True}

