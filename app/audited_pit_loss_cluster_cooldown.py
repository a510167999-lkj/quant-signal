"""Causal portfolio cooldown after a realized cluster of hard-stop exits."""

from __future__ import annotations

import hashlib
from collections import defaultdict
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
from app.research_common import _date_value
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptError
from app.research_portfolio import _selection_sha256, _selection_trade_key
from app.research_scope import market_scope_contract
from app.research_sweep import sweep_qualified_trades


LOSS_CLUSTER_COOLDOWN_SPEC = {
    **SIMPLE_BREAKOUT_SPEC,
    "schema_version": "development-breakout-loss-cluster-cooldown/v1",
    "required_signal_tags": ["breakout_20d", "loss_cluster_cooldown"],
    "portfolio_entry_control": {
        "event": "realized_stop_loss_exit",
        "event_count": 3,
        "lookback_completed_market_sessions": 5,
        "cooldown_market_sessions": 5,
        "event_consumption": "once",
        "cooldown_scope": "new_entries_only",
        "existing_trade_exit_paths_modified": False,
        "same_session_exit_available": False,
    },
}


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "cooldown_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return {
        "schema_version": "audited-pit-loss-cluster-cooldown-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _select_with_loss_cluster_cooldown(
    by_signal_date: Mapping[str, Sequence[Mapping[str, Any]]],
    sessions: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    control = LOSS_CLUSTER_COOLDOWN_SPEC["portfolio_entry_control"]
    event_count = int(control["event_count"])
    lookback = int(control["lookback_completed_market_sessions"])
    cooldown_duration = int(control["cooldown_market_sessions"])
    session_list = [str(value) for value in sessions]
    if len(session_list) != len(set(session_list)) or session_list != sorted(session_list):
        raise ValueError("cooldown sessions must be unique and sorted")
    session_index = {session: index for index, session in enumerate(session_list)}
    unknown_signal_dates = set(by_signal_date) - set(session_list)
    if unknown_signal_dates:
        raise ValueError("cooldown candidates contain a non-session signal date")

    selected: list[dict[str, Any]] = []
    active_positions: list[dict[str, Any]] = []
    resolved_trade_keys: set[str] = set()
    recent_stop_indices: list[int] = []
    cooldown_remaining = 0
    trigger_dates: list[str] = []
    blocked_candidate_count = 0
    days: list[dict[str, Any]] = []

    for index, signal_date in enumerate(session_list):
        newly_resolved = []
        newly_resolved_stops = []
        for trade in selected:
            trade_key = _selection_trade_key(trade)
            if trade_key in resolved_trade_keys:
                continue
            exit_date = str(trade["exit_date"])[:10]
            if exit_date >= signal_date:
                continue
            resolved_trade_keys.add(trade_key)
            newly_resolved.append(trade_key)
            if str(trade.get("exit_reason") or "") == "stop_loss":
                if exit_date not in session_index:
                    raise ValueError("cooldown stop exit is not an audited market session")
                newly_resolved_stops.append(trade_key)
                recent_stop_indices.append(session_index[exit_date])

        recent_stop_indices = [
            event_index
            for event_index in recent_stop_indices
            if event_index >= index - lookback
        ]
        triggered = bool(
            newly_resolved_stops and len(recent_stop_indices) >= event_count
        )
        trigger_event_count = len(recent_stop_indices) if triggered else 0
        if triggered:
            cooldown_remaining = max(cooldown_remaining, cooldown_duration)
            trigger_dates.append(signal_date)
            recent_stop_indices = []

        active_positions = [
            trade
            for trade in active_positions
            if _date_value(trade["exit_date"]) >= _date_value(signal_date)
        ]
        candidates = [
            dict(trade)
            for trade in sorted(
                by_signal_date.get(signal_date, []),
                key=lambda item: float(item["rank_score"]),
                reverse=True,
            )
        ]
        cooldown_before = cooldown_remaining
        selected_today: list[dict[str, Any]] = []
        if cooldown_remaining > 0:
            blocked_candidate_count += len(candidates)
        else:
            active_symbols = {str(trade["symbol"]) for trade in active_positions}
            for trade in candidates:
                symbol = str(trade["symbol"])
                if symbol in active_symbols:
                    continue
                if (
                    SIMPLE_BREAKOUT_SPEC["max_active_positions"] > 0
                    and len(active_positions)
                    >= SIMPLE_BREAKOUT_SPEC["max_active_positions"]
                ):
                    break
                selected.append(trade)
                selected_today.append(trade)
                active_positions.append(trade)
                active_symbols.add(symbol)
                if len(selected_today) >= SIMPLE_BREAKOUT_SPEC["top_n"]:
                    break
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
        days.append(
            {
                "signal_date": signal_date,
                "newly_resolved_trade_keys": newly_resolved,
                "newly_resolved_stop_keys": newly_resolved_stops,
                "recent_stop_count": len(recent_stop_indices),
                "trigger_event_count": trigger_event_count,
                "triggered": triggered,
                "cooldown_before": cooldown_before,
                "cooldown_after": cooldown_remaining,
                "candidate_count": len(candidates),
                "selected_trade_keys": [
                    _selection_trade_key(trade) for trade in selected_today
                ],
            }
        )

    selected_keys = [_selection_trade_key(trade) for trade in selected]
    receipt = {
        "schema_version": "loss-cluster-cooldown-selection-receipt/v1",
        "parameters": control,
        "session_count": len(session_list),
        "candidate_count": sum(len(items) for items in by_signal_date.values()),
        "selected_count": len(selected),
        "blocked_candidate_count": blocked_candidate_count,
        "trigger_count": len(trigger_dates),
        "trigger_dates": trigger_dates,
        "selected_trade_keys_sha256": _selection_sha256(selected_keys),
        "days": days,
    }
    receipt["receipt_sha256"] = _selection_sha256(receipt)
    return selected, receipt


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
            "fixed cooldown sweep must return exactly one result row"
        )
    return top[0]


def run_audited_pit_loss_cluster_cooldown(
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

    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_signal_date[str(trade["signal_date"])].append(trade)
    selected, receipt = _select_with_loss_cluster_cooldown(
        by_signal_date,
        sessions,
    )
    selected_for_evaluation = [
        {
            **trade,
            "signal_tags": ["breakout_20d", "loss_cluster_cooldown"],
        }
        for trade in selected
    ]
    cooldown_sweep = _evaluate(
        selected_for_evaluation,
        ["breakout_20d", "loss_cluster_cooldown"],
    )
    cooldown_row = _single_fixed_spec_row(cooldown_sweep)
    if (
        int(cooldown_row.get("selected_trade_count") or 0) != len(selected)
    ):
        raise AuditedPITDevelopmentReplayError(
            "cooldown-selected trades did not replay identically"
        )
    baseline_sweep = _evaluate(trades, ["breakout_20d"])
    exit_path_fields = (
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
    original_exit_paths = [
        {field: trade.get(field) for field in exit_path_fields} for trade in selected
    ]
    evaluated_exit_paths = [
        {field: trade.get(field) for field in exit_path_fields}
        for trade in selected_for_evaluation
    ]
    if original_exit_paths != evaluated_exit_paths:
        raise AuditedPITDevelopmentReplayError(
            "cooldown modified an existing trade exit path"
        )

    payload = {
        "schema_version": "audited-pit-loss-cluster-cooldown-result/v1",
        "strategy": {
            **LOSS_CLUSTER_COOLDOWN_SPEC,
            "strategy_sha256": _sha256(LOSS_CLUSTER_COOLDOWN_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "causal_realized_exit_control": True,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "selection_receipt": receipt,
        "selected_exit_paths_sha256": _sha256(original_exit_paths),
        "cooldown_sweep": cooldown_sweep,
        "baseline_sweep": baseline_sweep,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
