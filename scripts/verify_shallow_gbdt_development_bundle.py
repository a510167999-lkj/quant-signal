"""Publish a canonical development-only receipt after an independent GBDT replay.

The replay itself lives beside a frozen development run.  This entrypoint only
publishes a receipt when that replay returns a verified bundle; it never accepts
caller-supplied artifact, metric, or receipt identities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCRIPT_PATH = Path(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_PATH.parents[1]
RUNS_ROOT = PROJECT_ROOT / "data" / "research_runs"
RUN_NAME_PREFIX = (
    "audited_pit_ranked_liquidity_shallow_gbdt_rolling126_oof_v1_"
)
REGISTERED_RUN_ROOT_NAME = (
    RUN_NAME_PREFIX
    + "development_11_unbounded_verifier_flag_fix_local_research"
)
REGISTERED_COMPLETION_SHA256 = (
    "d3ea19cfc84fd782beef68b312c74b49062e2cb95b4a5462d40586f934848cca"
)
REGISTERED_REPLAY_SHA256 = (
    "e7fbdaa70f23a99134c3d3ac0f985ba5c7245db1f17ac77eb6f5d986c23ec767"
)
REGISTERED_HELPER_SHA256 = (
    "119dd7227b6e9871b80635e61ca7ceda973eca173a49feb1ca6e37c5193ca07d"
)
REGISTERED_ARTIFACT_SHA256 = (
    "59fd9f5353609f5b0181e74fd31080415f6d31db225acd699ae30c6af658bfd3"
)
REGISTERED_STRATEGY_SHA256 = (
    "53d00badc8683670ef3d6c02307697e2c3ef8ec769b9d8da072d36420cea70ac"
)
REGISTERED_PRODUCER_ROOT_SHA256 = (
    "a0ecd25793bdc57856facf9d9b2be957383689611989627eea52598556a00b9f"
)
REGISTERED_SOURCE_COMMIT = "2203204b3882993d8774ebfc4f973e735a2b613b"
REGISTERED_PYTHON_SHA256 = (
    "5fec912cd3c47c125754cfbcb9b21ce0b415f860cfa8e2a3b98ceb9cd73bd30f"
)
COMPLETION_NAME = "formal_run.completion.json"
REPLAY_NAME = "independent_verify_replay.py"
HELPER_NAME = "independent_verify_entry_fixed.py"
PREREGISTRATION_NAME = "formal_run.independent_verification.preregistration.json"
STATUS_NAME = "formal_run.independent_verification.status.json"
CLAIM_NAME = "formal_run.independent_verification.claim"
RECEIPT_ROOT_NAME = "independent_verification_receipts"
SCHEMA_VERSION = (
    "ranked-liquidity-shallow-gbdt-independent-verification-receipt/v1"
)
STATUS_SCHEMA_VERSION = (
    "ranked-liquidity-shallow-gbdt-independent-verification-status/v1"
)
VERIFICATION_SCHEMA_VERSION = (
    "ranked-liquidity-shallow-gbdt-result-bundle-verification/v1"
)
REQUIRED_CHECKS = frozenset(
    {
        "independent_rolling_oof_replay",
        "content_addressing_verified",
        "probability_score_contract_verified",
        "shared_positive_candidate_pool_verified",
        "strict_outcome_membership_verified",
        "independent_selection_replay",
        "independent_sweep_and_gate_replay",
    }
)
HEX_SHA256_LENGTH = 64
REPARSE_POINT_ATTRIBUTE = 0x400
PYTHON_EXECUTABLE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
MAX_REPLAY_STDOUT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_AUDIT_FILE_BYTES = 1024 * 1024
GIT_EXECUTABLE = Path(r"C:\Program Files\Git\cmd\git.exe")
WINDOWS_ROOT = Path(r"C:\Windows")
CAPTURE_REPLAY_DRIVER = r'''
import json
import runpy
import sys

sys.path.insert(0, sys.argv[3])
sys.path.insert(0, sys.argv[2])
from app import audited_pit_continuous_ridge_oof as ridge

captured = {}
original = ridge.verify_shallow_gbdt_result_bundle

def capture(*args, **kwargs):
    result = original(*args, **kwargs)
    captured["verification"] = result
    return result

ridge.verify_shallow_gbdt_result_bundle = capture
try:
    runpy.run_path(sys.argv[1], run_name="independent_replay_entry")
except SystemExit as exc:
    exit_code = int(exc.code or 0)
else:
    exit_code = 0
verification = captured.get("verification")
if exit_code != 0 or not isinstance(verification, dict):
    raise SystemExit(1)
print("INDEPENDENT_FULL_RESULT " + json.dumps(
    verification,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
))
'''
PRODUCER_PROBE_DRIVER = r'''
import json
import sys

sys.path.insert(0, sys.argv[1])
from app import audited_pit_shallow_gbdt as shallow
from app.audited_pit_continuous_ridge_oof import resolve_ranked_liquidity_run_variant

producer = resolve_ranked_liquidity_run_variant(
    shallow.SHALLOW_GBDT_OOF_SPEC
)["producer_binding"]()
print(json.dumps({"root_sha256": producer["root_sha256"]}, sort_keys=True))
'''


class IndependentVerificationError(RuntimeError):
    """Raised when a run cannot be independently verified or published."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_bounded(path: Path, label: str, *, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
        if size > maximum:
            raise IndependentVerificationError(f"{label} exceeds size bound")
        return path.read_bytes()
    except IndependentVerificationError:
        raise
    except OSError as exc:
        raise IndependentVerificationError(f"{label} is unreadable") from exc


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = _read_bounded(path, label, maximum=MAX_JSON_BYTES)
        value = json.loads(raw.decode("utf-8"))
    except IndependentVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise IndependentVerificationError(f"{label} is not an object")
    return value


def _fsync_directory(directory: Path) -> None:
    """Persist a directory entry without importing application source code."""

    if os.name != "nt":
        descriptor = os.open(
            str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    import ctypes
    from ctypes import wintypes

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
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = (wintypes.HANDLE,)
    flush_file_buffers.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        str(directory),
        0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "cannot open directory")
    try:
        if not flush_file_buffers(handle):
            raise OSError(ctypes.get_last_error(), "cannot flush directory")
    finally:
        close_handle(handle)


def _fsync_directory_chain(directory: Path, root: Path) -> None:
    current = directory
    while True:
        _fsync_directory(current)
        if current == root:
            return
        if current.parent == current:
            raise IndependentVerificationError("output durability root is invalid")
        current = current.parent


def _require_sha256(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) != HEX_SHA256_LENGTH or any(
        char not in "0123456789abcdef" for char in text
    ):
        raise IndependentVerificationError(f"{label} is not a SHA-256")
    return text


def _require_false(payload: Mapping[str, Any], field: str) -> None:
    if payload.get(field) is not False:
        raise IndependentVerificationError(f"{field} is not false")


def _require_sha256_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise IndependentVerificationError(f"{label} is invalid")
    normalized: dict[str, str] = {}
    for key, digest in value.items():
        if not isinstance(key, str) or not key:
            raise IndependentVerificationError(f"{label} key is invalid")
        normalized[key] = _require_sha256(digest, f"{label} digest")
    return dict(sorted(normalized.items()))


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = int(getattr(os.lstat(path), "st_file_attributes", 0))
    except OSError as exc:
        raise IndependentVerificationError("path metadata is unreadable") from exc
    return bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _assert_no_reparse(path: Path, label: str) -> None:
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise IndependentVerificationError(f"{label} uses a reparse point")
    try:
        raw_attributes = int(
            getattr(os.lstat(raw_path), "st_file_attributes", 0)
        )
    except FileNotFoundError:
        raw_attributes = 0
    except OSError as exc:
        raise IndependentVerificationError("path metadata is unreadable") from exc
    if raw_attributes & REPARSE_POINT_ATTRIBUTE:
        raise IndependentVerificationError(f"{label} uses a reparse point")
    absolute = Path(os.path.abspath(raw_path))
    candidates = [absolute, *absolute.parents]
    for candidate in candidates:
        if candidate == Path(candidate.anchor):
            break
        if candidate.exists() and _is_reparse_or_symlink(candidate):
            raise IndependentVerificationError(f"{label} uses a reparse point")


def _ensure_under(path: Path, root: Path, label: str) -> Path:
    _assert_no_reparse(path, label)
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise IndependentVerificationError(f"{label} escapes run root") from exc
    _assert_no_reparse(resolved, label)
    return resolved


def _assert_registered_run_root(run_root: Path) -> Path:
    raw_root = Path(run_root)
    _assert_no_reparse(raw_root, "run root")
    root = raw_root.resolve()
    expected = RUNS_ROOT / REGISTERED_RUN_ROOT_NAME
    _assert_no_reparse(expected, "registered run root")
    if root != expected.resolve() or root.parent != RUNS_ROOT.resolve():
        raise IndependentVerificationError("run root is outside research runs")
    _assert_no_reparse(root, "run root")
    return root


def _run_root(value: str) -> Path:
    raw_root = Path(value).expanduser()
    root = _assert_registered_run_root(raw_root)
    if not root.name.startswith(RUN_NAME_PREFIX):
        raise IndependentVerificationError("run root name is not a development run")
    if "_development_" not in root.name:
        raise IndependentVerificationError("run root is not a development partition")
    if "embargo" in root.name or "final_oos" in root.name:
        raise IndependentVerificationError("OOS run root is not allowed")
    return root


def _source_root(value: str) -> Path:
    root = Path(value).expanduser()
    _assert_no_reparse(root, "frozen source root")
    root = root.resolve()
    if not (root / ".git").exists() or not (root / "app").is_dir():
        raise IndependentVerificationError("frozen source root is invalid")
    _assert_no_reparse(root / "app", "frozen source app")
    if not _git_clean(root):
        raise IndependentVerificationError("frozen source worktree is not clean")
    if _git_head(root) != REGISTERED_SOURCE_COMMIT:
        raise IndependentVerificationError("frozen source commit differs")
    return root


def _git_head(root: Path = PROJECT_ROOT) -> str:
    _assert_no_reparse(GIT_EXECUTABLE, "Git executable")
    if not GIT_EXECUTABLE.is_file():
        raise IndependentVerificationError("trusted Git executable is missing")
    completed = subprocess.run(
        [
            str(GIT_EXECUTABLE),
            "-C",
            str(root),
            "--no-optional-locks",
            "rev-parse",
            "HEAD",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_safe_git_environment(),
    )
    return _require_git_commit(completed.stdout.strip(), "current Git commit")


def _git_clean(root: Path = PROJECT_ROOT) -> bool:
    _assert_no_reparse(GIT_EXECUTABLE, "Git executable")
    if not GIT_EXECUTABLE.is_file():
        raise IndependentVerificationError("trusted Git executable is missing")
    completed = subprocess.run(
        [
            str(GIT_EXECUTABLE),
            "-C",
            str(root),
            "--no-optional-locks",
            "-c",
            "status.showUntrackedFiles=all",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--no-renames",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_safe_git_environment(),
    )
    return not completed.stdout.strip()


def _safe_git_environment() -> dict[str, str]:
    system32 = WINDOWS_ROOT / "System32"
    return {
        "COMSPEC": str(system32 / "cmd.exe"),
        "PATH": os.pathsep.join(
            (str(GIT_EXECUTABLE.parent), str(system32), str(WINDOWS_ROOT))
        ),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SystemRoot": str(WINDOWS_ROOT),
        "TEMP": str(WINDOWS_ROOT / "Temp"),
        "TMP": str(WINDOWS_ROOT / "Temp"),
        "WINDIR": str(WINDOWS_ROOT),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "NUL",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "NUL",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _require_git_commit(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise IndependentVerificationError(f"{label} is invalid")
    return text


def _isolated_environment() -> dict[str, str]:
    system32 = WINDOWS_ROOT / "System32"
    return {
        "COMSPEC": str(system32 / "cmd.exe"),
        "PATH": os.pathsep.join(
            (str(PYTHON_EXECUTABLE.parent), str(system32), str(WINDOWS_ROOT))
        ),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PYTHONNOUSERSITE": "1",
        "SystemRoot": str(WINDOWS_ROOT),
        "TEMP": str(WINDOWS_ROOT / "Temp"),
        "TMP": str(WINDOWS_ROOT / "Temp"),
        "VPS_RUNTIME_ROLE": "local_research",
        "WINDIR": str(WINDOWS_ROOT),
    }


def _current_producer_root_sha256(source_root: Path) -> str:
    if not PYTHON_EXECUTABLE.is_file():
        raise IndependentVerificationError("project virtualenv is missing")
    completed = subprocess.run(
        [
            str(PYTHON_EXECUTABLE),
            "-I",
            "-B",
            "-c",
            PRODUCER_PROBE_DRIVER,
            str(source_root),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_isolated_environment(),
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_AUDIT_FILE_BYTES:
        raise IndependentVerificationError("frozen producer probe failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError("frozen producer probe is invalid") from exc
    if not isinstance(value, dict):
        raise IndependentVerificationError("frozen producer probe is invalid")
    return _require_sha256(value.get("root_sha256"), "current producer root")


def _python_executable_sha256() -> str:
    _assert_no_reparse(PYTHON_EXECUTABLE, "project virtualenv executable")
    if not PYTHON_EXECUTABLE.is_file():
        raise IndependentVerificationError("project virtualenv is missing")
    return _sha256_file(PYTHON_EXECUTABLE)


def _load_inputs(run_root: Path, source_root: Path) -> dict[str, Any]:
    run_root = _assert_registered_run_root(run_root)
    source_root = _source_root(str(source_root))
    _assert_no_reparse(SCRIPT_PATH, "verifier entrypoint")
    if not _git_clean():
        raise IndependentVerificationError("tracked worktree is not clean")
    entrypoint_git_commit = _git_head()
    completion_path = run_root / COMPLETION_NAME
    replay_path = run_root / REPLAY_NAME
    helper_path = run_root / HELPER_NAME
    preregistration_path = run_root / PREREGISTRATION_NAME
    if not all(
        path.is_file()
        for path in (
            completion_path,
            replay_path,
            helper_path,
            preregistration_path,
        )
    ):
        raise IndependentVerificationError("development run inputs are incomplete")
    for path, label in (
        (completion_path, "completion"),
        (replay_path, "replay"),
        (helper_path, "replay helper"),
        (preregistration_path, "preregistration"),
    ):
        _assert_no_reparse(path, label)
    completion = _read_object(completion_path, "completion")
    if completion.get("result_available") is not True:
        raise IndependentVerificationError("development result is unavailable")
    if completion.get("schema_version") != (
        "ranked-liquidity-shallow-gbdt-unbounded-completion/v1"
    ):
        raise IndependentVerificationError("completion schema drifted")
    if completion.get("classification") != (
        "completed_result_pending_independent_verification"
    ):
        raise IndependentVerificationError("completion classification is not pending")
    if completion.get("statistical_interpretation_allowed") is not False:
        raise IndependentVerificationError("statistical interpretation is already open")
    if completion.get("launcher_exception") is not None:
        raise IndependentVerificationError("launcher exception evidence is present")
    for field in (
        "artifact_content_addressed",
        "immutable_inputs_unchanged",
        "post_run_tracked_worktree_clean",
    ):
        if completion.get(field) is not True:
            raise IndependentVerificationError(f"{field} is not true")
    progress = completion.get("progress")
    artifact = completion.get("result_artifact")
    if not isinstance(progress, dict) or not isinstance(artifact, dict):
        raise IndependentVerificationError("completion nested objects are invalid")
    if progress.get("stage") != "completed":
        raise IndependentVerificationError("completion progress is not complete")
    if artifact.get("progress_artifact_sha256_matches") is not True:
        raise IndependentVerificationError("progress artifact binding is invalid")
    for field in ("embargo_consumed", "final_oos_consumed", "production_authority"):
        _require_false(completion, field)
    if completion.get("independent_verification_complete") is True:
        raise IndependentVerificationError("independent verification is already terminal")
    artifact_path = _ensure_under(
        run_root / str(artifact.get("path") or ""), run_root, "result artifact"
    )
    if not artifact_path.is_file():
        raise IndependentVerificationError("result artifact is missing")
    artifact_sha = _require_sha256(artifact.get("canonical_artifact_sha256"), "artifact")
    if (
        run_root.name == REGISTERED_RUN_ROOT_NAME
        and artifact_sha != REGISTERED_ARTIFACT_SHA256
    ):
        raise IndependentVerificationError("registered artifact identity differs")
    if artifact.get("file_name_matches_content_sha256") is not True:
        raise IndependentVerificationError("result artifact is not content addressed")
    if artifact_path.name != f"{artifact_sha}.json":
        raise IndependentVerificationError("result artifact name is not bound")
    if artifact.get("file_sha256") != _sha256_file(artifact_path):
        raise IndependentVerificationError("result artifact bytes changed")
    if progress.get("artifact_sha256") != artifact_sha:
        raise IndependentVerificationError("progress artifact identity differs")
    if completion.get("producer_root_sha256") != completion.get(
        "post_run_producer_root_sha256"
    ):
        raise IndependentVerificationError("producer binding changed after the run")
    if completion.get("strategy_sha256") != completion.get(
        "post_run_strategy_sha256"
    ):
        raise IndependentVerificationError("strategy binding changed after the run")
    if completion.get("git_commit") != completion.get("post_run_git_commit"):
        raise IndependentVerificationError("source commit changed after the run")
    if completion.get("post_run_git_commit") != REGISTERED_SOURCE_COMMIT:
        raise IndependentVerificationError("registered source commit differs")
    for field in (
        "formal_launcher_sha256",
        "formal_launcher_git_blob_sha256",
    ):
        if completion.get(field) != completion.get(f"post_run_{field}"):
            raise IndependentVerificationError(f"{field} changed after the run")
    registered_pairs = (
        ("completion", _sha256_file(completion_path), REGISTERED_COMPLETION_SHA256),
        ("replay", _sha256_file(replay_path), REGISTERED_REPLAY_SHA256),
        ("helper", _sha256_file(helper_path), REGISTERED_HELPER_SHA256),
    )
    for label, observed, expected in registered_pairs:
        if observed != expected:
            raise IndependentVerificationError(f"registered {label} identity differs")
    strategy_sha = _require_sha256(completion.get("strategy_sha256"), "strategy")
    if strategy_sha != REGISTERED_STRATEGY_SHA256:
        raise IndependentVerificationError("registered strategy identity differs")
    producer_sha = _require_sha256(
        completion.get("producer_root_sha256"), "producer root"
    )
    if producer_sha != REGISTERED_PRODUCER_ROOT_SHA256:
        raise IndependentVerificationError("registered producer identity differs")
    helper_sha = _sha256_file(helper_path)
    preregistration = _read_object(preregistration_path, "preregistration")
    if preregistration.get("schema_version") != (
        "ranked-liquidity-shallow-gbdt-independent-verification-preregistration/v1"
    ):
        raise IndependentVerificationError("preregistration schema drifted")
    if preregistration.get("run_root") != run_root.name:
        raise IndependentVerificationError("preregistration run root differs")
    if preregistration.get("completion_file_sha256") != _sha256_file(completion_path):
        raise IndependentVerificationError("preregistration completion differs")
    if preregistration.get("replay_script_sha256") != _sha256_file(replay_path):
        raise IndependentVerificationError("preregistration replay differs")
    if preregistration.get("helper_script_sha256") != helper_sha:
        raise IndependentVerificationError("preregistration helper differs")
    if preregistration.get("verifier_entrypoint_sha256") != _sha256_file(SCRIPT_PATH):
        raise IndependentVerificationError("preregistration entrypoint differs")
    if preregistration.get("strategy_sha256") != strategy_sha:
        raise IndependentVerificationError("preregistration strategy differs")
    if preregistration.get("producer_root_sha256") != producer_sha:
        raise IndependentVerificationError("preregistration producer differs")
    if preregistration.get("source_git_commit") != completion.get("post_run_git_commit"):
        raise IndependentVerificationError("preregistration source commit differs")
    if preregistration.get("verification_checkout_git_commit") != entrypoint_git_commit:
        raise IndependentVerificationError("preregistration checkout commit differs")
    python_sha = _python_executable_sha256()
    if python_sha != REGISTERED_PYTHON_SHA256:
        raise IndependentVerificationError("registered Python executable differs")
    if preregistration.get("python_executable_sha256") != python_sha:
        raise IndependentVerificationError("preregistration Python executable differs")
    if _current_producer_root_sha256(source_root) != producer_sha:
        raise IndependentVerificationError("current producer binding differs")
    for field in ("embargo_consumed", "final_oos_consumed", "production_authority"):
        _require_false(preregistration, field)
    return {
        "completion_path": completion_path,
        "completion": completion,
        "completion_sha256": _sha256_file(completion_path),
        "replay_path": replay_path,
        "replay_sha256": _sha256_file(replay_path),
        "helper_path": helper_path,
        "helper_sha256": helper_sha,
        "preregistration_path": preregistration_path,
        "preregistration_sha256": _sha256_file(preregistration_path),
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha,
        "artifact_file_sha256": _sha256_file(artifact_path),
        "strategy_sha256": strategy_sha,
        "producer_root_sha256": producer_sha,
        "verifier_entrypoint_sha256": _sha256_file(SCRIPT_PATH),
        "python_executable_sha256": python_sha,
        "source_git_commit": str(completion["post_run_git_commit"]),
        "verification_checkout_git_commit": entrypoint_git_commit,
        "source_root": source_root,
    }


def preflight(run_root: Path, source_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    inputs = _load_inputs(run_root, source_root)
    tracked_worktree_clean = _git_clean()
    if not tracked_worktree_clean:
        raise IndependentVerificationError("tracked worktree is not clean")
    return {
        "status": "preflight_ok",
        "run_root": run_root.name,
        "result_available": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "replay_script_present": inputs["replay_path"].is_file(),
        "replay_helper_present": inputs["helper_path"].is_file(),
        "tracked_worktree_clean": tracked_worktree_clean,
        "frozen_source_commit": inputs["source_git_commit"],
    }


def _write_atomic(
    path: Path,
    raw: bytes,
    *,
    durability_root: Path,
    expected_existing: bytes | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse(path.parent, "output parent")
    if expected_existing is not None:
        _assert_no_reparse(path, "existing output")
        if not path.is_file() or _read_bounded(
            path, "existing output", maximum=MAX_AUDIT_FILE_BYTES
        ) != expected_existing:
            raise IndependentVerificationError("output ownership changed")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory_chain(path.parent, durability_root)
    except FileExistsError as exc:
        raise IndependentVerificationError("atomic output collision") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_new(path: Path, raw: bytes, *, durability_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse(path.parent, "content-addressed output parent")
    if path.exists() and _is_reparse_or_symlink(path):
        raise IndependentVerificationError("content-addressed output is a reparse point")
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory_chain(path.parent, durability_root)
    except FileExistsError as exc:
        raise IndependentVerificationError("content-addressed output collision") from exc


def _replay_output(inputs: Mapping[str, Any]) -> list[str]:
    if _python_executable_sha256() != inputs["python_executable_sha256"]:
        raise IndependentVerificationError("Python executable changed before replay")
    process = subprocess.Popen(
        [
            str(PYTHON_EXECUTABLE),
            "-I",
            "-B",
            "-c",
            CAPTURE_REPLAY_DRIVER,
            str(inputs["helper_path"]),
            str(inputs["source_root"]),
            str(PROJECT_ROOT),
        ],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
    )
    chunks: list[bytes] = []
    total = 0
    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_REPLAY_STDOUT_BYTES:
                process.kill()
                process.wait()
                raise IndependentVerificationError("replay stdout exceeds bound")
            chunks.append(chunk)
        return_code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    if return_code != 0:
        raise IndependentVerificationError("replay returned nonzero")
    try:
        output = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IndependentVerificationError("replay stdout is not UTF-8") from exc
    return output.splitlines()


def _publish_receipt(run_root: Path, inputs: Mapping[str, Any], verification: Mapping[str, Any]) -> dict[str, Any]:
    if verification.get("verified") is not True:
        raise IndependentVerificationError("independent verifier did not verify")
    if verification.get("schema_version") != VERIFICATION_SCHEMA_VERSION:
        raise IndependentVerificationError("independent verifier schema drifted")
    checks = verification.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_CHECKS):
        raise IndependentVerificationError("independent verifier checks are incomplete")
    if any(value is not True for value in checks.values()):
        raise IndependentVerificationError("independent verifier checks failed")
    verification_without_hash = dict(verification)
    source_receipt_sha = _require_sha256(
        verification_without_hash.pop("receipt_sha256", None), "verifier receipt"
    )
    required_verification_fields = {
        "checks",
        "main_artifact_sha256",
        "producer_root_sha256",
        "schema_version",
        "sidecar_artifact_sha256",
        "strategy_sha256",
        "verified",
    }
    if set(verification_without_hash) != required_verification_fields:
        raise IndependentVerificationError("independent verifier fields drifted")
    if _sha256_bytes(_canonical_bytes(verification_without_hash)) != source_receipt_sha:
        raise IndependentVerificationError("verifier receipt hash is invalid")
    for field, expected in (
        ("main_artifact_sha256", inputs["artifact_sha256"]),
        ("strategy_sha256", inputs["strategy_sha256"]),
        ("producer_root_sha256", inputs["producer_root_sha256"]),
    ):
        observed = _require_sha256(verification.get(field), f"verifier {field}")
        if observed != expected:
            raise IndependentVerificationError(
                f"verifier {field} identity differs"
            )
    sidecar_artifact_sha256 = _require_sha256_mapping(
        verification.get("sidecar_artifact_sha256"), "verifier sidecars"
    )
    receipt_without_hash: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_root": run_root.name,
        "completion_file_sha256": inputs["completion_sha256"],
        "result_artifact_sha256": inputs["artifact_sha256"],
        "strategy_sha256": inputs["strategy_sha256"],
        "producer_root_sha256": inputs["producer_root_sha256"],
        "replay_script_sha256": inputs["replay_sha256"],
        "replay_helper_sha256": inputs["helper_sha256"],
        "preregistration_file_sha256": inputs["preregistration_sha256"],
        "verifier_entrypoint_sha256": inputs["verifier_entrypoint_sha256"],
        "source_git_commit": inputs["source_git_commit"],
        "verification_checkout_git_commit": inputs[
            "verification_checkout_git_commit"
        ],
        "verification": {
            "schema_version": verification["schema_version"],
            "source_receipt_sha256": source_receipt_sha,
            "main_artifact_sha256": verification["main_artifact_sha256"],
            "strategy_sha256": verification["strategy_sha256"],
            "producer_root_sha256": verification["producer_root_sha256"],
            "sidecar_artifact_sha256": sidecar_artifact_sha256,
            "checks": dict(sorted(checks.items())),
            "verified": True,
        },
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    }
    receipt_sha = _sha256_bytes(_canonical_bytes(receipt_without_hash))
    receipt = {**receipt_without_hash, "receipt_sha256": receipt_sha}
    receipt_path = (
        run_root
        / RECEIPT_ROOT_NAME
        / "sha256"
        / f"{receipt_sha}.json"
    )
    raw = _canonical_bytes(receipt) + b"\n"
    _assert_no_reparse(receipt_path.parent, "receipt output parent")
    if receipt_path.exists() or receipt_path.is_symlink():
        _assert_no_reparse(receipt_path, "receipt output")
        if not receipt_path.is_file():
            raise IndependentVerificationError("receipt output is not a regular file")
        if _read_bounded(
            receipt_path, "receipt output", maximum=len(raw)
        ) != raw:
            raise IndependentVerificationError("receipt path contains different bytes")
    else:
        _write_new(receipt_path, raw, durability_root=run_root)
    return {
        "path": str(receipt_path.relative_to(run_root)).replace("\\", "/"),
        "file_sha256": _sha256_file(receipt_path),
        "receipt_sha256": receipt_sha,
        "receipt": receipt,
    }


def _new_claim(started_at: str) -> bytes:
    claim = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-independent-verification-claim/v1"
        ),
        "nonce": secrets.token_hex(16),
        "pid": os.getpid(),
        "started_at": started_at,
    }
    return _canonical_bytes(claim) + b"\n"


def _remove_owned_claim(claim_path: Path, claim_raw: bytes, run_root: Path) -> None:
    try:
        _assert_no_reparse(claim_path, "verification claim")
        if (
            claim_path.is_file()
            and _read_bounded(
                claim_path, "verification claim", maximum=MAX_AUDIT_FILE_BYTES
            )
            == claim_raw
        ):
            claim_path.unlink()
            _fsync_directory_chain(claim_path.parent, run_root)
    except BaseException:
        return


def _record_failure(
    *,
    status_path: Path,
    running_raw: bytes | None,
    run_root: Path,
    claim_sha256: str,
    started_at: str,
    error: BaseException,
) -> None:
    failure = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "status": "failed",
        "stage": "failed",
        "pid": os.getpid(),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "run_root": run_root.name,
        "claim_sha256": claim_sha256,
        "error_type": type(error).__name__,
        "verified": False,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    raw = _canonical_bytes(failure) + b"\n"
    try:
        if running_raw is not None:
            _write_atomic(
                status_path,
                raw,
                durability_root=run_root,
                expected_existing=running_raw,
            )
        elif not status_path.exists():
            _write_new(status_path, raw, durability_root=run_root)
    except BaseException:
        return


def run(run_root: Path, source_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    run_root = _assert_registered_run_root(run_root)
    source_root = _source_root(str(source_root))
    claim_path = run_root / CLAIM_NAME
    status_path = run_root / STATUS_NAME
    started_at = _utc_now()
    claim_raw = _new_claim(started_at)
    claim_sha256 = _sha256_bytes(claim_raw)
    running_raw: bytes | None = None
    claimed = False
    try:
        _assert_no_reparse(status_path, "verification status")
        if status_path.exists():
            raise IndependentVerificationError(
                "independent verification status already exists"
            )
        _assert_no_reparse(claim_path, "verification claim")
        _write_new(claim_path, claim_raw, durability_root=run_root)
        claimed = True
        inputs = _load_inputs(run_root, source_root)
        running = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "status": "running",
            "stage": "replaying",
            "pid": os.getpid(),
            "started_at": started_at,
            "run_root": run_root.name,
            "claim_sha256": claim_sha256,
            "completion_file_sha256": inputs["completion_sha256"],
            "replay_script_sha256": inputs["replay_sha256"],
            "replay_helper_sha256": inputs["helper_sha256"],
            "preregistration_file_sha256": inputs["preregistration_sha256"],
            "verifier_entrypoint_sha256": inputs["verifier_entrypoint_sha256"],
            "verified": False,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        }
        running_raw = _canonical_bytes(running) + b"\n"
        _write_new(status_path, running_raw, durability_root=run_root)
        result_lines = [
            line[len("INDEPENDENT_FULL_RESULT ") :]
            for line in _replay_output(inputs)
            if line.startswith("INDEPENDENT_FULL_RESULT ")
        ]
        if len(result_lines) != 1:
            raise IndependentVerificationError("replay result line is not unique")
        try:
            verification = json.loads(result_lines[0])
        except json.JSONDecodeError as exc:
            raise IndependentVerificationError("replay result is not JSON") from exc
        if not isinstance(verification, dict):
            raise IndependentVerificationError("replay result is not an object")
        for path_key, hash_key, label in (
            ("replay_path", "replay_sha256", "replay source"),
            ("helper_path", "helper_sha256", "replay helper"),
            (
                "preregistration_path",
                "preregistration_sha256",
                "preregistration",
            ),
            ("completion_path", "completion_sha256", "completion"),
            ("artifact_path", "artifact_file_sha256", "result artifact"),
        ):
            if _sha256_file(inputs[path_key]) != inputs[hash_key]:
                raise IndependentVerificationError(f"{label} changed during verification")
        if _sha256_file(SCRIPT_PATH) != inputs["verifier_entrypoint_sha256"]:
            raise IndependentVerificationError("verifier entrypoint changed during verification")
        if not _git_clean() or _git_head() != inputs["verification_checkout_git_commit"]:
            raise IndependentVerificationError("source checkout changed during verification")
        if (
            not _git_clean(inputs["source_root"])
            or _git_head(inputs["source_root"]) != inputs["source_git_commit"]
        ):
            raise IndependentVerificationError("frozen source changed during verification")
        if _current_producer_root_sha256(inputs["source_root"]) != inputs[
            "producer_root_sha256"
        ]:
            raise IndependentVerificationError("producer binding changed during verification")
        if _python_executable_sha256() != inputs["python_executable_sha256"]:
            raise IndependentVerificationError("Python executable changed during verification")
        published = _publish_receipt(run_root, inputs, verification)
        status = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "status": "completed",
            "stage": "completed",
            "pid": os.getpid(),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "run_root": run_root.name,
            "claim_sha256": claim_sha256,
            "completion_file_sha256": inputs["completion_sha256"],
            "replay_script_sha256": inputs["replay_sha256"],
            "replay_helper_sha256": inputs["helper_sha256"],
            "preregistration_file_sha256": inputs["preregistration_sha256"],
            "verifier_entrypoint_sha256": inputs["verifier_entrypoint_sha256"],
            "receipt": {
                "path": published["path"],
                "file_sha256": published["file_sha256"],
                "receipt_sha256": published["receipt_sha256"],
            },
            "verified": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        }
        _write_atomic(
            status_path,
            _canonical_bytes(status) + b"\n",
            durability_root=run_root,
            expected_existing=running_raw,
        )
        return {"status": "completed", "receipt_sha256": published["receipt_sha256"]}
    except BaseException as exc:
        if claimed:
            _record_failure(
                status_path=status_path,
                running_raw=running_raw,
                run_root=run_root,
                claim_sha256=claim_sha256,
                started_at=started_at,
                error=exc,
            )
        raise
    finally:
        if claimed:
            _remove_owned_claim(claim_path, claim_raw, run_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--frozen-source-root", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_root = _run_root(args.run_root)
        source_root = _source_root(args.frozen_source_root)
        result = (
            preflight(run_root, source_root)
            if args.preflight
            else run(run_root, source_root)
        )
    except BaseException as exc:
        print(
            json.dumps(
                {"status": "rejected", "error_type": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
