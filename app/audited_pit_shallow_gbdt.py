"""Strict audited-PIT shallow GBDT research core."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from math import ceil
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from app.audited_pit_continuous_ridge_oof import (
    FEATURE_NAMES,
    ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
)
from app.audited_pit_score_contract import (
    SHALLOW_GBDT_SCORE_CONTRACT,
)


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
_FRICTION_PERCENTAGE_POINTS = 0.45
_PROBABILITY_COLUMN = "predicted_positive_utility_probability"


SHALLOW_GBDT_OOF_SPEC = deepcopy(ROLLING_CONTINUOUS_RIDGE_OOF_SPEC)
SHALLOW_GBDT_OOF_SPEC.update(
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
            "parameters": dict(FROZEN_XGBOOST_PARAMS),
            "num_boost_round": NUM_BOOST_ROUND,
            "xgboost_version": _XGBOOST_VERSION,
            "feature_input_dtype": "float64",
            "feature_input_layout": "C_contiguous",
            "feature_types": ["float"] * len(FEATURE_NAMES),
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
_SHALLOW_GBDT_OOF_SPEC_SHA256 = (
    "53d00badc8683670ef3d6c02307697e2c3ef8ec769b9d8da072d36420cea70ac"
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


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _model_json(booster: Any) -> bytes:
    return bytes(booster.save_raw(raw_format="json"))


def _model_raw(booster: Any) -> bytes:
    return bytes(booster.save_raw())


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
    model_raw = _model_raw(booster)
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
        "model_raw_sha256": hashlib.sha256(model_raw).hexdigest(),
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
    model_raw = _model_raw(booster)
    receipt = {
        "schema_version": "audited-pit-shallow-gbdt-prediction-receipt/v1",
        "runtime": runtime,
        "dmatrix": _dmatrix_contract(len(matrix)),
        "model_json_sha256": hashlib.sha256(model_json).hexdigest(),
        "model_raw_sha256": hashlib.sha256(model_raw).hexdigest(),
        "features_sha256": _array_sha256(matrix),
        "raw_margin_sha256": _array_sha256(raw_margins),
        "probability_sha256": _array_sha256(probabilities),
        "row_count": len(matrix),
    }
    return probabilities, receipt


def _ordered_signal_sessions(sessions: Sequence[str]) -> list[str]:
    values = [str(value) for value in sessions]
    try:
        parsed = [date.fromisoformat(value) for value in values]
    except ValueError as exc:
        raise ValueError("signal sessions must use ISO dates") from exc
    if (
        not values
        or values != sorted(values)
        or len(values) != len(set(values))
        or [value.isoformat() for value in parsed] != values
    ):
        raise ValueError("signal sessions must be ordered and unique")
    return values


def _rolling_oof_inputs(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int,
    training_window_sessions: int,
    validation_sessions: int,
) -> tuple[
    list[str],
    pd.DataFrame,
    dict[str, dict[str, Any]],
]:
    session_dates = _ordered_signal_sessions(sessions)
    if (
        minimum_training_sessions <= 0
        or training_window_sessions <= 0
        or training_window_sessions > minimum_training_sessions
        or validation_sessions <= 0
        or len(session_dates) <= minimum_training_sessions
    ):
        raise ValueError("shallow GBDT rolling window lengths are invalid")
    required = {"candidate_key", "signal_date", *FEATURE_NAMES}
    if not isinstance(features, pd.DataFrame) or not required.issubset(
        features.columns
    ):
        raise ValueError("shallow GBDT rolling OOF features are incomplete")
    rows = features.loc[
        :,
        ["candidate_key", "signal_date", *FEATURE_NAMES],
    ].copy()
    if (
        rows["candidate_key"].isna().any()
        or rows["signal_date"].isna().any()
    ):
        raise ValueError("shallow GBDT rolling OOF feature keys are invalid")
    rows["candidate_key"] = rows["candidate_key"].astype(str)
    rows["signal_date"] = rows["signal_date"].astype(str)
    if (
        rows.empty
        or rows["candidate_key"].eq("").any()
        or rows["candidate_key"].duplicated().any()
        or not rows["signal_date"].isin(session_dates).all()
    ):
        raise ValueError("shallow GBDT rolling OOF feature keys are invalid")
    _float64_feature_matrix(
        rows.loc[:, list(FEATURE_NAMES)].to_numpy(dtype=np.float64)
    )
    feature_dates = dict(zip(rows["candidate_key"], rows["signal_date"]))
    session_set = set(session_dates)
    outcome_lookup: dict[str, dict[str, Any]] = {}
    for raw_outcome in outcomes:
        key = str(raw_outcome.get("candidate_key") or "")
        if (
            not key
            or key in outcome_lookup
            or key not in feature_dates
        ):
            raise ValueError(
                "shallow GBDT rolling OOF outcomes have invalid keys"
            )
        right_censored = raw_outcome.get("right_censored", False)
        if type(right_censored) is not bool:
            raise ValueError(
                "shallow GBDT rolling OOF right_censored must be boolean"
            )
        outcome = {
            "candidate_key": key,
            "right_censored": right_censored,
        }
        if right_censored is not True:
            exit_date = str(raw_outcome.get("exit_date") or "")
            try:
                gross_return = float(raw_outcome.get("return_pct"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "shallow GBDT rolling OOF outcome return is invalid"
                ) from exc
            if (
                exit_date not in session_set
                or exit_date <= feature_dates[key]
                or not np.isfinite(gross_return)
            ):
                raise ValueError(
                    "shallow GBDT rolling OOF outcome boundary is invalid"
                )
            outcome["exit_date"] = exit_date
            outcome["return_pct"] = gross_return
        outcome_lookup[key] = outcome
    return session_dates, rows, outcome_lookup


def _rolling_fold_members(
    rows: pd.DataFrame,
    outcome_lookup: Mapping[str, Mapping[str, Any]],
    training_window: Sequence[str],
    validation_start: str,
) -> dict[str, Any]:
    window_set = set(training_window)
    training_records: list[dict[str, Any]] = []
    window_keys: list[str] = []
    right_censored_keys: list[str] = []
    incomplete_keys: list[str] = []
    eligible_complete_keys: list[str] = []
    immature_keys: list[str] = []
    for row in rows.itertuples(index=False):
        signal_date = str(row.signal_date)
        if signal_date not in window_set:
            continue
        key = str(row.candidate_key)
        window_keys.append(key)
        outcome = outcome_lookup.get(key)
        if outcome is None:
            incomplete_keys.append(key)
            continue
        if outcome.get("right_censored") is True:
            right_censored_keys.append(key)
            continue
        eligible_complete_keys.append(key)
        exit_date = str(outcome["exit_date"])
        if exit_date >= validation_start:
            immature_keys.append(key)
            continue
        training_records.append(
            {
                "candidate_key": key,
                "signal_date": signal_date,
                "exit_date": exit_date,
                "net_return": (
                    float(outcome["return_pct"])
                    - _FRICTION_PERCENTAGE_POINTS
                ),
                "features": [
                    float(getattr(row, name)) for name in FEATURE_NAMES
                ],
            }
        )
    training_records.sort(
        key=lambda item: (item["signal_date"], item["candidate_key"])
    )
    for keys in (
        window_keys,
        right_censored_keys,
        incomplete_keys,
        eligible_complete_keys,
        immature_keys,
    ):
        keys.sort()
    if not training_records:
        raise ValueError(
            "shallow GBDT rolling OOF fold has no mature training rows"
        )
    return {
        "training_records": training_records,
        "window_keys": window_keys,
        "right_censored_keys": right_censored_keys,
        "incomplete_keys": incomplete_keys,
        "eligible_complete_keys": eligible_complete_keys,
        "immature_keys": immature_keys,
    }


def _fold_receipt(
    *,
    fold_index: int,
    validation_start: str,
    validation_end: str,
    training_window: Sequence[str],
    members: Mapping[str, Any],
    clipped_returns: np.ndarray,
    cutoff: float,
    cutoff_index: int,
    labels: np.ndarray,
    weights: np.ndarray,
    daily_abs_sums: np.ndarray,
    fit_receipt: Mapping[str, Any],
    predict_receipt: Mapping[str, Any],
    validation: pd.DataFrame,
    validation_signal_sessions: Sequence[str],
    score_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    training_records = list(members["training_records"])
    validation_keys = validation["candidate_key"].astype(str).tolist()
    observed_validation_signal_sessions = sorted(
        validation["signal_date"].astype(str).unique().tolist()
    )
    receipt = {
        "fold": fold_index,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "training_window_type": "trailing_frozen_signal_sessions",
        "training_window_session_count": len(training_window),
        "training_window_start": training_window[0],
        "training_window_end": training_window[-1],
        "training_window_sessions_sha256": _canonical_sha256(
            list(training_window)
        ),
        "window_candidate_count": len(members["window_keys"]),
        "window_candidate_keys_sha256": _canonical_sha256(
            members["window_keys"]
        ),
        "right_censored_window_candidate_count": len(
            members["right_censored_keys"]
        ),
        "right_censored_window_candidate_keys_sha256": _canonical_sha256(
            members["right_censored_keys"]
        ),
        "incomplete_window_candidate_count": len(
            members["incomplete_keys"]
        ),
        "incomplete_window_candidate_keys_sha256": _canonical_sha256(
            members["incomplete_keys"]
        ),
        "eligible_complete_window_candidate_count": len(
            members["eligible_complete_keys"]
        ),
        "eligible_complete_window_candidate_keys_sha256": (
            _canonical_sha256(members["eligible_complete_keys"])
        ),
        "purged_immature_candidate_count": len(members["immature_keys"]),
        "purged_immature_candidate_keys_sha256": _canonical_sha256(
            members["immature_keys"]
        ),
        "training_candidate_count": len(training_records),
        "training_signal_date_count": len(
            {item["signal_date"] for item in training_records}
        ),
        "training_last_exit_date": max(
            item["exit_date"] for item in training_records
        ),
        "training_candidate_keys_sha256": _canonical_sha256(
            [item["candidate_key"] for item in training_records]
        ),
        "training_rows_sha256": _canonical_sha256(training_records),
        "training_label": (
            "gross_return_pct_minus_0.45_then_fold_p99_symmetric_clip"
        ),
        "clip": {
            "method": (
                "absolute_p99_zero_based_nearest_rank_symmetric"
            ),
            "cutoff": cutoff,
            "cutoff_index": cutoff_index,
            "clipped_net_returns_sha256": _array_sha256(clipped_returns),
        },
        "targets": {
            "label": "positive_clipped_net_return",
            "weight": (
                "abs_clipped_return_over_same_signal_date_abs_sum"
            ),
            "labels_sha256": _array_sha256(labels),
            "weights_sha256": _array_sha256(weights),
            "daily_abs_sums_sha256": _array_sha256(daily_abs_sums),
        },
        "fit_receipt": dict(fit_receipt),
        "predict_receipt": dict(predict_receipt),
        "validation_candidate_count": len(validation),
        "validation_signal_date_count": int(
            validation["signal_date"].nunique()
        ),
        "validation_signal_sessions": list(validation_signal_sessions),
        "validation_signal_sessions_sha256": _canonical_sha256(
            list(validation_signal_sessions)
        ),
        "observed_validation_signal_sessions": (
            observed_validation_signal_sessions
        ),
        "observed_validation_signal_sessions_sha256": _canonical_sha256(
            observed_validation_signal_sessions
        ),
        "validation_candidate_keys_sha256": _canonical_sha256(
            validation_keys
        ),
        "score_rows_sha256": _canonical_sha256(list(score_rows)),
    }
    positive_rows = [
        dict(row)
        for row in score_rows
        if float(row[_PROBABILITY_COLUMN]) > 0.5
    ]
    receipt.update(
        {
            "positive_utility_candidate_count": len(positive_rows),
            "positive_utility_candidate_keys_sha256": _canonical_sha256(
                [row["candidate_key"] for row in positive_rows]
            ),
            "positive_utility_rows_sha256": _canonical_sha256(
                positive_rows
            ),
        }
    )
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def _compute_shallow_gbdt_rolling_oof(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int,
    training_window_sessions: int,
    validation_sessions: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates, rows, outcome_lookup = _rolling_oof_inputs(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=minimum_training_sessions,
        training_window_sessions=training_window_sessions,
        validation_sessions=validation_sessions,
    )
    scored_frames: list[pd.DataFrame] = []
    fold_receipts: list[dict[str, Any]] = []
    for fold_index, start_position in enumerate(
        range(
            minimum_training_sessions,
            len(session_dates),
            validation_sessions,
        ),
        start=1,
    ):
        validation_start = session_dates[start_position]
        validation_end = session_dates[
            min(
                start_position + validation_sessions - 1,
                len(session_dates) - 1,
            )
        ]
        validation_signal_sessions = session_dates[
            start_position : min(
                start_position + validation_sessions,
                len(session_dates),
            )
        ]
        validation = rows[
            rows["signal_date"].between(
                validation_start,
                validation_end,
                inclusive="both",
            )
        ].sort_values(["signal_date", "candidate_key"], kind="mergesort")
        if validation.empty:
            raise ValueError(
                "shallow GBDT rolling OOF fold has no validation rows"
            )
        training_window = session_dates[
            start_position - training_window_sessions : start_position
        ]
        if len(training_window) != training_window_sessions:
            raise ValueError(
                "shallow GBDT rolling OOF training window is incomplete"
            )
        members = _rolling_fold_members(
            rows,
            outcome_lookup,
            training_window,
            validation_start,
        )
        training_records = members["training_records"]
        x_train = np.ascontiguousarray(
            [item["features"] for item in training_records],
            dtype=np.float64,
        )
        raw_returns = np.ascontiguousarray(
            [item["net_return"] for item in training_records],
            dtype=np.float64,
        )
        clipped, cutoff, cutoff_index = clip_training_net_returns(
            raw_returns
        )
        labels, weights, daily_abs_sums = build_daily_utility_targets(
            clipped,
            [item["signal_date"] for item in training_records],
        )
        booster, fit_receipt = fit_shallow_gbdt_fold(
            x_train,
            labels,
            weights,
        )
        validation_matrix = np.ascontiguousarray(
            validation.loc[:, list(FEATURE_NAMES)].to_numpy(
                dtype=np.float64
            )
        )
        probabilities, predict_receipt = predict_shallow_gbdt_fold(
            booster,
            validation_matrix,
        )
        scored = validation.loc[:, ["candidate_key", "signal_date"]].copy()
        scored[_PROBABILITY_COLUMN] = probabilities
        scored_frames.append(scored)
        score_rows = [
            {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                _PROBABILITY_COLUMN: float(probability),
            }
            for row, probability in zip(
                validation.itertuples(index=False),
                probabilities,
                strict=True,
            )
        ]
        fold_receipts.append(
            _fold_receipt(
                fold_index=fold_index,
                validation_start=validation_start,
                validation_end=validation_end,
                training_window=training_window,
                members=members,
                clipped_returns=clipped,
                cutoff=cutoff,
                cutoff_index=cutoff_index,
                labels=labels,
                weights=weights,
                daily_abs_sums=daily_abs_sums,
                fit_receipt=fit_receipt,
                predict_receipt=predict_receipt,
                validation=validation,
                validation_signal_sessions=validation_signal_sessions,
                score_rows=score_rows,
            )
        )
    if not scored_frames:
        raise ValueError("shallow GBDT rolling OOF produced no scores")
    scored_oof = pd.concat(scored_frames, ignore_index=True).sort_values(
        ["signal_date", "candidate_key"],
        kind="mergesort",
    ).reset_index(drop=True)
    if scored_oof["candidate_key"].duplicated().any():
        raise ValueError(
            "shallow GBDT rolling OOF keys overlap across folds"
        )
    score_payload = [
        {
            "candidate_key": str(row.candidate_key),
            "signal_date": str(row.signal_date),
            _PROBABILITY_COLUMN: float(
                getattr(row, _PROBABILITY_COLUMN)
            ),
        }
        for row in scored_oof.itertuples(index=False)
    ]
    positive_payload = [
        row
        for row in score_payload
        if float(row[_PROBABILITY_COLUMN]) > 0.5
    ]
    receipt = {
        "schema_version": (
            "audited-pit-shallow-gbdt-rolling-oof-receipt/v1"
        ),
        "minimum_training_sessions": minimum_training_sessions,
        "training_window_sessions": training_window_sessions,
        "validation_sessions": validation_sessions,
        "friction_percentage_points": _FRICTION_PERCENTAGE_POINTS,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "probability_column": _PROBABILITY_COLUMN,
        "probability_gate": "strictly_greater_than_0.5",
        "fold_count": len(fold_receipts),
        "folds": fold_receipts,
        "folds_sha256": _canonical_sha256(fold_receipts),
        "oof_candidate_count": len(scored_oof),
        "oof_candidate_keys_sha256": _canonical_sha256(
            scored_oof["candidate_key"].astype(str).tolist()
        ),
        "oof_scores_sha256": _canonical_sha256(score_payload),
        "positive_utility_candidate_count": len(positive_payload),
        "positive_utility_candidate_keys_sha256": _canonical_sha256(
            [row["candidate_key"] for row in positive_payload]
        ),
        "positive_utility_rows_sha256": _canonical_sha256(
            positive_payload
        ),
        "frozen_signal_sessions": session_dates,
        "frozen_signal_sessions_sha256": _canonical_sha256(
            session_dates
        ),
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return scored_oof, receipt


def build_shallow_gbdt_rolling_oof_scores(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    training_window_sessions: int = 126,
    validation_sessions: int = 63,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _compute_shallow_gbdt_rolling_oof(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=minimum_training_sessions,
        training_window_sessions=training_window_sessions,
        validation_sessions=validation_sessions,
    )


def _independent_xgboost_fold_replay(
    x_train: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    x_validation: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    import xgboost as xgb

    if xgb.__version__ != _XGBOOST_VERSION:
        raise ValueError
    build_info = xgb.build_info()
    if not isinstance(build_info, dict) or not build_info:
        raise ValueError
    feature_names = list(FEATURE_NAMES)
    feature_types = ["float"] * len(FEATURE_NAMES)
    train_matrix = xgb.DMatrix(
        data=x_train,
        label=labels,
        weight=weights,
        feature_names=feature_names,
        feature_types=feature_types,
        nthread=1,
        missing=np.nan,
        enable_categorical=False,
    )
    booster = xgb.train(
        params=dict(FROZEN_XGBOOST_PARAMS),
        dtrain=train_matrix,
        num_boost_round=NUM_BOOST_ROUND,
    )
    model_json = bytes(booster.save_raw(raw_format="json"))
    model_raw = bytes(booster.save_raw())
    model_config = booster.save_config().encode("utf-8")
    runtime = {
        "xgboost_version": xgb.__version__,
        "xgboost_build_info": build_info,
    }
    train_contract = {
        "shape": [len(x_train), len(FEATURE_NAMES)],
        "dtype": "float64",
        "c_contiguous": True,
        "feature_names": feature_names,
        "feature_types": feature_types,
        "nthread": 1,
        "missing": "NaN",
        "enable_categorical": False,
    }
    fit_receipt = {
        "schema_version": "audited-pit-shallow-gbdt-fit-receipt/v1",
        "runtime": runtime,
        "parameters": dict(FROZEN_XGBOOST_PARAMS),
        "num_boost_round": NUM_BOOST_ROUND,
        "dmatrix": train_contract,
        "inputs": {
            "features_sha256": _array_sha256(x_train),
            "labels_sha256": _array_sha256(labels),
            "weights_sha256": _array_sha256(weights),
            "weight_sum": float(weights.sum(dtype=np.float64)),
        },
        "model_json_sha256": hashlib.sha256(model_json).hexdigest(),
        "model_raw_sha256": hashlib.sha256(model_raw).hexdigest(),
        "model_config_sha256": hashlib.sha256(
            model_config
        ).hexdigest(),
    }

    validation_matrix = xgb.DMatrix(
        data=x_validation,
        feature_names=feature_names,
        feature_types=feature_types,
        nthread=1,
        missing=np.nan,
        enable_categorical=False,
    )
    raw_margins = np.ascontiguousarray(
        booster.predict(validation_matrix, output_margin=True)
    )
    probabilities = np.ascontiguousarray(
        booster.predict(validation_matrix)
    )
    expected_probabilities = 1.0 / (1.0 + np.exp(-raw_margins))
    if (
        raw_margins.ndim != 1
        or probabilities.ndim != 1
        or len(probabilities) != len(x_validation)
        or not np.isfinite(raw_margins).all()
        or not np.isfinite(probabilities).all()
        or ((probabilities < 0.0) | (probabilities > 1.0)).any()
        or not np.allclose(
            probabilities,
            expected_probabilities,
            rtol=0.0,
            atol=np.finfo(expected_probabilities.dtype).eps,
        )
    ):
        raise ValueError
    validation_contract = {
        "shape": [len(x_validation), len(FEATURE_NAMES)],
        "dtype": "float64",
        "c_contiguous": True,
        "feature_names": feature_names,
        "feature_types": feature_types,
        "nthread": 1,
        "missing": "NaN",
        "enable_categorical": False,
    }
    predict_receipt = {
        "schema_version": (
            "audited-pit-shallow-gbdt-prediction-receipt/v1"
        ),
        "runtime": runtime,
        "dmatrix": validation_contract,
        "model_json_sha256": hashlib.sha256(model_json).hexdigest(),
        "model_raw_sha256": hashlib.sha256(model_raw).hexdigest(),
        "features_sha256": _array_sha256(x_validation),
        "raw_margin_sha256": _array_sha256(raw_margins),
        "probability_sha256": _array_sha256(probabilities),
        "row_count": len(x_validation),
    }
    return probabilities, fit_receipt, predict_receipt


def _independent_shallow_gbdt_rolling_oof_replay(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int,
    training_window_sessions: int,
    validation_sessions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_dates = _ordered_signal_sessions(sessions)
    if (
        minimum_training_sessions <= 0
        or training_window_sessions <= 0
        or training_window_sessions > minimum_training_sessions
        or validation_sessions <= 0
        or len(session_dates) <= minimum_training_sessions
    ):
        raise ValueError
    required = {"candidate_key", "signal_date", *FEATURE_NAMES}
    if not isinstance(features, pd.DataFrame) or not required.issubset(
        features.columns
    ):
        raise ValueError
    if (
        features["candidate_key"].isna().any()
        or features["signal_date"].isna().any()
    ):
        raise ValueError
    rows = features.loc[
        :,
        ["candidate_key", "signal_date", *FEATURE_NAMES],
    ].copy()
    rows["candidate_key"] = rows["candidate_key"].astype(str)
    rows["signal_date"] = rows["signal_date"].astype(str)
    if (
        rows.empty
        or rows["candidate_key"].eq("").any()
        or rows["candidate_key"].duplicated().any()
        or not rows["signal_date"].isin(session_dates).all()
    ):
        raise ValueError
    feature_matrix = np.ascontiguousarray(
        rows.loc[:, list(FEATURE_NAMES)].to_numpy(dtype=np.float64)
    )
    if (
        feature_matrix.shape != (len(rows), len(FEATURE_NAMES))
        or not np.isfinite(feature_matrix).all()
    ):
        raise ValueError
    del feature_matrix
    feature_dates = dict(zip(rows["candidate_key"], rows["signal_date"]))
    session_set = set(session_dates)
    outcome_lookup: dict[str, dict[str, Any]] = {}
    for raw_outcome in outcomes:
        key = str(raw_outcome.get("candidate_key") or "")
        right_censored = raw_outcome.get("right_censored", False)
        if (
            not key
            or key in outcome_lookup
            or key not in feature_dates
            or type(right_censored) is not bool
        ):
            raise ValueError
        outcome = {
            "candidate_key": key,
            "right_censored": right_censored,
        }
        if right_censored is False:
            exit_date = str(raw_outcome.get("exit_date") or "")
            gross_return = float(raw_outcome.get("return_pct"))
            if (
                exit_date not in session_set
                or exit_date <= feature_dates[key]
                or not np.isfinite(gross_return)
            ):
                raise ValueError
            outcome["exit_date"] = exit_date
            outcome["return_pct"] = gross_return
        outcome_lookup[key] = outcome

    fold_receipts: list[dict[str, Any]] = []
    score_payload: list[dict[str, Any]] = []
    for fold_index, start_position in enumerate(
        range(
            minimum_training_sessions,
            len(session_dates),
            validation_sessions,
        ),
        start=1,
    ):
        validation_start = session_dates[start_position]
        validation_end = session_dates[
            min(
                start_position + validation_sessions - 1,
                len(session_dates) - 1,
            )
        ]
        validation_signal_sessions = session_dates[
            start_position : min(
                start_position + validation_sessions,
                len(session_dates),
            )
        ]
        validation = rows[
            rows["signal_date"].between(
                validation_start,
                validation_end,
                inclusive="both",
            )
        ].sort_values(["signal_date", "candidate_key"], kind="mergesort")
        if validation.empty:
            raise ValueError
        training_window = session_dates[
            start_position - training_window_sessions : start_position
        ]
        if len(training_window) != training_window_sessions:
            raise ValueError
        training_window_set = set(training_window)
        window_keys: list[str] = []
        right_censored_keys: list[str] = []
        incomplete_keys: list[str] = []
        eligible_complete_keys: list[str] = []
        immature_keys: list[str] = []
        training_records: list[dict[str, Any]] = []
        for row in rows.itertuples(index=False):
            signal_date = str(row.signal_date)
            if signal_date not in training_window_set:
                continue
            key = str(row.candidate_key)
            window_keys.append(key)
            outcome = outcome_lookup.get(key)
            if outcome is None:
                incomplete_keys.append(key)
                continue
            if outcome["right_censored"] is True:
                right_censored_keys.append(key)
                continue
            eligible_complete_keys.append(key)
            exit_date = str(outcome["exit_date"])
            if exit_date >= validation_start:
                immature_keys.append(key)
                continue
            training_records.append(
                {
                    "candidate_key": key,
                    "signal_date": signal_date,
                    "exit_date": exit_date,
                    "net_return": (
                        float(outcome["return_pct"])
                        - _FRICTION_PERCENTAGE_POINTS
                    ),
                    "features": [
                        float(getattr(row, name))
                        for name in FEATURE_NAMES
                    ],
                }
            )
        training_records.sort(
            key=lambda item: (
                item["signal_date"],
                item["candidate_key"],
            )
        )
        for keys in (
            window_keys,
            right_censored_keys,
            incomplete_keys,
            eligible_complete_keys,
            immature_keys,
        ):
            keys.sort()
        if not training_records:
            raise ValueError

        x_train = np.ascontiguousarray(
            [item["features"] for item in training_records],
            dtype=np.float64,
        )
        raw_returns = np.ascontiguousarray(
            [item["net_return"] for item in training_records],
            dtype=np.float64,
        )
        cutoff_index = ceil(0.99 * len(raw_returns)) - 1
        cutoff = float(np.sort(np.abs(raw_returns))[cutoff_index])
        clipped = np.ascontiguousarray(
            np.clip(raw_returns, -cutoff, cutoff)
        )
        training_dates = [
            item["signal_date"] for item in training_records
        ]
        daily_sums_lookup: dict[str, float] = {}
        for signal_date, absolute_return in zip(
            training_dates,
            np.abs(clipped),
            strict=True,
        ):
            daily_sums_lookup[signal_date] = (
                daily_sums_lookup.get(signal_date, 0.0)
                + float(absolute_return)
            )
        if any(
            not np.isfinite(value) or value <= 0.0
            for value in daily_sums_lookup.values()
        ):
            raise ValueError
        daily_abs_sums = np.ascontiguousarray(
            [daily_sums_lookup[value] for value in training_dates],
            dtype=np.float64,
        )
        labels = np.ascontiguousarray(
            (clipped > 0.0).astype(np.float64)
        )
        weights = np.ascontiguousarray(
            np.abs(clipped) / daily_abs_sums
        )
        validation_matrix = np.ascontiguousarray(
            validation.loc[:, list(FEATURE_NAMES)].to_numpy(
                dtype=np.float64
            )
        )
        (
            probabilities,
            fit_receipt,
            predict_receipt,
        ) = _independent_xgboost_fold_replay(
            x_train,
            labels,
            weights,
            validation_matrix,
        )
        fold_score_rows = [
            {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                _PROBABILITY_COLUMN: float(probability),
            }
            for row, probability in zip(
                validation.itertuples(index=False),
                probabilities,
                strict=True,
            )
        ]
        score_payload.extend(fold_score_rows)
        validation_keys = validation["candidate_key"].astype(str).tolist()
        observed_validation_signal_sessions = sorted(
            validation["signal_date"].astype(str).unique().tolist()
        )
        positive_rows = [
            row
            for row in fold_score_rows
            if float(row[_PROBABILITY_COLUMN]) > 0.5
        ]
        fold_receipt = {
            "fold": fold_index,
            "validation_start": validation_start,
            "validation_end": validation_end,
            "training_window_type": (
                "trailing_frozen_signal_sessions"
            ),
            "training_window_session_count": len(training_window),
            "training_window_start": training_window[0],
            "training_window_end": training_window[-1],
            "training_window_sessions_sha256": _canonical_sha256(
                training_window
            ),
            "window_candidate_count": len(window_keys),
            "window_candidate_keys_sha256": _canonical_sha256(
                window_keys
            ),
            "right_censored_window_candidate_count": len(
                right_censored_keys
            ),
            "right_censored_window_candidate_keys_sha256": (
                _canonical_sha256(right_censored_keys)
            ),
            "incomplete_window_candidate_count": len(incomplete_keys),
            "incomplete_window_candidate_keys_sha256": (
                _canonical_sha256(incomplete_keys)
            ),
            "eligible_complete_window_candidate_count": len(
                eligible_complete_keys
            ),
            "eligible_complete_window_candidate_keys_sha256": (
                _canonical_sha256(eligible_complete_keys)
            ),
            "purged_immature_candidate_count": len(immature_keys),
            "purged_immature_candidate_keys_sha256": _canonical_sha256(
                immature_keys
            ),
            "training_candidate_count": len(training_records),
            "training_signal_date_count": len(set(training_dates)),
            "training_last_exit_date": max(
                item["exit_date"] for item in training_records
            ),
            "training_candidate_keys_sha256": _canonical_sha256(
                [
                    item["candidate_key"]
                    for item in training_records
                ]
            ),
            "training_rows_sha256": _canonical_sha256(
                training_records
            ),
            "training_label": (
                "gross_return_pct_minus_0.45_then_fold_p99_"
                "symmetric_clip"
            ),
            "clip": {
                "method": (
                    "absolute_p99_zero_based_nearest_rank_symmetric"
                ),
                "cutoff": cutoff,
                "cutoff_index": cutoff_index,
                "clipped_net_returns_sha256": _array_sha256(clipped),
            },
            "targets": {
                "label": "positive_clipped_net_return",
                "weight": (
                    "abs_clipped_return_over_same_signal_date_abs_sum"
                ),
                "labels_sha256": _array_sha256(labels),
                "weights_sha256": _array_sha256(weights),
                "daily_abs_sums_sha256": _array_sha256(
                    daily_abs_sums
                ),
            },
            "fit_receipt": dict(fit_receipt),
            "predict_receipt": dict(predict_receipt),
            "validation_candidate_count": len(validation),
            "validation_signal_date_count": int(
                validation["signal_date"].nunique()
            ),
            "validation_signal_sessions": validation_signal_sessions,
            "validation_signal_sessions_sha256": _canonical_sha256(
                validation_signal_sessions
            ),
            "observed_validation_signal_sessions": (
                observed_validation_signal_sessions
            ),
            "observed_validation_signal_sessions_sha256": (
                _canonical_sha256(observed_validation_signal_sessions)
            ),
            "validation_candidate_keys_sha256": _canonical_sha256(
                validation_keys
            ),
            "score_rows_sha256": _canonical_sha256(fold_score_rows),
            "positive_utility_candidate_count": len(positive_rows),
            "positive_utility_candidate_keys_sha256": (
                _canonical_sha256(
                    [row["candidate_key"] for row in positive_rows]
                )
            ),
            "positive_utility_rows_sha256": _canonical_sha256(
                positive_rows
            ),
        }
        fold_receipt["receipt_sha256"] = _canonical_sha256(
            fold_receipt
        )
        fold_receipts.append(fold_receipt)

    score_payload.sort(
        key=lambda item: (item["signal_date"], item["candidate_key"])
    )
    if len({row["candidate_key"] for row in score_payload}) != len(
        score_payload
    ):
        raise ValueError
    positive_payload = [
        row
        for row in score_payload
        if float(row[_PROBABILITY_COLUMN]) > 0.5
    ]
    receipt = {
        "schema_version": (
            "audited-pit-shallow-gbdt-rolling-oof-receipt/v1"
        ),
        "minimum_training_sessions": minimum_training_sessions,
        "training_window_sessions": training_window_sessions,
        "validation_sessions": validation_sessions,
        "friction_percentage_points": _FRICTION_PERCENTAGE_POINTS,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "probability_column": _PROBABILITY_COLUMN,
        "probability_gate": "strictly_greater_than_0.5",
        "fold_count": len(fold_receipts),
        "folds": fold_receipts,
        "folds_sha256": _canonical_sha256(fold_receipts),
        "oof_candidate_count": len(score_payload),
        "oof_candidate_keys_sha256": _canonical_sha256(
            [row["candidate_key"] for row in score_payload]
        ),
        "oof_scores_sha256": _canonical_sha256(score_payload),
        "positive_utility_candidate_count": len(positive_payload),
        "positive_utility_candidate_keys_sha256": _canonical_sha256(
            [row["candidate_key"] for row in positive_payload]
        ),
        "positive_utility_rows_sha256": _canonical_sha256(
            positive_payload
        ),
        "frozen_signal_sessions": session_dates,
        "frozen_signal_sessions_sha256": _canonical_sha256(
            session_dates
        ),
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return score_payload, receipt


def verify_shallow_gbdt_rolling_oof_receipt(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    scored_oof: pd.DataFrame,
    receipt: Mapping[str, Any],
    *,
    minimum_training_sessions: int = 126,
    training_window_sessions: int = 126,
    validation_sessions: int = 63,
) -> dict[str, Any]:
    try:
        expected_payload, expected_receipt = (
            _independent_shallow_gbdt_rolling_oof_replay(
                features,
                outcomes,
                sessions,
                minimum_training_sessions=minimum_training_sessions,
                training_window_sessions=training_window_sessions,
                validation_sessions=validation_sessions,
            )
        )
        if dict(receipt) != expected_receipt:
            raise ValueError
        required = {
            "candidate_key",
            "signal_date",
            _PROBABILITY_COLUMN,
        }
        if (
            not isinstance(scored_oof, pd.DataFrame)
            or not required.issubset(scored_oof.columns)
            or len(scored_oof) != len(expected_payload)
        ):
            raise ValueError
        for row, expected in zip(
            scored_oof.itertuples(index=False),
            expected_payload,
            strict=True,
        ):
            actual = {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                _PROBABILITY_COLUMN: float(
                    getattr(row, _PROBABILITY_COLUMN)
                ),
            }
            if actual != expected:
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "shallow GBDT rolling OOF verification failed"
        ) from exc
    return {
        "verified": True,
        "receipt_sha256": expected_receipt["receipt_sha256"],
        "fold_count": expected_receipt["fold_count"],
        "oof_candidate_count": expected_receipt["oof_candidate_count"],
    }


_FROZEN_BUILD_SHALLOW_GBDT_ROLLING_OOF_SCORES = (
    build_shallow_gbdt_rolling_oof_scores
)
_FROZEN_VERIFY_SHALLOW_GBDT_ROLLING_OOF_RECEIPT = (
    verify_shallow_gbdt_rolling_oof_receipt
)
