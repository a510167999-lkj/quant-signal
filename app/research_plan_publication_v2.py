"""Versioned single-file publication for controlled treatment plan v5."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Mapping

from app.durable_io import fsync_directory, fsync_file
from app import research_plan_publication as publication_v1
from app.research_plan_publication import (
    _contained,
    _probe_delete_access,
    _rename_no_replace,
    _restart_manager_is_clear,
    _restart_manager_lockers,
    _safe_directory,
    _safe_regular_file,
    _strict_json_object,
    _validate_embedded_paths,
    _verify_signed_payload,
    _write_new,
    _write_signed_json,
)


_IDENTITY_SCHEMA = "research-treatment-plan-publication-identity/v2"
_AUDIT_SCHEMA = "research-treatment-plan-publication-audit/v2"
_RESULT_SCHEMA = "research-treatment-plan-publication-result/v2"
_SOURCE_SCHEMA = "research-treatment-plan-publication-source-bundle/v2"
_IDENTITY_BINDING_FIELDS = {
    "experiment_id",
    "plan_schema_version",
    "plan_sha256",
    "plan_file_sha256",
    "publisher_source_bundle_sha256",
    "publication_id",
}
_PREPUBLISH_FIELDS = {
    "schema_version",
    *_IDENTITY_BINDING_FIELDS,
    "workspace_root",
    "publication_parent",
    "publication_root",
    "audit_root",
    "pending_path",
    "final_path",
    "writer_claim_path",
    "writer_claim_file_sha256",
    "delete_access_probe",
    "restart_manager",
    "all_publisher_file_handles_closed",
    "rename_budget",
    "retry_count",
    "ledger_writes",
    "research_computations",
    "network_calls",
}
_RESULT_FIELDS = {
    "schema_version",
    *_IDENTITY_BINDING_FIELDS,
    "success",
    "stage",
    "rename_attempts",
    "retry_count",
    "publication_root",
    "audit_root",
    "pending_exists",
    "publication_exists",
    "plan_file_sha256",
    "prepublish_evidence_file_sha256",
    "writer_claim_path",
    "writer_claim_file_sha256",
    "completion_authorization_sha256",
    "ledger_writes",
    "research_computations",
    "network_calls",
}
_DELETE_ACCESS_FIELDS = {
    "path",
    "opened",
    "handle_value",
    "share_delete",
    "closed",
    "close_winerror",
}
_RESTART_MANAGER_CLEAR_FIELDS = {
    "path",
    "start_rc",
    "register_rc",
    "get_rc",
    "needed",
    "reboot",
    "lockers",
    "end_rc",
}


class PlanPublicationV2Error(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        audit_root: Path | None = None,
        publication_root: Path | None = None,
        rename_attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.audit_root = audit_root
        self.publication_root = publication_root
        self.rename_attempts = rename_attempts


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_regular_bytes(path: Path, label: str) -> tuple[Path, bytes]:
    candidate = _safe_regular_file(path, label)
    before = candidate.stat()
    if before.st_nlink != 1:
        raise PlanPublicationV2Error(f"{label} is a hardlink")
    raw = candidate.read_bytes()
    after = candidate.stat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise PlanPublicationV2Error(f"{label} changed during verification")
    return candidate, raw


def _stable_strict_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    candidate, before = _stable_regular_bytes(path, label)
    payload, raw = _strict_json_object(candidate, label)
    _candidate, after = _stable_regular_bytes(candidate, label)
    if before != raw or raw != after:
        raise PlanPublicationV2Error(f"{label} changed during verification")
    return payload, raw


def _source_bundle(workspace: Path) -> dict[str, Any]:
    actual_root = Path(__file__).resolve().parent.parent
    v1_path = Path(publication_v1.__file__).resolve()
    if workspace.resolve() != actual_root or v1_path.parent.parent != actual_root:
        raise PlanPublicationV2Error("publication v2 source workspace mismatch")
    files = []
    source_paths = (
        ("app/durable_io.py", Path(fsync_directory.__code__.co_filename).resolve()),
        ("app/research_plan_publication.py", v1_path),
        ("app/research_plan_publication_v2.py", Path(__file__).resolve()),
    )
    for relative, actual_path in source_paths:
        path, raw = _stable_regular_bytes(
            workspace / relative, "publication v2 source"
        )
        if path != actual_path:
            raise PlanPublicationV2Error("publication v2 source path mismatch")
        files.append(
            {
                "relative_path": relative,
                "bytes": len(raw),
                "sha256": _sha_bytes(raw),
            }
        )
    body = {"schema_version": _SOURCE_SCHEMA, "files": files}
    return {**body, "root_sha256": _sha_bytes(_canonical_bytes(body))}


def _validated_plan_v2(
    plan: Mapping[str, Any], *, expected_plan_schema: str = "research-treatment-input-plan/v5"
) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise PlanPublicationV2Error("plan schema mismatch")
    payload = dict(plan)
    if payload.get("schema_version") != expected_plan_schema:
        raise PlanPublicationV2Error("plan schema mismatch")
    plan_sha = payload.get("plan_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "plan_sha256"}
    if not isinstance(plan_sha, str) or plan_sha != _sha_bytes(
        _canonical_bytes(unsigned)
    ):
        raise PlanPublicationV2Error("plan SHA mismatch")
    if not isinstance(payload.get("experiment_id"), str) or not payload[
        "experiment_id"
    ]:
        raise PlanPublicationV2Error("plan experiment id is missing")
    raw = _canonical_bytes(payload) + b"\n"
    try:
        from app import jobs

        jobs._validate_treatment_input_plan_payload(payload, raw)
    except (TypeError, ValueError) as exc:
        raise PlanPublicationV2Error("plan control contract invalid") from exc
    return payload


def _expected_publication_parent_v2(
    plan: Mapping[str, Any], *, expected_plan_schema: str = "research-treatment-input-plan/v5"
) -> Path:
    payload = _validated_plan_v2(plan, expected_plan_schema=expected_plan_schema)
    workspace = Path(payload["development_payload_fixture"]["workspace_root"])
    return workspace / "tmp" / "research-plan-publications-v2"


def plan_publication_identity_v2(
    plan: Mapping[str, Any], *, expected_plan_schema: str = "research-treatment-input-plan/v5"
) -> dict[str, str]:
    payload = _validated_plan_v2(plan, expected_plan_schema=expected_plan_schema)
    workspace = _safe_directory(
        Path(payload["development_payload_fixture"]["workspace_root"]),
        "publication v2 workspace",
    )
    source_bundle = _source_bundle(workspace)
    body = {
        "schema_version": _IDENTITY_SCHEMA,
        "experiment_id": payload["experiment_id"],
        "plan_schema_version": payload["schema_version"],
        "plan_sha256": payload["plan_sha256"],
        "plan_file_sha256": _sha_bytes(_canonical_bytes(payload) + b"\n"),
        "publisher_source_bundle_sha256": source_bundle["root_sha256"],
    }
    return {**body, "publication_id": _sha_bytes(_canonical_bytes(body))}


def _write_failure(
    *,
    audit_root: Path,
    publication_root: Path,
    pending_path: Path,
    identity: Mapping[str, Any],
    stage: str,
    rename_attempts: int,
    writer_claim_path: Path,
    writer_claim_file_sha256: str | None,
    prepublish_file_sha256: str | None,
    error: BaseException | None = None,
) -> str:
    payload = {
        "schema_version": _RESULT_SCHEMA,
        "publication_id": identity["publication_id"],
        "experiment_id": identity["experiment_id"],
        "success": False,
        "stage": stage,
        "rename_attempts": rename_attempts,
        "retry_count": 0,
        "publication_root": str(publication_root),
        "pending_path": str(pending_path),
        "writer_claim_path": str(writer_claim_path),
        "writer_claim_file_sha256": writer_claim_file_sha256,
        "prepublish_evidence_file_sha256": prepublish_file_sha256,
        "error_type": type(error).__name__ if error is not None else None,
        "winerror": getattr(error, "winerror", None) if error is not None else None,
        "ledger_writes": 0,
        "research_computations": 0,
        "network_calls": 0,
    }
    return _write_signed_json(
        audit_root / "publish-failure.json", payload, "result_canonical_sha256"
    )


def _publish_treatment_plan(
    parent: str | Path,
    *,
    plan: Mapping[str, Any],
    workspace_root: str | Path,
    expected_experiment_id: str,
    expected_plan_schema: str,
) -> dict[str, Any]:
    payload = _validated_plan_v2(plan, expected_plan_schema=expected_plan_schema)
    if payload["experiment_id"] != expected_experiment_id:
        raise PlanPublicationV2Error("plan experiment id mismatch")
    workspace = _safe_directory(Path(workspace_root), "workspace")
    parent_path = _safe_directory(Path(parent), "publication v2 parent")
    plan_workspace = _safe_directory(
        Path(payload["development_payload_fixture"]["workspace_root"]),
        "publication v2 plan workspace",
    )
    actual_workspace = Path(__file__).resolve().parent.parent
    expected_parent = _expected_publication_parent_v2(
        payload, expected_plan_schema=expected_plan_schema
    )
    if (
        workspace.drive.casefold() != "e:"
        or workspace != actual_workspace
        or plan_workspace != workspace
        or not _contained(parent_path, workspace)
    ):
        raise PlanPublicationV2Error("publication parent is outside workspace")
    if parent_path != expected_parent:
        raise PlanPublicationV2Error("publication parent namespace mismatch")
    _validate_embedded_paths(payload, workspace)
    identity = plan_publication_identity_v2(
        payload, expected_plan_schema=expected_plan_schema
    )
    target = parent_path / identity["publication_id"]
    if target.exists():
        raise PlanPublicationV2Error(
            "plan publication target already exists", publication_root=target
        )
    nonce = f"{os.getpid()}-{time.time_ns()}"
    audit_root = parent_path / (
        f".plan-publication-audit-v2-{identity['publication_id']}-{nonce}"
    )
    try:
        audit_root.mkdir()
        fsync_directory(parent_path)
    except BaseException as exc:
        raise PlanPublicationV2Error(
            "plan publication audit directory failed",
            audit_root=audit_root,
            publication_root=target,
        ) from exc

    claim_path = parent_path / f".plan-publication-v2-{identity['publication_id']}.lock"
    claim_sha = None
    pending_path = target / "treatment-plan.pending"
    final_path = target / "treatment-plan.json"
    prepublish_sha = None
    stage = "writer_claim"
    try:
        descriptor = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(identity["publication_id"].encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        claim_sha = _sha_bytes(claim_path.read_bytes())
        fsync_file(claim_path)
        fsync_directory(parent_path)
        stage = "publication_directory"
        target.mkdir()
        stage = "pending_write"
        _write_new(pending_path, _canonical_bytes(payload) + b"\n")
        fsync_file(pending_path)
        fsync_directory(target)
        stage = "pre_rename_handle_audit"
        delete_access = _probe_delete_access(target)
        lockers = _restart_manager_lockers(pending_path)
        prepublish = {
            **identity,
            "schema_version": _AUDIT_SCHEMA,
            "workspace_root": str(workspace),
            "publication_parent": str(parent_path),
            "publication_root": str(target),
            "audit_root": str(audit_root),
            "pending_path": str(pending_path),
            "final_path": str(final_path),
            "writer_claim_path": str(claim_path),
            "writer_claim_file_sha256": claim_sha,
            "delete_access_probe": delete_access,
            "restart_manager": lockers,
            "all_publisher_file_handles_closed": True,
            "rename_budget": 1,
            "retry_count": 0,
            "ledger_writes": 0,
            "research_computations": 0,
            "network_calls": 0,
        }
        prepublish_sha = _write_signed_json(
            audit_root / "pre-rename.json", prepublish, "audit_canonical_sha256"
        )
        fsync_directory(audit_root)
        fsync_directory(parent_path)
        if (
            delete_access.get("opened") is not True
            or delete_access.get("closed") is not True
            or not _restart_manager_is_clear(lockers)
        ):
            raise PlanPublicationV2Error("plan publication handle audit failed")
    except BaseException as exc:
        try:
            _write_failure(
                audit_root=audit_root,
                publication_root=target,
                pending_path=pending_path,
                identity=identity,
                stage=stage,
                rename_attempts=0,
                writer_claim_path=claim_path,
                writer_claim_file_sha256=claim_sha,
                prepublish_file_sha256=prepublish_sha,
                error=exc,
            )
            fsync_directory(audit_root)
            fsync_directory(parent_path)
        except BaseException as seal_exc:
            raise PlanPublicationV2Error(
                "plan publication pre-commit failure could not be sealed",
                audit_root=audit_root,
                publication_root=target,
            ) from seal_exc
        raise PlanPublicationV2Error(
            "plan publication pre-commit publication failed",
            audit_root=audit_root,
            publication_root=target,
        ) from exc
    try:
        _rename_no_replace(pending_path, final_path)
    except OSError as exc:
        _write_failure(
            audit_root=audit_root,
            publication_root=target,
            pending_path=pending_path,
            identity=identity,
            stage="atomic_file_rename",
            rename_attempts=1,
            writer_claim_path=claim_path,
            writer_claim_file_sha256=claim_sha,
            prepublish_file_sha256=prepublish_sha,
            error=exc,
        )
        fsync_directory(audit_root)
        raise PlanPublicationV2Error(
            "plan publication atomic file rename failed",
            audit_root=audit_root,
            publication_root=target,
            rename_attempts=1,
        ) from exc
    try:
        fsync_file(final_path)
        fsync_directory(target)
        fsync_directory(parent_path)
        authorization = secrets.token_hex(32)
        authorization_sha = _sha_bytes(authorization.encode("ascii"))
        result = {
            **identity,
            "schema_version": _RESULT_SCHEMA,
            "success": True,
            "stage": "published",
            "rename_attempts": 1,
            "retry_count": 0,
            "publication_root": str(target),
            "audit_root": str(audit_root),
            "pending_exists": pending_path.exists(),
            "publication_exists": final_path.is_file(),
            "plan_file_sha256": _sha_bytes(final_path.read_bytes()),
            "prepublish_evidence_file_sha256": prepublish_sha,
            "writer_claim_path": str(claim_path),
            "writer_claim_file_sha256": claim_sha,
            "completion_authorization_sha256": authorization_sha,
            "ledger_writes": 0,
            "research_computations": 0,
            "network_calls": 0,
        }
        result_sha = _write_signed_json(
            audit_root / "publish-result.json", result, "result_canonical_sha256"
        )
        fsync_directory(audit_root)
        fsync_directory(parent_path)
    except BaseException as exc:
        try:
            _write_failure(
                audit_root=audit_root,
                publication_root=target,
                pending_path=pending_path,
                identity=identity,
                stage="post_commit_durability",
                rename_attempts=1,
                writer_claim_path=claim_path,
                writer_claim_file_sha256=claim_sha,
                prepublish_file_sha256=prepublish_sha,
                error=exc,
            )
            fsync_directory(audit_root)
        except BaseException as seal_exc:
            raise PlanPublicationV2Error(
                "plan publication post-commit failure could not be sealed",
                audit_root=audit_root,
                publication_root=target,
                rename_attempts=1,
            ) from seal_exc
        raise PlanPublicationV2Error(
            "plan publication post-commit durability failed",
            audit_root=audit_root,
            publication_root=target,
            rename_attempts=1,
        ) from exc
    return {
        **identity,
        "publication_root": str(target),
        "audit_root": str(audit_root),
        "prepublish_evidence_file_sha256": prepublish_sha,
        "result_file_sha256": result_sha,
        "writer_claim_path": str(claim_path),
        "writer_claim_file_sha256": claim_sha,
        "completion_authorization": authorization,
        "completion_authorization_sha256": authorization_sha,
        "rename_attempts": 1,
        "retry_count": 0,
    }


def publish_treatment_plan_v2(
    parent: str | Path,
    *,
    plan: Mapping[str, Any],
    workspace_root: str | Path,
    expected_experiment_id: str,
) -> dict[str, Any]:
    return _publish_treatment_plan(
        parent,
        plan=plan,
        workspace_root=workspace_root,
        expected_experiment_id=expected_experiment_id,
        expected_plan_schema="research-treatment-input-plan/v5",
    )


def publish_treatment_plan_v3(
    parent: str | Path,
    *,
    plan: Mapping[str, Any],
    workspace_root: str | Path,
    expected_experiment_id: str,
) -> dict[str, Any]:
    return _publish_treatment_plan(
        parent,
        plan=plan,
        workspace_root=workspace_root,
        expected_experiment_id=expected_experiment_id,
        expected_plan_schema="research-treatment-input-plan/v6",
    )


def _verify_published_treatment_plan(
    publication_root: str | Path,
    *,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    expected_completion_authorization: str,
    expected_plan_schema: str,
) -> dict[str, Any]:
    publication = _safe_directory(Path(publication_root), "publication v2 root")
    audit = _safe_directory(Path(audit_root), "publication v2 audit root")
    if (
        publication.name != expected_publication_id
        or publication.parent != audit.parent
        or sorted(path.name for path in publication.iterdir())
        != ["treatment-plan.json"]
        or sorted(path.name for path in audit.iterdir())
        != ["pre-rename.json", "publish-result.json"]
    ):
        raise PlanPublicationV2Error("published plan file set is invalid")
    plan_path, _ = _stable_regular_bytes(
        publication / "treatment-plan.json", "published treatment plan v2"
    )
    plan, plan_raw = _stable_strict_json(plan_path, "published treatment plan v2")
    if plan_raw != _canonical_bytes(plan) + b"\n":
        raise PlanPublicationV2Error("published plan serialization is invalid")
    identity = plan_publication_identity_v2(
        plan, expected_plan_schema=expected_plan_schema
    )
    if (
        identity["publication_id"] != expected_publication_id
        or _sha_bytes(plan_raw) != expected_plan_file_sha256
        or identity["plan_file_sha256"] != expected_plan_file_sha256
    ):
        raise PlanPublicationV2Error("published plan identity mismatch")
    workspace = _safe_directory(
        Path(plan["development_payload_fixture"]["workspace_root"]),
        "published plan v2 workspace",
    )
    expected_parent = _expected_publication_parent_v2(
        plan, expected_plan_schema=expected_plan_schema
    )
    if (
        workspace.drive.casefold() != "e:"
        or not _contained(publication, workspace)
        or publication.parent != expected_parent
    ):
        raise PlanPublicationV2Error("published plan is outside workspace")
    _validate_embedded_paths(plan, workspace)
    pre, pre_raw = _stable_strict_json(
        audit / "pre-rename.json", "publication v2 pre-audit"
    )
    result, result_raw = _stable_strict_json(
        audit / "publish-result.json", "publication v2 result"
    )
    pre_unsigned = _verify_signed_payload(
        pre, "audit_canonical_sha256", "publication v2 pre-audit"
    )
    result_unsigned = _verify_signed_payload(
        result, "result_canonical_sha256", "publication v2 result"
    )
    identity_binding = {
        key: value for key, value in identity.items() if key != "schema_version"
    }
    expected_pending = publication / "treatment-plan.pending"
    expected_claim = (
        publication.parent / f".plan-publication-v2-{expected_publication_id}.lock"
    )
    if (
        pre_raw != _canonical_bytes(pre) + b"\n"
        or result_raw != _canonical_bytes(result) + b"\n"
        or _sha_bytes(pre_raw) != expected_prepublish_evidence_file_sha256
        or _sha_bytes(result_raw) != expected_result_file_sha256
        or pre_unsigned.get("schema_version") != _AUDIT_SCHEMA
        or result_unsigned.get("schema_version") != _RESULT_SCHEMA
        or set(pre_unsigned) != _PREPUBLISH_FIELDS
        or set(result_unsigned) != _RESULT_FIELDS
        or any(pre.get(key) != value for key, value in identity_binding.items())
        or any(result.get(key) != value for key, value in identity_binding.items())
        or pre_unsigned.get("workspace_root") != str(workspace)
        or pre_unsigned.get("publication_parent") != str(publication.parent)
        or pre_unsigned.get("audit_root") != str(audit)
        or result_unsigned.get("audit_root") != str(audit)
        or not audit.name.startswith(
            f".plan-publication-audit-v2-{expected_publication_id}-"
        )
        or pre_unsigned.get("pending_path") != str(expected_pending)
        or pre_unsigned.get("final_path") != str(plan_path)
        or pre_unsigned.get("writer_claim_path") != str(expected_claim)
        or result_unsigned.get("writer_claim_path") != str(expected_claim)
        or pre_unsigned.get("delete_access_probe", {}).get("path")
        != str(publication)
        or set(pre_unsigned.get("delete_access_probe", {}))
        != _DELETE_ACCESS_FIELDS
        or pre_unsigned.get("delete_access_probe", {}).get("share_delete") is not True
        or pre_unsigned.get("delete_access_probe", {}).get("close_winerror") != 0
        or pre_unsigned.get("restart_manager", {}).get("path")
        != str(expected_pending)
        or set(pre_unsigned.get("restart_manager", {}))
        != _RESTART_MANAGER_CLEAR_FIELDS
        or result_unsigned.get("success") is not True
        or result_unsigned.get("stage") != "published"
        or result_unsigned.get("rename_attempts") != 1
        or result_unsigned.get("retry_count") != 0
        or result_unsigned.get("pending_exists") is not False
        or result_unsigned.get("publication_exists") is not True
        or result_unsigned.get("plan_file_sha256") != expected_plan_file_sha256
        or result_unsigned.get("prepublish_evidence_file_sha256")
        != expected_prepublish_evidence_file_sha256
        or pre_unsigned.get("rename_budget") != 1
        or pre_unsigned.get("retry_count") != 0
        or pre_unsigned.get("all_publisher_file_handles_closed") is not True
        or pre_unsigned.get("publication_root") != str(publication)
        or result_unsigned.get("publication_root") != str(publication)
        or any(
            payload.get(key) != 0
            for payload in (pre_unsigned, result_unsigned)
            for key in ("ledger_writes", "research_computations", "network_calls")
        )
        or not _restart_manager_is_clear(pre_unsigned.get("restart_manager", {}))
        or pre_unsigned.get("delete_access_probe", {}).get("opened") is not True
        or pre_unsigned.get("delete_access_probe", {}).get("closed") is not True
    ):
        raise PlanPublicationV2Error("published plan audit contract mismatch")
    try:
        authorization_raw = expected_completion_authorization.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PlanPublicationV2Error("completion authorization is invalid") from exc
    if (
        len(expected_completion_authorization) != 64
        or result_unsigned.get("completion_authorization_sha256")
        != _sha_bytes(authorization_raw)
    ):
        raise PlanPublicationV2Error("completion authorization mismatch")
    claim, claim_raw = _stable_regular_bytes(
        expected_claim, "publication v2 writer claim"
    )
    claim_sha = _sha_bytes(claim_raw)
    if (
        claim_raw != expected_publication_id.encode("ascii")
        or claim_sha != expected_writer_claim_file_sha256
        or pre_unsigned.get("writer_claim_file_sha256") != claim_sha
        or result_unsigned.get("writer_claim_file_sha256") != claim_sha
        or pre_unsigned.get("writer_claim_path") != str(claim)
        or result_unsigned.get("writer_claim_path") != str(claim)
    ):
        raise PlanPublicationV2Error("writer claim binding mismatch")
    return {
        **identity,
        "publication_root": str(publication),
        "audit_root": str(audit),
        "prepublish_evidence_file_sha256": _sha_bytes(pre_raw),
        "result_file_sha256": _sha_bytes(result_raw),
        "writer_claim_path": str(claim),
        "writer_claim_file_sha256": claim_sha,
        "verified": True,
    }


def verify_published_treatment_plan_v2(
    publication_root: str | Path,
    *,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    expected_completion_authorization: str,
) -> dict[str, Any]:
    return _verify_published_treatment_plan(
        publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=expected_completion_authorization,
        expected_plan_schema="research-treatment-input-plan/v5",
    )


def verify_published_treatment_plan_v3(
    publication_root: str | Path,
    *,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    expected_completion_authorization: str,
) -> dict[str, Any]:
    return _verify_published_treatment_plan(
        publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=expected_completion_authorization,
        expected_plan_schema="research-treatment-input-plan/v6",
    )


def verify_published_treatment_plan_v4(
    publication_root: str | Path,
    *,
    audit_root: str | Path,
    expected_publication_id: str,
    expected_plan_file_sha256: str,
    expected_prepublish_evidence_file_sha256: str,
    expected_result_file_sha256: str,
    expected_writer_claim_file_sha256: str,
    expected_completion_authorization: str,
) -> dict[str, Any]:
    return _verify_published_treatment_plan(
        publication_root,
        audit_root=audit_root,
        expected_publication_id=expected_publication_id,
        expected_plan_file_sha256=expected_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            expected_prepublish_evidence_file_sha256
        ),
        expected_result_file_sha256=expected_result_file_sha256,
        expected_writer_claim_file_sha256=expected_writer_claim_file_sha256,
        expected_completion_authorization=expected_completion_authorization,
        expected_plan_schema="research-treatment-input-plan/v7",
    )
