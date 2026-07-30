from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Any

from app import jiaoch_points_raw_authority as raw_authority
from app.durable_io import fsync_directory


class FormalRunSpecError(RuntimeError):
    pass


FORMAL_WORKTREE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v3-daily-basic-formal-run-v2"
)
EXPECTED_BRANCH = "codex/factor-v3-daily-basic-formal-run-v2"
FORMAL_REVIEW_SOURCE_RELATIVE_PATHS = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
    "scripts/run_factor_v3_daily_basic_formal.py",
    "app/__init__.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/current_pool.py",
    "app/current_pool_gate.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/factor_v3_formal_control_contract.py",
    "app/jiaoch_credential_slots.py",
    "app/jiaoch_daily_basic_collection_set.py",
    "app/jiaoch_daily_basic_exact_set_authority.py",
    "app/jiaoch_minute_collection_set.py",
    "app/jiaoch_minute_raw_authority.py",
    "app/jiaoch_minute_reconciliation.py",
    "app/jiaoch_points_collection_set.py",
    "app/jiaoch_points_raw_authority.py",
    "app/jiaoch_points_response_normalization.py",
    "app/jiaoch_trade_cal_authority.py",
    "app/research_market_data.py",
    "app/research_membership.py",
    "app/research_partitions.py",
    "app/research_pit_collector.py",
    "app/research_pit_contracts.py",
    "app/research_pit_sources.py",
    "app/research_pit_store.py",
    "app/research_pit_transport.py",
    "app/research_provider_evidence_partitions.py",
    "app/research_provider_pit_tail.py",
    "app/research_provider_pit_tail_v2.py",
    "app/research_proxy_data.py",
    "app/research_scope.py",
    "app/research_security_code_transition.py",
    "app/research_suspension_evidence.py",
)
MAIN_REPO_ROOT = Path(r"E:\AI workspace\quant-signal-lkj")
FORMAL_REVIEW_SOURCE_RELATIVE_PATHS = (
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/jiaoch_daily_basic_collection_set.py",
    "app/jiaoch_daily_basic_exact_set_authority.py",
    "app/jiaoch_points_response_normalization.py",
    "app/jiaoch_points_raw_authority.py",
    "app/research_pit_store.py",
    "app/research_scope.py",
    "app/research_security_code_transition.py",
)
FORMAL_REVIEW_SOURCE_ROOT_SHA256 = (
    "85942cbc494f7c033b038ce11e43ad7296b44e0c3fa54b4840a1f6016ab781cf"
)
FORMAL_REVIEW_RECEIPT_SHA256 = "0" * 64
FORMAL_REVIEW_RECEIPT_PATH = (
    MAIN_REPO_ROOT
    / "data/research_artifacts/factor_v3_daily_basic_formal_review_v1"
    / "review_receipts/sha256"
    / FORMAL_REVIEW_RECEIPT_SHA256[:2]
    / f"{FORMAL_REVIEW_RECEIPT_SHA256}.json"
)
FACTOR_V3_DAILY_BASIC_RUNNER_SHA256 = (
    "454e0f4437cdfe5283b59d1b1cf0383151fe017fa5d0a0918f1d1108a0901ce9"
)
SPEC_OUTPUT_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_run_spec_v2"
    / "run_specs"
    / "sha256"
)
PLANNED_RUN_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_collection_v2_development_733"
)
FEATURE_HISTORY_ATTESTATION_SHA256 = (
    "4f73e1e0515d7c7932ba2e8b5c8885dac56645f83c5ed3d1c6570fbdd24f184c"
)
EXACT_SET_AUTHORITY_INPUTS = {
    "feature_history_frozen_source_attestation_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "factor_v3_feature_history_frozen_source_attestation_v3"
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / "68"
        / "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675.json"
    ),
    "expected_feature_history_frozen_source_attestation_sha256": (
        "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675"
    ),
    "feature_history_frozen_source_root": (
        r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run"
    ),
    "expected_feature_history_frozen_source_commit": (
        "b8057962f7a9754848994a6cfda9c9bf85e3db89"
    ),
    "feature_history_run_spec_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_runs"
        / "audited_pit_factor_v3_feature_history_run_spec_v1"
        / "run_specs"
        / "sha256"
        / "1d"
        / "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
    ),
    "feature_history_run_root": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_runs"
        / "audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
    ),
    "audited_development_universe_sqlite_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "audited_pit_universe_v2"
        / "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4"
        / "metadata.sqlite3"
    ),
    "expected_development_coverage_audit_sha256": (
        "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
    ),
    "expected_development_artifact_root_sha256": (
        "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
    ),
    "expected_development_temporal_contract_sha256": (
        "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
    ),
    "expected_development_temporal_role": "development_4",
    "security_code_transition_evidence_root": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "security_code_transition_evidence_v1"
    ),
    "expected_security_code_transition_contract_sha256": (
        "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
    ),
}


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
        raise FormalRunSpecError("formal run spec is not canonical JSON") from exc


def _script_worktree_root() -> Path:
    return Path(__file__).resolve().parents[1]


@contextmanager
def _open_pinned_file(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
):
    candidate = raw_authority._safe_existing_file(path, label)
    before = candidate.lstat()
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise FormalRunSpecError(f"{label} rejected")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

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
            raise FormalRunSpecError(f"{label} safe open rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
    else:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        stream.seek(0)
        raw = stream.read(max_bytes + 1)
        stream.seek(0)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(getattr(opened, "st_file_attributes", 0)) & 0x00000400
            or not os.path.samestat(before, opened)
            or len(raw) != opened.st_size
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise FormalRunSpecError(f"{label} identity rejected")
        yield candidate.resolve(strict=True), stream
        _postverify_pinned_file(
            candidate,
            stream,
            expected_sha256=expected_sha256,
            label=label,
            max_bytes=max_bytes,
        )
    finally:
        stream.close()


def _postverify_pinned_file(
    path: Path,
    handle: Any,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> None:
    opened = os.fstat(handle.fileno())
    terminal = raw_authority._safe_existing_file(path, label).lstat()
    handle.seek(0)
    raw = handle.read(max_bytes + 1)
    handle.seek(0)
    if (
        not os.path.samestat(opened, terminal)
        or len(raw) != opened.st_size
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise FormalRunSpecError(f"{label} drifted")


def _git_output(*args: str) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    with _open_pinned_file(
        GIT_EXECUTABLE,
        expected_sha256=GIT_EXECUTABLE_SHA256,
        label="formal git executable",
    ) as (executable, _handle):
        completed = subprocess.run(
            [str(executable), "-C", str(FORMAL_WORKTREE_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    return completed.stdout.strip()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _formal_review_source_root() -> str:
    entries = []
    for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
        raw = raw_authority._read_safe_file(
            FORMAL_WORKTREE_ROOT / Path(*relative_path.split("/")),
            label="formal reviewed source",
            max_bytes=4 * 1024 * 1024,
        )
        entries.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return hashlib.sha256(_canonical_bytes(entries)).hexdigest()


def _validated_formal_review_receipt() -> dict[str, Any]:
    raw = raw_authority._read_safe_file(
        FORMAL_REVIEW_RECEIPT_PATH,
        label="factor-v3 formal review receipt",
        max_bytes=64 * 1024,
    )
    if hashlib.sha256(raw).hexdigest() != FORMAL_REVIEW_RECEIPT_SHA256:
        raise FormalRunSpecError("formal review receipt content rejected")
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormalRunSpecError("formal review receipt rejected") from exc
    if (
        type(receipt) is not dict
        or set(receipt)
        != {
            "branch",
            "review_status",
            "reviewed_source_root_sha256",
            "schema",
        }
        or _canonical_bytes(receipt) != raw
        or receipt["schema"]
        != "factor-v3-daily-basic-formal-review-receipt/v1"
        or receipt["branch"] != EXPECTED_BRANCH
        or receipt["review_status"] != "APPROVED_NO_P0_P1_P2"
        or receipt["reviewed_source_root_sha256"]
        != FORMAL_REVIEW_SOURCE_ROOT_SHA256
    ):
        raise FormalRunSpecError("formal review receipt rejected")
    return receipt


def verify_formal_worktree() -> None:
    script_root = _script_worktree_root()
    if (
        script_root != FORMAL_WORKTREE_ROOT
        or not FORMAL_WORKTREE_ROOT.is_dir()
        or _is_reparse(FORMAL_WORKTREE_ROOT)
    ):
        raise FormalRunSpecError("formal worktree root drifted")
    try:
        git_root = Path(_git_output("rev-parse", "--show-toplevel")).resolve()
        commit = _git_output("rev-parse", "HEAD")
        branch = _git_output("branch", "--show-current")
        dirty = _git_output("status", "--porcelain=v1", "--untracked-files=all")
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise FormalRunSpecError("formal worktree git identity unavailable") from exc
    if git_root != FORMAL_WORKTREE_ROOT or branch != EXPECTED_BRANCH:
        raise FormalRunSpecError("formal worktree branch drifted")
    if _COMMIT_RE.fullmatch(commit) is None:
        raise FormalRunSpecError("formal worktree commit rejected")
    if dirty:
        raise FormalRunSpecError("formal worktree is dirty")
    receipt = _validated_formal_review_receipt()
    source_root = _formal_review_source_root()
    if (
        source_root != FORMAL_REVIEW_SOURCE_ROOT_SHA256
        or receipt["reviewed_source_root_sha256"] != source_root
    ):
        raise FormalRunSpecError("formal worktree reviewed source drifted")


def verify_planned_run_root() -> None:
    parent = PLANNED_RUN_ROOT.parent
    if not parent.is_dir() or _is_reparse(parent):
        raise FormalRunSpecError("planned run-root parent rejected")
    sidecar_prefixes = (
        f"{PLANNED_RUN_ROOT.name}.",
        f".{PLANNED_RUN_ROOT.name}.",
    )
    try:
        sidecars = [
            child for child in parent.iterdir() if child.name.startswith(sidecar_prefixes)
        ]
    except OSError as exc:
        raise FormalRunSpecError("planned run-root parent unavailable") from exc
    if sidecars:
        raise FormalRunSpecError("planned run-root sidecar exists")
    if not PLANNED_RUN_ROOT.exists():
        if _is_reparse(PLANNED_RUN_ROOT):
            raise FormalRunSpecError("planned run-root rejected")
        return
    if not PLANNED_RUN_ROOT.is_dir() or _is_reparse(PLANNED_RUN_ROOT):
        raise FormalRunSpecError("planned run-root rejected")
    try:
        if next(PLANNED_RUN_ROOT.iterdir(), None) is not None:
            raise FormalRunSpecError("planned run-root is not empty")
    except OSError as exc:
        raise FormalRunSpecError("planned run-root unavailable") from exc


def _assert_no_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized
                in {
                    "token",
                    "credential",
                    "secret",
                    "password",
                    "api_key",
                    "route_credential",
                    "credential_material",
                }
                or "capability" in normalized
                or normalized.endswith(("_token", "_secret", "_password", "_api_key"))
            ):
                raise FormalRunSpecError("formal run spec contains a credential shape")
            _assert_no_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _assert_no_credential_shape(nested)


def _assert_formal_candidate(candidate: Any) -> dict[str, Any]:
    if type(candidate) is not dict:
        raise FormalRunSpecError("formal run spec rejected")
    sessions = candidate.get("sessions")
    if (
        candidate.get("schema") != "factor-v3-daily-basic-run-spec/v2"
        or candidate.get("session_count") != 733
        or type(sessions) is not list
        or len(sessions) != 733
        or len(set(sessions)) != 733
        or sessions != sorted(sessions)
    ):
        raise FormalRunSpecError("formal run spec must contain 733 collection sessions, not 732 T-1 labels")
    try:
        parsed_sessions = [date.fromisoformat(item).isoformat() for item in sessions]
    except (TypeError, ValueError) as exc:
        raise FormalRunSpecError("formal run spec sessions rejected") from exc
    if parsed_sessions != sessions:
        raise FormalRunSpecError("formal run spec sessions rejected")
    if sessions[0] != "2023-06-26":
        raise FormalRunSpecError("formal run spec must start at 2023-06-26")
    if sessions[-1] != "2026-07-03":
        raise FormalRunSpecError("formal run spec must end at 2026-07-03")
    if candidate.get("collector") != {
        "max_attempts": 3,
        "timeout_seconds": 30.0,
        "workers": 1,
    }:
        raise FormalRunSpecError("formal run spec collector drifted")
    if candidate.get("exact_set_authority_inputs") != EXACT_SET_AUTHORITY_INPUTS:
        raise FormalRunSpecError("formal run spec exact inputs drifted")
    _assert_no_credential_shape(candidate)
    return candidate


def build_and_verify_candidate(
    runner_module: ModuleType,
) -> tuple[dict[str, Any], bytes]:
    arguments = {
        "exact_set_authority_inputs": dict(EXACT_SET_AUTHORITY_INPUTS),
        "timeout_seconds": 30,
        "max_attempts": 3,
    }
    first = runner_module.build_factor_v3_daily_basic_run_spec(**arguments)
    second = runner_module.build_factor_v3_daily_basic_run_spec(**arguments)
    first_bytes = _canonical_bytes(first)
    if first_bytes != _canonical_bytes(second):
        raise FormalRunSpecError("formal run spec is not deterministic")
    candidate = _assert_formal_candidate(first)
    if first_bytes.endswith(b"\n"):
        raise FormalRunSpecError("formal run spec has a trailing newline")
    with tempfile.TemporaryDirectory(prefix="factor-v3-daily-basic-spec-") as temporary:
        candidate_path = Path(temporary) / "candidate.json"
        candidate_path.write_bytes(first_bytes)
        loaded = runner_module.load_factor_v3_daily_basic_run_spec(candidate_path)
    if _canonical_bytes(loaded) != first_bytes:
        raise FormalRunSpecError("formal run spec load verification drifted")
    _assert_formal_candidate(loaded)
    return candidate, first_bytes


def _target_for(content: bytes) -> Path:
    file_sha256 = hashlib.sha256(content).hexdigest()
    return SPEC_OUTPUT_ROOT / file_sha256[:2] / f"{file_sha256}.json"


def _verify_existing_target(target: Path, content: bytes) -> None:
    try:
        metadata = target.lstat()
        existing = target.read_bytes()
    except OSError as exc:
        raise FormalRunSpecError("content-addressed target unavailable") from exc
    if (
        target.is_symlink()
        or _is_reparse(target)
        or not stat.S_ISREG(metadata.st_mode)
        or existing != content
    ):
        raise FormalRunSpecError("content-addressed target exists with different bytes")


def publish_candidate(content: bytes) -> Path:
    try:
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormalRunSpecError("content-addressed candidate rejected") from exc
    if not content or content.endswith(b"\n") or _canonical_bytes(parsed) != content:
        raise FormalRunSpecError("content-addressed candidate rejected")
    digest = hashlib.sha256(content).hexdigest()
    if SPEC_OUTPUT_ROOT.name != "sha256":
        raise FormalRunSpecError("content-addressed output directory rejected")
    authority_root = SPEC_OUTPUT_ROOT.parent.parent
    try:
        authority_parent = raw_authority._safe_existing_directory(
            authority_root.parent,
            "factor-v3 formal spec output parent",
        )
        authority_root = raw_authority._ensure_child_directory(
            authority_parent,
            authority_root.name,
        )
        target_parent = raw_authority._content_addressed_directory(
            authority_root,
            SPEC_OUTPUT_ROOT.parent.name,
            digest,
        )
        target = target_parent / f"{digest}.json"
        raw_authority._write_create_only(
            target,
            content,
            label="factor-v3 formal run spec",
            reuse_identical=True,
        )
        fsync_directory(target_parent)
        stored = raw_authority._read_safe_file(
            target,
            label="factor-v3 formal run spec",
            max_bytes=len(content),
            expected_size=len(content),
        )
    except (OSError, ValueError) as exc:
        raise FormalRunSpecError(
            "content-addressed target exists with different bytes"
        ) from exc
    if stored != content:
        raise FormalRunSpecError("content-addressed target postverify rejected")
    return target


def safe_summary(
    candidate: dict[str, Any],
    content: bytes,
    *,
    published: bool,
) -> dict[str, Any]:
    target = _target_for(content)
    return {
        "status": "preflight-verified",
        "published": published,
        "session_count": candidate["session_count"],
        "session_start": candidate["sessions"][0],
        "session_end": candidate["sessions"][-1],
        "file_sha256": hashlib.sha256(content).hexdigest(),
        "target_path": str(target),
    }


def _load_runner() -> ModuleType:
    runner_path = FORMAL_WORKTREE_ROOT / "app/factor_v3_daily_basic_runner.py"
    with _open_pinned_file(
        runner_path,
        expected_sha256=FACTOR_V3_DAILY_BASIC_RUNNER_SHA256,
        label="factor-v3 daily-basic runner source",
        max_bytes=4 * 1024 * 1024,
    ) as (candidate, handle):
        worktree = str(FORMAL_WORKTREE_ROOT)
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        from app import factor_v3_daily_basic_runner

        imported_path = Path(
            factor_v3_daily_basic_runner.__file__
        ).resolve(strict=True)
        if imported_path != candidate:
            raise FormalRunSpecError("formal runner import identity rejected")
        _postverify_pinned_file(
            candidate,
            handle,
            expected_sha256=FACTOR_V3_DAILY_BASIC_RUNNER_SHA256,
            label="factor-v3 daily-basic runner source",
            max_bytes=4 * 1024 * 1024,
        )
        return factor_v3_daily_basic_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    verify_formal_worktree()
    verify_planned_run_root()
    candidate, content = build_and_verify_candidate(_load_runner())
    if args.write:
        publish_candidate(content)
    print(
        json.dumps(
            safe_summary(candidate, content, published=args.write),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
