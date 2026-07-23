"""Audited PIT replay for a fixed momentum-acceleration candidate rank."""

from __future__ import annotations

import hashlib
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
from app.audited_pit_walk_forward_rank import _candidate_features
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _candidate_trades_from_bars,
    _sha256,
    _write_content_addressed,
)
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptError
from app.research_portfolio import _selection_trade_key
from app.research_scope import market_scope_contract
from app.research_sweep import sweep_qualified_trades


MOMENTUM_ACCELERATION_RANK_SPEC = {
    **SIMPLE_BREAKOUT_SPEC,
    "schema_version": "development-breakout-momentum-acceleration-rank/v1",
    "required_signal_tags": ["breakout_20d", "momentum_acceleration_rank"],
    "rank": {
        "tag": "momentum_acceleration_rank",
        "formula": "signal_return_20d_pct - signal_return_60d_pct / 3",
        "price_basis": "causal_adjusted_close",
        "recent_window_sessions": 20,
        "long_window_sessions": 60,
        "recent_weight": 1.0,
        "long_weight": -1.0 / 3.0,
        "direction": "descending",
        "missing_values": "reject",
        "tie_breaker": "source_order_signal_date_then_symbol",
        "trained_parameters": False,
    },
}

_MOMENTUM_COLUMNS = ("signal_return_20d_pct", "signal_return_60d_pct")
_EXIT_PATH_FIELDS = (
    "signal_date",
    "symbol",
    "entry_date",
    "exit_date",
    "exit_reason",
    "holding_days",
    "return_pct",
    "max_adverse_pct",
    "max_favorable_pct",
    "mark_to_market_path",
)


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    feature_module = Path(_candidate_features.__code__.co_filename)
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "feature_module_sha256": hashlib.sha256(feature_module.read_bytes()).hexdigest(),
        "acceleration_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }
    return {
        "schema_version": "audited-pit-momentum-acceleration-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _exit_paths(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {field: trade.get(field) for field in _EXIT_PATH_FIELDS}
        for trade in trades
    ]


def _rank_momentum_acceleration(
    trades: Sequence[Mapping[str, Any]],
    features: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if len(features) != len(trades):
        raise AuditedPITDevelopmentReplayError(
            "momentum acceleration feature cardinality mismatch"
        )
    matrix = features[list(_MOMENTUM_COLUMNS)].to_numpy(dtype=float)
    complete = np.isfinite(matrix).all(axis=1)
    ranked: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    score_digest = hashlib.sha256()

    for row in features.loc[complete].itertuples(index=False):
        index = int(row.trade_index)
        source_trade = dict(trades[index])
        return_20d = float(row.signal_return_20d_pct)
        return_60d = float(row.signal_return_60d_pct)
        score = return_20d - return_60d / 3.0
        ranked_trade = {
            **source_trade,
            "rank_score": score,
            "signal_tags": ["breakout_20d", "momentum_acceleration_rank"],
        }
        ranked.append(ranked_trade)
        baseline.append(source_trade)
        score_digest.update(
            (
                f"{source_trade['signal_date']}\0{source_trade['symbol']}\0"
                f"{return_20d:.12g}\0{return_60d:.12g}\0{score:.12g}\n"
            ).encode("utf-8")
        )

    receipt = {
        "schema_version": "momentum-acceleration-rank-receipt/v1",
        "parameters": MOMENTUM_ACCELERATION_RANK_SPEC["rank"],
        "candidate_count": len(trades),
        "complete_candidate_count": len(ranked),
        "missing_candidate_count": len(trades) - len(ranked),
        "score_sha256": score_digest.hexdigest(),
        "ranked_trade_keys_sha256": _sha256(
            [_selection_trade_key(trade) for trade in ranked]
        ),
        "baseline_trade_keys_sha256": _sha256(
            [_selection_trade_key(trade) for trade in baseline]
        ),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return ranked, baseline, receipt


def _evaluate(trades: list[dict[str, Any]], required_tags: list[str]) -> dict[str, Any]:
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
        required_signal_tags=required_tags,
        fixed_spec=True,
    )


def _single_fixed_spec_row(sweep: Mapping[str, Any]) -> Mapping[str, Any]:
    top = sweep.get("top")
    if (
        not isinstance(top, list)
        or len(top) != 1
        or not isinstance(top[0], Mapping)
    ):
        raise AuditedPITDevelopmentReplayError(
            "fixed momentum acceleration sweep must return exactly one result row"
        )
    return top[0]


def run_audited_pit_momentum_acceleration_rank(
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
    ranked, baseline, receipt = _rank_momentum_acceleration(trades, features)
    ranked_exit_paths = _exit_paths(ranked)
    baseline_exit_paths = _exit_paths(baseline)
    if ranked_exit_paths != baseline_exit_paths:
        raise AuditedPITDevelopmentReplayError(
            "momentum acceleration rank modified a candidate exit path"
        )

    required_tags = list(MOMENTUM_ACCELERATION_RANK_SPEC["required_signal_tags"])
    acceleration_sweep = _evaluate(ranked, required_tags)
    _single_fixed_spec_row(acceleration_sweep)
    baseline_sweep = _evaluate(baseline, ["breakout_20d"])
    _single_fixed_spec_row(baseline_sweep)
    payload = {
        "schema_version": "audited-pit-momentum-acceleration-rank-result/v1",
        "strategy": {
            **MOMENTUM_ACCELERATION_RANK_SPEC,
            "strategy_sha256": _sha256(MOMENTUM_ACCELERATION_RANK_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "causal_signal_date_rank": True,
            "trained_parameters": False,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "rank_receipt": receipt,
        "ranked_candidate_count": len(ranked),
        "ranked_candidates_sha256": _sha256(ranked),
        "same_window_baseline_candidates_sha256": _sha256(baseline),
        "candidate_exit_paths_sha256": _sha256(ranked_exit_paths),
        "acceleration_sweep": acceleration_sweep,
        "same_window_liquidity_baseline": baseline_sweep,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
