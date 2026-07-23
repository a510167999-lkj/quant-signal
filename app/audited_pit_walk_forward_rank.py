"""Purged expanding-window rank research on the audited PIT breakout candidates."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.audited_pit_development_replay import (
    AuditedPITDevelopmentReplayError,
    _exact_membership_sessions,
    _load_exact_membership_bars,
    _producer_code_binding,
)
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _candidate_trades_from_bars,
    _sha256,
    _write_content_addressed,
)
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptError
from app.research_scope import market_scope_contract
from app.research_sweep import sweep_qualified_trades


FEATURE_NAMES = (
    "log_amount",
    "amount_to_prior20_median",
    "breakout_extension_pct",
    "signal_day_return_pct",
    "signal_close_location_pct",
    "signal_range_pct",
    "signal_return_20d_pct",
    "signal_return_60d_pct",
    "realized_volatility_20d_pct",
    "distance_ma20_pct",
)


WALK_FORWARD_RANK_SPEC = {
    "schema_version": "development-breakout-purged-ridge-rank/v1",
    "candidate_strategy_schema_version": SIMPLE_BREAKOUT_SPEC["schema_version"],
    "signal_tags": ["breakout_20d", "purged_ridge_rank"],
    "features": list(FEATURE_NAMES),
    "label": "gross_trade_return_minus_45bps_gt_0",
    "model": {
        "type": "weighted_linear_probability_ridge",
        "ridge_lambda": 1.0,
        "standardization": "training_fold_only",
        "intercept_penalized": False,
        "signal_date_total_weight": 1.0,
        "interactions": False,
    },
    "walk_forward": {
        "minimum_training_sessions": 126,
        "validation_sessions": 63,
        "purge": "training_exit_date_strictly_before_validation_start",
        "folds": "continuous_non_overlapping_validation_windows",
    },
    "selection": {
        "top_n": SIMPLE_BREAKOUT_SPEC["top_n"],
        "max_active_positions": SIMPLE_BREAKOUT_SPEC["max_active_positions"],
        "rank": "predicted_linear_probability_score_desc",
    },
    "costs": {
        "roundtrip_cost_bps": SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"],
        "slippage_bps_each_side": SIMPLE_BREAKOUT_SPEC["slippage_bps"],
        "total_trade_friction_bps": (
            SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"]
            + SIMPLE_BREAKOUT_SPEC["slippage_bps"] * 2
        ),
    },
}


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "walk_forward_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }
    return {
        "schema_version": "audited-pit-walk-forward-rank-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _candidate_features(
    trades: Sequence[Mapping[str, Any]],
    bars: pd.DataFrame,
) -> pd.DataFrame:
    frame = bars.sort_values(["ts_code", "date"], kind="mergesort").copy()
    frame["symbol"] = frame["ts_code"].str[:6]
    frame["signal_close"] = frame["close"] * frame["adj_factor"]
    frame["signal_high"] = frame["high"] * frame["adj_factor"]
    grouped = frame.groupby("ts_code", sort=False)
    frame["prior20_high"] = grouped["signal_high"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).max()
    )
    frame["prior20_amount_median"] = grouped["amount"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).median()
    )
    frame["signal_close_20d_ago"] = grouped["signal_close"].shift(20)
    frame["signal_close_60d_ago"] = grouped["signal_close"].shift(60)
    frame["signal_ma20"] = grouped["signal_close"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    frame["signal_daily_return"] = grouped["signal_close"].pct_change(fill_method=None)
    frame["signal_volatility20"] = frame.groupby("ts_code", sort=False)[
        "signal_daily_return"
    ].transform(lambda values: values.rolling(20, min_periods=20).std(ddof=0))

    day_range = frame["high"] - frame["low"]
    frame["log_amount"] = np.log1p(frame["amount"].clip(lower=0))
    frame["amount_to_prior20_median"] = (
        frame["amount"] / frame["prior20_amount_median"]
    )
    frame["breakout_extension_pct"] = (
        frame["signal_close"] / frame["prior20_high"] - 1
    ) * 100
    frame["signal_day_return_pct"] = (
        frame["close"] / frame["pre_close"] - 1
    ) * 100
    frame["signal_close_location_pct"] = np.where(
        day_range > 0,
        (frame["close"] - frame["low"]) / day_range * 100,
        50.0,
    )
    frame["signal_range_pct"] = (frame["high"] / frame["low"] - 1) * 100
    frame["signal_return_20d_pct"] = (
        frame["signal_close"] / frame["signal_close_20d_ago"] - 1
    ) * 100
    frame["signal_return_60d_pct"] = (
        frame["signal_close"] / frame["signal_close_60d_ago"] - 1
    ) * 100
    frame["realized_volatility_20d_pct"] = frame["signal_volatility20"] * 100
    frame["distance_ma20_pct"] = (
        frame["signal_close"] / frame["signal_ma20"] - 1
    ) * 100

    candidates = pd.DataFrame(
        {
            "trade_index": range(len(trades)),
            "symbol": [str(trade["symbol"]) for trade in trades],
            "signal_date": [str(trade["signal_date"]) for trade in trades],
            "exit_date": [str(trade["exit_date"]) for trade in trades],
            "gross_return_pct": [
                float(trade.get("return_pct") or 0) for trade in trades
            ],
        }
    )
    feature_frame = frame[
        ["symbol", "date", *FEATURE_NAMES]
    ].rename(columns={"date": "signal_date"})
    merged = candidates.merge(
        feature_frame,
        on=["symbol", "signal_date"],
        how="left",
        validate="one_to_one",
        sort=False,
    ).sort_values("trade_index", kind="mergesort")
    if len(merged) != len(trades):
        raise AuditedPITDevelopmentReplayError(
            "candidate feature join changed the candidate cardinality"
        )
    matrix = merged[list(FEATURE_NAMES)].to_numpy(dtype=float)
    merged["feature_complete"] = np.isfinite(matrix).all(axis=1)
    total_cost_pct = (
        float(WALK_FORWARD_RANK_SPEC["costs"]["total_trade_friction_bps"]) / 100
    )
    merged["positive_after_cost"] = (
        merged["gross_return_pct"] - total_cost_pct > 0
    ).astype(float)
    return merged


def _fold_ranges(
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    validation_sessions: int = 63,
) -> list[tuple[str, str]]:
    if minimum_training_sessions <= 0 or validation_sessions <= 0:
        raise ValueError("walk-forward session lengths must be positive")
    return [
        (
            str(sessions[start]),
            str(sessions[min(start + validation_sessions - 1, len(sessions) - 1)]),
        )
        for start in range(
            minimum_training_sessions,
            len(sessions),
            validation_sessions,
        )
    ]


def _fit_weighted_ridge(
    x: np.ndarray,
    y: np.ndarray,
    signal_dates: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
) -> dict[str, np.ndarray]:
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(signal_dates):
        raise ValueError("invalid ridge training shapes")
    if len(x) == 0 or len(set(float(value) for value in y)) < 2:
        raise ValueError("ridge training requires two label classes")
    counts = Counter(str(value) for value in signal_dates)
    weights = np.asarray(
        [1.0 / counts[str(value)] for value in signal_dates],
        dtype=float,
    )
    total_weight = float(weights.sum())
    mean = (x * weights[:, None]).sum(axis=0) / total_weight
    centered = x - mean
    variance = (centered**2 * weights[:, None]).sum(axis=0) / total_weight
    scale = np.sqrt(variance)
    scale = np.where(scale > 1e-12, scale, 1.0)
    standardized = centered / scale
    design = np.column_stack([np.ones(len(standardized)), standardized])
    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_target = y * np.sqrt(weights)
    penalty = np.eye(design.shape[1]) * max(float(ridge_lambda), 0.0)
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


def _predict_ridge(model: Mapping[str, np.ndarray], x: np.ndarray) -> np.ndarray:
    standardized = (x - model["mean"]) / model["scale"]
    design = np.column_stack([np.ones(len(standardized)), standardized])
    return design @ model["coefficients"]


def _evaluate_fixed_rank(
    trades: list[dict[str, Any]],
    *,
    required_signal_tags: list[str],
) -> dict[str, Any]:
    return sweep_qualified_trades(
        trades,
        hold_days=SIMPLE_BREAKOUT_SPEC["hold_days"],
        top_n=SIMPLE_BREAKOUT_SPEC["top_n"],
        max_active_positions=SIMPLE_BREAKOUT_SPEC["max_active_positions"],
        min_trades=20,
        target_win_rate_pct=52.0,
        target_drawdown_pct=15.0,
        target_one_year_return_pct=50.0,
        target_profit_factor=1.3,
        target_calmar=1.5,
        exposure_multipliers=[SIMPLE_BREAKOUT_SPEC["exposure_multiplier"]],
        annual_financing_rate_pct=SIMPLE_BREAKOUT_SPEC[
            "annual_financing_rate_pct"
        ],
        roundtrip_cost_bps=SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"],
        slippage_bps=SIMPLE_BREAKOUT_SPEC["slippage_bps"],
        capital_model=SIMPLE_BREAKOUT_SPEC["capital_model"],
        required_signal_tags=required_signal_tags,
        fixed_spec=True,
    )


def _walk_forward_rank(
    trades: Sequence[Mapping[str, Any]],
    features: pd.DataFrame,
    sessions: Sequence[str],
) -> dict[str, Any]:
    folds = _fold_ranges(
        sessions,
        minimum_training_sessions=int(
            WALK_FORWARD_RANK_SPEC["walk_forward"]["minimum_training_sessions"]
        ),
        validation_sessions=int(
            WALK_FORWARD_RANK_SPEC["walk_forward"]["validation_sessions"]
        ),
    )
    scored_oof: list[dict[str, Any]] = []
    baseline_oof: list[dict[str, Any]] = []
    fold_receipts = []
    for fold_index, (validation_start, validation_end) in enumerate(folds, start=1):
        training = features[
            features["feature_complete"]
            & (features["exit_date"] < validation_start)
        ]
        validation = features[
            features["feature_complete"]
            & (features["signal_date"] >= validation_start)
            & (features["signal_date"] <= validation_end)
        ]
        if training.empty or validation.empty:
            continue
        x_train = training[list(FEATURE_NAMES)].to_numpy(dtype=float)
        y_train = training["positive_after_cost"].to_numpy(dtype=float)
        model = _fit_weighted_ridge(
            x_train,
            y_train,
            training["signal_date"].tolist(),
            ridge_lambda=float(WALK_FORWARD_RANK_SPEC["model"]["ridge_lambda"]),
        )
        x_validation = validation[list(FEATURE_NAMES)].to_numpy(dtype=float)
        scores = _predict_ridge(model, x_validation)
        score_digest = hashlib.sha256()
        for row, score in zip(validation.itertuples(index=False), scores):
            trade = dict(trades[int(row.trade_index)])
            trade["rank_score"] = float(score)
            trade["signal_tags"] = [
                "breakout_20d",
                "purged_ridge_rank",
            ]
            scored_oof.append(trade)
            baseline_oof.append(dict(trades[int(row.trade_index)]))
            score_digest.update(
                f"{trade['signal_date']}\0{trade['symbol']}\0{score:.12g}\n".encode(
                    "utf-8"
                )
            )
        coefficients = model["coefficients"]
        fold_receipts.append(
            {
                "fold": fold_index,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "training_candidate_count": int(len(training)),
                "training_signal_date_count": int(training["signal_date"].nunique()),
                "training_last_exit_date": str(training["exit_date"].max()),
                "training_positive_rate_pct": round(
                    float(training["positive_after_cost"].mean()) * 100,
                    2,
                ),
                "validation_candidate_count": int(len(validation)),
                "validation_signal_date_count": int(
                    validation["signal_date"].nunique()
                ),
                "validation_positive_rate_pct": round(
                    float(validation["positive_after_cost"].mean()) * 100,
                    2,
                ),
                "intercept": round(float(coefficients[0]), 8),
                "standardized_coefficients": {
                    name: round(float(value), 8)
                    for name, value in zip(FEATURE_NAMES, coefficients[1:])
                },
                "score_sha256": score_digest.hexdigest(),
            }
        )
    if not scored_oof:
        raise AuditedPITDevelopmentReplayError(
            "walk-forward ranking produced no out-of-fold candidates"
        )
    return {
        "folds": fold_receipts,
        "oof_start_date": min(str(trade["signal_date"]) for trade in scored_oof),
        "oof_end_date": max(str(trade["signal_date"]) for trade in scored_oof),
        "oof_candidate_count": len(scored_oof),
        "oof_candidates_sha256": _sha256(scored_oof),
        "model_rank": _evaluate_fixed_rank(
            scored_oof,
            required_signal_tags=["breakout_20d", "purged_ridge_rank"],
        ),
        "same_window_liquidity_baseline": _evaluate_fixed_rank(
            baseline_oof,
            required_signal_tags=["breakout_20d"],
        ),
    }


def run_audited_pit_walk_forward_rank(
    *,
    settings: Settings,
    audited_pit_universe_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    temporal_contract_path: str | Path,
    expected_temporal_contract_sha256: str,
    start_date: str,
    end_date: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    contract = load_temporal_partition_contract(temporal_contract_path)
    if contract["contract_sha256"] != expected_temporal_contract_sha256:
        raise AuditedPITDevelopmentReplayError("temporal contract hash mismatch")
    assert_range_allowed(contract, "development", start_date, end_date, "backtest")

    try:
        universe = AuditedPointInTimeUniverse.from_file(
            str(audited_pit_universe_path),
            expected_coverage_audit_sha256=expected_coverage_audit_sha256,
            expected_artifact_root_sha256=expected_artifact_root_sha256,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            expected_temporal_role="development",
        )
    except (OSError, TypeError, ValueError, PITReceiptError) as exc:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact verification failed"
        ) from exc
    try:
        if universe.start_date != start_date or universe.end_date != end_date:
            raise AuditedPITDevelopmentReplayError(
                "audited PIT artifact range differs from frozen development range"
            )
        sessions = _exact_membership_sessions(
            universe,
            start_date=start_date,
            end_date=end_date,
        )
        bars = _load_exact_membership_bars(
            universe._require_open(),
            start_date=start_date,
            end_date=end_date,
        )
        trades = _candidate_trades_from_bars(
            bars,
            {},
            settings,
            membership_name_column="membership_name",
            current_universe_bias=False,
        )
        source = {
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "artifact_root_sha256": universe.artifact_root_sha256,
            "temporal_contract_sha256": universe.temporal_contract_sha256,
            "temporal_role": universe.temporal_role,
            "market_session_count": len(sessions),
            "exact_membership_session_count": len(sessions),
            "market_scope": market_scope_contract(),
            "producer_code": _producer_binding(),
        }
    finally:
        universe.close()

    features = _candidate_features(trades, bars)
    feature_rows = features[features["feature_complete"]]
    feature_digest = hashlib.sha256()
    for row in feature_rows.itertuples(index=False):
        values = "\0".join(
            f"{float(getattr(row, name)):.12g}" for name in FEATURE_NAMES
        )
        feature_digest.update(
            f"{row.signal_date}\0{row.symbol}\0{values}\n".encode("utf-8")
        )
    walk_forward = _walk_forward_rank(trades, features, sessions)
    payload = {
        "schema_version": "audited-pit-walk-forward-rank-result/v1",
        "spec": {
            **WALK_FORWARD_RANK_SPEC,
            "spec_sha256": _sha256(WALK_FORWARD_RANK_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "purged_out_of_fold": True,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "feature_complete_candidate_count": int(len(feature_rows)),
        "feature_rows_sha256": feature_digest.hexdigest(),
        "walk_forward": walk_forward,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
