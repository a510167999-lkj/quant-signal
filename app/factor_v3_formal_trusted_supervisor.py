from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import hmac
import json
import msvcrt
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Iterator


class FormalSupervisorError(RuntimeError):
    pass


_EMBEDDED_CONTROL_CONTRACT_SOURCE: bytes | None = None
_CONTROL_CONTRACT_SOURCE_NAME = "factor_v3_formal_control_contract.py"
_CONTROL_CONTRACT_SOURCE_SHA256 = "a821ff1b3d338528ca9b63918a5d5f7453513b6f54ee1b8a3c06e811ef9cfad6"


def _fixed_control_contract_source() -> bytes:
    raw = _EMBEDDED_CONTROL_CONTRACT_SOURCE
    if raw is None:
        path = Path(__file__).resolve().with_name(_CONTROL_CONTRACT_SOURCE_NAME)
        try:
            raw = path.read_bytes()
        except OSError:
            raise FormalSupervisorError("fixed control contract unavailable") from None
    if (
        type(raw) is not bytes
        or not raw
        or hashlib.sha256(raw).hexdigest() != _CONTROL_CONTRACT_SOURCE_SHA256
    ):
        raise FormalSupervisorError("fixed control contract identity rejected")
    return raw


def _load_fixed_control_contract() -> dict[str, Any]:
    raw = _fixed_control_contract_source()
    namespace: dict[str, Any] = {
        "__file__": f"<{_CONTROL_CONTRACT_SOURCE_NAME}>",
        "__name__": "_factor_v3_fixed_control_contract",
    }
    try:
        code = compile(
            raw,
            namespace["__file__"],
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        exec(code, namespace)
    except BaseException as exc:
        raise FormalSupervisorError("fixed control contract rejected") from exc
    required = {
        "EXECUTION_REPLAY_SCOPE",
        "PUBLICATION_COMPLETION_SCHEMA",
        "STDLIB_POLICY_SCHEMA",
        "STDLIB_ROOT_ENVIRONMENT",
        "WORKER_ACTION_BY_LAUNCH_ACTION",
        "WORKER_PROTOCOL",
        "WORKER_TERMINAL_FIELDS",
        "WORKER_TERMINAL_SCHEMA",
        "control_contract_descriptor_sha256",
        "exact_worker_argv",
        "validate_stdlib_policy",
        "worker_environment_policy",
        "worker_protocol_descriptor",
    }
    if not required.issubset(namespace):
        raise FormalSupervisorError("fixed control contract rejected")
    return namespace


_CONTROL_CONTRACT = _load_fixed_control_contract()
EXECUTION_REPLAY_SCOPE = _CONTROL_CONTRACT["EXECUTION_REPLAY_SCOPE"]
PUBLICATION_COMPLETION_SCHEMA = _CONTROL_CONTRACT["PUBLICATION_COMPLETION_SCHEMA"]
STDLIB_POLICY_SCHEMA = _CONTROL_CONTRACT["STDLIB_POLICY_SCHEMA"]
STDLIB_ROOT_ENVIRONMENT = _CONTROL_CONTRACT["STDLIB_ROOT_ENVIRONMENT"]
WORKER_ACTION_BY_LAUNCH_ACTION = _CONTROL_CONTRACT["WORKER_ACTION_BY_LAUNCH_ACTION"]
WORKER_PROTOCOL = _CONTROL_CONTRACT["WORKER_PROTOCOL"]
WORKER_TERMINAL_FIELDS = _CONTROL_CONTRACT["WORKER_TERMINAL_FIELDS"]
WORKER_TERMINAL_SCHEMA = _CONTROL_CONTRACT["WORKER_TERMINAL_SCHEMA"]
control_contract_descriptor_sha256 = _CONTROL_CONTRACT["control_contract_descriptor_sha256"]
exact_worker_argv = _CONTROL_CONTRACT["exact_worker_argv"]
validate_stdlib_policy = _CONTROL_CONTRACT["validate_stdlib_policy"]
worker_environment_policy = _CONTROL_CONTRACT["worker_environment_policy"]
worker_protocol_descriptor = _CONTROL_CONTRACT["worker_protocol_descriptor"]


LAUNCH_AUTHORIZATION_SCHEMA = "factor-v3-formal-supervisor-launch-authorization/v1"
BOOTSTRAP_EXECUTION_AUTHORIZATION_SCHEMA = "factor-v3-formal-bootstrap-execution-authorization/v2"
BOOTSTRAP_EXECUTION_AUTHORIZATION_KEY_ROLE = "factor-v3-bootstrap-execution-authorization"
PUBLICATION_RECEIPT_SCHEMA = "factor-v3-formal-bootstrap-publication-receipt/v1"
STDLIB_INVENTORY_SCHEMA = STDLIB_POLICY_SCHEMA
CLAIM_SCHEMA = "factor-v3-formal-supervisor-execution-claim/v1"
COMPLETED_SCHEMA = "factor-v3-formal-supervisor-execution-completed/v1"
WORKER_ENVIRONMENT_POLICY = worker_environment_policy()

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_MAX_AUTHORIZATION_BYTES = 4 * 1024 * 1024
_MAX_PUBLIC_KEY_BYTES = 64 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_WORKER_BYTES = 8 * 1024 * 1024
_MAX_CAPTURE_BYTES = 1024 * 1024
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_AUTHORIZATION_LIFETIME_SECONDS = 24 * 60 * 60
_MAX_WORKER_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_REPARSE_ATTRIBUTE = 0x00000400
_OS_WRITE = os.write

_LAUNCH_FIELDS = {
    "action",
    "authorization_id_sha256",
    "authorization_nonce_sha256",
    "base_python_executable_path",
    "base_python_executable_sha256",
    "bootstrap_execution_authorization_sha256",
    "bootstrap_execution_authorization_path",
    "bootstrap_output_root",
    "bootstrap_worker_path",
    "bootstrap_worker_sha256",
    "control_contract_descriptor_sha256",
    "control_contract_source_sha256",
    "environment_policy",
    "execution_key_id",
    "execution_ledger_root",
    "expires_at_utc",
    "formal_input_root_path",
    "formal_input_root_sha256",
    "formal_output_root",
    "git_executable_path",
    "git_executable_sha256",
    "issued_at_utc",
    "project_id",
    "publication_completion_marker_path",
    "publication_completion_marker_sha256",
    "publication_completion_schema",
    "python_executable_path",
    "python_executable_sha256",
    "repo_root",
    "replay_scope",
    "resume_of_authorization_id_sha256",
    "resume_of_authorization_sha256",
    "resume_of_authorization_nonce_sha256",
    "resume_of_bootstrap_execution_authorization_sha256",
    "resume_of_replay_scope",
    "resume_status_path",
    "resume_status_sha256",
    "review_public_key_spki_sha256",
    "reviewed_commit",
    "run_root",
    "run_spec_path",
    "run_spec_sha256",
    "schema",
    "source_root_sha256",
    "stdlib_inventory_root_sha256",
    "stdlib_policy_path",
    "stdlib_policy_sha256",
    "supervisor_expected_commit",
    "supervisor_source_sha256",
    "worker_action",
    "worker_argv",
    "worker_protocol",
    "worker_pycache_prefix",
    "worker_terminal_schema",
    "worker_timeout_seconds",
}
_TERMINAL_FIELDS = set(WORKER_TERMINAL_FIELDS)
_BOOTSTRAP_EXECUTION_AUTHORIZATION_FIELDS = {
    "action",
    "authorization_id_sha256",
    "authorization_nonce_sha256",
    "base_python_executable_path",
    "base_python_executable_sha256",
    "bootstrap_claim_path",
    "bootstrap_claim_sha256",
    "bootstrap_output_root",
    "builder_relative_path",
    "builder_sha256",
    "control_contract_descriptor_sha256",
    "expected_branch",
    "expected_commit",
    "execution_authorization_key_id",
    "execution_authorization_key_role",
    "expires_at_utc",
    "feature_attestation_sha256",
    "formal_input_root_path",
    "formal_input_root_sha256",
    "formal_output_root",
    "formal_runner_sha256",
    "git_executable_path",
    "git_executable_sha256",
    "issued_at_utc",
    "not_before_utc",
    "project_id",
    "python_executable_path",
    "python_executable_sha256",
    "repo_root",
    "replay_scope",
    "review_payload_sha256",
    "review_protocol_sha256",
    "review_public_key_spki_der_base64",
    "review_public_key_spki_sha256",
    "review_receipt_path",
    "review_receipt_sha256",
    "run_root",
    "run_spec_path",
    "run_spec_sha256",
    "runtime_template_sha256",
    "schema",
    "shim_relative_path",
    "shim_sha256",
    "source_manifest",
    "source_root_sha256",
    "stdlib_policy",
    "stdlib_policy_root_sha256",
    "supervisor_protocol",
}


@dataclass(frozen=True)
class _SupervisorPins:
    base_python_executable_path: str
    base_python_executable_sha256: str
    bootstrap_completion_marker_path: str
    bootstrap_completion_marker_sha256: str
    bootstrap_completion_schema: str
    control_contract_source_sha256: str
    execution_public_key_spki_der_base64: str
    execution_public_key_spki_sha256: str
    git_executable_path: str
    git_executable_sha256: str
    python_executable_path: str
    python_executable_sha256: str
    repo_root: str
    supervisor_expected_commit: str
    supervisor_source_relative_path: str
    supervisor_source_sha256: str
    worker_protocol: str
    worker_terminal_schema: str


_FIXED_PINS: _SupervisorPins | None = None


def _pinned_control_contract_path(pins: _SupervisorPins) -> Path:
    return Path(pins.repo_root) / "app" / _CONTROL_CONTRACT_SOURCE_NAME


def _render_supervisor_with_test_pins(pins: _SupervisorPins) -> bytes:
    _validate_pins(pins)
    source_path = Path(__file__).resolve()
    raw = source_path.read_bytes().replace(b"\r\n", b"\n")
    pins_marker = b"_FIXED_PINS: _SupervisorPins | None" + b" = None"
    pins_replacement = f"_FIXED_PINS: _SupervisorPins = {pins!r}".encode("utf-8")
    contract_marker = b"_EMBEDDED_CONTROL_CONTRACT_SOURCE: bytes | None" + b" = None"
    contract_source = _HeldFile(
        _pinned_control_contract_path(pins),
        expected_sha256=pins.control_contract_source_sha256,
        label="pinned control contract source",
        max_bytes=_MAX_WORKER_BYTES,
    )
    try:
        if contract_source.raw != _fixed_control_contract_source():
            raise FormalSupervisorError("pinned control contract source rejected")
        contract_replacement = b"_EMBEDDED_CONTROL_CONTRACT_SOURCE: bytes = " + repr(
            contract_source.raw
        ).encode("ascii")
        contract_source.postverify()
    finally:
        contract_source.close()
    if raw.count(pins_marker) != 1 or raw.count(contract_marker) != 1:
        raise FormalSupervisorError("supervisor template marker rejected")
    rendered = raw.replace(pins_marker, pins_replacement).replace(
        contract_marker,
        contract_replacement,
    )
    try:
        compile(rendered, "<rendered-factor-v3-supervisor>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise FormalSupervisorError("rendered supervisor rejected") from exc
    return rendered


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
        raise FormalSupervisorError("canonical JSON rejected") from exc


def _strict_canonical_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise FormalSupervisorError(f"{label} rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                FormalSupervisorError(f"{label} rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        FormalSupervisorError,
    ) as exc:
        raise FormalSupervisorError(f"{label} rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FormalSupervisorError(f"{label} rejected")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise FormalSupervisorError(f"{label} rejected")
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str:
        raise FormalSupervisorError(f"{label} rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalSupervisorError(f"{label} rejected")
    return path


def _write_all(
    descriptor: int,
    raw: bytes,
    *,
    writer: Any = _OS_WRITE,
) -> None:
    view = memoryview(raw)
    offset = 0
    while offset < len(view):
        try:
            written = writer(descriptor, view[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if type(written) is not int or isinstance(written, bool) or written <= 0:
            raise FormalSupervisorError("terminal output rejected")
        offset += written


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _kernel32() -> Any:
    if os.name != "nt":
        raise FormalSupervisorError("trusted supervisor requires Windows")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_directory_handle(
    path: Path,
    *,
    desired_access: int = 0x80000000,
    share_mode: int = 0x00000001 | 0x00000002,
) -> tuple[Any, tuple[int, int, int]]:
    kernel32 = _kernel32()
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
    handle = create_file(
        str(path),
        desired_access,
        share_mode,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise FormalSupervisorError("held directory open rejected")
    information = _ByHandleFileInformation()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
    get_information.restype = wintypes.BOOL
    if not get_information(handle, ctypes.byref(information)):
        kernel32.CloseHandle(handle)
        raise FormalSupervisorError("held directory identity rejected")
    if (
        not int(information.dwFileAttributes) & 0x00000010
        or int(information.dwFileAttributes) & _REPARSE_ATTRIBUTE
    ):
        kernel32.CloseHandle(handle)
        raise FormalSupervisorError("held directory identity rejected")
    identity = (
        int(information.dwVolumeSerialNumber),
        int(information.nFileIndexHigh),
        int(information.nFileIndexLow),
    )
    return handle, identity


class _HeldDirectoryChain:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
            raise FormalSupervisorError("held directory path rejected")
        self._handles: list[tuple[Any, Path, tuple[int, int, int]]] = []
        try:
            anchor = Path(path.anchor)
            self._append_existing(anchor)
            for part in path.parts[1:]:
                self._append_existing(self.path / part)
            if os.path.normcase(str(self.path)) != os.path.normcase(str(path.resolve(strict=True))):
                raise FormalSupervisorError("held directory path rejected")
        except BaseException:
            self.close()
            raise

    @property
    def path(self) -> Path:
        return self._handles[-1][1]

    def _append_existing(self, path: Path) -> None:
        handle, identity = _open_directory_handle(path)
        self._handles.append((handle, path, identity))

    def ensure_child(self, part: str) -> Path:
        if not part or part in {".", ".."} or "/" in part or "\\" in part:
            raise FormalSupervisorError("held child directory rejected")
        candidate = self.path / part
        try:
            os.mkdir(candidate)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FormalSupervisorError("held child directory rejected") from exc
        self._append_existing(candidate)
        return candidate

    def postverify(self) -> None:
        kernel32 = _kernel32()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
        get_information.restype = wintypes.BOOL
        for handle, path, expected in self._handles:
            information = _ByHandleFileInformation()
            if not get_information(handle, ctypes.byref(information)):
                raise FormalSupervisorError("held directory drifted")
            observed = (
                int(information.dwVolumeSerialNumber),
                int(information.nFileIndexHigh),
                int(information.nFileIndexLow),
            )
            if (
                observed != expected
                or not int(information.dwFileAttributes) & 0x00000010
                or int(information.dwFileAttributes) & _REPARSE_ATTRIBUTE
            ):
                raise FormalSupervisorError("held directory drifted")
            terminal_handle, terminal_identity = _open_directory_handle(path)
            kernel32.CloseHandle(terminal_handle)
            if terminal_identity != expected:
                raise FormalSupervisorError("held directory drifted")

    def close(self) -> None:
        if not self._handles:
            return
        kernel32 = _kernel32()
        while self._handles:
            handle, _path, _identity = self._handles.pop()
            kernel32.CloseHandle(handle)


@contextmanager
def _held_directory_chain(path: Path) -> Iterator[_HeldDirectoryChain]:
    chain = _HeldDirectoryChain(path)
    try:
        yield chain
        chain.postverify()
    finally:
        chain.close()


class _HeldFrozenDirectoryTree:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self._handles: list[tuple[Any, Path, tuple[int, int, int]]] = []
        seen: set[str] = set()
        try:
            for root in paths:
                candidates = [root]
                if root.is_dir():
                    for directory, names, _filenames in os.walk(root, topdown=True):
                        current = Path(directory)
                        names[:] = sorted(
                            name
                            for name in names
                            if not (
                                (current / name).is_symlink()
                                or int(
                                    getattr(
                                        (current / name).lstat(),
                                        "st_file_attributes",
                                        0,
                                    )
                                )
                                & _REPARSE_ATTRIBUTE
                            )
                        )
                        candidates.append(current)
                for candidate in candidates:
                    normalized = os.path.normcase(str(candidate))
                    if normalized in seen:
                        continue
                    handle, identity = _open_directory_handle(
                        candidate,
                        share_mode=0x00000001,
                    )
                    self._handles.append((handle, candidate, identity))
                    seen.add(normalized)
        except BaseException:
            self.close()
            raise

    def postverify(self) -> None:
        kernel32 = _kernel32()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        )
        get_information.restype = wintypes.BOOL
        for handle, _path, expected in self._handles:
            information = _ByHandleFileInformation()
            if not get_information(handle, ctypes.byref(information)):
                raise FormalSupervisorError("frozen stdlib directory drifted")
            observed = (
                int(information.dwVolumeSerialNumber),
                int(information.nFileIndexHigh),
                int(information.nFileIndexLow),
            )
            if observed != expected:
                raise FormalSupervisorError("frozen stdlib directory drifted")

    def close(self) -> None:
        if not self._handles:
            return
        kernel32 = _kernel32()
        while self._handles:
            handle, _path, _identity = self._handles.pop()
            kernel32.CloseHandle(handle)


@contextmanager
def _held_frozen_directory_tree(
    paths: tuple[Path, ...],
) -> Iterator[_HeldFrozenDirectoryTree]:
    tree = _HeldFrozenDirectoryTree(paths)
    try:
        yield tree
        tree.postverify()
    finally:
        tree.close()


class _HeldFile:
    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str | None,
        label: str,
        max_bytes: int,
        allow_empty: bool = False,
        allow_hardlinks: bool = False,
    ) -> None:
        self._chain = _HeldDirectoryChain(path.parent)
        candidate = self._chain.path / path.name
        try:
            before = candidate.lstat()
        except OSError:
            self._chain.close()
            raise FormalSupervisorError(f"{label} unavailable") from None
        link_count = int(getattr(before, "st_nlink", 1))
        if (
            not stat.S_ISREG(before.st_mode)
            or int(getattr(before, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
            or before.st_size < 0
            or (before.st_size == 0 and not allow_empty)
            or before.st_size > max_bytes
            or (link_count < 1 if allow_hardlinks else link_count != 1)
        ):
            self._chain.close()
            raise FormalSupervisorError(f"{label} rejected")
        kernel32 = _kernel32()
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
        handle = create_file(
            str(candidate),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            self._chain.close()
            raise FormalSupervisorError(f"{label} safe open rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            self._chain.close()
            raise
        self._stream = os.fdopen(descriptor, "rb")
        try:
            opened = os.fstat(self._stream.fileno())
            raw = self._stream.read(max_bytes + 1)
            self._stream.seek(0)
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
                or not os.path.samestat(before, opened)
                or len(raw) != opened.st_size
                or (
                    expected_sha256 is not None
                    and hashlib.sha256(raw).hexdigest() != expected_sha256
                )
            ):
                raise FormalSupervisorError(f"{label} identity rejected")
        except BaseException:
            self._stream.close()
            self._chain.close()
            raise
        self.path = candidate
        self.raw = raw
        self._expected_sha256 = expected_sha256 or hashlib.sha256(raw).hexdigest()
        self._label = label
        self._max_bytes = max_bytes
        self._allow_hardlinks = allow_hardlinks

    def postverify(self) -> None:
        self._chain.postverify()
        opened = os.fstat(self._stream.fileno())
        try:
            terminal = self.path.lstat()
        except OSError:
            raise FormalSupervisorError(f"{self._label} drifted") from None
        self._stream.seek(0)
        raw = self._stream.read(self._max_bytes + 1)
        self._stream.seek(0)
        link_count = int(getattr(terminal, "st_nlink", 1))
        if (
            not os.path.samestat(opened, terminal)
            or int(getattr(terminal, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
            or (link_count < 1 if self._allow_hardlinks else link_count != 1)
            or len(raw) != opened.st_size
            or hashlib.sha256(raw).hexdigest() != self._expected_sha256
        ):
            raise FormalSupervisorError(f"{self._label} drifted")

    def close(self) -> None:
        self._stream.close()
        self._chain.close()


class _HeldLedgerFile:
    def __init__(self, path: Path, raw: bytes, *, replay_label: str) -> None:
        self._chain = _HeldDirectoryChain(path.parent)
        candidate = self._chain.path / path.name
        kernel32 = _kernel32()
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
        handle = create_file(
            str(candidate),
            0x80000000 | 0x40000000,
            0x00000001,
            None,
            1,
            0x00200000 | 0x08000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            self._chain.close()
            if ctypes.get_last_error() in {80, 183}:
                raise FormalSupervisorError(f"{replay_label} replay rejected") from None
            raise FormalSupervisorError("execution ledger write rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            self._chain.close()
            raise
        self._stream = os.fdopen(descriptor, "w+b")
        self.path = candidate
        self.raw = raw
        self._sha256 = hashlib.sha256(raw).hexdigest()
        try:
            _write_all(self._stream.fileno(), raw, writer=os.write)
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self.postverify()
        except BaseException:
            self.close()
            raise

    def postverify(self) -> None:
        self._chain.postverify()
        opened = os.fstat(self._stream.fileno())
        terminal = self.path.lstat()
        self._stream.seek(0)
        observed = self._stream.read(_MAX_AUTHORIZATION_BYTES + 1)
        self._stream.seek(0)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(opened, terminal)
            or int(getattr(terminal, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
            or int(getattr(terminal, "st_nlink", 1)) != 1
            or not hmac.compare_digest(observed, self.raw)
            or hashlib.sha256(observed).hexdigest() != self._sha256
        ):
            raise FormalSupervisorError("execution ledger terminal write rejected")

    def close(self) -> None:
        self._stream.close()
        self._chain.close()


def _der_length(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise FormalSupervisorError("execution public key rejected")
    first = raw[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(raw) or raw[offset + 1] == 0:
        raise FormalSupervisorError("execution public key rejected")
    length = int.from_bytes(raw[offset + 1 : offset + 1 + count], "big")
    if length < 0x80:
        raise FormalSupervisorError("execution public key rejected")
    return length, offset + 1 + count


def _der_value(raw: bytes, offset: int, *, tag: int) -> tuple[bytes, int]:
    if offset >= len(raw) or raw[offset] != tag:
        raise FormalSupervisorError("execution public key rejected")
    length, value_offset = _der_length(raw, offset + 1)
    end = value_offset + length
    if end > len(raw):
        raise FormalSupervisorError("execution public key rejected")
    return raw[value_offset:end], end


def _positive_der_integer(raw: bytes, offset: int) -> tuple[int, int]:
    value, end = _der_value(raw, offset, tag=0x02)
    if not value or value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
        raise FormalSupervisorError("execution public key rejected")
    return int.from_bytes(value, "big"), end


def _parse_rsa3072_spki(raw: bytes) -> tuple[int, int]:
    outer, end = _der_value(raw, 0, tag=0x30)
    if end != len(raw):
        raise FormalSupervisorError("execution public key rejected")
    algorithm, offset = _der_value(outer, 0, tag=0x30)
    if algorithm != bytes.fromhex("06092a864886f70d0101010500"):
        raise FormalSupervisorError("execution public key rejected")
    bit_string, end = _der_value(outer, offset, tag=0x03)
    if end != len(outer) or not bit_string or bit_string[0] != 0:
        raise FormalSupervisorError("execution public key rejected")
    sequence, end = _der_value(bit_string[1:], 0, tag=0x30)
    if end != len(bit_string) - 1:
        raise FormalSupervisorError("execution public key rejected")
    modulus, offset = _positive_der_integer(sequence, 0)
    exponent, end = _positive_der_integer(sequence, offset)
    if (
        end != len(sequence)
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise FormalSupervisorError("execution public key rejected")
    return modulus, exponent


def _public_der_from_pem(raw: bytes) -> bytes:
    try:
        text = raw.decode("ascii").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise FormalSupervisorError("execution public key rejected") from None
    match = re.fullmatch(
        r"-----BEGIN PUBLIC KEY-----\n"
        r"([A-Za-z0-9+/=\n]+)"
        r"-----END PUBLIC KEY-----\n?",
        text,
    )
    if match is None:
        raise FormalSupervisorError("execution public key rejected")
    try:
        der = base64.b64decode("".join(match.group(1).splitlines()), validate=True)
    except ValueError:
        raise FormalSupervisorError("execution public key rejected") from None
    _parse_rsa3072_spki(der)
    return der


def _verify_signature(payload: bytes, signature: bytes, *, public_der: bytes) -> None:
    modulus, exponent = _parse_rsa3072_spki(public_der)
    if len(signature) != 384:
        raise FormalSupervisorError("launch authorization signature rejected")
    signature_int = int.from_bytes(signature, "big")
    if not 0 < signature_int < modulus:
        raise FormalSupervisorError("launch authorization signature rejected")
    encoded = pow(signature_int, exponent, modulus).to_bytes(384, "big")
    digest_info = (
        bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
    )
    expected = b"\x00\x01" + b"\xff" * (384 - len(digest_info) - 3) + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise FormalSupervisorError("launch authorization signature rejected")


def _decoded_signature(value: Any) -> bytes:
    if type(value) is not str:
        raise FormalSupervisorError("launch authorization signature rejected")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise FormalSupervisorError("launch authorization signature rejected") from None
    if base64.b64encode(raw).decode("ascii") != value:
        raise FormalSupervisorError("launch authorization signature rejected")
    return raw


def _validate_cas_path(
    path: Path,
    digest: str,
    *,
    category: str,
    suffix: str,
    label: str,
) -> None:
    if (
        path.name != f"{digest}{suffix}"
        or path.parent.name != digest[:2]
        or path.parent.parent.name != "sha256"
        or path.parent.parent.parent.name != category
    ):
        raise FormalSupervisorError(f"{label} CAS rejected")


def _parse_utc(value: Any, *, label: str) -> datetime:
    if type(value) is not str:
        raise FormalSupervisorError(f"{label} rejected")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise FormalSupervisorError(f"{label} rejected") from None
    if (
        parsed.tzinfo != timezone.utc
        or parsed.microsecond != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        raise FormalSupervisorError(f"{label} rejected")
    return parsed


def _fresh_environment_policy() -> dict[str, Any]:
    return worker_environment_policy()


def _validate_pins(pins: _SupervisorPins) -> None:
    if type(pins) is not _SupervisorPins:
        raise FormalSupervisorError("fixed supervisor pins unavailable")
    for field in (
        "base_python_executable_sha256",
        "bootstrap_completion_marker_sha256",
        "control_contract_source_sha256",
        "execution_public_key_spki_sha256",
        "git_executable_sha256",
        "python_executable_sha256",
        "supervisor_source_sha256",
    ):
        _require_sha256(getattr(pins, field), label=field)
    if _COMMIT_RE.fullmatch(pins.supervisor_expected_commit) is None:
        raise FormalSupervisorError("supervisor commit pin rejected")
    if (
        pins.control_contract_source_sha256 != _CONTROL_CONTRACT_SOURCE_SHA256
        or hashlib.sha256(_fixed_control_contract_source()).hexdigest()
        != pins.control_contract_source_sha256
        or pins.bootstrap_completion_schema != PUBLICATION_COMPLETION_SCHEMA
        or pins.worker_protocol != WORKER_PROTOCOL
        or pins.worker_terminal_schema != WORKER_TERMINAL_SCHEMA
    ):
        raise FormalSupervisorError("fixed supervisor protocol pins rejected")
    relative = pins.supervisor_source_relative_path
    if (
        not relative.startswith("app/")
        or not relative.endswith(".py")
        or "\\" in relative
        or ".." in relative.split("/")
    ):
        raise FormalSupervisorError("supervisor source pin rejected")
    for field in (
        "base_python_executable_path",
        "bootstrap_completion_marker_path",
        "git_executable_path",
        "python_executable_path",
        "repo_root",
    ):
        _absolute_path(getattr(pins, field), label=field)
    try:
        public_der = base64.b64decode(
            pins.execution_public_key_spki_der_base64.encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise FormalSupervisorError("embedded execution public key rejected") from None
    _parse_rsa3072_spki(public_der)
    if (
        base64.b64encode(public_der).decode("ascii") != pins.execution_public_key_spki_der_base64
        or hashlib.sha256(public_der).hexdigest() != pins.execution_public_key_spki_sha256
    ):
        raise FormalSupervisorError("embedded execution public key rejected")


def _validate_launch_payload(
    value: Any,
    *,
    pins: _SupervisorPins,
    now_utc: datetime,
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != _LAUNCH_FIELDS
        or value.get("schema") != LAUNCH_AUTHORIZATION_SCHEMA
        or value.get("project_id") != "quant-signal-lkj"
        or value.get("action") not in WORKER_ACTION_BY_LAUNCH_ACTION
        or value.get("worker_action")
        != WORKER_ACTION_BY_LAUNCH_ACTION.get(str(value.get("action")))
        or value.get("environment_policy") != _fresh_environment_policy()
    ):
        raise FormalSupervisorError("launch authorization payload rejected")
    payload = dict(value)
    for field in (
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "base_python_executable_sha256",
        "bootstrap_execution_authorization_sha256",
        "bootstrap_worker_sha256",
        "control_contract_descriptor_sha256",
        "control_contract_source_sha256",
        "formal_input_root_sha256",
        "git_executable_sha256",
        "publication_completion_marker_sha256",
        "python_executable_sha256",
        "review_public_key_spki_sha256",
        "run_spec_sha256",
        "source_root_sha256",
        "stdlib_inventory_root_sha256",
        "stdlib_policy_sha256",
        "supervisor_source_sha256",
    ):
        _require_sha256(payload.get(field), label=field)
    if (
        payload["execution_key_id"] != f"sha256:{pins.execution_public_key_spki_sha256}"
        or payload["review_public_key_spki_sha256"] == pins.execution_public_key_spki_sha256
        or payload["publication_completion_marker_path"] != pins.bootstrap_completion_marker_path
        or payload["publication_completion_marker_sha256"]
        != pins.bootstrap_completion_marker_sha256
        or payload["publication_completion_schema"] != pins.bootstrap_completion_schema
        or payload["worker_protocol"] != pins.worker_protocol
        or payload["worker_terminal_schema"] != pins.worker_terminal_schema
        or payload["control_contract_descriptor_sha256"] != control_contract_descriptor_sha256()
        or payload["control_contract_source_sha256"] != pins.control_contract_source_sha256
        or payload["replay_scope"] != EXECUTION_REPLAY_SCOPE
    ):
        raise FormalSupervisorError("execution/review key role or protocol pins rejected")
    if (
        payload["base_python_executable_path"] != pins.base_python_executable_path
        or payload["base_python_executable_sha256"] != pins.base_python_executable_sha256
        or payload["git_executable_path"] != pins.git_executable_path
        or payload["git_executable_sha256"] != pins.git_executable_sha256
        or payload["python_executable_path"] != pins.python_executable_path
        or payload["python_executable_sha256"] != pins.python_executable_sha256
        or payload["supervisor_expected_commit"] != pins.supervisor_expected_commit
        or payload["supervisor_source_sha256"] != pins.supervisor_source_sha256
        or _COMMIT_RE.fullmatch(str(payload.get("reviewed_commit"))) is None
    ):
        raise FormalSupervisorError("fixed supervisor identity rejected")
    paths = {
        field: _absolute_path(payload.get(field), label=field)
        for field in (
            "bootstrap_output_root",
            "bootstrap_worker_path",
            "bootstrap_execution_authorization_path",
            "execution_ledger_root",
            "formal_input_root_path",
            "formal_output_root",
            "publication_completion_marker_path",
            "repo_root",
            "run_root",
            "run_spec_path",
            "stdlib_policy_path",
            "worker_pycache_prefix",
        )
    }
    exact_argv = exact_worker_argv(
        python_executable_path=pins.python_executable_path,
        bootstrap_worker_path=str(paths["bootstrap_worker_path"]),
        pycache_prefix=str(paths["worker_pycache_prefix"]),
    )
    if payload.get("worker_argv") != exact_argv:
        raise FormalSupervisorError("exact worker argv rejected")
    if payload["action"] == "resume":
        if (
            payload["worker_action"] != "run"
            or any(
                _SHA256_RE.fullmatch(str(payload.get(field))) is None
                for field in (
                    "resume_of_authorization_id_sha256",
                    "resume_of_authorization_nonce_sha256",
                    "resume_of_authorization_sha256",
                    "resume_of_bootstrap_execution_authorization_sha256",
                )
            )
            or payload.get("resume_of_replay_scope") != EXECUTION_REPLAY_SCOPE
            or payload["authorization_id_sha256"] != payload["resume_of_authorization_id_sha256"]
            or payload["authorization_nonce_sha256"]
            != payload["resume_of_authorization_nonce_sha256"]
            or payload["bootstrap_execution_authorization_sha256"]
            != payload["resume_of_bootstrap_execution_authorization_sha256"]
            or payload["replay_scope"] != payload["resume_of_replay_scope"]
            or _SHA256_RE.fullmatch(str(payload.get("resume_status_sha256"))) is None
            or type(payload.get("resume_status_path")) is not str
        ):
            raise FormalSupervisorError("resume authorization rejected")
        _absolute_path(payload["resume_status_path"], label="resume status path")
    elif any(
        payload.get(field) is not None
        for field in (
            "resume_of_authorization_id_sha256",
            "resume_of_authorization_nonce_sha256",
            "resume_of_authorization_sha256",
            "resume_of_bootstrap_execution_authorization_sha256",
            "resume_of_replay_scope",
            "resume_status_path",
            "resume_status_sha256",
        )
    ):
        raise FormalSupervisorError("non-resume authorization rejected")
    issued_at = _parse_utc(payload.get("issued_at_utc"), label="issued time")
    expires_at = _parse_utc(payload.get("expires_at_utc"), label="expiry time")
    if (
        now_utc.tzinfo != timezone.utc
        or now_utc.microsecond != 0
        or not issued_at <= now_utc < expires_at
        or expires_at <= issued_at
        or (expires_at - issued_at).total_seconds() > _MAX_AUTHORIZATION_LIFETIME_SECONDS
    ):
        raise FormalSupervisorError("launch authorization validity rejected")
    timeout = payload.get("worker_timeout_seconds")
    if (
        type(timeout) is not int
        or isinstance(timeout, bool)
        or not 0 < timeout <= _MAX_WORKER_TIMEOUT_SECONDS
    ):
        raise FormalSupervisorError("worker timeout rejected")
    return payload


def _public_environment(snapshot: Mapping[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for name in _fresh_environment_policy()["public_passthrough_names"]:
        value = snapshot.get(name)
        if value is not None:
            if type(value) is not str or not value:
                raise FormalSupervisorError("public worker environment rejected")
            output[name] = value
    return output


def _worker_environment(
    payload: Mapping[str, Any],
    *,
    launch_authorization_sha256: str,
    snapshot: Mapping[str, str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    environment = _public_environment(snapshot)
    secret_names = tuple(
        _fresh_environment_policy()["required_secret_names_by_action"][payload["action"]]
    )
    for name in secret_names:
        value = snapshot.get(name)
        if type(value) is not str or not value:
            raise FormalSupervisorError("required worker credential unavailable")
        environment[name] = value
    environment.update(
        {
            "FACTOR_V3_FORMAL_LAUNCH_ACTION": str(payload["action"]),
            "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256": launch_authorization_sha256,
            "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL": WORKER_PROTOCOL,
            STDLIB_ROOT_ENVIRONMENT: str(payload["stdlib_inventory_root_sha256"]),
        }
    )
    allowed_names = (
        set(_fresh_environment_policy()["public_passthrough_names"])
        | set(secret_names)
        | set(_fresh_environment_policy()["marker_names"])
    )
    if not set(environment).issubset(allowed_names):
        raise FormalSupervisorError("worker environment rejected")
    return environment, secret_names


def _git_head(pins: _SupervisorPins, snapshot: Mapping[str, str]) -> str:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    startupinfo.lpAttributeList = {"handle_list": []}
    try:
        completed = subprocess.run(
            [
                pins.git_executable_path,
                "-C",
                pins.repo_root,
                "rev-parse",
                "HEAD",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=_public_environment(snapshot),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise FormalSupervisorError("supervisor commit verification rejected") from None
    if completed.returncode != 0 or completed.stderr or len(completed.stdout) > 128:
        raise FormalSupervisorError("supervisor commit verification rejected")
    try:
        return completed.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise FormalSupervisorError("supervisor commit verification rejected") from None


def claim_path_for_authorization(root: Path, authorization_sha256: str) -> Path:
    _require_sha256(authorization_sha256, label="launch authorization SHA")
    return root / "claims" / "sha256" / authorization_sha256[:2] / f"{authorization_sha256}.json"


def completed_path_for_authorization(root: Path, authorization_sha256: str) -> Path:
    _require_sha256(authorization_sha256, label="launch authorization SHA")
    return root / "completed" / "sha256" / authorization_sha256[:2] / f"{authorization_sha256}.json"


def _ledger_path(root: Path, category: str, identity_sha256: str) -> Path:
    _require_sha256(identity_sha256, label="ledger identity")
    return root / category / "sha256" / identity_sha256[:2] / f"{identity_sha256}.json"


def _flush_directory(
    path: Path,
    *,
    expected_identity: tuple[int, int, int],
) -> None:
    kernel32 = _kernel32()
    handle, identity = _open_directory_handle(
        path,
        desired_access=0x40000000,
    )
    if identity != expected_identity:
        kernel32.CloseHandle(handle)
        raise FormalSupervisorError("execution ledger directory flush rejected")
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    try:
        if not flush(handle):
            raise FormalSupervisorError("execution ledger directory flush rejected")
    finally:
        kernel32.CloseHandle(handle)


def _ledger_write_once(
    chain: _HeldDirectoryChain,
    *,
    stack: ExitStack,
    category: str,
    authorization_sha256: str,
    raw: bytes,
    replay_label: str,
) -> tuple[_HeldLedgerFile, Path, str]:
    _require_sha256(authorization_sha256, label="launch authorization SHA")
    base_depth = len(chain._handles)
    try:
        for part in (category, "sha256", authorization_sha256[:2]):
            parent_path = chain.path
            parent_identity = chain._handles[-1][2]
            chain.ensure_child(part)
            _flush_directory(
                parent_path,
                expected_identity=parent_identity,
            )
        path = chain.path / f"{authorization_sha256}.json"
        held = _HeldLedgerFile(
            path,
            raw,
            replay_label=replay_label,
        )
        stack.callback(held.close)
        _flush_directory(
            chain.path,
            expected_identity=chain._handles[-1][2],
        )
        held.postverify()
        return held, path, hashlib.sha256(raw).hexdigest()
    finally:
        kernel32 = _kernel32()
        while len(chain._handles) > base_depth:
            handle, _path, _identity = chain._handles.pop()
            kernel32.CloseHandle(handle)


def _capture_stream(
    stream: Any,
    buffer: bytearray,
    overflow: threading.Event,
) -> None:
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            remaining = _MAX_CAPTURE_BYTES + 1 - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            if len(buffer) > _MAX_CAPTURE_BYTES or len(chunk) > remaining:
                overflow.set()
                return
    except BaseException:
        overflow.set()


def _run_worker(
    argv: list[str],
    *,
    cwd: str,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int, bytes, bytes]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    startupinfo.lpAttributeList = {"handle_list": []}
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            bufsize=0,
        )
    except OSError:
        raise FormalSupervisorError("isolated worker launch rejected") from None
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise FormalSupervisorError("isolated worker capture rejected")
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    readers = (
        threading.Thread(
            target=_capture_stream,
            args=(process.stdout, stdout, overflow),
            daemon=True,
        ),
        threading.Thread(
            target=_capture_stream,
            args=(process.stderr, stderr, overflow),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            process.kill()
            break
        time.sleep(0.02)
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = process.wait(timeout=10)
        timed_out = True
    for reader in readers:
        reader.join(timeout=10)
    process.stdout.close()
    process.stderr.close()
    if any(reader.is_alive() for reader in readers) or overflow.is_set():
        raise FormalSupervisorError("isolated worker capture rejected")
    if timed_out:
        raise FormalSupervisorError("isolated worker timeout rejected")
    return returncode, bytes(stdout), bytes(stderr)


def _reject_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            compact = re.sub(r"[^a-z]", "", str(key).casefold())
            if (
                normalized
                in {
                    "api_key",
                    "credential",
                    "credential_material",
                    "password",
                    "secret",
                    "token",
                }
                or "privatekey" in compact
                or normalized.endswith(("_api_key", "_password", "_secret", "_token"))
            ):
                raise FormalSupervisorError("worker terminal credential shape rejected")
            _reject_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _reject_credential_shape(nested)


def _contains_secret(
    raw: bytes,
    *,
    snapshot: Mapping[str, str],
    secret_names: tuple[str, ...],
) -> bool:
    for name in secret_names:
        value = snapshot.get(name)
        if type(value) is str and value and value.encode("utf-8") in raw:
            return True
    return False


def _parse_worker_terminal(
    raw: bytes,
    *,
    payload: Mapping[str, Any],
    authorization_sha256: str,
) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_CAPTURE_BYTES or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise FormalSupervisorError("worker terminal frame rejected")
    frame = _strict_canonical_json(raw[:-1], label="worker terminal frame")
    if (
        set(frame) != _TERMINAL_FIELDS
        or frame.get("schema") != WORKER_TERMINAL_SCHEMA
        or frame.get("status") != "completed"
        or frame.get("launch_action") != payload["action"]
        or frame.get("worker_action") != payload["worker_action"]
        or frame.get("launch_authorization_sha256") != authorization_sha256
        or frame.get("bootstrap_execution_authorization_sha256")
        != payload["bootstrap_execution_authorization_sha256"]
        or frame.get("authorization_nonce_sha256") != payload["authorization_nonce_sha256"]
        or frame.get("stdlib_inventory_root_sha256") != payload["stdlib_inventory_root_sha256"]
        or type(frame.get("result")) is not dict
    ):
        raise FormalSupervisorError("worker terminal frame rejected")
    _reject_credential_shape(frame["result"])
    return frame


def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = os.path.normcase(str(path.resolve(strict=False)))
    for root in roots:
        root_text = os.path.normcase(str(root))
        try:
            if os.path.commonpath((candidate, root_text)) == root_text:
                return True
        except ValueError:
            continue
    return False


def _validate_artifacts(
    value: Any,
    *,
    roots: tuple[Path, ...],
    stack: ExitStack,
) -> tuple[str, tuple[_HeldFile, ...]]:
    if type(value) is not list:
        raise FormalSupervisorError("worker artifact manifest rejected")
    normalized: list[dict[str, Any]] = []
    handles: list[_HeldFile] = []
    seen: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != {"bytes", "path", "sha256"}:
            raise FormalSupervisorError("worker artifact manifest rejected")
        path = _absolute_path(item.get("path"), label="worker artifact path")
        byte_count = item.get("bytes")
        digest = _require_sha256(item.get("sha256"), label="worker artifact SHA")
        if (
            str(path) in seen
            or type(byte_count) is not int
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= _MAX_ARTIFACT_BYTES
            or not _is_within(path, roots)
        ):
            raise FormalSupervisorError("worker artifact manifest rejected")
        held = _HeldFile(
            path,
            expected_sha256=digest,
            label="worker artifact",
            max_bytes=_MAX_ARTIFACT_BYTES,
        )
        stack.callback(held.close)
        if len(held.raw) != byte_count:
            raise FormalSupervisorError("worker artifact manifest rejected")
        held.postverify()
        handles.append(held)
        seen.add(str(path))
        normalized.append(
            {
                "bytes": byte_count,
                "path": str(path),
                "sha256": digest,
            }
        )
    if normalized != sorted(normalized, key=lambda item: item["path"]):
        raise FormalSupervisorError("worker artifact manifest rejected")
    return hashlib.sha256(_canonical_bytes(normalized)).hexdigest(), tuple(handles)


def _validated_publication_completion(
    raw: bytes,
    *,
    payload: Mapping[str, Any],
    public_der: bytes,
) -> dict[str, Any]:
    outer = _strict_canonical_json(raw, label="publication completion marker")
    if set(outer) != {"payload", "signature_base64"}:
        raise FormalSupervisorError("publication completion marker rejected")
    completion = outer.get("payload")
    fields = {
        "action",
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "bootstrap_bytes",
        "bootstrap_relative_path",
        "bootstrap_sha256",
        "bootstrap_output_root",
        "control_contract_descriptor_sha256",
        "execution_authorization_sha256",
        "receipt_bytes",
        "receipt_relative_path",
        "receipt_sha256",
        "runtime_template_sha256",
        "schema",
        "status",
        "stdlib_inventory_root_sha256",
        "stdlib_policy_bytes",
        "stdlib_policy_relative_path",
        "stdlib_policy_sha256",
    }
    if (
        type(completion) is not dict
        or set(completion) != fields
        or completion.get("schema") != payload["publication_completion_schema"]
        or completion.get("status") != "completed"
        or completion.get("action") != payload["worker_action"]
        or completion.get("authorization_id_sha256") != payload["authorization_id_sha256"]
        or completion.get("authorization_nonce_sha256") != payload["authorization_nonce_sha256"]
        or completion.get("bootstrap_output_root") != payload["bootstrap_output_root"]
        or completion.get("bootstrap_sha256") != payload["bootstrap_worker_sha256"]
        or completion.get("execution_authorization_sha256")
        != payload["bootstrap_execution_authorization_sha256"]
        or completion.get("control_contract_descriptor_sha256")
        != payload["control_contract_descriptor_sha256"]
        or completion.get("stdlib_inventory_root_sha256") != payload["stdlib_inventory_root_sha256"]
        or completion.get("stdlib_policy_sha256") != payload["stdlib_policy_sha256"]
        or type(completion.get("bootstrap_bytes")) is not int
        or isinstance(completion.get("bootstrap_bytes"), bool)
        or type(completion.get("receipt_bytes")) is not int
        or isinstance(completion.get("receipt_bytes"), bool)
        or type(completion.get("stdlib_policy_bytes")) is not int
        or isinstance(completion.get("stdlib_policy_bytes"), bool)
        or any(
            type(completion.get(field)) is not str
            or "\\" in completion[field]
            or completion[field].startswith("/")
            or ".." in completion[field].split("/")
            for field in (
                "bootstrap_relative_path",
                "receipt_relative_path",
                "stdlib_policy_relative_path",
            )
        )
        or _SHA256_RE.fullmatch(str(completion.get("receipt_sha256"))) is None
        or _SHA256_RE.fullmatch(str(completion.get("runtime_template_sha256"))) is None
    ):
        raise FormalSupervisorError("publication completion marker rejected")
    _verify_signature(
        _canonical_bytes(completion),
        _decoded_signature(outer.get("signature_base64")),
        public_der=public_der,
    )
    root = Path(str(payload["bootstrap_output_root"]))
    expected_worker = root / Path(*completion["bootstrap_relative_path"].split("/"))
    expected_receipt = root / Path(*completion["receipt_relative_path"].split("/"))
    expected_stdlib_policy = root / Path(*completion["stdlib_policy_relative_path"].split("/"))
    if (
        os.path.normcase(str(expected_worker))
        != os.path.normcase(str(payload["bootstrap_worker_path"]))
        or os.path.normcase(str(expected_stdlib_policy))
        != os.path.normcase(str(payload["stdlib_policy_path"]))
        or expected_receipt.name != f"{completion['receipt_sha256']}.json"
    ):
        raise FormalSupervisorError("publication completion marker rejected")
    _validate_cas_path(
        expected_receipt,
        str(completion["receipt_sha256"]),
        category="publication_receipts",
        suffix=".json",
        label="publication receipt",
    )
    _validate_cas_path(
        expected_stdlib_policy,
        str(completion["stdlib_policy_sha256"]),
        category="stdlib_policies",
        suffix=".json",
        label="stdlib policy",
    )
    return dict(completion)


def _validate_publication_receipt(
    raw: bytes,
    *,
    completion: Mapping[str, Any],
) -> None:
    receipt = _strict_canonical_json(raw, label="publication receipt")
    expected = {
        "action": completion["action"],
        "bootstrap_bytes": completion["bootstrap_bytes"],
        "bootstrap_relative_path": completion["bootstrap_relative_path"],
        "bootstrap_sha256": completion["bootstrap_sha256"],
        "execution_authorization_sha256": completion["execution_authorization_sha256"],
        "runtime_template_sha256": completion["runtime_template_sha256"],
        "schema": PUBLICATION_RECEIPT_SCHEMA,
    }
    if (
        receipt != expected
        or len(raw) != completion["receipt_bytes"]
        or hashlib.sha256(raw).hexdigest() != completion["receipt_sha256"]
    ):
        raise FormalSupervisorError("publication receipt rejected")


def _validated_bootstrap_execution_authorization(
    raw: bytes,
    *,
    payload: Mapping[str, Any],
    public_der: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    outer = _strict_canonical_json(raw, label="bootstrap execution authorization")
    if set(outer) != {"payload", "signature_base64"}:
        raise FormalSupervisorError("bootstrap execution authorization rejected")
    authorization = outer.get("payload")
    if (
        type(authorization) is not dict
        or set(authorization) != _BOOTSTRAP_EXECUTION_AUTHORIZATION_FIELDS
        or authorization.get("schema") != BOOTSTRAP_EXECUTION_AUTHORIZATION_SCHEMA
        or authorization.get("project_id") != "quant-signal-lkj"
    ):
        raise FormalSupervisorError("bootstrap execution authorization rejected")
    _verify_signature(
        _canonical_bytes(authorization),
        _decoded_signature(outer.get("signature_base64")),
        public_der=public_der,
    )
    policy = authorization.get("stdlib_policy")
    try:
        normalized_policy = validate_stdlib_policy(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
            require_filesystem=False,
        )
    except ValueError as exc:
        raise FormalSupervisorError("bootstrap stdlib policy rejected") from exc
    issued_at = _parse_utc(
        authorization.get("issued_at_utc"),
        label="bootstrap authorization issuance",
    )
    not_before = _parse_utc(
        authorization.get("not_before_utc"),
        label="bootstrap authorization activation",
    )
    expires_at = _parse_utc(
        authorization.get("expires_at_utc"),
        label="bootstrap authorization expiry",
    )
    validation_clock = now_utc or not_before
    expected = {
        "action": payload["worker_action"],
        "authorization_id_sha256": payload["authorization_id_sha256"],
        "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
        "control_contract_descriptor_sha256": payload["control_contract_descriptor_sha256"],
        "replay_scope": payload["replay_scope"],
        "stdlib_policy_root_sha256": payload["stdlib_inventory_root_sha256"],
        "supervisor_protocol": worker_protocol_descriptor(),
    }
    if (
        any(
            authorization.get(key) != value
            for key, value in expected.items()
            if key != "supervisor_protocol"
        )
        or authorization.get("execution_authorization_key_id") != payload["execution_key_id"]
        or authorization.get("execution_authorization_key_role")
        != BOOTSTRAP_EXECUTION_AUTHORIZATION_KEY_ROLE
        or authorization.get("supervisor_protocol") != expected["supervisor_protocol"]
        or normalized_policy["pycache_prefix"] != payload["worker_pycache_prefix"]
        or not issued_at <= not_before < expires_at
        or not not_before <= validation_clock <= expires_at
        or (expires_at - issued_at).total_seconds() > _MAX_AUTHORIZATION_LIFETIME_SECONDS
    ):
        raise FormalSupervisorError("bootstrap execution authorization binding rejected")
    return dict(authorization)


class _StdlibInventoryGuard:
    def __init__(
        self,
        policy: Mapping[str, Any],
        *,
        expected_root_sha256: str,
    ) -> None:
        self._policy = dict(policy)
        self._expected_root_sha256 = expected_root_sha256

    def postverify(self) -> None:
        try:
            observed = validate_stdlib_policy(
                self._policy,
                expected_root_sha256=self._expected_root_sha256,
                require_filesystem=True,
            )
        except ValueError as exc:
            raise FormalSupervisorError("stdlib policy drifted") from exc
        if observed != self._policy:
            raise FormalSupervisorError("stdlib policy drifted")


def _hold_stdlib_inventory(
    raw: bytes,
    *,
    payload: Mapping[str, Any],
    authorization: Mapping[str, Any],
    stack: ExitStack,
) -> tuple[Any, ...]:
    policy = _strict_canonical_json(raw, label="stdlib policy")
    if policy != authorization["stdlib_policy"]:
        raise FormalSupervisorError("stdlib policy publication rejected")
    try:
        policy = validate_stdlib_policy(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
            require_filesystem=False,
        )
    except ValueError as exc:
        raise FormalSupervisorError("stdlib policy rejected") from exc
    frozen_directories = tuple(Path(str(item["path"])) for item in policy["roots"]) + tuple(
        Path(path).parent for path in policy["absent_paths"]
    )
    stack.enter_context(_held_frozen_directory_tree(frozen_directories))
    try:
        policy = validate_stdlib_policy(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
            require_filesystem=True,
        )
    except ValueError as exc:
        raise FormalSupervisorError("stdlib policy rejected") from exc
    handles: list[_HeldFile] = []
    pycache_blocker = _HeldFile(
        _absolute_path(
            policy["pycache_prefix"],
            label="stdlib pycache blocker",
        ),
        expected_sha256=None,
        label="stdlib pycache blocker",
        max_bytes=_MAX_AUTHORIZATION_BYTES,
        allow_empty=False,
        allow_hardlinks=False,
    )
    stack.callback(pycache_blocker.close)
    handles.append(pycache_blocker)
    for item in policy["entries"]:
        path = _absolute_path(item.get("path"), label="stdlib inventory path")
        byte_count = item.get("bytes")
        digest = _require_sha256(item.get("sha256"), label="stdlib inventory SHA")
        if type(byte_count) is not int or not 0 <= byte_count <= _MAX_EXECUTABLE_BYTES:
            raise FormalSupervisorError("stdlib inventory rejected")
        held = _HeldFile(
            path,
            expected_sha256=digest,
            label="stdlib inventory file",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_empty=item["kind"] == "source",
            allow_hardlinks=True,
        )
        stack.callback(held.close)
        if len(held.raw) != byte_count:
            raise FormalSupervisorError("stdlib inventory rejected")
        handles.append(held)
    try:
        validate_stdlib_policy(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
            require_filesystem=True,
        )
        validate_stdlib_policy(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
            require_filesystem=True,
        )
    except ValueError as exc:
        raise FormalSupervisorError("stdlib policy rejected") from exc
    return (
        *handles,
        _StdlibInventoryGuard(
            policy,
            expected_root_sha256=str(payload["stdlib_inventory_root_sha256"]),
        ),
    )


def _validate_resume_status(
    payload: Mapping[str, Any],
    stack: ExitStack,
) -> None:
    if payload["action"] != "resume":
        return
    _reject_completed_resume(payload)
    status_path = Path(str(payload["resume_status_path"]))
    expected_path = claim_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        str(payload["resume_of_authorization_sha256"]),
    )
    if os.path.normcase(str(status_path)) != os.path.normcase(str(expected_path)):
        raise FormalSupervisorError("resume status path rejected")
    expected_sha256 = str(payload["resume_status_sha256"])
    held = _HeldFile(
        status_path,
        expected_sha256=expected_sha256,
        label="resume status",
        max_bytes=_MAX_AUTHORIZATION_BYTES,
    )
    stack.callback(held.close)
    status = _strict_canonical_json(held.raw, label="resume status")
    if (
        status.get("schema") != CLAIM_SCHEMA
        or status.get("status") != "claimed"
        or status.get("launch_authorization_sha256") != payload["resume_of_authorization_sha256"]
        or status.get("authorization_id_sha256") != payload["resume_of_authorization_id_sha256"]
        or status.get("authorization_nonce_sha256")
        != payload["resume_of_authorization_nonce_sha256"]
        or status.get("bootstrap_execution_authorization_sha256")
        != payload["resume_of_bootstrap_execution_authorization_sha256"]
        or status.get("replay_scope") != payload["resume_of_replay_scope"]
    ):
        raise FormalSupervisorError("resume status rejected")
    nonce_key = hashlib.sha256(
        _canonical_bytes(
            {
                "authorization_nonce_sha256": payload["resume_of_authorization_nonce_sha256"],
                "replay_scope": payload["resume_of_replay_scope"],
            }
        )
    ).hexdigest()
    tuple_paths = (
        _ledger_path(
            Path(str(payload["execution_ledger_root"])),
            "bootstrap_authorizations",
            str(payload["resume_of_bootstrap_execution_authorization_sha256"]),
        ),
        _ledger_path(
            Path(str(payload["execution_ledger_root"])),
            "authorization_ids",
            str(payload["resume_of_authorization_id_sha256"]),
        ),
        _ledger_path(
            Path(str(payload["execution_ledger_root"])),
            "authorization_nonces",
            nonce_key,
        ),
    )
    for tuple_path in tuple_paths:
        tuple_handle = _HeldFile(
            tuple_path,
            expected_sha256=expected_sha256,
            label="resume replay tuple",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(tuple_handle.close)
        if tuple_handle.raw != held.raw:
            raise FormalSupervisorError("resume replay tuple rejected")


def _reject_completed_resume(payload: Mapping[str, Any]) -> None:
    if payload.get("action") != "resume":
        return
    ledger_root = _absolute_path(
        payload.get("execution_ledger_root"),
        label="execution ledger root",
    )
    original_sha256 = _require_sha256(
        payload.get("resume_of_authorization_sha256"),
        label="resume original authorization",
    )
    if completed_path_for_authorization(ledger_root, original_sha256).exists():
        raise FormalSupervisorError("completed authorization cannot be resumed")


def _supervise_with_pins(
    *,
    authorization_path: Path | str,
    pins: _SupervisorPins,
    now_utc: datetime,
    environment_snapshot: Mapping[str, str],
    output_writer: Any = _OS_WRITE,
) -> dict[str, Any]:
    if os.name != "nt":
        raise FormalSupervisorError("trusted supervisor requires Windows")
    _validate_pins(pins)
    authorization_path = _absolute_path(
        str(authorization_path),
        label="launch authorization path",
    )
    authorization_sha256 = authorization_path.stem
    _require_sha256(
        authorization_sha256,
        label="launch authorization path SHA",
    )
    _validate_cas_path(
        authorization_path,
        authorization_sha256,
        category="launch_authorizations",
        suffix=".json",
        label="launch authorization",
    )
    with ExitStack() as stack:
        authorization_handle = _HeldFile(
            authorization_path,
            expected_sha256=authorization_sha256,
            label="launch authorization",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(authorization_handle.close)
        try:
            public_der = base64.b64decode(
                pins.execution_public_key_spki_der_base64.encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, ValueError):
            raise FormalSupervisorError("embedded execution public key rejected") from None
        outer = _strict_canonical_json(
            authorization_handle.raw,
            label="launch authorization",
        )
        if set(outer) != {"payload", "signature_base64"}:
            raise FormalSupervisorError("launch authorization rejected")
        payload = _validate_launch_payload(
            outer.get("payload"),
            pins=pins,
            now_utc=now_utc,
        )
        _verify_signature(
            _canonical_bytes(payload),
            _decoded_signature(outer.get("signature_base64")),
            public_der=public_der,
        )
        self_source_path = Path(pins.repo_root) / Path(
            *pins.supervisor_source_relative_path.split("/")
        )
        self_source = _HeldFile(
            self_source_path,
            expected_sha256=pins.supervisor_source_sha256,
            label="trusted supervisor source",
            max_bytes=_MAX_WORKER_BYTES,
        )
        stack.callback(self_source.close)
        control_contract_source = _HeldFile(
            _pinned_control_contract_path(pins),
            expected_sha256=pins.control_contract_source_sha256,
            label="pinned control contract source",
            max_bytes=_MAX_WORKER_BYTES,
        )
        stack.callback(control_contract_source.close)
        if control_contract_source.raw != _fixed_control_contract_source():
            raise FormalSupervisorError("pinned control contract source rejected")
        python_handle = _HeldFile(
            Path(pins.python_executable_path),
            expected_sha256=pins.python_executable_sha256,
            label="fixed Python executable",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_hardlinks=True,
        )
        stack.callback(python_handle.close)
        base_python_handle = _HeldFile(
            Path(pins.base_python_executable_path),
            expected_sha256=pins.base_python_executable_sha256,
            label="fixed base Python executable",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_hardlinks=True,
        )
        stack.callback(base_python_handle.close)
        git_handle = _HeldFile(
            Path(pins.git_executable_path),
            expected_sha256=pins.git_executable_sha256,
            label="fixed Git executable",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_hardlinks=True,
        )
        stack.callback(git_handle.close)
        if _git_head(pins, environment_snapshot) != pins.supervisor_expected_commit:
            raise FormalSupervisorError("supervisor commit verification rejected")
        for field in (
            "bootstrap_output_root",
            "execution_ledger_root",
            "formal_input_root_path",
            "formal_output_root",
            "run_root",
        ):
            stack.enter_context(_held_directory_chain(Path(str(payload[field]))))
        worker_handle = _HeldFile(
            Path(str(payload["bootstrap_worker_path"])),
            expected_sha256=str(payload["bootstrap_worker_sha256"]),
            label="bootstrap worker",
            max_bytes=_MAX_WORKER_BYTES,
        )
        stack.callback(worker_handle.close)
        completion_handle = _HeldFile(
            Path(str(payload["publication_completion_marker_path"])),
            expected_sha256=str(payload["publication_completion_marker_sha256"]),
            label="publication completion marker",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(completion_handle.close)
        run_spec_handle = _HeldFile(
            Path(str(payload["run_spec_path"])),
            expected_sha256=str(payload["run_spec_sha256"]),
            label="run spec",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(run_spec_handle.close)
        bootstrap_authorization_handle = _HeldFile(
            Path(str(payload["bootstrap_execution_authorization_path"])),
            expected_sha256=str(payload["bootstrap_execution_authorization_sha256"]),
            label="bootstrap execution authorization",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(bootstrap_authorization_handle.close)
        stdlib_policy_handle = _HeldFile(
            Path(str(payload["stdlib_policy_path"])),
            expected_sha256=str(payload["stdlib_policy_sha256"]),
            label="stdlib policy",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(stdlib_policy_handle.close)
        _validate_cas_path(
            Path(str(payload["bootstrap_worker_path"])),
            str(payload["bootstrap_worker_sha256"]),
            category="bootstraps",
            suffix=".py",
            label="bootstrap worker",
        )
        _validate_cas_path(
            Path(str(payload["publication_completion_marker_path"])),
            str(payload["publication_completion_marker_sha256"]),
            category="completion_markers",
            suffix=".json",
            label="publication completion marker",
        )
        _validate_cas_path(
            Path(str(payload["bootstrap_execution_authorization_path"])),
            str(payload["bootstrap_execution_authorization_sha256"]),
            category="execution-authorizations",
            suffix=".json",
            label="bootstrap execution authorization",
        )
        _validate_cas_path(
            Path(str(payload["stdlib_policy_path"])),
            str(payload["stdlib_policy_sha256"]),
            category="stdlib_policies",
            suffix=".json",
            label="stdlib policy",
        )
        bootstrap_root = Path(str(payload["bootstrap_output_root"]))
        expected_worker_path = (
            bootstrap_root
            / "bootstraps"
            / "sha256"
            / str(payload["bootstrap_worker_sha256"])[:2]
            / f"{payload['bootstrap_worker_sha256']}.py"
        )
        expected_completion_path = (
            bootstrap_root
            / "completion_markers"
            / "sha256"
            / str(payload["publication_completion_marker_sha256"])[:2]
            / f"{payload['publication_completion_marker_sha256']}.json"
        )
        if os.path.normcase(str(expected_worker_path)) != os.path.normcase(
            str(payload["bootstrap_worker_path"])
        ) or os.path.normcase(str(expected_completion_path)) != os.path.normcase(
            str(payload["publication_completion_marker_path"])
        ):
            raise FormalSupervisorError("bootstrap publication root rejected")
        completion = _validated_publication_completion(
            completion_handle.raw,
            payload=payload,
            public_der=public_der,
        )
        receipt_path = bootstrap_root / Path(*str(completion["receipt_relative_path"]).split("/"))
        receipt_handle = _HeldFile(
            receipt_path,
            expected_sha256=str(completion["receipt_sha256"]),
            label="publication receipt",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        stack.callback(receipt_handle.close)
        _validate_publication_receipt(
            receipt_handle.raw,
            completion=completion,
        )
        if completion["bootstrap_bytes"] != len(worker_handle.raw) or completion[
            "stdlib_policy_bytes"
        ] != len(stdlib_policy_handle.raw):
            raise FormalSupervisorError("publication completion marker rejected")
        authorization = _validated_bootstrap_execution_authorization(
            bootstrap_authorization_handle.raw,
            payload=payload,
            public_der=public_der,
            now_utc=now_utc,
        )
        stdlib_handles = _hold_stdlib_inventory(
            stdlib_policy_handle.raw,
            payload=payload,
            authorization=authorization,
            stack=stack,
        )
        _validate_resume_status(payload, stack)
        worker_environment, secret_names = _worker_environment(
            payload,
            launch_authorization_sha256=authorization_sha256,
            snapshot=environment_snapshot,
        )
        ledger_chain = stack.enter_context(
            _held_directory_chain(Path(str(payload["execution_ledger_root"])))
        )
        claim_raw = _canonical_bytes(
            {
                "action": payload["action"],
                "authorization_id_sha256": payload["authorization_id_sha256"],
                "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
                "bootstrap_execution_authorization_sha256": payload[
                    "bootstrap_execution_authorization_sha256"
                ],
                "launch_authorization_sha256": authorization_sha256,
                "replay_scope": payload["replay_scope"],
                "schema": CLAIM_SCHEMA,
                "status": "claimed",
            }
        )
        ledger_handles: list[_HeldLedgerFile] = []
        claim_handle, claim_path, claim_sha256 = _ledger_write_once(
            ledger_chain,
            stack=stack,
            category="claims",
            authorization_sha256=authorization_sha256,
            raw=claim_raw,
            replay_label="execution authorization",
        )
        ledger_handles.append(claim_handle)
        if payload["action"] == "resume":
            resumed_handle, _resumed_path, _resumed_sha256 = _ledger_write_once(
                ledger_chain,
                stack=stack,
                category="resumed_authorizations",
                authorization_sha256=str(payload["resume_of_authorization_sha256"]),
                raw=claim_raw,
                replay_label="resume authorization",
            )
            ledger_handles.append(resumed_handle)
        else:
            replay_keys = (
                (
                    "bootstrap_authorizations",
                    str(payload["bootstrap_execution_authorization_sha256"]),
                    "bootstrap execution authorization",
                ),
                (
                    "authorization_ids",
                    str(payload["authorization_id_sha256"]),
                    "execution authorization id",
                ),
                (
                    "authorization_nonces",
                    hashlib.sha256(
                        _canonical_bytes(
                            {
                                "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
                                "replay_scope": payload["replay_scope"],
                            }
                        )
                    ).hexdigest(),
                    "execution authorization nonce",
                ),
            )
            for category, replay_key, replay_label in replay_keys:
                replay_handle, _replay_path, _replay_sha256 = _ledger_write_once(
                    ledger_chain,
                    stack=stack,
                    category=category,
                    authorization_sha256=replay_key,
                    raw=claim_raw,
                    replay_label=replay_label,
                )
                ledger_handles.append(replay_handle)
        returncode, stdout, stderr = _run_worker(
            list(payload["worker_argv"]),
            cwd=str(payload["repo_root"]),
            environment=worker_environment,
            timeout_seconds=int(payload["worker_timeout_seconds"]),
        )
        _reject_completed_resume(payload)
        if (
            _contains_secret(
                stdout + stderr,
                snapshot=environment_snapshot,
                secret_names=secret_names,
            )
            or returncode != 0
            or stderr
        ):
            raise FormalSupervisorError("isolated worker terminal rejected")
        frame = _parse_worker_terminal(
            stdout,
            payload=payload,
            authorization_sha256=authorization_sha256,
        )
        artifact_manifest_sha256, artifact_handles = _validate_artifacts(
            frame["artifacts"],
            roots=(
                Path(str(payload["formal_output_root"])),
                Path(str(payload["run_root"])),
            ),
            stack=stack,
        )
        terminal_handles = (
            authorization_handle,
            self_source,
            control_contract_source,
            python_handle,
            base_python_handle,
            git_handle,
            worker_handle,
            completion_handle,
            receipt_handle,
            run_spec_handle,
            bootstrap_authorization_handle,
            stdlib_policy_handle,
            *stdlib_handles,
            *artifact_handles,
            *ledger_handles,
        )
        for handle in terminal_handles:
            handle.postverify()
        completed_raw = _canonical_bytes(
            {
                "artifact_manifest_sha256": artifact_manifest_sha256,
                "claim_sha256": claim_sha256,
                "launch_authorization_sha256": authorization_sha256,
                "schema": COMPLETED_SCHEMA,
                "status": "completed",
                "worker_terminal_sha256": hashlib.sha256(stdout).hexdigest(),
            }
        )
        completed_handle, completed_path, completed_sha256 = _ledger_write_once(
            ledger_chain,
            stack=stack,
            category="completed",
            authorization_sha256=authorization_sha256,
            raw=completed_raw,
            replay_label="completed execution",
        )
        terminal_handles = (*terminal_handles, completed_handle)
        for handle in terminal_handles:
            handle.postverify()
        ledger_chain.postverify()
        _reject_completed_resume(payload)
        _write_all(1, stdout, writer=output_writer)
        for handle in terminal_handles:
            handle.postverify()
        ledger_chain.postverify()
        return {
            "claim_path": str(claim_path),
            "claim_sha256": claim_sha256,
            "completed_path": str(completed_path),
            "completed_sha256": completed_sha256,
            "launch_authorization_sha256": authorization_sha256,
            "status": "completed",
            "worker_terminal_sha256": hashlib.sha256(stdout).hexdigest(),
        }


def supervise_factor_v3_formal_execution(
    authorization_path: Path | str,
) -> dict[str, Any]:
    pins = _FIXED_PINS
    if pins is None:
        raise FormalSupervisorError("fixed production supervisor pins unavailable")
    return _supervise_with_pins(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime.now(timezone.utc).replace(microsecond=0),
        environment_snapshot=os.environ,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise FormalSupervisorError("exactly one launch authorization path is required")
    supervise_factor_v3_formal_execution(sys.argv[1])
