"""Development-only loss attribution for the frozen audited PIT breakout baseline."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

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
from app.research_portfolio import _select_with_portfolio_controls
from app.research_scope import market_scope_contract


ATTRIBUTION_SPEC = {
    "schema_version": "audited-pit-breakout-loss-attribution/v1",
    "strategy_schema_version": SIMPLE_BREAKOUT_SPEC["schema_version"],
    "selection": {
        "top_n": SIMPLE_BREAKOUT_SPEC["top_n"],
        "max_active_positions": SIMPLE_BREAKOUT_SPEC["max_active_positions"],
        "rank": "signal_date_amount_desc",
    },
    "net_trade_cost": {
        "roundtrip_cost_bps": SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"],
        "slippage_bps_each_side": SIMPLE_BREAKOUT_SPEC["slippage_bps"],
        "total_bps": (
            SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"]
            + SIMPLE_BREAKOUT_SPEC["slippage_bps"] * 2
        ),
        "financing_cost_at_1x": 0.0,
    },
    "bins": {
        "entry_gap_pct": [-999.0, 0.0, 2.0, 5.0, 999.0],
        "breakout_extension_pct": [-999.0, 1.0, 3.0, 5.0, 999.0],
        "signal_day_return_pct": [-999.0, 3.0, 6.0, 9.0, 999.0],
        "signal_close_location_pct": [-999.0, 50.0, 80.0, 999.0],
        "signal_range_pct": [-999.0, 4.0, 8.0, 999.0],
        "amount_to_prior20_median": [-999.0, 1.0, 2.0, 999.0],
        "signal_return_20d_pct": [-999.0, 10.0, 20.0, 40.0, 999.0],
        "loser_max_favorable_pct": [-999.0, 2.0, 5.0, 999.0],
    },
}


_BIN_LABELS = {
    "entry_gap_pct": ["lt_0", "0_to_2", "2_to_5", "gte_5"],
    "breakout_extension_pct": ["lt_1", "1_to_3", "3_to_5", "gte_5"],
    "signal_day_return_pct": ["lt_3", "3_to_6", "6_to_9", "gte_9"],
    "signal_close_location_pct": ["lt_50", "50_to_80", "gte_80"],
    "signal_range_pct": ["lt_4", "4_to_8", "gte_8"],
    "amount_to_prior20_median": ["lt_1", "1_to_2", "gte_2"],
    "signal_return_20d_pct": ["lt_10", "10_to_20", "20_to_40", "gte_40"],
    "loser_max_favorable_pct": ["lt_2", "2_to_5", "gte_5"],
}


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    module_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "attribution_module_sha256": module_sha256,
    }
    return {
        "schema_version": "audited-pit-loss-attribution-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _bucket(value: Any, field: str) -> str:
    number = _finite_number(value)
    if number is None:
        return "missing"
    boundaries = ATTRIBUTION_SPEC["bins"][field]
    labels = _BIN_LABELS[field]
    for index, label in enumerate(labels):
        if boundaries[index] <= number < boundaries[index + 1]:
            return label
    raise AuditedPITDevelopmentReplayError(f"unbounded attribution value: {field}")


def _annotate_selected_trades(
    selected: Sequence[Mapping[str, Any]],
    bars: pd.DataFrame,
) -> list[dict[str, Any]]:
    selected_keys = {
        (str(trade["symbol"]), str(trade["signal_date"])) for trade in selected
    }
    selected_symbols = {symbol for symbol, _date in selected_keys}
    frame = bars[bars["ts_code"].str[:6].isin(selected_symbols)].copy()
    frame = frame.sort_values(["ts_code", "date"], kind="mergesort")
    frame["signal_close"] = frame["close"] * frame["adj_factor"]
    frame["signal_high"] = frame["high"] * frame["adj_factor"]
    grouped = frame.groupby("ts_code", sort=False)
    frame["prior20_high"] = grouped["signal_high"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).max()
    )
    frame["signal_close_20d_ago"] = grouped["signal_close"].shift(20)
    frame["prior20_amount_median"] = grouped["amount"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).median()
    )

    features: dict[tuple[str, str], dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        key = (str(row.ts_code)[:6], str(row.date))
        if key not in selected_keys:
            continue
        prior_high = _finite_number(row.prior20_high)
        close_20d_ago = _finite_number(row.signal_close_20d_ago)
        prior_amount = _finite_number(row.prior20_amount_median)
        day_range = float(row.high) - float(row.low)
        features[key] = {
            "breakout_extension_pct": (
                round((float(row.signal_close) / prior_high - 1) * 100, 4)
                if prior_high and prior_high > 0
                else None
            ),
            "signal_day_return_pct": (
                round((float(row.close) / float(row.pre_close) - 1) * 100, 4)
                if float(row.pre_close or 0) > 0
                else None
            ),
            "signal_close_location_pct": (
                round((float(row.close) - float(row.low)) / day_range * 100, 4)
                if day_range > 0
                else 50.0
            ),
            "signal_range_pct": (
                round((float(row.high) / float(row.low) - 1) * 100, 4)
                if float(row.low) > 0
                else None
            ),
            "amount_to_prior20_median": (
                round(float(row.amount) / prior_amount, 4)
                if prior_amount and prior_amount > 0
                else None
            ),
            "signal_return_20d_pct": (
                round((float(row.signal_close) / close_20d_ago - 1) * 100, 4)
                if close_20d_ago and close_20d_ago > 0
                else None
            ),
        }
    missing = sorted(selected_keys - set(features))
    if missing:
        raise AuditedPITDevelopmentReplayError(
            "selected trade signal features are missing from audited bars"
        )

    total_cost_pct = float(ATTRIBUTION_SPEC["net_trade_cost"]["total_bps"]) / 100
    annotated = []
    for trade in selected:
        key = (str(trade["symbol"]), str(trade["signal_date"]))
        entry_gap = (trade.get("entry_executability") or {}).get("gap_pct")
        net_return = round(float(trade.get("return_pct") or 0) - total_cost_pct, 4)
        annotated.append(
            {
                "signal_date": key[1],
                "symbol": key[0],
                "exit_date": str(trade.get("exit_date") or ""),
                "exit_reason": str(trade.get("exit_reason") or "unknown"),
                "holding_days": int(trade.get("holding_days") or 0),
                "gross_return_pct": round(float(trade.get("return_pct") or 0), 4),
                "net_return_pct": net_return,
                "max_adverse_pct": round(float(trade.get("max_adverse_pct") or 0), 4),
                "max_favorable_pct": round(
                    float(trade.get("max_favorable_pct") or 0), 4
                ),
                "entry_gap_pct": _finite_number(entry_gap),
                **features[key],
            }
        )
    return annotated


def _stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    returns = [float(trade["net_return_pct"]) for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
        "average_net_return_pct": round(sum(returns) / len(returns), 4)
        if returns
        else None,
        "median_net_return_pct": round(median(returns), 4) if returns else None,
        "net_return_sum_pct": round(sum(returns), 4),
        "profit_factor": round(gross_profit / gross_loss, 2)
        if gross_loss > 0
        else None,
        "average_max_adverse_pct": round(
            sum(float(trade["max_adverse_pct"]) for trade in trades) / len(trades),
            4,
        )
        if trades
        else None,
        "average_max_favorable_pct": round(
            sum(float(trade["max_favorable_pct"]) for trade in trades) / len(trades),
            4,
        )
        if trades
        else None,
    }


def _grouped_stats(
    trades: Sequence[Mapping[str, Any]],
    *,
    field: str,
    bucket_field: str | None = None,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in trades:
        label = (
            _bucket(trade.get(field), bucket_field)
            if bucket_field is not None
            else str(trade.get(field) or "unknown")
        )
        groups[label].append(trade)
    preferred = _BIN_LABELS.get(bucket_field or "", [])
    order = {label: index for index, label in enumerate([*preferred, "missing"])}
    return [
        {"bucket": label, **_stats(items)}
        for label, items in sorted(
            groups.items(), key=lambda item: (order.get(item[0], 999), item[0])
        )
    ]


def _build_attribution(
    selected: Sequence[Mapping[str, Any]],
    bars: pd.DataFrame,
) -> dict[str, Any]:
    annotated = _annotate_selected_trades(selected, bars)
    losing = [trade for trade in annotated if float(trade["net_return_pct"]) <= 0]
    monthly_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in annotated:
        monthly_groups[str(trade["signal_date"])[:7]].append(trade)
    monthly = [
        {"month": month, **_stats(items)}
        for month, items in sorted(monthly_groups.items())
    ]
    worst_months = sorted(
        (row for row in monthly if float(row["net_return_sum_pct"]) < 0),
        key=lambda row: (float(row["net_return_sum_pct"]), row["month"]),
    )[:8]
    return {
        "selected_trade_count": len(annotated),
        "selected_trades_sha256": _sha256(annotated),
        "all_selected": _stats(annotated),
        "exit_reason": _grouped_stats(annotated, field="exit_reason"),
        "entry_gap_pct": _grouped_stats(
            annotated, field="entry_gap_pct", bucket_field="entry_gap_pct"
        ),
        "breakout_extension_pct": _grouped_stats(
            annotated,
            field="breakout_extension_pct",
            bucket_field="breakout_extension_pct",
        ),
        "signal_day_return_pct": _grouped_stats(
            annotated,
            field="signal_day_return_pct",
            bucket_field="signal_day_return_pct",
        ),
        "signal_close_location_pct": _grouped_stats(
            annotated,
            field="signal_close_location_pct",
            bucket_field="signal_close_location_pct",
        ),
        "signal_range_pct": _grouped_stats(
            annotated, field="signal_range_pct", bucket_field="signal_range_pct"
        ),
        "amount_to_prior20_median": _grouped_stats(
            annotated,
            field="amount_to_prior20_median",
            bucket_field="amount_to_prior20_median",
        ),
        "signal_return_20d_pct": _grouped_stats(
            annotated,
            field="signal_return_20d_pct",
            bucket_field="signal_return_20d_pct",
        ),
        "loser_max_favorable_pct": _grouped_stats(
            losing,
            field="max_favorable_pct",
            bucket_field="loser_max_favorable_pct",
        ),
        "monthly": monthly,
        "worst_months_by_equal_slot_net_return_sum": worst_months,
        "interpretation_guardrails": {
            "descriptive_only": True,
            "not_a_strategy_sweep": True,
            "equal_slot_trade_sums_are_not_portfolio_returns": True,
            "final_oos_consumed": False,
        },
    }


def run_audited_pit_loss_attribution(
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
            universe, start_date=start_date, end_date=end_date
        )
        bars = _load_exact_membership_bars(
            universe._require_open(), start_date=start_date, end_date=end_date
        )
        trades = _candidate_trades_from_bars(
            bars,
            {},
            settings,
            membership_name_column="membership_name",
            current_universe_bias=False,
        )
        by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            by_signal_date[str(trade["signal_date"])].append(trade)
        selected = _select_with_portfolio_controls(
            by_signal_date,
            SIMPLE_BREAKOUT_SPEC["top_n"],
            symbol_cooldown_days=0,
            max_active_positions=SIMPLE_BREAKOUT_SPEC["max_active_positions"],
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

    attribution = _build_attribution(selected, bars)
    payload = {
        "schema_version": "audited-pit-breakout-loss-attribution-result/v1",
        "spec": {
            **ATTRIBUTION_SPEC,
            "spec_sha256": _sha256(ATTRIBUTION_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "descriptive_only": True,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "attribution": attribution,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
