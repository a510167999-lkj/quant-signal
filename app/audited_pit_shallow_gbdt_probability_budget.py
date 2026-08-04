"""Frozen probability-to-cash allocation for the shallow GBDT development hypothesis."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from app.audited_pit_score_contract import (
    SHALLOW_GBDT_SCORE_CONTRACT,
    candidate_passes_gate,
    candidate_score,
)
from app.audited_pit_shallow_gbdt import SHALLOW_GBDT_OOF_SPEC


PROBABILITY_BUDGET_ALLOCATION_SPEC: Mapping[str, Any] = MappingProxyType(
    {
        "schema_version": "shallow-gbdt-probability-budget-allocation/v1",
        "score_contract": dict(SHALLOW_GBDT_SCORE_CONTRACT),
        "input_precision": "unrounded_float64",
        "method": "linear_excess_over_strict_probability_gate",
        "formula": (
            "exposure_multiplier * (probability - gate_value) / "
            "(1.0 - gate_value) / max_active_positions"
        ),
        "capacity_source": "exposure_multiplier_over_max_active_positions",
        "cash_policy": "unallocated_cash_remains_cash",
        "renormalize_across_positions": False,
    }
)
_PROBABILITY_BUDGET_ALLOCATION_SPEC_SHA256 = (
    "0e70e5852155fe59caa31ffd9e27f304785ea017e961ddfd3faf01efc94e70e7"
)


SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC = deepcopy(
    SHALLOW_GBDT_OOF_SPEC
)
SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC.update(
    {
        "schema_version": (
            "development-pit-cross-sectional-shallow-gbdt-probability-"
            "budget-utility-logit-rolling-126-oof/v1"
        ),
        "signal_tag": (
            "cross_sectional_shallow_gbdt_probability_budget_"
            "utility_logit_rolling_126_oof"
        ),
    }
)
SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC["selection"] = {
    **SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC["selection"],
    "position_budget_allocation": dict(
        PROBABILITY_BUDGET_ALLOCATION_SPEC
    ),
    "position_budget_control": (
        "same_probability_ranked_selected_trades_equal_slot_weight"
    ),
}
_SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256 = (
    "4bd7afa5a8694580f9eabc2c6aed1554ea2e199d189c8e5a808265700100aaf5"
)


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


def _exact_value_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping) or set(actual) != set(expected):
            return False
        return all(
            _exact_value_match(actual[key], expected[key])
            for key in expected
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _exact_value_match(actual_item, expected_item)
                for actual_item, expected_item in zip(actual, expected)
            )
        )
    return type(actual) is type(expected) and actual == expected


def _finite_positive(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} is invalid")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} is invalid")
    return number


def _strict_iso_date(value: Any, *, label: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} is invalid")
    return parsed


def _trade_key(trade: Mapping[str, Any]) -> str:
    values = [
        str(trade.get("security_id") or ""),
        str(trade.get("signal_date") or "")[:10],
        str(trade.get("entry_date") or "")[:10],
        str(trade.get("exit_date") or "")[:10],
    ]
    if not all(values):
        raise ValueError("probability budget trade key is incomplete")
    _strict_iso_date(values[1], label="probability budget signal date")
    _strict_iso_date(values[2], label="probability budget entry date")
    _strict_iso_date(values[3], label="probability budget exit date")
    return "|".join(values)


def _assert_frozen_allocation_spec(
    allocation_spec: Mapping[str, Any],
) -> None:
    expected = dict(PROBABILITY_BUDGET_ALLOCATION_SPEC)
    actual = dict(allocation_spec)
    if (
        not _exact_value_match(actual, expected)
        or _canonical_sha256(actual)
        != _PROBABILITY_BUDGET_ALLOCATION_SPEC_SHA256
    ):
        raise ValueError("probability budget allocation spec is not frozen")


def _validate_selected_active_capacity(
    selected: Sequence[Mapping[str, Any]],
    *,
    max_active_positions: int,
) -> None:
    entries: list[tuple[date, date]] = []
    for trade in selected:
        entry = _strict_iso_date(
            str(trade.get("entry_date") or "")[:10],
            label="probability budget entry date",
        )
        exit_ = _strict_iso_date(
            str(trade.get("exit_date") or "")[:10],
            label="probability budget exit date",
        )
        if exit_ <= entry:
            raise ValueError("probability budget holding interval is invalid")
        entries.append((entry, exit_))
    active_exits: list[date] = []
    for entry, exit_ in sorted(entries):
        active_exits = [item for item in active_exits if item > entry]
        active_exits.append(exit_)
        if len(active_exits) > max_active_positions:
            raise ValueError("probability budget active capacity is exceeded")


def _allocation_row(
    trade: Mapping[str, Any],
    *,
    probability: float,
    position_budget_fraction: float,
) -> dict[str, Any]:
    return {
        "trade_key": _trade_key(trade),
        "predicted_positive_utility_probability": probability,
        "position_budget_fraction": position_budget_fraction,
    }


def allocate_shallow_gbdt_probability_budget(
    selected: Sequence[Mapping[str, Any]],
    *,
    max_active_positions: int,
    exposure_multiplier: float,
    allocation_spec: Mapping[str, Any] = PROBABILITY_BUDGET_ALLOCATION_SPEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach deterministic cash budgets without changing candidate selection."""
    _assert_frozen_allocation_spec(allocation_spec)
    if isinstance(max_active_positions, bool) or not isinstance(
        max_active_positions,
        int,
    ) or max_active_positions <= 0:
        raise ValueError("probability budget maximum active positions is invalid")
    exposure = _finite_positive(
        exposure_multiplier,
        label="probability budget exposure multiplier",
    )
    source = [dict(trade) for trade in selected]
    if any("position_budget_fraction" in trade for trade in source):
        raise ValueError("probability budget must not be preloaded")
    trade_keys = [_trade_key(trade) for trade in source]
    if len(trade_keys) != len(set(trade_keys)):
        raise ValueError("probability budget trade keys are duplicated")
    _validate_selected_active_capacity(
        source,
        max_active_positions=max_active_positions,
    )

    gate_value = float(SHALLOW_GBDT_SCORE_CONTRACT["value"])
    capacity = exposure / max_active_positions
    allocated: list[dict[str, Any]] = []
    allocation_rows: list[dict[str, Any]] = []
    for trade in source:
        if not candidate_passes_gate(
            trade,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        ):
            raise ValueError("probability budget requires strict positive probability gate")
        probability = candidate_score(
            trade,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )
        position_budget_fraction = (
            capacity * (probability - gate_value) / (1.0 - gate_value)
        )
        if (
            not math.isfinite(position_budget_fraction)
            or position_budget_fraction <= 0.0
            or position_budget_fraction > capacity
        ):
            raise ValueError("probability budget calculation is invalid")
        allocated.append(
            {
                **trade,
                "position_budget_fraction": position_budget_fraction,
            }
        )
        allocation_rows.append(
            _allocation_row(
                trade,
                probability=probability,
                position_budget_fraction=position_budget_fraction,
            )
        )
    allocation_rows.sort(key=lambda item: str(item["trade_key"]))
    receipt = {
        "schema_version": "shallow-gbdt-probability-budget-receipt/v1",
        "allocation": dict(PROBABILITY_BUDGET_ALLOCATION_SPEC),
        "allocation_sha256": _PROBABILITY_BUDGET_ALLOCATION_SPEC_SHA256,
        "max_active_positions": max_active_positions,
        "exposure_multiplier": exposure,
        "selected_trade_keys": trade_keys,
        "selected_trade_keys_sha256": _canonical_sha256(trade_keys),
        "allocation_rows": allocation_rows,
        "allocation_rows_sha256": _canonical_sha256(allocation_rows),
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return allocated, receipt


def verify_shallow_gbdt_probability_budget_receipt(
    allocated: Sequence[Mapping[str, Any]],
    receipt: Mapping[str, Any],
    *,
    max_active_positions: int,
    exposure_multiplier: float,
) -> dict[str, Any]:
    """Rebuild the allocation from unbudgeted selected trades and compare exactly."""
    source: list[dict[str, Any]] = []
    for trade in allocated:
        item = dict(trade)
        if "position_budget_fraction" not in item:
            raise ValueError("probability budget allocation is missing")
        item.pop("position_budget_fraction")
        source.append(item)
    expected_allocated, expected_receipt = (
        allocate_shallow_gbdt_probability_budget(
            source,
            max_active_positions=max_active_positions,
            exposure_multiplier=exposure_multiplier,
        )
    )
    if (
        [dict(trade) for trade in allocated] != expected_allocated
        or dict(receipt) != expected_receipt
    ):
        raise ValueError("probability budget allocation differs from receipt")
    return {
        "verified": True,
        "receipt_sha256": expected_receipt["receipt_sha256"],
        "allocated_position_count": len(expected_allocated),
    }


def _assert_frozen_probability_budget_strategy(
    strategy_spec: Mapping[str, Any],
) -> None:
    expected = SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
    actual = dict(strategy_spec)
    if (
        not _exact_value_match(actual, expected)
        or _canonical_sha256(actual)
        != _SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256
    ):
        raise ValueError("shallow GBDT probability budget strategy is not frozen")


def evaluate_shallow_gbdt_probability_budget_hypothesis(
    candidates: Sequence[Mapping[str, Any]],
    *,
    evaluation_session_dates: Sequence[str],
    strategy_spec: Mapping[str, Any] = (
        SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
    ),
) -> dict[str, Any]:
    """Evaluate only the frozen allocation change against an equal-slot control."""
    _assert_frozen_probability_budget_strategy(strategy_spec)
    from app import audited_pit_continuous_ridge_oof as ridge

    main_sweep, main_selection_receipt = ridge._evaluate_fixed_oof(
        candidates,
        rank_mode="positive_utility_probability",
        evaluation_session_dates=evaluation_session_dates,
        strategy_spec=strategy_spec,
        sweep_schema_version=(
            "strict-ranked-liquidity-shallow-gbdt-probability-budget-"
            "fixed-oof/v1"
        ),
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        apply_position_budget=True,
    )
    control_sweep, control_selection_receipt = ridge._evaluate_fixed_oof(
        candidates,
        rank_mode="positive_utility_probability",
        evaluation_session_dates=evaluation_session_dates,
        strategy_spec=strategy_spec,
        sweep_schema_version=(
            "strict-ranked-liquidity-shallow-gbdt-probability-budget-"
            "fixed-oof/v1"
        ),
        score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
        apply_position_budget=False,
    )
    main_row = dict(main_sweep["top"][0])
    control_row = dict(control_sweep["top"][0])
    if (
        main_selection_receipt != control_selection_receipt
        or "position_budget_allocation_receipt" not in main_row
        or "position_budget_allocation_receipt" in control_row
    ):
        raise ValueError("probability budget allocation control differs")
    selected_trade_keys = list(
        main_selection_receipt["selected_trade_keys"]
    )
    return {
        "schema_version": (
            "shallow-gbdt-probability-budget-hypothesis-evaluation/v1"
        ),
        "strategy": {
            **SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC,
            "strategy_sha256": (
                _SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256
            ),
        },
        "main_sweep": main_sweep,
        "equal_weight_control_sweep": control_sweep,
        "main_selection_receipt": main_selection_receipt,
        "equal_weight_control_selection_receipt": (
            control_selection_receipt
        ),
        "comparison": {
            "same_selected_trade_keys": True,
            "selected_trade_keys_sha256": main_selection_receipt[
                "selected_trade_keys_sha256"
            ],
            "main_allocation": "probability_budget",
            "control_allocation": "equal_slot_weight",
            "unallocated_main_cash_remains_cash": True,
        },
        "advancement_gate_passed": ridge._advancement_gate_passes(
            main_row,
            control_row,
        ),
        "selected_position_count": len(selected_trade_keys),
    }
