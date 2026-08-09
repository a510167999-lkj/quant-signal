"""Structurally select a Factor V2 branch without granting source authority."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
LOW_RVOL_BRANCH = "low_rvol20_rank_overlay_20"
RECEIPT_SCHEMA_VERSION = "factor-v2-development-evaluation-decision-verification-receipt/v1"
BRANCH_RECEIPT_SCHEMA_VERSION = "factor-v2-decision-branch-structural-adapter/v2"
SELECTION_RULE = "first_green_in_arm_order_else_low_rvol20_rank_overlay_20"
FACTOR_V2_SPEC_SHA256 = "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
EVALUATION_PRODUCER_ROOT_SHA256 = "926b9229a2e6e47ae24f3b590fab46ded2244e757fbe92fc6bbf0dd3de1dd938"
VERIFICATION_PRODUCER_ROOT_SHA256 = (
    "3b4c62fa4a02ced6a2f271af74deb75b3234298fe61ae21fc120fd59f2bca55f"
)
SOURCE_RUN_IDENTITY = {
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
RECEIPT_FIELDS = {
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
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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
        raise RuntimeError("receipt is not canonical JSON") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _strict_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be a lowercase SHA-256")
    return value


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _reject_reparse_chain(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for current in (*reversed(absolute.parents), absolute):
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise RuntimeError("decision receipt is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise RuntimeError("decision receipt path must not contain a reparse point")
    return absolute


def _read_direct_bytes(path: Path) -> bytes:
    path = _reject_reparse_chain(path)
    try:
        path_metadata = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("decision receipt is unavailable") from exc
    if stat.S_ISLNK(path_metadata.st_mode) or _is_reparse(path_metadata):
        raise RuntimeError("decision receipt must not be a symbolic link")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("decision receipt is unavailable") from exc
    try:
        before_fd = os.fstat(descriptor)
        if not stat.S_ISREG(before_fd.st_mode):
            raise RuntimeError("decision receipt must be a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
        try:
            after_path = os.lstat(path)
        except OSError as exc:
            raise RuntimeError("decision receipt changed while being read") from exc
        if (
            _identity(path_metadata) != _identity(before_fd)
            or _identity(before_fd) != _identity(after_fd)
            or _identity(before_fd) != _identity(after_path)
            or stat.S_ISLNK(after_path.st_mode)
            or _is_reparse(after_path)
        ):
            raise RuntimeError("decision receipt changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_receipt(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("decision receipt is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("decision receipt must be a JSON object")
    if _canonical_bytes(payload) != raw:
        raise RuntimeError("decision receipt bytes are not canonical")
    return payload


def _validate_receipt(payload: dict[str, Any]) -> None:
    if set(payload) != RECEIPT_FIELDS:
        raise RuntimeError("decision receipt fields drifted")
    if (
        payload.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or payload.get("temporal_role") != "development"
        or payload.get("factor_v2_spec_sha256") != FACTOR_V2_SPEC_SHA256
        or payload.get("evaluation_producer_root_sha256") != EVALUATION_PRODUCER_ROOT_SHA256
        or payload.get("verification_producer_root_sha256") != VERIFICATION_PRODUCER_ROOT_SHA256
        or payload.get("arm_order") != list(ARM_ORDER)
        or payload.get("verified") is not True
        or payload.get("embargo_consumed") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("production_recommendation_eligible") is not False
    ):
        raise RuntimeError("decision receipt trusted values drifted")

    for field in (
        "factor_v2_spec_sha256",
        "evaluation_artifact_sha256",
        "evaluation_manifest_file_sha256",
        "evaluation_producer_root_sha256",
        "verification_producer_root_sha256",
        "receipt_sha256",
    ):
        _strict_sha256(payload.get(field), field)

    decisions = payload.get("arm_decisions")
    if (
        not isinstance(decisions, dict)
        or set(decisions) != set(ARM_ORDER)
        or any(decisions.get(arm) not in {"GREEN", "RED"} for arm in ARM_ORDER)
    ):
        raise RuntimeError("decision receipt arm decisions drifted")

    source_identity = payload.get("source_run_identity")
    if (
        not isinstance(source_identity, dict)
        or set(source_identity) != set(SOURCE_RUN_IDENTITY)
        or source_identity != SOURCE_RUN_IDENTITY
    ):
        raise RuntimeError("decision receipt source identity drifted")

    unsigned = dict(payload)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    if _canonical_sha256(unsigned) != receipt_sha256:
        raise RuntimeError("decision receipt self hash drifted")


def _validated_receipt(
    path: str | os.PathLike[str],
    expected_raw_file_sha256: str,
) -> tuple[dict[str, Any], str]:
    expected = _strict_sha256(
        expected_raw_file_sha256,
        "expected raw file SHA-256",
    )
    receipt_path = Path(path)
    if receipt_path.name != f"{expected}.json":
        raise RuntimeError("decision receipt path is not content addressed")
    raw = _read_direct_bytes(receipt_path)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError("decision receipt raw file hash drifted")
    payload = _parse_receipt(raw)
    _validate_receipt(payload)
    return payload, expected


def _select_branch(receipt: dict[str, Any]) -> str:
    decisions = receipt["arm_decisions"]
    for arm in ARM_ORDER:
        if decisions[arm] == "GREEN":
            return arm
    return LOW_RVOL_BRANCH


def select_factor_v2_decision_branch(
    path: str | os.PathLike[str],
    *,
    expected_raw_file_sha256: str,
) -> str:
    """Return the structural branch choice; this does not grant authority."""

    receipt, _ = _validated_receipt(path, expected_raw_file_sha256)
    return _select_branch(receipt)


def build_factor_v2_decision_branch_receipt(
    path: str | os.PathLike[str],
    *,
    expected_raw_file_sha256: str,
) -> dict[str, Any]:
    """Build an explicitly unverified structural adapter receipt."""

    source, raw_sha256 = _validated_receipt(
        path,
        expected_raw_file_sha256,
    )
    selected_branch = _select_branch(source)
    selected_arm = selected_branch if selected_branch in ARM_ORDER else None
    unsigned = {
        "schema_version": BRANCH_RECEIPT_SCHEMA_VERSION,
        "source_decision_receipt_raw_file_sha256": raw_sha256,
        "source_decision_receipt_sha256": source["receipt_sha256"],
        "evaluation_artifact_sha256": source["evaluation_artifact_sha256"],
        "arm_order": list(ARM_ORDER),
        "arm_decisions": dict(source["arm_decisions"]),
        "selection_rule": SELECTION_RULE,
        "selected_branch": selected_branch,
        "selected_arm": selected_arm,
        "low_rvol_overlay_status": ("VOID" if selected_arm is not None else "ELIGIBLE"),
        "contract_binding_validated": True,
        "publisher_terminal_chain_verified": False,
        "source_authority_complete": False,
        "formal_materialization_eligible": False,
        "verified": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    return {
        **unsigned,
        "receipt_sha256": _canonical_sha256(unsigned),
    }
