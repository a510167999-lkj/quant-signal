"""Publish a development-only repair receipt for a failed verifier wrapper.

The repair path never mutates the original failed terminal status and never
re-runs the formal research job.  It revalidates the already-completed isolated
replay after reconstructing its content-addressed runtime-verification envelope.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import verify_shallow_gbdt_probability_budget_development_1 as verifier  # noqa: E402


SCRIPT_GIT_PATH = "scripts/repair_shallow_gbdt_probability_budget_independent_verification.py"
CORE_SCRIPT_GIT_PATH = "scripts/verify_shallow_gbdt_probability_budget_development_1.py"
PREREGISTRATION_NAME = "formal_run.probability_budget_independent_verification_repair.preregistration.json"
STATUS_NAME = "formal_run.probability_budget_independent_verification_repair.status.json"
CLAIM_NAME = "formal_run.probability_budget_independent_verification_repair.claim"
RECEIPT_ROOT_NAME = "probability_budget_independent_verification_repair_receipts"
PREREGISTRATION_SCHEMA = "ranked-liquidity-shallow-gbdt-probability-budget-independent-verification-repair-preregistration/v1"
STATUS_SCHEMA = "ranked-liquidity-shallow-gbdt-probability-budget-independent-verification-repair-status/v1"
RECEIPT_SCHEMA = "ranked-liquidity-shallow-gbdt-probability-budget-independent-verification-repair-receipt/v1"
LEGACY_ENVELOPE_MISMATCH_VERIFIER_COMMIT = "29353a4f2df6991fae56b9e24c566afafd58ed4f"
LEGACY_ENVELOPE_MISMATCH_VERIFIER_BLOB_SHA256 = "736779d16d0a39e251876d53ccf69ccdf128da6164d68a63b6f6b1c98a938e82"


class RepairVerificationError(RuntimeError):
    """Raised when the failed independent-verification lineage cannot be repaired."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise RepairVerificationError("repair verifier git inspection failed")
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RepairVerificationError("repair verifier git output is invalid") from exc


def _require_sha256(value: Any, label: str) -> str:
    try:
        return verifier._require_sha256(value, label)
    except verifier.IndependentVerificationError as exc:
        raise RepairVerificationError(f"{label} is invalid") from exc


def _identity(expected_repair_commit: str) -> dict[str, str]:
    try:
        expected = verifier._validate_expected_commit(expected_repair_commit)
    except verifier.IndependentVerificationError as exc:
        raise RepairVerificationError("expected repair commit is invalid") from exc
    if Path(_git_output("rev-parse", "--show-toplevel")).resolve() != PROJECT_ROOT:
        raise RepairVerificationError("repair verifier root is not a Git worktree root")
    if _git_output("status", "--porcelain"):
        raise RepairVerificationError("repair verifier worktree is not clean")
    if _git_output("rev-parse", "HEAD").lower() != expected:
        raise RepairVerificationError("repair verifier commit drifted")
    scripts = {
        "repair": (SCRIPT_GIT_PATH, SCRIPT_PATH),
        "core": (CORE_SCRIPT_GIT_PATH, verifier.SCRIPT_PATH),
    }
    identity: dict[str, str] = {"git_commit": expected}
    for name, (git_path, path) in scripts.items():
        completed = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "show", f"{expected}:{git_path}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        normalized = path.read_bytes().replace(b"\r\n", b"\n")
        if completed.returncode != 0 or completed.stdout != normalized:
            raise RepairVerificationError(f"{name} verifier entrypoint does not match its commit")
        identity[f"{name}_script_sha256"] = verifier._sha256_file(path)
        identity[f"{name}_script_git_blob_sha256"] = verifier._sha256_bytes(completed.stdout)
    return identity


def _read_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = verifier._read_bounded(path, label)
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, verifier.IndependentVerificationError) as exc:
        raise RepairVerificationError(f"{label} is invalid") from exc
    if not isinstance(document, dict):
        raise RepairVerificationError(f"{label} is not an object")
    return document, raw


def _require_false(document: Mapping[str, Any], field: str, label: str) -> None:
    if document.get(field) is not False:
        raise RepairVerificationError(f"{label} is not fail-closed")


def _load_prior_failure(inputs: Mapping[str, Any]) -> dict[str, Any]:
    run_root = Path(inputs["run_root"])
    status_path = verifier._safe_child(run_root, verifier.STATUS_NAME, "original independent verification status")
    status, status_raw = _read_object(status_path, "original independent verification status")
    expected_status_fields = {
        "schema_version", "status", "stage", "pid", "finished_at_utc", "run_root", "claim_sha256",
        "verified", "receipt", "error_type", "development_statistical_interpretation_allowed",
        "profile_registration_authority", "production_recommendation_authority", "automatic_trading_authority",
        "development_only", "embargo_consumed", "final_oos_consumed", "production_authority",
    }
    if (
        set(status) != expected_status_fields
        or status.get("schema_version") != verifier.STATUS_SCHEMA
        or status.get("status") != "failed"
        or status.get("stage") != "failed"
        or status.get("run_root") != verifier.RUN_ROOT_NAME
        or status.get("verified") is not False
        or status.get("receipt") is not None
        or status.get("error_type") != "IndependentVerificationError"
        or not isinstance(status.get("pid"), int)
        or not isinstance(status.get("finished_at_utc"), str)
        or not status["finished_at_utc"]
        or not isinstance(status.get("development_only"), bool)
        or status["development_only"] is not True
    ):
        raise RepairVerificationError("original independent verification is not a preserved failure")
    for field in (
        "development_statistical_interpretation_allowed", "profile_registration_authority",
        "production_recommendation_authority", "automatic_trading_authority", "embargo_consumed",
        "final_oos_consumed", "production_authority",
    ):
        _require_false(status, field, "original independent verification status")
    _require_sha256(status.get("claim_sha256"), "original claim")

    preregistration_path = verifier._safe_child(
        run_root, verifier.PREREGISTRATION_NAME, "original independent verification preregistration"
    )
    preregistration, preregistration_raw = _read_object(
        preregistration_path, "original independent verification preregistration"
    )
    expected_preregistration_fields = {
        "schema_version", "run_root", "completion_sha256", "main_artifact_sha256",
        "runtime_verification_artifact_sha256", "source_git_commit", "source_python_sha256",
        "strategy_sha256", "producer_root_sha256", "replay_plan_sha256", "verifier_git_commit",
        "verifier_script_sha256", "verifier_script_git_blob_sha256", "scope", "resource_contract",
    }
    if (
        set(preregistration) != expected_preregistration_fields
        or preregistration.get("schema_version") != verifier.PREREGISTRATION_SCHEMA
        or preregistration.get("run_root") != verifier.RUN_ROOT_NAME
        or _require_sha256(preregistration.get("completion_sha256"), "original completion") != inputs["completion_sha256"]
        or _require_sha256(preregistration.get("main_artifact_sha256"), "original main artifact") != inputs["main_artifact_sha256"]
        or _require_sha256(preregistration.get("runtime_verification_artifact_sha256"), "original runtime artifact") != inputs["runtime_verification_artifact_sha256"]
        or preregistration.get("source_git_commit") != inputs["source"]["git_commit"]
        or _require_sha256(preregistration.get("source_python_sha256"), "original source Python") != inputs["source"]["python_executable_sha256"]
        or _require_sha256(preregistration.get("strategy_sha256"), "original strategy") != inputs["source"]["strategy_sha256"]
        or _require_sha256(preregistration.get("producer_root_sha256"), "original producer") != inputs["source"]["producer_root_sha256"]
        or _require_sha256(preregistration.get("replay_plan_sha256"), "original replay plan") != verifier.EXPECTED_REPLAY_PLAN_SHA256
        or preregistration.get("verifier_git_commit") != LEGACY_ENVELOPE_MISMATCH_VERIFIER_COMMIT
        or _require_sha256(
            preregistration.get("verifier_script_git_blob_sha256"),
            "original verifier script blob",
        ) != LEGACY_ENVELOPE_MISMATCH_VERIFIER_BLOB_SHA256
        or not isinstance(preregistration.get("scope"), dict)
        or preregistration["scope"] != {
            "point_in_time": True, "development_only": True, "embargo_consumed": False,
            "final_oos_consumed": False, "production_authority": False,
            "automatic_trading_authority": False,
        }
        or preregistration.get("resource_contract") != {"memory_policy": "unbounded", "enforcement": "none"}
    ):
        raise RepairVerificationError("original preregistration binding is invalid")
    _require_sha256(preregistration.get("verifier_script_sha256"), "original verifier script")
    return {
        "status_path": status_path,
        "status_sha256": verifier._sha256_bytes(status_raw),
        "preregistration_path": preregistration_path,
        "preregistration_sha256": verifier._sha256_bytes(preregistration_raw),
    }


def _load_existing_replay(inputs: Mapping[str, Any]) -> dict[str, Any]:
    run_root = Path(inputs["run_root"])
    result_path = verifier._safe_child(run_root, verifier.REPLAY_RESULT_NAME, "original isolated replay result")
    stdout_path = verifier._safe_child(run_root, verifier.REPLAY_STDOUT_NAME, "original isolated replay stdout")
    stderr_path = verifier._safe_child(run_root, verifier.REPLAY_STDERR_NAME, "original isolated replay stderr")
    replay, _ = _read_object(result_path, "original isolated replay result")
    try:
        replayed_verification = replay.get("verification")
        if not isinstance(replayed_verification, dict) or "artifact_sha256" in replayed_verification:
            raise verifier.IndependentVerificationError("legacy replay envelope differs")
        if {
            **replayed_verification,
            "artifact_sha256": inputs["runtime_verification_artifact_sha256"],
        } != inputs["runtime_verification"]:
            raise verifier.IndependentVerificationError("legacy replay cannot be reconstructed")
        verifier._validate_replay(inputs, {"result": replay})
        stdout = verifier._regular_file(stdout_path, "original isolated replay stdout")
        stderr = verifier._regular_file(stderr_path, "original isolated replay stderr")
    except verifier.IndependentVerificationError as exc:
        raise RepairVerificationError("original isolated replay cannot be revalidated") from exc
    return {
        "result_path": result_path,
        "result_sha256": verifier._sha256_file(result_path),
        "stdout": {"path": stdout.name, "bytes": stdout.stat().st_size, "sha256": verifier._sha256_file(stdout)},
        "stderr": {"path": stderr.name, "bytes": stderr.stat().st_size, "sha256": verifier._sha256_file(stderr)},
    }


def _binding(prepared: Mapping[str, Any]) -> dict[str, Any]:
    inputs = prepared["inputs"]
    prior_failure = prepared["prior_failure"]
    replay = prepared["replay"]
    return {
        "identity": dict(prepared["identity"]),
        "inputs": {
            "completion_sha256": inputs["completion_sha256"],
            "main_artifact_sha256": inputs["main_artifact_sha256"],
            "runtime_verification_artifact_sha256": inputs["runtime_verification_artifact_sha256"],
            "source": {
                "git_commit": inputs["source"]["git_commit"],
                "python_executable_sha256": inputs["source"]["python_executable_sha256"],
                "strategy_sha256": inputs["source"]["strategy_sha256"],
                "producer_root_sha256": inputs["source"]["producer_root_sha256"],
            },
        },
        "prior_failure": {
            "status_sha256": prior_failure["status_sha256"],
            "preregistration_sha256": prior_failure["preregistration_sha256"],
        },
        "replay": {
            "result_sha256": replay["result_sha256"],
            "stdout": dict(replay["stdout"]),
            "stderr": dict(replay["stderr"]),
        },
    }


def _require_stable_preflight(expected: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    if not hmac.compare_digest(
        verifier._canonical_bytes(_binding(expected)),
        verifier._canonical_bytes(_binding(current)),
    ):
        raise RepairVerificationError("repair evidence changed after preflight")


class _HeldWindowsRunRoot:
    def __init__(
        self,
        run_root: Path,
        *,
        expected_chain: tuple[tuple[int, int], ...] | None = None,
        include_receipt_tree: bool = False,
    ) -> None:
        self.run_root = run_root
        self.expected_chain = expected_chain
        self.include_receipt_tree = include_receipt_tree
        self._handles: list[tuple[int, tuple[int, int]]] = []
        self._run_chain: tuple[tuple[int, int], ...] = ()
        self._run_root_handle: int | None = None
        self._receipt_directory_handle: int | None = None
        self._run_root_path: Path | None = None
        self._held_read_descriptors: list[int] = []
        self._terminal_status_committed = False

    @staticmethod
    def _close(handle: int) -> bool:
        import ctypes
        from ctypes import wintypes

        closer = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        closer.argtypes = (wintypes.HANDLE,)
        closer.restype = wintypes.BOOL
        return bool(closer(handle))

    @staticmethod
    def _attribute_tag(handle: int) -> tuple[bool, bool]:
        import ctypes
        from ctypes import wintypes

        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

        get_information = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        get_information.restype = wintypes.BOOL
        info = FileAttributeTagInfo()
        if not get_information(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise RepairVerificationError("repair run-root handle metadata is unavailable")
        return bool(info.file_attributes & 0x00000010), bool(info.file_attributes & 0x00000400)

    @classmethod
    def _directory_identity(cls, handle: int) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation_time", FileTime),
                ("last_access_time", FileTime),
                ("last_write_time", FileTime),
                ("volume_serial", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("link_count", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        is_directory, is_reparse = cls._attribute_tag(handle)
        if not is_directory or is_reparse:
            raise RepairVerificationError("repair run-root directory handle is unsafe")
        get_information = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
        get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation))
        get_information.restype = wintypes.BOOL
        info = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(info)):
            raise RepairVerificationError("repair run-root directory identity is unavailable")
        return int(info.volume_serial), (int(info.file_index_high) << 32) | int(info.file_index_low)

    @staticmethod
    def _validate_leaf_name(name: str) -> None:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or "\x00" in name
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise RepairVerificationError("repair relative name is invalid")

    @classmethod
    def _nt_create_relative(
        cls,
        *,
        parent_handle: int,
        name: str,
        desired_access: int,
        file_attributes: int,
        create_disposition: int,
        create_options: int,
        share_mode: int = 0x00000001 | 0x00000002,
    ) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.USHORT),
                ("maximum_length", wintypes.USHORT),
                ("buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.ULONG),
                ("root_directory", wintypes.HANDLE),
                ("object_name", ctypes.POINTER(UnicodeString)),
                ("attributes", wintypes.ULONG),
                ("security_descriptor", wintypes.LPVOID),
                ("security_quality_of_service", wintypes.LPVOID),
            ]

        class IoStatusValue(ctypes.Union):
            _fields_ = [("status", wintypes.LONG), ("pointer", wintypes.LPVOID)]

        class IoStatusBlock(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("value", IoStatusValue), ("information", ctypes.c_size_t)]

        cls._validate_leaf_name(name)
        ntdll = ctypes.WinDLL("ntdll")
        create_file = ntdll.NtCreateFile
        create_file.argtypes = (
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        )
        create_file.restype = wintypes.LONG
        status_to_error = ntdll.RtlNtStatusToDosError
        status_to_error.argtypes = (wintypes.LONG,)
        status_to_error.restype = wintypes.ULONG
        encoded = name.encode("utf-16-le")
        buffer = ctypes.create_unicode_buffer(name)
        unicode_name = UnicodeString(
            len(encoded),
            len(encoded),
            ctypes.cast(buffer, wintypes.LPWSTR),
        )
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            parent_handle,
            ctypes.pointer(unicode_name),
            0x00000040,
            None,
            None,
        )
        io_status = IoStatusBlock()
        handle = wintypes.HANDLE()
        result = create_file(
            ctypes.byref(handle),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            file_attributes,
            share_mode,
            create_disposition,
            create_options,
            None,
            0,
        )
        raw_handle = int(handle.value) if handle.value is not None else None
        invalid_handle = ctypes.c_void_p(-1).value
        if result < 0 or raw_handle in (None, invalid_handle):
            raise OSError(int(status_to_error(result)), "repair relative open rejected")
        return raw_handle, int(io_status.information)

    @classmethod
    def _open_volume_root(cls, path: Path) -> int:
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
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
        handle = create_file(
            str(path),
            0x00000001 | 0x00000020 | 0x00000080 | 0x00100000,
            0x00000001 | 0x00000002,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, invalid_handle):
            raise RepairVerificationError("repair volume-root handle is unavailable")
        raw_handle = int(handle)
        try:
            cls._directory_identity(raw_handle)
            return raw_handle
        except BaseException:
            try:
                cls._close(raw_handle)
            except BaseException:
                pass
            raise

    @classmethod
    def _open_directory(cls, parent_handle: int, name: str, *, create: bool) -> int:
        try:
            handle, _ = cls._nt_create_relative(
                parent_handle=parent_handle,
                name=name,
                desired_access=0x00000001 | 0x00000020 | 0x00000080 | 0x00100000,
                file_attributes=0x00000010,
                create_disposition=3 if create else 1,
                create_options=0x00000001 | 0x00000020 | 0x00200000,
            )
        except OSError as exc:
            raise RepairVerificationError("repair directory handle open rejected") from exc
        try:
            cls._directory_identity(handle)
            return handle
        except BaseException:
            try:
                cls._close(handle)
            except BaseException:
                pass
            raise

    @staticmethod
    def _error_code(exc: OSError) -> int | None:
        value = getattr(exc, "winerror", None) or exc.errno
        return int(value) if value is not None else None

    def _hold_directory(self, handle: int) -> None:
        self._handles.append((handle, self._directory_identity(handle)))

    def _absolute_components(self) -> tuple[Path, tuple[str, ...]]:
        path = Path(os.path.abspath(str(self.run_root)))
        if not path.is_absolute() or not path.drive or str(path.anchor).startswith("\\\\"):
            raise RepairVerificationError("repair run root must be a local absolute path")
        components = tuple(path.parts[1:])
        if not components:
            raise RepairVerificationError("repair run root is invalid")
        for component in components:
            self._validate_leaf_name(component)
        return path, components

    def __enter__(self) -> _HeldWindowsRunRoot:
        if os.name != "nt":
            raise RepairVerificationError("repair publication requires Windows handle binding")
        path, components = self._absolute_components()
        try:
            current = self._open_volume_root(Path(path.anchor))
            self._hold_directory(current)
            for component in components:
                current = self._open_directory(current, component, create=False)
                self._hold_directory(current)
            self._run_root_handle = current
            self._run_root_path = path
            self._run_chain = tuple(identity for _handle, identity in self._handles)
            if self.expected_chain is not None and self._run_chain != self.expected_chain:
                raise RepairVerificationError("repair run-root directory chain drifted")
            if self.include_receipt_tree:
                receipt_root = self._open_directory(current, RECEIPT_ROOT_NAME, create=True)
                self._hold_directory(receipt_root)
                self._receipt_directory_handle = self._open_directory(receipt_root, "sha256", create=True)
                self._hold_directory(self._receipt_directory_handle)
            return self
        except BaseException:
            self._close_all()
            raise

    def __exit__(self, exc_type: object, *_: Any) -> None:
        try:
            if exc_type is None and not self._terminal_status_committed:
                self.assert_bound()
        finally:
            self._close_all()

    def _close_all(self) -> None:
        for descriptor in reversed(self._held_read_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._held_read_descriptors.clear()
        for handle, _identity in reversed(self._handles):
            try:
                self._close(handle)
            except BaseException:
                pass
        self._handles.clear()
        self._run_root_handle = None
        self._receipt_directory_handle = None

    @property
    def chain_identity(self) -> tuple[tuple[int, int], ...]:
        if not self._run_chain:
            raise RepairVerificationError("repair run-root directory chain is unavailable")
        return self._run_chain

    def assert_bound(self) -> None:
        if self._run_root_handle is None or not self._run_chain:
            raise RepairVerificationError("repair run-root directory chain is unavailable")
        current = tuple(self._directory_identity(handle) for handle, _identity in self._handles[:len(self._run_chain)])
        if current != self._run_chain:
            raise RepairVerificationError("repair run-root directory chain drifted")
        if self.expected_chain is not None and current != self.expected_chain:
            raise RepairVerificationError("repair run-root directory chain drifted")

    def mark_terminal_status_committed(self) -> None:
        self._terminal_status_committed = True

    def root_file_exists(self, name: str) -> bool:
        if self._run_root_handle is None:
            raise RepairVerificationError("repair run-root handle is unavailable")
        try:
            handle, _ = self._nt_create_relative(
                parent_handle=self._run_root_handle,
                name=name,
                desired_access=0x00000080 | 0x00100000,
                file_attributes=0x00000080,
                create_disposition=1,
                create_options=0x00000020 | 0x00000040 | 0x00200000,
            )
        except OSError as exc:
            if self._error_code(exc) in {2, 3}:
                return False
            raise RepairVerificationError("repair root-file probe rejected") from exc
        try:
            is_directory, is_reparse = self._attribute_tag(handle)
            if is_directory or is_reparse:
                raise RepairVerificationError("repair root-file probe is unsafe")
            return True
        finally:
            try:
                self._close(handle)
            except BaseException:
                pass

    def _write_relative_once(
        self,
        *,
        parent_handle: int,
        name: str,
        raw: bytes,
        label: str,
        tolerate_close_failure: bool = False,
    ) -> None:
        import msvcrt

        try:
            handle, _ = self._nt_create_relative(
                parent_handle=parent_handle,
                name=name,
                desired_access=0x00000001 | 0x00000002 | 0x00000080 | 0x00100000,
                file_attributes=0x00000080,
                create_disposition=2,
                create_options=0x00000020 | 0x00000040 | 0x00200000,
            )
            created = True
        except OSError as exc:
            if self._error_code(exc) not in {80, 183}:
                raise RepairVerificationError(f"{label} creation rejected") from exc
            try:
                handle, _ = self._nt_create_relative(
                    parent_handle=parent_handle,
                    name=name,
                    desired_access=0x00000001 | 0x00000080 | 0x00100000,
                    file_attributes=0x00000080,
                    create_disposition=1,
                    create_options=0x00000020 | 0x00000040 | 0x00200000,
                )
            except OSError as open_exc:
                raise RepairVerificationError(f"{label} collision open rejected") from open_exc
            created = False
        descriptor = -1
        try:
            is_directory, is_reparse = self._attribute_tag(handle)
            if is_directory or is_reparse:
                raise RepairVerificationError(f"{label} file handle is unsafe")
            descriptor = msvcrt.open_osfhandle(
                handle,
                (os.O_RDWR if created else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
            )
            handle = -1
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or int(getattr(metadata, "st_nlink", 1)) != 1:
                raise RepairVerificationError(f"{label} file handle is unsafe")
            if created:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise RepairVerificationError(f"{label} write rejected")
                    view = view[written:]
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            observed = b""
            while len(observed) <= len(raw):
                chunk = os.read(descriptor, min(64 * 1024, len(raw) + 1 - len(observed)))
                if not chunk:
                    break
                observed += chunk
            if not hmac.compare_digest(observed, raw):
                raise RepairVerificationError(f"{label} collision or terminal verification failed")
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    if not tolerate_close_failure:
                        raise
            elif handle != -1:
                try:
                    self._close(handle)
                except BaseException:
                    pass

    def write_root_once(
        self,
        *,
        name: str,
        raw: bytes,
        label: str,
        tolerate_close_failure: bool = False,
    ) -> Path:
        if self._run_root_handle is None or self._run_root_path is None:
            raise RepairVerificationError("repair run-root handle is unavailable")
        self._write_relative_once(
            parent_handle=self._run_root_handle,
            name=name,
            raw=raw,
            label=label,
            tolerate_close_failure=tolerate_close_failure,
        )
        return self._run_root_path / name

    def _read_relative_bounded(
        self,
        *,
        parent_handle: int,
        name: str,
        label: str,
        maximum: int = verifier.MAX_JSON_BYTES,
        hold: bool = False,
    ) -> bytes:
        import msvcrt

        try:
            handle, _ = self._nt_create_relative(
                parent_handle=parent_handle,
                name=name,
                desired_access=0x00000001 | 0x00000080 | 0x00100000,
                file_attributes=0x00000080,
                create_disposition=1,
                create_options=0x00000020 | 0x00000040 | 0x00200000,
                share_mode=0x00000001,
            )
        except OSError as exc:
            raise RepairVerificationError(f"{label} open rejected") from exc
        descriptor = -1
        try:
            is_directory, is_reparse = self._attribute_tag(handle)
            if is_directory or is_reparse:
                raise RepairVerificationError(f"{label} file handle is unsafe")
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            handle = -1
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or int(getattr(metadata, "st_nlink", 1)) != 1
                or metadata.st_size > maximum
            ):
                raise RepairVerificationError(f"{label} file handle is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise RepairVerificationError(f"{label} exceeds its bound")
            raw = b"".join(chunks)
            if hold:
                self._held_read_descriptors.append(descriptor)
                descriptor = -1
            return raw
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            elif handle != -1:
                try:
                    self._close(handle)
                except BaseException:
                    pass

    def read_root_bounded(self, *, name: str, label: str, hold: bool = False) -> bytes:
        if self._run_root_handle is None:
            raise RepairVerificationError("repair run-root handle is unavailable")
        return self._read_relative_bounded(
            parent_handle=self._run_root_handle,
            name=name,
            label=label,
            hold=hold,
        )

    def read_receipt_bounded(self, *, digest: str, label: str, hold: bool = False) -> bytes:
        if self._run_root_handle is None or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RepairVerificationError("repair receipt lookup is invalid")
        receipt_root = self._open_directory(self._run_root_handle, RECEIPT_ROOT_NAME, create=False)
        sha_root: int | None = None
        try:
            sha_root = self._open_directory(receipt_root, "sha256", create=False)
            raw = self._read_relative_bounded(
                parent_handle=sha_root,
                name=f"{digest}.json",
                label=label,
                hold=hold,
            )
            if hold:
                self._hold_directory(receipt_root)
                self._hold_directory(sha_root)
                receipt_root = None
                sha_root = None
            return raw
        finally:
            for handle in (sha_root, receipt_root):
                if handle is not None:
                    try:
                        self._close(handle)
                    except BaseException:
                        pass

    def write_receipt_once(self, *, digest: str, raw: bytes) -> Path:
        try:
            document = json.loads(raw.decode("utf-8"))
            unsigned = dict(document)
            embedded = unsigned.pop("receipt_sha256")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RepairVerificationError("repair receipt payload is invalid") from exc
        if (
            self._receipt_directory_handle is None
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or embedded != digest
            or verifier._sha256_bytes(verifier._canonical_bytes(unsigned)) != digest
        ):
            raise RepairVerificationError("repair receipt payload is invalid")
        name = f"{digest}.json"
        self._write_relative_once(
            parent_handle=self._receipt_directory_handle,
            name=name,
            raw=raw,
            label="repair receipt",
        )
        if self._run_root_path is None:
            raise RepairVerificationError("repair run-root path is unavailable")
        return self._run_root_path / RECEIPT_ROOT_NAME / "sha256" / name


def preflight(source_root: str, expected_repair_commit: str) -> dict[str, Any]:
    identity = _identity(expected_repair_commit)
    try:
        source = verifier._source_identity(source_root)
        inputs = verifier._load_completed_run(source)
    except verifier.IndependentVerificationError as exc:
        raise RepairVerificationError("formal development inputs cannot be repaired") from exc
    prior_failure = _load_prior_failure(inputs)
    replay = _load_existing_replay(inputs)
    return {"identity": identity, "inputs": inputs, "prior_failure": prior_failure, "replay": replay}


def _preregistration_raw(prepared: Mapping[str, Any]) -> bytes:
    inputs = prepared["inputs"]
    prior_failure = prepared["prior_failure"]
    replay = prepared["replay"]
    body = {
        "schema_version": PREREGISTRATION_SCHEMA,
        "run_root": verifier.RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "main_artifact_sha256": inputs["main_artifact_sha256"],
        "runtime_verification_artifact_sha256": inputs["runtime_verification_artifact_sha256"],
        "original_failure_status_sha256": prior_failure["status_sha256"],
        "original_preregistration_sha256": prior_failure["preregistration_sha256"],
        "original_replay_result_sha256": replay["result_sha256"],
        "original_replay_stdout_sha256": replay["stdout"]["sha256"],
        "original_replay_stderr_sha256": replay["stderr"]["sha256"],
        "source_git_commit": inputs["source"]["git_commit"],
        "source_python_sha256": inputs["source"]["python_executable_sha256"],
        "strategy_sha256": inputs["source"]["strategy_sha256"],
        "producer_root_sha256": inputs["source"]["producer_root_sha256"],
        "repair_verifier": dict(prepared["identity"]),
        "scope": {
            "point_in_time": True, "development_only": True, "embargo_consumed": False,
            "final_oos_consumed": False, "production_authority": False,
            "automatic_trading_authority": False,
        },
        "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
    }
    return verifier._canonical_bytes(body) + b"\n"


def _preregistration(
    prepared: Mapping[str, Any],
    *,
    publisher: _HeldWindowsRunRoot,
) -> dict[str, Any]:
    raw = _preregistration_raw(prepared)
    path = publisher.write_root_once(
        name=PREREGISTRATION_NAME,
        raw=raw,
        label="repair preregistration",
    )
    return {"path": path, "sha256": verifier._sha256_bytes(raw)}


def _new_claim(prepared: Mapping[str, Any], preregistration: Mapping[str, Any]) -> bytes:
    body = {
        "schema_version": STATUS_SCHEMA,
        "kind": "probability_budget_independent_verification_repair_claim",
        "nonce": secrets.token_hex(16),
        "pid": os.getpid(),
        "started_at_utc": _utc_now(),
        "completion_sha256": prepared["inputs"]["completion_sha256"],
        "prior_failure_status_sha256": prepared["prior_failure"]["status_sha256"],
        "preregistration_sha256": preregistration["sha256"],
        "claim_authority": {
            "terminal_repair_status_required": True,
            "claim_alone_authoritative": False,
        },
    }
    return verifier._canonical_bytes(body) + b"\n"


def _receipt_body(
    prepared: Mapping[str, Any],
    preregistration: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = prepared["inputs"]
    prior_failure = prepared["prior_failure"]
    body = {
        "schema_version": RECEIPT_SCHEMA,
        "run_root": verifier.RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "preregistration_sha256": preregistration["sha256"],
        "original_failure": {
            "status_path": prior_failure["status_path"].name,
            "status_sha256": prior_failure["status_sha256"],
            "preregistration_path": prior_failure["preregistration_path"].name,
            "preregistration_sha256": prior_failure["preregistration_sha256"],
        },
        "source": {
            "git_commit": inputs["source"]["git_commit"],
            "python_executable_sha256": inputs["source"]["python_executable_sha256"],
            "strategy_sha256": inputs["source"]["strategy_sha256"],
            "producer_root_sha256": inputs["source"]["producer_root_sha256"],
        },
        "repair_verifier": dict(prepared["identity"]),
        "original_isolated_replay": {
            "result_file_sha256": replay["result_sha256"],
            "stdout": replay["stdout"],
            "stderr": replay["stderr"],
        },
        "checks": {
            "prior_failed_status_preserved": True,
            "formal_completion_contract_verified": True,
            "source_commit_and_cleanliness_verified": True,
            "legacy_envelope_contract_proven": True,
            "corrected_artifact_envelope_reconstructed": True,
            "original_isolated_replay_revalidated": True,
            "point_in_time_development_scope_preserved": True,
        },
        "post_verification_authority": {
            "development_statistical_interpretation_allowed": False,
            "profile_registration_authority": False,
            "production_recommendation_authority": False,
            "automatic_trading_authority": False,
        },
        "receipt_authority": {
            "terminal_completed_repair_status_required": True,
            "current_input_revalidation_required": True,
            "receipt_alone_authoritative": False,
        },
        "scope": {
            "point_in_time": True, "development_only": True, "embargo_consumed": False,
            "final_oos_consumed": False, "production_authority": False,
            "automatic_trading_authority": False,
        },
        "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
    }
    return body


def _publish_receipt(
    prepared: Mapping[str, Any],
    preregistration: Mapping[str, Any],
    replay: Mapping[str, Any],
    publisher: _HeldWindowsRunRoot,
) -> dict[str, Any]:
    body = _receipt_body(prepared, preregistration, replay)
    receipt_sha256 = verifier._sha256_bytes(verifier._canonical_bytes(body))
    receipt = {**body, "receipt_sha256": receipt_sha256}
    raw = verifier._canonical_bytes(receipt) + b"\n"
    path = publisher.write_receipt_once(digest=receipt_sha256, raw=raw)
    return {"path": path, "sha256": receipt_sha256, "raw": raw}


def _canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepairVerificationError(f"{label} is invalid") from exc
    if not isinstance(value, dict) or verifier._canonical_bytes(value) + b"\n" != raw:
        raise RepairVerificationError(f"{label} is invalid")
    return value


def _require_utc_second_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RepairVerificationError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise RepairVerificationError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc or parsed.microsecond != 0:
        raise RepairVerificationError(f"{label} is invalid")
    return value


def _validate_completed_snapshot(
    prepared: Mapping[str, Any],
    *,
    publisher: _HeldWindowsRunRoot,
    hold_reads: bool = False,
) -> None:
    inputs = prepared["inputs"]
    preregistration_raw = publisher.read_root_bounded(
        name=PREREGISTRATION_NAME,
        label="repair preregistration",
        hold=hold_reads,
    )
    if not hmac.compare_digest(preregistration_raw, _preregistration_raw(prepared)):
        raise RepairVerificationError("repair preregistration drifted")
    claim_raw = publisher.read_root_bounded(
        name=CLAIM_NAME,
        label="repair claim",
        hold=hold_reads,
    )
    claim = _canonical_object(claim_raw, "repair claim")
    if (
        set(claim)
        != {
            "schema_version",
            "kind",
            "nonce",
            "pid",
            "started_at_utc",
            "completion_sha256",
            "prior_failure_status_sha256",
            "preregistration_sha256",
            "claim_authority",
        }
        or claim.get("schema_version") != STATUS_SCHEMA
        or claim.get("kind") != "probability_budget_independent_verification_repair_claim"
        or not isinstance(claim.get("nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", claim["nonce"]) is None
        or not isinstance(claim.get("pid"), int)
        or isinstance(claim.get("pid"), bool)
        or claim.get("completion_sha256") != inputs["completion_sha256"]
        or claim.get("prior_failure_status_sha256") != prepared["prior_failure"]["status_sha256"]
        or claim.get("preregistration_sha256") != verifier._sha256_bytes(preregistration_raw)
        or claim.get("claim_authority")
        != {"terminal_repair_status_required": True, "claim_alone_authoritative": False}
    ):
        raise RepairVerificationError("repair claim drifted")
    _require_utc_second_timestamp(claim["started_at_utc"], "repair claim timestamp")
    status_raw = publisher.read_root_bounded(
        name=STATUS_NAME,
        label="repair status",
        hold=hold_reads,
    )
    status = _canonical_object(status_raw, "repair status")
    if (
        set(status)
        != {
            "schema_version",
            "status",
            "stage",
            "pid",
            "finished_at_utc",
            "run_root",
            "claim_sha256",
            "original_failure_status_sha256",
            "verified",
            "receipt",
            "error_type",
            "development_statistical_interpretation_allowed",
            "profile_registration_authority",
            "production_recommendation_authority",
            "automatic_trading_authority",
            "repair_status_authority",
            "development_only",
            "embargo_consumed",
            "final_oos_consumed",
            "production_authority",
        }
        or status.get("schema_version") != STATUS_SCHEMA
        or status.get("status") != "completed"
        or status.get("stage") != "completed"
        or status.get("run_root") != verifier.RUN_ROOT_NAME
        or status.get("claim_sha256") != verifier._sha256_bytes(claim_raw)
        or status.get("original_failure_status_sha256") != prepared["prior_failure"]["status_sha256"]
        or status.get("verified") is not True
        or status.get("error_type") is not None
        or status.get("development_statistical_interpretation_allowed") is not False
        or status.get("profile_registration_authority") is not False
        or status.get("production_recommendation_authority") is not False
        or status.get("automatic_trading_authority") is not False
        or status.get("repair_status_authority")
        != {"current_input_revalidation_required": True, "status_alone_authoritative": False}
        or status.get("development_only") is not True
        or status.get("embargo_consumed") is not False
        or status.get("final_oos_consumed") is not False
        or status.get("production_authority") is not False
        or not isinstance(status.get("pid"), int)
        or isinstance(status.get("pid"), bool)
    ):
        raise RepairVerificationError("repair status drifted")
    _require_utc_second_timestamp(status["finished_at_utc"], "repair status timestamp")
    receipt_reference = status.get("receipt")
    if not isinstance(receipt_reference, dict) or set(receipt_reference) != {"path", "sha256"}:
        raise RepairVerificationError("repair receipt reference is invalid")
    receipt_sha256 = _require_sha256(receipt_reference["sha256"], "repair receipt reference")
    expected_relative_path = f"{RECEIPT_ROOT_NAME}/sha256/{receipt_sha256}.json"
    if receipt_reference["path"] != expected_relative_path:
        raise RepairVerificationError("repair receipt reference is invalid")
    receipt_raw = publisher.read_receipt_bounded(
        digest=receipt_sha256,
        label="repair receipt",
        hold=hold_reads,
    )
    receipt = _canonical_object(receipt_raw, "repair receipt")
    unsigned_receipt = dict(receipt)
    embedded_receipt_sha256 = unsigned_receipt.pop("receipt_sha256", None)
    if (
        embedded_receipt_sha256 != receipt_sha256
        or verifier._sha256_bytes(verifier._canonical_bytes(unsigned_receipt)) != receipt_sha256
    ):
        raise RepairVerificationError("repair receipt digest drifted")
    expected_receipt = _receipt_body(
        prepared,
        {"sha256": verifier._sha256_bytes(preregistration_raw)},
        prepared["replay"],
    )
    if unsigned_receipt != expected_receipt:
        raise RepairVerificationError("repair receipt drifted")


def verify_current_repair_snapshot(source_root: str, expected_repair_commit: str) -> dict[str, str]:
    prepared = preflight(source_root, expected_repair_commit)
    with _HeldWindowsRunRoot(Path(prepared["inputs"]["run_root"])) as publisher:
        publisher.assert_bound()
        _validate_completed_snapshot(prepared, publisher=publisher, hold_reads=True)
        current = preflight(source_root, expected_repair_commit)
        _require_stable_preflight(prepared, current)
        publisher.assert_bound()
    return {"status": "current_snapshot_verified"}


def _record_status(
    prepared: Mapping[str, Any],
    claim_raw: bytes,
    *,
    status: str,
    verified: bool,
    receipt: Mapping[str, Any] | None,
    error_type: str | None,
    publisher: _HeldWindowsRunRoot,
) -> None:
    inputs = prepared["inputs"]
    payload = {
        "schema_version": STATUS_SCHEMA,
        "status": status,
        "stage": status,
        "pid": os.getpid(),
        "finished_at_utc": _utc_now(),
        "run_root": verifier.RUN_ROOT_NAME,
        "claim_sha256": verifier._sha256_bytes(claim_raw),
        "original_failure_status_sha256": prepared["prior_failure"]["status_sha256"],
        "verified": verified,
        "receipt": ({"path": str(receipt["path"].relative_to(inputs["run_root"])).replace("\\", "/"), "sha256": receipt["sha256"]} if receipt is not None else None),
        "error_type": error_type,
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "repair_status_authority": {
            "current_input_revalidation_required": True,
            "status_alone_authoritative": False,
        },
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    publisher.write_root_once(
        name=STATUS_NAME,
        raw=verifier._canonical_bytes(payload) + b"\n",
        label="repair status",
        tolerate_close_failure=True,
    )


def _record_failed_status(
    prepared: Mapping[str, Any],
    claim_raw: bytes,
    *,
    error_type: str,
    expected_chain: tuple[tuple[int, int], ...] | None,
) -> None:
    try:
        with _HeldWindowsRunRoot(
            Path(prepared["inputs"]["run_root"]),
            expected_chain=expected_chain,
        ) as publisher:
            if publisher.root_file_exists(STATUS_NAME):
                return
            publisher.assert_bound()
            _record_status(
                prepared,
                claim_raw,
                status="failed",
                verified=False,
                receipt=None,
                error_type=error_type,
                publisher=publisher,
            )
            publisher.mark_terminal_status_committed()
    except BaseException:
        return


def run(source_root: str, expected_repair_commit: str) -> dict[str, str]:
    prepared = preflight(source_root, expected_repair_commit)
    inputs = prepared["inputs"]
    claim_raw: bytes | None = None
    chain_identity: tuple[tuple[int, int], ...] | None = None
    try:
        with _HeldWindowsRunRoot(
            Path(inputs["run_root"]),
            include_receipt_tree=True,
        ) as publisher:
            chain_identity = publisher.chain_identity
            if publisher.root_file_exists(STATUS_NAME):
                raise RepairVerificationError("repair verification is already terminal")
            preregistration = _preregistration(prepared, publisher=publisher)
            claim_raw = _new_claim(prepared, preregistration)
            publisher.write_root_once(name=CLAIM_NAME, raw=claim_raw, label="repair claim")
            revalidated = preflight(source_root, expected_repair_commit)
            _require_stable_preflight(prepared, revalidated)
            receipt = _publish_receipt(
                revalidated,
                preregistration,
                revalidated["replay"],
                publisher,
            )
            final = preflight(source_root, expected_repair_commit)
            _require_stable_preflight(prepared, final)
        with _HeldWindowsRunRoot(
            Path(final["inputs"]["run_root"]),
            expected_chain=chain_identity,
        ) as publisher:
            publisher.assert_bound()
            _record_status(
                final,
                claim_raw,
                status="completed",
                verified=True,
                receipt=receipt,
                error_type=None,
                publisher=publisher,
            )
            publisher.mark_terminal_status_committed()
        return {"status": "completed", "receipt_sha256": receipt["sha256"]}
    except BaseException as exc:
        if claim_raw is not None:
            _record_failed_status(
                prepared,
                claim_raw,
                error_type=type(exc).__name__,
                expected_chain=chain_identity,
            )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=r"E:\AI workspace\quant-signal-lkj")
    parser.add_argument("--expected-repair-commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--verify-current", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.preflight:
            preflight(args.source_root, args.expected_repair_commit)
            print("status=preflight_verified")
            return 0
        if args.verify_current:
            verify_current_repair_snapshot(args.source_root, args.expected_repair_commit)
            print("status=current_snapshot_verified")
            return 0
        run(args.source_root, args.expected_repair_commit)
        print("status=repair_verified")
        return 0
    except Exception:
        print("status=failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
