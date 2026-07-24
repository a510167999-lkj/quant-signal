"""Strict audited-PIT shallow GBDT research core."""

from __future__ import annotations

from math import ceil
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from app.audited_pit_continuous_ridge_oof import FEATURE_NAMES


NUM_BOOST_ROUND = 200
FROZEN_XGBOOST_PARAMS: Mapping[str, Any] = MappingProxyType(
    {
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
)


def _float64_vector(
    values: Sequence[float] | np.ndarray,
    *,
    label: str,
) -> np.ndarray:
    try:
        vector = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if vector.ndim != 1:
        raise ValueError(f"{label} must be one-dimensional")
    if not np.isfinite(vector).all():
        raise ValueError(f"{label} must be finite")
    return np.ascontiguousarray(vector)


def clip_training_net_returns(
    net_returns: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, float, int]:
    values = _float64_vector(net_returns, label="training returns")
    if values.size == 0:
        raise ValueError("training returns are empty")
    cutoff_index = ceil(0.99 * int(values.size)) - 1
    cutoff = float(np.sort(np.abs(values))[cutoff_index])
    clipped = np.clip(values, -cutoff, cutoff)
    return np.ascontiguousarray(clipped), cutoff, cutoff_index


def build_daily_utility_targets(
    clipped_returns: Sequence[float] | np.ndarray,
    signal_dates: Sequence[str] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    returns = _float64_vector(clipped_returns, label="clipped returns")
    dates = np.asarray(signal_dates, dtype=object)
    if dates.ndim != 1 or len(dates) != len(returns):
        raise ValueError("signal dates must align with clipped returns")
    if len(dates) == 0:
        raise ValueError("utility targets are empty")
    normalized_dates = np.asarray([str(value) for value in dates], dtype=object)
    if any(not value for value in normalized_dates):
        raise ValueError("signal dates must be non-empty")

    absolute_returns = np.abs(returns)
    sums_by_date: dict[str, float] = {}
    for signal_date, absolute_return in zip(
        normalized_dates,
        absolute_returns,
        strict=True,
    ):
        sums_by_date[signal_date] = (
            sums_by_date.get(signal_date, 0.0) + float(absolute_return)
        )
    if any(
        not np.isfinite(value) or value <= 0.0
        for value in sums_by_date.values()
    ):
        raise ValueError("daily absolute sum must be positive and finite")

    daily_abs_sums = np.asarray(
        [sums_by_date[value] for value in normalized_dates],
        dtype=np.float64,
    )
    labels = (returns > 0.0).astype(np.float64)
    weights = absolute_returns / daily_abs_sums
    return (
        np.ascontiguousarray(labels),
        np.ascontiguousarray(weights),
        np.ascontiguousarray(daily_abs_sums),
    )


def prepare_feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("feature input must be a DataFrame")
    if any(list(frame.columns).count(name) != 1 for name in FEATURE_NAMES):
        raise ValueError("feature columns are missing or duplicated")
    try:
        matrix = frame.loc[:, list(FEATURE_NAMES)].to_numpy(
            dtype=np.float64,
            copy=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("feature values must be numeric") from exc
    matrix = np.ascontiguousarray(matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_NAMES):
        raise ValueError("feature matrix shape is invalid")
    if not np.isfinite(matrix).all():
        raise ValueError("feature values must be finite")
    return matrix


def positive_utility_mask(
    probabilities: Sequence[float] | np.ndarray,
) -> np.ndarray:
    values = _float64_vector(probabilities, label="probabilities")
    if ((values < 0.0) | (values > 1.0)).any():
        raise ValueError("probabilities must be within [0, 1]")
    return np.ascontiguousarray(values > 0.5)
