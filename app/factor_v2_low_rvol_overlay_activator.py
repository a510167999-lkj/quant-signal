from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


EXPECTED_PREREGISTRATION_RAW_SHA256 = (
    "54928441f34b92497c1c67b1b26c85e4bb267f9d3b069179ae870d017e6049a5"
)
EXPECTED_PREREGISTRATION_INTRODUCING_COMMIT = "e8baab1ac7d3c86a700fb89b5c8d6a912e43d2c0"
EXPECTED_FACTOR_V2_SPEC_SHA256 = "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
EXPECTED_EVALUATION_PRODUCER_ROOT_SHA256 = (
    "926b9229a2e6e47ae24f3b590fab46ded2244e757fbe92fc6bbf0dd3de1dd938"
)
EXPECTED_VERIFICATION_PRODUCER_ROOT_SHA256 = (
    "3b4c62fa4a02ced6a2f271af74deb75b3234298fe61ae21fc120fd59f2bca55f"
)
DECISION_RECEIPT_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-decision-verification-receipt/v1"
)
OVERLAY_RESULT_SCHEMA_VERSION = "factor-v2-low-rvol-overlay-result/v1"
OVERLAY_ID = "low_rvol20_rank_overlay_20"
EXPECTED_SELECTION_CONTRACT_SHA256 = (
    "ec7b0b455e6ed7ec53f81ef5696d9717136a5a6fe106dca1bf587f4f48cfa788"
)
ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
EXPECTED_SOURCE_RUN_IDENTITY = {
    "status_path": (
        "E:/AI workspace/quant-signal-lkj/data/research_runs/"
        "audited_pit_factor_v2_development_evaluation_v1_development_4"
        ".run.status.json"
    ),
    "pid": 31564,
    "started_at": "2026-07-29T02:44:43.361756+00:00",
    "runner_file_sha256": ("997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e"),
    "claim_file_sha256": ("d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228"),
    "lock_file_sha256": ("d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228"),
}

_DESCRIPTOR_FIELDS = {
    "path",
    "raw_file_sha256",
    "size_bytes",
    "schema_version",
    "receipt_sha256",
}
_RECEIPT_FIELDS = {
    "schema_version",
    "temporal_role",
    "factor_v2_spec_sha256",
    "evaluation_artifact_sha256",
    "evaluation_manifest_file_sha256",
    "evaluation_producer_root_sha256",
    "verification_producer_root_sha256",
    "arm_order",
    "arm_decisions",
    "source_run_identity",
    "verified",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "receipt_sha256",
}
_SAFETY_FLAGS = (
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
)
_FORBIDDEN_OPERATION_FLAGS = (
    "second_score_database_created",
    "parameter_search_performed",
    "weights_changed",
    "second_overlay_evaluated",
    "model_retrained",
    "model_rescored",
    "trade_deleted",
)
_PHASE_TWO_RESULT_FIELDS = {
    "schema_version",
    "overlay_id",
    "decision",
    "claim_key",
    "evaluation_artifact_sha256",
    "preregistration_raw_sha256",
    "selection_contract_sha256",
    "source_replay_receipt_sha256",
    "selection_receipt_sha256",
    "control_trade_keys_sha256",
    "overlay_trade_keys_sha256",
    "source_decision_receipt_sha256",
    "overlay_evaluation_receipt_sha256",
    "execution_stress_receipt_sha256",
    "evidence_complete",
    *_SAFETY_FLAGS,
    *_FORBIDDEN_OPERATION_FLAGS,
    "result_sha256",
}
_ALLOWED_PHASE_TWO_DECISIONS = {
    "GREEN",
    "RED",
    "RED_NO_INCREMENT_WITHOUT_STRESS",
}
_SELECTION_CONTRACT = {
    "selection_contract_sha256": EXPECTED_SELECTION_CONTRACT_SHA256,
    "top_n": 3,
    "max_active_positions": 3,
    "same_security_exclusion": True,
    "one_position_per_signal_industry": True,
    "same_day_exit_before_selection": True,
}
_SAME_KEY_REUSE_WAIT_SECONDS = 30.0
_RESULT_POINTER_STABILIZE_SECONDS = 1.0
_REUSE_POLL_SECONDS = 0.01


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse_strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"{label} has invalid or duplicate JSON key") from exc
    if type(value) is not dict:
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def verify_phase_one_decision_receipt(
    descriptor: Mapping[str, Any],
    *,
    read_receipt_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    frozen_descriptor = dict(descriptor)
    if set(frozen_descriptor) != _DESCRIPTOR_FIELDS:
        raise RuntimeError("decision receipt descriptor field schema drifted")
    if (
        frozen_descriptor["schema_version"] != DECISION_RECEIPT_SCHEMA_VERSION
        or not _is_sha256(frozen_descriptor["raw_file_sha256"])
        or not _is_sha256(frozen_descriptor["receipt_sha256"])
        or type(frozen_descriptor["size_bytes"]) is not int
        or frozen_descriptor["size_bytes"] < 0
        or type(frozen_descriptor["path"]) is not str
    ):
        raise RuntimeError("decision receipt descriptor schema drifted")

    path = Path(frozen_descriptor["path"])
    reader = read_receipt_bytes or Path.read_bytes
    try:
        raw = reader(path)
    except OSError as exc:
        raise RuntimeError("decision receipt cannot be read") from exc
    if type(raw) is not bytes:
        raise RuntimeError("decision receipt reader must return bytes")
    if (
        len(raw) != frozen_descriptor["size_bytes"]
        or hashlib.sha256(raw).hexdigest() != frozen_descriptor["raw_file_sha256"]
    ):
        raise RuntimeError("decision receipt raw content hash drifted")

    receipt = _parse_strict_json(raw, "decision receipt")
    if set(receipt) != _RECEIPT_FIELDS:
        raise RuntimeError("decision receipt field schema has extra or missing fields")
    if raw != _canonical_bytes(receipt):
        raise RuntimeError("decision receipt file is not canonical JSON")

    unsigned = dict(receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    if (
        not _is_sha256(receipt_sha256)
        or canonical_sha256(unsigned) != receipt_sha256
        or frozen_descriptor["receipt_sha256"] != receipt_sha256
    ):
        raise RuntimeError("decision receipt self hash or descriptor hash drifted")
    if receipt["schema_version"] != DECISION_RECEIPT_SCHEMA_VERSION:
        raise RuntimeError("decision receipt schema version drifted")
    if (
        receipt["temporal_role"] != "development"
        or receipt["factor_v2_spec_sha256"] != EXPECTED_FACTOR_V2_SPEC_SHA256
    ):
        raise RuntimeError("decision receipt development scope drifted")
    if (
        receipt["evaluation_producer_root_sha256"] != EXPECTED_EVALUATION_PRODUCER_ROOT_SHA256
        or receipt["verification_producer_root_sha256"]
        != EXPECTED_VERIFICATION_PRODUCER_ROOT_SHA256
    ):
        raise RuntimeError("decision receipt producer root drifted")
    if receipt["source_run_identity"] != EXPECTED_SOURCE_RUN_IDENTITY:
        raise RuntimeError("decision receipt source run identity drifted")
    if receipt["verified"] is not True:
        raise RuntimeError("decision receipt verified flag is not true")
    for flag in _SAFETY_FLAGS:
        if receipt[flag] is not False:
            raise RuntimeError(f"decision receipt safety flag {flag} is not false")
    if (
        receipt["arm_order"] != list(ARM_ORDER)
        or type(receipt["arm_decisions"]) is not dict
        or set(receipt["arm_decisions"]) != set(ARM_ORDER)
        or any(receipt["arm_decisions"].get(arm) != "RED" for arm in ARM_ORDER)
    ):
        raise RuntimeError("all arm decisions must be exactly RED")
    for field in (
        "evaluation_artifact_sha256",
        "evaluation_manifest_file_sha256",
    ):
        if not _is_sha256(receipt[field]):
            raise RuntimeError(f"decision receipt {field} is invalid")
    return receipt


def compute_overlay_claim_key(evaluation_artifact_sha256: str) -> str:
    if not _is_sha256(evaluation_artifact_sha256):
        raise RuntimeError("evaluation artifact SHA-256 is invalid")
    return hashlib.sha256(
        (EXPECTED_PREREGISTRATION_RAW_SHA256 + evaluation_artifact_sha256).encode("ascii")
    ).hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise RuntimeError(f"{label} must be finite")
    return float(value)


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value:
        raise RuntimeError(f"{field} is missing or invalid")
    return value


def _iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"{field} is invalid") from exc


def build_low_rvol_selection(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    trade_keys: set[str] = set()
    for source in rows:
        row = dict(source)
        signal_date = _required_string(row, "signal_date")
        exit_date = _required_string(row, "exit_date")
        signal_industry = _required_string(row, "signal_industry")
        security_id = _required_string(row, "stable_security_id")
        trade_key = _required_string(row, "trade_key")
        signal_date_value = _iso_date(signal_date, "signal_date")
        if _iso_date(exit_date, "exit_date") < signal_date_value:
            raise RuntimeError("exit_date precedes signal_date")
        if trade_key in trade_keys:
            raise RuntimeError("duplicate trade_key is forbidden")
        trade_keys.add(trade_key)

        probability = _finite_number(
            row.get("predicted_positive_utility_probability"),
            "predicted_positive_utility_probability",
        )
        if probability <= 0.5:
            continue
        if "realized_volatility_20_pct_rank" not in row:
            raise RuntimeError("realized_volatility factor is missing")
        volatility = _finite_number(
            row["realized_volatility_20_pct_rank"],
            "realized_volatility_20_pct_rank",
        )
        if not -1.0 < volatility < 1.0:
            raise RuntimeError("realized_volatility factor is outside its open domain")
        amount = _finite_number(row.get("candidate_amount"), "candidate_amount")
        row["_probability"] = probability
        row["_amount"] = amount
        row["_signal_date_value"] = signal_date_value
        row["_exit_date_value"] = _iso_date(exit_date, "exit_date")
        row["_signal_industry"] = signal_industry
        row["_security_id"] = security_id
        grouped.setdefault(signal_date, []).append(row)

    ranked_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for signal_date in sorted(grouped):
        candidates = grouped[signal_date]
        count = len(candidates)
        by_probability = sorted(
            candidates,
            key=lambda item: (
                item["_probability"],
                item["stable_security_id"].encode("utf-8"),
            ),
        )
        offset = 0
        while offset < count:
            end = offset + 1
            while (
                end < count
                and by_probability[end]["_probability"] == by_probability[offset]["_probability"]
            ):
                end += 1
            average_rank = ((offset + 1) + end) / 2.0
            probability_rank = average_rank / (count + 1)
            for index in range(offset, end):
                by_probability[index]["probability_rank"] = probability_rank
            offset = end

        for candidate in candidates:
            low_volatility = 1.0 - candidate["realized_volatility_20_pct_rank"]
            candidate["low_volatility_term"] = low_volatility
            candidate["rank_score"] = 0.80 * candidate["probability_rank"] + 0.20 * low_volatility
            candidate.pop("_probability")
            candidate.pop("_amount")
        ranked = sorted(
            candidates,
            key=lambda item: (
                -item["rank_score"],
                -float(item["candidate_amount"]),
                item["stable_security_id"].encode("utf-8"),
            ),
        )
        ranked_rows.extend(ranked)
        signal_date_value = _iso_date(signal_date, "signal_date")
        active = [
            position for position in active if position["_exit_date_value"] > signal_date_value
        ]
        active_security_ids = {position["_security_id"] for position in active}
        active_industries = {position["_signal_industry"] for position in active}
        available_slots = _SELECTION_CONTRACT["max_active_positions"] - len(active)
        selected_today = 0
        for candidate in ranked:
            if available_slots <= 0 or selected_today >= _SELECTION_CONTRACT["top_n"]:
                break
            if (
                candidate["_security_id"] in active_security_ids
                or candidate["_signal_industry"] in active_industries
            ):
                continue
            selected_rows.append(candidate)
            active.append(candidate)
            active_security_ids.add(candidate["_security_id"])
            active_industries.add(candidate["_signal_industry"])
            available_slots -= 1
            selected_today += 1

    for row in ranked_rows:
        for field in (
            "_signal_date_value",
            "_exit_date_value",
            "_signal_industry",
            "_security_id",
        ):
            row.pop(field)
    selected_trade_keys = [row["trade_key"] for row in selected_rows]
    unsigned_selection_receipt = {
        "schema_version": ("factor-v2-low-rvol-overlay-selection-receipt/v1"),
        "selection_contract_sha256": (EXPECTED_SELECTION_CONTRACT_SHA256),
        "selected_trade_keys": selected_trade_keys,
        "selected_trade_keys_sha256": canonical_sha256(selected_trade_keys),
    }
    return {
        "ranked_rows": ranked_rows,
        "selected_rows": selected_rows,
        "selection_contract": dict(_SELECTION_CONTRACT),
        "selection_receipt": {
            **unsigned_selection_receipt,
            "receipt_sha256": canonical_sha256(unsigned_selection_receipt),
        },
    }


def _ordered_unique_trade_keys(
    trade_keys: Sequence[str],
    label: str,
) -> list[str]:
    values = list(trade_keys)
    if any(type(value) is not str for value in values):
        raise RuntimeError(f"{label} trade_key is invalid")
    if len(set(values)) != len(values):
        raise RuntimeError(f"{label} trade_key values must be unique")
    return sorted(values, key=lambda value: value.encode("utf-8"))


def adjudicate_trade_key_increment(
    *,
    control_trade_keys: Sequence[str],
    overlay_trade_keys: Sequence[str],
    read_performance_metrics: Callable[[], Any],
    run_execution_stress: Callable[[Any], Mapping[str, Any]],
) -> dict[str, Any]:
    ordered_control = _ordered_unique_trade_keys(
        control_trade_keys,
        "control",
    )
    ordered_overlay = _ordered_unique_trade_keys(
        overlay_trade_keys,
        "overlay",
    )
    control_sha256 = canonical_sha256(ordered_control)
    overlay_sha256 = canonical_sha256(ordered_overlay)
    comparison = {
        "ordered_control_trade_keys": ordered_control,
        "ordered_overlay_trade_keys": ordered_overlay,
        "control_trade_keys_sha256": control_sha256,
        "overlay_trade_keys_sha256": overlay_sha256,
    }
    if (
        len(ordered_control) == len(ordered_overlay)
        and ordered_control == ordered_overlay
        and control_sha256 == overlay_sha256
    ):
        return {
            **comparison,
            "decision": "RED_NO_INCREMENT_WITHOUT_STRESS",
        }

    metrics = read_performance_metrics()
    stress = run_execution_stress(metrics)
    if not isinstance(stress, Mapping):
        raise RuntimeError("execution stress result must be a mapping")
    stress_result = dict(stress)
    reserved_overlap = set(comparison) & set(stress_result)
    if reserved_overlap:
        raise RuntimeError("execution stress overlaps reserved trade-key evidence")
    return {**comparison, **stress_result}


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("exclusive write made no forward progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_strict_file(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"{label} cannot be read") from exc
    value = _parse_strict_json(raw, label)
    if raw != _canonical_bytes(value):
        raise RuntimeError(f"{label} is not canonical JSON")
    return value, raw


def _validate_phase_two_result(
    value: Mapping[str, Any],
    *,
    claim_key: str,
    decision_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError("overlay result must be a mapping")
    result = dict(value)
    if set(result) != _PHASE_TWO_RESULT_FIELDS:
        raise RuntimeError("overlay result field schema has extra or missing fields")
    unsigned = dict(result)
    result_sha256 = unsigned.pop("result_sha256")
    if not _is_sha256(result_sha256) or canonical_sha256(unsigned) != result_sha256:
        raise RuntimeError("overlay result self hash drifted")
    if result.get("schema_version") != OVERLAY_RESULT_SCHEMA_VERSION:
        raise RuntimeError("overlay result schema_version drifted")
    if result.get("overlay_id") != OVERLAY_ID:
        raise RuntimeError("overlay result overlay_id drifted")
    decision = result.get("decision")
    if decision not in _ALLOWED_PHASE_TWO_DECISIONS:
        raise RuntimeError("overlay result decision is invalid")
    if result.get("claim_key") != claim_key:
        raise RuntimeError("overlay result claim binding drifted")
    if result.get("evaluation_artifact_sha256") != decision_receipt["evaluation_artifact_sha256"]:
        raise RuntimeError("overlay result evaluation artifact binding drifted")
    if result.get("preregistration_raw_sha256") != EXPECTED_PREREGISTRATION_RAW_SHA256:
        raise RuntimeError("overlay result preregistration binding drifted")
    if result.get("selection_contract_sha256") != EXPECTED_SELECTION_CONTRACT_SHA256:
        raise RuntimeError("overlay result selection contract binding drifted")
    if result.get("source_decision_receipt_sha256") != decision_receipt["receipt_sha256"]:
        raise RuntimeError("overlay result source decision receipt binding drifted")
    if result.get("evidence_complete") is not True:
        raise RuntimeError("overlay result evidence is incomplete")
    hash_fields = {
        "source_replay_receipt_sha256": "source replay receipt SHA-256",
        "selection_receipt_sha256": "selection receipt SHA-256",
        "control_trade_keys_sha256": "control trade key SHA-256",
        "overlay_trade_keys_sha256": "overlay trade key SHA-256",
    }
    for field, label in hash_fields.items():
        if not _is_sha256(result.get(field)):
            raise RuntimeError(f"overlay result {label} is invalid")

    if decision == "RED_NO_INCREMENT_WITHOUT_STRESS":
        if (
            result["control_trade_keys_sha256"] != result["overlay_trade_keys_sha256"]
            or result.get("overlay_evaluation_receipt_sha256") is not None
            or result.get("execution_stress_receipt_sha256") is not None
        ):
            raise RuntimeError("no-increment result cannot claim evaluation or stress")
    else:
        if result["control_trade_keys_sha256"] == result["overlay_trade_keys_sha256"]:
            raise RuntimeError("equal trade roots require the no-increment decision")
        if not _is_sha256(result.get("overlay_evaluation_receipt_sha256")):
            raise RuntimeError("overlay evaluation receipt SHA-256 is invalid")
        if not _is_sha256(result.get("execution_stress_receipt_sha256")):
            raise RuntimeError("execution stress receipt SHA-256 is invalid")
    for flag in _SAFETY_FLAGS + _FORBIDDEN_OPERATION_FLAGS:
        if result.get(flag) is not False:
            raise RuntimeError(f"overlay result flag {flag} must be false")
    _canonical_bytes(result)
    return result


def _reuse_result(
    claim_directory: Path,
    claim_key: str,
    decision_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    pointer_path = claim_directory / f"{claim_key}.result-pointer.json"
    deadline = time.monotonic() + _SAME_KEY_REUSE_WAIT_SECONDS
    stabilize_deadline: float | None = None
    while True:
        if pointer_path.is_file():
            if stabilize_deadline is None:
                stabilize_deadline = time.monotonic() + _RESULT_POINTER_STABILIZE_SECONDS
            try:
                pointer, _ = _read_strict_file(
                    pointer_path,
                    "overlay result pointer",
                )
                break
            except RuntimeError as exc:
                if time.monotonic() >= stabilize_deadline:
                    raise RuntimeError("same-key overlay result pointer did not stabilize") from exc
        if time.monotonic() >= deadline:
            raise RuntimeError("same-key overlay trial is still in progress")
        time.sleep(_REUSE_POLL_SECONDS)
    if set(pointer) != {"claim_key", "result_sha256", "result_file"}:
        raise RuntimeError("overlay result pointer fields drifted")
    result_sha256 = pointer["result_sha256"]
    if (
        pointer["claim_key"] != claim_key
        or not _is_sha256(result_sha256)
        or pointer["result_file"] != f"{result_sha256}.json"
    ):
        raise RuntimeError("overlay result pointer identity drifted")
    result, raw = _read_strict_file(
        claim_directory / pointer["result_file"],
        "overlay content-addressed result",
    )
    if hashlib.sha256(raw).hexdigest() != result_sha256:
        raise RuntimeError("overlay content-addressed result hash drifted")
    return {
        "claim_key": claim_key,
        "reused": True,
        "result": _validate_phase_two_result(
            result,
            claim_key=claim_key,
            decision_receipt=decision_receipt,
        ),
        "result_sha256": result_sha256,
    }


def activate_low_rvol_overlay(
    receipt_descriptor: Mapping[str, Any],
    *,
    claim_directory: Path,
    phase_two_runner: Callable[
        [dict[str, Any], dict[str, Any]],
        Mapping[str, Any],
    ],
) -> dict[str, Any]:
    receipt = verify_phase_one_decision_receipt(receipt_descriptor)
    claim_key = compute_overlay_claim_key(receipt["evaluation_artifact_sha256"])
    claim_directory = Path(claim_directory)
    claim_directory.mkdir(parents=True, exist_ok=True)
    claim_path = claim_directory / f"{OVERLAY_ID}.claim.json"
    claim_payload = {
        "claim_key": claim_key,
        "evaluation_artifact_sha256": receipt["evaluation_artifact_sha256"],
        "preregistration_raw_sha256": (EXPECTED_PREREGISTRATION_RAW_SHA256),
    }
    claim_bytes = _canonical_bytes(claim_payload)
    try:
        _write_exclusive(claim_path, claim_bytes)
    except FileExistsError:
        existing, existing_raw = _read_strict_file(
            claim_path,
            "overlay claim",
        )
        if existing.get("claim_key") != claim_key:
            raise RuntimeError("a second distinct overlay claim is forbidden")
        if existing != claim_payload or existing_raw != claim_bytes:
            raise RuntimeError("same-key overlay claim identity drifted")
        return _reuse_result(
            claim_directory,
            claim_key,
            receipt,
        )

    claim = {
        **claim_payload,
        "path": str(claim_path),
    }
    result = _validate_phase_two_result(
        phase_two_runner(receipt, claim),
        claim_key=claim_key,
        decision_receipt=receipt,
    )
    result_bytes = _canonical_bytes(result)
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    result_path = claim_directory / f"{result_sha256}.json"
    try:
        _write_exclusive(result_path, result_bytes)
    except FileExistsError:
        if result_path.read_bytes() != result_bytes:
            raise RuntimeError("content-addressed result collision")

    pointer = {
        "claim_key": claim_key,
        "result_sha256": result_sha256,
        "result_file": result_path.name,
    }
    _write_exclusive(
        claim_directory / f"{claim_key}.result-pointer.json",
        _canonical_bytes(pointer),
    )
    return {
        "claim_key": claim_key,
        "reused": False,
        "result": result,
        "result_sha256": result_sha256,
    }
