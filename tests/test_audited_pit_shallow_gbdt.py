from __future__ import annotations

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
    positive_utility_mask,
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
