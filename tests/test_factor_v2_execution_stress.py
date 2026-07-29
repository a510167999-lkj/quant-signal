from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from typing import Any

import pytest

from app.factor_v2_execution_stress import (
    COMPLETION_BINDING_FILE_SHA256,
    IMPLEMENTATION_CONTRACT_FILE_SHA256,
    IMPLEMENTATION_CONTRACT_SUPPLEMENT_FILE_SHA256,
    STRESS_PREREGISTRATION_FILE_SHA256,
    build_execution_stress_decision_receipt,
    build_execution_stress_input_receipt,
)


ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
FIRST_GREEN_ARM = "overnight_20"
INPUT_FIELDS = {
    "initial_capital_cny",
    "twelve_month_capital_upper_bound_cny",
    "buy_commission_bps",
    "sell_commission_bps",
    "minimum_commission_cny",
    "sell_side_tax_bps",
    "other_buy_fees_bps",
    "other_sell_fees_bps",
    "conservative_one_way_slippage_bps",
    "execution_window",
    "maximum_participation_rate",
    "board_lot_and_odd_lot_policy",
}
SOURCE_BINDING = {
    "stress_preregistration_file_sha256": (
        "7610b851982dbbc3d1f570ed778f433068500ad12d6bb1d8034119647a106234"
    ),
    "implementation_contract_file_sha256": (
        "c13e7d023faa8f588fce7d23b940a20577121f22f2cd3751b227a1af3af48869"
    ),
    "implementation_contract_supplement_file_sha256": (
        "6092894657fc5706829b429aab1d7726de2c2b63361d918ceb5be04bee22c606"
    ),
    "completion_binding_file_sha256": (
        "efbf897315f03cb9d0609461258afec6a37c9da39201b357cafe2a523313e17f"
    ),
}


def _sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _signed(unsigned: dict[str, Any]) -> dict[str, Any]:
    return {**unsigned, "receipt_sha256": _sha256(unsigned)}


def _resign(receipt: dict[str, Any]) -> None:
    unsigned = deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = _sha256(unsigned)


def _inputs() -> dict[str, Any]:
    return {
        "initial_capital_cny": 100_000.0,
        "twelve_month_capital_upper_bound_cny": 300_000.0,
        "buy_commission_bps": 2.5,
        "sell_commission_bps": 2.5,
        "minimum_commission_cny": 5.0,
        "sell_side_tax_bps": 5.0,
        "other_buy_fees_bps": 0.1,
        "other_sell_fees_bps": 0.1,
        "conservative_one_way_slippage_bps": 10.0,
        "execution_window": {
            "timezone": "Asia/Shanghai",
            "entry": {
                "start_time": "09:30:00",
                "end_time": "09:35:00",
                "include_call_auction": False,
            },
            "exit": {
                "start_time": "14:55:00",
                "end_time": "15:00:00",
                "include_call_auction": False,
            },
        },
        "maximum_participation_rate": 0.10,
        "board_lot_and_odd_lot_policy": {
            "policy_id": "cn_a_buy_floor_lot_sell_all_v1",
            "buy_board_lot_shares": 100,
            "buy_rounding": "floor",
            "sell_rule": "sell_all_available_shares_including_odd_lots",
        },
    }


def _selector_receipt() -> dict[str, Any]:
    return _signed(
        {
            "schema_version": "factor-v2-decision-branch-selector-receipt/v1",
            "arm_order": list(ARM_ORDER),
            "arm_decisions": {
                "v2_control": "RED",
                "overnight_20": "GREEN",
                "intraday_20": "GREEN",
            },
            "selected_arm": FIRST_GREEN_ARM,
            "selected_branch": FIRST_GREEN_ARM,
            "low_rvol_overlay_status": "VOID",
            "selection_rule": (
                "first_green_in_arm_order_else_low_rvol20_rank_overlay_20"
            ),
            "source_decision_receipt_raw_file_sha256": "1" * 64,
            "source_decision_receipt_sha256": "8" * 64,
            "verified": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_recommendation_eligible": False,
        }
    )


def _source_authority_receipt(
    selector: dict[str, Any],
    input_receipt: dict[str, Any],
) -> dict[str, Any]:
    return _signed(
        {
            "schema_version": "factor-v2-execution-source-authority-receipt/v1",
            "selected_arm": FIRST_GREEN_ARM,
            "branch_selector_receipt_sha256": selector["receipt_sha256"],
            "input_receipt_sha256": input_receipt["receipt_sha256"],
            "liquidity_source_artifact_sha256": "2" * 64,
            "source_descriptor_receipt_sha256": "3" * 64,
            "full_source_rows_receipt_sha256": "4" * 64,
            "source_schema_sha256": "5" * 64,
            "collector_commit": "6" * 40,
            "pit_cutoff": "2026-07-28T15:00:00+08:00",
            "source_authority_status": "BOUND",
            "evidence_complete": True,
            "verified": True,
        }
    )


def _gate_receipt(
    gate: str,
    selector: dict[str, Any],
    input_receipt: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    details: dict[str, Any]
    if gate == "cost":
        details = {
            "roundtrip_diagnostic_bps": [45, 55, 65, 85],
            "actual_additional_slippage_bps_per_side": [0, 5, 10, 20],
            "required_pass_additional_slippage_bps_per_side": [0, 5, 10],
            "required_scenario_decisions": {
                "0": "GREEN",
                "5": "GREEN",
                "10": "GREEN",
            },
            "diagnostic_20bps_decision": "GREEN",
            "actual_fee_cash_replay_passed": True,
        }
    elif gate == "capacity":
        details = {
            "scenario_decisions": {
                "C_and_p0": "GREEN",
                "C_and_p0_over_2": "GREEN",
                "2C_and_p0": "GREEN",
                "2C_and_p0_over_2": "GREEN",
            },
            "overall_required_formula": (
                "C_and_p0 AND (C_and_p0_over_2 OR 2C_and_p0)"
            ),
            "overall_required_passed": True,
            "all_four_scenarios_persisted": True,
        }
    elif gate == "evidence":
        details = {
            "buy_and_sell_legs_present": True,
            "at_C_sizing_receipts_present": True,
            "at_two_C_sizing_receipts_present": True,
            "source_descriptor_present": True,
            "full_source_rows_present": True,
        }
    else:
        details = {
            "minimum_complete_trades_passed": True,
            "full_development_win_rate_passed": True,
            "raw_max_drawdown_passed": True,
            "profit_factor_passed": True,
            "latest_complete_365d_return_passed": True,
            "latest_complete_365d_calmar_passed": True,
            "every_complete_365d_window_passed": True,
        }
    return _signed(
        {
            "schema_version": f"factor-v2-execution-stress-{gate}-gate/v1",
            "gate": gate,
            "selected_arm": FIRST_GREEN_ARM,
            "branch_selector_receipt_sha256": selector["receipt_sha256"],
            "input_receipt_sha256": input_receipt["receipt_sha256"],
            "source_authority_receipt_sha256": authority["receipt_sha256"],
            "selected_trade_keys_root_sha256": "7" * 64,
            "status": "GREEN",
            "evidence_complete": True,
            "details": details,
            "switch_selected_arm_allowed": False,
            "trade_deletion_allowed": False,
            "partial_fill_assumption_allowed": False,
            "retraining_allowed": False,
            "rescoring_allowed": False,
        }
    )


def _receipts() -> dict[str, dict[str, Any]]:
    selector = _selector_receipt()
    input_receipt = build_execution_stress_input_receipt(_inputs())
    authority = _source_authority_receipt(selector, input_receipt)
    return {
        "branch_selector_receipt": selector,
        "input_receipt": input_receipt,
        "source_authority_receipt": authority,
        "cost_gate_receipt": _gate_receipt(
            "cost", selector, input_receipt, authority
        ),
        "capacity_gate_receipt": _gate_receipt(
            "capacity", selector, input_receipt, authority
        ),
        "evidence_gate_receipt": _gate_receipt(
            "evidence", selector, input_receipt, authority
        ),
        "threshold_gate_receipt": _gate_receipt(
            "threshold", selector, input_receipt, authority
        ),
    }


def _rebind_selector_dependents(
    receipts: dict[str, dict[str, Any]],
    *,
    selected_arm: str | None = None,
) -> None:
    selector = receipts["branch_selector_receipt"]
    authority = receipts["source_authority_receipt"]
    authority["branch_selector_receipt_sha256"] = selector["receipt_sha256"]
    if selected_arm is not None:
        authority["selected_arm"] = selected_arm
    _resign(authority)
    for gate_key in (
        "cost_gate_receipt",
        "capacity_gate_receipt",
        "evidence_gate_receipt",
        "threshold_gate_receipt",
    ):
        gate = receipts[gate_key]
        gate["branch_selector_receipt_sha256"] = selector["receipt_sha256"]
        gate["source_authority_receipt_sha256"] = authority["receipt_sha256"]
        if selected_arm is not None:
            gate["selected_arm"] = selected_arm
        _resign(gate)


def test_input_receipt_requires_exactly_twelve_explicit_frozen_inputs() -> None:
    inputs = _inputs()
    assert set(inputs) == INPUT_FIELDS

    receipt = build_execution_stress_input_receipt(inputs)

    assert receipt["inputs"] == inputs
    assert receipt["source_binding"] == SOURCE_BINDING
    assert receipt["receipt_sha256"] == _sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    assert STRESS_PREREGISTRATION_FILE_SHA256 == (
        SOURCE_BINDING["stress_preregistration_file_sha256"]
    )
    assert IMPLEMENTATION_CONTRACT_FILE_SHA256 == (
        SOURCE_BINDING["implementation_contract_file_sha256"]
    )
    assert IMPLEMENTATION_CONTRACT_SUPPLEMENT_FILE_SHA256 == (
        SOURCE_BINDING["implementation_contract_supplement_file_sha256"]
    )
    assert COMPLETION_BINDING_FILE_SHA256 == (
        SOURCE_BINDING["completion_binding_file_sha256"]
    )

    for missing in INPUT_FIELDS:
        incomplete = deepcopy(inputs)
        incomplete.pop(missing)
        with pytest.raises(ValueError):
            build_execution_stress_input_receipt(incomplete)
    with pytest.raises(ValueError):
        build_execution_stress_input_receipt({**inputs, "implicit_default": 1})


def test_formal_orchestrator_has_no_defaulted_receipt_inputs() -> None:
    parameters = inspect.signature(
        build_execution_stress_decision_receipt
    ).parameters

    assert set(parameters) == {
        "branch_selector_receipt",
        "input_receipt",
        "source_authority_receipt",
        "cost_gate_receipt",
        "capacity_gate_receipt",
        "evidence_gate_receipt",
        "threshold_gate_receipt",
    }
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in parameters.values()
    )


def test_all_bound_green_gates_emit_content_addressed_safe_green() -> None:
    receipts = _receipts()

    result = build_execution_stress_decision_receipt(**receipts)

    assert result["status"] == "GREEN"
    assert result["selected_arm"] == FIRST_GREEN_ARM
    assert result["branch_selector_receipt_sha256"] == (
        receipts["branch_selector_receipt"]["receipt_sha256"]
    )
    assert result["input_receipt_sha256"] == (
        receipts["input_receipt"]["receipt_sha256"]
    )
    assert result["source_authority_receipt_sha256"] == (
        receipts["source_authority_receipt"]["receipt_sha256"]
    )
    assert result["source_decision_receipt_raw_file_sha256"] == (
        receipts["branch_selector_receipt"][
            "source_decision_receipt_raw_file_sha256"
        ]
    )
    assert result["source_decision_receipt_sha256"] == (
        receipts["branch_selector_receipt"]["source_decision_receipt_sha256"]
    )
    assert result["gate_receipt_sha256s"] == {
        "cost": receipts["cost_gate_receipt"]["receipt_sha256"],
        "capacity": receipts["capacity_gate_receipt"]["receipt_sha256"],
        "evidence": receipts["evidence_gate_receipt"]["receipt_sha256"],
        "threshold": receipts["threshold_gate_receipt"]["receipt_sha256"],
    }
    assert result["switch_selected_arm_allowed"] is False
    assert result["trade_deletion_allowed"] is False
    assert result["partial_fill_assumption_allowed"] is False
    assert result["retraining_allowed"] is False
    assert result["rescoring_allowed"] is False
    assert result["embargo_consumed"] is False
    assert result["final_oos_consumed"] is False
    assert result["production_profile_registered"] is False
    assert result["production_recommendation_eligible"] is False
    assert result["receipt_sha256"] == _sha256(
        {key: value for key, value in result.items() if key != "receipt_sha256"}
    )


@pytest.mark.parametrize(
    "gate_key",
    [
        "cost_gate_receipt",
        "capacity_gate_receipt",
        "evidence_gate_receipt",
        "threshold_gate_receipt",
    ],
)
def test_any_gate_failure_is_red_without_switching_to_later_green_arm(
    gate_key: str,
) -> None:
    receipts = _receipts()
    receipts[gate_key]["status"] = "RED"
    receipts[gate_key]["evidence_complete"] = gate_key != "evidence_gate_receipt"
    _resign(receipts[gate_key])

    result = build_execution_stress_decision_receipt(**receipts)

    assert result["status"] == "RED"
    assert result["selected_arm"] == FIRST_GREEN_ARM
    assert result["switch_selected_arm_allowed"] is False
    assert result["failure_codes"]


def test_unbound_source_authority_is_red() -> None:
    receipts = _receipts()
    authority = receipts["source_authority_receipt"]
    authority["source_authority_status"] = "UNBOUND"
    authority["evidence_complete"] = False
    _resign(authority)
    for gate_key in (
        "cost_gate_receipt",
        "capacity_gate_receipt",
        "evidence_gate_receipt",
        "threshold_gate_receipt",
    ):
        receipts[gate_key]["source_authority_receipt_sha256"] = authority[
            "receipt_sha256"
        ]
        _resign(receipts[gate_key])

    result = build_execution_stress_decision_receipt(**receipts)

    assert result["status"] == "RED"
    assert result["selected_arm"] == FIRST_GREEN_ARM
    assert result["production_recommendation_eligible"] is False


@pytest.mark.parametrize(
    ("gate_key", "forbidden_field"),
    [
        ("cost_gate_receipt", "retraining_allowed"),
        ("cost_gate_receipt", "rescoring_allowed"),
        ("capacity_gate_receipt", "trade_deletion_allowed"),
        ("capacity_gate_receipt", "partial_fill_assumption_allowed"),
    ],
)
def test_forbidden_strategy_or_trade_mutation_is_red(
    gate_key: str,
    forbidden_field: str,
) -> None:
    receipts = _receipts()
    receipts[gate_key][forbidden_field] = True
    _resign(receipts[gate_key])

    result = build_execution_stress_decision_receipt(**receipts)

    assert result["status"] == "RED"
    assert result["selected_arm"] == FIRST_GREEN_ARM


@pytest.mark.parametrize(
    "drift",
    ["branch_arm_mismatch", "manually_selected_later_green"],
)
def test_rejects_selector_drift_or_manually_selected_later_green(
    drift: str,
) -> None:
    receipts = _receipts()
    selector = receipts["branch_selector_receipt"]
    selector["selected_branch"] = "intraday_20"
    if drift == "manually_selected_later_green":
        selector["selected_arm"] = "intraday_20"
    _resign(selector)
    _rebind_selector_dependents(
        receipts,
        selected_arm=(
            "intraday_20" if drift == "manually_selected_later_green" else None
        ),
    )

    with pytest.raises(ValueError):
        build_execution_stress_decision_receipt(**receipts)


@pytest.mark.parametrize(
    "field",
    [
        "source_decision_receipt_raw_file_sha256",
        "source_decision_receipt_sha256",
    ],
)
def test_selector_requires_decision_minimal_raw_and_self_hashes(
    field: str,
) -> None:
    receipts = _receipts()
    selector = receipts["branch_selector_receipt"]
    selector.pop(field)
    _resign(selector)
    _rebind_selector_dependents(receipts)

    with pytest.raises(ValueError):
        build_execution_stress_decision_receipt(**receipts)


def test_selector_requires_low_rvol_overlay_to_remain_void() -> None:
    receipts = _receipts()
    selector = receipts["branch_selector_receipt"]
    selector["low_rvol_overlay_status"] = "ACTIVATED"
    _resign(selector)
    _rebind_selector_dependents(receipts)

    with pytest.raises(ValueError):
        build_execution_stress_decision_receipt(**receipts)


def test_rejects_tampered_content_addressed_receipt() -> None:
    receipts = _receipts()
    receipts["capacity_gate_receipt"]["status"] = "RED"

    with pytest.raises(ValueError):
        build_execution_stress_decision_receipt(**receipts)
