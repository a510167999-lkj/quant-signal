"""Strict audited-PIT replay for industry residual reversal research."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

from app.a_share_universe import _is_excluded_name
from app.audited_pit_development_replay import (
    AuditedPITDevelopmentReplayError,
    _exact_membership_sessions,
    _producer_code_binding,
)
from app.audited_pit_trend_pullback import (
    TREND_PULLBACK_SPEC,
    _fixed_signal_masks,
    _load_suspension_evidence,
    _load_terminal_listing_evidence,
    _stable_sidecar_reference,
    _strict_close_stop_trade,
)
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _sha256,
    _write_content_addressed,
)
from app.execution import assess_entry_executability
from app.research_artifact_replay import ArtifactNativeReplayAdapter
from app.research_backtest import _artifact_open_verdict
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_pit_store import (
    AuditedPointInTimeUniverse,
    PITReceiptError,
)
from app.research_portfolio import _selection_trade_key
from app.research_scope import (
    is_mainboard_chinext_symbol,
    market_scope_contract,
)
from app.research_sweep import _trade_metrics


INDUSTRY_RESIDUAL_SPEC = {
    "schema_version": "development-pit-industry-residual-reversal-strict/v1",
    "signal_tag": "industry_residual_reversal_5d_1d",
    "signal_price_basis": "raw_close_times_session_adj_factor",
    "membership_application": "signal_date_only",
    "minimum_signal_history_sessions": 60,
    "feature_sessions": {
        "short_return_sessions": 1,
        "residual_return_sessions": 5,
        "offset_basis": "global_audited_sse_sessions",
    },
    "industry": {
        "source": "signal_date_exact_pit_daily_universe_industry",
        "normalization": "trim",
        "minimum_complete_members": 10,
        "median_includes_target": True,
        "median_even_rule": "arithmetic_mean_of_middle_two",
    },
    "signal": {
        "r5_residual": "stock_lt_industry_median",
        "r1_residual": "stock_gt_industry_median",
        "comparison_rounding": "none_float64",
    },
    "entry_signal_offset_sessions": 1,
    "planned_exit_signal_offset_sessions": 6,
    "entry_numeric_abs_tolerance": 1e-9,
    "hold_days": 5,
    "close_stop_loss_pct": 5.0,
    "close_stop_execution": "first_strictly_fillable_open_after_trigger_close",
    "blocked_sell_policy": "retry_each_following_open",
    "coverage_end_policy": "uniform_cutoff_before_entry_or_outcome_query",
    "terminal_listing_policy": (
        "right_censor_without_settlement_return_and_block_if_selected"
    ),
    "intraday_stop_fill_assumed": False,
    "exposure_multiplier": 1.0,
    "top_n": 3,
    "max_active_positions": 3,
    "max_active_positions_per_industry": 1,
    "capital_model": "slot-daily",
    "roundtrip_cost_bps": 25.0,
    "slippage_bps": 10.0,
    "annual_financing_rate_pct": 8.0,
    "entry_execution": dict(SIMPLE_BREAKOUT_SPEC["entry_execution"]),
    "main_rank": [
        "industry_r5_gap_desc",
        "signal_date_amount_desc",
        "symbol_asc",
    ],
    "amount_baseline_rank": ["signal_date_amount_desc", "symbol_asc"],
    "amount_baseline": {
        "candidate_table": "shared_exact",
        "industry_capacity": "same_signal_date_frozen_max_one",
        "entry_contract": "shared_exact",
        "portfolio_replay": "independent_empty",
        "performance_is_advancement_gate": False,
        "evidence_completeness_is_advancement_gate": True,
    },
    "overlap": {
        "families": [
            "breakout_20d",
            "trend_pullback_ma20_reclaim",
        ],
        "comparison_stage": (
            "after_uniform_tail_cutoff_and_strict_entry_before_outcome"
        ),
        "candidate_key_fields": ["symbol", "signal_date", "entry_date"],
        "used_as_filter": False,
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

_SYMBOL_FEATURE_COLUMNS = (
    "date",
    "ts_code",
    "close",
    "amount",
    "adj_factor",
    "membership_name",
    "membership_industry",
    "membership_receipt_dataset",
    "membership_receipt_partition",
)


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    root = Path(__file__).resolve().parent
    module_names = (
        "artifact_outcome_evidence.py",
        "audited_pit_trend_pullback.py",
        "execution.py",
        "research_artifact_replay.py",
        "research_backtest.py",
        "research_common.py",
        "research_equity.py",
        "research_portfolio.py",
        "research_sweep.py",
    )
    dependency_modules = [
        {
            "module": name,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
        for name in module_names
    ]
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "strict_dependency_modules": dependency_modules,
        "industry_residual_module_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }
    return {
        "schema_version": "audited-pit-industry-residual-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _assert_producer_binding_unchanged(
    expected: Mapping[str, Any],
) -> None:
    if _producer_binding() != dict(expected):
        raise AuditedPITDevelopmentReplayError(
            "industry residual producer code changed during replay"
        )


def _frames_by_symbol(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    required = {
        "date",
        "ts_code",
        "open",
        "high",
        "low",
        "close",
        "amount",
        "adj_factor",
        "membership_name",
    }
    if not required.issubset(bars.columns):
        raise ValueError("industry residual bars cannot build symbol frames")
    frames: dict[str, pd.DataFrame] = {}
    ts_codes_by_symbol: dict[str, str] = {}
    for ts_code, group in bars.groupby("ts_code", sort=True):
        symbol = str(ts_code)[:6]
        previous = ts_codes_by_symbol.get(symbol)
        if previous is not None and previous != str(ts_code):
            raise ValueError("industry residual symbol code is ambiguous")
        ts_codes_by_symbol[symbol] = str(ts_code)
        frame = group.sort_values("date", kind="mergesort").reset_index(
            drop=True
        )
        dates = [str(value) for value in frame["date"].tolist()]
        if len(dates) != len(set(dates)):
            raise ValueError("industry residual frame has duplicate dates")
        frames[symbol] = frame
    return frames


def _eligible_name(value: Any) -> str | None:
    if pd.isna(value):
        return None
    name = str(value or "").strip()
    if not name or _is_excluded_name(name):
        return None
    return name


def _load_industry_feature_bars(
    connection: Any,
    *,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_sql_query(
        """
        SELECT daily.trade_date AS date, daily.ts_code, daily.open, daily.high,
               daily.low, daily.close, daily.pre_close, daily.amount,
               adjustment.adj_factor, membership.name AS membership_name,
               membership.industry AS membership_industry,
               membership.receipt_dataset AS membership_receipt_dataset,
               membership.receipt_partition AS membership_receipt_partition,
               EXISTS(
                   SELECT 1
                   FROM market_session_generation_rows_suspend_d AS suspension
                   WHERE suspension.generation_id = daily.generation_id
                     AND suspension.ts_code = daily.ts_code
                     AND suspension.trade_date = daily.trade_date
                     AND suspension.suspend_type = 'S'
               ) AS suspended
        FROM market_session_generation_rows_daily AS daily
        JOIN market_session_generation_head AS head
          ON head.trade_date = daily.trade_date
         AND head.generation_id = daily.generation_id
        JOIN market_session_generation_rows_adj_factor AS adjustment
          ON adjustment.generation_id = daily.generation_id
         AND adjustment.trade_date = daily.trade_date
         AND adjustment.ts_code = daily.ts_code
        LEFT JOIN daily_universe AS membership
          ON membership.trade_date = daily.trade_date
         AND membership.ts_code = daily.ts_code
        WHERE daily.trade_date BETWEEN ? AND ?
        ORDER BY daily.ts_code, daily.trade_date
        """,
        connection,
        params=(start_date, end_date),
    )
    if frame.empty:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact has no industry feature bars"
        )
    source_row_count = len(frame)
    frame = frame[
        frame["ts_code"].map(is_mainboard_chinext_symbol)
    ].copy()
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "amount",
        "adj_factor",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=["open", "high", "low", "close", "amount", "adj_factor"]
    )
    frame = frame[
        (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["amount"] >= 0)
        & (frame["adj_factor"] > 0)
    ].copy()
    if frame.empty:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact has no eligible industry feature bars"
        )
    frame["date"] = frame["date"].astype(str)
    frame["ts_code"] = frame["ts_code"].astype(str)
    if frame.duplicated(["ts_code", "date"]).any():
        raise AuditedPITDevelopmentReplayError(
            "industry feature bars contain duplicate symbol dates"
        )
    exact_membership = (
        frame["membership_receipt_dataset"].eq("bak_basic")
        & frame["membership_receipt_partition"].astype(str).eq(frame["date"])
    )
    membership_receipt_present = (
        frame["membership_receipt_dataset"].notna()
        | frame["membership_receipt_partition"].notna()
    )
    if bool((membership_receipt_present & ~exact_membership).any()):
        raise AuditedPITDevelopmentReplayError(
            "industry feature bar has a non-exact membership receipt"
        )
    membership_refs = sorted(
        {
            (
                str(row.membership_receipt_dataset),
                str(row.membership_receipt_partition),
            )
            for row in frame[exact_membership].itertuples(index=False)
        }
    )
    row_keys = [
        (
            str(row.date),
            str(row.ts_code),
            str(row.membership_industry)
            if not pd.isna(row.membership_industry)
            else "",
            str(row.membership_receipt_dataset)
            if not pd.isna(row.membership_receipt_dataset)
            else "",
            str(row.membership_receipt_partition)
            if not pd.isna(row.membership_receipt_partition)
            else "",
        )
        for row in frame.itertuples(index=False)
    ]
    receipt = {
        "schema_version": "industry-feature-bar-loader-receipt/v1",
        "range": {"start_date": start_date, "end_date": end_date},
        "source_row_count": source_row_count,
        "eligible_market_row_count": len(frame),
        "matched_exact_membership_row_count": int(exact_membership.sum()),
        "missing_exact_membership_row_count": int((~exact_membership).sum()),
        "membership_receipt_refs": [
            {"dataset": dataset, "partition": partition}
            for dataset, partition in membership_refs
        ],
        "membership_receipt_refs_sha256": _sha256(membership_refs),
        "row_keys_sha256": _sha256(row_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return frame.reset_index(drop=True), receipt


def _build_symbol_return_features(
    bars: pd.DataFrame,
    sessions: Sequence[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    missing = [column for column in _SYMBOL_FEATURE_COLUMNS if column not in bars]
    if missing:
        raise ValueError("industry residual bars are missing required columns")
    session_dates = [str(value) for value in sessions]
    if (
        not session_dates
        or len(session_dates) != len(set(session_dates))
        or session_dates != sorted(session_dates)
    ):
        raise ValueError("industry residual sessions must be ordered and unique")
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    values = bars.copy()
    values["date"] = values["date"].astype(str)
    values["ts_code"] = values["ts_code"].astype(str)
    if values.duplicated(["ts_code", "date"]).any():
        raise ValueError("industry residual bars contain duplicate symbol dates")
    values["session_position"] = values["date"].map(session_positions)
    if values["session_position"].isna().any():
        raise ValueError("industry residual bars contain dates outside session grid")
    values["session_position"] = values["session_position"].astype(int)
    for column in ("close", "amount", "adj_factor"):
        values[column] = pd.to_numeric(values[column], errors="coerce")
    values["adjusted_close"] = values["close"] * values["adj_factor"]
    valid_price = (
        values["close"].map(
            lambda value: math.isfinite(float(value))
            if not pd.isna(value)
            else False
        )
        & values["adj_factor"].map(
            lambda value: math.isfinite(float(value))
            if not pd.isna(value)
            else False
        )
        & values["adjusted_close"].map(
            lambda value: math.isfinite(float(value))
            if not pd.isna(value)
            else False
        )
        & (values["close"] > 0)
        & (values["adj_factor"] > 0)
        & (values["adjusted_close"] > 0)
    )
    invalid_price_count = int((~valid_price).sum())
    values = values[valid_price].copy()
    values = values.sort_values(
        ["ts_code", "session_position"],
        kind="mergesort",
    ).reset_index(drop=True)
    values["observed_history_count"] = (
        values.groupby("ts_code", sort=False).cumcount() + 1
    )
    lookup = values[
        ["ts_code", "session_position", "adjusted_close"]
    ].copy()
    previous_one = lookup.rename(
        columns={"adjusted_close": "adjusted_close_t_minus_1"}
    )
    previous_one["session_position"] += 1
    previous_five = lookup.rename(
        columns={"adjusted_close": "adjusted_close_t_minus_5"}
    )
    previous_five["session_position"] += 5
    merged = values.merge(
        previous_one,
        on=["ts_code", "session_position"],
        how="left",
        validate="one_to_one",
    ).merge(
        previous_five,
        on=["ts_code", "session_position"],
        how="left",
        validate="one_to_one",
    )
    status_counts: Counter[str] = Counter(
        {"invalid_price_or_adjustment": invalid_price_count}
    )
    insufficient_history = (
        merged["observed_history_count"]
        < int(INDUSTRY_RESIDUAL_SPEC["minimum_signal_history_sessions"])
    )
    status_counts["insufficient_history"] = int(insufficient_history.sum())
    missing_exact = (
        ~insufficient_history
        & (
            merged["adjusted_close_t_minus_1"].isna()
            | merged["adjusted_close_t_minus_5"].isna()
        )
    )
    status_counts["missing_exact_feature_session"] = int(missing_exact.sum())
    name_values = merged["membership_name"].map(_eligible_name)
    industry_values = merged["membership_industry"].map(
        lambda value: "" if pd.isna(value) else str(value).strip()
    )
    invalid_membership = name_values.isna() | (industry_values == "")
    status_counts["invalid_signal_date_membership"] = int(
        invalid_membership.sum()
    )
    eligible = ~(insufficient_history | missing_exact | invalid_membership)
    features = merged[eligible].copy()
    features["name"] = name_values[eligible]
    features["industry_key"] = industry_values[eligible]
    features["symbol"] = features["ts_code"].str[:6]
    features["r1"] = (
        features["adjusted_close"]
        / features["adjusted_close_t_minus_1"]
        - 1.0
    )
    features["r5"] = (
        features["adjusted_close"]
        / features["adjusted_close_t_minus_5"]
        - 1.0
    )
    finite_returns = features["r1"].map(math.isfinite) & features["r5"].map(
        math.isfinite
    )
    status_counts["nonfinite_return"] = int((~finite_returns).sum())
    features = features[finite_returns].copy()
    output_columns = [
        "date",
        "ts_code",
        "symbol",
        "name",
        "industry_key",
        "amount",
        "adjusted_close",
        "r1",
        "r5",
        "observed_history_count",
        "membership_receipt_dataset",
        "membership_receipt_partition",
    ]
    features = features[output_columns].sort_values(
        ["date", "industry_key", "ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    receipt = {
        "schema_version": "industry-residual-symbol-feature-receipt/v1",
        "parameters": {
            "minimum_signal_history_sessions": int(
                INDUSTRY_RESIDUAL_SPEC["minimum_signal_history_sessions"]
            ),
            "exact_feature_session_offsets": [0, 1, 5],
            "offset_basis": "global_audited_sse_sessions",
            "price_basis": "raw_close_times_session_adj_factor",
        },
        "input_row_count": len(bars),
        "feature_row_count": len(features),
        "status_counts": dict(sorted(status_counts.items())),
        "feature_keys_sha256": _sha256(
            [
                f"{row.date}|{row.ts_code}"
                for row in features.itertuples(index=False)
            ]
        ),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return features, receipt


def _deterministic_industry_record(
    signal_date: str,
    industry_key: str,
    members: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = []
    for member in members:
        symbol = str(member.get("symbol") or "")
        ts_code = str(member.get("ts_code") or "")
        r1 = float(member["r1"])
        r5 = float(member["r5"])
        if (
            not symbol
            or not ts_code
            or not math.isfinite(r1)
            or not math.isfinite(r5)
        ):
            raise ValueError("industry median member is invalid")
        normalized.append(
            {
                "symbol": symbol,
                "ts_code": ts_code,
                "r1": r1,
                "r5": r5,
                "membership_receipt_dataset": str(
                    member.get("membership_receipt_dataset") or ""
                ),
                "membership_receipt_partition": str(
                    member.get("membership_receipt_partition") or ""
                ),
            }
        )
    if len({item["ts_code"] for item in normalized}) != len(normalized):
        raise ValueError("industry median members contain duplicates")
    r1_inputs = sorted(
        (
            {"symbol": item["symbol"], "ts_code": item["ts_code"], "value": item["r1"]}
            for item in normalized
        ),
        key=lambda item: (item["value"], item["ts_code"]),
    )
    r5_inputs = sorted(
        (
            {"symbol": item["symbol"], "ts_code": item["ts_code"], "value": item["r5"]}
            for item in normalized
        ),
        key=lambda item: (item["value"], item["ts_code"]),
    )

    def median(inputs: Sequence[Mapping[str, Any]]) -> float:
        count = len(inputs)
        if count == 0:
            raise ValueError("industry median group is empty")
        middle = count // 2
        if count % 2:
            return float(inputs[middle]["value"])
        return (
            float(inputs[middle - 1]["value"])
            + float(inputs[middle]["value"])
        ) / 2.0

    member_keys = sorted(item["ts_code"] for item in normalized)
    membership_receipt_refs = sorted(
        {
            (
                item["membership_receipt_dataset"],
                item["membership_receipt_partition"],
            )
            for item in normalized
        }
    )
    if any(
        dataset != "bak_basic" or partition != str(signal_date)
        for dataset, partition in membership_receipt_refs
    ):
        raise ValueError("industry median membership receipt is not exact PIT")
    industry_evidence = sorted(
        (
            {
                "ts_code": item["ts_code"],
                "membership_receipt_dataset": item[
                    "membership_receipt_dataset"
                ],
                "membership_receipt_partition": item[
                    "membership_receipt_partition"
                ],
            }
            for item in normalized
        ),
        key=lambda item: item["ts_code"],
    )
    record = {
        "signal_date": str(signal_date),
        "industry_key": str(industry_key).strip(),
        "member_count": len(normalized),
        "member_keys": member_keys,
        "member_keys_sha256": _sha256(member_keys),
        "membership_receipt_refs": [
            {"dataset": dataset, "partition": partition}
            for dataset, partition in membership_receipt_refs
        ],
        "sorted_r1_inputs_sha256": _sha256(r1_inputs),
        "sorted_r5_inputs_sha256": _sha256(r5_inputs),
        "industry_evidence_sha256": _sha256(industry_evidence),
        "median_r1": median(r1_inputs),
        "median_r5": median(r5_inputs),
    }
    record["group_sha256"] = _sha256(record)
    return record


def _is_residual_candidate(
    *,
    stock_r5: float,
    industry_median_r5: float,
    stock_r1: float,
    industry_median_r1: float,
) -> bool:
    values = (
        float(stock_r5),
        float(industry_median_r5),
        float(stock_r1),
        float(industry_median_r1),
    )
    return bool(
        all(math.isfinite(value) for value in values)
        and values[0] - values[1] < 0.0
        and values[2] - values[3] > 0.0
    )


def _build_industry_features(
    symbol_features: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "date",
        "ts_code",
        "symbol",
        "industry_key",
        "r1",
        "r5",
        "amount",
        "membership_receipt_dataset",
        "membership_receipt_partition",
    }
    if not required.issubset(symbol_features.columns):
        raise ValueError("industry feature rows are missing required columns")
    status_counts: Counter[str] = Counter()
    groups: list[dict[str, Any]] = []
    enriched: list[pd.DataFrame] = []
    minimum = int(
        INDUSTRY_RESIDUAL_SPEC["industry"]["minimum_complete_members"]
    )
    for (signal_date, industry_key), raw_group in symbol_features.groupby(
        ["date", "industry_key"],
        sort=True,
    ):
        group = raw_group.sort_values("ts_code", kind="mergesort").copy()
        if len(group) < minimum:
            status_counts["industry_member_count_lt_10"] += 1
            continue
        record = _deterministic_industry_record(
            str(signal_date),
            str(industry_key),
            group.to_dict("records"),
        )
        group["industry_median_r1"] = float(record["median_r1"])
        group["industry_median_r5"] = float(record["median_r5"])
        group["industry_r1_residual"] = (
            group["r1"] - group["industry_median_r1"]
        )
        group["industry_r5_residual"] = (
            group["r5"] - group["industry_median_r5"]
        )
        group["industry_r5_gap"] = -group["industry_r5_residual"]
        group["residual_signal"] = [
            _is_residual_candidate(
                stock_r5=row.r5,
                industry_median_r5=record["median_r5"],
                stock_r1=row.r1,
                industry_median_r1=record["median_r1"],
            )
            for row in group.itertuples(index=False)
        ]
        raw_candidate_keys = sorted(
            f"{row.date}|{row.symbol}"
            for row in group[group["residual_signal"]].itertuples(
                index=False
            )
        )
        record.pop("group_sha256")
        record["raw_candidate_count"] = len(raw_candidate_keys)
        record["raw_candidate_keys"] = raw_candidate_keys
        record["raw_candidate_keys_sha256"] = _sha256(
            raw_candidate_keys
        )
        record["group_sha256"] = _sha256(record)
        group["industry_group_sha256"] = record["group_sha256"]
        groups.append(record)
        enriched.append(group)
    complete = (
        pd.concat(enriched, ignore_index=True)
        if enriched
        else symbol_features.iloc[0:0].copy()
    )
    complete = complete.sort_values(
        ["date", "industry_key", "ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    receipt = {
        "schema_version": "industry-residual-cross-section-receipt/v1",
        "minimum_complete_members": minimum,
        "group_count": len(groups),
        "complete_member_row_count": len(complete),
        "status_counts": dict(sorted(status_counts.items())),
        "groups": groups,
        "groups_sha256": _sha256(groups),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return complete, receipt


def verify_industry_feature_receipt(
    symbol_features: pd.DataFrame,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        _, expected = _build_industry_features(symbol_features)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("industry feature receipt is invalid") from exc
    if dict(receipt) != expected:
        raise ValueError("industry feature receipt replay mismatch")
    return {
        "verified": True,
        "receipt_sha256": expected["receipt_sha256"],
        "group_count": expected["group_count"],
        "complete_member_row_count": expected["complete_member_row_count"],
    }


def _build_residual_raw_candidates(
    industry_features: pd.DataFrame,
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {
        "date",
        "ts_code",
        "symbol",
        "name",
        "industry_key",
        "r1",
        "r5",
        "amount",
        "industry_median_r1",
        "industry_median_r5",
        "industry_r1_residual",
        "industry_r5_residual",
        "industry_r5_gap",
        "industry_group_sha256",
        "residual_signal",
        "membership_receipt_dataset",
        "membership_receipt_partition",
    }
    if not required.issubset(industry_features.columns):
        raise ValueError("residual candidate rows are missing required columns")
    frame_positions: dict[str, dict[str, int]] = {}
    for symbol, frame in frames_by_symbol.items():
        dates = [str(value) for value in frame["date"].tolist()]
        if len(dates) != len(set(dates)):
            raise ValueError("residual candidate frame has duplicate dates")
        frame_positions[str(symbol)] = {
            trade_date: index for index, trade_date in enumerate(dates)
        }
    candidates: list[dict[str, Any]] = []
    feature_values: list[dict[str, Any]] = []
    selected_rows = industry_features[
        industry_features["residual_signal"].astype(bool)
    ]
    for row in selected_rows.itertuples(index=False):
        signal_date = str(row.date)
        symbol = str(row.symbol)
        signal_index = frame_positions.get(symbol, {}).get(signal_date)
        if signal_index is None:
            raise ValueError("residual candidate has no matching signal bar")
        amount = float(row.amount)
        numeric_values = (
            float(row.r1),
            float(row.r5),
            float(row.industry_median_r1),
            float(row.industry_median_r5),
            float(row.industry_r1_residual),
            float(row.industry_r5_residual),
            float(row.industry_r5_gap),
            amount,
        )
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("residual candidate feature is nonfinite")
        feature = {
            "signal_date": signal_date,
            "symbol": symbol,
            "ts_code": str(row.ts_code),
            "signal_industry": str(row.industry_key),
            "stock_r1": numeric_values[0],
            "stock_r5": numeric_values[1],
            "industry_median_r1": numeric_values[2],
            "industry_median_r5": numeric_values[3],
            "industry_r1_residual": numeric_values[4],
            "industry_r5_residual": numeric_values[5],
            "industry_r5_gap": numeric_values[6],
            "candidate_amount": amount,
            "industry_group_sha256": str(row.industry_group_sha256),
            "membership_receipt_dataset": str(
                row.membership_receipt_dataset
            ),
            "membership_receipt_partition": str(
                row.membership_receipt_partition
            ),
        }
        feature_values.append(feature)
        candidates.append(
            {
                **feature,
                "name": str(row.name),
                "signal_frame_index": int(signal_index),
            }
        )
    candidates.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    feature_values.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    raw_keys = [
        f"{candidate['signal_date']}|{candidate['symbol']}"
        for candidate in candidates
    ]
    if len(raw_keys) != len(set(raw_keys)):
        raise ValueError("residual raw candidates contain duplicate keys")
    receipt = {
        "schema_version": "industry-residual-raw-candidate-receipt/v1",
        "signal_tag": INDUSTRY_RESIDUAL_SPEC["signal_tag"],
        "raw_candidate_count": len(candidates),
        "raw_candidate_keys": raw_keys,
        "raw_candidate_keys_sha256": _sha256(raw_keys),
        "raw_candidate_feature_values_sha256": _sha256(feature_values),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return candidates, receipt


def _apply_uniform_tail_cutoff(
    candidates: Sequence[Mapping[str, Any]],
    *,
    sessions: Sequence[str],
    family: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_dates = [str(value) for value in sessions]
    if (
        not session_dates
        or len(session_dates) != len(set(session_dates))
        or session_dates != sorted(session_dates)
    ):
        raise ValueError("uniform tail cutoff sessions are invalid")
    positions = {
        trade_date: index
        for index, trade_date in enumerate(session_dates)
    }
    offset = int(
        INDUSTRY_RESIDUAL_SPEC["planned_exit_signal_offset_sessions"]
    )
    ordered = sorted(
        (dict(candidate) for candidate in candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    input_keys: list[str] = []
    kept: list[dict[str, Any]] = []
    kept_keys: list[str] = []
    cut_keys: list[str] = []
    for candidate in ordered:
        signal_date = str(candidate.get("signal_date") or "")[:10]
        symbol = str(candidate.get("symbol") or "")
        key = f"{signal_date}|{symbol}"
        input_keys.append(key)
        signal_position = positions.get(signal_date)
        if signal_position is None:
            raise ValueError("tail cutoff signal date is outside sessions")
        planned_exit_position = signal_position + offset
        if planned_exit_position >= len(session_dates):
            cut_keys.append(key)
            continue
        kept.append(
            {
                **candidate,
                "planned_exit_date": session_dates[planned_exit_position],
            }
        )
        kept_keys.append(key)
    if len(input_keys) != len(set(input_keys)):
        raise ValueError("tail cutoff candidates contain duplicate keys")
    receipt = {
        "schema_version": "uniform-signal-tail-cutoff-receipt/v1",
        "family": str(family),
        "required_signal_to_exit_offset_sessions": offset,
        "coverage_end_date": session_dates[-1],
        "input_count": len(ordered),
        "input_keys_sha256": _sha256(input_keys),
        "kept_count": len(kept),
        "kept_keys_sha256": _sha256(kept_keys),
        "cut_count": len(cut_keys),
        "cut_keys": cut_keys,
        "cut_keys_sha256": _sha256(cut_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return kept, receipt


def _build_legacy_raw_candidates(
    frames_by_symbol: Mapping[str, pd.DataFrame],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    family_columns = {
        "breakout_20d": "breakout",
        "trend_pullback_ma20_reclaim": "pullback",
    }
    families: dict[str, list[dict[str, Any]]] = {
        family: [] for family in family_columns
    }
    rejected_name_counts: Counter[str] = Counter()
    for symbol in sorted(frames_by_symbol):
        frame = frames_by_symbol[symbol]
        dates = [str(value) for value in frame["date"].tolist()]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("legacy overlap frame dates are invalid")
        masks = _fixed_signal_masks(frame)
        for family, column in family_columns.items():
            for signal_index in masks.index[masks[column].astype(bool)]:
                name = _eligible_name(
                    frame.at[int(signal_index), "membership_name"]
                )
                if name is None:
                    rejected_name_counts[family] += 1
                    continue
                families[family].append(
                    {
                        "signal_date": str(
                            frame.at[int(signal_index), "date"]
                        ),
                        "symbol": str(symbol),
                        "name": name,
                        "signal_frame_index": int(signal_index),
                    }
                )
    receipt_families: dict[str, dict[str, Any]] = {}
    for family in sorted(families):
        candidates = sorted(
            families[family],
            key=lambda item: (item["signal_date"], item["symbol"]),
        )
        families[family] = candidates
        keys = [
            f"{candidate['signal_date']}|{candidate['symbol']}"
            for candidate in candidates
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("legacy overlap candidates contain duplicates")
        receipt_families[family] = {
            "raw_candidate_count": len(candidates),
            "raw_candidate_keys_sha256": _sha256(keys),
            "signal_date_membership_rejected_count": int(
                rejected_name_counts[family]
            ),
        }
    receipt = {
        "schema_version": "legacy-fixed-signal-raw-candidate-receipt/v1",
        "signal_source": "audited_pit_trend_pullback._fixed_signal_masks",
        "families": receipt_families,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return families, receipt


def _candidate_table_payload(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        trade = dict(candidate)
        industry_key = str(trade.get("signal_industry") or "").strip()
        residual_score = float(trade.get("industry_r5_gap"))
        amount = float(trade.get("candidate_amount") or 0.0)
        if (
            not industry_key
            or not math.isfinite(residual_score)
            or not math.isfinite(amount)
        ):
            raise ValueError("industry selection candidate is invalid")
        rows.append(
            {
                "trade_key": _selection_trade_key(trade),
                "signal_industry": industry_key,
                "industry_r5_gap": residual_score,
                "candidate_amount": amount,
                "right_censored": trade.get("right_censored") is True,
            }
        )
    rows.sort(key=lambda item: item["trade_key"])
    if len({item["trade_key"] for item in rows}) != len(rows):
        raise ValueError("industry selection candidates contain duplicate trade keys")
    return rows


def _selection_rank_key(
    trade: Mapping[str, Any],
    *,
    rank_mode: str,
) -> tuple[Any, ...]:
    symbol = str(trade.get("symbol") or "")
    amount = float(trade.get("candidate_amount") or 0.0)
    if rank_mode == "industry_residual":
        residual_score = float(trade.get("industry_r5_gap"))
        if not math.isfinite(residual_score):
            raise ValueError("industry residual rank score is invalid")
        return (-residual_score, -amount, symbol)
    if rank_mode == "signal_date_amount":
        return (-amount, symbol)
    raise ValueError("industry selection rank mode is invalid")


def _select_with_industry_cap_receipt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    top_n: int,
    max_active_positions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if top_n <= 0 or max_active_positions <= 0:
        raise ValueError("industry selection capacities must be positive")
    candidate_rows = _candidate_table_payload(candidates)
    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        trade = dict(candidate)
        signal_date = str(trade.get("signal_date") or "")[:10]
        if not signal_date:
            raise ValueError("industry selection signal date is missing")
        by_signal_date[signal_date].append(trade)

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
                "industry selection candidates contain duplicate trade keys"
            )
        all_ordered_keys.extend(ordered_keys)
        active_symbols = {
            str(trade.get("symbol") or "")
            for trade in active_positions
        }
        active_industries = {
            str(trade.get("signal_industry") or "").strip()
            for trade in active_positions
        }
        day_selected = 0
        day_selected_keys: list[str] = []
        decisions: list[dict[str, str]] = []
        for index, trade in enumerate(trades):
            trade_key = ordered_keys[index]
            symbol = str(trade.get("symbol") or "")
            industry_key = str(trade.get("signal_industry") or "").strip()
            if symbol in active_symbols:
                decisions.append(
                    {"trade_key": trade_key, "decision": "active_symbol"}
                )
                continue
            if industry_key in active_industries:
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
            active_symbols.add(symbol)
            active_industries.add(industry_key)
            day_selected += 1
            day_selected_keys.append(trade_key)
            decisions.append(
                {"trade_key": trade_key, "decision": "selected"}
            )
            if day_selected >= top_n:
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
                "selected_trade_keys": day_selected_keys,
            }
        )
    selected_keys = [_selection_trade_key(trade) for trade in selected]
    payload = {
        "schema_version": "industry-capped-portfolio-selection-receipt/v1",
        "parameters": {
            "rank_mode": rank_mode,
            "top_n": int(top_n),
            "max_active_positions": int(max_active_positions),
            "max_active_positions_per_industry": 1,
            "same_day_exit_before_signal_selection": True,
            "industry_source": "signal_date_frozen",
        },
        "candidate_count": len(candidate_rows),
        "candidate_table_sha256": _sha256(candidate_rows),
        "ordered_candidate_trade_keys_sha256": _sha256(all_ordered_keys),
        "selected_count": len(selected_keys),
        "selected_trade_keys": selected_keys,
        "selected_trade_keys_sha256": _sha256(selected_keys),
        "days": days,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return selected, payload


def verify_industry_selection_receipt(
    candidates: Sequence[Mapping[str, Any]],
    receipt: Mapping[str, Any],
    *,
    expected_rank_mode: str,
) -> dict[str, Any]:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version")
        != "industry-capped-portfolio-selection-receipt/v1"
    ):
        raise ValueError("industry selection receipt is invalid")
    parameters = receipt.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("industry selection receipt is invalid")
    frozen_rank_modes = {"industry_residual", "signal_date_amount"}
    if (
        expected_rank_mode not in frozen_rank_modes
        or parameters.get("rank_mode") != expected_rank_mode
        or parameters.get("top_n") != INDUSTRY_RESIDUAL_SPEC["top_n"]
        or parameters.get("max_active_positions")
        != INDUSTRY_RESIDUAL_SPEC["max_active_positions"]
        or parameters.get("max_active_positions_per_industry") != 1
        or parameters.get("same_day_exit_before_signal_selection") is not True
        or parameters.get("industry_source") != "signal_date_frozen"
    ):
        raise ValueError(
            "industry selection receipt differs from frozen strategy"
        )
    try:
        _, expected = _select_with_industry_cap_receipt(
            candidates,
            rank_mode=str(parameters["rank_mode"]),
            top_n=int(parameters["top_n"]),
            max_active_positions=int(parameters["max_active_positions"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("industry selection receipt is invalid") from exc
    if dict(receipt) != expected:
        raise ValueError("industry selection receipt replay mismatch")
    return {
        "verified": True,
        "receipt_sha256": expected["receipt_sha256"],
        "candidate_count": expected["candidate_count"],
        "selected_count": expected["selected_count"],
    }


def _selected_evidence_complete(
    selected: Sequence[Mapping[str, Any]],
) -> bool:
    return not any(trade.get("right_censored") is True for trade in selected)


def _advancement_gate_passes(
    main_row: Mapping[str, Any],
    amount_baseline_row: Mapping[str, Any],
) -> bool:
    return bool(
        main_row.get("target_all_pass")
        and main_row.get("target_rolling_12m_stability_pass")
        and main_row.get("evidence_complete")
        and amount_baseline_row.get("evidence_complete")
    )


def _overlap_receipt(
    new_keys: set[tuple[str, str, str]],
    legacy_keys: Mapping[str, set[tuple[str, str, str]]],
) -> dict[str, Any]:
    normalized_new = {
        (str(symbol), str(signal_date), str(entry_date))
        for symbol, signal_date, entry_date in new_keys
    }
    families: dict[str, dict[str, Any]] = {}
    new_serialized = sorted("|".join(key) for key in normalized_new)
    for family in sorted(legacy_keys):
        old = {
            (str(symbol), str(signal_date), str(entry_date))
            for symbol, signal_date, entry_date in legacy_keys[family]
        }
        intersection = normalized_new & old
        union = normalized_new | old
        old_serialized = sorted("|".join(key) for key in old)
        intersection_serialized = sorted("|".join(key) for key in intersection)
        families[family] = {
            "new_candidate_count": len(normalized_new),
            "old_candidate_count": len(old),
            "intersection_count": len(intersection),
            "union_count": len(union),
            "intersection_over_new": (
                len(intersection) / len(normalized_new)
                if normalized_new
                else 0.0
            ),
            "intersection_over_old": (
                len(intersection) / len(old) if old else 0.0
            ),
            "jaccard": len(intersection) / len(union) if union else 0.0,
            "new_keys_sha256": _sha256(new_serialized),
            "old_keys_sha256": _sha256(old_serialized),
            "intersection_keys_sha256": _sha256(
                intersection_serialized
            ),
        }
    payload = {
        "schema_version": "strict-entry-candidate-overlap-receipt/v1",
        "candidate_key_fields": ["symbol", "signal_date", "entry_date"],
        "comparison_stage": INDUSTRY_RESIDUAL_SPEC["overlap"][
            "comparison_stage"
        ],
        "legacy_signal_contracts": {
            "breakout_20d_spec_sha256": _sha256(SIMPLE_BREAKOUT_SPEC),
            "trend_pullback_spec_sha256": _sha256(TREND_PULLBACK_SPEC),
        },
        "families": families,
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def _assert_strict_execution_contract() -> None:
    comparisons = (
        (
            INDUSTRY_RESIDUAL_SPEC["entry_execution"],
            TREND_PULLBACK_SPEC["entry_execution"],
            "entry execution",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["hold_days"],
            TREND_PULLBACK_SPEC["hold_days"],
            "hold days",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["close_stop_loss_pct"],
            TREND_PULLBACK_SPEC["close_stop_loss_pct"],
            "close stop",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["capital_model"],
            TREND_PULLBACK_SPEC["capital_model"],
            "capital model",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["exposure_multiplier"],
            TREND_PULLBACK_SPEC["exposure_multiplier"],
            "exposure multiplier",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["roundtrip_cost_bps"],
            TREND_PULLBACK_SPEC["roundtrip_cost_bps"],
            "roundtrip cost",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["slippage_bps"],
            TREND_PULLBACK_SPEC["slippage_bps"],
            "slippage",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["annual_financing_rate_pct"],
            TREND_PULLBACK_SPEC["annual_financing_rate_pct"],
            "financing",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["blocked_sell_policy"],
            TREND_PULLBACK_SPEC["blocked_sell_policy"],
            "blocked sell",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["terminal_listing_policy"],
            TREND_PULLBACK_SPEC["terminal_listing_policy"],
            "terminal listing",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["top_n"],
            TREND_PULLBACK_SPEC["top_n"],
            "daily selection capacity",
        ),
        (
            INDUSTRY_RESIDUAL_SPEC["max_active_positions"],
            TREND_PULLBACK_SPEC["max_active_positions"],
            "portfolio capacity",
        ),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            raise ValueError(f"strict industry residual {label} contract drifted")


def _preflight_strict_entries(
    raw_candidates: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    sessions: Sequence[str],
    adapter: Any,
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[tuple[str, str, str], dict[str, Any]],
]:
    _assert_strict_execution_contract()
    session_dates = [str(value) for value in sessions]
    if (
        not session_dates
        or len(session_dates) != len(set(session_dates))
        or session_dates != sorted(session_dates)
    ):
        raise ValueError("strict entry preflight sessions are invalid")
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    cache = verdict_cache if verdict_cache is not None else {}
    status_counts: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    executable: list[dict[str, Any]] = []
    ordered = sorted(
        (dict(candidate) for candidate in raw_candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    raw_identity_keys = [
        (
            str(candidate.get("symbol") or ""),
            str(candidate.get("signal_date") or "")[:10],
        )
        for candidate in ordered
    ]
    if len(raw_identity_keys) != len(set(raw_identity_keys)):
        raise ValueError("entry preflight candidates contain duplicate keys")
    date_positions_by_symbol: dict[str, dict[str, int]] = {}
    for symbol in sorted(
        {str(candidate.get("symbol") or "") for candidate in ordered}
    ):
        frame = frames_by_symbol.get(symbol)
        if frame is None:
            raise ValueError("entry preflight symbol frame is missing")
        frame_dates = [str(value) for value in frame["date"].tolist()]
        if len(frame_dates) != len(set(frame_dates)):
            raise ValueError(
                "entry preflight symbol frame has duplicate dates"
            )
        date_positions_by_symbol[symbol] = {
            trade_date: position
            for position, trade_date in enumerate(frame_dates)
        }
    raw_keys: list[str] = []
    for candidate in ordered:
        symbol = str(candidate.get("symbol") or "")
        signal_date = str(candidate.get("signal_date") or "")[:10]
        raw_key = f"{symbol}|{signal_date}"
        raw_keys.append(raw_key)
        event: dict[str, Any] = {
            "symbol": symbol,
            "signal_date": signal_date,
            "status": None,
        }
        signal_position = session_positions.get(signal_date)
        if signal_position is None:
            raise ValueError("entry preflight signal date is outside sessions")
        entry_position = signal_position + 1
        planned_exit_position = entry_position + int(
            INDUSTRY_RESIDUAL_SPEC["hold_days"]
        )
        if (
            entry_position >= len(session_dates)
            or planned_exit_position >= len(session_dates)
        ):
            event["status"] = "administrative_signal_cutoff"
            status_counts[str(event["status"])] += 1
            events.append(event)
            continue
        entry_date = session_dates[entry_position]
        planned_exit_date = session_dates[planned_exit_position]
        preregistered_planned_exit = candidate.get("planned_exit_date")
        if (
            preregistered_planned_exit is not None
            and str(preregistered_planned_exit)[:10] != planned_exit_date
        ):
            raise ValueError(
                "entry preflight planned exit differs from tail cutoff"
            )
        frame = frames_by_symbol[symbol]
        date_positions = date_positions_by_symbol[symbol]
        signal_index = date_positions.get(signal_date)
        entry_index = date_positions.get(entry_date)
        expected_signal_index = candidate.get("signal_frame_index")
        if (
            signal_index is None
            or (
                expected_signal_index is not None
                and int(expected_signal_index) != signal_index
            )
        ):
            raise ValueError("entry preflight signal frame index mismatch")
        buy = _artifact_open_verdict(
            adapter,
            cache,
            symbol,
            entry_date,
            "buy",
        )
        event["entry_date"] = entry_date
        event["planned_exit_date"] = planned_exit_date
        event["entry_verdict"] = buy
        event["entry_verdict_sha256"] = _sha256(buy)
        if not buy["fillable"]:
            event["status"] = "entry_unfillable"
            event["entry_reason"] = buy.get("reason")
            status_counts[str(event["status"])] += 1
            events.append(event)
            continue
        if entry_index is None:
            raise ValueError("fillable entry has no matching market bar")
        signal_row = frame.iloc[signal_index]
        entry_row = frame.iloc[entry_index]
        raw_open = float(entry_row["open"])
        if not math.isclose(
            raw_open,
            float(buy["raw_price"]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("entry preflight open differs from market bar")
        signal_adjusted_close = float(signal_row["close"]) * float(
            signal_row["adj_factor"]
        )
        entry_adjusted_open = raw_open * float(entry_row["adj_factor"])
        control = INDUSTRY_RESIDUAL_SPEC["entry_execution"]
        strategy_filter = assess_entry_executability(
            {"close": signal_adjusted_close},
            {"open": entry_adjusted_open},
            max_gap_up_pct=float(control["max_gap_up_pct"]),
            max_gap_down_pct=float(control["max_gap_down_pct"]),
            locked_limit_gap_pct=float(control["locked_limit_gap_pct"]),
            max_intraday_range_pct=float(
                control["max_intraday_range_pct"]
            ),
            decision_cutoff=str(control["decision_cutoff"]),
        )
        event["strategy_entry_filter"] = strategy_filter
        event["strategy_entry_filter_sha256"] = _sha256(strategy_filter)
        if not strategy_filter["executable"]:
            event["status"] = "entry_strategy_filter_rejected"
            status_counts[str(event["status"])] += 1
            events.append(event)
            continue
        event["status"] = "entry_executable"
        status_counts[str(event["status"])] += 1
        events.append(event)
        executable.append(
            {
                **candidate,
                "entry_date": entry_date,
                "planned_exit_date": planned_exit_date,
                "entry_preflight": {
                    **dict(strategy_filter),
                    "evidence_source": "audited_artifact_next_open",
                    "strict_fill_gate": buy,
                },
                "entry_preflight_event_sha256": _sha256(event),
            }
        )
    executable_keys = [
        (
            str(candidate["symbol"]),
            str(candidate["signal_date"])[:10],
            str(candidate["entry_date"])[:10],
        )
        for candidate in executable
    ]
    receipt = {
        "schema_version": "strict-entry-preflight-receipt/v1",
        "parameters": {
            "uniform_tail_cutoff": "planned_exit_t_plus_6_within_coverage",
            "entry_execution": dict(
                INDUSTRY_RESIDUAL_SPEC["entry_execution"]
            ),
            "intraday_high_low_read": False,
        },
        "raw_candidate_count": len(ordered),
        "raw_candidate_keys_sha256": _sha256(raw_keys),
        "status_counts": dict(sorted(status_counts.items())),
        "event_count": len(events),
        "events": events,
        "events_sha256": _sha256(events),
        "executable_candidate_count": len(executable),
        "executable_candidate_keys_sha256": _sha256(executable_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return executable, receipt, cache


def _build_strict_outcomes(
    executable_candidates: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    sessions: Sequence[str],
    adapter: Any,
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
    terminal_listing_evidence: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _assert_strict_execution_contract()
    session_dates = [str(value) for value in sessions]
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    completed: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    ordered = sorted(
        (dict(candidate) for candidate in executable_candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    for candidate in ordered:
        symbol = str(candidate.get("symbol") or "")
        frame = frames_by_symbol.get(symbol)
        if frame is None:
            raise AuditedPITDevelopmentReplayError(
                "strict outcome symbol frame is missing"
            )
        signal_index = int(candidate["signal_frame_index"])
        realized, unresolved, event = _strict_close_stop_trade(
            adapter=adapter,
            verdict_cache=verdict_cache,
            frame=frame,
            symbol=symbol,
            signal_index=signal_index,
            sessions=session_dates,
            session_positions=session_positions,
            suspension_evidence=suspension_evidence,
            terminal_listing_evidence=terminal_listing_evidence,
            hold_days=int(INDUSTRY_RESIDUAL_SPEC["hold_days"]),
            stop_loss_pct=float(
                INDUSTRY_RESIDUAL_SPEC["close_stop_loss_pct"]
            ),
        )
        status = str(event.get("status") or "")
        if status not in {"candidate_built", "entered_unresolved_exit"}:
            raise AuditedPITDevelopmentReplayError(
                "preflight-executable candidate failed during outcome replay"
            )
        if str(event.get("entry_date") or "") != str(
            candidate.get("entry_date") or ""
        ):
            raise AuditedPITDevelopmentReplayError(
                "strict outcome entry date differs from preflight"
            )
        status_counts[status] += 1
        events.append(event)
        metadata = {
            **candidate,
            "signal_date": str(candidate["signal_date"])[:10],
            "symbol": symbol,
            "name": str(candidate.get("name") or ""),
            "action": "BUY",
            "score": float(candidate["industry_r5_gap"]),
            "rank_score": float(candidate["industry_r5_gap"]),
            "market_level": "audited_pit_development",
            "signal_tags": [INDUSTRY_RESIDUAL_SPEC["signal_tag"]],
            "current_universe_bias": False,
        }
        if realized is not None:
            completed.append({**metadata, **realized})
        if unresolved is not None:
            censored.append({**metadata, **unresolved})
    completed.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    censored.sort(key=lambda item: (item["signal_date"], item["symbol"]))
    receipt = {
        "schema_version": "industry-residual-strict-outcome-receipt/v1",
        "input_executable_candidate_count": len(ordered),
        "status_counts": dict(sorted(status_counts.items())),
        "execution_event_count": len(events),
        "execution_events": events,
        "execution_events_sha256": _sha256(events),
        "completed_candidate_count": len(completed),
        "completed_candidates_sha256": _sha256(completed),
        "right_censored_position_count": len(censored),
        "right_censored_positions_sha256": _sha256(censored),
        "verdict_cache_count": len(verdict_cache),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return completed, censored, receipt


def _evaluate_fixed_family(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    required_tags: Sequence[str],
    evaluation_session_dates: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        isinstance(evaluation_session_dates, (str, bytes))
        or not evaluation_session_dates
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict evaluation requires an audited evaluation session grid"
        )
    allowed_censor_reasons = {
        "no_strict_sell_fill_through_coverage_end",
        "terminal_listing_without_settlement_evidence",
    }
    selection_candidates = [dict(candidate) for candidate in candidates]
    if any(
        candidate.get("right_censored") is True
        and candidate.get("censor_reason") not in allowed_censor_reasons
        for candidate in selection_candidates
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict evaluation received an unknown censor reason"
        )
    selected, selection_receipt = _select_with_industry_cap_receipt(
        selection_candidates,
        rank_mode=rank_mode,
        top_n=int(INDUSTRY_RESIDUAL_SPEC["top_n"]),
        max_active_positions=int(
            INDUSTRY_RESIDUAL_SPEC["max_active_positions"]
        ),
    )
    selected_censored = [
        trade for trade in selected if trade.get("right_censored") is True
    ]
    selected_complete = [
        trade for trade in selected if trade.get("right_censored") is not True
    ]
    metrics = _trade_metrics(
        selected_complete,
        hold_days=int(INDUSTRY_RESIDUAL_SPEC["hold_days"]),
        max_active_positions=int(
            INDUSTRY_RESIDUAL_SPEC["max_active_positions"]
        ),
        exposure_multiplier=float(
            INDUSTRY_RESIDUAL_SPEC["exposure_multiplier"]
        ),
        annual_financing_rate_pct=float(
            INDUSTRY_RESIDUAL_SPEC["annual_financing_rate_pct"]
        ),
        roundtrip_cost_bps=float(
            INDUSTRY_RESIDUAL_SPEC["roundtrip_cost_bps"]
        ),
        slippage_bps=float(INDUSTRY_RESIDUAL_SPEC["slippage_bps"]),
        capital_model=str(INDUSTRY_RESIDUAL_SPEC["capital_model"]),
        evaluation_start_date=str(evaluation_session_dates[0]),
        evaluation_end_date=str(evaluation_session_dates[-1]),
        evaluation_session_dates=evaluation_session_dates,
    )
    thresholds = INDUSTRY_RESIDUAL_SPEC["advancement_thresholds"]
    sample_pass = len(selected_complete) >= int(
        thresholds["minimum_complete_trades"]
    )
    evidence_complete = not selected_censored
    win_rate = metrics.get("trade_win_rate_pct")
    full_drawdown = metrics.get("portfolio_max_drawdown_pct")
    full_profit_factor = metrics.get("trade_profit_factor")
    full_quality_pass = bool(
        sample_pass
        and win_rate is not None
        and float(win_rate)
        >= float(thresholds["minimum_full_win_rate_pct"])
        and full_drawdown is not None
        and abs(float(full_drawdown))
        <= float(thresholds["maximum_full_drawdown_pct"])
        and full_profit_factor is not None
        and float(full_profit_factor)
        >= float(thresholds["minimum_full_profit_factor"])
    )
    latest_return = metrics.get("rolling_1y_latest_return_pct")
    latest_calmar = metrics.get("calmar_latest_12m")
    latest_window_pass = bool(
        metrics.get("rolling_1y_latest_full_window")
        and latest_return is not None
        and float(latest_return)
        >= float(thresholds["minimum_latest_365d_return_pct"])
        and latest_calmar is not None
        and float(latest_calmar)
        >= float(thresholds["minimum_latest_365d_calmar"])
    )
    windows = metrics.get("rolling_1y_windows") or []
    rolling_stability_pass = bool(
        windows
        and all(
            window.get("return_pct") is not None
            and float(window["return_pct"])
            >= float(
                thresholds["all_complete_365d_minimum_return_pct"]
            )
            and window.get("max_drawdown_pct") is not None
            and abs(float(window["max_drawdown_pct"]))
            <= float(
                thresholds["all_complete_365d_maximum_drawdown_pct"]
            )
            and window.get("payoff_ratio") is not None
            and float(window["payoff_ratio"])
            >= float(
                thresholds["all_complete_365d_minimum_payoff_ratio"]
            )
            and window.get("profit_factor") is not None
            and float(window["profit_factor"])
            >= float(
                thresholds["all_complete_365d_minimum_profit_factor"]
            )
            and window.get("calmar") is not None
            and float(window["calmar"])
            >= float(thresholds["all_complete_365d_minimum_calmar"])
            for window in windows
        )
    )
    target_all_pass = bool(
        evidence_complete and full_quality_pass and latest_window_pass
    )
    row = {
        "label": "+".join(sorted(str(tag) for tag in required_tags))
        + "|all_market_levels",
        "required_signal_tags": sorted(str(tag) for tag in required_tags),
        "market_levels": [],
        **metrics,
        "rank_mode": rank_mode,
        "selection_candidate_count": len(selection_candidates),
        "selected_position_count": len(selected),
        "selected_complete_trade_count": len(selected_complete),
        "selected_right_censored_position_count": len(selected_censored),
        "selected_right_censored_trade_keys": [
            _selection_trade_key(trade) for trade in selected_censored
        ],
        "evidence_complete": evidence_complete,
        "target_minimum_sample_pass": sample_pass,
        "target_full_development_quality_pass": full_quality_pass,
        "target_latest_12m_pass": latest_window_pass,
        "target_rolling_12m_stability_pass": rolling_stability_pass,
        "target_all_pass": target_all_pass,
        "target_gap_1y_return_pct": (
            round(
                float(thresholds["minimum_latest_365d_return_pct"])
                - float(latest_return),
                2,
            )
            if latest_return is not None
            else None
        ),
    }
    sweep = {
        "schema_version": "strict-industry-residual-fixed-evaluation/v1",
        "qualified_trade_count": sum(
            candidate.get("right_censored") is not True
            for candidate in selection_candidates
        ),
        "right_censored_position_count": sum(
            candidate.get("right_censored") is True
            for candidate in selection_candidates
        ),
        "selection_candidate_count": len(selection_candidates),
        "spec_count": 1,
        "top": [row],
        "target_all_pass_count": int(target_all_pass),
        "target_rolling_12m_stability_pass_count": int(
            rolling_stability_pass
        ),
        "evidence_complete": evidence_complete,
    }
    return sweep, selection_receipt


def _single_fixed_spec_row(sweep: Mapping[str, Any]) -> Mapping[str, Any]:
    top = sweep.get("top")
    if (
        not isinstance(top, list)
        or len(top) != 1
        or not isinstance(top[0], Mapping)
    ):
        raise AuditedPITDevelopmentReplayError(
            "fixed industry residual sweep must return exactly one result row"
        )
    return top[0]


def _receipt_summary(
    receipt: Mapping[str, Any],
    *,
    excluded_fields: Sequence[str],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in set(excluded_fields)
    }


def run_audited_pit_industry_residual_reversal(
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
    if not isinstance(settings, Settings):
        raise AuditedPITDevelopmentReplayError(
            "industry residual replay requires frozen settings"
        )
    producer_code_start = _producer_binding()
    contract = load_temporal_partition_contract(temporal_contract_path)
    if contract["contract_sha256"] != expected_temporal_contract_sha256:
        raise AuditedPITDevelopmentReplayError(
            "temporal contract hash mismatch"
        )
    assert_range_allowed(
        contract,
        "development",
        start_date,
        end_date,
        "backtest",
    )
    try:
        universe = AuditedPointInTimeUniverse.from_file(
            str(audited_pit_universe_path),
            expected_coverage_audit_sha256=(
                expected_coverage_audit_sha256
            ),
            expected_artifact_root_sha256=expected_artifact_root_sha256,
            expected_temporal_contract_sha256=(
                expected_temporal_contract_sha256
            ),
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
        connection = universe._require_open()
        bars, bar_loader_receipt = _load_industry_feature_bars(
            connection,
            start_date=start_date,
            end_date=end_date,
        )
        frames = _frames_by_symbol(bars)
        symbol_features, symbol_feature_receipt = (
            _build_symbol_return_features(bars, sessions)
        )
        industry_features, industry_feature_receipt = (
            _build_industry_features(symbol_features)
        )
        verify_industry_feature_receipt(
            symbol_features,
            industry_feature_receipt,
        )
        raw_residual, raw_residual_receipt = (
            _build_residual_raw_candidates(
                industry_features,
                frames_by_symbol=frames,
            )
        )
        group_raw_candidate_keys = sorted(
            key
            for group in industry_feature_receipt["groups"]
            for key in group["raw_candidate_keys"]
        )
        if (
            group_raw_candidate_keys
            != raw_residual_receipt["raw_candidate_keys"]
        ):
            raise AuditedPITDevelopmentReplayError(
                "industry group candidates differ from raw candidate table"
            )
        legacy_raw, legacy_raw_receipt = _build_legacy_raw_candidates(
            frames
        )
        residual_tail, residual_tail_receipt = (
            _apply_uniform_tail_cutoff(
                raw_residual,
                sessions=sessions,
                family=INDUSTRY_RESIDUAL_SPEC["signal_tag"],
            )
        )
        legacy_tail: dict[str, list[dict[str, Any]]] = {}
        legacy_tail_receipts: dict[str, dict[str, Any]] = {}
        for family in sorted(legacy_raw):
            kept, receipt = _apply_uniform_tail_cutoff(
                legacy_raw[family],
                sessions=sessions,
                family=family,
            )
            legacy_tail[family] = kept
            legacy_tail_receipts[family] = receipt

        suspension_evidence = _load_suspension_evidence(
            connection,
            start_date=start_date,
            end_date=end_date,
        )
        terminal_listing_evidence = _load_terminal_listing_evidence(
            connection,
            start_date=start_date,
            end_date=end_date,
        )
        adapter = ArtifactNativeReplayAdapter(
            universe,
            expected_temporal_contract_sha256=(
                expected_temporal_contract_sha256
            ),
            expected_temporal_role="development",
        )
        verdict_cache: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        residual_executable, residual_entry_receipt, verdict_cache = (
            _preflight_strict_entries(
                residual_tail,
                frames_by_symbol=frames,
                sessions=sessions,
                adapter=adapter,
                verdict_cache=verdict_cache,
            )
        )
        legacy_executable: dict[str, list[dict[str, Any]]] = {}
        legacy_entry_receipts: dict[str, dict[str, Any]] = {}
        for family in sorted(legacy_tail):
            executable, receipt, verdict_cache = (
                _preflight_strict_entries(
                    legacy_tail[family],
                    frames_by_symbol=frames,
                    sessions=sessions,
                    adapter=adapter,
                    verdict_cache=verdict_cache,
                )
            )
            legacy_executable[family] = executable
            legacy_entry_receipts[family] = receipt
        overlap_receipt = _overlap_receipt(
            {
                (
                    str(candidate["symbol"]),
                    str(candidate["signal_date"]),
                    str(candidate["entry_date"]),
                )
                for candidate in residual_executable
            },
            {
                family: {
                    (
                        str(candidate["symbol"]),
                        str(candidate["signal_date"]),
                        str(candidate["entry_date"]),
                    )
                    for candidate in candidates
                }
                for family, candidates in legacy_executable.items()
            },
        )
        completed, censored, outcome_receipt = _build_strict_outcomes(
            residual_executable,
            frames_by_symbol=frames,
            sessions=sessions,
            adapter=adapter,
            verdict_cache=verdict_cache,
            suspension_evidence=suspension_evidence,
            terminal_listing_evidence=terminal_listing_evidence,
        )
        source = {
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "artifact_root_sha256": universe.artifact_root_sha256,
            "temporal_contract_sha256": universe.temporal_contract_sha256,
            "temporal_role": universe.temporal_role,
            "market_session_count": len(sessions),
            "exact_membership_session_count": len(sessions),
            "market_scope": market_scope_contract(),
            "artifact_native_replay_contract_sha256": (
                adapter.contract_sha256
            ),
            "bar_loader_receipt_sha256": bar_loader_receipt[
                "receipt_sha256"
            ],
            "symbol_feature_receipt_sha256": symbol_feature_receipt[
                "receipt_sha256"
            ],
            "industry_feature_receipt_sha256": industry_feature_receipt[
                "receipt_sha256"
            ],
            "full_session_suspension_evidence_count": len(
                suspension_evidence
            ),
            "full_session_suspension_evidence_sha256": _sha256(
                [
                    {
                        "symbol": key[0],
                        "trade_date": key[1],
                        "proofs": value,
                    }
                    for key, value in sorted(suspension_evidence.items())
                ]
            ),
            "terminal_listing_evidence_count": len(
                terminal_listing_evidence
            ),
            "terminal_listing_evidence_sha256": _sha256(
                [
                    terminal_listing_evidence[symbol]
                    for symbol in sorted(terminal_listing_evidence)
                ]
            ),
            "terminal_listing_evidence_usage": (
                "outcome_censor_only_not_signal_or_settlement_return"
            ),
            "producer_code": producer_code_start,
        }
    finally:
        universe.close()

    selection_candidates = [*completed, *censored]
    selection_candidates.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    main_sweep, main_selection_receipt = _evaluate_fixed_family(
        selection_candidates,
        rank_mode="industry_residual",
        required_tags=[INDUSTRY_RESIDUAL_SPEC["signal_tag"]],
        evaluation_session_dates=sessions,
    )
    amount_baseline_sweep, amount_baseline_selection_receipt = (
        _evaluate_fixed_family(
            selection_candidates,
            rank_mode="signal_date_amount",
            required_tags=[INDUSTRY_RESIDUAL_SPEC["signal_tag"]],
            evaluation_session_dates=sessions,
        )
    )
    verify_industry_selection_receipt(
        selection_candidates,
        main_selection_receipt,
        expected_rank_mode="industry_residual",
    )
    verify_industry_selection_receipt(
        selection_candidates,
        amount_baseline_selection_receipt,
        expected_rank_mode="signal_date_amount",
    )
    main_row = _single_fixed_spec_row(main_sweep)
    baseline_row = _single_fixed_spec_row(amount_baseline_sweep)
    advancement_gate = _advancement_gate_passes(
        main_row,
        baseline_row,
    )
    strategy_sha256 = _sha256(INDUSTRY_RESIDUAL_SPEC)
    source_anchors = {
        key: source[key]
        for key in (
            "coverage_audit_sha256",
            "artifact_root_sha256",
            "temporal_contract_sha256",
            "temporal_role",
            "market_session_count",
            "exact_membership_session_count",
            "artifact_native_replay_contract_sha256",
            "full_session_suspension_evidence_sha256",
            "terminal_listing_evidence_sha256",
            "producer_code",
        )
    }
    industry_sidecar_payload = {
        "schema_version": "audited-pit-industry-feature-sidecar/v1",
        "strategy_sha256": strategy_sha256,
        "source": source_anchors,
        "bar_loader_receipt": bar_loader_receipt,
        "symbol_feature_receipt": symbol_feature_receipt,
        "industry_feature_receipt": industry_feature_receipt,
        "raw_candidate_receipt": raw_residual_receipt,
    }
    _assert_producer_binding_unchanged(producer_code_start)
    industry_sidecar = _write_content_addressed(
        Path(output_dir) / "sidecars",
        industry_sidecar_payload,
    )
    execution_sidecar_payload = {
        "schema_version": "audited-pit-industry-residual-execution-sidecar/v1",
        "strategy_sha256": strategy_sha256,
        "source": source_anchors,
        "tail_cutoff_receipts": {
            INDUSTRY_RESIDUAL_SPEC["signal_tag"]: residual_tail_receipt,
            **legacy_tail_receipts,
        },
        "entry_preflight_receipts": {
            INDUSTRY_RESIDUAL_SPEC["signal_tag"]: residual_entry_receipt,
            **legacy_entry_receipts,
        },
        "legacy_raw_candidate_receipt": legacy_raw_receipt,
        "overlap_receipt": overlap_receipt,
        "outcome_receipt": outcome_receipt,
        "completed_candidates": completed,
        "right_censored_positions": censored,
        "main_selection_receipt": main_selection_receipt,
        "amount_baseline_selection_receipt": (
            amount_baseline_selection_receipt
        ),
    }
    _assert_producer_binding_unchanged(producer_code_start)
    execution_sidecar = _write_content_addressed(
        Path(output_dir) / "sidecars",
        execution_sidecar_payload,
    )
    payload = {
        "schema_version": "audited-pit-industry-residual-result/v1",
        "strategy": {
            **INDUSTRY_RESIDUAL_SPEC,
            "strategy_sha256": strategy_sha256,
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "final_oos_consumed": False,
            "advancement_gate_passed": advancement_gate,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "feature_receipt": {
            "bar_loader": bar_loader_receipt,
            "symbol_features": symbol_feature_receipt,
            "industry_features": _receipt_summary(
                industry_feature_receipt,
                excluded_fields=["groups"],
            ),
        },
        "industry_receipt_sidecar": _stable_sidecar_reference(
            industry_sidecar
        ),
        "tail_cutoff_receipt": {
            INDUSTRY_RESIDUAL_SPEC["signal_tag"]: _receipt_summary(
                residual_tail_receipt,
                excluded_fields=["cut_keys"],
            ),
            **{
                family: _receipt_summary(
                    receipt,
                    excluded_fields=["cut_keys"],
                )
                for family, receipt in legacy_tail_receipts.items()
            },
        },
        "entry_receipt": {
            INDUSTRY_RESIDUAL_SPEC["signal_tag"]: _receipt_summary(
                residual_entry_receipt,
                excluded_fields=["events"],
            ),
            **{
                family: _receipt_summary(
                    receipt,
                    excluded_fields=["events"],
                )
                for family, receipt in legacy_entry_receipts.items()
            },
        },
        "overlap_receipt": overlap_receipt,
        "candidate_receipt": {
            "raw_residual": _receipt_summary(
                raw_residual_receipt,
                excluded_fields=["raw_candidate_keys"],
            ),
            "legacy_raw": legacy_raw_receipt,
            "strict_outcome": _receipt_summary(
                outcome_receipt,
                excluded_fields=["execution_events"],
            ),
        },
        "execution_candidate_sidecar": _stable_sidecar_reference(
            execution_sidecar
        ),
        "completed_candidate_count": len(completed),
        "completed_candidates_sha256": _sha256(completed),
        "right_censored_position_count": len(censored),
        "right_censored_positions_sha256": _sha256(censored),
        "selection_candidate_count": len(selection_candidates),
        "selection_candidates_sha256": _sha256(selection_candidates),
        "main_selection_receipt": main_selection_receipt,
        "amount_baseline_selection_receipt": (
            amount_baseline_selection_receipt
        ),
        "main_sweep": main_sweep,
        "amount_baseline_sweep": amount_baseline_sweep,
        "advancement_gate": {
            "main_latest_and_full_quality_passed": bool(
                main_row.get("target_all_pass")
            ),
            "main_rolling_12m_stability_passed": bool(
                main_row.get("target_rolling_12m_stability_pass")
            ),
            "main_evidence_complete": bool(
                main_row.get("evidence_complete")
            ),
            "amount_baseline_evidence_complete": bool(
                baseline_row.get("evidence_complete")
            ),
            "all_required_gates_passed": advancement_gate,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
        "comparison": {
            "shared_selection_candidate_table": True,
            "shared_candidate_table_sha256": main_selection_receipt[
                "candidate_table_sha256"
            ],
            "main_selected_position_count": main_row.get(
                "selected_position_count"
            ),
            "amount_baseline_selected_position_count": baseline_row.get(
                "selected_position_count"
            ),
            "independent_portfolio_replays": True,
            "same_execution_contract": True,
        },
    }
    _assert_producer_binding_unchanged(producer_code_start)
    artifact = _write_content_addressed(output_dir, payload)
    _assert_producer_binding_unchanged(producer_code_start)
    return {
        **payload,
        "artifact": artifact,
        "runtime_sidecars": {
            "industry_features": industry_sidecar,
            "execution_candidates": execution_sidecar,
        },
    }
