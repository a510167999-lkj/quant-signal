"""Strict audited-PIT continuous ridge out-of-fold research."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import math
from typing import Any

import numpy as np
import pandas as pd

from app.a_share_universe import _is_excluded_name
from app.current_pool_development_replay import _sha256
from app.research_scope import (
    is_mainboard_chinext_symbol,
    market_scope_contract,
)


FEATURE_NAMES = (
    "log_amount",
    "amount_to_prior20_median",
    "distance_prior20_high_pct",
    "signal_return_1d_pct",
    "signal_close_location_pct",
    "signal_range_pct",
    "signal_return_20d_pct",
    "signal_return_60d_pct",
    "realized_volatility_20d_pct",
    "distance_ma20_pct",
    "cross_section_above_ma20_fraction",
    "cross_section_median_return_20d_pct",
)


CONTINUOUS_RIDGE_OOF_SPEC = {
    "schema_version": (
        "development-pit-cross-sectional-continuous-ridge-oof/v1"
    ),
    "signal_tag": "cross_sectional_continuous_ridge_oof",
    "market_scope": {
        **market_scope_contract(),
        "allowed_boards": [
            "shanghai_main",
            "shenzhen_main",
            "chinext",
        ],
    },
    "features": list(FEATURE_NAMES),
    "feature_history_sessions": 61,
    "minimum_cross_section_members": 1_000,
    "label": {
        "target": "gross_return_pct_minus_0.45",
        "friction_percentage_points": 0.45,
        "clipping": False,
    },
    "model": {
        "type": "weighted_continuous_linear_ridge",
        "ridge_lambda": 1.0,
        "standardization": "training_fold_only",
        "intercept_penalized": False,
        "signal_date_total_weight": 1.0,
        "interactions": False,
    },
    "walk_forward": {
        "minimum_training_sessions": 126,
        "validation_sessions": 63,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "folds": "continuous_non_overlapping_validation_windows",
    },
    "selection": {
        "minimum_predicted_net_return_pct": 0.0,
        "comparison": "strictly_greater_unrounded_float64",
        "top_n": 3,
        "max_active_positions": 3,
        "max_active_positions_per_industry": 1,
        "main_rank": [
            "predicted_net_return_pct_desc",
            "signal_date_amount_desc",
            "security_id_asc",
        ],
        "amount_baseline_rank": [
            "signal_date_amount_desc",
            "security_id_asc",
        ],
    },
    "entry_signal_offset_sessions": 1,
    "planned_exit_signal_offset_sessions": 6,
    "hold_days": 5,
    "close_stop_loss_pct": 5.0,
    "exposure_multiplier": 1.0,
    "capital_model": "slot-daily",
    "roundtrip_cost_bps": 25.0,
    "slippage_bps": 10.0,
    "annual_financing_rate_pct": 8.0,
    "entry_numeric_abs_tolerance": 1e-9,
    "entry_execution": {
        "decision_cutoff": "next_open",
        "max_gap_up_pct": 6.0,
        "max_gap_down_pct": 7.0,
        "locked_limit_gap_pct": 9.3,
        "max_intraday_range_pct": 8.0,
    },
    "advancement_thresholds": {
        "minimum_complete_trades": 20,
        "minimum_full_win_rate_pct": 52.0,
        "maximum_full_drawdown_pct": 15.0,
        "minimum_full_profit_factor": 1.3,
        "minimum_latest_365d_return_pct": 50.0,
        "minimum_latest_365d_calmar": 1.5,
        "all_complete_365d_minimum_return_pct": 50.0,
        "all_complete_365d_maximum_drawdown_pct": 15.0,
        "all_complete_365d_minimum_payoff_ratio": 1.3,
        "all_complete_365d_minimum_profit_factor": 1.3,
        "all_complete_365d_minimum_calmar": 1.5,
    },
}


_BAR_COLUMNS = (
    "date",
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "adj_factor",
    "membership_name",
    "membership_industry",
    "membership_receipt_dataset",
    "membership_receipt_partition",
)


def _ordered_sessions(sessions: Sequence[str]) -> list[str]:
    values = [str(value) for value in sessions]
    if (
        not values
        or values != sorted(values)
        or len(values) != len(set(values))
    ):
        raise ValueError("continuous ridge sessions must be ordered and unique")
    return values


def _deterministic_median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not all(math.isfinite(value) for value in ordered):
        raise ValueError("continuous ridge median inputs are invalid")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _eligible_signal_name(value: Any) -> str | None:
    if pd.isna(value):
        return None
    name = str(value or "").strip()
    if not name or _is_excluded_name(name):
        return None
    return name


def _feature_row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_key": str(row["candidate_key"]),
        "signal_date": str(row["signal_date"]),
        "ts_code": str(row["ts_code"]),
        "source_ts_code": str(row["source_ts_code"]),
        "security_id": str(row["security_id"]),
        "signal_industry": str(row["signal_industry"]),
        "candidate_amount": float(row["candidate_amount"]),
        **{name: float(row[name]) for name in FEATURE_NAMES},
    }


def _build_exact_cross_section_features(
    bars: pd.DataFrame,
    sessions: Sequence[str],
    *,
    minimum_cross_section_members: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    minimum = (
        int(minimum_cross_section_members)
        if minimum_cross_section_members is not None
        else int(
            CONTINUOUS_RIDGE_OOF_SPEC["minimum_cross_section_members"]
        )
    )
    if minimum <= 0:
        raise ValueError("minimum cross-section members must be positive")
    missing_columns = [column for column in _BAR_COLUMNS if column not in bars]
    if missing_columns:
        raise ValueError("continuous ridge bars are missing required columns")

    values = bars.copy()
    values["date"] = values["date"].astype(str)
    values["ts_code"] = values["ts_code"].astype(str)
    if "source_ts_code" not in values:
        values["source_ts_code"] = values["ts_code"]
    if "security_id" not in values:
        values["security_id"] = values["ts_code"].map(
            lambda ts_code: f"cn-a-share:{ts_code}"
        )
    if "security_code_transition_id" not in values:
        values["security_code_transition_id"] = None
    if "security_code_transition_contract_sha256" not in values:
        values["security_code_transition_contract_sha256"] = None

    status_counts: Counter[str] = Counter()
    source_row_count = len(values)
    in_scope = values["ts_code"].map(is_mainboard_chinext_symbol)
    status_counts["out_of_scope_market_row"] = int((~in_scope).sum())
    values = values[in_scope].copy()
    if values.empty:
        raise ValueError("continuous ridge bars have no in-scope rows")

    numeric_columns = (
        "open",
        "high",
        "low",
        "close",
        "amount",
        "adj_factor",
    )
    for column in numeric_columns:
        values[column] = pd.to_numeric(values[column], errors="coerce")
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    values["session_position"] = values["date"].map(session_positions)
    if values["session_position"].isna().any():
        raise ValueError("continuous ridge bars contain dates outside sessions")
    values["session_position"] = values["session_position"].astype(int)
    if values.duplicated(["security_id", "date"]).any():
        raise ValueError(
            "continuous ridge bars contain duplicate stable entity dates"
        )
    values = values.sort_values(
        ["security_id", "session_position", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)

    longitudinal_rows: list[dict[str, Any]] = []
    for security_id, group in values.groupby("security_id", sort=True):
        frame = group.sort_values("session_position", kind="mergesort")
        positions = frame["session_position"].to_numpy(dtype=int)
        for row_position in range(60, len(frame)):
            signal_session_position = int(positions[row_position])
            if signal_session_position < 60:
                continue
            if signal_session_position - int(positions[row_position - 60]) != 60:
                status_counts["missing_exact_61_session_history"] += 1
                continue
            window = frame.iloc[row_position - 60 : row_position + 1]
            if not np.array_equal(
                window["session_position"].to_numpy(dtype=int),
                np.arange(
                    signal_session_position - 60,
                    signal_session_position + 1,
                    dtype=int,
                ),
            ):
                status_counts["missing_exact_61_session_history"] += 1
                continue
            signal = window.iloc[-1]
            signal_date = str(signal["date"])
            exact_membership = (
                str(signal["membership_receipt_dataset"]) == "bak_basic"
                and str(signal["membership_receipt_partition"])
                == signal_date
            )
            name = _eligible_signal_name(signal["membership_name"])
            industry = (
                ""
                if pd.isna(signal["membership_industry"])
                else str(signal["membership_industry"]).strip()
            )
            raw_matrix = window[
                ["open", "high", "low", "close", "amount", "adj_factor"]
            ].to_numpy(dtype=float)
            if (
                not exact_membership
                or name is None
                or not industry
                or not np.isfinite(raw_matrix).all()
                or (raw_matrix[:, :4] <= 0).any()
                or (raw_matrix[:, 5] <= 0).any()
                or float(signal["amount"]) <= 0
                or float(signal["high"]) <= float(signal["low"])
            ):
                status_counts["signal_ineligible"] += 1
                continue

            adjusted_close = (
                window["close"].to_numpy(dtype=float)
                * window["adj_factor"].to_numpy(dtype=float)
            )
            adjusted_high = (
                window["high"].to_numpy(dtype=float)
                * window["adj_factor"].to_numpy(dtype=float)
            )
            prior_amounts = window["amount"].to_numpy(dtype=float)[-21:-1]
            prior_amount_median = _deterministic_median(prior_amounts)
            prior_high = float(np.max(adjusted_high[-21:-1]))
            ma20 = float(np.mean(adjusted_close[-20:]))
            if (
                prior_amount_median <= 0
                or prior_high <= 0
                or ma20 <= 0
            ):
                status_counts["nonpositive_feature_denominator"] += 1
                continue
            one_step_returns = (
                adjusted_close[-21:][1:] / adjusted_close[-21:][:-1] - 1.0
            )
            signal_close = float(adjusted_close[-1])
            raw_close = float(signal["close"])
            raw_high = float(signal["high"])
            raw_low = float(signal["low"])
            feature_values = {
                "log_amount": math.log1p(float(signal["amount"])),
                "amount_to_prior20_median": (
                    float(signal["amount"]) / prior_amount_median
                ),
                "distance_prior20_high_pct": (
                    signal_close / prior_high - 1.0
                )
                * 100.0,
                "signal_return_1d_pct": (
                    signal_close / float(adjusted_close[-2]) - 1.0
                )
                * 100.0,
                "signal_close_location_pct": (
                    (raw_close - raw_low) / (raw_high - raw_low) * 100.0
                ),
                "signal_range_pct": (raw_high / raw_low - 1.0) * 100.0,
                "signal_return_20d_pct": (
                    signal_close / float(adjusted_close[-21]) - 1.0
                )
                * 100.0,
                "signal_return_60d_pct": (
                    signal_close / float(adjusted_close[0]) - 1.0
                )
                * 100.0,
                "realized_volatility_20d_pct": (
                    float(np.std(one_step_returns, ddof=0)) * 100.0
                ),
                "distance_ma20_pct": (
                    signal_close / ma20 - 1.0
                )
                * 100.0,
            }
            if not all(
                math.isfinite(value) for value in feature_values.values()
            ):
                status_counts["nonfinite_longitudinal_feature"] += 1
                continue
            ts_code = str(signal["ts_code"])
            source_ts_code = str(signal["source_ts_code"])
            candidate_key = f"{security_id}|{signal_date}"
            longitudinal_rows.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "date": signal_date,
                    "ts_code": ts_code,
                    "source_ts_code": source_ts_code,
                    "security_id": str(security_id),
                    "security_code_transition_id": (
                        None
                        if pd.isna(signal["security_code_transition_id"])
                        else str(signal["security_code_transition_id"])
                    ),
                    "security_code_transition_contract_sha256": (
                        None
                        if pd.isna(
                            signal[
                                "security_code_transition_contract_sha256"
                            ]
                        )
                        else str(
                            signal[
                                "security_code_transition_contract_sha256"
                            ]
                        )
                    ),
                    "symbol": ts_code[:6],
                    "name": name,
                    "signal_industry": industry,
                    "candidate_amount": float(signal["amount"]),
                    "signal_frame_index": int(row_position),
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": signal_date,
                    **feature_values,
                }
            )

    if not longitudinal_rows:
        raise ValueError("continuous ridge produced no longitudinal features")
    longitudinal = pd.DataFrame(longitudinal_rows)
    complete_groups: list[pd.DataFrame] = []
    group_receipts: list[dict[str, Any]] = []
    for signal_date, raw_group in longitudinal.groupby(
        "signal_date",
        sort=True,
    ):
        group = raw_group.sort_values(
            ["security_id", "source_ts_code"],
            kind="mergesort",
        ).copy()
        if len(group) < minimum:
            status_counts["cross_section_member_count_below_minimum"] += len(
                group
            )
            continue
        member_keys = group["security_id"].astype(str).tolist()
        if len(member_keys) != len(set(member_keys)):
            raise ValueError(
                "continuous ridge cross-section has duplicate entities"
            )
        return_inputs = [
            {
                "security_id": str(row.security_id),
                "value": float(row.signal_return_20d_pct),
            }
            for row in group.itertuples(index=False)
        ]
        breadth_inputs = [
            {
                "security_id": str(row.security_id),
                "above_ma20": bool(float(row.distance_ma20_pct) > 0.0),
            }
            for row in group.itertuples(index=False)
        ]
        breadth = sum(
            item["above_ma20"] for item in breadth_inputs
        ) / len(breadth_inputs)
        median_return = _deterministic_median(
            [item["value"] for item in return_inputs]
        )
        group["cross_section_above_ma20_fraction"] = float(breadth)
        group["cross_section_median_return_20d_pct"] = float(
            median_return
        )
        receipt = {
            "signal_date": str(signal_date),
            "member_count": len(group),
            "member_keys_sha256": _sha256(member_keys),
            "breadth_inputs_sha256": _sha256(breadth_inputs),
            "return_20d_inputs_sha256": _sha256(return_inputs),
            "cross_section_above_ma20_fraction": float(breadth),
            "cross_section_median_return_20d_pct": float(median_return),
        }
        receipt["statistics_sha256"] = _sha256(receipt)
        group_receipts.append(receipt)
        complete_groups.append(group)

    if not complete_groups:
        raise ValueError("continuous ridge produced no complete cross-section")
    features = pd.concat(complete_groups, ignore_index=True).sort_values(
        ["signal_date", "security_id", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    if features["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge feature keys are duplicated")
    matrix = features[list(FEATURE_NAMES)].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("continuous ridge feature matrix is nonfinite")
    feature_payload = [
        _feature_row_payload(row)
        for row in features.to_dict("records")
    ]
    scope = market_scope_contract()
    receipt = {
        "schema_version": "continuous-ridge-feature-receipt/v1",
        "scope_policy_id": scope["policy_id"],
        "scope_policy_sha256": scope["policy_sha256"],
        "session_count": len(session_dates),
        "sessions_sha256": _sha256(session_dates),
        "source_row_count": source_row_count,
        "in_scope_row_count": len(values),
        "minimum_cross_section_members": minimum,
        "status_counts": dict(sorted(status_counts.items())),
        "cross_section_group_count": len(group_receipts),
        "cross_section_groups": group_receipts,
        "cross_section_groups_sha256": _sha256(group_receipts),
        "feature_row_count": len(features),
        "feature_rows_sha256": _sha256(feature_payload),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return features, receipt


def _signal_date_weights(signal_dates: Sequence[str]) -> np.ndarray:
    dates = [str(value) for value in signal_dates]
    if not dates:
        raise ValueError("continuous ridge training dates are empty")
    counts = Counter(dates)
    return np.asarray(
        [1.0 / counts[value] for value in dates],
        dtype=float,
    )


def _fit_weighted_continuous_ridge(
    x: np.ndarray,
    y: np.ndarray,
    signal_dates: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
) -> dict[str, np.ndarray]:
    x_values = np.asarray(x, dtype=float)
    y_values = np.asarray(y, dtype=float)
    if (
        x_values.ndim != 2
        or y_values.ndim != 1
        or len(x_values) != len(y_values)
        or len(y_values) != len(signal_dates)
        or len(x_values) == 0
        or not np.isfinite(x_values).all()
        or not np.isfinite(y_values).all()
        or not math.isfinite(float(ridge_lambda))
        or float(ridge_lambda) < 0
    ):
        raise ValueError("invalid continuous ridge training inputs")
    weights = _signal_date_weights(signal_dates)
    total_weight = float(weights.sum())
    mean = (x_values * weights[:, None]).sum(axis=0) / total_weight
    centered = x_values - mean
    variance = (
        (centered**2 * weights[:, None]).sum(axis=0) / total_weight
    )
    scale = np.sqrt(variance)
    scale = np.where(scale > 1e-12, scale, 1.0)
    standardized = centered / scale
    design = np.column_stack(
        [np.ones(len(standardized), dtype=float), standardized]
    )
    sqrt_weights = np.sqrt(weights)
    weighted_design = design * sqrt_weights[:, None]
    weighted_target = y_values * sqrt_weights
    penalty = np.eye(design.shape[1], dtype=float) * float(ridge_lambda)
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(
        weighted_design.T @ weighted_design + penalty,
        weighted_design.T @ weighted_target,
    )
    return {
        "mean": mean,
        "scale": scale,
        "coefficients": coefficients,
    }


def _predict_continuous_ridge(
    model: Mapping[str, np.ndarray],
    x: np.ndarray,
) -> np.ndarray:
    values = np.asarray(x, dtype=float)
    mean = np.asarray(model["mean"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    if (
        values.ndim != 2
        or values.shape[1] != len(mean)
        or len(scale) != len(mean)
        or len(coefficients) != len(mean) + 1
        or not np.isfinite(values).all()
    ):
        raise ValueError("invalid continuous ridge prediction inputs")
    standardized = (values - mean) / scale
    design = np.column_stack(
        [np.ones(len(standardized), dtype=float), standardized]
    )
    scores = design @ coefficients
    if not np.isfinite(scores).all():
        raise ValueError("continuous ridge predictions are nonfinite")
    return scores


def _fold_ranges(
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    validation_sessions: int = 63,
) -> list[tuple[str, str]]:
    values = _ordered_sessions(sessions)
    if minimum_training_sessions <= 0 or validation_sessions <= 0:
        raise ValueError("continuous ridge fold lengths must be positive")
    return [
        (
            values[start],
            values[min(start + validation_sessions - 1, len(values) - 1)],
        )
        for start in range(
            minimum_training_sessions,
            len(values),
            validation_sessions,
        )
    ]


def _model_payload(model: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {
        "mean": [float(value) for value in model["mean"]],
        "scale": [float(value) for value in model["scale"]],
        "coefficients": [
            float(value) for value in model["coefficients"]
        ],
    }


def _build_purged_oof_scores(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    validation_sessions: int = 63,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    required = {"candidate_key", "signal_date", *FEATURE_NAMES}
    if not required.issubset(features.columns):
        raise ValueError("continuous ridge OOF features are incomplete")
    rows = features.copy()
    rows["candidate_key"] = rows["candidate_key"].astype(str)
    rows["signal_date"] = rows["signal_date"].astype(str)
    if rows["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge OOF feature keys are duplicated")
    if not np.isfinite(rows[list(FEATURE_NAMES)].to_numpy(dtype=float)).all():
        raise ValueError("continuous ridge OOF feature matrix is nonfinite")

    outcome_lookup: dict[str, dict[str, Any]] = {}
    for raw_outcome in outcomes:
        outcome = dict(raw_outcome)
        key = str(outcome.get("candidate_key") or "")
        if not key or key in outcome_lookup:
            raise ValueError("continuous ridge outcomes have invalid keys")
        outcome_lookup[key] = outcome

    completed: dict[str, dict[str, Any]] = {}
    friction = float(
        CONTINUOUS_RIDGE_OOF_SPEC["label"][
            "friction_percentage_points"
        ]
    )
    for key, outcome in outcome_lookup.items():
        if outcome.get("right_censored") is True:
            continue
        exit_date = str(outcome.get("exit_date") or "")[:10]
        return_pct = outcome.get("return_pct")
        try:
            gross_return = float(return_pct)
        except (TypeError, ValueError):
            continue
        if not exit_date or not math.isfinite(gross_return):
            continue
        completed[key] = {
            "exit_date": exit_date,
            "net_label": gross_return - friction,
        }

    folds = _fold_ranges(
        session_dates,
        minimum_training_sessions=minimum_training_sessions,
        validation_sessions=validation_sessions,
    )
    scored_frames: list[pd.DataFrame] = []
    fold_receipts: list[dict[str, Any]] = []
    for fold_index, (validation_start, validation_end) in enumerate(
        folds,
        start=1,
    ):
        validation = rows[
            rows["signal_date"].between(
                validation_start,
                validation_end,
                inclusive="both",
            )
        ].sort_values(
            ["signal_date", "candidate_key"],
            kind="mergesort",
        )
        if validation.empty:
            continue
        training_records = []
        for row in rows.itertuples(index=False):
            key = str(row.candidate_key)
            outcome = completed.get(key)
            if (
                outcome is None
                or str(outcome["exit_date"]) >= validation_start
            ):
                continue
            training_records.append(
                {
                    "candidate_key": key,
                    "signal_date": str(row.signal_date),
                    "exit_date": str(outcome["exit_date"]),
                    "net_label": float(outcome["net_label"]),
                    "features": [
                        float(getattr(row, name)) for name in FEATURE_NAMES
                    ],
                }
            )
        training_records.sort(
            key=lambda item: (
                item["signal_date"],
                item["candidate_key"],
            )
        )
        if not training_records:
            raise ValueError(
                "continuous ridge fold has no mature complete training rows"
            )
        x_train = np.asarray(
            [item["features"] for item in training_records],
            dtype=float,
        )
        y_train = np.asarray(
            [item["net_label"] for item in training_records],
            dtype=float,
        )
        model = _fit_weighted_continuous_ridge(
            x_train,
            y_train,
            [item["signal_date"] for item in training_records],
            ridge_lambda=float(
                CONTINUOUS_RIDGE_OOF_SPEC["model"]["ridge_lambda"]
            ),
        )
        scores = _predict_continuous_ridge(
            model,
            validation[list(FEATURE_NAMES)].to_numpy(dtype=float),
        )
        scored = validation.copy()
        scored["predicted_net_return_pct"] = scores
        scored_frames.append(scored)
        score_rows = [
            {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                "predicted_net_return_pct": float(score),
            }
            for row, score in zip(
                validation.itertuples(index=False),
                scores,
            )
        ]
        model_payload = _model_payload(model)
        fold_receipt = {
            "fold": fold_index,
            "validation_start": validation_start,
            "validation_end": validation_end,
            "training_candidate_count": len(training_records),
            "training_signal_date_count": len(
                {item["signal_date"] for item in training_records}
            ),
            "training_last_exit_date": max(
                item["exit_date"] for item in training_records
            ),
            "training_candidate_keys_sha256": _sha256(
                [item["candidate_key"] for item in training_records]
            ),
            "training_label": "gross_return_pct_minus_0.45",
            "model": model_payload,
            "model_sha256": _sha256(model_payload),
            "validation_candidate_count": len(validation),
            "validation_signal_date_count": int(
                validation["signal_date"].nunique()
            ),
            "score_rows_sha256": _sha256(score_rows),
        }
        fold_receipt["receipt_sha256"] = _sha256(fold_receipt)
        fold_receipts.append(fold_receipt)

    if not scored_frames:
        raise ValueError("continuous ridge produced no OOF scores")
    scored_oof = pd.concat(scored_frames, ignore_index=True).sort_values(
        ["signal_date", "candidate_key"],
        kind="mergesort",
    ).reset_index(drop=True)
    if scored_oof["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge OOF keys overlap across folds")
    score_payload = [
        {
            "candidate_key": str(row.candidate_key),
            "signal_date": str(row.signal_date),
            "predicted_net_return_pct": float(
                row.predicted_net_return_pct
            ),
        }
        for row in scored_oof.itertuples(index=False)
    ]
    receipt = {
        "schema_version": "continuous-ridge-purged-oof-receipt/v1",
        "minimum_training_sessions": int(minimum_training_sessions),
        "validation_sessions": int(validation_sessions),
        "purge": "complete_exit_date_strictly_before_validation_start",
        "fold_count": len(fold_receipts),
        "folds": fold_receipts,
        "folds_sha256": _sha256(fold_receipts),
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": _sha256(score_payload),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return scored_oof, receipt


def _selection_trade_key(trade: Mapping[str, Any]) -> str:
    values = [
        str(trade.get("security_id") or ""),
        str(trade.get("signal_date") or "")[:10],
        str(trade.get("entry_date") or "")[:10],
        str(trade.get("exit_date") or "")[:10],
    ]
    if not all(values):
        raise ValueError("continuous ridge selection key is incomplete")
    return "|".join(values)


def _positive_score_pool(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered = sorted(
        (dict(candidate) for candidate in candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("candidate_key") or ""),
        ),
    )
    input_keys: list[str] = []
    positive: list[dict[str, Any]] = []
    positive_keys: list[str] = []
    for candidate in ordered:
        key = str(candidate.get("candidate_key") or "")
        if not key:
            raise ValueError("continuous ridge score candidate key is missing")
        score = float(candidate.get("predicted_net_return_pct"))
        if not math.isfinite(score):
            raise ValueError("continuous ridge prediction is nonfinite")
        input_keys.append(key)
        if score > 0.0:
            positive.append(candidate)
            positive_keys.append(key)
    if len(input_keys) != len(set(input_keys)):
        raise ValueError("continuous ridge score pool keys are duplicated")
    receipt = {
        "schema_version": "continuous-ridge-positive-score-pool/v1",
        "comparison": "predicted_net_return_pct_strictly_greater_than_zero",
        "input_candidate_count": len(ordered),
        "input_candidate_keys_sha256": _sha256(input_keys),
        "positive_candidate_count": len(positive),
        "positive_candidate_keys_sha256": _sha256(positive_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return positive, receipt


def _selection_candidate_table(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        trade = dict(candidate)
        score = float(trade.get("predicted_net_return_pct"))
        amount = float(trade.get("candidate_amount"))
        industry = str(trade.get("signal_industry") or "").strip()
        security_id = str(trade.get("security_id") or "")
        if (
            not math.isfinite(score)
            or score <= 0.0
            or not math.isfinite(amount)
            or not industry
            or not security_id
        ):
            raise ValueError("continuous ridge selection candidate is invalid")
        rows.append(
            {
                "trade_key": _selection_trade_key(trade),
                "candidate_key": str(trade.get("candidate_key") or ""),
                "security_id": security_id,
                "signal_industry": industry,
                "predicted_net_return_pct": score,
                "candidate_amount": amount,
                "right_censored": trade.get("right_censored") is True,
            }
        )
    rows.sort(key=lambda item: item["trade_key"])
    if len({item["trade_key"] for item in rows}) != len(rows):
        raise ValueError("continuous ridge selection candidates are duplicated")
    return rows


def _selection_rank_key(
    trade: Mapping[str, Any],
    *,
    rank_mode: str,
) -> tuple[Any, ...]:
    security_id = str(trade.get("security_id") or "")
    amount = float(trade.get("candidate_amount"))
    if not security_id or not math.isfinite(amount):
        raise ValueError("continuous ridge selection rank inputs are invalid")
    if rank_mode == "predicted_net_return":
        score = float(trade.get("predicted_net_return_pct"))
        if not math.isfinite(score):
            raise ValueError("continuous ridge model score is invalid")
        return (-score, -amount, security_id)
    if rank_mode == "signal_date_amount":
        return (-amount, security_id)
    raise ValueError("continuous ridge selection rank mode is invalid")


def _select_with_industry_cap_receipt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    top_n: int,
    max_active_positions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if top_n <= 0 or max_active_positions <= 0:
        raise ValueError("continuous ridge selection capacities are invalid")
    candidate_table = _selection_candidate_table(candidates)
    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        signal_date = str(candidate.get("signal_date") or "")[:10]
        if not signal_date:
            raise ValueError("continuous ridge selection date is missing")
        by_signal_date[signal_date].append(candidate)

    selected: list[dict[str, Any]] = []
    active_positions: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    all_ordered_keys: list[str] = []
    for signal_date in sorted(by_signal_date):
        signal_day = date.fromisoformat(signal_date)
        active_positions = [
            trade
            for trade in active_positions
            if date.fromisoformat(str(trade["exit_date"])[:10]) > signal_day
        ]
        trades = sorted(
            by_signal_date[signal_date],
            key=lambda trade: _selection_rank_key(
                trade,
                rank_mode=rank_mode,
            ),
        )
        ordered_keys = [_selection_trade_key(trade) for trade in trades]
        if len(ordered_keys) != len(set(ordered_keys)):
            raise ValueError(
                "continuous ridge daily selection keys are duplicated"
            )
        all_ordered_keys.extend(ordered_keys)
        active_securities = {
            str(trade["security_id"]) for trade in active_positions
        }
        active_industries = {
            str(trade["signal_industry"]).strip()
            for trade in active_positions
        }
        selected_today = 0
        selected_keys: list[str] = []
        decisions: list[dict[str, str]] = []
        for index, trade in enumerate(trades):
            trade_key = ordered_keys[index]
            security_id = str(trade["security_id"])
            industry = str(trade["signal_industry"]).strip()
            if security_id in active_securities:
                decisions.append(
                    {"trade_key": trade_key, "decision": "active_security"}
                )
                continue
            if industry in active_industries:
                decisions.append(
                    {"trade_key": trade_key, "decision": "active_industry"}
                )
                continue
            if len(active_positions) >= max_active_positions:
                decisions.extend(
                    {
                        "trade_key": ordered_keys[remaining],
                        "decision": "max_active_positions_break",
                    }
                    for remaining in range(index, len(trades))
                )
                break
            selected.append(trade)
            active_positions.append(trade)
            active_securities.add(security_id)
            active_industries.add(industry)
            selected_today += 1
            selected_keys.append(trade_key)
            decisions.append(
                {"trade_key": trade_key, "decision": "selected"}
            )
            if selected_today >= top_n:
                decisions.extend(
                    {
                        "trade_key": ordered_keys[remaining],
                        "decision": "top_n_break",
                    }
                    for remaining in range(index + 1, len(trades))
                )
                break
        days.append(
            {
                "signal_date": signal_date,
                "ordered_candidate_trade_keys": ordered_keys,
                "ordered_candidate_root_sha256": _sha256(ordered_keys),
                "decisions": decisions,
                "selected_trade_keys": selected_keys,
            }
        )

    selected_keys = [_selection_trade_key(trade) for trade in selected]
    receipt = {
        "schema_version": "continuous-ridge-industry-selection-receipt/v1",
        "parameters": {
            "rank_mode": rank_mode,
            "top_n": int(top_n),
            "max_active_positions": int(max_active_positions),
            "max_active_positions_per_industry": 1,
            "same_day_exit_before_signal_selection": True,
            "industry_source": "signal_date_frozen",
            "stable_identity": "security_id",
        },
        "candidate_count": len(candidate_table),
        "candidate_table_sha256": _sha256(candidate_table),
        "ordered_candidate_trade_keys_sha256": _sha256(
            all_ordered_keys
        ),
        "selected_count": len(selected),
        "selected_trade_keys": selected_keys,
        "selected_trade_keys_sha256": _sha256(selected_keys),
        "days": days,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return selected, receipt

