"""Audited PIT replay for the pre-registered next-open gap confirmation."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
from app.research_portfolio import _selection_trade_key
from app.research_scope import market_scope_contract
from app.research_sweep import sweep_qualified_trades


NEXT_OPEN_GAP_CONFIRMATION_SPEC = {
    **SIMPLE_BREAKOUT_SPEC,
    "schema_version": "development-breakout-next-open-gap-confirmation/v1",
    "required_signal_tags": ["breakout_20d", "next_open_gap_2_to_5"],
    "entry_confirmation": {
        "tag": "next_open_gap_2_to_5",
        "reference_price": "signal_date_raw_close",
        "observed_price": "next_session_raw_open",
        "decision_cutoff": "next_open",
        "gap_precision_decimals": 2,
        "minimum_inclusive_pct": 2.0,
        "maximum_exclusive_pct": 5.0,
        "missing_values": "reject",
        "application": "before_cross_sectional_rank_and_portfolio_selection",
    },
}

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
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "next_open_gap_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return {
        "schema_version": "audited-pit-next-open-gap-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _exit_paths(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {field: trade.get(field) for field in _EXIT_PATH_FIELDS}
        for trade in trades
    ]


def _filter_next_open_gap_candidates(
    trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    control = NEXT_OPEN_GAP_CONFIRMATION_SPEC["entry_confirmation"]
    minimum = float(control["minimum_inclusive_pct"])
    maximum = float(control["maximum_exclusive_pct"])
    filtered: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    missing_count = 0
    below_count = 0
    above_count = 0

    for raw_trade in trades:
        trade = dict(raw_trade)
        execution = trade.get("entry_executability")
        if not isinstance(execution, Mapping):
            gap_pct = None
            cutoff = None
        else:
            cutoff = execution.get("decision_cutoff")
            value = execution.get("gap_pct")
            try:
                gap_pct = float(value)
            except (TypeError, ValueError):
                gap_pct = None
            if gap_pct is not None and not math.isfinite(gap_pct):
                gap_pct = None
        if cutoff not in {None, "next_open"}:
            raise AuditedPITDevelopmentReplayError(
                "next-open confirmation received a different decision cutoff"
            )
        if cutoff is None:
            gap_pct = None

        passed = gap_pct is not None and minimum <= gap_pct < maximum
        if gap_pct is None:
            missing_count += 1
        elif gap_pct < minimum:
            below_count += 1
        elif gap_pct >= maximum:
            above_count += 1
        if passed:
            tags = list(trade.get("signal_tags") or [])
            if control["tag"] not in tags:
                tags.append(control["tag"])
            trade["signal_tags"] = tags
            filtered.append(trade)
        decisions.append(
            {
                "trade_key": _selection_trade_key(trade),
                "gap_pct": gap_pct,
                "passed": passed,
            }
        )

    receipt = {
        "schema_version": "next-open-gap-filter-receipt/v1",
        "parameters": control,
        "candidate_count": len(trades),
        "pass_candidate_count": len(filtered),
        "missing_count": missing_count,
        "below_minimum_count": below_count,
        "at_or_above_maximum_count": above_count,
        "decision_count": len(decisions),
        "decisions_sha256": _sha256(decisions),
        "passed_trade_keys_sha256": _sha256(
            [decision["trade_key"] for decision in decisions if decision["passed"]]
        ),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return filtered, receipt


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
            "fixed next-open gap sweep must return exactly one result row"
        )
    return top[0]


def run_audited_pit_next_open_gap_confirmation(
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

    original_exit_paths = _exit_paths(trades)
    filtered, receipt = _filter_next_open_gap_candidates(trades)
    filtered_exit_paths = _exit_paths(filtered)
    original_by_key = {
        _selection_trade_key(trade): path
        for trade, path in zip(trades, original_exit_paths)
    }
    if any(
        original_by_key[_selection_trade_key(trade)] != path
        for trade, path in zip(filtered, filtered_exit_paths)
    ):
        raise AuditedPITDevelopmentReplayError(
            "next-open confirmation modified a candidate exit path"
        )

    required_tags = list(NEXT_OPEN_GAP_CONFIRMATION_SPEC["required_signal_tags"])
    confirmed_sweep = _evaluate(filtered, required_tags)
    _single_fixed_spec_row(confirmed_sweep)
    baseline_sweep = _evaluate(trades, ["breakout_20d"])
    _single_fixed_spec_row(baseline_sweep)
    payload = {
        "schema_version": "audited-pit-next-open-gap-confirmation-result/v1",
        "strategy": {
            **NEXT_OPEN_GAP_CONFIRMATION_SPEC,
            "strategy_sha256": _sha256(NEXT_OPEN_GAP_CONFIRMATION_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "causal_next_open_confirmation": True,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "filter_receipt": receipt,
        "confirmed_candidate_count": len(filtered),
        "confirmed_candidates_sha256": _sha256(filtered),
        "confirmed_exit_paths_sha256": _sha256(filtered_exit_paths),
        "confirmed_sweep": confirmed_sweep,
        "baseline_sweep": baseline_sweep,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
