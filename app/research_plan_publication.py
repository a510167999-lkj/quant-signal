"""Fail-closed treatment-plan publication with contemporaneous Windows lock evidence."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import stat
import time
from ctypes import wintypes
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from app.durable_io import fsync_directory, fsync_file


_PLAN_SCHEMAS = {
    "research-treatment-input-plan/v3",
    "research-treatment-input-plan/v4",
}
_IDENTITY_SCHEMA = "research-treatment-plan-publication-identity/v1"
_AUDIT_SCHEMA = "research-treatment-plan-publication-audit/v1"
_RESULT_SCHEMA = "research-treatment-plan-publication-result/v1"
_REPARSE_ATTRIBUTE = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class PlanPublicationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        audit_root: Path | None = None,
        staging_root: Path | None = None,
        publication_root: Path | None = None,
        rename_attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.audit_root = audit_root
        self.staging_root = staging_root
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


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _signed(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result[field] = _sha_bytes(_canonical_bytes(payload))
    return result


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _write_signed_json(path: Path, payload: Mapping[str, Any], field: str) -> str:
    signed = _signed(payload, field)
    raw = _canonical_bytes(signed) + b"\n"
    _write_new(path, raw)
    fsync_file(path)
    return _sha_bytes(raw)


def _safe_directory(path: Path, label: str) -> Path:
    _reject_reparse_components(path, label)
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or attributes & _REPARSE_ATTRIBUTE
    ):
        raise PlanPublicationError(f"{label} is unsafe")
    resolved = path.resolve(strict=True)
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        current /= part
        metadata = current.lstat()
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if current.is_symlink() or attributes & _REPARSE_ATTRIBUTE:
            raise PlanPublicationError(f"{label} contains an unsafe path component")
    return resolved


def _reject_reparse_components(
    path: Path, label: str, *, allow_missing_leaf: bool = False
) -> None:
    if not path.is_absolute():
        raise PlanPublicationError(f"{label} must be absolute")
    current = Path(path.anchor)
    components = path.parts[1:]
    for index, part in enumerate(components):
        if part in {".", ".."}:
            raise PlanPublicationError(f"{label} contains a path alias")
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf:
                return
            raise PlanPublicationError(f"{label} does not exist")
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if current.is_symlink() or attributes & _REPARSE_ATTRIBUTE:
            raise PlanPublicationError(f"{label} contains an unsafe path component")
        if index == len(components) - 1:
            return


def _safe_regular_file(path: Path, label: str) -> Path:
    resolved_parent = _safe_directory(path.parent, f"{label} parent")
    candidate = resolved_parent / path.name
    metadata = candidate.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or candidate.is_symlink()
        or attributes & _REPARSE_ATTRIBUTE
    ):
        raise PlanPublicationError(f"{label} is unsafe")
    return candidate.resolve(strict=True)


def _has_alias(path: Path) -> bool:
    return any(part in {".", ".."} for part in path.parts)


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _validate_embedded_paths(plan: Mapping[str, Any], workspace: Path) -> None:
    for value in _iter_strings(plan):
        candidate = PureWindowsPath(value)
        if not candidate.drive and not value.startswith(("\\\\", "//")):
            continue
        if any(part in {".", ".."} for part in candidate.parts):
            raise PlanPublicationError("plan contains a path alias")
        path_input = Path(value)
        _reject_reparse_components(
            path_input, "plan embedded path", allow_missing_leaf=True
        )
        path = path_input.resolve(strict=False)
        if path.drive.casefold() != workspace.drive.casefold() or not _contained(
            path, workspace
        ):
            raise PlanPublicationError("plan contains an absolute path outside workspace")


def _validated_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    if payload.get("schema_version") not in _PLAN_SCHEMAS:
        raise PlanPublicationError("plan schema mismatch")
    plan_sha = payload.get("plan_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "plan_sha256"}
    try:
        calculated_plan_sha = _sha_bytes(_canonical_bytes(unsigned))
    except (TypeError, ValueError) as error:
        raise PlanPublicationError("plan control contract invalid") from error
    if not isinstance(plan_sha, str) or plan_sha != calculated_plan_sha:
        raise PlanPublicationError("plan SHA mismatch")
    experiment_id = payload.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise PlanPublicationError("plan experiment id is missing")
    raw = _canonical_bytes(payload) + b"\n"
    try:
        from app import jobs

        jobs._validate_treatment_input_plan_payload(payload, raw)
    except (TypeError, ValueError) as error:
        raise PlanPublicationError("plan control contract invalid") from error
    return payload


def plan_publication_identity(plan: Mapping[str, Any]) -> dict[str, str]:
    payload = _validated_plan(plan)
    plan_raw = _canonical_bytes(payload) + b"\n"
    identity = {
        "schema": _IDENTITY_SCHEMA,
        "experiment_id": payload["experiment_id"],
        "plan_sha256": payload["plan_sha256"],
        "plan_file_sha256": _sha_bytes(plan_raw),
        "publisher_source_sha256": _sha_file(Path(__file__)),
    }
    return {**identity, "publication_id": _sha_bytes(_canonical_bytes(identity))}


def _probe_delete_access(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise PlanPublicationError("plan publication successor requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = create_file(
        str(path),
        0x00010000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        error = ctypes.get_last_error()
        return {
            "path": str(path),
            "opened": False,
            "winerror": error,
            "message": ctypes.FormatError(error),
        }
    handle_value = int(handle)
    ctypes.set_last_error(0)
    closed = bool(close_handle(handle))
    error = 0 if closed else ctypes.get_last_error()
    return {
        "path": str(path),
        "opened": True,
        "handle_value": handle_value,
        "share_delete": True,
        "closed": closed,
        "close_winerror": error,
    }


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", _RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * 256),
        ("strServiceShortName", wintypes.WCHAR * 64),
        ("ApplicationType", wintypes.DWORD),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def _restart_manager_lockers(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise PlanPublicationError("plan publication successor requires Windows")
    library = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    start_session = library.RmStartSession
    start_session.argtypes = (
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        wintypes.LPWSTR,
    )
    start_session.restype = wintypes.DWORD
    register_resources = library.RmRegisterResources
    register_resources.argtypes = (
        wintypes.DWORD,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT,
        ctypes.POINTER(_RM_UNIQUE_PROCESS),
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
    )
    register_resources.restype = wintypes.DWORD
    get_list = library.RmGetList
    get_list.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_RM_PROCESS_INFO),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_list.restype = wintypes.DWORD
    end_session = library.RmEndSession
    end_session.argtypes = (wintypes.DWORD,)
    end_session.restype = wintypes.DWORD
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    start_rc = start_session(ctypes.byref(session), 0, key)
    result: dict[str, Any] = {"path": str(path), "start_rc": start_rc}
    if start_rc:
        return result
    try:
        resources = (wintypes.LPCWSTR * 1)(str(path))
        register_rc = register_resources(
            session.value, 1, resources, 0, None, 0, None
        )
        result["register_rc"] = register_rc
        if register_rc:
            return result
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot = wintypes.DWORD(0)
        get_rc = get_list(
            session.value,
            ctypes.byref(needed),
            ctypes.byref(count),
            None,
            ctypes.byref(reboot),
        )
        result.update({"get_rc": get_rc, "needed": needed.value, "reboot": reboot.value})
        if not needed.value:
            result["lockers"] = []
            return result
        processes = (_RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        second_rc = get_list(
            session.value,
            ctypes.byref(needed),
            ctypes.byref(count),
            processes,
            ctypes.byref(reboot),
        )
        result["second_get_rc"] = second_rc
        if second_rc:
            result["lockers"] = []
            return result
        result["lockers"] = [
            {
                "pid": processes[index].Process.dwProcessId,
                "process_start_time_100ns": (
                    int(processes[index].Process.ProcessStartTime.dwHighDateTime) << 32
                )
                | int(processes[index].Process.ProcessStartTime.dwLowDateTime),
                "application": processes[index].strAppName,
                "service": processes[index].strServiceShortName,
                "status": processes[index].AppStatus,
                "restartable": bool(processes[index].bRestartable),
            }
            for index in range(count.value)
        ]
        return result
    finally:
        result["end_rc"] = int(end_session(session.value))


def _restart_manager_is_clear(result: Mapping[str, Any]) -> bool:
    if (
        result.get("start_rc") != 0
        or result.get("register_rc") != 0
        or result.get("end_rc") != 0
        or result.get("lockers") != []
    ):
        return False
    get_rc = result.get("get_rc")
    if get_rc == 0:
        return result.get("needed", 0) == 0
    if get_rc == 234:
        return (
            isinstance(result.get("needed"), int)
            and result["needed"] > 0
            and result.get("second_get_rc") == 0
        )
    return False


def _strict_json_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()

    def reject_duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PlanPublicationError(f"{label} contains duplicate JSON fields")
            result[key] = value
        return result

    def reject_constant(value):
        raise PlanPublicationError(f"{label} contains non-finite JSON value {value}")

    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except PlanPublicationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlanPublicationError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise PlanPublicationError(f"{label} must be a JSON object")
    return payload, raw


def _verify_signed_payload(
    payload: Mapping[str, Any], field: str, label: str
) -> dict[str, Any]:
    signature = payload.get(field)
    unsigned = {key: value for key, value in payload.items() if key != field}
    if (
        not isinstance(signature, str)
        or len(signature) != 64
        or signature != _sha_bytes(_canonical_bytes(unsigned))
    ):
        raise PlanPublicationError(f"{label} canonical signature mismatch")
    return unsigned


def _rename_no_replace(source: Path, target: Path) -> None:
    if os.name != "nt":
        raise PlanPublicationError("plan publication successor requires Windows")
    os.rename(source, target)


def _result_error(
    *,
    audit_root: Path,
    staging: Path,
    target: Path,
    identity: Mapping[str, str],
    stage: str,
    rename_attempts: int,
    claim_path: Path,
    claim_file_sha256: str,
    prepublish_file_sha256: str | None,
    error: BaseException | None = None,
) -> str:
    plan_path = (
        staging / "treatment-plan.json"
        if staging.exists()
        else target / "treatment-plan.json"
    )
    def diagnostic(probe, path: Path) -> dict[str, Any]:
        try:
            return probe(path)
        except BaseException as probe_error:
            return {
                "completed": False,
                "error_type": type(probe_error).__name__,
                "winerror": getattr(probe_error, "winerror", None),
                "errno": getattr(probe_error, "errno", None),
            }

    result = {
        "schema": _RESULT_SCHEMA,
        "publication_id": identity["publication_id"],
        "experiment_id": identity["experiment_id"],
        "plan_sha256": identity["plan_sha256"],
        "plan_file_sha256": identity["plan_file_sha256"],
        "publisher_source_sha256": identity["publisher_source_sha256"],
        "success": False,
        "stage": stage,
        "rename_attempts": rename_attempts,
        "retry_count": 0,
        "staging_root": str(staging),
        "publication_root": str(target),
        "staging_exists": staging.exists(),
        "publication_exists": target.exists(),
        "writer_claim_path": str(claim_path),
        "writer_claim_file_sha256": claim_file_sha256,
        "prepublish_evidence_file_sha256": prepublish_file_sha256,
        "winerror": getattr(error, "winerror", None) if error else None,
        "errno": getattr(error, "errno", None) if error else None,
        "error_type": type(error).__name__ if error else None,
        "restart_manager_after_failure": (
            diagnostic(_restart_manager_lockers, plan_path) if plan_path.exists() else None
        ),
        "delete_access_after_failure": (
            diagnostic(_probe_delete_access, staging) if staging.exists() else None
        ),
        "ledger_writes": 0,
        "research_computations": 0,
        "network_calls": 0,
    }
    result_file_sha256 = _write_signed_json(
        audit_root / "publish-failure.json", result, "result_canonical_sha256"
    )
    fsync_directory(audit_root)
    return result_file_sha256


def publish_treatment_plan_v1(
    parent: str | Path,
    *,
    plan: Mapping[str, Any],
    workspace_root: str | Path,
    expected_experiment_id: str,
) -> dict[str, Any]:
    payload = _validated_plan(plan)
    if payload["experiment_id"] != expected_experiment_id:
        raise PlanPublicationError("plan experiment id mismatch")
    workspace_input = Path(workspace_root)
    parent_input = Path(parent)
    if _has_alias(workspace_input) or _has_alias(parent_input):
        raise PlanPublicationError("workspace or parent path alias is forbidden")
    workspace = _safe_directory(workspace_input, "workspace")
    parent_path = _safe_directory(parent_input, "publication parent")
    if workspace.drive.casefold() != "e:" or not _contained(parent_path, workspace):
        raise PlanPublicationError("publication parent is outside workspace")
    _validate_embedded_paths(payload, workspace)

    identity = plan_publication_identity(payload)
    plan_raw = _canonical_bytes(payload) + b"\n"
    target = parent_path / identity["publication_id"]
    if target.exists():
        raise PlanPublicationError(
            "plan publication target already exists", publication_root=target
        )

    claim_path = parent_path / f".plan-publication-v1-{identity['publication_id']}.lock"
    try:
        claim = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        raise PlanPublicationError("plan publication writer claim already exists") from None
    try:
        os.write(claim, identity["publication_id"].encode("ascii"))
        os.fsync(claim)
    finally:
        os.close(claim)
    fsync_directory(parent_path)
    claim_file_sha256 = _sha_file(claim_path)

    nonce = f"{os.getpid()}-{time.time_ns()}"
    staging = parent_path / f".plan-publication-v1-{identity['publication_id']}-{nonce}"
    audit_root = parent_path / f".plan-publication-audit-v1-{identity['publication_id']}-{nonce}"
    staging.mkdir()
    audit_root.mkdir()
    plan_path = staging / "treatment-plan.json"
    _write_new(plan_path, plan_raw)
    fsync_file(plan_path)
    fsync_directory(staging)

    delete_access = _probe_delete_access(staging)
    lockers = _restart_manager_lockers(plan_path)
    prepublish = {
        "schema": _AUDIT_SCHEMA,
        "publication_id": identity["publication_id"],
        "experiment_id": payload["experiment_id"],
        "plan_sha256": identity["plan_sha256"],
        "plan_file_sha256": identity["plan_file_sha256"],
        "publisher_source_sha256": identity["publisher_source_sha256"],
        "writer_claim_path": str(claim_path),
        "writer_claim_file_sha256": claim_file_sha256,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "workspace_root": str(workspace),
        "publication_parent": str(parent_path),
        "staging_root": str(staging),
        "publication_root": str(target),
        "target_exists_before": target.exists(),
        "staging_attributes": int(getattr(staging.lstat(), "st_file_attributes", 0)),
        "staging_is_symlink": staging.is_symlink(),
        "delete_access_probe": delete_access,
        "restart_manager": lockers,
        "all_publisher_file_handles_closed": True,
        "rename_budget": 1,
        "retry_count": 0,
        "ledger_writes": 0,
        "research_computations": 0,
        "network_calls": 0,
    }
    prepublish_file_sha = _write_signed_json(
        audit_root / "pre-rename.json", prepublish, "audit_canonical_sha256"
    )
    fsync_directory(audit_root)
    fsync_directory(parent_path)

    if (
        delete_access.get("opened") is not True
        or delete_access.get("closed") is not True
        or not _restart_manager_is_clear(lockers)
    ):
        _result_error(
            audit_root=audit_root,
            staging=staging,
            target=target,
            identity=identity,
            stage="pre_rename_handle_audit",
            rename_attempts=0,
            claim_path=claim_path,
            claim_file_sha256=claim_file_sha256,
            prepublish_file_sha256=prepublish_file_sha,
        )
        raise PlanPublicationError(
            "plan publication handle audit failed",
            audit_root=audit_root,
            staging_root=staging,
            publication_root=target,
            rename_attempts=0,
        )
    if target.exists():
        _result_error(
            audit_root=audit_root,
            staging=staging,
            target=target,
            identity=identity,
            stage="target_appeared_before_rename",
            rename_attempts=0,
            claim_path=claim_path,
            claim_file_sha256=claim_file_sha256,
            prepublish_file_sha256=prepublish_file_sha,
        )
        raise PlanPublicationError(
            "plan publication target appeared before rename",
            audit_root=audit_root,
            staging_root=staging,
            publication_root=target,
            rename_attempts=0,
        )

    try:
        _rename_no_replace(staging, target)
    except OSError as error:
        _result_error(
            audit_root=audit_root,
            staging=staging,
            target=target,
            identity=identity,
            stage="atomic_rename",
            rename_attempts=1,
            claim_path=claim_path,
            claim_file_sha256=claim_file_sha256,
            prepublish_file_sha256=prepublish_file_sha,
            error=error,
        )
        raise PlanPublicationError(
            "plan publication atomic rename failed",
            audit_root=audit_root,
            staging_root=staging,
            publication_root=target,
            rename_attempts=1,
        ) from error

    published_plan = target / "treatment-plan.json"
    post_rename_stage = "post_rename_durability"
    try:
        completion_authorization = secrets.token_hex(32)
        completion_authorization_sha256 = _sha_bytes(
            completion_authorization.encode("ascii")
        )
        fsync_directory(parent_path)
        integrity_ok = (
            not staging.exists()
            and target.exists()
            and _sha_file(published_plan) == identity["plan_file_sha256"]
        )
        if not integrity_ok:
            raise PlanPublicationError("published plan integrity mismatch")
        result = {
            "schema": _RESULT_SCHEMA,
            "publication_id": identity["publication_id"],
            "experiment_id": identity["experiment_id"],
            "plan_sha256": identity["plan_sha256"],
            "publisher_source_sha256": identity["publisher_source_sha256"],
            "success": True,
            "stage": "published",
            "rename_attempts": 1,
            "retry_count": 0,
            "staging_root": str(staging),
            "publication_root": str(target),
            "staging_exists": False,
            "publication_exists": True,
            "plan_file_sha256": _sha_file(published_plan),
            "prepublish_evidence_file_sha256": prepublish_file_sha,
            "writer_claim_path": str(claim_path),
            "writer_claim_file_sha256": claim_file_sha256,
            "completion_authorization_sha256": completion_authorization_sha256,
            "ledger_writes": 0,
            "research_computations": 0,
            "network_calls": 0,
        }
        post_rename_stage = "success_receipt_durability"
        result_file_sha = _write_signed_json(
            audit_root / "publish-result.json", result, "result_canonical_sha256"
        )
        fsync_directory(audit_root)
        fsync_directory(parent_path)
    except BaseException as error:
        try:
            _result_error(
                audit_root=audit_root,
                staging=staging,
                target=target,
                identity=identity,
                stage=post_rename_stage,
                rename_attempts=1,
                claim_path=claim_path,
                claim_file_sha256=claim_file_sha256,
                prepublish_file_sha256=prepublish_file_sha,
                error=error,
            )
        except BaseException as audit_error:
            label = {
                "post_rename_durability": "post-rename durability",
                "success_receipt_durability": "success receipt durability",
            }[post_rename_stage]
            raise PlanPublicationError(
                f"plan publication {label} and failure audit were not durable",
                audit_root=audit_root,
                staging_root=staging,
                publication_root=target,
                rename_attempts=1,
            ) from audit_error
        label = {
            "post_rename_durability": "post-rename durability",
            "success_receipt_durability": "success receipt durability",
        }[post_rename_stage]
        raise PlanPublicationError(
            f"plan publication {label} failed",
            audit_root=audit_root,
            staging_root=staging,
            publication_root=target,
            rename_attempts=1,
        ) from error
    return {
        **identity,
        "publication_root": str(target),
        "audit_root": str(audit_root),
        "plan_file_sha256": identity["plan_file_sha256"],
        "prepublish_evidence_file_sha256": prepublish_file_sha,
        "result_file_sha256": result_file_sha,
        "writer_claim_path": str(claim_path),
        "writer_claim_file_sha256": claim_file_sha256,
        "completion_authorization": completion_authorization,
        "completion_authorization_sha256": completion_authorization_sha256,
        "rename_attempts": 1,
        "retry_count": 0,
    }


def verify_published_treatment_plan_v1(
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
    publication = _safe_directory(Path(publication_root), "publication root")
    audit = _safe_directory(Path(audit_root), "publication audit root")
    if publication.name != expected_publication_id:
        raise PlanPublicationError("published plan directory identity mismatch")
    if publication.parent != audit.parent:
        raise PlanPublicationError("published plan audit parent mismatch")
    plan_entries = sorted(path.name for path in publication.iterdir())
    if plan_entries != ["treatment-plan.json"]:
        raise PlanPublicationError("published plan file set is invalid")
    audit_entries = sorted(path.name for path in audit.iterdir())
    if audit_entries != ["pre-rename.json", "publish-result.json"]:
        raise PlanPublicationError("published plan success receipt set is invalid")

    plan_path = _safe_regular_file(
        publication / "treatment-plan.json", "published treatment plan"
    )
    plan, plan_raw = _strict_json_object(plan_path, "published treatment plan")
    if not plan_raw.endswith(b"\n") or plan_raw != _canonical_bytes(plan) + b"\n":
        raise PlanPublicationError("published treatment plan serialization is invalid")
    identity = plan_publication_identity(plan)
    if identity["publication_id"] != expected_publication_id:
        raise PlanPublicationError("published plan identity mismatch")
    plan_file_sha256 = _sha_bytes(plan_raw)
    if (
        plan_file_sha256 != expected_plan_file_sha256
        or plan_file_sha256 != identity["plan_file_sha256"]
    ):
        raise PlanPublicationError("published plan file SHA mismatch")

    workspace = _safe_directory(
        Path(plan["development_payload_fixture"]["workspace_root"]),
        "published plan workspace",
    )
    if workspace.drive.casefold() != "e:" or not _contained(publication, workspace):
        raise PlanPublicationError("published plan is outside its bound workspace")
    _validate_embedded_paths(plan, workspace)

    pre_path = _safe_regular_file(audit / "pre-rename.json", "pre-rename audit")
    pre, pre_raw = _strict_json_object(pre_path, "pre-rename audit")
    if not pre_raw.endswith(b"\n") or pre_raw != _canonical_bytes(pre) + b"\n":
        raise PlanPublicationError("pre-rename audit serialization is invalid")
    pre_unsigned = _verify_signed_payload(
        pre, "audit_canonical_sha256", "pre-rename audit"
    )
    pre_file_sha = _sha_bytes(pre_raw)
    if pre_file_sha != expected_prepublish_evidence_file_sha256:
        raise PlanPublicationError("pre-rename audit external binding mismatch")
    expected_pre_fields = {
        "schema",
        "publication_id",
        "experiment_id",
        "plan_sha256",
        "plan_file_sha256",
        "publisher_source_sha256",
        "writer_claim_path",
        "writer_claim_file_sha256",
        "pid",
        "parent_pid",
        "workspace_root",
        "publication_parent",
        "staging_root",
        "publication_root",
        "target_exists_before",
        "staging_attributes",
        "staging_is_symlink",
        "delete_access_probe",
        "restart_manager",
        "all_publisher_file_handles_closed",
        "rename_budget",
        "retry_count",
        "ledger_writes",
        "research_computations",
        "network_calls",
    }
    if set(pre_unsigned) != expected_pre_fields:
        raise PlanPublicationError("pre-rename audit fields are invalid")

    result_path = _safe_regular_file(
        audit / "publish-result.json", "published plan success receipt"
    )
    result, result_raw = _strict_json_object(
        result_path, "published plan success receipt"
    )
    if not result_raw.endswith(b"\n") or result_raw != _canonical_bytes(result) + b"\n":
        raise PlanPublicationError("published plan success receipt serialization is invalid")
    result_unsigned = _verify_signed_payload(
        result, "result_canonical_sha256", "published plan success receipt"
    )
    result_file_sha = _sha_bytes(result_raw)
    if result_file_sha != expected_result_file_sha256:
        raise PlanPublicationError("published plan success receipt external binding mismatch")
    expected_result_fields = {
        "schema",
        "publication_id",
        "experiment_id",
        "plan_sha256",
        "plan_file_sha256",
        "publisher_source_sha256",
        "success",
        "stage",
        "rename_attempts",
        "retry_count",
        "staging_root",
        "publication_root",
        "staging_exists",
        "publication_exists",
        "prepublish_evidence_file_sha256",
        "writer_claim_path",
        "writer_claim_file_sha256",
        "completion_authorization_sha256",
        "ledger_writes",
        "research_computations",
        "network_calls",
    }
    if set(result_unsigned) != expected_result_fields:
        raise PlanPublicationError("published plan success receipt fields are invalid")
    if (
        result.get("success") is not True
        or result.get("stage") != "published"
        or result.get("rename_attempts") != 1
        or result.get("retry_count") != 0
        or result.get("staging_exists") is not False
        or result.get("publication_exists") is not True
    ):
        raise PlanPublicationError("published plan success receipt is not terminal success")
    try:
        completion_authorization_raw = expected_completion_authorization.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as error:
        raise PlanPublicationError(
            "published plan completion authorization is invalid"
        ) from error
    if (
        len(expected_completion_authorization) != 64
        or result.get("completion_authorization_sha256")
        != _sha_bytes(completion_authorization_raw)
    ):
        raise PlanPublicationError("published plan completion authorization mismatch")

    identity_fields = (
        "publication_id",
        "experiment_id",
        "plan_sha256",
        "plan_file_sha256",
        "publisher_source_sha256",
    )
    for field in identity_fields:
        if pre.get(field) != identity[field] or result.get(field) != identity[field]:
            raise PlanPublicationError("published plan audit identity binding mismatch")
    if (
        pre.get("schema") != _AUDIT_SCHEMA
        or result.get("schema") != _RESULT_SCHEMA
        or pre.get("workspace_root") != str(workspace)
        or pre.get("publication_parent") != str(publication.parent)
        or pre.get("publication_root") != str(publication)
        or result.get("publication_root") != str(publication)
        or pre.get("target_exists_before") is not False
        or pre.get("staging_is_symlink") is not False
        or pre.get("all_publisher_file_handles_closed") is not True
        or pre.get("rename_budget") != 1
        or pre.get("retry_count") != 0
        or pre.get("ledger_writes") != 0
        or pre.get("research_computations") != 0
        or pre.get("network_calls") != 0
        or result.get("ledger_writes") != 0
        or result.get("research_computations") != 0
        or result.get("network_calls") != 0
        or pre.get("delete_access_probe", {}).get("opened") is not True
        or pre.get("delete_access_probe", {}).get("closed") is not True
        or not _restart_manager_is_clear(pre.get("restart_manager", {}))
    ):
        raise PlanPublicationError("published plan audit contract mismatch")

    staging = Path(str(pre.get("staging_root")))
    prefix = f".plan-publication-v1-{expected_publication_id}-"
    audit_prefix = f".plan-publication-audit-v1-{expected_publication_id}-"
    if (
        staging.parent.resolve(strict=True) != publication.parent
        or not staging.name.startswith(prefix)
        or staging.exists()
        or not audit.name.startswith(audit_prefix)
        or staging.name.removeprefix(prefix) != audit.name.removeprefix(audit_prefix)
        or result.get("staging_root") != str(staging)
    ):
        raise PlanPublicationError("published plan staging lineage mismatch")

    claim_path = publication.parent / f".plan-publication-v1-{expected_publication_id}.lock"
    claim = _safe_regular_file(claim_path, "published plan writer claim")
    claim_sha = _sha_file(claim)
    if claim.read_bytes() != expected_publication_id.encode("ascii"):
        raise PlanPublicationError("published plan writer claim content mismatch")
    if (
        claim_sha != expected_writer_claim_file_sha256
        or pre.get("writer_claim_path") != str(claim)
        or result.get("writer_claim_path") != str(claim)
        or pre.get("writer_claim_file_sha256") != claim_sha
        or result.get("writer_claim_file_sha256") != claim_sha
    ):
        raise PlanPublicationError("published plan writer claim binding mismatch")
    if result.get("prepublish_evidence_file_sha256") != pre_file_sha:
        raise PlanPublicationError("published plan pre-rename evidence binding mismatch")

    return {
        **identity,
        "publication_root": str(publication),
        "audit_root": str(audit),
        "plan_file_sha256": plan_file_sha256,
        "prepublish_evidence_file_sha256": pre_file_sha,
        "result_file_sha256": result_file_sha,
        "writer_claim_path": str(claim),
        "writer_claim_file_sha256": claim_sha,
        "verified": True,
    }
