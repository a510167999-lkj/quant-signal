from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


class LauncherError(RuntimeError):
    pass


_PATH_ENVIRONMENT_KEYS = (
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "XDG_CACHE_HOME",
    "PYTHONPYCACHEPREFIX",
)
_PASSTHROUGH_ENVIRONMENT_KEYS = (
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
)
_FIXED_ENVIRONMENT = {
    "PYTHONNOUSERSITE": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "PYTEST_ADDOPTS": "",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONHASHSEED": "0",
}
_CHILD_PATH_OPTIONS = {
    "--audited-pit-artifact-path",
    "--audited-pit-universe-path",
    "--cache-dir",
    "--composite-pit-descriptor-path",
    "--input-plan-path",
    "--plan-publication-parent-proof-path",
    "--pit-universe-path",
    "--qualified-trades-output",
    "--temporal-contract-path",
}
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^@?[A-Za-z]:[\\/]")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CREATE_SUSPENDED = 0x00000004
_READY_V1_BINDING_KEYS = (
    "plan_sha256",
    "plan_file_sha256",
    "fixture_id",
    "fixture_manifest_file_sha256",
)
_READY_V2_BINDING_KEYS = (
    "experiment_id",
    "registered_record_hash",
    "registered_sequence",
    "registration_contract_sha256",
    "publication_id",
    "publication_result_file_sha256",
    "plan_sha256",
    "plan_file_sha256",
    "fixture_id",
    "fixture_manifest_file_sha256",
    "run_claim_file_sha256",
    "control_source_bundle_sha256",
)
_READY_V3_BINDING_KEYS = (
    *_READY_V2_BINDING_KEYS,
    "ledger_path_sha256",
    "launch_lease_file_sha256",
)
_READY_V4_BINDING_KEYS = (
    *_READY_V3_BINDING_KEYS,
    "precompute_launch_started_schema_version",
    "launch_started_record_hash",
    "launch_started_sequence",
    "parent_proof_file_sha256",
    "parent_proof_canonical_sha256",
)
_READY_V5_BINDING_KEYS = (
    *_READY_V4_BINDING_KEYS,
    "legacy_quarantine_sha256",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolved_directory(path: Path, *, parent: Path | None = None) -> Path:
    if not path.is_absolute():
        raise LauncherError("launcher path must be absolute")
    resolved = path.resolve()
    if path != resolved or path.is_symlink() or not path.is_dir():
        raise LauncherError("launcher directory must be a real non-alias directory")
    if parent is not None and not _is_within(resolved, parent):
        raise LauncherError("launcher directory is outside workspace")
    return resolved


def _resolved_file(path: Path, *, parent: Path) -> Path:
    if not path.is_absolute():
        raise LauncherError("launcher executable must be absolute")
    resolved = path.resolve()
    if path != resolved or path.is_symlink() or not path.is_file():
        raise LauncherError("launcher executable must be a real non-alias file")
    if not _is_within(resolved, parent):
        raise LauncherError("launcher executable is outside workspace")
    return resolved


def build_e_only_environment(
    base_environment: Mapping[str, str], *, sandbox_root: Path
) -> dict[str, str]:
    sandbox = _resolved_directory(Path(sandbox_root))
    if os.name == "nt" and sandbox.drive.lower() != "e:":
        raise LauncherError("launcher sandbox must be on E drive")
    casefolded: dict[str, tuple[str, str]] = {}
    for key, value in base_environment.items():
        normalized = str(key).casefold()
        if normalized in casefolded:
            raise LauncherError("launcher base environment has duplicate keys")
        casefolded[normalized] = (str(key), str(value))
    environment = {}
    for key in _PASSTHROUGH_ENVIRONMENT_KEYS:
        item = casefolded.get(key.casefold())
        if item is not None and item[1]:
            environment[key] = item[1]
    relative_paths = {
        "TEMP": "temp",
        "TMP": "temp",
        "TMPDIR": "temp",
        "HOME": "home",
        "USERPROFILE": "home",
        "LOCALAPPDATA": "localappdata",
        "APPDATA": "appdata",
        "XDG_CACHE_HOME": "xdg-cache",
        "PYTHONPYCACHEPREFIX": "pycache",
    }
    for key, relative in relative_paths.items():
        target = sandbox / relative
        target.mkdir(parents=False, exist_ok=True)
        environment[key] = str(target)
    environment.update(_FIXED_ENVIRONMENT)
    return environment


def _validate_environment(environment: Mapping[str, str], sandbox: Path) -> dict[str, str]:
    allowed = {
        *(_PATH_ENVIRONMENT_KEYS),
        *(_PASSTHROUGH_ENVIRONMENT_KEYS),
        *(_FIXED_ENVIRONMENT),
    }
    if any(key not in allowed for key in environment):
        raise LauncherError("launcher environment contains an unapproved key")
    bound: dict[str, str] = {}
    for key in _PATH_ENVIRONMENT_KEYS:
        value = environment.get(key)
        if not isinstance(value, str) or not value:
            raise LauncherError(f"launcher environment {key} is missing")
        path = _resolved_directory(Path(value), parent=sandbox)
        bound[key] = str(path)
    if environment.get("PYTHONNOUSERSITE") != "1":
        raise LauncherError("launcher must disable the user site")
    if environment.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1":
        raise LauncherError("launcher must disable pytest plugin autoload")
    for key, expected in _FIXED_ENVIRONMENT.items():
        if environment.get(key) != expected:
            raise LauncherError(f"launcher environment {key} is invalid")
    return bound


def _strict_json_object(raw: str) -> dict:
    def object_pairs(pairs):
        payload = {}
        for key, value in pairs:
            if key in payload:
                raise LauncherError("launcher READY JSON contains duplicate keys")
            payload[key] = value
        return payload

    def reject_constant(value):
        raise LauncherError(f"launcher READY JSON constant is invalid: {value}")

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except LauncherError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LauncherError("launcher READY JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise LauncherError("launcher READY payload is invalid")
    return payload


def _validate_child_arguments(
    arguments: Sequence[str],
    workspace: Path,
    *,
    allow_test_inline_code: bool,
    allow_zero_write_probe: bool,
) -> list[str]:
    tokens = [str(token) for token in arguments]
    if not tokens:
        raise LauncherError("launcher child arguments are empty")
    if allow_test_inline_code:
        if tokens[0] != "-c":
            raise LauncherError("launcher test command shape is invalid")
    elif allow_zero_write_probe and tokens[:2] == [
        "-m",
        "app.research_launcher_probe",
    ]:
        required = {
            "--plan-sha256",
            "--plan-file-sha256",
            "--fixture-id",
            "--fixture-manifest-file-sha256",
        }
        options = tokens[2:]
        if len(options) != len(required) * 2 or set(options[::2]) != required:
            raise LauncherError("launcher zero-write probe command is invalid")
    elif tokens[:3] != ["-m", "app.jobs", "research-historical-universe"]:
        raise LauncherError("launcher research command shape is invalid")
    for index, token in enumerate(tokens):
        if "\x00" in token:
            raise LauncherError("launcher child argument contains NUL")
        candidate = token[1:] if token.startswith("@") else token
        if (
            _WINDOWS_ABSOLUTE_PATH.match(candidate)
            or candidate.startswith(("\\\\", "//"))
            or ".." in re.split(r"[\\\\/]", candidate)
        ):
            resolved = Path(candidate).resolve()
            if not _is_within(resolved, workspace):
                raise LauncherError("launcher argument path is outside workspace")
        if index > 0 and tokens[index - 1] in _CHILD_PATH_OPTIONS:
            path = Path(token)
            if not path.is_absolute() or not _is_within(path.resolve(), workspace):
                raise LauncherError("launcher argument path is outside workspace")
        for option in _CHILD_PATH_OPTIONS:
            prefix = f"{option}="
            if token.startswith(prefix):
                path = Path(token.removeprefix(prefix))
                if not path.is_absolute() or not _is_within(
                    path.resolve(), workspace
                ):
                    raise LauncherError("launcher argument path is outside workspace")
    return tokens


def _atomic_write_json(path: Path, payload: dict) -> None:
    encoded = _canonical_bytes(payload) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise LauncherError("launcher audit output already exists")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _ready_binding_keys(payload: Mapping[str, object]) -> tuple[str, ...]:
    schema_version = payload.get("schema_version")
    if schema_version == "research-launcher-ready/v1":
        return _READY_V1_BINDING_KEYS
    if schema_version == "research-launcher-ready/v2":
        return _READY_V2_BINDING_KEYS
    if schema_version == "research-launcher-ready/v3":
        return _READY_V3_BINDING_KEYS
    if schema_version == "research-launcher-ready/v4":
        return _READY_V4_BINDING_KEYS
    if schema_version == "research-launcher-ready/v5":
        return _READY_V5_BINDING_KEYS
    raise LauncherError("launcher READY contract is invalid")


def _validated_ready_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise LauncherError("launcher READY payload is invalid")
    if payload.get("event") != "READY":
        raise LauncherError("launcher READY contract is invalid")
    binding_keys = _ready_binding_keys(payload)
    if payload["schema_version"] in {
        "research-launcher-ready/v2",
        "research-launcher-ready/v3",
        "research-launcher-ready/v4",
        "research-launcher-ready/v5",
    }:
        if set(payload) != {"schema_version", "event", *binding_keys}:
            raise LauncherError("launcher READY fields are invalid")
        experiment_id = payload.get("experiment_id")
        sequence = payload.get("registered_sequence")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise LauncherError("launcher READY experiment binding is invalid")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= 126
        ):
            raise LauncherError("launcher READY ledger sequence is invalid")
        if payload["schema_version"] in {
            "research-launcher-ready/v4",
            "research-launcher-ready/v5",
        }:
            launch_sequence = payload.get("launch_started_sequence")
            if (
                payload.get("precompute_launch_started_schema_version")
                != (
                    "research-precompute-launch-started/v2"
                    if payload["schema_version"] == "research-launcher-ready/v5"
                    else "research-precompute-launch-started/v1"
                )
                or isinstance(launch_sequence, bool)
                or not isinstance(launch_sequence, int)
                or launch_sequence != sequence + 1
            ):
                raise LauncherError("launcher READY launch binding is invalid")
    hash_keys = binding_keys
    if payload["schema_version"] in {
        "research-launcher-ready/v2",
        "research-launcher-ready/v3",
        "research-launcher-ready/v4",
        "research-launcher-ready/v5",
    }:
        hash_keys = tuple(
            key
            for key in binding_keys
            if key
            not in {
                "experiment_id",
                "registered_sequence",
                "launch_started_sequence",
                "precompute_launch_started_schema_version",
            }
        )
    for key in hash_keys:
        if not isinstance(payload.get(key), str) or not _LOWER_SHA256.fullmatch(
            payload[key]
        ):
            raise LauncherError("launcher READY hash binding is invalid")
    return payload


class _NoopProcessGuard:
    description = "direct-child-reap"

    def close(self) -> None:
        return None


class _WindowsJobProcessGuard:
    description = "windows-job-object-active-process-limit-2-venv-trampoline"

    def __init__(self, handle: int, kernel32):
        self._handle = handle
        self._kernel32 = kernel32

    def terminate_tree(self) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            if not self._kernel32.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = 0


def _attach_process_guard(process):
    if os.name != "nt":
        return _NoopProcessGuard()

    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = tuple(
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        )

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x00000008 | 0x00002000
        limits.BasicLimitInformation.ActiveProcessLimit = 2
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return _WindowsJobProcessGuard(handle, kernel32)


def _resume_suspended_process(process) -> None:
    if os.name != "nt":
        return

    from ctypes import wintypes

    class THREADENTRY32(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(THREADENTRY32),
    )
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(THREADENTRY32),
    )
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    if int(snapshot) == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    thread_ids = []
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.th32OwnerProcessID == process.pid:
                thread_ids.append(entry.th32ThreadID)
            present = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if len(thread_ids) != 1:
        raise LauncherError("launcher suspended process thread identity is invalid")
    thread = kernel32.OpenThread(0x0002, False, thread_ids[0])
    if not thread:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        previous_count = kernel32.ResumeThread(thread)
        if previous_count == 0xFFFFFFFF or previous_count != 1:
            raise LauncherError("launcher suspended process could not be resumed once")
    finally:
        kernel32.CloseHandle(thread)


def _terminate_and_reap(process) -> tuple[bool, bool]:
    terminated = False
    killed = False
    if process.poll() is None:
        process.terminate()
        terminated = True
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            killed = True
            process.wait(timeout=5.0)
    else:
        process.wait()
    return terminated, killed


def run_supervised(
    *,
    workspace_root: Path,
    python_executable: Path,
    sandbox_root: Path,
    audit_dir: Path,
    child_args: Sequence[str],
    expected_ready: Mapping[str, object],
    ready_timeout_seconds: float = 30.0,
    environment: Mapping[str, str] | None = None,
    allow_test_inline_code: bool = False,
    allow_zero_write_probe: bool = False,
) -> dict:
    if (
        isinstance(ready_timeout_seconds, bool)
        or not isinstance(ready_timeout_seconds, (int, float))
        or not math.isfinite(float(ready_timeout_seconds))
        or not 0 < float(ready_timeout_seconds) <= 30.0
    ):
        raise LauncherError("launcher READY timeout is invalid")
    workspace = _resolved_directory(Path(workspace_root))
    if workspace.drive.lower() != "e:":
        raise LauncherError("launcher workspace must be on E drive")
    executable = _resolved_file(Path(python_executable), parent=workspace)
    if os.name == "nt" and executable != (
        workspace / ".venv" / "Scripts" / "python.exe"
    ).resolve():
        raise LauncherError("launcher executable must be the workspace E venv")
    sandbox = _resolved_directory(Path(sandbox_root), parent=workspace)
    audit = _resolved_directory(Path(audit_dir), parent=sandbox)
    if any(audit.iterdir()):
        raise LauncherError("launcher audit directory must be empty")
    child_tokens = _validate_child_arguments(
        child_args,
        workspace,
        allow_test_inline_code=allow_test_inline_code,
        allow_zero_write_probe=allow_zero_write_probe,
    )
    expected_ready_payload = _validated_ready_payload(dict(expected_ready))
    ready_binding_keys = _ready_binding_keys(expected_ready_payload)
    child_environment = dict(
        environment
        if environment is not None
        else build_e_only_environment(os.environ, sandbox_root=sandbox)
    )
    environment_paths = _validate_environment(child_environment, sandbox)
    command = [str(executable), *child_tokens]
    arguments_sha256 = hashlib.sha256(_canonical_bytes({"tokens": child_tokens})).hexdigest()
    stdout_path = audit / "launcher-stdout.log"
    stderr_path = audit / "launcher-stderr.log"
    start_path = audit / "launcher-start.json"
    receipt_path = audit / "launcher-receipt.json"
    ready_ack_path = audit / "launcher-ready-ack.json"
    child_environment["RESEARCH_SUPERVISED_READY_ACK_PATH"] = str(ready_ack_path)
    started_at = _utc_now()
    process = None
    guard = None
    stdout_thread = None
    stderr_thread = None
    ready_event = threading.Event()
    ready_state: dict[str, object] = {}
    pump_errors: list[BaseException] = []

    def pump(stream, output_path: Path, *, inspect_ready: bool) -> None:
        try:
            with output_path.open("xb") as output:
                for raw_line in iter(stream.readline, b""):
                    output.write(raw_line)
                    output.flush()
                    if inspect_ready and raw_line.startswith(b"READY "):
                        decoded = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
                        ready_state["count"] = int(ready_state.get("count", 0)) + 1
                        if ready_state["count"] == 1:
                            ready_state["payload"] = _validated_ready_payload(
                                _strict_json_object(decoded.removeprefix("READY "))
                            )
                        ready_event.set()
        except BaseException as exc:
            pump_errors.append(exc)
            ready_event.set()

    try:
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=child_environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=_CREATE_SUSPENDED if os.name == "nt" else 0,
        )
        guard = _attach_process_guard(process)
        guard_description = getattr(guard, "description", "custom-process-guard")
        _atomic_write_json(
            start_path,
            {
                "schema_version": "research-supervised-launch-start/v1",
                "pid": process.pid,
                "started_at_utc": started_at,
                "workspace_root": str(workspace),
                "python_executable": str(executable),
                "arguments_sha256": arguments_sha256,
                "argument_count": len(child_tokens),
                "environment_paths": environment_paths,
                "expected_ready": expected_ready_payload,
                "shell": False,
                "use_shell_execute": False,
                "created_suspended": os.name == "nt",
                "stdin_closed": True,
                "process_tree_guard": guard_description,
                "venv_trampoline_process_allowance": 1,
                "child_processes_created": 1,
            },
        )
        _resume_suspended_process(process)
        stdout_thread = threading.Thread(
            target=pump,
            args=(process.stdout, stdout_path),
            kwargs={"inspect_ready": True},
            name="research-launcher-stdout",
        )
        stderr_thread = threading.Thread(
            target=pump,
            args=(process.stderr, stderr_path),
            kwargs={"inspect_ready": False},
            name="research-launcher-stderr",
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + float(ready_timeout_seconds)
        failure_code = None
        terminated = False
        killed = False
        while not ready_event.is_set():
            if process.poll() is not None:
                ready_event.wait(0.1)
                if not ready_event.is_set():
                    failure_code = "READY_MISSING"
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure_code = "READY_TIMEOUT"
                break
            ready_event.wait(min(remaining, 0.05))
        if pump_errors:
            failure_code = "OUTPUT_CAPTURE_FAILED"
        ready_payload = ready_state.get("payload")
        if failure_code is None and ready_payload is not None and (
            ready_payload.get("schema_version")
            != expected_ready_payload["schema_version"]
            or any(
                ready_payload.get(key) != expected_ready_payload[key]
                for key in ready_binding_keys
            )
        ):
            failure_code = "READY_BINDING_MISMATCH"
        direct_process_alive_at_ready = (
            ready_payload is not None and process.poll() is None
        )
        if failure_code is None and not direct_process_alive_at_ready:
            failure_code = "READY_PROCESS_EXITED"
        if failure_code is None:
            _atomic_write_json(
                ready_ack_path,
                {
                    "schema_version": expected_ready_payload["schema_version"].replace(
                        "research-launcher-ready/", "research-launcher-ready-ack/"
                    ),
                    "event": "READY_ACK",
                    **{
                        key: expected_ready_payload[key]
                        for key in ready_binding_keys
                    },
                },
            )
        if failure_code is not None:
            terminate_tree = getattr(guard, "terminate_tree", None)
            if terminate_tree is not None:
                terminate_tree()
            terminated, killed = _terminate_and_reap(process)
        exit_code = process.wait()
        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            pump_errors.append(LauncherError("launcher output thread did not close"))
        ready_line_count = int(ready_state.get("count", 0))
        if failure_code is None and ready_line_count > 1:
            failure_code = "READY_DUPLICATE"
        if pump_errors and failure_code is None:
            failure_code = "OUTPUT_CAPTURE_FAILED"
        if failure_code is None and ready_payload is None:
            failure_code = "READY_MISSING"
        if failure_code is None and exit_code != 0:
            failure_code = "CHILD_EXIT_NONZERO"
        receipt = {
            "schema_version": "research-supervised-launch-receipt/v2",
            "pid": process.pid,
            "started_at_utc": started_at,
            "finished_at_utc": _utc_now(),
            "workspace_root": str(workspace),
            "python_executable": str(executable),
            "arguments_sha256": arguments_sha256,
            "argument_count": len(child_tokens),
            "environment_paths": environment_paths,
            "expected_ready": expected_ready_payload,
            "shell": False,
            "use_shell_execute": False,
            "created_suspended": os.name == "nt",
            "stdin_closed": True,
            "process_tree_guard": guard_description,
            "venv_trampoline_process_allowance": 1,
            "child_processes_created": 1,
            "child_reaped": process.poll() is not None,
            "ready_received": ready_payload is not None,
            "ready_line_count": ready_line_count,
            "direct_process_alive_at_ready": direct_process_alive_at_ready,
            "ready_payload": ready_payload,
            "ready_ack": (
                {
                    "path": str(ready_ack_path),
                    "bytes": ready_ack_path.stat().st_size,
                    "sha256": _sha256_file(ready_ack_path),
                }
                if ready_ack_path.exists()
                else None
            ),
            "ready_timeout_seconds": float(ready_timeout_seconds),
            "exit_code": exit_code,
            "failure_code": failure_code,
            "terminated": terminated,
            "killed": killed,
            "stdout": {
                "path": str(stdout_path),
                "bytes": stdout_path.stat().st_size,
                "sha256": _sha256_file(stdout_path),
            },
            "stderr": {
                "path": str(stderr_path),
                "bytes": stderr_path.stat().st_size,
                "sha256": _sha256_file(stderr_path),
            },
        }
        _atomic_write_json(receipt_path, receipt)
        if failure_code is not None:
            labels = {
                "READY_TIMEOUT": "launcher READY timeout",
                "READY_MISSING": "launcher READY missing",
                "READY_BINDING_MISMATCH": "launcher READY binding mismatch",
                "READY_PROCESS_EXITED": "launcher READY process exited",
                "READY_DUPLICATE": "launcher duplicate READY",
                "OUTPUT_CAPTURE_FAILED": "launcher output capture failed",
                "CHILD_EXIT_NONZERO": "launcher child exited nonzero",
            }
            raise LauncherError(labels[failure_code])
        return receipt
    except BaseException:
        if process is not None:
            _terminate_and_reap(process)
        raise
    finally:
        if stdout_thread is not None and stdout_thread.is_alive():
            stdout_thread.join(timeout=5.0)
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=5.0)
        if guard is not None:
            guard.close()


def run_zero_write_probe_v1(
    *,
    workspace_root: Path,
    python_executable: Path,
    sandbox_root: Path,
    audit_dir: Path,
    expected_ready: Mapping[str, object],
    environment: Mapping[str, str] | None = None,
    ready_timeout_seconds: float = 30.0,
) -> dict:
    """Run only the real child READY/ACK handshake; never touch research state."""

    ready = _validated_ready_payload(dict(expected_ready))
    if ready["schema_version"] != "research-launcher-ready/v1":
        raise LauncherError("launcher zero-write probe READY contract is invalid")
    receipt = run_supervised(
        workspace_root=workspace_root,
        python_executable=python_executable,
        sandbox_root=sandbox_root,
        audit_dir=audit_dir,
        child_args=[
            "-m",
            "app.research_launcher_probe",
            "--plan-sha256",
            ready["plan_sha256"],
            "--plan-file-sha256",
            ready["plan_file_sha256"],
            "--fixture-id",
            ready["fixture_id"],
            "--fixture-manifest-file-sha256",
            ready["fixture_manifest_file_sha256"],
        ],
        expected_ready=ready,
        ready_timeout_seconds=ready_timeout_seconds,
        environment=environment,
        allow_zero_write_probe=True,
    )
    return {
        "schema_version": "research-launcher-zero-write-probe-receipt/v1",
        "supervised": receipt,
        "zero_research_writes": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervised research process launcher")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--ready-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-plan-file-sha256", required=True)
    parser.add_argument("--expected-fixture-id", required=True)
    parser.add_argument("--expected-fixture-manifest-file-sha256", required=True)
    parser.add_argument("child_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    child_args = list(args.child_args)
    if child_args[:1] == ["--"]:
        child_args.pop(0)
    environment = build_e_only_environment(
        os.environ, sandbox_root=Path(args.sandbox_root)
    )
    receipt = run_supervised(
        workspace_root=Path(args.workspace_root),
        python_executable=Path(args.python_executable),
        sandbox_root=Path(args.sandbox_root),
        audit_dir=Path(args.audit_dir),
        child_args=child_args,
        expected_ready={
            "schema_version": "research-launcher-ready/v1",
            "event": "READY",
            "plan_sha256": args.expected_plan_sha256,
            "plan_file_sha256": args.expected_plan_file_sha256,
            "fixture_id": args.expected_fixture_id,
            "fixture_manifest_file_sha256": (
                args.expected_fixture_manifest_file_sha256
            ),
        },
        ready_timeout_seconds=args.ready_timeout_seconds,
        environment=environment,
    )
    print("LAUNCHER_EXIT " + _canonical_bytes(receipt).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
