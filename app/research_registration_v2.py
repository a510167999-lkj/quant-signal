"""Two-phase, publish-stop registration contract for experiment 038."""

from __future__ import annotations

import hashlib
import hmac
import json
import ctypes
import errno
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from app.durable_io import fsync_directory, fsync_file
from app.research_trusted_roots import TrustedFixtureRootV1, TrustedSnapshotRootV1


class RegistrationV2Error(ValueError):
    pass


_HEX = frozenset("0123456789abcdef")
_BODY_KEYS = {
    "experiment_id",
    "data_cutoff",
    "development_window",
    "final_oos_start",
    "trusted_snapshot_root",
    "trusted_fixture_root",
    "topology",
    "determinism",
    "output_policy",
    "budgets",
    "safety",
}
_REGISTRATION_KEYS = {
    "schema",
    "registration_id",
    "body_canonical_sha256",
    "body",
    "registration_canonical_sha256",
}
_RECEIPT_KEYS = {
    "schema",
    "registration_id",
    "registration_file_bytes",
    "registration_file_sha256",
    "registration_canonical_sha256",
    "publish_stop",
    "single_writer",
    "atomic_publish",
    "outputs_created",
    "network_calls",
    "purged_calls",
    "receipt_canonical_sha256",
}
_T = TypeVar("_T")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _HEX


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise RegistrationV2Error(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistrationV2Error(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise RegistrationV2Error(f"{label} is not an object")
    return value


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise RegistrationV2Error(f"{label} fields are not exact")
    return value


def _validate_body(value: Any) -> dict[str, Any]:
    body = dict(_exact(value, _BODY_KEYS, "registration body"))
    snapshot = TrustedSnapshotRootV1.from_dict(body["trusted_snapshot_root"])
    fixture = TrustedFixtureRootV1.from_dict(body["trusted_fixture_root"])
    if fixture.snapshot != snapshot:
        raise RegistrationV2Error("registration trusted snapshot tuples differ")
    topology = _exact(
        body["topology"],
        {"logical_calls", "physical_raw_files", "segments", "zero_rows", "source_indices"},
        "registration topology",
    )
    determinism = _exact(
        body["determinism"],
        {"call_order", "sort_keys", "random_seed", "workers"},
        "registration determinism",
    )
    output = _exact(
        body["output_policy"],
        {"fresh_nonexistent", "single_writer", "atomic_publish", "overwrite", "reuse", "hardlink"},
        "registration output policy",
    )
    budgets = _exact(body["budgets"], {"network", "credential_access", "purged"}, "registration budgets")
    safety = _exact(
        body["safety"],
        {
            "eligible_pool_count",
            "production_recommendation_eligible",
            "embargo_allowed",
            "final_oos_allowed",
            "deployment_allowed",
            "orders_allowed",
            "push_allowed",
            "paper_trading_validated",
            "live_ready",
        },
        "registration safety",
    )
    if (
        body["experiment_id"] != "auto-iter-038-h1-hold3-development"
        or body["data_cutoff"] != "2026-07-10"
        or body["development_window"] != {"start": "2022-01-04", "end": "2023-12-29"}
        or body["final_oos_start"] != "2026-07-13"
        or fixture.evidence_use != "research_development_historical"
        or topology
        != {
            "logical_calls": 53,
            "physical_raw_files": 50,
            "segments": [14, 39],
            "zero_rows": [4, 20],
            "source_indices": list(range(53)),
        }
        or fixture.call_count != 53
        or fixture.physical_raw_file_count != 50
        or determinism
        != {
            "call_order": "source_index_ascending",
            "sort_keys": ["symbol", "date"],
            "random_seed": 0,
            "workers": 1,
        }
        or output
        != {
            "fresh_nonexistent": True,
            "single_writer": True,
            "atomic_publish": True,
            "overwrite": False,
            "reuse": False,
            "hardlink": False,
        }
        or budgets != {"network": 0, "credential_access": 0, "purged": 1}
        or safety
        != {
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
            "embargo_allowed": False,
            "final_oos_allowed": False,
            "deployment_allowed": False,
            "orders_allowed": False,
            "push_allowed": False,
            "paper_trading_validated": False,
            "live_ready": False,
        }
    ):
        raise RegistrationV2Error("registration frozen contract mismatch")
    return body


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _rename_no_replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        try:
            os.rename(source, target)
        except FileExistsError:
            raise RegistrationV2Error("registration target already exists") from None
        return
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise RegistrationV2Error("atomic no-replace rename is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(target),
            1,
        )
    elif sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(library, "renamex_np", None)
        if renamex_np is None:
            raise RegistrationV2Error("atomic no-replace rename is unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(os.fsencode(source), os.fsencode(target), 4)
    else:
        raise RegistrationV2Error("atomic no-replace rename is unavailable")
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise RegistrationV2Error("registration target already exists")
    raise OSError(error, os.strerror(error), str(source), str(target))


def _signed(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result[field] = _sha(payload)
    return result


def _safe_directory(path: Path, label: str) -> Path:
    if not path.exists() or not stat.S_ISDIR(path.lstat().st_mode) or path.is_symlink():
        raise RegistrationV2Error(f"{label} is missing or unsafe")
    attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    if attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise RegistrationV2Error(f"{label} is a reparse point")
    return path.resolve(strict=True)


def _read_independent_file(path: Path) -> bytes:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or path.is_symlink()
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise RegistrationV2Error("registration artifact is not an independent regular file")
    return path.read_bytes()


def publish_registration_v2(
    parent: str | Path,
    *,
    body: Mapping[str, Any],
    expected_snapshot: TrustedSnapshotRootV1,
    expected_fixture: TrustedFixtureRootV1,
) -> dict[str, Any]:
    validated = _validate_body(body)
    if (
        not isinstance(expected_snapshot, TrustedSnapshotRootV1)
        or not isinstance(expected_fixture, TrustedFixtureRootV1)
        or expected_fixture.snapshot != expected_snapshot
        or validated["trusted_snapshot_root"] != expected_snapshot.to_dict()
        or validated["trusted_fixture_root"] != expected_fixture.to_dict()
    ):
        raise RegistrationV2Error("parent trusted root tuple mismatch")
    body_sha = _sha(validated)
    registration_id = _sha(
        {"schema": "experiment-038-registration-identity/v2", "body_canonical_sha256": body_sha}
    )
    registration = _signed(
        {
            "schema": "experiment-038-registration/v2",
            "registration_id": registration_id,
            "body_canonical_sha256": body_sha,
            "body": validated,
        },
        "registration_canonical_sha256",
    )
    registration_raw = _canonical_bytes(registration) + b"\n"
    receipt = _signed(
        {
            "schema": "experiment-038-registration-publish-receipt/v2",
            "registration_id": registration_id,
            "registration_file_bytes": len(registration_raw),
            "registration_file_sha256": _sha_bytes(registration_raw),
            "registration_canonical_sha256": registration["registration_canonical_sha256"],
            "publish_stop": True,
            "single_writer": True,
            "atomic_publish": True,
            "outputs_created": 0,
            "network_calls": 0,
            "purged_calls": 0,
        },
        "receipt_canonical_sha256",
    )
    parent_path = Path(parent)
    if parent_path.exists():
        _safe_directory(parent_path, "registration parent")
    else:
        parent_path.mkdir(parents=True)
    target = parent_path / registration_id
    lock_path = parent_path / f".registration-v2-{registration_id}.lock"
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        raise RegistrationV2Error("registration writer claim already exists") from None
    try:
        os.write(lock_descriptor, registration_id.encode("ascii"))
        os.fsync(lock_descriptor)
    finally:
        os.close(lock_descriptor)
    fsync_directory(parent_path)
    if target.exists():
        lock_path.unlink()
        fsync_directory(parent_path)
        raise RegistrationV2Error("registration target already exists")
    staging = parent_path / f".registration-v2-{os.getpid()}-{time.time_ns()}"
    staging.mkdir()
    _write_new(staging / "registration.json", registration_raw)
    _write_new(staging / "publish-receipt.json", _canonical_bytes(receipt) + b"\n")
    fsync_file(staging / "registration.json")
    fsync_file(staging / "publish-receipt.json")
    fsync_directory(staging)
    if target.exists():
        raise RegistrationV2Error("registration target appeared after writer claim")
    _rename_no_replace(staging, target)
    fsync_directory(parent_path)
    files = sorted(path.name for path in target.iterdir())
    if files != ["publish-receipt.json", "registration.json"]:
        raise RegistrationV2Error("registration publish tree mismatch")
    lock_path.unlink()
    fsync_directory(parent_path)
    return {
        "registration_root": str(target),
        "registration_id": registration_id,
        "registration_file_sha256": _sha_bytes(registration_raw),
        "registration_canonical_sha256": registration["registration_canonical_sha256"],
        "receipt_file_sha256": _sha_bytes(_read_independent_file(target / "publish-receipt.json")),
        "receipt_canonical_sha256": receipt["receipt_canonical_sha256"],
        "outputs_created": 0,
        "network_calls": 0,
        "purged_calls": 0,
    }


def load_frozen_registration(
    root: str | Path,
    *,
    expected_file_sha256: str,
    expected_canonical_sha256: str,
) -> dict[str, Any]:
    if not _is_sha(expected_file_sha256) or not _is_sha(expected_canonical_sha256):
        raise RegistrationV2Error("parent registration hashes are required")
    root_path = _safe_directory(Path(root), "registration root")
    registration_raw = _read_independent_file(root_path / "registration.json")
    if not hmac.compare_digest(_sha_bytes(registration_raw), expected_file_sha256):
        raise RegistrationV2Error("parent registration file SHA mismatch")
    registration = dict(_exact(_strict_json(registration_raw, "registration"), _REGISTRATION_KEYS, "registration"))
    unsigned = dict(registration)
    claimed = unsigned.pop("registration_canonical_sha256", None)
    if (
        claimed != expected_canonical_sha256
        or not hmac.compare_digest(_sha(unsigned), expected_canonical_sha256)
    ):
        raise RegistrationV2Error("parent registration canonical SHA mismatch")
    body = _validate_body(registration.get("body"))
    if (
        registration.get("body_canonical_sha256") != _sha(body)
        or registration.get("registration_id")
        != _sha(
            {
                "schema": "experiment-038-registration-identity/v2",
                "body_canonical_sha256": registration["body_canonical_sha256"],
            }
        )
        or root_path.name != registration.get("registration_id")
    ):
        raise RegistrationV2Error("registration content address mismatch")
    receipt_raw = _read_independent_file(root_path / "publish-receipt.json")
    receipt = dict(_exact(_strict_json(receipt_raw, "registration receipt"), _RECEIPT_KEYS, "registration receipt"))
    receipt_unsigned = dict(receipt)
    receipt_claimed = receipt_unsigned.pop("receipt_canonical_sha256", None)
    if (
        receipt_claimed != _sha(receipt_unsigned)
        or receipt.get("registration_id") != registration["registration_id"]
        or receipt.get("registration_file_bytes") != len(registration_raw)
        or receipt.get("registration_file_sha256") != expected_file_sha256
        or receipt.get("registration_canonical_sha256") != expected_canonical_sha256
        or receipt.get("publish_stop") is not True
        or receipt.get("single_writer") is not True
        or receipt.get("atomic_publish") is not True
        or receipt.get("outputs_created") != 0
        or receipt.get("network_calls") != 0
        or receipt.get("purged_calls") != 0
        or sorted(path.name for path in root_path.iterdir())
        != ["publish-receipt.json", "registration.json"]
    ):
        raise RegistrationV2Error("registration publish receipt mismatch")
    return registration


def run_frozen_registration_v2(
    root: str | Path,
    *,
    expected_file_sha256: str,
    expected_canonical_sha256: str,
    runner: Callable[[dict[str, Any]], _T],
) -> _T:
    """Invoke a runner only after the parent-frozen registration is reloaded."""

    registration = load_frozen_registration(
        root,
        expected_file_sha256=expected_file_sha256,
        expected_canonical_sha256=expected_canonical_sha256,
    )
    return runner(registration)
