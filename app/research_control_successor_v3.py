"""Single-process control entry for one treatment-plan v5 successor."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import stat
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Mapping, Sequence

from app import jobs
from app import research_precompute_control as precompute_control
from app import research_precompute_parent as precompute_parent
from app import research_validation
from app.durable_io import fsync_directory
from app.research_plan_publication_v2 import publish_treatment_plan_v2
from app.research_partitions import load_temporal_partition_contract


class ControlSuccessorV3Error(RuntimeError):
    pass


_REQUEST_SCHEMA = "research-control-successor-request/v3"
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
    "ledger",
    "registration",
    "request_sha256",
}
_LEDGER_FIELDS = {
    "path",
    "expected_file_sha256",
    "expected_lock_file_sha256",
    "expected_tip_sequence",
    "expected_tip_record_hash",
}
_REGISTRATION_FIELDS = {
    "hypothesis",
    "expected_mechanism",
    "falsification_criterion",
    "exit_criterion",
}
_ACCEPTANCE_GATES = {
    "net_annualized_return_pct_min": 50.0,
    "win_rate_pct_min": 52.0,
    "win_rate_pct_max": 60.0,
    "max_drawdown_pct_max": 15.0,
    "profit_factor_min": 1.3,
    "calmar_min": 1.5,
}
_VALIDATION_PROTOCOL = {
    "schema_version": "research-purged-validation-protocol/v1",
    "train_days": 365,
    "validation_days": 90,
    "step_days": 90,
    "embargo_days": 5,
    "minimum_oos_trades": 200,
    "exposure_multiplier": 1.0,
    "pre_exit_calendar_gap_days": 0,
    "partial_profit_activation_pct": None,
    "partial_profit_fraction": 0.0,
    "correlation_threshold": None,
    "correlation_lookback_days": 60,
    "artifact_dir_relative": "validation-artifacts",
    "pre_purged_development_gate": {
        "schema_version": "research-development-pre-purged-gate/v1",
        "fixed_spec": True,
        "minimum_trades": 200,
    },
}
_PARAMETERS = {
    "max_deep": 80,
    "top_n": 3,
    "hold_days": 3,
    "lookback_days": 620,
    "max_universe_symbols": 0,
    "live_snapshot": False,
    "buy_only": False,
    "min_score": None,
    "stop_loss_pct": None,
    "take_profit_pct": None,
    "trailing_stop_pct": None,
    "symbol_cooldown_days": 5,
    "max_active_positions": 3,
    "announcement_context": False,
    "announcement_lookback_days": None,
    "require_announcement_event": None,
    "require_all_announcement_events": False,
    "exclude_announcement_event": None,
    "require_market_level": None,
    "require_signal_tag": None,
    "require_all_signal_tags": False,
    "exclude_signal_tag": None,
    "min_prior_win_rate": None,
    "min_prior_avg_return": None,
    "max_prior_avg_adverse": None,
    "margin_eligibility_context": False,
}
_SETTINGS_VALUES = {
    "scan_min_amount": 30_000_000.0,
    "scan_min_price": 3.0,
    "scan_max_price": 300.0,
    "max_entry_gap_up_pct": 6.0,
    "max_entry_gap_down_pct": 7.0,
    "locked_limit_gap_pct": 9.3,
    "max_entry_intraday_range_pct": 8.0,
    "min_backtest_trades": 1.0,
    "min_backtest_win_rate": 35.0,
    "min_backtest_avg_return": 0.0,
    "max_backtest_avg_adverse": 5.0,
}
_TERMINAL_EVENTS = {"decision", "failed", "aborted"}
_REGISTRATION_SECRET_LOCK = threading.Lock()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_object(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ControlSuccessorV3Error(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ControlSuccessorV3Error(
                    f"{label} contains a non-finite value: {value}"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlSuccessorV3Error(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ControlSuccessorV3Error(f"{label} must be an object")
    return payload


def _safe_existing_file(path: Path, *, workspace: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ControlSuccessorV3Error(f"{label} is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise ControlSuccessorV3Error(f"{label} is unsafe")
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ControlSuccessorV3Error(f"{label} is outside workspace") from exc
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode) or opened_before.st_nlink != 1:
            raise ControlSuccessorV3Error(f"{label} is unsafe")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        named_after = path.stat()
        def identity(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
            )
        if identity(opened_before) != identity(opened_after) or (
            opened_after.st_dev,
            opened_after.st_ino,
        ) != (named_after.st_dev, named_after.st_ino) or (
            opened_after.st_nlink != 1 or named_after.st_nlink != 1
        ):
            raise ControlSuccessorV3Error(f"{label} changed while read")
        raw = b"".join(chunks)
        if len(raw) != opened_after.st_size:
            raise ControlSuccessorV3Error(f"{label} changed while read")
        return raw
    finally:
        os.close(descriptor)


def validate_control_request_v3(
    payload: Mapping[str, Any], raw: bytes | None = None
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ControlSuccessorV3Error("control request must be an object")
    request = dict(payload)
    if set(request) != _REQUEST_FIELDS:
        raise ControlSuccessorV3Error("control request fields are invalid")
    if request.get("schema_version") != _REQUEST_SCHEMA:
        raise ControlSuccessorV3Error("control request schema is invalid")
    if raw is not None and raw != _canonical_bytes(request) + b"\n":
        raise ControlSuccessorV3Error("control request serialization is invalid")
    unsigned = dict(request)
    claimed = unsigned.pop("request_sha256", None)
    if not _is_sha256(claimed) or claimed != _sha256(_canonical_bytes(unsigned)):
        raise ControlSuccessorV3Error("control request hash mismatch")

    workspace = Path(str(request.get("workspace_root") or ""))
    actual_workspace = Path(__file__).resolve().parent.parent
    if (
        not workspace.is_absolute()
        or workspace.drive.casefold() != "e:"
        or workspace.resolve(strict=True) != workspace
        or workspace != actual_workspace
    ):
        raise ControlSuccessorV3Error("control request workspace is invalid")
    experiment_id = request.get("experiment_id")
    if (
        not isinstance(experiment_id, str)
        or not experiment_id
        or len(experiment_id) > 128
        or Path(experiment_id).name != experiment_id
        or any(
            not (
                character.isascii()
                and (character.isalnum() or character in "-_")
            )
            for character in experiment_id
        )
        or experiment_id[-1] in {".", " "}
    ):
        raise ControlSuccessorV3Error("control request experiment id is invalid")
    run_root = Path(str(request.get("run_root") or ""))
    expected_parent = workspace / "tmp" / "research-precompute-runs-v2"
    if (
        not run_root.is_absolute()
        or run_root.resolve(strict=False) != run_root
        or run_root.parent != expected_parent
        or run_root.name != experiment_id
    ):
        raise ControlSuccessorV3Error("control request run namespace is invalid")
    output_basename = request.get("output_basename")
    if (
        not isinstance(output_basename, str)
        or output_basename != "qualified-current-schema.json"
    ):
        raise ControlSuccessorV3Error("control request output basename is invalid")

    try:
        fixture = jobs._validate_development_payload_fixture_binding(
            request.get("development_payload_fixture")
        )
        settings = jobs._validate_research_generation_settings_fingerprint(
            request.get("settings_fingerprint")
        )
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV3Error("control request fixture or settings is invalid") from exc
    if (
        fixture["workspace_root"] != str(workspace)
        or fixture["date_bounds"]
        != {"start_date": "2022-01-04", "end_date": "2023-12-29"}
        or fixture["temporal_role"] != "development"
    ):
        raise ControlSuccessorV3Error("control request fixture boundary is invalid")
    if settings.get("values") != _SETTINGS_VALUES:
        raise ControlSuccessorV3Error("control request settings policy is invalid")
    if (
        request.get("producer_source_sha256")
        != jobs.historical_treatment_producer_source_sha256()
    ):
        raise ControlSuccessorV3Error("control request producer source drifted")
    if (
        request.get("control_source_bundle_sha256")
        != precompute_control.precompute_control_source_bundle_v2(workspace)[
            "root_sha256"
        ]
    ):
        raise ControlSuccessorV3Error("control request source bundle drifted")

    ledger = request.get("ledger")
    if not isinstance(ledger, dict) or set(ledger) != _LEDGER_FIELDS:
        raise ControlSuccessorV3Error("control request ledger fields are invalid")
    expected_ledger = workspace / "data" / "research_experiments" / "ledger.jsonl"
    if (
        ledger.get("path") != str(expected_ledger)
        or ledger.get("expected_tip_sequence") != 126
        or not _is_sha256(ledger.get("expected_tip_record_hash"))
        or not _is_sha256(ledger.get("expected_file_sha256"))
        or not _is_sha256(ledger.get("expected_lock_file_sha256"))
    ):
        raise ControlSuccessorV3Error("control request ledger binding is invalid")
    registration = request.get("registration")
    if not isinstance(registration, dict) or set(registration) != _REGISTRATION_FIELDS:
        raise ControlSuccessorV3Error("control request registration fields are invalid")
    if any(
        not isinstance(registration.get(key), str)
        or not registration[key].strip()
        or len(registration[key]) > 2048
        for key in _REGISTRATION_FIELDS
    ):
        raise ControlSuccessorV3Error("control request registration text is invalid")
    return request


def load_control_request_v3(
    path: str | Path, *, expected_file_sha256: str
) -> dict[str, Any]:
    if not _is_sha256(expected_file_sha256):
        raise ControlSuccessorV3Error("expected request file hash is invalid")
    workspace = Path(__file__).resolve().parent.parent
    request_path = Path(path)
    raw = _safe_existing_file(
        request_path, workspace=workspace, label="control request"
    )
    if _sha256(raw) != expected_file_sha256:
        raise ControlSuccessorV3Error("control request file hash mismatch")
    return validate_control_request_v3(_strict_object(raw, "control request"), raw)


def build_treatment_plan_v5(request: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_control_request_v3(request)
    workspace = Path(validated["workspace_root"])
    run_root = Path(validated["run_root"])
    control_root = run_root / "control"
    fixture = dict(validated["development_payload_fixture"])
    parameters = dict(_PARAMETERS)
    baseline = dict(parameters)
    baseline["hold_days"] = 5
    plan = {
        "schema_version": "research-treatment-input-plan/v5",
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
        "acceptance_gates": dict(_ACCEPTANCE_GATES),
        "validation_protocol": json.loads(json.dumps(_VALIDATION_PROTOCOL)),
        "settings_fingerprint": dict(validated["settings_fingerprint"]),
        "treatment": {"parameter": "hold_days", "baseline": 5, "candidate": 3},
        "baseline_parameters": baseline,
        "parameters": parameters,
        "precompute_execution": {
            "schema_version": "research-precompute-execution-plan/v2",
            "control_required": True,
            "launcher_ready_schema": "research-launcher-ready/v4",
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
            "network_calls_allowed": 0,
            "single_writer": True,
        },
    }
    plan["plan_sha256"] = _sha256(_canonical_bytes(plan))
    raw = _canonical_bytes(plan) + b"\n"
    try:
        verified, _artifact = jobs._validate_treatment_input_plan_payload(plan, raw)
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV3Error("generated v5 treatment plan is invalid") from exc
    if verified != plan:
        raise ControlSuccessorV3Error("generated v5 treatment plan changed")
    return plan


def _verify_execute_preflight_v3(request: dict, plan: dict) -> None:
    workspace = Path(request["workspace_root"])
    if Path.cwd().resolve() != workspace:
        raise ControlSuccessorV3Error("control successor cwd is invalid")
    precompute_parent._validate_parent_environment(workspace)
    executable = Path(sys.executable).resolve(strict=True)
    try:
        executable.relative_to(workspace / ".venv")
    except ValueError as exc:
        raise ControlSuccessorV3Error("control successor Python is outside E venv") from exc
    fixture = plan["development_payload_fixture"]
    try:
        jobs._verify_development_payload_fixture_binding(
            fixture,
            composite_pit_descriptor_path=fixture["source_descriptor_path"],
        )
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV3Error("control successor fixture verification failed") from exc
    contract_path = Path(plan["precompute_execution"]["temporal_contract_path"])
    contract = load_temporal_partition_contract(str(contract_path))
    if contract.get("contract_sha256") != fixture["temporal_contract_sha256"]:
        raise ControlSuccessorV3Error("control successor temporal contract mismatch")

    ledger = request["ledger"]
    ledger_path = Path(ledger["path"])
    lock_path = precompute_parent._require_existing_ledger_lock(
        ledger_path, workspace=workspace
    )
    before_ledger = _safe_existing_file(
        ledger_path, workspace=workspace, label="control successor ledger"
    )
    before_lock = _safe_existing_file(
        lock_path, workspace=workspace, label="control successor ledger lock"
    )
    if (
        _sha256(before_ledger) != ledger["expected_file_sha256"]
        or _sha256(before_lock) != ledger["expected_lock_file_sha256"]
    ):
        raise ControlSuccessorV3Error("control successor ledger bytes drifted")
    rows = research_validation.read_experiment_ledger(str(ledger_path))
    after_ledger = _safe_existing_file(
        ledger_path, workspace=workspace, label="control successor ledger"
    )
    after_lock = _safe_existing_file(
        lock_path, workspace=workspace, label="control successor ledger lock"
    )
    if before_ledger != after_ledger or before_lock != after_lock:
        raise ControlSuccessorV3Error("control successor ledger changed in preflight")
    if (
        not rows
        or rows[-1].get("sequence") != ledger["expected_tip_sequence"]
        or rows[-1].get("record_hash") != ledger["expected_tip_record_hash"]
    ):
        raise ControlSuccessorV3Error("control successor ledger tip mismatch")
    existing = _detect_existing_execution_v3(request, plan, rows=rows)
    if existing is not None:
        if existing["state"] not in _TERMINAL_EVENTS:
            raise ControlSuccessorV3Error("control successor found a nonterminal experiment")
        raise ControlSuccessorV3Error("control successor experiment id was already used")
    latest = {}
    for row in rows:
        latest[row.get("experiment_id")] = row
    if any(row.get("event_type") not in _TERMINAL_EVENTS for row in latest.values()):
        raise ControlSuccessorV3Error("control successor found a nonterminal experiment")
    if request["experiment_id"] in latest:
        raise ControlSuccessorV3Error("control successor experiment id was already used")
    run_root = Path(request["run_root"])
    if run_root.exists():
        raise ControlSuccessorV3Error("control successor run root already exists")


def _publication_parent_v3(request: Mapping[str, Any]) -> Path:
    return Path(request["workspace_root"]) / "tmp" / "research-plan-publications-v2"


def _verify_safe_directory_v3(path: Path, *, workspace: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        path.relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise ControlSuccessorV3Error(f"{label} is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        resolved != path
        or path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise ControlSuccessorV3Error(f"{label} is unsafe")
    return path


def _publish_plan_v3(request: dict, plan: dict) -> dict[str, Any]:
    parent = _publication_parent_v3(request)
    workspace = Path(request["workspace_root"])
    if not parent.exists():
        parent.mkdir(parents=False)
        fsync_directory(parent.parent)
    _verify_safe_directory_v3(parent, workspace=workspace, label="publication parent")
    return publish_treatment_plan_v2(
        parent,
        plan=plan,
        workspace_root=request["workspace_root"],
        expected_experiment_id=request["experiment_id"],
    )


def _create_proof_only_skeleton_v3(request: dict, plan: dict) -> None:
    run_root = Path(request["run_root"])
    workspace = Path(request["workspace_root"])
    namespace = run_root.parent
    if not namespace.exists():
        namespace.mkdir(parents=False)
        fsync_directory(namespace.parent)
    run_root.mkdir()
    control_root = run_root / "control"
    control_root.mkdir()
    _verify_safe_directory_v3(namespace, workspace=workspace, label="run namespace")
    _verify_safe_directory_v3(run_root, workspace=workspace, label="run root")
    _verify_safe_directory_v3(control_root, workspace=workspace, label="control root")
    fsync_directory(control_root)
    fsync_directory(run_root)
    fsync_directory(namespace)
    if {path.name for path in run_root.iterdir()} != {"control"} or any(
        control_root.iterdir()
    ):
        raise ControlSuccessorV3Error("control successor skeleton is not proof-only")
    if Path(plan["precompute_execution"]["parent_proof_path"]).parent != control_root:
        raise ControlSuccessorV3Error("control successor proof path mismatch")


def _publish_probe_proof_v3(
    request: dict, plan: dict, publication: dict
) -> dict[str, Any]:
    execution = plan["precompute_execution"]
    return precompute_control.publish_precompute_parent_proof_v2(
        execution["parent_proof_path"],
        workspace_root=request["workspace_root"],
        publication_root=publication["publication_root"],
        audit_root=publication["audit_root"],
        expected_publication_id=publication["publication_id"],
        expected_plan_file_sha256=publication["plan_file_sha256"],
        expected_prepublish_evidence_file_sha256=publication[
            "prepublish_evidence_file_sha256"
        ],
        expected_result_file_sha256=publication["result_file_sha256"],
        expected_writer_claim_file_sha256=publication[
            "writer_claim_file_sha256"
        ],
        completion_authorization=publication["completion_authorization"],
        expected_fixture_id=plan["development_payload_fixture"]["fixture_id"],
        expected_fixture_manifest_file_sha256=plan["development_payload_fixture"][
            "manifest_file_sha256"
        ],
        expected_control_source_bundle_sha256=request[
            "control_source_bundle_sha256"
        ],
        minimum_registration_sequence_exclusive=126,
    )


def _safe_publication_result(publication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: publication[key]
        for key in (
            "publication_id",
            "publication_root",
            "audit_root",
            "plan_file_sha256",
            "prepublish_evidence_file_sha256",
            "result_file_sha256",
            "writer_claim_file_sha256",
            "completion_authorization_sha256",
        )
    }


def probe_control_successor_v3(request: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_control_request_v3(request)
    plan = build_treatment_plan_v5(validated)
    _verify_execute_preflight_v3(validated, plan)
    publication = _publish_plan_v3(validated, plan)
    _create_proof_only_skeleton_v3(validated, plan)
    proof = _publish_probe_proof_v3(validated, plan, publication)
    return {
        "schema_version": "research-control-successor-probe-result/v3",
        "request_sha256": validated["request_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "publication": _safe_publication_result(publication),
        "parent_proof": {
            "path": str(proof.get("path")),
            "bytes": proof.get("bytes"),
            "sha256": proof.get("sha256"),
            "canonical_sha256": proof.get("canonical_sha256"),
        },
        "ledger_writes": 0,
        "research_computations": 0,
        "network_calls": 0,
    }


def _registration_args_v3(
    request: dict, plan: dict, publication: Mapping[str, Any]
) -> list[str]:
    execution = plan["precompute_execution"]
    fixture = plan["development_payload_fixture"]
    ledger = request["ledger"]
    registration = request["registration"]
    protocol = plan["validation_protocol"]
    return [
        "research-validate-file",
        "--qualified-trades-path",
        execution["qualified_trades_output_path"],
        "--composite-pit-descriptor-path",
        fixture["source_descriptor_path"],
        "--experiment-id",
        request["experiment_id"],
        "--hypothesis",
        registration["hypothesis"],
        "--expected-mechanism",
        registration["expected_mechanism"],
        "--falsification-criterion",
        registration["falsification_criterion"],
        "--exit-criterion",
        registration["exit_criterion"],
        "--final-oos-start",
        "2026-07-13",
        "--start-date",
        plan["start_date"],
        "--end-date",
        plan["end_date"],
        "--temporal-contract-path",
        execution["temporal_contract_path"],
        "--expected-temporal-contract-sha256",
        fixture["temporal_contract_sha256"],
        "--expected-temporal-role",
        "development",
        "--expected-composite-root-sha256",
        fixture["composite_root_sha256"],
        "--ledger-path",
        ledger["path"],
        "--artifact-dir",
        str(Path(execution["run_root"]) / protocol["artifact_dir_relative"]),
        "--hold-days",
        "3",
        "--top-n",
        "3",
        "--symbol-cooldown-days",
        "5",
        "--max-active-positions",
        "3",
        "--annual-financing-rate-pct",
        "8",
        "--roundtrip-cost-bps",
        "25",
        "--slippage-bps",
        "10",
        "--capital-model",
        "slot-daily",
        "--train-days",
        str(protocol["train_days"]),
        "--validation-days",
        str(protocol["validation_days"]),
        "--step-days",
        str(protocol["step_days"]),
        "--embargo-days",
        str(protocol["embargo_days"]),
        "--minimum-oos-trades",
        str(protocol["minimum_oos_trades"]),
        "--exposure-multiplier",
        format(protocol["exposure_multiplier"], "g"),
        "--pre-exit-calendar-gap-days",
        str(protocol["pre_exit_calendar_gap_days"]),
        "--partial-profit-fraction",
        format(protocol["partial_profit_fraction"], "g"),
        "--correlation-lookback-days",
        str(protocol["correlation_lookback_days"]),
        "--target-win-rate-pct",
        "52",
        "--target-win-rate-max-pct",
        "60",
        "--target-drawdown-pct",
        "15",
        "--target-one-year-return-pct",
        "50",
        "--target-profit-factor",
        "1.3",
        "--target-calmar",
        "1.5",
        "--input-plan-path",
        str(Path(publication["publication_root"]) / "treatment-plan.json"),
        "--register-only",
        "--plan-publication-root",
        publication["publication_root"],
        "--plan-publication-audit-root",
        publication["audit_root"],
        "--expected-plan-publication-id",
        publication["publication_id"],
        "--expected-published-plan-file-sha256",
        publication["plan_file_sha256"],
        "--expected-plan-prepublish-file-sha256",
        publication["prepublish_evidence_file_sha256"],
        "--expected-plan-publication-result-file-sha256",
        publication["result_file_sha256"],
        "--expected-plan-writer-claim-file-sha256",
        publication["writer_claim_file_sha256"],
        "--expected-precompute-control-source-bundle-sha256",
        request["control_source_bundle_sha256"],
        "--minimum-registration-sequence-exclusive",
        "126",
        "--plan-publication-parent-proof-path",
        execution["parent_proof_path"],
        "--expected-ledger-sequence",
        str(ledger["expected_tip_sequence"]),
        "--expected-ledger-record-hash",
        ledger["expected_tip_record_hash"],
    ]


def _validation_args_v3(
    request: dict,
    plan: dict,
    publication: Mapping[str, Any],
    registered: Mapping[str, Any],
    parent: Mapping[str, Any],
    control: Mapping[str, Any],
) -> list[str]:
    registration_args = _registration_args_v3(request, plan, publication)
    register_only_index = registration_args.index("--register-only")
    args = registration_args[:register_only_index]
    args.extend(
        [
            "--registered-record-hash",
            registered["record_hash"],
            "--plan-publication-parent-proof-path",
            control["parent_proof_path"],
            "--expected-plan-publication-parent-proof-file-sha256",
            control["parent_proof_file_sha256"],
            "--expected-plan-publication-parent-proof-canonical-sha256",
            control["parent_proof_canonical_sha256"],
            "--precompute-ledger-path",
            request["ledger"]["path"],
            "--precompute-registered-record-hash",
            registered["record_hash"],
            "--precompute-launch-started-record-hash",
            parent["launch_started"]["record_hash"],
            "--precompute-run-claim-path",
            parent["run_claim"]["path"],
            "--expected-precompute-run-claim-file-sha256",
            parent["run_claim"]["sha256"],
            "--precompute-launch-lease-path",
            parent["expected_launch_lease"]["path"],
            "--expected-precompute-launch-lease-file-sha256",
            parent["expected_launch_lease"]["sha256"],
            "--precompute-completed-record-hash",
            parent["precompute_completed"]["record_hash"],
            "--precompute-run-result-path",
            parent["run_result"]["path"],
            "--expected-precompute-run-result-file-sha256",
            parent["run_result"]["sha256"],
        ]
    )
    return args


def _evaluate_purged_result_v3(payload: Mapping[str, Any]) -> dict[str, Any]:
    qualification = payload.get("qualification")
    aggregate = payload.get("aggregate_validation")
    if not isinstance(qualification, Mapping) or not isinstance(aggregate, Mapping):
        raise ControlSuccessorV3Error("purged validation result is incomplete")
    calmar = aggregate.get("calmar_latest_12m")
    if calmar is None:
        calmar = aggregate.get("portfolio_calmar_latest_1y")
    metrics = {
        "net_annualized_return_pct": aggregate.get("rolling_1y_latest_return_pct"),
        "win_rate_pct": aggregate.get("trade_win_rate_pct"),
        "max_drawdown_pct": aggregate.get("portfolio_max_drawdown_pct"),
        "profit_factor": aggregate.get("trade_profit_factor"),
        "calmar": calmar,
    }
    finite = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in metrics.values()
    )
    gates = {
        "integrity_pass": finite
        and qualification.get("minimum_sample_pass") is True
        and qualification.get("full_rolling_12m_pass") is True,
        "return_pass": finite
        and float(metrics["net_annualized_return_pct"])
        >= _ACCEPTANCE_GATES["net_annualized_return_pct_min"],
        "win_rate_floor_pass": finite
        and float(metrics["win_rate_pct"]) >= _ACCEPTANCE_GATES["win_rate_pct_min"],
        "win_rate_ceiling_pass": finite
        and float(metrics["win_rate_pct"]) <= _ACCEPTANCE_GATES["win_rate_pct_max"],
        "drawdown_pass": finite
        and -_ACCEPTANCE_GATES["max_drawdown_pct_max"]
        <= float(metrics["max_drawdown_pct"])
        <= 0.0,
        "profit_factor_pass": finite
        and float(metrics["profit_factor"]) >= _ACCEPTANCE_GATES["profit_factor_min"],
        "calmar_pass": finite
        and float(metrics["calmar"]) >= _ACCEPTANCE_GATES["calmar_min"],
    }
    return {
        "schema_version": "research-purged-validation-gate-result/v1",
        "metrics": metrics,
        "gates": gates,
        "all_pass": all(gates.values())
        and qualification.get("development_primary_gates_pass") is True,
        "qualification": dict(qualification),
    }


def _run_validation_once_v3(
    request: dict,
    plan: dict,
    publication: Mapping[str, Any],
    registered: Mapping[str, Any],
    parent: Mapping[str, Any],
    control: Mapping[str, Any],
) -> dict[str, Any]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        exit_code = jobs.main(
            _validation_args_v3(
                request, plan, publication, registered, parent, control
            )
        )
    if exit_code != 0:
        raise ControlSuccessorV3Error("purged validation exited nonzero")
    payload = _single_json_output(stream.getvalue(), "purged validation")
    gate = _evaluate_purged_result_v3(payload)
    return {"exit_code": exit_code, "payload": payload, "gate": gate}


def _single_json_output(raw: str, label: str) -> dict[str, Any]:
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ControlSuccessorV3Error(f"{label} output is not singular")
    return _strict_object(lines[0].encode("utf-8"), label)


def _latest_experiment_state_v3(request: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = research_validation.read_experiment_ledger(
        request["ledger"]["path"], require_existing_lock=True
    )
    matches = [
        dict(row)
        for row in rows
        if row.get("experiment_id") == request["experiment_id"]
    ]
    return matches[-1] if matches else None


def _seal_registered_failure_v3(
    request: Mapping[str, Any],
    registered: Mapping[str, Any],
    *,
    failure_code: str,
    failure_phase: str,
    error_type: str,
) -> dict[str, Any]:
    return research_validation.fail_registered_experiment_if_current(
        request["ledger"]["path"],
        experiment_id=request["experiment_id"],
        registered_record_hash=registered["record_hash"],
        failure_code=failure_code,
        failure_phase=failure_phase,
        error_type=error_type,
        run_claim_file_sha256=None,
    )


def _register_once_v3(
    request: dict,
    plan: dict,
    publication: Mapping[str, Any],
    *,
    completion_authorization: str,
) -> dict[str, Any]:
    secret_key = "RESEARCH_PLAN_PUBLICATION_COMPLETION_AUTHORIZATION"
    stream = io.StringIO()
    owned_registered: dict[str, Any] | None = None
    try:
        with _REGISTRATION_SECRET_LOCK:
            if secret_key in os.environ:
                raise ControlSuccessorV3Error(
                    "publication authorization already exists"
                )
            os.environ[secret_key] = completion_authorization
            try:
                with redirect_stdout(stream):
                    exit_code = jobs.main(
                        _registration_args_v3(request, plan, publication)
                    )
            finally:
                os.environ.pop(secret_key, None)
        if exit_code != 0:
            raise ControlSuccessorV3Error("controlled registration failed")
        registered = _single_json_output(stream.getvalue(), "controlled registration")
        ledger = request["ledger"]
        if (
            registered.get("event_type") != "registered"
            or registered.get("experiment_id") != request["experiment_id"]
            or registered.get("sequence") != ledger["expected_tip_sequence"] + 1
            or registered.get("previous_record_hash")
            != ledger["expected_tip_record_hash"]
            or not _is_sha256(registered.get("record_hash"))
        ):
            raise ControlSuccessorV3Error(
                "controlled registration result is invalid"
            )
        owned_registered = registered
        return registered
    except BaseException as exc:
        if owned_registered is not None:
            latest = _latest_experiment_state_v3(request)
            if (
                latest is not None
                and latest.get("event_type") == "registered"
                and latest.get("record_hash") == owned_registered["record_hash"]
            ):
                try:
                    _seal_registered_failure_v3(
                        request,
                        owned_registered,
                        failure_code="CONTROLLED_REGISTRATION_RESULT_INVALID",
                        failure_phase="registration_control",
                        error_type=type(exc).__name__,
                    )
                except BaseException as seal_exc:
                    raise ControlSuccessorV3Error(
                        "controlled registration failed and could not be sealed"
                    ) from seal_exc
        raise ControlSuccessorV3Error("controlled registration failed") from exc


def _run_parent_once_v3(
    request: dict, plan: dict, registered: Mapping[str, Any]
) -> dict[str, Any]:
    stream = io.StringIO()
    try:
        with redirect_stdout(stream):
            exit_code = precompute_parent.main(
                [
                    "run",
                    "--ledger-path",
                    request["ledger"]["path"],
                    "--experiment-id",
                    request["experiment_id"],
                    "--registered-record-hash",
                    registered["record_hash"],
                    "--expected-current-tip-sequence",
                    str(registered["sequence"]),
                    "--expected-current-tip-record-hash",
                    registered["record_hash"],
                ]
            )
        payload = _single_json_output(stream.getvalue(), "controlled parent")
        if exit_code != 0:
            raise ControlSuccessorV3Error("controlled parent exited nonzero")
        return {"exit_code": exit_code, "payload": payload}
    except BaseException as exc:
        _seal_parent_failure_v3(request, registered, error_type=type(exc).__name__)
        raise ControlSuccessorV3Error("controlled parent failed") from exc


def _seal_parent_failure_v3(
    request: Mapping[str, Any],
    registered: Mapping[str, Any],
    *,
    error_type: str,
) -> dict[str, Any] | None:
    latest = _latest_experiment_state_v3(request)
    if latest is None or latest.get("event_type") in {"failed", "aborted"}:
        return latest
    event_type = latest.get("event_type")
    if event_type == "registered":
        return _seal_registered_failure_v3(
            request,
            registered,
            failure_code="PRECOMPUTE_PARENT_FAILED",
            failure_phase="precompute_parent",
            error_type=error_type,
        )
    if event_type == "precompute_launch_started":
        return research_validation.fail_authorized_precompute_launch_if_current(
            request["ledger"]["path"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=latest["record_hash"],
            run_claim_file_sha256=latest["run_claim_file_sha256"],
            launch_lease_file_sha256=latest["launch_lease_file_sha256"],
            parent_proof_file_sha256=latest["parent_proof_file_sha256"],
            parent_proof_canonical_sha256=latest[
                "parent_proof_canonical_sha256"
            ],
            failure_code="PRECOMPUTE_PARENT_FAILED",
            failure_phase="precompute_parent",
            error_type=error_type,
        )
    if event_type == "precompute_completed":
        launch_hash = latest.get("launch_started_record_hash")
        return research_validation.fail_precomputed_experiment_if_current(
            request["ledger"]["path"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=launch_hash,
            precompute_completed_record_hash=latest["record_hash"],
            failure_code="PRECOMPUTE_PARENT_RESULT_INVALID",
            failure_phase="precompute_parent_result",
            error_type=error_type,
        )
    raise ControlSuccessorV3Error("controlled parent left an unknown ledger state")


def _seal_validation_failure_v3(
    request: Mapping[str, Any],
    registered: Mapping[str, Any],
    parent: Mapping[str, Any],
    *,
    error_type: str,
) -> dict[str, Any] | None:
    latest = _latest_experiment_state_v3(request)
    if latest is None or latest.get("event_type") in _TERMINAL_EVENTS:
        return latest
    event_type = latest.get("event_type")
    if event_type == "precompute_completed":
        return research_validation.fail_precomputed_experiment_if_current(
            request["ledger"]["path"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=parent["precompute_launch_started"][
                "record_hash"
            ],
            precompute_completed_record_hash=latest["record_hash"],
            failure_code="PURGED_VALIDATION_PRECLAIM_FAILED",
            failure_phase="purged_validation_preclaim",
            error_type=error_type,
        )
    if event_type == "validation_started":
        return research_validation.fail_validation_started_experiment_if_current(
            request["ledger"]["path"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            validation_started_record_hash=latest["record_hash"],
            failure_code="PURGED_VALIDATION_FAILED",
            failure_phase="purged_validation",
            error_type=error_type,
        )
    if event_type == "completed":
        failure_payload = {
            "schema_version": "research-purged-validation-control-failure/v1",
            "error_type": error_type,
        }
        failure_sha256 = _sha256(_canonical_bytes(failure_payload))
        return research_validation.decide_completed_validation_if_current(
            request["ledger"]["path"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            validation_started_record_hash=latest["validation_started_record_hash"],
            completed_record_hash=latest["record_hash"],
            decision_code="PURGED_RESULT_INVALID",
            validation_result_sha256=failure_sha256,
            gate_result_sha256=failure_sha256,
            all_gates_pass=False,
        )
    raise ControlSuccessorV3Error("controlled validation left an unknown ledger state")


def _detect_existing_execution_v3(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Read and bind an existing v3 execution without changing its lifecycle."""

    if rows is None:
        rows = research_validation.read_experiment_ledger(
            request["ledger"]["path"], require_existing_lock=True
        )
    experiment_rows = [
        dict(row)
        for row in rows
        if row.get("experiment_id") == request["experiment_id"]
    ]
    if not experiment_rows:
        return None
    registrations = [
        row for row in experiment_rows if row.get("event_type") == "registered"
    ]
    if len(registrations) != 1:
        raise ControlSuccessorV3Error("interrupted controlled registration is ambiguous")
    registered = registrations[0]
    research_validation._require_v3_launch_registration(registered)
    contract = registered.get("registration_contract")
    input_plan = contract.get("input_plan") if isinstance(contract, Mapping) else None
    if not isinstance(input_plan, Mapping) or input_plan.get("payload") != dict(plan):
        raise ControlSuccessorV3Error(
            "interrupted controlled registration does not match frozen plan"
        )
    latest = experiment_rows[-1]
    return {
        "state": latest.get("event_type"),
        "registered": registered,
        "latest": latest,
    }


def _record_validation_decision_v3(
    request: Mapping[str, Any],
    registered: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    latest = _latest_experiment_state_v3(request)
    if (
        latest is None
        or latest.get("event_type") != "completed"
        or latest.get("registered_record_hash") != registered.get("record_hash")
        or not _is_sha256(latest.get("validation_started_record_hash"))
    ):
        raise ControlSuccessorV3Error("purged validation completion is unbound")
    payload = validation.get("payload")
    gate = validation.get("gate")
    if not isinstance(payload, Mapping) or not isinstance(gate, Mapping):
        raise ControlSuccessorV3Error("purged validation result is invalid")
    all_pass = gate.get("all_pass")
    if not isinstance(all_pass, bool):
        raise ControlSuccessorV3Error("purged validation gate is invalid")
    return research_validation.decide_completed_validation_if_current(
        request["ledger"]["path"],
        experiment_id=request["experiment_id"],
        registered_record_hash=registered["record_hash"],
        validation_started_record_hash=latest["validation_started_record_hash"],
        completed_record_hash=latest["record_hash"],
        decision_code=(
            "PURGED_ACCEPTANCE_GATE_GREEN"
            if all_pass
            else "PURGED_ACCEPTANCE_GATE_RED"
        ),
        validation_result_sha256=_sha256(_canonical_bytes(dict(payload))),
        gate_result_sha256=_sha256(_canonical_bytes(dict(gate))),
        all_gates_pass=all_pass,
    )


def _verify_parent_success_v3(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    registered: Mapping[str, Any],
    parent_result: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(parent_result, Mapping)
        or parent_result.get("exit_code") != 0
        or not isinstance(parent_result.get("payload"), Mapping)
    ):
        raise ControlSuccessorV3Error("controlled parent result is invalid")
    payload = dict(parent_result["payload"])
    required = {
        "schema_version",
        "launch_started",
        "run_claim",
        "expected_launch_lease",
        "ready",
        "launcher_receipt",
        "run_result",
        "precompute_completed",
    }
    if (
        set(payload) not in (required, {*required, "recovered_after_commit"})
        or payload.get("schema_version")
        != "research-precompute-supervised-result/v2"
    ):
        raise ControlSuccessorV3Error("controlled parent payload is invalid")
    for key in required - {"schema_version"}:
        if not isinstance(payload.get(key), Mapping):
            raise ControlSuccessorV3Error("controlled parent payload is incomplete")
    if (
        payload["precompute_completed"].get("experiment_id")
        != request["experiment_id"]
        or payload["precompute_completed"].get("registered_record_hash")
        != registered.get("record_hash")
    ):
        raise ControlSuccessorV3Error("controlled parent identity mismatch")
    contract = registered.get("registration_contract")
    control = contract.get("precompute_control") if isinstance(contract, Mapping) else None
    if not isinstance(control, Mapping):
        raise ControlSuccessorV3Error("controlled parent registration is unbound")
    try:
        verified = precompute_control.verify_registered_precompute_run_result_v1(
            request["ledger"]["path"],
            workspace_root=request["workspace_root"],
            experiment_id=request["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=payload["launch_started"]["record_hash"],
            verified_control=control,
            run_claim_path=payload["run_claim"]["path"],
            expected_run_claim_file_sha256=payload["run_claim"]["sha256"],
            launch_lease_path=payload["expected_launch_lease"]["path"],
            expected_launch_lease_file_sha256=payload[
                "expected_launch_lease"
            ]["sha256"],
            precompute_completed_record_hash=payload["precompute_completed"][
                "record_hash"
            ],
            run_result_path=payload["run_result"]["path"],
            expected_run_result_file_sha256=payload["run_result"]["sha256"],
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ControlSuccessorV3Error(
            "controlled parent evidence verification failed"
        ) from exc
    if (
        verified.get("registered") != dict(registered)
        or verified.get("precompute_completed")
        != dict(payload["precompute_completed"])
        or verified.get("run_result_payload") != payload["run_result"].get("payload")
    ):
        raise ControlSuccessorV3Error("controlled parent verified tuple mismatch")
    return {**verified, "control": dict(control), "parent_payload": payload}


def _evaluate_pre_purged_gate_v3(
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    protocol = _VALIDATION_PROTOCOL
    sweep = jobs.sweep_qualified_trades(
        trades,
        hold_days=3,
        top_n=3,
        symbol_cooldown_days=5,
        max_active_positions=3,
        min_trades=protocol["pre_purged_development_gate"]["minimum_trades"],
        target_win_rate_pct=_ACCEPTANCE_GATES["win_rate_pct_min"],
        target_drawdown_pct=_ACCEPTANCE_GATES["max_drawdown_pct_max"],
        target_one_year_return_pct=_ACCEPTANCE_GATES[
            "net_annualized_return_pct_min"
        ],
        target_profit_factor=_ACCEPTANCE_GATES["profit_factor_min"],
        target_calmar=_ACCEPTANCE_GATES["calmar_min"],
        exposure_multipliers=[protocol["exposure_multiplier"]],
        annual_financing_rate_pct=8.0,
        roundtrip_cost_bps=25.0,
        slippage_bps=10.0,
        capital_model="slot-daily",
        required_signal_tags=[],
        excluded_signal_tags=[],
        market_levels=[],
        force_exposure_multipliers=True,
        pre_exit_calendar_gap_days=protocol["pre_exit_calendar_gap_days"],
        partial_profit_activation_pct=protocol[
            "partial_profit_activation_pct"
        ],
        partial_profit_fraction=protocol["partial_profit_fraction"],
        correlation_threshold=protocol["correlation_threshold"],
        correlation_lookback_days=protocol["correlation_lookback_days"],
        fixed_spec=True,
    )
    if not isinstance(sweep, Mapping):
        raise ControlSuccessorV3Error("pre-purged development sweep is invalid")
    rows = sweep.get("top")
    if sweep.get("returned_count") != 1 or not isinstance(rows, list) or len(rows) != 1:
        raise ControlSuccessorV3Error("pre-purged development sweep is ambiguous")
    row = rows[0]
    calmar = row.get("calmar_latest_12m")
    if calmar is None:
        calmar = row.get("portfolio_calmar_latest_1y")
    metrics = {
        "selected_trade_count": row.get("selected_trade_count"),
        "full_rolling_12m": row.get("rolling_1y_latest_full_window"),
        "net_annualized_return_pct": row.get("rolling_1y_latest_return_pct"),
        "win_rate_pct": row.get("trade_win_rate_pct"),
        "max_drawdown_pct": row.get("portfolio_max_drawdown_pct"),
        "profit_factor": row.get("trade_profit_factor"),
        "calmar": calmar,
    }
    numeric = {
        key: value
        for key, value in metrics.items()
        if key
        not in {
            "selected_trade_count",
            "full_rolling_12m",
        }
    }
    finite = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in numeric.values()
    )
    gates = {
        "integrity_pass": finite,
        "minimum_sample_pass": isinstance(metrics["selected_trade_count"], int)
        and metrics["selected_trade_count"]
        >= protocol["pre_purged_development_gate"]["minimum_trades"],
        "full_rolling_12m_pass": metrics["full_rolling_12m"] is True,
        "return_pass": finite
        and float(metrics["net_annualized_return_pct"])
        >= _ACCEPTANCE_GATES["net_annualized_return_pct_min"],
        "win_rate_floor_pass": finite
        and float(metrics["win_rate_pct"])
        >= _ACCEPTANCE_GATES["win_rate_pct_min"],
        "win_rate_ceiling_pass": finite
        and float(metrics["win_rate_pct"])
        <= _ACCEPTANCE_GATES["win_rate_pct_max"],
        "drawdown_pass": finite
        and -_ACCEPTANCE_GATES["max_drawdown_pct_max"]
        <= float(metrics["max_drawdown_pct"])
        <= 0.0,
        "profit_factor_pass": finite
        and float(metrics["profit_factor"])
        >= _ACCEPTANCE_GATES["profit_factor_min"],
        "calmar_pass": finite
        and float(metrics["calmar"]) >= _ACCEPTANCE_GATES["calmar_min"],
    }
    return {
        "schema_version": "research-development-pre-purged-gate-result/v1",
        "metrics": metrics,
        "gates": gates,
        "all_pass": all(gates.values()),
    }


def _publish_json_exclusive_v3(path: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        **dict(body),
        "canonical_sha256": _sha256(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControlSuccessorV3Error("control successor receipt already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        fsync_directory(path.parent)
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": _sha256(raw),
        "canonical_sha256": payload["canonical_sha256"],
        "payload": payload,
    }


def _load_and_evaluate_pre_purged_gate_v3(
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    publication: Mapping[str, Any],
    verified_parent: Mapping[str, Any],
) -> dict[str, Any]:
    result_payload = verified_parent.get("run_result_payload")
    descriptor = (
        result_payload.get("qualified_output")
        if isinstance(result_payload, Mapping)
        else None
    )
    if not isinstance(descriptor, Mapping):
        raise ControlSuccessorV3Error("precompute qualified output is missing")
    output_path = Path(str(descriptor.get("path") or ""))
    if output_path != Path(plan["precompute_execution"]["qualified_trades_output_path"]):
        raise ControlSuccessorV3Error("precompute qualified output path mismatch")
    raw = _safe_existing_file(
        output_path,
        workspace=Path(request["workspace_root"]),
        label="precompute qualified output",
    )
    if (
        descriptor.get("bytes") != len(raw)
        or descriptor.get("sha256") != _sha256(raw)
    ):
        raise ControlSuccessorV3Error("precompute qualified output hash mismatch")
    try:
        payload = jobs._load_qualified_trades_payload(str(output_path), raw_bytes=raw)
        jobs._verify_treatment_bound_payload(
            payload,
            dict(plan),
            {"sha256": publication["plan_file_sha256"]},
        )
    except (TypeError, ValueError) as exc:
        raise ControlSuccessorV3Error(
            "precompute treatment output verification failed"
        ) from exc
    trades = payload.get("qualified_trades")
    if (
        not isinstance(trades, list)
        or descriptor.get("qualified_trade_count") != len(trades)
    ):
        raise ControlSuccessorV3Error("precompute qualified trade count mismatch")
    gate = _evaluate_pre_purged_gate_v3(trades)
    receipt = _publish_json_exclusive_v3(
        Path(request["run_root"]) / "control" / "development-gate.json",
        {
            "schema_version": "research-development-pre-purged-gate-receipt/v1",
            "experiment_id": request["experiment_id"],
            "request_sha256": request["request_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "qualified_output": dict(descriptor),
            "acceptance_gates": dict(_ACCEPTANCE_GATES),
            "result": gate,
            "purged_validation_calls": 0,
            "network_calls": 0,
        },
    )
    return {**gate, "receipt": {key: receipt[key] for key in receipt if key != "payload"}}


def execute_control_successor_v3(request: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_control_request_v3(request)
    plan = build_treatment_plan_v5(validated)
    _verify_execute_preflight_v3(validated, plan)
    publication = _publish_plan_v3(validated, plan)
    _create_proof_only_skeleton_v3(validated, plan)
    authorization = publication["completion_authorization"]
    registered = _register_once_v3(
        validated,
        plan,
        publication,
        completion_authorization=authorization,
    )
    authorization = None
    publication.pop("completion_authorization", None)
    parent_result = _run_parent_once_v3(validated, plan, registered)
    try:
        verified_parent = _verify_parent_success_v3(
            validated, plan, registered, parent_result
        )
        development_gate = _load_and_evaluate_pre_purged_gate_v3(
            validated, plan, publication, verified_parent
        )
    except BaseException as exc:
        _seal_parent_failure_v3(validated, registered, error_type=type(exc).__name__)
        raise
    if not development_gate["all_pass"]:
        failed = research_validation.fail_precomputed_experiment_if_current(
            validated["ledger"]["path"],
            experiment_id=validated["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=verified_parent[
                "precompute_launch_started"
            ]["record_hash"],
            precompute_completed_record_hash=verified_parent[
                "precompute_completed"
            ]["record_hash"],
            failure_code="DEVELOPMENT_ACCEPTANCE_GATE_RED",
            failure_phase="pre_purged_development_gate",
            error_type="AcceptanceGateRed",
        )
        return {
            "schema_version": "research-control-successor-execution-result/v3",
            "status": "development_gate_red",
            "request_sha256": validated["request_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "publication": _safe_publication_result(publication),
            "registered": dict(registered),
            "parent": parent_result,
            "development_gate": development_gate,
            "failed": failed,
            "purged_validation_calls": 0,
            "network_calls": 0,
        }
    try:
        validation = _run_validation_once_v3(
            validated,
            plan,
            publication,
            registered,
            verified_parent["parent_payload"],
            verified_parent["control"],
        )
        decision = _record_validation_decision_v3(
            validated, registered, validation
        )
    except BaseException as exc:
        _seal_validation_failure_v3(
            validated,
            registered,
            verified_parent,
            error_type=type(exc).__name__,
        )
        raise
    return {
        "schema_version": "research-control-successor-execution-result/v3",
        "status": (
            "purged_gate_green"
            if validation["gate"]["all_pass"]
            else "purged_gate_red"
        ),
        "request_sha256": validated["request_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "publication": _safe_publication_result(publication),
        "registered": dict(registered),
        "parent": parent_result,
        "development_gate": development_gate,
        "purged_validation": validation,
        "decision": decision,
        "purged_validation_calls": 1,
        "network_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("probe", "execute"):
        subparser = subparsers.add_parser(command, allow_abbrev=False)
        subparser.add_argument("--request-path", required=True)
        subparser.add_argument("--expected-request-file-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    request = load_control_request_v3(
        args.request_path,
        expected_file_sha256=args.expected_request_file_sha256,
    )
    result = (
        probe_control_successor_v3(request)
        if args.command == "probe"
        else execute_control_successor_v3(request)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
