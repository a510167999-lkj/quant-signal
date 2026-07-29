"""Publish the minimal independently verified factor-v2 decision receipt."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
import uuid


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GIT_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = PROJECT_ROOT / "data" / "research_runs"
PREREGISTRATION_PATH = (
    PROJECT_ROOT
    / "docs"
    / "research_preregistrations"
    / "factor_v2_low_rvol20_rank_overlay_dormant_preregistration_v2.json"
)
PREREGISTRATION_SIDECAR_PATH = PREREGISTRATION_PATH.with_suffix(".sha256")
PRODUCER_BINDING_PATH = (
    PROJECT_ROOT
    / "docs"
    / "research_preregistrations"
    / "factor_v2_decision_verification_receipt_publisher_binding_v1.json"
)
PRODUCER_BINDING_SIDECAR_PATH = PRODUCER_BINDING_PATH.with_suffix(".sha256")
RUNNER_PATH = RUNS_ROOT / ".run_factor_v2_development_evaluation.py"
VERIFIER_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "verify_factor_v2_development_evaluation_v2.py"
)
FROZEN_SOURCE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v2-eval-3e9bd1b"
)
EVALUATION_OUTPUT_DIR = (
    RUNS_ROOT
    / "audited_pit_factor_v2_development_evaluation_v1_development_4"
)
BUILD_STATUS_PATH = EVALUATION_OUTPUT_DIR.with_name(
    f"{EVALUATION_OUTPUT_DIR.name}.run.status.json"
)
VERIFY_STATUS_PATH = EVALUATION_OUTPUT_DIR.with_name(
    f"{EVALUATION_OUTPUT_DIR.name}.verify.status.json"
)
EVALUATION_LOCK_PATH = (
    RUNS_ROOT / ".factor_v2_development_evaluation.lock"
)
RECEIPT_OUTPUT_DIR = (
    RUNS_ROOT
    / (
        "audited_pit_factor_v2_development_evaluation_"
        "decision_verification_receipt_v1_development_4"
    )
)
PUBLISH_STATUS_PATH = RECEIPT_OUTPUT_DIR.with_name(
    f"{RECEIPT_OUTPUT_DIR.name}.publish.status.json"
)
PUBLISH_CLAIM_PATH = PUBLISH_STATUS_PATH.with_name(
    f"{PUBLISH_STATUS_PATH.name}.claim"
)

EXPECTED_PREREGISTRATION_RAW_SHA256 = (
    "54928441f34b92497c1c67b1b26c85e4bb267f9d3b069179ae870d017e6049a5"
)
EXPECTED_PREREGISTRATION_INTRODUCING_COMMIT = (
    "e8baab1ac7d3c86a700fb89b5c8d6a912e43d2c0"
)
EXPECTED_RUNNER_FILE_SHA256 = (
    "997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e"
)
EXPECTED_VERIFIER_FILE_SHA256 = (
    "ffce06e2707809aa17592fdd032a896ca86337be684fbd792b921a4b9f89f976"
)
EXPECTED_VERIFIER_INTRODUCING_COMMIT = (
    "0bfd66d6f7e74197903c033ee42d335629410a7d"
)
SUPERSEDED_VERIFIER_FILE_SHA256 = (
    "6d224dc61c421ea84f6358e83a5b1724783d3095f7a0addfd15e18c87d6ff405"
)
NONCANONICAL_VERIFIER_COPY_FILE_SHA256 = (
    "4f7483f7e6a01f1e81e685e54df7a4afd00ae9986951711f22d268725c88d72c"
)
EXPECTED_FROZEN_SOURCE_COMMIT = (
    "3e9bd1bcf12024f9bf52a0b0fbcdd86f7bc64109"
)
EXPECTED_EVALUATION_PRODUCER_ROOT_SHA256 = (
    "926b9229a2e6e47ae24f3b590fab46ded2244e757fbe92fc6bbf0dd3de1dd938"
)

PREREGISTRATION_SCHEMA_VERSION = (
    "factor-v2-low-rvol20-rank-overlay-dormant-preregistration/v2"
)
DECISION_RECEIPT_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-decision-verification-receipt/v1"
)
PRODUCER_BINDING_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-decision-verification-"
    "receipt-publisher-binding/v1"
)
PUBLISH_STATUS_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-decision-verification-"
    "receipt-publish-status/v1"
)
BUILD_STATUS_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-run-status/v1"
)
VERIFY_STATUS_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-independent-verification-status/v1"
)
EVALUATION_MANIFEST_SCHEMA_VERSION = (
    "audited-pit-factor-v2-development-evaluation/v1"
)
EVALUATION_VERIFICATION_RECEIPT_SCHEMA_VERSION = (
    "audited-pit-factor-v2-development-evaluation-verification/v1"
)
EVALUATION_PRODUCER_SCHEMA_VERSION = (
    "audited-pit-factor-v2-development-evaluation-producer/v1"
)
ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
EXPECTED_COMMON_SCORE_ROWS = 1_511_000

DECISION_RECEIPT_FIELDS = (
    "schema_version",
    "temporal_role",
    "factor_v2_spec_sha256",
    "evaluation_artifact_sha256",
    "evaluation_manifest_file_sha256",
    "evaluation_producer_root_sha256",
    "verification_producer_root_sha256",
    "arm_order",
    "arm_decisions",
    "source_run_identity",
    "verified",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "receipt_sha256",
)
SOURCE_RUN_IDENTITY_FIELDS = (
    "status_path",
    "pid",
    "started_at",
    "runner_file_sha256",
    "claim_file_sha256",
    "lock_file_sha256",
)
PUBLIC_VERIFICATION_RECEIPT_FIELDS = (
    "schema_version",
    "artifact_sha256",
    "manifest_file_sha256",
    "factor_v2_spec_sha256",
    "arm_order",
    "common_identity_root_sha256",
    "arm_decisions",
    "automatic_winner_selected",
    "checks",
    "verified",
    "receipt_sha256",
)
EVALUATION_MANIFEST_FIELDS = {
    "schema_version",
    "evaluation_producer_binding",
    "temporal_role",
    "factor_v2_spec_sha256",
    "arm_order",
    "evaluation_contract",
    "source_binding",
    "evaluation_sessions",
    "evaluation_sessions_sha256",
    "common_identity",
    "arms",
    "comparison",
    "scope",
    "automatic_winner_selected",
    "production_profile_registered",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "artifact_sha256",
}
PRODUCER_BINDING_FIELDS = (
    "schema_version",
    "preregistration_introducing_commit",
    "preregistration_raw_sha256",
    "publisher_file_sha256",
    "runner_file_sha256",
    "verifier_file_sha256",
    "verifier_introducing_commit",
    "superseded_verifier_file_sha256",
    "superseded_verifier_use_allowed",
    "superseded_verifier_reason_code",
    "noncanonical_verifier_copy_file_sha256",
    "noncanonical_verifier_copy_use_allowed",
    "frozen_source_commit",
    "evaluation_producer_root_sha256",
    "decision_receipt_schema_version",
    "evaluation_manifest_schema_version",
    "evaluation_verification_receipt_schema_version",
    "build_status_schema_version",
    "verify_status_schema_version",
    "canonicalization",
    "verification_producer_root_sha256",
)
EXPECTED_RECEIPT_CHECKS = {
    "content_addressing_verified": True,
    "three_arm_source_replay_verified": True,
    "exact_common_identity_verified": True,
    "exact_score_outcome_coverage_verified": True,
    "frozen_selection_cost_and_gates_verified": True,
    "common_subset_control_baseline_verified": True,
    "development_scope_verified": True,
    "embargo_not_consumed": True,
    "final_oos_not_consumed": True,
    "production_ineligible": True,
}
SAFETY_FLAGS = (
    "embargo_consumed",
    "final_oos_consumed",
    "production_profile_registered",
    "production_recommendation_eligible",
)


@dataclass(frozen=True)
class PublisherConfig:
    project_root: Path = PROJECT_ROOT
    preregistration_path: Path = PREREGISTRATION_PATH
    preregistration_sidecar_path: Path = PREREGISTRATION_SIDECAR_PATH
    expected_preregistration_raw_sha256: str = (
        EXPECTED_PREREGISTRATION_RAW_SHA256
    )
    expected_preregistration_introducing_commit: str = (
        EXPECTED_PREREGISTRATION_INTRODUCING_COMMIT
    )
    producer_binding_path: Path = PRODUCER_BINDING_PATH
    producer_binding_sidecar_path: Path = PRODUCER_BINDING_SIDECAR_PATH
    expected_producer_binding_raw_sha256: str = ""
    expected_producer_binding_introducing_commit: str = ""
    runner_path: Path = RUNNER_PATH
    expected_runner_file_sha256: str = EXPECTED_RUNNER_FILE_SHA256
    verifier_path: Path = VERIFIER_PATH
    expected_verifier_file_sha256: str = EXPECTED_VERIFIER_FILE_SHA256
    frozen_source_root: Path = FROZEN_SOURCE_ROOT
    expected_frozen_source_commit: str = EXPECTED_FROZEN_SOURCE_COMMIT
    expected_evaluation_producer_root_sha256: str = (
        EXPECTED_EVALUATION_PRODUCER_ROOT_SHA256
    )
    build_status_path: Path = BUILD_STATUS_PATH
    verify_status_path: Path = VERIFY_STATUS_PATH
    evaluation_lock_path: Path = EVALUATION_LOCK_PATH
    receipt_output_dir: Path = RECEIPT_OUTPUT_DIR
    publish_status_path: Path = PUBLISH_STATUS_PATH
    publish_claim_path: Path = PUBLISH_CLAIM_PATH


def canonical_sha256(value: Any) -> str:
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _canonical_file_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(
        _read_direct_bytes(path, f"SHA-256 input {path}")
    ).hexdigest()


def _strict_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be a lowercase SHA-256")
    return value


def _entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _read_direct_bytes(path: Path, field: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"{field} is unavailable: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                f"{field} is not a direct regular file: {path}"
            )
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_direct_json_evidence(
    path: Path,
    field: str,
) -> tuple[dict[str, Any], str]:
    raw = _read_direct_bytes(path, field)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"{field} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{field} must contain a JSON object: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_direct_json(path: Path, field: str) -> dict[str, Any]:
    return _read_direct_json_evidence(path, field)[0]


def _assert_exact_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...] | set[str],
    field: str,
) -> None:
    if set(payload) != set(fields):
        raise RuntimeError(f"{field} fields drifted")


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _git_file_bytes(root: Path, commit: str, relative_path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{relative_path}"],
        check=True,
        capture_output=True,
    ).stdout


def _process_is_running(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        raise RuntimeError("process PID is invalid")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return False
            if error == 5:
                return True
            raise OSError(error, ctypes.FormatError(error))
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                error = ctypes.get_last_error()
                raise OSError(error, ctypes.FormatError(error))
            return exit_code.value == still_active
        finally:
            if not kernel32.CloseHandle(handle):
                error = ctypes.get_last_error()
                raise OSError(error, ctypes.FormatError(error))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _verify_frozen_source(
    config: PublisherConfig,
    git_output: Callable[..., str],
) -> None:
    root = config.frozen_source_root
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("frozen evaluator worktree is unavailable")
    if (
        git_output(root, "rev-parse", "HEAD")
        != config.expected_frozen_source_commit
    ):
        raise RuntimeError("frozen evaluator source commit drifted")
    if git_output(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ):
        raise RuntimeError("frozen evaluator source worktree is dirty")


def _read_producer_binding(
    config: PublisherConfig,
    *,
    git_output: Callable[..., str],
    git_file_bytes: Callable[[Path, str, str], bytes],
) -> tuple[dict[str, Any], str]:
    descriptor, raw_sha256 = _read_direct_json_evidence(
        config.producer_binding_path,
        "receipt publisher producer binding",
    )
    _assert_exact_fields(
        descriptor,
        PRODUCER_BINDING_FIELDS,
        "receipt publisher producer binding",
    )
    if _read_direct_bytes(
        config.producer_binding_path,
        "receipt publisher producer binding",
    ) != _canonical_file_bytes(descriptor) + b"\n":
        raise RuntimeError("producer-binding canonical bytes drifted")
    expected_raw_sha256 = _strict_sha256(
        config.expected_producer_binding_raw_sha256,
        "anchored producer-binding raw SHA-256",
    )
    introducing_commit = (
        config.expected_producer_binding_introducing_commit
    )
    if (
        not isinstance(introducing_commit, str)
        or GIT_COMMIT_PATTERN.fullmatch(introducing_commit) is None
        or raw_sha256 != expected_raw_sha256
    ):
        raise RuntimeError("producer-binding Git anchor drifted")
    try:
        sidecar_raw = _read_direct_bytes(
            config.producer_binding_sidecar_path,
            "producer-binding sidecar",
        )
        sidecar = sidecar_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("producer-binding sidecar is invalid") from exc
    expected_sidecar = (
        f"{raw_sha256}  {config.producer_binding_path.name}\n"
    )
    if sidecar != expected_sidecar:
        raise RuntimeError("producer-binding raw SHA-256 sidecar drifted")
    try:
        descriptor_relative_path = (
            config.producer_binding_path.resolve()
            .relative_to(config.project_root.resolve())
            .as_posix()
        )
        sidecar_relative_path = (
            config.producer_binding_sidecar_path.resolve()
            .relative_to(config.project_root.resolve())
            .as_posix()
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("producer-binding files are outside the repository") from exc
    if (
        git_output(
            config.project_root,
            "rev-parse",
            f"{introducing_commit}^{{commit}}",
        )
        != introducing_commit
        or git_output(
            config.project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            descriptor_relative_path,
        )
        != introducing_commit
        or git_output(
            config.project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            sidecar_relative_path,
        )
        != introducing_commit
        or git_file_bytes(
            config.project_root,
            introducing_commit,
            descriptor_relative_path,
        )
        != config.producer_binding_path.read_bytes()
        or git_file_bytes(
            config.project_root,
            introducing_commit,
            sidecar_relative_path,
        )
        != config.producer_binding_sidecar_path.read_bytes()
    ):
        raise RuntimeError("producer-binding introducing commit drifted")

    publisher_path = Path(__file__).resolve()
    expected_values = {
        "schema_version": PRODUCER_BINDING_SCHEMA_VERSION,
        "preregistration_introducing_commit": (
            config.expected_preregistration_introducing_commit
        ),
        "preregistration_raw_sha256": (
            config.expected_preregistration_raw_sha256
        ),
        "publisher_file_sha256": _file_sha256(publisher_path),
        "runner_file_sha256": config.expected_runner_file_sha256,
        "verifier_file_sha256": config.expected_verifier_file_sha256,
        "verifier_introducing_commit": (
            EXPECTED_VERIFIER_INTRODUCING_COMMIT
        ),
        "superseded_verifier_file_sha256": (
            SUPERSEDED_VERIFIER_FILE_SHA256
        ),
        "superseded_verifier_use_allowed": False,
        "superseded_verifier_reason_code": (
            "nested_arm_decisions_order_changed_by_sorted_status_json"
        ),
        "noncanonical_verifier_copy_file_sha256": (
            NONCANONICAL_VERIFIER_COPY_FILE_SHA256
        ),
        "noncanonical_verifier_copy_use_allowed": False,
        "frozen_source_commit": config.expected_frozen_source_commit,
        "evaluation_producer_root_sha256": (
            config.expected_evaluation_producer_root_sha256
        ),
        "decision_receipt_schema_version": (
            DECISION_RECEIPT_SCHEMA_VERSION
        ),
        "evaluation_manifest_schema_version": (
            EVALUATION_MANIFEST_SCHEMA_VERSION
        ),
        "evaluation_verification_receipt_schema_version": (
            EVALUATION_VERIFICATION_RECEIPT_SCHEMA_VERSION
        ),
        "build_status_schema_version": BUILD_STATUS_SCHEMA_VERSION,
        "verify_status_schema_version": VERIFY_STATUS_SCHEMA_VERSION,
        "canonicalization": (
            "receipt-json-utf8-sort-keys-compact-no-nan-no-newline;"
            "binding-same-plus-one-lf/v1"
        ),
    }
    for key, expected in expected_values.items():
        if descriptor.get(key) != expected:
            raise RuntimeError(f"producer-binding value drifted: {key}")
    unsigned = dict(descriptor)
    root_sha256 = _strict_sha256(
        unsigned.pop("verification_producer_root_sha256", None),
        "verification producer root",
    )
    if canonical_sha256(unsigned) != root_sha256:
        raise RuntimeError("verification producer root drifted")
    return descriptor, raw_sha256


def _read_preregistration(
    config: PublisherConfig,
    *,
    git_output: Callable[..., str],
    git_file_bytes: Callable[[Path, str, str], bytes],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    introducing_commit = config.expected_preregistration_introducing_commit
    try:
        preregistration_relative_path = (
            config.preregistration_path.resolve()
            .relative_to(config.project_root.resolve())
            .as_posix()
        )
        sidecar_relative_path = (
            config.preregistration_sidecar_path.resolve()
            .relative_to(config.project_root.resolve())
            .as_posix()
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("dormant preregistration is outside the repository") from exc
    anchored_raw = git_file_bytes(
        config.project_root,
        introducing_commit,
        preregistration_relative_path,
    )
    anchored_sidecar = git_file_bytes(
        config.project_root,
        introducing_commit,
        sidecar_relative_path,
    )
    preregistration_raw_sha256 = hashlib.sha256(anchored_raw).hexdigest()
    expected_sidecar = (
        f"{preregistration_raw_sha256}  "
        f"{config.preregistration_path.name}\n"
    ).encode("ascii")
    if (
        git_output(
            config.project_root,
            "rev-parse",
            f"{introducing_commit}^{{commit}}",
        )
        != introducing_commit
        or git_output(
            config.project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            preregistration_relative_path,
        )
        != introducing_commit
        or git_output(
            config.project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            sidecar_relative_path,
        )
        != introducing_commit
        or anchored_sidecar != expected_sidecar
        or _read_direct_bytes(
            config.preregistration_path,
            "current dormant preregistration",
        )
        != anchored_raw
        or _read_direct_bytes(
            config.preregistration_sidecar_path,
            "current dormant preregistration sidecar",
        )
        != anchored_sidecar
    ):
        raise RuntimeError("dormant preregistration Git anchor drifted")
    try:
        preregistration = json.loads(
            anchored_raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("anchored dormant preregistration is invalid") from exc
    if not isinstance(preregistration, dict):
        raise RuntimeError("anchored dormant preregistration is invalid")
    if (
        preregistration_raw_sha256
        != config.expected_preregistration_raw_sha256
        or preregistration.get("schema_version")
        != PREREGISTRATION_SCHEMA_VERSION
    ):
        raise RuntimeError("dormant preregistration identity drifted")
    activation = preregistration.get("activation_policy")
    source_binding = preregistration.get("source_binding")
    if not isinstance(activation, dict) or not isinstance(
        source_binding, dict
    ):
        raise RuntimeError("dormant preregistration contract is missing")
    if (
        activation.get("required_verified_arm_order") != list(ARM_ORDER)
        or activation.get(
            "decision_verification_receipt_schema_version"
        )
        != DECISION_RECEIPT_SCHEMA_VERSION
        or activation.get("decision_verification_receipt_exact_fields")
        != list(DECISION_RECEIPT_FIELDS)
        or activation.get(
            "decision_verification_receipt_extra_fields_allowed"
        )
        is not False
        or activation.get(
            "decision_verification_receipt_source_run_identity_exact_fields"
        )
        != list(SOURCE_RUN_IDENTITY_FIELDS)
        or source_binding.get("development_evaluator_source_commit")
        != config.expected_frozen_source_commit
    ):
        raise RuntimeError("dormant preregistration receipt contract drifted")
    required = activation.get(
        "decision_verification_receipt_required_values"
    )
    source_run_identity = activation.get(
        "decision_verification_receipt_source_run_identity_required_values"
    )
    if (
        not isinstance(required, dict)
        or set(required)
        != {
            "temporal_role",
            "factor_v2_spec_sha256",
            "arm_order",
            "verified",
            "embargo_consumed",
            "final_oos_consumed",
            "production_recommendation_eligible",
        }
        or required.get("temporal_role") != "development"
        or required.get("arm_order") != list(ARM_ORDER)
        or required.get("verified") is not True
        or required.get("embargo_consumed") is not False
        or required.get("final_oos_consumed") is not False
        or required.get("production_recommendation_eligible") is not False
        or source_binding.get("factor_v2_spec_sha256")
        != required.get("factor_v2_spec_sha256")
        or not isinstance(source_run_identity, dict)
        or set(source_run_identity) != set(SOURCE_RUN_IDENTITY_FIELDS)
    ):
        raise RuntimeError("dormant preregistration required values drifted")
    _strict_sha256(
        required["factor_v2_spec_sha256"],
        "factor-v2 specification",
    )
    for field in ("runner_file_sha256", "claim_file_sha256", "lock_file_sha256"):
        _strict_sha256(source_run_identity.get(field), f"source {field}")
    return required, source_run_identity, preregistration_raw_sha256


def _assert_no_evaluation_lock_or_claim(
    config: PublisherConfig,
) -> None:
    paths = (
        config.evaluation_lock_path,
        config.build_status_path.with_name(
            f"{config.build_status_path.name}.claim"
        ),
        config.verify_status_path.with_name(
            f"{config.verify_status_path.name}.claim"
        ),
    )
    if any(_entry_exists(path) for path in paths):
        raise RuntimeError("evaluation lock or claim is still present")


def _assert_safety_flags_false(
    payload: Mapping[str, Any],
    field: str,
) -> None:
    if any(payload.get(flag) is not False for flag in SAFETY_FLAGS):
        raise RuntimeError(f"{field} safety flags drifted")


def _assert_public_verification_receipt(
    payload: Any,
    field: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{field} is missing")
    _assert_exact_fields(
        payload,
        PUBLIC_VERIFICATION_RECEIPT_FIELDS,
        field,
    )
    decisions = payload.get("arm_decisions")
    if (
        payload.get("schema_version")
        != EVALUATION_VERIFICATION_RECEIPT_SCHEMA_VERSION
        or payload.get("arm_order") != list(ARM_ORDER)
        or payload.get("automatic_winner_selected") is not False
        or payload.get("checks") != EXPECTED_RECEIPT_CHECKS
        or payload.get("verified") is not True
        or not isinstance(decisions, dict)
        or set(decisions) != set(ARM_ORDER)
        or any(decisions.get(arm) not in {"RED", "GREEN"} for arm in ARM_ORDER)
    ):
        raise RuntimeError(f"{field} drifted")
    for name in (
        "artifact_sha256",
        "manifest_file_sha256",
        "factor_v2_spec_sha256",
        "common_identity_root_sha256",
        "receipt_sha256",
    ):
        _strict_sha256(payload.get(name), f"{field} {name}")
    unsigned = dict(payload)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    if canonical_sha256(unsigned) != receipt_sha256:
        raise RuntimeError(f"{field} content addressing drifted")
    return dict(payload)


def _assert_request(
    request: Any,
    config: PublisherConfig,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RuntimeError("evaluation request is missing")
    expected_output_dir = config.build_status_path.with_name(
        config.build_status_path.name.removesuffix(".run.status.json")
    )
    if (
        request.get("source_root") != str(config.frozen_source_root)
        or request.get("expected_source_commit")
        != config.expected_frozen_source_commit
        or request.get("arm_order") != list(ARM_ORDER)
        or request.get("expected_common_score_row_count")
        != EXPECTED_COMMON_SCORE_ROWS
        or request.get("output_dir") != str(expected_output_dir)
        or request.get("status_path") != str(config.build_status_path)
        or request.get("evaluation_lock_path")
        != str(config.evaluation_lock_path)
    ):
        raise RuntimeError("evaluation request binding drifted")
    return dict(request)


def _manifest_summary(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    common = manifest.get("common_identity")
    if not isinstance(common, Mapping):
        raise RuntimeError("evaluation manifest common identity is missing")
    expected_counts = {arm: EXPECTED_COMMON_SCORE_ROWS for arm in ARM_ORDER}
    if (
        common.get("expected_score_row_count") != EXPECTED_COMMON_SCORE_ROWS
        or common.get("observed_arm_score_row_counts") != expected_counts
        or common.get("all_three_arms_exact_identity") is not True
    ):
        raise RuntimeError("evaluation manifest common identity drifted")
    return {
        "artifact_sha256": receipt["artifact_sha256"],
        "manifest_file_sha256": receipt["manifest_file_sha256"],
        "manifest_path": str(manifest_path),
        "arm_order": list(ARM_ORDER),
        "arm_decisions": receipt["arm_decisions"],
        "expected_common_score_row_count": EXPECTED_COMMON_SCORE_ROWS,
        "observed_arm_score_row_counts": expected_counts,
        "all_three_arms_exact_identity": True,
        "automatic_winner_selected": False,
        "production_profile_registered": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }


def _read_and_verify_manifest(
    config: PublisherConfig,
    build_result: Mapping[str, Any],
    receipt: Mapping[str, Any],
    required: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], str, str]:
    artifact_sha256 = receipt["artifact_sha256"]
    manifest_file_sha256 = receipt["manifest_file_sha256"]
    manifest_path = Path(str(build_result.get("manifest_path", "")))
    expected_output_dir = config.build_status_path.with_name(
        config.build_status_path.name.removesuffix(".run.status.json")
    )
    expected_path = expected_output_dir / f"{artifact_sha256}.json"
    if (
        os.path.normcase(os.path.abspath(manifest_path))
        != os.path.normcase(os.path.abspath(expected_path))
        or manifest_path.name != f"{artifact_sha256}.json"
    ):
        raise RuntimeError("evaluation manifest path drifted")
    if (
        not expected_output_dir.is_dir()
        or expected_output_dir.is_symlink()
    ):
        raise RuntimeError("evaluation output directory is unavailable")
    entries = list(os.scandir(expected_output_dir))
    if (
        len(entries) != 1
        or entries[0].name != manifest_path.name
        or not entries[0].is_file(follow_symlinks=False)
    ):
        raise RuntimeError("evaluation output file set drifted")
    manifest, observed_manifest_file_sha256 = (
        _read_direct_json_evidence(
            manifest_path,
            "evaluation manifest",
        )
    )
    if observed_manifest_file_sha256 != manifest_file_sha256:
        raise RuntimeError("evaluation manifest raw SHA-256 drifted")
    _assert_exact_fields(
        manifest,
        EVALUATION_MANIFEST_FIELDS,
        "evaluation manifest",
    )
    unsigned = dict(manifest)
    embedded_artifact = unsigned.pop("artifact_sha256", None)
    arms = manifest.get("arms")
    scope = manifest.get("scope")
    producer_binding = manifest.get("evaluation_producer_binding")
    if (
        embedded_artifact != artifact_sha256
        or canonical_sha256(unsigned) != artifact_sha256
        or manifest.get("schema_version")
        != EVALUATION_MANIFEST_SCHEMA_VERSION
        or manifest.get("temporal_role") != "development"
        or manifest.get("factor_v2_spec_sha256")
        != required["factor_v2_spec_sha256"]
        or manifest.get("arm_order") != list(ARM_ORDER)
        or manifest.get("automatic_winner_selected") is not False
        or not isinstance(scope, dict)
        or scope.get("embargo_consumed") is not False
        or scope.get("final_oos_consumed") is not False
        or scope.get("eligible_for_profile_registration") is not False
        or scope.get("production_recommendation_eligible") is not False
        or not isinstance(arms, dict)
        or set(arms) != set(ARM_ORDER)
        or {
            arm: arms[arm].get("preregistered_decision")
            for arm in ARM_ORDER
        }
        != receipt["arm_decisions"]
        or not isinstance(producer_binding, dict)
    ):
        raise RuntimeError("evaluation manifest drifted")
    _assert_safety_flags_false(manifest, "evaluation manifest")
    producer_identity = dict(producer_binding)
    producer_root = _strict_sha256(
        producer_identity.pop("root_sha256", None),
        "evaluation producer root",
    )
    if (
        producer_binding.get("schema_version")
        != EVALUATION_PRODUCER_SCHEMA_VERSION
        or canonical_sha256(producer_identity) != producer_root
        or producer_root
        != config.expected_evaluation_producer_root_sha256
    ):
        raise RuntimeError("evaluation producer root drifted")
    common = manifest.get("common_identity")
    if (
        not isinstance(common, dict)
        or common.get("common_identity_root_sha256")
        != receipt["common_identity_root_sha256"]
    ):
        raise RuntimeError("evaluation manifest receipt binding drifted")
    return (
        manifest_path,
        manifest,
        producer_root,
        observed_manifest_file_sha256,
    )


def _claim_identity_sha256(build_status: Mapping[str, Any]) -> str:
    token = build_status.get("run_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("evaluation build run token is invalid")
    payload = {
        "operation": "build_factor_v2_development_evaluation",
        "pid": build_status.get("pid"),
        "started_at": build_status.get("started_at"),
        "token": token,
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_inputs(
    config: PublisherConfig,
    *,
    process_is_running: Callable[[int], bool],
    git_output: Callable[..., str],
    git_file_bytes: Callable[[Path, str, str], bytes],
) -> dict[str, Any]:
    _verify_frozen_source(config, git_output)
    _assert_no_evaluation_lock_or_claim(config)
    producer_binding, producer_binding_raw_sha256 = (
        _read_producer_binding(
            config,
            git_output=git_output,
            git_file_bytes=git_file_bytes,
        )
    )
    (
        required,
        source_run_identity,
        preregistration_raw_sha256,
    ) = _read_preregistration(
        config,
        git_output=git_output,
        git_file_bytes=git_file_bytes,
    )
    if (
        _file_sha256(config.runner_path)
        != config.expected_runner_file_sha256
        or _file_sha256(config.verifier_path)
        != config.expected_verifier_file_sha256
    ):
        raise RuntimeError("evaluation runner or verifier SHA-256 drifted")

    build_status, build_status_raw_sha256 = _read_direct_json_evidence(
        config.build_status_path,
        "evaluation build status",
    )
    verify_status, verify_status_raw_sha256 = _read_direct_json_evidence(
        config.verify_status_path,
        "independent verification status",
    )
    if (
        build_status.get("schema_version") != BUILD_STATUS_SCHEMA_VERSION
        or build_status.get("status") != "completed"
        or build_status.get("stage") != "completed"
        or build_status.get("runner_file_sha256")
        != config.expected_runner_file_sha256
        or build_status.get("independent_verification_started") is not False
    ):
        raise RuntimeError("evaluation build completion status drifted")
    _assert_safety_flags_false(build_status, "evaluation build completion")
    if (
        verify_status.get("schema_version") != VERIFY_STATUS_SCHEMA_VERSION
        or verify_status.get("status") != "completed"
        or verify_status.get("stage") != "completed"
        or verify_status.get("verified") is not True
        or verify_status.get("checks") != EXPECTED_RECEIPT_CHECKS
        or verify_status.get("runner_file_sha256")
        != config.expected_runner_file_sha256
        or verify_status.get("verifier_file_sha256")
        != config.expected_verifier_file_sha256
        or verify_status.get("build_status_path")
        != str(config.build_status_path)
        or verify_status.get("build_status_file_sha256")
        != build_status_raw_sha256
    ):
        raise RuntimeError("independent verification completion status drifted")
    _assert_safety_flags_false(
        verify_status,
        "independent verification completion",
    )
    for field, status in (
        ("evaluation build", build_status),
        ("independent verification", verify_status),
    ):
        pid = status.get("pid")
        if type(pid) is not int or pid <= 0:
            raise RuntimeError(f"{field} PID is invalid")
        if process_is_running(pid):
            raise RuntimeError(f"{field} process has not exited")

    request = _assert_request(build_status.get("request"), config)
    if verify_status.get("request") != request:
        raise RuntimeError("build and verification requests differ")
    build_result = build_status.get("result")
    if not isinstance(build_result, dict):
        raise RuntimeError("evaluation build result is missing")
    _assert_exact_fields(
        build_result,
        set(PUBLIC_VERIFICATION_RECEIPT_FIELDS) | {"manifest_path"},
        "evaluation build result",
    )
    build_receipt = _assert_public_verification_receipt(
        {
            field: build_result[field]
            for field in PUBLIC_VERIFICATION_RECEIPT_FIELDS
        },
        "evaluation build receipt",
    )
    verify_receipt = _assert_public_verification_receipt(
        verify_status.get("receipt"),
        "independent verification receipt",
    )
    if build_receipt != verify_receipt:
        raise RuntimeError("build and verification receipts differ")
    if build_receipt["factor_v2_spec_sha256"] != required[
        "factor_v2_spec_sha256"
    ]:
        raise RuntimeError("factor-v2 specification binding drifted")

    (
        manifest_path,
        manifest,
        evaluation_producer_root,
        manifest_raw_sha256,
    ) = (
        _read_and_verify_manifest(
            config,
            build_result,
            build_receipt,
            required,
        )
    )
    summary = _manifest_summary(manifest_path, manifest, build_receipt)
    if (
        build_status.get("manifest_summary") != summary
        or verify_status.get("manifest_summary") != summary
    ):
        raise RuntimeError("build or verification manifest summary drifted")

    if (
        source_run_identity.get("status_path")
        != config.build_status_path.as_posix()
        or source_run_identity.get("pid") != build_status.get("pid")
        or source_run_identity.get("started_at")
        != build_status.get("started_at")
        or source_run_identity.get("runner_file_sha256")
        != build_status.get("runner_file_sha256")
    ):
        raise RuntimeError("source run identity drifted")
    claim_sha256 = _claim_identity_sha256(build_status)
    if (
        source_run_identity.get("claim_file_sha256") != claim_sha256
        or source_run_identity.get("lock_file_sha256") != claim_sha256
    ):
        raise RuntimeError("source claim identity drifted")

    snapshots = {
        "preregistration": preregistration_raw_sha256,
        "producer_binding": producer_binding_raw_sha256,
        "producer_binding_sidecar": _file_sha256(
            config.producer_binding_sidecar_path
        ),
        "publisher": _file_sha256(Path(__file__).resolve()),
        "runner": _file_sha256(config.runner_path),
        "verifier": _file_sha256(config.verifier_path),
        "build_status": build_status_raw_sha256,
        "verify_status": verify_status_raw_sha256,
        "evaluation_manifest": manifest_raw_sha256,
    }
    return {
        "required": required,
        "source_run_identity": source_run_identity,
        "build_receipt": build_receipt,
        "evaluation_producer_root_sha256": evaluation_producer_root,
        "verification_producer": producer_binding,
        "producer_binding_raw_sha256": producer_binding_raw_sha256,
        "manifest_path": manifest_path,
        "snapshots": snapshots,
        "build_pid": build_status["pid"],
        "verify_pid": verify_status["pid"],
    }


def _assert_inputs_unchanged(
    config: PublisherConfig,
    evidence: Mapping[str, Any],
    *,
    process_is_running: Callable[[int], bool],
    git_output: Callable[..., str],
) -> None:
    _verify_frozen_source(config, git_output)
    _assert_no_evaluation_lock_or_claim(config)
    paths = {
        "preregistration": config.preregistration_path,
        "producer_binding": config.producer_binding_path,
        "producer_binding_sidecar": config.producer_binding_sidecar_path,
        "publisher": Path(__file__).resolve(),
        "runner": config.runner_path,
        "verifier": config.verifier_path,
        "build_status": config.build_status_path,
        "verify_status": config.verify_status_path,
        "evaluation_manifest": evidence["manifest_path"],
    }
    current = {name: _file_sha256(path) for name, path in paths.items()}
    if current != evidence["snapshots"]:
        raise RuntimeError("receipt publication inputs changed")
    if process_is_running(evidence["build_pid"]) or process_is_running(
        evidence["verify_pid"]
    ):
        raise RuntimeError("evaluation process restarted before publication")


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("exclusive write made no forward progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        _write_exclusive(temporary, content)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_owned_claim(path: Path, expected: bytes) -> None:
    if _read_direct_bytes(path, "receipt publication claim") != expected:
        raise RuntimeError("receipt publication claim ownership drifted")


def _release_owned_claim(path: Path, expected: bytes) -> None:
    try:
        _assert_owned_claim(path, expected)
    except RuntimeError:
        return
    path.unlink(missing_ok=True)


def publish_decision_verification_receipt(
    config: PublisherConfig = PublisherConfig(),
    *,
    process_is_running: Callable[[int], bool] = _process_is_running,
    git_output: Callable[..., str] = _git_output,
    git_file_bytes: Callable[[Path, str, str], bytes] = _git_file_bytes,
) -> dict[str, Any]:
    """Validate frozen evidence and publish one exact 15-field receipt."""

    if any(
        _entry_exists(path)
        for path in (
            config.receipt_output_dir,
            config.publish_status_path,
            config.publish_claim_path,
        )
    ):
        raise RuntimeError("receipt publication target is not fresh")
    config.publish_claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_payload = {
        "operation": "publish_factor_v2_decision_verification_receipt",
        "pid": os.getpid(),
        "token": uuid.uuid4().hex,
    }
    claim_bytes = _canonical_file_bytes(claim_payload)
    _write_exclusive(
        config.publish_claim_path,
        claim_bytes,
    )
    receipt_published = False
    try:
        evidence = _validate_inputs(
            config,
            process_is_running=process_is_running,
            git_output=git_output,
            git_file_bytes=git_file_bytes,
        )
        _assert_owned_claim(config.publish_claim_path, claim_bytes)
        required = evidence["required"]
        build_receipt = evidence["build_receipt"]
        unsigned_receipt = {
            "schema_version": DECISION_RECEIPT_SCHEMA_VERSION,
            "temporal_role": required["temporal_role"],
            "factor_v2_spec_sha256": required["factor_v2_spec_sha256"],
            "evaluation_artifact_sha256": build_receipt[
                "artifact_sha256"
            ],
            "evaluation_manifest_file_sha256": build_receipt[
                "manifest_file_sha256"
            ],
            "evaluation_producer_root_sha256": evidence[
                "evaluation_producer_root_sha256"
            ],
            "verification_producer_root_sha256": evidence[
                "verification_producer"
            ]["verification_producer_root_sha256"],
            "arm_order": list(ARM_ORDER),
            "arm_decisions": build_receipt["arm_decisions"],
            "source_run_identity": evidence["source_run_identity"],
            "verified": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_recommendation_eligible": False,
        }
        receipt = {
            **unsigned_receipt,
            "receipt_sha256": canonical_sha256(unsigned_receipt),
        }
        _assert_exact_fields(
            receipt,
            DECISION_RECEIPT_FIELDS,
            "decision verification receipt",
        )
        _assert_inputs_unchanged(
            config,
            evidence,
            process_is_running=process_is_running,
            git_output=git_output,
        )
        _assert_owned_claim(config.publish_claim_path, claim_bytes)

        raw_receipt = _canonical_file_bytes(receipt)
        raw_file_sha256 = hashlib.sha256(raw_receipt).hexdigest()
        receipt_path = (
            config.receipt_output_dir / f"{raw_file_sha256}.json"
        )
        config.receipt_output_dir.mkdir(parents=False, exist_ok=False)
        _fsync_directory(config.receipt_output_dir.parent)
        try:
            _write_atomic_new(receipt_path, raw_receipt)
        except BaseException:
            config.receipt_output_dir.rmdir()
            raise
        receipt_published = True
        receipt_descriptor = {
            "path": str(receipt_path),
            "raw_file_sha256": raw_file_sha256,
            "size_bytes": len(raw_receipt),
            "schema_version": DECISION_RECEIPT_SCHEMA_VERSION,
            "receipt_sha256": receipt["receipt_sha256"],
        }
        terminal_status = {
            "schema_version": PUBLISH_STATUS_SCHEMA_VERSION,
            "status": "completed",
            "stage": "completed",
            "publisher_file_sha256": evidence["snapshots"]["publisher"],
            "producer_binding_path": str(config.producer_binding_path),
            "producer_binding_raw_sha256": evidence[
                "producer_binding_raw_sha256"
            ],
            "verification_producer": evidence["verification_producer"],
            "receipt": receipt_descriptor,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_profile_registered": False,
            "production_recommendation_eligible": False,
        }
        _assert_owned_claim(config.publish_claim_path, claim_bytes)
        _write_atomic_new(
            config.publish_status_path,
            _canonical_file_bytes(terminal_status),
        )
        return terminal_status
    except BaseException:
        if receipt_published:
            for path in config.receipt_output_dir.iterdir():
                path.unlink()
            config.receipt_output_dir.rmdir()
        raise
    finally:
        _release_owned_claim(config.publish_claim_path, claim_bytes)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish the preregistered minimal decision verification "
            "receipt from completed, independently verified evidence."
        )
    )
    parser.add_argument(
        "--producer-binding-raw-sha256",
        required=True,
    )
    parser.add_argument(
        "--producer-binding-introducing-commit",
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
        raise RuntimeError("launch requires Python -I -B")
    args = _arguments()
    config = PublisherConfig(
        expected_producer_binding_raw_sha256=(
            args.producer_binding_raw_sha256
        ),
        expected_producer_binding_introducing_commit=(
            args.producer_binding_introducing_commit
        ),
    )
    descriptor = publish_decision_verification_receipt(config)
    print(
        json.dumps(descriptor, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
