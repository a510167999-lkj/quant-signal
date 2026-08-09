from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


STRESS_PREREGISTRATION_FILE_SHA256 = (
    "7610b851982dbbc3d1f570ed778f433068500ad12d6bb1d8034119647a106234"
)
IMPLEMENTATION_CONTRACT_FILE_SHA256 = (
    "c13e7d023faa8f588fce7d23b940a20577121f22f2cd3751b227a1af3af48869"
)
IMPLEMENTATION_CONTRACT_SUPPLEMENT_FILE_SHA256 = (
    "6092894657fc5706829b429aab1d7726de2c2b63361d918ceb5be04bee22c606"
)
COMPLETION_BINDING_FILE_SHA256 = "efbf897315f03cb9d0609461258afec6a37c9da39201b357cafe2a523313e17f"

_ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
_SOURCE_BINDING = {
    "stress_preregistration_file_sha256": STRESS_PREREGISTRATION_FILE_SHA256,
    "implementation_contract_file_sha256": IMPLEMENTATION_CONTRACT_FILE_SHA256,
    "implementation_contract_supplement_file_sha256": (
        IMPLEMENTATION_CONTRACT_SUPPLEMENT_FILE_SHA256
    ),
    "completion_binding_file_sha256": COMPLETION_BINDING_FILE_SHA256,
}
_INPUT_FIELDS = (
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
)
_NONNEGATIVE_INPUT_FIELDS = (
    "buy_commission_bps",
    "sell_commission_bps",
    "minimum_commission_cny",
    "sell_side_tax_bps",
    "other_buy_fees_bps",
    "other_sell_fees_bps",
    "conservative_one_way_slippage_bps",
)
_WINDOW_FIELDS = ("timezone", "entry", "exit")
_WINDOW_LEG_FIELDS = ("start_time", "end_time", "include_call_auction")
_POLICY_FIELDS = (
    "policy_id",
    "buy_board_lot_shares",
    "buy_rounding",
    "sell_rule",
)
_SUPPORTED_POLICY = {
    "policy_id": "cn_a_buy_floor_lot_sell_all_v1",
    "buy_board_lot_shares": 100,
    "buy_rounding": "floor",
    "sell_rule": "sell_all_available_shares_including_odd_lots",
}
_SELECTOR_FIELDS = (
    "schema_version",
    "arm_order",
    "arm_decisions",
    "selected_arm",
    "selected_branch",
    "low_rvol_overlay_status",
    "selection_rule",
    "source_decision_receipt_raw_file_sha256",
    "source_decision_receipt_sha256",
    "evaluation_artifact_sha256",
    "contract_binding_validated",
    "publisher_terminal_chain_verified",
    "source_authority_complete",
    "formal_materialization_eligible",
    "verified",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "receipt_sha256",
)
_AUTHORITY_FIELDS = (
    "schema_version",
    "selected_arm",
    "branch_selector_receipt_sha256",
    "input_receipt_sha256",
    "liquidity_source_artifact_sha256",
    "source_descriptor_receipt_sha256",
    "full_source_rows_receipt_sha256",
    "source_schema_sha256",
    "collector_commit",
    "pit_cutoff",
    "source_authority_status",
    "evidence_complete",
    "verified",
    "receipt_sha256",
)
_GATE_FIELDS = (
    "schema_version",
    "gate",
    "selected_arm",
    "branch_selector_receipt_sha256",
    "input_receipt_sha256",
    "source_authority_receipt_sha256",
    "selected_trade_keys_root_sha256",
    "status",
    "evidence_complete",
    "details",
    "switch_selected_arm_allowed",
    "trade_deletion_allowed",
    "partial_fill_assumption_allowed",
    "retraining_allowed",
    "rescoring_allowed",
    "receipt_sha256",
)
_FORBIDDEN_GATE_FLAGS = (
    "switch_selected_arm_allowed",
    "trade_deletion_allowed",
    "partial_fill_assumption_allowed",
    "retraining_allowed",
    "rescoring_allowed",
)


def _canonical_sha256(value: Any) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt is not canonically serializable") from exc
    return hashlib.sha256(raw).hexdigest()


def _exact_mapping(
    value: Any,
    fields: tuple[str, ...],
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    source = dict(value)
    expected = set(fields)
    if set(source) != expected:
        raise ValueError(f"{field} fields must be exactly {sorted(expected)}")
    return source


def _require_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")
    return value


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _require_sha256(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _require_commit(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase commit hash")
    return value


def _verify_self_hash(
    receipt: Any,
    fields: tuple[str, ...],
    *,
    field: str,
) -> dict[str, Any]:
    source = _exact_mapping(receipt, fields, field=field)
    receipt_sha256 = _require_sha256(
        source["receipt_sha256"],
        field=f"{field}.receipt_sha256",
    )
    unsigned = {key: deepcopy(value) for key, value in source.items() if key != "receipt_sha256"}
    if _canonical_sha256(unsigned) != receipt_sha256:
        raise ValueError(f"{field} content hash drifted")
    return source


def _validate_window_leg(value: Any, *, field: str) -> None:
    leg = _exact_mapping(value, _WINDOW_LEG_FIELDS, field=field)
    _require_bool(
        leg["include_call_auction"],
        field=f"{field}.include_call_auction",
    )
    parsed: list[datetime] = []
    for name in ("start_time", "end_time"):
        raw = leg[name]
        if not isinstance(raw, str) or len(raw) != 8:
            raise ValueError(f"{field}.{name} must use HH:MM:SS")
        try:
            parsed.append(datetime.strptime(raw, "%H:%M:%S"))
        except ValueError as exc:
            raise ValueError(f"{field}.{name} must use HH:MM:SS") from exc
    if parsed[0] >= parsed[1]:
        raise ValueError(f"{field}.start_time must precede end_time")


def _validate_execution_window(value: Any) -> None:
    window = _exact_mapping(
        value,
        _WINDOW_FIELDS,
        field="inputs.execution_window",
    )
    if window["timezone"] != "Asia/Shanghai":
        raise ValueError("execution window timezone drifted")
    _validate_window_leg(
        window["entry"],
        field="inputs.execution_window.entry",
    )
    _validate_window_leg(
        window["exit"],
        field="inputs.execution_window.exit",
    )


def _validate_board_lot_policy(value: Any) -> None:
    policy = _exact_mapping(
        value,
        _POLICY_FIELDS,
        field="inputs.board_lot_and_odd_lot_policy",
    )
    if policy != _SUPPORTED_POLICY:
        raise ValueError("board-lot and odd-lot policy drifted")


def build_execution_stress_input_receipt(
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    source = _exact_mapping(inputs, _INPUT_FIELDS, field="inputs")
    initial = _finite_number(
        source["initial_capital_cny"],
        field="inputs.initial_capital_cny",
    )
    if initial <= 0:
        raise ValueError("initial capital must be positive")
    upper = _finite_number(
        source["twelve_month_capital_upper_bound_cny"],
        field="inputs.twelve_month_capital_upper_bound_cny",
    )
    if upper < initial:
        raise ValueError("twelve-month capital upper bound is below initial capital")
    for name in _NONNEGATIVE_INPUT_FIELDS:
        if _finite_number(source[name], field=f"inputs.{name}") < 0:
            raise ValueError(f"inputs.{name} must be nonnegative")
    participation = _finite_number(
        source["maximum_participation_rate"],
        field="inputs.maximum_participation_rate",
    )
    if not 0 < participation <= 1:
        raise ValueError("maximum participation rate must be in (0, 1]")
    _validate_execution_window(source["execution_window"])
    _validate_board_lot_policy(source["board_lot_and_odd_lot_policy"])
    unsigned = {
        "schema_version": "factor-v2-execution-stress-input-receipt/v1",
        "source_binding": dict(_SOURCE_BINDING),
        "inputs": deepcopy(source),
    }
    return {**unsigned, "receipt_sha256": _canonical_sha256(unsigned)}


def _verify_input_receipt(receipt: Any) -> dict[str, Any]:
    source = _verify_self_hash(
        receipt,
        ("schema_version", "source_binding", "inputs", "receipt_sha256"),
        field="input_receipt",
    )
    if source["schema_version"] != ("factor-v2-execution-stress-input-receipt/v1"):
        raise ValueError("input receipt schema drifted")
    if source["source_binding"] != _SOURCE_BINDING:
        raise ValueError("input receipt source binding drifted")
    if build_execution_stress_input_receipt(source["inputs"]) != source:
        raise ValueError("input receipt replay drifted")
    return source


def _verify_selector(receipt: Any) -> dict[str, Any]:
    source = _verify_self_hash(
        receipt,
        _SELECTOR_FIELDS,
        field="branch_selector_receipt",
    )
    if source["schema_version"] != (
        "factor-v2-decision-branch-structural-adapter/v2"
    ):
        raise ValueError("branch selector schema drifted")
    if source["arm_order"] != list(_ARM_ORDER):
        raise ValueError("branch selector arm order drifted")
    decisions = _exact_mapping(
        source["arm_decisions"],
        _ARM_ORDER,
        field="branch_selector_receipt.arm_decisions",
    )
    if any(decision not in {"GREEN", "RED"} for decision in decisions.values()):
        raise ValueError("branch selector decision is invalid")
    first_green = next(
        (arm for arm in _ARM_ORDER if decisions[arm] == "GREEN"),
        None,
    )
    if first_green is None:
        raise ValueError("execution stress requires a first-GREEN arm")
    if source["selected_arm"] != first_green or source["selected_branch"] != first_green:
        raise ValueError("branch selector did not select the first-GREEN arm")
    if source["low_rvol_overlay_status"] != "VOID":
        raise ValueError("low-rvol overlay must remain VOID")
    if source["selection_rule"] != ("first_green_in_arm_order_else_low_rvol20_rank_overlay_20"):
        raise ValueError("branch selector rule drifted")
    _require_sha256(
        source["source_decision_receipt_raw_file_sha256"],
        field="branch_selector_receipt.source_decision_receipt_raw_file_sha256",
    )
    _require_sha256(
        source["source_decision_receipt_sha256"],
        field="branch_selector_receipt.source_decision_receipt_sha256",
    )
    _require_sha256(
        source["evaluation_artifact_sha256"],
        field="branch_selector_receipt.evaluation_artifact_sha256",
    )
    if (
        source["contract_binding_validated"] is not True
        or source["publisher_terminal_chain_verified"] is not False
        or source["source_authority_complete"] is not False
        or source["formal_materialization_eligible"] is not False
        or source["verified"] is not False
    ):
        raise ValueError("branch selector structural adapter scope drifted")
    for name in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_recommendation_eligible",
    ):
        if _require_bool(
            source[name],
            field=f"branch_selector_receipt.{name}",
        ):
            raise ValueError(f"branch selector safety flag {name} is open")
    return source


def _verify_authority(
    receipt: Any,
    *,
    selected_arm: str,
    selector_sha256: str,
    input_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    source = _verify_self_hash(
        receipt,
        _AUTHORITY_FIELDS,
        field="source_authority_receipt",
    )
    if source["schema_version"] != ("factor-v2-execution-source-authority-receipt/v1"):
        raise ValueError("source authority schema drifted")
    if source["selected_arm"] != selected_arm:
        raise ValueError("source authority selected arm drifted")
    if source["branch_selector_receipt_sha256"] != selector_sha256:
        raise ValueError("source authority selector binding drifted")
    if source["input_receipt_sha256"] != input_sha256:
        raise ValueError("source authority input binding drifted")
    for name in (
        "liquidity_source_artifact_sha256",
        "source_descriptor_receipt_sha256",
        "full_source_rows_receipt_sha256",
        "source_schema_sha256",
    ):
        _require_sha256(source[name], field=f"source_authority_receipt.{name}")
    _require_commit(
        source["collector_commit"],
        field="source_authority_receipt.collector_commit",
    )
    if not isinstance(source["pit_cutoff"], str) or not source["pit_cutoff"]:
        raise ValueError("source authority PIT cutoff is missing")
    try:
        cutoff = datetime.fromisoformat(source["pit_cutoff"])
    except ValueError as exc:
        raise ValueError("source authority PIT cutoff is invalid") from exc
    if cutoff.tzinfo is None:
        raise ValueError("source authority PIT cutoff must be timezone-aware")
    if source["source_authority_status"] not in {"BOUND", "UNBOUND"}:
        raise ValueError("source authority status is invalid")
    evidence_complete = _require_bool(
        source["evidence_complete"],
        field="source_authority_receipt.evidence_complete",
    )
    verified = _require_bool(
        source["verified"],
        field="source_authority_receipt.verified",
    )
    failures: list[str] = []
    if source["source_authority_status"] != "BOUND":
        failures.append("SOURCE_AUTHORITY_UNBOUND")
    if not evidence_complete:
        failures.append("SOURCE_AUTHORITY_EVIDENCE_INCOMPLETE")
    if not verified:
        failures.append("SOURCE_AUTHORITY_UNVERIFIED")
    return source, failures


def _cost_details_pass(details: Any) -> bool:
    source = _exact_mapping(
        details,
        (
            "roundtrip_diagnostic_bps",
            "actual_additional_slippage_bps_per_side",
            "required_pass_additional_slippage_bps_per_side",
            "required_scenario_decisions",
            "diagnostic_20bps_decision",
            "actual_fee_cash_replay_passed",
        ),
        field="cost_gate_receipt.details",
    )
    if source["roundtrip_diagnostic_bps"] != [45, 55, 65, 85]:
        raise ValueError("cost diagnostic grid drifted")
    if source["actual_additional_slippage_bps_per_side"] != [0, 5, 10, 20]:
        raise ValueError("actual slippage grid drifted")
    if source["required_pass_additional_slippage_bps_per_side"] != [0, 5, 10]:
        raise ValueError("required slippage grid drifted")
    decisions = _exact_mapping(
        source["required_scenario_decisions"],
        ("0", "5", "10"),
        field="cost_gate_receipt.details.required_scenario_decisions",
    )
    if any(decision not in {"GREEN", "RED"} for decision in decisions.values()):
        raise ValueError("cost scenario decision is invalid")
    if source["diagnostic_20bps_decision"] not in {"GREEN", "RED"}:
        raise ValueError("20bps diagnostic decision is invalid")
    actual_fee_passed = _require_bool(
        source["actual_fee_cash_replay_passed"],
        field="cost_gate_receipt.details.actual_fee_cash_replay_passed",
    )
    return actual_fee_passed and all(decision == "GREEN" for decision in decisions.values())


def _capacity_details_pass(details: Any) -> bool:
    source = _exact_mapping(
        details,
        (
            "scenario_decisions",
            "overall_required_formula",
            "overall_required_passed",
            "all_four_scenarios_persisted",
        ),
        field="capacity_gate_receipt.details",
    )
    decisions = _exact_mapping(
        source["scenario_decisions"],
        (
            "C_and_p0",
            "C_and_p0_over_2",
            "2C_and_p0",
            "2C_and_p0_over_2",
        ),
        field="capacity_gate_receipt.details.scenario_decisions",
    )
    if any(decision not in {"GREEN", "RED"} for decision in decisions.values()):
        raise ValueError("capacity scenario decision is invalid")
    if source["overall_required_formula"] != ("C_and_p0 AND (C_and_p0_over_2 OR 2C_and_p0)"):
        raise ValueError("capacity required formula drifted")
    declared_pass = _require_bool(
        source["overall_required_passed"],
        field="capacity_gate_receipt.details.overall_required_passed",
    )
    all_persisted = _require_bool(
        source["all_four_scenarios_persisted"],
        field="capacity_gate_receipt.details.all_four_scenarios_persisted",
    )
    replayed_pass = decisions["C_and_p0"] == "GREEN" and (
        decisions["C_and_p0_over_2"] == "GREEN" or decisions["2C_and_p0"] == "GREEN"
    )
    return declared_pass and replayed_pass and all_persisted


def _all_boolean_details_pass(
    details: Any,
    fields: tuple[str, ...],
    *,
    field: str,
) -> bool:
    source = _exact_mapping(details, fields, field=field)
    return all(_require_bool(value, field=f"{field}.{name}") for name, value in source.items())


def _gate_details_pass(gate: str, details: Any) -> bool:
    if gate == "cost":
        return _cost_details_pass(details)
    if gate == "capacity":
        return _capacity_details_pass(details)
    if gate == "evidence":
        return _all_boolean_details_pass(
            details,
            (
                "buy_and_sell_legs_present",
                "at_C_sizing_receipts_present",
                "at_two_C_sizing_receipts_present",
                "source_descriptor_present",
                "full_source_rows_present",
            ),
            field="evidence_gate_receipt.details",
        )
    return _all_boolean_details_pass(
        details,
        (
            "minimum_complete_trades_passed",
            "full_development_win_rate_passed",
            "raw_max_drawdown_passed",
            "profit_factor_passed",
            "latest_complete_365d_return_passed",
            "latest_complete_365d_calmar_passed",
            "every_complete_365d_window_passed",
        ),
        field="threshold_gate_receipt.details",
    )


def _verify_gate(
    receipt: Any,
    *,
    gate: str,
    selected_arm: str,
    selector_sha256: str,
    input_sha256: str,
    authority_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    source = _verify_self_hash(
        receipt,
        _GATE_FIELDS,
        field=f"{gate}_gate_receipt",
    )
    if source["schema_version"] != (f"factor-v2-execution-stress-{gate}-gate/v1"):
        raise ValueError(f"{gate} gate schema drifted")
    if source["gate"] != gate:
        raise ValueError(f"{gate} gate identity drifted")
    if source["selected_arm"] != selected_arm:
        raise ValueError(f"{gate} gate selected arm drifted")
    expected_bindings = {
        "branch_selector_receipt_sha256": selector_sha256,
        "input_receipt_sha256": input_sha256,
        "source_authority_receipt_sha256": authority_sha256,
    }
    for name, expected in expected_bindings.items():
        if source[name] != expected:
            raise ValueError(f"{gate} gate {name} drifted")
    _require_sha256(
        source["selected_trade_keys_root_sha256"],
        field=f"{gate}_gate_receipt.selected_trade_keys_root_sha256",
    )
    if source["status"] not in {"GREEN", "RED"}:
        raise ValueError(f"{gate} gate status is invalid")
    evidence_complete = _require_bool(
        source["evidence_complete"],
        field=f"{gate}_gate_receipt.evidence_complete",
    )
    details_pass = _gate_details_pass(gate, source["details"])
    failures: list[str] = []
    if source["status"] != "GREEN":
        failures.append(f"{gate.upper()}_GATE_RED")
    if not evidence_complete:
        failures.append(f"{gate.upper()}_EVIDENCE_INCOMPLETE")
    if not details_pass:
        failures.append(f"{gate.upper()}_DETAILS_FAILED")
    for name in _FORBIDDEN_GATE_FLAGS:
        if _require_bool(source[name], field=f"{gate}_gate_receipt.{name}"):
            failures.append(f"{gate.upper()}_{name.upper()}")
    return source, failures


def build_execution_stress_decision_receipt(
    branch_selector_receipt: Mapping[str, Any],
    input_receipt: Mapping[str, Any],
    source_authority_receipt: Mapping[str, Any],
    cost_gate_receipt: Mapping[str, Any],
    capacity_gate_receipt: Mapping[str, Any],
    evidence_gate_receipt: Mapping[str, Any],
    threshold_gate_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    selector = _verify_selector(branch_selector_receipt)
    verified_input = _verify_input_receipt(input_receipt)
    selected_arm = selector["selected_arm"]
    selector_sha256 = selector["receipt_sha256"]
    input_sha256 = verified_input["receipt_sha256"]
    authority, failure_codes = _verify_authority(
        source_authority_receipt,
        selected_arm=selected_arm,
        selector_sha256=selector_sha256,
        input_sha256=input_sha256,
    )
    gates: dict[str, dict[str, Any]] = {}
    gate_inputs = {
        "cost": cost_gate_receipt,
        "capacity": capacity_gate_receipt,
        "evidence": evidence_gate_receipt,
        "threshold": threshold_gate_receipt,
    }
    for gate, receipt in gate_inputs.items():
        verified_gate, gate_failures = _verify_gate(
            receipt,
            gate=gate,
            selected_arm=selected_arm,
            selector_sha256=selector_sha256,
            input_sha256=input_sha256,
            authority_sha256=authority["receipt_sha256"],
        )
        gates[gate] = verified_gate
        failure_codes.extend(gate_failures)
    trade_roots = {gate["selected_trade_keys_root_sha256"] for gate in gates.values()}
    if len(trade_roots) != 1:
        raise ValueError("gate selected-trade roots drifted")
    unsigned = {
        "schema_version": "factor-v2-execution-stress-decision-receipt/v1",
        "source_binding": dict(_SOURCE_BINDING),
        "selected_arm": selected_arm,
        "source_decision_receipt_raw_file_sha256": selector[
            "source_decision_receipt_raw_file_sha256"
        ],
        "source_decision_receipt_sha256": selector["source_decision_receipt_sha256"],
        "evaluation_artifact_sha256": selector["evaluation_artifact_sha256"],
        "branch_selector_receipt_sha256": selector_sha256,
        "input_receipt_sha256": input_sha256,
        "source_authority_receipt_sha256": authority["receipt_sha256"],
        "gate_receipt_sha256s": {
            gate: receipt["receipt_sha256"] for gate, receipt in gates.items()
        },
        "selected_trade_keys_root_sha256": next(iter(trade_roots)),
        "status": "RED" if failure_codes else "GREEN",
        "failure_codes": failure_codes,
        "switch_selected_arm_allowed": False,
        "trade_deletion_allowed": False,
        "partial_fill_assumption_allowed": False,
        "retraining_allowed": False,
        "rescoring_allowed": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    return {**unsigned, "receipt_sha256": _canonical_sha256(unsigned)}
