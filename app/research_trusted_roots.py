"""Caller-supplied trust roots for frozen research evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from app.current_pool_offline_fixture_v2 import (
    EVIDENCE_USE,
    OfflineFixtureV2Error,
    _path_is_link_or_reparse,
    _read_file,
    _require_directory,
    _sha,
    _sha_bytes,
    _strict_json_bytes,
    _tree_files,
    _verify_clean_snapshot,
    preflight_current_pool_offline_fixture_v2,
)


_HEX = frozenset("0123456789abcdef")


class TrustedRootError(ValueError):
    pass


def _is_sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _HEX


def _canonical_sha(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: Any, field: str) -> str:
    if not _is_sha(value):
        raise TrustedRootError(f"{field} is not a lowercase SHA256")
    return value


def _exact_mapping(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TrustedRootError(f"{label} fields are not exact")
    return value


@dataclass(frozen=True)
class TrustedSnapshotRootV1:
    schema: str
    artifact_schema: str
    directory_id: str
    created_at_utc: str
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    manifest_content_canonical_sha256: str
    receipt_file_sha256: str
    receipt_canonical_sha256: str
    receipt_identity_canonical_sha256: str
    audit_file_sha256: str
    audit_canonical_sha256: str
    source_files_root_sha256: str
    payload_file_count: int
    payload_tree_sha256: str
    payload_directory_set_sha256: str
    origin_source_tree_sha256: str
    origin_content_manifest_sha256: str
    origin_import_receipt_canonical_sha256: str
    root_canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-snapshot-root/v1" or self.artifact_schema != "clean-source-snapshot/v2":
            raise TrustedRootError("trusted snapshot schema mismatch")
        for field in (
            "directory_id",
            "manifest_file_sha256",
            "manifest_canonical_sha256",
            "manifest_content_canonical_sha256",
            "receipt_file_sha256",
            "receipt_canonical_sha256",
            "receipt_identity_canonical_sha256",
            "audit_file_sha256",
            "audit_canonical_sha256",
            "source_files_root_sha256",
            "payload_tree_sha256",
            "payload_directory_set_sha256",
            "origin_source_tree_sha256",
            "origin_content_manifest_sha256",
            "origin_import_receipt_canonical_sha256",
            "root_canonical_sha256",
        ):
            _require_sha(getattr(self, field), field)
        if type(self.payload_file_count) is not int or self.payload_file_count <= 0:
            raise TrustedRootError("trusted snapshot file count is invalid")
        unsigned = self.to_dict()
        unsigned.pop("root_canonical_sha256")
        if not hmac.compare_digest(self.root_canonical_sha256, _canonical_sha(unsigned)):
            raise TrustedRootError("trusted snapshot root canonical SHA mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrustedSnapshotRootV1":
        expected = set(cls.__dataclass_fields__)
        return cls(**dict(_exact_mapping(value, expected, "trusted snapshot root")))


@dataclass(frozen=True)
class TrustedFixtureRootV1:
    schema: str
    artifact_schema: str
    fixture_id: str
    as_of: str
    evidence_use: str
    manifest_file_sha256: str
    manifest_canonical_sha256: str
    manifest_content_canonical_sha256: str
    receipt_file_sha256: str
    receipt_canonical_sha256: str
    adjudication_file_sha256: str
    adjudication_canonical_sha256: str
    declared_files_root_sha256: str
    calls_root_sha256: str
    source_segment_calls_root_sha256: str
    risk_segment_calls_root_sha256: str
    payload_file_count: int
    payload_tree_sha256: str
    payload_directory_set_sha256: str
    call_count: int
    physical_raw_file_count: int
    invalid_run_id: str
    invalid_run_tree_sha256: str
    invalid_audit_file_sha256: str
    invalid_audit_canonical_sha256: str
    snapshot: TrustedSnapshotRootV1
    root_canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema != "trusted-fixture-root/v1" or self.artifact_schema != "current-pool-offline-fixture/v2":
            raise TrustedRootError("trusted fixture schema mismatch")
        if not isinstance(self.snapshot, TrustedSnapshotRootV1):
            raise TrustedRootError("trusted fixture snapshot root is invalid")
        for field in (
            "fixture_id",
            "manifest_file_sha256",
            "manifest_canonical_sha256",
            "manifest_content_canonical_sha256",
            "receipt_file_sha256",
            "receipt_canonical_sha256",
            "adjudication_file_sha256",
            "adjudication_canonical_sha256",
            "declared_files_root_sha256",
            "calls_root_sha256",
            "source_segment_calls_root_sha256",
            "risk_segment_calls_root_sha256",
            "payload_tree_sha256",
            "payload_directory_set_sha256",
            "invalid_run_id",
            "invalid_run_tree_sha256",
            "invalid_audit_file_sha256",
            "invalid_audit_canonical_sha256",
            "root_canonical_sha256",
        ):
            _require_sha(getattr(self, field), field)
        for field in ("payload_file_count", "call_count", "physical_raw_file_count"):
            value = getattr(self, field)
            if type(value) is not int or value <= 0:
                raise TrustedRootError(f"{field} is invalid")
        unsigned = self.to_dict()
        unsigned.pop("root_canonical_sha256")
        if not hmac.compare_digest(self.root_canonical_sha256, _canonical_sha(unsigned)):
            raise TrustedRootError("trusted fixture root canonical SHA mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrustedFixtureRootV1":
        expected = set(cls.__dataclass_fields__)
        payload = dict(_exact_mapping(value, expected, "trusted fixture root"))
        payload["snapshot"] = TrustedSnapshotRootV1.from_dict(payload["snapshot"])
        return cls(**payload)


_SNAPSHOT_MANIFEST_KEYS = {
    "contamination_audit",
    "copy_parity_count",
    "created_at_utc",
    "eligible_pool_count",
    "external_paths",
    "manifest_canonical_sha256",
    "manifest_content_canonical_sha256",
    "network_calls",
    "production_recommendation_eligible",
    "receipt_identity_canonical_sha256",
    "schema",
    "self_contained_lineage_file_count",
    "snapshot_id",
    "source_file_count",
    "source_files",
    "source_files_root_sha256",
    "source_lineage",
    "source_total_bytes",
    "sqlite_immutable_probe",
    "status",
    "symlink_reparse_hardlink_count",
}
_SNAPSHOT_RECEIPT_KEYS = {
    "contamination_audit_canonical_sha256",
    "contamination_audit_file_sha256",
    "copy_parity_count",
    "created_at_utc",
    "eligible_pool_count",
    "manifest",
    "manifest_content_canonical_sha256",
    "network_calls",
    "production_recommendation_eligible",
    "receipt_canonical_sha256",
    "receipt_identity_canonical_sha256",
    "schema",
    "sidecars_created_in_snapshot",
    "snapshot_id",
    "source_files_root_sha256",
    "sqlite_access_contract",
}
_SNAPSHOT_AUDIT_KEYS = {
    "actual_file_count",
    "actual_files_root_sha256",
    "audit_canonical_sha256",
    "cause",
    "declared_file_count",
    "declared_files_root_sha256",
    "extra_files",
    "frozen_content_manifest_sha256",
    "frozen_source_tree_sha256",
    "main_sqlite_sha256",
    "missing_files",
    "network_calls",
    "observed_at_utc",
    "schema",
    "sidecars_deleted_or_modified",
    "source_after",
    "source_before",
    "source_modified",
    "status",
}
_FIXTURE_MANIFEST_KEYS = {
    "as_of",
    "calls",
    "calls_root_sha256",
    "canonical_sha256",
    "content_canonical_sha256",
    "declared_files",
    "declared_files_root_sha256",
    "eligible_pool_count",
    "evidence_use",
    "fixture_id",
    "physical_raw_summary",
    "production_recommendation_eligible",
    "replay_mode",
    "risk_adjudication",
    "schema",
    "segments",
    "source_lineage",
    "temporal_role",
    "zero_row_summary",
}
_FIXTURE_RECEIPT_KEYS = {
    "atomic_publish",
    "call_count",
    "credential_accesses",
    "declared_files_root_sha256",
    "eligible_pool_count",
    "fixture_id",
    "manifest_canonical_sha256",
    "manifest_file_sha256",
    "network_calls",
    "physical_raw_file_count",
    "production_recommendation_eligible",
    "receipt_canonical_sha256",
    "schema",
    "segment_count",
    "single_writer",
}
_ADJUDICATION_KEYS = {
    "adjudication_canonical_sha256",
    "allowed_source_indices",
    "eligible_pool_count",
    "entries",
    "historical_v1_run_status",
    "historical_v1_run_success_adopted",
    "invalid_audit_canonical_sha256",
    "invalid_audit_file_sha256",
    "invalid_checkpoint_canonical_sha256",
    "invalid_checkpoint_file_sha256",
    "invalid_database_file_sha256",
    "invalid_plan_canonical_sha256",
    "invalid_plan_file_sha256",
    "invalid_receipt_index_canonical_sha256",
    "invalid_receipt_index_file_sha256",
    "invalid_run_id",
    "invalid_run_manifest_canonical_sha256",
    "invalid_run_manifest_file_sha256",
    "invalid_run_tree_sha256",
    "production_recommendation_eligible",
    "schema",
    "status",
}


def _metadata(root: Path, name: str, keys: set[str], label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_file(root / name)
    payload = _strict_json_bytes(raw, label)
    return dict(_exact_mapping(payload, keys, label)), raw


def _directory_set(root: Path) -> list[str]:
    rows: list[str] = []
    for base, directories, _files in os.walk(root, topdown=True, followlinks=False):
        base_path = Path(base)
        _require_directory(base_path, "trusted root directory")
        for name in directories:
            child = base_path / name
            if _path_is_link_or_reparse(child):
                raise TrustedRootError("trusted root contains a link or reparse point")
            _require_directory(child, "trusted root directory")
            rows.append(child.relative_to(root).as_posix())
    return sorted(rows)


def _verify_physical_root(root: Path, *, file_count: int, tree_sha: str, directory_sha: str) -> None:
    files = _tree_files(root)
    directories = _directory_set(root)
    if (
        len(files) != file_count
        or not hmac.compare_digest(_sha(files), tree_sha)
        or not hmac.compare_digest(_canonical_sha(directories), directory_sha)
    ):
        raise TrustedRootError("trusted physical root mismatch")


def verify_snapshot_v2(
    snapshot_root: str | Path,
    *,
    expected: TrustedSnapshotRootV1,
) -> dict[str, Any]:
    try:
        if not isinstance(expected, TrustedSnapshotRootV1):
            raise TrustedRootError("caller expected snapshot root is required")
        root = _require_directory(Path(snapshot_root), "trusted snapshot root")
        if root.name != expected.directory_id:
            raise TrustedRootError("trusted snapshot directory identity mismatch")
        manifest, manifest_raw = _metadata(
            root, "clean-snapshot-manifest.json", _SNAPSHOT_MANIFEST_KEYS, "snapshot manifest"
        )
        receipt, receipt_raw = _metadata(
            root, "clean-snapshot-receipt.json", _SNAPSHOT_RECEIPT_KEYS, "snapshot receipt"
        )
        audit, audit_raw = _metadata(
            root, "contamination-audit.json", _SNAPSHOT_AUDIT_KEYS, "snapshot audit"
        )
        if (
            not hmac.compare_digest(_sha_bytes(manifest_raw), expected.manifest_file_sha256)
            or manifest.get("manifest_canonical_sha256") != expected.manifest_canonical_sha256
            or manifest.get("manifest_content_canonical_sha256")
            != expected.manifest_content_canonical_sha256
            or not hmac.compare_digest(_sha_bytes(receipt_raw), expected.receipt_file_sha256)
            or receipt.get("receipt_canonical_sha256") != expected.receipt_canonical_sha256
            or receipt.get("receipt_identity_canonical_sha256")
            != expected.receipt_identity_canonical_sha256
            or not hmac.compare_digest(_sha_bytes(audit_raw), expected.audit_file_sha256)
            or audit.get("audit_canonical_sha256") != expected.audit_canonical_sha256
            or manifest.get("snapshot_id") != expected.directory_id
            or receipt.get("snapshot_id") != expected.directory_id
            or manifest.get("created_at_utc") != expected.created_at_utc
            or manifest.get("source_files_root_sha256") != expected.source_files_root_sha256
            or audit.get("frozen_source_tree_sha256") != expected.origin_source_tree_sha256
            or audit.get("frozen_content_manifest_sha256")
            != expected.origin_content_manifest_sha256
            or manifest.get("source_lineage", {}).get("import_receipt", {}).get("canonical_sha256")
            != expected.origin_import_receipt_canonical_sha256
        ):
            raise TrustedRootError("trusted snapshot external tuple mismatch")
        _verify_physical_root(
            root,
            file_count=expected.payload_file_count,
            tree_sha=expected.payload_tree_sha256,
            directory_sha=expected.payload_directory_set_sha256,
        )
        verified = _verify_clean_snapshot(root)
        return {
            "schema": expected.artifact_schema,
            "snapshot_root": str(root),
            "directory_id": expected.directory_id,
            "payload_file_count": expected.payload_file_count,
            "payload_tree_sha256": expected.payload_tree_sha256,
            "root_canonical_sha256": expected.root_canonical_sha256,
            "verified": verified,
        }
    except (OfflineFixtureV2Error, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, TrustedRootError):
            raise
        raise TrustedRootError(f"trusted snapshot rejected: {exc}") from None


def verify_fixture_v2(
    fixture_root: str | Path,
    *,
    snapshot_root: str | Path,
    expected: TrustedFixtureRootV1,
    evidence_use: str,
) -> dict[str, Any]:
    try:
        if not isinstance(expected, TrustedFixtureRootV1):
            raise TrustedRootError("caller expected fixture root is required")
        if evidence_use != expected.evidence_use or evidence_use != EVIDENCE_USE:
            raise TrustedRootError("trusted fixture evidence use mismatch")
        verified_snapshot = verify_snapshot_v2(
            snapshot_root,
            expected=expected.snapshot,
        )
        root = _require_directory(Path(fixture_root), "trusted fixture root")
        if root.name != expected.fixture_id:
            raise TrustedRootError("trusted fixture directory identity mismatch")
        manifest, manifest_raw = _metadata(root, "manifest.json", _FIXTURE_MANIFEST_KEYS, "fixture manifest")
        receipt, receipt_raw = _metadata(
            root, "fixture-receipt.json", _FIXTURE_RECEIPT_KEYS, "fixture receipt"
        )
        adjudication, adjudication_raw = _metadata(
            root,
            "risk-semantic-empty-adjudication.json",
            _ADJUDICATION_KEYS,
            "fixture adjudication",
        )
        segments = manifest.get("segments")
        if not isinstance(segments, list) or len(segments) != 2:
            raise TrustedRootError("trusted fixture segments are invalid")
        snapshot = expected.snapshot
        expected_lineage = {
            "clean_snapshot_id": snapshot.directory_id,
            "clean_snapshot_manifest_canonical_sha256": snapshot.manifest_canonical_sha256,
            "clean_snapshot_manifest_file_sha256": snapshot.manifest_file_sha256,
            "clean_snapshot_receipt_canonical_sha256": snapshot.receipt_canonical_sha256,
            "clean_snapshot_receipt_file_sha256": snapshot.receipt_file_sha256,
            "clean_snapshot_audit_canonical_sha256": snapshot.audit_canonical_sha256,
            "clean_snapshot_audit_file_sha256": snapshot.audit_file_sha256,
            "clean_snapshot_tree_sha256": snapshot.payload_tree_sha256,
            "origin_source_tree_sha256": snapshot.origin_source_tree_sha256,
            "origin_content_manifest_sha256": snapshot.origin_content_manifest_sha256,
            "origin_import_receipt_canonical_sha256": snapshot.origin_import_receipt_canonical_sha256,
        }
        if (
            not hmac.compare_digest(_sha_bytes(manifest_raw), expected.manifest_file_sha256)
            or manifest.get("canonical_sha256") != expected.manifest_canonical_sha256
            or manifest.get("content_canonical_sha256")
            != expected.manifest_content_canonical_sha256
            or not hmac.compare_digest(_sha_bytes(receipt_raw), expected.receipt_file_sha256)
            or receipt.get("receipt_canonical_sha256") != expected.receipt_canonical_sha256
            or not hmac.compare_digest(
                _sha_bytes(adjudication_raw), expected.adjudication_file_sha256
            )
            or adjudication.get("adjudication_canonical_sha256")
            != expected.adjudication_canonical_sha256
            or manifest.get("fixture_id") != expected.fixture_id
            or manifest.get("as_of") != expected.as_of
            or manifest.get("evidence_use") != expected.evidence_use
            or manifest.get("declared_files_root_sha256")
            != expected.declared_files_root_sha256
            or manifest.get("calls_root_sha256") != expected.calls_root_sha256
            or segments[0].get("calls_root_sha256")
            != expected.source_segment_calls_root_sha256
            or segments[1].get("calls_root_sha256")
            != expected.risk_segment_calls_root_sha256
            or receipt.get("call_count") != expected.call_count
            or receipt.get("physical_raw_file_count") != expected.physical_raw_file_count
            or manifest.get("source_lineage") != expected_lineage
            or adjudication.get("invalid_run_id") != expected.invalid_run_id
            or adjudication.get("invalid_run_tree_sha256") != expected.invalid_run_tree_sha256
            or adjudication.get("invalid_audit_file_sha256")
            != expected.invalid_audit_file_sha256
            or adjudication.get("invalid_audit_canonical_sha256")
            != expected.invalid_audit_canonical_sha256
        ):
            raise TrustedRootError("trusted fixture external tuple mismatch")
        _verify_physical_root(
            root,
            file_count=expected.payload_file_count,
            tree_sha=expected.payload_tree_sha256,
            directory_sha=expected.payload_directory_set_sha256,
        )
        verified = preflight_current_pool_offline_fixture_v2(
            fixture_root=root,
            expected_manifest_sha256=expected.manifest_file_sha256,
            evidence_use=evidence_use,
        )
        return {
            **verified,
            "physical_raw_file_count": expected.physical_raw_file_count,
            "payload_tree_sha256": expected.payload_tree_sha256,
            "root_canonical_sha256": expected.root_canonical_sha256,
            "snapshot_root_canonical_sha256": verified_snapshot[
                "root_canonical_sha256"
            ],
        }
    except (OfflineFixtureV2Error, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, TrustedRootError):
            raise
        raise TrustedRootError(f"trusted fixture rejected: {exc}") from None
