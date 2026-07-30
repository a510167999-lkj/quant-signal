from __future__ import annotations

import argparse
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
    completed = subprocess.run(
        [str(GIT_EXECUTABLE), "-C", str(FORMAL_WORKTREE_ROOT), *args],
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


def verify_formal_worktree(*, expected_reviewed_commit: str) -> None:
    if (
        type(expected_reviewed_commit) is not str
        or _COMMIT_RE.fullmatch(expected_reviewed_commit) is None
    ):
        raise FormalRunSpecError("formal worktree reviewed commit rejected")
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
    if commit != expected_reviewed_commit:
        raise FormalRunSpecError("formal worktree reviewed commit drifted")
    if dirty:
        raise FormalRunSpecError("formal worktree is dirty")


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
    target = _target_for(content)
    target.parent.mkdir(parents=True, exist_ok=True)
    for directory in (SPEC_OUTPUT_ROOT, target.parent):
        if not directory.is_dir() or _is_reparse(directory):
            raise FormalRunSpecError("content-addressed output directory rejected")
    if target.exists() or target.is_symlink():
        _verify_existing_target(target, content)
        return target
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(target), flags, 0o600)
    except FileExistsError:
        _verify_existing_target(target, content)
        return target
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
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
    worktree = str(FORMAL_WORKTREE_ROOT)
    if worktree not in sys.path:
        sys.path.insert(0, worktree)
    from app import factor_v3_daily_basic_runner

    return factor_v3_daily_basic_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--expected-reviewed-commit", required=True)
    args = parser.parse_args(argv)
    verify_formal_worktree(
        expected_reviewed_commit=args.expected_reviewed_commit,
    )
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
