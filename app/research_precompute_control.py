"""Fail-closed control bindings for one registered historical precompute run."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.durable_io import fsync_directory
from app import research_validation
from app.research_plan_publication import verify_published_treatment_plan_v1
from app.research_control_quarantine import (
    quarantine_binding_sha256_v1,
    validate_frozen_quarantine_binding_v1,
)
from app.research_plan_publication_v2 import (
    verify_published_treatment_plan_v2,
    verify_published_treatment_plan_v3,
    verify_published_treatment_plan_v4,
)
from app.research_supervised_launcher import build_e_only_environment, run_supervised


class PrecomputeControlError(RuntimeError):
    pass


_CONTROL_SCHEMA = "research-precompute-control-binding/v1"
_CONTROL_SCHEMA_V2 = "research-precompute-control-binding/v2"
_PARENT_PROOF_SCHEMA = "research-plan-publication-parent-proof/v1"
_CLAIM_SCHEMA = "research-precompute-run-claim/v1"
_CLAIM_SCHEMA_V2 = "research-precompute-run-claim/v2"
_LEDGER_BINDING_SCHEMA = "research-precompute-ledger-binding/v1"
_LEDGER_BINDING_SCHEMA_V2 = "research-precompute-ledger-binding/v2"
_LEDGER_BINDING_SCHEMA_V3 = "research-precompute-ledger-binding/v3"
_LEDGER_BINDING_SCHEMA_V4 = "research-precompute-ledger-binding/v4"
_LAUNCH_LEASE_SCHEMA = "research-precompute-launch-lease/v1"
_RUN_RESULT_SCHEMA_V2 = "research-precompute-run-result/v2"
_RUN_RESULT_SCHEMA_V3 = "research-precompute-run-result/v3"
_RUN_RESULT_SCHEMA_V4 = "research-precompute-run-result/v4"
_LOWER_HEX = frozenset("0123456789abcdef")
_CONTROL_FIELDS = {
    "schema_version",
    "publication_root",
    "audit_root",
    "publication_id",
    "prepublish_evidence_file_sha256",
    "publication_result_file_sha256",
    "writer_claim_file_sha256",
    "plan_sha256",
    "plan_file_sha256",
    "fixture_id",
    "fixture_manifest_file_sha256",
    "completion_authorization_sha256",
    "control_source_bundle_sha256",
    "minimum_registration_sequence_exclusive",
    "verified",
}
_CONTROL_FIELDS_V2 = {
    *_CONTROL_FIELDS,
    "parent_proof_path",
    "parent_proof_file_sha256",
    "parent_proof_canonical_sha256",
}
_CONTROL_HASH_FIELDS = {
    "publication_id",
    "prepublish_evidence_file_sha256",
    "publication_result_file_sha256",
    "writer_claim_file_sha256",
    "plan_sha256",
    "plan_file_sha256",
    "fixture_id",
    "fixture_manifest_file_sha256",
    "completion_authorization_sha256",
    "control_source_bundle_sha256",
}
_CONTROL_SOURCE_PATHS = (
    "app/jobs.py",
    "app/research_plan_publication.py",
    "app/research_precompute_control.py",
    "app/research_precompute_parent.py",
    "app/research_supervised_launcher.py",
    "app/research_validation.py",
)
_CONTROL_SOURCE_PATHS_V2 = (
    *_CONTROL_SOURCE_PATHS,
    "app/research_control_successor_v3.py",
    "app/research_plan_publication_v2.py",
    "app/durable_io.py",
    "app/research_partitions.py",
)
_CONTROL_SOURCE_PATHS_V3 = (
    *_CONTROL_SOURCE_PATHS_V2,
    "app/research_control_successor_v4.py",
    "app/research_control_quarantine.py",
    "app/research_launcher_probe.py",
    "app/research_launcher_ack.py",
)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWER_HEX for character in value)
    )


def _strict_object(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise PrecomputeControlError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PrecomputeControlError(f"{label} contains a non-finite value: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrecomputeControlError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PrecomputeControlError(f"{label} must be an object")
    return payload


def _validated_control(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PrecomputeControlError("precompute control binding is invalid")
    payload = dict(value)
    schema = payload.get("schema_version")
    expected_fields = (
        _CONTROL_FIELDS_V2 if schema == _CONTROL_SCHEMA_V2 else _CONTROL_FIELDS
    )
    if set(payload) != expected_fields:
        raise PrecomputeControlError("precompute control binding fields are invalid")
    if schema not in {_CONTROL_SCHEMA, _CONTROL_SCHEMA_V2} or payload.get(
        "verified"
    ) is not True:
        raise PrecomputeControlError("precompute control binding is not verified")
    for field in _CONTROL_HASH_FIELDS:
        if not _is_sha256(payload.get(field)):
            raise PrecomputeControlError("precompute control binding hash is invalid")
    minimum_sequence = payload.get("minimum_registration_sequence_exclusive")
    if (
        isinstance(minimum_sequence, bool)
        or not isinstance(minimum_sequence, int)
        or minimum_sequence < 126
    ):
        raise PrecomputeControlError("precompute control sequence floor is invalid")
    for field in ("publication_root", "audit_root"):
        path = Path(str(payload.get(field) or ""))
        if not path.is_absolute() or path.drive.casefold() != "e:":
            raise PrecomputeControlError("precompute control path is outside E drive")
    if Path(payload["publication_root"]).name != payload["publication_id"]:
        raise PrecomputeControlError("precompute control publication identity mismatch")
    if schema == _CONTROL_SCHEMA_V2:
        proof_path = Path(str(payload.get("parent_proof_path") or ""))
        if not proof_path.is_absolute() or proof_path.drive.casefold() != "e:":
            raise PrecomputeControlError("precompute parent proof path is outside E drive")
        if (
            not _is_sha256(payload.get("parent_proof_file_sha256"))
            or not _is_sha256(payload.get("parent_proof_canonical_sha256"))
        ):
            raise PrecomputeControlError("precompute parent proof hash is invalid")
    return payload


def _parent_proof_body(control: Mapping[str, Any]) -> dict[str, Any]:
    verified = _validated_control(control)
    if verified["schema_version"] != _CONTROL_SCHEMA:
        raise PrecomputeControlError("precompute parent proof requires v1 control")
    return {
        "schema_version": _PARENT_PROOF_SCHEMA,
        "verification_mode": "parent_secret_verified_child_secret_free",
        "publication_root": verified["publication_root"],
        "audit_root": verified["audit_root"],
        "publication_id": verified["publication_id"],
        "prepublish_evidence_file_sha256": verified[
            "prepublish_evidence_file_sha256"
        ],
        "publication_result_file_sha256": verified[
            "publication_result_file_sha256"
        ],
        "writer_claim_file_sha256": verified["writer_claim_file_sha256"],
        "plan_sha256": verified["plan_sha256"],
        "plan_file_sha256": verified["plan_file_sha256"],
        "fixture_id": verified["fixture_id"],
        "fixture_manifest_file_sha256": verified[
            "fixture_manifest_file_sha256"
        ],
        "completion_authorization_sha256": verified[
            "completion_authorization_sha256"
        ],
        "control_source_bundle_sha256": verified[
            "control_source_bundle_sha256"
        ],
        "minimum_registration_sequence_exclusive": verified[
            "minimum_registration_sequence_exclusive"
        ],
        "authorization_forwarded_to_child": False,
        "verified": True,
    }


def _publish_precompute_parent_proof_from_verified_control(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    path = Path(proof_path)
    if (
        not path.is_absolute()
        or path.resolve(strict=False) != path
        or path.name != "parent-publication-proof.json"
    ):
        raise PrecomputeControlError("precompute parent proof path is invalid")
    parent = _safe_directory(path.parent, workspace_root=workspace)
    body = _parent_proof_body(verified_control)
    canonical_sha256 = _sha256_bytes(_canonical_bytes(body))
    payload = {**body, "proof_canonical_sha256": canonical_sha256}
    raw = _canonical_bytes(payload) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PrecomputeControlError("precompute parent proof already exists") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(parent)
    file_sha256 = _sha256_bytes(raw)
    control_v1 = _validated_control(verified_control)
    control_v2 = _validated_control(
        {
            **control_v1,
            "schema_version": _CONTROL_SCHEMA_V2,
            "parent_proof_path": str(path),
            "parent_proof_file_sha256": file_sha256,
            "parent_proof_canonical_sha256": canonical_sha256,
        }
    )
    return {
        "path": path,
        "sha256": file_sha256,
        "bytes": len(raw),
        "canonical_sha256": canonical_sha256,
        "payload": payload,
        "verified_control": control_v2,
    }


def publish_precompute_parent_proof_v1(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    verified = verify_precompute_publication_v1(
        workspace_root=workspace_root,
        publication_root=publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        completion_authorization=completion_authorization,
        expected_fixture_id=expected_fixture_id,
        expected_fixture_manifest_file_sha256=(
            expected_fixture_manifest_file_sha256
        ),
        expected_control_source_bundle_sha256=(
            expected_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            minimum_registration_sequence_exclusive
        ),
    )
    return _publish_precompute_parent_proof_from_verified_control(
        proof_path,
        workspace_root=workspace_root,
        verified_control=verified,
    )


def publish_precompute_parent_proof_v2(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    verified = verify_precompute_publication_v2(
        workspace_root=workspace_root,
        publication_root=publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        completion_authorization=completion_authorization,
        expected_fixture_id=expected_fixture_id,
        expected_fixture_manifest_file_sha256=(
            expected_fixture_manifest_file_sha256
        ),
        expected_control_source_bundle_sha256=(
            expected_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            minimum_registration_sequence_exclusive
        ),
    )
    return _publish_precompute_parent_proof_from_verified_control(
        proof_path,
        workspace_root=workspace_root,
        verified_control=verified,
    )


def publish_precompute_parent_proof_v3(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    verified = verify_precompute_publication_v3(
        workspace_root=workspace_root,
        publication_root=publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        completion_authorization=completion_authorization,
        expected_fixture_id=expected_fixture_id,
        expected_fixture_manifest_file_sha256=(
            expected_fixture_manifest_file_sha256
        ),
        expected_control_source_bundle_sha256=(
            expected_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            minimum_registration_sequence_exclusive
        ),
    )
    return _publish_precompute_parent_proof_from_verified_control(
        proof_path,
        workspace_root=workspace_root,
        verified_control=verified,
    )


def publish_precompute_parent_proof_v4(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    verified = verify_precompute_publication_v4(
        workspace_root=workspace_root,
        publication_root=publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        completion_authorization=completion_authorization,
        expected_fixture_id=expected_fixture_id,
        expected_fixture_manifest_file_sha256=(
            expected_fixture_manifest_file_sha256
        ),
        expected_control_source_bundle_sha256=(
            expected_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            minimum_registration_sequence_exclusive
        ),
    )
    return _publish_precompute_parent_proof_from_verified_control(
        proof_path,
        workspace_root=workspace_root,
        verified_control=verified,
    )


def load_precompute_parent_proof_v1(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_file_sha256: str,
    expected_canonical_sha256: str,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    path = _safe_regular_file(Path(proof_path), workspace_root=workspace)
    raw = path.read_bytes()
    payload = _strict_object(raw, "precompute parent proof")
    body = dict(payload)
    claimed_canonical = body.pop("proof_canonical_sha256", None)
    if (
        _sha256_bytes(raw) != expected_file_sha256
        or raw != _canonical_bytes(payload) + b"\n"
        or claimed_canonical != _sha256_bytes(_canonical_bytes(body))
        or claimed_canonical != expected_canonical_sha256
    ):
        raise PrecomputeControlError("precompute parent proof binding mismatch")
    proof_keys = {
        "schema_version",
        "verification_mode",
        "publication_root",
        "audit_root",
        "publication_id",
        "prepublish_evidence_file_sha256",
        "publication_result_file_sha256",
        "writer_claim_file_sha256",
        "plan_sha256",
        "plan_file_sha256",
        "fixture_id",
        "fixture_manifest_file_sha256",
        "completion_authorization_sha256",
        "control_source_bundle_sha256",
        "minimum_registration_sequence_exclusive",
        "authorization_forwarded_to_child",
        "verified",
    }
    if (
        set(body) != proof_keys
        or body.get("schema_version") != _PARENT_PROOF_SCHEMA
        or body.get("verification_mode")
        != "parent_secret_verified_child_secret_free"
        or body.get("authorization_forwarded_to_child") is not False
        or body.get("verified") is not True
    ):
        raise PrecomputeControlError("precompute parent proof binding mismatch")
    base_control = {
        "schema_version": _CONTROL_SCHEMA,
        **{
            key: value
            for key, value in body.items()
            if key
            not in {
                "schema_version",
                "verification_mode",
                "authorization_forwarded_to_child",
            }
        },
    }
    control_v1 = _validated_control(base_control)
    control_v2 = _validated_control(
        {
            **control_v1,
            "schema_version": _CONTROL_SCHEMA_V2,
            "parent_proof_path": str(path),
            "parent_proof_file_sha256": expected_file_sha256,
            "parent_proof_canonical_sha256": expected_canonical_sha256,
        }
    )
    return {
        "path": path,
        "sha256": expected_file_sha256,
        "bytes": len(raw),
        "canonical_sha256": expected_canonical_sha256,
        "payload": payload,
        "verified_control": control_v2,
    }


def verify_precompute_parent_proof_v1(
    proof_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_file_sha256: str,
    expected_canonical_sha256: str,
    expected_verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    loaded = load_precompute_parent_proof_v1(
        proof_path,
        workspace_root=workspace_root,
        expected_file_sha256=expected_file_sha256,
        expected_canonical_sha256=expected_canonical_sha256,
    )
    control_v2 = _validated_control(expected_verified_control)
    if (
        control_v2["schema_version"] != _CONTROL_SCHEMA_V2
        or loaded["verified_control"] != control_v2
    ):
        raise PrecomputeControlError("precompute parent proof binding mismatch")
    return loaded


def precompute_control_source_bundle_v1(
    workspace_root: str | Path,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    files = []
    for relative_path in _CONTROL_SOURCE_PATHS:
        path = workspace / Path(relative_path)
        raw = _stable_control_source_bytes(path)
        files.append(
            {
                "path": relative_path,
                "bytes": len(raw),
                "sha256": _sha256_bytes(raw),
            }
        )
    body = {
        "schema_version": "research-precompute-control-source-bundle/v1",
        "files": files,
    }
    return {**body, "root_sha256": _sha256_bytes(_canonical_bytes(body))}


def precompute_control_source_bundle_v2(
    workspace_root: str | Path,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    files = []
    for relative_path in _CONTROL_SOURCE_PATHS_V2:
        path = workspace / Path(relative_path)
        raw = _stable_control_source_bytes(path)
        files.append(
            {
                "path": relative_path,
                "bytes": len(raw),
                "sha256": _sha256_bytes(raw),
            }
        )
    body = {
        "schema_version": "research-precompute-control-source-bundle/v2",
        "files": files,
    }
    return {**body, "root_sha256": _sha256_bytes(_canonical_bytes(body))}


def precompute_control_source_bundle_v3(
    workspace_root: str | Path,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    files = []
    for relative_path in _CONTROL_SOURCE_PATHS_V3:
        path = workspace / Path(relative_path)
        raw = _stable_control_source_bytes(path)
        files.append(
            {
                "path": relative_path,
                "bytes": len(raw),
                "sha256": _sha256_bytes(raw),
            }
        )
    body = {
        "schema_version": "research-precompute-control-source-bundle/v3",
        "files": files,
    }
    return {**body, "root_sha256": _sha256_bytes(_canonical_bytes(body))}


def _stable_control_source_bytes(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PrecomputeControlError("precompute control source is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute control source is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PrecomputeControlError("precompute control source is unsafe")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        named = path.stat()
        def identity(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
            )
        if (
            identity(before) != identity(after)
            or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
            or after.st_nlink != 1
            or named.st_nlink != 1
        ):
            raise PrecomputeControlError("precompute control source changed while read")
        raw = b"".join(chunks)
        if len(raw) != after.st_size:
            raise PrecomputeControlError("precompute control source changed while read")
        return raw
    finally:
        os.close(descriptor)


def verify_precompute_publication_v1(
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    for value in (
        expected_publication_id,
        expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256,
        expected_result_file_sha256,
        expected_writer_claim_file_sha256,
        expected_fixture_id,
        expected_fixture_manifest_file_sha256,
        expected_control_source_bundle_sha256,
    ):
        if not _is_sha256(value):
            raise PrecomputeControlError("precompute publication expected hash is invalid")
    if (
        isinstance(minimum_registration_sequence_exclusive, bool)
        or not isinstance(minimum_registration_sequence_exclusive, int)
        or minimum_registration_sequence_exclusive < 126
    ):
        raise PrecomputeControlError("precompute publication sequence floor is invalid")
    try:
        authorization_raw = completion_authorization.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PrecomputeControlError("precompute publication authorization is invalid") from exc

    workspace = _safe_directory(Path(workspace_root))
    publication = _safe_directory(Path(publication_root), workspace_root=workspace)
    audit = _safe_directory(Path(audit_root), workspace_root=workspace)
    verified = verify_published_treatment_plan_v1(
        publication,
        audit_root=audit,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=completion_authorization,
    )
    if (
        verified.get("verified") is not True
        or verified.get("publication_id") != expected_publication_id
        or verified.get("plan_file_sha256") != expected_plan_file_sha256
        or verified.get("result_file_sha256") != expected_result_file_sha256
    ):
        raise PrecomputeControlError("precompute publication verifier result mismatch")

    plan_path = publication / "treatment-plan.json"
    try:
        metadata = plan_path.lstat()
    except OSError as exc:
        raise PrecomputeControlError("precompute published plan is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        plan_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute published plan is unsafe")
    plan_raw = plan_path.read_bytes()
    if _sha256_bytes(plan_raw) != expected_plan_file_sha256:
        raise PrecomputeControlError("precompute published plan file SHA mismatch")
    plan = _strict_object(plan_raw, "precompute published plan")
    if plan_raw != _canonical_bytes(plan) + b"\n":
        raise PrecomputeControlError("precompute published plan serialization is invalid")
    fixture = plan.get("development_payload_fixture")
    if (
        plan.get("schema_version") != "research-treatment-input-plan/v4"
        or plan.get("plan_sha256") != verified.get("plan_sha256")
        or not isinstance(fixture, dict)
        or fixture.get("fixture_id") != expected_fixture_id
        or fixture.get("manifest_file_sha256")
        != expected_fixture_manifest_file_sha256
    ):
        raise PrecomputeControlError("precompute publication fixture binding mismatch")
    bundle = precompute_control_source_bundle_v1(workspace)
    if bundle.get("root_sha256") != expected_control_source_bundle_sha256:
        raise PrecomputeControlError("precompute control source bundle mismatch")
    return _validated_control(
        {
            "schema_version": _CONTROL_SCHEMA,
            "publication_root": str(publication),
            "audit_root": str(audit),
            "publication_id": expected_publication_id,
            "prepublish_evidence_file_sha256": (
                expected_prepublish_evidence_file_sha256
            ),
            "publication_result_file_sha256": expected_result_file_sha256,
            "writer_claim_file_sha256": expected_writer_claim_file_sha256,
            "plan_sha256": verified["plan_sha256"],
            "plan_file_sha256": expected_plan_file_sha256,
            "fixture_id": expected_fixture_id,
            "fixture_manifest_file_sha256": expected_fixture_manifest_file_sha256,
            "completion_authorization_sha256": _sha256_bytes(authorization_raw),
            "control_source_bundle_sha256": expected_control_source_bundle_sha256,
            "minimum_registration_sequence_exclusive": (
                minimum_registration_sequence_exclusive
            ),
            "verified": True,
        }
    )


def verify_precompute_publication_v2(
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    for value in (
        expected_publication_id,
        expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256,
        expected_result_file_sha256,
        expected_writer_claim_file_sha256,
        expected_fixture_id,
        expected_fixture_manifest_file_sha256,
        expected_control_source_bundle_sha256,
    ):
        if not _is_sha256(value):
            raise PrecomputeControlError("precompute publication expected hash is invalid")
    if (
        isinstance(minimum_registration_sequence_exclusive, bool)
        or not isinstance(minimum_registration_sequence_exclusive, int)
        or minimum_registration_sequence_exclusive < 126
    ):
        raise PrecomputeControlError("precompute publication sequence floor is invalid")
    try:
        authorization_raw = completion_authorization.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PrecomputeControlError("precompute publication authorization is invalid") from exc

    workspace = _safe_directory(Path(workspace_root))
    publication = _safe_directory(Path(publication_root), workspace_root=workspace)
    audit = _safe_directory(Path(audit_root), workspace_root=workspace)
    verified = verify_published_treatment_plan_v2(
        publication,
        audit_root=audit,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=completion_authorization,
    )
    if (
        verified.get("verified") is not True
        or verified.get("publication_id") != expected_publication_id
        or verified.get("plan_file_sha256") != expected_plan_file_sha256
        or verified.get("result_file_sha256") != expected_result_file_sha256
    ):
        raise PrecomputeControlError("precompute publication verifier result mismatch")

    plan_path = publication / "treatment-plan.json"
    try:
        metadata = plan_path.lstat()
    except OSError as exc:
        raise PrecomputeControlError("precompute published plan is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        plan_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute published plan is unsafe")
    plan_raw = plan_path.read_bytes()
    if _sha256_bytes(plan_raw) != expected_plan_file_sha256:
        raise PrecomputeControlError("precompute published plan file SHA mismatch")
    plan = _strict_object(plan_raw, "precompute published plan")
    if plan_raw != _canonical_bytes(plan) + b"\n":
        raise PrecomputeControlError("precompute published plan serialization is invalid")
    fixture = plan.get("development_payload_fixture")
    if (
        plan.get("schema_version") != "research-treatment-input-plan/v5"
        or plan.get("plan_sha256") != verified.get("plan_sha256")
        or not isinstance(fixture, dict)
        or fixture.get("fixture_id") != expected_fixture_id
        or fixture.get("manifest_file_sha256")
        != expected_fixture_manifest_file_sha256
    ):
        raise PrecomputeControlError("precompute publication fixture binding mismatch")
    bundle = precompute_control_source_bundle_v2(workspace)
    if bundle.get("root_sha256") != expected_control_source_bundle_sha256:
        raise PrecomputeControlError("precompute control source bundle mismatch")
    return _validated_control(
        {
            "schema_version": _CONTROL_SCHEMA,
            "publication_root": str(publication),
            "audit_root": str(audit),
            "publication_id": expected_publication_id,
            "prepublish_evidence_file_sha256": (
                expected_prepublish_evidence_file_sha256
            ),
            "publication_result_file_sha256": expected_result_file_sha256,
            "writer_claim_file_sha256": expected_writer_claim_file_sha256,
            "plan_sha256": verified["plan_sha256"],
            "plan_file_sha256": expected_plan_file_sha256,
            "fixture_id": expected_fixture_id,
            "fixture_manifest_file_sha256": expected_fixture_manifest_file_sha256,
            "completion_authorization_sha256": _sha256_bytes(authorization_raw),
            "control_source_bundle_sha256": expected_control_source_bundle_sha256,
            "minimum_registration_sequence_exclusive": (
                minimum_registration_sequence_exclusive
            ),
            "verified": True,
        }
    )


def verify_precompute_publication_v3(
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
    expected_plan_schema: str = "research-treatment-input-plan/v6",
) -> dict[str, Any]:
    if expected_plan_schema not in {
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        raise PrecomputeControlError("precompute publication plan schema is invalid")
    for value in (
        expected_publication_id,
        expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256,
        expected_result_file_sha256,
        expected_writer_claim_file_sha256,
        expected_fixture_id,
        expected_fixture_manifest_file_sha256,
        expected_control_source_bundle_sha256,
    ):
        if not _is_sha256(value):
            raise PrecomputeControlError("precompute publication expected hash is invalid")
    if (
        isinstance(minimum_registration_sequence_exclusive, bool)
        or not isinstance(minimum_registration_sequence_exclusive, int)
        or minimum_registration_sequence_exclusive < 126
    ):
        raise PrecomputeControlError("precompute publication sequence floor is invalid")
    try:
        authorization_raw = completion_authorization.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PrecomputeControlError("precompute publication authorization is invalid") from exc

    workspace = _safe_directory(Path(workspace_root))
    publication = _safe_directory(Path(publication_root), workspace_root=workspace)
    audit = _safe_directory(Path(audit_root), workspace_root=workspace)
    published_verifier = (
        verify_published_treatment_plan_v4
        if expected_plan_schema == "research-treatment-input-plan/v7"
        else verify_published_treatment_plan_v3
    )
    verified = published_verifier(
        publication,
        audit_root=audit,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=completion_authorization,
    )
    if (
        verified.get("verified") is not True
        or verified.get("publication_id") != expected_publication_id
        or verified.get("plan_file_sha256") != expected_plan_file_sha256
        or verified.get("result_file_sha256") != expected_result_file_sha256
    ):
        raise PrecomputeControlError("precompute publication verifier result mismatch")

    plan_path = publication / "treatment-plan.json"
    try:
        metadata = plan_path.lstat()
    except OSError as exc:
        raise PrecomputeControlError("precompute published plan is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        plan_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute published plan is unsafe")
    plan_raw = plan_path.read_bytes()
    if _sha256_bytes(plan_raw) != expected_plan_file_sha256:
        raise PrecomputeControlError("precompute published plan file SHA mismatch")
    plan = _strict_object(plan_raw, "precompute published plan")
    if plan_raw != _canonical_bytes(plan) + b"\n":
        raise PrecomputeControlError("precompute published plan serialization is invalid")
    fixture = plan.get("development_payload_fixture")
    try:
        quarantine = validate_frozen_quarantine_binding_v1(
            plan.get("legacy_quarantine")
        )
    except ValueError as exc:
        raise PrecomputeControlError(
            "precompute publication quarantine binding is invalid"
        ) from exc
    execution = plan.get("precompute_execution")
    if (
        plan.get("schema_version") != expected_plan_schema
        or plan.get("plan_sha256") != verified.get("plan_sha256")
        or not isinstance(fixture, dict)
        or fixture.get("fixture_id") != expected_fixture_id
        or fixture.get("manifest_file_sha256")
        != expected_fixture_manifest_file_sha256
        or not isinstance(execution, dict)
        or execution.get("schema_version")
        != (
            "research-precompute-execution-plan/v4"
            if expected_plan_schema == "research-treatment-input-plan/v7"
            else "research-precompute-execution-plan/v3"
        )
        or execution.get("legacy_quarantine_sha256")
        != quarantine_binding_sha256_v1(quarantine)
    ):
        raise PrecomputeControlError("precompute publication fixture binding mismatch")
    try:
        from app import jobs

        validated, _artifact = jobs._validate_treatment_input_plan_payload(
            plan, plan_raw
        )
    except (TypeError, ValueError) as exc:
        raise PrecomputeControlError("precompute publication v3 plan is invalid") from exc
    if validated != plan:
        raise PrecomputeControlError("precompute publication v3 plan changed")
    if expected_plan_schema == "research-treatment-input-plan/v7":
        binding = plan.get("control_request_binding")
        if (
            not isinstance(binding, dict)
            or binding.get("control_source_bundle_sha256")
            != expected_control_source_bundle_sha256
            or binding.get("ledger", {}).get("expected_tip_sequence")
            != minimum_registration_sequence_exclusive
            or execution.get("control_request_binding_sha256")
            != _sha256_bytes(_canonical_bytes(binding))
        ):
            raise PrecomputeControlError("precompute publication v4 request binding mismatch")
    bundle = precompute_control_source_bundle_v3(workspace)
    if bundle.get("root_sha256") != expected_control_source_bundle_sha256:
        raise PrecomputeControlError("precompute control source bundle mismatch")
    return _validated_control(
        {
            "schema_version": _CONTROL_SCHEMA,
            "publication_root": str(publication),
            "audit_root": str(audit),
            "publication_id": expected_publication_id,
            "prepublish_evidence_file_sha256": (
                expected_prepublish_evidence_file_sha256
            ),
            "publication_result_file_sha256": expected_result_file_sha256,
            "writer_claim_file_sha256": expected_writer_claim_file_sha256,
            "plan_sha256": verified["plan_sha256"],
            "plan_file_sha256": expected_plan_file_sha256,
            "fixture_id": expected_fixture_id,
            "fixture_manifest_file_sha256": expected_fixture_manifest_file_sha256,
            "completion_authorization_sha256": _sha256_bytes(authorization_raw),
            "control_source_bundle_sha256": expected_control_source_bundle_sha256,
            "minimum_registration_sequence_exclusive": (
                minimum_registration_sequence_exclusive
            ),
            "verified": True,
        }
    )


def verify_precompute_publication_v4(
    *,
    workspace_root: str | Path,
    publication_root: str | Path,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    completion_authorization: str,
    expected_fixture_id: str,
    expected_fixture_manifest_file_sha256: str,
    expected_control_source_bundle_sha256: str,
    minimum_registration_sequence_exclusive: int,
) -> dict[str, Any]:
    return verify_precompute_publication_v3(
        workspace_root=workspace_root,
        publication_root=publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        completion_authorization=completion_authorization,
        expected_fixture_id=expected_fixture_id,
        expected_fixture_manifest_file_sha256=(
            expected_fixture_manifest_file_sha256
        ),
        expected_control_source_bundle_sha256=(
            expected_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            minimum_registration_sequence_exclusive
        ),
        expected_plan_schema="research-treatment-input-plan/v7",
    )


def _validated_registration(
    event: Mapping[str, Any], verified_control: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise PrecomputeControlError("precompute registration is invalid")
    payload = dict(event)
    if payload.get("event_type") != "registered":
        raise PrecomputeControlError("precompute registration event type is invalid")
    experiment_id = payload.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise PrecomputeControlError("precompute registration experiment id is invalid")
    artifact = payload.get("input_plan_artifact")
    if (
        not isinstance(artifact, dict)
        or artifact.get("basename") != "treatment-plan.json"
        or artifact.get("sha256") != verified_control["plan_file_sha256"]
    ):
        raise PrecomputeControlError("precompute registration plan artifact mismatch")
    contract = payload.get("registration_contract")
    contract_schema = contract.get("schema_version") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or contract_schema
        not in {
            "research-validation-registration/v2",
            "research-validation-registration/v3",
            "research-validation-registration/v4",
        }
        or contract.get("precompute_control") != verified_control
        or payload.get("registration_contract_sha256")
        != _sha256_bytes(_canonical_bytes(contract))
    ):
        raise PrecomputeControlError("precompute registration control binding mismatch")
    input_plan = contract.get("input_plan")
    plan = input_plan.get("payload") if isinstance(input_plan, dict) else None
    if contract_schema == "research-validation-registration/v3":
        lifecycle = contract.get("precompute_lifecycle")
        if (
            verified_control.get("schema_version") != _CONTROL_SCHEMA_V2
            or not isinstance(plan, dict)
            or plan.get("schema_version") != "research-treatment-input-plan/v5"
            or lifecycle
            != {
                "schema_version": "research-precompute-lifecycle/v1",
                "launch_started_event_schema": "research-precompute-launch-started/v1",
                "launcher_ready_schema": "research-launcher-ready/v4",
                "run_result_schema": "research-precompute-run-result/v3",
                "global_tip_cas": True,
                "sole_nonterminal_cas": True,
            }
        ):
            raise PrecomputeControlError("precompute registration lifecycle is invalid")
    elif contract_schema == "research-validation-registration/v4":
        try:
            research_validation._require_v4_launch_registration(payload)
        except ValueError as exc:
            raise PrecomputeControlError(
                "precompute registration lifecycle is invalid"
            ) from exc
    elif verified_control.get("schema_version") != _CONTROL_SCHEMA:
        raise PrecomputeControlError("legacy registration forbids v2 control")
    return payload


def precompute_execution_from_registration_v1(
    event: Mapping[str, Any], verified_control: Mapping[str, Any]
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    registration = _validated_registration(event, control)
    contract = registration["registration_contract"]
    input_plan = contract.get("input_plan")
    if not isinstance(input_plan, dict) or set(input_plan) != {"payload", "artifact"}:
        raise PrecomputeControlError("precompute registration plan binding is invalid")
    plan = input_plan.get("payload")
    artifact = input_plan.get("artifact")
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version")
        not in {
            "research-treatment-input-plan/v4",
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
        or plan.get("plan_sha256") != control["plan_sha256"]
        or artifact != registration.get("input_plan_artifact")
        or not isinstance(artifact, dict)
        or artifact.get("sha256") != control["plan_file_sha256"]
    ):
        raise PrecomputeControlError("precompute registration plan binding mismatch")
    try:
        from app import jobs

        validated, _descriptor = jobs._validate_treatment_input_plan_payload(
            plan, _canonical_bytes(plan)
        )
    except (TypeError, ValueError) as exc:
        raise PrecomputeControlError("precompute registration plan is invalid") from exc
    fixture = validated["development_payload_fixture"]
    execution = validated["precompute_execution"]
    expected_execution_schema = (
        "research-precompute-execution-plan/v4"
        if validated["schema_version"] == "research-treatment-input-plan/v7"
        else (
            "research-precompute-execution-plan/v3"
            if validated["schema_version"] == "research-treatment-input-plan/v6"
            else (
                "research-precompute-execution-plan/v2"
                if validated["schema_version"] == "research-treatment-input-plan/v5"
                else "research-precompute-execution-plan/v1"
            )
        )
    )
    expected_ledger_schema = (
        _LEDGER_BINDING_SCHEMA_V4
        if validated["schema_version"] == "research-treatment-input-plan/v7"
        else (
            _LEDGER_BINDING_SCHEMA_V3
            if validated["schema_version"]
            in {
                "research-treatment-input-plan/v5",
                "research-treatment-input-plan/v6",
            }
            else _LEDGER_BINDING_SCHEMA_V2
        )
    )
    ledger_binding = _registration_ledger_binding(registration)
    if (
        fixture.get("fixture_id") != control["fixture_id"]
        or fixture.get("manifest_file_sha256")
        != control["fixture_manifest_file_sha256"]
        or execution.get("workspace_root") != fixture.get("workspace_root")
        or execution.get("schema_version") != expected_execution_schema
        or execution.get("ledger_path") != ledger_binding.get("ledger_path")
        or ledger_binding.get("schema_version") != expected_ledger_schema
    ):
        raise PrecomputeControlError("precompute registration execution binding mismatch")
    if validated["schema_version"] in {
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        try:
            quarantine = validate_frozen_quarantine_binding_v1(
                registration["registration_contract"].get("legacy_quarantine")
            )
        except ValueError as exc:
            raise PrecomputeControlError(
                "precompute registration quarantine binding is invalid"
            ) from exc
        if (
            registration["registration_contract"].get("legacy_quarantine_sha256")
            != quarantine_binding_sha256_v1(quarantine)
            or execution.get("legacy_quarantine_sha256")
            != quarantine_binding_sha256_v1(quarantine)
        ):
            raise PrecomputeControlError(
                "precompute registration quarantine binding is invalid"
            )
    if validated["schema_version"] == "research-treatment-input-plan/v7":
        binding = validated.get("control_request_binding")
        if (
            not isinstance(binding, dict)
            or binding.get("control_source_bundle_sha256")
            != control.get("control_source_bundle_sha256")
            or binding.get("ledger", {}).get("path")
            != ledger_binding.get("ledger_path")
            or binding.get("ledger", {}).get("expected_tip_sequence")
            != ledger_binding.get("expected_tip_sequence")
            or binding.get("ledger", {}).get("expected_tip_record_hash")
            != ledger_binding.get("expected_tip_record_hash")
            or binding.get("ledger", {}).get("expected_lock_file_sha256")
            != ledger_binding.get("lock_file_sha256")
            or binding.get("ledger", {}).get("expected_file_sha256")
            != ledger_binding.get("pre_registration_ledger_file_sha256")
            or execution.get("control_request_binding_sha256")
            != _sha256_bytes(_canonical_bytes(binding))
        ):
            raise PrecomputeControlError("precompute registration v4 request binding mismatch")
    return dict(execution)


def register_precompute_v1(
    ledger_path: str,
    *,
    workspace_root: str | Path,
    event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    expected_sequence: int,
    expected_record_hash: str | None,
    allowed_nonterminal_records: list[dict[str, Any]],
    minimum_sequence_exclusive: int,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    registration = _validated_registration(event, control)
    if "input_plan" in registration["registration_contract"]:
        precompute_execution_from_registration_v1(registration, control)
    ledger_binding = _registration_ledger_binding(registration)
    verified_ledger_binding = _verify_registration_ledger_binding(
        ledger_path,
        workspace_root=workspace_root,
        expected=ledger_binding,
    )
    if (
        ledger_binding["expected_tip_sequence"] != expected_sequence
        or ledger_binding["expected_tip_record_hash"] != expected_record_hash
    ):
        raise PrecomputeControlError("precompute registration ledger tip mismatch")
    if minimum_sequence_exclusive != control["minimum_registration_sequence_exclusive"]:
        raise PrecomputeControlError("precompute registration sequence floor mismatch")
    expected_ledger_identity = None
    expected_lock_identity = None
    expected_lock_file_sha256 = None
    if verified_ledger_binding["schema_version"] in {
        _LEDGER_BINDING_SCHEMA_V2,
        _LEDGER_BINDING_SCHEMA_V3,
        _LEDGER_BINDING_SCHEMA_V4,
    }:
        expected_ledger_identity = (
            verified_ledger_binding["ledger_device"],
            verified_ledger_binding["ledger_inode"],
        )
    if verified_ledger_binding["schema_version"] in {
        _LEDGER_BINDING_SCHEMA_V3,
        _LEDGER_BINDING_SCHEMA_V4,
    }:
        expected_lock_identity = (
            verified_ledger_binding["lock_device"],
            verified_ledger_binding["lock_inode"],
        )
        expected_lock_file_sha256 = verified_ledger_binding["lock_file_sha256"]
    registration_kwargs = {
        "expected_sequence": expected_sequence,
        "expected_record_hash": expected_record_hash,
        "allowed_nonterminal_records": allowed_nonterminal_records,
        "minimum_sequence_exclusive": minimum_sequence_exclusive,
        "expected_ledger_identity": expected_ledger_identity,
        "expected_lock_identity": expected_lock_identity,
        "expected_lock_file_sha256": expected_lock_file_sha256,
    }
    if verified_ledger_binding["schema_version"] == _LEDGER_BINDING_SCHEMA_V4:
        registration_kwargs["expected_ledger_file_sha256"] = verified_ledger_binding[
            "pre_registration_ledger_file_sha256"
        ]
    registered = research_validation.register_experiment_if_tip_matches(
        ledger_path, registration, **registration_kwargs
    )
    _validated_registration(registered, control)
    return registered


def _safe_directory(path: Path, *, workspace_root: Path | None = None) -> Path:
    if not path.is_absolute():
        raise PrecomputeControlError("precompute claim directory must be absolute")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PrecomputeControlError("precompute claim directory is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute claim directory is unsafe")
    if workspace_root is not None:
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise PrecomputeControlError("precompute claim directory is outside workspace") from exc
    return resolved


def _safe_planned_directory(path: Path, *, workspace_root: Path) -> Path:
    if not path.is_absolute() or path.exists():
        raise PrecomputeControlError("precompute planned directory is not fresh")
    parent = _safe_directory(path.parent, workspace_root=workspace_root)
    planned = parent / path.name
    if planned != path or planned.resolve(strict=False) != path:
        raise PrecomputeControlError("precompute planned directory is aliased")
    return planned


def _safe_regular_file(path: Path, *, workspace_root: Path) -> Path:
    if not path.is_absolute():
        raise PrecomputeControlError("precompute file path must be absolute")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PrecomputeControlError("precompute file is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        path != resolved
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeControlError("precompute file path is unsafe")
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise PrecomputeControlError("precompute file is outside workspace") from exc
    return resolved


def _ledger_path_sha256(path: Path) -> str:
    return _sha256_bytes(str(path).encode("utf-8"))


def build_precompute_ledger_binding_v1(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_sequence: int,
    expected_record_hash: str | None,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    ledger = _safe_regular_file(Path(ledger_path), workspace_root=workspace)
    if (
        isinstance(expected_sequence, bool)
        or not isinstance(expected_sequence, int)
        or expected_sequence < 0
    ):
        raise PrecomputeControlError("precompute ledger sequence is invalid")
    if expected_sequence == 0:
        if expected_record_hash is not None:
            raise PrecomputeControlError("precompute ledger tip hash is invalid")
    elif not _is_sha256(expected_record_hash):
        raise PrecomputeControlError("precompute ledger tip hash is invalid")
    return {
        "schema_version": _LEDGER_BINDING_SCHEMA,
        "ledger_path": str(ledger),
        "ledger_path_sha256": _ledger_path_sha256(ledger),
        "expected_tip_sequence": expected_sequence,
        "expected_tip_record_hash": expected_record_hash,
    }


def verify_precompute_ledger_binding_v1(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, Mapping):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    payload = dict(expected)
    if set(payload) != {
        "schema_version",
        "ledger_path",
        "ledger_path_sha256",
        "expected_tip_sequence",
        "expected_tip_record_hash",
    } or payload.get("schema_version") != _LEDGER_BINDING_SCHEMA:
        raise PrecomputeControlError("precompute ledger binding is invalid")
    actual = build_precompute_ledger_binding_v1(
        ledger_path,
        workspace_root=workspace_root,
        expected_sequence=payload.get("expected_tip_sequence"),
        expected_record_hash=payload.get("expected_tip_record_hash"),
    )
    if actual != payload:
        raise PrecomputeControlError("precompute ledger path binding mismatch")
    return actual


def _ledger_file_identity(path: Path, *, workspace_root: Path) -> tuple[Path, int, int]:
    ledger = _safe_regular_file(path, workspace_root=workspace_root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(ledger, flags)
    try:
        opened = os.fstat(descriptor)
        named = ledger.stat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or opened.st_ino == 0
        ):
            raise PrecomputeControlError("precompute ledger file identity is unsafe")
        return ledger, int(opened.st_dev), int(opened.st_ino)
    finally:
        os.close(descriptor)


def _ledger_file_sha256(path: Path, *, workspace_root: Path) -> str:
    ledger = _safe_regular_file(path, workspace_root=workspace_root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(ledger, flags)
    try:
        opened = os.fstat(descriptor)
        named = ledger.stat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or opened.st_ino == 0
        ):
            raise PrecomputeControlError("precompute ledger file identity is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or after.st_nlink != 1
            or named.st_nlink != 1
            or size != after.st_size
        ):
            raise PrecomputeControlError("precompute ledger changed while read")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _ledger_lock_identity(path: Path, *, workspace_root: Path) -> tuple[Path, int, int, str]:
    lock = _safe_regular_file(path, workspace_root=workspace_root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock, flags)
    try:
        opened = os.fstat(descriptor)
        named = lock.stat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or opened.st_ino == 0
        ):
            raise PrecomputeControlError("precompute ledger lock identity is unsafe")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or after.st_nlink != 1
            or named.st_nlink != 1
            or len(b"".join(chunks)) != after.st_size
            or after.st_size <= 0
        ):
            raise PrecomputeControlError("precompute ledger lock changed while read")
        return lock, int(opened.st_dev), int(opened.st_ino), _sha256_bytes(b"".join(chunks))
    finally:
        os.close(descriptor)


def build_precompute_ledger_binding_v2(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_sequence: int,
    expected_record_hash: str | None,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    ledger, device, inode = _ledger_file_identity(
        Path(ledger_path), workspace_root=workspace
    )
    if (
        isinstance(expected_sequence, bool)
        or not isinstance(expected_sequence, int)
        or expected_sequence < 0
    ):
        raise PrecomputeControlError("precompute ledger sequence is invalid")
    if expected_sequence == 0:
        if expected_record_hash is not None:
            raise PrecomputeControlError("precompute ledger tip hash is invalid")
    elif not _is_sha256(expected_record_hash):
        raise PrecomputeControlError("precompute ledger tip hash is invalid")
    return {
        "schema_version": _LEDGER_BINDING_SCHEMA_V2,
        "ledger_path": str(ledger),
        "ledger_path_sha256": _ledger_path_sha256(ledger),
        "ledger_device": device,
        "ledger_inode": inode,
        "expected_tip_sequence": expected_sequence,
        "expected_tip_record_hash": expected_record_hash,
    }


def build_precompute_ledger_binding_v3(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_sequence: int,
    expected_record_hash: str | None,
) -> dict[str, Any]:
    binding = build_precompute_ledger_binding_v2(
        ledger_path,
        workspace_root=workspace_root,
        expected_sequence=expected_sequence,
        expected_record_hash=expected_record_hash,
    )
    workspace = _safe_directory(Path(workspace_root))
    ledger = Path(binding["ledger_path"])
    lock, lock_device, lock_inode, lock_file_sha256 = _ledger_lock_identity(
        ledger.with_name(ledger.name + ".lock"), workspace_root=workspace
    )
    return {
        **binding,
        "schema_version": _LEDGER_BINDING_SCHEMA_V3,
        "lock_path": str(lock),
        "lock_path_sha256": _ledger_path_sha256(lock),
        "lock_device": lock_device,
        "lock_inode": lock_inode,
        "lock_file_sha256": lock_file_sha256,
    }


def build_precompute_ledger_binding_v4(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected_sequence: int,
    expected_record_hash: str | None,
) -> dict[str, Any]:
    binding = build_precompute_ledger_binding_v3(
        ledger_path,
        workspace_root=workspace_root,
        expected_sequence=expected_sequence,
        expected_record_hash=expected_record_hash,
    )
    workspace = _safe_directory(Path(workspace_root))
    return {
        **binding,
        "schema_version": _LEDGER_BINDING_SCHEMA_V4,
        "pre_registration_ledger_file_sha256": _ledger_file_sha256(
            Path(binding["ledger_path"]), workspace_root=workspace
        ),
    }


def verify_precompute_ledger_binding_v2(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, Mapping):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    payload = dict(expected)
    expected_fields_v2 = {
        "schema_version",
        "ledger_path",
        "ledger_path_sha256",
        "ledger_device",
        "ledger_inode",
        "expected_tip_sequence",
        "expected_tip_record_hash",
    }
    if (
        set(payload) != expected_fields_v2
        or payload.get("schema_version") != _LEDGER_BINDING_SCHEMA_V2
    ):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    actual = build_precompute_ledger_binding_v2(
        ledger_path,
        workspace_root=workspace_root,
        expected_sequence=payload.get("expected_tip_sequence"),
        expected_record_hash=payload.get("expected_tip_record_hash"),
    )
    if actual["ledger_path"] != payload["ledger_path"] or actual[
        "ledger_path_sha256"
    ] != payload["ledger_path_sha256"]:
        raise PrecomputeControlError("precompute ledger path binding mismatch")
    if (
        actual["ledger_device"] != payload.get("ledger_device")
        or actual["ledger_inode"] != payload.get("ledger_inode")
    ):
        raise PrecomputeControlError("precompute ledger file identity mismatch")
    if actual != payload:
        raise PrecomputeControlError("precompute ledger binding mismatch")
    return actual


def verify_precompute_ledger_binding_v3(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, Mapping):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    payload = dict(expected)
    expected_fields = {
        "schema_version",
        "ledger_path",
        "ledger_path_sha256",
        "ledger_device",
        "ledger_inode",
        "lock_path",
        "lock_path_sha256",
        "lock_device",
        "lock_inode",
        "lock_file_sha256",
        "expected_tip_sequence",
        "expected_tip_record_hash",
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != _LEDGER_BINDING_SCHEMA_V3
    ):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    actual = build_precompute_ledger_binding_v3(
        ledger_path,
        workspace_root=workspace_root,
        expected_sequence=payload.get("expected_tip_sequence"),
        expected_record_hash=payload.get("expected_tip_record_hash"),
    )
    if actual != payload:
        raise PrecomputeControlError("precompute ledger binding mismatch")
    return actual


def verify_precompute_ledger_binding_v4(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, Mapping):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    payload = dict(expected)
    expected_fields = {
        "schema_version",
        "ledger_path",
        "ledger_path_sha256",
        "ledger_device",
        "ledger_inode",
        "lock_path",
        "lock_path_sha256",
        "lock_device",
        "lock_inode",
        "lock_file_sha256",
        "pre_registration_ledger_file_sha256",
        "expected_tip_sequence",
        "expected_tip_record_hash",
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema_version") != _LEDGER_BINDING_SCHEMA_V4
        or not _is_sha256(payload.get("pre_registration_ledger_file_sha256"))
    ):
        raise PrecomputeControlError("precompute ledger binding is invalid")
    v3_expected = dict(payload)
    v3_expected["schema_version"] = _LEDGER_BINDING_SCHEMA_V3
    v3_expected.pop("pre_registration_ledger_file_sha256")
    verify_precompute_ledger_binding_v3(
        ledger_path, workspace_root=workspace_root, expected=v3_expected
    )
    return payload


def _registration_ledger_binding(event: Mapping[str, Any]) -> dict[str, Any]:
    contract = event.get("registration_contract")
    binding = contract.get("precompute_ledger") if isinstance(contract, dict) else None
    if not isinstance(binding, dict):
        raise PrecomputeControlError("precompute registration ledger binding is missing")
    return dict(binding)


def _verify_registration_ledger_binding(
    ledger_path: str | Path,
    *,
    workspace_root: str | Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    schema = expected.get("schema_version") if isinstance(expected, Mapping) else None
    if schema == _LEDGER_BINDING_SCHEMA_V4:
        return verify_precompute_ledger_binding_v4(
            ledger_path, workspace_root=workspace_root, expected=expected
        )
    if schema == _LEDGER_BINDING_SCHEMA_V3:
        return verify_precompute_ledger_binding_v3(
            ledger_path, workspace_root=workspace_root, expected=expected
        )
    if schema == _LEDGER_BINDING_SCHEMA_V2:
        return verify_precompute_ledger_binding_v2(
            ledger_path, workspace_root=workspace_root, expected=expected
        )
    if schema == _LEDGER_BINDING_SCHEMA:
        return verify_precompute_ledger_binding_v1(
            ledger_path, workspace_root=workspace_root, expected=expected
        )
    raise PrecomputeControlError("precompute registration ledger binding is invalid")


def _claim_body(
    registered_event: Mapping[str, Any], verified_control: Mapping[str, Any]
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    contract = registered_event.get("registration_contract")
    v4_registration = isinstance(contract, Mapping) and contract.get(
        "schema_version"
    ) == "research-validation-registration/v4"
    return {
        "schema_version": (
            _CLAIM_SCHEMA_V2
            if control["schema_version"] == _CONTROL_SCHEMA_V2
            else _CLAIM_SCHEMA
        ),
        "experiment_id": registered_event["experiment_id"],
        "registered_record_hash": registered_event["record_hash"],
        "registered_sequence": registered_event["sequence"],
        "registration_contract_sha256": registered_event[
            "registration_contract_sha256"
        ],
        "publication_id": control["publication_id"],
        "prepublish_evidence_file_sha256": control[
            "prepublish_evidence_file_sha256"
        ],
        "publication_result_file_sha256": control[
            "publication_result_file_sha256"
        ],
        "writer_claim_file_sha256": control["writer_claim_file_sha256"],
        "plan_sha256": control["plan_sha256"],
        "plan_file_sha256": control["plan_file_sha256"],
        "fixture_id": control["fixture_id"],
        "fixture_manifest_file_sha256": control[
            "fixture_manifest_file_sha256"
        ],
        "control_source_bundle_sha256": control[
            "control_source_bundle_sha256"
        ],
        "ledger_path_sha256": _registration_ledger_binding(registered_event)[
            "ledger_path_sha256"
        ],
        "single_use": True,
        "run_attempt": 1,
        **(
            {
                "parent_proof_file_sha256": control[
                    "parent_proof_file_sha256"
                ],
                "parent_proof_canonical_sha256": control[
                    "parent_proof_canonical_sha256"
                ],
            }
            if control["schema_version"] == _CONTROL_SCHEMA_V2
            else {}
        ),
        **(
            {"legacy_quarantine_sha256": contract["legacy_quarantine_sha256"]}
            if v4_registration
            else {}
        ),
    }


def _launch_lease_body(
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    *,
    run_claim_file_sha256: str,
) -> dict[str, Any]:
    contract = registered_event.get("registration_contract")
    v4_registration = isinstance(contract, Mapping) and contract.get(
        "schema_version"
    ) == "research-validation-registration/v4"
    return {
        "schema_version": _LAUNCH_LEASE_SCHEMA,
        "experiment_id": registered_event["experiment_id"],
        "registered_record_hash": registered_event["record_hash"],
        "registered_sequence": registered_event["sequence"],
        "ledger_path_sha256": _registration_ledger_binding(registered_event)[
            "ledger_path_sha256"
        ],
        "run_claim_file_sha256": run_claim_file_sha256,
        "control_source_bundle_sha256": verified_control[
            "control_source_bundle_sha256"
        ],
        "single_launch": True,
        "launch_attempt": 1,
        **(
            {"legacy_quarantine_sha256": contract["legacy_quarantine_sha256"]}
            if v4_registration
            else {}
        ),
    }


def precompute_run_claim_identity_v1(
    claim_parent: str | Path,
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    registered = _validated_registration(registered_event, control)
    sequence = registered.get("sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= control["minimum_registration_sequence_exclusive"]
        or not _is_sha256(registered.get("record_hash"))
    ):
        raise PrecomputeControlError("precompute registered record is invalid")
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    parent = _safe_directory(Path(claim_parent), workspace_root=workspace)
    path = parent / f"{registered['record_hash']}.json"
    body = _claim_body(registered, control)
    payload = {
        **body,
        "claim_canonical_sha256": _sha256_bytes(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    return {
        "path": path,
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "payload": payload,
        "raw": raw,
    }


def planned_precompute_run_claim_identity_v2(
    claim_parent: str | Path,
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    if control["schema_version"] != _CONTROL_SCHEMA_V2:
        raise PrecomputeControlError("planned claim identity requires v2 control")
    registered = _validated_registration(registered_event, control)
    sequence = registered.get("sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= control["minimum_registration_sequence_exclusive"]
        or not _is_sha256(registered.get("record_hash"))
    ):
        raise PrecomputeControlError("precompute registered record is invalid")
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    parent = _safe_planned_directory(Path(claim_parent), workspace_root=workspace)
    body = _claim_body(registered, control)
    payload = {
        **body,
        "claim_canonical_sha256": _sha256_bytes(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    return {
        "path": parent / f"{registered['record_hash']}.json",
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "payload": payload,
        "raw": raw,
    }


def expected_precompute_run_claim_identity_v2(
    claim_parent: str | Path,
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    parent = Path(claim_parent)
    if parent.exists():
        return precompute_run_claim_identity_v1(
            parent,
            workspace_root=workspace_root,
            registered_event=registered_event,
            verified_control=verified_control,
        )
    return planned_precompute_run_claim_identity_v2(
        parent,
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
    )


def publish_precompute_run_claim_v1(
    claim_parent: str | Path,
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    expected_file_sha256: str,
) -> dict[str, Any]:
    identity = precompute_run_claim_identity_v1(
        claim_parent,
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
    )
    if identity["sha256"] != expected_file_sha256:
        raise PrecomputeControlError("precompute run claim identity mismatch")
    path = identity["path"]
    raw = identity.pop("raw")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PrecomputeControlError("precompute run claim already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise
    fsync_directory(path.parent)
    return identity


def create_precompute_run_claim_v1(
    claim_parent: str | Path,
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    identity = precompute_run_claim_identity_v1(
        claim_parent,
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
    )
    return publish_precompute_run_claim_v1(
        claim_parent,
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
        expected_file_sha256=identity["sha256"],
    )


def precompute_launch_lease_identity_from_claim_v1(
    lease_parent: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    expected_run_claim_file_sha256: str,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    registered = _validated_registration(registered_event, control)
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    parent = _safe_directory(Path(lease_parent), workspace_root=workspace)
    ledger_binding = _registration_ledger_binding(registered)
    _verify_registration_ledger_binding(
        ledger_path, workspace_root=workspace, expected=ledger_binding
    )
    if not _is_sha256(expected_run_claim_file_sha256):
        raise PrecomputeControlError("precompute run claim expected SHA is invalid")
    body = _launch_lease_body(
        registered,
        control,
        run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    payload = {
        **body,
        "lease_canonical_sha256": _sha256_bytes(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    return {
        "path": parent / f"{registered['record_hash']}.launch.json",
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "payload": payload,
    }


def planned_precompute_launch_lease_identity_v2(
    lease_parent: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    expected_run_claim_file_sha256: str,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    if control["schema_version"] != _CONTROL_SCHEMA_V2:
        raise PrecomputeControlError("planned lease identity requires v2 control")
    registered = _validated_registration(registered_event, control)
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    parent = _safe_planned_directory(Path(lease_parent), workspace_root=workspace)
    ledger_binding = _registration_ledger_binding(registered)
    _verify_registration_ledger_binding(
        ledger_path, workspace_root=workspace, expected=ledger_binding
    )
    if not _is_sha256(expected_run_claim_file_sha256):
        raise PrecomputeControlError("precompute run claim expected SHA is invalid")
    body = _launch_lease_body(
        registered,
        control,
        run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    payload = {
        **body,
        "lease_canonical_sha256": _sha256_bytes(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    return {
        "path": parent / f"{registered['record_hash']}.launch.json",
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "payload": payload,
    }


def expected_precompute_launch_lease_identity_v2(
    lease_parent: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    expected_run_claim_file_sha256: str,
) -> dict[str, Any]:
    parent = Path(lease_parent)
    if parent.exists():
        return precompute_launch_lease_identity_from_claim_v1(
            parent,
            workspace_root=workspace_root,
            ledger_path=ledger_path,
            registered_event=registered_event,
            verified_control=verified_control,
            expected_run_claim_file_sha256=expected_run_claim_file_sha256,
        )
    return planned_precompute_launch_lease_identity_v2(
        parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered_event,
        verified_control=verified_control,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )


def precompute_launch_lease_identity_v1(
    lease_parent: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
) -> dict[str, Any]:
    workspace = _safe_directory(Path(workspace_root))
    claim_path = _safe_regular_file(Path(run_claim_path), workspace_root=workspace)
    parent = _safe_directory(Path(lease_parent), workspace_root=workspace)
    if claim_path.parent != parent:
        raise PrecomputeControlError("precompute run claim parent mismatch")
    _load_claim(
        claim_path,
        expected_file_sha256=expected_run_claim_file_sha256,
        registered_event=registered_event,
        verified_control=verified_control,
    )
    return precompute_launch_lease_identity_from_claim_v1(
        lease_parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered_event,
        verified_control=verified_control,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )


def consume_precompute_launch_lease_v1(
    lease_parent: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
) -> dict[str, Any]:
    identity = precompute_launch_lease_identity_v1(
        lease_parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered_event,
        verified_control=verified_control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    path = identity["path"]
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PrecomputeControlError("precompute launch lease already consumed") from exc
    raw = _canonical_bytes(identity["payload"]) + b"\n"
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise
    fsync_directory(path.parent)
    return identity


def verify_precompute_launch_lease_v1(
    launch_lease_path: str | Path,
    *,
    workspace_root: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
    expected_launch_lease_file_sha256: str,
) -> dict[str, Any]:
    if not _is_sha256(expected_launch_lease_file_sha256):
        raise PrecomputeControlError("precompute launch lease expected SHA is invalid")
    path = Path(launch_lease_path)
    identity = precompute_launch_lease_identity_v1(
        path.parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered_event,
        verified_control=verified_control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    workspace = _safe_directory(Path(workspace_root))
    actual_path = _safe_regular_file(path, workspace_root=workspace)
    if actual_path != identity["path"]:
        raise PrecomputeControlError("precompute launch lease path mismatch")
    raw = actual_path.read_bytes()
    if (
        _sha256_bytes(raw) != expected_launch_lease_file_sha256
        or expected_launch_lease_file_sha256 != identity["sha256"]
        or raw != _canonical_bytes(identity["payload"]) + b"\n"
    ):
        raise PrecomputeControlError("precompute launch lease binding mismatch")
    return identity


def _load_claim(
    path: Path,
    *,
    expected_file_sha256: str,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> dict[str, Any]:
    if not _is_sha256(expected_file_sha256):
        raise PrecomputeControlError("precompute run claim expected SHA is invalid")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PrecomputeControlError("precompute run claim is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        or path.name != f"{registered_event['record_hash']}.json"
    ):
        raise PrecomputeControlError("precompute run claim path is unsafe")
    raw = path.read_bytes()
    if _sha256_bytes(raw) != expected_file_sha256:
        raise PrecomputeControlError("precompute run claim file SHA mismatch")
    payload = _strict_object(raw, "precompute run claim")
    if raw != _canonical_bytes(payload) + b"\n":
        raise PrecomputeControlError("precompute run claim serialization is invalid")
    body = dict(payload)
    signed_sha = body.pop("claim_canonical_sha256", None)
    if signed_sha != _sha256_bytes(_canonical_bytes(body)):
        raise PrecomputeControlError("precompute run claim canonical SHA mismatch")
    if body != _claim_body(registered_event, verified_control):
        raise PrecomputeControlError("precompute run claim binding mismatch")
    return payload


def _ready_payload_v3(
    registered: Mapping[str, Any],
    control: Mapping[str, Any],
    ledger_binding: Mapping[str, Any],
    *,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "research-launcher-ready/v3",
        "event": "READY",
        "experiment_id": registered["experiment_id"],
        "registered_record_hash": registered["record_hash"],
        "registered_sequence": registered["sequence"],
        "registration_contract_sha256": registered[
            "registration_contract_sha256"
        ],
        "publication_id": control["publication_id"],
        "publication_result_file_sha256": control[
            "publication_result_file_sha256"
        ],
        "plan_sha256": control["plan_sha256"],
        "plan_file_sha256": control["plan_file_sha256"],
        "fixture_id": control["fixture_id"],
        "fixture_manifest_file_sha256": control[
            "fixture_manifest_file_sha256"
        ],
        "run_claim_file_sha256": run_claim_file_sha256,
        "control_source_bundle_sha256": control["control_source_bundle_sha256"],
        "ledger_path_sha256": ledger_binding["ledger_path_sha256"],
        "launch_lease_file_sha256": launch_lease_file_sha256,
    }


def _ready_payload_v4(
    registered: Mapping[str, Any],
    launch_started: Mapping[str, Any],
    control: Mapping[str, Any],
    ledger_binding: Mapping[str, Any],
    *,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
) -> dict[str, Any]:
    return {
        **_ready_payload_v3(
            registered,
            control,
            ledger_binding,
            run_claim_file_sha256=run_claim_file_sha256,
            launch_lease_file_sha256=launch_lease_file_sha256,
        ),
        "schema_version": "research-launcher-ready/v4",
        "precompute_launch_started_schema_version": (
            "research-precompute-launch-started/v1"
        ),
        "launch_started_record_hash": launch_started["record_hash"],
        "launch_started_sequence": launch_started["sequence"],
        "parent_proof_file_sha256": control["parent_proof_file_sha256"],
        "parent_proof_canonical_sha256": control[
            "parent_proof_canonical_sha256"
        ],
    }


def _ready_payload_v5(
    registered: Mapping[str, Any],
    launch_started: Mapping[str, Any],
    control: Mapping[str, Any],
    ledger_binding: Mapping[str, Any],
    *,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
) -> dict[str, Any]:
    contract = registered.get("registration_contract")
    if not isinstance(contract, Mapping) or contract.get("schema_version") != (
        "research-validation-registration/v4"
    ):
        raise PrecomputeControlError("v5 READY requires v4 registration")
    quarantine_sha256 = contract.get("legacy_quarantine_sha256")
    if not _is_sha256(quarantine_sha256):
        raise PrecomputeControlError("v5 READY quarantine binding is invalid")
    return {
        **_ready_payload_v4(
            registered,
            launch_started,
            control,
            ledger_binding,
            run_claim_file_sha256=run_claim_file_sha256,
            launch_lease_file_sha256=launch_lease_file_sha256,
        ),
        "schema_version": "research-launcher-ready/v5",
        "precompute_launch_started_schema_version": (
            "research-precompute-launch-started/v2"
        ),
        "legacy_quarantine_sha256": quarantine_sha256,
    }


def verify_registered_precompute_v2(
    ledger_path: str,
    *,
    workspace_root: str | Path,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str,
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
    launch_lease_path: str | Path,
    expected_launch_lease_file_sha256: str,
    require_launch_lease: bool = True,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    if control["schema_version"] != _CONTROL_SCHEMA_V2:
        raise PrecomputeControlError("v2 launch requires parent-proof control")
    for value in (
        registered_record_hash,
        launch_started_record_hash,
        expected_run_claim_file_sha256,
        expected_launch_lease_file_sha256,
    ):
        if not _is_sha256(value):
            raise PrecomputeControlError("v2 launch SHA binding is invalid")
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    actual_ledger = _safe_regular_file(Path(ledger_path), workspace_root=workspace)
    verify_precompute_parent_proof_v1(
        control["parent_proof_path"],
        workspace_root=workspace,
        expected_file_sha256=control["parent_proof_file_sha256"],
        expected_canonical_sha256=control["parent_proof_canonical_sha256"],
        expected_verified_control=control,
    )
    rows = research_validation.read_experiment_ledger(str(actual_ledger))
    registered_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    launch_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == launch_started_record_hash
        and row.get("event_type") == "precompute_launch_started"
    ]
    if len(registered_matches) != 1 or len(launch_matches) != 1:
        raise PrecomputeControlError("v2 launch records were not found")
    registered = _validated_registration(registered_matches[0], control)
    contract = registered["registration_contract"]
    v4_registration = contract.get("schema_version") == (
        "research-validation-registration/v4"
    )
    launch_started = launch_matches[0]
    execution = precompute_execution_from_registration_v1(registered, control)
    if (
        Path(run_claim_path).parent != Path(execution["claim_parent"])
        or Path(launch_lease_path).parent != Path(execution["claim_parent"])
        or actual_ledger != Path(execution["ledger_path"])
    ):
        raise PrecomputeControlError("v2 launch execution path binding mismatch")
    expected_launch = {
        "event_id": f"{experiment_id}:precompute_launch_started",
        "experiment_id": experiment_id,
        "event_type": "precompute_launch_started",
        "precompute_launch_started_schema_version": (
            "research-precompute-launch-started/v2"
            if v4_registration
            else "research-precompute-launch-started/v1"
        ),
        "registered_record_hash": registered_record_hash,
        "registered_sequence": registered["sequence"],
        "registration_contract_sha256": registered[
            "registration_contract_sha256"
        ],
        "run_claim_file_sha256": expected_run_claim_file_sha256,
        "launch_lease_file_sha256": expected_launch_lease_file_sha256,
        "parent_proof_file_sha256": control["parent_proof_file_sha256"],
        "parent_proof_canonical_sha256": control[
            "parent_proof_canonical_sha256"
        ],
        "control_source_bundle_sha256": control["control_source_bundle_sha256"],
        "ledger_path_sha256": _registration_ledger_binding(registered)[
            "ledger_path_sha256"
        ],
        "launch_attempt": 1,
        "single_launch": True,
    }
    if v4_registration:
        expected_launch["legacy_quarantine_sha256"] = contract[
            "legacy_quarantine_sha256"
        ]
    actual_launch = {
        key: value
        for key, value in launch_started.items()
        if key
        not in {
            "schema_version",
            "sequence",
            "recorded_at",
            "previous_record_hash",
            "record_hash",
        }
    }
    experiment_rows = [row for row in rows if row.get("experiment_id") == experiment_id]
    if (
        actual_launch != expected_launch
        or launch_started.get("sequence") != registered.get("sequence") + 1
        or experiment_rows[-1].get("record_hash") != launch_started_record_hash
    ):
        raise PrecomputeControlError("v2 launch ledger binding mismatch")
    _load_claim(
        Path(run_claim_path),
        expected_file_sha256=expected_run_claim_file_sha256,
        registered_event=registered,
        verified_control=control,
    )
    expected_lease = precompute_launch_lease_identity_v1(
        Path(launch_lease_path).parent,
        workspace_root=workspace,
        ledger_path=actual_ledger,
        registered_event=registered,
        verified_control=control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    if (
        Path(launch_lease_path) != expected_lease["path"]
        or expected_launch_lease_file_sha256 != expected_lease["sha256"]
    ):
        raise PrecomputeControlError("v2 launch lease binding mismatch")
    if require_launch_lease:
        verify_precompute_launch_lease_v1(
            launch_lease_path,
            workspace_root=workspace,
            ledger_path=actual_ledger,
            registered_event=registered,
            verified_control=control,
            run_claim_path=run_claim_path,
            expected_run_claim_file_sha256=expected_run_claim_file_sha256,
            expected_launch_lease_file_sha256=expected_launch_lease_file_sha256,
        )
    ready_payload = _ready_payload_v5 if v4_registration else _ready_payload_v4
    return ready_payload(
        registered,
        launch_started,
        control,
        _registration_ledger_binding(registered),
        run_claim_file_sha256=expected_run_claim_file_sha256,
        launch_lease_file_sha256=expected_launch_lease_file_sha256,
    )


def consume_registered_precompute_launch_v2(
    ledger_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    ready = verify_registered_precompute_v2(
        ledger_path, require_launch_lease=False, **kwargs
    )
    consume_precompute_launch_lease_v1(
        Path(kwargs["launch_lease_path"]).parent,
        workspace_root=kwargs["workspace_root"],
        ledger_path=ledger_path,
        registered_event=(
            research_validation.read_experiment_ledger(ledger_path)[
                int(ready["registered_sequence"]) - 1
            ]
        ),
        verified_control=kwargs["verified_control"],
        run_claim_path=kwargs["run_claim_path"],
        expected_run_claim_file_sha256=kwargs[
            "expected_run_claim_file_sha256"
        ],
    )
    verified = verify_registered_precompute_v2(
        ledger_path, require_launch_lease=True, **kwargs
    )
    if verified != ready:
        raise PrecomputeControlError("v2 launch binding changed during lease consumption")
    return verified


def verify_registered_precompute_v3(
    ledger_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    ready = verify_registered_precompute_v2(ledger_path, **kwargs)
    if (
        ready.get("schema_version") != "research-launcher-ready/v5"
        or ready.get("precompute_launch_started_schema_version")
        != "research-precompute-launch-started/v2"
        or not _is_sha256(ready.get("legacy_quarantine_sha256"))
    ):
        raise PrecomputeControlError("v3 launch requires v4 lifecycle bindings")
    return ready


def consume_registered_precompute_launch_v3(
    ledger_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    ready = consume_registered_precompute_launch_v2(ledger_path, **kwargs)
    if (
        ready.get("schema_version") != "research-launcher-ready/v5"
        or ready.get("precompute_launch_started_schema_version")
        != "research-precompute-launch-started/v2"
        or not _is_sha256(ready.get("legacy_quarantine_sha256"))
    ):
        raise PrecomputeControlError("v3 launch requires v4 lifecycle bindings")
    return ready


def verify_registered_precompute_v1(
    ledger_path: str,
    *,
    workspace_root: str | Path,
    experiment_id: str,
    registered_record_hash: str,
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
    launch_lease_path: str | Path,
    expected_launch_lease_file_sha256: str,
    require_launch_lease: bool = True,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    if not _is_sha256(registered_record_hash):
        raise PrecomputeControlError("precompute registered record hash is invalid")
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    _safe_regular_file(Path(ledger_path), workspace_root=workspace)
    rows = research_validation.read_experiment_ledger(ledger_path)
    matches = [
        row
        for row in rows
        if row.get("record_hash") == registered_record_hash
        and row.get("experiment_id") == experiment_id
    ]
    if len(matches) != 1 or matches[0].get("event_type") != "registered":
        raise PrecomputeControlError("precompute registered record was not found")
    registered = _validated_registration(matches[0], control)
    ledger_binding = _registration_ledger_binding(registered)
    _verify_registration_ledger_binding(
        ledger_path, workspace_root=workspace, expected=ledger_binding
    )
    contract = registered["registration_contract"]
    if "input_plan" in contract:
        execution = precompute_execution_from_registration_v1(registered, control)
        if (
            Path(run_claim_path).parent != Path(execution["claim_parent"])
            or Path(launch_lease_path).parent != Path(execution["claim_parent"])
            or Path(ledger_path) != Path(execution["ledger_path"])
        ):
            raise PrecomputeControlError("precompute v4 execution path binding mismatch")
    sequence = registered.get("sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= control["minimum_registration_sequence_exclusive"]
    ):
        raise PrecomputeControlError("precompute registered sequence is invalid")
    experiment_rows = [row for row in rows if row.get("experiment_id") == experiment_id]
    if experiment_rows[-1].get("record_hash") != registered_record_hash:
        raise PrecomputeControlError("precompute experiment is no longer registered")
    _load_claim(
        Path(run_claim_path),
        expected_file_sha256=expected_run_claim_file_sha256,
        registered_event=registered,
        verified_control=control,
    )
    expected_lease = precompute_launch_lease_identity_v1(
        Path(launch_lease_path).parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered,
        verified_control=control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    if (
        Path(launch_lease_path) != expected_lease["path"]
        or expected_launch_lease_file_sha256 != expected_lease["sha256"]
    ):
        raise PrecomputeControlError("precompute launch lease expected binding mismatch")
    if require_launch_lease:
        verify_precompute_launch_lease_v1(
            launch_lease_path,
            workspace_root=workspace_root,
            ledger_path=ledger_path,
            registered_event=registered,
            verified_control=control,
            run_claim_path=run_claim_path,
            expected_run_claim_file_sha256=expected_run_claim_file_sha256,
            expected_launch_lease_file_sha256=expected_launch_lease_file_sha256,
        )
    return _ready_payload_v3(
        registered,
        control,
        ledger_binding,
        run_claim_file_sha256=expected_run_claim_file_sha256,
        launch_lease_file_sha256=expected_launch_lease_file_sha256,
    )


def consume_registered_precompute_launch_v1(
    ledger_path: str,
    *,
    workspace_root: str | Path,
    experiment_id: str,
    registered_record_hash: str,
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
    launch_lease_path: str | Path,
    expected_launch_lease_file_sha256: str,
) -> dict[str, Any]:
    expected_ready = verify_registered_precompute_v1(
        ledger_path,
        workspace_root=workspace_root,
        experiment_id=experiment_id,
        registered_record_hash=registered_record_hash,
        verified_control=verified_control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
        launch_lease_path=launch_lease_path,
        expected_launch_lease_file_sha256=expected_launch_lease_file_sha256,
        require_launch_lease=False,
    )
    rows = research_validation.read_experiment_ledger(ledger_path)
    matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    if len(matches) != 1:
        raise PrecomputeControlError("precompute registered record was not found")
    lease = consume_precompute_launch_lease_v1(
        Path(launch_lease_path).parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=matches[0],
        verified_control=verified_control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
    )
    if (
        lease["path"] != Path(launch_lease_path)
        or lease["sha256"] != expected_launch_lease_file_sha256
    ):
        raise PrecomputeControlError("precompute launch lease binding mismatch")
    verified_ready = verify_registered_precompute_v1(
        ledger_path,
        workspace_root=workspace_root,
        experiment_id=experiment_id,
        registered_record_hash=registered_record_hash,
        verified_control=verified_control,
        run_claim_path=run_claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
        launch_lease_path=launch_lease_path,
        expected_launch_lease_file_sha256=expected_launch_lease_file_sha256,
    )
    if verified_ready != expected_ready:
        raise PrecomputeControlError("precompute control changed while consuming launch")
    return verified_ready


_MANAGED_CHILD_OPTIONS = {
    "--input-plan-path",
    "--supervised-launch-mode",
    "--plan-publication-root",
    "--plan-publication-audit-root",
    "--expected-plan-publication-id",
    "--expected-published-plan-file-sha256",
    "--expected-plan-prepublish-file-sha256",
    "--expected-plan-publication-result-file-sha256",
    "--expected-plan-writer-claim-file-sha256",
    "--expected-precompute-control-source-bundle-sha256",
    "--minimum-registration-sequence-exclusive",
    "--plan-publication-parent-proof-path",
    "--expected-plan-publication-parent-proof-file-sha256",
    "--expected-plan-publication-parent-proof-canonical-sha256",
    "--precompute-registered-record-hash",
    "--precompute-launch-started-record-hash",
    "--precompute-ledger-path",
    "--precompute-run-claim-path",
    "--expected-precompute-run-claim-file-sha256",
    "--precompute-launch-lease-path",
    "--expected-precompute-launch-lease-file-sha256",
    "--precompute-completed-record-hash",
    "--precompute-run-result-path",
    "--expected-precompute-run-result-file-sha256",
}


def _managed_child_override(tokens: list[str]) -> bool:
    for token in tokens:
        option = token.split("=", 1)[0]
        if any(
            option == managed
            or (
                option.startswith("--")
                and len(option) > 2
                and managed.startswith(option)
            )
            for managed in _MANAGED_CHILD_OPTIONS
        ):
            return True
    return False


def _required_child_option(tokens: list[str], option: str) -> str:
    if any(token.startswith(option + "=") for token in tokens):
        raise PrecomputeControlError(f"precompute child {option} must be tokenized")
    positions = [index for index, token in enumerate(tokens) if token == option]
    if len(positions) != 1:
        raise PrecomputeControlError(f"precompute child {option} is not unique")
    index = positions[0]
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
        raise PrecomputeControlError(f"precompute child {option} value is missing")
    return tokens[index + 1]


def _read_bounded_snapshot(
    path: Path,
    *,
    workspace_root: Path,
    max_bytes: int,
    allow_empty: bool = False,
) -> tuple[Path, bytes]:
    safe_path = _safe_regular_file(path, workspace_root=workspace_root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(safe_path, flags)
    try:
        before = os.fstat(descriptor)
        named = safe_path.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != named.st_dev
            or before.st_ino != named.st_ino
            or before.st_size < 0
            or (before.st_size == 0 and not allow_empty)
            or before.st_size > max_bytes
        ):
            raise PrecomputeControlError("precompute artifact snapshot is unsafe")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        named_after = safe_path.stat()
        if (
            len(raw) != before.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or (named_after.st_dev, named_after.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise PrecomputeControlError("precompute artifact changed while being read")
        return safe_path, raw
    finally:
        os.close(descriptor)


def _artifact_from_snapshot(path: Path, raw: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
    }


def _verify_launcher_success_audit_v2(
    *,
    workspace: Path,
    execution: Mapping[str, Any],
    ready_payload: Mapping[str, Any],
    launcher_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    audit = _safe_directory(Path(execution["audit_dir"]), workspace_root=workspace)
    launcher_path, launcher_raw = _read_bounded_snapshot(
        audit / "launcher-receipt.json",
        workspace_root=workspace,
        max_bytes=1024 * 1024,
    )
    launcher_payload = _strict_object(launcher_raw, "precompute launcher receipt")
    exact_fields = {
        "schema_version",
        "pid",
        "started_at_utc",
        "finished_at_utc",
        "workspace_root",
        "python_executable",
        "arguments_sha256",
        "argument_count",
        "environment_paths",
        "expected_ready",
        "shell",
        "use_shell_execute",
        "created_suspended",
        "stdin_closed",
        "process_tree_guard",
        "venv_trampoline_process_allowance",
        "child_processes_created",
        "child_reaped",
        "ready_received",
        "ready_line_count",
        "direct_process_alive_at_ready",
        "ready_payload",
        "ready_ack",
        "ready_timeout_seconds",
        "exit_code",
        "failure_code",
        "terminated",
        "killed",
        "stdout",
        "stderr",
    }
    if (
        launcher_raw != _canonical_bytes(launcher_payload) + b"\n"
        or launcher_payload != dict(launcher_receipt)
        or set(launcher_payload) != exact_fields
        or launcher_payload.get("schema_version")
        != "research-supervised-launch-receipt/v2"
        or launcher_payload.get("expected_ready") != ready_payload
        or launcher_payload.get("ready_payload") != ready_payload
        or launcher_payload.get("ready_received") is not True
        or launcher_payload.get("ready_line_count") != 1
        or launcher_payload.get("child_reaped") is not True
        or launcher_payload.get("direct_process_alive_at_ready") is not True
        or launcher_payload.get("exit_code") != 0
        or launcher_payload.get("failure_code") is not None
        or launcher_payload.get("shell") is not False
        or launcher_payload.get("use_shell_execute") is not False
        or launcher_payload.get("stdin_closed") is not True
        or launcher_payload.get("terminated") is not False
        or launcher_payload.get("killed") is not False
        or launcher_payload.get("child_processes_created") != 1
        or launcher_payload.get("venv_trampoline_process_allowance") != 1
        or launcher_payload.get("created_suspended") is not (os.name == "nt")
        or isinstance(launcher_payload.get("pid"), bool)
        or not isinstance(launcher_payload.get("pid"), int)
        or launcher_payload.get("pid") <= 0
        or isinstance(launcher_payload.get("argument_count"), bool)
        or not isinstance(launcher_payload.get("argument_count"), int)
        or launcher_payload.get("argument_count") <= 0
        or not _is_sha256(launcher_payload.get("arguments_sha256"))
        or not isinstance(launcher_payload.get("process_tree_guard"), str)
        or not launcher_payload.get("process_tree_guard")
    ):
        raise PrecomputeControlError("precompute launcher receipt is invalid")
    if Path(launcher_payload["workspace_root"]) != workspace:
        raise PrecomputeControlError("precompute launcher workspace binding mismatch")
    expected_python = workspace / ".venv" / "Scripts" / "python.exe"
    if Path(launcher_payload["python_executable"]) != expected_python:
        raise PrecomputeControlError("precompute launcher executable binding mismatch")
    environment_paths = launcher_payload.get("environment_paths")
    expected_environment_keys = {
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "XDG_CACHE_HOME",
        "PYTHONPYCACHEPREFIX",
    }
    sandbox = _safe_directory(Path(execution["sandbox_root"]), workspace_root=workspace)
    if not isinstance(environment_paths, dict) or set(environment_paths) != (
        expected_environment_keys
    ):
        raise PrecomputeControlError("precompute launcher environment binding mismatch")
    for value in environment_paths.values():
        _safe_directory(Path(value), workspace_root=sandbox)
    try:
        started = datetime.fromisoformat(launcher_payload["started_at_utc"])
        finished = datetime.fromisoformat(launcher_payload["finished_at_utc"])
        timeout = float(launcher_payload["ready_timeout_seconds"])
    except (TypeError, ValueError) as exc:
        raise PrecomputeControlError("precompute launcher time binding is invalid") from exc
    if (
        started.tzinfo is None
        or finished.tzinfo is None
        or finished < started
        or not 0 < timeout <= 30
    ):
        raise PrecomputeControlError("precompute launcher time binding is invalid")

    start_path, start_raw = _read_bounded_snapshot(
        audit / "launcher-start.json", workspace_root=workspace, max_bytes=1024 * 1024
    )
    start_payload = _strict_object(start_raw, "precompute launcher start")
    expected_start = {
        "schema_version": "research-supervised-launch-start/v1",
        **{
            key: launcher_payload[key]
            for key in (
                "pid",
                "started_at_utc",
                "workspace_root",
                "python_executable",
                "arguments_sha256",
                "argument_count",
                "environment_paths",
                "expected_ready",
                "shell",
                "use_shell_execute",
                "created_suspended",
                "stdin_closed",
                "process_tree_guard",
                "venv_trampoline_process_allowance",
                "child_processes_created",
            )
        },
    }
    if start_raw != _canonical_bytes(start_payload) + b"\n" or start_payload != (
        expected_start
    ):
        raise PrecomputeControlError("precompute launcher start binding mismatch")

    def bound_artifact(name: str, *, allow_empty: bool) -> tuple[Path, bytes, dict]:
        claimed = launcher_payload.get(name)
        expected_path = audit / {
            "ready_ack": "launcher-ready-ack.json",
            "stdout": "launcher-stdout.log",
            "stderr": "launcher-stderr.log",
        }[name]
        if not isinstance(claimed, dict) or set(claimed) != {"path", "bytes", "sha256"}:
            raise PrecomputeControlError("precompute launcher artifact binding is invalid")
        path, raw = _read_bounded_snapshot(
            expected_path,
            workspace_root=workspace,
            max_bytes=16 * 1024 * 1024,
            allow_empty=allow_empty,
        )
        actual = _artifact_from_snapshot(path, raw)
        if claimed != actual:
            raise PrecomputeControlError("precompute launcher artifact binding mismatch")
        return path, raw, actual

    ack_path, ack_raw, ack_artifact = bound_artifact("ready_ack", allow_empty=False)
    ack_payload = _strict_object(ack_raw, "precompute launcher ACK")
    expected_ack = {
        "schema_version": ready_payload["schema_version"].replace(
            "research-launcher-ready/", "research-launcher-ready-ack/"
        ),
        "event": "READY_ACK",
        **{
            key: value
            for key, value in ready_payload.items()
            if key not in {"schema_version", "event"}
        },
    }
    if ack_raw != _canonical_bytes(ack_payload) + b"\n" or ack_payload != expected_ack:
        raise PrecomputeControlError("precompute launcher ACK binding mismatch")
    stdout_path, stdout_raw, stdout_artifact = bound_artifact(
        "stdout", allow_empty=False
    )
    stderr_path, stderr_raw, stderr_artifact = bound_artifact(
        "stderr", allow_empty=True
    )
    ready_lines = [line for line in stdout_raw.splitlines() if line.startswith(b"READY ")]
    expected_ready_line = b"READY " + _canonical_bytes(dict(ready_payload))
    if ready_lines != [expected_ready_line]:
        raise PrecomputeControlError("precompute launcher unique READY binding mismatch")
    return {
        "receipt": _artifact_from_snapshot(launcher_path, launcher_raw),
        "start": _artifact_from_snapshot(start_path, start_raw),
        "ready_ack": ack_artifact,
        "stdout": stdout_artifact,
        "stderr": stderr_artifact,
        "pid": launcher_payload["pid"],
        "arguments_sha256": launcher_payload["arguments_sha256"],
        "ready_line_count": 1,
    }


def _precompute_run_result_body(
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    ready: Mapping[str, Any],
    run_claim: Mapping[str, Any],
    launch_lease: Mapping[str, Any],
    launcher_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], Path]:
    workspace = _safe_directory(Path(workspace_root))
    control = _validated_control(verified_control)
    registered = _validated_registration(registered_event, control)
    execution = precompute_execution_from_registration_v1(registered, control)
    registration_schema = registered["registration_contract"]["schema_version"]
    v4_registration = registration_schema == "research-validation-registration/v4"
    ready_payload = dict(ready)
    ready_schema = (
        "research-launcher-ready/v5"
        if v4_registration
        else (
            "research-launcher-ready/v4"
            if control["schema_version"] == _CONTROL_SCHEMA_V2
            else "research-launcher-ready/v3"
        )
    )
    if (
        ready_payload.get("schema_version") != ready_schema
        or ready_payload.get("event") != "READY"
        or ready_payload.get("experiment_id") != registered["experiment_id"]
        or ready_payload.get("registered_record_hash") != registered["record_hash"]
        or ready_payload.get("registration_contract_sha256")
        != registered["registration_contract_sha256"]
    ):
        raise PrecomputeControlError("precompute run result READY binding mismatch")
    claim_sha = run_claim.get("sha256") if isinstance(run_claim, Mapping) else None
    lease_sha = launch_lease.get("sha256") if isinstance(launch_lease, Mapping) else None
    if (
        not _is_sha256(claim_sha)
        or not _is_sha256(lease_sha)
        or ready_payload.get("run_claim_file_sha256") != claim_sha
        or ready_payload.get("launch_lease_file_sha256") != lease_sha
    ):
        raise PrecomputeControlError("precompute run result single-run binding mismatch")
    if v4_registration and ready_payload.get("legacy_quarantine_sha256") != (
        registered["registration_contract"]["legacy_quarantine_sha256"]
    ):
        raise PrecomputeControlError("precompute run result quarantine binding mismatch")

    launcher_audit = _verify_launcher_success_audit_v2(
        workspace=workspace,
        execution=execution,
        ready_payload=ready_payload,
        launcher_receipt=launcher_receipt,
    )

    output_path, output_raw = _read_bounded_snapshot(
        Path(execution["qualified_trades_output_path"]),
        workspace_root=workspace,
        max_bytes=512 * 1024 * 1024,
    )
    output_payload = _strict_object(output_raw, "precompute qualified output")
    summary = output_payload.get("summary")
    trades = output_payload.get("qualified_trades")
    if (
        not isinstance(summary, dict)
        or summary.get("precompute_control") != ready_payload
        or not isinstance(trades, list)
    ):
        raise PrecomputeControlError("precompute qualified output binding mismatch")
    result_path = Path(execution["run_result_receipt_path"])
    parent_proof_artifact = None
    if control["schema_version"] == _CONTROL_SCHEMA_V2:
        proof_path = _safe_regular_file(
            Path(control["parent_proof_path"]), workspace_root=workspace
        )
        proof_raw = proof_path.read_bytes()
        if (
            _sha256_bytes(proof_raw) != control["parent_proof_file_sha256"]
            or ready_payload.get("parent_proof_file_sha256")
            != control["parent_proof_file_sha256"]
            or ready_payload.get("parent_proof_canonical_sha256")
            != control["parent_proof_canonical_sha256"]
        ):
            raise PrecomputeControlError("precompute run result parent proof mismatch")
        parent_proof_artifact = {
            "path": str(proof_path),
            "basename": proof_path.name,
            "bytes": len(proof_raw),
            "sha256": control["parent_proof_file_sha256"],
            "canonical_sha256": control["parent_proof_canonical_sha256"],
        }
    return (
        {
            "schema_version": (
                _RUN_RESULT_SCHEMA_V4
                if v4_registration
                else (
                    _RUN_RESULT_SCHEMA_V3
                    if control["schema_version"] == _CONTROL_SCHEMA_V2
                    else _RUN_RESULT_SCHEMA_V2
                )
            ),
            "experiment_id": registered["experiment_id"],
            "registered_record_hash": registered["record_hash"],
            "registered_sequence": registered["sequence"],
            "registration_contract_sha256": registered[
                "registration_contract_sha256"
            ],
            "publication_id": control["publication_id"],
            "publication_result_file_sha256": control[
                "publication_result_file_sha256"
            ],
            "plan_sha256": control["plan_sha256"],
            "plan_file_sha256": control["plan_file_sha256"],
            "fixture_id": control["fixture_id"],
            "fixture_manifest_file_sha256": control[
                "fixture_manifest_file_sha256"
            ],
            "control_source_bundle_sha256": control[
                "control_source_bundle_sha256"
            ],
            "ledger_path_sha256": _registration_ledger_binding(registered)[
                "ledger_path_sha256"
            ],
            "run_claim_file_sha256": claim_sha,
            "launch_lease_file_sha256": lease_sha,
            **(
                {
                    "legacy_quarantine_sha256": registered[
                        "registration_contract"
                    ]["legacy_quarantine_sha256"]
                }
                if v4_registration
                else {}
            ),
            **(
                {
                    "launch_started_record_hash": ready_payload[
                        "launch_started_record_hash"
                    ],
                    "launch_started_sequence": ready_payload[
                        "launch_started_sequence"
                    ],
                    "parent_proof": parent_proof_artifact,
                }
                if parent_proof_artifact is not None
                else {}
            ),
            "ready_canonical_sha256": _sha256_bytes(
                _canonical_bytes(ready_payload)
            ),
            "launcher_audit": launcher_audit,
            "qualified_output": {
                "path": str(output_path),
                "basename": output_path.name,
                "bytes": len(output_raw),
                "sha256": _sha256_bytes(output_raw),
                "qualified_trade_count": len(trades),
            },
            "run_attempt": 1,
            "single_writer": True,
            "network_calls": 0,
        },
        result_path,
    )


def create_precompute_run_result_v1(
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    ready: Mapping[str, Any],
    run_claim: Mapping[str, Any],
    launch_lease: Mapping[str, Any],
    launcher_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    body, result_path = _precompute_run_result_body(
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
        ready=ready,
        run_claim=run_claim,
        launch_lease=launch_lease,
        launcher_receipt=launcher_receipt,
    )
    workspace = _safe_directory(Path(workspace_root))
    parent = _safe_directory(result_path.parent, workspace_root=workspace)
    if result_path.parent != parent or result_path.exists():
        raise PrecomputeControlError("precompute run result path is not fresh")
    payload = {
        **body,
        "result_canonical_sha256": _sha256_bytes(_canonical_bytes(body)),
    }
    raw = _canonical_bytes(payload) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(result_path, flags, 0o600)
    except FileExistsError as exc:
        raise PrecomputeControlError("precompute run result already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        fsync_directory(parent)
    return {
        "path": result_path,
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "payload": payload,
    }


def verify_precompute_run_result_v1(
    result_path: str | Path,
    *,
    expected_file_sha256: str,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    ready: Mapping[str, Any],
    run_claim: Mapping[str, Any],
    launch_lease: Mapping[str, Any],
    launcher_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    if not _is_sha256(expected_file_sha256):
        raise PrecomputeControlError("precompute run result expected SHA is invalid")
    expected_body, expected_path = _precompute_run_result_body(
        workspace_root=workspace_root,
        registered_event=registered_event,
        verified_control=verified_control,
        ready=ready,
        run_claim=run_claim,
        launch_lease=launch_lease,
        launcher_receipt=launcher_receipt,
    )
    workspace = _safe_directory(Path(workspace_root))
    actual_path = _safe_regular_file(Path(result_path), workspace_root=workspace)
    if actual_path != expected_path:
        raise PrecomputeControlError("precompute run result path mismatch")
    raw = actual_path.read_bytes()
    payload = _strict_object(raw, "precompute run result")
    if (
        _sha256_bytes(raw) != expected_file_sha256
        or raw != _canonical_bytes(payload) + b"\n"
        or payload.get("result_canonical_sha256")
        != _sha256_bytes(_canonical_bytes(expected_body))
        or {key: value for key, value in payload.items() if key != "result_canonical_sha256"}
        != expected_body
    ):
        raise PrecomputeControlError("precompute run result binding mismatch")
    return payload


def verify_registered_precompute_run_result_v1(
    ledger_path: str,
    *,
    workspace_root: str | Path,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str | None = None,
    verified_control: Mapping[str, Any],
    run_claim_path: str | Path,
    expected_run_claim_file_sha256: str,
    launch_lease_path: str | Path,
    expected_launch_lease_file_sha256: str,
    precompute_completed_record_hash: str,
    run_result_path: str | Path,
    expected_run_result_file_sha256: str,
    validation_started_record_hash: str | None = None,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    v2_launch = control["schema_version"] == _CONTROL_SCHEMA_V2
    for value in (
        registered_record_hash,
        expected_run_claim_file_sha256,
        expected_launch_lease_file_sha256,
        precompute_completed_record_hash,
        expected_run_result_file_sha256,
    ):
        if not _is_sha256(value):
            raise PrecomputeControlError("precompute completed binding SHA is invalid")
    if v2_launch:
        if not _is_sha256(launch_started_record_hash):
            raise PrecomputeControlError("precompute launch started SHA is invalid")
    elif launch_started_record_hash is not None:
        raise PrecomputeControlError("legacy precompute result forbids launch binding")
    if validation_started_record_hash is not None and not _is_sha256(
        validation_started_record_hash
    ):
        raise PrecomputeControlError("validation started record SHA is invalid")
    workspace = _safe_directory(Path(workspace_root))
    if workspace.drive.casefold() != "e:":
        raise PrecomputeControlError("precompute workspace must be on E drive")
    actual_ledger = _safe_regular_file(Path(ledger_path), workspace_root=workspace)
    rows = research_validation.read_experiment_ledger(str(actual_ledger))
    registered_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    completed_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == precompute_completed_record_hash
        and row.get("event_type") == "precompute_completed"
    ]
    launch_matches = (
        [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == launch_started_record_hash
            and row.get("event_type") == "precompute_launch_started"
        ]
        if v2_launch
        else []
    )
    if (
        len(registered_matches) != 1
        or len(completed_matches) != 1
        or (v2_launch and len(launch_matches) != 1)
    ):
        raise PrecomputeControlError("precompute completed records were not found")
    registered = _validated_registration(registered_matches[0], control)
    v4_registration = registered["registration_contract"].get(
        "schema_version"
    ) == "research-validation-registration/v4"
    launch_started = launch_matches[0] if launch_matches else None
    completed = completed_matches[0]
    validation_matches = (
        [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == validation_started_record_hash
            and row.get("event_type") == "validation_started"
        ]
        if validation_started_record_hash is not None
        else []
    )
    if validation_started_record_hash is not None and len(validation_matches) != 1:
        raise PrecomputeControlError("validation started record was not found")
    validation_started = validation_matches[0] if validation_matches else None
    experiment_rows = [
        row for row in rows if row.get("experiment_id") == experiment_id
    ]
    expected_latest_hash = (
        validation_started_record_hash
        if validation_started_record_hash is not None
        else precompute_completed_record_hash
    )
    if (
        experiment_rows[-1].get("record_hash") != expected_latest_hash
        or completed.get("registered_record_hash") != registered_record_hash
        or completed.get("run_claim_file_sha256")
        != expected_run_claim_file_sha256
        or completed.get("launch_lease_file_sha256")
        != expected_launch_lease_file_sha256
        or (
            v2_launch
            and (
                completed.get("launch_started_record_hash")
                != launch_started_record_hash
                or completed.get("parent_proof_file_sha256")
                != control["parent_proof_file_sha256"]
                or completed.get("parent_proof_canonical_sha256")
                != control["parent_proof_canonical_sha256"]
                or (
                    v4_registration
                    and completed.get("legacy_quarantine_sha256")
                    != registered["registration_contract"][
                        "legacy_quarantine_sha256"
                    ]
                )
                or launch_started.get("sequence") != registered.get("sequence") + 1
                or completed.get("sequence") != launch_started.get("sequence") + 1
            )
        )
    ):
        raise PrecomputeControlError("precompute completed state binding mismatch")
    ledger_binding = _registration_ledger_binding(registered)
    _verify_registration_ledger_binding(
        actual_ledger, workspace_root=workspace, expected=ledger_binding
    )
    execution = precompute_execution_from_registration_v1(registered, control)
    claim_path = _safe_regular_file(Path(run_claim_path), workspace_root=workspace)
    lease_path = _safe_regular_file(Path(launch_lease_path), workspace_root=workspace)
    result_path = _safe_regular_file(Path(run_result_path), workspace_root=workspace)
    if (
        actual_ledger != Path(execution["ledger_path"])
        or claim_path.parent != Path(execution["claim_parent"])
        or lease_path.parent != Path(execution["claim_parent"])
        or result_path != Path(execution["run_result_receipt_path"])
    ):
        raise PrecomputeControlError("precompute completed execution path mismatch")
    _load_claim(
        claim_path,
        expected_file_sha256=expected_run_claim_file_sha256,
        registered_event=registered,
        verified_control=control,
    )
    verify_precompute_launch_lease_v1(
        lease_path,
        workspace_root=workspace,
        ledger_path=actual_ledger,
        registered_event=registered,
        verified_control=control,
        run_claim_path=claim_path,
        expected_run_claim_file_sha256=expected_run_claim_file_sha256,
        expected_launch_lease_file_sha256=(
            expected_launch_lease_file_sha256
        ),
    )
    if v2_launch:
        expected_launch = {
            "event_id": f"{experiment_id}:precompute_launch_started",
            "experiment_id": experiment_id,
            "event_type": "precompute_launch_started",
            "precompute_launch_started_schema_version": (
                "research-precompute-launch-started/v2"
                if v4_registration
                else "research-precompute-launch-started/v1"
            ),
            "registered_record_hash": registered_record_hash,
            "registered_sequence": registered["sequence"],
            "registration_contract_sha256": registered[
                "registration_contract_sha256"
            ],
            "run_claim_file_sha256": expected_run_claim_file_sha256,
            "launch_lease_file_sha256": expected_launch_lease_file_sha256,
            "parent_proof_file_sha256": control["parent_proof_file_sha256"],
            "parent_proof_canonical_sha256": control[
                "parent_proof_canonical_sha256"
            ],
            "control_source_bundle_sha256": control[
                "control_source_bundle_sha256"
            ],
            "ledger_path_sha256": ledger_binding["ledger_path_sha256"],
            "launch_attempt": 1,
            "single_launch": True,
        }
        if v4_registration:
            expected_launch["legacy_quarantine_sha256"] = registered[
                "registration_contract"
            ]["legacy_quarantine_sha256"]
        actual_launch = {
            key: value
            for key, value in launch_started.items()
            if key
            not in {
                "schema_version",
                "sequence",
                "recorded_at",
                "previous_record_hash",
                "record_hash",
            }
        }
        if actual_launch != expected_launch:
            raise PrecomputeControlError("precompute launch started binding mismatch")
        ready_builder = _ready_payload_v5 if v4_registration else _ready_payload_v4
        ready = ready_builder(
            registered,
            launch_started,
            control,
            ledger_binding,
            run_claim_file_sha256=expected_run_claim_file_sha256,
            launch_lease_file_sha256=expected_launch_lease_file_sha256,
        )
    else:
        ready = _ready_payload_v3(
            registered,
            control,
            ledger_binding,
            run_claim_file_sha256=expected_run_claim_file_sha256,
            launch_lease_file_sha256=expected_launch_lease_file_sha256,
        )
    launcher_path = _safe_regular_file(
        Path(execution["audit_dir"]) / "launcher-receipt.json",
        workspace_root=workspace,
    )
    launcher_raw = launcher_path.read_bytes()
    launcher_receipt = _strict_object(
        launcher_raw, "precompute launcher receipt"
    )
    if launcher_raw != _canonical_bytes(launcher_receipt) + b"\n":
        raise PrecomputeControlError(
            "precompute launcher receipt serialization is invalid"
        )
    run_claim = {
        "path": claim_path,
        "sha256": expected_run_claim_file_sha256,
    }
    launch_lease = {
        "path": lease_path,
        "sha256": expected_launch_lease_file_sha256,
    }
    result_payload = verify_precompute_run_result_v1(
        result_path,
        expected_file_sha256=expected_run_result_file_sha256,
        workspace_root=workspace,
        registered_event=registered,
        verified_control=control,
        ready=ready,
        run_claim=run_claim,
        launch_lease=launch_lease,
        launcher_receipt=launcher_receipt,
    )
    result_raw = result_path.read_bytes()
    result_artifact = {
        "path": str(result_path),
        "basename": result_path.name,
        "bytes": len(result_raw),
        "sha256": _sha256_bytes(result_raw),
    }
    if (
        result_artifact["sha256"] != expected_run_result_file_sha256
        or completed.get("run_result_artifact") != result_artifact
    ):
        raise PrecomputeControlError("precompute completed result binding mismatch")
    if validation_started is not None and (
        validation_started.get("registered_record_hash")
        != registered_record_hash
        or validation_started.get("precompute_completed_record_hash")
        != precompute_completed_record_hash
        or (
            v2_launch
            and validation_started.get("launch_started_record_hash")
            != launch_started_record_hash
        )
        or (
            v4_registration
            and validation_started.get("legacy_quarantine_sha256")
            != registered["registration_contract"]["legacy_quarantine_sha256"]
        )
        or validation_started.get("run_result_artifact") != result_artifact
        or not isinstance(validation_started.get("claimed_input_artifacts"), dict)
        or validation_started["claimed_input_artifacts"].get(
            "precompute_run_result"
        )
        != result_artifact
    ):
        raise PrecomputeControlError("validation started result binding mismatch")
    _verify_registration_ledger_binding(
        actual_ledger, workspace_root=workspace, expected=ledger_binding
    )
    verified = {
        "registered": registered,
        "precompute_completed": completed,
        "ready": ready,
        "run_result_artifact": result_artifact,
        "run_result_payload": result_payload,
        "launcher_receipt": launcher_receipt,
    }
    if launch_started is not None:
        verified["precompute_launch_started"] = launch_started
    if validation_started is not None:
        verified["validation_started"] = validation_started
    return verified


def verify_registered_precompute_run_result_v2(
    ledger_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    verified = verify_registered_precompute_run_result_v1(ledger_path, **kwargs)
    result_payload = verified.get("run_result_payload")
    ready = verified.get("ready")
    registered = verified.get("registered")
    contract = (
        registered.get("registration_contract")
        if isinstance(registered, Mapping)
        else None
    )
    if (
        not isinstance(result_payload, Mapping)
        or result_payload.get("schema_version") != _RUN_RESULT_SCHEMA_V4
        or not isinstance(ready, Mapping)
        or ready.get("schema_version") != "research-launcher-ready/v5"
        or not isinstance(contract, Mapping)
        or contract.get("schema_version") != "research-validation-registration/v4"
        or result_payload.get("legacy_quarantine_sha256")
        != contract.get("legacy_quarantine_sha256")
    ):
        raise PrecomputeControlError("v2 precompute result requires v4 lifecycle bindings")
    return verified


def run_registered_precompute_supervised_v1(
    *,
    workspace_root: str | Path,
    python_executable: str | Path,
    sandbox_root: str | Path,
    audit_dir: str | Path,
    claim_parent: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    child_args: list[str],
    environment: Mapping[str, str],
    ready_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    registered = _validated_registration(registered_event, control)
    execution = precompute_execution_from_registration_v1(registered, control)
    tokens = [str(token) for token in child_args]
    if tokens[:3] != ["-m", "app.jobs", "research-historical-universe"]:
        raise PrecomputeControlError("precompute child command is invalid")
    if _managed_child_override(tokens):
        raise PrecomputeControlError("precompute child command overrides control arguments")
    exact_paths = {
        "workspace": (Path(workspace_root), Path(execution["workspace_root"])),
        "ledger": (Path(ledger_path), Path(execution["ledger_path"])),
        "claim parent": (Path(claim_parent), Path(execution["claim_parent"])),
        "sandbox": (Path(sandbox_root), Path(execution["sandbox_root"])),
        "audit": (Path(audit_dir), Path(execution["audit_dir"])),
    }
    for label, (supplied, expected) in exact_paths.items():
        if supplied != expected or supplied.resolve(strict=False) != expected:
            raise PrecomputeControlError(
                f"precompute {label} path does not match the v4 plan"
            )
    if Path(_required_child_option(tokens, "--qualified-trades-output")) != Path(
        execution["qualified_trades_output_path"]
    ):
        raise PrecomputeControlError("precompute child output path mismatch")
    if Path(_required_child_option(tokens, "--cache-dir")) != Path(
        execution["cache_dir"]
    ):
        raise PrecomputeControlError("precompute child cache path mismatch")
    authorization = environment.get(
        "RESEARCH_PLAN_PUBLICATION_COMPLETION_AUTHORIZATION"
    )
    if not isinstance(authorization, str) or not authorization:
        raise PrecomputeControlError("precompute publication authorization is missing")
    claim = None
    try:
        reverified = verify_precompute_publication_v1(
            workspace_root=workspace_root,
            publication_root=control["publication_root"],
            audit_root=control["audit_root"],
            expected_publication_id=control["publication_id"],
            expected_plan_file_sha256=control["plan_file_sha256"],
            expected_prepublish_evidence_file_sha256=control[
                "prepublish_evidence_file_sha256"
            ],
            expected_result_file_sha256=control["publication_result_file_sha256"],
            expected_writer_claim_file_sha256=control["writer_claim_file_sha256"],
            completion_authorization=authorization,
            expected_fixture_id=control["fixture_id"],
            expected_fixture_manifest_file_sha256=control[
                "fixture_manifest_file_sha256"
            ],
            expected_control_source_bundle_sha256=control[
                "control_source_bundle_sha256"
            ],
            minimum_registration_sequence_exclusive=control[
                "minimum_registration_sequence_exclusive"
            ],
        )
        if reverified != control:
            raise PrecomputeControlError("precompute publication changed before launch")
        claim = create_precompute_run_claim_v1(
            claim_parent,
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
        )
        launch_lease = precompute_launch_lease_identity_v1(
            claim_parent,
            workspace_root=workspace_root,
            ledger_path=ledger_path,
            registered_event=registered,
            verified_control=control,
            run_claim_path=claim["path"],
            expected_run_claim_file_sha256=claim["sha256"],
        )
        ready = verify_registered_precompute_v1(
            str(ledger_path),
            workspace_root=workspace_root,
            experiment_id=registered["experiment_id"],
            registered_record_hash=registered["record_hash"],
            verified_control=control,
            run_claim_path=claim["path"],
            expected_run_claim_file_sha256=claim["sha256"],
            launch_lease_path=launch_lease["path"],
            expected_launch_lease_file_sha256=launch_lease["sha256"],
            require_launch_lease=False,
        )
        controlled_tokens = [
            *tokens,
            "--input-plan-path",
            str(Path(control["publication_root"]) / "treatment-plan.json"),
            "--supervised-launch-mode",
            "run",
            "--plan-publication-root",
            control["publication_root"],
            "--plan-publication-audit-root",
            control["audit_root"],
            "--expected-plan-publication-id",
            control["publication_id"],
            "--expected-published-plan-file-sha256",
            control["plan_file_sha256"],
            "--expected-plan-prepublish-file-sha256",
            control["prepublish_evidence_file_sha256"],
            "--expected-plan-publication-result-file-sha256",
            control["publication_result_file_sha256"],
            "--expected-plan-writer-claim-file-sha256",
            control["writer_claim_file_sha256"],
            "--expected-precompute-control-source-bundle-sha256",
            control["control_source_bundle_sha256"],
            "--minimum-registration-sequence-exclusive",
            str(control["minimum_registration_sequence_exclusive"]),
            "--precompute-ledger-path",
            str(ledger_path),
            "--precompute-registered-record-hash",
            registered["record_hash"],
            "--precompute-run-claim-path",
            str(claim["path"]),
            "--expected-precompute-run-claim-file-sha256",
            claim["sha256"],
            "--precompute-launch-lease-path",
            str(launch_lease["path"]),
            "--expected-precompute-launch-lease-file-sha256",
            launch_lease["sha256"],
        ]
        launcher_receipt = run_supervised(
            workspace_root=Path(workspace_root),
            python_executable=Path(python_executable),
            sandbox_root=Path(sandbox_root),
            audit_dir=Path(audit_dir),
            child_args=controlled_tokens,
            expected_ready=ready,
            ready_timeout_seconds=ready_timeout_seconds,
            environment=environment,
        )
        run_result = create_precompute_run_result_v1(
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
            ready=ready,
            run_claim=claim,
            launch_lease=launch_lease,
            launcher_receipt=launcher_receipt,
        )
        verify_precompute_run_result_v1(
            run_result["path"],
            expected_file_sha256=run_result["sha256"],
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
            ready=ready,
            run_claim=claim,
            launch_lease=launch_lease,
            launcher_receipt=launcher_receipt,
        )
        precompute_completed = (
            research_validation.complete_registered_precompute_if_current(
                str(ledger_path),
                experiment_id=registered["experiment_id"],
                registered_record_hash=registered["record_hash"],
                run_claim_file_sha256=claim["sha256"],
                launch_lease_file_sha256=launch_lease["sha256"],
                run_result_artifact={
                    "path": str(run_result["path"]),
                    "basename": run_result["path"].name,
                    "bytes": run_result["bytes"],
                    "sha256": run_result["sha256"],
                },
            )
        )
        return {
            "schema_version": "research-precompute-supervised-result/v1",
            "run_claim": claim,
            "expected_launch_lease": launch_lease,
            "ready": ready,
            "launcher_receipt": launcher_receipt,
            "run_result": run_result,
            "precompute_completed": precompute_completed,
        }
    except BaseException as exc:
        try:
            research_validation.fail_registered_experiment_if_current(
                str(ledger_path),
                experiment_id=registered["experiment_id"],
                registered_record_hash=registered["record_hash"],
                failure_code="PRECOMPUTE_LAUNCH_FAILED",
                failure_phase="precompute_launch",
                error_type=type(exc).__name__,
                run_claim_file_sha256=(claim["sha256"] if claim is not None else None),
            )
        except BaseException as terminal_exc:
            raise PrecomputeControlError(
                "precompute launch failed and could not be sealed"
            ) from terminal_exc
        raise


def _materialize_v2_execution_environment(
    execution: Mapping[str, Any],
    *,
    workspace_root: str | Path,
    base_environment: Mapping[str, str],
) -> dict[str, str]:
    workspace = _safe_directory(Path(workspace_root))
    run_root = _safe_directory(
        Path(execution["run_root"]), workspace_root=workspace
    )
    control_root = _safe_directory(run_root / "control", workspace_root=workspace)
    proof_path = _safe_regular_file(
        Path(execution["parent_proof_path"]), workspace_root=workspace
    )
    if proof_path.parent != control_root:
        raise PrecomputeControlError("precompute proof is outside the control root")
    paths = {
        "output": Path(execution["qualified_trades_output_path"]),
        "cache": Path(execution["cache_dir"]),
        "claims": Path(execution["claim_parent"]),
        "sandbox": Path(execution["sandbox_root"]),
        "audit": Path(execution["audit_dir"]),
        "result": Path(execution["run_result_receipt_path"]),
    }
    if (
        {item.name for item in run_root.iterdir()} != {"control"}
        or {item.name for item in control_root.iterdir()} != {proof_path.name}
        or paths["output"].parent != run_root
        or paths["cache"].parent != run_root
        or paths["claims"].parent != control_root
        or paths["sandbox"].parent != control_root
        or paths["audit"].parent != paths["sandbox"]
        or paths["result"].parent != control_root
        or any(path.exists() for path in paths.values())
    ):
        raise PrecomputeControlError("precompute execution skeleton is not fresh")
    paths["claims"].mkdir()
    fsync_directory(control_root)
    paths["cache"].mkdir()
    fsync_directory(run_root)
    paths["sandbox"].mkdir()
    fsync_directory(control_root)
    paths["audit"].mkdir()
    fsync_directory(paths["sandbox"])
    return build_e_only_environment(
        base_environment, sandbox_root=paths["sandbox"]
    )


def _read_exact_v2_launch_state(
    ledger_path: str | Path,
    *,
    registered_event: Mapping[str, Any],
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
    parent_proof_file_sha256: str,
    parent_proof_canonical_sha256: str,
    legacy_quarantine_sha256: str | None = None,
) -> dict[str, Any] | None:
    registered = dict(registered_event)
    rows = research_validation.read_experiment_ledger(str(ledger_path))
    registered_matches = [
        row
        for row in rows
        if row.get("experiment_id") == registered.get("experiment_id")
        and row.get("record_hash") == registered.get("record_hash")
        and row.get("event_type") == "registered"
    ]
    if len(registered_matches) != 1:
        raise PrecomputeControlError("registered launch state is unavailable")
    launch_matches = [
        row
        for row in rows
        if row.get("experiment_id") == registered["experiment_id"]
        and row.get("event_type") == "precompute_launch_started"
        and row.get("registered_record_hash") == registered["record_hash"]
    ]
    if not launch_matches:
        return None
    if len(launch_matches) != 1:
        raise PrecomputeControlError("precompute launch state is ambiguous")
    launch = launch_matches[0]
    expected = {
        "registered_record_hash": registered["record_hash"],
        "run_claim_file_sha256": run_claim_file_sha256,
        "launch_lease_file_sha256": launch_lease_file_sha256,
        "parent_proof_file_sha256": parent_proof_file_sha256,
        "parent_proof_canonical_sha256": parent_proof_canonical_sha256,
    }
    if legacy_quarantine_sha256 is not None:
        if not _is_sha256(legacy_quarantine_sha256):
            raise PrecomputeControlError("precompute launch quarantine SHA is invalid")
        expected["legacy_quarantine_sha256"] = legacy_quarantine_sha256
    if (
        launch.get("previous_record_hash") != registered["record_hash"]
        or launch.get("sequence") != registered["sequence"] + 1
        or any(launch.get(key) != value for key, value in expected.items())
    ):
        raise PrecomputeControlError("precompute launch state binding mismatch")
    tip = rows[-1]
    if tip.get("record_hash") == launch.get("record_hash"):
        return launch
    if (
        tip.get("experiment_id") != registered["experiment_id"]
        or tip.get("event_type") not in {"precompute_completed", "failed"}
        or tip.get("previous_record_hash") != launch.get("record_hash")
        or tip.get("launch_started_record_hash") != launch.get("record_hash")
        or any(tip.get(key) != value for key, value in expected.items())
    ):
        raise PrecomputeControlError("precompute launch terminal state is ambiguous")
    return tip


def run_registered_precompute_supervised_v2(
    *,
    workspace_root: str | Path,
    python_executable: str | Path,
    sandbox_root: str | Path,
    audit_dir: str | Path,
    claim_parent: str | Path,
    ledger_path: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
    child_args: list[str],
    environment: Mapping[str, str],
    ready_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    control = _validated_control(verified_control)
    if control["schema_version"] != _CONTROL_SCHEMA_V2:
        raise PrecomputeControlError("v2 supervised launch requires parent-proof control")
    registered = _validated_registration(registered_event, control)
    v4_registration = registered["registration_contract"].get(
        "schema_version"
    ) == "research-validation-registration/v4"
    execution = precompute_execution_from_registration_v1(registered, control)
    tokens = [str(token) for token in child_args]
    if tokens[:3] != ["-m", "app.jobs", "research-historical-universe"]:
        raise PrecomputeControlError("precompute child command is invalid")
    if _managed_child_override(tokens):
        raise PrecomputeControlError("precompute child command overrides control arguments")
    try:
        from app.research_precompute_parent import build_registered_child_args_v1

        canonical_tokens = build_registered_child_args_v1(
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise PrecomputeControlError(
            "canonical registered command could not be derived"
        ) from exc
    if tokens != canonical_tokens:
        raise PrecomputeControlError(
            "precompute child command is not the canonical registered command"
        )
    exact_paths = {
        "workspace": (Path(workspace_root), Path(execution["workspace_root"])),
        "ledger": (Path(ledger_path), Path(execution["ledger_path"])),
        "claim parent": (Path(claim_parent), Path(execution["claim_parent"])),
        "sandbox": (Path(sandbox_root), Path(execution["sandbox_root"])),
        "audit": (Path(audit_dir), Path(execution["audit_dir"])),
    }
    for label, (supplied, expected) in exact_paths.items():
        if supplied != expected or supplied.resolve(strict=False) != expected:
            raise PrecomputeControlError(f"precompute {label} path binding mismatch")
    if Path(_required_child_option(tokens, "--qualified-trades-output")) != Path(
        execution["qualified_trades_output_path"]
    ) or Path(_required_child_option(tokens, "--cache-dir")) != Path(
        execution["cache_dir"]
    ):
        raise PrecomputeControlError("precompute child output path binding mismatch")
    if any(
        key.casefold()
        == "research_plan_publication_completion_authorization".casefold()
        for key in environment
    ):
        raise PrecomputeControlError("v2 supervised child environment contains a secret")
    verify_precompute_parent_proof_v1(
        control["parent_proof_path"],
        workspace_root=workspace_root,
        expected_file_sha256=control["parent_proof_file_sha256"],
        expected_canonical_sha256=control["parent_proof_canonical_sha256"],
        expected_verified_control=control,
    )
    source_bundle = (
        precompute_control_source_bundle_v3(workspace_root)
        if v4_registration
        else precompute_control_source_bundle_v2(workspace_root)
    )
    if source_bundle["root_sha256"] != control["control_source_bundle_sha256"]:
        raise PrecomputeControlError("precompute control source changed before launch")
    claim_identity = planned_precompute_run_claim_identity_v2(
        claim_parent,
        workspace_root=workspace_root,
        registered_event=registered,
        verified_control=control,
    )
    launch_lease = planned_precompute_launch_lease_identity_v2(
        claim_parent,
        workspace_root=workspace_root,
        ledger_path=ledger_path,
        registered_event=registered,
        verified_control=control,
        expected_run_claim_file_sha256=claim_identity["sha256"],
    )
    launch_started = None
    claim = None
    ready = None
    launcher_receipt = None
    run_result = None
    try:
        launch_started = (
            research_validation.authorize_registered_precompute_launch_if_current(
                str(ledger_path),
                experiment_id=registered["experiment_id"],
                registered_record_hash=registered["record_hash"],
                run_claim_file_sha256=claim_identity["sha256"],
                launch_lease_file_sha256=launch_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
            )
        )
        child_environment = _materialize_v2_execution_environment(
            execution,
            workspace_root=workspace_root,
            base_environment=environment,
        )
        claim = publish_precompute_run_claim_v1(
            claim_parent,
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
            expected_file_sha256=claim_identity["sha256"],
        )
        ready = verify_registered_precompute_v2(
            str(ledger_path),
            workspace_root=workspace_root,
            experiment_id=registered["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=launch_started["record_hash"],
            verified_control=control,
            run_claim_path=claim["path"],
            expected_run_claim_file_sha256=claim["sha256"],
            launch_lease_path=launch_lease["path"],
            expected_launch_lease_file_sha256=launch_lease["sha256"],
            require_launch_lease=False,
        )
        controlled_tokens = [
            *tokens,
            "--input-plan-path",
            str(Path(control["publication_root"]) / "treatment-plan.json"),
            "--supervised-launch-mode",
            "run",
            "--plan-publication-parent-proof-path",
            control["parent_proof_path"],
            "--expected-plan-publication-parent-proof-file-sha256",
            control["parent_proof_file_sha256"],
            "--expected-plan-publication-parent-proof-canonical-sha256",
            control["parent_proof_canonical_sha256"],
            "--precompute-ledger-path",
            str(ledger_path),
            "--precompute-registered-record-hash",
            registered["record_hash"],
            "--precompute-launch-started-record-hash",
            launch_started["record_hash"],
            "--precompute-run-claim-path",
            str(claim["path"]),
            "--expected-precompute-run-claim-file-sha256",
            claim["sha256"],
            "--precompute-launch-lease-path",
            str(launch_lease["path"]),
            "--expected-precompute-launch-lease-file-sha256",
            launch_lease["sha256"],
        ]
        launcher_receipt = run_supervised(
            workspace_root=Path(workspace_root),
            python_executable=Path(python_executable),
            sandbox_root=Path(sandbox_root),
            audit_dir=Path(audit_dir),
            child_args=controlled_tokens,
            expected_ready=ready,
            ready_timeout_seconds=ready_timeout_seconds,
            environment=child_environment,
        )
        run_result = create_precompute_run_result_v1(
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
            ready=ready,
            run_claim=claim,
            launch_lease=launch_lease,
            launcher_receipt=launcher_receipt,
        )
        verify_precompute_run_result_v1(
            run_result["path"],
            expected_file_sha256=run_result["sha256"],
            workspace_root=workspace_root,
            registered_event=registered,
            verified_control=control,
            ready=ready,
            run_claim=claim,
            launch_lease=launch_lease,
            launcher_receipt=launcher_receipt,
        )
        completed = research_validation.complete_authorized_precompute_launch_if_current(
            str(ledger_path),
            experiment_id=registered["experiment_id"],
            registered_record_hash=registered["record_hash"],
            launch_started_record_hash=launch_started["record_hash"],
            run_claim_file_sha256=claim["sha256"],
            launch_lease_file_sha256=launch_lease["sha256"],
            parent_proof_file_sha256=control["parent_proof_file_sha256"],
            parent_proof_canonical_sha256=control[
                "parent_proof_canonical_sha256"
            ],
            run_result_artifact={
                "path": str(run_result["path"]),
                "basename": run_result["path"].name,
                "bytes": run_result["bytes"],
                "sha256": run_result["sha256"],
            },
        )
        return {
            "schema_version": (
                "research-precompute-supervised-result/v3"
                if v4_registration
                else "research-precompute-supervised-result/v2"
            ),
            "launch_started": launch_started,
            "run_claim": claim,
            "expected_launch_lease": launch_lease,
            "ready": ready,
            "launcher_receipt": launcher_receipt,
            "run_result": run_result,
            "precompute_completed": completed,
        }
    except BaseException as exc:
        if launch_started is None:
            state = _read_exact_v2_launch_state(
                ledger_path,
                registered_event=registered,
                run_claim_file_sha256=claim_identity["sha256"],
                launch_lease_file_sha256=launch_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
                legacy_quarantine_sha256=(
                    registered["registration_contract"]["legacy_quarantine_sha256"]
                    if v4_registration
                    else None
                ),
            )
            if state is None:
                raise
            launch_started = state
        else:
            state = launch_started
            if run_result is not None:
                state = (
                    _read_exact_v2_launch_state(
                        ledger_path,
                        registered_event=registered,
                        run_claim_file_sha256=claim_identity["sha256"],
                        launch_lease_file_sha256=launch_lease["sha256"],
                        parent_proof_file_sha256=control[
                            "parent_proof_file_sha256"
                        ],
                        parent_proof_canonical_sha256=control[
                            "parent_proof_canonical_sha256"
                        ],
                        legacy_quarantine_sha256=(
                            registered["registration_contract"]["legacy_quarantine_sha256"]
                            if v4_registration
                            else None
                        ),
                    )
                    or launch_started
                )
        if state.get("event_type") == "precompute_completed":
            expected_result_artifact = (
                {
                    "path": str(run_result["path"]),
                    "basename": run_result["path"].name,
                    "bytes": run_result["bytes"],
                    "sha256": run_result["sha256"],
                }
                if run_result is not None
                else None
            )
            if (
                expected_result_artifact is None
                or state.get("run_result_artifact") != expected_result_artifact
            ):
                raise PrecomputeControlError(
                    "completed precompute launch result could not be recovered"
                ) from exc
            return {
                "schema_version": (
                    "research-precompute-supervised-result/v3"
                    if v4_registration
                    else "research-precompute-supervised-result/v2"
                ),
                "launch_started": next(
                    row
                    for row in research_validation.read_experiment_ledger(
                        str(ledger_path)
                    )
                    if row.get("record_hash")
                    == state["launch_started_record_hash"]
                ),
                "run_claim": claim,
                "expected_launch_lease": launch_lease,
                "ready": ready,
                "launcher_receipt": launcher_receipt,
                "run_result": run_result,
                "precompute_completed": state,
                "recovered_after_commit": True,
            }
        if state.get("event_type") == "failed":
            raise
        try:
            research_validation.fail_authorized_precompute_launch_if_current(
                str(ledger_path),
                experiment_id=registered["experiment_id"],
                registered_record_hash=registered["record_hash"],
                launch_started_record_hash=launch_started["record_hash"],
                run_claim_file_sha256=claim_identity["sha256"],
                launch_lease_file_sha256=launch_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
                failure_code="PRECOMPUTE_LAUNCH_FAILED",
                failure_phase="precompute_launch",
                error_type=type(exc).__name__,
            )
        except BaseException as terminal_exc:
            terminal = _read_exact_v2_launch_state(
                ledger_path,
                registered_event=registered,
                run_claim_file_sha256=claim_identity["sha256"],
                launch_lease_file_sha256=launch_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
                legacy_quarantine_sha256=(
                    registered["registration_contract"]["legacy_quarantine_sha256"]
                    if v4_registration
                    else None
                ),
            )
            if terminal is None or terminal.get("event_type") != "failed":
                raise PrecomputeControlError(
                    "v2 precompute launch failed and could not be sealed"
                ) from terminal_exc
        raise


def run_registered_precompute_supervised_v3(**kwargs: Any) -> dict[str, Any]:
    control = _validated_control(kwargs.get("verified_control"))
    registered = _validated_registration(kwargs.get("registered_event"), control)
    if registered["registration_contract"].get("schema_version") != (
        "research-validation-registration/v4"
    ):
        raise PrecomputeControlError("v3 supervised launch requires v4 registration")
    result = run_registered_precompute_supervised_v2(**kwargs)
    ready = result.get("ready")
    run_result = result.get("run_result")
    payload = run_result.get("payload") if isinstance(run_result, Mapping) else None
    if (
        result.get("schema_version") != "research-precompute-supervised-result/v3"
        or not isinstance(ready, Mapping)
        or ready.get("schema_version") != "research-launcher-ready/v5"
        or not isinstance(payload, Mapping)
        or payload.get("schema_version") != _RUN_RESULT_SCHEMA_V4
        or payload.get("legacy_quarantine_sha256")
        != registered["registration_contract"]["legacy_quarantine_sha256"]
    ):
        raise PrecomputeControlError("v3 supervised launch lifecycle binding mismatch")
    return result
