"""Strict audited-PIT shallow GBDT research core."""

from __future__ import annotations

import hashlib
import json
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
_XGBOOST_VERSION = "3.2.0"


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
        raise ValueError(f"{label} shape must be one-dimensional")
    if not np.isfinite(vector).all():
        raise ValueError(f"{label} must be finite")
    return np.ascontiguousarray(vector)


def _float64_feature_matrix(values: np.ndarray) -> np.ndarray:
    try:
        matrix = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("feature matrix must be numeric") from exc
    if matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_NAMES):
        raise ValueError("feature matrix shape is invalid")
    if not np.isfinite(matrix).all():
        raise ValueError("feature matrix must be finite")
    return np.ascontiguousarray(matrix)


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


def _model_json(booster: Any) -> bytes:
    return bytes(booster.save_raw(raw_format="json"))


def _dmatrix_contract(row_count: int) -> dict[str, Any]:
    return {
        "shape": [row_count, len(FEATURE_NAMES)],
        "dtype": "float64",
        "c_contiguous": True,
        "feature_names": list(FEATURE_NAMES),
        "feature_types": ["float"] * len(FEATURE_NAMES),
        "nthread": 1,
        "missing": "NaN",
        "enable_categorical": False,
    }


def _xgboost_runtime() -> tuple[Any, dict[str, Any]]:
    import xgboost as xgb

    if xgb.__version__ != _XGBOOST_VERSION:
        raise RuntimeError(
            f"xgboost version must be {_XGBOOST_VERSION}, got {xgb.__version__}"
        )
    build_info = xgb.build_info()
    if not isinstance(build_info, dict) or not build_info:
        raise RuntimeError("xgboost build_info must be a non-empty mapping")
    return xgb, {
        "xgboost_version": xgb.__version__,
        "xgboost_build_info": build_info,
    }


def _make_dmatrix(
    xgb: Any,
    matrix: np.ndarray,
    *,
    labels: np.ndarray | None = None,
    weights: np.ndarray | None = None,
) -> Any:
    return xgb.DMatrix(
        data=matrix,
        label=labels,
        weight=weights,
        feature_names=list(FEATURE_NAMES),
        feature_types=["float"] * len(FEATURE_NAMES),
        nthread=1,
        missing=np.nan,
        enable_categorical=False,
    )


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


def fit_shallow_gbdt_fold(
    features: np.ndarray,
    labels: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
) -> tuple[Any, dict[str, Any]]:
    matrix = _float64_feature_matrix(features)
    label_values = _float64_vector(labels, label="labels")
    weight_values = _float64_vector(weights, label="weights")
    if len(label_values) != len(matrix) or len(weight_values) != len(matrix):
        raise ValueError("labels and weights shape must match feature matrix")
    if not np.isin(label_values, (0.0, 1.0)).all():
        raise ValueError("labels must contain only binary label values")
    if (weight_values < 0.0).any():
        raise ValueError("weights must be non-negative")
    total_weight = float(weight_values.sum(dtype=np.float64))
    if not np.isfinite(total_weight) or total_weight <= 0.0:
        raise ValueError("weights must have a positive finite sum")

    xgb, runtime = _xgboost_runtime()
    dmatrix = _make_dmatrix(
        xgb,
        matrix,
        labels=label_values,
        weights=weight_values,
    )
    booster = xgb.train(
        params=dict(FROZEN_XGBOOST_PARAMS),
        dtrain=dmatrix,
        num_boost_round=NUM_BOOST_ROUND,
    )
    model_json = _model_json(booster)
    model_config = booster.save_config().encode("utf-8")
    receipt = {
        "schema_version": "audited-pit-shallow-gbdt-fit-receipt/v1",
        "runtime": runtime,
        "parameters": dict(FROZEN_XGBOOST_PARAMS),
        "num_boost_round": NUM_BOOST_ROUND,
        "dmatrix": _dmatrix_contract(len(matrix)),
        "inputs": {
            "features_sha256": _array_sha256(matrix),
            "labels_sha256": _array_sha256(label_values),
            "weights_sha256": _array_sha256(weight_values),
            "weight_sum": total_weight,
        },
        "model_json_sha256": hashlib.sha256(model_json).hexdigest(),
        "model_config_sha256": hashlib.sha256(model_config).hexdigest(),
    }
    return booster, receipt


def predict_shallow_gbdt_fold(
    booster: Any,
    features: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    matrix = _float64_feature_matrix(features)
    xgb, runtime = _xgboost_runtime()
    dmatrix = _make_dmatrix(xgb, matrix)
    raw_margins = np.ascontiguousarray(
        booster.predict(dmatrix, output_margin=True)
    )
    probabilities = np.ascontiguousarray(booster.predict(dmatrix))
    if (
        raw_margins.ndim != 1
        or probabilities.ndim != 1
        or len(raw_margins) != len(matrix)
        or len(probabilities) != len(matrix)
        or not np.isfinite(raw_margins).all()
        or not np.isfinite(probabilities).all()
        or ((probabilities < 0.0) | (probabilities > 1.0)).any()
    ):
        raise RuntimeError("xgboost prediction output is invalid")
    expected_probabilities = 1.0 / (1.0 + np.exp(-raw_margins))
    if not np.allclose(
        probabilities,
        expected_probabilities,
        rtol=0.0,
        atol=np.finfo(expected_probabilities.dtype).eps,
    ):
        raise RuntimeError("xgboost probabilities do not match raw-margin sigmoid")

    model_json = _model_json(booster)
    receipt = {
        "schema_version": "audited-pit-shallow-gbdt-prediction-receipt/v1",
        "runtime": runtime,
        "dmatrix": _dmatrix_contract(len(matrix)),
        "model_json_sha256": hashlib.sha256(model_json).hexdigest(),
        "features_sha256": _array_sha256(matrix),
        "raw_margin_sha256": _array_sha256(raw_margins),
        "probability_sha256": _array_sha256(probabilities),
        "row_count": len(matrix),
    }
    return probabilities, receipt
