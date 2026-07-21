"""Versioned control request and treatment-plan builder with frozen ledger quarantine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from app import jobs
from app import research_control_successor_v3 as v3
from app import research_precompute_control as precompute_control
from app.research_control_quarantine import (
    quarantine_binding_sha256_v1,
    validate_frozen_quarantine_binding_v1,
)


class ControlSuccessorV4Error(RuntimeError):
    pass


_REQUEST_SCHEMA = "research-control-successor-request/v4"
_REQUEST_FIELDS = {
    "schema_version",
    "experiment_id",
    "workspace_root",
    "run_root",
    "output_basename",
    "development_payload_fixture",
    "settings_fingerprint",
    "producer_source_sha256",
    "control_source_bundle_sha256",
    "legacy_quarantine",
    "ledger",
    "registration",
    "request_sha256",
}


def _canonical_bytes(payload: Any) -> bytes:
    return v3._canonical_bytes(payload)


def _sha256(raw: bytes) -> str:
    return v3._sha256(raw)


def _is_sha256(value: object) -> bool:
    return v3._is_sha256(value)


def _strict_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        return v3._strict_object(raw, label)
    except v3.ControlSuccessorV3Error as exc:
        raise ControlSuccessorV4Error(str(exc)) from exc


def _safe_existing_file(path: Path, *, workspace: Path, label: str) -> bytes:
    try:
        return v3._safe_existing_file(path, workspace=workspace, label=label)
    except v3.ControlSuccessorV3Error as exc:
        raise ControlSuccessorV4Error(str(exc)) from exc


def validate_control_request_v4(
    payload: Mapping[str, Any], raw: bytes | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ControlSuccessorV4Error("control request must be an object")
    request = dict(payload)
    if set(request) != _REQUEST_FIELDS:
        raise ControlSuccessorV4Error("control request fields are invalid")
    if request.get("schema_version") != _REQUEST_SCHEMA:
        raise ControlSuccessorV4Error("control request schema is invalid")
    if raw is not None and raw != _canonical_bytes(request) + b"\n":
        raise ControlSuccessorV4Error("control request serialization is invalid")
    unsigned = dict(request)
    claimed = unsigned.pop("request_sha256", None)
    if not _is_sha256(claimed) or claimed != _sha256(_canonical_bytes(unsigned)):
        raise ControlSuccessorV4Error("control request hash mismatch")

    workspace = Path(str(request.get("workspace_root") or ""))
    actual_workspace = Path(__file__).resolve().parent.parent
    if (
        not workspace.is_absolute()
        or workspace.drive.casefold() != "e:"
        or workspace.resolve(strict=True) != workspace
        or workspace != actual_workspace
    ):
        raise ControlSuccessorV4Error("control request workspace is invalid")
    experiment_id = request.get("experiment_id")
    if (
        not isinstance(experiment_id, str)
        or not experiment_id
        or len(experiment_id) > 128
        or Path(experiment_id).name != experiment_id
        or any(
            not (character.isascii() and (character.isalnum() or character in "-_"))
            for character in experiment_id
        )
        or experiment_id[-1] in {".", " "}
    ):
        raise ControlSuccessorV4Error("control request experiment id is invalid")
    run_root = Path(str(request.get("run_root") or ""))
    expected_parent = workspace / "tmp" / "research-precompute-runs-v3"
    if (
        not run_root.is_absolute()
        or run_root.resolve(strict=False) != run_root
        or run_root.parent != expected_parent
        or run_root.name != experiment_id
    ):
        raise ControlSuccessorV4Error("control request run namespace is invalid")
    if request.get("output_basename") != "qualified-current-schema.json":
        raise ControlSuccessorV4Error("control request output basename is invalid")
    try:
        fixture = jobs._validate_development_payload_fixture_binding(
            request.get("development_payload_fixture")
        )
        settings = jobs._validate_research_generation_settings_fingerprint(
            request.get("settings_fingerprint")
        )
        quarantine = validate_frozen_quarantine_binding_v1(
            request.get("legacy_quarantine")
        )
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV4Error(
            "control request fixture, settings, or quarantine is invalid"
        ) from exc
    if (
        fixture["workspace_root"] != str(workspace)
        or fixture["date_bounds"]
        != {"start_date": "2022-01-04", "end_date": "2023-12-29"}
        or fixture["temporal_role"] != "development"
    ):
        raise ControlSuccessorV4Error("control request fixture boundary is invalid")
    if settings.get("values") != v3._SETTINGS_VALUES:
        raise ControlSuccessorV4Error("control request settings policy is invalid")
    if request.get("producer_source_sha256") != jobs.historical_treatment_producer_source_sha256():
        raise ControlSuccessorV4Error("control request producer source drifted")
    if request.get("control_source_bundle_sha256") != (
        precompute_control.precompute_control_source_bundle_v3(workspace)["root_sha256"]
    ):
        raise ControlSuccessorV4Error("control request source bundle drifted")
    if quarantine != request.get("legacy_quarantine"):
        raise ControlSuccessorV4Error("control request quarantine changed")

    ledger = request.get("ledger")
    if not isinstance(ledger, dict) or set(ledger) != v3._LEDGER_FIELDS:
        raise ControlSuccessorV4Error("control request ledger fields are invalid")
    expected_ledger = workspace / "data" / "research_experiments" / "ledger.jsonl"
    if (
        ledger.get("path") != str(expected_ledger)
        or ledger.get("expected_tip_sequence") != 126
        or not _is_sha256(ledger.get("expected_tip_record_hash"))
        or not _is_sha256(ledger.get("expected_file_sha256"))
        or not _is_sha256(ledger.get("expected_lock_file_sha256"))
    ):
        raise ControlSuccessorV4Error("control request ledger binding is invalid")
    registration = request.get("registration")
    if not isinstance(registration, dict) or set(registration) != v3._REGISTRATION_FIELDS:
        raise ControlSuccessorV4Error("control request registration fields are invalid")
    if any(
        not isinstance(registration.get(key), str)
        or not registration[key].strip()
        or len(registration[key]) > 2048
        for key in v3._REGISTRATION_FIELDS
    ):
        raise ControlSuccessorV4Error("control request registration text is invalid")
    return request


def load_control_request_v4(
    path: str | Path, *, expected_file_sha256: str
) -> dict[str, Any]:
    if not _is_sha256(expected_file_sha256):
        raise ControlSuccessorV4Error("expected request file hash is invalid")
    workspace = Path(__file__).resolve().parent.parent
    raw = _safe_existing_file(
        Path(path), workspace=workspace, label="control request"
    )
    if _sha256(raw) != expected_file_sha256:
        raise ControlSuccessorV4Error("control request file hash mismatch")
    return validate_control_request_v4(_strict_object(raw, "control request"), raw)


def build_treatment_plan_v6(request: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_control_request_v4(request)
    workspace = Path(validated["workspace_root"])
    run_root = Path(validated["run_root"])
    control_root = run_root / "control"
    fixture = dict(validated["development_payload_fixture"])
    quarantine = validate_frozen_quarantine_binding_v1(validated["legacy_quarantine"])
    parameters = dict(v3._PARAMETERS)
    baseline = dict(parameters)
    baseline["hold_days"] = 5
    plan = {
        "schema_version": "research-treatment-input-plan/v6",
        "experiment_id": validated["experiment_id"],
        "producer": "research-historical-universe",
        "producer_source_sha256": validated["producer_source_sha256"],
        "output_basename": validated["output_basename"],
        "temporal_role": "development",
        "start_date": "2022-01-04",
        "end_date": "2023-12-29",
        "authority": {
            "kind": "ordered_composite",
            "artifact_root_sha256": None,
            "coverage_audit_sha256": None,
            "composite_root_sha256": fixture["composite_root_sha256"],
            "temporal_contract_sha256": fixture["temporal_contract_sha256"],
        },
        "development_payload_fixture": fixture,
        "data_cutoff": "2026-07-10",
        "acceptance_gates": dict(v3._ACCEPTANCE_GATES),
        "validation_protocol": json.loads(json.dumps(v3._VALIDATION_PROTOCOL)),
        "legacy_quarantine": quarantine,
        "settings_fingerprint": dict(validated["settings_fingerprint"]),
        "treatment": {"parameter": "hold_days", "baseline": 5, "candidate": 3},
        "baseline_parameters": baseline,
        "parameters": parameters,
        "precompute_execution": {
            "schema_version": "research-precompute-execution-plan/v3",
            "control_required": True,
            "launcher_ready_schema": "research-launcher-ready/v5",
            "minimum_registration_sequence_exclusive": 126,
            "workspace_root": str(workspace),
            "ledger_path": validated["ledger"]["path"],
            "run_root": str(run_root),
            "qualified_trades_output_path": str(
                run_root / validated["output_basename"]
            ),
            "cache_dir": str(run_root / "cache"),
            "claim_parent": str(control_root / "claims"),
            "sandbox_root": str(control_root / "sandbox"),
            "audit_dir": str(control_root / "sandbox" / "audit"),
            "run_result_receipt_path": str(
                control_root / "precompute-run-result.json"
            ),
            "temporal_contract_path": str(
                workspace / "data" / "research_partitions" / "frozen-v1.json"
            ),
            "progress_every": 25,
            "parent_proof_path": str(
                control_root / "parent-publication-proof.json"
            ),
            "legacy_quarantine_sha256": quarantine_binding_sha256_v1(quarantine),
            "network_calls_allowed": 0,
            "single_writer": True,
        },
    }
    plan["plan_sha256"] = _sha256(_canonical_bytes(plan))
    raw = _canonical_bytes(plan) + b"\n"
    try:
        verified, _artifact = jobs._validate_treatment_input_plan_payload(plan, raw)
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV4Error("generated v6 treatment plan is invalid") from exc
    if verified != plan:
        raise ControlSuccessorV4Error("generated v6 treatment plan changed")
    return plan


def build_treatment_plan_v7(request: Mapping[str, Any]) -> dict[str, Any]:
    """Build the successor plan that keeps its request-time control anchors."""

    validated = validate_control_request_v4(request)
    plan = json.loads(json.dumps(build_treatment_plan_v6(validated)))
    binding = {
        "schema_version": "research-control-request-binding/v1",
        "request_sha256": validated["request_sha256"],
        "control_source_bundle_sha256": validated["control_source_bundle_sha256"],
        "ledger": dict(validated["ledger"]),
    }
    plan["schema_version"] = "research-treatment-input-plan/v7"
    plan["control_request_binding"] = binding
    plan["precompute_execution"]["schema_version"] = (
        "research-precompute-execution-plan/v4"
    )
    plan["precompute_execution"]["control_request_binding_sha256"] = _sha256(
        _canonical_bytes(binding)
    )
    plan.pop("plan_sha256")
    plan["plan_sha256"] = _sha256(_canonical_bytes(plan))
    raw = _canonical_bytes(plan) + b"\n"
    try:
        verified, _artifact = jobs._validate_treatment_input_plan_payload(plan, raw)
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV4Error("generated v7 treatment plan is invalid") from exc
    if verified != plan:
        raise ControlSuccessorV4Error("generated v7 treatment plan changed")
    return plan
