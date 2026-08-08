from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Iterator, Mapping, Sequence


SCRIPT_PATH = Path(__file__).resolve()
WORKSPACE = Path(r"E:\AI workspace\quant-signal-lkj")
LAUNCHER_GIT_PATH = "scripts/run_shallow_gbdt_risk_on_breadth_development_1.py"
RELATIVE_OUTPUT_DIR = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_risk_on_breadth_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
EXPECTED_STRATEGY_SHA256 = (
    "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "2c8530853a5d906dae2e6fa9895aa0a620d93a870d3bfd9dabada30444013f22"
)
EXPECTED_RUN_SPEC_SHA256 = (
    "5152b35fd399e15f036205c330c5a5765624faf784b2030c87669d7b04a71068"
)
EXPECTED_PROGRESS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-replay-progress/v1"
)
EXPECTED_RESULT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-result/v1"
)
EXPECTED_RESULT_VERIFICATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "result-bundle-verification/v1"
)
PROGRESS_FILE_NAME = (
    ".ranked_liquidity_shallow_gbdt_risk_on_breadth_v1_progress.json"
)
HEX_ARTIFACT = re.compile(r"^[0-9a-f]{64}\.json$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

RUN_SPEC: dict[str, Any] = {
    "schema_version": (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-formal-run-spec/v1"
    ),
    "hypothesis_id": "fixed_shallow_gbdt_with_pit_market_breadth_risk_on_gate",
    "command": (
        "research-audited-pit-ranked-liquidity-"
        "shallow-gbdt-risk-on-breadth-rolling-oof"
    ),
    "inputs": {
        "audited_pit_universe_path": (
            "data/research_artifacts/audited_pit_universe_v2/"
            "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4/"
            "metadata.sqlite3"
        ),
        "expected_coverage_audit_sha256": (
            "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
        ),
        "expected_artifact_root_sha256": (
            "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
        ),
        "temporal_contract_path": "data/research_partitions/frozen-v2.json",
        "expected_temporal_contract_sha256": (
            "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
        ),
        "current_pool_development_audit_path": (
            "data/research_artifacts/current_pool_audits/"
            "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55.json"
        ),
        "expected_current_pool_development_audit_sha256": (
            "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55"
        ),
        "security_code_transition_evidence_root": (
            "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
        "observed_attestation": {
            "schema_version": "formal-frozen-input-attestation/v1",
            "algorithm": "sha256",
            "targets": [
                {
                    "kind": "file",
                    "path_field": "audited_pit_universe_path",
                },
                {
                    "kind": "file",
                    "path_field": "temporal_contract_path",
                },
                {
                    "kind": "file",
                    "path_field": "current_pool_development_audit_path",
                },
                {
                    "kind": "directory_tree",
                    "path_field": "security_code_transition_evidence_root",
                },
            ],
        },
    },
    "development_partition": {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    },
    "runtime_contract": {
        "environment_policy": "minimal-research-environment/v1",
        "inherit_parent_environment": False,
        "required_environment": {
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "VPS_RUNTIME_ROLE": "local_research",
        },
        "critical_distributions": [
            "numpy",
            "pandas",
            "scikit-learn",
            "xgboost",
        ],
    },
    "resource_contract": {
        "memory_policy": "unbounded",
        "enforcement": "none",
        "process_tree_completion": "windows_job_object_assigned_and_drained",
    },
    "attempt_contract": {
        "ledger_relative_path": (
            "data/research_attempts/"
            "risk_on_breadth_development_1.formal_attempt.json"
        ),
        "max_formal_attempts": 1,
        "ledger_outside_run_root": True,
    },
    "scope": {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    },
}

RUN_SPEC_SHA256 = EXPECTED_RUN_SPEC_SHA256


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_once(path: Path, payload: Mapping[str, Any]) -> None:
    raw = _canonical_bytes(dict(payload)) + b"\n"
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def git_bytes(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def normalized_source_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _assert_frozen_run_spec() -> None:
    if _sha256(RUN_SPEC) != EXPECTED_RUN_SPEC_SHA256:
        raise RuntimeError("formal risk-on breadth run spec drifted")
    if RUN_SPEC["resource_contract"] != {
        "memory_policy": "unbounded",
        "enforcement": "none",
        "process_tree_completion": "windows_job_object_assigned_and_drained",
    }:
        raise RuntimeError("formal risk-on breadth resource contract drifted")
    if RUN_SPEC["development_partition"] != {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }:
        raise RuntimeError("formal risk-on breadth partition drifted")
    if RUN_SPEC["scope"] != {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    }:
        raise RuntimeError("formal risk-on breadth scope drifted")
    if RUN_SPEC["attempt_contract"] != {
        "ledger_relative_path": (
            "data/research_attempts/"
            "risk_on_breadth_development_1.formal_attempt.json"
        ),
        "max_formal_attempts": 1,
        "ledger_outside_run_root": True,
    }:
        raise RuntimeError("formal risk-on breadth attempt contract drifted")
    audit_path = Path(RUN_SPEC["inputs"]["current_pool_development_audit_path"])
    if (
        audit_path.stem
        != RUN_SPEC["inputs"]["expected_current_pool_development_audit_sha256"]
    ):
        raise RuntimeError("formal risk-on breadth current-pool audit drifted")


def _safe_workspace_path(workspace: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise RuntimeError(f"{label} path is invalid")
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise RuntimeError(f"{label} path is invalid")
    root = workspace.resolve(strict=True)
    try:
        resolved = (root / relative_path).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{label} path is invalid") from exc
    return resolved


def _is_reparse(path: Path) -> bool:
    details = path.lstat()
    attributes = getattr(details, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _attested_file_paths(
    workspace: Path,
    inputs: Mapping[str, Any],
) -> list[Path]:
    policy = inputs.get("observed_attestation")
    expected_policy = RUN_SPEC["inputs"]["observed_attestation"]
    if policy != expected_policy:
        raise RuntimeError("formal frozen input attestation policy differs")
    root = workspace.resolve(strict=True)
    paths: list[Path] = []
    for target in policy["targets"]:
        path = _safe_workspace_path(
            root,
            inputs.get(target["path_field"]),
            target["path_field"],
        )
        if _is_reparse(path):
            raise RuntimeError("formal frozen input type differs")
        if target["kind"] == "file":
            if not path.is_file():
                raise RuntimeError("formal frozen input type differs")
            paths.append(path)
            continue
        if target["kind"] != "directory_tree" or not path.is_dir():
            raise RuntimeError("formal frozen input type differs")
        tree_files: list[Path] = []
        for child in path.rglob("*"):
            if _is_reparse(child):
                raise RuntimeError("formal frozen input type differs")
            if child.is_file():
                tree_files.append(child)
            elif not child.is_dir():
                raise RuntimeError("formal frozen input type differs")
        if not tree_files:
            raise RuntimeError("formal frozen input tree is empty")
        paths.extend(tree_files)
    unique = {path.resolve(strict=True): path for path in paths}
    if len(unique) != len(paths):
        raise RuntimeError("formal frozen input path is duplicated")
    return sorted(unique, key=lambda path: path.as_posix().casefold())


def frozen_input_attestation(
    workspace: Path,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    root = workspace.resolve(strict=True)
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _attested_file_paths(root, inputs)
    ]
    files.sort(key=lambda item: item["path"])
    body = {
        "schema_version": "formal-frozen-input-attestation/v1",
        "algorithm": "sha256",
        "files": files,
    }
    return {**body, "root_sha256": _sha256(body)}


def _minimal_child_environment(
    parent: Mapping[str, str],
    *,
    python_executable: Path,
    temp_dir: Path,
) -> dict[str, str]:
    system_root = parent.get("SystemRoot") or parent.get("SYSTEMROOT") or parent.get(
        "WINDIR"
    )
    if not system_root:
        raise RuntimeError("Windows system root is unavailable")
    resolved_python = python_executable.resolve()
    resolved_temp = temp_dir.resolve()
    required = dict(RUN_SPEC["runtime_contract"]["required_environment"])
    path_parts = [
        str(resolved_python.parent),
        str(Path(system_root) / "System32"),
        str(Path(system_root)),
    ]
    deduplicated = list(dict.fromkeys(part.casefold() for part in path_parts))
    normalized_paths = [
        next(part for part in path_parts if part.casefold() == key)
        for key in deduplicated
    ]
    return {
        **required,
        "SYSTEMROOT": str(Path(system_root)),
        "WINDIR": str(Path(system_root)),
        "COMSPEC": str(Path(system_root) / "System32" / "cmd.exe"),
        "PATH": os.pathsep.join(normalized_paths),
        "TEMP": str(resolved_temp),
        "TMP": str(resolved_temp),
    }


def risk_on_breadth_producer_binding(
    python_executable: Path,
    *,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    code = (
        "import json\n"
        "from app import audited_pit_continuous_ridge_oof as ridge\n"
        "from app import audited_pit_shallow_gbdt_risk_on_breadth as risk\n"
        "print(json.dumps({\n"
        "'strategy_sha256': risk._SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC_SHA256,\n"
        "'producer_binding': ridge._shallow_gbdt_risk_on_breadth_producer_binding(),\n"
        "}, ensure_ascii=False, sort_keys=True))\n"
    )
    completed = subprocess.run(
        [str(python_executable), "-c", code],
        cwd=WORKSPACE,
        env=dict(environment),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    binding = json.loads(completed.stdout)
    if (
        not isinstance(binding, dict)
        or binding.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or not isinstance(binding.get("producer_binding"), dict)
        or binding["producer_binding"].get("root_sha256")
        != EXPECTED_PRODUCER_ROOT_SHA256
    ):
        raise RuntimeError("formal risk-on breadth producer binding drifted")
    return binding


def _runtime_attestation(
    python_executable: Path,
    *,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    distributions = RUN_SPEC["runtime_contract"]["critical_distributions"]
    probe = r'''
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def distribution_attestation(name):
    distribution = metadata.distribution(name)
    entries = []
    for item in sorted(distribution.files or (), key=lambda value: str(value)):
        path = Path(distribution.locate_file(item)).resolve()
        if path.is_file():
            entries.append({
                "path": str(item).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": file_hash(path),
            })
    return {
        "name": name,
        "version": distribution.version,
        "file_count": len(entries),
        "files_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
    }

names = json.loads(sys.argv[1])
executable = Path(sys.executable).resolve()
base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
print(json.dumps({
    "schema_version": "formal-python-runtime-attestation/v1",
    "implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "cache_tag": sys.implementation.cache_tag,
    "platform": platform.platform(),
    "executable": str(executable),
    "executable_sha256": file_hash(executable),
    "base_executable": str(base_executable),
    "base_executable_sha256": file_hash(base_executable),
    "distributions": [distribution_attestation(name) for name in names],
}, sort_keys=True))
'''
    completed = subprocess.run(
        [str(python_executable), "-c", probe, json.dumps(distributions)],
        cwd=WORKSPACE,
        env=dict(environment),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    value = json.loads(completed.stdout)
    observed_distributions = value.get("distributions") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "formal-python-runtime-attestation/v1"
        or not HEX_SHA256.fullmatch(str(value.get("executable_sha256") or ""))
        or not HEX_SHA256.fullmatch(str(value.get("base_executable_sha256") or ""))
        or not isinstance(observed_distributions, list)
        or [item.get("name") for item in observed_distributions] != distributions
        or any(
            not isinstance(item, dict)
            or not HEX_SHA256.fullmatch(str(item.get("files_sha256") or ""))
            or not isinstance(item.get("file_count"), int)
            or item["file_count"] <= 0
            for item in observed_distributions
        )
    ):
        raise RuntimeError("formal risk-on breadth runtime attestation is invalid")
    environment_binding = {
        "schema_version": RUN_SPEC["runtime_contract"]["environment_policy"],
        "keys": sorted(environment),
        "environment_sha256": _sha256(dict(environment)),
        "inherit_parent_environment": False,
    }
    body = {**value, "environment": environment_binding}
    return {**body, "root_sha256": _sha256(body)}


def _reserve_single_attempt(
    *,
    ledger_path: Path,
    output_dir: Path,
    claim: Mapping[str, Any],
) -> None:
    resolved_ledger = ledger_path.resolve(strict=False)
    resolved_output = output_dir.resolve(strict=False)
    try:
        resolved_ledger.relative_to(resolved_output)
    except ValueError:
        pass
    else:
        raise ValueError("formal attempt ledger must be outside output root")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_json_once(ledger_path, claim)
    except FileExistsError as exc:
        raise FileExistsError("formal risk-on breadth attempt is already claimed") from exc


def _command_arguments() -> list[str]:
    inputs = RUN_SPEC["inputs"]
    partition = RUN_SPEC["development_partition"]
    return [
        "-m",
        "app.jobs",
        RUN_SPEC["command"],
        "--audited-pit-universe-path",
        inputs["audited_pit_universe_path"],
        "--expected-coverage-audit-sha256",
        inputs["expected_coverage_audit_sha256"],
        "--expected-artifact-root-sha256",
        inputs["expected_artifact_root_sha256"],
        "--temporal-contract-path",
        inputs["temporal_contract_path"],
        "--expected-temporal-contract-sha256",
        inputs["expected_temporal_contract_sha256"],
        "--security-code-transition-evidence-root",
        inputs["security_code_transition_evidence_root"],
        "--expected-security-code-transition-contract-sha256",
        inputs["expected_security_code_transition_contract_sha256"],
        "--start-date",
        partition["start_date"],
        "--end-date",
        partition["end_date"],
        "--output-dir",
        RELATIVE_OUTPUT_DIR.as_posix(),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _WindowsJob:
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _BASIC_ACCOUNTING_INFORMATION = 1
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("formal risk-on breadth launcher requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen[Any]) -> None:
        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self) -> int:
        accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(accounting.ActiveProcesses)

    def wait_drained(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self.active_processes() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def terminate(self) -> None:
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def create_gate_event(self) -> Any:
        handle = self._kernel32.CreateEventW(None, True, False, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        value = int(handle) if isinstance(handle, int) else int(handle.value)
        try:
            os.set_handle_inheritable(value, True)
        except BaseException:
            self._kernel32.CloseHandle(handle)
            raise
        return handle

    def release_gate(self, handle: Any) -> None:
        if not self._kernel32.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close_handle(self, handle: Any) -> None:
        if handle:
            self._kernel32.CloseHandle(handle)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> _WindowsJob:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def run_unbounded_command(
    *,
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    if not command or any(not isinstance(token, str) or not token for token in command):
        raise ValueError("unbounded research command is invalid")
    if stdout_path.exists() or stderr_path.exists() or stdout_path == stderr_path:
        raise ValueError("unbounded research output paths are invalid")
    started_at = utc_now()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    bootstrap = r'''
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
event = wintypes.HANDLE(int(sys.argv[1]))
if kernel32.WaitForSingleObject(event, 0xFFFFFFFF) != 0:
    raise SystemExit(125)
command = json.loads(sys.argv[2])
raise SystemExit(subprocess.call(command, close_fds=True))
'''
    process: subprocess.Popen[Any] | None = None
    assigned = False
    event_handle: Any = None
    job: _WindowsJob | None = None
    process_tree_drained = False
    exit_code: int | None = None
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        try:
            job = _WindowsJob()
            event_handle = job.create_gate_event()
            event_value = (
                int(event_handle)
                if isinstance(event_handle, int)
                else int(event_handle.value)
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.lpAttributeList = {"handle_list": [event_value]}
            process = subprocess.Popen(
                [
                    command[0],
                    "-c",
                    bootstrap,
                    str(event_value),
                    json.dumps(command, ensure_ascii=False),
                ],
                cwd=str(cwd),
                env=dict(environment),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=creationflags,
                close_fds=True,
                startupinfo=startupinfo,
            )
            job.assign(process)
            assigned = True
            job.release_gate(event_handle)
            job.close_handle(event_handle)
            event_handle = None
            exit_code = process.wait()
            process_tree_drained = job.wait_drained(60.0)
            if not process_tree_drained:
                job.terminate()
                job.wait_drained(10.0)
        except BaseException:
            if job is not None and assigned:
                try:
                    job.terminate()
                except BaseException:
                    pass
            elif process is not None and process.poll() is None:
                try:
                    process.kill()
                except BaseException:
                    pass
            if process is not None:
                try:
                    process.wait(timeout=10)
                except BaseException:
                    pass
            if job is not None and assigned:
                try:
                    job.wait_drained(10.0)
                except BaseException:
                    pass
            raise
        finally:
            if job is not None:
                job.close_handle(event_handle)
                job.close()
    if process is None or exit_code is None:
        raise RuntimeError("unbounded research process was not created")
    return {
        "schema_version": "research-unbounded-job-object-command-receipt/v1",
        "pid": process.pid,
        "pid_role": "gated_bootstrap_supervisor",
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "exit_code": exit_code,
        "command_sha256": _sha256({"tokens": command}),
        "argument_count": len(command),
        "cwd": str(cwd),
        "shell": False,
        "stdin_closed": True,
        "memory_limit_enforced": False,
        "child_reaped": process.poll() is not None,
        "job_object_assigned": True,
        "process_tree_drained": process_tree_drained,
        "process_tree_drain_verification": (
            "windows_job_object_active_process_count_zero"
        ),
        "stdout": {
            "path": str(stdout_path),
            "bytes": stdout_path.stat().st_size,
            "sha256": sha256_file(stdout_path),
        },
        "stderr": {
            "path": str(stderr_path),
            "bytes": stderr_path.stat().st_size,
            "sha256": sha256_file(stderr_path),
        },
    }


@contextmanager
def _held_frozen_inputs(
    workspace: Path,
    inputs: Mapping[str, Any],
) -> Iterator[None]:
    if os.name != "nt":
        raise RuntimeError("formal risk-on breadth launcher requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[Any] = []
    try:
        for path in _attested_file_paths(workspace, inputs):
            handle = kernel32.CreateFileW(
                str(path),
                0x80000000,
                0x00000001,
                None,
                3,
                0x00000080,
                None,
            )
            if handle == invalid_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def _safe_progress(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        "schema_version": value.get("schema_version"),
        "stage": value.get("stage"),
        "artifact_sha256": value.get("artifact_sha256"),
    }


def _content_addressed_document(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or not HEX_ARTIFACT.fullmatch(path.name):
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    unsigned = dict(document)
    embedded = unsigned.pop("artifact_sha256", None)
    canonical = _sha256(unsigned)
    if embedded != canonical or path.stem != canonical:
        return None
    return {
        "path": path.name,
        "file_sha256": sha256_file(path),
        "canonical_artifact_sha256": canonical,
        "embedded_artifact_sha256": embedded,
        "document": document,
    }


def _result_artifact(output_dir: Path) -> tuple[dict[str, Any] | None, bool]:
    artifacts = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and HEX_ARTIFACT.fullmatch(path.name)
    )
    if len(artifacts) != 1:
        return None, False
    artifact = _content_addressed_document(artifacts[0])
    if artifact is None:
        return None, False
    document = artifact.pop("document")
    scope = document.get("scope") if isinstance(document, dict) else None
    strategy = document.get("strategy") if isinstance(document, dict) else None
    producer = document.get("producer_code") if isinstance(document, dict) else None
    if (
        document.get("schema_version") != EXPECTED_RESULT_SCHEMA
        or not isinstance(strategy, dict)
        or strategy.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or not isinstance(producer, dict)
        or producer.get("root_sha256") != EXPECTED_PRODUCER_ROOT_SHA256
        or not isinstance(scope, dict)
        or scope.get("development_only") is not True
        or scope.get("embargo_consumed") is not False
        or scope.get("final_oos_consumed") is not False
    ):
        return None, False
    return artifact, True


def _runtime_verification(output_dir: Path) -> tuple[dict[str, Any] | None, bool]:
    directory = output_dir / "verifications"
    artifacts = (
        sorted(path for path in directory.iterdir() if path.is_file())
        if directory.is_dir()
        else []
    )
    if len(artifacts) != 1:
        return None, False
    artifact = _content_addressed_document(artifacts[0])
    if artifact is None:
        return None, False
    document = artifact.pop("document")
    expected_checks = {
        "independent_rolling_oof_replay": True,
        "content_addressing_verified": True,
        "probability_score_contract_verified": True,
        "shared_positive_candidate_pool_verified": True,
        "strict_outcome_membership_verified": True,
        "independent_selection_replay": True,
        "independent_sweep_and_gate_replay": True,
        "market_breadth_filter_replayed": True,
    }
    receipt_unsigned = dict(document)
    receipt_unsigned.pop("artifact_sha256", None)
    receipt_sha256 = receipt_unsigned.pop("receipt_sha256", None)
    sidecars = document.get("sidecar_artifact_sha256")
    if (
        document.get("schema_version") != EXPECTED_RESULT_VERIFICATION_SCHEMA
        or document.get("verified") is not True
        or document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or document.get("producer_root_sha256") != EXPECTED_PRODUCER_ROOT_SHA256
        or document.get("checks") != expected_checks
        or receipt_sha256 != _sha256(receipt_unsigned)
        or not HEX_SHA256.fullmatch(
            str(document.get("main_artifact_sha256") or "")
        )
        or not HEX_SHA256.fullmatch(
            str(document.get("market_breadth_feature_binding_receipt_sha256") or "")
        )
        or not isinstance(sidecars, dict)
        or set(sidecars) != {"features", "models", "execution", "selection"}
        or any(not HEX_SHA256.fullmatch(str(value or "")) for value in sidecars.values())
    ):
        return None, False
    return {
        **artifact,
        "schema_version": document["schema_version"],
        "main_artifact_sha256": document["main_artifact_sha256"],
        "market_breadth_feature_binding_receipt_sha256": document[
            "market_breadth_feature_binding_receipt_sha256"
        ],
    }, True


def _validated_expected_commit(value: str) -> str:
    expected = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("expected commit must be a full lowercase SHA-1")
    return expected


def _preflight(
    *,
    expected_commit: str,
    python_executable: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    _assert_frozen_run_spec()
    current_commit = git_output("rev-parse", "HEAD").lower()
    if current_commit != expected_commit:
        raise RuntimeError("formal risk-on breadth commit drifted")
    if git_output("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("formal risk-on breadth worktree is not clean")
    launcher_blob = git_bytes("show", f"{expected_commit}:{LAUNCHER_GIT_PATH}")
    if launcher_blob != normalized_source_bytes(SCRIPT_PATH):
        raise RuntimeError("formal risk-on breadth launcher does not match commit")
    binding = risk_on_breadth_producer_binding(
        python_executable,
        environment=environment,
    )
    return {
        "git_commit": current_commit,
        "strategy_sha256": binding["strategy_sha256"],
        "producer_binding": binding["producer_binding"],
        "formal_launcher_sha256": sha256_file(SCRIPT_PATH),
        "formal_launcher_git_blob_sha256": hashlib.sha256(launcher_blob).hexdigest(),
        "run_spec_sha256": RUN_SPEC_SHA256,
        "frozen_input_attestation": frozen_input_attestation(
            WORKSPACE,
            RUN_SPEC["inputs"],
        ),
        "runtime_attestation": _runtime_attestation(
            python_executable,
            environment=environment,
        ),
    }


def _completion_payload(
    *,
    started_at: str,
    preflight: Mapping[str, Any],
    postflight: Mapping[str, Any] | None,
    launch_path: Path,
    attempt_path: Path,
    resource_receipt_path: Path,
    resource_receipt: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None,
    result_artifact: Mapping[str, Any] | None,
    artifact_content_addressed: bool,
    runtime_verification: Mapping[str, Any] | None,
    runtime_verification_content_addressed: bool,
    launcher_error_type: str | None,
) -> dict[str, Any]:
    immutable_inputs_unchanged = postflight == preflight
    command_sha256 = _sha256(
        {"tokens": [str((WORKSPACE / ".venv/Scripts/python.exe").resolve()), *_command_arguments()]}
    )
    resource_ok = bool(
        resource_receipt
        and resource_receipt.get("schema_version")
        == "research-unbounded-job-object-command-receipt/v1"
        and resource_receipt.get("exit_code") == 0
        and resource_receipt.get("child_reaped") is True
        and resource_receipt.get("job_object_assigned") is True
        and resource_receipt.get("process_tree_drained") is True
        and resource_receipt.get("memory_limit_enforced") is False
        and resource_receipt.get("command_sha256") == command_sha256
    )
    result_available = bool(
        resource_ok
        and progress
        and progress.get("schema_version") == EXPECTED_PROGRESS_SCHEMA
        and progress.get("stage") == "completed"
        and artifact_content_addressed
        and runtime_verification_content_addressed
        and progress.get("artifact_sha256")
        == (result_artifact or {}).get("canonical_artifact_sha256")
        and (runtime_verification or {}).get("main_artifact_sha256")
        == (result_artifact or {}).get("canonical_artifact_sha256")
        and immutable_inputs_unchanged
    )
    classification = (
        "completed_result_pending_independent_verification"
        if result_available
        else "formal_run_without_valid_completed_result"
    )
    return {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-completion/v1"
        ),
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "classification": classification,
        "preflight": dict(preflight),
        "post_run_preflight": dict(postflight) if postflight is not None else None,
        "immutable_inputs_unchanged": immutable_inputs_unchanged,
        "launch_receipt_sha256": sha256_file(launch_path),
        "attempt_claim_sha256": sha256_file(attempt_path),
        "resource_receipt": (
            {
                "path": resource_receipt_path.name,
                "sha256": sha256_file(resource_receipt_path),
                "exit_code": resource_receipt["exit_code"],
                "memory_limit_enforced": resource_receipt["memory_limit_enforced"],
                "job_object_assigned": resource_receipt["job_object_assigned"],
                "process_tree_drained": resource_receipt["process_tree_drained"],
            }
            if resource_receipt is not None
            else None
        ),
        "launcher_error_type": launcher_error_type,
        "progress": dict(progress) if progress is not None else None,
        "result_artifact": dict(result_artifact) if result_artifact is not None else None,
        "runtime_verification": (
            dict(runtime_verification) if runtime_verification is not None else None
        ),
        "result_available": result_available,
        "artifact_content_addressed": artifact_content_addressed,
        "runtime_verification_content_addressed": (
            runtime_verification_content_addressed
        ),
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    expected_commit = _validated_expected_commit(args.expected_commit)
    if __package__ != "scripts" or SCRIPT_PATH.parents[1] != WORKSPACE.resolve():
        raise RuntimeError("formal launcher must run as the committed scripts module")
    if os.name != "nt":
        raise RuntimeError("formal risk-on breadth launcher requires Windows")

    os.chdir(WORKSPACE)
    output_dir = WORKSPACE / RELATIVE_OUTPUT_DIR
    attempt_path = WORKSPACE / RUN_SPEC["attempt_contract"]["ledger_relative_path"]
    if output_dir.exists():
        raise FileExistsError("formal risk-on breadth output directory already exists")
    if attempt_path.exists():
        raise FileExistsError("formal risk-on breadth attempt is already claimed")
    python_executable = (WORKSPACE / ".venv/Scripts/python.exe").resolve()
    runtime_temp_dir = output_dir / "runtime_tmp"
    environment = _minimal_child_environment(
        os.environ,
        python_executable=python_executable,
        temp_dir=runtime_temp_dir,
    )
    preliminary = _preflight(
        expected_commit=expected_commit,
        python_executable=python_executable,
        environment=environment,
    )
    if args.dry_run:
        print("status=preflight_verified")
        return 0

    started_at = utc_now()
    claim = {
        "schema_version": "formal-single-attempt-claim/v1",
        "started_at_utc": started_at,
        "run_spec_sha256": RUN_SPEC_SHA256,
        "expected_commit": expected_commit,
        "output_dir": RELATIVE_OUTPUT_DIR.as_posix(),
        "preflight_sha256": _sha256(preliminary),
        "max_formal_attempts": 1,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    _reserve_single_attempt(
        ledger_path=attempt_path,
        output_dir=output_dir,
        claim=claim,
    )
    output_dir.mkdir(parents=False)
    runtime_temp_dir.mkdir()

    progress_path = output_dir / PROGRESS_FILE_NAME
    stdout_path = output_dir / "formal_run.stdout.log"
    stderr_path = output_dir / "formal_run.stderr.log"
    resource_receipt_path = output_dir / "formal_run.resource_receipt.json"
    launch_path = output_dir / "formal_run.launch.json"
    completion_path = output_dir / "formal_run.completion.json"
    failure_path = output_dir / "formal_run.failure.json"
    preflight_path = output_dir / "formal_run.preflight.json"
    command = [str(python_executable), *_command_arguments()]
    command_sha256 = _sha256({"tokens": command})
    resource_receipt: Mapping[str, Any] | None = None
    postflight: Mapping[str, Any] | None = None
    launcher_error_type: str | None = None

    try:
        with _held_frozen_inputs(WORKSPACE, RUN_SPEC["inputs"]):
            preflight = _preflight(
                expected_commit=expected_commit,
                python_executable=python_executable,
                environment=environment,
            )
            if preflight != preliminary:
                raise RuntimeError("formal risk-on breadth preflight changed before launch")
            _write_json_once(
                preflight_path,
                {
                    "schema_version": (
                        "ranked-liquidity-shallow-gbdt-risk-on-breadth-preflight/v1"
                    ),
                    "observed_at_utc": started_at,
                    **preflight,
                    "run_spec": RUN_SPEC,
                    "attempt_claim_sha256": sha256_file(attempt_path),
                    "statistical_result_available": False,
                },
            )
            _write_json_once(
                launch_path,
                {
                    "schema_version": (
                        "ranked-liquidity-shallow-gbdt-risk-on-breadth-launch/v1"
                    ),
                    "started_at_utc": started_at,
                    "preflight_sha256": _sha256(preflight),
                    "executable": str(python_executable),
                    "arguments": _command_arguments(),
                    "command_sha256": command_sha256,
                    "runtime_environment_attestation_sha256": preflight[
                        "runtime_attestation"
                    ]["root_sha256"],
                    "memory_policy": "unbounded",
                    "process_tree_policy": (
                        "windows_job_object_assigned_and_drained"
                    ),
                    "development_only": True,
                    "embargo_consumed": False,
                    "final_oos_consumed": False,
                    "production_authority": False,
                    "automatic_trading_authority": False,
                },
            )
            try:
                resource_receipt = run_unbounded_command(
                    command=command,
                    cwd=WORKSPACE,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    environment=environment,
                )
                _write_json_once(resource_receipt_path, resource_receipt)
            except BaseException as exc:
                launcher_error_type = type(exc).__name__
            try:
                postflight = _preflight(
                    expected_commit=expected_commit,
                    python_executable=python_executable,
                    environment=environment,
                )
            except BaseException as exc:
                launcher_error_type = launcher_error_type or type(exc).__name__
                postflight = None
    except BaseException as exc:
        launcher_error_type = launcher_error_type or type(exc).__name__
        preflight = preliminary

    progress = _safe_progress(progress_path)
    result_artifact, artifact_content_addressed = _result_artifact(output_dir)
    runtime_verification, runtime_verification_content_addressed = (
        _runtime_verification(output_dir)
    )
    if not launch_path.exists():
        _write_json_once(
            launch_path,
            {
                "schema_version": (
                    "ranked-liquidity-shallow-gbdt-risk-on-breadth-launch/v1"
                ),
                "started_at_utc": started_at,
                "launch_started": False,
                "launcher_error_type": launcher_error_type,
                "development_only": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "production_authority": False,
                "automatic_trading_authority": False,
            },
        )
    completion = _completion_payload(
        started_at=started_at,
        preflight=preflight,
        postflight=postflight,
        launch_path=launch_path,
        attempt_path=attempt_path,
        resource_receipt_path=resource_receipt_path,
        resource_receipt=resource_receipt,
        progress=progress,
        result_artifact=result_artifact,
        artifact_content_addressed=artifact_content_addressed,
        runtime_verification=runtime_verification,
        runtime_verification_content_addressed=(
            runtime_verification_content_addressed
        ),
        launcher_error_type=launcher_error_type,
    )
    _write_json_once(completion_path, completion)
    if completion["result_available"] is not True:
        _write_json_once(
            failure_path,
            {
                "schema_version": (
                    "ranked-liquidity-shallow-gbdt-risk-on-breadth-failure/v1"
                ),
                "observed_at_utc": utc_now(),
                "classification": completion["classification"],
                "launcher_error_type": launcher_error_type,
                "resource_receipt_available": resource_receipt is not None,
                "statistical_result_available": False,
                "usable_for_hyperparameter_selection": False,
                "profile_registration_authority": False,
                "production_recommendation_authority": False,
                "automatic_trading_authority": False,
                "development_only": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "production_authority": False,
            },
        )
        return 1
    return 0


def cli(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except Exception:
        print("status=failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
