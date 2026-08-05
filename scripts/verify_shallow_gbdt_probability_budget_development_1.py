"""Independently replay the frozen probability-budget development run.

This verifier is deliberately separate from the formal launcher.  It consumes
only the already-frozen development inputs, replays the complete computation in
an isolated Python process, and publishes hashes plus boolean checks.  It never
opens embargo/final-OOS/production authority and never prints strategy output.
"""

from __future__ import annotations

import argparse
import hashlib
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
SCRIPT_GIT_PATH = "scripts/verify_shallow_gbdt_probability_budget_development_1.py"
RUN_ROOT_NAME = (
    "audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
RUN_ROOT_RELATIVE = Path("data/research_runs") / RUN_ROOT_NAME
EXPECTED_SOURCE_COMMIT = "e5cf0a727ae00a73b62da1b92e36b3a8ac2a6b9b"
EXPECTED_STRATEGY_SHA256 = (
    "4bd7afa5a8694580f9eabc2c6aed1554ea2e199d189c8e5a808265700100aaf5"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "5e04d64e6719e25f49ac342556f529877e87e39bc7abe6efda7549c193fd9bca"
)
EXPECTED_RUN_SPEC_SHA256 = (
    "bb676ebfc55a31334ac8be2f80f955fe837d8cd6b903c1d52ef0588272255daf"
)
EXPECTED_COMPLETION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-completion/v1"
)
EXPECTED_PROGRESS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-replay-progress/v1"
)
EXPECTED_VERIFICATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "result-bundle-verification/v1"
)
PREREGISTRATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "independent-verification-preregistration/v1"
)
STATUS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "independent-verification-status/v1"
)
RECEIPT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "independent-verification-receipt/v1"
)
REPLAY_RESULT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "independent-replay-result/v1"
)
COMPLETION_NAME = "formal_run.completion.json"
RESOURCE_RECEIPT_NAME = "formal_run.resource_receipt.json"
PREREGISTRATION_NAME = (
    "formal_run.probability_budget_independent_verification.preregistration.json"
)
STATUS_NAME = "formal_run.probability_budget_independent_verification.status.json"
CLAIM_NAME = "formal_run.probability_budget_independent_verification.claim"
REPLAY_RESULT_NAME = "formal_run.probability_budget_independent_replay.result.json"
REPLAY_STDOUT_NAME = "formal_run.probability_budget_independent_replay.stdout.log"
REPLAY_STDERR_NAME = "formal_run.probability_budget_independent_replay.stderr.log"
RECEIPT_ROOT_NAME = "probability_budget_independent_verification_receipts"
SCRATCH_ROOT_NAME = ".probability_budget_independent_replay_scratch"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PROBE_BYTES = 1024 * 1024
REPARSE_POINT_ATTRIBUTE = 0x400

REPLAY_PLAN: dict[str, Any] = {
    "schema_version": (
        "ranked-liquidity-shallow-gbdt-probability-budget-"
        "independent-replay-plan/v1"
    ),
    "callable": (
        "run_audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_"
        "rolling_oof"
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
        "security_code_transition_evidence_root": (
            "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
    },
    "development_partition": {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    },
    "scope": {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    },
    "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
}
EXPECTED_REPLAY_PLAN_SHA256 = (
    "718839358607b21656c0b53bbb73dcde38650522bf54c20855082a1cdef13d46"
)

REQUIRED_VERIFICATION_CHECKS = frozenset(
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

ISOLATED_REPLAY_DRIVER = r'''
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
scratch_root = Path(sys.argv[2]).resolve()
result_path = Path(sys.argv[3]).resolve()
plan = json.loads(sys.argv[4])
sys.path.insert(0, str(source_root))
os.chdir(source_root)

from app.audited_pit_continuous_ridge_oof import (
    run_audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_rolling_oof,
)
from app.config import get_settings

inputs = plan["inputs"]
partition = plan["development_partition"]
with tempfile.TemporaryDirectory(
    prefix="probability-budget-independent-",
    dir=str(scratch_root),
) as output_dir:
    result = run_audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_rolling_oof(
        settings=get_settings(),
        audited_pit_universe_path=source_root / inputs["audited_pit_universe_path"],
        expected_coverage_audit_sha256=inputs["expected_coverage_audit_sha256"],
        expected_artifact_root_sha256=inputs["expected_artifact_root_sha256"],
        temporal_contract_path=source_root / inputs["temporal_contract_path"],
        expected_temporal_contract_sha256=inputs["expected_temporal_contract_sha256"],
        security_code_transition_evidence_root=(
            source_root / inputs["security_code_transition_evidence_root"]
        ),
        expected_security_code_transition_contract_sha256=(
            inputs["expected_security_code_transition_contract_sha256"]
        ),
        start_date=partition["start_date"],
        end_date=partition["end_date"],
        output_dir=output_dir,
    )
    verification = dict(result["verification"])
    artifact = dict(result["artifact"])
    runtime_verification = dict(result["runtime_verification"])
    if verification.get("verified") is not True:
        raise RuntimeError("independent replay did not verify its result bundle")
    payload = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-probability-budget-"
            "independent-replay-result/v1"
        ),
        "main_artifact_sha256": artifact["artifact_sha256"],
        "runtime_verification_artifact_sha256": runtime_verification["artifact_sha256"],
        "verification": verification,
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    descriptor = os.open(str(result_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
'''

PRODUCER_PROBE_DRIVER = r'''
import json
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source_root))
from app import audited_pit_shallow_gbdt_probability_budget as probability_budget
from app.audited_pit_continuous_ridge_oof import resolve_ranked_liquidity_run_variant

variant = resolve_ranked_liquidity_run_variant(
    probability_budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
)
print(json.dumps({
    "strategy_sha256": probability_budget._SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256,
    "producer_root_sha256": variant["producer_binding"]()["root_sha256"],
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
'''


class IndependentVerificationError(RuntimeError):
    """Raised when the formal development result cannot be independently verified."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
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


def _require_sha256(value: object, label: str) -> str:
    observed = str(value or "").lower()
    if not HEX_SHA256.fullmatch(observed):
        raise IndependentVerificationError(f"{label} is not a SHA-256")
    return observed


def _require_false(value: Mapping[str, Any], field: str) -> None:
    if value.get(field) is not False:
        raise IndependentVerificationError(f"{field} must remain false")


def _require_true(value: Mapping[str, Any], field: str) -> None:
    if value.get(field) is not True:
        raise IndependentVerificationError(f"{field} must be true")


def _assert_no_reparse(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise IndependentVerificationError(f"{label} is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if stat.S_ISLNK(metadata.st_mode) or attributes & REPARSE_POINT_ATTRIBUTE:
        raise IndependentVerificationError(f"{label} must not be a reparse point")


def _regular_file(path: Path, label: str) -> Path:
    _assert_no_reparse(path, label)
    if not path.is_file():
        raise IndependentVerificationError(f"{label} is not a regular file")
    return path


def _read_bounded(path: Path, label: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    _regular_file(path, label)
    try:
        if path.stat().st_size > maximum:
            raise IndependentVerificationError(f"{label} exceeds its bound")
        return path.read_bytes()
    except IndependentVerificationError:
        raise
    except OSError as exc:
        raise IndependentVerificationError(f"{label} is unreadable") from exc


def _read_object(path: Path, label: str, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        value = json.loads(_read_bounded(path, label, maximum=maximum).decode("utf-8-sig"))
    except IndependentVerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise IndependentVerificationError(f"{label} is not an object")
    return value


def _safe_child(root: Path, relative_name: str, label: str) -> Path:
    _assert_no_reparse(root, f"{label} parent")
    candidate = root / relative_name
    if Path(relative_name).name != relative_name:
        raise IndependentVerificationError(f"{label} path is not a direct child")
    return candidate


def _content_addressed_document(path: Path, label: str) -> dict[str, Any]:
    document = _read_object(path, label)
    unsigned = dict(document)
    embedded = _require_sha256(unsigned.pop("artifact_sha256", None), label)
    canonical = _sha256_bytes(_canonical_bytes(unsigned))
    if embedded != canonical or path.name != f"{canonical}.json":
        raise IndependentVerificationError(f"{label} is not content addressed")
    return {
        "document": document,
        "canonical_artifact_sha256": canonical,
        "file_sha256": _sha256_file(path),
    }


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise IndependentVerificationError("git inspection failed")
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise IndependentVerificationError("git output is invalid") from exc


def _normalized_source_bytes(path: Path) -> bytes:
    return _regular_file(path, "verifier entrypoint").read_bytes().replace(b"\r\n", b"\n")


def _validate_expected_commit(value: str) -> str:
    expected = value.strip().lower()
    if not HEX_COMMIT.fullmatch(expected):
        raise IndependentVerificationError("expected verifier commit is invalid")
    return expected


def _verifier_identity(expected_commit: str) -> dict[str, str]:
    expected = _validate_expected_commit(expected_commit)
    if Path(_git_output(PROJECT_ROOT, "rev-parse", "--show-toplevel")).resolve() != PROJECT_ROOT:
        raise IndependentVerificationError("verifier root is not a Git worktree root")
    if _git_output(PROJECT_ROOT, "status", "--porcelain"):
        raise IndependentVerificationError("verifier worktree is not clean")
    if _git_output(PROJECT_ROOT, "rev-parse", "HEAD").lower() != expected:
        raise IndependentVerificationError("verifier commit drifted")
    blob = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "show", f"{expected}:{SCRIPT_GIT_PATH}"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if blob.returncode != 0 or blob.stdout != _normalized_source_bytes(SCRIPT_PATH):
        raise IndependentVerificationError("verifier entrypoint does not match its commit")
    return {
        "git_commit": expected,
        "script_sha256": _sha256_file(SCRIPT_PATH),
        "script_git_blob_sha256": _sha256_bytes(blob.stdout),
    }


def _source_producer_identity(source_root: Path, python_executable: Path) -> dict[str, str]:
    completed = subprocess.run(
        [str(python_executable), "-I", "-c", PRODUCER_PROBE_DRIVER, str(source_root)],
        check=False,
        cwd=source_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_PROBE_BYTES:
        raise IndependentVerificationError("frozen producer probe failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError("frozen producer probe is invalid") from exc
    if not isinstance(value, dict):
        raise IndependentVerificationError("frozen producer probe is invalid")
    strategy = _require_sha256(value.get("strategy_sha256"), "source strategy")
    producer = _require_sha256(value.get("producer_root_sha256"), "source producer")
    if strategy != EXPECTED_STRATEGY_SHA256 or producer != EXPECTED_PRODUCER_ROOT_SHA256:
        raise IndependentVerificationError("frozen source producer binding drifted")
    return {"strategy_sha256": strategy, "producer_root_sha256": producer}


def _source_identity(source_root_value: str) -> dict[str, Any]:
    source_candidate = Path(source_root_value).expanduser().absolute()
    _assert_no_reparse(source_candidate, "frozen source root")
    source_root = source_candidate.resolve()
    if not source_root.is_dir():
        raise IndependentVerificationError("frozen source root is not a directory")
    if Path(_git_output(source_root, "rev-parse", "--show-toplevel")).resolve() != source_root:
        raise IndependentVerificationError("frozen source root is not a Git worktree root")
    if _git_output(source_root, "status", "--porcelain"):
        raise IndependentVerificationError("frozen source worktree is not clean")
    if _git_output(source_root, "rev-parse", "HEAD").lower() != EXPECTED_SOURCE_COMMIT:
        raise IndependentVerificationError("frozen source commit drifted")
    python_executable = source_root / ".venv" / "Scripts" / "python.exe"
    _regular_file(python_executable, "frozen source Python executable")
    producer = _source_producer_identity(source_root, python_executable)
    return {
        "root": source_root,
        "git_commit": EXPECTED_SOURCE_COMMIT,
        "python_executable": python_executable,
        "python_executable_sha256": _sha256_file(python_executable),
        **producer,
    }


def _verify_runtime_verification(
    document: Mapping[str, Any],
    *,
    main_artifact_sha256: str,
) -> None:
    expected_fields = {
        "schema_version",
        "strategy_sha256",
        "producer_root_sha256",
        "main_artifact_sha256",
        "sidecar_artifact_sha256",
        "checks",
        "verified",
        "receipt_sha256",
        "artifact_sha256",
    }
    if set(document) != expected_fields:
        raise IndependentVerificationError("runtime verification fields drifted")
    unsigned = dict(document)
    artifact_sha = _require_sha256(unsigned.pop("artifact_sha256"), "runtime artifact")
    if artifact_sha != _sha256_bytes(_canonical_bytes(unsigned)):
        raise IndependentVerificationError("runtime verification artifact hash is invalid")
    receipt_unsigned = dict(unsigned)
    receipt_sha = _require_sha256(receipt_unsigned.pop("receipt_sha256"), "runtime receipt")
    if receipt_sha != _sha256_bytes(_canonical_bytes(receipt_unsigned)):
        raise IndependentVerificationError("runtime verification receipt hash is invalid")
    checks = document.get("checks")
    sidecars = document.get("sidecar_artifact_sha256")
    if (
        document.get("schema_version") != EXPECTED_VERIFICATION_SCHEMA
        or document.get("verified") is not True
        or _require_sha256(document.get("strategy_sha256"), "runtime strategy")
        != EXPECTED_STRATEGY_SHA256
        or _require_sha256(document.get("producer_root_sha256"), "runtime producer")
        != EXPECTED_PRODUCER_ROOT_SHA256
        or _require_sha256(document.get("main_artifact_sha256"), "runtime main artifact")
        != main_artifact_sha256
        or not isinstance(sidecars, dict)
        or not sidecars
        or any(not HEX_SHA256.fullmatch(str(value)) for value in sidecars.values())
        or not isinstance(checks, dict)
        or set(checks) != set(REQUIRED_VERIFICATION_CHECKS)
        or any(value is not True for value in checks.values())
    ):
        raise IndependentVerificationError("runtime verification contract is invalid")


def _load_completed_run(source: Mapping[str, Any]) -> dict[str, Any]:
    source_root = Path(source["root"])
    run_root = source_root / RUN_ROOT_RELATIVE
    _assert_no_reparse(run_root, "formal run root")
    if not run_root.is_dir():
        raise IndependentVerificationError("formal run root is missing")
    completion_path = _safe_child(run_root, COMPLETION_NAME, "completion")
    completion_raw = _read_bounded(completion_path, "formal completion")
    try:
        completion = json.loads(completion_raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError("formal completion is invalid") from exc
    if not isinstance(completion, dict):
        raise IndependentVerificationError("formal completion is not an object")
    if (
        completion.get("schema_version") != EXPECTED_COMPLETION_SCHEMA
        or completion.get("classification")
        != "completed_result_pending_independent_verification"
        or completion.get("result_available") is not True
        or completion.get("independent_verification_complete") is not False
        or completion.get("statistical_interpretation_allowed") is not False
    ):
        raise IndependentVerificationError("formal completion is not eligible for verification")
    for field in (
        "immutable_inputs_unchanged",
        "artifact_content_addressed",
        "runtime_verification_content_addressed",
    ):
        _require_true(completion, field)
    for field in ("embargo_consumed", "final_oos_consumed", "production_authority"):
        _require_false(completion, field)
    preflight = completion.get("preflight")
    postflight = completion.get("post_run_preflight")
    if not isinstance(preflight, dict) or postflight != preflight:
        raise IndependentVerificationError("formal preflight binding changed")
    producer_binding = preflight.get("producer_binding")
    if (
        preflight.get("git_commit") != EXPECTED_SOURCE_COMMIT
        or _require_sha256(preflight.get("strategy_sha256"), "formal strategy")
        != EXPECTED_STRATEGY_SHA256
        or _require_sha256(preflight.get("run_spec_sha256"), "formal run spec")
        != EXPECTED_RUN_SPEC_SHA256
        or not isinstance(producer_binding, dict)
        or _require_sha256(producer_binding.get("root_sha256"), "formal producer")
        != EXPECTED_PRODUCER_ROOT_SHA256
    ):
        raise IndependentVerificationError("formal launcher identity drifted")
    resource_reference = completion.get("resource_receipt")
    if not isinstance(resource_reference, dict):
        raise IndependentVerificationError("formal resource receipt is missing")
    if (
        resource_reference.get("path") != RESOURCE_RECEIPT_NAME
        or resource_reference.get("exit_code") != 0
        or resource_reference.get("memory_limit_enforced") is not False
        or resource_reference.get("process_tree_drained") is not None
    ):
        raise IndependentVerificationError("formal resource receipt summary is invalid")
    resource_path = _safe_child(run_root, RESOURCE_RECEIPT_NAME, "resource receipt")
    if _sha256_file(_regular_file(resource_path, "resource receipt")) != _require_sha256(
        resource_reference.get("sha256"), "formal resource receipt"
    ):
        raise IndependentVerificationError("formal resource receipt changed")
    resource = _read_object(resource_path, "formal resource receipt")
    if (
        resource.get("schema_version") != "research-unbounded-command-receipt/v1"
        or resource.get("exit_code") != 0
        or resource.get("child_reaped") is not True
        or resource.get("memory_limit_enforced") is not False
        or resource.get("process_tree_drained") is not None
        or resource.get("process_tree_drain_verification") != "not_performed"
    ):
        raise IndependentVerificationError("formal resource receipt is invalid")
    progress = completion.get("progress")
    artifact_reference = completion.get("result_artifact")
    runtime_reference = completion.get("runtime_verification")
    if (
        not isinstance(progress, dict)
        or progress.get("schema_version") != EXPECTED_PROGRESS_SCHEMA
        or progress.get("stage") != "completed"
        or not isinstance(artifact_reference, dict)
        or not isinstance(runtime_reference, dict)
    ):
        raise IndependentVerificationError("formal result references are invalid")
    artifact_name = str(artifact_reference.get("path") or "")
    artifact_path = _safe_child(run_root, artifact_name, "main artifact")
    artifact = _content_addressed_document(artifact_path, "main artifact")
    main_sha = artifact["canonical_artifact_sha256"]
    main_document = artifact["document"]
    file_name_claim = artifact_reference.get("file_name_matches_content_sha256")
    if (
        _require_sha256(artifact_reference.get("canonical_artifact_sha256"), "formal main artifact")
        != main_sha
        or _require_sha256(progress.get("artifact_sha256"), "formal progress artifact")
        != main_sha
        or artifact_reference.get("file_sha256") != artifact["file_sha256"]
        or (file_name_claim is not None and file_name_claim is not True)
        or main_document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or not isinstance(main_document.get("producer_code"), dict)
        or main_document["producer_code"].get("root_sha256")
        != EXPECTED_PRODUCER_ROOT_SHA256
        or not isinstance(main_document.get("scope"), dict)
    ):
        raise IndependentVerificationError("main artifact binding is invalid")
    for field, expected in (
        ("point_in_time", True),
        ("development_only", True),
        ("embargo_consumed", False),
        ("final_oos_consumed", False),
        ("eligible_for_profile_registration", False),
        ("production_recommendation_eligible", False),
    ):
        if main_document["scope"].get(field) is not expected:
            raise IndependentVerificationError("main artifact scope is invalid")
    runtime_name = str(runtime_reference.get("path") or "")
    runtime_directory = run_root / "verifications"
    _assert_no_reparse(runtime_directory, "runtime verification directory")
    runtime_path = _safe_child(runtime_directory, runtime_name, "runtime verification")
    runtime = _content_addressed_document(runtime_path, "runtime verification")
    runtime_document = runtime["document"]
    if (
        _require_sha256(
            runtime_reference.get("canonical_artifact_sha256"), "runtime verification artifact"
        )
        != runtime["canonical_artifact_sha256"]
        or runtime_reference.get("file_sha256") != runtime["file_sha256"]
    ):
        raise IndependentVerificationError("runtime verification reference changed")
    _verify_runtime_verification(runtime_document, main_artifact_sha256=main_sha)
    return {
        "run_root": run_root,
        "completion_sha256": _sha256_bytes(completion_raw),
        "completion_path": completion_path,
        "main_artifact_sha256": main_sha,
        "runtime_verification_artifact_sha256": runtime["canonical_artifact_sha256"],
        "runtime_verification": runtime_document,
        "source": dict(source),
    }


def _assert_replay_plan() -> None:
    if _sha256_bytes(_canonical_bytes(REPLAY_PLAN)) != EXPECTED_REPLAY_PLAN_SHA256:
        raise IndependentVerificationError("independent replay plan drifted")
    partition = REPLAY_PLAN["development_partition"]
    scope = REPLAY_PLAN["scope"]
    if (
        partition["temporal_role"] != "development"
        or partition["embargo_consumed"] is not False
        or partition["final_oos_consumed"] is not False
        or scope["point_in_time"] is not True
        or scope["development_only"] is not True
        or scope["production_authority"] is not False
        or scope["automatic_trading_authority"] is not False
        or REPLAY_PLAN["resource_contract"]
        != {"memory_policy": "unbounded", "enforcement": "none"}
    ):
        raise IndependentVerificationError("independent replay scope drifted")


def _write_once(path: Path, raw: bytes, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse(path.parent, f"{label} parent")
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if _read_bounded(path, label, maximum=len(raw)) != raw:
            raise IndependentVerificationError(f"{label} already contains different bytes")
        return
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _preregistration(inputs: Mapping[str, Any], verifier: Mapping[str, str]) -> dict[str, Any]:
    body = {
        "schema_version": PREREGISTRATION_SCHEMA,
        "run_root": RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "main_artifact_sha256": inputs["main_artifact_sha256"],
        "runtime_verification_artifact_sha256": inputs[
            "runtime_verification_artifact_sha256"
        ],
        "source_git_commit": inputs["source"]["git_commit"],
        "source_python_sha256": inputs["source"]["python_executable_sha256"],
        "strategy_sha256": inputs["source"]["strategy_sha256"],
        "producer_root_sha256": inputs["source"]["producer_root_sha256"],
        "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
        "verifier_git_commit": verifier["git_commit"],
        "verifier_script_sha256": verifier["script_sha256"],
        "verifier_script_git_blob_sha256": verifier["script_git_blob_sha256"],
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
            "automatic_trading_authority": False,
        },
        "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
    }
    raw = _canonical_bytes(body) + b"\n"
    path = _safe_child(inputs["run_root"], PREREGISTRATION_NAME, "preregistration")
    _write_once(path, raw, "preregistration")
    return {"path": path, "sha256": _sha256_bytes(raw), "document": body}


def preflight(source_root: str, expected_verifier_commit: str) -> dict[str, Any]:
    _assert_replay_plan()
    verifier = _verifier_identity(expected_verifier_commit)
    source = _source_identity(source_root)
    inputs = _load_completed_run(source)
    return {"verifier": verifier, "inputs": inputs}


def _new_claim(inputs: Mapping[str, Any], preregistration: Mapping[str, Any]) -> bytes:
    body = {
        "schema_version": STATUS_SCHEMA,
        "kind": "probability_budget_independent_verification_claim",
        "nonce": secrets.token_hex(16),
        "pid": os.getpid(),
        "started_at_utc": _utc_now(),
        "completion_sha256": inputs["completion_sha256"],
        "preregistration_sha256": preregistration["sha256"],
    }
    return _canonical_bytes(body) + b"\n"


def _remove_owned_claim(path: Path, raw: bytes) -> None:
    try:
        if path.exists() and _read_bounded(path, "verification claim", maximum=len(raw)) == raw:
            path.unlink()
    except OSError:
        return


def _run_isolated_replay(inputs: Mapping[str, Any]) -> dict[str, Any]:
    run_root = Path(inputs["run_root"])
    source = inputs["source"]
    result_path = _safe_child(run_root, REPLAY_RESULT_NAME, "independent replay result")
    stdout_path = _safe_child(run_root, REPLAY_STDOUT_NAME, "independent replay stdout")
    stderr_path = _safe_child(run_root, REPLAY_STDERR_NAME, "independent replay stderr")
    if any(path.exists() for path in (result_path, stdout_path, stderr_path)):
        raise IndependentVerificationError("independent replay output already exists")
    scratch_root = _safe_child(run_root, SCRATCH_ROOT_NAME, "independent replay scratch root")
    scratch_root.mkdir(exist_ok=False)
    _assert_no_reparse(scratch_root, "independent replay scratch root")
    command = [
        str(source["python_executable"]),
        "-I",
        "-c",
        ISOLATED_REPLAY_DRIVER,
        str(source["root"]),
        str(scratch_root),
        str(result_path),
        _canonical_bytes(REPLAY_PLAN).decode("utf-8"),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=source["root"],
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            shell=False,
            creationflags=creationflags,
        )
        exit_code = process.wait()
    if exit_code != 0:
        raise IndependentVerificationError("independent replay returned nonzero")
    replay = _read_object(result_path, "independent replay result")
    return {
        "result": replay,
        "result_path": result_path,
        "stdout": {"path": stdout_path.name, "bytes": stdout_path.stat().st_size, "sha256": _sha256_file(stdout_path)},
        "stderr": {"path": stderr_path.name, "bytes": stderr_path.stat().st_size, "sha256": _sha256_file(stderr_path)},
        "exit_code": exit_code,
    }


def _validate_replay(inputs: Mapping[str, Any], replay: Mapping[str, Any]) -> None:
    value = replay["result"]
    if not isinstance(value, dict):
        raise IndependentVerificationError("independent replay result is invalid")
    if set(value) != {
        "schema_version",
        "main_artifact_sha256",
        "runtime_verification_artifact_sha256",
        "verification",
        "scope",
    }:
        raise IndependentVerificationError("independent replay result fields drifted")
    verification = value.get("verification")
    if not isinstance(verification, dict) or "artifact_sha256" in verification:
        raise IndependentVerificationError("independent replay verification shape differs")
    replayed_runtime_verification = {
        **verification,
        "artifact_sha256": _require_sha256(
            value.get("runtime_verification_artifact_sha256"),
            "replayed runtime verification",
        ),
    }
    if (
        value.get("schema_version") != REPLAY_RESULT_SCHEMA
        or _require_sha256(value.get("main_artifact_sha256"), "replayed main artifact")
        != inputs["main_artifact_sha256"]
        or _require_sha256(
            value.get("runtime_verification_artifact_sha256"), "replayed runtime verification"
        )
        != inputs["runtime_verification_artifact_sha256"]
        or replayed_runtime_verification != inputs["runtime_verification"]
        or not isinstance(value.get("scope"), dict)
    ):
        raise IndependentVerificationError("independent replay identity differs")
    for field in ("development_only", "embargo_consumed", "final_oos_consumed", "production_authority"):
        expected = field == "development_only"
        if value["scope"].get(field) is not expected:
            raise IndependentVerificationError("independent replay scope is invalid")
    _verify_runtime_verification(
        replayed_runtime_verification,
        main_artifact_sha256=inputs["main_artifact_sha256"],
    )


def _publish_receipt(
    inputs: Mapping[str, Any],
    verifier: Mapping[str, str],
    preregistration: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "schema_version": RECEIPT_SCHEMA,
        "run_root": RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "preregistration_sha256": preregistration["sha256"],
        "source": {
            "git_commit": inputs["source"]["git_commit"],
            "python_executable_sha256": inputs["source"]["python_executable_sha256"],
            "strategy_sha256": inputs["source"]["strategy_sha256"],
            "producer_root_sha256": inputs["source"]["producer_root_sha256"],
        },
        "verifier": dict(verifier),
        "replay_plan_sha256": EXPECTED_REPLAY_PLAN_SHA256,
        "original": {
            "main_artifact_sha256": inputs["main_artifact_sha256"],
            "runtime_verification_artifact_sha256": inputs[
                "runtime_verification_artifact_sha256"
            ],
        },
        "independent_replay": {
            "result_file_sha256": _sha256_file(replay["result_path"]),
            "exit_code": replay["exit_code"],
            "stdout": replay["stdout"],
            "stderr": replay["stderr"],
        },
        "checks": {
            "formal_completion_contract_verified": True,
            "source_commit_and_cleanliness_verified": True,
            "producer_binding_verified": True,
            "full_isolated_replay_completed": True,
            "replayed_main_artifact_matches": True,
            "replayed_runtime_verification_matches": True,
            "point_in_time_development_scope_preserved": True,
        },
        "post_verification_authority": {
            "development_statistical_interpretation_allowed": True,
            "profile_registration_authority": False,
            "production_recommendation_authority": False,
            "automatic_trading_authority": False,
        },
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
            "automatic_trading_authority": False,
        },
        "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
    }
    receipt_sha = _sha256_bytes(_canonical_bytes(body))
    receipt = {**body, "receipt_sha256": receipt_sha}
    path = inputs["run_root"] / RECEIPT_ROOT_NAME / "sha256" / f"{receipt_sha}.json"
    raw = _canonical_bytes(receipt) + b"\n"
    _write_once(path, raw, "independent verification receipt")
    return {"path": path, "sha256": receipt_sha}


def _record_status(
    inputs: Mapping[str, Any],
    claim_raw: bytes,
    *,
    status: str,
    verified: bool,
    receipt: Mapping[str, Any] | None,
    error_type: str | None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA,
        "status": status,
        "stage": status,
        "pid": os.getpid(),
        "finished_at_utc": _utc_now(),
        "run_root": RUN_ROOT_NAME,
        "claim_sha256": _sha256_bytes(claim_raw),
        "verified": verified,
        "receipt": (
            {"path": str(receipt["path"].relative_to(inputs["run_root"])).replace("\\", "/"), "sha256": receipt["sha256"]}
            if receipt is not None
            else None
        ),
        "error_type": error_type,
        "development_statistical_interpretation_allowed": verified,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    raw = _canonical_bytes(payload) + b"\n"
    path = _safe_child(inputs["run_root"], STATUS_NAME, "independent verification status")
    _write_once(path, raw, "independent verification status")


def run(source_root: str, expected_verifier_commit: str) -> dict[str, str]:
    prepared = preflight(source_root, expected_verifier_commit)
    verifier = prepared["verifier"]
    inputs = prepared["inputs"]
    status_path = _safe_child(inputs["run_root"], STATUS_NAME, "independent verification status")
    if status_path.exists():
        raise IndependentVerificationError("independent verification is already terminal")
    preregistration = _preregistration(inputs, verifier)
    claim_path = _safe_child(inputs["run_root"], CLAIM_NAME, "independent verification claim")
    claim_raw = _new_claim(inputs, preregistration)
    _write_once(claim_path, claim_raw, "independent verification claim")
    try:
        replay = _run_isolated_replay(inputs)
        _validate_replay(inputs, replay)
        receipt = _publish_receipt(inputs, verifier, preregistration, replay)
        _record_status(
            inputs,
            claim_raw,
            status="completed",
            verified=True,
            receipt=receipt,
            error_type=None,
        )
        return {"status": "completed", "receipt_sha256": receipt["sha256"]}
    except BaseException as exc:
        _record_status(
            inputs,
            claim_raw,
            status="failed",
            verified=False,
            receipt=None,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        _remove_owned_claim(claim_path, claim_raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        default=r"E:\AI workspace\quant-signal-lkj",
    )
    parser.add_argument("--expected-verifier-commit", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.preflight:
            preflight(args.source_root, args.expected_verifier_commit)
            print("status=preflight_verified")
            return 0
        run(args.source_root, args.expected_verifier_commit)
        print("status=independently_verified")
        return 0
    except IndependentVerificationError:
        print("status=failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
