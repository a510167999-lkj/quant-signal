from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from app.audited_pit_shallow_gbdt import (
    FEATURE_NAMES,
    FROZEN_XGBOOST_PARAMS,
    NUM_BOOST_ROUND,
    build_daily_utility_targets,
    clip_training_net_returns,
    fit_shallow_gbdt_fold,
    positive_utility_mask,
    predict_shallow_gbdt_fold,
    prepare_feature_matrix,
)


EXPECTED_FEATURE_NAMES = (
    "amount_level_20_rank",
    "amount_volatility_20_rank",
    "amount_surge_5_to_60_rank",
    "amihud_20_rank",
    "realized_volatility_20_pct_rank",
    "max_return_20_pct_rank",
    "signal_return_1d_pct_rank",
    "reversal_20_skip5_pct_rank",
    "cross_section_above_ma20_fraction",
    "cross_section_median_return_5d_pct",
)


def _array_sha256(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(list(array.shape), separators=(",", ":")).encode("ascii")
    )
    digest.update(b"\0")
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _fold_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    generator = np.random.default_rng(20260724)
    matrix = np.ascontiguousarray(
        generator.normal(size=(64, len(EXPECTED_FEATURE_NAMES))),
        dtype=np.float64,
    )
    labels = (
        matrix[:, 0] * matrix[:, 1] + matrix[:, 2] - 0.25 * matrix[:, 3] > 0.0
    ).astype(np.float64)
    weights = np.ascontiguousarray(
        np.linspace(1.0, 2.0, len(matrix), dtype=np.float64)
    )
    weights /= weights.sum()
    return matrix, labels, weights


def _expected_dmatrix_contract(row_count: int) -> dict[str, object]:
    return {
        "shape": [row_count, len(EXPECTED_FEATURE_NAMES)],
        "dtype": "float64",
        "c_contiguous": True,
        "feature_names": list(EXPECTED_FEATURE_NAMES),
        "feature_types": ["float"] * len(EXPECTED_FEATURE_NAMES),
        "nthread": 1,
        "missing": "NaN",
        "enable_categorical": False,
    }


def test_p99_uses_zero_based_nearest_rank_and_clips_symmetrically() -> None:
    net_returns = np.arange(1.0, 101.0, dtype=np.float64)
    net_returns[::2] *= -1.0
    original = net_returns.copy()

    clipped, cutoff, cutoff_index = clip_training_net_returns(net_returns)

    assert cutoff_index == math.ceil(0.99 * len(net_returns)) - 1 == 98
    assert cutoff == 99.0
    assert clipped[-1] == 99.0
    assert clipped[-2] == -99.0
    np.testing.assert_array_equal(net_returns, original)


def test_daily_utility_targets_are_date_balanced_and_preserve_inputs() -> None:
    clipped_returns = np.array([2.0, -1.0, 0.0, 4.0, -2.0], dtype=np.float64)
    signal_dates = np.array(
        ["2026-01-05", "2026-01-05", "2026-01-05", "2026-01-06", "2026-01-06"],
        dtype=object,
    )
    original_returns = clipped_returns.copy()
    original_dates = signal_dates.copy()

    labels, weights, daily_abs_sums = build_daily_utility_targets(
        clipped_returns,
        signal_dates,
    )

    np.testing.assert_array_equal(labels, np.array([1, 0, 0, 1, 0], dtype=np.float64))
    np.testing.assert_allclose(weights, np.array([2 / 3, 1 / 3, 0, 2 / 3, 1 / 3]))
    np.testing.assert_array_equal(daily_abs_sums, np.array([3.0, 3.0, 3.0, 6.0, 6.0]))
    assert weights[signal_dates == "2026-01-05"].sum() == pytest.approx(1.0)
    assert weights[signal_dates == "2026-01-06"].sum() == pytest.approx(1.0)
    assert weights[2] == 0.0
    np.testing.assert_array_equal(clipped_returns, original_returns)
    np.testing.assert_array_equal(signal_dates, original_dates)


def test_empty_training_returns_fail_closed() -> None:
    with pytest.raises(ValueError, match="empty"):
        clip_training_net_returns(np.array([], dtype=np.float64))


def test_zero_daily_absolute_sum_fails_closed() -> None:
    with pytest.raises(ValueError, match="absolute sum"):
        build_daily_utility_targets(
            np.array([1.0, -1.0, 0.0, 0.0], dtype=np.float64),
            np.array(
                ["2026-01-05", "2026-01-05", "2026-01-06", "2026-01-06"],
                dtype=object,
            ),
        )


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_training_returns_fail_closed(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        clip_training_net_returns(np.array([1.0, invalid], dtype=np.float64))


def test_feature_matrix_is_float64_contiguous_and_uses_frozen_column_order() -> None:
    assert FEATURE_NAMES == EXPECTED_FEATURE_NAMES
    frame = pd.DataFrame(
        {
            name: np.array([index + 0.25, index + 0.75], dtype=np.float64)
            for index, name in enumerate(reversed(EXPECTED_FEATURE_NAMES))
        }
    )
    original = frame.copy(deep=True)

    matrix = prepare_feature_matrix(frame)

    assert matrix.dtype == np.dtype(np.float64)
    assert matrix.flags.c_contiguous
    assert matrix.shape == (2, 10)
    for index, name in enumerate(EXPECTED_FEATURE_NAMES):
        np.testing.assert_array_equal(matrix[:, index], frame[name].to_numpy())
    pd.testing.assert_frame_equal(frame, original)


def test_feature_matrix_missing_column_and_nonfinite_value_fail_closed() -> None:
    complete = pd.DataFrame(
        {
            name: np.array([index + 0.25], dtype=np.float64)
            for index, name in enumerate(EXPECTED_FEATURE_NAMES)
        }
    )

    with pytest.raises(ValueError, match="feature"):
        prepare_feature_matrix(complete.drop(columns=[EXPECTED_FEATURE_NAMES[0]]))

    with_nan = complete.copy(deep=True)
    with_nan.loc[0, EXPECTED_FEATURE_NAMES[-1]] = np.nan
    with pytest.raises(ValueError, match="finite"):
        prepare_feature_matrix(with_nan)


def test_positive_utility_mask_uses_strict_half_probability_threshold() -> None:
    below = np.nextafter(np.float64(0.5), np.float64(0.0))
    above = np.nextafter(np.float64(0.5), np.float64(1.0))

    mask = positive_utility_mask(
        np.array([0.0, below, 0.5, above, 1.0], dtype=np.float64)
    )

    np.testing.assert_array_equal(mask, np.array([False, False, False, True, True]))


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf, -0.01, 1.01])
def test_probability_gate_rejects_nonfinite_and_out_of_range_values(
    invalid: float,
) -> None:
    with pytest.raises(ValueError, match="probabil"):
        positive_utility_mask(np.array([invalid], dtype=np.float64))


def test_xgboost_parameters_and_round_count_are_frozen() -> None:
    assert FROZEN_XGBOOST_PARAMS == {
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
    }
    assert NUM_BOOST_ROUND == 200


def test_fold_fit_receipt_freezes_runtime_parameters_and_dmatrix_contract() -> None:
    matrix, labels, weights = _fold_fixture()
    original_matrix = matrix.copy()
    original_labels = labels.copy()
    original_weights = weights.copy()

    booster, receipt = fit_shallow_gbdt_fold(matrix, labels, weights)

    assert receipt["schema_version"] == "audited-pit-shallow-gbdt-fit-receipt/v1"
    assert receipt["runtime"]["xgboost_version"] == "3.2.0"
    assert isinstance(receipt["runtime"]["xgboost_build_info"], dict)
    assert receipt["runtime"]["xgboost_build_info"]
    assert receipt["parameters"] == FROZEN_XGBOOST_PARAMS
    assert receipt["num_boost_round"] == NUM_BOOST_ROUND == 200
    assert receipt["dmatrix"] == _expected_dmatrix_contract(len(matrix))
    model_json = bytes(booster.save_raw(raw_format="json"))
    assert receipt["model_json_sha256"] == hashlib.sha256(model_json).hexdigest()
    np.testing.assert_array_equal(matrix, original_matrix)
    np.testing.assert_array_equal(labels, original_labels)
    np.testing.assert_array_equal(weights, original_weights)


@pytest.mark.parametrize(
    ("labels", "weights", "match"),
    [
        (
            np.array([0.0, 1.0], dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "shape",
        ),
        (
            np.zeros(64, dtype=np.float64),
            np.array([1.0], dtype=np.float64),
            "shape",
        ),
        (
            np.full((64, 1), 1.0, dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "shape",
        ),
        (
            np.full(64, np.nan, dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "finite",
        ),
        (
            np.full(64, np.inf, dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "finite",
        ),
        (
            np.full(64, -0.01, dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "label",
        ),
        (
            np.full(64, 1.01, dtype=np.float64),
            np.ones(64, dtype=np.float64),
            "label",
        ),
        (
            np.zeros(64, dtype=np.float64),
            np.full(64, np.nan, dtype=np.float64),
            "finite",
        ),
        (
            np.zeros(64, dtype=np.float64),
            np.full(64, np.inf, dtype=np.float64),
            "finite",
        ),
        (
            np.zeros(64, dtype=np.float64),
            np.full(64, -0.01, dtype=np.float64),
            "weight",
        ),
        (
            np.zeros(64, dtype=np.float64),
            np.zeros(64, dtype=np.float64),
            "positive",
        ),
    ],
)
def test_fold_fit_rejects_invalid_labels_and_weights(
    labels: np.ndarray,
    weights: np.ndarray,
    match: str,
) -> None:
    matrix, _, _ = _fold_fixture()

    with pytest.raises(ValueError, match=match):
        fit_shallow_gbdt_fold(matrix, labels, weights)


def test_fold_fit_is_deterministic_and_prediction_receipt_binds_both_outputs() -> None:
    matrix, labels, weights = _fold_fixture()

    booster_a, fit_receipt_a = fit_shallow_gbdt_fold(matrix, labels, weights)
    booster_b, fit_receipt_b = fit_shallow_gbdt_fold(matrix, labels, weights)
    probabilities_a, prediction_receipt_a = predict_shallow_gbdt_fold(
        booster_a,
        matrix,
    )
    probabilities_b, prediction_receipt_b = predict_shallow_gbdt_fold(
        booster_b,
        matrix,
    )

    assert fit_receipt_a["model_json_sha256"] == fit_receipt_b["model_json_sha256"]
    np.testing.assert_array_equal(probabilities_a, probabilities_b)
    assert prediction_receipt_a == prediction_receipt_b
    assert (
        prediction_receipt_a["schema_version"]
        == "audited-pit-shallow-gbdt-prediction-receipt/v1"
    )
    assert prediction_receipt_a["dmatrix"] == _expected_dmatrix_contract(len(matrix))
    assert (
        prediction_receipt_a["model_json_sha256"]
        == fit_receipt_a["model_json_sha256"]
    )

    raw_margins = np.asarray(
        booster_a.inplace_predict(matrix, predict_type="margin"),
    )
    expected_probabilities = 1.0 / (1.0 + np.exp(-raw_margins))
    np.testing.assert_allclose(
        probabilities_a,
        expected_probabilities,
        rtol=0.0,
        atol=np.finfo(expected_probabilities.dtype).eps,
    )
    assert prediction_receipt_a["raw_margin_sha256"] == _array_sha256(raw_margins)
    assert prediction_receipt_a["probability_sha256"] == _array_sha256(
        probabilities_a
    )
    assert prediction_receipt_a["row_count"] == len(matrix)


def test_fold_prediction_preserves_input_and_rejects_nonfinite_matrix() -> None:
    matrix, labels, weights = _fold_fixture()
    booster, _ = fit_shallow_gbdt_fold(matrix, labels, weights)
    original = matrix.copy()

    predict_shallow_gbdt_fold(booster, matrix)

    np.testing.assert_array_equal(matrix, original)
    invalid = matrix.copy()
    invalid[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        predict_shallow_gbdt_fold(booster, invalid)
