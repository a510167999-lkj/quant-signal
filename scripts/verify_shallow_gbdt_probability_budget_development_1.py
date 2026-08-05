"""Independently replay the frozen probability-budget development run.

This verifier is deliberately separate from the formal launcher.  It consumes
only the already-frozen development inputs, replays the complete computation in
an isolated Python process, and publishes hashes plus boolean checks.  It never
opens embargo/final-OOS/production authority and never prints strategy output.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[1]
SCRIPT_GIT_PATH = "scripts/verify_shallow_gbdt_probability_budget_development_1.py"
RUN_ROOT_BINDER_GIT_PATH = (
    "scripts/repair_shallow_gbdt_probability_budget_independent_verification.py"
)
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
RELOCATION_EQUIVALENCE_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "relocation-equivalence/v1"
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
MAX_SNAPSHOT_FILES = 200_000
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024 * 1024
REPARSE_POINT_ATTRIBUTE = 0x400
RUNTIME_PROBE_SCHEMA = "probability-budget-independent-runtime-probe/v1"
PYTHON_RUNTIME_PROBE_SCHEMA = (
    "probability-budget-independent-python-runtime-probe/v1"
)
XGBOOST_RELOCATION_SCHEMA = (
    "probability-budget-independent-xgboost-relocation/v2"
)
XGBOOST_RELOCATION_PUBLIC_FIELDS = frozenset(
    {
        "schema_version",
        "formal_producer_root_sha256",
        "snapshot_producer_root_sha256",
        "formal_xgboost_library_sha256",
        "snapshot_xgboost_library_sha256",
        "xgboost_library_relative",
        "normalized_shallow_binding",
        "normalized_probability_binding",
    }
)
SNAPSHOT_SCHEMA = "probability-budget-independent-execution-snapshot/v1"
SAFE_SNAPSHOT_COMPONENT = re.compile(r"^(?!\.\.?$)[A-Za-z0-9_.-]+$")

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
RESULT_BUNDLE_SIDECAR_NAMES = frozenset({"features", "models", "execution", "selection"})

ISOLATED_REPLAY_DRIVER = r'''
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

code_root = Path(sys.argv[1]).resolve()
data_root = Path(sys.argv[2]).resolve()
runtime_root = Path(sys.argv[3]).resolve()
scratch_root = Path(sys.argv[4]).resolve()
result_path = Path(sys.argv[5]).resolve()
relocation_descriptor_path = Path(sys.argv[6]).resolve()
plan = json.loads(sys.argv[7])
stdlib_paths = [
    value
    for value in sys.path
    if value and "site-packages" not in value.lower()
]
sys.path[:] = [str(code_root), str(runtime_root), *stdlib_paths]
os.chdir(code_root)

import xgboost

xgboost_build_info = dict(xgboost.build_info())
xgboost_library = Path(str(xgboost_build_info.get("libxgboost") or "")).resolve()
relocation_descriptor = json.loads(relocation_descriptor_path.read_text(encoding="utf-8"))
descriptor_unsigned = dict(relocation_descriptor)
descriptor_sha256 = str(descriptor_unsigned.pop("descriptor_sha256", "") or "")
if (
    set(descriptor_unsigned)
    != {
        "schema_version",
        "formal_producer_root_sha256",
        "snapshot_producer_root_sha256",
        "formal_xgboost_library_sha256",
        "snapshot_xgboost_library_sha256",
        "xgboost_library_relative",
        "normalized_shallow_binding",
        "normalized_probability_binding",
    }
    or relocation_descriptor.get("schema_version")
    != "probability-budget-independent-xgboost-relocation/v2"
    or hashlib.sha256(
        json.dumps(
            descriptor_unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    != descriptor_sha256
):
    raise RuntimeError("isolated xgboost relocation descriptor is invalid")

def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

def normalized_binding(value, schema_version, label):
    if not isinstance(value, dict):
        raise RuntimeError(f"isolated {label} binding is invalid")
    body = dict(value)
    root_sha256 = str(body.pop("root_sha256", "") or "")
    if (
        body.get("schema_version") != schema_version
        or len(root_sha256) != 64
        or any(character not in "0123456789abcdef" for character in root_sha256)
        or hashlib.sha256(canonical_bytes(body)).hexdigest() != root_sha256
    ):
        raise RuntimeError(f"isolated {label} binding is invalid")
    return {**body, "root_sha256": root_sha256}

normalized_shallow_binding = normalized_binding(
    descriptor_unsigned["normalized_shallow_binding"],
    "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1",
    "shallow producer",
)
normalized_probability_binding = normalized_binding(
    descriptor_unsigned["normalized_probability_binding"],
    "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1",
    "probability producer",
)
normalized_build_info = normalized_shallow_binding.get("xgboost_build_info")
if (
    not isinstance(normalized_build_info, dict)
    or normalized_build_info.get("libxgboost") != "<relocated-xgboost-library>"
    or normalized_probability_binding.get("base_shallow_gbdt_producer_root_sha256")
    != normalized_shallow_binding["root_sha256"]
):
    raise RuntimeError("isolated normalized producer relocation is invalid")
try:
    xgboost_library_relative = xgboost_library.relative_to(runtime_root)
except ValueError as exc:
    raise RuntimeError("isolated xgboost library is outside the runtime snapshot") from exc
if (
    len(xgboost_library_relative.parts) != 3
    or xgboost_library_relative.parts[:2] != ("xgboost", "lib")
    or xgboost_library_relative.name.lower()
    not in {"xgboost.dll", "libxgboost.dll"}
):
    raise RuntimeError("isolated xgboost library layout is invalid")
if (
    xgboost_library_relative.as_posix()
    != relocation_descriptor["xgboost_library_relative"]
    or hashlib.sha256(xgboost_library.read_bytes()).hexdigest()
    != relocation_descriptor["snapshot_xgboost_library_sha256"]
):
    raise RuntimeError("isolated xgboost relocation runtime is invalid")
from app import audited_pit_continuous_ridge_oof as continuous_ridge
continuous_ridge._shallow_gbdt_producer_binding = (
    lambda: copy.deepcopy(normalized_shallow_binding)
)
continuous_ridge._shallow_gbdt_probability_budget_producer_binding = (
    lambda: copy.deepcopy(normalized_probability_binding)
)
from app.audited_pit_continuous_ridge_oof import (
    run_audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_rolling_oof,
)
from app.config import Settings

inputs = plan["inputs"]
partition = plan["development_partition"]
output_dir = scratch_root / "bundle"
output_dir.mkdir()
result = run_audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_rolling_oof(
    settings=Settings(),
    audited_pit_universe_path=data_root / inputs["audited_pit_universe_path"],
    expected_coverage_audit_sha256=inputs["expected_coverage_audit_sha256"],
    expected_artifact_root_sha256=inputs["expected_artifact_root_sha256"],
    temporal_contract_path=data_root / inputs["temporal_contract_path"],
    expected_temporal_contract_sha256=inputs["expected_temporal_contract_sha256"],
    security_code_transition_evidence_root=(
        data_root / inputs["security_code_transition_evidence_root"]
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

PYTHON_RUNTIME_PROBE_DRIVER = r'''
import json
import sys
import sysconfig
from pathlib import Path

print(json.dumps({
    "schema_version": "probability-budget-independent-python-runtime-probe/v1",
    "base_python_executable": str(
        Path(getattr(sys, "_base_executable", sys.executable)).absolute()
    ),
    "stdlib_root": str(Path(sysconfig.get_path("stdlib")).absolute()),
    "python_abi": {
        "implementation": sys.implementation.name,
        "cache_tag": str(sys.implementation.cache_tag or ""),
        "version": list(sys.version_info[:3]),
    },
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
'''

RAW_PRODUCER_PROBE_DRIVER = r'''
import json
import sys
from pathlib import Path

code_root = Path(sys.argv[1]).resolve()
runtime_root = Path(sys.argv[2]).resolve()
stdlib_paths = [
    value
    for value in sys.path
    if value and "site-packages" not in value.lower()
]
sys.path[:] = [str(code_root), str(runtime_root), *stdlib_paths]

import xgboost
from app import audited_pit_shallow_gbdt_probability_budget as probability_budget
from app import audited_pit_continuous_ridge_oof as continuous_ridge

variant = continuous_ridge.resolve_ranked_liquidity_run_variant(
    probability_budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
)
print(json.dumps({
    "strategy_sha256": probability_budget._SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256,
    "probability_binding": variant["producer_binding"](),
    "shallow_binding": continuous_ridge._shallow_gbdt_producer_binding(),
    "ridge_binding": continuous_ridge._producer_binding(artifact_version=3),
    "xgboost_build_info": dict(xgboost.build_info()),
}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
'''


RUNTIME_PROBE_DRIVER = r'''
import importlib.metadata
import json
import sys
from pathlib import Path

code_root = Path(sys.argv[1]).resolve()
site_root = Path(sys.argv[2]).resolve()
stdlib_paths = [
    value
    for value in sys.path
    if value and "site-packages" not in value.lower()
]
sys.path[:] = [str(code_root), str(site_root), *stdlib_paths]

requested_distributions = set()
original_distribution = importlib.metadata.distribution
original_version = importlib.metadata.version

def tracked_distribution(name):
    requested_distributions.add(str(name))
    return original_distribution(name)

def tracked_version(name):
    requested_distributions.add(str(name))
    return original_version(name)

importlib.metadata.distribution = tracked_distribution
importlib.metadata.version = tracked_version

from app import audited_pit_shallow_gbdt_probability_budget as probability_budget
from app import audited_pit_continuous_ridge_oof as continuous_ridge

continuous_ridge.package_version = tracked_version

variant = continuous_ridge.resolve_ranked_liquidity_run_variant(
    probability_budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
)
variant["producer_binding"]()

top_levels = set()
for module in tuple(sys.modules.values()):
    path_value = getattr(module, "__file__", None)
    if not path_value:
        continue
    try:
        relative = Path(path_value).resolve().relative_to(site_root)
    except (OSError, ValueError):
        continue
    if relative.parts:
        top_levels.add(relative.parts[0])

distributions = set()
metadata_paths = set()
package_map = importlib.metadata.packages_distributions()
for top_level in top_levels:
    for distribution_name in package_map.get(top_level, ()):
        distributions.add(distribution_name)
distributions.update(requested_distributions)
for distribution_name in distributions:
    distribution = original_distribution(distribution_name)
    distribution_path = getattr(distribution, "_path", None)
    if distribution_path is None:
        raise RuntimeError("distribution metadata path is unavailable")
    try:
        relative = Path(distribution_path).resolve().relative_to(site_root)
    except (OSError, ValueError):
        raise RuntimeError("distribution metadata is outside site root")
    metadata_paths.add(relative.as_posix())

print(json.dumps({
    "schema_version": "probability-budget-independent-runtime-probe/v1",
    "top_levels": sorted(top_levels),
    "metadata_paths": sorted(metadata_paths),
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
'''


class IndependentVerificationError(RuntimeError):
    """Raised when the formal development result cannot be independently verified."""


def _held_run_root(
    run_root: Path,
    *,
    binder_type: type[Any],
    expected_chain: tuple[tuple[int, int], ...] | None = None,
) -> Any:
    try:
        return binder_type(run_root, expected_chain=expected_chain)
    except Exception as exc:
        raise IndependentVerificationError(
            "independent publication handle binder is unavailable"
        ) from exc


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


def _frozen_run_root_binder(
    expected_commit: str,
    expected_blob_sha256: str,
) -> type[Any]:
    expected = _validate_expected_commit(expected_commit)
    expected_digest = _require_sha256(
        expected_blob_sha256,
        "publication handle binder",
    )
    completed = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "show", f"{expected}:{RUN_ROOT_BINDER_GIT_PATH}"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or _sha256_bytes(completed.stdout) != expected_digest:
        raise IndependentVerificationError("frozen publication handle binder is unavailable")
    try:
        tree = ast.parse(
            completed.stdout.decode("utf-8"),
            filename=f"<frozen:{expected}:{RUN_ROOT_BINDER_GIT_PATH}>",
        )
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise IndependentVerificationError("frozen publication handle binder is invalid") from exc
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_HeldWindowsRunRoot"
    ]
    if len(classes) != 1:
        raise IndependentVerificationError("frozen publication handle binder is invalid")
    frozen_tree = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            classes[0],
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(frozen_tree)
    binder_helpers = SimpleNamespace(
        MAX_JSON_BYTES=MAX_JSON_BYTES,
        _canonical_bytes=_canonical_bytes,
        _sha256_bytes=_sha256_bytes,
    )
    namespace: dict[str, Any] = {
        "__name__": "_frozen_probability_budget_run_root_binder",
        "Any": Any,
        "Path": Path,
        "RepairVerificationError": IndependentVerificationError,
        "RECEIPT_ROOT_NAME": RECEIPT_ROOT_NAME,
        "hmac": hmac,
        "os": os,
        "re": re,
        "stat": stat,
        "verifier": binder_helpers,
    }
    try:
        exec(
            compile(
                frozen_tree,
                f"<frozen:{expected}:{RUN_ROOT_BINDER_GIT_PATH}>",
                "exec",
            ),
            namespace,
            namespace,
        )
    except Exception as exc:
        raise IndependentVerificationError("frozen publication handle binder is invalid") from exc
    binder_type = namespace.get("_HeldWindowsRunRoot")
    required = {
        "__enter__",
        "__exit__",
        "assert_bound",
        "mark_terminal_status_committed",
        "root_file_exists",
        "write_root_once",
        "read_root_bounded",
    }
    if not isinstance(binder_type, type) or any(
        not callable(getattr(binder_type, name, None)) for name in required
    ):
        raise IndependentVerificationError("frozen publication handle binder is invalid")
    return binder_type


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


def _safe_descendant(root: Path, relative_name: str, label: str) -> Path:
    _assert_no_reparse(root, f"{label} root")
    relative = _snapshot_relative(relative_name, label)
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        _assert_no_reparse(current, f"{label} parent")
        if not current.is_dir():
            raise IndependentVerificationError(f"{label} parent is missing")
    return current / relative.name


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
    binder_path = PROJECT_ROOT / Path(RUN_ROOT_BINDER_GIT_PATH)
    binder_blob = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "show", f"{expected}:{RUN_ROOT_BINDER_GIT_PATH}"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if binder_blob.returncode != 0 or binder_blob.stdout != _normalized_source_bytes(
        binder_path
    ):
        raise IndependentVerificationError("publication handle binder does not match its commit")
    return {
        "git_commit": expected,
        "script_sha256": _sha256_file(SCRIPT_PATH),
        "script_git_blob_sha256": _sha256_bytes(blob.stdout),
        "run_root_binder_git_blob_sha256": _sha256_bytes(binder_blob.stdout),
    }


def _minimal_child_environment() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("COMSPEC", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "WINDIR")
        if os.environ.get(key)
    }


def _snapshot_relative(value: str, label: str) -> Path:
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(not _safe_snapshot_name(part) for part in candidate.parts)
    ):
        raise IndependentVerificationError(f"{label} path is invalid")
    return Path(*candidate.parts)


def _ignored_snapshot_relative_prefixes(
    values: frozenset[str],
    label: str,
) -> frozenset[str]:
    return frozenset(
        _snapshot_relative(value, f"{label} ignored path").as_posix()
        for value in values
    )


def _is_ignored_snapshot_relative(
    relative: Path,
    ignored_relative_prefixes: frozenset[str],
) -> bool:
    if not relative.parts:
        return False
    normalized = relative.as_posix()
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in ignored_relative_prefixes
    )


def _safe_snapshot_name(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _manifest_tree(
    root: Path,
    label: str,
    *,
    ignored_root_names: frozenset[str] = frozenset(),
    ignored_relative_prefixes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    _assert_no_reparse(root, label)
    if not root.is_dir():
        raise IndependentVerificationError(f"{label} is not a directory")
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    ignored_prefixes = _ignored_snapshot_relative_prefixes(
        ignored_relative_prefixes,
        label,
    )

    def visit(path: Path, relative: Path) -> None:
        nonlocal total_bytes
        if _is_ignored_snapshot_relative(relative, ignored_prefixes):
            return
        _assert_no_reparse(path, label)
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise IndependentVerificationError(f"{label} is unavailable") from exc
        normalized = relative.as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            if normalized:
                entries.append({"kind": "directory", "path": normalized})
            try:
                children = sorted(path.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                raise IndependentVerificationError(f"{label} is unreadable") from exc
            for child in children:
                if not relative.parts and child.name in ignored_root_names:
                    continue
                if not _safe_snapshot_name(child.name):
                    raise IndependentVerificationError(f"{label} contains an unsafe name")
                child_relative = relative / child.name
                if _is_ignored_snapshot_relative(child_relative, ignored_prefixes):
                    continue
                visit(child, child_relative)
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise IndependentVerificationError(f"{label} contains a non-regular entry")
        if len(entries) >= MAX_SNAPSHOT_FILES:
            raise IndependentVerificationError(f"{label} exceeds its file bound")
        total_bytes += int(metadata.st_size)
        if total_bytes > MAX_SNAPSHOT_BYTES:
            raise IndependentVerificationError(f"{label} exceeds its byte bound")
        before = (metadata.st_size, metadata.st_mtime_ns, metadata.st_ino)
        digest = _sha256_file(path)
        try:
            after_metadata = os.lstat(path)
        except OSError as exc:
            raise IndependentVerificationError(f"{label} changed while being read") from exc
        after = (after_metadata.st_size, after_metadata.st_mtime_ns, after_metadata.st_ino)
        if before != after:
            raise IndependentVerificationError(f"{label} changed while being read")
        entries.append(
            {
                "kind": "file",
                "path": normalized,
                "bytes": int(metadata.st_size),
                "sha256": digest,
            }
        )

    visit(root, Path())
    body = {"entries": entries}
    return {
        "sha256": _sha256_bytes(_canonical_bytes(body)),
        "entries": entries,
        "file_count": sum(entry["kind"] == "file" for entry in entries),
    }


def _copy_snapshot_path(
    source: Path,
    target: Path,
    label: str,
    *,
    ignored_relative_prefixes: frozenset[str] = frozenset(),
    relative: Path = Path(),
) -> None:
    ignored_prefixes = _ignored_snapshot_relative_prefixes(
        ignored_relative_prefixes,
        label,
    )
    if _is_ignored_snapshot_relative(relative, ignored_prefixes):
        return
    _assert_no_reparse(source, label)
    try:
        metadata = os.lstat(source)
    except OSError as exc:
        raise IndependentVerificationError(f"{label} is unavailable") from exc
    if stat.S_ISDIR(metadata.st_mode):
        try:
            target.mkdir(parents=True, exist_ok=False)
            children = sorted(source.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise IndependentVerificationError(f"{label} cannot be snapshotted") from exc
        for child in children:
            if not _safe_snapshot_name(child.name):
                raise IndependentVerificationError(f"{label} contains an unsafe name")
            child_relative = relative / child.name
            if _is_ignored_snapshot_relative(child_relative, ignored_prefixes):
                continue
            _copy_snapshot_path(
                child,
                target / child.name,
                label,
                ignored_relative_prefixes=ignored_prefixes,
                relative=child_relative,
            )
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise IndependentVerificationError(f"{label} is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    before = (metadata.st_size, metadata.st_mtime_ns, metadata.st_ino)
    try:
        with source.open("rb") as read_handle, target.open("xb") as write_handle:
            while chunk := read_handle.read(1024 * 1024):
                write_handle.write(chunk)
            write_handle.flush()
            os.fsync(write_handle.fileno())
    except OSError as exc:
        raise IndependentVerificationError(f"{label} cannot be snapshotted") from exc
    try:
        after_metadata = os.lstat(source)
    except OSError as exc:
        raise IndependentVerificationError(f"{label} changed while being snapshotted") from exc
    after = (after_metadata.st_size, after_metadata.st_mtime_ns, after_metadata.st_ino)
    if before != after or _sha256_file(source) != _sha256_file(target):
        raise IndependentVerificationError(f"{label} changed while being snapshotted")


def _expected_source_tree(source_root: Path) -> tuple[tuple[Path, str], ...]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            EXPECTED_SOURCE_COMMIT,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise IndependentVerificationError("frozen source tree is unavailable")
    try:
        values = []
        for item in completed.stdout.split(b"\0"):
            if not item:
                continue
            header, raw_path = item.split(b"\t", 1)
            mode, kind, raw_blob = header.split(b" ", 2)
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise IndependentVerificationError("frozen source tree entry is invalid")
            relative = _snapshot_relative(
                raw_path.decode("utf-8"),
                "frozen source tree path",
            )
            blob_sha1 = raw_blob.decode("ascii").lower()
            if not HEX_COMMIT.fullmatch(blob_sha1):
                raise IndependentVerificationError("frozen source tree blob is invalid")
            values.append((relative, blob_sha1))
    except (UnicodeDecodeError, ValueError) as exc:
        raise IndependentVerificationError("frozen source tree entry is invalid") from exc
    entries = tuple(values)
    if (
        not entries
        or len(entries) > MAX_SNAPSHOT_FILES
        or len({path for path, _blob in entries}) != len(entries)
        or tuple(sorted(entries, key=lambda item: item[0].as_posix())) != entries
        or any(path.name.lower() == ".env" for path, _blob in entries)
    ):
        raise IndependentVerificationError("frozen source tree entries are invalid")
    return entries


def _git_filtered_blob_sha1(
    source_root: Path,
    relative: Path,
    source: Path,
    label: str,
) -> str:
    try:
        with source.open("rb") as handle:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "hash-object",
                    f"--path={relative.as_posix()}",
                    "--stdin",
                ],
                check=False,
                stdin=handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
    except OSError as exc:
        raise IndependentVerificationError(f"{label} is unreadable") from exc
    try:
        observed = completed.stdout.decode("ascii").strip().lower()
    except UnicodeDecodeError as exc:
        raise IndependentVerificationError(f"{label} Git blob is invalid") from exc
    if completed.returncode != 0 or not HEX_COMMIT.fullmatch(observed):
        raise IndependentVerificationError(f"{label} Git blob is invalid")
    return observed


def _tracked_source_manifest(
    root: Path,
    tree_entries: tuple[tuple[Path, str], ...],
    label: str,
    *,
    git_filter_root: Path | None = None,
) -> dict[str, Any]:
    _assert_no_reparse(root, label)
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for relative, expected_blob_sha1 in tree_entries:
        source = root / relative
        _regular_file(source, label)
        try:
            before_metadata = os.lstat(source)
        except OSError as exc:
            raise IndependentVerificationError(f"{label} is unavailable") from exc
        if len(entries) >= MAX_SNAPSHOT_FILES:
            raise IndependentVerificationError(f"{label} exceeds its file bound")
        total_bytes += int(before_metadata.st_size)
        if total_bytes > MAX_SNAPSHOT_BYTES:
            raise IndependentVerificationError(f"{label} exceeds its byte bound")
        before_digest = _sha256_file(source)
        if (
            git_filter_root is not None
            and _git_filtered_blob_sha1(
                git_filter_root,
                relative,
                source,
                label,
            )
            != expected_blob_sha1
        ):
            raise IndependentVerificationError(
                f"{label} differs from its frozen Git blob"
            )
        digest = _sha256_file(source)
        try:
            after_metadata = os.lstat(source)
        except OSError as exc:
            raise IndependentVerificationError(f"{label} changed while being read") from exc
        before = (
            before_metadata.st_size,
            before_metadata.st_mtime_ns,
            before_metadata.st_ino,
        )
        after = (
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
            after_metadata.st_ino,
        )
        if before != after or before_digest != digest:
            raise IndependentVerificationError(f"{label} changed while being read")
        entries.append(
            {
                "kind": "file",
                "path": relative.as_posix(),
                "bytes": int(before_metadata.st_size),
                "sha256": digest,
                "git_blob_sha1": expected_blob_sha1,
            }
        )
    body = {"entries": entries}
    return {
        "sha256": _sha256_bytes(_canonical_bytes(body)),
        "entries": entries,
        "file_count": len(entries),
    }


def _overlay_snapshot_file(source: Path, target: Path, label: str) -> None:
    _regular_file(target, label)
    _assert_no_reparse(target.parent, label)
    try:
        target.unlink()
    except OSError as exc:
        raise IndependentVerificationError(f"{label} cannot be prepared") from exc
    _copy_snapshot_path(source, target, label)


def _runtime_dependency_plan(
    code_root: Path,
    python_executable: Path,
    runtime_site_root: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(python_executable),
            "-I",
            "-S",
            "-B",
            "-c",
            RUNTIME_PROBE_DRIVER,
            str(code_root),
            str(runtime_site_root),
        ],
        check=False,
        cwd=code_root,
        env=_minimal_child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_PROBE_BYTES:
        raise IndependentVerificationError("runtime dependency probe failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError("runtime dependency probe is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "top_levels", "metadata_paths"}
        or value.get("schema_version") != RUNTIME_PROBE_SCHEMA
        or not isinstance(value.get("top_levels"), list)
        or not isinstance(value.get("metadata_paths"), list)
    ):
        raise IndependentVerificationError("runtime dependency probe is invalid")
    top_levels = tuple(sorted({str(item) for item in value["top_levels"]}))
    metadata_paths = tuple(sorted({str(item) for item in value["metadata_paths"]}))
    if (
        not top_levels
        or any(not SAFE_SNAPSHOT_COMPONENT.fullmatch(item) for item in top_levels)
        or any(_snapshot_relative(item, "runtime metadata") is None for item in metadata_paths)
    ):
        raise IndependentVerificationError("runtime dependency probe is invalid")
    body = {"top_levels": list(top_levels), "metadata_paths": list(metadata_paths)}
    return {**body, "sha256": _sha256_bytes(_canonical_bytes(body))}


def _copy_runtime_snapshot(
    runtime_site_root: Path,
    runtime_root: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    _assert_no_reparse(runtime_site_root, "runtime site-packages root")
    if not runtime_site_root.is_dir():
        raise IndependentVerificationError("runtime site-packages root is missing")
    runtime_root.mkdir(parents=True, exist_ok=False)
    copied: set[Path] = set()

    def copy_candidate(relative: Path, label: str, *, required: bool) -> bool:
        if relative in copied:
            return True
        candidate = runtime_site_root / relative
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            if required:
                raise IndependentVerificationError(f"{label} is missing")
            return False
        except OSError as exc:
            raise IndependentVerificationError(f"{label} is unavailable") from exc
        _copy_snapshot_path(candidate, runtime_root / relative, label)
        copied.add(relative)
        return True

    for top_level in plan["top_levels"]:
        root = _snapshot_relative(str(top_level), "runtime dependency")
        found = False
        for relative in (
            root,
            Path(f"{root.name}.py"),
            Path(f"{root.name}.pyd"),
            Path(f"{root.name}.dll"),
            Path(f"{root.name}.libs"),
        ):
            found = copy_candidate(relative, "runtime dependency", required=False) or found
        if not found:
            raise IndependentVerificationError("runtime dependency is missing")
    for metadata_path in plan["metadata_paths"]:
        copy_candidate(
            _snapshot_relative(str(metadata_path), "runtime metadata"),
            "runtime metadata",
            required=True,
        )
    return _manifest_tree(runtime_root, "runtime snapshot")


def _stdlib_manifest(stdlib_root: Path) -> dict[str, Any]:
    return _manifest_tree(
        stdlib_root,
        "frozen Python standard library",
        ignored_root_names=frozenset({"site-packages", "__pycache__"}),
    )


def _run_isolated_json_probe(
    code_root: Path,
    python_executable: Path,
    driver: str,
    arguments: tuple[Path, ...],
    label: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(python_executable),
            "-I",
            "-S",
            "-B",
            "-c",
            driver,
            str(code_root),
            *(str(argument) for argument in arguments),
        ],
        check=False,
        cwd=code_root,
        env=_minimal_child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_PROBE_BYTES:
        raise IndependentVerificationError(f"{label} failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise IndependentVerificationError(f"{label} is invalid")
    return value


def _binding_identity(binding: object, label: str) -> tuple[dict[str, Any], str]:
    if not isinstance(binding, dict):
        raise IndependentVerificationError(f"{label} is invalid")
    body = dict(binding)
    root_sha256 = _require_sha256(body.pop("root_sha256", None), label)
    schema_version = body.get("schema_version")
    if schema_version == "audited-pit-ranked-liquidity-producer/v3":
        root_body = dict(body)
        root_body.pop("schema_version")
    elif schema_version in {
        "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1",
        "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1",
    }:
        root_body = body
    else:
        raise IndependentVerificationError(f"{label} schema is invalid")
    if root_sha256 != _sha256_bytes(_canonical_bytes(root_body)):
        raise IndependentVerificationError(f"{label} hash is invalid")
    return body, root_sha256


def _raw_producer_probe(
    code_root: Path,
    python_executable: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    value = _run_isolated_json_probe(
        code_root,
        python_executable,
        RAW_PRODUCER_PROBE_DRIVER,
        (runtime_root,),
        "raw producer probe",
    )
    if set(value) != {
        "strategy_sha256",
        "probability_binding",
        "shallow_binding",
        "ridge_binding",
        "xgboost_build_info",
    }:
        raise IndependentVerificationError("raw producer probe is invalid")
    _require_sha256(value.get("strategy_sha256"), "raw source strategy")
    for field in ("probability_binding", "shallow_binding", "ridge_binding"):
        _binding_identity(value.get(field), f"raw {field}")
    if not isinstance(value.get("xgboost_build_info"), dict):
        raise IndependentVerificationError("raw xgboost build info is invalid")
    return value


def _xgboost_library_relative(
    value: object,
    runtime_root: Path,
    label: str,
) -> tuple[Path, Path, str]:
    if not isinstance(value, Mapping):
        raise IndependentVerificationError(f"{label} is invalid")
    library_value = value.get("libxgboost")
    if not isinstance(library_value, str) or not library_value:
        raise IndependentVerificationError(f"{label} is invalid")
    library = Path(library_value).absolute().resolve()
    try:
        relative = library.relative_to(runtime_root)
    except ValueError as exc:
        raise IndependentVerificationError(f"{label} is outside its runtime") from exc
    if (
        len(relative.parts) != 3
        or relative.parts[:2] != ("xgboost", "lib")
        or relative.name.lower() not in {"xgboost.dll", "libxgboost.dll"}
    ):
        raise IndependentVerificationError(f"{label} layout is invalid")
    _regular_file(library, label)
    return relative, library, library_value


def _normalized_shallow_binding(
    body: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    normalized = json.loads(_canonical_bytes(dict(body)).decode("utf-8"))
    runtime = normalized.get("xgboost_build_info")
    if not isinstance(runtime, dict):
        raise IndependentVerificationError(f"{label} is invalid")
    runtime["libxgboost"] = "<relocated-xgboost-library>"
    result = {
        **normalized,
        "root_sha256": _sha256_bytes(_canonical_bytes(normalized)),
    }
    _binding_identity(result, label)
    return result


def _normalized_probability_binding(
    body: Mapping[str, Any],
    normalized_shallow_root_sha256: str,
    label: str,
) -> dict[str, Any]:
    normalized = json.loads(_canonical_bytes(dict(body)).decode("utf-8"))
    if not HEX_SHA256.fullmatch(normalized_shallow_root_sha256):
        raise IndependentVerificationError(f"{label} is invalid")
    normalized["base_shallow_gbdt_producer_root_sha256"] = normalized_shallow_root_sha256
    result = {
        **normalized,
        "root_sha256": _sha256_bytes(_canonical_bytes(normalized)),
    }
    _binding_identity(result, label)
    return result


def _build_xgboost_relocation_descriptor(
    formal: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    formal_runtime_root: Path,
    snapshot_runtime_root: Path,
) -> dict[str, Any]:
    formal_strategy = _require_sha256(
        formal.get("strategy_sha256"),
        "formal source strategy",
    )
    snapshot_strategy = _require_sha256(
        snapshot.get("strategy_sha256"),
        "snapshot source strategy",
    )
    if (
        formal_strategy != EXPECTED_STRATEGY_SHA256
        or snapshot_strategy != formal_strategy
    ):
        raise IndependentVerificationError("source strategy binding drifted")
    formal_probability, formal_probability_root = _binding_identity(
        formal.get("probability_binding"),
        "formal probability binding",
    )
    snapshot_probability, snapshot_probability_root = _binding_identity(
        snapshot.get("probability_binding"),
        "snapshot probability binding",
    )
    if formal_probability_root != EXPECTED_PRODUCER_ROOT_SHA256:
        raise IndependentVerificationError("formal producer binding drifted")
    formal_shallow, formal_shallow_root = _binding_identity(
        formal.get("shallow_binding"),
        "formal shallow binding",
    )
    snapshot_shallow, snapshot_shallow_root = _binding_identity(
        snapshot.get("shallow_binding"),
        "snapshot shallow binding",
    )
    formal_ridge, formal_ridge_root = _binding_identity(
        formal.get("ridge_binding"),
        "formal ridge binding",
    )
    snapshot_ridge, snapshot_ridge_root = _binding_identity(
        snapshot.get("ridge_binding"),
        "snapshot ridge binding",
    )
    if (
        formal_ridge != snapshot_ridge
        or formal_ridge_root != snapshot_ridge_root
        or formal_shallow.get("base_ranked_liquidity_producer_root_sha256")
        != formal_ridge_root
        or snapshot_shallow.get("base_ranked_liquidity_producer_root_sha256")
        != snapshot_ridge_root
        or formal_probability.get("base_shallow_gbdt_producer_root_sha256")
        != formal_shallow_root
        or snapshot_probability.get("base_shallow_gbdt_producer_root_sha256")
        != snapshot_shallow_root
    ):
        raise IndependentVerificationError("producer dependency binding drifted")
    formal_runtime = formal_shallow.get("xgboost_build_info")
    snapshot_runtime = snapshot_shallow.get("xgboost_build_info")
    if (
        formal_runtime != formal.get("xgboost_build_info")
        or snapshot_runtime != snapshot.get("xgboost_build_info")
    ):
        raise IndependentVerificationError("xgboost build info binding drifted")
    formal_relative, formal_library, _formal_library_value = (
        _xgboost_library_relative(
            formal_runtime,
            formal_runtime_root,
            "formal xgboost runtime library",
        )
    )
    snapshot_relative, snapshot_library, _snapshot_library_value = (
        _xgboost_library_relative(
            snapshot_runtime,
            snapshot_runtime_root,
            "snapshot xgboost runtime library",
        )
    )
    formal_shallow_normalized = _normalized_shallow_binding(
        formal_shallow,
        "formal shallow binding",
    )
    snapshot_shallow_normalized = _normalized_shallow_binding(
        snapshot_shallow,
        "snapshot shallow binding",
    )
    formal_probability_normalized = _normalized_probability_binding(
        formal_probability,
        formal_shallow_normalized["root_sha256"],
        "formal probability binding",
    )
    snapshot_probability_normalized = _normalized_probability_binding(
        snapshot_probability,
        snapshot_shallow_normalized["root_sha256"],
        "snapshot probability binding",
    )
    formal_library_sha256 = _sha256_file(formal_library)
    snapshot_library_sha256 = _sha256_file(snapshot_library)
    if (
        formal_relative != snapshot_relative
        or formal_shallow_normalized != snapshot_shallow_normalized
        or formal_probability_normalized != snapshot_probability_normalized
        or formal_library_sha256 != snapshot_library_sha256
    ):
        raise IndependentVerificationError("xgboost relocation equivalence failed")
    public = {
        "schema_version": XGBOOST_RELOCATION_SCHEMA,
        "formal_producer_root_sha256": formal_probability_root,
        "snapshot_producer_root_sha256": snapshot_probability_root,
        "formal_xgboost_library_sha256": formal_library_sha256,
        "snapshot_xgboost_library_sha256": snapshot_library_sha256,
        "xgboost_library_relative": formal_relative.as_posix(),
        "normalized_shallow_binding": formal_shallow_normalized,
        "normalized_probability_binding": formal_probability_normalized,
    }
    return {
        **public,
        "descriptor_sha256": _sha256_bytes(_canonical_bytes(public)),
    }


def _read_xgboost_relocation_descriptor(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    raw = _read_bounded(path, label, maximum=MAX_PROBE_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(f"{label} is invalid") from exc
    if not isinstance(value, dict) or raw != _canonical_bytes(value) + b"\n":
        raise IndependentVerificationError(f"{label} is invalid")
    unsigned = dict(value)
    descriptor_sha256 = _require_sha256(
        unsigned.pop("descriptor_sha256", None),
        label,
    )
    if (
        set(unsigned) != XGBOOST_RELOCATION_PUBLIC_FIELDS
        or unsigned.get("schema_version") != XGBOOST_RELOCATION_SCHEMA
        or descriptor_sha256 != _sha256_bytes(_canonical_bytes(unsigned))
    ):
        raise IndependentVerificationError(f"{label} is invalid")
    for field in (
        "formal_producer_root_sha256",
        "snapshot_producer_root_sha256",
        "formal_xgboost_library_sha256",
        "snapshot_xgboost_library_sha256",
    ):
        _require_sha256(unsigned[field], label)
    relative = _snapshot_relative(
        str(unsigned["xgboost_library_relative"]),
        label,
    )
    normalized_shallow, normalized_shallow_root = _binding_identity(
        unsigned.get("normalized_shallow_binding"),
        f"{label} normalized shallow producer",
    )
    normalized_probability, _normalized_probability_root = _binding_identity(
        unsigned.get("normalized_probability_binding"),
        f"{label} normalized probability producer",
    )
    normalized_runtime = normalized_shallow.get("xgboost_build_info")
    if (
        unsigned["formal_producer_root_sha256"] != EXPECTED_PRODUCER_ROOT_SHA256
        or unsigned["formal_xgboost_library_sha256"]
        != unsigned["snapshot_xgboost_library_sha256"]
        or len(relative.parts) != 3
        or relative.parts[:2] != ("xgboost", "lib")
        or relative.name.lower() not in {"xgboost.dll", "libxgboost.dll"}
        or not isinstance(normalized_runtime, dict)
        or normalized_runtime.get("libxgboost") != "<relocated-xgboost-library>"
        or normalized_probability.get("base_shallow_gbdt_producer_root_sha256")
        != normalized_shallow_root
    ):
        raise IndependentVerificationError(f"{label} is invalid")
    return dict(unsigned), descriptor_sha256


def _source_producer_identity(
    relocation: Mapping[str, Any],
    runtime_root: Path,
) -> dict[str, str]:
    if set(relocation) != XGBOOST_RELOCATION_PUBLIC_FIELDS:
        raise IndependentVerificationError("relocated producer binding is invalid")
    strategy = EXPECTED_STRATEGY_SHA256
    producer = _require_sha256(
        relocation.get("formal_producer_root_sha256"),
        "source producer",
    )
    xgboost_library_relative = _snapshot_relative(
        str(relocation.get("xgboost_library_relative") or ""),
        "xgboost runtime library",
    )
    if (
        len(xgboost_library_relative.parts) != 3
        or xgboost_library_relative.parts[:2] != ("xgboost", "lib")
        or xgboost_library_relative.name.lower()
        not in {"xgboost.dll", "libxgboost.dll"}
    ):
        raise IndependentVerificationError("xgboost runtime library layout is invalid")
    _regular_file(
        runtime_root / xgboost_library_relative,
        "isolated xgboost runtime library",
    )
    if (
        _sha256_file(runtime_root / xgboost_library_relative)
        != _require_sha256(
            relocation.get("snapshot_xgboost_library_sha256"),
            "isolated xgboost runtime library",
        )
    ):
        raise IndependentVerificationError("isolated xgboost runtime library drifted")
    if strategy != EXPECTED_STRATEGY_SHA256 or producer != EXPECTED_PRODUCER_ROOT_SHA256:
        raise IndependentVerificationError("frozen source producer binding drifted")
    return {
        "strategy_sha256": strategy,
        "producer_root_sha256": producer,
        "xgboost_library_relative": xgboost_library_relative.as_posix(),
    }


def _python_core_runtime_identity(
    python_executable: Path,
    label: str,
) -> dict[str, Any]:
    _regular_file(python_executable, f"{label} executable")
    completed = subprocess.run(
        [
            str(python_executable),
            "-I",
            "-S",
            "-B",
            "-c",
            PYTHON_RUNTIME_PROBE_DRIVER,
        ],
        check=False,
        cwd=python_executable.parent,
        env=_minimal_child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0 or len(completed.stdout) > MAX_PROBE_BYTES:
        raise IndependentVerificationError(f"{label} runtime probe failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVerificationError(
            f"{label} runtime probe is invalid"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "base_python_executable",
            "stdlib_root",
            "python_abi",
        }
        or value.get("schema_version") != PYTHON_RUNTIME_PROBE_SCHEMA
        or not isinstance(value.get("python_abi"), dict)
    ):
        raise IndependentVerificationError(
            f"{label} runtime probe is invalid"
        )
    base_python_executable = Path(
        str(value["base_python_executable"])
    ).absolute()
    _regular_file(base_python_executable, f"{label} base executable")
    stdlib_root = Path(str(value["stdlib_root"])).absolute()
    _assert_no_reparse(stdlib_root, f"{label} standard library")
    if not stdlib_root.is_dir():
        raise IndependentVerificationError(f"{label} standard library is missing")
    python_abi = value["python_abi"]
    if (
        set(python_abi) != {"implementation", "cache_tag", "version"}
        or not isinstance(python_abi["implementation"], str)
        or not isinstance(python_abi["cache_tag"], str)
        or not isinstance(python_abi["version"], list)
        or len(python_abi["version"]) != 3
        or any(type(item) is not int for item in python_abi["version"])
    ):
        raise IndependentVerificationError(f"{label} ABI is invalid")
    return {
        "base_python_executable": base_python_executable,
        "base_python_executable_sha256": _sha256_file(base_python_executable),
        "stdlib_root": stdlib_root,
        "stdlib_manifest_sha256": _stdlib_manifest(stdlib_root)["sha256"],
        "python_abi": python_abi,
    }


def _source_python_runtime_identity(python_executable: Path) -> dict[str, Any]:
    core = _python_core_runtime_identity(python_executable, "frozen source Python")
    venv_root = python_executable.parent.parent
    _assert_no_reparse(venv_root, "frozen Python virtual environment")
    if not venv_root.is_dir():
        raise IndependentVerificationError("frozen Python virtual environment is missing")
    venv_config = _regular_file(
        venv_root / "pyvenv.cfg",
        "frozen Python virtual environment configuration",
    )
    runtime_site_root = venv_root / "Lib" / "site-packages"
    _assert_no_reparse(runtime_site_root, "frozen runtime site-packages root")
    if not runtime_site_root.is_dir():
        raise IndependentVerificationError(
            "frozen runtime site-packages root is missing"
        )
    return {
        **core,
        "runtime_site_root": runtime_site_root,
        "venv_config_sha256": _sha256_file(venv_config),
    }


def _base_runtime_excluded_relative_prefixes(
    base_runtime_root: Path,
    stdlib_root: Path,
) -> frozenset[str]:
    try:
        stdlib_relative = stdlib_root.relative_to(base_runtime_root)
    except ValueError as exc:
        raise IndependentVerificationError(
            "frozen Python standard library escapes its base runtime"
        ) from exc
    if not stdlib_relative.parts:
        raise IndependentVerificationError("frozen Python standard library layout is invalid")
    return _ignored_snapshot_relative_prefixes(
        frozenset(
            {
                (stdlib_relative / "site-packages").as_posix(),
                (stdlib_relative / "__pycache__").as_posix(),
            }
        ),
        "frozen base Python runtime",
    )


def _source_base_runtime_excluded_relative_prefixes(
    source: Mapping[str, Any],
) -> frozenset[str]:
    expected = _base_runtime_excluded_relative_prefixes(
        Path(source["base_runtime_root"]),
        Path(source["stdlib_root"]),
    )
    declared = source.get("base_runtime_excluded_relative_prefixes")
    if not isinstance(declared, list) or declared != sorted(expected):
        raise IndependentVerificationError("frozen base Python runtime exclusions drifted")
    return expected


def _source_base_identity(source_root_value: str) -> dict[str, Any]:
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
    runtime = _source_python_runtime_identity(python_executable)
    base_runtime_root = Path(runtime["base_python_executable"]).parent
    _assert_no_reparse(base_runtime_root, "frozen base Python runtime")
    if not base_runtime_root.is_dir():
        raise IndependentVerificationError("frozen base Python runtime is missing")
    excluded_relative_prefixes = _base_runtime_excluded_relative_prefixes(
        base_runtime_root,
        Path(runtime["stdlib_root"]),
    )
    base_runtime_manifest = _manifest_tree(
        base_runtime_root,
        "frozen base Python runtime",
        ignored_relative_prefixes=excluded_relative_prefixes,
    )
    tracked_tree_entries = _expected_source_tree(source_root)
    tracked_manifest = _tracked_source_manifest(
        source_root,
        tracked_tree_entries,
        "frozen source worktree",
        git_filter_root=source_root,
    )
    return {
        "root": source_root,
        "git_commit": EXPECTED_SOURCE_COMMIT,
        "python_executable": python_executable,
        "python_executable_sha256": _sha256_file(python_executable),
        **runtime,
        "base_runtime_root": base_runtime_root,
        "base_runtime_excluded_relative_prefixes": sorted(excluded_relative_prefixes),
        "base_runtime_manifest_sha256": base_runtime_manifest["sha256"],
        "tracked_tree_entries": tracked_tree_entries,
        "tracked_manifest_sha256": tracked_manifest["sha256"],
    }


def _assert_source_base_unchanged(source: Mapping[str, Any]) -> dict[str, Any]:
    observed = _source_base_identity(str(source["root"]))
    for field in (
        "git_commit",
        "python_executable",
        "python_executable_sha256",
        "base_python_executable",
        "base_python_executable_sha256",
        "stdlib_root",
        "stdlib_manifest_sha256",
        "python_abi",
        "runtime_site_root",
        "venv_config_sha256",
        "base_runtime_root",
        "base_runtime_excluded_relative_prefixes",
        "base_runtime_manifest_sha256",
        "tracked_tree_entries",
        "tracked_manifest_sha256",
    ):
        if observed[field] != source.get(field):
            raise IndependentVerificationError("frozen source identity drifted")
    return observed


def _copy_source_snapshot(source: Mapping[str, Any], code_root: Path) -> dict[str, Any]:
    source_root = Path(source["root"])
    tracked_tree_entries = tuple(source["tracked_tree_entries"])
    before = _tracked_source_manifest(
        source_root,
        tracked_tree_entries,
        "frozen source worktree",
        git_filter_root=source_root,
    )
    if before["sha256"] != source["tracked_manifest_sha256"]:
        raise IndependentVerificationError("frozen source worktree drifted")
    for relative, _blob_sha1 in tracked_tree_entries:
        _overlay_snapshot_file(
            source_root / relative,
            code_root / relative,
            "frozen source worktree",
        )
    after = _tracked_source_manifest(
        source_root,
        tracked_tree_entries,
        "frozen source worktree",
        git_filter_root=source_root,
    )
    if after["sha256"] != before["sha256"]:
        raise IndependentVerificationError(
            "frozen source worktree changed while being snapshotted"
        )
    snapshot = _tracked_source_manifest(
        code_root,
        tracked_tree_entries,
        "isolated source worktree",
    )
    if snapshot["sha256"] != before["sha256"]:
        raise IndependentVerificationError("isolated source worktree copy is invalid")
    return snapshot


def _verify_runtime_verification(
    document: Mapping[str, Any],
    *,
    main_artifact_sha256: str,
    producer_root_sha256: str = EXPECTED_PRODUCER_ROOT_SHA256,
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
        != _require_sha256(producer_root_sha256, "expected runtime producer")
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


def _load_content_addressed_result_bundle(
    main_artifact_path: Path,
    runtime_verification_path: Path,
    *,
    expected_producer_root_sha256: str,
    label: str,
) -> dict[str, Any]:
    main_artifact = _content_addressed_document(
        main_artifact_path,
        f"{label} main artifact",
    )
    main_document = main_artifact["document"]
    main_producer = main_document.get("producer_code")
    if (
        main_document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or not isinstance(main_producer, dict)
        or _require_sha256(main_producer.get("root_sha256"), f"{label} main producer")
        != _require_sha256(expected_producer_root_sha256, f"{label} expected producer")
    ):
        raise IndependentVerificationError(f"{label} main artifact binding is invalid")
    sidecar_references = main_document.get("sidecars")
    if not isinstance(sidecar_references, dict) or set(sidecar_references) != RESULT_BUNDLE_SIDECAR_NAMES:
        raise IndependentVerificationError(f"{label} sidecar references are invalid")
    sidecar_paths: dict[str, Path] = {}
    sidecar_documents: dict[str, dict[str, Any]] = {}
    sidecar_hashes: dict[str, str] = {}
    for name in sorted(RESULT_BUNDLE_SIDECAR_NAMES):
        reference = sidecar_references[name]
        if not isinstance(reference, dict) or set(reference) != {"artifact_sha256", "relative_path"}:
            raise IndependentVerificationError(f"{label} sidecar reference is invalid")
        digest = _require_sha256(reference.get("artifact_sha256"), f"{label} {name} sidecar")
        relative = str(reference.get("relative_path") or "")
        if relative != f"sidecars/{digest}.json":
            raise IndependentVerificationError(f"{label} sidecar reference is invalid")
        path = _safe_descendant(main_artifact_path.parent, relative, f"{label} {name} sidecar")
        sidecar = _content_addressed_document(path, f"{label} {name} sidecar")
        document = sidecar["document"]
        if (
            sidecar["canonical_artifact_sha256"] != digest
            or document.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
            or document.get("producer_code") != main_producer
        ):
            raise IndependentVerificationError(f"{label} {name} sidecar binding is invalid")
        sidecar_paths[name] = path
        sidecar_documents[name] = document
        sidecar_hashes[name] = digest
    runtime_artifact = _content_addressed_document(
        runtime_verification_path,
        f"{label} runtime verification",
    )
    runtime_document = runtime_artifact["document"]
    _verify_runtime_verification(
        runtime_document,
        main_artifact_sha256=main_artifact["canonical_artifact_sha256"],
        producer_root_sha256=expected_producer_root_sha256,
    )
    if runtime_document.get("sidecar_artifact_sha256") != sidecar_hashes:
        raise IndependentVerificationError(f"{label} runtime sidecar binding is invalid")
    return {
        "main_artifact_path": main_artifact_path,
        "main_artifact_sha256": main_artifact["canonical_artifact_sha256"],
        "main_document": main_document,
        "sidecar_paths": sidecar_paths,
        "sidecar_documents": sidecar_documents,
        "sidecar_artifact_sha256": sidecar_hashes,
        "runtime_verification_path": runtime_verification_path,
        "runtime_verification_artifact_sha256": runtime_artifact[
            "canonical_artifact_sha256"
        ],
        "runtime_verification": runtime_document,
    }


def _relocation_equivalence_sha256(
    result_bundle: Mapping[str, Any],
    *,
    expected_producer_code: Mapping[str, Any],
    normalized_producer_code: Mapping[str, Any],
    expected_xgboost_build_info: Mapping[str, Any],
    normalized_xgboost_build_info: Mapping[str, Any],
    label: str,
) -> str:
    expected = json.loads(_canonical_bytes(dict(expected_producer_code)).decode("utf-8"))
    normalized = json.loads(_canonical_bytes(dict(normalized_producer_code)).decode("utf-8"))
    normalized_body, normalized_root = _binding_identity(
        normalized,
        f"{label} normalized producer",
    )
    if normalized_root != normalized["root_sha256"]:
        raise IndependentVerificationError(f"{label} normalized producer is invalid")
    expected_runtime = json.loads(
        _canonical_bytes(dict(expected_xgboost_build_info)).decode("utf-8")
    )
    normalized_runtime = json.loads(
        _canonical_bytes(dict(normalized_xgboost_build_info)).decode("utf-8")
    )
    if (
        not isinstance(expected_runtime.get("libxgboost"), str)
        or not expected_runtime["libxgboost"]
        or normalized_runtime
        != {**expected_runtime, "libxgboost": "<relocated-xgboost-library>"}
    ):
        raise IndependentVerificationError(f"{label} normalized xgboost runtime is invalid")

    def clone_document(value: Mapping[str, Any], item_label: str) -> dict[str, Any]:
        cloned = json.loads(_canonical_bytes(dict(value)).decode("utf-8"))
        if not isinstance(cloned, dict):
            raise IndependentVerificationError(f"{item_label} is invalid")
        return cloned

    def normalize_fold(
        original_fold: object,
        fold_index: int,
    ) -> dict[str, Any]:
        if not isinstance(original_fold, dict):
            raise IndependentVerificationError(f"{label} OOF fold is invalid")
        original_unsigned = dict(original_fold)
        receipt_sha256 = _require_sha256(
            original_unsigned.pop("receipt_sha256", None),
            f"{label} OOF fold receipt",
        )
        if receipt_sha256 != _sha256_bytes(_canonical_bytes(original_unsigned)):
            raise IndependentVerificationError(f"{label} OOF fold receipt drifted")
        normalized_fold = clone_document(
            original_unsigned,
            f"{label} OOF fold {fold_index}",
        )
        for receipt_name in ("fit_receipt", "predict_receipt"):
            original_receipt = original_unsigned.get(receipt_name)
            normalized_receipt = normalized_fold.get(receipt_name)
            if (
                not isinstance(original_receipt, dict)
                or not isinstance(normalized_receipt, dict)
                or not isinstance(original_receipt.get("runtime"), dict)
                or not isinstance(normalized_receipt.get("runtime"), dict)
                or original_receipt["runtime"].get("xgboost_build_info")
                != expected_runtime
            ):
                raise IndependentVerificationError(
                    f"{label} {receipt_name} xgboost runtime binding drifted"
                )
            normalized_receipt["runtime"]["xgboost_build_info"] = normalized_runtime
        normalized_fold["receipt_sha256"] = _sha256_bytes(
            _canonical_bytes(normalized_fold)
        )
        return normalized_fold

    def normalize_models_oof_sidecar(document: Mapping[str, Any]) -> dict[str, Any]:
        original_document = dict(document)
        has_oof_receipt = "oof_receipt" in original_document
        has_oof_verification = "oof_replay_verification" in original_document
        if not has_oof_receipt and not has_oof_verification:
            return clone_document(original_document, f"{label} models sidecar")
        original_oof = original_document.get("oof_receipt")
        original_verification = original_document.get("oof_replay_verification")
        if not isinstance(original_oof, dict) or not isinstance(original_verification, dict):
            raise IndependentVerificationError(f"{label} OOF sidecar is invalid")
        original_oof_unsigned = dict(original_oof)
        oof_receipt_sha256 = _require_sha256(
            original_oof_unsigned.pop("receipt_sha256", None),
            f"{label} OOF receipt",
        )
        if oof_receipt_sha256 != _sha256_bytes(_canonical_bytes(original_oof_unsigned)):
            raise IndependentVerificationError(f"{label} OOF receipt drifted")
        original_folds = original_oof_unsigned.get("folds")
        folds_sha256 = _require_sha256(
            original_oof_unsigned.get("folds_sha256"),
            f"{label} OOF folds",
        )
        if (
            not isinstance(original_folds, list)
            or not original_folds
            or folds_sha256 != _sha256_bytes(_canonical_bytes(original_folds))
            or original_verification.get("receipt_sha256") != oof_receipt_sha256
        ):
            raise IndependentVerificationError(f"{label} OOF receipt binding drifted")
        normalized_document = clone_document(
            original_document,
            f"{label} models sidecar",
        )
        normalized_oof = dict(original_oof_unsigned)
        normalized_folds = [
            normalize_fold(fold, index)
            for index, fold in enumerate(original_folds)
        ]
        normalized_oof["folds"] = normalized_folds
        normalized_oof["folds_sha256"] = _sha256_bytes(
            _canonical_bytes(normalized_folds)
        )
        normalized_oof["receipt_sha256"] = _sha256_bytes(
            _canonical_bytes(normalized_oof)
        )
        normalized_verification = dict(original_verification)
        normalized_verification["receipt_sha256"] = normalized_oof["receipt_sha256"]
        normalized_document["oof_receipt"] = normalized_oof
        normalized_document["oof_replay_verification"] = normalized_verification
        return normalized_document

    main_document = result_bundle.get("main_document")
    sidecar_documents = result_bundle.get("sidecar_documents")
    runtime_document = result_bundle.get("runtime_verification")
    if (
        not isinstance(main_document, dict)
        or not isinstance(sidecar_documents, dict)
        or not isinstance(runtime_document, dict)
        or set(sidecar_documents) != RESULT_BUNDLE_SIDECAR_NAMES
        or main_document.get("producer_code") != expected
    ):
        raise IndependentVerificationError(f"{label} relocation bundle is invalid")
    normalized_sidecars: dict[str, dict[str, Any]] = {}
    semantic_sidecar_hashes: dict[str, str] = {}
    for name in sorted(RESULT_BUNDLE_SIDECAR_NAMES):
        document = sidecar_documents[name]
        if not isinstance(document, dict) or document.get("producer_code") != expected:
            raise IndependentVerificationError(f"{label} {name} relocation sidecar is invalid")
        unsigned = (
            normalize_models_oof_sidecar(document)
            if name == "models"
            else clone_document(document, f"{label} {name} sidecar")
        )
        unsigned.pop("artifact_sha256", None)
        unsigned["producer_code"] = normalized
        normalized_sidecars[name] = unsigned
        semantic_sidecar_hashes[name] = _sha256_bytes(_canonical_bytes(unsigned))
    normalized_main = clone_document(main_document, f"{label} main document")
    normalized_main.pop("artifact_sha256", None)
    normalized_main["producer_code"] = normalized
    normalized_main["sidecars"] = {
        name: {"semantic_sha256": semantic_sidecar_hashes[name]}
        for name in sorted(RESULT_BUNDLE_SIDECAR_NAMES)
    }
    semantic_main_sha256 = _sha256_bytes(_canonical_bytes(normalized_main))
    normalized_runtime_document = clone_document(
        runtime_document,
        f"{label} runtime verification",
    )
    normalized_runtime_document.pop("artifact_sha256", None)
    normalized_runtime_document.pop("receipt_sha256", None)
    normalized_runtime_document["producer_root_sha256"] = normalized_root
    normalized_runtime_document["main_artifact_sha256"] = semantic_main_sha256
    normalized_runtime_document["sidecar_artifact_sha256"] = semantic_sidecar_hashes
    body = {
        "schema_version": RELOCATION_EQUIVALENCE_SCHEMA,
        "main": normalized_main,
        "sidecars": normalized_sidecars,
        "runtime_verification": normalized_runtime_document,
    }
    return _sha256_bytes(_canonical_bytes(body))


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
    bundle = _load_content_addressed_result_bundle(
        artifact_path,
        runtime_path,
        expected_producer_root_sha256=EXPECTED_PRODUCER_ROOT_SHA256,
        label="formal result bundle",
    )
    if (
        bundle["main_artifact_sha256"] != main_sha
        or bundle["runtime_verification_artifact_sha256"]
        != runtime["canonical_artifact_sha256"]
        or bundle["runtime_verification"] != runtime_document
    ):
        raise IndependentVerificationError("formal result bundle binding drifted")
    return {
        "run_root": run_root,
        "completion_sha256": _sha256_bytes(completion_raw),
        "completion_path": completion_path,
        "resource_receipt_path": resource_path,
        "main_artifact_path": artifact_path,
        "runtime_verification_path": runtime_path,
        "main_artifact_sha256": main_sha,
        "runtime_verification_artifact_sha256": runtime["canonical_artifact_sha256"],
        "runtime_verification": runtime_document,
        "main_document": bundle["main_document"],
        "sidecar_paths": bundle["sidecar_paths"],
        "sidecar_documents": bundle["sidecar_documents"],
        "sidecar_artifact_sha256": bundle["sidecar_artifact_sha256"],
        "source": dict(source),
    }


def _copy_data_snapshot(
    live_inputs: Mapping[str, Any],
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_root = Path(live_inputs["source"]["root"])
    data_root.mkdir(parents=True, exist_ok=False)
    universe_path = source_root / Path(REPLAY_PLAN["inputs"]["audited_pit_universe_path"])
    _assert_sqlite_quiescent(universe_path, "frozen PIT universe")
    selected = (
        universe_path.parent,
        source_root / Path(REPLAY_PLAN["inputs"]["temporal_contract_path"]),
        source_root / "data" / "research_artifacts" / "current_pool_audits",
        source_root / Path(REPLAY_PLAN["inputs"]["security_code_transition_evidence_root"]),
        Path(live_inputs["completion_path"]),
        Path(live_inputs["resource_receipt_path"]),
        Path(live_inputs["main_artifact_path"]),
        *(Path(path) for path in live_inputs["sidecar_paths"].values()),
        Path(live_inputs["runtime_verification_path"]),
    )
    copied: set[Path] = set()
    for source_path in selected:
        try:
            relative = source_path.relative_to(source_root)
        except ValueError as exc:
            raise IndependentVerificationError("frozen data path escapes its source root") from exc
        if relative in copied:
            continue
        copied.add(relative)
        _copy_snapshot_path(source_path, data_root / relative, "frozen replay data")
    snapshot_universe = data_root / Path(REPLAY_PLAN["inputs"]["audited_pit_universe_path"])
    _assert_sqlite_quiescent(snapshot_universe, "snapshotted PIT universe")
    manifest = _manifest_tree(data_root, "frozen replay data snapshot")
    snapshot_source = {**dict(live_inputs["source"]), "root": data_root}
    snapshot_inputs = _load_completed_run(snapshot_source)
    for field in (
        "completion_sha256",
        "main_artifact_sha256",
        "runtime_verification_artifact_sha256",
    ):
        if snapshot_inputs[field] != live_inputs[field]:
            raise IndependentVerificationError("frozen replay data binding drifted")
    return snapshot_inputs, manifest


def _assert_sqlite_quiescent(path: Path, label: str) -> None:
    _regular_file(path, label)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(f"{path.name}{suffix}")
        if sidecar.exists():
            raise IndependentVerificationError(f"{label} has an active sidecar")


def _normalized_path_identity(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _assert_copied_python_core_binding(
    source: Mapping[str, Any],
    interpreter_root: Path,
    python_executable: Path,
    core: Mapping[str, Any],
) -> None:
    base_runtime_root = Path(source["base_runtime_root"])
    try:
        executable_relative = Path(source["base_python_executable"]).relative_to(
            base_runtime_root
        )
        stdlib_relative = Path(source["stdlib_root"]).relative_to(base_runtime_root)
    except ValueError as exc:
        raise IndependentVerificationError(
            "frozen Python core path escapes its base runtime"
        ) from exc
    expected_executable = interpreter_root / _snapshot_relative(
        executable_relative.as_posix(),
        "isolated Python executable",
    )
    expected_stdlib = interpreter_root / _snapshot_relative(
        stdlib_relative.as_posix(),
        "isolated Python standard library",
    )
    observed_executable = Path(str(core.get("base_python_executable") or ""))
    observed_stdlib = Path(str(core.get("stdlib_root") or ""))
    if (
        _normalized_path_identity(python_executable)
        != _normalized_path_identity(expected_executable)
        or _normalized_path_identity(observed_executable)
        != _normalized_path_identity(expected_executable)
        or _normalized_path_identity(observed_stdlib)
        != _normalized_path_identity(expected_stdlib)
    ):
        raise IndependentVerificationError(
            "isolated Python core resolves outside its snapshot"
        )
    _regular_file(expected_executable, "isolated Python executable")
    _assert_no_reparse(expected_stdlib, "isolated Python standard library")
    if not expected_stdlib.is_dir():
        raise IndependentVerificationError("isolated Python standard library is missing")


def _copy_interpreter_snapshot(
    source: Mapping[str, Any],
    interpreter_root: Path,
) -> tuple[Path, dict[str, Any]]:
    base_runtime_root = Path(source["base_runtime_root"])
    excluded_relative_prefixes = _source_base_runtime_excluded_relative_prefixes(source)
    before = _manifest_tree(
        base_runtime_root,
        "frozen base Python runtime",
        ignored_relative_prefixes=excluded_relative_prefixes,
    )
    if before["sha256"] != source["base_runtime_manifest_sha256"]:
        raise IndependentVerificationError("frozen base Python runtime drifted")
    _copy_snapshot_path(
        base_runtime_root,
        interpreter_root,
        "frozen base Python runtime",
        ignored_relative_prefixes=excluded_relative_prefixes,
    )
    after = _manifest_tree(
        base_runtime_root,
        "frozen base Python runtime",
        ignored_relative_prefixes=excluded_relative_prefixes,
    )
    snapshot = _manifest_tree(interpreter_root, "isolated Python runtime")
    if (
        after["sha256"] != before["sha256"]
        or snapshot["sha256"] != before["sha256"]
    ):
        raise IndependentVerificationError("isolated Python runtime copy is invalid")
    try:
        executable_relative = Path(source["base_python_executable"]).relative_to(
            base_runtime_root
        )
    except ValueError as exc:
        raise IndependentVerificationError(
            "frozen base Python executable escapes its runtime"
        ) from exc
    python_executable = interpreter_root / executable_relative
    core = _python_core_runtime_identity(
        python_executable,
        "isolated Python runtime",
    )
    _assert_copied_python_core_binding(
        source,
        interpreter_root,
        python_executable,
        core,
    )
    if (
        core["base_python_executable_sha256"]
        != source["base_python_executable_sha256"]
        or core["stdlib_manifest_sha256"]
        != source["stdlib_manifest_sha256"]
        or core["python_abi"] != source["python_abi"]
    ):
        raise IndependentVerificationError("isolated Python runtime binding drifted")
    return python_executable, snapshot


class _IsolatedReplaySnapshot:
    def __init__(self, source: Mapping[str, Any], live_inputs: Mapping[str, Any] | None = None):
        self.source = dict(source)
        self.live_inputs = live_inputs
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.code_root: Path | None = None
        self.interpreter_root: Path | None = None
        self.python_executable: Path | None = None
        self.runtime_root: Path | None = None
        self.data_root: Path | None = None
        self.code_manifest: dict[str, Any] | None = None
        self.interpreter_manifest: dict[str, Any] | None = None
        self.runtime_manifest: dict[str, Any] | None = None
        self.data_manifest: dict[str, Any] | None = None
        self.runtime_plan: dict[str, Any] | None = None
        self.snapshot_inputs: dict[str, Any] | None = None
        self.producer: dict[str, str] | None = None
        self.formal_raw_producer: dict[str, Any] | None = None
        self.snapshot_raw_producer: dict[str, Any] | None = None
        self.code_tree_sha1: str | None = None
        self.xgboost_relocation_descriptor_path: Path | None = None
        self.xgboost_relocation_public: dict[str, Any] | None = None
        self.xgboost_relocation_descriptor_sha256: str | None = None

    def __enter__(self) -> "_IsolatedReplaySnapshot":
        self._temporary = tempfile.TemporaryDirectory(prefix="qsl-probability-replay-")
        self.root = Path(self._temporary.name)
        _assert_no_reparse(self.root, "isolated replay snapshot root")
        self.code_root = self.root / "code"
        source_root = Path(self.source["root"])
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "worktree",
                "add",
                "--detach",
                str(self.code_root),
                EXPECTED_SOURCE_COMMIT,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            self.close()
            raise IndependentVerificationError("isolated source worktree creation failed")
        try:
            current_source = _assert_source_base_unchanged(self.source)
            if (
                Path(
                    _git_output(self.code_root, "rev-parse", "--show-toplevel")
                ).resolve()
                != self.code_root
                or _git_output(self.code_root, "status", "--porcelain")
                or _git_output(self.code_root, "rev-parse", "HEAD").lower()
                != EXPECTED_SOURCE_COMMIT
            ):
                raise IndependentVerificationError(
                    "isolated source worktree identity is invalid"
                )
            self.code_tree_sha1 = _git_output(
                Path(current_source["root"]),
                "rev-parse",
                f"{EXPECTED_SOURCE_COMMIT}^{{tree}}",
            ).lower()
            if not HEX_COMMIT.fullmatch(self.code_tree_sha1):
                raise IndependentVerificationError("frozen source tree is invalid")
            self.code_manifest = _copy_source_snapshot(self.source, self.code_root)
            _assert_source_base_unchanged(self.source)
            self.interpreter_root = self.root / "interpreter"
            self.python_executable, self.interpreter_manifest = (
                _copy_interpreter_snapshot(
                    self.source,
                    self.interpreter_root,
                )
            )
            self.runtime_plan = _runtime_dependency_plan(
                self.code_root,
                self.python_executable,
                Path(self.source["runtime_site_root"]),
            )
            self.runtime_root = self.root / "runtime"
            self.runtime_manifest = _copy_runtime_snapshot(
                Path(self.source["runtime_site_root"]),
                self.runtime_root,
                self.runtime_plan,
            )
            formal_raw_producer = _raw_producer_probe(
                self.code_root,
                self.python_executable,
                Path(self.source["runtime_site_root"]),
            )
            snapshot_raw_producer = _raw_producer_probe(
                self.code_root,
                self.python_executable,
                self.runtime_root,
            )
            relocation = _build_xgboost_relocation_descriptor(
                formal_raw_producer,
                snapshot_raw_producer,
                Path(self.source["runtime_site_root"]),
                self.runtime_root,
            )
            self.formal_raw_producer = formal_raw_producer
            self.snapshot_raw_producer = snapshot_raw_producer
            self.xgboost_relocation_descriptor_path = (
                self.root / "xgboost-relocation.json"
            )
            _write_once(
                self.xgboost_relocation_descriptor_path,
                _canonical_bytes(relocation) + b"\n",
                "isolated xgboost relocation descriptor",
            )
            (
                self.xgboost_relocation_public,
                self.xgboost_relocation_descriptor_sha256,
            ) = _read_xgboost_relocation_descriptor(
                self.xgboost_relocation_descriptor_path,
                "isolated xgboost relocation descriptor",
            )
            self.producer = _source_producer_identity(
                self.xgboost_relocation_public,
                self.runtime_root,
            )
            if self.live_inputs is not None:
                self.data_root = self.root / "data"
                self.snapshot_inputs, self.data_manifest = _copy_data_snapshot(
                    self.live_inputs,
                    self.data_root,
                )
            self._assert_expected_binding()
            self.verify()
            return self
        except BaseException:
            self.close()
            raise

    def _assert_expected_binding(self) -> None:
        assert self.code_manifest is not None
        assert self.interpreter_manifest is not None
        assert self.runtime_manifest is not None
        assert self.runtime_plan is not None
        assert self.code_tree_sha1 is not None
        assert self.xgboost_relocation_public is not None
        assert self.xgboost_relocation_descriptor_sha256 is not None
        for field, observed in (
            ("code_tree_sha1", self.code_tree_sha1),
            ("code_manifest_sha256", self.code_manifest["sha256"]),
            ("runtime_plan_sha256", self.runtime_plan["sha256"]),
            ("runtime_manifest_sha256", self.runtime_manifest["sha256"]),
            ("interpreter_manifest_sha256", self.interpreter_manifest["sha256"]),
        ):
            expected = self.source.get(field)
            if expected is not None and expected != observed:
                raise IndependentVerificationError("isolated replay snapshot binding drifted")
        if (
            self.interpreter_manifest["sha256"]
            != self.source["base_runtime_manifest_sha256"]
        ):
            raise IndependentVerificationError("isolated Python runtime binding drifted")
        for field in (
            "strategy_sha256",
            "producer_root_sha256",
            "xgboost_library_relative",
        ):
            expected = self.source.get(field)
            if expected is not None and self.producer is not None and self.producer[field] != expected:
                raise IndependentVerificationError("isolated producer binding drifted")
        expected_relocation = self.source.get("xgboost_relocation")
        if (
            expected_relocation is not None
            and expected_relocation != self.xgboost_relocation_binding()
        ):
            raise IndependentVerificationError("isolated xgboost relocation binding drifted")

    def verify(self) -> None:
        if (
            self.code_root is None
            or self.interpreter_root is None
            or self.python_executable is None
            or self.runtime_root is None
            or self.code_manifest is None
            or self.interpreter_manifest is None
            or self.runtime_manifest is None
            or self.runtime_plan is None
            or self.producer is None
            or self.code_tree_sha1 is None
            or self.xgboost_relocation_descriptor_path is None
            or self.xgboost_relocation_public is None
            or self.xgboost_relocation_descriptor_sha256 is None
        ):
            raise IndependentVerificationError("isolated replay snapshot is incomplete")
        source_python = Path(self.source["python_executable"])
        if _sha256_file(source_python) != self.source["python_executable_sha256"]:
            raise IndependentVerificationError("frozen source Python executable drifted")
        runtime = _assert_source_base_unchanged(self.source)
        for field in (
            "base_python_executable",
            "base_python_executable_sha256",
            "stdlib_root",
            "stdlib_manifest_sha256",
            "runtime_site_root",
            "venv_config_sha256",
            "base_runtime_root",
            "base_runtime_excluded_relative_prefixes",
            "base_runtime_manifest_sha256",
            "python_abi",
        ):
            if runtime[field] != self.source[field]:
                raise IndependentVerificationError("frozen source Python runtime drifted")
        if (
            _git_output(self.code_root, "rev-parse", "HEAD").lower()
            != EXPECTED_SOURCE_COMMIT
            or _git_output(
                self.code_root,
                "rev-parse",
                f"{EXPECTED_SOURCE_COMMIT}^{{tree}}",
            ).lower()
            != self.code_tree_sha1
        ):
            raise IndependentVerificationError("isolated source worktree ref drifted")
        if _tracked_source_manifest(
            self.code_root,
            tuple(self.source["tracked_tree_entries"]),
            "isolated source worktree",
        )["sha256"] != self.code_manifest["sha256"]:
            raise IndependentVerificationError("isolated source worktree manifest drifted")
        if _manifest_tree(self.runtime_root, "runtime snapshot")["sha256"] != self.runtime_manifest["sha256"]:
            raise IndependentVerificationError("runtime snapshot drifted")
        if (
            _manifest_tree(self.interpreter_root, "isolated Python runtime")["sha256"]
            != self.interpreter_manifest["sha256"]
        ):
            raise IndependentVerificationError("isolated Python runtime drifted")
        child_runtime = _python_core_runtime_identity(
            self.python_executable,
            "isolated Python runtime",
        )
        _assert_copied_python_core_binding(
            self.source,
            self.interpreter_root,
            self.python_executable,
            child_runtime,
        )
        if (
            child_runtime["base_python_executable_sha256"]
            != self.source["base_python_executable_sha256"]
            or child_runtime["stdlib_manifest_sha256"]
            != self.source["stdlib_manifest_sha256"]
            or child_runtime["python_abi"] != self.source["python_abi"]
        ):
            raise IndependentVerificationError("isolated Python runtime binding drifted")
        (
            observed_relocation_public,
            observed_relocation_descriptor_sha256,
        ) = _read_xgboost_relocation_descriptor(
            self.xgboost_relocation_descriptor_path,
            "isolated xgboost relocation descriptor",
        )
        if (
            observed_relocation_public != self.xgboost_relocation_public
            or observed_relocation_descriptor_sha256
            != self.xgboost_relocation_descriptor_sha256
        ):
            raise IndependentVerificationError("isolated xgboost relocation descriptor drifted")
        if self.data_root is not None and self.data_manifest is not None:
            if _manifest_tree(self.data_root, "frozen replay data snapshot")["sha256"] != self.data_manifest["sha256"]:
                raise IndependentVerificationError("frozen replay data snapshot drifted")

    def binding(self) -> dict[str, Any]:
        self.verify()
        assert self.code_manifest is not None
        assert self.runtime_manifest is not None
        assert self.runtime_plan is not None
        assert self.code_tree_sha1 is not None
        if self.data_manifest is None:
            raise IndependentVerificationError("frozen replay data snapshot is missing")
        body = {
            "schema_version": SNAPSHOT_SCHEMA,
            "code": {
                "git_commit": EXPECTED_SOURCE_COMMIT,
                "git_tree_sha1": self.code_tree_sha1,
                "manifest_sha256": self.code_manifest["sha256"],
            },
            "runtime": {
                "source_venv_python_executable_sha256": self.source[
                    "python_executable_sha256"
                ],
                "child_python_executable_sha256": _sha256_file(
                    self.python_executable
                ),
                "base_python_executable_sha256": self.source["base_python_executable_sha256"],
                "python_abi": self.source["python_abi"],
                "stdlib_manifest_sha256": self.source["stdlib_manifest_sha256"],
                "venv_config_sha256": self.source["venv_config_sha256"],
                "base_runtime_excluded_relative_prefixes": self.source[
                    "base_runtime_excluded_relative_prefixes"
                ],
                "interpreter_manifest_sha256": self.interpreter_manifest["sha256"],
                "dependency_plan_sha256": self.runtime_plan["sha256"],
                "manifest_sha256": self.runtime_manifest["sha256"],
                "xgboost_relocation": self.xgboost_relocation_binding(),
            },
            "data": {
                "manifest_sha256": self.data_manifest["sha256"],
                "file_count": self.data_manifest["file_count"],
            },
        }
        return {**body, "snapshot_sha256": _sha256_bytes(_canonical_bytes(body))}

    def xgboost_relocation_binding(self) -> dict[str, Any]:
        if (
            self.xgboost_relocation_public is None
            or self.xgboost_relocation_descriptor_sha256 is None
        ):
            raise IndependentVerificationError("isolated xgboost relocation is missing")
        return {
            **self.xgboost_relocation_public,
            "descriptor_sha256": self.xgboost_relocation_descriptor_sha256,
        }

    def close(self) -> None:
        code_root = self.code_root
        source_root = Path(self.source["root"])
        if code_root is not None and code_root.exists():
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "worktree",
                    "remove",
                    "--force",
                    str(code_root),
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> bool:
        self.close()
        return False


def _source_identity(source_root_value: str) -> dict[str, Any]:
    source = _source_base_identity(source_root_value)
    with _IsolatedReplaySnapshot(source) as snapshot:
        assert snapshot.code_manifest is not None
        assert snapshot.runtime_plan is not None
        assert snapshot.runtime_manifest is not None
        assert snapshot.interpreter_manifest is not None
        assert snapshot.code_tree_sha1 is not None
        assert snapshot.producer is not None
        return {
            **source,
            **snapshot.producer,
            "code_tree_sha1": snapshot.code_tree_sha1,
            "code_manifest_sha256": snapshot.code_manifest["sha256"],
            "runtime_plan_sha256": snapshot.runtime_plan["sha256"],
            "runtime_manifest_sha256": snapshot.runtime_manifest["sha256"],
            "interpreter_manifest_sha256": snapshot.interpreter_manifest["sha256"],
            "xgboost_relocation": snapshot.xgboost_relocation_binding(),
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


def _preregistration(
    inputs: Mapping[str, Any],
    verifier: Mapping[str, str],
    *,
    publisher: Any,
) -> dict[str, Any]:
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
        "source_base_python_sha256": inputs["source"]["base_python_executable_sha256"],
        "source_stdlib_manifest_sha256": inputs["source"]["stdlib_manifest_sha256"],
        "source_venv_config_sha256": inputs["source"]["venv_config_sha256"],
        "source_base_runtime_manifest_sha256": inputs["source"][
            "base_runtime_manifest_sha256"
        ],
        "source_interpreter_manifest_sha256": inputs["source"][
            "interpreter_manifest_sha256"
        ],
        "source_code_tree_sha1": inputs["source"]["code_tree_sha1"],
        "source_code_manifest_sha256": inputs["source"]["code_manifest_sha256"],
        "source_runtime_plan_sha256": inputs["source"]["runtime_plan_sha256"],
        "source_runtime_manifest_sha256": inputs["source"]["runtime_manifest_sha256"],
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
    path = publisher.write_root_once(
        name=PREREGISTRATION_NAME,
        raw=raw,
        label="preregistration",
    )
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


def _run_isolated_replay(
    inputs: Mapping[str, Any],
    snapshot: _IsolatedReplaySnapshot,
    *,
    publisher: Any,
) -> dict[str, Any]:
    if (
        snapshot.root is None
        or snapshot.code_root is None
        or snapshot.python_executable is None
        or snapshot.runtime_root is None
        or snapshot.data_root is None
        or snapshot.xgboost_relocation_descriptor_path is None
        or snapshot.snapshot_inputs is None
        or snapshot.formal_raw_producer is None
        or snapshot.snapshot_raw_producer is None
        or snapshot.xgboost_relocation_public is None
    ):
        raise IndependentVerificationError("isolated replay snapshot is incomplete")
    snapshot.verify()
    if any(
        publisher.root_file_exists(name)
        for name in (REPLAY_RESULT_NAME, REPLAY_STDOUT_NAME, REPLAY_STDERR_NAME)
    ):
        raise IndependentVerificationError("independent replay output already exists")
    staging_root = snapshot.root / "replay-staging"
    _assert_no_reparse(snapshot.root, "isolated replay snapshot root")
    staging_root.mkdir(exist_ok=False)
    _assert_no_reparse(staging_root, "isolated replay staging root")
    result_path = staging_root / REPLAY_RESULT_NAME
    stdout_path = staging_root / REPLAY_STDOUT_NAME
    stderr_path = staging_root / REPLAY_STDERR_NAME
    scratch_root = staging_root / SCRATCH_ROOT_NAME
    scratch_root.mkdir(exist_ok=False)
    _assert_no_reparse(scratch_root, "independent replay scratch root")
    command = [
        str(snapshot.python_executable),
        "-I",
        "-S",
        "-B",
        "-c",
        ISOLATED_REPLAY_DRIVER,
        str(snapshot.code_root),
        str(snapshot.data_root),
        str(snapshot.runtime_root),
        str(scratch_root),
        str(result_path),
        str(snapshot.xgboost_relocation_descriptor_path),
        _canonical_bytes(REPLAY_PLAN).decode("utf-8"),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=snapshot.code_root,
            env=_minimal_child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            shell=False,
            creationflags=creationflags,
        )
        exit_code = process.wait()
    snapshot.verify()
    if exit_code != 0:
        raise IndependentVerificationError("independent replay returned nonzero")
    replay = _read_object(result_path, "independent replay result")
    replay_main_sha256 = _require_sha256(
        replay.get("main_artifact_sha256"),
        "replayed main artifact",
    )
    replay_runtime_sha256 = _require_sha256(
        replay.get("runtime_verification_artifact_sha256"),
        "replayed runtime verification artifact",
    )
    normalized_producer = snapshot.xgboost_relocation_public[
        "normalized_probability_binding"
    ]
    if not isinstance(normalized_producer, dict):
        raise IndependentVerificationError("normalized replay producer is invalid")
    formal_producer = snapshot.formal_raw_producer.get("probability_binding")
    formal_xgboost_runtime = snapshot.formal_raw_producer.get("xgboost_build_info")
    snapshot_xgboost_runtime = snapshot.snapshot_raw_producer.get(
        "xgboost_build_info"
    )
    normalized_shallow = snapshot.xgboost_relocation_public.get(
        "normalized_shallow_binding"
    )
    if not isinstance(normalized_shallow, dict):
        raise IndependentVerificationError("normalized shallow producer is invalid")
    normalized_xgboost_runtime = normalized_shallow.get("xgboost_build_info")
    if (
        not isinstance(formal_producer, dict)
        or not isinstance(formal_xgboost_runtime, dict)
        or not isinstance(snapshot_xgboost_runtime, dict)
        or not isinstance(normalized_xgboost_runtime, dict)
    ):
        raise IndependentVerificationError("formal replay producer is invalid")
    replay_bundle = _load_content_addressed_result_bundle(
        scratch_root / "bundle" / f"{replay_main_sha256}.json",
        scratch_root / "bundle" / "verifications" / f"{replay_runtime_sha256}.json",
        expected_producer_root_sha256=normalized_producer.get("root_sha256", ""),
        label="isolated replay result bundle",
    )
    formal_equivalence_sha256 = _relocation_equivalence_sha256(
        snapshot.snapshot_inputs,
        expected_producer_code=formal_producer,
        normalized_producer_code=normalized_producer,
        expected_xgboost_build_info=formal_xgboost_runtime,
        normalized_xgboost_build_info=normalized_xgboost_runtime,
        label="formal relocation bundle",
    )
    replay_equivalence_sha256 = _relocation_equivalence_sha256(
        replay_bundle,
        expected_producer_code=normalized_producer,
        normalized_producer_code=normalized_producer,
        expected_xgboost_build_info=snapshot_xgboost_runtime,
        normalized_xgboost_build_info=normalized_xgboost_runtime,
        label="isolated replay relocation bundle",
    )
    result_raw = _read_bounded(result_path, "independent replay result")
    stdout_raw = _read_bounded(stdout_path, "independent replay stdout")
    stderr_raw = _read_bounded(stderr_path, "independent replay stderr")
    publisher.assert_bound()
    published_result_path = publisher.write_root_once(
        name=REPLAY_RESULT_NAME,
        raw=result_raw,
        label="independent replay result",
    )
    publisher.write_root_once(
        name=REPLAY_STDOUT_NAME,
        raw=stdout_raw,
        label="independent replay stdout",
    )
    publisher.write_root_once(
        name=REPLAY_STDERR_NAME,
        raw=stderr_raw,
        label="independent replay stderr",
    )
    publisher.assert_bound()
    return {
        "result": replay,
        "result_path": published_result_path,
        "result_sha256": _sha256_bytes(result_raw),
        "stdout": {
            "path": REPLAY_STDOUT_NAME,
            "bytes": len(stdout_raw),
            "sha256": _sha256_bytes(stdout_raw),
        },
        "stderr": {
            "path": REPLAY_STDERR_NAME,
            "bytes": len(stderr_raw),
            "sha256": _sha256_bytes(stderr_raw),
        },
        "exit_code": exit_code,
        "execution_snapshot": snapshot.binding(),
        "formal_relocation_equivalence_sha256": formal_equivalence_sha256,
        "replay_relocation_equivalence_sha256": replay_equivalence_sha256,
    }


def _validate_execution_snapshot(
    value: object,
    expected: Mapping[str, Any],
) -> None:
    if not isinstance(value, dict):
        raise IndependentVerificationError("execution snapshot binding is invalid")
    unsigned = dict(value)
    embedded = _require_sha256(unsigned.pop("snapshot_sha256", None), "execution snapshot")
    if (
        set(unsigned) != {"schema_version", "code", "runtime", "data"}
        or unsigned.get("schema_version") != SNAPSHOT_SCHEMA
        or embedded != _sha256_bytes(_canonical_bytes(unsigned))
        or value != dict(expected)
    ):
        raise IndependentVerificationError("execution snapshot binding drifted")


def _validate_replay(
    inputs: Mapping[str, Any],
    replay: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
) -> None:
    _validate_execution_snapshot(replay.get("execution_snapshot"), execution_snapshot)
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
    runtime = execution_snapshot.get("runtime")
    if not isinstance(runtime, dict):
        raise IndependentVerificationError("execution runtime binding is invalid")
    relocation = runtime.get("xgboost_relocation")
    if not isinstance(relocation, dict):
        raise IndependentVerificationError("execution relocation binding is invalid")
    normalized_producer = relocation.get("normalized_probability_binding")
    _normalized_body, normalized_producer_root = _binding_identity(
        normalized_producer,
        "execution normalized producer",
    )
    formal_equivalence_sha256 = _require_sha256(
        replay.get("formal_relocation_equivalence_sha256"),
        "formal relocation equivalence",
    )
    replay_equivalence_sha256 = _require_sha256(
        replay.get("replay_relocation_equivalence_sha256"),
        "replayed relocation equivalence",
    )
    replayed_runtime_verification = {
        **verification,
        "artifact_sha256": _require_sha256(
            value.get("runtime_verification_artifact_sha256"),
            "replayed runtime verification",
        ),
    }
    if (
        value.get("schema_version") != REPLAY_RESULT_SCHEMA
        or not _require_sha256(value.get("main_artifact_sha256"), "replayed main artifact")
        or not _require_sha256(
            value.get("runtime_verification_artifact_sha256"), "replayed runtime verification"
        )
        or formal_equivalence_sha256 != replay_equivalence_sha256
        or not isinstance(value.get("scope"), dict)
    ):
        raise IndependentVerificationError("independent replay identity differs")
    for field in ("development_only", "embargo_consumed", "final_oos_consumed", "production_authority"):
        expected = field == "development_only"
        if value["scope"].get(field) is not expected:
            raise IndependentVerificationError("independent replay scope is invalid")
    _verify_runtime_verification(
        replayed_runtime_verification,
        main_artifact_sha256=value["main_artifact_sha256"],
        producer_root_sha256=normalized_producer_root,
    )


def _publish_receipt(
    inputs: Mapping[str, Any],
    verifier: Mapping[str, str],
    preregistration: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    publisher: Any,
) -> dict[str, Any]:
    body = {
        "schema_version": RECEIPT_SCHEMA,
        "run_root": RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "preregistration_sha256": preregistration["sha256"],
        "source": {
            "git_commit": inputs["source"]["git_commit"],
            "python_executable_sha256": inputs["source"]["python_executable_sha256"],
            "base_python_executable_sha256": inputs["source"]["base_python_executable_sha256"],
            "stdlib_manifest_sha256": inputs["source"]["stdlib_manifest_sha256"],
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
        "execution_snapshot": dict(replay["execution_snapshot"]),
        "independent_replay": {
            "result_file_sha256": replay["result_sha256"],
            "exit_code": replay["exit_code"],
            "stdout": replay["stdout"],
            "stderr": replay["stderr"],
            "relocation_equivalence": {
                "formal_sha256": replay["formal_relocation_equivalence_sha256"],
                "replayed_sha256": replay["replay_relocation_equivalence_sha256"],
            },
        },
        "checks": {
            "formal_completion_contract_verified": True,
            "source_commit_and_cleanliness_verified": True,
            "producer_binding_verified": True,
            "isolated_code_snapshot_verified": True,
            "isolated_runtime_snapshot_verified": True,
            "isolated_data_snapshot_verified": True,
            "full_isolated_replay_completed": True,
            "replayed_result_bundle_relocation_equivalence_verified": True,
            "point_in_time_development_scope_preserved": True,
        },
        "post_verification_authority": {
            "development_statistical_interpretation_allowed": False,
            "profile_registration_authority": False,
            "production_recommendation_authority": False,
            "automatic_trading_authority": False,
        },
        "terminal_status_required": True,
        "receipt_alone_authoritative": False,
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
    raw = _canonical_bytes(receipt) + b"\n"
    path = publisher.write_root_once(
        name=f"{RECEIPT_ROOT_NAME}.{receipt_sha}.json",
        raw=raw,
        label="independent verification receipt",
    )
    return {"path": path, "sha256": receipt_sha}


def _record_status(
    inputs: Mapping[str, Any],
    claim_raw: bytes,
    *,
    status: str,
    verified: bool,
    receipt: Mapping[str, Any] | None,
    error_type: str | None,
    publisher: Any,
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
    publisher.write_root_once(
        name=STATUS_NAME,
        raw=raw,
        label="independent verification status",
        tolerate_close_failure=True,
    )


def run(source_root: str, expected_verifier_commit: str) -> dict[str, str]:
    prepared = preflight(source_root, expected_verifier_commit)
    verifier = prepared["verifier"]
    inputs = prepared["inputs"]
    binder_type = _frozen_run_root_binder(
        verifier["git_commit"],
        verifier["run_root_binder_git_blob_sha256"],
    )
    claim_raw: bytes | None = None
    with _held_run_root(
        Path(inputs["run_root"]),
        binder_type=binder_type,
    ) as publisher:
        publisher.assert_bound()
        if publisher.root_file_exists(STATUS_NAME):
            raise IndependentVerificationError("independent verification is already terminal")
        preregistration = _preregistration(inputs, verifier, publisher=publisher)
        claim_raw = _new_claim(inputs, preregistration)
        publisher.write_root_once(
            name=CLAIM_NAME,
            raw=claim_raw,
            label="independent verification claim",
        )
        try:
            with _IsolatedReplaySnapshot(inputs["source"], inputs) as snapshot:
                if snapshot.snapshot_inputs is None:
                    raise IndependentVerificationError("frozen replay data snapshot is missing")
                replay = _run_isolated_replay(inputs, snapshot, publisher=publisher)
                execution_snapshot = snapshot.binding()
                _validate_replay(snapshot.snapshot_inputs, replay, execution_snapshot)
                snapshot.verify()
                receipt = _publish_receipt(
                    inputs,
                    verifier,
                    preregistration,
                    replay,
                    publisher=publisher,
                )
                snapshot.verify()
            publisher.assert_bound()
            _record_status(
                inputs,
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
            if claim_raw is not None and not publisher.root_file_exists(STATUS_NAME):
                publisher.assert_bound()
                _record_status(
                    inputs,
                    claim_raw,
                    status="failed",
                    verified=False,
                    receipt=None,
                    error_type=type(exc).__name__,
                    publisher=publisher,
                )
                publisher.mark_terminal_status_committed()
            raise


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
